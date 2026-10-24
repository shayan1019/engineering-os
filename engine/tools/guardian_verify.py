#!/usr/bin/env python3
"""guardian_verify.py ? EOS Guardian Truth Engine: verify claims against evidence.

Evaluates engineering claims and classifies them as:
  CLAIMED, OBSERVED, TESTED, VERIFIED, CONTRADICTED, UNKNOWN

Usage:
  python engine/tools/guardian_verify.py --claim "feature X is complete" --evidence <path>
  python engine/tools/guardian_verify.py --audit <audit.json> --snapshot <snapshot.json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep


CLAIM_STATES = ["CLAIMED", "OBSERVED", "TESTED", "VERIFIED", "CONTRADICTED", "UNKNOWN"]


def verify_claim(claim: str, evidence_paths: list[str] | None = None) -> dict:
    """Verify a single claim against available evidence."""
    result = {
        "claim": claim,
        "state": "CLAIMED",
        "reason": "No evidence provided",
        "evidence_checked": [],
    }

    if not evidence_paths:
        result["state"] = "CLAIMED"
        result["reason"] = "No evidence files provided; claim is unverified"
        return result

    for ep_path in evidence_paths:
        if not os.path.isfile(ep_path):
            result["evidence_checked"].append({"path": ep_path, "status": "NOT_FOUND"})
            continue
        try:
            with open(ep_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            result["evidence_checked"].append({"path": ep_path, "status": "UNREADABLE"})
            continue

        result["evidence_checked"].append({"path": ep_path, "status": "READ"})

        # Check for verdict/pass/fail in evidence
        verdict = data.get("verdict") or data.get("status") or data.get("result")
        if verdict:
            v = str(verdict).upper()
            if v in ("PASS", "VERIFIED", "OK", "SUCCESS"):
                result["state"] = "TESTED"
                result["reason"] = f"Evidence shows {verdict}"
            elif v in ("FAIL", "ERROR", "CONTRADICTED"):
                result["state"] = "CONTRADICTED"
                result["reason"] = f"Evidence shows {verdict}"
            elif v in ("BLOCKED", "PARTIAL"):
                result["state"] = "OBSERVED"
                result["reason"] = f"Evidence shows {verdict} (incomplete)"

    # If we have evidence but no clear verdict
    if result["state"] == "CLAIMED" and result["evidence_checked"]:
        read_count = sum(1 for e in result["evidence_checked"] if e["status"] == "READ")
        if read_count > 0:
            result["state"] = "OBSERVED"
            result["reason"] = "Evidence files read but no clear verdict found"

    return result


def verify_audit(audit_path: str, snapshot_path: str | None = None) -> dict:
    """Verify audit findings against a project snapshot."""
    with open(audit_path, encoding="utf-8") as fh:
        audit_data = json.load(fh)

    findings = audit_data.get("findings", [])
    result = {
        "audit_file": audit_path,
        "snapshot_file": snapshot_path,
        "findings_count": len(findings),
        "by_severity": {},
        "verification": "OBSERVED",
    }

    for f in findings:
        sev = f.get("severity", "UNKNOWN")
        result["by_severity"][sev] = result["by_severity"].get(sev, 0) + 1

    if snapshot_path and os.path.isfile(snapshot_path):
        with open(snapshot_path, encoding="utf-8") as fh:
            snapshot = json.load(fh)
        # Cross-check: if audit says no tests but snapshot shows test files
        no_test_findings = [f for f in findings if "no test" in f.get("title", "").lower()]
        if no_test_findings and snapshot.get("test_count", 0) > 0:
            result["verification"] = "CONTRADICTED"
            result["note"] = "Audit claims no tests but snapshot shows test files"
        else:
            result["verification"] = "OBSERVED"

    return result


def main() -> int:
    ap = argparse.ArgumentParser(prog="guardian_verify.py")
    ap.add_argument("--claim", default=None, help="claim to verify")
    ap.add_argument("--evidence", nargs="*", default=None, help="evidence file paths")
    ap.add_argument("--audit", default=None, help="audit JSON file")
    ap.add_argument("--snapshot", default=None, help="snapshot JSON file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.claim:
        result = verify_claim(args.claim, args.evidence)
    elif args.audit:
        result = verify_audit(args.audit, args.snapshot)
    else:
        ap.print_help()
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if "claim" in result:
            print(f"claim: {result['claim']}")
            print(f"state: {result['state']}")
            print(f"reason: {result['reason']}")
            for e in result.get("evidence_checked", []):
                print(f"  evidence: {e['path']} -> {e['status']}")
        else:
            print(f"audit: {result['audit_file']}")
            print(f"findings: {result['findings_count']}")
            print(f"verification: {result['verification']}")
            if result.get("note"):
                print(f"note: {result['note']}")
            for sev, count in result.get("by_severity", {}).items():
                print(f"  {sev}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
