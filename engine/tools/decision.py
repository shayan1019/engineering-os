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


def review_contract_marker(risk: str) -> int:
    """Declare the requirement at open time, independent of module presence.

    A STANDARD/CRITICAL decision written on a deployment without
    review_contract.py must fail closed at close time, not silently become
    legacy. The marker is therefore a policy declaration, not a probe.
    """
    return 1 if risk in ("STANDARD", "CRITICAL") else 0


def _record_contract_open(workspace: str, decision_id: str, risk: str) -> None:
    """Append the open event that anchors the contract to this decision id.

    created_at is mutable through save(); the registry is the anchor that a
    backdated record cannot remove by rewriting itself.
    """
    if risk not in ("STANDARD", "CRITICAL"):
        return
    path = os.path.join(workspace, ".agents", "evidence", "decisions",
                        "_review_contract.jsonl")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"id": decision_id, "risk": risk, "at": utc_now()},
                                    ensure_ascii=True) + "\n")
    except OSError as exc:
        print(f"WARN: could not record the review-contract open event: {exc}")


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


DISCOVERY_FILES = ("pyproject.toml", "setup.py", "package.json", "Cargo.toml",
                   "go.mod", "pom.xml", "build.gradle", "composer.json",
                   "Gemfile", "mix.exs", "requirements.txt", "Makefile")
DISCOVERY_EXT = (".csproj", ".sln")


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
    A workspace with no identity and a real project marker is discovered as
    `auto-<folder>` so a brand-new folder works without a command.
    """
    absolute = os.path.abspath(path)
    current = absolute if os.path.isdir(absolute) else os.path.dirname(absolute)
    workspace_abs = os.path.abspath(workspace)
    control = os.path.join(workspace_abs, ".agents")
    if absolute == control or absolute.startswith(control + os.sep):
        return "eos"
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
    if not workspace_id:
        try:
            names = set(os.listdir(workspace_abs))
        except OSError:
            names = set()
        marker = any(name in names for name in DISCOVERY_FILES) or any(
            name.lower().endswith(DISCOVERY_EXT) for name in names)
        if marker:
            slug = re.sub(r"[^A-Za-z0-9._-]+", "-",
                          os.path.basename(workspace_abs)).strip("-._")
            if slug:
                return ("auto-" + slug)[:64]
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


def scope_input_problem(workspace: str, raw: str) -> str:
    """Diagnose an accidentally comma-joined set of existing scope paths.

    Never split or grant permission automatically: a comma can be part of a
    legitimate filename. Diagnose only if the exact literal path is absent and
    every independently specified component exists inside this workspace.
    The caller must resubmit an explicit scope list before any EDR is opened.
    """
    if not isinstance(raw, str):
        return ""
    value = normalise_rel(raw)
    if "," not in value or any(char in value for char in "*?["):
        return ""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) < 2 or any(not part for part in parts):
        return ""
    root = os.path.realpath(os.path.abspath(workspace))
    literal = os.path.join(root, value.replace("/", os.sep))
    if os.path.lexists(literal):
        return ""
    for part in parts:
        try:
            scope_pattern(part)
        except DecisionError:
            return ""
        candidate = os.path.realpath(os.path.join(root, part.replace("/", os.sep)))
        if not (candidate == root or candidate.startswith(root + os.sep)):
            return ""
        if not os.path.exists(candidate):
            return ""
    suggestion = " ".join(f'--scope "{part}"' for part in parts)
    return (f"ambiguous comma-joined scope {value!r}; no authorization created. "
            f"If these are separate scopes, pass {suggestion}. "
            "A literal comma filename must already exist to disambiguate.")


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
    if not any(char in pattern for char in "*?["):
        # A scope that names a directory covers its descendants even without a
        # trailing slash. This is the difference between an approved EDR and a
        # silently minted second one.
        return path == pattern or path.startswith(pattern + "/")
    return bool(_pattern_regex(pattern).match(path))


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _segments(pattern: str) -> list:
    return [part for part in normalise_rel(pattern).split("/") if part]


def _segment_prefix(segment: str) -> str:
    for index, char in enumerate(segment):
        if char in "*?[":
            return segment[:index]
    return segment


def _segment_overlap(first: str, second: str) -> bool:
    first_glob = _has_glob(first)
    second_glob = _has_glob(second)
    if not first_glob and not second_glob:
        return first == second
    if not first_glob:
        return bool(_pattern_regex(second).match(first))
    if not second_glob:
        return bool(_pattern_regex(first).match(second))
    first_prefix, second_prefix = _segment_prefix(first), _segment_prefix(second)
    if not first_prefix or not second_prefix:
        return True
    return first_prefix.startswith(second_prefix) or second_prefix.startswith(first_prefix)


def _head_segments(segments: list) -> list:
    head = []
    for segment in segments:
        if "**" in segment:
            break
        head.append(segment)
    return head


def _segments_overlap(first: str, second: str) -> bool:
    first_segments, second_segments = _segments(first), _segments(second)
    if any("**" in segment for segment in first_segments + second_segments):
        for left, right in zip(_head_segments(first_segments), _head_segments(second_segments)):
            if not _segment_overlap(left, right):
                return False
        return True
    if len(first_segments) != len(second_segments):
        shorter, longer = ((first_segments, second_segments)
                           if len(first_segments) < len(second_segments)
                           else (second_segments, first_segments))
        if any(_has_glob(segment) for segment in shorter):
            return False
        return all(_segment_overlap(left, right)
                   for left, right in zip(shorter, longer))
    return all(_segment_overlap(left, right)
               for left, right in zip(first_segments, second_segments))


def scopes_overlap(first: str, second: str) -> bool:
    """Conservative overlap test for two declared scopes.

    A concrete path that matches the other glob overlaps. Two globs overlap
    when their segment languages can meet; when that cannot be decided, the
    answer is True. A false negative would authorise two writers over the same
    file, which is the failure this function exists to prevent.
    """
    first = normalise_rel(first).rstrip("/")
    second = normalise_rel(second).rstrip("/")
    if not first or not second:
        return False
    if first == second:
        return True
    first_glob = _has_glob(first)
    second_glob = _has_glob(second)
    if not first_glob and path_in_scope(first + "/", second):
        return True
    if not second_glob and path_in_scope(second + "/", first):
        return True
    return _segments_overlap(first, second)


def active_conflicts(workspace: str, project: str, scope: list, ignore_id: str = "") -> list:
    conflicts = []
    for other in latest_decisions(workspace, project):
        if other.get("id") == ignore_id:
            continue
        if other.get("state") not in ("APPROVED_FOR_EDIT", "IMPLEMENTED"):
            continue
        if integrity(other):
            continue
        for new_scope in scope or []:
            for old_scope in scope_list(other):
                if scopes_overlap(new_scope, old_scope):
                    conflicts.append({"id": other.get("id"), "state": other.get("state"),
                                      "scope": old_scope, "path": new_scope,
                                      "session_id": other.get("session_id") or ""})
                    break
            else:
                continue
            break
    return conflicts


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
    challenges = record.get("challenges")
    if challenges is not None and not isinstance(challenges, dict):
        problems.append("challenges is not an object")
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
    if args.risk in ("STANDARD", "CRITICAL") and not (getattr(args, "session", "") or "").strip():
        print("BLOCKED: STANDARD/CRITICAL decisions require --session; "
              "reviewer independence cannot be checked without it")
        return 2
    if not (args.task or "").strip():
        print("BLOCKED: a decision needs the task it is about")
        return 2
    scope = []
    for raw in args.scope or []:
        problem = scope_input_problem(workspace, raw)
        if problem:
            print(f"BLOCKED: {problem}")
            return 2
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
        "session_id": getattr(args, "session", "") or "",
        "fields": {},
        "approval": None,
        "gate": None,
        "change": {"files": [], "diff_sha256": ""},
        "challenges": {"open_blockers": 0, "ledger": ""},
        "review_contract": review_contract_marker(args.risk),
        "human_acceptance": [],
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
    _record_contract_open(workspace, record["id"], args.risk)
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
    if name == "impact":
        if not isinstance(text, dict):
            raise DecisionError("impact must be an object, not a free-form value")
        if not isinstance(text.get("files") or [], list):
            raise DecisionError("impact.files must be a list")
        if text.get("unknowns") is not None and not isinstance(text.get("unknowns"), list):
            raise DecisionError("impact.unknowns must be a list")
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
    if record["state"] == "VERIFIED" and args.field in ("impact", "scope"):
        print("BLOCKED: a verified decision cannot narrow its reviewed surfaces; open a new decision")
        return 1
    reviewed_at = (record.get("challenges") or {}).get("reviewed_at")
    if reviewed_at and args.field in ("impact", "scope"):
        print("BLOCKED: a reviewed decision cannot narrow its surfaces; open a new decision")
        return 1
    value = args.value
    if isinstance(value, str) and value[:1] in "[{":
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            print(f"BLOCKED: --value looks like JSON but does not parse: {exc}")
            return 2
    if args.field == "scope":
        for raw in value if isinstance(value, list) else [value]:
            problem = scope_input_problem(workspace, raw)
            if problem:
                print(f"BLOCKED: {problem}")
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
        try:
            import review_contract as review  # noqa: PLC0415
        except ImportError:
            if record.get("risk") in ("STANDARD", "CRITICAL"):
                print("BLOCKED: review_contract.py is missing; "
                      "a STANDARD/CRITICAL decision cannot close without the contract")
                return 1
        else:
            if review.applies(record, workspace):
                verdict = review.validate(workspace, record)
                if not verdict["ok"]:
                    print("BLOCKED: engineering review evidence is incomplete")
                    for problem in verdict["problems"]:
                        print(f"  - {problem}")
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


def approve_record(workspace: str, record: dict, actor: str, role: str, auto: bool,
                   confirm: str = "") -> int:
    tier = record["risk"]
    challenges = record.get("challenges")
    if challenges is not None and not isinstance(challenges, dict):
        print("BLOCKED: challenges is not an object")
        return 1
    gaps = missing(record, REQUIRED_BEFORE_EDIT[tier])
    if gaps:
        print("BLOCKED: the pre-edit contract is incomplete")
        print("  missing: " + ", ".join(gaps))
        return 1
    if int((record.get("challenges") or {}).get("open_blockers") or 0) > 0:
        print("BLOCKED: open blocker(s) in the challenge ledger")
        return 1
    if auto and tier != "ATOMIC":
        print("BLOCKED: only ATOMIC can be auto-approved")
        return 1
    if not auto and role not in ("architect", "chief-architect", "reviewer", "human"):
        print("BLOCKED: only architect, reviewer or a human may approve a non-atomic change")
        return 1
    if tier == "CRITICAL":
        if auto:
            print("BLOCKED: CRITICAL never auto-approves, even with --auto")
            return 1
        if role != "human":
            print("BLOCKED: CRITICAL approval requires a human, not a declared model role")
            return 1
        if confirm != record["id"]:
            print("BLOCKED: human approval requires --confirm <decision-id>")
            return 1
    lock_path = os.path.join(project_dir(workspace, record["project"]), ".approve.lock")
    try:
        handle = ep.acquire_file_lock(lock_path, float(os.environ.get("EOS_LOCK_TIMEOUT", "15")))
    except (TimeoutError, OSError) as exc:
        print(f"BLOCKED: approval lock unavailable: {exc}")
        return 1
    try:
        conflicts = active_conflicts(workspace, record["project"], scope_list(record), record["id"])
        if conflicts:
            print("BLOCKED: another active write authorization covers this surface")
            for conflict in conflicts[:5]:
                print(f"  - {conflict['id']} ({conflict['state']}) scope {conflict['scope']} "
                      f"overlaps {conflict['path']} (session {conflict['session_id'] or '-'})")
            print("  next: close or supersede the other decision before approving this one")
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
    finally:
        ep.unlock_handle(handle)


def cmd_approve(args) -> int:
    workspace = resolve_workspace(args.workspace)
    record = load(workspace, args.project, args.id)
    problems = integrity(record)
    if problems:
        print("BLOCKED: decision record is not trustworthy")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    return approve_record(workspace, record, args.actor, args.role, args.auto,
                          getattr(args, "confirm", "") or "")


def cmd_accept_unknown(args) -> int:
    """Seal a human acceptance for one unresolved unknown claim."""
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
    if args.confirm != record["id"]:
        print("BLOCKED: human acceptance requires --confirm <decision-id>")
        return 2
    claim = (args.claim or "").strip()
    if not claim:
        print("BLOCKED: a human acceptance needs the exact unknown claim")
        return 2
    try:
        import review_contract as review  # noqa: PLC0415
    except ImportError:
        print("BLOCKED: review_contract.py is not installed")
        return 2
    entry = {"claim": claim[:1000], "claim_sha256": review.claim_hash(claim),
             "actor": args.actor or "human", "at": utc_now(),
             "reason": (args.reason or "").strip()[:1000]}
    record.setdefault("human_acceptance", []).append(entry)
    save(workspace, record)
    print(f"ACCEPTED: {record['id']} unknown {entry['claim_sha256'][:12]}")
    return 0


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
    hashes = dict((record.get("change") or {}).get("hashes") or {})
    for item in known:
        full_path = ep.load_stored_path(item, workspace)
        try:
            hashes[item] = sha256_file(full_path)
        except OSError:
            continue
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
    record["change"] = {"files": known, "hashes": hashes,
                        "diff_sha256": sha256_bytes(canonical(hashes)),
                        "recorded_at": recorded_at}
    save(workspace, record)
    if record["state"] == "APPROVED_FOR_EDIT":
        return transition(workspace, record, "IMPLEMENTED", args.actor or "implementer",
                          "implementation recorded")
    print(f"IMPLEMENTED: {record['id']} ({len(known)} file(s))")
    return 0


def cmd_record_change(args) -> int:
    """Attach the real changed files and hashes to the decision that allowed them.

    Authorization-only EDRs were a known audit gap: the write receipt lived in
    the work state and the guard log, not in the EDR. This command records the
    exact file content hashes without changing the decision state or gate.
    """
    workspace = resolve_workspace(args.workspace)
    if not args.project:
        first = (args.file or [""])[0]
        absolute = os.path.abspath(first if os.path.isabs(first)
                                   else os.path.join(workspace, first))
        args.project = infer_project(workspace, absolute) or ""
        if not args.project:
            print("BLOCKED: project could not be inferred for the change evidence")
            return 1
    record = load(workspace, args.project, args.id)
    problems = integrity(record)
    if problems:
        print("BLOCKED: decision record is not trustworthy")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if record["state"] not in ("APPROVED_FOR_EDIT", "IMPLEMENTED"):
        print(f"BLOCKED: {record['state']} is not a writable state")
        return 1
    owner = str(record.get("session_id") or "")
    if owner and args.session and owner != args.session:
        print(f"BLOCKED: {record['id']} is owned by session {owner}")
        return 1
    files = []
    hashes = {}
    problems = []
    for raw in args.file or []:
        absolute = os.path.abspath(raw if os.path.isabs(raw)
                                   else os.path.join(workspace, raw))
        if not (absolute == workspace or absolute.startswith(workspace + os.sep)):
            problems.append(f"outside the workspace: {raw}")
            continue
        rel = ep.store_path(absolute, workspace)
        if not covers(scope_list(record), rel):
            problems.append(f"outside the approved scope: {rel}")
            continue
        if not os.path.isfile(absolute):
            problems.append(f"changed file does not exist: {rel}")
            continue
        files.append(rel)
        hashes[rel] = sha256_file(absolute)
    if problems:
        print("BLOCKED: change evidence rejected")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if not files:
        print("BLOCKED: no changed file was recorded")
        return 2
    record["change"] = {
        "files": files,
        "hashes": hashes,
        "diff_sha256": sha256_bytes(canonical(hashes)),
        "recorded_at": utc_now(),
        "session": args.session or owner,
    }
    save(workspace, record)
    result = {"ok": True, "decision": record["id"], "files": files,
              "hashes": hashes, "diff_sha256": record["change"]["diff_sha256"],
              "state": record["state"]}
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"CHANGE: {record['id']} ({len(files)} file(s)) "
              f"diff={record['change']['diff_sha256'][:12]}")
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
    canonical = ch.path_for(workspace, record["project"], ledger["id"])
    if os.path.realpath(ledger_path) != os.path.realpath(canonical):
        print("BLOCKED: attach the canonical ledger path, not a copy")
        return 1
    blockers = ch.count_open_blockers(ledger)
    record["challenges"] = {
        "open_blockers": blockers,
        "ledger": ep.store_path(canonical, workspace),
        "sha256": sha256_file(canonical),
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
               max_age_hours: float = 24.0, session: str = "") -> dict:
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
        owner = str(record.get("session_id") or "")
        if owner and session and owner != session:
            reasons.append(f"{record.get('id')}: owned by session {owner}")
            continue
        updated = parse_time(record.get("updated_at", ""))
        if max_age_hours and updated:
            age_h = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
            if age_h > max_age_hours:
                reasons.append(f"{record['id']}: decision is {age_h:.1f}h old (limit {max_age_hours}h)")
                continue
        return {"ok": True, "decision": record["id"], "project": record["project"],
                "state": record["state"], "path": rel, "reasons": [],
                "risk": record.get("risk"), "session_id": owner}
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
        result = check_edit(workspace, args.project, absolute, args.max_age_hours,
                            getattr(args, "session", ""))
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
        try:
            import review_contract as review  # noqa: PLC0415
        except ImportError:
            if record.get("risk") in ("STANDARD", "CRITICAL") and int(record.get("review_contract") or 0):
                result["ok"] = False
                result["problems"] = result["problems"] + ["review_contract.py is required but missing"]
        else:
            if review.applies(record, workspace):
                verdict = review.validate(workspace, record)
                if not verdict["ok"]:
                    result["ok"] = False
                    result["problems"] = result["problems"] + [
                        f"review evidence: {item}" for item in verdict["problems"]]
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

    engine_dir = os.path.dirname(os.path.abspath(__file__))

    def race_scopes(scopes) -> tuple:
        root = workspace()
        start = os.path.join(root, "start.txt")
        ready_files = [os.path.join(root, f"ready-{index}.txt")
                       for index in range(len(scopes))]
        helper = (
            "import argparse, os, sys, time\n"
            f"sys.path.insert(0, {engine_dir!r})\n"
            "import decision as edr\n"
            "root, scope, ready, start = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]\n"
            "task = 'race-' + scope\n"
            "args = argparse.Namespace(project='eos', task=task, risk='ATOMIC',\n"
            "    scope=[scope], suite='unit', actor='race', approve=False,\n"
            "    workspace=root, session='')\n"
            "assert edr.cmd_open(args) == 0\n"
            "record = next(item for item in edr.latest_decisions(root, 'eos')\n"
            "              if item.get('task') == task)\n"
            "with open(ready, 'w', encoding='utf-8') as handle:\n"
            "    handle.write('ready')\n"
            "deadline = time.time() + 15\n"
            "while not os.path.exists(start):\n"
            "    if time.time() > deadline:\n"
            "        sys.exit(4)\n"
            "    time.sleep(0.01)\n"
            "sys.exit(0 if edr.approve_record(root, record, 'race', 'eos.atomic', True) == 0 else 3)\n"
        )
        procs = []
        for index, scope in enumerate(scopes):
            procs.append(subprocess.Popen(
                [sys.executable, "-c", helper, root, scope, ready_files[index], start],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        deadline = time.time() + 20
        while time.time() < deadline and not all(os.path.exists(path) for path in ready_files):
            time.sleep(0.01)
        if not all(os.path.exists(path) for path in ready_files):
            for proc in procs:
                proc.kill()
            raise AssertionError("processes did not reach the approval barrier")
        with open(start, "w", encoding="utf-8") as handle:
            handle.write("go")
        codes = [proc.wait(timeout=30) for proc in procs]
        active = [item for item in latest_decisions(root, "eos")
                  if item.get("state") == "APPROVED_FOR_EDIT"]
        return codes, active

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

    @case("a successful write records the changed file hash on the decision")
    def _():
        root = workspace()
        target = os.path.join(root, "engine", "tools", "x.py")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("x = 1\n")
        open_args = argparse.Namespace(
            project="eos", task="hash evidence", risk="ATOMIC",
            scope=["engine/tools/x.py"], suite="unit", actor="eos.test",
            approve=True, workspace=root, session="owner",
        )
        assert cmd_open(open_args) == 0
        decision_id = latest_decisions(root, "eos")[0]["id"]
        args = argparse.Namespace(project="eos", id=decision_id,
                                  file=["engine/tools/x.py"], session="owner",
                                  workspace=root, json=True)
        assert cmd_record_change(args) == 0
        record_value = load(root, "eos", decision_id)
        assert record_value["change"]["files"] == ["engine/tools/x.py"], record_value
        assert len(record_value["change"]["hashes"]["engine/tools/x.py"]) == 64
        assert len(record_value["change"]["diff_sha256"]) == 64
        args.session = "other"
        assert cmd_record_change(args) == 1
        args.session = "owner"
        args.file = ["engine/tools/y.py"]
        with open(os.path.join(root, "engine", "tools", "y.py"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write("y = 1\n")
        assert cmd_record_change(args) == 1
        return "hash attached"

    @case("accidentally comma-joined scope is rejected before an EDR opens")
    def _():
        root = workspace()
        os.makedirs(os.path.join(root, "6.0_final_merge"))
        os.makedirs(os.path.join(root, "_canonical"))
        raw = "6.0_final_merge,_canonical"
        problem = scope_input_problem(root, raw)
        assert "comma-joined" in problem and '--scope "_canonical"' in problem, problem
        before = len(latest_decisions(root, "eos"))
        args = argparse.Namespace(project="eos", task="repair scope",
                                  risk="ATOMIC", scope=[raw], suite="unit",
                                  actor="eos.test", approve=True,
                                  workspace=root, session="")
        assert cmd_open(args) == 2
        assert len(latest_decisions(root, "eos")) == before
        return "no ambiguous EDR"

    @case("real comma filename does not trigger joined-scope diagnosis")
    def _():
        root = workspace()
        for name in ("part", "other", "part,other"):
            os.makedirs(os.path.join(root, name))
        assert not scope_input_problem(root, "part,other")
        assert not scope_input_problem(root, "new,not-yet-existing")
        return "literal commas preserved"

    @case("directory scope covers descendants without a trailing slash")
    def _():
        assert path_in_scope("engine/tools", "engine/tools/x.py")
        assert path_in_scope("engine/tools", "engine/tools")
        assert not path_in_scope("engine/tools/x.py", "engine/tools/y.py")
        assert scopes_overlap("engine/tools", "engine/tools/x.py")
        assert not scopes_overlap("engine/tools/x.py", "engine/tools/y.py")
        return "directory prefix"

    @case("wildcard scopes overlap conservatively")
    def _():
        assert scopes_overlap("*.py", "foo.py")
        assert scopes_overlap("src/f*.py", "src/foo.py")
        assert scopes_overlap("src/f*.py", "src/foo*.py")
        assert scopes_overlap("*.py", "*.js"), "unprovable overlap must fail closed"
        assert not scopes_overlap("src/f*.py", "src/b*.py")
        return "wildcard fail closed"

    @case("overlapping wildcard prefixes conflict")
    def _():
        assert scopes_overlap("src/f*.py", "src/foo*.py")
        assert not scopes_overlap("src/f*.py", "src/b*.py")
        return "prefix intersection"

    @case("a literal directory conflicts with a deeper wildcard")
    def _():
        assert scopes_overlap("src/foo", "src/f*/bar.py")
        assert scopes_overlap("src/f*/bar.py", "src/foo")
        assert scopes_overlap("src/foo", "src/foo*/bar.py")
        assert scopes_overlap("src/foo*/bar.py", "src/foo")
        assert not scopes_overlap("src/foo", "src/bar/baz.py")
        return "directory prefix"

    @case("a second active authorization over the same surface is refused")
    def _():
        root = workspace()
        first = open_atomic(root, scope="engine/tools", approve=True)
        args = argparse.Namespace(
            project="eos", task="second writer", risk="ATOMIC",
            scope=["engine/tools/x.py"], suite="unit", actor="eos.test",
            approve=False, workspace=root, session="",
        )
        assert cmd_open(args) == 0
        second = latest_decisions(root, "eos")[0]["id"]
        record_value = load(root, "eos", second)
        assert approve_record(root, record_value, "eos.test", "eos.atomic", True) == 1
        assert check_edit(root, "eos", os.path.join(root, "engine/tools/x.py"))["decision"] == first
        return "overlap blocked"

    @case("two processes cannot approve overlapping decisions")
    def _():
        codes, active = race_scopes(("src/f*.py", "src/foo*.py"))
        assert sorted(codes) == [0, 3], codes
        assert len(active) == 1, active
        return "one process won"

    @case("two processes cannot approve a directory/wildcard conflict")
    def _():
        codes, active = race_scopes(("src/foo", "src/f*/bar.py"))
        assert sorted(codes) == [0, 3], codes
        assert len(active) == 1, active
        return "one process won"

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
                                  approve=False, session="ses_decision", workspace=root)
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
                                  approve=False, session="ses_decision", workspace=root)
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

    @case("an unregistered folder with a project marker is discovered")
    def _():
        root = workspace()
        with open(os.path.join(root, "package.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write("{}\n")
        project = infer_project(root, os.path.join(root, "src", "app.js"))
        assert project.startswith("auto-"), project
        return project

    @case("an empty unregistered folder is not invented as a project")
    def _():
        root = workspace()
        assert infer_project(root, os.path.join(root, "x.py")) == ""
        return "empty"

    @case("unregistered path is denied")
    def _():
        root = workspace()
        result = check_edit(root, "eos", os.path.join(root, "engine/tools/x.py"))
        assert not result["ok"], result
        return "denied"

    def standard_ready(root, marker=True):
        args = argparse.Namespace(project="eos", task="bigger change", risk="STANDARD",
                                  scope=["engine/tools/x.py"], suite="unit", actor="architect",
                                  approve=False, session="ses_decision", workspace=root)
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
        if not marker:
            r.pop("review_contract", None)
            r["created_at"] = "2020-01-01T00:00:00Z"
            registry = os.path.join(root, ".agents", "evidence", "decisions",
                                    "_review_contract.jsonl")
            if os.path.isfile(registry):
                kept = []
                with open(registry, encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event.get("id") != decision_id:
                            kept.append(line)
                with open(registry, "w", encoding="utf-8", newline="\n") as handle:
                    handle.writelines(kept)
        save(root, r)
        return decision_id

    def close_ready(root, decision_id):
        r = record(root, decision_id)
        assert approve_record(root, r, "architect", "architect", auto=False) == 0
        r = record(root, decision_id)
        r["change"] = {"files": ["engine/tools/x.py"], "diff_sha256": ""}
        save(root, r)
        assert transition(root, r, "IMPLEMENTED", "implementer") == 0
        gate = _fixture_gate(root)
        assert cmd_verify(argparse.Namespace(project="eos", id=decision_id, gate=gate,
                                             max_age_min=None, actor="verifier",
                                             workspace=root)) == 0
        return record(root, decision_id)

    @case("a new STANDARD decision cannot close without review evidence")
    def _():
        root = workspace()
        decision_id = standard_ready(root, marker=True)
        r = close_ready(root, decision_id)
        assert transition(root, r, "CLOSED", "verifier", "done") == 1
        return "review missing"

    @case("rewriting risk to ATOMIC does not skip the registry contract")
    def _():
        root = workspace()
        decision_id = standard_ready(root, marker=True)
        r = record(root, decision_id)
        r["risk"] = "ATOMIC"
        save(root, r)
        r = close_ready(root, decision_id)
        assert transition(root, r, "CLOSED", "verifier", "done") == 1
        return "risk rewrite blocked"

    @case("a backdated record is still covered by the open registry")
    def _():
        root = workspace()
        decision_id = standard_ready(root, marker=True)
        r = record(root, decision_id)
        r.pop("review_contract", None)
        r["created_at"] = "2020-01-01T00:00:00Z"
        save(root, r)
        r = close_ready(root, decision_id)
        assert transition(root, r, "CLOSED", "verifier", "done") == 1
        return "registry anchor"

    @case("a legacy STANDARD decision without the marker still closes")
    def _():
        root = workspace()
        decision_id = standard_ready(root, marker=False)
        r = close_ready(root, decision_id)
        assert transition(root, r, "CLOSED", "verifier", "done") == 0
        return "legacy closed"

    @case("a STANDARD decision with a sealed review closes")
    def _():
        root = workspace()
        decision_id = standard_ready(root, marker=True)
        import challenge as ch
        import review_contract as rc
        assert ch.cmd_open(argparse.Namespace(project="eos", decision=decision_id,
                                              task="t", workspace=root)) == 0
        ledger_id = sorted(os.listdir(os.path.join(root, ".agents", "evidence",
                                                   "challenges", "eos")))[-1][:-5]
        ledger_rel = ep.store_path(ch.path_for(root, "eos", ledger_id), root)
        assert cmd_attach(argparse.Namespace(project="eos", id=decision_id,
                                             ledger=ledger_rel, workspace=root)) == 0
        current = record(root, decision_id)
        required, unknowns = rc.surfaces(current)
        os.makedirs(os.path.join(root, ".agents", "evidence", "tools"), exist_ok=True)
        with open(os.path.join(root, ".agents", "evidence", "tools", "ses_review.jsonl"),
                  "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"ts": utc_now(), "kind": "permission", "action": "read",
                                     "resource": "engine/tools/x.py", "verdict": "ALLOW"}) + "\n")
        review = {
            "version": 1,
            "reviewer": {"role": "reviewer", "agent": "reviewer",
                         "session": "ses_review", "ran_at": utc_now()},
            "coverage": {"inspected": [{"surface": item, "evidence": ["engine/tools/x.py:1"]}
                                       for item in required],
                         "not_inspected": []},
            "falsification": [{"claim": "x is wrong", "test": "assert x",
                               "result": "passed", "evidence": ["cmd:pytest -q"]}],
            "unknowns": [{"claim": claim, "disposition": "closed",
                          "evidence": ["test:unit"]} for claim in unknowns],
        }
        r = close_ready(root, decision_id)
        sealed = rc.record_review(root, "eos", decision_id, review, "reviewer")
        assert sealed["sealed"], sealed
        r = record(root, decision_id)
        assert transition(root, r, "CLOSED", "verifier", "done") == 0
        return "closed with review"

    @case("STANDARD cannot be auto-approved")
    def _():
        root = workspace()
        decision_id = standard_ready(root, marker=True)
        r = record(root, decision_id)
        assert approve_record(root, r, "eos.auto", "eos.atomic", auto=True) == 1
        return "auto refused"

    @case("human acceptance is sealed against the exact claim")
    def _():
        root = workspace()
        decision_id = standard_ready(root, marker=True)
        args = argparse.Namespace(project="eos", id=decision_id, claim="unit not proven",
                                  actor="owner", reason="accepted for release",
                                  confirm=decision_id, workspace=root)
        assert cmd_accept_unknown(args) == 0
        r = record(root, decision_id)
        assert r["human_acceptance"][0]["claim"] == "unit not proven"
        import review_contract as rc
        assert r["human_acceptance"][0]["claim_sha256"] == rc.claim_hash("unit not proven")
        return "sealed acceptance"

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
    p.add_argument("--session", default="")
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
    p.add_argument("--confirm", default="")
    p.add_argument("--auto", action="store_true")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("accept-unknown", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--claim", required=True)
    p.add_argument("--actor", default="human")
    p.add_argument("--reason", required=True)
    p.add_argument("--confirm", required=True)
    p.set_defaults(func=cmd_accept_unknown)

    p = sub.add_parser("implement", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--file", action="append", default=[])
    p.add_argument("--actor", default="implementer")
    p.set_defaults(func=cmd_implement)

    p = sub.add_parser("record-change", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--file", action="append", default=[])
    p.add_argument("--session", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_record_change, project="")

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
    p.add_argument("--session", default="")
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
