#!/usr/bin/env python3
"""Install the EOS host plugin into a project (and remove it again).

The plugin is what makes EOS intelligence automatic: the host calls it on its
own session lifecycle (`prompt` at admission, `context` before a model request),
and EOS answers with the project, the rules, the relevant skills, the
specialist set and the gate. The user keeps using the host's own Plan or Build
mode. A V1 host that only offers `chat.params` is still served by `server()`.

Rules this installer follows
----------------------------
* It never overwrites a file it did not write. If a plugin with the same name
  exists and is not in the manifest, the install is refused.
* The previous version is backed up next to the plugin, outside the directory
  the host scans for plugins.
* The install is reversible: `uninstall` removes exactly what the manifest
  lists and restores the backup.
* The plugin is *additive*. It adds no tool and no permission; it only adds a
  context block to the session.

Usage
-----
    python plugin_install.py install   [--project <dir>] [--host opencode]
    python plugin_install.py status    [--project <dir>]
    python plugin_install.py uninstall [--project <dir>]
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

PLUGIN_NAME = "eos-activation.js"
CANONICAL = os.path.join(ep.ENGINE_ROOT, "host-plugins", "opencode", PLUGIN_NAME)
MANIFEST_DIR = ep.STATE_DIR


def plugin_dir(project: str, host: str) -> str:
    if host == "opencode":
        return os.path.join(project, ".opencode", "plugin")
    return os.path.join(project, f".{host}", "plugin")


def manifest_path() -> str:
    return os.path.join(MANIFEST_DIR, "plugin.manifest.json")


def load_manifest() -> dict:
    try:
        with open(manifest_path(), "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "installed": {}}


def save_manifest(data: dict) -> None:
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    with open(manifest_path(), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def install(project: str, host: str, force: bool) -> int:
    if not os.path.isfile(CANONICAL):
        print(f"BLOCKED: the plugin is not in this engine ({CANONICAL})")
        return 1
    target_dir = plugin_dir(project, host)
    target = os.path.join(target_dir, PLUGIN_NAME)
    manifest = load_manifest()
    key = os.path.abspath(target)

    if os.path.isfile(target):
        existing = open(target, "r", encoding="utf-8", errors="replace").read()
        canonical = open(CANONICAL, "r", encoding="utf-8", errors="replace").read()
        if existing == canonical:
            print(f"PASS: plugin already current at {target}")
            manifest["installed"].setdefault(key, {})["status"] = "current"
            save_manifest(manifest)
            return 0
        if key not in manifest.get("installed", {}) and not force:
            print(f"BLOCKED: {target} exists and was not written by EOS. "
                  f"Move it aside or re-run with --force.")
            return 1

    os.makedirs(target_dir, exist_ok=True)
    backup = None
    if os.path.isfile(target):
        # The backup lives outside the plugin directory: the host scans that
        # directory and would load a backup as a second plugin.
        backup = os.path.join(os.path.dirname(target_dir), f"_eos-backup-{stamp()}", PLUGIN_NAME)
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.copyfile(target, backup)

    shutil.copyfile(CANONICAL, target)
    manifest.setdefault("installed", {})[key] = {
        "status": "installed",
        "host": host,
        "project": os.path.abspath(project),
        "backup": backup,
        "installed_at": stamp(),
        "source": CANONICAL,
    }
    save_manifest(manifest)
    print(f"PASS: plugin installed at {target}")
    if backup:
        print(f"  previous version backed up to {backup}")
    print(f"  manifest: {manifest_path()}")
    return 0


def uninstall(project: str, host: str) -> int:
    target_dir = plugin_dir(project, host)
    target = os.path.join(target_dir, PLUGIN_NAME)
    manifest = load_manifest()
    key = os.path.abspath(target)
    entry = manifest.get("installed", {}).get(key)
    if not entry:
        print(f"PASS: no EOS plugin recorded for {target}")
        return 0
    if os.path.isfile(target):
        os.remove(target)
    backup = entry.get("backup")
    if backup and os.path.isfile(backup):
        shutil.copyfile(backup, target)
        print(f"  previous version restored from {backup}")
    manifest["installed"].pop(key, None)
    save_manifest(manifest)
    try:
        os.rmdir(target_dir)  # only succeeds when the host's directory is empty
    except OSError:
        pass
    print(f"PASS: plugin removed from {target}")
    return 0


def status(project: str, host: str) -> int:
    target = os.path.join(plugin_dir(project, host), PLUGIN_NAME)
    manifest = load_manifest()
    key = os.path.abspath(target)
    entry = manifest.get("installed", {}).get(key)
    exists = os.path.isfile(target)
    current = False
    if exists and os.path.isfile(CANONICAL):
        current = (open(target, "r", encoding="utf-8", errors="replace").read()
                   == open(CANONICAL, "r", encoding="utf-8", errors="replace").read())
    print(f"plugin path   : {target}")
    print(f"present       : {exists}")
    print(f"current       : {current}")
    print(f"eos managed   : {bool(entry)}")
    if entry:
        print(f"installed at  : {entry.get('installed_at')}")
    return 0 if (exists and current) else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="plugin_install.py")
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--project", default="")
    ap.add_argument("--host", default="opencode")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    project = os.path.abspath(args.project) if args.project else ep.resolve_workspace()
    if args.action == "install":
        return install(project, args.host, args.force)
    if args.action == "uninstall":
        return uninstall(project, args.host)
    return status(project, args.host)


if __name__ == "__main__":
    sys.exit(main())
