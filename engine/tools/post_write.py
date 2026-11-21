#!/usr/bin/env python3
"""Post-write refresh hook.

The write guard calls `touch` immediately after it allows a write. That marks
the changed file in the project map and bumps the context epoch, so the next
activation cannot reuse an old context block.

`refresh` recomputes the map and clears the dirty set. The next risky write
requires a CURRENT map, so a stale map cannot silently authorise it.

Commands:
  touch   --path <file>... [--project <id>] [--session <id>] [--json]
  refresh --project <id> [--json]
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


def touch(workspace: str, paths: list, project: str = "", session: str = "") -> dict:
    if not project:
        first = paths[0] if paths else workspace
        absolute = os.path.join(workspace, first) if not os.path.isabs(first) else first
        project = edr.infer_project(workspace, absolute) or ""
    if not project:
        return {"ok": False, "reason": "project could not be inferred", "paths": paths}
    result = pm.mark_dirty(project, workspace, paths)
    if not result.get("ok"):
        return {"ok": False, "project": project, "reason": result.get("reason")}
    epoch = os.path.join(workspace, ".agents", "state", "context.epoch")
    return {"ok": True, "project": project, "session": session,
            "dirty_files": result.get("dirty_files", []),
            "epoch": os.path.getmtime(epoch) if os.path.isfile(epoch) else 0}


def refresh(project: str, workspace: str) -> dict:
    result = pm.ensure(project, workspace)
    epoch = os.path.join(workspace, ".agents", "state", "context.epoch")
    return {"ok": result.get("state") == "CURRENT", "project": project,
            "state": result.get("state"), "reasons": result.get("reasons", []),
            "epoch": os.path.getmtime(epoch) if os.path.isfile(epoch) else 0}


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-pw-")
        registry = os.path.join(root, ".agents", "registry")
        os.makedirs(registry)
        with open(os.path.join(registry, "PROJECT_PROFILES.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"profiles": [{"id": "demo", "path": "proj"}]}, handle)
        project = os.path.join(root, "proj")
        os.makedirs(project)
        open(os.path.join(project, "a.py"), "w").write("x = 1\n")
        assert pm.cmd_create(argparse.Namespace(project="demo", workspace=root,
                                                force=False, project_path="")) == 0
        return root

    @case("touch marks the map dirty and changes the epoch")
    def _():
        root = fixture()
        before = touch(root, ["proj/a.py"], "demo", "s1")
        assert before["ok"] is True, before
        assert pm.verify("demo", root)["state"] == "STALE"
        return "dirty"

    @case("refresh restores CURRENT and clears dirty")
    def _():
        root = fixture()
        touch(root, ["proj/a.py"], "demo", "s1")
        result = refresh("demo", root)
        assert result["ok"] is True, result
        assert pm.verify("demo", root)["state"] == "CURRENT"
        return "current"

    @case("touch without an inferable project is refused")
    def _():
        root = tempfile.mkdtemp(prefix="eos-pw-empty-")
        result = touch(root, ["x.py"], "", "s1")
        assert result["ok"] is False
        return "refused"

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
    parser = argparse.ArgumentParser(prog="post_write.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("touch")
    p.add_argument("--workspace")
    p.add_argument("--project", default="")
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--session", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=lambda args: _touch_cmd(args))
    p = sub.add_parser("refresh")
    p.add_argument("--workspace")
    p.add_argument("--project", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=lambda args: _refresh_cmd(args))
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


def _touch_cmd(args) -> int:
    result = touch(ep.resolve_workspace(args.workspace), list(args.path or []),
                   args.project, args.session)
    print(json.dumps(result, ensure_ascii=True) if args.json else
          (f"DIRTY: {result['project']} ({len(result.get('dirty_files', []))} file(s))"
           if result.get("ok") else f"BLOCKED: {result.get('reason')}"))
    return 0 if result.get("ok") else 1


def _refresh_cmd(args) -> int:
    result = refresh(args.project, ep.resolve_workspace(args.workspace))
    print(json.dumps(result, ensure_ascii=True) if args.json else
          f"{result['state']}: {args.project}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
