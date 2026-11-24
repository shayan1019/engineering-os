#!/usr/bin/env python3
"""Persistent work state and session continuity.

A conversation is not a durable record. This tool keeps, per project:

  state.json     the current task, status, active decision, files, tests,
                 open errors, owner, next safe action, map version, git state
  events.jsonl   an append-only hash-chained log; every event carries the state
                 snapshot it produced, so a corrupt state file can be recovered

Concurrent sessions are explicit: a different session may not update or close a
task owned by another session without `--takeover` and a reason.

A multi-step request additionally has a durable objective:

  objective.json the request, its items, what is done, and the next action

One approved file write is a step of it, never its completion. The objective is
progress, not an ownership lease: it blocks no other session's write, and any
later session may continue it. Only `objective done` (every item done) or
`objective abandon --reason` ends it.

Commands:
  objective open|show|checkpoint|done|abandon --project <id> --session <id>
            [--done N]... [--next <text>] [--add <item>]... [--reason <text>]
  start   --project <id> --task <text> --session <id> [--owner <name>] [--new]
  update  --project <id> --session <id> [--status ...] [--decision ...] ...
  close   --project <id> --session <id>
  resume  --project <id> [--json]
  status  --project <id> [--json]
  recover --project <id> [--apply]
  selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

SCHEMA = 1
KIND = "work_state"
STATUSES = ("planned", "in_progress", "blocked", "verified", "closed")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest().upper()


def root(workspace: str, project: str) -> str:
    if not project or any(part in ("..", "/", "\\") for part in [project]):
        raise SystemExit(f"BLOCKED: invalid project id: {project!r}")
    return os.path.join(workspace, ".agents", "evidence", "work", project)


def state_path(workspace: str, project: str) -> str:
    return os.path.join(root(workspace, project), "state.json")


def events_path(workspace: str, project: str) -> str:
    return os.path.join(root(workspace, project), "events.jsonl")


def lock_path(workspace: str, project: str) -> str:
    return os.path.join(root(workspace, project), ".lock")


def acquire_lock(workspace: str, project: str, session: str, owner: str,
                 timeout: float | None = None):
    """An exclusive writer lease backed by an OS file lock.

    The lock is exclusive across processes even when two calls carry the same
    session id: a session is not a process, and a host can run concurrent tool
    calls inside one session. The OS releases it when the holder exits, so
    there is no TTL to steal and no lock file to delete.
    """
    path = lock_path(workspace, project)
    if timeout is None:
        timeout = float(os.environ.get("EOS_LOCK_TIMEOUT", "15"))
    try:
        handle = ep.acquire_file_lock(path, timeout)
    except TimeoutError:
        raise SystemExit(
            f"BLOCKED: work state is locked by another process (waited {timeout:.0f}s)")
    token = uuid.uuid4().hex
    os.lseek(handle, 0, os.SEEK_SET)
    os.ftruncate(handle, 0)
    os.write(handle, json.dumps({
        "pid": os.getpid(), "session": session, "owner": owner,
        "token": token, "ts": time.time()}).encode("utf-8"))
    os.fsync(handle)
    return handle, path, token


def release_lock(handle, path: str, token: str) -> None:
    """Release only when this handle still owns the metadata it wrote. The lock
    file is kept: deleting it would race a process that already opened it."""
    try:
        os.lseek(handle, 0, os.SEEK_SET)
        data = json.loads(os.read(handle, 8192).decode("utf-8") or "{}")
    except (OSError, ValueError):
        data = {}
    if data.get("token") == token:
        ep.unlock_handle(handle)
    try:
        os.close(handle)
    except OSError:
        pass


def read_json(path: str):
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    os.replace(tmp, path)


def state_hash(value: dict) -> str:
    return digest({k: v for k, v in value.items() if k != "state_sha256"})


def load_state(workspace: str, project: str) -> dict | None:
    path = state_path(workspace, project)
    if not os.path.isfile(path):
        return None
    value = read_json(path)
    if value.get("kind") != KIND:
        raise SystemExit("BLOCKED: work state has the wrong kind")
    if state_hash(value) != value.get("state_sha256"):
        raise SystemExit("BLOCKED: work state hash mismatch")
    return value


def last_event(workspace: str, project: str) -> dict:
    path = events_path(workspace, project)
    if not os.path.isfile(path):
        return {}
    last = {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def append_event(workspace: str, project: str, action: str, state: dict, extra: dict) -> None:
    previous = last_event(workspace, project)
    event = {
        "ts": utc_now(), "action": action, "project": project,
        "session": state.get("session_id"), "state": state,
        "prev_sha256": previous.get("event_sha256", ""),
        **extra,
    }
    event["event_sha256"] = digest(event)
    path = events_path(workspace, project)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")


def save_state(workspace: str, project: str, state: dict, action: str, extra: dict | None = None) -> None:
    state["updated_at"] = utc_now()
    state["state_sha256"] = state_hash(state)
    write_json(state_path(workspace, project), state)
    append_event(workspace, project, action, state, extra or {})


def new_state(project: str, task: str, session: str, owner: str) -> dict:
    return {
        "schema": SCHEMA, "kind": KIND, "project": project, "task": task,
        "status": "planned", "session_id": session, "owner": owner or session,
        "created_at": utc_now(), "updated_at": utc_now(),
        "decision": "", "challenge": "", "files": [], "tests": [],
        "open_errors": [], "next_action": "preflight, then prepare the EDR",
        "map_sha256": "", "git": {}, "state_sha256": "",
    }


def same_session(state: dict, session: str) -> bool:
    return str(state.get("session_id") or "") == str(session or "")


def guard_owner(state: dict, session: str, takeover: bool, reason: str) -> bool:
    if same_session(state, session) or state.get("status") == "closed":
        return True
    if takeover:
        if not (reason or "").strip():
            raise SystemExit("BLOCKED: takeover requires a reason")
        return True
    return False


# ------------------------------------------------------------- durable objective
ITEM_RE = re.compile(r"^\s{0,6}\d{1,2}[.)]\s+(\S.*)$", re.M)
MAX_ITEMS = 40
MAX_ITEM_CHARS = 240


def objective_path(workspace: str, project: str) -> str:
    return os.path.join(root(workspace, project), "objective.json")


def task_items(task: str) -> list:
    """The numbered lines of a request, verbatim and bounded. Bullets are not
    items: in practice they are pasted status, not work."""
    return [item.strip()[:MAX_ITEM_CHARS] for item in ITEM_RE.findall(task or "")][:MAX_ITEMS]


def is_multi_step(task: str) -> bool:
    return len(task_items(task)) >= 2


def load_objective(workspace: str, project: str) -> dict | None:
    path = objective_path(workspace, project)
    if not os.path.isfile(path):
        return None
    try:
        value = read_json(path)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"BLOCKED: objective is unreadable: {exc}")
    if value.get("kind") != "objective" or state_hash(value) != value.get("state_sha256"):
        raise SystemExit("BLOCKED: objective hash mismatch")
    return value


def open_objective(workspace: str, project: str) -> dict | None:
    value = load_objective(workspace, project)
    return value if value and value.get("status") == "open" else None


def pending_items(value: dict) -> list:
    return [(n, item["text"]) for n, item in enumerate(value.get("items") or [], 1)
            if not item.get("done")]


def derived_next(value: dict) -> str:
    """Never empty while work is open: a resume must always have a next step."""
    if value.get("next_action"):
        return value["next_action"]
    pending = pending_items(value)
    if pending:
        return f"item {pending[0][0]}: {pending[0][1]}"
    return "verify the result against the request, then: objective done"


def save_objective(workspace: str, project: str, value: dict, action: str) -> None:
    value["updated_at"] = utc_now()
    value["state_sha256"] = state_hash(value)
    write_json(objective_path(workspace, project), value)
    path = os.path.join(root(workspace, project), "objective.log.jsonl")
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"ts": utc_now(), "action": action, "objective": value},
                                ensure_ascii=True) + "\n")


def note_objective(workspace: str, project: str, task: str, session: str,
                   files: list | None = None, decision: str = "",
                   explicit: bool = False) -> dict | None:
    """Create or advance the objective. Caller holds the work-state lock.

    A multi-step request opens one. A short follow-up ("continue") keeps the open
    one. A different multi-step request becomes current; unfinished items of the
    previous one are kept, because being interrupted is not being done.
    """
    current = open_objective(workspace, project)
    task = (task or "").strip()
    if (explicit or is_multi_step(task)) and (not current or current.get("task") != task):
        closed = load_objective(workspace, project)
        if not current and closed and closed.get("task") == task:
            # A late write of a request that was already finished or abandoned.
            return None
        carried = list((current or {}).get("carried") or [])
        if current:
            carried += [text for _, text in pending_items(current)]
        new_items = task_items(task) if is_multi_step(task) else [task[:MAX_ITEM_CHARS]]
        carried = [text for text in dict.fromkeys(carried) if text not in new_items][-MAX_ITEMS:]
        current = {
            "schema": SCHEMA, "kind": "objective", "project": project, "status": "open",
            "task": task, "session_id": session, "created_at": utc_now(),
            "items": [{"text": text, "done": False} for text in new_items],
            "carried": carried, "files": [], "decisions": [], "next_action": "",
        }
    if not current:
        return None
    current["session_id"] = session or current.get("session_id", "")
    current["files"] = sorted(set(current.get("files") or []) | set(files or []))
    if decision:
        current["decisions"] = (current.get("decisions") or [])[-200:] + [decision]
    save_objective(workspace, project, current, "step" if files else "open")
    return current


def objective_lines(value: dict, limit: int = 12) -> list:
    items = value.get("items") or []
    pending = pending_items(value)
    lines = [f"OPEN OBJECTIVE: {len(items) - len(pending)}/{len(items)} items done; "
             f"next: {derived_next(value)}"]
    lines += [f"  [ ] {n}. {text}" for n, text in pending[:limit]]
    if len(pending) > limit:
        lines.append(f"  ... {len(pending) - limit} more")
    if value.get("carried"):
        lines.append(f"  carried from an interrupted request: {len(value['carried'])} item(s)")
    return lines


def cmd_objective(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    if args.action == "show":
        try:
            value = load_objective(workspace, args.project)
        except SystemExit as exc:
            print(exc)
            return 1
        if args.json:
            print(json.dumps(value or {}, ensure_ascii=True))
        elif not value:
            print("NONE: no objective")
        else:
            print(f"{value['status']}: {value['task'][:200]}")
            print("\n".join(objective_lines(value, 40)))
        return 0
    handle, path, token = acquire_lock(workspace, args.project, args.session, args.session)
    try:
        return _objective(args, workspace)
    finally:
        release_lock(handle, path, token)


def _objective(args, workspace: str) -> int:
    try:
        value = open_objective(workspace, args.project)
        if args.action == "open":
            if not (args.task or "").strip():
                print("BLOCKED: --task is required")
                return 2
            value = note_objective(workspace, args.project, args.task, args.session, explicit=True)
            print("OPEN" if value else "SKIPPED: this request was already finished or abandoned")
            return 0
        if args.action == "step":
            # The guard's record of a completed write outside the ATOMIC path.
            value = note_objective(workspace, args.project, args.task, args.session,
                                   [os.path.normpath(item) for item in args.file], args.decision)
            print(json.dumps({"ok": True, "status": "open" if value else "none"}))
            return 0
    except SystemExit as exc:
        print(exc)
        return 1
    if not value:
        print("BLOCKED: no open objective")
        return 1
    items = value["items"]
    for number in args.done or []:
        if not 1 <= number <= len(items):
            print(f"BLOCKED: there is no item {number}")
            return 1
    for number in args.done or []:
        items[number - 1].update(done=True, done_at=utc_now(), done_by=args.session)
    for text in args.add or []:
        items.append({"text": text.strip()[:MAX_ITEM_CHARS], "done": False})
    # A next action described work that may now be done; keep it only if restated.
    if args.next or args.done:
        value["next_action"] = args.next or ""
    value["session_id"] = args.session
    if args.action == "done":
        pending = pending_items(value)
        if pending:
            print(f"BLOCKED: {len(pending)} item(s) are not done")
            for number, text in pending[:12]:
                print(f"  [ ] {number}. {text}")
            return 1
        value["status"] = "done"
    elif args.action == "abandon":
        if not (args.reason or "").strip():
            print("BLOCKED: abandon requires --reason")
            return 1
        value["status"] = "abandoned"
        value["reason"] = args.reason
    save_objective(workspace, args.project, value, args.action)
    print(f"{args.action.upper()}: {len(items) - len(pending_items(value))}/{len(items)} items done; "
          f"next: {derived_next(value) if value['status'] == 'open' else '-'}")
    return 0


def cmd_start(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    handle, path, token = acquire_lock(workspace, args.project, args.session,
                                       args.owner or args.session)
    try:
        return _start(args, workspace)
    finally:
        release_lock(handle, path, token)


def _start(args, workspace: str) -> int:
    existing = None
    try:
        existing = load_state(workspace, args.project)
    except SystemExit as exc:
        print(exc)
        return 1
    if existing and existing.get("status") != "closed" and not args.new:
        print(f"RESUME: {existing.get('task')} [{existing.get('status')}] "
              f"owned by {existing.get('owner')}")
        print(f"  next: {existing.get('next_action') or 'UNKNOWN'}")
        print(f"  state: {state_path(workspace, args.project)}")
        return 0
    if existing and existing.get("status") != "closed" and args.new:
        if not guard_owner(existing, args.session, args.takeover, args.reason):
            print(f"BLOCKED: active task is owned by session {existing.get('session_id')}; "
                  f"use --takeover with a reason")
            return 1
        append_event(workspace, args.project, "supersede", existing,
                     {"reason": args.reason or "new task", "by": args.session})
    state = new_state(args.project, args.task, args.session, args.owner or args.session)
    save_state(workspace, args.project, state, "start")
    print(f"START: {args.project} {state['status']} ({state['session_id']})")
    return 0


def cmd_update(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    handle, path, token = acquire_lock(workspace, args.project, args.session,
                                       args.owner or args.session)
    try:
        return _update(args, workspace)
    finally:
        release_lock(handle, path, token)


def _update(args, workspace: str) -> int:
    try:
        state = load_state(workspace, args.project)
    except SystemExit as exc:
        print(exc)
        return 1
    if not state:
        print("BLOCKED: no work state; run start first")
        return 1
    if not guard_owner(state, args.session, args.takeover, args.reason):
        print(f"BLOCKED: task is owned by session {state.get('session_id')}; "
              f"use --takeover with a reason")
        return 1
    if args.takeover:
        state["session_id"] = args.session
        state["owner"] = args.owner or args.session
    if args.status:
        if args.status not in STATUSES:
            print(f"BLOCKED: status must be one of {', '.join(STATUSES)}")
            return 2
        state["status"] = args.status
    if args.decision:
        state["decision"] = args.decision
    if args.challenge:
        state["challenge"] = args.challenge
    if args.file:
        state["files"] = sorted(set(state.get("files") or []) | set(args.file))
    if args.error:
        state["open_errors"] = sorted(set(state.get("open_errors") or []) | set(args.error))
    if args.clear_error:
        state["open_errors"] = [item for item in state.get("open_errors") or []
                                if item not in set(args.clear_error)]
    if args.test:
        state["tests"] = (state.get("tests") or []) + [{"cmd": args.test, "at": utc_now()}]
    if args.next:
        state["next_action"] = args.next
    if args.map_sha:
        state["map_sha256"] = args.map_sha
    if args.git_head:
        state["git"] = {"head": args.git_head, "branch": args.git_branch or ""}
    save_state(workspace, args.project, state, "update")
    print(f"UPDATE: {args.project} {state['status']} ({state['session_id']})")
    return 0


def cmd_close(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    handle, path, token = acquire_lock(workspace, args.project, args.session,
                                       args.owner or args.session)
    try:
        return _close(args, workspace)
    finally:
        release_lock(handle, path, token)


def _close(args, workspace: str) -> int:
    try:
        state = load_state(workspace, args.project)
    except SystemExit as exc:
        print(exc)
        return 1
    if not state:
        print("BLOCKED: no work state")
        return 1
    if not guard_owner(state, args.session, args.takeover, args.reason):
        print(f"BLOCKED: task is owned by session {state.get('session_id')}")
        return 1
    decision_id = str(state.get("decision") or "")
    if decision_id:
        try:
            import decision as edr  # noqa: PLC0415
            record = edr.load(workspace, args.project, decision_id)
        except Exception:  # noqa: BLE001 - an unreadable link cannot prove completion
            print(f"BLOCKED: linked decision {decision_id} is not readable; completion is unproven")
            return 1
        problems = edr.integrity(record)
        if problems:
            print(f"BLOCKED: linked decision {decision_id} is not trustworthy")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        try:
            import review_contract as review  # noqa: PLC0415
        except ImportError:
            if record.get("risk") in ("STANDARD", "CRITICAL"):
                print("BLOCKED: review_contract.py is required for this decision but is not installed")
                return 1
            state["completion"] = "decision_only"
        else:
            if review.applies(record, workspace):
                if record.get("state") != "CLOSED":
                    print(f"BLOCKED: linked decision {decision_id} is {record.get('state')}, not CLOSED; "
                          "work-state cannot claim completion")
                    return 1
                verdict = review.validate(workspace, record)
                if not verdict["ok"]:
                    print("BLOCKED: engineering review evidence is incomplete")
                    for problem in verdict["problems"]:
                        print(f"  - {problem}")
                    return 1
                state["completion"] = "review_contract"
            else:
                state["completion"] = "decision_only"
    else:
        state["completion"] = "untracked"
        print("WARN: no linked decision; this close is a work-state cleanup, not an EOS completion claim")
    state["status"] = "closed"
    state["next_action"] = ""
    save_state(workspace, args.project, state, "close")
    print(f"CLOSED: {args.project}")
    return 0


def cmd_record_atomic(args) -> int:
    """Record a completed ATOMIC post-write as closed history.

    This is intentionally not a task start: it never creates an open ownership
    lease, so a second session can still write. The record is written under the
    same OS lock as every other work-state transition and is idempotent by
    decision + session + files.
    """
    workspace = ep.resolve_workspace(args.workspace)
    handle, path, token = acquire_lock(workspace, args.project, args.session,
                                       args.owner or "eos.atomic")
    try:
        result = _record_atomic(args, workspace)
    finally:
        release_lock(handle, path, token)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"{str(result.get('status', 'blocked')).upper()}: {args.project} "
              f"{result.get('record_id', '')}".rstrip())
    return 0 if result.get("ok") else 1


def _record_atomic(args, workspace: str) -> dict:
    try:
        existing = load_state(workspace, args.project)
    except SystemExit as exc:
        return {"ok": False, "status": "blocked", "reason": str(exc)}

    files = [os.path.normpath(item) for item in (args.file or []) if item]
    # Deliberately exclude map_state: a retry after a refresh must still be the
    # same atomic completion, not a second history record.
    record_id = digest({"project": args.project, "session": args.session,
                        "decision": args.decision, "files": sorted(files)})
    # One file is a step. The request it belongs to stays open until its items
    # are done, so an interrupted session has something to resume.
    try:
        objective = note_objective(workspace, args.project, args.task, args.session,
                                   files, args.decision)
    except SystemExit as exc:
        return {"ok": False, "status": "blocked", "reason": str(exc)}

    history = os.path.join(root(workspace, args.project), "history", record_id + ".json")
    if os.path.isfile(history):
        return {"ok": True, "status": "duplicate", "record_id": record_id}

    atomic = {
        "record_id": record_id, "task": args.task, "session": args.session,
        "decision": args.decision, "files": files,
        "map_state": args.map_state, "at": utc_now(),
    }

    if existing and existing.get("status") != "closed":
        # An open STANDARD/CRITICAL task owns the state. Record the completed
        # ATOMIC write in the hash-chained event log, never by closing or
        # rewriting the open task or stealing its session.
        append_event(workspace, args.project, "atomic_complete", existing,
                     {"atomic": atomic, "atomic_session": args.session})
        return {"ok": True, "status": "recorded_event_only", "record_id": record_id,
                "open_task": existing.get("task"),
                "open_session": existing.get("session_id")}

    state = new_state(args.project,
                      (f"atomic write: {', '.join(files) or 'unknown'}"
                       if objective or not args.task else args.task),
                      args.session, args.owner or "eos.atomic")
    state["status"] = "closed"
    state["decision"] = args.decision
    state["files"] = files
    state["map_state"] = args.map_state
    state["map_sha256"] = args.map_sha or ""
    state["next_action"] = ""
    state["closed_at"] = utc_now()
    state["record_id"] = record_id
    state["completion"] = "atomic_post_write"
    save_state(workspace, args.project, state, "atomic_complete", {"atomic": atomic})
    os.makedirs(os.path.dirname(history), exist_ok=True)
    write_json(history, state)
    return {"ok": True, "status": "recorded", "record_id": record_id,
            "objective": "open" if objective else "",
            "state": state_path(workspace, args.project)}


def cmd_record_blocked(args) -> int:
    """Persist a DENY without touching any session's active work state.

    A blocked write is evidence, not a task transition. It is written to a
    standalone file under the project's blocked/ directory so another session's
    open state and hash-chained events remain untouched.
    """
    workspace = ep.resolve_workspace(args.workspace)
    absolute = os.path.abspath(args.path if os.path.isabs(args.path)
                               else os.path.join(workspace, args.path))
    project = args.project or ""
    inferred = bool(project)
    if not project:
        try:
            import decision as edr
            project = edr.infer_project(workspace, absolute) or ""
            inferred = bool(project)
        except (ImportError, SystemExit, OSError):
            project = ""
    if not project:
        project = "auto-work"
    rel = os.path.relpath(absolute, workspace).replace("\\", "/")
    folder = os.path.join(root(workspace, project), "blocked")
    record_id = uuid.uuid4().hex[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    value = {
        "schema": SCHEMA, "kind": "blocked_write", "project": project,
        "project_inferred": inferred, "session": args.session or "", "path": rel,
        "reason": args.reason, "decision": args.decision or "",
        "map_state": args.map_state or "", "map_sha256": args.map_sha or "",
        "at": utc_now(), "record_id": record_id,
    }
    target = os.path.join(folder, f"blocked-{stamp}-{record_id}.json")
    write_json(target, value)
    result = {"ok": True, "status": "recorded", "path": target,
              "record_id": record_id, "project": project, "file": rel,
              "session": value["session"], "project_inferred": inferred}
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"{str(result.get('status', 'blocked')).upper()}: {result.get('path', '')}".rstrip())
    return 0 if result.get("ok") else 1


def cmd_resume(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    try:
        state = load_state(workspace, args.project)
    except SystemExit as exc:
        print(exc)
        return 1
    try:
        objective = open_objective(workspace, args.project)
    except SystemExit as exc:
        print(exc)
        return 1
    if not state and not objective:
        print("MISSING: no work state")
        return 1
    if args.json:
        value = dict(state or {})
        if objective:
            value["objective"] = {**objective, "next_action": derived_next(objective)}
        print(json.dumps(value, ensure_ascii=True))
        return 0
    if objective:
        print("\n".join(objective_lines(objective)))
    if state:
        print(f"{state['status']}: {state['task']}")
        print(f"  session: {state.get('session_id')}  owner: {state.get('owner')}")
        print(f"  decision: {state.get('decision') or '-'}  "
              f"challenge: {state.get('challenge') or '-'}")
        print(f"  files: {', '.join((state.get('files') or [])[:8]) or '-'}")
        print(f"  tests: {len(state.get('tests') or [])}  "
              f"open errors: {len(state.get('open_errors') or [])}")
        print(f"  next: {state.get('next_action') or 'UNKNOWN'}")
    return 0


def cmd_status(args) -> int:
    return cmd_resume(args)


def cmd_recover(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    path = events_path(workspace, args.project)
    if not os.path.isfile(path):
        print("MISSING: no event log")
        return 1
    previous = ""
    last_state = None
    problems = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"line {number}: not JSON")
                break
            if event.get("prev_sha256") != previous:
                problems.append(f"line {number}: broken hash chain")
                break
            expected = digest({k: v for k, v in event.items() if k != "event_sha256"})
            if event.get("event_sha256") != expected:
                problems.append(f"line {number}: event hash mismatch")
                break
            previous = event["event_sha256"]
            if isinstance(event.get("state"), dict):
                last_state = event["state"]
    if problems:
        print("BLOCKED: event log is corrupt")
        for problem in problems[:10]:
            print(f"  - {problem}")
        return 1
    if not last_state:
        print("BLOCKED: no valid state in the event log")
        return 1
    print(f"RECOVERABLE: last valid state for {args.project} is "
          f"{last_state.get('status')} ({last_state.get('task')})")
    if args.apply:
        write_json(state_path(workspace, args.project), last_state)
        print("RECOVERED: state.json restored from the event log")
    return 0


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def workspace() -> str:
        return tempfile.mkdtemp(prefix="eos-work-")

    @case("start creates a state and a hash-chained event")
    def _():
        root = workspace()
        assert cmd_start(argparse.Namespace(project="demo", task="fix a", session="s1",
                                           owner="agent1", new=False, workspace=root)) == 0
        state = load_state(root, "demo")
        assert state["status"] == "planned"
        event = last_event(root, "demo")
        assert event["event_sha256"] == digest({k: v for k, v in event.items()
                                                if k != "event_sha256"})
        return "created"

    @case("a new session resumes an active task")
    def _():
        root = workspace()
        cmd_start(argparse.Namespace(project="demo", task="fix a", session="s1",
                                     owner="agent1", new=False, workspace=root))
        assert cmd_start(argparse.Namespace(project="demo", task="another",
                                            session="s2", owner="agent2", new=False,
                                            workspace=root)) == 0
        state = load_state(root, "demo")
        assert state["task"] == "fix a"
        assert state["session_id"] == "s1"
        return "resumed"

    @case("another session cannot update without takeover")
    def _():
        root = workspace()
        cmd_start(argparse.Namespace(project="demo", task="fix a", session="s1",
                                     owner="agent1", new=False, workspace=root))
        args = argparse.Namespace(project="demo", session="s2", status="in_progress",
                                  decision="", challenge="", file=[], error=[], clear_error=[],
                                  test="", next="", map_sha="", git_head="", git_branch="",
                                  takeover=False, reason="", owner="", workspace=root)
        assert cmd_update(args) == 1
        return "blocked"

    @case("takeover with a reason succeeds")
    def _():
        root = workspace()
        cmd_start(argparse.Namespace(project="demo", task="fix a", session="s1",
                                     owner="agent1", new=False, workspace=root))
        args = argparse.Namespace(project="demo", session="s2", status="in_progress",
                                  decision="edr-1", challenge="", file=["a.py"], error=[],
                                  clear_error=[], test="pytest", next="run gate",
                                  map_sha="M", git_head="H", git_branch="main",
                                  takeover=True, reason="owner gone", owner="", workspace=root)
        assert cmd_update(args) == 0
        state = load_state(root, "demo")
        assert state["session_id"] == "s2"
        assert state["decision"] == "edr-1"
        return "taken"

    @case("start --new cannot replace another session's task")
    def _():
        root = workspace()
        cmd_start(argparse.Namespace(project="demo", task="fix a", session="s1",
                                     owner="agent1", new=False, workspace=root))
        blocked = cmd_start(argparse.Namespace(project="demo", task="new", session="s2",
                                               owner="agent2", new=True, takeover=False,
                                               reason="", workspace=root))
        assert blocked == 1
        allowed = cmd_start(argparse.Namespace(project="demo", task="new", session="s2",
                                               owner="agent2", new=True, takeover=True,
                                               reason="owner gone", workspace=root))
        assert allowed == 0
        assert load_state(root, "demo")["task"] == "new"
        return "supersede guarded"

    @case("a live lease blocks a real process even with the same session")
    def _():
        import subprocess
        root = workspace()
        handle, path, token = acquire_lock(root, "demo", "s1", "a")
        try:
            child = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "start",
                 "--project", "demo", "--task", "x", "--session", "s1",
                 "--owner", "a", "--new", "--workspace", root],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "EOS_LOCK_TIMEOUT": "1"})
            assert child.returncode != 0, child.stdout
            assert "BLOCKED" in (child.stdout + child.stderr), child.stdout
        finally:
            release_lock(handle, path, token)
        return "same-session process blocked"

    @case("an ancient timestamp on disk does not let another process steal a live lease")
    def _():
        import subprocess
        root = workspace()
        os.makedirs(os.path.dirname(lock_path(root, "demo")), exist_ok=True)
        with open(lock_path(root, "demo"), "w", encoding="utf-8", newline="\n") as handle_file:
            json.dump({"pid": 1, "session": "old", "owner": "x", "ts": 0}, handle_file)
        handle, path, token = acquire_lock(root, "demo", "s1", "a")
        try:
            child = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "start",
                 "--project", "demo", "--task", "x", "--session", "s2",
                 "--owner", "b", "--new", "--workspace", root],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "EOS_LOCK_TIMEOUT": "1"})
            assert child.returncode != 0, child.stdout
            assert "BLOCKED" in (child.stdout + child.stderr), child.stdout
        finally:
            release_lock(handle, path, token)
        return "no TTL steal"

    @case("the lease is exclusive and released")
    def _():
        root = workspace()
        handle, path, token = acquire_lock(root, "demo", "s1", "a", timeout=1)
        release_lock(handle, path, token)
        handle, path, token = acquire_lock(root, "demo", "s2", "b", timeout=1)
        release_lock(handle, path, token)
        return "released"

    @case("two real processes cannot both become the writer")
    def _():
        import subprocess
        root = workspace()
        script = os.path.abspath(__file__)
        children = []
        for session in ("p1", "p2"):
            children.append(subprocess.Popen(
                [sys.executable, script, "start", "--project", "demo",
                 "--task", "race", "--session", session, "--owner", session,
                 "--new", "--workspace", root],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
        outputs = [child.communicate(timeout=120)[0] for child in children]
        codes = [child.returncode for child in children]
        assert codes.count(0) == 1, (codes, outputs)
        loser = [text for text, code in zip(outputs, codes) if code != 0][0]
        assert "BLOCKED" in loser, loser
        state = load_state(root, "demo")
        winner = "p1" if codes[0] == 0 else "p2"
        assert state["session_id"] == winner, (state, codes)
        return "exactly one writer"

    def atomic_args(root, session="s1", decision="edr-1", file="a.py"):
        return argparse.Namespace(project="demo", task="create a", session=session,
                                  decision=decision, file=[file],
                                  map_state="CURRENT", map_sha="", owner="eos.atomic",
                                  json=True, workspace=root)

    @case("an atomic completion is recorded closed with a verifiable event")
    def _():
        root = workspace()
        assert cmd_record_atomic(atomic_args(root)) == 0
        state = load_state(root, "demo")
        assert state["status"] == "closed", state
        assert state["decision"] == "edr-1", state
        assert state["files"] == ["a.py"], state
        assert state["map_state"] == "CURRENT", state
        assert state["completion"] == "atomic_post_write", state
        event = last_event(root, "demo")
        assert event["action"] == "atomic_complete", event
        assert event["event_sha256"] == digest({k: v for k, v in event.items()
                                                if k != "event_sha256"})
        return "closed"

    @case("the same atomic completion is idempotent")
    def _():
        root = workspace()
        assert cmd_record_atomic(atomic_args(root)) == 0
        before = len(open(events_path(root, "demo"), encoding="utf-8").readlines())
        assert cmd_record_atomic(atomic_args(root)) == 0
        after = len(open(events_path(root, "demo"), encoding="utf-8").readlines())
        assert before == after, (before, after)
        history = os.path.join(root, ".agents", "evidence", "work", "demo", "history")
        assert len(os.listdir(history)) == 1, os.listdir(history)
        return "duplicate skipped"

    @case("an open task is never closed or overwritten by an atomic completion")
    def _():
        root = workspace()
        cmd_start(argparse.Namespace(project="demo", task="standard task", session="s1",
                                     owner="agent1", new=False, workspace=root))
        assert cmd_record_atomic(atomic_args(root, session="s2", decision="edr-atomic",
                                             file="b.py")) == 0
        state = load_state(root, "demo")
        assert state["status"] == "planned", state
        assert state["session_id"] == "s1", state
        assert state["task"] == "standard task", state
        event = last_event(root, "demo")
        assert event["action"] == "atomic_complete", event
        assert event["atomic"]["decision"] == "edr-atomic", event
        assert event["atomic_session"] == "s2", event
        return "open task preserved"

    @case("a new session replaces a closed record but keeps both histories")
    def _():
        root = workspace()
        assert cmd_record_atomic(atomic_args(root, session="s1", decision="edr-1",
                                             file="a.py")) == 0
        assert cmd_record_atomic(atomic_args(root, session="s2", decision="edr-2",
                                             file="b.py")) == 0
        state = load_state(root, "demo")
        assert state["decision"] == "edr-2", state
        assert state["files"] == ["b.py"], state
        history = os.path.join(root, ".agents", "evidence", "work", "demo", "history")
        assert len(os.listdir(history)) == 2, os.listdir(history)
        actions = [json.loads(line)["action"]
                   for line in open(events_path(root, "demo"), encoding="utf-8")
                   if line.strip()]
        assert actions.count("atomic_complete") == 2, actions
        return "two sessions"

    MULTI = "Finish the release:\n1. wire the switch\n2. admin UI\n3. run the suite\nGo."

    def step(root, number, task=MULTI, session="s1"):
        args = atomic_args(root, session=session, decision=f"edr-{number}",
                           file=f"f{number}.py")
        args.task = task
        return cmd_record_atomic(args)

    def objective(root, action, **kw):
        base = dict(project="demo", session="s1", task="", done=[], add=[], next="", reason="",
                    json=False, workspace=root, action=action, file=[], decision="")
        base.update(kw)
        return cmd_objective(argparse.Namespace(**base))

    @case("one file write never completes a multi-step request")
    def _():
        root = workspace()
        assert step(root, 1) == 0
        assert step(root, 2) == 0
        value = open_objective(root, "demo")
        assert value and value["status"] == "open" and len(value["items"]) == 3, value
        assert value["files"] == ["f1.py", "f2.py"]
        assert derived_next(value) == "item 1: wire the switch"
        assert "admin UI" not in load_state(root, "demo")["task"], "closed under the request's name"
        return "open"

    @case("a single-step write still closes and opens no objective")
    def _():
        root = workspace()
        assert step(root, 1, task="fix the typo in a.py") == 0
        assert open_objective(root, "demo") is None
        assert load_state(root, "demo")["task"] == "fix the typo in a.py"
        return "unchanged"

    @case("another session resumes with items and a next action and is not locked out")
    def _():
        root = workspace()
        step(root, 1)
        assert objective(root, "checkpoint", done=[1], next="bind the form") == 0
        assert step(root, 2, task="devam et", session="s2") == 0
        value = open_objective(root, "demo")
        assert value["task"] == MULTI and value["session_id"] == "s2"
        assert derived_next(value) == "bind the form"
        assert [n for n, _ in pending_items(value)] == [2, 3]
        assert objective(root, "checkpoint", session="s2", done=[2]) == 0
        assert derived_next(open_objective(root, "demo")) == "item 3: run the suite"
        return "resumed"

    @case("done is refused while an item is open; abandon needs a reason")
    def _():
        root = workspace()
        step(root, 1)
        assert objective(root, "done") == 1
        assert objective(root, "abandon") == 1
        assert objective(root, "checkpoint", done=[9]) == 1
        assert objective(root, "done", done=[1, 2, 3]) == 0
        assert open_objective(root, "demo") is None
        assert step(root, 4) == 0
        assert open_objective(root, "demo") is None, "a late write reopened a finished request"
        return "explicit end"

    @case("a new multi-step request keeps the unfinished items of the old one")
    def _():
        root = workspace()
        step(root, 1)
        objective(root, "checkpoint", done=[1])
        step(root, 2, task="Also:\n1. run the suite\n2. write notes")
        value = open_objective(root, "demo")
        assert [i["text"] for i in value["items"]] == ["run the suite", "write notes"]
        assert value["carried"] == ["admin UI"], value["carried"]
        return "carried"

    @case("a STANDARD write advances the objective too, and pasted bullets open none")
    def _():
        root = workspace()
        assert objective(root, "step", task="- PASS a\n- PASS b", file=["x.py"]) == 0
        assert open_objective(root, "demo") is None
        assert objective(root, "step", task=MULTI, file=["x.py"], decision="edr-9") == 0
        assert open_objective(root, "demo")["files"] == ["x.py"]
        assert objective(root, "open", session="s2", task="migrate the panel") == 0
        value = open_objective(root, "demo")
        assert [i["text"] for i in value["items"]] == ["migrate the panel"]
        assert len(value["carried"]) == 3
        return "advanced"

    @case("a tampered objective is refused, not silently replaced")
    def _():
        root = workspace()
        step(root, 1)
        value = read_json(objective_path(root, "demo"))
        value["items"][0]["done"] = True
        write_json(objective_path(root, "demo"), value)
        assert step(root, 2) == 1
        assert objective(root, "checkpoint", done=[2]) == 1
        return "fail closed"

    @case("concurrent atomic records serialise under the OS lock")
    def _():
        import subprocess
        root = workspace()
        script = os.path.abspath(__file__)
        children = []
        for session, decision, file in (("s1", "edr-a", "a.py"),
                                        ("s2", "edr-b", "b.py")):
            children.append(subprocess.Popen(
                [sys.executable, script, "record-atomic", "--project", "demo",
                 "--task", "atomic", "--session", session, "--decision", decision,
                 "--file", file, "--map-state", "CURRENT", "--json",
                 "--workspace", root],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
        outputs = [child.communicate(timeout=120)[0] for child in children]
        codes = [child.returncode for child in children]
        assert codes == [0, 0], (codes, outputs)
        events = [json.loads(line) for line in open(events_path(root, "demo"),
                                                    encoding="utf-8") if line.strip()]
        assert [event["action"] for event in events].count("atomic_complete") == 2, events
        previous = ""
        for event in events:
            assert event["prev_sha256"] == previous, event
            assert event["event_sha256"] == digest({k: v for k, v in event.items()
                                                    if k != "event_sha256"}), event
            previous = event["event_sha256"]
        history = os.path.join(root, ".agents", "evidence", "work", "demo", "history")
        assert len(os.listdir(history)) == 2, os.listdir(history)
        return "serialised"

    @case("a corrupt state is refused and recover restores the event state")
    def _():
        root = workspace()
        cmd_start(argparse.Namespace(project="demo", task="fix a", session="s1",
                                     owner="agent1", new=False, workspace=root))
        path = state_path(root, "demo")
        data = read_json(path)
        data["task"] = "tampered"
        write_json(path, data)
        try:
            load_state(root, "demo")
        except SystemExit:
            pass
        else:
            raise AssertionError("corrupt state was accepted")
        assert cmd_recover(argparse.Namespace(project="demo", apply=True, workspace=root)) == 0
        state = load_state(root, "demo")
        assert state["task"] == "fix a"
        return "recovered"

    @case("closing a work state with an unreadable linked decision is blocked")
    def _():
        root = workspace()
        cmd_start(argparse.Namespace(project="demo", task="fix a", session="s1",
                                     owner="agent1", new=False, workspace=root))
        args = argparse.Namespace(project="demo", session="s1", status="in_progress",
                                  decision="edr-missing", challenge="", file=[], error=[],
                                  clear_error=[], test="", next="", map_sha="", git_head="",
                                  git_branch="", takeover=False, reason="", owner="",
                                  workspace=root)
        assert cmd_update(args) == 0
        close = argparse.Namespace(project="demo", session="s1", takeover=False,
                                   reason="", owner="", workspace=root)
        assert cmd_close(close) == 1
        return "unproven completion blocked"

    @case("a blocked write is recorded without touching the owner's state")
    def _():
        root = workspace()
        assert cmd_start(argparse.Namespace(project="demo", task="fix a", session="s1",
                                            owner="agent1", new=False,
                                            workspace=root)) == 0
        state_file = state_path(root, "demo")
        events_file = events_path(root, "demo")
        with open(state_file, "rb") as handle:
            state_before = handle.read()
        with open(events_file, "rb") as handle:
            events_before = handle.read()
        args = argparse.Namespace(
            project="demo", path="blocked.py", session="s2",
            reason="another session owns this write surface", decision="",
            map_state="CURRENT", map_sha="ABC", json=True, workspace=root,
        )
        assert cmd_record_blocked(args) == 0
        with open(state_file, "rb") as handle:
            assert handle.read() == state_before
        with open(events_file, "rb") as handle:
            assert handle.read() == events_before
        blocked_dir = os.path.join(root, ".agents", "evidence", "work", "demo", "blocked")
        files = os.listdir(blocked_dir)
        assert len(files) == 1, files
        value = read_json(os.path.join(blocked_dir, files[0]))
        assert value["session"] == "s2", value
        assert value["path"] == "blocked.py", value
        assert "another session owns" in value["reason"], value
        return "blocked evidence isolated"

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
    parser = argparse.ArgumentParser(prog="work_state.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")
    common.add_argument("--project", required=True)

    p = sub.add_parser("start", parents=[common])
    p.add_argument("--task", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--owner", default="")
    p.add_argument("--new", action="store_true")
    p.add_argument("--takeover", action="store_true")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("update", parents=[common])
    p.add_argument("--session", required=True)
    p.add_argument("--status", default="")
    p.add_argument("--decision", default="")
    p.add_argument("--challenge", default="")
    p.add_argument("--file", action="append", default=[])
    p.add_argument("--error", action="append", default=[])
    p.add_argument("--clear-error", action="append", default=[])
    p.add_argument("--test", default="")
    p.add_argument("--next", default="")
    p.add_argument("--map-sha", default="")
    p.add_argument("--git-head", default="")
    p.add_argument("--git-branch", default="")
    p.add_argument("--takeover", action="store_true")
    p.add_argument("--reason", default="")
    p.add_argument("--owner", default="")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("close", parents=[common])
    p.add_argument("--session", required=True)
    p.add_argument("--takeover", action="store_true")
    p.add_argument("--reason", default="")
    p.add_argument("--owner", default="")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("record-atomic", parents=[common])
    p.add_argument("--task", default="")
    p.add_argument("--session", default="")
    p.add_argument("--decision", required=True)
    p.add_argument("--file", action="append", default=[])
    p.add_argument("--map-state", default="")
    p.add_argument("--map-sha", default="")
    p.add_argument("--owner", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_record_atomic)

    p = sub.add_parser("record-blocked")
    p.add_argument("--workspace")
    p.add_argument("--project", default="")
    p.add_argument("--path", required=True)
    p.add_argument("--session", default="")
    p.add_argument("--reason", required=True)
    p.add_argument("--decision", default="")
    p.add_argument("--map-state", default="")
    p.add_argument("--map-sha", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_record_blocked)

    p = sub.add_parser("objective", parents=[common])
    p.add_argument("action", choices=["open", "step", "show", "checkpoint", "done", "abandon"])
    p.add_argument("--file", action="append", default=[])
    p.add_argument("--decision", default="")
    p.add_argument("--session", default="")
    p.add_argument("--task", default="")
    p.add_argument("--done", type=int, action="append", default=[])
    p.add_argument("--add", action="append", default=[])
    p.add_argument("--next", default="")
    p.add_argument("--reason", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_objective)

    p = sub.add_parser("resume", parents=[common])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resume)
    p = sub.add_parser("status", parents=[common])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("recover", parents=[common])
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_recover)
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
