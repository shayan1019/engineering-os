#!/usr/bin/env python3
"""Mechanical pre-write gate.

The guard already checks an EDR. This gate checks the rest of the contract in
one place:

  project identity -> map revision -> Git/dirty state -> EDR scope ->
  session ownership -> writer ownership

Policy:

  map INVALID                  BLOCKED
  map MISSING/STALE            BLOCKED for every tier until a successful
                               post-write refresh (0.3.4)
  map CURRENT + EDR            allowed
  map CURRENT + no EDR         needs_decision (the guard prepares an EDR)
  another session's EDR        BLOCKED
  another session's work task  BLOCKED
  corrupt work state           BLOCKED
  control-plane evidence path  BLOCKED (use the owning EOS tool)

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
    if map_state in ("MISSING", "STALE"):
        return {"allow": False, "needs_decision": False,
                "reason": f"project map is {map_state}; a successful post-write refresh is required"}
    if not decision:
        return {"allow": False, "needs_decision": True,
                "reason": "no approved decision covers this path"}
    return {"allow": True, "needs_decision": False, "reason": "map current; decision valid"}


def check(workspace: str, path: str, session: str = "", project: str = "") -> dict:
    absolute = os.path.realpath(os.path.abspath(
        path if os.path.isabs(path) else os.path.join(workspace, path)))
    workspace_real = os.path.realpath(os.path.abspath(workspace))
    if not (absolute == workspace_real or absolute.startswith(workspace_real + os.sep)):
        return {"schema": SCHEMA, "allow": False, "needs_decision": False,
                "project": project or "", "map_state": "BLOCKED",
                "reasons": ["path is outside the workspace (real path)"]}
    rel = ep.store_path(absolute, workspace).replace("\\", "/")
    if ep.is_control_plane(rel):
        return {"schema": SCHEMA, "allow": False, "needs_decision": False,
                "project": project or "", "map_state": "PROTECTED",
                "reasons": [f"{rel} is control-plane evidence; use the owning EOS tool, "
                            f"not a direct write"]}
    if not project:
        project = edr.infer_project(workspace, absolute) or ""
    if not project:
        return {"schema": SCHEMA, "allow": False, "needs_decision": False,
                "project": "", "map_state": "BLOCKED",
                "reasons": ["project could not be inferred"]}

    map_result = pm.verify(project, workspace)
    if map_result["state"] in ("MISSING", "STALE"):
        # The map is derived from the files. A package install, a git command or
        # the user's own editor changes them mid-session; refusing every write
        # until the next prompt repaired nothing. Re-read, then decide. A map
        # edited outside EOS is INVALID and stays refused.
        map_result = pm.ensure(project, workspace)
    map_state = map_result["state"]
    map_value = map_result.get("map") or {}
    decision = edr.check_edit(workspace, project, absolute, session=session)
    verdict = policy(map_state, decision if decision.get("ok") else None)

    owner = ""
    try:
        state = ws.load_state(workspace, project)
    except (SystemExit, ValueError, OSError) as exc:
        return {"schema": SCHEMA, "allow": False, "needs_decision": False,
                "project": project, "path": rel, "session": session,
                "map_state": map_state, "map_coverage": map_value.get("coverage_state", "UNKNOWN"),
                "map_dirty_files": map_value.get("dirty_files") or [],
                "decision": "", "risk": "", "owner": "",
                "reasons": [f"work state is corrupt: {exc}"]}
    if state and state.get("status") != "closed":
        owner = str(state.get("session_id") or "")
        if owner and session and owner != session:
            verdict = {"allow": False, "needs_decision": False,
                       "reason": f"work task is owned by session {owner}"}

    reasons = [verdict["reason"]]
    map_sha = str(map_value.get("map_sha256") or "")
    map_generated = str(map_value.get("generated_at") or "")
    if map_sha:
        reasons.append(f"map {map_state} sha256={map_sha[:16]} generated_at={map_generated}")
    if decision.get("reasons"):
        reasons.extend(decision["reasons"][:3])
    return {
        "schema": SCHEMA, "allow": bool(verdict["allow"]),
        "needs_decision": bool(verdict["needs_decision"]),
        "project": project, "path": ep.store_path(absolute, workspace),
        "session": session, "owner": owner,
        "map_state": map_state,
        "map_sha256": map_sha,
        "map_generated_at": map_generated,
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

    @case("policy blocks every tier on a stale map")
    def _():
        atomic = policy("STALE", {"risk": "ATOMIC"})
        assert atomic["allow"] is False, atomic
        standard = policy("STALE", {"risk": "STANDARD"})
        assert standard["allow"] is False, standard
        missing = policy("MISSING", {"risk": "ATOMIC"})
        assert missing["allow"] is False, missing
        return "fail closed"

    @case("policy allows an ATOMIC decision only on a CURRENT map")
    def _():
        current = policy("CURRENT", {"risk": "ATOMIC"})
        assert current["allow"] is True, current
        return "current only"

    @case("policy blocks an invalid map and asks for a decision on CURRENT")
    def _():
        assert policy("INVALID", {"risk": "ATOMIC"})["allow"] is False
        needs = policy("CURRENT", None)
        assert needs["allow"] is False and needs["needs_decision"] is True
        return "invalid/needs"

    @case("map revision is part of the gate evidence")
    def _():
        root = tempfile.mkdtemp(prefix="eos-gate-m-")
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
        result = check(root, os.path.join(project, "a.py"), "s1", "demo")
        assert result["map_sha256"], result
        assert result["map_generated_at"], result
        assert any("sha256=" in reason for reason in result["reasons"]), result
        return "map sha recorded"

    @case("every language: a second write after the first is not refused as STALE")
    def _():
        root = tempfile.mkdtemp(prefix="eos-gate-lang-")
        names = ["a.go", "b.rs", "C.java", "d.c", "e.cpp", "F.cs", "g.dart", "h.vue", "i.svelte",
                 "j.ino", "k.kt", "l.swift", "m.php", "n.rb", "o.sql", "p.html", "q.txt", "r.py",
                 "s.ts", ".github/workflows/t.yml", "node_modules/u.js"]
        for name in names:
            assert check(root, name, "s1")["map_state"] == "CURRENT", name
            full = os.path.join(root, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            for text in ("1", "2"):
                open(full, "w").write(text)
                project = edr.infer_project(root, full)
                assert pm.update_files(project, root, [name])["ok"], name
                assert pm.verify(project, root)["state"] == "CURRENT", name
        return f"{len(names)} files"

    @case("a map staled by git, an install or the user's editor is re-read, not refused")
    def _():
        root = tempfile.mkdtemp(prefix="eos-gate-stale-")
        open(os.path.join(root, "a.go"), "w").write("1")
        assert check(root, "a.go", "s1")["map_state"] == "CURRENT"
        open(os.path.join(root, "a.go"), "w").write("22")
        open(os.path.join(root, "new.rs"), "w").write("1")
        result = check(root, "a.go", "s1")
        assert result["map_state"] == "CURRENT" and result["needs_decision"], result
        path = pm.store_path(root, result["project"])
        open(path, "w").write("{cut")
        assert check(root, "a.go", "s1")["map_state"] == "CURRENT"
        value = pm.read_json(path)
        value["modules"] = [{"path": "forged"}]
        pm.write_json(path, value)
        assert check(root, "a.go", "s1")["map_state"] == "INVALID"
        return "refreshed; a forged map stays refused"

    @case("a relative path is resolved against the workspace")
    def _():
        root = tempfile.mkdtemp(prefix="eos-gate-rel-")
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
        result = check(root, "proj/a.py", "s1", "demo")
        assert result["allow"] is True, result
        assert result["path"].endswith("proj/a.py"), result
        return "workspace-relative"

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

    @case("control-plane evidence paths are refused")
    def _():
        root = tempfile.mkdtemp(prefix="eos-gate-p-")
        os.makedirs(os.path.join(root, ".agents", "evidence"))
        result = check(root, os.path.join(root, ".agents", "evidence", "x.json"), "s1")
        assert result["allow"] is False, result
        assert result["map_state"] == "PROTECTED", result
        return "protected"

    @case("a corrupt work state blocks instead of being swallowed")
    def _():
        root = tempfile.mkdtemp(prefix="eos-gate-c-")
        registry = os.path.join(root, ".agents", "registry")
        os.makedirs(registry)
        with open(os.path.join(registry, "PROJECT_PROFILES.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"profiles": [{"id": "demo", "path": "proj"}]}, handle)
        os.makedirs(os.path.join(root, "proj"))
        open(os.path.join(root, "proj", "a.py"), "w").write("x = 1\n")
        assert pm.cmd_create(argparse.Namespace(project="demo", workspace=root,
                                                force=False, project_path="")) == 0
        state_path = ws.state_path(root, "demo")
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("{not json")
        result = check(root, os.path.join(root, "proj", "a.py"), "s1", "demo")
        assert result["allow"] is False, result
        assert any("corrupt" in reason for reason in result["reasons"]), result
        return "corrupt blocked"

    @case("a nested project's control plane is refused")
    def _():
        root = tempfile.mkdtemp(prefix="eos-gate-n-")
        nested = os.path.join(root, "proj", "sub", ".agents", "evidence")
        os.makedirs(nested)
        result = check(root, os.path.join(nested, "x.json"), "s1", "sub")
        assert result["allow"] is False, result
        assert result["map_state"] == "PROTECTED", result
        return "nested protected"

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
