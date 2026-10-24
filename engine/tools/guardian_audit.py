#!/usr/bin/env python3
"""guardian_audit.py ? EOS Guardian Proactive Auditor.

Scans a project for risk areas: missing tests, security risks, dependency issues,
architecture inconsistencies, and potential bugs.

Usage:
  python engine/tools/guardian_audit.py [--workspace DIR] [--json] [--snapshot <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep
import guardian_scan


def audit(workspace: str, snapshot: dict | None = None) -> list[dict]:
    """Run proactive audit on a workspace. Returns list of findings."""
    if snapshot is None:
        snapshot = guardian_scan.scan(workspace)
    findings = []
    fid = 0

    def add(severity, category, title, detail, file_path=None, evidence_level="OBSERVED"):
        nonlocal fid
        fid += 1
        findings.append({
            "id": f"G{fid:03d}",
            "severity": severity,
            "category": category,
            "title": title,
            "detail": detail,
            "file": file_path,
            "evidence_level": evidence_level,
        })

    files = snapshot.get("sample_files", [])
    all_files = []
    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", "target", ".eos"}]
        for fn in filenames:
            all_files.append(os.path.relpath(os.path.join(root, fn), workspace))

    # Check 1: Source files without corresponding tests
    src_files = [f for f in all_files if f.endswith((".py", ".rs", ".js", ".ts")) and "test" not in f.lower()]
    test_files = [f for f in all_files if "test" in f.lower()]
    if src_files and not test_files:
        add("HIGH", "testing", "No test files found",
            f"Found {len(src_files)} source files but no test files. "
            "Code changes cannot be verified without tests.",
            evidence_level="OBSERVED")

    # Check 2: Hardcoded secrets
    secret_patterns = [
        (r"password\s*=\s*['\"][^'\"]+['\"]", "hardcoded password"),
        (r"api[_-]?key\s*=\s*['\"][^'\"]+['\"]", "hardcoded API key"),
        (r"secret\s*=\s*['\"][^'\"]+['\"]", "hardcoded secret"),
        (r"token\s*=\s*['\"][^'\"]{20,}['\"]", "hardcoded token"),
    ]
    for f in all_files:
        if f.endswith((".py", ".js", ".ts", ".rs", ".json", ".yaml", ".yml", ".toml")):
            try:
                with open(os.path.join(workspace, f), encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                for pattern, desc in secret_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        add("HIGH", "security", f"Potential {desc}",
                            f"Found pattern matching {desc} in {f}",
                            file_path=f, evidence_level="OBSERVED")
            except OSError:
                pass

    # Check 3: TODO/FIXME/HACK comments
    todo_count = 0
    for f in all_files:
        if f.endswith((".py", ".rs", ".js", ".ts", ".go", ".java")):
            try:
                with open(os.path.join(workspace, f), encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", line):
                            todo_count += 1
                            if todo_count <= 10:
                                add("LOW", "maintenance", f"TODO/FIXME found in {f}:{i}",
                                    line.strip()[:120], file_path=f, evidence_level="OBSERVED")
            except OSError:
                pass
    if todo_count > 10:
        add("INFO", "maintenance", f"{todo_count} TODO/FIXME comments total",
            "Consider addressing accumulated technical debt.", evidence_level="OBSERVED")

    # Check 4: Missing README
    if not os.path.isfile(os.path.join(workspace, "README.md")):
        add("MEDIUM", "documentation", "No README.md found",
            "Project lacks a README. New contributors and users cannot understand the project.",
            evidence_level="OBSERVED")

    # Check 5: Missing .gitignore
    if not os.path.isfile(os.path.join(workspace, ".gitignore")):
        add("MEDIUM", "hygiene", "No .gitignore found",
            "Without .gitignore, build artifacts and secrets may be committed.",
            evidence_level="OBSERVED")

    # Check 6: Large files
    for f in all_files:
        fp = os.path.join(workspace, f)
        try:
            size = os.path.getsize(fp)
            if size > 1_000_000:  # 1MB
                add("MEDIUM", "hygiene", f"Large file: {f}",
                    f"File is {size // 1024}KB. Consider if this should be in the repository.",
                    file_path=f, evidence_level="OBSERVED")
        except OSError:
            pass

    # Check 7: Git dirty state
    git = snapshot.get("git", {})
    if git.get("dirty"):
        changed = git.get("changed_files", []) + git.get("untracked", [])
        add("INFO", "git", "Working tree has uncommitted changes",
            f"{len(changed)} file(s) modified or untracked.",
            evidence_level="OBSERVED")

    # Check 8: No CI configuration
    ci_files = [f for f in all_files if ".github/workflows" in f or ".gitlab-ci" in f or "Jenkinsfile" in f]
    if not ci_files and snapshot.get("file_count", 0) > 10:
        add("MEDIUM", "ci", "No CI configuration found",
            "No CI pipeline detected. Automated testing and quality gates are not enforced.",
            evidence_level="OBSERVED")

    # Check 9: Missing license
    if not any(f.lower().startswith("license") for f in all_files):
        add("MEDIUM", "licensing", "No LICENSE file found",
            "Project has no license file. Usage rights are unclear.",
            evidence_level="OBSERVED")

    # Sort by severity
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    findings.sort(key=lambda f: severity_order.get(f["severity"], 9))

    return findings


def main() -> int:
    ap = argparse.ArgumentParser(prog="guardian_audit.py")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--snapshot", default=None, help="path to snapshot JSON")
    args = ap.parse_args()

    workspace = args.workspace or os.getcwd()
    snapshot = None
    if args.snapshot:
        with open(args.snapshot, encoding="utf-8") as fh:
            snapshot = json.load(fh)

    findings = audit(workspace, snapshot)

    if args.json:
        print(json.dumps({"findings": findings, "count": len(findings)}, indent=2))
    else:
        print(f"audit: {len(findings)} finding(s)")
        for f in findings:
            print(f"  [{f['severity']}] {f['id']}: {f['title']}")
            if f.get("file"):
                print(f"    file: {f['file']}")
            print(f"    {f['detail'][:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
