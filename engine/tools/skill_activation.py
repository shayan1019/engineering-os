#!/usr/bin/env python3
"""skill_activation.py ? track and verify skill activation evidence.

Records which skills were selected for a task through the full lifecycle:
  DISCOVERED -> SELECTED -> RESOLVED -> AVAILABLE -> LOAD_REQUESTED ->
  LOAD_CONFIRMED -> EXECUTION_OBSERVED -> OUTCOME_VERIFIED

Also supports: BLOCKED, FAILED, UNOBSERVABLE.

A skill file existing on disk does NOT prove loading.
A model claiming to have used a skill does NOT prove execution.
A file-read event proves loading, not that the model followed instructions.
A successful task outcome must be evaluated independently.

Usage:
  python engine/tools/skill_activation.py record --task "..." --skills "tdd,code-quality"
  python engine/tools/skill_activation.py request-load --ledger <path> --skill tdd
  python engine/tools/skill_activation.py confirm-load --ledger <path> --skill tdd --evidence <hash>
  python engine/tools/skill_activation.py observe-execution --ledger <path> --skill tdd --action "test ran"
  python engine/tools/skill_activation.py verify-outcome --ledger <path> --skill tdd --gate-passed
  python engine/tools/skill_activation.py verify --ledger <path>
  python engine/tools/skill_activation.py status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep

LIFECYCLE_STATES = [
    "DISCOVERED", "SELECTED", "RESOLVED", "AVAILABLE",
    "LOAD_REQUESTED", "LOAD_CONFIRMED", "EXECUTION_OBSERVED",
    "OUTCOME_VERIFIED", "BLOCKED", "FAILED", "UNOBSERVABLE",
]


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
    candidates = []
    candidates.append(os.path.join(ep.ENGINE_ROOT, "skills", name, "SKILL.md"))
    ws = ep.resolve_workspace()
    for prefix in (".agents", ".claude", ".opencode"):
        candidates.append(os.path.join(ws, prefix, "skills", name, "SKILL.md"))
    home = os.path.expanduser("~")
    for root_dir in (os.path.join(home, ".agents", "skills"),
                     os.path.join(home, ".claude", "skills"),
                     os.path.join(home, ".config", "opencode", "skills")):
        candidates.append(os.path.join(root_dir, name, "SKILL.md"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _load_ledger(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_ledger(path: str, ledger: dict) -> None:
    ledger["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(ledger, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _get_skill_entry(ledger: dict, skill_name: str) -> dict | None:
    for e in ledger.get("skills", []):
        if e.get("skill") == skill_name:
            return e
    return None


def record(task: str, skills: list[str], out_dir: str | None = None) -> dict:
    """Record skill selection. State: SELECTED."""
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
            "state": "SELECTED" if path else "BLOCKED",
            "state_history": [{"state": "DISCOVERED", "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}],
            "load_requested_at": None,
            "load_confirmed_at": None,
            "execution_observed_at": None,
            "outcome_verified_at": None,
            "verification_evidence": None,
        })
        # Add SELECTED transition
        entries[-1]["state_history"].append({
            "state": "SELECTED",
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        if path:
            entries[-1]["state_history"].append({
                "state": "RESOLVED",
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            entries[-1]["state_history"].append({
                "state": "AVAILABLE",
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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


def request_load(ledger_path: str, skill_name: str) -> int:
    """Mark a skill as LOAD_REQUESTED."""
    ledger = _load_ledger(ledger_path)
    entry = _get_skill_entry(ledger, skill_name)
    if not entry:
        print(f"NOT FOUND: {skill_name} in ledger")
        return 1
    if not entry.get("exists"):
        entry["state"] = "BLOCKED"
        print(f"BLOCKED: {skill_name} (skill file missing)")
        _save_ledger(ledger_path, ledger)
        return 1
    entry["state"] = "LOAD_REQUESTED"
    entry["load_requested_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry["state_history"].append({"state": "LOAD_REQUESTED", "at": entry["load_requested_at"]})
    _save_ledger(ledger_path, ledger)
    print(f"LOAD_REQUESTED: {skill_name} ({entry['path']})")
    return 0


def confirm_load(ledger_path: str, skill_name: str, evidence_hash: str | None = None) -> int:
    """Confirm a skill was loaded. Requires evidence (file hash or host event)."""
    ledger = _load_ledger(ledger_path)
    entry = _get_skill_entry(ledger, skill_name)
    if not entry:
        print(f"NOT FOUND: {skill_name} in ledger")
        return 1
    if entry["state"] not in ("LOAD_REQUESTED", "AVAILABLE", "RESOLVED", "SELECTED"):
        print(f"BLOCKED: {skill_name} in state {entry['state']}, cannot confirm load")
        return 1

    # Verify the skill file still exists with same hash
    if entry.get("path") and os.path.isfile(entry["path"]):
        current_hash = _sha256_file(entry["path"])
        if evidence_hash and current_hash != evidence_hash:
            entry["state"] = "FAILED"
            entry["state_history"].append({"state": "FAILED", "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          "reason": "hash mismatch"})
            _save_ledger(ledger_path, ledger)
            print(f"FAILED: {skill_name} (hash mismatch)")
            return 1
        entry["state"] = "LOAD_CONFIRMED"
        entry["load_confirmed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry["state_history"].append({"state": "LOAD_CONFIRMED", "at": entry["load_confirmed_at"]})
        _save_ledger(ledger_path, ledger)
        print(f"LOAD_CONFIRMED: {skill_name}")
        return 0
    else:
        entry["state"] = "BLOCKED"
        print(f"BLOCKED: {skill_name} (skill file missing)")
        _save_ledger(ledger_path, ledger)
        return 1


def observe_execution(ledger_path: str, skill_name: str, action: str) -> int:
    """Record that a skill's guidance was actually executed."""
    ledger = _load_ledger(ledger_path)
    entry = _get_skill_entry(ledger, skill_name)
    if not entry:
        print(f"NOT FOUND: {skill_name} in ledger")
        return 1
    if entry["state"] not in ("LOAD_CONFIRMED", "LOAD_REQUESTED"):
        print(f"BLOCKED: {skill_name} not loaded (state={entry['state']})")
        return 1
    entry["state"] = "EXECUTION_OBSERVED"
    entry["execution_observed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry.setdefault("execution_actions", []).append({
        "action": action,
        "at": entry["execution_observed_at"],
    })
    entry["state_history"].append({"state": "EXECUTION_OBSERVED", "at": entry["execution_observed_at"]})
    _save_ledger(ledger_path, ledger)
    print(f"EXECUTION_OBSERVED: {skill_name} ({action})")
    return 0


def verify_outcome(ledger_path: str, skill_name: str, gate_passed: bool = False,
                   evidence_path: str | None = None) -> int:
    """Verify that a skill's application produced a good outcome."""
    ledger = _load_ledger(ledger_path)
    entry = _get_skill_entry(ledger, skill_name)
    if not entry:
        print(f"NOT FOUND: {skill_name} in ledger")
        return 1
    if entry["state"] not in ("EXECUTION_OBSERVED", "LOAD_CONFIRMED"):
        print(f"BLOCKED: {skill_name} not executed (state={entry['state']})")
        return 1
    if gate_passed:
        entry["state"] = "OUTCOME_VERIFIED"
        entry["outcome_verified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry["verification_evidence"] = evidence_path
        entry["state_history"].append({"state": "OUTCOME_VERIFIED", "at": entry["outcome_verified_at"]})
        _save_ledger(ledger_path, ledger)
        print(f"OUTCOME_VERIFIED: {skill_name}")
        return 0
    else:
        entry["state"] = "FAILED"
        entry["state_history"].append({"state": "FAILED", "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "reason": "gate did not pass"})
        _save_ledger(ledger_path, ledger)
        print(f"FAILED: {skill_name} (gate did not pass)")
        return 1


def verify(ledger_path: str) -> int:
    """Verify a skill activation ledger. Checks integrity of all entries."""
    ledger = _load_ledger(ledger_path)
    problems = 0
    for entry in ledger.get("skills", []):
        name = entry.get("skill")
        path = entry.get("path")
        state = entry.get("state")
        if not path or not os.path.isfile(path):
            print(f"MISSING: {name} (file not found at {path})")
            problems += 1
            continue
        current_hash = _sha256_file(path)
        if current_hash != entry.get("hash"):
            print(f"MODIFIED: {name} (file changed since selection)")
            problems += 1
        # Check state validity
        if state not in LIFECYCLE_STATES:
            print(f"INVALID STATE: {name} ({state})")
            problems += 1
        # Check state history consistency
        history = entry.get("state_history", [])
        states = [h.get("state") for h in history]
        if state not in states:
            print(f"INCONSISTENT: {name} (current state {state} not in history)")
            problems += 1
    if problems:
        print(f"FAIL: {problems} issue(s)")
        return 1
    print(f"PASS: {len(ledger.get('skills', []))} skill(s) verified")
    # Print lifecycle summary
    for entry in ledger.get("skills", []):
        print(f"  {entry['skill']}: {entry['state']}")
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
            skills = [f"{s['skill']}:{s['state']}" for s in data.get("skills", [])]
            print(f"  {data.get('run_id', f)}: {', '.join(skills)}")
        except (json.JSONDecodeError, OSError):
            print(f"  {f}: unreadable")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record")
    r.add_argument("--task", required=True)
    r.add_argument("--skills", required=True, help="comma-separated skill names")

    rl = sub.add_parser("request-load")
    rl.add_argument("--ledger", required=True)
    rl.add_argument("--skill", required=True)

    cl = sub.add_parser("confirm-load")
    cl.add_argument("--ledger", required=True)
    cl.add_argument("--skill", required=True)
    cl.add_argument("--evidence-hash", default=None)

    oe = sub.add_parser("observe-execution")
    oe.add_argument("--ledger", required=True)
    oe.add_argument("--skill", required=True)
    oe.add_argument("--action", required=True)

    vo = sub.add_parser("verify-outcome")
    vo.add_argument("--ledger", required=True)
    vo.add_argument("--skill", required=True)
    vo.add_argument("--gate-passed", action="store_true")
    vo.add_argument("--evidence", default=None)

    v = sub.add_parser("verify")
    v.add_argument("--ledger", required=True)

    sub.add_parser("status")

    args = ap.parse_args()

    if args.cmd == "record":
        skills = [s.strip() for s in args.skills.split(",") if s.strip()]
        ledger = record(args.task, skills)
        print(f"recorded: {ledger['run_id']}")
        for e in ledger["skills"]:
            print(f"  {e['skill']}: {e['state']} ({e.get('path') or 'not found'})")
        return 0 if ledger["verdict"] == "SELECTED" else 1
    if args.cmd == "request-load":
        return request_load(args.ledger, args.skill)
    if args.cmd == "confirm-load":
        return confirm_load(args.ledger, args.skill, args.evidence_hash)
    if args.cmd == "observe-execution":
        return observe_execution(args.ledger, args.skill, args.action)
    if args.cmd == "verify-outcome":
        return verify_outcome(args.ledger, args.skill, args.gate_passed, args.evidence)
    if args.cmd == "verify":
        return verify(args.ledger)
    if args.cmd == "status":
        return status()
    return 2


if __name__ == "__main__":
    sys.exit(main())
