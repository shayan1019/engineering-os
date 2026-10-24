#!/usr/bin/env python3
"""guardian_status.py ? EOS Guardian status reporter.

Reports current project intelligence status, open findings, and work ledger.

Usage:
  python engine/tools/guardian_status.py [--workspace DIR] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep
import guardian_scan
import guardian_audit


def status(workspace: str) -> dict:
    """Generate a status report for the workspace."""
    snapshot = guardian_scan.scan(workspace)
    findings = guardian_audit.audit(workspace, snapshot)

    # Check for existing ledger
    ledger_dir = os.path.join(ep.USER_EVIDENCE, "guardian-ledger")
    ledger_items = []
    if os.path.isdir(ledger_dir):
        for f in sorted(os.listdir(ledger_dir)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(ledger_dir, f), encoding="utf-8") as fh:
                        ledger_items.append(json.load(fh))
                except (json.JSONDecodeError, OSError):
                    pass

    high = [f for f in findings if f["severity"] == "HIGH"]
    medium = [f for f in findings if f["severity"] == "MEDIUM"]

    # Health should consider test coverage, not just findings
    test_count = snapshot.get("test_count", 0)
    file_count = snapshot.get("file_count", 0)
    test_ratio = test_count / max(file_count, 1)
    has_tests = test_count > 0

    health_issues = []
    if high:
        health_issues.append(f"{len(high)} HIGH findings")
    if not has_tests and file_count > 5:
        health_issues.append("no test files found")
    if test_ratio < 0.1 and file_count > 20:
        health_issues.append(f"low test ratio ({test_count}/{file_count})")

    if health_issues:
        health = "ATTENTION"
    else:
        health = "PASS"

    return {
        "schema": 1,
        "kind": "guardian_status",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace": workspace,
        "project": {
            "languages": snapshot["languages"],
            "file_count": snapshot["file_count"],
            "test_count": snapshot["test_count"],
            "git_branch": snapshot["git"].get("branch"),
            "git_commit": (snapshot["git"].get("commit") or "")[:12],
            "dirty": snapshot["git"].get("dirty", False),
        },
        "audit": {
            "total_findings": len(findings),
            "high": len(high),
            "medium": len(medium),
            "low": len([f for f in findings if f["severity"] == "LOW"]),
            "info": len([f for f in findings if f["severity"] == "INFO"]),
        },
        "ledger": {
            "open_items": len([i for i in ledger_items if i.get("status") not in ("DONE", "BLOCKED")]),
            "total_items": len(ledger_items),
        },
        "health": health,
        "health_issues": health_issues,
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="guardian_status.py")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    workspace = args.workspace or os.getcwd()
    rep = status(workspace)

    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"workspace: {rep['workspace']}")
        print(f"health: {rep['health']}")
        print(f"languages: {rep['project']['languages']}")
        print(f"files: {rep['project']['file_count']}, tests: {rep['project']['test_count']}")
        print(f"git: {rep['project']['git_branch']} @ {rep['project']['git_commit']} (dirty={rep['project']['dirty']})")
        print(f"audit: {rep['audit']['total_findings']} findings (HIGH={rep['audit']['high']}, MEDIUM={rep['audit']['medium']})")
        print(f"ledger: {rep['ledger']['open_items']} open / {rep['ledger']['total_items']} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
