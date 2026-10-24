#!/usr/bin/env python3
"""host_file_manager.py ? shared safe file management for EOS host adapters.

Provides:
- Ownership tracking via manifest with recursive SHA-256
- Atomic writes (temp file + rename)
- Backup before overwrite with collision-safe naming
- Safe uninstall (only EOS-owned files, preserve foreign/modified)
- Path traversal and symlink protection
- Restore from backup

All host adapters (claude_host, codex_host, gemini_host) should use this
module instead of ad-hoc shutil operations.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

# Import _eos_paths
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def sha256_dir(directory: str) -> dict[str, str]:
    """Compute SHA-256 for every file in a directory, keyed by relative path."""
    result = {}
    if not os.path.isdir(directory):
        return result
    for root, _, files in os.walk(directory):
        for f in files:
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, directory)
            result[rel] = sha256_file(fp)
    return result


def safe_path(base: str, relative: str) -> str | None:
    """Resolve a path and verify it stays inside base. Rejects traversal and symlinks."""
    if not relative or os.path.isabs(relative):
        return None
    parts = relative.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    full = os.path.normpath(os.path.join(base, relative))
    base_real = os.path.realpath(base)
    full_real = os.path.realpath(full) if os.path.exists(full) else full
    if not (full_real == base_real or full_real.startswith(base_real + os.sep)):
        return None
    return full


def atomic_copy(src: str, dst: str) -> None:
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


def backup_dir(src_dir: str, backup_root: str, name: str) -> str:
    """Create a collision-safe backup of a directory. Returns backup path."""
    os.makedirs(backup_root, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = os.path.join(backup_root, f"{name}.{ts}.bak")
    counter = 0
    while os.path.exists(backup_path):
        counter += 1
        backup_path = os.path.join(backup_root, f"{name}.{ts}.{counter}.bak")
    shutil.copytree(src_dir, backup_path)
    return backup_path


def backup_file(src_file: str, backup_root: str) -> str:
    """Create a collision-safe backup of a file. Returns backup path."""
    os.makedirs(backup_root, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    basename = os.path.basename(src_file)
    backup_path = os.path.join(backup_root, f"{basename}.{ts}.bak")
    counter = 0
    while os.path.exists(backup_path):
        counter += 1
        backup_path = os.path.join(backup_root, f"{basename}.{ts}.{counter}.bak")
    shutil.copy2(src_file, backup_path)
    return backup_path


def load_manifest(manifest_path: str) -> dict:
    if not os.path.isfile(manifest_path):
        return {"version": 1, "created_at": None}
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"version": 1, "created_at": None}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "created_at": None, "corrupt": True}


def save_manifest(manifest_path: str, manifest: dict) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, manifest_path)


def install_directory(src_dir: str, dst_dir: str, force: bool = False,
                      backup_root: str = "", manifest: dict = None,
                      manifest_key: str = "items") -> dict:
    """Install a directory from src to dst safely.

    Returns dict with: installed (int), skipped (int), backed_up (int), backup_path (str or None).
    Only tracks files that exist in src_dir (EOS-owned), not user-added files.
    """
    result = {"installed": 0, "skipped": 0, "backed_up": 0, "backup_path": None}

    if not os.path.isdir(src_dir):
        return result

    os.makedirs(os.path.dirname(dst_dir) or ".", exist_ok=True)

    if os.path.exists(dst_dir):
        # Check if EOS-owned (manifest has entry with matching files)
        eos_owned = False
        if manifest and manifest_key in manifest and dst_dir in manifest[manifest_key]:
            entry = manifest[manifest_key][dst_dir]
            if entry.get("files"):
                # Verify at least one tracked file matches
                eos_owned = True
                for rel, expected_hash in entry["files"].items():
                    fp = os.path.join(dst_dir, rel)
                    if os.path.exists(fp):
                        if sha256_file(fp) != expected_hash:
                            eos_owned = False
                            break
                    else:
                        eos_owned = False
                        break

        if not eos_owned and not force:
            result["skipped"] = 1
            return result

        if force and os.path.exists(dst_dir):
            if backup_root:
                bp = backup_dir(dst_dir, backup_root, os.path.basename(dst_dir))
                result["backup_path"] = bp
                result["backed_up"] = 1
            shutil.rmtree(dst_dir)

    # Copy from source (only EOS-owned files)
    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

    # Record only files that exist in source
    installed_files = {}
    for root, _, files in os.walk(src_dir):
        for f in files:
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, src_dir)
            dst_fp = os.path.join(dst_dir, rel)
            if os.path.isfile(dst_fp):
                installed_files[rel] = sha256_file(dst_fp)

    if manifest is not None:
        manifest.setdefault(manifest_key, {})[dst_dir] = {
            "source": src_dir,
            "files": installed_files,
            "backup_path": result["backup_path"],
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    result["installed"] = 1
    return result


def install_file(src_file: str, dst_file: str, force: bool = False,
                 backup_root: str = "", manifest: dict = None,
                 manifest_key: str = "items") -> dict:
    """Install a single file safely. Returns dict with status info."""
    result = {"installed": False, "skipped": False, "backed_up": False, "backup_path": None}

    if not os.path.isfile(src_file):
        return result

    dst_dir = os.path.dirname(dst_file)
    os.makedirs(dst_dir, exist_ok=True)

    if os.path.exists(dst_file):
        eos_owned = False
        if manifest and manifest_key in manifest and dst_file in manifest[manifest_key]:
            entry = manifest[manifest_key][dst_file]
            if entry.get("hash") and sha256_file(dst_file) == entry["hash"]:
                eos_owned = True

        if not eos_owned and not force:
            result["skipped"] = True
            return result

        if force and os.path.exists(dst_file):
            if backup_root:
                result["backup_path"] = backup_file(dst_file, backup_root)
                result["backed_up"] = True

    atomic_copy(src_file, dst_file)
    result["installed"] = True

    if manifest is not None:
        manifest.setdefault(manifest_key, {})[dst_file] = {
            "source": src_file,
            "hash": sha256_file(dst_file),
            "backup_path": result["backup_path"],
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    return result


def uninstall_directory(dst_dir: str, manifest: dict, manifest_key: str = "items",
                       preserve_foreign: bool = True) -> dict:
    """Uninstall EOS-owned files from a directory. Preserve foreign/modified files.

    Returns dict with: removed (int), skipped (int), preserved (list of foreign files).
    """
    result = {"removed": 0, "skipped": 0, "preserved": []}

    if not os.path.isdir(dst_dir):
        if manifest and manifest_key in manifest and dst_dir in manifest[manifest_key]:
            del manifest[manifest_key][dst_dir]
        return result

    info = (manifest.get(manifest_key) or {}).get(dst_dir, {})
    eos_files = set(info.get("files", {}).keys())

    if not eos_files:
        # No tracked files; check if SKILL.md was tracked (old format)
        if info.get("skill_md_hash"):
            eos_files = {"SKILL.md"}

    # Check if any EOS-owned file was modified
    modified = False
    for rel, expected_hash in info.get("files", {}).items():
        fp = os.path.join(dst_dir, rel)
        if os.path.exists(fp) and sha256_file(fp) != expected_hash:
            modified = True
            break

    if modified:
        result["skipped"] = 1
        # Keep manifest entry
        return result

    # Remove only EOS-owned files
    for root, _, files in os.walk(dst_dir):
        for f in files:
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, dst_dir)
            if rel in eos_files:
                os.remove(fp)
                result["removed"] += 1
            else:
                result["preserved"].append(rel)

    # Remove empty directories
    for root, dirs, files in os.walk(dst_dir, topdown=False):
        if os.path.isdir(root) and not os.listdir(root):
            os.rmdir(root)

    # Remove from manifest only if all EOS files were removed
    if dst_dir in (manifest.get(manifest_key) or {}):
        del manifest[manifest_key][dst_dir]

    return result


def uninstall_file(dst_file: str, manifest: dict, manifest_key: str = "items") -> dict:
    """Uninstall a single EOS-owned file. Preserve if modified."""
    result = {"removed": False, "skipped": False}

    if not os.path.exists(dst_file):
        if manifest and manifest_key in manifest and dst_file in manifest[manifest_key]:
            del manifest[manifest_key][dst_file]
        return result

    info = (manifest.get(manifest_key) or {}).get(dst_file, {})
    current_hash = sha256_file(dst_file)
    expected_hash = info.get("hash")

    if expected_hash and current_hash != expected_hash:
        result["skipped"] = True
        # Keep manifest entry
        return result

    os.remove(dst_file)
    result["removed"] = True
    if manifest and manifest_key in manifest and dst_file in manifest[manifest_key]:
        del manifest[manifest_key][dst_file]
    return result


def restore_from_backup(backup_path: str, target_path: str, manifest: dict,
                        manifest_key: str = "items", is_dir: bool = True) -> dict:
    """Restore from a backup to a target path using manifest data.

    Never guesses target from backup contents. Requires explicit target_path.
    Preserves foreign/modified files during restore.
    """
    result = {"restored": 0, "error": None}

    if not os.path.isdir(backup_path):
        result["error"] = f"backup directory not found: {backup_path}"
        return result

    if is_dir:
        if os.path.exists(target_path):
            # Back up current before restoring
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            cur_backup = os.path.join(os.path.dirname(backup_path), f"{os.path.basename(target_path)}.pre-restore.{ts}.bak")
            shutil.copytree(target_path, cur_backup)
            # Remove only EOS-owned files, preserve foreign
            info = (manifest.get(manifest_key) or {}).get(target_path, {})
            eos_files = set(info.get("files", {}).keys())
            for root, _, files in os.walk(target_path):
                for f in files:
                    fp = os.path.join(root, f)
                    rel = os.path.relpath(fp, target_path)
                    if rel in eos_files:
                        os.remove(fp)
            for root, dirs, files in os.walk(target_path, topdown=False):
                if os.path.isdir(root) and not os.listdir(root):
                    os.rmdir(root)
        shutil.copytree(backup_path, target_path, dirs_exist_ok=True)
        result["restored"] = 1
    else:
        if os.path.exists(target_path):
            backup_file(target_path, os.path.dirname(backup_path))
        os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
        # Find the file in backup
        for item in os.listdir(backup_path):
            if os.path.isfile(os.path.join(backup_path, item)):
                shutil.copy2(os.path.join(backup_path, item), target_path)
                result["restored"] = 1
                break

    return result
