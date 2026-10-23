#!/usr/bin/env python3
"""EOS release gate: bind the shipped files to a real, commit-scoped gate run.

The release workflow may not treat "an artifact exists" as proof that the
product passed its own quality gate. This tool produces and verifies a
`release_gate` record that carries:

  * the commit the suites ran against (and the ref/repository that asked for it),
  * the exact gate registry (`GATES.json`) hash used for the commands,
  * the baseline identity of every tracked file at that commit,
  * the embedded quality-gate artifact of every required suite (engine,
    security, regression), with their command exit codes and output hashes,
  * whether the tracked tree was unchanged before *and* after the run,
  * the CI run id/attempt/repository when it runs inside GitHub Actions.

It also seals the built packages: `seal` hashes the installers and ZIP/DMG
files and records the gate artifact hash. `verify` recomputes every one of
those hashes and refuses a forged PASS file, a missing suite, a failed
required command, a different commit, a stale artifact, a different workflow
run, or a package that changed after sealing.

Commands:
  run      --out <dir> [--version V] [--commit SHA] [--ref REF]
  seal     --gate <json> --dist <dir> [--out <json>] [--require-ext .exe]
  verify   --gate <json> [--manifest <json>] [--dist <dir>] [--commit SHA]
  selftest

The verifier never runs a test and never trusts a suite's own verdict alone:
the embedded artifact's command records must agree with it, and the artifact
hash must match the SHA recorded by the outer record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SCHEMA = 1
KIND = "release_gate"
MANIFEST_KIND = "release_manifest"
REQUIRED_SUITES = ("engine", "security", "regression")
MIN_BASELINE_ENTRIES = 10
MAX_AGE_MIN = 1440
GATES_REL = os.path.join("registry", "GATES.json")
_TOOLS = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(_TOOLS)

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
HEX_RE = re.compile(r"^[0-9A-F]{64}$")


class ReleaseError(Exception):
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


def canonical_sha(value) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256_bytes(body.encode("utf-8"))


def load_json(path: str):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, indent=2, sort_keys=False)
        fh.write("\n")


def git(root: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", root, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {(proc.stderr or '').strip()[:300]}")
    return proc.stdout


def git_head(root: str) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def tracked_files(root: str) -> list:
    raw = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True)
    if raw.returncode != 0:
        raise ReleaseError("git ls-files failed; a release gate needs a git checkout")
    names = [n.decode("utf-8", "replace") for n in raw.stdout.split(b"\x00") if n]
    return sorted(names)


def tracked_tree(root: str) -> dict:
    entries = {}
    missing = []
    for rel in tracked_files(root):
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            missing.append(rel)
            continue
        entries[rel] = sha256_file(path)
    if missing:
        raise ReleaseError(f"tracked file missing from the checkout: {missing[0]}")
    return entries


def dirty_tracked(root: str) -> list:
    out = git(root, "status", "--porcelain", "--untracked-files=no")
    return [line for line in out.splitlines() if line.strip()]


def tree_diff(before: dict, after: dict) -> list:
    problems = []
    for rel in sorted(set(before) - set(after)):
        problems.append(f"tracked file disappeared during the gate: {rel}")
    for rel in sorted(set(after) - set(before)):
        problems.append(f"tracked file appeared during the gate: {rel}")
    for rel in sorted(set(before) & set(after)):
        if before[rel] != after[rel]:
            problems.append(f"tracked file changed during the gate: {rel}")
    return problems


def product_version(engine: str):
    """The version the built program reports: Cargo crate version + suffix."""
    cargo = os.path.join(os.path.dirname(engine), "cli", "Cargo.toml")
    install_rs = os.path.join(os.path.dirname(engine), "cli", "src", "install.rs")
    if not os.path.isfile(cargo):
        return None
    text = open(cargo, encoding="utf-8").read()
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        return None
    version = match.group(1)
    if os.path.isfile(install_rs):
        rs = open(install_rs, encoding="utf-8").read()
        suffix = re.search(
            r'VERSION\s*:\s*&str\s*=\s*concat!\(env!\("CARGO_PKG_VERSION"\)\s*,\s*"([^"]+)"\)', rs)
        if suffix:
            version += suffix.group(1)
    return version


def write_baseline(root: str, out_dir: str, label: str) -> dict:
    """A baseline in the HASHES.txt format the quality gate consumes, built
    from the git index at the exact commit under test."""
    entries = tracked_tree(root)
    if len(entries) < MIN_BASELINE_ENTRIES:
        raise ReleaseError(
            f"only {len(entries)} tracked file(s); a release baseline must cover the product")
    directory = os.path.join(out_dir, "baselines", label)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "HASHES.txt")
    body = "\n".join(f"{digest}|{rel}" for rel, digest in entries.items()) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return {"label": label, "kind": "git_commit_tree", "sha256": sha256_file(path),
            "entries": len(entries)}


def parse_artifact_path(stdout: str) -> str:
    for line in reversed(stdout.splitlines()):
        if line.strip().startswith("artifact:"):
            return line.split(":", 1)[1].strip()
    return ""


def run_suite(engine: str, work: str, suite: str, timeout: int = 1800) -> dict:
    evidence = os.path.join(work, "evidence")
    env = {k: os.environ[k] for k in (
        "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "HOME",
        "USERPROFILE", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL") if k in os.environ}
    env.update({
        "EOS_HOME": os.path.join(work, "home"),
        "EOS_BASELINES": os.path.join(work, "baselines"),
        "EOS_EVIDENCE_DIR": evidence,
        "EOS_WORKSPACE": engine,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "CI": "1",
    })
    cmd = [sys.executable, os.path.join(engine, "tools", "quality_gate.py"),
           "run", "--project", "eos", "--suite", suite, "--root", engine]
    try:
        proc = subprocess.run(cmd, cwd=engine, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {"id": suite, "verdict": "BLOCKED",
                "detail": f"suite runner exceeded {timeout}s",
                "stdout": (exc.stdout or "")[-4000:]}
    out = proc.stdout or ""
    artifact_path = parse_artifact_path(out)
    record = {"id": suite, "stdout": out[-8000:], "stderr": (proc.stderr or "")[-4000:],
              "runner_exit": proc.returncode}
    if not artifact_path or not os.path.isfile(artifact_path):
        record.update({"verdict": "BLOCKED",
                       "detail": "the suite produced no gate artifact"})
        return record
    try:
        artifact = load_json(artifact_path)
    except (OSError, ValueError) as exc:
        record.update({"verdict": "BLOCKED", "detail": f"unreadable gate artifact: {exc}"})
        return record
    record.update({
        "verdict": artifact.get("verdict", "BLOCKED"),
        "artifact": artifact,
        "artifact_sha256": canonical_sha(artifact),
        "artifact_path": artifact_path,
    })
    return record


def cmd_run(args) -> int:
    engine = os.path.abspath(args.engine)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    commit = args.commit or git_head(engine)
    if not COMMIT_RE.match(commit):
        print(f"BLOCKED: '{commit}' is not a full 40-character commit id")
        return 2
    version = args.version or product_version(engine) or ""
    if not version:
        print("BLOCKED: cannot determine the product version; pass --version")
        return 2
    expected = product_version(engine)
    if expected and version != expected:
        print(f"BLOCKED: requested version {version} != built version {expected}")
        return 2

    dirty = dirty_tracked(engine)
    if dirty:
        print("BLOCKED: the tracked tree is dirty; a release gate must run on a commit")
        for line in dirty[:10]:
            print("  " + line)
        return 2

    head = git_head(engine)
    if head != commit:
        print(f"BLOCKED: --commit {commit} is not HEAD ({head})")
        return 2

    gates = os.path.join(engine, GATES_REL)
    if not os.path.isfile(gates):
        print(f"BLOCKED: gate registry missing: {gates}")
        return 2
    gates_sha = sha256_file(gates)

    before = tracked_tree(engine)
    baseline = write_baseline(engine, out, f"baseline_ci_{commit[:12]}")

    suites = []
    for suite in REQUIRED_SUITES:
        print(f"[gate] {suite}")
        record = run_suite(engine, out, suite)
        suites.append(record)
        print(f"       {record['verdict']} ({len((record.get('artifact') or {}).get('commands') or [])} command(s))")
        if record.get("artifact"):
            # Keep the outer record self-contained but do not duplicate the
            # evidence log; the artifact carries its hash and path.
            write_json(os.path.join(out, f"suite-{suite}.json"), record["artifact"])

    after = tracked_tree(engine)
    diff = tree_diff(before, after)
    dirty_after = dirty_tracked(engine)
    if dirty_after:
        diff.append(f"{len(dirty_after)} tracked path(s) dirty after the gate")

    failed = [s["id"] for s in suites if s["verdict"] != "PASS"]
    verdict = "PASS" if not failed and not diff else "FAIL"
    artifact = {
        "schema": SCHEMA,
        "kind": KIND,
        "created_at": utc_now(),
        "version": version,
        "commit": commit,
        "ref": args.ref or os.environ.get("GITHUB_REF", ""),
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "workflow_run": {
            "run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
            "job": os.environ.get("GITHUB_JOB", ""),
        },
        "gates_sha256": gates_sha,
        "baseline": baseline,
        "workspace_clean_before": True,
        "workspace_clean_after": not diff,
        "required_suites": list(REQUIRED_SUITES),
        "suites": [{
            "id": s["id"],
            "verdict": s["verdict"],
            "artifact_sha256": s.get("artifact_sha256", ""),
            "detail": s.get("detail", ""),
            "artifact": s.get("artifact"),
        } for s in suites],
        "failures": failed,
        "tree_problems": diff,
        "verdict": verdict,
    }
    path = os.path.join(out, "release-gate.json")
    write_json(path, artifact)

    print(f"\n{verdict}: release gate {version} @ {commit[:12]}")
    for s in suites:
        print(f"  {s['id']:<12} {s['verdict']:<8} {s.get('artifact_sha256', '')[:16]}")
    for p in diff:
        print(f"  TREE {p}")
    print(f"  artifact: {path}")
    return 0 if verdict == "PASS" else 1


def validate_suite(record: dict, problems: list) -> None:
    sid = record.get("id") or "?"
    if record.get("verdict") != "PASS":
        problems.append(f"suite {sid}: verdict is {record.get('verdict')}")
    embedded = record.get("artifact")
    if not isinstance(embedded, dict):
        problems.append(f"suite {sid}: no embedded gate artifact")
        return
    if embedded.get("kind") != "quality_gate":
        problems.append(f"suite {sid}: embedded artifact is not a quality_gate")
    if embedded.get("verdict") != "PASS":
        problems.append(f"suite {sid}: embedded verdict is {embedded.get('verdict')}")
    if embedded.get("project") != "eos":
        problems.append(f"suite {sid}: embedded project is {embedded.get('project')}")
    if embedded.get("suite") != sid:
        problems.append(f"suite {sid}: embedded suite is {embedded.get('suite')}")
    expected_hash = record.get("artifact_sha256", "")
    if not HEX_RE.match(str(expected_hash)):
        problems.append(f"suite {sid}: artifact hash is not a SHA-256")
    elif canonical_sha(embedded) != expected_hash:
        problems.append(f"suite {sid}: artifact hash does not match the embedded record")
    commands = embedded.get("commands") or []
    if not commands:
        problems.append(f"suite {sid}: no commands recorded")
    for command in commands:
        if command.get("required", True) and (command.get("exit_code") != 0
                                              or command.get("timed_out")):
            problems.append(f"suite {sid}: required command {command.get('name')} failed")
        if not HEX_RE.match(str(command.get("output_sha256", ""))):
            problems.append(f"suite {sid}: command {command.get('name')} has no output hash")
    if embedded.get("failures"):
        problems.append(f"suite {sid}: failures recorded: {embedded.get('failures')}")


def validate_gate(gate, expected_commit=None, gates_path=None,
                  check_env=False, max_age_min=MAX_AGE_MIN) -> list:
    problems = []
    if not isinstance(gate, dict):
        return ["release gate is not an object"]
    if gate.get("schema") != SCHEMA:
        problems.append(f"schema {gate.get('schema')} != {SCHEMA}")
    if gate.get("kind") != KIND:
        problems.append(f"kind is {gate.get('kind')}, not {KIND}")
    commit = str(gate.get("commit") or "")
    if not COMMIT_RE.match(commit):
        problems.append("commit is not a full 40-character git sha")
    if expected_commit and commit != expected_commit:
        problems.append(f"commit mismatch: artifact={commit or '?'} expected={expected_commit}")
    if not gate.get("version"):
        problems.append("no product version recorded")
    verdict = gate.get("verdict")
    if verdict != "PASS":
        problems.append(f"verdict is {verdict}")
    if gate.get("workspace_clean_before") is not True:
        problems.append("the tracked tree was not clean before the gate")
    if gate.get("workspace_clean_after") is not True:
        problems.append("the tracked tree changed during the gate")
    if gate.get("tree_problems"):
        problems.append(f"tree problems recorded: {gate.get('tree_problems')[:3]}")
    if gate.get("failures"):
        problems.append(f"failed suites recorded: {gate.get('failures')}")

    gates_sha = str(gate.get("gates_sha256") or "")
    if not HEX_RE.match(gates_sha):
        problems.append("gates_sha256 is missing or not a SHA-256")
    elif gates_path and os.path.isfile(gates_path):
        if sha256_file(gates_path) != gates_sha:
            problems.append("GATES.json changed since the gate ran")

    baseline = gate.get("baseline") or {}
    entries = baseline.get("entries")
    if not isinstance(entries, int) or entries < MIN_BASELINE_ENTRIES:
        problems.append(f"baseline covers {entries} file(s); at least {MIN_BASELINE_ENTRIES} are required")
    if baseline.get("kind") != "git_commit_tree":
        problems.append(f"baseline kind is {baseline.get('kind')}")
    if not HEX_RE.match(str(baseline.get("sha256", ""))):
        problems.append("baseline sha256 is missing or not a SHA-256")

    suites = gate.get("suites") or []
    ids = [s.get("id") for s in suites if isinstance(s, dict)]
    if len(ids) != len(set(ids)):
        problems.append("duplicate suite ids")
    missing = [s for s in REQUIRED_SUITES if s not in ids]
    if missing:
        problems.append(f"required suite(s) missing: {', '.join(missing)}")
    for record in suites:
        if isinstance(record, dict):
            validate_suite(record, problems)

    if max_age_min is not None and gate.get("created_at"):
        try:
            created = datetime.fromisoformat(str(gate["created_at"]))
            age = (datetime.now(timezone.utc) - created).total_seconds() / 60
            if age > max_age_min:
                problems.append(f"artifact is {age:.0f} min old (limit {max_age_min})")
            if age < -10:
                problems.append("artifact created_at is in the future")
        except ValueError:
            problems.append("created_at is not ISO-8601")

    if check_env:
        run = gate.get("workflow_run") or {}
        # run_id and run_attempt live under workflow_run; the repository is a
        # top-level field. Looking it up in workflow_run would reject every
        # real CI artifact with `repository=(none)`.
        for env_key, field, recorded in (
                ("GITHUB_RUN_ID", "run_id", run.get("run_id")),
                ("GITHUB_RUN_ATTEMPT", "run_attempt", run.get("run_attempt")),
                ("GITHUB_REPOSITORY", "repository", gate.get("repository"))):
            actual = os.environ.get(env_key)
            if actual and str(recorded or "") != actual:
                problems.append(
                    f"workflow binding mismatch: {field}={recorded or '(none)'} env={actual}")
    return problems


def classify_artifacts(dist: str, skip=()):
    rows = []
    # Release manifests are named RELEASE-MANIFEST.json on Linux and
    # release-manifest.json in the default path. Compare case-insensitively on
    # every platform, or the seal itself becomes an "unsealed artifact".
    skip = {os.path.normcase(s).lower() for s in skip}
    for dirpath, _dirnames, filenames in os.walk(dist):
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, dist).replace(os.sep, "/")
            if os.path.normcase(rel).lower() in skip:
                continue
            size = os.path.getsize(path)
            if size == 0:
                raise ReleaseError(f"refusing to ship an empty artifact: {rel}")
            rows.append({"name": rel, "size": size, "sha256": sha256_file(path)})
    if not rows:
        raise ReleaseError("no artifacts to seal")
    names = [r["name"] for r in rows]
    if len(names) != len(set(names)):
        raise ReleaseError("duplicate artifact names after flattening")
    return sorted(rows, key=lambda r: r["name"])


def make_manifest(gate_path: str, dist: str, require_ext=None) -> dict:
    gate = load_json(gate_path)
    problems = validate_gate(gate, gates_path=None, max_age_min=None)
    if problems:
        raise ReleaseError("gate artifact is not valid: " + "; ".join(problems[:5]))
    artifacts = classify_artifacts(dist, skip={"release-manifest.json"})
    if require_ext:
        ext = require_ext.lower()
        if not any(a["name"].lower().endswith(ext) for a in artifacts):
            raise ReleaseError(f"no {require_ext} artifact in {dist}")
    return {
        "schema": SCHEMA,
        "kind": MANIFEST_KIND,
        "created_at": utc_now(),
        "version": gate.get("version"),
        "commit": gate.get("commit"),
        "gate_sha256": sha256_file(gate_path),
        "workflow_run": gate.get("workflow_run") or {},
        "artifacts": artifacts,
        "verdict": "PASS",
    }


def cmd_seal(args) -> int:
    try:
        manifest = make_manifest(args.gate, args.dist, args.require_ext)
    except (ReleaseError, OSError, ValueError) as exc:
        print(f"BLOCKED: {exc}")
        return 1
    out = os.path.abspath(args.out or os.path.join(args.dist, "release-manifest.json"))
    write_json(out, manifest)
    print(f"PASS: sealed {len(manifest['artifacts'])} artifact(s) for "
          f"{manifest['version']} @ {manifest['commit'][:12]}")
    for row in manifest["artifacts"]:
        print(f"  {row['sha256']}  {row['name']}  ({row['size']} bytes)")
    print(f"  manifest: {out}")
    return 0


def cmd_verify(args) -> int:
    problems = []
    if not os.path.isfile(args.gate):
        print(f"BLOCKED: gate artifact not found: {args.gate}")
        return 1
    try:
        gate = load_json(args.gate)
    except (OSError, ValueError) as exc:
        print(f"BLOCKED: gate artifact unreadable: {exc}")
        return 1
    gates_path = os.path.join(ENGINE, GATES_REL)
    problems.extend(validate_gate(gate, expected_commit=args.commit,
                                  gates_path=gates_path, check_env=True,
                                  max_age_min=args.max_age_min))
    manifest = None
    if args.manifest:
        if not os.path.isfile(args.manifest):
            problems.append(f"release manifest missing: {args.manifest}")
        else:
            try:
                manifest = load_json(args.manifest)
            except (OSError, ValueError) as exc:
                problems.append(f"release manifest unreadable: {exc}")
    if manifest is not None:
        if manifest.get("kind") != MANIFEST_KIND or manifest.get("verdict") != "PASS":
            problems.append("release manifest is not a PASS record")
        if manifest.get("gate_sha256") != sha256_file(args.gate):
            problems.append("manifest does not point at this gate artifact")
        if manifest.get("commit") != gate.get("commit"):
            problems.append("manifest commit does not match the gate")
        if manifest.get("version") != gate.get("version"):
            problems.append("manifest version does not match the gate")
        recorded = manifest.get("artifacts") or []
        if not recorded:
            problems.append("manifest lists no artifacts")
        if args.dist and recorded:
            if not os.path.isdir(args.dist):
                problems.append(f"dist directory missing: {args.dist}")
            else:
                on_disk = {row["name"]: row for row in classify_artifacts(
                    args.dist, skip={"release-manifest.json"})}
                listed = {row.get("name"): row for row in recorded}
                for name, row in listed.items():
                    if name not in on_disk:
                        problems.append(f"artifact missing from dist: {name}")
                        continue
                    actual = on_disk[name]
                    if actual["sha256"] != row.get("sha256"):
                        problems.append(f"artifact hash changed: {name}")
                    if actual["size"] != row.get("size"):
                        problems.append(f"artifact size changed: {name}")
                for name in sorted(set(on_disk) - set(listed)):
                    problems.append(f"unsealed artifact in dist: {name}")
                if args.require_ext and not any(
                        n.lower().endswith(args.require_ext.lower()) for n in listed):
                    problems.append(f"no {args.require_ext} artifact in the manifest")
    if problems:
        print("BLOCKED: the release is not authorized")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"PASS: release gate is valid ({gate.get('version')} @ {gate.get('commit')[:12]})")
    print(f"  suites: {', '.join(s.get('id', '?') for s in gate.get('suites') or [])}")
    if manifest:
        print(f"  artifacts: {len(manifest.get('artifacts') or [])} sealed")
    return 0


def _fixture_gate(gates_sha: str, commit: str) -> dict:
    suites = []
    for suite in REQUIRED_SUITES:
        embedded = {
            "schema": 1,
            "kind": "quality_gate",
            "created_at": utc_now(),
            "project": "eos",
            "suite": suite,
            "verdict": "PASS",
            "baseline": {"label": "fixture", "path": "fixture/HASHES.txt",
                         "sha256": "B" * 64, "entries": 42},
            "project_path": ".",
            "commands": [{
                "name": f"{suite}_case",
                "argv": ["python", "-c", "pass"],
                "exit_code": 0,
                "timed_out": False,
                "duration_seconds": 0.01,
                "output_sha256": "C" * 64,
                "output_bytes": 12,
                "required": True,
            }],
            "failures": [],
            "optional_failures": [],
            "log": {"path": "fixture/gate.log", "sha256": "D" * 64, "bytes": 12},
            "runtime": {"python": "fixture"},
            "limits": {},
        }
        suites.append({"id": suite, "verdict": "PASS",
                       "artifact_sha256": canonical_sha(embedded), "artifact": embedded})
    return {
        "schema": SCHEMA,
        "kind": KIND,
        "created_at": utc_now(),
        "version": "0.0.0-test",
        "commit": commit,
        "ref": "refs/heads/fixture",
        "repository": "fixture/repo",
        "workflow_run": {"run_id": "1", "run_attempt": "1",
                         "workflow": "fixture", "job": "gate"},
        "gates_sha256": gates_sha,
        "baseline": {"label": "baseline_fixture", "kind": "git_commit_tree",
                     "sha256": "A" * 64, "entries": 42},
        "workspace_clean_before": True,
        "workspace_clean_after": True,
        "required_suites": list(REQUIRED_SUITES),
        "suites": suites,
        "failures": [],
        "tree_problems": [],
        "verdict": "PASS",
    }


def selftest() -> int:
    """A forged PASS must never verify. Every case mutates one real field."""
    import copy

    tmp = tempfile.mkdtemp(prefix="eos_release_gate_")
    gates = os.path.join(tmp, "GATES.json")
    write_json(gates, {"version": 2, "projects": {}})
    gates_sha = sha256_file(gates)
    commit = "a" * 40
    valid = _fixture_gate(gates_sha, commit)

    failures = []

    def expect_pass(name, gate, **kwargs):
        kwargs.setdefault("check_env", False)
        problems = validate_gate(gate, gates_path=gates, **kwargs)
        if problems:
            failures.append(f"{name}: valid record rejected: {problems[0]}")

    def expect_block(name, gate, needle, **kwargs):
        kwargs.setdefault("check_env", False)
        problems = validate_gate(gate, gates_path=gates, **kwargs)
        if not problems:
            failures.append(f"{name}: forged record was accepted")
            return
        if needle and not any(needle in p for p in problems):
            failures.append(f"{name}: rejected for the wrong reason: {problems[0]}")

    expect_pass("valid", valid)
    expect_pass("valid_with_commit", valid, expected_commit=commit)

    forged = copy.deepcopy(valid); forged["verdict"] = "FAIL"
    expect_block("verdict_fail", forged, "verdict is FAIL")

    forged = copy.deepcopy(valid); forged["suites"] = forged["suites"][:-1]
    expect_block("missing_suite", forged, "required suite")

    forged = copy.deepcopy(valid); forged["commit"] = "b" * 40
    expect_block("wrong_commit", forged, "commit mismatch", expected_commit=commit)

    forged = copy.deepcopy(valid)
    forged["suites"][0]["artifact"]["commands"][0]["exit_code"] = 1
    expect_block("failed_command", forged, "required command")

    forged = copy.deepcopy(valid)
    forged["suites"][0]["artifact"]["commands"][0]["output_sha256"] = ""
    expect_block("no_output_hash", forged, "no output hash")

    forged = copy.deepcopy(valid)
    forged["suites"][0]["artifact"]["commands"] = []
    expect_block("no_commands", forged, "no commands")

    forged = copy.deepcopy(valid)
    forged["suites"][0]["artifact_sha256"] = "0" * 64
    expect_block("tampered_suite", forged, "does not match")

    forged = copy.deepcopy(valid); forged["gates_sha256"] = "0" * 64
    expect_block("gates_changed", forged, "GATES.json changed")

    forged = copy.deepcopy(valid); forged["workspace_clean_after"] = False
    expect_block("dirty_tree", forged, "changed during")

    forged = copy.deepcopy(valid); forged["baseline"]["entries"] = 0
    expect_block("empty_baseline", forged, "baseline covers")

    forged = copy.deepcopy(valid); forged["workflow_run"]["run_id"] = "999"
    os.environ["GITHUB_RUN_ID"] = "123"
    try:
        expect_block("run_binding", forged, "run_id", check_env=True)
    finally:
        os.environ.pop("GITHUB_RUN_ID", None)

    forged = copy.deepcopy(valid); forged["repository"] = "other/repo"
    os.environ["GITHUB_REPOSITORY"] = "fixture/repo"
    try:
        expect_block("repository_binding", forged, "repository", check_env=True)
        expect_pass("repository_binding_valid", valid, check_env=True)
    finally:
        os.environ.pop("GITHUB_REPOSITORY", None)

    # A hand-written file that claims PASS but carries no evidence at all.
    expect_block("handwritten_pass", {
        "schema": SCHEMA, "kind": KIND, "created_at": utc_now(),
        "version": "9.9.9", "commit": commit, "verdict": "PASS",
        "workspace_clean_before": True, "workspace_clean_after": True,
        "gates_sha256": gates_sha,
        "baseline": {"kind": "git_commit_tree", "sha256": "A" * 64, "entries": 42},
        "suites": [],
    }, "required suite")

    # ---------------------------------------------------------- sealing
    dist = os.path.join(tmp, "dist")
    os.makedirs(dist)
    exe = os.path.join(dist, "EngineeringOS-Setup-0.0.0-test.exe")
    with open(exe, "wb") as fh:
        fh.write(b"installer payload")
    with open(os.path.join(dist, "SHA256SUMS.txt"), "w", encoding="utf-8") as fh:
        fh.write("sums\n")
    gate_path = os.path.join(tmp, "release-gate.json")
    write_json(gate_path, valid)

    manifest = make_manifest(gate_path, dist, require_ext=".exe")
    if len(manifest.get("artifacts") or []) != 2:
        failures.append("seal: expected two artifacts")
    if manifest.get("gate_sha256") != sha256_file(gate_path):
        failures.append("seal: gate hash not recorded")
    manifest_path = os.path.join(dist, "release-manifest.json")
    write_json(manifest_path, manifest)

    verifier = argparse.Namespace(gate=gate_path, manifest=manifest_path, dist=dist,
                                   commit=None, max_age_min=None, require_ext=".exe")
    # The production verifier also checks the engine's own GATES.json, which is
    # the real registry; for the selftest the fixture file stands in for it.
    # The runtime-workflow env vars would bind the fixture to this CI run, so
    # they are masked while the fixture is exercised.
    old_engine = globals()["ENGINE"]
    old_env = {k: os.environ.pop(k) for k in (
        "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_REPOSITORY") if k in os.environ}
    globals()["ENGINE"] = tmp
    os.makedirs(os.path.join(tmp, "registry"), exist_ok=True)
    with open(os.path.join(tmp, "registry", "GATES.json"), "wb") as fh:
        fh.write(open(gates, "rb").read())
    import contextlib
    import io
    import shutil

    def verify_quiet():
        with contextlib.redirect_stdout(io.StringIO()):
            return cmd_verify(verifier)

    try:
        checks = 0
        checks += 1
        if verify_quiet() != 0:
            failures.append("seal: a valid manifest did not verify")
        # The workflow writes RELEASE-MANIFEST.json; the default path is
        # lowercase. Neither name may be treated as an unsealed package.
        upper_manifest = os.path.join(dist, "RELEASE-MANIFEST.json")
        write_json(upper_manifest, manifest)
        verifier.manifest = upper_manifest
        checks += 1
        if verify_quiet() != 0:
            failures.append("seal: an uppercase manifest name did not verify")
        verifier.manifest = manifest_path
        os.remove(upper_manifest)
        with open(exe, "ab") as fh:
            fh.write(b"tampered")
        checks += 1
        if verify_quiet() == 0:
            failures.append("seal: a tampered package was accepted")
        with open(exe, "wb") as fh:
            fh.write(b"installer payload")
        bad = copy.deepcopy(manifest); bad["gate_sha256"] = "0" * 64
        write_json(manifest_path, bad)
        checks += 1
        if verify_quiet() == 0:
            failures.append("seal: a manifest for another gate was accepted")
        write_json(manifest_path, manifest)
        bad = copy.deepcopy(manifest); bad["artifacts"] = []
        write_json(manifest_path, bad)
        checks += 1
        if verify_quiet() == 0:
            failures.append("seal: an empty manifest was accepted")
        write_json(manifest_path, manifest)
        forged_gate = copy.deepcopy(valid); forged_gate["commit"] = "b" * 40
        write_json(gate_path, forged_gate)
        checks += 1
        if verify_quiet() == 0:
            failures.append("seal: a manifest bound to another commit was accepted")
        write_json(gate_path, valid)
    finally:
        globals()["ENGINE"] = old_engine
        os.environ.update(old_env)
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("FAIL release-gate selftest")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"PASS release-gate selftest: 16 gate cases + {checks} seal/binding cases handled correctly")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="release_gate.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--engine", default=ENGINE)
    r.add_argument("--out", required=True)
    r.add_argument("--version")
    r.add_argument("--commit")
    r.add_argument("--ref")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("seal")
    s.add_argument("--gate", required=True)
    s.add_argument("--dist", required=True)
    s.add_argument("--out")
    s.add_argument("--require-ext", default=".exe")
    s.set_defaults(func=cmd_seal)

    v = sub.add_parser("verify")
    v.add_argument("--gate", required=True)
    v.add_argument("--manifest")
    v.add_argument("--dist")
    v.add_argument("--commit")
    v.add_argument("--max-age-min", type=int, default=MAX_AGE_MIN)
    v.add_argument("--require-ext", default="")
    v.set_defaults(func=cmd_verify)

    t = sub.add_parser("selftest")
    t.set_defaults(func=lambda _a: selftest())

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
