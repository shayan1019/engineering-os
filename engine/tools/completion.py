#!/usr/bin/env python3
"""Completion gate: a task is not done because the model says so.

PASS requires all of:

  1. a CLOSED Engineering Decision Record for this project
  2. a gate artifact that still hashes the same and says PASS
  3. zero open blockers in the challenge ledger
  4. the tool evidence the task actually needs (write/test/research/review)
  5. the skill evidence the task selected (optional, when --skills is given)

A missing item is BLOCKED with the exact missing piece. This gate never runs a
model and never edits a file; it reads sealed evidence.

Commands:
  check --project <id> --task <text> [--risk ...] [--decision <id>]
        [--session S] [--skills a,b] [--json]
  selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402
import decision as edr  # noqa: E402
import tool_evidence as te  # noqa: E402

SCHEMA = 1


def check(workspace: str, project: str, task: str, risk: str = "STANDARD",
          decision_id: str = "", session: str = "", skills: list | None = None) -> dict:
    problems, evidence = [], {}
    if decision_id:
        try:
            record = edr.load(workspace, project, decision_id)
        except edr.DecisionError as exc:
            return {"schema": SCHEMA, "verdict": "BLOCKED", "problems": [str(exc)], "evidence": {}}
    else:
        records = [item for item in edr.latest_decisions(workspace, project)
                   if item.get("state") == "CLOSED"]
        if not records:
            return {"schema": SCHEMA, "verdict": "BLOCKED",
                    "problems": ["no CLOSED decision exists for this project"], "evidence": {}}
        record = records[0]

    decision_id = record.get("id", "")
    problems.extend(edr.integrity(record))
    if record.get("state") != "CLOSED":
        problems.append(f"decision {decision_id} is {record.get('state')}, not CLOSED")
    if record.get("project") != project:
        problems.append(f"decision project {record.get('project')} != {project}")
    order = {"ATOMIC": 0, "STANDARD": 1, "CRITICAL": 2}
    if order.get(risk, 1) > order.get(record.get("risk"), 0):
        problems.append(f"decision risk {record.get('risk')} is lower than the requested risk {risk}")

    gate = record.get("gate") or {}
    if not gate.get("ok") or gate.get("verdict") != "PASS":
        problems.append("decision has no verified PASS gate")
    gate_path = ep.load_stored_path(gate.get("path", ""), workspace)
    if not gate.get("path") or not os.path.isfile(gate_path):
        problems.append("gate artifact is missing")
    elif edr.sha256_file(gate_path) != gate.get("sha256"):
        problems.append("gate artifact changed after verification")
    evidence["decision"] = edr.decision_path(workspace, project, decision_id)
    evidence["gate"] = gate_path

    challenges = record.get("challenges")
    if challenges is not None and not isinstance(challenges, dict):
        problems.append("decision challenges field is not an object")
        challenges = {}
    challenges = challenges or {}
    if int(challenges.get("open_blockers") or 0) > 0:
        problems.append(f"{challenges.get('open_blockers')} open blocker(s) in the challenge ledger")
    if challenges.get("ledger"):
        evidence["challenge"] = ep.load_stored_path(challenges["ledger"], workspace)

    try:
        import review_contract as review  # noqa: PLC0415
    except ImportError:
        if record.get("risk") in ("STANDARD", "CRITICAL"):
            problems.append("review contract is required but review_contract.py is not installed")
    else:
        if review.applies(record, workspace):
            verdict = review.validate(workspace, record)
            evidence["review"] = verdict
            problems.extend(f"review evidence: {item}" for item in verdict["problems"])

    required = [name for name in te.required_for(task, risk)
                if name in ("test", "research")]
    if skills:
        required.append("skill")
    tool_result = te.check(workspace, required, session or None, skills or [])
    problems.extend(f"missing tool evidence: {name}" for name in tool_result["missing"])
    evidence["tools"] = tool_result.get("report", {})

    return {
        "schema": SCHEMA, "verdict": "PASS" if not problems else "BLOCKED",
        "project": project, "decision": decision_id, "problems": problems,
        "required": required, "evidence": evidence,
    }


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def workspace() -> str:
        root = tempfile.mkdtemp(prefix="eos-done-")
        os.makedirs(os.path.join(root, ".agents", "evidence", "tools"), exist_ok=True)
        return root

    def closed_decision(root, task="fix the bug") -> str:
        args = argparse.Namespace(project="eos", task=task, risk="ATOMIC",
                                  scope=["src/app.py"], suite="unit", actor="eos.test",
                                  approve=True, workspace=root)
        assert edr.cmd_open(args) == 0
        decision_id = edr.latest_decisions(root, "eos")[0]["id"]
        record = edr.load(root, "eos", decision_id)
        record["change"] = {"files": ["src/app.py"], "diff_sha256": ""}
        edr.save(root, record)
        assert edr.transition(root, record, "IMPLEMENTED", "implementer") == 0
        gate = edr._fixture_gate(root)
        assert edr.cmd_verify(argparse.Namespace(project="eos", id=decision_id, gate=gate,
                                                 max_age_min=None, actor="verifier",
                                                 workspace=root)) == 0
        record = edr.load(root, "eos", decision_id)
        assert edr.transition(root, record, "CLOSED", "verifier", "done") == 0
        return decision_id

    def evidence(root, with_test=True) -> None:
        path = os.path.join(root, ".agents", "evidence", "tools", "ses_x.jsonl")
        lines = [
            {"ts": "2026-01-01T00:00:00Z", "kind": "permission", "action": "write",
             "resource": "src/app.py", "verdict": "ALLOW", "decision": "edr-1"},
            {"ts": "2026-01-01T00:00:01Z", "kind": "permission", "action": "read",
             "resource": ".agents/skills/tdd/SKILL.md", "verdict": "ALLOW"},
        ]
        if with_test:
            lines.append({"ts": "2026-01-01T00:00:02Z", "kind": "permission", "action": "shell",
                          "resource": "pytest tests -q", "verdict": "ALLOW"})
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")

    @case("a closed decision with test evidence passes")
    def _():
        root = workspace()
        decision_id = closed_decision(root)
        evidence(root)
        result = check(root, "eos", "fix the bug", "ATOMIC", decision_id, "ses_x", ["tdd"])
        assert result["verdict"] == "PASS", result["problems"]
        return decision_id

    @case("a test claim without a test run is BLOCKED")
    def _():
        root = workspace()
        decision_id = closed_decision(root)
        evidence(root, with_test=False)
        result = check(root, "eos", "fix the bug", "STANDARD", decision_id, "ses_x")
        assert result["verdict"] == "BLOCKED"
        assert any("test" in problem for problem in result["problems"]), result["problems"]
        return "test missing"

    @case("an open decision is BLOCKED")
    def _():
        root = workspace()
        args = argparse.Namespace(project="eos", task="fix", risk="ATOMIC",
                                  scope=["src/app.py"], suite="unit", actor="eos.test",
                                  approve=False, workspace=root)
        edr.cmd_open(args)
        decision_id = edr.latest_decisions(root, "eos")[0]["id"]
        result = check(root, "eos", "fix", "STANDARD", decision_id)
        assert result["verdict"] == "BLOCKED"
        return "not closed"

    @case("a tampered decision is BLOCKED")
    def _():
        root = workspace()
        decision_id = closed_decision(root)
        evidence(root)
        path = edr.decision_path(root, "eos", decision_id)
        data = edr.read_json(path)
        data["task"] = "rewritten"
        edr.write_json_atomic(path, data)
        result = check(root, "eos", "fix the bug", "STANDARD", decision_id, "ses_x")
        assert result["verdict"] == "BLOCKED"
        return "hash mismatch"

    @case("a marked STANDARD decision without review is BLOCKED")
    def _():
        root = workspace()
        decision_id = closed_decision(root)
        evidence(root)
        record = edr.load(root, "eos", decision_id)
        record["risk"] = "STANDARD"
        record["review_contract"] = 1
        edr.save(root, record)
        result = check(root, "eos", "fix the bug", "STANDARD", decision_id, "ses_x")
        assert result["verdict"] == "BLOCKED", result["problems"]
        assert any("review" in problem for problem in result["problems"]), result["problems"]
        return "review missing"

    @case("a legacy decision without the marker still PASSes")
    def _():
        root = workspace()
        decision_id = closed_decision(root)
        evidence(root)
        record = edr.load(root, "eos", decision_id)
        record.pop("review_contract", None)
        record["risk"] = "STANDARD"
        record["created_at"] = "2020-01-01T00:00:00Z"
        edr.save(root, record)
        result = check(root, "eos", "fix the bug", "STANDARD", decision_id, "ses_x")
        assert result["verdict"] == "PASS", result["problems"]
        return "legacy pass"

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
    parser = argparse.ArgumentParser(prog="completion.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--workspace")
    p.add_argument("--project", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--risk", default="STANDARD")
    p.add_argument("--decision", default="")
    p.add_argument("--session", default="")
    p.add_argument("--skills", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=lambda args: _cmd_check(args))
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


def _cmd_check(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    skills = [item.strip() for item in (args.skills or "").split(",") if item.strip()]
    result = check(workspace, args.project, args.task, args.risk, args.decision,
                   args.session, skills)
    if args.json:
        print(json.dumps({k: result[k] for k in ("verdict", "project", "decision",
                                                 "problems", "required")}, ensure_ascii=True))
    else:
        print(f"{result['verdict']}: {result.get('decision') or '(no decision)'}")
        for problem in result["problems"]:
            print(f"  - {problem}")
        if not result["problems"]:
            print(f"  decision: {result['evidence'].get('decision')}")
            print(f"  gate    : {result['evidence'].get('gate')}")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
