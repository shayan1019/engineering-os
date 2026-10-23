#!/usr/bin/env python3
"""Build the EOS context block for a host session.

Why this exists
---------------
The user should be able to work in the host's own Plan or Build mode and still
get EOS intelligence: the project, the applicable rules, the relevant skills,
the smallest competent specialist set, the gate that will judge the result and
the host's permission boundaries. Selecting "Chief Architect" by hand for every
task was the thing this replaces.

The block is deliberately *lazy*: names, paths and one-line reasons only. The
full instruction text of 200+ skills never enters the model context; a skill is
read when the task actually needs it.

Usage
-----
    python eos_activate.py --task "demo project entry bug" [--project id]
                            [--host opencode] [--max-skills 6] [--json]

Exit codes: 0 = context produced, 1 = the task could not be routed,
2 = the engine could not produce a context block at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

# Roles that may be selected for a task, in the order EOS prefers them. The
# list is the *candidate* set; the router decides which of them are needed.
ROLE_HINTS = [
    ("architect", "design, structure, blast radius, approach comparison"),
    ("implementer", "code change inside the authorised workspace"),
    ("reviewer", "independent adversarial review of a change"),
    ("verifier", "run the quality gate and report the artifact"),
    ("security-auditor", "secret, dependency, installer and release security"),
    ("test-engineer", "test design and test execution"),
    ("performance-engineer", "profiling and measured performance work"),
    ("evidence-analyst", "provenance and evidence-chain claims"),
]

MAX_BLOCK_CHARS = 2400


def load_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def route(task: str) -> dict:
    """Route the task with the project's own router."""
    import subprocess

    router = os.path.join(ep.ENGINE_ROOT, "router", "route_task.py")
    if not os.path.isfile(router):
        return {}
    try:
        proc = subprocess.run(
            [sys.executable, router, "--task", task, "--root", ep.resolve_workspace()],
            capture_output=True, text=True, timeout=120, cwd=ep.resolve_workspace(),
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def skills_for(task: str, project: str, limit: int) -> list:
    """The skill names that are relevant to this task, with their reason."""
    import subprocess

    tool = os.path.join(ep.ENGINE_ROOT, "tools", "skill_index.py")
    if not os.path.isfile(tool):
        return []
    cmd = [sys.executable, tool, "resolve", "--task", task, "-k", str(limit)]
    if project:
        cmd += ["--project", project]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    out = []
    for entry in data.get("chosen", []):
        out.append({
            "name": entry.get("name"),
            "path": entry.get("path"),
            "trust": entry.get("trust"),
            "score": entry.get("score"),
        })
    return out


def gate_for(project: str) -> dict:
    """The gate suite that will judge a change in this project."""
    gates = load_json(os.path.join(ep.REGISTRY, "GATES.json"), {}) or {}
    suites = ((gates.get("projects") or {}).get(project) or {}).get("suites") or {}
    for name, suite in sorted(suites.items()):
        if (suite.get("commands") or []) and suite.get("status") != "NOT_CONFIGURED":
            return {"suite": name, "description": (suite.get("description") or "")[:160]}
    for name, suite in sorted(suites.items()):
        if suite.get("status") == "NOT_CONFIGURED":
            return {"suite": name, "status": "NOT_CONFIGURED",
                    "description": (suite.get("description") or "")[:160]}
    return {}


def rules_files(workspace: str) -> list:
    """The rule files the host will read for this project."""
    found = []
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        path = os.path.join(workspace, name)
        if os.path.isfile(path):
            found.append(name)
    return found


def build(task: str, project: str, host: str, max_skills: int) -> dict:
    workspace = ep.resolve_workspace()
    routed = route(task)
    resolved = routed.get("resolved") or {}
    resolved_project = project or resolved.get("project_id") or ""
    risk = routed.get("risk") or {}
    kg = bool(routed.get("kg_required"))

    # The router already resolves the skills for the task, with the trust label
    # and the reason. A second ranking pass would only produce a second answer
    # to the same question.
    router_skills = routed.get("skills") or {}
    primary = [s for s in (router_skills.get("primary") or [])][:max_skills]
    ranked = {e.get("name"): e for e in (router_skills.get("ranked") or []) if isinstance(e, dict)}
    skill_list = [
        {
            "name": name,
            "trust": (ranked.get(name) or {}).get("trust"),
            "reasons": (ranked.get(name) or {}).get("reasons"),
        }
        for name in primary
    ]
    if not skill_list:
        skill_list = skills_for(task, resolved_project, max_skills)

    agents = routed.get("agents") or {}
    selected = [a for a in (agents.get("specialists") or [])]
    not_needed = [a for a in (agents.get("not_needed") or [])]
    orchestrator = agents.get("orchestrator") or "chief-architect"
    rationale = agents.get("rationale") or {}

    gate = gate_for(resolved_project) if resolved_project else {}

    return {
        "task": task,
        "workspace": workspace,
        "project": resolved_project or None,
        "project_path": resolved.get("path"),
        "project_source": resolved.get("source"),
        "risk": risk.get("level") or "unknown",
        "risk_gate": (risk.get("policy") or {}).get("gate"),
        "kg_required": kg,
        "kg_reason": routed.get("kg_reason"),
        "domains": routed.get("domains") or [],
        "rules": rules_files(workspace),
        "skills": skill_list,
        "suppressed_skills": router_skills.get("suppressed") or [],
        "orchestrator": orchestrator,
        "specialists": selected,
        "not_needed": not_needed,
        "rationale": rationale,
        "roles": [{"name": n, "purpose": p} for n, p in ROLE_HINTS],
        "gate": gate,
        "host": host,
        "engine": ep.ENGINE_ROOT,
    }


def render(ctx: dict) -> str:
    """The block that is injected into the host session.

    Bounded on purpose: a context block that grows with the skill library is
    how an agent framework becomes unusable.
    """
    lines = ["<eos-context>"]
    project = ctx.get("project")
    if project:
        lines.append(f"project: {project} (matched by {ctx.get('project_source') or 'unknown'})"
                     + f" | risk: {ctx.get('risk')}"
                     + (f" | gate policy: {ctx.get('risk_gate')}" if ctx.get("risk_gate") else "")
                     + (" | knowledge-graph work required" if ctx.get("kg_required") else ""))
    else:
        lines.append(f"project: unresolved (routing did not match a known project) | risk: {ctx.get('risk')}")
    if ctx.get("domains"):
        lines.append("domains: " + ", ".join(ctx["domains"][:8]))
    if ctx.get("rules"):
        lines.append("rules in effect: " + ", ".join(ctx["rules"]))
    if ctx.get("skills"):
        names = ", ".join(
            s["name"] + (f" ({s['trust']})" if s.get("trust") else "")
            for s in ctx["skills"][:8]
        )
        lines.append("relevant skills: " + names)
        for sk in ctx["skills"][:8]:
            if sk.get("path"):
                lines.append("  skill path: " + sk["path"])
        lines.append("  read a skill file only when the task actually needs it; record activation via eos skills activate")
    if ctx.get("specialists"):
        lines.append("orchestrator: " + str(ctx.get("orchestrator")))
        lines.append("specialists to use: " + ", ".join(ctx["specialists"]))
        purposes = {r["name"]: r["purpose"] for r in ctx["roles"]}
        rationale = ctx.get("rationale") or {}
        lines.append("why: " + "; ".join(
            f"{name}={rationale.get(name) or purposes.get(name) or 'needed for this task'}"
            for name in ctx["specialists"]
        ))
    if ctx.get("not_needed"):
        lines.append("not needed for this task: " + ", ".join(ctx["not_needed"]))
    if ctx.get("gate"):
        if ctx["gate"].get("status") == "NOT_CONFIGURED":
            lines.append(f"gate: {ctx['gate']['suite']} is NOT_CONFIGURED - report BLOCKED, never PASS")
        else:
            lines.append(f"gate for this project: eos gate run --project {ctx['project']} --suite {ctx['gate']['suite']}")
    lines.append("permissions: only the implementer may write; the others are read-only")
    lines.append("procedure: use the specialists above, delegate the change to the implementer, "
                 "review it, then verify with the gate")
    lines.append("a PASS claim requires a gate artifact; an agent's own statement is never evidence")
    lines.append("</eos-context>")
    block = "\n".join(lines)
    if len(block) > MAX_BLOCK_CHARS:
        block = block[: MAX_BLOCK_CHARS - 20] + "\n...(truncated)</eos-context>"
    return block


def main() -> int:
    ap = argparse.ArgumentParser(prog="eos_activate.py")
    ap.add_argument("--task", default="")
    ap.add_argument("--task-file", default="",
                    help="read the task from a file (the host plugin uses this so that a "
                         "task with quotes or shell characters never reaches a command line)")
    ap.add_argument("--project", default="")
    ap.add_argument("--host", default="opencode")
    ap.add_argument("--max-skills", type=int, default=6)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    task = args.task
    if not task and args.task_file:
        try:
            with open(args.task_file, "r", encoding="utf-8", errors="replace") as fh:
                task = fh.read()
        except OSError as exc:
            print(f"BLOCKED: cannot read the task file: {exc}")
            return 2
    task = (task or "").strip()

    if not task:
        print("BLOCKED: empty task")
        return 1
    ctx = build(task, args.project, args.host, max(1, args.max_skills))
    if args.json:
        print(json.dumps(ctx, indent=2, ensure_ascii=False))
    else:
        print(render(ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
