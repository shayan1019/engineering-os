#!/usr/bin/env python3
"""Persistent work state and session continuity.

A conversation is not a durable record. This tool keeps, per project:

  state.json     the current task, status, active decision, files, tests,
                 open errors, owner, next safe action, map version, git state
  events.jsonl   an append-only hash-chained log; every event carries the state
                 snapshot it produced, so a corrupt state file can be recovered

Concurrent sessions are explicit: a different session may not update or close a
task owned by another session without `--takeover` and a reason.

Commands:
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
                      args.task or f"atomic write: {', '.join(files) or 'unknown'}",
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
            "state": state_path(workspace, args.project)}


def cmd_resume(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    try:
        state = load_state(workspace, args.project)
    except SystemExit as exc:
        print(exc)
        return 1
    if not state:
        print("MISSING: no work state")
        return 1
    if args.json:
        print(json.dumps(state, ensure_ascii=True))
    else:
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
