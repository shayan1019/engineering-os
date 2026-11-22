#!/usr/bin/env python3
"""Install the EOS OpenCode plugins.

Two files, two jobs:

  eos-activation.js  injects the bounded EOS context block (always-on)
  eos-guard.js       denies a write that has no approved decision, and denies a
                     shell command that can write (permission layer)

They are installed together and backed up together. The guard is not optional:
installing activation without the guard would make EOS look active while the
write contract is unenforced.

Usage:
  plugin_install.py install   [--project DIR] [--host opencode] [--force]
  plugin_install.py uninstall [--project DIR] [--host opencode]
  plugin_install.py status    [--project DIR] [--host opencode]
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

PLUGINS = ("eos-activation.js", "eos-guard.js")
PLUGIN_NAME = PLUGINS[0]  # kept for callers that imported the old constant
MANIFEST_DIR = ep.STATE_DIR


def plugin_dir(project: str, host: str) -> str:
    if host == "opencode":
        return os.path.join(project, ".opencode", "plugins")
    return os.path.join(project, f".{host}", "plugins")


def legacy_plugin_dir(project: str, host: str) -> str:
    """The retired singular path. Read only to remove it safely."""
    if host == "opencode":
        return os.path.join(project, ".opencode", "plugin")
    return os.path.join(project, f".{host}", "plugin")


def global_plugin_dir(host: str) -> str:
    if host != "opencode":
        return ""
    config = os.environ.get("OPENCODE_CONFIG_DIR")
    if not config:
        config = os.path.join(os.path.expanduser("~"), ".config", "opencode")
    return os.path.join(config, "plugins")


def canonical(name: str) -> str:
    return os.path.join(ep.ENGINE_ROOT, "host-plugins", "opencode", name)


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


def install_one(project: str, host: str, force: bool, name: str) -> int:
    source = canonical(name)
    if not os.path.isfile(source):
        print(f"BLOCKED: {name} is not in this engine ({source})")
        return 1
    target_dir = plugin_dir(project, host)
    target = os.path.join(target_dir, name)
    legacy = os.path.join(legacy_plugin_dir(project, host), name)
    if os.path.isfile(legacy):
        os.remove(legacy)
        print(f"  retired legacy plugin: {legacy}")
    global_dir = global_plugin_dir(host)
    if global_dir and os.path.abspath(target_dir) != os.path.abspath(global_dir):
        global_target = os.path.join(global_dir, name)
        if os.path.isfile(global_target):
            print(f"SKIP: global plugin already provides {name}; workspace copy not created")
            return 0
    manifest = load_manifest()
    key = os.path.abspath(target)

    if os.path.isfile(target):
        existing = open(target, "r", encoding="utf-8", errors="replace").read()
        current = open(source, "r", encoding="utf-8", errors="replace").read()
        if existing == current:
            print(f"PASS: {name} already current at {target}")
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
        backup = os.path.join(os.path.dirname(target_dir), f"_eos-backup-{stamp()}", name)
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.copyfile(target, backup)

    shutil.copyfile(source, target)
    manifest.setdefault("installed", {})[key] = {
        "status": "installed",
        "host": host,
        "project": os.path.abspath(project),
        "backup": backup,
        "installed_at": stamp(),
        "source": source,
    }
    save_manifest(manifest)
    print(f"PASS: {name} installed at {target}")
    if backup:
        print(f"  previous version backed up to {backup}")
    return 0


def install(project: str, host: str, force: bool) -> int:
    for name in PLUGINS:
        rc = install_one(project, host, force, name)
        if rc != 0:
            return rc
    print(f"  manifest: {manifest_path()}")
    return 0


def uninstall(project: str, host: str) -> int:
    target_dir = plugin_dir(project, host)
    manifest = load_manifest()
    for name in PLUGINS:
        target = os.path.join(target_dir, name)
        key = os.path.abspath(target)
        entry = manifest.get("installed", {}).get(key)
        if not entry:
            print(f"PASS: no EOS plugin recorded for {target}")
            continue
        if os.path.isfile(target):
            os.remove(target)
        backup = entry.get("backup")
        if backup and os.path.isfile(backup):
            shutil.copyfile(backup, target)
            print(f"  previous version restored from {backup}")
        manifest["installed"].pop(key, None)
        print(f"PASS: {name} removed from {target}")
    save_manifest(manifest)
    legacy_dir = legacy_plugin_dir(project, host)
    for name in PLUGINS:
        legacy = os.path.join(legacy_dir, name)
        if os.path.isfile(legacy):
            os.remove(legacy)
            print(f"PASS: retired legacy plugin removed: {legacy}")
    try:
        os.rmdir(target_dir)  # only succeeds when the host's directory is empty
    except OSError:
        pass
    try:
        os.rmdir(legacy_dir)
    except OSError:
        pass
    return 0


def status(project: str, host: str) -> int:
    target_dir = plugin_dir(project, host)
    global_dir = global_plugin_dir(host)
    manifest = load_manifest()
    ok = True
    for name in PLUGINS:
        target = os.path.join(target_dir, name)
        entry = manifest.get("installed", {}).get(os.path.abspath(target))
        exists = os.path.isfile(target)
        provided_by_global = bool(global_dir) and os.path.isfile(os.path.join(global_dir, name))
        source = canonical(name)
        current = False
        if exists and os.path.isfile(source):
            current = (open(target, "r", encoding="utf-8", errors="replace").read()
                       == open(source, "r", encoding="utf-8", errors="replace").read())
        print(f"{name}")
        print(f"  path        : {target}")
        print(f"  present     : {exists}")
        print(f"  current     : {current}")
        print(f"  global      : {provided_by_global}")
        print(f"  eos managed : {bool(entry)}")
        if entry:
            print(f"  installed at: {entry.get('installed_at')}")
        ok = ok and (current or provided_by_global)
    return 0 if ok else 1


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
