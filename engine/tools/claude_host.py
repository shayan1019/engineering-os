#!/usr/bin/env python3
"""claude_host.py ? EOS adapter for Claude Code.

Installs EOS agents and skills into Claude Code's documented locations:
  agents: ~/.claude/agents/<id>.md
  skills: ~/.claude/skills/<name>/SKILL.md

Usage:
  python engine/tools/claude_host.py install [--force]
  python engine/tools/claude_host.py status
  python engine/tools/claude_host.py uninstall
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep

CLAUDE_HOME = os.path.join(os.path.expanduser("~"), ".claude")
AGENTS_DIR = os.path.join(CLAUDE_HOME, "agents")
SKILLS_DIR = os.path.join(CLAUDE_HOME, "skills")


def install_agents(force: bool = False) -> int:
    src = ep.AGENTS_CANONICAL
    if not os.path.isdir(src):
        print(f"BLOCKED: canonical agents not found at {src}")
        return 1
    os.makedirs(AGENTS_DIR, exist_ok=True)
    count = 0
    for name in sorted(os.listdir(src)):
        if not name.endswith(".md"):
            continue
        s = os.path.join(src, name)
        d = os.path.join(AGENTS_DIR, name)
        if os.path.exists(d) and not force:
            print(f"SKIP: {name} (exists, use --force)")
            continue
        shutil.copy2(s, d)
        count += 1
    print(f"PASS: installed {count} agent(s) to {AGENTS_DIR}")
    return 0


def install_skills(force: bool = False) -> int:
    # Skills are discovered, not copied. EOS skills ship in engine/skills/.
    src = os.path.join(ep.ENGINE_ROOT, "skills")
    if not os.path.isdir(src):
        print(f"BLOCKED: skills not found at {src}")
        return 1
    os.makedirs(SKILLS_DIR, exist_ok=True)
    count = 0
    for name in sorted(os.listdir(src)):
        sdir = os.path.join(src, name)
        if not os.path.isdir(sdir):
            continue
        ddir = os.path.join(SKILLS_DIR, name)
        if os.path.exists(ddir) and not force:
            print(f"SKIP: {name} (exists, use --force)")
            continue
        if os.path.exists(ddir):
            shutil.rmtree(ddir)
        shutil.copytree(sdir, ddir)
        count += 1
    print(f"PASS: installed {count} skill(s) to {SKILLS_DIR}")
    return 0


def status() -> int:
    agents = [f for f in os.listdir(AGENTS_DIR) if f.endswith(".md")] if os.path.isdir(AGENTS_DIR) else []
    skills = [d for d in os.listdir(SKILLS_DIR) if os.path.isdir(os.path.join(SKILLS_DIR, d))] if os.path.isdir(SKILLS_DIR) else []
    print(f"agents: {len(agents)} in {AGENTS_DIR}")
    print(f"skills: {len(skills)} in {SKILLS_DIR}")
    return 0


def uninstall() -> int:
    if os.path.isdir(AGENTS_DIR):
        for f in os.listdir(AGENTS_DIR):
            if f.endswith(".md"):
                os.remove(os.path.join(AGENTS_DIR, f))
    # Don't remove skills dir entirely; only remove EOS-shipped ones
    src = os.path.join(ep.ENGINE_ROOT, "skills")
    if os.path.isdir(src) and os.path.isdir(SKILLS_DIR):
        for name in os.listdir(src):
            ddir = os.path.join(SKILLS_DIR, name)
            if os.path.isdir(ddir):
                shutil.rmtree(ddir)
    print("PASS: Claude Code adapter uninstalled")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("install")
    i.add_argument("--force", action="store_true")
    sub.add_parser("status")
    sub.add_parser("uninstall")
    args = ap.parse_args()

    if args.cmd == "install":
        rc = install_agents(args.force)
        rc2 = install_skills(args.force)
        return rc or rc2
    if args.cmd == "status":
        return status()
    if args.cmd == "uninstall":
        return uninstall()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
