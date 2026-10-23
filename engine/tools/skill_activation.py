#!/usr/bin/env python3
"""skill_activation.py ? track and verify skill activation evidence.

Records which skills were selected for a task, whether they were loaded,
and produces an activation ledger for evidence inspection.

Usage:
  python engine/tools/skill_activation.py record --task "..." --skills "tdd,code-quality"
  python engine/tools/skill_activation.py verify --ledger <path>
  python engine/tools/skill_activation.py status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _find_skill_path(name: str) -> str | None:
    """Find a skill's SKILL.md across all discovery roots."""
    candidates = []
    # Engine shipped skills
    candidates.append(os.path.join(ep.ENGINE_ROOT, "skills", name, "SKILL.md"))
    # Workspace roots
    ws = ep.resolve_workspace()
    for prefix in (".agents", ".claude", ".opencode"):
        candidates.append(os.path.join(ws, prefix, "skills", name, "SKILL.md"))
    # User roots (various naming conventions)
    home = os.path.expanduser("~")
    for root_dir in (os.path.join(home, ".agents", "skills"),
                     os.path.join(home, ".claude", "skills"),
                     os.path.join(home, ".config", "opencode", "skills")):
        candidates.append(os.path.join(root_dir, name, "SKILL.md"))
    # Also search user-level .agents (without dot, common on Windows)
    candidates.append(os.path.join(home, "agents", "skills", name, "SKILL.md"))

    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def record(task: str, skills: list[str], out_dir: str | None = None) -> dict:
    """Record skill selection for a task. Does not claim loading."""
    ledger_dir = out_dir or os.path.join(ep.USER_EVIDENCE, "skill-activations")
    os.makedirs(ledger_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"skill-activation-{ts}-{hashlib.sha256(task.encode()).hexdigest()[:6]}"

    entries = []
    for name in skills:
        path = _find_skill_path(name)
        entries.append({
            "skill": name,
            "path": path,
            "exists": path is not None,
            "hash": _sha256_file(path) if path else None,
            "status": "SELECTED" if path else "MISSING",
        })

    ledger = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task": task[:500],
        "skills": entries,
        "verdict": "SELECTED" if all(e["exists"] for e in entries) else "PARTIAL",
    }

    ledger_path = os.path.join(ledger_dir, f"{run_id}.json")
    with open(ledger_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(ledger, fh, indent=2)
        fh.write("\n")

    ledger["ledger_path"] = ledger_path
    return ledger


def verify(ledger_path: str) -> int:
    """Verify a skill activation ledger. Checks that selected skills still exist with same hash."""
    with open(ledger_path, encoding="utf-8") as fh:
        ledger = json.load(fh)

    problems = []
    for entry in ledger.get("skills", []):
        name = entry.get("skill")
        path = entry.get("path")
        if not path or not os.path.isfile(path):
            problems.append(f"{name}: skill file missing at {path}")
            continue
        current_hash = _sha256_file(path)
        if current_hash != entry.get("hash"):
            problems.append(f"{name}: skill file changed since selection (hash mismatch)")

    if problems:
        print(f"FAIL: {len(problems)} issue(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"PASS: {len(ledger.get('skills', []))} skill(s) verified")
    return 0


def status() -> int:
    ledger_dir = os.path.join(ep.USER_EVIDENCE, "skill-activations")
    if not os.path.isdir(ledger_dir):
        print("No skill activation records found")
        return 0
    files = sorted(f for f in os.listdir(ledger_dir) if f.endswith(".json"))
    print(f"{len(files)} activation record(s) in {ledger_dir}")
    for f in files[-5:]:
        try:
            with open(os.path.join(ledger_dir, f), encoding="utf-8") as fh:
                data = json.load(fh)
            skills = [s["skill"] for s in data.get("skills", [])]
            print(f"  {data.get('run_id', f)}: {', '.join(skills)} ({data.get('verdict', '?')})")
        except (json.JSONDecodeError, OSError):
            print(f"  {f}: unreadable")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("record")
    r.add_argument("--task", required=True)
    r.add_argument("--skills", required=True, help="comma-separated skill names")
    r.add_argument("--out", default=None)

    v = sub.add_parser("verify")
    v.add_argument("--ledger", required=True)

    sub.add_parser("status")

    args = ap.parse_args()

    if args.cmd == "record":
        skills = [s.strip() for s in args.skills.split(",") if s.strip()]
        ledger = record(args.task, skills, args.out)
        print(f"recorded: {ledger['run_id']} ({ledger['verdict']})")
        for e in ledger["skills"]:
            print(f"  {e['skill']}: {e['status']} ({e.get('path') or 'not found'})")
        return 0 if ledger["verdict"] == "SELECTED" else 1
    if args.cmd == "verify":
        return verify(args.ledger)
    if args.cmd == "status":
        return status()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
