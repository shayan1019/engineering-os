#!/usr/bin/env python3
"""Publish the EOS engine from the live control plane into the product repository.

The live control plane (.agents/) stays the working copy. The repository receives
a verified copy plus a manifest of source hashes, so drift is detectable instead
of silent.

Never published: skill bodies, backups, evidence, logs, databases, .env files.

Commands:
  sync    copy engine files into the repo and write engine/MANIFEST.json
  verify  prove the repo copy still matches both the manifest and the sources
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPO = os.path.join(ROOT, "engineering-os")

# (source relative to workspace root, destination relative to repo root)
FILES = [
    "AGENTS.md",
    ".agents/core/FABLE_CORE.md",
    ".agents/core/GRAPH_CORE.md",
    ".agents/registry/PROJECT_PROFILES.json",
    ".agents/registry/AGENT_REGISTRY.json",
    ".agents/registry/ROUTING_MAP.json",
    ".agents/registry/COMPATIBILITY.json",
    ".agents/registry/GATES.json",
    ".agents/registry/ORCHESTRATORS.json",
    ".agents/eos/ALPHA_STATUS.md",
]
DIRS = [
    (".agents/tools", "engine/tools", (".py",)),
    (".agents/router", "engine/router", (".py",)),
    (".agents/eos/agents", "engine/agents", (".md",)),
    (".agents/eos/selftest", "engine/selftest", (".py",)),
]

DEST_MAP = {
    "AGENTS.md": "docs/control-plane.md",
    ".agents/core/FABLE_CORE.md": "engine/core/FABLE_CORE.md",
    ".agents/core/GRAPH_CORE.md": "engine/core/GRAPH_CORE.md",
    ".agents/registry/PROJECT_PROFILES.json": "engine/registry/PROJECT_PROFILES.json",
    ".agents/registry/AGENT_REGISTRY.json": "engine/registry/AGENT_REGISTRY.json",
    ".agents/registry/ROUTING_MAP.json": "engine/registry/ROUTING_MAP.json",
    ".agents/registry/COMPATIBILITY.json": "engine/registry/COMPATIBILITY.json",
    ".agents/registry/GATES.json": "engine/registry/GATES.json",
    ".agents/registry/ORCHESTRATORS.json": "engine/registry/ORCHESTRATORS.json",
    ".agents/eos/ALPHA_STATUS.md": "docs/alpha-status.md",
}
FORBIDDEN_DEST = (".db", ".log", ".env", ".ex5", ".hcc", ".pem", ".key", ".pyc")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def collect():
    out = []
    for rel in FILES:
        src = os.path.join(ROOT, rel)
        if os.path.isfile(src):
            out.append((rel, DEST_MAP.get(rel, rel)))
    for rel_dir, dest_dir, exts in DIRS:
        base = os.path.join(ROOT, rel_dir)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not name.endswith(exts):
                continue
            out.append((f"{rel_dir}/{name}", f"{dest_dir}/{name}"))
    return out


def cmd_sync(args) -> int:
    entries, skipped = [], []
    for rel, dest in collect():
        src = os.path.join(ROOT, rel)
        if not os.path.isfile(src):
            skipped.append(rel)
            continue
        if dest.lower().endswith(FORBIDDEN_DEST):
            skipped.append(f"{rel} (forbidden destination)")
            continue
        dst = os.path.join(REPO, dest)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        entries.append({"source": rel, "path": dest, "sha256": sha256_file(src),
                        "bytes": os.path.getsize(src)})

    manifest = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "policy": {
            "published": ["engine source", "agent definitions", "registry contracts", "control-plane contract"],
            "never_published": ["skill bodies", "backups", "evidence", "logs", "databases",
                                ".env", "credentials", "compiled MQL artifacts"],
        },
        "files": sorted(entries, key=lambda e: e["path"]),
    }
    out = os.path.join(REPO, "engine", "MANIFEST.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"PASS: published {len(entries)} file(s) ({manifest['total_bytes']} bytes) into {REPO}")
    for s in skipped:
        print(f"  skipped: {s}")
    return 0


def cmd_verify(args) -> int:
    manifest_path = os.path.join(REPO, "engine", "MANIFEST.json")
    if not os.path.isfile(manifest_path):
        print("FAIL: manifest missing; run sync first")
        return 1
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    problems = []
    for entry in manifest["files"]:
        dst = os.path.join(REPO, entry["path"])
        src = os.path.join(ROOT, entry["source"])
        if not os.path.isfile(dst):
            problems.append(f"missing in repo: {entry['path']}")
            continue
        if sha256_file(dst) != entry["sha256"]:
            problems.append(f"repo copy modified without sync: {entry['path']}")
        if not os.path.isfile(src):
            problems.append(f"source removed: {entry['source']}")
        elif sha256_file(src) != entry["sha256"]:
            problems.append(f"source changed since publish: {entry['source']}")
    if problems:
        print(f"FAIL: {len(problems)} drift problem(s)")
        for p in problems[:40]:
            print("  - " + p)
        return 1
    print(f"PASS: repo matches manifest and live sources ({manifest['file_count']} file(s), "
          f"published {manifest['generated_at']})")
    return 0


def cmd_status(args) -> int:
    print(f"repo dir : {REPO} exists={os.path.isdir(REPO)}")
    if os.path.isdir(os.path.join(REPO, ".git")):
        out = subprocess.run(["git", "-C", REPO, "log", "--oneline", "-3"],
                             capture_output=True, text=True)
        print("git log  :\n" + (out.stdout or out.stderr))
        subprocess.run(["git", "-C", REPO, "status", "--short"])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="publish.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync").set_defaults(func=cmd_sync)
    sub.add_parser("verify").set_defaults(func=cmd_verify)
    sub.add_parser("status").set_defaults(func=cmd_status)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
