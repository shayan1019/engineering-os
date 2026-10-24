#!/usr/bin/env python3
"""guardian.py ? EOS Guardian CLI entry point.

Usage:
  python engine/tools/guardian.py scan [--workspace DIR] [--json]
  python engine/tools/guardian.py map [--workspace DIR] [--json]
  python engine/tools/guardian.py audit [--workspace DIR] [--json]
  python engine/tools/guardian.py verify [--claim "..."] [--evidence <path>]
  python engine/tools/guardian.py status [--workspace DIR] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardian_scan
import guardian_audit
import guardian_verify
import guardian_status
import guardian_ledger


def main() -> int:
    ap = argparse.ArgumentParser(prog="guardian.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--workspace", default=None)
    s.add_argument("--json", action="store_true")
    s.add_argument("--snapshot-out", default=None)

    sub.add_parser("map", parents=[s], add_help=False)

    a = sub.add_parser("audit")
    a.add_argument("--workspace", default=None)
    a.add_argument("--json", action="store_true")
    a.add_argument("--snapshot", default=None)

    v = sub.add_parser("verify")
    v.add_argument("--claim", default=None)
    v.add_argument("--evidence", nargs="*", default=None)
    v.add_argument("--audit", default=None)
    v.add_argument("--snapshot", default=None)
    v.add_argument("--json", action="store_true")

    st = sub.add_parser("status")
    st.add_argument("--workspace", default=None)
    st.add_argument("--json", action="store_true")

    args = ap.parse_args()
    workspace = getattr(args, "workspace", None) or os.getcwd()

    if args.cmd == "scan":
        snapshot = guardian_scan.scan(workspace)
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
            print(f"git: {snapshot['git']['branch']} @ {(snapshot['git']['commit'] or '')[:12]}")
            print(f"dirty: {snapshot['git']['dirty']}")
            print(f"fingerprint: {snapshot['fingerprint'][:16]}...")
        return 0

    if args.cmd == "map":
        snapshot = guardian_scan.scan(workspace)
        print(json.dumps(snapshot, indent=2))
        return 0

    if args.cmd == "audit":
        snapshot = None
        if args.snapshot:
            with open(args.snapshot, encoding="utf-8") as fh:
                snapshot = json.load(fh)
        if snapshot is None:
            snapshot = guardian_scan.scan(workspace)
        findings = guardian_audit.audit(workspace, snapshot)
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

    if args.cmd == "verify":
        if args.claim:
            result = guardian_verify.verify_claim(args.claim, args.evidence)
        elif args.audit:
            result = guardian_verify.verify_audit(args.audit, args.snapshot)
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
            else:
                print(f"audit: {result['audit_file']}")
                print(f"findings: {result['findings_count']}")
                print(f"verification: {result['verification']}")
        return 0

    if args.cmd == "status":
        rep = guardian_status.status(workspace)
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

    return 2


if __name__ == "__main__":
    sys.exit(main())
