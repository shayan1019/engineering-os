#!/usr/bin/env python3
"""guardian_scan.py ? EOS Guardian Project Brain: discover and snapshot project state.

Scans a project directory and creates a Project Intelligence Snapshot
containing: file inventory, languages, entry points, git state, dependencies,
test files, and risk areas.

Usage:
  python engine/tools/guardian_scan.py [--workspace DIR] [--json]
  python engine/tools/guardian_scan.py --snapshot-out <path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep


def _git_info(workspace: str) -> dict:
    """Collect git state for the workspace."""
    info = {"is_git": False, "branch": None, "commit": None, "dirty": False,
            "changed_files": [], "untracked": []}
    git_dir = os.path.join(workspace, ".git")
    if not os.path.isdir(git_dir):
        return info
    info["is_git"] = True
    try:
        proc = subprocess.run(["git", "-C", workspace, "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        info["branch"] = (proc.stdout or "").strip() or None
        proc = subprocess.run(["git", "-C", workspace, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        info["commit"] = (proc.stdout or "").strip() or None
        proc = subprocess.run(["git", "-C", workspace, "status", "--porcelain"],
                              capture_output=True, text=True, timeout=10)
        lines = (proc.stdout or "").strip().splitlines()
        for line in lines:
            if line.startswith("??"):
                info["untracked"].append(line[3:].strip())
            elif line.strip():
                info["changed_files"].append(line[3:].strip())
        info["dirty"] = bool(lines)
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def _file_inventory(workspace: str, max_files: int = 5000) -> dict:
    """Inventory source files by language."""
    extensions = {
        ".py": "Python", ".rs": "Rust", ".js": "JavaScript", ".ts": "TypeScript",
        ".jsx": "JavaScript", ".tsx": "TypeScript", ".go": "Go", ".java": "Java",
        ".c": "C", ".cpp": "C++", ".h": "C/C++ Header", ".cs": "C#",
        ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin",
        ".md": "Markdown", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
        ".toml": "TOML", ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
        ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    }
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "target",
                 "dist", "build", ".eos", ".opencode", ".claude"}
    languages = {}
    files = []
    total = 0
    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in filenames:
            total += 1
            if total > max_files:
                break
            ext = os.path.splitext(fn)[1].lower()
            lang = extensions.get(ext)
            if lang:
                languages[lang] = languages.get(lang, 0) + 1
                rel = os.path.relpath(os.path.join(root, fn), workspace)
                files.append({"path": rel, "language": lang, "ext": ext})
        if total > max_files:
            break
    return {"languages": languages, "file_count": len(files), "files": files[:200]}


def _find_entry_points(workspace: str, inventory: dict) -> list[str]:
    """Find likely entry points."""
    entry_patterns = ["main.py", "main.rs", "index.js", "index.ts", "app.py",
                      "app.js", "server.py", "server.js", "main.go", "Main.java",
                      "cli/src/main.rs", "src/main.rs", "src/index.js", "src/index.ts"]
    found = []
    for ep_name in entry_patterns:
        path = os.path.join(workspace, ep_name)
        if os.path.isfile(path):
            found.append(ep_name)
    # Also check for Cargo.toml, package.json, pyproject.toml
    for marker in ("Cargo.toml", "package.json", "pyproject.toml", "go.mod", "pom.xml"):
        if os.path.isfile(os.path.join(workspace, marker)):
            found.append(marker)
    return found


def _find_tests(workspace: str) -> list[str]:
    """Find test files."""
    tests = []
    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", "target"}]
        for fn in filenames:
            lower = fn.lower()
            if ("test" in lower or lower.startswith("test_") or lower.endswith("_test.py")
                    or lower.endswith(".test.js") or lower.endswith(".test.ts")
                    or lower.endswith("_test.go")):
                rel = os.path.relpath(os.path.join(root, fn), workspace)
                tests.append(rel)
    return tests[:50]


def _find_configs(workspace: str) -> list[str]:
    """Find configuration files."""
    config_patterns = [".env", ".env.example", "config.json", "config.yaml", "config.yml",
                      "settings.json", "settings.yaml", "Cargo.toml", "package.json",
                      "pyproject.toml", "requirements.txt", "go.mod", "Makefile",
                      "Dockerfile", "docker-compose.yml", ".github"]
    found = []
    for name in config_patterns:
        path = os.path.join(workspace, name)
        if os.path.exists(path):
            found.append(name)
    return found


def _sha256_workspace(workspace: str) -> str:
    """Compute a fingerprint of the workspace (sorted file hashes)."""
    h = hashlib.sha256()
    files = []
    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", "target", ".eos"}]
        for fn in filenames:
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, workspace)
            try:
                with open(fp, "rb") as fh:
                    h.update(rel.encode())
                    h.update(hashlib.sha256(fh.read()).hexdigest().encode())
            except OSError:
                pass
    return h.hexdigest()


def scan(workspace: str) -> dict:
    """Scan a workspace and produce a Project Intelligence Snapshot."""
    workspace = os.path.abspath(workspace)
    inventory = _file_inventory(workspace)
    git = _git_info(workspace)
    entry_points = _find_entry_points(workspace, inventory)
    tests = _find_tests(workspace)
    configs = _find_configs(workspace)
    fingerprint = _sha256_workspace(workspace)

    snapshot = {
        "schema": 1,
        "kind": "project_intelligence_snapshot",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace": workspace,
        "fingerprint": fingerprint,
        "git": git,
        "languages": inventory["languages"],
        "file_count": inventory["file_count"],
        "entry_points": entry_points,
        "test_files": tests,
        "test_count": len(tests),
        "config_files": configs,
        "sample_files": inventory["files"][:50],
        "evidence_level": "OBSERVED",
    }
    return snapshot


def main() -> int:
    ap = argparse.ArgumentParser(prog="guardian_scan.py")
    ap.add_argument("--workspace", default=None, help="workspace to scan (default: cwd)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--snapshot-out", default=None, help="write snapshot to file")
    args = ap.parse_args()

    workspace = args.workspace or os.getcwd()
    snapshot = scan(workspace)

    if args.snapshot_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.snapshot_out)), exist_ok=True)
        with open(args.snapshot_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(snapshot, fh, indent=2)
            fh.write("\n")
        print(f"snapshot written to {args.snapshot_out}")

    if args.json:
        print(json.dumps(snapshot, indent=2))
    else:
        print(f"workspace: {snapshot['workspace']}")
        print(f"languages: {snapshot['languages']}")
        print(f"files: {snapshot['file_count']}")
        print(f"tests: {snapshot['test_count']}")
        print(f"entry_points: {snapshot['entry_points']}")
        print(f"git: {snapshot['git']['branch']} @ {snapshot['git']['commit'][:12] if snapshot['git']['commit'] else 'N/A'}")
        print(f"dirty: {snapshot['git']['dirty']}")
        print(f"fingerprint: {snapshot['fingerprint'][:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
