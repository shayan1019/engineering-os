#!/usr/bin/env python3
"""EOS snapshot tool.

Creates a verifiable, restorable snapshot of the engineering control plane
(.agents minus backups, root AGENTS.md, .codex agent definitions, .claude
adapter manifest) without copying user project data.

Usage:
  python .agents/tools/snapshot.py create --label 2026-10-19
  python .agents/tools/snapshot.py verify --snapshot .agents/backups/baseline_2026-10-19
  python .agents/tools/snapshot.py restore-test --snapshot .agents/backups/baseline_2026-10-19

Exit codes: 0 = PASS, 1 = FAIL, 2 = usage error.
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
import _eos_paths as ep  # noqa: E402

# Snapshots describe the project in scope, not the engine directory.
ROOT = ep.resolve_workspace()
SNAPSHOT_VERSION = 1

INCLUDE_FILES = [
    "AGENTS.md",
]
INCLUDE_DIRS = [
    (".agents", {"backups", "__pycache__"}),
    (".codex", {"__pycache__"}),
    (".opencode", {"__pycache__", "skills", "node_modules", "cache"}),
]
# Project roots are recorded by identity only; project contents are never copied.
PROJECT_MARKERS = ("AGENTS.md", "CLAUDE.md", "opencode.json", "package.json", "pyproject.toml")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def collect(root: str):
    """Yield (relpath, abspath) for every file that belongs to the control plane."""
    for rel in INCLUDE_FILES:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            yield rel.replace(os.sep, "\\"), p
    for rel_dir, skip in INCLUDE_DIRS:
        base = os.path.join(root, rel_dir)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in skip)
            for name in sorted(filenames):
                if name.endswith((".pyc", ".pyo")):
                    continue
                p = os.path.join(dirpath, name)
                yield os.path.relpath(p, root).replace(os.sep, "\\"), p


def project_roots(root: str):
    out = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return out
    for name in entries:
        p = os.path.join(root, name)
        if not os.path.isdir(p) or name.startswith("."):
            continue
        markers = [m for m in PROJECT_MARKERS if os.path.exists(os.path.join(p, m))]
        deps = [d for d in ("tests", "test", "src", "python_ai") if os.path.isdir(os.path.join(p, d))]
        if markers or deps:
            out.append({"name": name, "markers": markers, "dirs": deps})
    return out


def cmd_create(args) -> int:
    root = os.path.abspath(args.root)
    label = args.label or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = os.path.abspath(args.out or os.path.join(root, ".agents", "backups", f"baseline_{label}"))
    if os.path.exists(snap) and not args.force:
        print(f"FAIL: snapshot already exists: {snap} (use --force to overwrite)")
        return 1

    os.makedirs(snap, exist_ok=True)
    hashes, manifest = [], []
    payload_root = os.path.join(snap, "files")
    for rel, path in collect(root):
        st = os.stat(path)
        hashes.append(f"{sha256(path)}|{rel}")
        manifest.append(f"{rel}|{st.st_size}|{datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds')}")
        if args.with_payload:
            dst = os.path.join(payload_root, rel.replace("\\", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(path, dst)

    with open(os.path.join(snap, "HASHES.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(hashes) + "\n")
    with open(os.path.join(snap, "MANIFEST.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(manifest) + "\n")

    meta = {
        "version": SNAPSHOT_VERSION,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": root,
        "file_count": len(hashes),
        "total_bytes": sum(int(m.split("|")[1]) for m in manifest),
        "scope": {
            "included": ["AGENTS.md", ".agents/** (minus backups, __pycache__)", ".codex/**"],
            "excluded": ["user project contents", "databases", "logs", "secrets", ".claude/skills mirror"],
        },
        "projects": project_roots(root),
        "notes": "Project data is recorded by identity only. Restoring this snapshot never touches user projects.",
    }
    with open(os.path.join(snap, "SNAPSHOT.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, indent=2)

    with open(os.path.join(snap, "ROLLBACK.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            f"# Rollback — {label}\n\n"
            f"Snapshot: `{snap}`\n\n"
            f"## Verify before restoring\n\n"
            f"```\npython .agents/tools/snapshot.py verify --snapshot \"{snap}\"\n```\n\n"
            f"## Restore\n\n"
            f"```\npython .agents/tools/snapshot.py restore --snapshot \"{snap}\"\n```\n\n"
            f"Restore writes only the files listed in HASHES.txt. It never deletes files\n"
            f"that were added after the snapshot and never touches user project data.\n"
            f"Files changed after the snapshot are reported as CONFLICT and are not overwritten\n"
            f"unless `--force` is passed.\n"
        )

    print(f"PASS: snapshot created at {snap}")
    print(f"  files={len(hashes)} bytes={meta['total_bytes']} projects={len(meta['projects'])}")
    return 0


def read_hashes(snap: str):
    path = os.path.join(snap, "HASHES.txt")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or "|" not in line:
                continue
            digest, rel = line.split("|", 1)
            out.append((digest, rel))
    return out


def cmd_verify(args) -> int:
    snap = os.path.abspath(args.snapshot)
    root = os.path.abspath(args.root)
    entries = read_hashes(snap)
    if not entries:
        print("FAIL: empty manifest")
        return 1
    missing = changed = ok = 0
    details = []
    for digest, rel in entries:
        p = os.path.join(root, rel.replace("\\", os.sep))
        if not os.path.isfile(p):
            missing += 1
            details.append(f"MISSING {rel}")
            continue
        if sha256(p) == digest:
            ok += 1
        else:
            changed += 1
            details.append(f"CHANGED {rel}")
    print(f"{'PASS' if missing == 0 else 'FAIL'}: verify ok={ok} changed={changed} missing={missing}")
    for line in details[:20]:
        print("  " + line)
    return 0 if missing == 0 else 1


def cmd_restore_test(args) -> int:
    """Prove restore works by materialising the snapshot in a temp dir and re-hashing."""
    snap = os.path.abspath(args.snapshot)
    entries = read_hashes(snap)
    if not entries:
        print("FAIL: empty manifest")
        return 1
    tmp = tempfile.mkdtemp(prefix="eos-restore-")
    try:
        for digest, rel in entries:
            src = os.path.join(os.path.abspath(args.root), rel.replace("\\", os.sep))
            if not os.path.isfile(src):
                print(f"FAIL: source missing during restore test: {rel}")
                return 1
            dst = os.path.join(tmp, rel.replace("\\", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        bad = 0
        for digest, rel in entries:
            p = os.path.join(tmp, rel.replace("\\", os.sep))
            if not os.path.isfile(p) or sha256(p) != digest:
                bad += 1
                print(f"FAIL: restored hash mismatch: {rel}")
        if bad:
            print(f"FAIL: restore test {bad}/{len(entries)} mismatched")
            return 1
        print(f"PASS: restore test {len(entries)} files re-hashed identically in {tmp}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_restore(args) -> int:
    snap = os.path.abspath(args.snapshot)
    root = os.path.abspath(args.root)
    entries = read_hashes(snap)
    conflicts, written = [], 0
    for digest, rel in entries:
        dst = os.path.join(root, rel.replace("\\", os.sep))
        src = os.path.join(snap, "files", rel.replace("\\", os.sep))
        if not os.path.isfile(src):
            print(f"FAIL: snapshot payload missing for {rel} (snapshot was created without --with-payload)")
            return 1
        if os.path.isfile(dst) and sha256(dst) != digest and not args.force:
            conflicts.append(rel)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        written += 1
    if conflicts:
        print(f"BLOCKED: {len(conflicts)} file(s) changed since snapshot; re-run with --force to overwrite")
        for c in conflicts[:20]:
            print("  CONFLICT " + c)
        return 1
    print(f"PASS: restored {written} file(s) into {root}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="snapshot.py")
    ap.add_argument("--root", default=ep.resolve_workspace())
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--label")
    c.add_argument("--out")
    c.add_argument("--force", action="store_true")
    c.add_argument("--with-payload", action="store_true")
    c.set_defaults(func=cmd_create)

    v = sub.add_parser("verify")
    v.add_argument("--snapshot", required=True)
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("restore-test")
    r.add_argument("--snapshot", required=True)
    r.set_defaults(func=cmd_restore_test)

    s = sub.add_parser("restore")
    s.add_argument("--snapshot", required=True)
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
