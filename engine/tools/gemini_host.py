#!/usr/bin/env python3
"""gemini_host.py ? EOS adapter for Google Gemini CLI.

Gemini CLI supports skills via `gemini skills install/link` and subagents.
EOS agents are installed to ~/.gemini/skills/ or linked.

Usage:
  python engine/tools/gemini_host.py install [--force]
  python engine/tools/gemini_host.py status
  python engine/tools/gemini_host.py uninstall
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep

GEMINI_HOME = os.path.join(os.path.expanduser("~"), ".gemini")
SKILLS_DIR = os.path.join(GEMINI_HOME, "skills")
MANIFEST_PATH = os.path.join(GEMINI_HOME, ".eos-gemini-manifest.json")


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
    # Check if gemini binary is available
    gemini_bin = None
    import shutil as sh
    gemini_bin = sh.which("gemini")
    print(f"gemini binary: {gemini_bin or 'NOT FOUND'}")
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
