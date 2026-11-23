#!/usr/bin/env python3
"""Bounded, safe recovery diagnosis for blocked writes.

The tool never creates or approves an EDR and never bypasses ownership. It
diagnoses a blocked write, refreshes a stale/missing project map (safe, no
authorization change), and returns the exact next EOS command. A session/path
pair gets two diagnostic attempts; a third returns BLOCKED.

Commands:
  diagnose --path <file> [--task T] [--project P] [--session S] [--workspace W] [--json]
  selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402
import decision as edr  # noqa: E402
import project_map as pm  # noqa: E402
import write_gate as wg  # noqa: E402

SCHEMA = 1
MAX_ATTEMPTS = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_session(value: str) -> str:
    text = "".join(char if char.isalnum() or char in "._-" else "-"
                   for char in str(value or "")).strip("-")
    return text[:80] or "unknown"


def attempts_path(workspace: str, session: str) -> str:
    return os.path.join(workspace, ".agents", "evidence", "recovery",
                        safe_session(session) + ".json")


def load_attempts(workspace: str, session: str) -> dict:
    path = attempts_path(workspace, session)
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_attempts(workspace: str, session: str, value: dict) -> None:
    path = attempts_path(workspace, session)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=True, indent=2)


def bump(workspace: str, session: str, key: str) -> int:
    value = load_attempts(workspace, session)
    count = int(value.get(key) or 0) + 1
    value[key] = count
    save_attempts(workspace, session, value)
    return count


def reset(workspace: str, session: str, key: str) -> None:
    value = load_attempts(workspace, session)
    if key in value:
        value.pop(key, None)
        save_attempts(workspace, session, value)


def project_root_for(path: str) -> str:
    current = os.path.dirname(os.path.abspath(path))
    while current and current != os.path.dirname(current):
        if (os.path.isfile(os.path.join(current, ".eos", "project.json"))
                or os.path.isdir(os.path.join(current, ".agents"))):
            return current
        current = os.path.dirname(current)
    return ""


def result(ok: bool, action: str, recoverable: bool, reason: str, **extra) -> dict:
    return {"schema": SCHEMA, "ok": ok, "action": action,
            "recoverable": recoverable, "reason": reason, **extra}


def diagnose(workspace: str, path: str, task: str = "", session: str = "",
             project: str = "") -> dict:
    workspace = os.path.realpath(os.path.abspath(workspace))
    absolute = os.path.realpath(path if os.path.isabs(path)
                                else os.path.join(workspace, path))
    key = os.path.normcase(absolute)

    if not (absolute == workspace or absolute.startswith(workspace + os.sep)):
        root = project_root_for(absolute)
        return result(
            False, "wrong_workspace_root", True,
            "the path is outside the active workspace",
            path=absolute, workspace=workspace, suggested_workspace=root,
            next=("re-run with the project workspace root and a workspace-relative path"
                  if root else "select the correct workspace root first"),
        )

    rel = os.path.relpath(absolute, workspace).replace("\\", "/")
    problem = edr.scope_input_problem(workspace, rel)
    if problem:
        count = bump(workspace, session, key)
        if count > MAX_ATTEMPTS:
            return result(False, "retry_budget_exhausted", False,
                          "too many recovery attempts for this path",
                          path=rel, attempts=count)
        return result(False, "split_scope", True, problem, path=rel,
                      attempts=count, next="resubmit the explicit scope list")

    project = project or edr.infer_project(workspace, absolute) or ""
    if not project:
        return result(False, "project_not_inferred", False,
                      "the project identity could not be inferred",
                      path=rel, next="create or select the project identity first")

    before = pm.verify(project, workspace)
    if before.get("state") in ("MISSING", "STALE"):
        pm.ensure(project, workspace)
        after = pm.verify(project, workspace)
        return result(True, "map_refreshed", True,
                      "the project map was rebuilt; authorization was not changed",
                      path=rel, project=project, map_state=after.get("state"),
                      map_sha256=(after.get("map") or {}).get("map_sha256", ""),
                      next="retry the write; no new EDR is created by this step")
    if before.get("state") != "CURRENT":
        return result(False, "map_invalid", False,
                      f"project map is {before.get('state')}",
                      path=rel, project=project,
                      next="repair the map with project_map.py create --force")

    gate = wg.check(workspace, absolute, session, project)
    reasons = [str(item) for item in gate.get("reasons") or []]
    if gate.get("allow"):
        reset(workspace, session, key)
        return result(True, "retry_write", True,
                      "an approved decision covers this path",
                      path=rel, project=project, decision=gate.get("decision", ""),
                      map_state=gate.get("map_state"),
                      map_sha256=gate.get("map_sha256", ""),
                      next="retry the write now")
    if any("owned by session" in item for item in reasons):
        return result(False, "owner_conflict", False,
                      "another session owns this write surface",
                      path=rel, project=project, reasons=reasons,
                      next="do not retry; the owner or a human must release the surface")
    count = bump(workspace, session, key)
    if count > MAX_ATTEMPTS:
        return result(False, "retry_budget_exhausted", False,
                      "too many recovery attempts for this path",
                      path=rel, project=project, attempts=count, reasons=reasons)
    if gate.get("needs_decision"):
        scope = f'--scope "{rel}"'
        return result(True, "prepare_decision", True,
                      "no approved decision covers this path",
                      path=rel, project=project, attempts=count, reasons=reasons,
                      next=(f"auto_decision.py prepare --project {project} "
                            f'--task "{task or "repair the blocked write"}" '
                            f"{scope} --session {session or '<session>'}"))
    return result(False, "blocked", False,
                  "the write gate denied this path for a non-recoverable reason",
                  path=rel, project=project, attempts=count, reasons=reasons,
                  next="inspect the reasons; do not bypass a valid security denial")


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-recovery-")
        os.makedirs(os.path.join(root, ".agents", "evidence"), exist_ok=True)
        registry = os.path.join(root, ".agents", "registry")
        os.makedirs(registry, exist_ok=True)
        with open(os.path.join(registry, "PROJECT_PROFILES.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"profiles": [{"id": "demo", "path": "."}]}, handle)
        with open(os.path.join(root, "app.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("x = 1\n")
        for name in ("6.0_final_merge", "_canonical"):
            os.makedirs(os.path.join(root, name), exist_ok=True)
        return root

    @case("a stale or missing map is refreshed without authorization")
    def _():
        root = fixture()
        value = diagnose(root, os.path.join(root, "app.py"), "fix", "s1", "demo")
        assert value["action"] in ("map_refreshed", "prepare_decision"), value
        assert pm.verify("demo", root)["state"] == "CURRENT"
        return value["action"]

    @case("a comma-joined scope returns a split instruction")
    def _():
        root = fixture()
        value = diagnose(root, os.path.join(root, "6.0_final_merge,_canonical"),
                         "canonical", "s1", "demo")
        assert value["action"] == "split_scope", value
        assert "comma-joined" in value["reason"], value
        return "split scope"

    @case("a path outside the workspace suggests the real root")
    def _():
        root = fixture()
        outside = tempfile.mkdtemp(prefix="eos-recovery-out-")
        value = diagnose(root, os.path.join(outside, "x.py"), "fix", "s1", "demo")
        assert value["action"] == "wrong_workspace_root", value
        return "wrong root"

    @case("a repeated blocked path exhausts the retry budget")
    def _():
        root = fixture()
        first = diagnose(root, os.path.join(root, "app.py"), "fix", "s1", "demo")
        assert first["action"] == "map_refreshed", first
        for _index in range(MAX_ATTEMPTS):
            value = diagnose(root, os.path.join(root, "app.py"), "fix", "s1", "demo")
            assert value["action"] == "prepare_decision", value
        value = diagnose(root, os.path.join(root, "app.py"), "fix", "s1", "demo")
        assert value["action"] == "retry_budget_exhausted", value
        assert value["ok"] is False
        return "bounded"

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
    parser = argparse.ArgumentParser(prog="recovery.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("diagnose")
    p.add_argument("--path", required=True)
    p.add_argument("--task", default="")
    p.add_argument("--project", default="")
    p.add_argument("--session", default=os.environ.get("OPENCODE_SESSION_ID", "") or "")
    p.add_argument("--workspace", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=lambda args: _cmd(args))
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


def _cmd(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    value = diagnose(workspace, args.path, args.task, args.session, args.project)
    if args.json:
        print(json.dumps(value, ensure_ascii=True))
    else:
        print(f"{value['action'].upper()}: {value['reason']}")
        for key in ("path", "project", "map_state", "decision", "attempts", "next"):
            if value.get(key):
                print(f"  {key}: {value[key]}")
    return 0 if value["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
