#!/usr/bin/env python3
"""Challenge ledger: typed objections with evidence, not free-text opinions.

A multi-agent team that only produces prose cannot be audited. Every finding is
a record:

    {id, role, severity, claim, evidence[], confidence, falsifiable_test,
     status, resolution}

`blocker` findings keep a decision out of APPROVED_FOR_EDIT and CLOSED. A
rejected finding is still recorded, with the reason and the evidence that
refuted it. Deleting a finding is not an operation this tool supports.

Commands:
  open    --project <id> --decision <edr-id> [--task <text>]
  add     --id <ledger> --role <role> --severity blocker|major|minor|info \
          --claim <text> [--evidence <path>]... [--test <text>] [--confidence HIGH|MEDIUM|LOW]
  resolve --id <ledger> --finding <f-id> --status resolved|rejected \
          --reason <text> [--by <role>] [--evidence <path>]...
  status  --id <ledger> [--json]
  check   --id <ledger>            exit 1 when an open blocker remains
  selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

SCHEMA = 1
KIND = "challenge_ledger"
SEVERITIES = ("blocker", "major", "minor", "info")
STATUSES = ("open", "resolved", "rejected")
CONFIDENCE = ("HIGH", "MEDIUM", "LOW")


class LedgerError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def ledger_hash(record: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in record.items() if k != "record_sha256"})).hexdigest().upper()


def root(workspace: str) -> str:
    return os.path.join(workspace, ".agents", "evidence", "challenges")


def safe_project(project: str) -> str:
    """A project id is one path segment, never a traversal."""
    if not project or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", project):
        raise LedgerError(f"invalid project id: {project!r}")
    if project in (".", ".."):
        raise LedgerError(f"invalid project id: {project!r}")
    return project


def path_for(workspace: str, project: str, ledger_id: str) -> str:
    project = safe_project(project)
    if not ledger_id.startswith("chl-") or not all(c.isalnum() or c in "-_." for c in ledger_id):
        raise LedgerError(f"invalid ledger id: {ledger_id!r}")
    return os.path.join(root(workspace), project, ledger_id + ".json")


def write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    os.replace(tmp, path)


def load(workspace: str, project: str, ledger_id: str) -> dict:
    path = path_for(workspace, project, ledger_id)
    if not os.path.isfile(path):
        raise LedgerError(f"ledger not found: {ledger_id}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save(workspace: str, record: dict) -> str:
    record["updated_at"] = utc_now()
    record["open_blockers"] = count_open_blockers(record)
    record["record_sha256"] = ledger_hash(record)
    path = path_for(workspace, record["project"], record["id"])
    write_json(path, record)
    return path


def count_open_blockers(record: dict) -> int:
    return len([f for f in record.get("findings", [])
                if f.get("severity") == "blocker" and f.get("status") == "open"])


def integrity(record: dict) -> list:
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("wrong schema")
    if record.get("kind") != KIND:
        problems.append("wrong kind")
    if not record.get("record_sha256"):
        problems.append("not sealed")
    elif ledger_hash(record) != record["record_sha256"]:
        problems.append("hash mismatch (edited outside this tool)")
    if count_open_blockers(record) != int(record.get("open_blockers", -1)):
        problems.append("open_blockers is stale")
    return problems


def cmd_open(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    ledger_id = "chl-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(2).hex()
    record = {
        "schema": SCHEMA, "kind": KIND, "id": ledger_id,
        "project": args.project, "decision": args.decision, "task": args.task,
        "created_at": utc_now(), "updated_at": utc_now(),
        "findings": [], "open_blockers": 0, "record_sha256": "",
    }
    save(workspace, record)
    print(f"OPEN: {ledger_id} ({args.project}, decision {args.decision})")
    print(f"  ledger: {ledger_path_display(workspace, record)}")
    return 0


def ledger_path_display(workspace: str, record: dict) -> str:
    return ep.store_path(path_for(workspace, record["project"], record["id"]), workspace)


def cmd_add(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    problems = integrity(record)
    if problems:
        print("BLOCKED: ledger is not trustworthy")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if args.severity not in SEVERITIES:
        print(f"BLOCKED: severity must be one of {', '.join(SEVERITIES)}")
        return 2
    if not (args.claim or "").strip():
        print("BLOCKED: a finding needs a claim")
        return 2
    if args.severity == "blocker" and not (args.test or "").strip():
        print("BLOCKED: a blocker needs a falsifiable test; otherwise it is an opinion")
        return 2
    finding = {
        "id": "f-" + str(len(record["findings"]) + 1).zfill(3),
        "role": args.role, "severity": args.severity,
        "claim": args.claim.strip()[:4000],
        "evidence": list(args.evidence or []),
        "confidence": args.confidence,
        "falsifiable_test": (args.test or "").strip()[:2000],
        "affected_components": list(args.component or []),
        "status": "open", "resolution": None,
        "created_at": utc_now(),
    }
    record["findings"].append(finding)
    save(workspace, record)
    print(f"ADD: {finding['id']} [{finding['severity']}] {finding['role']}")
    print(f"  open blockers: {record['open_blockers']}")
    return 0


def cmd_resolve(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    problems = integrity(record)
    if problems:
        print("BLOCKED: ledger is not trustworthy")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if args.status not in ("resolved", "rejected"):
        print("BLOCKED: status must be resolved or rejected")
        return 2
    if not (args.reason or "").strip():
        print("BLOCKED: a resolution needs a reason")
        return 2
    for finding in record["findings"]:
        if finding["id"] == args.finding:
            if finding["status"] != "open":
                print(f"BLOCKED: {finding['id']} is already {finding['status']}")
                return 1
            finding["status"] = args.status
            finding["resolution"] = {
                "at": utc_now(), "by": args.by or "unknown",
                "reason": args.reason.strip()[:2000],
                "evidence": list(args.evidence or []),
            }
            save(workspace, record)
            print(f"{args.status.upper()}: {finding['id']}  open blockers: {record['open_blockers']}")
            return 0
    print(f"BLOCKED: finding not found: {args.finding}")
    return 1


def cmd_status(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    problems = integrity(record)
    result = {
        "id": record["id"], "decision": record.get("decision"),
        "open_blockers": record.get("open_blockers"),
        "findings": len(record.get("findings", [])),
        "trusted": not problems, "problems": problems,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
        return 0 if not problems else 1
    print(f"{record['id']}  decision={record.get('decision')}  findings={result['findings']}  "
          f"open_blockers={result['open_blockers']}  trusted={result['trusted']}")
    for finding in record.get("findings", []):
        print(f"  {finding['id']} [{finding['severity']}/{finding['status']}] {finding['role']}: "
              f"{finding['claim'][:100]}")
    for problem in problems:
        print(f"  ! {problem}")
    return 0 if not problems else 1


def cmd_check(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    problems = integrity(record)
    blockers = count_open_blockers(record)
    if problems or blockers:
        print("BLOCKED: challenge ledger is not clean")
        for problem in problems:
            print(f"  - {problem}")
        for finding in record.get("findings", []):
            if finding.get("severity") == "blocker" and finding.get("status") == "open":
                print(f"  - open blocker {finding['id']}: {finding['claim'][:120]}")
        return 1
    print(f"PASS: no open blocker in {record['id']}")
    return 0


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def workspace() -> str:
        root_dir = tempfile.mkdtemp(prefix="eos-chl-")
        os.makedirs(os.path.join(root_dir, ".agents", "evidence"), exist_ok=True)
        return root_dir

    def open_ledger(root_dir) -> str:
        args = argparse.Namespace(project="eos", decision="edr-test-1", task="test",
                                  workspace=root_dir)
        assert cmd_open(args) == 0
        directory = os.path.join(root_dir, ".agents", "evidence", "challenges", "eos")
        name = sorted(os.listdir(directory))[-1]
        return name[:-5]

    def add(root_dir, ledger_id, severity="blocker", claim="x is wrong", test="a test"):
        args = argparse.Namespace(project="eos", id=ledger_id, role="logic-reviewer",
                                  severity=severity, claim=claim, evidence=["a.py:1"],
                                  confidence="HIGH", test=test, component=["a"], workspace=root_dir)
        return cmd_add(args)

    @case("blocker without a falsifiable test is refused")
    def _():
        root_dir = workspace()
        ledger = open_ledger(root_dir)
        assert add(root_dir, ledger, test="") == 2
        return "refused"

    @case("open blocker prevents check")
    def _():
        root_dir = workspace()
        ledger = open_ledger(root_dir)
        assert add(root_dir, ledger) == 0
        assert cmd_check(argparse.Namespace(project="eos", id=ledger, workspace=root_dir)) == 1
        return ledger

    @case("resolved blocker clears check")
    def _():
        root_dir = workspace()
        ledger = open_ledger(root_dir)
        add(root_dir, ledger)
        args = argparse.Namespace(project="eos", id=ledger, finding="f-001",
                                  status="resolved", reason="proven wrong by test",
                                  by="verifier", evidence=["test.py"], workspace=root_dir)
        assert cmd_resolve(args) == 0
        assert cmd_check(argparse.Namespace(project="eos", id=ledger, workspace=root_dir)) == 0
        return "cleared"

    @case("tampered ledger is refused")
    def _():
        root_dir = workspace()
        ledger = open_ledger(root_dir)
        path = path_for(root_dir, "eos", ledger)
        data = json.load(open(path, encoding="utf-8"))
        data["findings"].append({"id": "f-999", "severity": "info", "status": "open", "claim": "injected"})
        write_json(path, data)
        assert cmd_check(argparse.Namespace(project="eos", id=ledger, workspace=root_dir)) == 1
        return "hash mismatch"

    @case("project id cannot escape the challenges directory")
    def _():
        root_dir = workspace()
        for bad in ("../escape", "..", ".", "a/b", "a\\b"):
            try:
                path_for(root_dir, bad, "chl-test-0000")
            except LedgerError:
                continue
            raise AssertionError(f"accepted unsafe project id: {bad!r}")
        return "rejected"

    @case("deleting a finding is not a supported operation")
    def _():
        root_dir = workspace()
        ledger = open_ledger(root_dir)
        add(root_dir, ledger)
        assert not hasattr(sys.modules[__name__], "cmd_delete")
        return "no delete command"

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
    parser = argparse.ArgumentParser(prog="challenge.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")
    common.add_argument("--project", default="eos")

    p = sub.add_parser("open", parents=[common])
    p.add_argument("--decision", required=True)
    p.add_argument("--task", default="")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("add", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--severity", required=True)
    p.add_argument("--claim", required=True)
    p.add_argument("--evidence", action="append", default=[])
    p.add_argument("--test", default="")
    p.add_argument("--confidence", default="MEDIUM", choices=CONFIDENCE)
    p.add_argument("--component", action="append", default=[])
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("resolve", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--finding", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--by", default="")
    p.add_argument("--evidence", action="append", default=[])
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("status", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", parents=[common])
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except LedgerError as exc:
        print(f"BLOCKED: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
