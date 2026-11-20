#!/usr/bin/env python3
"""Model capability probe.

A model appearing in a list is not a model that can be invoked. This tool keeps
the two apart:

  list    free: ask the host which model IDs exist, record the raw output
  invoke  costs money: one bounded request, record exit code, latency and a
          short output hash. It never parses a price or a token count it did not
          receive.

Evidence is written under <EOS_HOME>/evidence/models (or the workspace legacy
evidence directory when one exists).

Commands:
  list    [--models a,b] [--command "<json array>"]
  invoke  --model <id> [--prompt "Reply with OK"] [--agent build] [--timeout 120]
  selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

EVIDENCE = ep.evidence_dir("models", os.path.join("evidence", "models"))
DEFAULT_MODELS = ("opencode/claude-fable-5-2", "opencode/gpt-6.2-astra")
PROTOCOL = "eos-model-probe/1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest().upper()


def command_parts(raw: str) -> list:
    if not raw:
        return [resolve_opencode(), "models"]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    return shlex.split(raw, posix=False)


def resolve_opencode() -> str:
    """Return an executable the subprocess layer can actually start.

    On Windows the host installs `opencode.ps1`/`opencode.cmd`, and neither
    CreateProcess nor Python's subprocess applies PATHEXT. `shutil.which` does,
    so it is the resolver; a bare "opencode" is never passed to Popen.
    """
    explicit = os.environ.get("EOS_OPENCODE")
    if explicit and os.path.isfile(explicit):
        return explicit
    for name in ("opencode", "opencode.exe", "opencode.cmd", "opencode.bat"):
        found = shutil.which(name)
        if found:
            return found
    local = os.environ.get("LOCALAPPDATA") or ""
    for candidate in (
        os.path.join(local, "hermes", "node", "opencode.exe"),
        os.path.join(local, "hermes", "node", "node_modules", "@opencode", "cli",
                     "bin", "opencode.exe"),
        os.path.join(local, "Programs", "opencode", "opencode.exe"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    return "opencode"


def write_evidence(record: dict) -> str:
    os.makedirs(EVIDENCE, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(EVIDENCE, f"model-probe-{stamp}-{os.urandom(2).hex()}.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    return path


def cmd_list(args) -> int:
    argv = command_parts(args.command)
    started = time.time()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=args.timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"BLOCKED: model list command failed: {exc}")
        return 2
    duration = time.time() - started
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    available = set(lines)
    wanted = [item.strip() for item in (args.models or ",".join(DEFAULT_MODELS)).split(",") if item.strip()]
    found = {model: (model in available) for model in wanted}
    record = {
        "protocol": PROTOCOL, "kind": "model_list", "created_at": utc_now(),
        "command": argv, "exit_code": proc.returncode, "duration_seconds": round(duration, 2),
        "count": len(lines), "requested": found,
        "missing": [model for model, present in found.items() if not present],
        "stdout_sha256": sha256(proc.stdout), "stderr_tail": proc.stderr[-1000:],
    }
    path = write_evidence(record)
    print(f"{'PASS' if proc.returncode == 0 and not record['missing'] else 'BLOCKED'}: "
          f"model list ({len(lines)} entries, {len(record['missing'])} missing)")
    for model, present in found.items():
        print(f"  {'present' if present else 'MISSING'}  {model}")
    print(f"  evidence: {ep.store_path(path, ep.resolve_workspace(args.workspace))}")
    return 0 if proc.returncode == 0 and not record["missing"] else 1


def cmd_invoke(args) -> int:
    argv = [resolve_opencode(), "run", "--standalone", "--auto", "--format", "json",
            "--agent", args.agent, "--model", args.model, args.prompt]
    started = time.time()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"BLOCKED: invocation exceeded {args.timeout}s and was killed")
        return 2
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"BLOCKED: invocation failed: {exc}")
        return 2
    duration = time.time() - started
    record = {
        "protocol": PROTOCOL, "kind": "model_invoke", "created_at": utc_now(),
        "model": args.model, "agent": args.agent, "prompt": args.prompt,
        "exit_code": proc.returncode, "duration_seconds": round(duration, 2),
        "stdout_sha256": sha256(proc.stdout), "stdout_chars": len(proc.stdout),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-2000:],
        "auth_error": any(token in (proc.stdout + proc.stderr).lower()
                          for token in ("unauthorized", "api key", "authentication", "invalid model")),
    }
    path = write_evidence(record)
    verdict = "PASS" if proc.returncode == 0 and not record["auth_error"] else "BLOCKED"
    print(f"{verdict}: {args.model} rc={proc.returncode} {duration:.1f}s")
    if proc.returncode != 0:
        print(f"  stdout: {proc.stdout.strip()[-800:]}")
        print(f"  stderr: {proc.stderr.strip()[-500:]}")
    print(f"  evidence: {ep.store_path(path, ep.resolve_workspace(args.workspace))}")
    return 0 if verdict == "PASS" else 1


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    @case("list parses the host output and detects a missing model")
    def _():
        fake = ("import sys; print('opencode/a'); print('opencode/b')")
        args = argparse.Namespace(models="opencode/a,opencode/missing",
                                  command=json.dumps([sys.executable, "-c", fake]),
                                  timeout=20, workspace=tempfile.mkdtemp(prefix="eos-mp-"))
        assert cmd_list(args) == 1
        files = os.listdir(EVIDENCE)
        assert any(name.startswith("model-probe-") for name in files)
        return "missing detected"

    @case("list passes when every requested model is present")
    def _():
        fake = "print('opencode/a'); print('opencode/b')"
        args = argparse.Namespace(models="opencode/a,opencode/b",
                                  command=json.dumps([sys.executable, "-c", fake]),
                                  timeout=20, workspace=tempfile.mkdtemp(prefix="eos-mp-"))
        assert cmd_list(args) == 0
        return "all present"

    @case("a broken command is BLOCKED, not a silent pass")
    def _():
        args = argparse.Namespace(models="opencode/a",
                                  command=json.dumps([sys.executable, "-c", "import sys; sys.exit(3)"]),
                                  timeout=20, workspace=tempfile.mkdtemp(prefix="eos-mp-"))
        assert cmd_list(args) == 1
        return "blocked"

    passed = failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            passed += 1
            print(f"PASS  {name}  [{detail}]")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(cases)} cases passed")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="model_probe.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list")
    p.add_argument("--models", default="")
    p.add_argument("--command", default="", help="override the list command (JSON array)")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--workspace")
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("invoke")
    p.add_argument("--model", required=True)
    p.add_argument("--prompt", default="Reply with exactly OK")
    p.add_argument("--agent", default="build")
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--workspace")
    p.set_defaults(func=cmd_invoke)
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
