#!/usr/bin/env python3
"""Run pytest and compare the failures with a stored baseline.

The problem this solves
-----------------------
BigHero has 17 failing tests that pre-date EOS. A gate that returns FAIL on
every run cannot tell "you broke three more" from "the same seventeen are still
broken", so nobody reads it. This wrapper makes the gate differential:

    no new failure + no disappeared test  -> PASS (known failures are recorded)
    a failure not in the baseline         -> FAIL (new regression)
    a baseline test no longer exists      -> FAIL (coverage lost or renamed)
    no baseline / pytest did not run      -> BLOCKED (never a fake green)

The baseline is a JSON file with the environment identity that produced it. It
is never generated implicitly: `--update-baseline` is an explicit, reviewable
act. A missing baseline is BLOCKED, not empty.

Usage:
  pytest_delta.py --project bighero --suite unit \
      --baseline .agents/registry/failures/bighero-unit.json -- \
      python_ai/tests -q --no-header -p no:cacheprovider

  pytest_delta.py ... --update-baseline      # write the current result
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SCHEMA = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    os.replace(tmp, path)


def pytest_version() -> str:
    try:
        import pytest  # noqa: F401
        return getattr(pytest, "__version__", "unknown")
    except ImportError:
        return "missing"


def identity() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "pytest": pytest_version(),
    }


def parse_junit(path: str) -> dict:
    tree = ET.parse(path)
    root = tree.getroot()
    cases = []
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        file = (case.get("file") or "").replace("\\", "/")
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        status = "passed"
        detail = ""
        if failure is not None or error is not None:
            node = failure if failure is not None else error
            status = "failed"
            detail = (node.get("message") or "").strip().splitlines()[0][:300] if node is not None else ""
        elif skipped is not None:
            status = "skipped"
        cases.append({
            "id": f"{classname}::{name}" if classname else name,
            "file": file,
            "name": name,
            "status": status,
            "detail": detail,
        })
    return {"cases": cases}


def run_pytest(args: list, runner: list | None = None) -> tuple:
    xml_path = os.path.join(tempfile.mkdtemp(prefix="eos-junit-"), "report.xml")
    argv = [*(runner or [sys.executable, "-m", "pytest"]), *args, f"--junitxml={xml_path}"]
    started = datetime.now(timezone.utc)
    proc = subprocess.run(argv, capture_output=True, text=True)
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    return proc, xml_path, duration, argv


def summarise(cases: list) -> dict:
    failed = [c for c in cases if c["status"] == "failed"]
    return {
        "total": len(cases),
        "failed": len(failed),
        "skipped": len([c for c in cases if c["status"] == "skipped"]),
        "passed": len([c for c in cases if c["status"] == "passed"]),
        "failure_ids": sorted(c["id"] for c in failed),
    }


def selftest() -> int:
    root = tempfile.mkdtemp(prefix="eos-delta-")
    test_file = os.path.join(root, "test_sample.py")
    baseline = os.path.join(root, "baseline.json")
    with open(test_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("def test_ok():\n    assert True\n\ndef test_known():\n    assert False\n")

    def run(update: bool = False) -> int:
        argv = [sys.executable, os.path.abspath(__file__), "--project", "eos", "--suite", "unit",
                "--baseline", baseline, "--allow-identity-drift",
                "--runner", json.dumps([sys.executable, fake])]
        if update:
            argv.append("--update-baseline")
        argv.extend(["--", test_file, "-q", "--no-header", "-p", "no:cacheprovider"])
        return subprocess.run(argv, capture_output=True, text=True).returncode

    state_path = os.path.join(root, "state.json")
    fake = os.path.join(root, "fake_pytest.py")
    with open(fake, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "import json, sys\n"
            "import xml.etree.ElementTree as ET\n"
            "xml = [a for a in sys.argv if a.startswith('--junitxml=')][0].split('=', 1)[1]\n"
            "state = json.load(open(r'" + state_path.replace('\\', '\\\\') + "', encoding='utf-8'))\n"
            "root = ET.Element('testsuites')\n"
            "suite = ET.SubElement(root, 'testsuite')\n"
            "for name in state['tests']:\n"
            "    case = ET.SubElement(suite, 'testcase', {'classname': 'test_sample', 'name': name, 'file': 'test_sample.py'})\n"
            "    if name in state['fail']:\n"
            "        ET.SubElement(case, 'failure', {'message': 'assert False'})\n"
            "ET.ElementTree(root).write(xml, encoding='utf-8', xml_declaration=True)\n"
            "sys.exit(1 if state['fail'] else 0)\n"
        )

    def set_state(tests, fail) -> None:
        with open(state_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"tests": tests, "fail": fail}, handle)

    set_state(["test_ok", "test_known"], ["test_known"])
    cases = []
    rc = run(update=True)
    cases.append(("baseline is written explicitly", rc == 0))
    cases.append(("known failure is PASS, not a fake green", run() == 0))
    set_state(["test_ok", "test_known", "test_new_regression"], ["test_known", "test_new_regression"])
    cases.append(("a new failure is FAIL", run() == 1))
    set_state(["test_ok"], [])
    cases.append(("a disappeared baseline test is FAIL", run() == 1))
    missing = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--project", "eos", "--suite", "unit",
         "--baseline", os.path.join(root, "absent.json"), "--", test_file, "-q"],
        capture_output=True, text=True).returncode
    cases.append(("a missing baseline is BLOCKED", missing == 2))

    passed = 0
    for name, ok in cases:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        passed += 1 if ok else 0
    print(f"\n{passed}/{len(cases)} cases passed")
    return 0 if passed == len(cases) else 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        return selftest()
    parser = argparse.ArgumentParser(prog="pytest_delta.py")
    parser.add_argument("--project", required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--report", default="")
    parser.add_argument("--runner", default="", help="override the pytest command (JSON array)")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--allow-identity-drift", action="store_true")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    pytest_args = args.pytest_args
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    if not pytest_args:
        print("BLOCKED: no pytest arguments after --")
        return 2

    baseline = None
    if os.path.isfile(args.baseline):
        try:
            baseline = read_json(args.baseline)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"BLOCKED: baseline unreadable: {exc}")
            return 2
    elif not args.update_baseline:
        print(f"BLOCKED: no baseline at {args.baseline}")
        print("  a differential gate without a baseline cannot tell a regression from a known failure")
        print("  create one explicitly: add --update-baseline, review the diff, commit the file")
        return 2

    identity_now = identity()
    if baseline and not args.update_baseline:
        drift = {k: (baseline.get("identity", {}).get(k), identity_now.get(k))
                 for k in identity_now if baseline.get("identity", {}).get(k) != identity_now.get(k)}
        if drift and not args.allow_identity_drift:
            print("BLOCKED: environment drift; the baseline was measured elsewhere")
            for key, (was, now) in drift.items():
                print(f"  - {key}: baseline={was} now={now}")
            print("  re-measure with --update-baseline or pass --allow-identity-drift")
            return 2

    runner = None
    if args.runner:
        try:
            runner = json.loads(args.runner)
            if not isinstance(runner, list) or not runner:
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            print("BLOCKED: --runner must be a non-empty JSON array")
            return 2
    proc, xml_path, duration, argv = run_pytest(pytest_args, runner)
    print(f"$ {' '.join(argv)}  -> rc={proc.returncode} {duration:.1f}s")
    if proc.stdout.strip():
        print(proc.stdout.strip()[-4000:])
    if proc.stderr.strip():
        print(proc.stderr.strip()[-2000:])

    if not os.path.isfile(xml_path):
        print("BLOCKED: pytest produced no JUnit report (collection or startup failure)")
        return 2
    try:
        parsed = parse_junit(xml_path)
    except (ET.ParseError, OSError) as exc:
        print(f"BLOCKED: JUnit report unreadable: {exc}")
        return 2
    cases = parsed["cases"]
    if not cases:
        print("BLOCKED: pytest collected zero tests")
        return 2

    summary = summarise(cases)
    record = {
        "schema": SCHEMA,
        "project": args.project,
        "suite": args.suite,
        "generated_at": utc_now(),
        "identity": identity_now,
        "duration_seconds": round(duration, 1),
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "skipped": summary["skipped"],
        "failures": summary["failure_ids"],
        "failure_details": {c["id"]: c["detail"] for c in cases if c["status"] == "failed"},
    }

    if args.update_baseline:
        write_json(args.baseline, record)
        print(f"BASELINE: wrote {summary['failed']} known failure(s) to {args.baseline}")
        print(f"  tests={summary['total']} passed={summary['passed']} skipped={summary['skipped']}")
        return 0

    known = set(baseline.get("failures") or [])
    current = set(summary["failure_ids"])
    all_current = {c["id"] for c in cases}
    new_failures = sorted(current - known)
    still_failing = sorted(current & known)
    disappeared = sorted(known - all_current)
    fixed = sorted(known - current - set(disappeared))

    report = {
        **record,
        "baseline": {
            "path": args.baseline,
            "generated_at": baseline.get("generated_at"),
            "known_failures": len(known),
        },
        "delta": {
            "new_failures": [{"id": item, "detail": record["failure_details"].get(item, "")}
                             for item in new_failures],
            "still_failing": still_failing,
            "fixed": fixed,
            "disappeared_tests": disappeared,
        },
        "verdict": "PASS" if not new_failures and not disappeared else "FAIL",
    }
    report_path = args.report or os.path.join(
        os.path.dirname(os.path.abspath(args.baseline)), "last-delta.json")
    write_json(report_path, report)

    print("")
    print(f"{report['verdict']}: {args.project}/{args.suite} "
          f"({summary['total']} tests, {len(still_failing)} known failure(s))")
    if new_failures:
        print(f"  NEW FAILURES ({len(new_failures)}):")
        for item in new_failures:
            print(f"    - {item}  {record['failure_details'].get(item, '')}")
    if disappeared:
        print(f"  DISAPPEARED TESTS ({len(disappeared)}):")
        for item in disappeared[:40]:
            print(f"    - {item}")
    if fixed:
        print(f"  fixed since baseline: {len(fixed)} (update the baseline after review)")
    print(f"  delta report: {report_path}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
