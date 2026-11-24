#!/usr/bin/env python3
"""Always-On preflight.

One call answers the ten questions a session must answer before it writes:

  1. is this path inside an EOS scope?
  2. which project owns it?
  3. is the project map current, and if not, can it be built?
  4. does Git HEAD / dirty state match the map?
  5. what work is already open, and what must not regress?
  6. is this prompt a continuation or a new task?
  7. what does the risk/impact evidence say?
  8. which agents, tools and skills are selected?
  9. is there an approved EDR, or will the write gate prepare one?
 10. what is the next safe action?

The output is evidence, not prose. A missing map is built from real files; an
invalid map is BLOCKED; an out-of-scope path is BLOCKED. Nothing is guessed.

Commands:
  run --task <text> [--project <id>] [--cwd <dir>] [--session <id>] [--json] [--no-route]
  selftest
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "router"))
import _eos_paths as ep  # noqa: E402
import decision as edr  # noqa: E402
import project_map as pm  # noqa: E402
import work_state as ws  # noqa: E402
import workspace as ws_scope  # noqa: E402

SCHEMA = 1


def route_task(task: str, cwd: str, root: str) -> dict:
    script = os.path.join(ep.ENGINE_ROOT, "router", "route_task.py")
    try:
        proc = subprocess.run([sys.executable, script, "--task", task, "--cwd", cwd,
                               "--root", root], capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return {}


def profile_invariants(project: str, workspace: str) -> list:
    for registry in (os.path.join(workspace, ".agents", "registry"), ep.REGISTRY):
        path = os.path.join(registry, "PROJECT_PROFILES.json")
        if not os.path.isfile(path):
            continue
        try:
            data = pm.read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for profile in data.get("profiles", []):
            if profile.get("id") == project:
                return list(profile.get("invariants") or [])
    return []


def ensure_map(project: str, workspace: str) -> dict:
    result = pm.verify(project, workspace)
    if result["state"] == "MISSING":
        value = pm.build(project, workspace)
        pm.write_json(pm.store_path(workspace, project), value)
        result = pm.verify(project, workspace)
        result["action"] = "created"
    elif result["state"] == "STALE":
        value = pm.build(project, workspace, result.get("map"))
        pm.write_json(pm.store_path(workspace, project), value)
        result = pm.verify(project, workspace)
        result["action"] = "updated"
    else:
        result["action"] = "none"
    return result


def run(task: str, workspace: str, project: str = "", cwd: str = "", session: str = "",
        use_route: bool = True) -> dict:
    cwd = os.path.abspath(cwd or workspace)
    scope = ws_scope.resolve(cwd, workspace)
    route = route_task(task, cwd, workspace) if use_route else {}
    resolved = route.get("resolved") or {}
    project = project or resolved.get("project_id") or ""
    blockers, notes = [], []
    if not scope["in_scope"] and scope.get("mode") != "unconfigured":
        blockers.append(f"OUT_OF_SCOPE: {scope.get('reason')}")
    elif scope.get("mode") == "unconfigured":
        notes.append("scope is not configured; run `workspace.py scope set` for workspace/global isolation")
    if not project:
        blockers.append("PROJECT_UNRESOLVED: no registered or discovered project")
        return {"schema": SCHEMA, "verdict": "BLOCKED", "scope": scope,
                "project": None, "blockers": blockers, "notes": notes}

    map_result = ensure_map(project, workspace)
    if map_result["state"] == "INVALID":
        blockers.append("MAP_INVALID: repair with project_map create --force")
    elif map_result["state"] != "CURRENT":
        blockers.append(f"MAP_{map_result['state']}: {map_result.get('reasons', [''])[0]}")
    map_value = map_result.get("map") or {}

    state = None
    try:
        state = ws.load_state(workspace, project)
    except SystemExit as exc:
        blockers.append(str(exc))
    work = {"state": "MISSING"}
    continuation = "new"
    if state:
        work = {k: state.get(k) for k in ("task", "status", "session_id", "owner",
                                          "decision", "challenge", "next_action",
                                          "files", "record_id", "closed_at")}
        work["state"] = state.get("status")
        if state.get("status") != "closed":
            continuation = "resume" if state.get("session_id") == session or not session else "owned"
        if state.get("open_errors"):
            notes.append(f"{len(state['open_errors'])} open error(s) from the previous session")

    objective = None
    try:
        objective = ws.open_objective(workspace, project)
    except SystemExit as exc:
        blockers.append(str(exc))
    if objective:
        work["objective"] = ws.objective_lines(objective)
        work["next_action"] = ws.derived_next(objective)
        if continuation == "new":
            continuation = "resume"

    active = [item for item in edr.latest_decisions(workspace, project)
              if item.get("state") in ("APPROVED_FOR_EDIT", "IMPLEMENTED")]
    edr_state = {"state": "NONE", "decision": "", "policy": "ATOMIC auto on write; "
                                                             "STANDARD needs review; CRITICAL needs a human"}
    if active:
        edr_state = {"state": active[0].get("state"), "decision": active[0].get("id"),
                     "policy": edr_state["policy"]}

    risk = route.get("risk") or {}
    agents = route.get("agents") or {}
    skills = (route.get("skills") or {}).get("primary") or []
    result = {
        "schema": SCHEMA,
        "verdict": "READY" if not blockers else "BLOCKED",
        "scope": scope,
        "project": {"id": project, "path": resolved.get("path"),
                    "source": resolved.get("source"), "tech": resolved.get("tech") or [],
                    "confidence": resolved.get("confidence") or "UNKNOWN"},
        "map": {"state": map_result["state"], "action": map_result.get("action"),
                "path": map_result.get("path"), "excerpt": pm.excerpt(project, workspace, 20)},
        "git": map_value.get("git") or {},
        "work": work,
        "continuation": continuation,
        "invariants": profile_invariants(project, workspace),
        "risk": {"level": risk.get("level") or "unknown",
                 "reasons": risk.get("keywords") or []},
        "agents": {"orchestrator": agents.get("orchestrator") or "host-session",
                   "specialists": agents.get("specialists") or []},
        "skills": skills[:8],
        "edr": edr_state,
        "blockers": blockers,
        "notes": notes,
        "next": ("write inside the approved scope" if edr_state["state"] in ("APPROVED_FOR_EDIT", "IMPLEMENTED")
                 else "the write gate prepares an ATOMIC decision; otherwise review first"),
    }
    return result


def cmd_run(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    result = run(args.task, workspace, args.project, args.cwd, args.session, not args.no_route)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"{result['verdict']}: {result.get('project', {}).get('id') if result.get('project') else 'unresolved'}")
        print(f"  scope: {'IN' if result['scope']['in_scope'] else 'OUT'} "
              f"({result['scope'].get('mode')})")
        if result.get("map"):
            print(f"  map  : {result['map']['state']} ({result['map'].get('action')})")
            print("  " + (result["map"].get("excerpt") or "").replace("\n", "\n  "))
        if result.get("work"):
            print(f"  work : {result['work'].get('state')} "
                  f"({result['work'].get('task') or '-'}) continuation={result['continuation']}")
        print(f"  edr  : {result['edr']['state']} {result['edr'].get('decision') or ''}")
        print(f"  risk : {result['risk']['level']}")
        if result.get("agents", {}).get("specialists"):
            print("  team : " + ", ".join(result["agents"]["specialists"]))
        for blocker in result.get("blockers") or []:
            print(f"  - {blocker}")
        for note in result.get("notes") or []:
            print(f"  note: {note}")
    return 0 if result["verdict"] != "BLOCKED" else 1


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        workspace = tempfile.mkdtemp(prefix="eos-preflight-")
        os.makedirs(os.path.join(workspace, ".agents", "registry"), exist_ok=True)
        registry = os.path.join(workspace, ".agents", "registry")
        with open(os.path.join(registry, "PROJECT_PROFILES.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"profiles": [{"id": "demo", "path": "proj",
                                     "invariants": ["do not break the calculator"]}]}, handle)
        project = os.path.join(workspace, "proj")
        os.makedirs(project, exist_ok=True)
        with open(os.path.join(project, "calc.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("def add(a, b):\n    return a + b\n")
        os.makedirs(os.path.join(workspace, ".eos"), exist_ok=True)
        with open(os.path.join(workspace, ".eos", "scope.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"schema": 1, "mode": "workspace", "root": workspace,
                       "hosts": ["opencode"]}, handle)
        return workspace

    @case("a missing map is created and work state is resumed")
    def _():
        workspace = fixture()
        first = run("fix the calculator", workspace, "demo", os.path.join(workspace, "proj"),
                    "s1", use_route=False)
        assert first["map"]["state"] == "CURRENT", first["map"]
        assert first["work"]["state"] == "MISSING"
        assert first["invariants"] == ["do not break the calculator"]
        return "ready"

    @case("out of scope is BLOCKED")
    def _():
        workspace = fixture()
        result = run("fix", workspace, "demo", os.path.dirname(workspace), "s1", use_route=False)
        assert result["verdict"] == "BLOCKED"
        assert any("OUT_OF_SCOPE" in item for item in result["blockers"])
        return "blocked"

    @case("an invalid map is BLOCKED")
    def _():
        workspace = fixture()
        run("fix", workspace, "demo", os.path.join(workspace, "proj"), "s1", use_route=False)
        path = pm.store_path(workspace, "demo")
        data = pm.read_json(path)
        data["modules"].append({"path": "evil.py"})
        pm.write_json(path, data)
        result = run("fix", workspace, "demo", os.path.join(workspace, "proj"), "s1",
                     use_route=False)
        assert result["verdict"] == "BLOCKED"
        assert any("MAP_INVALID" in item for item in result["blockers"])
        return "blocked"

    @case("an open objective is reported as resume although the last write closed")
    def _():
        workspace = fixture()
        where = os.path.join(workspace, "proj")
        run("fix", workspace, "demo", where, "s1", use_route=False)
        assert ws.cmd_record_atomic(argparse.Namespace(
            project="demo", task="Do:\n1. first\n2. second", session="s1", decision="edr-1",
            file=["src/a.py"], map_state="CURRENT", map_sha="", owner="", json=False,
            workspace=workspace)) == 0
        result = run("devam et", workspace, "demo", where, "s2", use_route=False)
        assert result["continuation"] == "resume", result["continuation"]
        assert result["work"]["next_action"] == "item 1: first", result["work"]
        return "resume"

    @case("active work is reported as resume")
    def _():
        workspace = fixture()
        run("fix", workspace, "demo", os.path.join(workspace, "proj"), "s1", use_route=False)
        assert ws.cmd_start(argparse.Namespace(project="demo", task="fix", session="s1",
                                               owner="agent", new=False, workspace=workspace)) == 0
        result = run("fix again", workspace, "demo", os.path.join(workspace, "proj"), "s1",
                     use_route=False)
        assert result["continuation"] == "resume", result["continuation"]
        return "resume"

    @case("a closed atomic record is visible to the next session")
    def _():
        workspace = fixture()
        run("fix", workspace, "demo", os.path.join(workspace, "proj"), "s1", use_route=False)
        assert ws.cmd_record_atomic(argparse.Namespace(
            project="demo", task="create a", session="s1", decision="edr-1",
            file=["proj/a.py"], map_state="CURRENT", map_sha="", owner="eos.atomic",
            json=True, workspace=workspace)) == 0
        result = run("write another", workspace, "demo",
                     os.path.join(workspace, "proj"), "s2", use_route=False)
        assert result["work"]["state"] == "closed", result["work"]
        assert result["work"]["decision"] == "edr-1", result["work"]
        assert result["work"]["files"] == [os.path.normpath("proj/a.py")], result["work"]
        assert result["continuation"] == "new", result["continuation"]
        return "history visible"

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
    parser = argparse.ArgumentParser(prog="preflight.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--workspace")
    p.add_argument("--project", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--task", required=True)
    p.add_argument("--session", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-route", action="store_true")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
