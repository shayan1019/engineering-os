#!/usr/bin/env python3
"""Mechanical proof that EOS agents are registered with the intended boundaries.

Reads the runtime's own view (`opencode debug agents`), not the files on disk.
A green file tree with an unregistered agent is exactly the failure this test
exists to catch.

Usage:
  python .agents/tools/agent_dispatch_test.py [--json]

Exit codes: 0 = PASS, 1 = FAIL, 2 = opencode unavailable.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

# `opencode debug agents` must run in the project whose agents are being checked.
ROOT = ep.resolve_workspace()

EXPECTED = {
    "chief-architect": {"mode": "primary", "edit": "deny"},
    "architect": {"mode": "subagent", "edit": "deny"},
    "implementer": {"mode": "subagent", "edit": "allow"},
    "reviewer": {"mode": "subagent", "edit": "deny"},
    "verifier": {"mode": "subagent", "edit": "deny"},
    "security-auditor": {"mode": "subagent", "edit": "deny"},
    "test-engineer": {"mode": "subagent", "edit": "glob"},
    "performance-engineer": {"mode": "subagent", "edit": "deny"},
    "evidence-analyst": {"mode": "subagent", "edit": "deny"},
}


def effective(agent: dict, action: str):
    """Last matching rule wins, mirroring OpenCode's permission evaluation."""
    result = None
    for rule in agent.get("permissions", []):
        if rule.get("action") != action:
            continue
        resource = rule.get("resource", "")
        if resource == "*" or resource.startswith("*") or "*" in resource:
            result = rule.get("effect")
        else:
            result = rule.get("effect")
    return result


def edit_mode(agent: dict) -> str:
    rules = [r for r in agent.get("permissions", []) if r.get("action") == "edit"]
    if not rules:
        return "unset"
    globbed = [r for r in rules if r.get("resource") not in ("*",)]
    if globbed and any(r.get("effect") == "allow" for r in globbed):
        return "glob"
    returns = {r.get("effect") for r in rules}
    if returns == {"deny"}:
        return "deny"
    if "allow" in returns:
        return "allow"
    return "unset"


def task_allowlist(agent: dict):
    # The runtime normalises the `task` permission to the `subagent` action.
    # Verified by probe: config {"permission":{"task":...}} -> action "subagent".
    rules = [r for r in agent.get("permissions", [])
             if r.get("action") in ("task", "subagent")]
    if not rules:
        return None
    allow = [r["resource"] for r in rules if r.get("effect") == "allow" and r.get("resource") != "*"]
    star_allow = any(r.get("resource") == "*" and r.get("effect") == "allow" for r in rules)
    return {"allow": sorted(allow), "star_allow": star_allow}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    exe = shutil.which("opencode")
    if not exe:
        print("BLOCKED: opencode executable not found on PATH")
        return 2
    proc = subprocess.run([exe, "debug", "agents"], cwd=ROOT, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        print(f"BLOCKED: 'opencode debug agents' failed rc={proc.returncode}\n{proc.stderr[:800]}")
        return 2
    try:
        agents = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("BLOCKED: runtime output is not JSON; cannot verify registration mechanically")
        return 2

    by_id = {a.get("id"): a for a in agents}
    findings = []

    for name, want in EXPECTED.items():
        agent = by_id.get(name)
        if not agent:
            findings.append({"severity": "ERROR", "agent": name, "detail": "not registered in runtime"})
            continue
        got_mode = agent.get("mode")
        if got_mode != want["mode"]:
            findings.append({"severity": "ERROR", "agent": name,
                             "detail": f"mode {got_mode!r} != expected {want['mode']!r}"})
        got_edit = edit_mode(agent)
        if got_edit != want["edit"]:
            findings.append({"severity": "ERROR", "agent": name,
                             "detail": f"edit boundary {got_edit!r} != expected {want['edit']!r}"})

    chief = by_id.get("chief-architect")
    if chief:
        tasks = task_allowlist(chief)
        if not tasks:
            findings.append({"severity": "ERROR", "agent": "chief-architect",
                             "detail": "no task permission block: any subagent can be invoked"})
        else:
            if tasks["star_allow"]:
                findings.append({"severity": "ERROR", "agent": "chief-architect",
                                 "detail": "task '*' is allowed; delegation is unbounded"})
            missing = sorted(set(EXPECTED) - {"chief-architect"} - set(tasks["allow"]))
            if missing:
                findings.append({"severity": "ERROR", "agent": "chief-architect",
                                 "detail": f"cannot delegate to: {', '.join(missing)}"})

    for name in EXPECTED:
        agent = by_id.get(name)
        if not agent or name == "chief-architect":
            continue
        tasks = task_allowlist(agent)
        if tasks and (tasks["star_allow"] or tasks["allow"]):
            findings.append({"severity": "WARN", "agent": name,
                             "detail": f"subagent can delegate: {tasks['allow'] or ['*']}"})

    if "general" not in by_id:
        findings.append({"severity": "WARN", "agent": "general",
                         "detail": "built-in general agent not visible; delegation fallback unclear"})

    errors = [f for f in findings if f["severity"] == "ERROR"]
    if args.json:
        print(json.dumps({"registered": len(by_id), "errors": errors, "findings": findings}, indent=2))
    else:
        print(f"runtime registered agents: {len(by_id)}")
        print(f"EOS agents verified      : {len(EXPECTED) - len([e for e in errors if e['detail'] == 'not registered in runtime'])}/{len(EXPECTED)}")
        for f in errors:
            print(f"  ERROR {f['agent']}: {f['detail']}")
        for f in [x for x in findings if x["severity"] == "WARN"]:
            print(f"  WARN  {f['agent']}: {f['detail']}")
        print("PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
