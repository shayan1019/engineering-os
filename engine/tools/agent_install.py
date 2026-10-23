#!/usr/bin/env python3
"""EOS specialist agent installer.

Writes versioned agent definitions into the EOS-owned directory
(.agents/eos/agents) and mirrors them into an OpenCode-discoverable location.

Format follows OpenCode V2: an ordered `permissions` list (last matching rule
wins) plus `request.body` for model parameters. The legacy `permission` map and
top-level `temperature` are deliberately not used.

Nothing outside these two directories is touched and no existing agent file is
overwritten: a conflicting file is reported as CONFLICT and skipped unless
--force is passed. Global installs are reversible via `uninstall`.

Usage:
  python engine/tools/agent_install.py install --target project
  python engine/tools/agent_install.py install --target global [--force]
  python engine/tools/agent_install.py status
  python engine/tools/agent_install.py uninstall --target global
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

ROOT = ep.ENGINE_ROOT
CANONICAL = ep.AGENTS_CANONICAL
GLOBAL_TARGET = os.path.expanduser(os.path.join("~", ".config", "opencode", "agents"))
# Project installs target the project being operated on. `--project <dir>` (or
# EOS_WORKSPACE, or cwd) selects it; the engine's own directory is never used.
PROJECT_ROOT = ep.resolve_workspace(os.environ.get("EOS_PROJECT"))
PROJECT_TARGET = os.path.join(PROJECT_ROOT, ".opencode", "agents")
MANIFEST = os.path.join(ROOT, "eos", "agents.manifest.json") if os.path.isdir(
    os.path.join(ROOT, "eos")) else os.path.join(ep.STATE_DIR, "agents.manifest.json")

# Ordered permission rules: broad rule first, exception after. Last match wins.
DENY_EDIT = [("edit", "*", "deny")]
DENY_EXTERNAL = [("external_directory", "*", "deny")]
DENY_WEB = [("webfetch", "*", "deny"), ("websearch", "*", "deny")]
NO_DELEGATION = [("subagent", "*", "deny")]
# The `shell: *` rule is the last resort for a command that matches no narrower
# pattern. `ask` there meant the user was prompted for permission on every
# ordinary command (`cat`, `ls`, `npm test`, `cargo test`), which is what made
# Build mode unusable. The read-only roles now refuse the unmatched command
# outright - deterministic, and no human in the loop - and the single writer
# may run project commands, with the destructive ones still denied.
SHELL_DENY = [("shell", "*", "deny")]

SAFE_READ = [("shell", "git status*", "allow"), ("shell", "git diff*", "allow"),
             ("shell", "git log*", "allow"), ("shell", "git show*", "allow"),
             ("shell", "rg *", "allow"), ("shell", "grep *", "allow"),
             ("shell", "cat *", "allow"), ("shell", "ls *", "allow"),
             ("shell", "dir *", "allow"), ("shell", "type *", "allow"),
             ("shell", "head *", "allow"), ("shell", "tail *", "allow"),
             ("shell", "wc *", "allow"), ("shell", "find *", "allow"),
             ("shell", "tree *", "allow"), ("shell", "pwd", "allow"),
             ("shell", "python engine/tools/skill_index.py *", "allow"),
             ("shell", "python engine/tools/graph_freshness.py *", "allow")]


def delegations(names):
    return NO_DELEGATION + [("subagent", n, "allow") for n in names]


SPECIALISTS = ["architect", "implementer", "reviewer", "verifier", "security-auditor",
               "test-engineer", "performance-engineer", "evidence-analyst", "explore"]

AGENTS = {
    "chief-architect": {
        "mode": "primary",
        "steps": 30,
        "description": "EOS chief architect. Owns task intake, project resolution, specialist selection and the final decision. Coordinates, never writes code and never bypasses the quality gate.",
        "temperature": "0.1",
        "permissions": (DENY_EDIT + DENY_EXTERNAL + DENY_WEB
                        + SHELL_DENY
                        + [("shell", "git status*", "allow"), ("shell", "git diff*", "allow"),
                           ("shell", "python engine/tools/skill_index.py *", "allow"),
                           ("shell", "python engine/tools/route_task.py *", "allow"),
                           ("shell", "opencode debug agents", "allow")]
                        + delegations(SPECIALISTS)),
        "prompt": """You are the EOS Chief Architect.

You coordinate; you do not implement. You have no edit permission by design.

On every task:
1. Resolve the project: `python engine/tools/route_task.py --task "<task>"`.
2. Resolve skills: `python engine/tools/skill_index.py resolve --task "<task>" --project <id>`.
3. Select the smallest competent team. Do not invoke every specialist. Always-on: you.
   architecture change -> architect; code change -> implementer; material change -> reviewer;
   release or claim -> verifier; risk -> security-auditor; test work -> test-engineer;
   perf work -> performance-engineer; evidence or provenance claim -> evidence-analyst.
4. Give every specialist the same project id, task text and evidence paths.
5. A PASS claim requires a quality_gate artifact. Your own statement is never evidence.
6. Report: decision, evidence file paths, PASS/FAIL/BLOCKED, rollback path.

Never claim a capability you did not verify in this session. If a tool or agent is
missing, report BLOCKED with the exact missing piece.
""",
    },
    "architect": {
        "mode": "subagent",
        "steps": 20,
        "description": "Designs approaches, compares alternatives and maps blast radius. Read-only: proposes, never edits.",
        "temperature": "0.2",
        "permissions": DENY_EDIT + DENY_EXTERNAL + DENY_WEB + SHELL_DENY + SAFE_READ + NO_DELEGATION,
        "prompt": """You are the EOS Architect. You are read-only.

Produce: (1) the constraint set that actually applies, (2) at least two viable
approaches, (3) a comparison on cost, blast radius, reversibility and testability,
(4) a recommendation with the evidence that supports it, (5) the exact files and
contracts that would change.

Do not write code. Do not assume a graph covers a language it does not cover: MQL5
is not indexed by graphify in this workspace, so MQL5 impact analysis must be marked
PARTIAL and backed by an explicit fallback (`python engine/tools/graph_freshness.py fallback`),
never FULL. Run `graph_freshness.py report` before trusting any graph answer.
""",
    },
    "implementer": {
        "mode": "subagent",
        "steps": 40,
        "description": "The single authorised writer. Implements the approved change inside the assigned workspace only.",
        "temperature": "0.1",
        "permissions": ([("edit", "*", "allow")] + DENY_EXTERNAL + DENY_WEB
                        + SHELL_DENY
                        + [("shell", "python *", "allow"), ("shell", "pytest *", "allow"),
                           ("shell", "cargo test*", "allow"), ("shell", "cargo build*", "allow"),
                           ("shell", "npm test*", "allow"), ("shell", "npm run build*", "allow"),
                           ("shell", "git status*", "allow"), ("shell", "git diff*", "allow"),
                           ("shell", "git add *", "deny"), ("shell", "git commit *", "deny"),
                           ("shell", "git push *", "deny"), ("shell", "rm *", "deny")]
                        + NO_DELEGATION),
        "prompt": """You are the EOS Implementer. You are the only agent allowed to write code,
and only inside the workspace you were assigned.

Rules:
- One writer per workspace. If another implementer is active, stop and report CONFLICT.
- Read the surrounding code before writing. Follow existing patterns; do not add comments
  unless they explain a non-obvious decision.
- Never write to production databases, live trading systems, .env files, credentials or any
  path outside the assigned workspace. Commits, pushes and deletions are denied: propose them.
- After writing, run the project's own tests. Report the exact command and its real output.
- You may not declare PASS. Only the verifier can, based on a quality_gate artifact.
""",
    },
    "reviewer": {
        "mode": "subagent",
        "steps": 20,
        "description": "Adversarial independent reviewer. Tries to break the change; finds missing tests, wrong assumptions and contract drift.",
        "temperature": "0.1",
        "permissions": DENY_EDIT + DENY_EXTERNAL + DENY_WEB + SHELL_DENY + SAFE_READ + NO_DELEGATION,
        "prompt": """You are the EOS Reviewer. You are read-only and adversarial.

Your job is to refute the change, not to approve it. Check:
- Does it actually solve the stated problem, or a neighbouring one?
- Which existing behaviour could regress? Name the contract and the file.
- Are the tests proving the behaviour, or only exercising the code path?
- Does it introduce a second source of truth, a silent fallback, or a swallowed error?
- Is any claim unsupported by an artifact produced in this session?

Report findings as: severity, file:line, problem, required fix. If you find nothing,
say so explicitly and list what you checked. Never edit files.
""",
    },
    "verifier": {
        "mode": "subagent",
        "steps": 12,
        "description": "Independent verification. Runs the constrained quality gate and issues PASS/FAIL/BLOCKED from its artifact only.",
        "temperature": "0.0",
        "permissions": (DENY_EDIT + DENY_EXTERNAL + DENY_WEB + SHELL_DENY
                        + [("shell", "python engine/tools/quality_gate.py *", "allow"),
                           ("shell", "python engine/tools/promote.py verify*", "allow"),
                           ("shell", "python engine/tools/snapshot.py verify*", "allow")]
                        + NO_DELEGATION),
        "prompt": """You are the EOS Verifier. You are read-only except for the gate runner.

You may not approve anything from your own judgement. Your verdict comes from an
artifact produced by `python engine/tools/quality_gate.py run`:
exit code, output hash, project id, baseline id.

If the artifact is missing, stale, from another project, or lacks a command record,
your verdict is BLOCKED. If any required command failed, your verdict is FAIL.

Report exactly: verdict, artifact path, commands executed, failures, rollback path.
""",
    },
    "security-auditor": {
        "mode": "subagent",
        "steps": 20,
        "description": "Security, secrets, dependencies and permission-boundary audit.",
        "temperature": "0.1",
        "permissions": (DENY_EDIT + DENY_EXTERNAL + DENY_WEB + SHELL_DENY + SAFE_READ
                        + [("shell", "python engine/tools/secret_scan.py *", "allow")]
                        + NO_DELEGATION),
        "prompt": """You are the EOS Security Auditor. You are read-only.

Audit: secrets in the diff, credentials in config, dependency risk, permission boundaries,
external input handling, logging of sensitive data, and any path that lets an agent write
outside its workspace.

Report: severity, evidence path, exploit path, required fix. If a scanner is not installed,
report the capability as MISSING rather than assuming the area is clean.
""",
    },
    "test-engineer": {
        "mode": "subagent",
        "steps": 30,
        "description": "Designs and writes tests only. Cannot modify production code.",
        "temperature": "0.1",
        "permissions": ([("edit", "*", "deny"), ("edit", "tests/**", "allow"),
                         ("edit", "test/**", "allow"), ("edit", "**/tests/**", "allow")]
                        + DENY_EXTERNAL + DENY_WEB + SHELL_DENY
                        + [("shell", "pytest *", "allow"), ("shell", "python -m pytest *", "allow")]
                        + NO_DELEGATION),
        "prompt": """You are the EOS Test Engineer. You may write only inside test directories.

Write tests that fail for the right reason before the fix and pass after it. Prefer one
behaviour per test. Do not weaken assertions to make a suite green, do not skip, do not
mock the unit under test.

Report: test file, behaviour covered, red evidence, green evidence. If you cannot make a
test fail first, say so: the test may be proving nothing.
""",
    },
    "performance-engineer": {
        "mode": "subagent",
        "steps": 20,
        "description": "Profiling and benchmark comparison. Measured numbers only.",
        "temperature": "0.1",
        "permissions": (DENY_EDIT + DENY_EXTERNAL + DENY_WEB + SHELL_DENY
                        + [("shell", "pytest *", "allow")] + NO_DELEGATION),
        "prompt": """You are the EOS Performance Engineer. You are read-only.

Measure before and after with the same command, input and machine. Report raw numbers,
variance and the number of runs. Never claim an improvement from a single run or from a
different workload. If a benchmark cannot be reproduced, report INCONCLUSIVE.
""",
    },
    "evidence-analyst": {
        "mode": "subagent",
        "steps": 20,
        "description": "Data provenance and claim verification. Decides whether evidence supports the claim.",
        "temperature": "0.0",
        "permissions": DENY_EDIT + DENY_EXTERNAL + DENY_WEB + SHELL_DENY + SAFE_READ + NO_DELEGATION,
        "prompt": """You are the EOS Evidence Analyst. You are read-only.

For every claim produce: source of truth, query or method, row counts, exclusions, and the
exact disagreement between sources. If sources disagree, do not pick one silently: report
the discrepancy and mark the claim INSUFFICIENT_EVIDENCE.

Known workspace lesson: a merged table can hold two trading accounts at once. Always check
account or tenant isolation before treating a dataset as a single population.
""",
    },
}


def render(name: str, spec: dict) -> str:
    lines = ["---", f"description: {spec['description']}", f"mode: {spec['mode']}"]
    if spec.get("steps"):
        lines.append(f"steps: {spec['steps']}")
    lines.append("request:")
    lines.append("  body:")
    lines.append(f"    temperature: {spec['temperature']}")
    lines.append("permissions:")
    for action, resource, effect in spec["permissions"]:
        lines.append(f"  - action: {action}")
        lines.append(f"    resource: \"{resource}\"")
        lines.append(f"    effect: {effect}")
    lines.append("---")
    lines.append("")
    lines.append(spec["prompt"].strip())
    lines.append("")
    return "\n".join(lines)


def manifest_path(target: str) -> str:
    name = "agents.global.manifest.json" if target == "global" else "agents.manifest.json"
    base = os.path.join(ROOT, "eos") if os.path.isdir(os.path.join(ROOT, "eos")) else ep.STATE_DIR
    return os.path.join(base, name)


def _disp(path: str) -> str:
    """Human-readable, portable form; absolute when the file lives outside the
    engine (the state directory legitimately does)."""
    try:
        rel = os.path.relpath(path, ROOT)
    except ValueError:
        return path.replace(os.sep, "/")
    return path.replace(os.sep, "/") if rel.startswith("..") else rel.replace(os.sep, "/")


def set_project(explicit):
    """Point the project install at the project being operated on."""
    global PROJECT_ROOT, PROJECT_TARGET
    if explicit:
        PROJECT_ROOT = ep.resolve_workspace(explicit)
        PROJECT_TARGET = os.path.join(PROJECT_ROOT, ".opencode", "agents")


def install(target: str, force: bool) -> int:
    os.makedirs(CANONICAL, exist_ok=True)
    dest = GLOBAL_TARGET if target == "global" else PROJECT_TARGET
    os.makedirs(dest, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # The backup must NOT live inside the agents directory: the host scans that
    # directory recursively and would register the backup copies as live agents,
    # which is how the old permission contracts kept coming back.
    backup_dir = None
    if target == "global":
        backup_dir = os.path.join(os.path.dirname(GLOBAL_TARGET), f"_eos-backup-{stamp}")
    written, conflicts, backed_up = [], [], []

    for name, spec in AGENTS.items():
        text = render(name, spec)
        canonical_path = os.path.join(CANONICAL, f"{name}.md")
        with open(canonical_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        dst = os.path.join(dest, f"{name}.md")
        if os.path.isfile(dst):
            current = open(dst, encoding="utf-8", errors="replace").read()
            if current != text and not force:
                conflicts.append(name)
                continue
            if backup_dir:
                os.makedirs(backup_dir, exist_ok=True)
                shutil.copyfile(dst, os.path.join(backup_dir, f"{name}.md"))
                backed_up.append(name)
        shutil.copyfile(canonical_path, dst)
        written.append(name)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "canonical": _disp(CANONICAL),
        "target": dest,
        "target_kind": target,
        "agents": sorted(AGENTS),
        "written": written,
        "conflicts": conflicts,
        "backup_dir": backup_dir,
        "backed_up": backed_up,
        "reversible": True,
        "format": "opencode-v2 ordered permissions list",
    }
    path = manifest_path(target)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)

    print(f"{'PASS' if not conflicts else 'BLOCKED'}: wrote {len(written)} agent(s) to {dest}")
    if conflicts:
        print("  existing files differ and were NOT overwritten: " + ", ".join(conflicts))
    if backed_up:
        print(f"  backed up {len(backed_up)} replaced file(s) to {backup_dir}")
    print(f"  canonical: {CANONICAL}")
    print(f"  manifest : {_disp(path)}")
    return 0 if not conflicts else 1


def uninstall(target: str) -> int:
    dest = GLOBAL_TARGET if target == "global" else PROJECT_TARGET
    path = manifest_path(target)
    if os.path.isfile(path):
        manifest = json.load(open(path, encoding="utf-8"))
        names = manifest.get("written") or sorted(AGENTS)
        backup_dir = manifest.get("backup_dir")
    else:
        names, backup_dir = sorted(AGENTS), None

    removed = restored = 0
    for name in names:
        p = os.path.join(dest, f"{name}.md")
        if os.path.isfile(p):
            os.remove(p)
            removed += 1
    if backup_dir and os.path.isdir(backup_dir):
        for name in os.listdir(backup_dir):
            shutil.copyfile(os.path.join(backup_dir, name), os.path.join(dest, name))
            restored += 1
        shutil.rmtree(backup_dir, ignore_errors=True)
    print(f"PASS: removed {removed} EOS agent file(s) from {dest}; restored {restored} previous file(s)")
    return 0


def status() -> int:
    print(f"canonical: {CANONICAL} exists={os.path.isdir(CANONICAL)}")
    for label, path in (("project", PROJECT_TARGET), ("global", GLOBAL_TARGET)):
        present = []
        if os.path.isdir(path):
            present = sorted(f[:-3] for f in os.listdir(path) if f.endswith(".md"))
        print(f"{label:8} {path} -> {len(present)}: {', '.join(present[:12])}")
    for label in ("project", "global"):
        path = manifest_path(label)
        if os.path.isfile(path):
            m = json.load(open(path, encoding="utf-8"))
            print(f"manifest[{label}]: {m['generated_at']} written={len(m['written'])} conflicts={m['conflicts']}")
    return 0


def _fallback(spec: dict, action: str):
    """Effect of the last `<action> *` rule.

    OpenCode applies the most specific (last matching) rule, and `*` is what an
    unmatched resource falls through to. Reporting the fallback is what tells
    the user whether Build mode will prompt, refuse or proceed.
    """
    effect = None
    for a, resource, e in spec["permissions"]:
        if a == action and resource == "*":
            effect = e
    return effect


def edit_scope(spec: dict) -> str:
    """`all` when any file may be written, `scoped` when only listed paths may.

    A bare "writes: yes" would read as unrestricted for the test engineer, who
    may write tests/** and nothing else.
    """
    allowed = [(r, e) for a, r, e in spec["permissions"] if a == "edit" and e == "allow"]
    if not allowed:
        return "no"
    return "all" if any(r == "*" for r, _e in allowed) else "scoped"


def agent_facts(name: str, spec: dict) -> dict:
    return {
        "name": name,
        "mode": spec["mode"],
        "steps": spec.get("steps"),
        "temperature": spec["temperature"],
        "writes": edit_scope(spec),
        "shell_fallback": _fallback(spec, "shell"),
        "web_fallback": _fallback(spec, "webfetch"),
        "external_directory": _fallback(spec, "external_directory"),
        "delegates_to": sorted(r for a, r, e in spec["permissions"]
                               if a == "subagent" and e == "allow"),
        "description": spec["description"],
        "installed": {
            "canonical": os.path.isfile(os.path.join(CANONICAL, f"{name}.md")),
            "project": os.path.isfile(os.path.join(PROJECT_TARGET, f"{name}.md")),
            "global": os.path.isfile(os.path.join(GLOBAL_TARGET, f"{name}.md")),
        },
    }


def cmd_list(as_json: bool) -> int:
    rows = [agent_facts(n, s) for n, s in AGENTS.items()]
    if as_json:
        print(json.dumps({"agents": rows, "count": len(rows)}, indent=2))
        return 0
    print(f"{'agent':22} {'mode':8} {'writes':7} {'shell*':7} {'delegates':9} installed")
    for r in rows:
        where = "/".join(k for k, v in r["installed"].items() if v) or "-"
        print(f"{r['name']:22} {r['mode']:8} {r['writes']:7} "
              f"{str(r['shell_fallback']):7} {len(r['delegates_to']):9} {where}")
    print(f"\n{len(rows)} roles. `eos agents explain <name>` shows the ordered rule list; "
          f"`eos agents test` proves the live runtime agrees.")
    return 0


def cmd_explain(name: str, as_json: bool, prompt: bool) -> int:
    spec = AGENTS.get(name)
    if spec is None:
        print(f"unknown agent: {name}")
        print("known: " + ", ".join(sorted(AGENTS)))
        return 2
    facts = agent_facts(name, spec)
    if as_json:
        facts["permissions"] = [{"action": a, "resource": r, "effect": e}
                                for a, r, e in spec["permissions"]]
        if prompt:
            facts["prompt"] = spec["prompt"]
        print(json.dumps(facts, indent=2))
        return 0
    print(f"{name}  ({facts['mode']})")
    print(f"  {facts['description']}")
    print(f"  steps={facts['steps']} temperature={facts['temperature']} writes={facts['writes']}")
    print(f"  shell fallback={facts['shell_fallback']} webfetch={facts['web_fallback']} "
          f"external_directory={facts['external_directory']}")
    print(f"  delegates to: {', '.join(facts['delegates_to']) or '(none)'}")
    print("  permissions (ordered; the last matching rule wins):")
    for a, r, e in spec["permissions"]:
        print(f"    {a:20} {r:40} {e}")
    installed = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in facts["installed"].items())
    print(f"  installed: {installed}")
    print("  proven by: eos agents test  (live runtime view, not the files on disk)")
    if prompt:
        print("\n" + spec["prompt"].strip())
    else:
        print(f"  prompt: eos agents explain {name} --prompt")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="agent_install.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--target", choices=("project", "global"), default="project")
    i.add_argument("--force", action="store_true")
    i.add_argument("--project", help="project directory to install into")
    sub.add_parser("status")
    u = sub.add_parser("uninstall")
    u.add_argument("--target", choices=("project", "global"), default="project")
    u.add_argument("--project")
    ls = sub.add_parser("list")
    ls.add_argument("--json", action="store_true")
    ls.add_argument("--project")
    ex = sub.add_parser("explain")
    ex.add_argument("name")
    ex.add_argument("--json", action="store_true")
    ex.add_argument("--prompt", action="store_true")
    ex.add_argument("--project")
    args = ap.parse_args()
    set_project(getattr(args, "project", None))
    if args.cmd == "install":
        return install(args.target, args.force)
    if args.cmd == "uninstall":
        return uninstall(args.target)
    if args.cmd == "list":
        return cmd_list(args.json)
    if args.cmd == "explain":
        return cmd_explain(args.name, args.json, args.prompt)
    return status()


if __name__ == "__main__":
    sys.exit(main())

