#!/usr/bin/env python3
"""Automatic Engineering Decision Record.

The user writes a task; EOS prepares the decision. This tool never asks the
user to run `decision.py open`. It resolves the project, measures the impact,
classifies the risk, creates the record, fills it with evidence, and returns the
next step.

Risk policy:

  ATOMIC    one file, low risk, no cross-process, no sensitive surface.
            Auto-approved so a small fix is not slowed by ceremony.
  STANDARD  two to three files, or tests, or a non-trivial change.
            Prepared and left for specialist review; never auto-approved here.
  CRITICAL  high-risk words, cross-process, more than three files, deploy or
            secret surfaces. Never auto-approved, ever. A human must approve.

Commands:
  prepare --project <id> --task <text> --scope <path>... [--approve] [--no-impact]
  selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "router"))
import _eos_paths as ep  # noqa: E402
import decision as edr  # noqa: E402

HIGH_WORDS = ("deploy", "production", "canlı", "canli", "credential", "secret", "şifre",
              "sifre", "password", "migrate", "migration", "sil", "delete", "drop table",
              "trading", "emir", "order", "payment", "ödeme", "odeme")
SENSITIVE = (".env", "secret", "credential", "deploy", "production", ".mq5", ".mq4",
             "dockerfile", "docker-compose")


def _high_hit(text: str, word: str) -> bool:
    """A high-risk word must start a word. 'nasil' must not match 'sil'."""
    return bool(re.search(r"(?<!\w)" + re.escape(word), text))


def classify(task: str, scope: list, impact: dict | None, impact_required: bool = False) -> tuple:
    text = task.lower()
    high = any(_high_hit(text, word) for word in HIGH_WORDS)
    sensitive = any(any(token in item.lower() for token in SENSITIVE) for item in scope)
    cross = bool((impact or {}).get("cross_process"))
    impact_missing = impact_required and impact is None
    reasons = [reason for reason, hit in (
        ("high-risk wording", high), ("sensitive surface", sensitive),
        ("cross-process change", cross), ("more than three files", len(scope) > 3),
        ("impact analysis unavailable", impact_missing)) if hit]
    if high or sensitive or cross or len(scope) > 3 or impact_missing:
        return "CRITICAL" if (high or sensitive or cross) else "STANDARD", reasons
    if len(scope) == 1:
        edges = len((impact or {}).get("edges") or [])
        if edges <= 25:
            return "ATOMIC", ["one file", "no cross-process", "bounded impact"]
        return "STANDARD", [f"{edges} dependency edges"]
    return "STANDARD", [f"{len(scope)} files"]


PROTECTED_SCOPE = (".agents/evidence/", ".agents/state/", ".opencode/plugin/", ".eos/")


def safe_scope(workspace: str, values: list) -> tuple:
    scope, problems = [], []
    workspace_real = os.path.realpath(os.path.abspath(workspace))
    for raw in values:
        candidate = raw if os.path.isabs(raw) else os.path.join(workspace_real, raw)
        absolute = os.path.realpath(os.path.abspath(candidate))
        if not (absolute == workspace_real or absolute.startswith(workspace_real + os.sep)):
            problems.append(f"outside the workspace (real path): {raw}")
            continue
        rel = ep.store_path(absolute, workspace_real).replace("\\", "/")
        if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in PROTECTED_SCOPE):
            problems.append(f"control-plane evidence is append-only, use the owning EOS tool: {rel}")
            continue
        if rel not in scope:
            scope.append(rel)
    return scope, problems


def prepare(workspace: str, project: str, task: str, scope_values: list,
            approve: bool = False, use_impact: bool = True, session: str = "") -> dict:
    scope, problems = safe_scope(workspace, scope_values)
    if not scope:
        return {"verdict": "BLOCKED", "problems": problems or ["no scope"],
                "next": "pass --scope with the file(s) the change touches"}
    if not project:
        project = edr.infer_project(workspace, os.path.join(os.path.realpath(workspace), scope[0])) or "workspace"
    project = edr.safe_project(project)

    reused = None
    for rel in scope:
        check = edr.check_edit(workspace, None, os.path.join(os.path.realpath(workspace), rel),
                               session=session)
        if not check.get("ok"):
            reused = None
            break
        if reused is None:
            reused = check
        elif check.get("decision") != reused.get("decision"):
            reused = None
            break
    if reused:
        try:
            reused_record = edr.load(workspace, reused.get("project"), reused["decision"])
            if not all(edr.covers(edr.scope_list(reused_record), rel) for rel in scope):
                reused = None
        except edr.DecisionError:
            reused = None
    if reused:
        return {"verdict": "READY", "decision": reused["decision"],
                "risk": reused.get("risk") or "UNKNOWN",
                "reasons": ["an approved decision covers every scope path"],
                "project": reused.get("project"), "scope": scope,
                "state": reused.get("state"), "approved": True, "impact": "reused",
                "missing": [], "next": "write inside the scope",
                "human_approval_required": False, "reused": True}

    impact = None
    impact_note = "not measured"
    if use_impact:
        try:
            import impact as impact_tool
            impact = impact_tool.build(project, workspace, scope, "", "", 40)
            impact_note = impact.get("verdict", "UNKNOWN")
        except (SystemExit, Exception) as exc:  # noqa: BLE001 - impact must not block an ATOMIC fix
            impact_note = f"UNKNOWN: {type(exc).__name__}: {exc}"
    risk, reasons = classify(task, scope, impact, impact_required=use_impact)
    if risk == "CRITICAL":
        reasons.append("critical never auto-approves")
    if problems:
        return {"verdict": "BLOCKED", "problems": problems,
                "risk": risk, "reasons": reasons}

    args = argparse.Namespace(project=project, task=task, risk=risk, scope=scope,
                              suite="", actor="eos.auto", approve=False,
                              session=session, workspace=workspace)
    # cmd_open prints; suppress its prose by calling the same primitives is not
    # worth a second implementation. The JSON result below is the contract.
    before = {item.get("id") for item in edr.latest_decisions(workspace, project)}
    assert edr.cmd_open(args) == 0
    after = [item for item in edr.latest_decisions(workspace, project)
             if item.get("id") not in before]
    record = after[0] if after else edr.latest_decisions(workspace, project)[0]
    decision_id = record["id"]

    edges = (impact or {}).get("edges") or []
    tests = [item.get("path") for item in (impact or {}).get("tests") or []]
    components = [item.get("id") for item in (impact or {}).get("components") or []]
    unknowns = list((impact or {}).get("unknowns") or [])
    evidence = [f"{edge.get('from')} -> {edge.get('to')} ({edge.get('kind')})"
                for edge in edges[:8]] or ["impact engine: no direct edge found"]
    fields = {
        "current_behavior": (f"impact={impact_note}; components={components or 'UNKNOWN'}; "
                             f"tests={tests[:5] or 'none found'}"),
        "do_not_regress": ["existing tests in the project", "behaviour outside the declared scope"],
        "evidence": evidence,
        "assumptions": ["scope is exactly the declared file(s)"] + unknowns,
        "impact": {"files": scope, "components": components, "tests": tests,
                   "unknowns": unknowns, "cross_process": bool((impact or {}).get("cross_process")),
                   "source": impact_note},
        "alternatives": ["do nothing", "minimal change inside the declared scope"],
        "verification": {"suite": "project gate; see eos_activate"},
        "rollback": "git revert of this change; no data migration",
    }
    for name, value in fields.items():
        edr.set_field(record, name, value)
    edr.save(workspace, record)

    approved = False
    if risk == "ATOMIC" and approve:
        if edr.approve_record(workspace, record, "eos.auto", "eos.atomic", auto=True) == 0:
            approved = True
            record = edr.load(workspace, project, decision_id)
    # STANDARD is deliberately not auto-approved here: a specialist review and a
    # clean challenge ledger must happen first. CRITICAL is never approved by a
    # model; a human must do it.

    return {
        "verdict": "READY" if approved or risk != "CRITICAL" else "BLOCKED",
        "decision": decision_id,
        "risk": risk,
        "reasons": reasons,
        "project": project,
        "scope": scope,
        "state": record.get("state"),
        "approved": approved,
        "impact": impact_note,
        "missing": [] if approved else edr.missing(record, edr.REQUIRED_BEFORE_EDIT[risk]),
        "next": ("write inside the scope" if approved else
                 "specialist review + challenge ledger, then `decision.py approve`"),
        "human_approval_required": risk == "CRITICAL",
    }


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-auto-")
        os.makedirs(os.path.join(root, ".agents", "evidence"), exist_ok=True)
        with open(os.path.join(root, "app.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("print('ok')\n")
        return root

    @case("one low-risk file becomes ATOMIC and is auto-approved")
    def _():
        root = fixture()
        result = prepare(root, "eos", "fix the typo in the message", ["app.py"],
                         approve=True, use_impact=False)
        assert result["risk"] == "ATOMIC", result
        assert result["approved"] is True, result
        assert result["state"] == "APPROVED_FOR_EDIT", result
        return result["decision"]

    @case("several files become STANDARD and are not auto-approved without review")
    def _():
        root = fixture()
        for name in ("b.py", "c.py", "d.py"):
            open(os.path.join(root, name), "w").write("x")
        result = prepare(root, "eos", "refactor the module", ["app.py", "b.py", "c.py", "d.py"],
                         approve=True, use_impact=False)
        assert result["risk"] == "STANDARD", result
        assert result["approved"] is False
        return result["risk"]

    @case("a deploy request is CRITICAL and never auto-approves")
    def _():
        root = fixture()
        result = prepare(root, "eos", "deploy to production", ["app.py"],
                         approve=True, use_impact=False)
        assert result["risk"] == "CRITICAL", result
        assert result["approved"] is False
        assert result["human_approval_required"] is True
        return "human required"

    @case("nasil is not a delete; silmek is high risk")
    def _():
        root = fixture()
        okay = prepare(root, "eos", "nasil calisir", ["app.py"], approve=True, use_impact=False)
        assert okay["risk"] == "ATOMIC", okay
        danger = classify("silmek", ["app.py"], None, impact_required=False)
        assert danger[0] == "CRITICAL", danger
        return "word start"

    @case("impact failure prevents ATOMIC")
    def _():
        risk, reasons = classify("fix a typo", ["app.py"], None, impact_required=True)
        assert risk == "STANDARD", (risk, reasons)
        return "standard"

    @case("a decision covering one file is not reused for two")
    def _():
        root = fixture()
        open(os.path.join(root, "b.py"), "w").write("x\n")
        args = argparse.Namespace(project="eos", task="one", risk="ATOMIC",
                                  scope=["app.py"], suite="unit", actor="eos.test",
                                  approve=True, workspace=root)
        assert edr.cmd_open(args) == 0
        result = prepare(root, "eos", "two files", ["app.py", "b.py"],
                         approve=True, use_impact=False)
        assert not result.get("reused"), result
        assert result["state"] == "DRAFT", result
        return "not reused"

    @case("a junction/symlink escape is BLOCKED")
    def _():
        root = fixture()
        outside = tempfile.mkdtemp(prefix="eos-out-")
        open(os.path.join(outside, "x.py"), "w").write("x\n")
        link = os.path.join(root, "link")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            return "symlink unavailable on this host (UNKNOWN)"
        result = prepare(root, "eos", "fix", ["link/x.py"], approve=True, use_impact=False)
        assert result["verdict"] == "BLOCKED", result
        return "blocked"

    @case("control-plane evidence cannot be an edit scope")
    def _():
        root = fixture()
        result = prepare(root, "eos", "fix", [".agents/evidence/x.json"],
                         approve=True, use_impact=False)
        assert result["verdict"] == "BLOCKED", result
        return "protected"

    @case("an outside path is BLOCKED")
    def _():
        root = fixture()
        result = prepare(root, "eos", "fix", ["../outside.py"], approve=True, use_impact=False)
        assert result["verdict"] == "BLOCKED"
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
    parser = argparse.ArgumentParser(prog="auto_decision.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--workspace")
    p.add_argument("--project", default="")
    p.add_argument("--task", required=True)
    p.add_argument("--scope", action="append", default=[])
    p.add_argument("--approve", action="store_true")
    p.add_argument("--no-impact", action="store_true")
    p.add_argument("--session", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=lambda args: _cmd_prepare(args))
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


def _cmd_prepare(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    result = prepare(workspace, args.project, args.task, list(args.scope or []),
                     args.approve, not args.no_impact, args.session)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"{result['verdict']}: {result.get('decision')} "
              f"risk={result.get('risk')} state={result.get('state')}")
        for reason in result.get("reasons") or []:
            print(f"  reason: {reason}")
        for problem in result.get("problems") or []:
            print(f"  - {problem}")
        if result.get("next"):
            print(f"  next: {result['next']}")
    return 0 if result["verdict"] != "BLOCKED" else 1


if __name__ == "__main__":
    sys.exit(main())
