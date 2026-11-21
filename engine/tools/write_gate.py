#!/usr/bin/env python3
"""Mechanical pre-write gate.

The guard already checks an EDR. This gate checks the rest of the contract in
one place:

  project identity -> map revision -> Git/dirty state -> EDR scope ->
  session ownership -> writer ownership

Policy:

  map INVALID                  BLOCKED
  map MISSING/STALE + ATOMIC   allowed, but recorded as dirty
  map MISSING/STALE + STANDARD/CRITICAL
                               BLOCKED until preflight/refresh
  map CURRENT + EDR            allowed
  map CURRENT + no EDR         needs_decision (the guard prepares an EDR)
  another session's EDR        BLOCKED
  another session's work task  BLOCKED

Commands:
  check --path <file> [--project <id>] [--session <id>] [--json]
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
import project_map as pm  # noqa: E402
import work_state as ws  # noqa: E402

SCHEMA = 1


def policy(map_state: str, decision: dict | None) -> dict:
    if map_state == "INVALID":
        return {"allow": False, "needs_decision": False,
                "reason": "project map is INVALID; repair with project_map create --force"}
    if not decision:
        if map_state in ("MISSING", "STALE"):
            return {"allow": False, "needs_decision": True,
                    "reason": f"project map is {map_state}; preflight must refresh it before a write"}
        return {"allow": False, "needs_decision": True,
                "reason": "no approved decision covers this path"}
    risk = str(decision.get("risk") or "UNKNOWN")
    if map_state == "CURRENT":
        return {"allow": True, "needs_decision": False, "reason": "map current; decision valid"}
    if risk == "ATOMIC":
        return {"allow": True, "needs_decision": False,
                "reason": f"map {map_state}; ATOMIC change allowed and marked dirty"}
    return {"allow": False, "needs_decision": False,
            "reason": f"map {map_state}; {risk} change requires a refreshed map"}


def check(workspace: str, path: str, session: str = "", project: str = "") -> dict:
    absolute = os.path.realpath(os.path.abspath(path))
    workspace_real = os.path.realpath(os.path.abspath(workspace))
    if not (absolute == workspace_real or absolute.startswith(workspace_real + os.sep)):
        return {"schema": SCHEMA, "allow": False, "needs_decision": False,
                "project": project or "", "map_state": "BLOCKED",
                "reasons": ["path is outside the workspace (real path)"]}
    if not project:
        project = edr.infer_project(workspace, absolute) or ""
    if not project:
        return {"schema": SCHEMA, "allow": False, "needs_decision": False,
                "project": "", "map_state": "BLOCKED",
                "reasons": ["project could not be inferred"]}

    map_result = pm.verify(project, workspace)
    if map_result["state"] == "MISSING":
        map_result = pm.ensure(project, workspace)
    map_state = map_result["state"]
    map_value = map_result.get("map") or {}
    decision = edr.check_edit(workspace, project, absolute, session=session)
    verdict = policy(map_state, decision if decision.get("ok") else None)

    owner = ""
    try:
        state = ws.load_state(workspace, project)
        if state and state.get("status") != "closed":
            owner = str(state.get("session_id") or "")
            if owner and session and owner != session:
                verdict = {"allow": False, "needs_decision": False,
                           "reason": f"work task is owned by session {owner}"}
    except SystemExit:
        pass

    reasons = [verdict["reason"]]
    if decision.get("reasons"):
        reasons.extend(decision["reasons"][:3])
    return {
        "schema": SCHEMA, "allow": bool(verdict["allow"]),
        "needs_decision": bool(verdict["needs_decision"]),
        "project": project, "path": ep.store_path(absolute, workspace),
        "session": session, "owner": owner,
        "map_state": map_state,
        "map_coverage": map_value.get("coverage_state", "UNKNOWN"),
        "map_dirty_files": map_value.get("dirty_files") or [],
        "decision": decision.get("decision") if decision.get("ok") else "",
        "risk": decision.get("risk") if decision.get("ok") else "",
        "reasons": reasons,
    }


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    @case("policy allows ATOMIC on a dirty map and blocks STANDARD")
    def _():
        atomic = policy("STALE", {"risk": "ATOMIC"})
        assert atomic["allow"] is True, atomic
        standard = policy("STALE", {"risk": "STANDARD"})
        assert standard["allow"] is False, standard
        return "tiered"

    @case("policy blocks an invalid map and asks for a decision on CURRENT")
    def _():
        assert policy("INVALID", {"risk": "ATOMIC"})["allow"] is False
        needs = policy("CURRENT", None)
        assert needs["allow"] is False and needs["needs_decision"] is True
        return "invalid/needs"

    @case("another session's EDR is refused")
    def _():
        root = tempfile.mkdtemp(prefix="eos-gate-")
        registry = os.path.join(root, ".agents", "registry")
        os.makedirs(registry)
        with open(os.path.join(registry, "PROJECT_PROFILES.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"profiles": [{"id": "demo", "path": "proj"}]}, handle)
        project = os.path.join(root, "proj")
        os.makedirs(project)
        with open(os.path.join(project, "a.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("x = 1\n")
        assert pm.cmd_create(argparse.Namespace(project="demo", workspace=root,
                                                force=False, project_path="")) == 0
        args = argparse.Namespace(project="demo", task="fix", risk="ATOMIC",
                                  scope=["proj/a.py"], suite="unit", actor="test",
                                  session="s1", approve=True, workspace=root)
        assert edr.cmd_open(args) == 0
        result = check(root, os.path.join(project, "a.py"), "s2", "demo")
        assert result["allow"] is False, result
        assert any("owned by session" in reason for reason in result["reasons"]), result
        return "session isolated"

    @case("another session's work task is refused")
    def _():
        root = tempfile.mkdtemp(prefix="eos-gate-w-")
        registry = os.path.join(root, ".agents", "registry")
        os.makedirs(registry)
        with open(os.path.join(registry, "PROJECT_PROFILES.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"profiles": [{"id": "demo", "path": "proj"}]}, handle)
        os.makedirs(os.path.join(root, "proj"))
        open(os.path.join(root, "proj", "a.py"), "w").write("x = 1\n")
        assert pm.cmd_create(argparse.Namespace(project="demo", workspace=root,
                                                force=False, project_path="")) == 0
        assert ws.cmd_start(argparse.Namespace(project="demo", task="fix", session="s1",
                                               owner="a", new=False, workspace=root)) == 0
        result = check(root, os.path.join(root, "proj", "a.py"), "s2", "demo")
        assert result["allow"] is False, result
        assert any("work task" in reason for reason in result["reasons"]), result
        return "owner isolated"

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
    parser = argparse.ArgumentParser(prog="write_gate.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--workspace")
    p.add_argument("--path", required=True)
    p.add_argument("--project", default="")
    p.add_argument("--session", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=lambda args: _cmd_check(args))
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


def _cmd_check(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    result = check(workspace, args.path, args.session, args.project)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"{'ALLOW' if result['allow'] else 'DENY'}: {result['path']} "
              f"(map={result['map_state']}, decision={result['decision'] or '-'})")
        for reason in result["reasons"]:
            print(f"  - {reason}")
    return 0 if result["allow"] else (3 if result.get("needs_decision") else 1)


if __name__ == "__main__":
    sys.exit(main())
