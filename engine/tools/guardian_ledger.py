#!/usr/bin/env python3
"""guardian_ledger.py ? EOS Guardian Work Ledger: persistent task tracking.

Usage:
  python engine/tools/guardian_ledger.py add --id G001 --scope "fix auth" --objective "..." 
  python engine/tools/guardian_ledger.py list [--status OPEN]
  python engine/tools/guardian_ledger.py update --id G001 --status DONE --evidence <path>
  python engine/tools/guardian_ledger.py show --id G001
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep

LEDGER_DIR = os.path.join(ep.USER_EVIDENCE, "guardian-ledger")


def _ensure_dir():
    os.makedirs(LEDGER_DIR, exist_ok=True)


def _ledger_path(item_id: str) -> str:
    # Reject path traversal
    if not item_id or "/" in item_id or "\\" in item_id or ".." in item_id:
        raise ValueError(f"invalid item_id: {item_id}")
    # Only allow alphanumeric, hyphen, underscore
    import re
    if not re.match(r"^[A-Za-z0-9_\-]+$", item_id):
        raise ValueError(f"invalid item_id (must be alphanumeric/hyphen/underscore): {item_id}")
    return os.path.join(LEDGER_DIR, f"{item_id}.json")


def add(item_id: str, scope: str, objective: str, prerequisites: str = "",
        implementation: str = "", verification: str = "", status: str = "OPEN") -> int:
    _ensure_dir()
    try:
        path = _ledger_path(item_id)
    except ValueError as e:
        print(f"BLOCKED: {e}")
        return 1
    if os.path.exists(path):
        print(f"EXISTS: {item_id} (use update to modify)")
        return 1
    item = {
        "id": item_id,
        "scope": scope,
        "objective": objective,
        "prerequisites": prerequisites,
        "implementation": implementation,
        "verification": verification,
        "status": status,
        "evidence": [],
        "blockers": [],
        "commit": None,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(item, fh, indent=2)
        fh.write("\n")
    print(f"ADDED: {item_id}")
    return 0


def update(item_id: str, status: str | None = None, evidence: str | None = None,
          commit: str | None = None, blockers: str | None = None,
          implementation: str | None = None, verification: str | None = None) -> int:
    try:
        path = _ledger_path(item_id)
    except ValueError as e:
        print(f"BLOCKED: {e}")
        return 1
    if not os.path.exists(path):
        print(f"NOT FOUND: {item_id}")
        return 1
    with open(path, encoding="utf-8") as fh:
        item = json.load(fh)
    if status:
        item["status"] = status
    if evidence:
        item.setdefault("evidence", []).append(evidence)
    if commit:
        item["commit"] = commit
    if blockers:
        item.setdefault("blockers", []).append(blockers)
    if implementation:
        item["implementation"] = implementation
    if verification:
        item["verification"] = verification
    item["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(item, fh, indent=2)
        fh.write("\n")
    print(f"UPDATED: {item_id} (status={item['status']})")
    return 0


def list_items(status_filter: str | None = None) -> int:
    _ensure_dir()
    items = []
    for f in sorted(os.listdir(LEDGER_DIR)):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(LEDGER_DIR, f), encoding="utf-8") as fh:
                items.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            pass
    if status_filter:
        items = [i for i in items if i.get("status") == status_filter]
    print(f"{len(items)} item(s)")
    for item in items:
        print(f"  {item['id']}: {item['status']} ? {item['scope'][:60]}")
    return 0


def show(item_id: str) -> int:
    try:
        path = _ledger_path(item_id)
    except ValueError as e:
        print(f"BLOCKED: {e}")
        return 1
    if not os.path.exists(path):
        print(f"NOT FOUND: {item_id}")
        return 1
    with open(path, encoding="utf-8") as fh:
        item = json.load(fh)
    print(json.dumps(item, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="guardian_ledger.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--id", required=True)
    a.add_argument("--scope", required=True)
    a.add_argument("--objective", required=True)
    a.add_argument("--prerequisites", default="")
    a.add_argument("--implementation", default="")
    a.add_argument("--verification", default="")
    a.add_argument("--status", default="OPEN")

    u = sub.add_parser("update")
    u.add_argument("--id", required=True)
    u.add_argument("--status", default=None)
    u.add_argument("--evidence", default=None)
    u.add_argument("--commit", default=None)
    u.add_argument("--blockers", default=None)
    u.add_argument("--implementation", default=None)
    u.add_argument("--verification", default=None)

    l = sub.add_parser("list")
    l.add_argument("--status", default=None)

    s = sub.add_parser("show")
    s.add_argument("--id", required=True)

    args = ap.parse_args()

    if args.cmd == "add":
        return add(args.id, args.scope, args.objective, args.prerequisites,
                   args.implementation, args.verification, args.status)
    if args.cmd == "update":
        return update(args.id, args.status, args.evidence, args.commit,
                     args.blockers, args.implementation, args.verification)
    if args.cmd == "list":
        return list_items(args.status)
    if args.cmd == "show":
        return show(args.id)
    return 2


if __name__ == "__main__":
    sys.exit(main())
