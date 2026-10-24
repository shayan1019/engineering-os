#!/usr/bin/env python3
"""claude_host.py ? EOS adapter for Claude Code.

Installs EOS agents and skills into Claude Code's documented locations:
  agents: ~/.claude/agents/<id>.md
  skills: ~/.claude/skills/<name>/SKILL.md

Safety contract:
- EOS tracks every file it creates in a manifest with SHA-256 of the original.
- On install, existing files that EOS did not create are never overwritten
  without --force; with --force a backup is created first.
- On uninstall, only files listed in the manifest are removed.
- Atomic writes: write to temp file, then rename.
- Path traversal and symlink escapes are rejected.

Usage:
  python engine/tools/claude_host.py install [--force]
  python engine/tools/claude_host.py status
  python engine/tools/claude_host.py uninstall
  python engine/tools/claude_host.py verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep

CLAUDE_HOME = os.path.join(os.path.expanduser("~"), ".claude")
AGENTS_DIR = os.path.join(CLAUDE_HOME, "agents")
SKILLS_DIR = os.path.join(CLAUDE_HOME, "skills")
MANIFEST_PATH = os.path.join(CLAUDE_HOME, ".eos-claude-manifest.json")
BACKUP_DIR = os.path.join(CLAUDE_HOME, ".eos-claude-backups")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_path(base: str, relative: str) -> str | None:
    """Resolve a path and verify it stays inside base. Rejects traversal and symlinks."""
    if not relative or os.path.isabs(relative):
        return None
    # Reject path traversal components
    parts = relative.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    full = os.path.normpath(os.path.join(base, relative))
    # Verify the resolved path is still inside base
    base_real = os.path.realpath(base)
    full_real = os.path.realpath(full) if os.path.exists(full) else full
    if not full_real.startswith(base_real + os.sep) and full_real != base_real:
        return None
    return full


def _load_manifest() -> dict:
    if not os.path.isfile(MANIFEST_PATH):
        return {"version": 1, "created_at": None, "agents": {}, "skills": {}}
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"version": 1, "created_at": None, "agents": {}, "skills": {}}
        data.setdefault("agents", {})
        data.setdefault("skills", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "created_at": None, "agents": {}, "skills": {}}


def _save_manifest(manifest: dict) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, MANIFEST_PATH)


def _atomic_copy(src: str, dst: str) -> None:
    """Copy src to dst atomically via temp file + rename."""
    dst_dir = os.path.dirname(dst)
    os.makedirs(dst_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dst_dir, prefix=".eos-tmp-")
    try:
        with os.fdopen(fd, "wb") as out:
            with open(src, "rb") as inp:
                shutil.copyfileobj(inp, out)
        os.replace(tmp, dst)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _backup_file(path: str, manifest: dict) -> str | None:
    """Back up a file before overwriting. Returns backup path or None."""
    if not os.path.exists(path):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    basename = os.path.basename(path)
    backup_path = os.path.join(BACKUP_DIR, f"{basename}.{ts}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def install_agents(force: bool = False) -> int:
    src = ep.AGENTS_CANONICAL
    if not os.path.isdir(src):
        print(f"BLOCKED: canonical agents not found at {src}")
        return 1

    os.makedirs(AGENTS_DIR, exist_ok=True)
    manifest = _load_manifest()
    installed = 0
    skipped = 0
    backed_up = 0

    for name in sorted(os.listdir(src)):
        if not name.endswith(".md"):
            continue
        safe = _safe_path(AGENTS_DIR, name)
        if safe is None:
            print(f"BLOCKED: unsafe path for {name}")
            continue
        src_path = os.path.join(src, name)
        dst_path = safe

        # Check if file exists and is not EOS-owned
        if os.path.exists(dst_path):
            existing_hash = _sha256_file(dst_path)
            eos_owned = dst_path in manifest.get("agents", {}) and \
                        manifest["agents"][dst_path].get("hash") == existing_hash
            if not eos_owned and not force:
                print(f"SKIP: {name} (exists, not EOS-owned; use --force)")
                skipped += 1
                continue
            if not eos_owned and force:
                bp = _backup_file(dst_path, manifest)
                if bp:
                    backed_up += 1
                    print(f"BACKUP: {name} -> {os.path.basename(bp)}")

        # Record original hash if file existed
        original_hash = _sha256_file(dst_path) if os.path.exists(dst_path) else None

        _atomic_copy(src_path, dst_path)
        new_hash = _sha256_file(dst_path)

        manifest.setdefault("agents", {})[dst_path] = {
            "source": src_path,
            "hash": new_hash,
            "original_hash": original_hash,
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        installed += 1
        print(f"INSTALL: {name}")

    _save_manifest(manifest)
    print(f"PASS: installed {installed}, skipped {skipped}, backed up {backed_up}")
    return 0


def install_skills(force: bool = False) -> int:
    src = os.path.join(ep.ENGINE_ROOT, "skills")
    if not os.path.isdir(src):
        print(f"BLOCKED: skills not found at {src}")
        return 1

    os.makedirs(SKILLS_DIR, exist_ok=True)
    manifest = _load_manifest()
    installed = 0
    skipped = 0
    backed_up = 0

    for name in sorted(os.listdir(src)):
        sdir = os.path.join(src, name)
        if not os.path.isdir(sdir):
            continue
        safe = _safe_path(SKILLS_DIR, name)
        if safe is None:
            print(f"BLOCKED: unsafe path for {name}")
            continue
        ddir = safe

        if os.path.exists(ddir):
            # Check if EOS-owned: manifest has entry and at least one file hash matches
            eos_owned = False
            if ddir in manifest.get("skills", {}):
                entry = manifest["skills"][ddir]
                # Verify by checking if files match (new format) or SKILL.md (old format)
                files_map = entry.get("files")
                if files_map:
                    skill_md = os.path.join(ddir, "SKILL.md")
                    if os.path.isfile(skill_md) and "SKILL.md" in files_map:
                        eos_owned = _sha256_file(skill_md) == files_map["SKILL.md"]
                elif entry.get("skill_md_hash"):
                    skill_md = os.path.join(ddir, "SKILL.md")
                    if os.path.isfile(skill_md):
                        eos_owned = _sha256_file(skill_md) == entry["skill_md_hash"]

            if not eos_owned and not force:
                print(f"SKIP: {name} (exists, not EOS-owned; use --force)")
                skipped += 1
                continue
            if not eos_owned and force:
                # Backup entire directory
                os.makedirs(BACKUP_DIR, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_path = os.path.join(BACKUP_DIR, f"{name}.{ts}.bak")
                shutil.copytree(ddir, backup_path)
                backed_up += 1
                print(f"BACKUP: {name} -> {os.path.basename(backup_path)}")
                shutil.rmtree(ddir)

        # Record original files (all files currently in ddir) for restore
        original_files = {}
        if os.path.isdir(ddir):
            for root, _, files in os.walk(ddir):
                for f in files:
                    fp = os.path.join(root, f)
                    rel = os.path.relpath(fp, ddir)
                    original_files[rel] = _sha256_file(fp)

        # Backup if force-overwriting an existing directory
        backup_path = None
        if os.path.exists(ddir) and force:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = os.path.join(BACKUP_DIR, f"{name}.{ts}.bak")
            # Avoid backup collision
            counter = 0
            while os.path.exists(backup_path):
                counter += 1
                backup_path = os.path.join(BACKUP_DIR, f"{name}.{ts}.{counter}.bak")
            shutil.copytree(ddir, backup_path)
            print(f"BACKUP: {name} -> {os.path.basename(backup_path)}")
            shutil.rmtree(ddir)

        shutil.copytree(sdir, ddir, dirs_exist_ok=True)

        # Record ONLY files that exist in the source (EOS-owned files)
        # Do NOT record user-added files that happen to be in ddir
        installed_files = {}
        for root, _, files in os.walk(sdir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, sdir)
                dst_fp = os.path.join(ddir, rel)
                if os.path.isfile(dst_fp):
                    installed_files[rel] = _sha256_file(dst_fp)

        manifest.setdefault("skills", {})[ddir] = {
            "source": sdir,
            "files": installed_files,
            "original_files": original_files,
            "backup_path": backup_path,
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        installed += 1
        print(f"INSTALL: {name}")

    _save_manifest(manifest)
    print(f"PASS: installed {installed}, skipped {skipped}, backed up {backed_up}")
    return 0


def status() -> int:
    manifest = _load_manifest()
    agents = [f for f in os.listdir(AGENTS_DIR) if f.endswith(".md")] if os.path.isdir(AGENTS_DIR) else []
    skills = [d for d in os.listdir(SKILLS_DIR) if os.path.isdir(os.path.join(SKILLS_DIR, d))] if os.path.isdir(SKILLS_DIR) else []
    eos_agents = len(manifest.get("agents", {}))
    eos_skills = len(manifest.get("skills", {}))
    print(f"agents: {len(agents)} total, {eos_agents} EOS-owned in {AGENTS_DIR}")
    print(f"skills: {len(skills)} total, {eos_skills} EOS-owned in {SKILLS_DIR}")
    # Verify integrity
    problems = 0
    for path, info in manifest.get("agents", {}).items():
        if os.path.exists(path):
            current = _sha256_file(path)
            if current != info.get("hash"):
                print(f"  MODIFIED: {os.path.basename(path)} (EOS-owned file changed externally)")
                problems += 1
        else:
            print(f"  MISSING: {os.path.basename(path)} (EOS-owned file removed)")
            problems += 1
    if problems:
        print(f"  {problems} integrity issue(s)")
    return 0


def verify() -> int:
    """Verify that EOS-owned files are intact and foreign files are untouched."""
    manifest = _load_manifest()
    problems = 0
    # Check EOS-owned files
    for path, info in manifest.get("agents", {}).items():
        if not os.path.exists(path):
            print(f"MISSING (EOS-owned): {path}")
            problems += 1
            continue
        current = _sha256_file(path)
        if current != info.get("hash"):
            print(f"MODIFIED (EOS-owned): {path}")
            problems += 1
    for path, info in manifest.get("skills", {}).items():
        files_map = info.get("files")
        if files_map:
            # New format: check all tracked files
            for rel, expected_hash in files_map.items():
                fp = os.path.join(path, rel)
                if not os.path.exists(fp):
                    print(f"MISSING (EOS-owned): {fp}")
                    problems += 1
                    continue
                current = _sha256_file(fp)
                if current != expected_hash:
                    print(f"MODIFIED (EOS-owned): {fp}")
                    problems += 1
        elif info.get("skill_md_hash"):
            # Old format: check SKILL.md only
            skill_md = os.path.join(path, "SKILL.md")
            if not os.path.exists(skill_md):
                print(f"MISSING (EOS-owned): {skill_md}")
                problems += 1
                continue
            current = _sha256_file(skill_md)
            if current != info.get("skill_md_hash"):
                print(f"MODIFIED (EOS-owned): {skill_md}")
                problems += 1
        else:
            print(f"NO HASH (EOS-owned): {path}")
            problems += 1
    if problems:
        print(f"FAIL: {problems} integrity issue(s)")
        return 1
    print("PASS: all EOS-owned files intact")
    return 0


def uninstall() -> int:
    manifest = _load_manifest()
    removed = 0
    skipped = 0
    # Remove only EOS-owned agents (verify hash first)
    for path, info in list(manifest.get("agents", {}).items()):
        if os.path.exists(path):
            current = _sha256_file(path)
            if current == info.get("hash"):
                os.remove(path)
                removed += 1
                print(f"REMOVE: {os.path.basename(path)}")
                del manifest["agents"][path]
            else:
                print(f"SKIP: {os.path.basename(path)} (modified externally, not removing)")
                skipped += 1
                # Keep manifest entry for modified files
        else:
            # File already removed; clean up manifest
            del manifest["agents"][path]

    # Remove only EOS-owned skill files, preserving foreign files
    for path, info in list(manifest.get("skills", {}).items()):
        if not os.path.isdir(path):
            del manifest["skills"][path]
            continue
        eos_files = set(info.get("files", {}).keys())
        # Check if any EOS-owned file was modified
        modified = False
        for rel, expected_hash in info.get("files", {}).items():
            fp = os.path.join(path, rel)
            if os.path.exists(fp):
                if _sha256_file(fp) != expected_hash:
                    modified = True
                    break
        if modified:
            print(f"SKIP: {os.path.basename(path)}/ (modified externally, not removing)")
            skipped += 1
            # Keep manifest entry for modified skills
            continue
        # Remove only EOS-owned files, preserve foreign ones
        removed_files = 0
        foreign_files = []
        for root, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, path)
                if rel in eos_files:
                    os.remove(fp)
                    removed_files += 1
                else:
                    foreign_files.append(rel)
        # Remove empty directories
        for root, dirs, files in os.walk(path, topdown=False):
            if not os.listdir(root):
                os.rmdir(root)
        if foreign_files:
            print(f"PARTIAL REMOVE: {os.path.basename(path)}/ ({removed_files} EOS files removed, {len(foreign_files)} foreign files preserved)")
        else:
            print(f"REMOVE: {os.path.basename(path)}/ ({removed_files} files)")
        removed += 1
        del manifest["skills"][path]

    _save_manifest(manifest)
    print(f"PASS: removed {removed}, skipped {skipped}")
    return 0



def restore(backup_path: str) -> int:
    """Restore files from a backup directory created by --force.

    Uses the manifest to determine the original target path. Never guesses
    from backup contents.
    """
    if not os.path.isdir(backup_path):
        print(f"BLOCKED: backup directory not found: {backup_path}")
        return 1
    manifest = _load_manifest()
    restored = 0

    # Find which manifest entry references this backup
    target_path = None
    is_skill = False
    for path, info in manifest.get("skills", {}).items():
        if info.get("backup_path") == backup_path:
            target_path = path
            is_skill = True
            break
    for path, info in manifest.get("agents", {}).items():
        # Agent backups are single files; check if backup contains this agent
        basename = os.path.basename(path)
        if os.path.isfile(os.path.join(backup_path, basename)):
            target_path = path
            is_skill = False
            break

    if target_path is None:
        # No manifest entry references this backup. Do NOT guess from contents.
        print("BLOCKED: no manifest entry references this backup; cannot determine restore target safely")
        return 1

    if is_skill:
        if os.path.exists(target_path):
            # Back up current before restoring
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            cur_backup = os.path.join(BACKUP_DIR, f"{os.path.basename(target_path)}.pre-restore.{ts}.bak")
            shutil.copytree(target_path, cur_backup)
            # Remove only EOS-owned files, preserve foreign
            info = manifest.get("skills", {}).get(target_path, {})
            eos_files = set(info.get("files", {}).keys())
            for root, _, files in os.walk(target_path):
                for f in files:
                    fp = os.path.join(root, f)
                    rel = os.path.relpath(fp, target_path)
                    if rel in eos_files:
                        os.remove(fp)
            # Remove empty dirs
            for root, dirs, files in os.walk(target_path, topdown=False):
                if not os.listdir(root):
                    os.rmdir(root)
        shutil.copytree(backup_path, target_path, dirs_exist_ok=True)
        restored = 1
        print(f"RESTORE: {os.path.basename(target_path)}/ from {os.path.basename(backup_path)}")
    else:
        if os.path.exists(target_path):
            _backup_file(target_path, manifest)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        # Find the agent file in backup
        for item in os.listdir(backup_path):
            if item.endswith(".md"):
                shutil.copy2(os.path.join(backup_path, item), target_path)
                restored = 1
                print(f"RESTORE: {item} -> {target_path}")
                break

    print(f"PASS: restored {restored} item(s) from {backup_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("install")
    i.add_argument("--force", action="store_true")
    sub.add_parser("status")
    sub.add_parser("verify")
    sub.add_parser("uninstall")
    r = sub.add_parser("restore")
    r.add_argument("--backup", required=True, help="backup directory to restore from")
    args = ap.parse_args()

    if args.cmd == "install":
        rc = install_agents(args.force)
        rc2 = install_skills(args.force)
        return rc or rc2
    if args.cmd == "status":
        return status()
    if args.cmd == "verify":
        return verify()
    if args.cmd == "uninstall":
        return uninstall()
    if args.cmd == "restore":
        return restore(args.backup)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
