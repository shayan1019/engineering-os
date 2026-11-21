#!/usr/bin/env python3
"""P5-C executable runner.

Mechanical half (no model):

  validate   copies each broken/fixed fixture to an isolated directory, keeps
             the oracle outside the repository, checks that the broken version
             fails and the fixed version passes, and measures timeout and
             cancellation behaviour.

Model half:

  run        is BLOCKED unless a provider is configured. No result is invented.

Every validation artifact is sealed and carries the host, source commit and a
`model_benchmark` verdict that is separate from the mechanical verdict.

Commands:
  validate [--json] [--workspace <dir>] [--out <file>]
  run --model <provider/model> [--json]
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
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

SCHEMA = 1
KIND = "p5c_validation"
VERSION = "2026-11-21.1"

CASES = [
    {
        "id": "math_area",
        "category": "mathematics",
        "broken": {"formula.py": "def area(r):\n    return 2 * 3.14 * r\n"},
        "fixed": {"formula.py": "import math\n\ndef area(r):\n    return math.pi * r * r\n"},
        "oracle": (
            "import math\nfrom formula import area\n"
            "checks = [abs(area(1) - math.pi) < 1e-6,"
            " abs(area(2) - 4 * math.pi) < 1e-6, abs(area(0)) < 1e-9]\n"
            "print('PASS' if all(checks) else 'FAIL', checks)\n"
            "raise SystemExit(0 if all(checks) else 1)\n"),
    },
    {
        "id": "auth_role",
        "category": "security",
        "broken": {"auth.py": "def can_access(user, resource):\n    return True\n"},
        "fixed": {"auth.py": 'def can_access(user, resource):\n    return user.get("role") == "admin"\n'},
        "oracle": (
            "from auth import can_access\n"
            "checks = [can_access({'role': 'admin'}, 'doc') is True,"
            " can_access({'role': 'viewer'}, 'doc') is False,"
            " can_access({}, 'doc') is False]\n"
            "print('PASS' if all(checks) else 'FAIL', checks)\n"
            "raise SystemExit(0 if all(checks) else 1)\n"),
    },
    {
        "id": "api_contract",
        "category": "backend",
        "broken": {"api.py": 'def get_user():\n    return {"name": "x"}\n'},
        "fixed": {"api.py": 'def get_user():\n    return {"id": 1, "name": "x"}\n'},
        "oracle": (
            "from api import get_user\n"
            "value = get_user()\n"
            "ok = isinstance(value, dict) and 'id' in value\n"
            "print('PASS' if ok else 'FAIL', value)\n"
            "raise SystemExit(0 if ok else 1)\n"),
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seal(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def source_meta() -> dict:
    root = os.path.dirname(ep.ENGINE_ROOT)

    def run(args):
        try:
            return subprocess.run(["git", "-C", root, *args], capture_output=True,
                                  text=True, timeout=15).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {"commit": run(["rev-parse", "HEAD"]),
            "branch": run(["rev-parse", "--abbrev-ref", "HEAD"])}


def write_repo(directory: str, files: dict) -> None:
    os.makedirs(directory, exist_ok=True)
    for name, text in files.items():
        path = os.path.join(directory, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)


def run_oracle(repo: str, oracle: str, timeout: float) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = repo
    begin = time.time()
    try:
        child = subprocess.run([sys.executable, oracle], cwd=os.path.dirname(oracle),
                               env=env, capture_output=True, text=True,
                               timeout=timeout)
        return {"exit": child.returncode, "timed_out": False,
                "output": (child.stdout + child.stderr).strip()[-400:],
                "duration_seconds": round(time.time() - begin, 3)}
    except subprocess.TimeoutExpired as exc:
        return {"exit": None, "timed_out": True,
                "output": str(exc)[:400],
                "duration_seconds": round(time.time() - begin, 3)}


def case_result(case: dict, timeout: float) -> dict:
    root = tempfile.mkdtemp(prefix=f"eos-p5c-{case['id']}-")
    oracle_dir = os.path.join(root, "oracle")
    os.makedirs(oracle_dir)
    oracle = os.path.join(oracle_dir, "oracle.py")
    with open(oracle, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(case["oracle"])
    results = {}
    for variant in ("broken", "fixed"):
        repo = os.path.join(root, variant)
        write_repo(repo, case[variant])
        results[variant] = run_oracle(repo, oracle, timeout)
        results[variant]["oracle_in_repo"] = os.path.commonpath(
            [os.path.abspath(repo), os.path.abspath(oracle)]) == os.path.abspath(repo)
    broken, fixed = results["broken"], results["fixed"]
    ok = (broken["timed_out"] is False and broken["exit"] not in (0, None)
          and fixed["timed_out"] is False and fixed["exit"] == 0
          and not broken["oracle_in_repo"] and not fixed["oracle_in_repo"])
    shutil.rmtree(root, ignore_errors=True)
    return {"id": case["id"], "category": case["category"], "ok": ok,
            "broken": broken, "fixed": fixed,
            "expected": "broken fails, fixed passes, oracle is outside the repository"}


def timeout_test() -> dict:
    root = tempfile.mkdtemp(prefix="eos-p5c-timeout-")
    repo = os.path.join(root, "repo")
    write_repo(repo, {"slow.py": "import time\n\ndef slow():\n    time.sleep(5)\n"})
    oracle_dir = os.path.join(root, "oracle")
    os.makedirs(oracle_dir)
    oracle = os.path.join(oracle_dir, "oracle.py")
    with open(oracle, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("import slow\nslow.slow()\n")
    result = run_oracle(repo, oracle, 0.4)
    shutil.rmtree(root, ignore_errors=True)
    return {"id": "timeout_cancel", "ok": result["timed_out"] is True,
            "observed": result,
            "expected": "a slow oracle is cancelled and reported as timeout"}


def validate() -> dict:
    started = time.time()
    cases = [case_result(case, 30) for case in CASES]
    timeout = timeout_test()
    mechanical = [case["ok"] for case in cases] + [timeout["ok"]]
    passed = sum(1 for item in mechanical if item)
    value = {
        "schema": SCHEMA, "kind": KIND, "created_at": utc_now(),
        "scenario_version": VERSION,
        "source": source_meta(),
        "host": {"platform": platform.platform(),
                 "python": platform.python_version()},
        "cases": cases, "timeout": timeout,
        "mechanical": {"total": len(mechanical), "passed": passed,
                       "failed": len(mechanical) - passed,
                       "verdict": "PASS" if passed == len(mechanical) else "FAIL",
                       "duration_seconds": round(time.time() - started, 3),
                       "model_calls": 0},
        "model_benchmark": {"verdict": "BLOCKED",
                            "reason": "no provider is configured; Fable/Astra returned 402 on 2026-11-20",
                            "result_claimed": False},
        "provenance": {"signed": False, "note": "unsigned validation artifact"},
    }
    value["artifact_sha256"] = seal(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(prog="p5c_runner.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate")
    p.add_argument("--workspace")
    p.add_argument("--out", default="")
    p.add_argument("--json", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--model", required=True)
    r.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "run":
        value = {"schema": SCHEMA, "kind": "p5c_model_run",
                 "created_at": utc_now(), "model": args.model,
                 "verdict": "BLOCKED", "reason": "no provider configured",
                 "result_claimed": False}
        print(json.dumps(value, ensure_ascii=True) if args.json else
              f"BLOCKED: {args.model}: no provider configured; run 'validate' for the mechanical runner")
        return 3
    value = validate()
    workspace = ep.resolve_workspace(args.workspace)
    out = args.out or os.path.join(workspace, ".agents", "evidence", "p5c",
                                   f"validate-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    if args.json:
        print(json.dumps(value, ensure_ascii=True))
    else:
        card = value["mechanical"]
        print(f"P5C: {card['verdict']} {card['passed']}/{card['total']} mechanical  "
              f"model=BLOCKED  {card['duration_seconds']}s")
        for case in value["cases"]:
            print(f"  {'PASS' if case['ok'] else 'FAIL'}  {case['id']}")
        print(f"  {'PASS' if value['timeout']['ok'] else 'FAIL'}  timeout_cancel")
        print(f"  artifact: {out}")
    return 0 if value["mechanical"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
