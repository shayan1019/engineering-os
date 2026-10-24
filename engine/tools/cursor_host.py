#!/usr/bin/env python3
"""cursor_host.py ? EOS adapter for Cursor IDE.

Cursor supports skills via .agents/skills/ and .cursor/skills/.
Cursor supports custom subagents.
EOS agents are installed to .cursor/agents/ (if supported) or rules only.

Usage:
  python engine/tools/cursor_host.py install [--force]
  python engine/tools/cursor_host.py status
  python engine/tools/cursor_host.py verify
  python engine/tools/cursor_host.py uninstall
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep
import host_file_manager as hfm

CURSOR_HOME = os.path.join(os.path.expanduser("~"), ".cursor")
SKILLS_DIR = os.path.join(CURSOR_HOME, "skills")
RULES_DIR = os.path.join(CURSOR_HOME, "rules")
MANIFEST_PATH = os.path.join(CURSOR_HOME, ".eos-cursor-manifest.json")
BACKUP_DIR = os.path.join(CURSOR_HOME, ".eos-cursor-backups")


def _load_manifest():
    return hfm.load_manifest(MANIFEST_PATH)


def _save_manifest(m):
    hfm.save_manifest(MANIFEST_PATH, m)


def install_skills(force=False):
    src = os.path.join(ep.ENGINE_ROOT, "skills")
    if not os.path.isdir(src):
        print(f"BLOCKED: skills not found at {src}")
        return 1
    os.makedirs(SKILLS_DIR, exist_ok=True)
    os.makedirs(RULES_DIR, exist_ok=True)
    manifest = _load_manifest()
    installed = 0
    skipped = 0
    backed_up = 0
    for name in sorted(os.listdir(src)):
        sdir = os.path.join(src, name)
        if not os.path.isdir(sdir):
            continue
        ddir = os.path.join(SKILLS_DIR, name)
        res = hfm.install_directory(sdir, ddir, force=force, backup_root=BACKUP_DIR, manifest=manifest, manifest_key="skills")
        if res["skipped"]:
            print(f"SKIP: {name} (exists, not EOS-owned; use --force)")
            skipped += 1
        else:
            print(f"INSTALL: {name}")
            installed += 1
            if res["backed_up"]:
                backed_up += 1
    # Also install EOS rules file
    rules_path = os.path.join(RULES_DIR, "eos.md")
    rules_content = "# EOS Rules\n\nEOS engineering control plane is active.\nRun `eos guardian scan` for project intelligence.\n"
    with open(rules_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rules_content)
    manifest.setdefault("rules", {})["eos.md"] = {"path": rules_path, "installed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}
    _save_manifest(manifest)
    print(f"PASS: installed {installed}, skipped {skipped}, backed up {backed_up}")
    print(f"Rules written to {rules_path}")
    return 0


def status():
    skills = [d for d in os.listdir(SKILLS_DIR) if os.path.isdir(os.path.join(SKILLS_DIR, d))] if os.path.isdir(SKILLS_DIR) else []
    manifest = _load_manifest()
    print(f"skills: {len(skills)} total, {len(manifest.get('skills', {}))} EOS-owned in {SKILLS_DIR}")
    print(f"rules: {os.path.join(RULES_DIR, 'eos.md')} {'exists' if os.path.isfile(os.path.join(RULES_DIR, 'eos.md')) else 'missing'}")
    print("agents: Cursor supports custom subagents via .cursor/agents/")
    print("mcp: supported via Cursor settings")
    return 0


def verify():
    manifest = _load_manifest()
    problems = 0
    for path, info in manifest.get("skills", {}).items():
        files_map = info.get("files", {})
        if files_map:
            for rel, expected_hash in files_map.items():
                fp = os.path.join(path, rel)
                if not os.path.exists(fp):
                    print(f"MISSING (EOS-owned): {fp}")
                    problems += 1
                    continue
                if hfm.sha256_file(fp) != expected_hash:
                    print(f"MODIFIED (EOS-owned): {fp}")
                    problems += 1
        else:
            print(f"NO HASH (EOS-owned): {path}")
            problems += 1
    if problems:
        print(f"FAIL: {problems} integrity issue(s)")
        return 1
    print("PASS: all EOS-owned files intact")
    return 0


def uninstall():
    manifest = _load_manifest()
    removed = 0
    skipped = 0
    for path in list(manifest.get("skills", {}).keys()):
        res = hfm.uninstall_directory(path, manifest, manifest_key="skills", preserve_foreign=True)
        removed += res["removed"]
        skipped += res["skipped"]
        if res["preserved"]:
            print(f"PARTIAL REMOVE: {os.path.basename(path)}/ ({res['removed']} EOS files removed, {len(res['preserved'])} foreign preserved)")
        elif res["removed"] > 0:
            print(f"REMOVE: {os.path.basename(path)}/ ({res['removed']} files)")
        else:
            print(f"SKIP: {os.path.basename(path)}/ (modified externally)")
    # Remove rules
    rules_path = os.path.join(RULES_DIR, "eos.md")
    if os.path.exists(rules_path):
        os.remove(rules_path)
        print(f"REMOVE: rules/eos.md")
    _save_manifest(manifest)
    print(f"PASS: removed {removed}, skipped {skipped}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("install")
    i.add_argument("--force", action="store_true")
    sub.add_parser("status")
    sub.add_parser("verify")
    sub.add_parser("uninstall")
    args = ap.parse_args()
    if args.cmd == "install":
        return install_skills(args.force)
    if args.cmd == "status":
        return status()
    if args.cmd == "verify":
        return verify()
    if args.cmd == "uninstall":
        return uninstall()
    ap.print_help()
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
