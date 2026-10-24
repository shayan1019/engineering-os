#!/usr/bin/env python3
"""codex_host.py ? EOS adapter for OpenAI Codex CLI.

Codex reads skills from .agents/skills/ and .codex/skills/.
EOS agents are not natively supported by Codex (no subagent fleet).
EOS provides rules and gate commands only.

Usage:
  python engine/tools/codex_host.py install [--force]
  python engine/tools/codex_host.py status
  python engine/tools/codex_host.py uninstall
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep

CODEX_HOME = os.path.join(os.path.expanduser("~"), ".codex")
SKILLS_DIR = os.path.join(CODEX_HOME, "skills")
AGENTS_DIR = None  # Codex has no native agent directory
MANIFEST_PATH = os.path.join(CODEX_HOME, ".eos-codex-manifest.json")


def _load_manifest():
    if not os.path.isfile(MANIFEST_PATH):
        return {"version": 1, "skills": {}}
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "skills": {}}


def _save_manifest(m):
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(m, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, MANIFEST_PATH)


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def install_skills(force=False):
    src = os.path.join(ep.ENGINE_ROOT, "skills")
    if not os.path.isdir(src):
        print(f"BLOCKED: skills not found at {src}")
        return 1
    os.makedirs(SKILLS_DIR, exist_ok=True)
    manifest = _load_manifest()
    installed = 0
    skipped = 0
    for name in sorted(os.listdir(src)):
        sdir = os.path.join(src, name)
        if not os.path.isdir(sdir):
            continue
        ddir = os.path.join(SKILLS_DIR, name)
        if os.path.exists(ddir) and not force:
            print(f"SKIP: {name} (exists, use --force)")
            skipped += 1
            continue
        if os.path.exists(ddir):
            shutil.rmtree(ddir)
        shutil.copytree(sdir, ddir)
        installed += 1
        print(f"INSTALL: {name}")
        manifest.setdefault("skills", {})[ddir] = {
            "source": sdir,
            "installed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
    _save_manifest(manifest)
    print(f"PASS: installed {installed}, skipped {skipped}")
    return 0


def status():
    skills = [d for d in os.listdir(SKILLS_DIR) if os.path.isdir(os.path.join(SKILLS_DIR, d))] if os.path.isdir(SKILLS_DIR) else []
    manifest = _load_manifest()
    print(f"skills: {len(skills)} total, {len(manifest.get('skills', {}))} EOS-owned in {SKILLS_DIR}")
    print("agents: not supported by Codex (no native agent fleet)")
    print("mcp: supported via codex mcp")
    return 0


def uninstall():
    manifest = _load_manifest()
    removed = 0
    for path in list(manifest.get("skills", {}).keys()):
        if os.path.isdir(path):
            shutil.rmtree(path)
            removed += 1
            print(f"REMOVE: {os.path.basename(path)}/")
        del manifest["skills"][path]
    _save_manifest(manifest)
    print(f"PASS: removed {removed}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("install")
    i.add_argument("--force", action="store_true")
    sub.add_parser("status")
    sub.add_parser("uninstall")
    args = ap.parse_args()
    if args.cmd == "install":
        return install_skills(args.force)
    if args.cmd == "status":
        return status()
    if args.cmd == "uninstall":
        return uninstall()
    ap.print_help()
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
