#!/usr/bin/env python3
"""EOS mechanical quality gate.

The gate is deliberately not programmable by an agent. Commands come from a
versioned registry (.agents/registry/GATES.json); the CLI can only choose which
project and suite to run. There is no `--command` flag by design.

An artifact is trusted only if it carries: schema version, project id, baseline
snapshot identity, exact commands executed, per-command exit codes, output
hashes, and a log file that still hashes to the recorded value.

Commands:
  run     --project <id> --suite <name> [--timeout N]
  verify  --file <artifact> [--project <id>] [--max-age-min N]
  list
  show    --file <artifact>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

ROOT = ep.ENGINE_ROOT                  # program files; the registry lives here
WORKSPACE = ep.resolve_workspace()     # the project tree being gated
REGISTRY = ep.REGISTRY
GATES_PATH = os.path.join(REGISTRY, "GATES.json")
PROFILES_PATH = os.path.join(REGISTRY, "PROJECT_PROFILES.json")
EVIDENCE = ep.GATE_EVIDENCE
BACKUPS = ep.BACKUPS
SCHEMA = 1
DEFAULT_TIMEOUT = 300
LOG_TAIL = 20000

SAFE_ENV_KEYS = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
                 "HOME", "USERPROFILE", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS",
                 "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL")


class GateError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def load_json(path: str):
    # utf-8-sig: a hand-edited registry saved by a Windows editor must not take
    # the gate down over a byte-order mark.
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def latest_baseline():
    if not os.path.isdir(BACKUPS):
        return None
    candidates = []
    for name in os.listdir(BACKUPS):
        d = os.path.join(BACKUPS, name)
        hashes = os.path.join(d, "HASHES.txt")
        if name.startswith("baseline_") and os.path.isfile(hashes):
            candidates.append((os.path.getmtime(hashes), name, hashes))
    if not candidates:
        return None
    _, name, hashes = max(candidates)
    return {"label": name, "path": ep.store_path(hashes, WORKSPACE),
            "sha256": sha256_file(hashes), "entries": sum(1 for _ in open(hashes, encoding="utf-8"))}


def resolve_project(project: str) -> dict:
    gates = load_json(GATES_PATH)
    spec = (gates.get("projects") or {}).get(project)
    if not spec:
        raise GateError(f"unknown project '{project}' (not in GATES.json)")
    path = spec.get("path")
    if not path:
        profiles = load_json(PROFILES_PATH).get("profiles", [])
        match = next((p for p in profiles if p.get("id") == project), None)
        if not match:
            raise GateError(f"project '{project}' has no path and no profile")
        path = match.get("path", "")
    abs_path = ep.resolve_project_path(path, WORKSPACE)
    if not os.path.isdir(abs_path):
        raise GateError(f"project path does not exist: {path}")
    return {"id": project, "root": abs_path, "rel": ep.store_path(abs_path, WORKSPACE),
            "suites": spec.get("suites") or {}}


def build_env(spec_env: dict) -> dict:
    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["CI"] = "1"
    for k, v in (spec_env or {}).items():
        env[str(k)] = str(v)
    return env


def expand_tokens(value: str) -> str:
    """`%ENGINE%`, `%WORKSPACE%` and `%SELFTEST%` keep GATES.json portable: the
    file records *which* fixture or interpreter, never a machine-specific path.
    `%SELFTEST%` exists because a workspace keeps the fixtures at
    `<engine>/eos/selftest` while the product keeps them at `<engine>/selftest`."""
    return (
        value.replace("%SELFTEST%", ep.SELFTEST_DIR)
        .replace("%ENGINE%", ROOT)
        .replace("%WORKSPACE%", WORKSPACE)
    )


def resolve_interpreter(suite: dict):
    """A suite may name an interpreter from the registry's `interpreters` map.
    A missing interpreter is BLOCKED, never FAIL: the tests did not run."""
    name = suite.get("interpreter")
    if not name:
        return sys.executable, None
    gates = load_json(GATES_PATH)
    spec = (gates.get("interpreters") or {}).get(name)
    if not spec:
        return None, f"suite names interpreter '{name}' which GATES.json does not define"
    path = expand_tokens(spec.get("path", ""))
    if not os.path.isfile(path):
        return None, (
            f"interpreter '{name}' is not present at {path}. "
            f"{spec.get('description', '')} The suite is BLOCKED, not passed."
        )
    return path, None


def run_command(cmd: list, cwd: str, env: dict, timeout: int, python: str | None = None) -> dict:
    argv = [expand_tokens(str(a)) for a in cmd]
    if argv and argv[0] == "python":
        argv[0] = python or sys.executable
    started = time.time()
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout)
        out, err, rc, timed_out = proc.stdout, proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or b""
        err = (exc.stderr or b"") + f"\n[eos] TIMEOUT after {timeout}s".encode()
        rc, timed_out = 124, True
    except FileNotFoundError as exc:
        out, err, rc, timed_out = b"", f"[eos] executable not found: {exc}".encode(), 127, False
    duration = round(time.time() - started, 3)
    combined = out + b"\n--- stderr ---\n" + err
    return {
        "argv": argv,
        "exit_code": rc,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "output_sha256": sha256_bytes(combined),
        "output_bytes": len(combined),
        "tail": combined.decode("utf-8", "replace")[-LOG_TAIL:],
    }


def cmd_run(args) -> int:
    try:
        project = resolve_project(args.project)
    except GateError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    suite = project["suites"].get(args.suite)
    if not suite:
        print(f"BLOCKED: suite '{args.suite}' not defined for '{args.project}' "
              f"(available: {', '.join(sorted(project['suites'])) or 'none'})")
        return 2

    # A suite with no verified command, or one explicitly marked NOT_CONFIGURED,
    # is BLOCKED. It must never produce a gate artifact: a file in the evidence
    # directory is a claim that something ran.
    if suite.get("status") == "NOT_CONFIGURED" or not (suite.get("commands") or []):
        reason = suite.get("description") or "no executable command is configured"
        print(f"BLOCKED: {args.project}/{args.suite} has no runnable command")
        print(f"  {reason}")
        return 2

    baseline = latest_baseline()
    if not baseline:
        print("BLOCKED: no verified baseline snapshot; a gate without a baseline cannot detect drift")
        return 2

    timeout = int(args.timeout or suite.get("timeout_seconds") or DEFAULT_TIMEOUT)
    interpreter, why = resolve_interpreter(suite)
    if interpreter is None:
        print(f"BLOCKED: {why}")
        return 2
    env = build_env(suite.get("env"))
    results = []
    for step in suite.get("commands") or []:
        name = step.get("name") or " ".join(step.get("cmd") or [])
        cmd = step.get("cmd") or []
        if not cmd:
            results.append({"name": name, "argv": [], "exit_code": 126, "timed_out": False,
                            "duration_seconds": 0, "output_sha256": "", "output_bytes": 0,
                            "tail": "[eos] empty command rejected", "required": True})
            continue
        record = run_command(cmd, project["root"], env, timeout, interpreter)
        record["name"] = name
        record["required"] = bool(step.get("required", True))
        results.append(record)

    required_failed = [r for r in results if r.get("required") and (r["exit_code"] != 0 or r["timed_out"])]
    optional_failed = [r for r in results if not r.get("required") and (r["exit_code"] != 0 or r["timed_out"])]
    verdict = "PASS" if not required_failed else "FAIL"
    if not results:
        verdict = "BLOCKED"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.path.join(EVIDENCE, args.project)
    os.makedirs(out_dir, exist_ok=True)
    base = f"gate-{args.suite}-{stamp}"
    log_path = os.path.join(out_dir, base + ".log")
    art_path = os.path.join(out_dir, base + ".json")

    lines = [f"# EOS gate {args.project}/{args.suite} {verdict}",
             f"# baseline {baseline['label']} sha256={baseline['sha256']}",
             f"# cwd {project['rel']}"]
    for r in results:
        lines.append(f"\n$ {' '.join(r['argv'])}  -> rc={r['exit_code']} timeout={r['timed_out']} {r['duration_seconds']}s")
        lines.append(r["tail"])
    log_bytes = "\n".join(lines).encode("utf-8")
    with open(log_path, "wb") as fh:
        fh.write(log_bytes)

    artifact = {
        "schema": SCHEMA,
        "kind": "quality_gate",
        "created_at": utc_now(),
        "project": args.project,
        "suite": args.suite,
        "verdict": verdict,
        "baseline": baseline,
        "project_path": project["rel"],
        "commands": [{k: r[k] for k in ("name", "argv", "exit_code", "timed_out", "duration_seconds",
                                        "output_sha256", "output_bytes", "required")} for r in results],
        "failures": [r["name"] for r in required_failed],
        "optional_failures": [r["name"] for r in optional_failed],
        "log": {"path": ep.store_path(log_path, WORKSPACE),
                "sha256": sha256_bytes(log_bytes), "bytes": len(log_bytes)},
        "runtime": {"python": platform.python_version(), "platform": platform.system(),
                    "machine": platform.machine()},
        "limits": {"timeout_seconds": timeout,
                   "env_allowlist": list(SAFE_ENV_KEYS),
                   "note": "commands are fixed in GATES.json; agents cannot inject a command"},
    }
    with open(art_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(artifact, fh, indent=2)

    print(f"{verdict}: {args.project}/{args.suite} ({len(results)} command(s), {len(required_failed)} required failure(s))")
    for r in results:
        print(f"  rc={r['exit_code']:<4} {r['duration_seconds']:>7}s  {r['name']}")
    print(f"  artifact: {ep.store_path(art_path, WORKSPACE)}")
    print(f"  log hash: {artifact['log']['sha256'][:16]}...  baseline: {baseline['label']}")
    return 0 if verdict == "PASS" else 1


def verify_artifact(path: str, project: str | None = None, max_age_min: int | None = 1440) -> dict:
    problems = []
    if not os.path.isfile(path):
        return {"ok": False, "problems": [f"artifact not found: {path}"], "artifact": None}
    try:
        a = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "problems": [f"artifact unreadable: {exc}"], "artifact": None}

    if a.get("schema") != SCHEMA:
        problems.append(f"schema {a.get('schema')} != {SCHEMA}")
    if a.get("kind") != "quality_gate":
        problems.append("kind is not quality_gate")
    if not a.get("project"):
        problems.append("missing project id")
    if project and a.get("project") != project:
        problems.append(f"project mismatch: artifact={a.get('project')} expected={project}")
    if a.get("verdict") != "PASS":
        problems.append(f"verdict is {a.get('verdict')}")
    if not a.get("commands"):
        problems.append("no commands recorded")
    if any(c.get("exit_code") != 0 or c.get("timed_out") for c in a.get("commands", []) if c.get("required", True)):
        problems.append("a required command failed in the record")

    baseline = a.get("baseline") or {}
    if not baseline.get("sha256"):
        problems.append("no baseline identity recorded")
    else:
        bl_path = ep.load_stored_path(baseline.get("path", ""), WORKSPACE)
        if not os.path.isfile(bl_path):
            problems.append(f"baseline file missing: {baseline.get('path')}")
        elif sha256_file(bl_path) != baseline["sha256"]:
            problems.append("baseline changed since the gate ran (tampered or replaced snapshot)")

    log = a.get("log") or {}
    log_path = ep.load_stored_path(log.get("path", ""), WORKSPACE)
    if not log.get("path") or not os.path.isfile(log_path):
        problems.append("gate log missing")
    elif sha256_file(log_path) != log.get("sha256"):
        problems.append("gate log hash mismatch (output was edited after the run)")

    if max_age_min is not None and a.get("created_at"):
        try:
            created = datetime.fromisoformat(a["created_at"])
            age_min = (datetime.now(timezone.utc) - created).total_seconds() / 60
            if age_min > max_age_min:
                problems.append(f"artifact is {age_min:.0f} min old (limit {max_age_min})")
        except ValueError:
            problems.append("created_at is not ISO-8601")

    return {"ok": not problems, "problems": problems, "artifact": a, "path": path}


def cmd_verify(args) -> int:
    result = verify_artifact(args.file, args.project, args.max_age_min)
    a = result.get("artifact") or {}
    if result["ok"]:
        print(f"PASS: gate artifact is valid ({a.get('project')}/{a.get('suite')}, {a.get('created_at')})")
        print(f"  baseline: {(a.get('baseline') or {}).get('label')}")
        for c in a.get("commands", []):
            print(f"  rc={c.get('exit_code')} {c.get('name')}")
        return 0
    print("BLOCKED: gate artifact is not trustworthy")
    for p in result["problems"]:
        print(f"  - {p}")
    return 1


def cmd_list(args) -> int:
    if not os.path.isdir(EVIDENCE):
        print("no gate artifacts")
        return 0
    rows = []
    for project in sorted(os.listdir(EVIDENCE)):
        d = os.path.join(EVIDENCE, project)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".json"):
                try:
                    a = load_json(os.path.join(d, name))
                    rows.append((a.get("created_at", ""), project, a.get("suite", ""), a.get("verdict", "")))
                except (OSError, json.JSONDecodeError):
                    rows.append(("", project, name, "UNREADABLE"))
    for created, project, suite, verdict in sorted(rows)[-25:]:
        print(f"{created}  {project:<16} {suite:<20} {verdict}")
    return 0


def cmd_show(args) -> int:
    result = verify_artifact(args.file, None, None)
    a = result.get("artifact")
    if not a:
        print("BLOCKED: " + "; ".join(result["problems"]))
        return 1
    print(json.dumps(a, indent=2)[:6000])
    if not result["ok"]:
        print("\nPROBLEMS:")
        for p in result["problems"]:
            print("  - " + p)
    return 0 if result["ok"] else 1


def set_workspace(args) -> None:
    """`--root` (or EOS_WORKSPACE, or cwd) selects the project tree the gate is
    about. The engine's own location is never used for that decision."""
    global WORKSPACE
    explicit = getattr(args, "root", None)
    if explicit:
        WORKSPACE = ep.resolve_workspace(explicit)


def main() -> int:
    ap = argparse.ArgumentParser(prog="quality_gate.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--project", required=True)
    r.add_argument("--suite", required=True)
    r.add_argument("--timeout", type=int)
    r.add_argument("--root", help="workspace the project path is relative to")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify")
    v.add_argument("--file", required=True)
    v.add_argument("--project")
    v.add_argument("--max-age-min", type=int, default=1440)
    v.add_argument("--root")
    v.set_defaults(func=cmd_verify)

    sub.add_parser("list").set_defaults(func=cmd_list)

    s = sub.add_parser("show")
    s.add_argument("--file", required=True)
    s.add_argument("--root")
    s.set_defaults(func=cmd_show)

    args = ap.parse_args()
    set_workspace(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
