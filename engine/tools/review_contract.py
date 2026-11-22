#!/usr/bin/env python3
"""Engineering Review Evidence Contract (v1).

The completion gate could prove "no open blocker". It could not prove that a
blocker was looked for. This module is the single validator for that missing
negative evidence.

A decision opts into the contract by carrying `review_contract: 1`. Only
STANDARD and CRITICAL require it; ATOMIC and legacy records are skipped so the
fast path and the historical CLOSED records keep their meaning.

The evidence lives in the existing challenge ledger (sealed and bound to the
decision id) under a `review` block:

    reviewer       independent role and the session or team artifact
    coverage       required surfaces, inspected[], not_inspected[] with reasons
    falsification  tests actually run against the change, with evidence
    unknowns       every impact unknown with a disposition

Dispositions:
    closed              proven wrong or fixed, with evidence
    out_of_scope        deliberately not covered, with a reason
    accepted_by_human   a sealed human acceptance entry in the decision record

Required surfaces are derived from the decision's own scope and impact report,
not from a fixed checklist: files, components, contracts, tests, processes and
impact unknowns. A reviewer cannot pass by filling a template that ignores the
decision's actual blast radius.

No module here imports decision.py or completion.py at import time; both import
this validator, so the contract is checked in exactly one place and the call
graph stays acyclic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

CONTRACT = 1
# Decisions created after this instant are covered even if the marker is
# removed from the record. The marker is an explicit opt-in for records that
# predate it; it is not the only source of truth, so deleting it cannot
# downgrade a new STANDARD/CRITICAL decision to legacy.
ACTIVATED_AT = "2026-11-22T09:00:00Z"
RISKS = ("STANDARD", "CRITICAL")
REVIEW_ROLES = ("reviewer", "bug-hunter", "logic-reviewer", "security-auditor",
                "architect", "evidence-analyst")
RESULTS = ("passed", "failed", "inconclusive")
DISPOSITIONS = ("closed", "out_of_scope", "accepted_by_human")
MIN_FALSIFICATION = {"STANDARD": 1, "CRITICAL": 2}
MIN_SESSION_EVENTS = {"STANDARD": 1, "CRITICAL": 2}
SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TEAM_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ReviewError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def claim_hash(value: str) -> str:
    """Stable identity of an unknown claim: whitespace-normalised, case kept."""
    normalised = " ".join(str(value or "").split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest().upper()


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _value(record: dict, name: str):
    entry = (record.get("fields") or {}).get(name)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _stable(items) -> list:
    out, seen = [], set()
    for item in items:
        text = str(item or "")
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def applies(record: dict, workspace: str = "") -> bool:
    decision_id = str(record.get("id") or "")
    if workspace:
        registered = _registered_risk(workspace, decision_id)
        if registered in RISKS:
            # The registry outranks the mutable risk field: rewriting the
            # record to ATOMIC must not downgrade a registered decision.
            return True
    if record.get("risk") not in RISKS:
        return False
    if _as_int(record.get("review_contract")) == CONTRACT:
        return True
    created = parse_time(record.get("created_at"))
    activated = parse_time(ACTIVATED_AT)
    return bool(created and activated and created >= activated)


def _registered_risk(workspace: str, decision_id: str) -> str:
    """The risk recorded at open time, or "" when the decision is not registered.

    The registry is append-only evidence written by decision.py at open time.
    It anchors the requirement to something other than the mutable risk and
    created_at fields, so rewriting either does not turn a new record into
    legacy.
    """
    if not decision_id:
        return ""
    path = os.path.join(workspace, ".agents", "evidence", "decisions",
                        "_review_contract.jsonl")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("id") == decision_id:
                    return str(event.get("risk") or "")
    except OSError:
        return ""
    return ""


def surfaces(record: dict) -> tuple:
    """Return (required surface ids, impact unknown claims)."""
    impact = _value(record, "impact")
    if not isinstance(impact, dict):
        impact = {}
    ids, unknowns = [], []

    for item in (_value(record, "scope") or []):
        ids.append(f"file:{item}")
    for item in (impact.get("files") or []):
        ids.append(f"file:{item}")
    for item in ((record.get("change") or {}).get("files") or []):
        ids.append(f"file:{item}")

    for item in (impact.get("components") or []):
        ident = item.get("id") if isinstance(item, dict) else item
        if ident:
            ids.append(f"component:{ident}")
            process = item.get("process") if isinstance(item, dict) else ""
            if process:
                ids.append(f"process:{process}")
    for item in (impact.get("contracts") or []):
        if isinstance(item, dict):
            path = item.get("path") or ""
            symbol = item.get("symbol") or item.get("kind") or ""
            ident = f"{path}::{symbol}" if path or symbol else ""
        else:
            ident = str(item or "")
        if ident:
            ids.append(f"contract:{ident}")
    for item in (impact.get("tests") or []):
        ident = item.get("path") if isinstance(item, dict) else item
        if ident:
            ids.append(f"test:{ident}")
    for item in (impact.get("unknowns") or []):
        claim = " ".join(str(item or "").split())
        if claim:
            unknowns.append(claim)
            ids.append(f"unknown:{claim_hash(claim)[:12]}")
    return _stable(ids), _stable(unknowns)


def _session_events(workspace: str, session: str) -> int:
    if not SESSION_RE.match(str(session or "")):
        return 0
    path = os.path.join(workspace, ".agents", "evidence", "tools", session + ".jsonl")
    if not os.path.isfile(path):
        return 0
    count = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("kind") != "permission":
                    continue
                if event.get("verdict") != "ALLOW":
                    continue
                if event.get("action") not in ("read", "shell", "grep", "glob", "list"):
                    continue
                count += 1
    except OSError:
        return 0
    return count


def _team_problems(workspace: str, record: dict, team) -> list:
    problems = []
    if not isinstance(team, dict):
        return ["review.team must be an object with plan and node"]
    plan_id = str(team.get("plan") or "")
    node = str(team.get("node") or "")
    if not plan_id.startswith("team-") or not node:
        return ["review.team needs a plan id (team-...) and a node id"]
    if not TEAM_ID_RE.match(plan_id) or not TEAM_ID_RE.match(node):
        return ["review.team plan/node ids contain invalid characters"]
    project = str(record.get("project") or "")
    base = os.path.join(workspace, ".agents", "evidence", "team", project, plan_id)
    plan_path = os.path.join(base, "plan.json")
    result_path = os.path.join(base, "results", node + ".json")
    if not os.path.isfile(plan_path):
        return [f"team plan artifact is missing: {plan_id}"]
    if not os.path.isfile(result_path):
        return [f"team result artifact is missing: {plan_id}/{node}"]
    try:
        with open(plan_path, encoding="utf-8") as handle:
            plan = json.load(handle)
        with open(result_path, encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"team artifact is unreadable: {exc}"]
    if plan.get("decision") != record.get("id"):
        problems.append(f"team plan belongs to decision {plan.get('decision')}, not {record.get('id')}")
    if plan.get("project") != record.get("project"):
        problems.append(f"team plan project {plan.get('project')} != {record.get('project')}")
    if plan.get("risk") != record.get("risk"):
        problems.append(f"team plan risk {plan.get('risk')} != {record.get('risk')}")
    nodes = [item for item in (plan.get("nodes") or []) if isinstance(item, dict)]
    selected = [item for item in nodes if item.get("id") == node]
    if not selected:
        problems.append(f"team node {node} is not part of plan {plan_id}")
    elif selected[0].get("role") != result.get("role"):
        problems.append("team result role does not match the planned node")
    roles = {str(item.get("role") or "") for item in nodes}
    independent = roles & set(REVIEW_ROLES)
    if len(independent) < 2:
        problems.append("team plan needs at least two independent review roles")
    for item in nodes:
        if item.get("role") in REVIEW_ROLES:
            item_id = str(item.get("id") or "")
            if not item_id or not os.path.isfile(os.path.join(base, "results", item_id + ".json")):
                problems.append(f"team review node {item_id or '(missing id)'} has no result")
    if result.get("node") != node:
        problems.append("team result does not identify the planned node")
    if result.get("protocol") != plan.get("protocol"):
        problems.append("team result protocol does not match the plan")
    if result.get("role") not in REVIEW_ROLES:
        problems.append(f"team node role {result.get('role')} is not an independent review role")
    if not result.get("evidence"):
        problems.append("team result carries no evidence")
    return problems


def _entry_list(value, keys: tuple, label: str) -> list:
    problems = []
    if not isinstance(value, list):
        return [f"{label} must be a list"]
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            problems.append(f"{label}[{index}] must be an object")
            continue
        for key in keys:
            if not str(item.get(key) or "").strip():
                problems.append(f"{label}[{index}].{key} is empty")
    return problems


def check_block(review, record: dict, workspace: str) -> list:
    """Validate a review block against the decision it belongs to."""
    problems = []
    if not isinstance(review, dict):
        return ["the ledger has no review block"]
    if _as_int(review.get("version")) != 1:
        problems.append("review.version must be 1")

    reviewer = review.get("reviewer")
    if not isinstance(reviewer, dict):
        problems.append("review.reviewer must be an object")
        reviewer = {}
    role = str(reviewer.get("role") or "")
    session = str(reviewer.get("session") or "")
    if role not in REVIEW_ROLES:
        problems.append(f"reviewer role {role or '(empty)'} is not an independent role")
    if not str(reviewer.get("agent") or "").strip():
        problems.append("reviewer.agent is empty")
    if not SESSION_RE.match(session):
        problems.append("reviewer.session is not a valid session id")
    ran_at = parse_time(reviewer.get("ran_at"))
    created_at = parse_time(record.get("created_at"))
    change_at = parse_time((record.get("change") or {}).get("recorded_at"))
    if not ran_at:
        problems.append("reviewer.ran_at is not a timestamp")
    elif created_at and ran_at < created_at:
        problems.append("reviewer.ran_at predates the decision")
    elif change_at and ran_at < change_at:
        problems.append("reviewer.ran_at predates the recorded change")

    team = review.get("team")
    team_problems = _team_problems(workspace, record, team) if team else []
    problems.extend(team_problems)
    if record.get("risk") == "CRITICAL" and not team:
        reviewers = review.get("reviewers")
        if not isinstance(reviewers, list) or len(reviewers) < 2:
            problems.append("CRITICAL requires a team artifact or two independent reviewer sessions")
        else:
            seen = {session}
            for index, item in enumerate(reviewers):
                if not isinstance(item, dict):
                    problems.append(f"review.reviewers[{index}] must be an object")
                    continue
                other_role = str(item.get("role") or "")
                other_session = str(item.get("session") or "")
                if other_role not in REVIEW_ROLES:
                    problems.append(f"reviewers[{index}] role is not an independent role")
                if not SESSION_RE.match(other_session):
                    problems.append(f"reviewers[{index}] session is not valid")
                    continue
                if other_session in seen:
                    problems.append(f"reviewers[{index}] session is not independent")
                seen.add(other_session)
                if other_session == str(record.get("session_id") or ""):
                    problems.append(f"reviewers[{index}] is the decision session")
                events = _session_events(workspace, other_session)
                if events < MIN_SESSION_EVENTS["CRITICAL"]:
                    problems.append(f"reviewers[{index}] has {events} recorded event(s); "
                                    "at least 2 required")

    same_session = bool(session and session == str(record.get("session_id") or ""))
    if same_session and not team:
        problems.append("reviewer session is the decision session and no team artifact proves independence")
    if not team:
        events = _session_events(workspace, session)
        minimum = MIN_SESSION_EVENTS.get(record.get("risk", "STANDARD"), 1)
        if events < minimum:
            problems.append(f"reviewer session has {events} recorded event(s); at least {minimum} required")

    required, impact_unknowns = surfaces(record)
    coverage = review.get("coverage")
    if not isinstance(coverage, dict):
        problems.append("review.coverage must be an object")
        coverage = {}
    inspected = coverage.get("inspected")
    not_inspected = coverage.get("not_inspected")
    problems.extend(_entry_list(inspected, ("surface", "evidence"), "coverage.inspected"))
    problems.extend(_entry_list(not_inspected, ("surface", "reason"), "coverage.not_inspected"))
    inspected = inspected if isinstance(inspected, list) else []
    not_inspected = not_inspected if isinstance(not_inspected, list) else []
    seen_in = {str(item.get("surface")) for item in inspected if isinstance(item, dict)}
    seen_out = {str(item.get("surface")) for item in not_inspected if isinstance(item, dict)}
    overlap = sorted(seen_in & seen_out)
    if overlap:
        problems.append("surfaces listed as both inspected and not inspected: " + ", ".join(overlap[:5]))
    missing = [item for item in required if item not in (seen_in | seen_out)]
    if missing:
        problems.append(f"{len(missing)} required surface(s) are not accounted for: " + ", ".join(missing[:5]))
    if not seen_in:
        problems.append("at least one surface must be inspected")
    changed = {f"file:{item}" for item in ((record.get("change") or {}).get("files") or [])}
    deferred_changed = sorted(changed & seen_out)
    if deferred_changed:
        problems.append("changed files cannot be deferred as not inspected: "
                        + ", ".join(deferred_changed[:5]))

    falsification = review.get("falsification")
    minimum = MIN_FALSIFICATION.get(record.get("risk", "STANDARD"), 1)
    if not isinstance(falsification, list) or len(falsification) < minimum:
        problems.append(f"at least {minimum} falsification test(s) are required")
    else:
        for index, item in enumerate(falsification):
            if not isinstance(item, dict):
                problems.append(f"falsification[{index}] must be an object")
                continue
            for key in ("claim", "test", "result", "evidence"):
                if not item.get(key):
                    problems.append(f"falsification[{index}].{key} is empty")
            if item.get("result") not in RESULTS:
                problems.append(f"falsification[{index}].result must be one of {', '.join(RESULTS)}")
        results = [item.get("result") for item in falsification if isinstance(item, dict)]
        if "passed" not in results:
            problems.append("at least one falsification test must pass before close")
        if "failed" in results:
            problems.append("a failed falsification test must be resolved and re-run before close")

    unknowns = review.get("unknowns")
    if not isinstance(unknowns, list):
        problems.append("review.unknowns must be a list")
        unknowns = []
    accepted = record.get("human_acceptance") or []
    accepted_hashes = {str(item.get("claim_sha256") or "") for item in accepted
                       if isinstance(item, dict)}
    declared = {}
    for index, item in enumerate(unknowns):
        if not isinstance(item, dict):
            problems.append(f"unknowns[{index}] must be an object")
            continue
        claim = " ".join(str(item.get("claim") or "").split())
        disposition = str(item.get("disposition") or "")
        if not claim:
            problems.append(f"unknowns[{index}].claim is empty")
            continue
        if claim in declared:
            problems.append(f"unknown is listed twice: {claim[:60]}")
        declared[claim] = item
        if disposition not in DISPOSITIONS:
            problems.append(f"unknown has no valid disposition: {claim[:60]}")
            continue
        if disposition == "closed" and not item.get("evidence"):
            problems.append(f"closed unknown has no evidence: {claim[:60]}")
        if disposition == "out_of_scope" and not str(item.get("reason") or "").strip():
            problems.append(f"out_of_scope unknown has no reason: {claim[:60]}")
        if disposition == "accepted_by_human" and claim_hash(claim) not in accepted_hashes:
            problems.append(f"accepted_by_human has no sealed human acceptance: {claim[:60]}")
    for claim in impact_unknowns:
        if claim not in declared:
            problems.append(f"impact unknown is not dispositioned: {claim[:80]}")
    return problems


def validate(workspace: str, record: dict) -> dict:
    """The single review validator used by decision close and completion."""
    workspace = ep.resolve_workspace(workspace)
    result = {"schema": 1, "ok": True, "skipped": False, "problems": [],
              "ledger": "", "required": {"surfaces": [], "unknowns": []}}
    if not applies(record, workspace):
        result["skipped"] = True
        return result

    required, impact_unknowns = surfaces(record)
    result["required"] = {"surfaces": required, "unknowns": impact_unknowns}
    if not isinstance(_value(record, "impact"), dict):
        result["problems"].append("impact is not a structured report; the blast radius cannot be derived")

    challenges = record.get("challenges")
    if challenges is not None and not isinstance(challenges, dict):
        result["problems"].append("challenges is not an object")
        result["ok"] = False
        return result
    challenges = challenges or {}
    ledger_ref = str(challenges.get("ledger") or "")
    if not ledger_ref:
        result["problems"].append("no sealed challenge ledger is bound to this decision")
        result["ok"] = False
        return result
    ledger_path = ep.load_stored_path(ledger_ref, workspace)
    if not os.path.isfile(ledger_path):
        result["problems"].append(f"bound challenge ledger is missing: {ledger_ref}")
        result["ok"] = False
        return result
    result["ledger"] = ledger_path
    stored = str(challenges.get("sha256") or "")
    if stored:
        with open(ledger_path, "rb") as handle:
            actual = hashlib.sha256(handle.read()).hexdigest().upper()
        if actual != stored:
            result["problems"].append("challenge ledger changed after it was attached")

    try:
        import challenge as ch
    except ImportError as exc:
        result["problems"].append(f"challenge tool is unavailable: {exc}")
        result["ok"] = False
        return result
    ledger_id = os.path.basename(ledger_path)[:-5]
    try:
        ledger = ch.load(workspace, str(record.get("project") or ""), ledger_id)
    except (ch.LedgerError, json.JSONDecodeError, OSError) as exc:
        result["problems"].append(str(exc))
        result["ok"] = False
        return result
    result["problems"].extend(ch.integrity(ledger))
    if ledger.get("decision") != record.get("id"):
        result["problems"].append(
            f"ledger belongs to decision {ledger.get('decision')}, not {record.get('id')}")
    if ledger.get("project") != record.get("project"):
        result["problems"].append(
            f"ledger project {ledger.get('project')} != {record.get('project')}")
    stored_blockers = _as_int(challenges.get("open_blockers"), -1)
    actual_blockers = ch.count_open_blockers(ledger)
    if stored_blockers != actual_blockers:
        result["problems"].append(
            f"challenge blocker count is stale: record={stored_blockers}, ledger={actual_blockers}")
    result["problems"].extend(check_block(ledger.get("review"), record, workspace))
    result["ok"] = not result["problems"]
    return result


def record_review(workspace: str, project: str, decision_id: str, review: dict,
                  actor: str = "") -> dict:
    """Seal a review block into the decision's ledger after full validation."""
    import decision as edr

    workspace = ep.resolve_workspace(workspace)
    record = edr.load(workspace, project, decision_id)
    problems = edr.integrity(record)
    if problems:
        raise ReviewError("; ".join(problems))
    if not applies(record, workspace):
        raise ReviewError("this decision does not require the review contract")
    if record.get("state") in ("CLOSED", "FAILED"):
        raise ReviewError(f"{decision_id} is {record.get('state')}; a sealed review is terminal")
    if record.get("state") != "VERIFIED":
        raise ReviewError(f"{decision_id} is {record.get('state')}; run the gate and verify before sealing the review")
    ledger_ref = str((record.get("challenges") or {}).get("ledger") or "")
    if not ledger_ref:
        raise ReviewError("attach a challenge ledger before recording the review")
    ledger_path = ep.load_stored_path(ledger_ref, workspace)
    if not os.path.isfile(ledger_path):
        raise ReviewError(f"challenge ledger is missing: {ledger_ref}")
    import challenge as ch
    ledger = ch.load(workspace, project, os.path.basename(ledger_path)[:-5])
    problems = ch.integrity(ledger)
    blockers = ch.count_open_blockers(ledger)
    if blockers:
        raise ReviewError(f"{blockers} open blocker(s) remain; resolve them before sealing the review")
    problems.extend(check_block(review, record, workspace))
    if problems:
        raise ReviewError("; ".join(problems))
    ledger["review"] = review
    ledger["reviewed_at"] = utc_now()
    ledger["reviewed_by"] = actor or "reviewer"
    path = ch.save(workspace, ledger)
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest().upper()
    record["challenges"]["sha256"] = digest
    record["challenges"]["open_blockers"] = ch.count_open_blockers(ledger)
    record["challenges"]["reviewed_at"] = ledger["reviewed_at"]
    record.setdefault("history", []).append(
        {"from": record.get("state"), "to": record.get("state"), "at": utc_now(),
         "actor": actor or "reviewer", "reason": "review sealed"})
    edr.save(workspace, record)
    return {"sealed": True, "ledger": path, "decision": decision_id,
            "surfaces": len(surfaces(record)[0])}


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def cmd_surfaces(args) -> int:
    import decision as edr
    workspace = ep.resolve_workspace(args.workspace)
    record = edr.load(workspace, args.project, args.decision)
    required, unknowns = surfaces(record)
    value = {"decision": args.decision, "risk": record.get("risk"),
             "surfaces": required, "unknowns": unknowns}
    print(json.dumps(value, indent=2, ensure_ascii=True) if args.json
          else "\n".join(required + [f"unknown: {item}" for item in unknowns]))
    return 0


def cmd_record(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    review = _load(args.file)
    try:
        value = record_review(workspace, args.project, args.decision, review, args.actor)
    except (ReviewError, OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {exc}")
        return 1
    print(f"SEALED: {value['decision']} review ({value['surfaces']} surface(s))")
    print(f"  ledger: {ep.store_path(value['ledger'], workspace)}")
    return 0


def cmd_check(args) -> int:
    import decision as edr
    workspace = ep.resolve_workspace(args.workspace)
    record = edr.load(workspace, args.project, args.decision)
    result = validate(workspace, record)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result["ok"] else 1
    if result["skipped"]:
        print("SKIPPED: the review contract does not apply to this decision")
        return 0
    print(("PASS" if result["ok"] else "BLOCKED") + f": {args.decision}")
    for problem in result["problems"]:
        print(f"  - {problem}")
    return 0 if result["ok"] else 1


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def workspace() -> str:
        root = tempfile.mkdtemp(prefix="eos-review-")
        os.makedirs(os.path.join(root, ".agents", "evidence", "tools"), exist_ok=True)
        os.makedirs(os.path.join(root, ".agents", "evidence", "challenges", "eos"), exist_ok=True)
        return root

    def fixture(root, risk="STANDARD", marker=1, session="ses_decision"):
        import challenge as ch
        decision_id = "edr-review-fixture"
        record = {
            "schema": 1, "kind": "engineering_decision", "id": decision_id,
            "project": "eos", "task": "x", "risk": risk, "state": "IMPLEMENTED",
            "created_at": "2026-01-01T00:00:00Z", "session_id": session,
            "review_contract": marker,
            "fields": {
                "scope": {"value": ["engine/tools/x.py"]},
                "impact": {"value": {"files": ["engine/tools/x.py"],
                                     "components": [{"id": "gate", "process": "main"}],
                                     "contracts": [{"path": "x.py", "symbol": "f"}],
                                     "tests": ["tests/test_x.py"],
                                     "unknowns": ["unit not proven"]}},
            },
            "challenges": {"open_blockers": 0, "ledger": ""},
            "human_acceptance": [],
            "change": {"files": ["engine/tools/x.py"]},
        }
        ledger = {"schema": 1, "kind": "challenge_ledger", "id": "chl-review-fixture",
                  "project": "eos", "decision": decision_id, "task": "x",
                  "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
                  "findings": [], "open_blockers": 0, "record_sha256": ""}
        path = ch.save(root, ledger)
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest().upper()
        record["challenges"] = {"open_blockers": 0,
                                "ledger": ep.store_path(path, root), "sha256": digest}
        required, _ = surfaces(record)
        review = {
            "version": 1,
            "reviewer": {"role": "reviewer", "agent": "reviewer",
                         "session": "ses_review", "ran_at": "2026-01-01T00:00:01Z"},
            "coverage": {
                "inspected": [{"surface": item, "evidence": ["x.py:1"]} for item in required],
                "not_inspected": [],
            },
            "falsification": [{"claim": "x is wrong", "test": "assert x",
                               "result": "passed", "evidence": ["cmd:pytest -q"]}],
            "unknowns": [{"claim": "unit not proven", "disposition": "closed",
                          "evidence": ["test:units"]}],
        }
        with open(os.path.join(root, ".agents", "evidence", "tools", "ses_review.jsonl"),
                  "w", encoding="utf-8", newline="\n") as handle:
            for index in (1, 2):
                handle.write(json.dumps({"ts": f"2026-01-01T00:00:0{index}Z", "kind": "permission",
                                         "action": "read", "resource": "x.py",
                                         "verdict": "ALLOW"}) + "\n")
        ledger["review"] = review
        ch.save(root, ledger)
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest().upper()
        record["challenges"]["sha256"] = digest
        return record, review

    @case("a complete review passes")
    def _():
        root = workspace()
        record, review = fixture(root)
        problems = check_block(review, record, root)
        assert not problems, problems
        result = validate(root, record)
        assert not result["problems"], result["problems"]
        return f"{len(surfaces(record)[0])} surfaces"

    @case("a decision without a ledger is blocked")
    def _():
        root = workspace()
        record, _ = fixture(root)
        record["challenges"] = {"open_blockers": 0, "ledger": ""}
        result = validate(root, record)
        assert not result["ok"]
        assert any("ledger" in item for item in result["problems"]), result["problems"]
        return "no ledger"

    @case("a missing review block is blocked")
    def _():
        root = workspace()
        record, _ = fixture(root)
        problems = check_block(None, record, root)
        assert any("no review block" in item for item in problems), problems
        return "no review"

    @case("a missing required surface is blocked")
    def _():
        root = workspace()
        record, review = fixture(root)
        review["coverage"]["inspected"].pop()
        problems = check_block(review, record, root)
        assert any("not accounted" in item for item in problems), problems
        return "surface gap"

    @case("an undispositioned impact unknown is blocked")
    def _():
        root = workspace()
        record, review = fixture(root)
        review["unknowns"] = []
        problems = check_block(review, record, root)
        assert any("not dispositioned" in item for item in problems), problems
        return "unknown gap"

    @case("accepted_by_human without a sealed record is blocked")
    def _():
        root = workspace()
        record, review = fixture(root)
        review["unknowns"][0] = {"claim": "unit not proven",
                                 "disposition": "accepted_by_human"}
        problems = check_block(review, record, root)
        assert any("human acceptance" in item for item in problems), problems
        record["human_acceptance"] = [{"claim": "unit not proven",
                                       "claim_sha256": claim_hash("unit not proven"),
                                       "actor": "owner", "at": "2026-01-02T00:00:00Z",
                                       "reason": "accepted for release"}]
        assert not check_block(review, record, root)
        return "human gate"

    @case("the marker cannot be removed from a new decision")
    def _():
        root = workspace()
        record, _ = fixture(root, marker=0)
        record["created_at"] = utc_now()
        assert applies(record)
        record["challenges"] = {"open_blockers": 0, "ledger": ""}
        result = validate(root, record)
        assert not result["ok"]
        assert any("ledger" in item for item in result["problems"]), result["problems"]
        return "cutoff enforced"

    @case("all surfaces deferred is blocked")
    def _():
        root = workspace()
        record, review = fixture(root)
        review["coverage"]["not_inspected"] = [
            {"surface": item["surface"], "reason": "not looked at"} for item in review["coverage"]["inspected"]
        ]
        review["coverage"]["inspected"] = []
        problems = check_block(review, record, root)
        assert any("at least one surface must be inspected" in item for item in problems), problems
        return "zero inspected"

    @case("a changed file cannot be deferred")
    def _():
        root = workspace()
        record, review = fixture(root)
        target = "file:engine/tools/x.py"
        review["coverage"]["inspected"] = [item for item in review["coverage"]["inspected"]
                                           if item["surface"] != target]
        review["coverage"]["not_inspected"] = [{"surface": target, "reason": "deferred"}]
        problems = check_block(review, record, root)
        assert any("changed files cannot be deferred" in item for item in problems), problems
        return "changed file inspected"

    @case("a DENY-only reviewer session is blocked")
    def _():
        root = workspace()
        record, review = fixture(root)
        with open(os.path.join(root, ".agents", "evidence", "tools", "ses_review.jsonl"),
                  "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"ts": "2026-01-01T00:00:02Z", "kind": "permission",
                                     "action": "read", "resource": "x.py",
                                     "verdict": "DENY"}) + "\n")
        problems = check_block(review, record, root)
        assert any("recorded event" in item for item in problems), problems
        return "deny rejected"

    @case("a legacy decision without the marker is skipped")
    def _():
        root = workspace()
        record, _ = fixture(root, marker=0)
        result = validate(root, record)
        assert result["ok"] and result["skipped"], result
        return "legacy skipped"

    @case("ATOMIC is skipped")
    def _():
        root = workspace()
        record, _ = fixture(root, risk="ATOMIC")
        result = validate(root, record)
        assert result["ok"] and result["skipped"], result
        return "atomic skipped"

    @case("CRITICAL without a team or two reviewers is blocked")
    def _():
        root = workspace()
        record, review = fixture(root, risk="CRITICAL")
        review["falsification"].append({"claim": "y", "test": "t", "result": "passed",
                                        "evidence": ["cmd:pytest"]})
        problems = check_block(review, record, root)
        assert any("two independent reviewer sessions" in item for item in problems), problems
        return "team or two reviewers required"

    @case("a failed falsification blocks close")
    def _():
        root = workspace()
        record, review = fixture(root)
        review["falsification"][0]["result"] = "failed"
        problems = check_block(review, record, root)
        assert any("failed falsification" in item for item in problems), problems
        return "failed test blocks"

    @case("CRITICAL passes with two independent reviewer sessions")
    def _():
        root = workspace()
        record, review = fixture(root, risk="CRITICAL")
        review["falsification"].append({"claim": "y", "test": "t", "result": "passed",
                                        "evidence": ["cmd:pytest"]})
        review["reviewers"] = [
            {"role": "bug-hunter", "session": "ses_review2"},
            {"role": "logic-reviewer", "session": "ses_review3"},
        ]
        for session in ("ses_review2", "ses_review3"):
            with open(os.path.join(root, ".agents", "evidence", "tools", session + ".jsonl"),
                      "w", encoding="utf-8", newline="\n") as handle:
                for index in (1, 2):
                    handle.write(json.dumps({"ts": f"2026-01-01T00:00:0{index}Z",
                                             "kind": "permission", "action": "read",
                                             "resource": "x.py", "verdict": "ALLOW"}) + "\n")
        problems = check_block(review, record, root)
        assert not problems, problems
        return "two reviewers"

    @case("a reviewer session with no evidence is blocked")
    def _():
        root = workspace()
        record, review = fixture(root)
        review["reviewer"]["session"] = "ses_missing"
        problems = check_block(review, record, root)
        assert any("recorded event" in item for item in problems), problems
        return "fake session"

    @case("a stale blocker count is blocked")
    def _():
        root = workspace()
        record, _ = fixture(root)
        import challenge as ch
        ledger = ch.load(root, "eos", "chl-review-fixture")
        ledger["findings"] = [{"id": "f-001", "role": "reviewer", "severity": "blocker",
                               "status": "open", "claim": "x", "evidence": ["x.py:1"],
                               "confidence": "HIGH", "falsifiable_test": "t"}]
        ch.save(root, ledger)
        result = validate(root, record)
        assert not result["ok"]
        assert any("blocker count is stale" in item for item in result["problems"]), result["problems"]
        return "stale count"

    @case("a tampered ledger is blocked")
    def _():
        root = workspace()
        record, _ = fixture(root)
        path = ep.load_stored_path(record["challenges"]["ledger"], root)
        data = _load(path)
        data["findings"].append({"id": "f-999", "severity": "info", "status": "open",
                                 "claim": "injected"})
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle)
        result = validate(root, record)
        assert not result["ok"]
        return "tamper"

    passed = failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            passed += 1
            print(f"PASS  {name}  [{detail}]")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(cases)} cases passed")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="review_contract.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")
    common.add_argument("--project", default="eos")

    p = sub.add_parser("surfaces", parents=[common])
    p.add_argument("--decision", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_surfaces)

    p = sub.add_parser("record", parents=[common])
    p.add_argument("--decision", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--actor", default="")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("check", parents=[common])
    p.add_argument("--decision", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
