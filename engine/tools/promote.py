#!/usr/bin/env python3
"""EOS promotion gate.

A change may only be promoted when an independent, verifiable quality-gate
artifact exists for the same project, against the same baseline, and every
promoted file still hashes to what the gate saw.

The promoter never runs tests. It refuses instead. That separation is the point:
the agent that wrote the code cannot be the one that certifies it.

Commands:
  promote --project <id> --gate <artifact.json> --artifact <path>... [--label L]
  verify  --file <promotion.json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality_gate as qg  # noqa: E402
import _eos_paths as ep  # noqa: E402

ROOT = qg.ROOT
WORKSPACE = qg.WORKSPACE
# The promotion ledger lives with the evidence, resolved by the same rules as
# every other artifact. Reading it from quality_gate would break as soon as
# that module changes its own constant names.
PROMOTIONS = ep.PROMOTION_EVIDENCE
SCHEMA = 1


def set_workspace(args) -> None:
    global WORKSPACE
    explicit = getattr(args, "root", None)
    if explicit:
        WORKSPACE = qg.ep.resolve_workspace(explicit)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_rel(path: str) -> str:
    """Promoted files must live in the workspace. Gate artifacts and other
    evidence may also live under EOS_HOME. Everything else is refused."""
    absolute = os.path.abspath(path)
    in_workspace = absolute == WORKSPACE or absolute.startswith(WORKSPACE + os.sep)
    in_home = absolute == qg.ep.EOS_HOME or absolute.startswith(qg.ep.EOS_HOME + os.sep)
    if not (in_workspace or in_home):
        raise ValueError(f"path outside the workspace and outside EOS_HOME: {path}")
    return qg.ep.store_path(absolute, WORKSPACE)


def cmd_promote(args) -> int:
    gate = qg.verify_artifact(args.gate, args.project, args.max_age_min)
    if not gate["ok"]:
        print("BLOCKED: quality gate is not valid for this project")
        for p in gate["problems"]:
            print(f"  - {p}")
        return 1
    gate_artifact = gate["artifact"]

    if not args.artifact:
        print("BLOCKED: no artifact provided; a promotion must name what it promotes")
        return 1

    files, problems = [], []
    for raw in args.artifact:
        try:
            rel = safe_rel(raw)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        full = qg.ep.load_stored_path(rel, WORKSPACE)
        if not os.path.isfile(full):
            problems.append(f"artifact file missing: {rel}")
            continue
        files.append({"path": rel, "sha256": qg.sha256_file(full), "bytes": os.path.getsize(full)})
    if problems:
        print("BLOCKED: invalid promotion payload")
        for p in problems:
            print(f"  - {p}")
        return 1

    label = args.label or "promotion"
    record = {
        "schema": SCHEMA,
        "kind": "promotion",
        "created_at": qg.utc_now(),
        "project": args.project,
        "label": label,
        "gate": {
            "path": safe_rel(args.gate),
            "sha256": qg.sha256_file(qg.ep.load_stored_path(safe_rel(args.gate), WORKSPACE)),
            "suite": gate_artifact.get("suite"),
            "created_at": gate_artifact.get("created_at"),
            "baseline": gate_artifact.get("baseline"),
            "commands": [{"name": c.get("name"), "exit_code": c.get("exit_code")}
                         for c in gate_artifact.get("commands", [])],
        },
        "artifacts": files,
        "approved_by": "eos.promote (mechanical)",
        "note": "Promotion certifies evidence, not intent. Re-run the gate after any change.",
    }

    out_dir = os.path.join(PROMOTIONS, args.project)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"promo-{label}-{utc_stamp()}.json")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, indent=2)

    print(f"PASS: promotion recorded for {args.project} ({len(files)} artifact(s))")
    print(f"  gate     : {record['gate']['path']} ({record['gate']['suite']})")
    print(f"  baseline : {(record['gate']['baseline'] or {}).get('label')}")
    print(f"  record   : {qg.ep.store_path(out, WORKSPACE)}")
    return 0


def cmd_verify(args) -> int:
    if not os.path.isfile(args.file):
        print(f"BLOCKED: promotion record not found: {args.file}")
        return 1
    try:
        rec = qg.load_json(args.file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: promotion unreadable: {exc}")
        return 1

    problems = []
    if rec.get("schema") != SCHEMA or rec.get("kind") != "promotion":
        problems.append("wrong schema or kind")

    gate_path = qg.ep.load_stored_path((rec.get("gate") or {}).get("path", ""), WORKSPACE)
    if not os.path.isfile(gate_path):
        problems.append("gate artifact referenced by the promotion is missing")
    elif qg.sha256_file(gate_path) != (rec.get("gate") or {}).get("sha256"):
        problems.append("gate artifact changed after promotion")
    else:
        gate = qg.verify_artifact(gate_path, rec.get("project"), None)
        problems.extend(f"gate: {p}" for p in gate["problems"])

    for item in rec.get("artifacts", []):
        full = qg.ep.load_stored_path(item["path"], WORKSPACE)
        if not os.path.isfile(full):
            problems.append(f"promoted file missing: {item['path']}")
        elif qg.sha256_file(full) != item["sha256"]:
            problems.append(f"promoted file changed since promotion: {item['path']}")

    if problems:
        print("BLOCKED: promotion is no longer valid")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"PASS: promotion valid ({rec.get('project')}, {len(rec.get('artifacts', []))} artifact(s), {rec.get('created_at')})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="promote.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("promote")
    p.add_argument("--project", required=True)
    p.add_argument("--gate", required=True)
    p.add_argument("--artifact", action="append", default=[])
    p.add_argument("--label")
    p.add_argument("--max-age-min", type=int, default=1440)
    p.add_argument("--root")
    p.set_defaults(func=cmd_promote)

    v = sub.add_parser("verify")
    v.add_argument("--file", required=True)
    v.add_argument("--root")
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    set_workspace(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
