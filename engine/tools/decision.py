#!/usr/bin/env python3
"""EOS Engineering Decision Record (EDR).

The quality gate answers "did the commands pass". It cannot answer "was this
change allowed to exist". This tool owns that second question.

A record is a small, hash-sealed state machine:

    DRAFT -> REVIEWED -> APPROVED_FOR_EDIT -> IMPLEMENTED -> VERIFIED -> CLOSED

with BLOCKED and FAILED as terminal-until-reopened side states. Two enforcement
points read it:

  * require-edit    before a write tool runs; the path must be inside the scope
                    of an APPROVED_FOR_EDIT or IMPLEMENTED record for the project
  * check --for promotion
                    before promote/ship; the record must be CLOSED, its gate
                    artifact must PASS, and the artifact must still hash the same

The record is sealed with record_sha256 over canonical JSON. Editing any field
without going through this tool invalidates the record and every enforcement
point refuses it.

Commands:
  open        --project <id> --task <text> --risk ATOMIC|STANDARD|CRITICAL
  set         --id <id> --field <name> --value <text|json> [--evidence <path>]
  transition  --id <id> --to <state> --actor <role> [--reason <text>]
  approve     --id <id> --actor <name> --role <role> [--auto]
  implement   --id <id> --actor <name> --file <path>...
  verify      --id <id> --gate <artifact.json> [--max-age-min N]
  close       --id <id> --actor <name>
  check       --id <id> [--for edit|promotion]
  require-edit --path <path> [--project <id>] [--json]
  status      --id <id>
  list        [--project <id>]
  selftest

Exit codes: 0 allowed / PASS, 1 FAIL/BLOCKED, 3 denied (edit not authorised).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

SCHEMA = 1
KIND = "engineering_decision"
STATES = ("DRAFT", "REVIEWED", "APPROVED_FOR_EDIT", "IMPLEMENTED",
          "VERIFIED", "CLOSED", "BLOCKED", "FAILED")
TERMINAL = ("CLOSED", "FAILED")
RISKS = ("ATOMIC", "STANDARD", "CRITICAL")
STATE_ORDER = {name: index for index, name in enumerate(STATES[:6])}

# The pre-edit contract. A field listed here must be present and non-empty
# before APPROVED_FOR_EDIT. ATOMIC is deliberately cheap, never empty.
REQUIRED_BEFORE_EDIT = {
    "ATOMIC": ("objective", "scope", "risk", "verification", "rollback"),
    "STANDARD": ("objective", "current_behavior", "do_not_regress", "evidence",
                 "assumptions", "impact", "alternatives", "scope", "risk",
                 "verification", "rollback"),
    "CRITICAL": ("objective", "current_behavior", "do_not_regress", "evidence",
                 "assumptions", "impact", "alternatives", "scope", "risk",
                 "verification", "rollback", "math_check", "independent_review"),
}

# Fields that must be present to leave DRAFT. Investigation, not ceremony:
# without these the reviewer has nothing to review.
REQUIRED_BEFORE_REVIEW = {
    "ATOMIC": ("objective", "scope", "verification"),
    "STANDARD": ("objective", "evidence", "impact", "do_not_regress", "assumptions"),
    "CRITICAL": ("objective", "evidence", "impact", "do_not_regress", "assumptions",
                 "current_behavior"),
}

MAX_SCOPE = 200
MAX_FIELD_CHARS = 20000


class DecisionError(Exception):
    pass


# --------------------------------------------------------------------- basics
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    return sha256_bytes(canonical(body))


def resolve_workspace(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("EOS_WORKSPACE")
    if env:
        return os.path.abspath(env)
    return os.getcwd()


def safe_project(project: str) -> str:
    if not project or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", project):
        raise DecisionError(f"invalid project id: {project!r}")
    return project


def infer_project(workspace: str, path: str) -> str:
    """Which project owns this path? A decision from another project must not
    authorise it.

    The workspace root's `.eos/project.json` is the *workspace* identity, not a
    project identity: it must never swallow every child project. A marker below
    the root wins; registered profile paths come before the workspace fallback.
    """
    absolute = os.path.abspath(path)
    current = absolute if os.path.isdir(absolute) else os.path.dirname(absolute)
    workspace_abs = os.path.abspath(workspace)
    workspace_id = ""
    while current == workspace_abs or current.startswith(workspace_abs + os.sep):
        marker = os.path.join(current, ".eos", "project.json")
        if os.path.isfile(marker):
            try:
                data = read_json(marker)
                marker_id = str(data.get("id") or "")
            except (OSError, json.JSONDecodeError):
                marker_id = ""
            if marker_id:
                if current != workspace_abs:
                    return marker_id
                workspace_id = marker_id
        if current == workspace_abs:
            break
        current = os.path.dirname(current)
    for registry in (os.path.join(workspace, ".agents", "registry"), ep.REGISTRY):
        profiles = os.path.join(registry, "PROJECT_PROFILES.json")
        if not os.path.isfile(profiles):
            continue
        try:
            data = read_json(profiles)
        except (OSError, json.JSONDecodeError):
            continue
        for profile in data.get("profiles", []):
            root = os.path.abspath(os.path.join(workspace, profile.get("path", "")))
            if absolute == root or absolute.startswith(root + os.sep):
                return str(profile.get("id") or "")
    return workspace_id


def decisions_root(workspace: str) -> str:
    return os.path.join(workspace, ".agents", "evidence", "decisions")


def project_dir(workspace: str, project: str) -> str:
    return os.path.join(decisions_root(workspace), safe_project(project))


def decision_path(workspace: str, project: str, decision_id: str) -> str:
    if not re.fullmatch(r"edr-[A-Za-z0-9._-]{4,80}", decision_id or ""):
        raise DecisionError(f"invalid decision id: {decision_id!r}")
    return os.path.join(project_dir(workspace, project), decision_id + ".json")


def new_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"edr-{stamp}-{os.urandom(3).hex()}"


def write_json_atomic(path: str, value) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    os.replace(tmp, path)


def read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------- path scope
def normalise_rel(value: str) -> str:
    text = (value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def scope_pattern(pattern: str) -> str:
    norm = normalise_rel(pattern)
    if not norm:
        raise DecisionError("empty scope pattern")
    if any(part == ".." for part in norm.split("/")):
        raise DecisionError(f"scope may not contain '..': {pattern}")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        raise DecisionError(f"scope must be workspace-relative: {pattern}")
    return norm


def _pattern_regex(pattern: str) -> re.Pattern:
    out = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index:index + 3] == "**/":
                out.append(r"(?:.*/)?")
                index += 3
                continue
            if pattern[index:index + 2] == "**":
                out.append(r".*")
                index += 2
                continue
            out.append(r"[^/]*")
        elif char == "?":
            out.append(r"[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(out) + "$")


def path_in_scope(pattern: str, path: str) -> bool:
    pattern = normalise_rel(pattern)
    path = normalise_rel(path)
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return bool(_pattern_regex(pattern).match(path))


def covers(scope: list, path: str) -> bool:
    return any(path_in_scope(pattern, path) for pattern in scope or [])


# ------------------------------------------------------------------- records
def load(workspace: str, project: str, decision_id: str) -> dict:
    path = decision_path(workspace, project, decision_id)
    if not os.path.isfile(path):
        raise DecisionError(f"decision not found: {decision_id}")
    try:
        record = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionError(f"decision unreadable: {exc}") from exc
    return record


def save(workspace: str, record: dict) -> str:
    record["updated_at"] = utc_now()
    record["record_sha256"] = record_hash(record)
    path = decision_path(workspace, record["project"], record["id"])
    write_json_atomic(path, record)
    return path


def integrity(record: dict) -> list:
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append(f"schema {record.get('schema')} != {SCHEMA}")
    if record.get("kind") != KIND:
        problems.append("kind is not engineering_decision")
    if record.get("state") not in STATES:
        problems.append(f"unknown state: {record.get('state')}")
    if record.get("risk") not in RISKS:
        problems.append(f"unknown risk tier: {record.get('risk')}")
    if not record.get("record_sha256"):
        problems.append("record is not sealed (record_sha256 missing)")
    elif record_hash(record) != record["record_sha256"]:
        problems.append("record hash mismatch (edited outside this tool)")
    if not record.get("history"):
        problems.append("empty history")
    return problems


def field_value(record: dict, name: str):
    entry = (record.get("fields") or {}).get(name)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def missing(record: dict, names) -> list:
    return [name for name in names if not is_filled(field_value(record, name))]


def scope_list(record: dict) -> list:
    value = field_value(record, "scope")
    return [str(item) for item in value] if isinstance(value, list) else []


# ------------------------------------------------------------------ commands
def cmd_open(args) -> int:
    workspace = resolve_workspace(args.workspace)
    project = safe_project(args.project)
    if args.risk not in RISKS:
        print(f"BLOCKED: risk must be one of {', '.join(RISKS)}")
        return 2
    if not (args.task or "").strip():
        print("BLOCKED: a decision needs the task it is about")
        return 2
    scope = []
    for raw in args.scope or []:
        try:
            scope.append(scope_pattern(raw))
        except DecisionError as exc:
            print(f"BLOCKED: {exc}")
            return 2
    if not scope:
        print("BLOCKED: a decision needs --scope; an unregistered write is not allowed")
        return 2
    if len(scope) > MAX_SCOPE:
        print(f"BLOCKED: scope is limited to {MAX_SCOPE} entries")
        return 2

    base_commit = ""
    try:
        base_commit = subprocess.run(
            ["git", "-C", workspace, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        base_commit = ""

    record = {
        "schema": SCHEMA,
        "kind": KIND,
        "id": new_id(),
        "project": project,
        "task": args.task.strip()[:4000],
        "task_sha256": sha256_bytes(args.task.strip().encode("utf-8")),
        "risk": args.risk,
        "state": "DRAFT",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "workspace": ep.store_path(workspace, workspace),
        "base_commit": base_commit,
        "fields": {},
        "approval": None,
        "gate": None,
        "change": {"files": [], "diff_sha256": ""},
        "challenges": {"open_blockers": 0, "ledger": ""},
        "history": [{"from": "", "to": "DRAFT", "at": utc_now(),
                     "actor": args.actor or "eos.decision", "reason": "opened"}],
        "record_sha256": "",
    }

    # ATOMIC is the only tier that may auto-fill. It is still a record with a
    # scope, a verification target and a rollback, and it is still hash-sealed.
    fields = record["fields"]
    fields["objective"] = {"value": record["task"], "evidence": []}
    fields["scope"] = {"value": scope, "evidence": []}
    fields["risk"] = {"value": args.risk, "evidence": []}
    if args.risk == "ATOMIC":
        fields["current_behavior"] = {"value": "not investigated: atomic change inside the stated scope",
                                      "evidence": []}
        fields["do_not_regress"] = {"value": ["nothing outside the declared scope may change"],
                                    "evidence": []}
        fields["evidence"] = {"value": [], "evidence": scope[:10]}
        fields["assumptions"] = {"value": ["the change is local to the declared scope"], "evidence": []}
        fields["impact"] = {"value": {"files": scope, "components": [], "contracts": [],
                                      "tests": [], "unknowns": [],
                                      "source": "declared scope only", "confidence": "LOW"},
                            "evidence": []}
        fields["alternatives"] = {"value": ["do nothing"], "evidence": []}
        fields["verification"] = {"value": {"suite": args.suite or "", "commands": []}, "evidence": []}
        fields["rollback"] = {"value": "git revert of the change; no data migration", "evidence": []}

    path = save(workspace, record)
    print(f"OPEN: {record['id']} ({project}, {args.risk}, DRAFT)")
    print(f"  record: {ep.store_path(path, workspace)}")
    print(f"  scope : {', '.join(scope)}")
    if args.approve:
        if args.risk != "ATOMIC":
            print("BLOCKED: --approve is only allowed for ATOMIC; every other tier needs a reviewer")
            return 2
        return approve_record(workspace, record, actor=args.actor or "eos.atomic",
                              role="eos.atomic", auto=True)
    return 0


def set_field(record: dict, name: str, value, evidence=None) -> None:
    if name not in {
        "objective", "current_behavior", "do_not_regress", "evidence", "assumptions",
        "impact", "alternatives", "scope", "risk", "verification", "rollback",
        "math_check", "independent_review", "change_scope", "notes",
    }:
        raise DecisionError(f"unknown field: {name}")
    text = value.strip() if isinstance(value, str) else value
    if isinstance(text, str) and len(text) > MAX_FIELD_CHARS:
        raise DecisionError(f"field {name} exceeds {MAX_FIELD_CHARS} characters")
    if name == "scope":
        if isinstance(text, str):
            text = [text]
        if not isinstance(text, list):
            raise DecisionError("scope must be a list of paths or globs")
        text = [scope_pattern(item) for item in text]
    record["fields"][name] = {"value": text, "evidence": list(evidence or [])}


def cmd_set(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    problems = integrity(record)
    if problems:
        print("BLOCKED: decision record is not trustworthy")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if record["state"] in TERMINAL:
        print(f"BLOCKED: {record['id']} is {record['state']}; open a new decision")
        return 1
    value = args.value
    if isinstance(value, str) and value[:1] in "[{":
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            print(f"BLOCKED: --value looks like JSON but does not parse: {exc}")
            return 2
    try:
        set_field(record, args.field, value, args.evidence)
    except DecisionError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    save(workspace, record)
    print(f"SET: {record['id']} {args.field}")
    return 0


def transition(workspace: str, record: dict, target: str, actor: str, reason: str = "") -> int:
    problems = integrity(record)
    if problems:
        print("BLOCKED: decision record is not trustworthy")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    current = record["state"]
    if target not in STATES:
        print(f"BLOCKED: unknown state {target}")
        return 2
    if current == target:
        print(f"BLOCKED: already {target}")
        return 1
    if current in TERMINAL:
        print(f"BLOCKED: {current} is terminal; open a new decision")
        return 1

    allowed = {
        "DRAFT": {"REVIEWED", "BLOCKED", "FAILED"},
        "REVIEWED": {"APPROVED_FOR_EDIT", "DRAFT", "BLOCKED", "FAILED"},
        "APPROVED_FOR_EDIT": {"IMPLEMENTED", "BLOCKED", "FAILED"},
        "IMPLEMENTED": {"VERIFIED", "BLOCKED", "FAILED"},
        "VERIFIED": {"CLOSED", "BLOCKED", "FAILED"},
        "BLOCKED": {"DRAFT", "FAILED"},
    }
    if target not in allowed.get(current, set()):
        print(f"BLOCKED: illegal transition {current} -> {target}")
        return 1

    tier = record["risk"]
    if target == "REVIEWED":
        gaps = missing(record, REQUIRED_BEFORE_REVIEW[tier])
        if gaps:
            print("BLOCKED: investigation is incomplete before review")
            print("  missing: " + ", ".join(gaps))
            return 1
    if target == "APPROVED_FOR_EDIT":
        gaps = missing(record, REQUIRED_BEFORE_EDIT[tier])
        if gaps:
            print("BLOCKED: the pre-edit contract is incomplete")
            print("  missing: " + ", ".join(gaps))
            return 1
        if int((record.get("challenges") or {}).get("open_blockers") or 0) > 0:
            print("BLOCKED: open blocker(s) in the challenge ledger")
            return 1
        if not record.get("approval"):
            print("BLOCKED: APPROVED_FOR_EDIT requires an approval record")
            return 1
    if target == "IMPLEMENTED":
        files = (record.get("change") or {}).get("files") or []
        if not files:
            print("BLOCKED: no changed file was recorded; use `implement --file`")
            return 1
        outside = [f for f in files if not covers(scope_list(record), f)]
        if outside:
            print("BLOCKED: change left the approved scope")
            for item in outside[:20]:
                print(f"  - {item}")
            return 1
        change = record.setdefault("change", {})
        if not change.get("recorded_at"):
            change["recorded_at"] = utc_now()
    if target == "VERIFIED":
        gate = record.get("gate") or {}
        if not gate.get("ok"):
            print("BLOCKED: no verified gate artifact on this decision; use `verify --gate`")
            return 1
        if gate.get("verdict") != "PASS":
            print(f"BLOCKED: gate verdict is {gate.get('verdict')}")
            return 1
    if target == "CLOSED":
        if not is_filled(field_value(record, "rollback")):
            print("BLOCKED: rollback path is not recorded")
            return 1
        if int((record.get("challenges") or {}).get("open_blockers") or 0) > 0:
            print("BLOCKED: open blocker(s) remain")
            return 1
    if target == "BLOCKED" and not (reason or "").strip():
        print("BLOCKED: BLOCKED requires a reason")
        return 2

    record["state"] = target
    record["history"].append({"from": current, "to": target, "at": utc_now(),
                              "actor": actor or "eos.decision",
                              "reason": (reason or "").strip()[:1000]})
    save(workspace, record)
    print(f"{target}: {record['id']}")
    return 0


def cmd_transition(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    return transition(workspace, record, args.to, args.actor, args.reason or "")


def approve_record(workspace: str, record: dict, actor: str, role: str, auto: bool) -> int:
    tier = record["risk"]
    gaps = missing(record, REQUIRED_BEFORE_EDIT[tier])
    if gaps:
        print("BLOCKED: the pre-edit contract is incomplete")
        print("  missing: " + ", ".join(gaps))
        return 1
    if int((record.get("challenges") or {}).get("open_blockers") or 0) > 0:
        print("BLOCKED: open blocker(s) in the challenge ledger")
        return 1
    if not auto and role not in ("architect", "chief-architect", "reviewer"):
        print("BLOCKED: only architect or reviewer may approve a non-atomic change")
        return 1
    record["approval"] = {
        "by": actor or role or "eos",
        "role": role or "architect",
        "at": utc_now(),
        "mode": "auto" if auto else "reviewed",
    }
    if record["state"] == "DRAFT":
        record["state"] = "REVIEWED"
        record["history"].append({"from": "DRAFT", "to": "REVIEWED", "at": utc_now(),
                                  "actor": actor or role, "reason": "approval review"})
    save(workspace, record)
    return transition(workspace, record, "APPROVED_FOR_EDIT", actor or role,
                      "pre-edit contract complete")


def cmd_approve(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    return approve_record(workspace, record, args.actor, args.role, args.auto)


def cmd_implement(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    if record["state"] not in ("APPROVED_FOR_EDIT", "IMPLEMENTED"):
        print(f"BLOCKED: {record['state']} is not a writable state")
        return 1
    files = []
    problems = []
    for raw in args.file or []:
        absolute = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(workspace, raw))
        if not (absolute == workspace or absolute.startswith(workspace + os.sep)):
            problems.append(f"outside the workspace: {raw}")
            continue
        rel = ep.store_path(absolute, workspace)
        if not covers(scope_list(record), rel):
            problems.append(f"outside the approved scope: {rel}")
            continue
        files.append(rel)
    if problems:
        print("BLOCKED: implementation record rejected")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if not files:
        print("BLOCKED: no file recorded")
        return 2
    known = list((record.get("change") or {}).get("files") or [])
    for item in files:
        if item not in known:
            known.append(item)
    stamps = []
    for item in known:
        full_path = ep.load_stored_path(item, workspace)
        try:
            stamps.append(os.path.getmtime(full_path))
        except OSError:
            stamps.append(time.time())
    # The gate must be newer than the last real edit, not newer than the moment
    # the agent got around to recording it. File mtime is the only honest clock
    # available here; `implement` may legitimately run after the gate.
    recorded_at = iso_from_timestamp(max(stamps)) if stamps else utc_now()
    record["change"] = {"files": known, "diff_sha256": "", "recorded_at": recorded_at}
    save(workspace, record)
    if record["state"] == "APPROVED_FOR_EDIT":
        return transition(workspace, record, "IMPLEMENTED", args.actor or "implementer",
                          "implementation recorded")
    print(f"IMPLEMENTED: {record['id']} ({len(known)} file(s))")
    return 0


def cmd_attach(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    if record["state"] in TERMINAL:
        print(f"BLOCKED: {record['id']} is {record['state']}")
        return 1
    ledger_path = os.path.abspath(args.ledger if os.path.isabs(args.ledger)
                                  else os.path.join(workspace, args.ledger))
    if not (ledger_path == workspace or ledger_path.startswith(workspace + os.sep)):
        print("BLOCKED: the ledger must live inside the workspace")
        return 1
    if not os.path.isfile(ledger_path):
        print(f"BLOCKED: ledger not found: {args.ledger}")
        return 1
    try:
        import challenge as ch
    except ImportError as exc:
        print(f"BLOCKED: challenge tool is unavailable: {exc}")
        return 2
    try:
        ledger = ch.load(workspace, args.project, os.path.basename(ledger_path)[:-5])
    except ch.LedgerError as exc:
        print(f"BLOCKED: {exc}")
        return 1
    problems = ch.integrity(ledger)
    if problems:
        print("BLOCKED: challenge ledger is not trustworthy")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if ledger.get("project") != record["project"]:
        print(f"BLOCKED: ledger project {ledger.get('project')} != {record['project']}")
        return 1
    if ledger.get("decision") != record["id"]:
        print(f"BLOCKED: ledger belongs to decision {ledger.get('decision')}, not {record['id']}")
        return 1
    blockers = ch.count_open_blockers(ledger)
    record["challenges"] = {
        "open_blockers": blockers,
        "ledger": ep.store_path(ledger_path, workspace),
        "sha256": sha256_file(ledger_path),
    }
    save(workspace, record)
    print(f"ATTACH: {record['id']} <- {ledger['id']} ({blockers} open blocker(s))")
    return 0


def cmd_verify(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    if record["state"] not in ("IMPLEMENTED", "VERIFIED"):
        print(f"BLOCKED: {record['state']} cannot be verified; implementation first")
        return 1
    try:
        import quality_gate as qg
    except ImportError as exc:
        print(f"BLOCKED: quality gate is unavailable: {exc}")
        return 2
    qg.WORKSPACE = workspace
    result = qg.verify_artifact(os.path.abspath(args.gate), record["project"],
                                args.max_age_min)
    artifact = result.get("artifact") or {}
    change_at = parse_time((record.get("change") or {}).get("recorded_at", ""))
    gate_at = parse_time(artifact.get("created_at", ""))
    stale_gate = bool(change_at and gate_at and gate_at < change_at)
    problems = list(result["problems"])
    if stale_gate:
        problems.append("gate artifact predates the recorded change")
    record["gate"] = {
        "ok": bool(result["ok"]) and not stale_gate,
        "path": ep.store_path(os.path.abspath(args.gate), workspace),
        "sha256": sha256_file(os.path.abspath(args.gate)) if os.path.isfile(args.gate) else "",
        "project": artifact.get("project"),
        "suite": artifact.get("suite"),
        "verdict": artifact.get("verdict"),
        "created_at": artifact.get("created_at"),
        "problems": problems,
    }
    save(workspace, record)
    if not record["gate"]["ok"]:
        print("BLOCKED: gate artifact is not trustworthy")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if record["state"] == "VERIFIED":
        print(f"VERIFIED: {record['id']} (artifact re-checked)")
        return 0
    return transition(workspace, record, "VERIFIED", args.actor or "verifier",
                      "gate artifact verified")


def cmd_close(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    return transition(workspace, record, "CLOSED", args.actor or "verifier",
                      args.reason or "promotion sealed")


def latest_decisions(workspace: str, project: str | None = None) -> list:
    root = decisions_root(workspace)
    if not os.path.isdir(root):
        return []
    records = []
    projects = [safe_project(project)] if project else [
        name for name in os.listdir(root) if os.path.isdir(os.path.join(root, name))
    ]
    for name in projects:
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        for entry in os.listdir(directory):
            if not entry.endswith(".json"):
                continue
            try:
                record = read_json(os.path.join(directory, entry))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("kind") != KIND:
                continue
            records.append(record)
    records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return records


def check_edit(workspace: str, project: str | None, path: str,
               max_age_hours: float = 24.0) -> dict:
    rel = ep.store_path(path, workspace)
    if project is None:
        project = infer_project(workspace, path) or None
    reasons = []
    for record in latest_decisions(workspace, project):
        problems = integrity(record)
        if problems:
            reasons.append(f"{record.get('id')}: untrusted record ({problems[0]})")
            continue
        if record.get("state") not in ("APPROVED_FOR_EDIT", "IMPLEMENTED"):
            continue
        if not covers(scope_list(record), rel):
            continue
        updated = parse_time(record.get("updated_at", ""))
        if max_age_hours and updated:
            age_h = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
            if age_h > max_age_hours:
                reasons.append(f"{record['id']}: decision is {age_h:.1f}h old (limit {max_age_hours}h)")
                continue
        return {"ok": True, "decision": record["id"], "project": record["project"],
                "state": record["state"], "path": rel, "reasons": []}
    if not reasons:
        reasons.append("no APPROVED_FOR_EDIT decision covers this path")
    return {"ok": False, "decision": None, "path": rel, "reasons": reasons}


def cmd_require_edit(args) -> int:
    workspace = resolve_workspace(args.workspace)
    absolute = args.path if os.path.isabs(args.path) else os.path.join(workspace, args.path)
    absolute = os.path.abspath(absolute)
    if not (absolute == workspace or absolute.startswith(workspace + os.sep)):
        result = {"ok": False, "path": args.path,
                  "reasons": ["path is outside the workspace"]}
    else:
        result = check_edit(workspace, args.project, absolute, args.max_age_hours)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    elif result["ok"]:
        print(f"ALLOW: {result['path']} covered by {result['decision']} ({result['state']})")
    else:
        print(f"DENY: no approved decision covers {result['path']}")
        for reason in result["reasons"]:
            print(f"  - {reason}")
        print("  next: python .agents/tools/decision.py open --project <id> --task \"...\" "
              "--risk ATOMIC --scope <path> --approve")
    return 0 if result["ok"] else 3


def cmd_check(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    problems = integrity(record)
    result = {"ok": not problems, "state": record.get("state"), "problems": problems}
    if args.for_ == "edit":
        if record.get("state") not in ("APPROVED_FOR_EDIT", "IMPLEMENTED"):
            result["ok"] = False
            result["problems"] = problems + [f"state {record.get('state')} is not writable"]
    elif args.for_ == "promotion":
        if record.get("state") != "CLOSED":
            result["ok"] = False
            result["problems"] = problems + [f"state {record.get('state')} is not CLOSED"]
        gate = record.get("gate") or {}
        if not gate.get("ok") or gate.get("verdict") != "PASS":
            result["ok"] = False
            result["problems"] = result["problems"] + ["gate is not a verified PASS"]
        if gate.get("sha256") and gate.get("path"):
            gate_path = ep.load_stored_path(gate["path"], workspace)
            if not os.path.isfile(gate_path):
                result["ok"] = False
                result["problems"] = result["problems"] + ["gate artifact is missing"]
            elif sha256_file(gate_path) != gate["sha256"]:
                result["ok"] = False
                result["problems"] = result["problems"] + ["gate artifact changed after verification"]
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(("PASS" if result["ok"] else "BLOCKED") + f": {record['id']} state={record.get('state')}")
        for problem in result["problems"]:
            print(f"  - {problem}")
    return 0 if result["ok"] else 1


def cmd_status(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    problems = integrity(record)
    print(f"{record['id']}  {record['project']}  {record['risk']}  {record['state']}")
    print(f"  task    : {record['task'][:120]}")
    print(f"  scope   : {', '.join(scope_list(record))}")
    print(f"  updated : {record['updated_at']}")
    print(f"  seal    : {'OK' if not problems else 'BROKEN'}")
    for problem in problems:
        print(f"    - {problem}")
    gate = record.get("gate") or {}
    if gate:
        print(f"  gate    : {gate.get('verdict')} {gate.get('suite')} ok={gate.get('ok')}")
    print("  history :")
    for item in record.get("history", []):
        print(f"    {item.get('at')} {item.get('from') or '-'} -> {item.get('to')} ({item.get('actor')})")
    return 0 if not problems else 1


def cmd_list(args) -> int:
    workspace = resolve_workspace(args.workspace)
    records = latest_decisions(workspace, args.project)
    if args.json:
        print(json.dumps([{k: r.get(k) for k in ("id", "project", "risk", "state", "task", "updated_at")}
                          for r in records], ensure_ascii=True))
        return 0
    if not records:
        print("no decisions recorded")
        return 0
    for record in records:
        print(f"{record.get('updated_at')}  {record.get('state'):<17} {record.get('risk'):<8} "
              f"{record.get('id')}  {str(record.get('task'))[:60]}")
    return 0


# ------------------------------------------------------------------- selftest
def _fixture_gate(workspace: str, project: str = "eos", verdict: str = "PASS",
                  age_minutes: float = 0, with_log: bool = True) -> str:
    gate_dir = os.path.join(workspace, ".agents", "evidence", "gates", project)
    os.makedirs(gate_dir, exist_ok=True)
    baseline = os.path.join(gate_dir, "baseline.json")
    with open(baseline, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"files": [], "label": "baseline_test"}, handle)
    log = os.path.join(gate_dir, "gate-pass.log")
    with open(log, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("fixture\n")
    created = datetime.now(timezone.utc)
    if age_minutes:
        created = created.replace(microsecond=0)
        created = created.fromtimestamp(created.timestamp() - age_minutes * 60, tz=timezone.utc)
    artifact = {
        "schema": 1,
        "kind": "quality_gate",
        "created_at": created.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "project": project,
        "suite": "unit",
        "verdict": verdict,
        "baseline": {"label": "baseline_test", "sha256": sha256_file(baseline),
                     "path": ep.store_path(baseline, workspace)},
        "project_path": ".",
        "commands": [{"name": "pytest", "argv": ["pytest"], "exit_code": 0,
                      "timed_out": False, "duration_seconds": 1.0,
                      "output_sha256": "00", "output_bytes": 1, "required": True}],
        "failures": [],
        "optional_failures": [],
        "log": {"path": ep.store_path(log, workspace) if with_log else "",
                "sha256": sha256_file(log) if with_log else "", "bytes": 1},
        "runtime": {"python": "test", "platform": "test", "machine": "test"},
        "limits": {"timeout_seconds": 1},
    }
    if verdict != "PASS":
        artifact["commands"][0]["exit_code"] = 1
        artifact["failures"] = ["pytest"]
    path = os.path.join(gate_dir, "gate-unit-fixture.json")
    write_json_atomic(path, artifact)
    return path


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def workspace() -> str:
        root = tempfile.mkdtemp(prefix="eos-edr-")
        os.makedirs(os.path.join(root, ".agents", "evidence"), exist_ok=True)
        return root

    def open_atomic(root, scope="engine/tools/x.py", approve=True) -> str:
        args = argparse.Namespace(
            project="eos", task="fix a local bug", risk="ATOMIC", scope=[scope],
            suite="unit", actor="eos.test", approve=approve, workspace=root,
        )
        assert cmd_open(args) == 0
        return latest_decisions(root, "eos")[0]["id"]

    def record(root, decision_id) -> dict:
        return load(root, "eos", decision_id)

    @case("atomic write denied before approval")
    def _():
        root = workspace()
        decision_id = open_atomic(root, approve=False)
        result = check_edit(root, "eos", os.path.join(root, "engine/tools/x.py"))
        assert not result["ok"], result
        return decision_id

    @case("atomic approval allows only its scope")
    def _():
        root = workspace()
        open_atomic(root)
        inside = check_edit(root, "eos", os.path.join(root, "engine/tools/x.py"))
        outside = check_edit(root, "eos", os.path.join(root, "engine/tools/y.py"))
        assert inside["ok"], inside
        assert not outside["ok"], outside
        return inside["decision"]

    @case("scope cannot escape the workspace")
    def _():
        root = workspace()
        try:
            scope_pattern("../outside.py")
        except DecisionError:
            return "rejected"
        raise AssertionError(".. was accepted")

    @case("tampered record is refused")
    def _():
        root = workspace()
        decision_id = open_atomic(root)
        path = decision_path(root, "eos", decision_id)
        data = read_json(path)
        data["fields"]["objective"]["value"] = "rewritten after approval"
        write_json_atomic(path, data)
        result = check_edit(root, "eos", os.path.join(root, "engine/tools/x.py"))
        assert not result["ok"], result
        return "hash mismatch detected"

    @case("edit after verification requires a new decision")
    def _():
        root = workspace()
        decision_id = open_atomic(root)
        r = record(root, decision_id)
        r["change"] = {"files": ["engine/tools/x.py"], "diff_sha256": ""}
        save(root, r)
        assert transition(root, r, "IMPLEMENTED", "implementer") == 0
        gate = _fixture_gate(root)
        assert cmd_verify(argparse.Namespace(project="eos", id=decision_id, gate=gate,
                                             max_age_min=None, actor="verifier",
                                             workspace=root)) == 0
        r = record(root, decision_id)
        assert transition(root, r, "CLOSED", "verifier", "done") == 0
        result = check_edit(root, "eos", os.path.join(root, "engine/tools/x.py"))
        assert not result["ok"], result
        return "CLOSED is not writable"

    @case("promotion requires CLOSED with matching PASS")
    def _():
        root = workspace()
        decision_id = open_atomic(root)
        r = record(root, decision_id)
        r["change"] = {"files": ["engine/tools/x.py"], "diff_sha256": ""}
        save(root, r)
        transition(root, r, "IMPLEMENTED", "implementer")
        gate = _fixture_gate(root)
        cmd_verify(argparse.Namespace(project="eos", id=decision_id, gate=gate,
                                      max_age_min=None, actor="verifier", workspace=root))
        r = record(root, decision_id)
        blocked = cmd_check(argparse.Namespace(project="eos", id=decision_id, for_="promotion",
                                               json=True, workspace=root))
        assert blocked == 1
        transition(root, r, "CLOSED", "verifier", "done")
        allowed = cmd_check(argparse.Namespace(project="eos", id=decision_id, for_="promotion",
                                               json=True, workspace=root))
        assert allowed == 0
        return decision_id

    @case("failed gate cannot verify")
    def _():
        root = workspace()
        decision_id = open_atomic(root)
        r = record(root, decision_id)
        r["change"] = {"files": ["engine/tools/x.py"], "diff_sha256": ""}
        save(root, r)
        transition(root, r, "IMPLEMENTED", "implementer")
        gate = _fixture_gate(root, verdict="FAIL")
        rc = cmd_verify(argparse.Namespace(project="eos", id=decision_id, gate=gate,
                                           max_age_min=None, actor="verifier", workspace=root))
        assert rc == 1
        return "FAIL refused"

    @case("wrong-project gate cannot verify")
    def _():
        root = workspace()
        decision_id = open_atomic(root)
        r = record(root, decision_id)
        r["change"] = {"files": ["engine/tools/x.py"], "diff_sha256": ""}
        save(root, r)
        transition(root, r, "IMPLEMENTED", "implementer")
        gate = _fixture_gate(root, project="bighero")
        rc = cmd_verify(argparse.Namespace(project="eos", id=decision_id, gate=gate,
                                           max_age_min=None, actor="verifier", workspace=root))
        assert rc == 1
        return "project mismatch refused"

    @case("gate older than the change is refused")
    def _():
        root = workspace()
        decision_id = open_atomic(root)
        r = record(root, decision_id)
        r["change"] = {"files": ["engine/tools/x.py"], "diff_sha256": ""}
        save(root, r)
        transition(root, r, "IMPLEMENTED", "implementer")
        gate = _fixture_gate(root, age_minutes=120)
        rc = cmd_verify(argparse.Namespace(project="eos", id=decision_id, gate=gate,
                                           max_age_min=None, actor="verifier", workspace=root))
        assert rc == 1
        return "stale gate refused"

    @case("post-verification gate edit is detected")
    def _():
        root = workspace()
        decision_id = open_atomic(root)
        r = record(root, decision_id)
        r["change"] = {"files": ["engine/tools/x.py"], "diff_sha256": ""}
        save(root, r)
        transition(root, r, "IMPLEMENTED", "implementer")
        gate = _fixture_gate(root)
        cmd_verify(argparse.Namespace(project="eos", id=decision_id, gate=gate,
                                      max_age_min=None, actor="verifier", workspace=root))
        r = record(root, decision_id)
        transition(root, r, "CLOSED", "verifier", "done")
        data = read_json(gate)
        data["commands"][0]["duration_seconds"] = 999.0
        write_json_atomic(gate, data)
        rc = cmd_check(argparse.Namespace(project="eos", id=decision_id, for_="promotion",
                                          json=False, workspace=root))
        assert rc == 1
        return "artifact hash mismatch"

    @case("out-of-scope implementation is refused")
    def _():
        root = workspace()
        decision_id = open_atomic(root)
        rc = cmd_implement(argparse.Namespace(project="eos", id=decision_id,
                                              file=["engine/other/y.py"],
                                              actor="implementer", workspace=root))
        assert rc == 1
        return "scope enforced"

    @case("open blocker prevents approval and closure")
    def _():
        root = workspace()
        args = argparse.Namespace(project="eos", task="bigger change", risk="STANDARD",
                                  scope=["engine/tools/x.py"], suite="unit", actor="architect",
                                  approve=False, workspace=root)
        assert cmd_open(args) == 0
        decision_id = latest_decisions(root, "eos")[0]["id"]
        r = record(root, decision_id)
        for name, value in (
            ("current_behavior", "the tool refuses everything"),
            ("do_not_regress", ["existing PASS cases"]),
            ("evidence", ["engine/tools/x.py:1"]),
            ("assumptions", ["the gate is reachable"]),
            ("impact", {"files": ["engine/tools/x.py"], "unknowns": []}),
            ("alternatives", ["do nothing"]),
            ("verification", {"suite": "unit"}),
            ("rollback", "git revert"),
        ):
            set_field(r, name, value)
        r["challenges"] = {"open_blockers": 1, "ledger": "challenge.json"}
        save(root, r)
        rc = approve_record(root, r, "architect", "architect", auto=False)
        assert rc == 1
        return "blocker refused"

    @case("stale decision cannot authorise a write")
    def _():
        root = workspace()
        decision_id = open_atomic(root)
        path = decision_path(root, "eos", decision_id)
        data = read_json(path)
        data["updated_at"] = "2020-01-01T00:00:00Z"
        data["record_sha256"] = record_hash(data)
        write_json_atomic(path, data)
        result = check_edit(root, "eos", os.path.join(root, "engine/tools/x.py"))
        assert not result["ok"], result
        return "stale refused"

    @case("challenge ledger blocks approval until resolved")
    def _():
        root = workspace()
        args = argparse.Namespace(project="eos", task="bigger change", risk="STANDARD",
                                  scope=["engine/tools/x.py"], suite="unit", actor="architect",
                                  approve=False, workspace=root)
        assert cmd_open(args) == 0
        decision_id = latest_decisions(root, "eos")[0]["id"]
        r = record(root, decision_id)
        for name, value in (
            ("current_behavior", "the tool refuses everything"),
            ("do_not_regress", ["existing PASS cases"]),
            ("evidence", ["engine/tools/x.py:1"]),
            ("assumptions", ["the gate is reachable"]),
            ("impact", {"files": ["engine/tools/x.py"], "unknowns": []}),
            ("alternatives", ["do nothing"]),
            ("verification", {"suite": "unit"}),
            ("rollback", "git revert"),
        ):
            set_field(r, name, value)
        save(root, r)

        import challenge as ch
        assert ch.cmd_open(argparse.Namespace(project="eos", decision=decision_id,
                                              task="t", workspace=root)) == 0
        ledger_id = sorted(os.listdir(os.path.join(root, ".agents", "evidence",
                                                   "challenges", "eos")))[-1][:-5]
        assert ch.cmd_add(argparse.Namespace(project="eos", id=ledger_id, role="logic-reviewer",
                                             severity="blocker", claim="units are wrong",
                                             evidence=["engine/tools/x.py:1"], confidence="HIGH",
                                             test="assert 1000x", component=["x"],
                                             workspace=root)) == 0
        ledger_rel = ep.store_path(ch.path_for(root, "eos", ledger_id), root)
        assert cmd_attach(argparse.Namespace(project="eos", id=decision_id,
                                             ledger=ledger_rel, workspace=root)) == 0
        r = record(root, decision_id)
        assert approve_record(root, r, "architect", "architect", auto=False) == 1
        assert ch.cmd_resolve(argparse.Namespace(project="eos", id=ledger_id, finding="f-001",
                                                 status="rejected", reason="golden test proves it",
                                                 by="verifier", evidence=["test.py"],
                                                 workspace=root)) == 0
        assert cmd_attach(argparse.Namespace(project="eos", id=decision_id,
                                             ledger=ledger_rel, workspace=root)) == 0
        r = record(root, decision_id)
        assert approve_record(root, r, "architect", "architect", auto=False) == 0
        return "blocked then released"

    @case("another project's decision cannot authorise a path")
    def _():
        root = workspace()
        project_dir = os.path.join(root, "proj")
        os.makedirs(os.path.join(project_dir, ".eos"))
        with open(os.path.join(project_dir, ".eos", "project.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"schema": 1, "id": "other"}, handle)
        with open(os.path.join(project_dir, "x.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("x = 1\n")
        args = argparse.Namespace(project="demo", task="demo change", risk="ATOMIC",
                                  scope=["proj/x.py"], suite="unit", actor="eos.test",
                                  approve=True, workspace=root)
        assert cmd_open(args) == 0
        denied = check_edit(root, None, os.path.join(project_dir, "x.py"))
        assert not denied["ok"], denied
        args = argparse.Namespace(project="other", task="other change", risk="ATOMIC",
                                  scope=["proj/x.py"], suite="unit", actor="eos.test",
                                  approve=True, workspace=root)
        assert cmd_open(args) == 0
        allowed = check_edit(root, None, os.path.join(project_dir, "x.py"))
        assert allowed["ok"], allowed
        return "project isolation"

    @case("workspace root marker does not swallow a registered child project")
    def _():
        root = workspace()
        os.makedirs(os.path.join(root, ".eos"))
        with open(os.path.join(root, ".eos", "project.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"schema": 1, "id": "workspace-root"}, handle)
        os.makedirs(os.path.join(root, "proj"))
        open(os.path.join(root, "proj", "x.py"), "w").write("x\n")
        registry = os.path.join(root, ".agents", "registry")
        os.makedirs(registry)
        with open(os.path.join(registry, "PROJECT_PROFILES.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"profiles": [{"id": "child", "path": "proj"}]}, handle)
        assert infer_project(root, os.path.join(root, "proj", "x.py")) == "child"
        assert infer_project(root, os.path.join(root, "other.py")) == "workspace-root"
        return "child wins"

    @case("unregistered path is denied")
    def _():
        root = workspace()
        result = check_edit(root, "eos", os.path.join(root, "engine/tools/x.py"))
        assert not result["ok"], result
        return "denied"

    passed = failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            passed += 1
            print(f"PASS  {name}  [{detail}]")
        except Exception as exc:  # noqa: BLE001 - selftest must report every failure
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(cases)} cases passed")
    return 0 if failed == 0 else 1


# ------------------------------------------------------------------------ CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="decision.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")
    common.add_argument("--project", default="eos")

    p = sub.add_parser("open", parents=[common])
    p.add_argument("--task", required=True)
    p.add_argument("--risk", default="ATOMIC")
    p.add_argument("--scope", action="append", default=[])
    p.add_argument("--suite", default="")
    p.add_argument("--actor", default="")
    p.add_argument("--approve", action="store_true")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("set", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--evidence", action="append", default=[])
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("transition", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--actor", default="")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_transition)

    p = sub.add_parser("approve", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--actor", default="")
    p.add_argument("--role", default="architect")
    p.add_argument("--auto", action="store_true")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("implement", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--file", action="append", default=[])
    p.add_argument("--actor", default="implementer")
    p.set_defaults(func=cmd_implement)

    p = sub.add_parser("verify", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--gate", required=True)
    p.add_argument("--max-age-min", type=int, default=1440)
    p.add_argument("--actor", default="verifier")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("attach", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--ledger", required=True)
    p.set_defaults(func=cmd_attach)

    p = sub.add_parser("close", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--actor", default="verifier")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("check", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--for", dest="for_", choices=("edit", "promotion"), default="edit")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("require-edit", parents=[common])
    p.add_argument("--path", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--max-age-hours", type=float, default=24.0)
    p.set_defaults(func=cmd_require_edit)

    p = sub.add_parser("status", parents=[common])
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("list", parents=[common])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except DecisionError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
