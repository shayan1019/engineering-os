"""Project-scoped, local host telemetry. Never participates in EOS policy."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from uuid import uuid4

import _eos_paths as ep

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}\Z")
SECRET = re.compile(r"(?i)(?:^|[/_.-])(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]+|xox[baprs]-[A-Za-z0-9-]+|akia[A-Z0-9]{16})(?:$|[/_.-])")
MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_HOOK_BYTES = 1024 * 1024
SESSION_EVENTS = {"SessionStart": "session.started", "SessionEnd": "session.ended",
                  "UserPromptSubmit": "session.resumed", "Stop": "session.idle"}
TOOL_EVENTS = {"PreToolUse": "tool.started", "PostToolUse": "tool.completed",
               "PostToolUseFailure": "tool.failed", "PermissionDenied": "tool.denied"}


def identifier(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) and not SECRET.search(value) else None


def opaque(value):
    if isinstance(value, str) and 0 < len(value) <= 256:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return None


def workspace_digest(path):
    normalized = str(Path(path).resolve()).replace("\\", "/").rstrip("/")
    if os.name == "nt":
        normalized = normalized.lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def emit(workspace, session, host, kind, **fields):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,95}", session):
        return
    home = Path(ep.EOS_HOME).resolve()
    directory = home / "evidence" / "nexus" / workspace_digest(workspace)
    path = directory / (session + ".jsonl")

    def encode(event_kind, values):
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return (json.dumps({"schema": 1, "ts": now, "id": uuid4().hex, "host": host,
                            "kind": event_kind, **values}, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")

    try:
        if directory.resolve() != directory or path.is_symlink():
            return
        directory.mkdir(parents=True, exist_ok=True)
        info = path.stat() if path.exists() else None
        if info and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            return
        if info and info.st_size >= MAX_STREAM_BYTES:
            # Keep the newest bounded segment and signal the discarded segment.
            path.replace(path.with_suffix(".previous"))
            with path.open("ab", buffering=0) as stream:
                stream.write(encode("telemetry.loss", {}))
        with path.open("ab", buffering=0) as stream:
            stream.write(encode(kind, {key: value for key, value in fields.items() if value is not None}))
    except OSError:
        return


def auto_open(workspace, session, host):
    if sys.platform != "win32":
        return
    home = Path(ep.EOS_HOME)
    prefs = home / "nexus" / "preferences.json"
    exe = home / "bin" / "eos.exe"
    try:
        with prefs.open(encoding="utf-8-sig") as stream:
            preferences = json.loads(stream.read(32769))
        if not isinstance(preferences, dict) or preferences.get("autoOpen") is not True or not exe.is_file():
            return
        subprocess.Popen([str(exe), "nexus", "--auto", "--workspace", str(workspace), "--session", session,
                          "--host", host], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS,
                         close_fds=True)
    except (OSError, ValueError):
        return


def observe_hook(host, payload):
    workspace = os.environ.get("EOS_NEXUS_WORKSPACE")
    cwd = payload.get("cwd")
    if not workspace or not isinstance(cwd, str):
        return
    workspace = Path(workspace).resolve()
    if not Path(cwd).resolve().is_relative_to(workspace):
        return
    native_session = opaque(payload.get("session_id"))
    if not native_session:
        return
    session = f"{host}-{native_session}"
    event = payload.get("hook_event_name")
    if not isinstance(event, str):
        return
    if event in SESSION_EVENTS:
        emit(workspace, session, host, SESSION_EVENTS[event])
        model = identifier(payload.get("model"))
        provider = identifier(payload.get("provider"))
        if model or provider:
            emit(workspace, session, host, "model.reported", model=model, provider=provider)
        if event == "SessionStart":
            auto_open(workspace, session, host)
    elif event in TOOL_EVENTS:
        tool = identifier(payload.get("tool_name"))
        correlation = opaque(payload.get("tool_use_id"))
        emit(workspace, session, host, TOOL_EVENTS[event], tool=tool, correlation=correlation)
        if tool and tool.startswith("mcp__"):
            emit(workspace, session, host, "mcp.reported", tool=tool, correlation=correlation)
        tool_input = payload.get("tool_input")
        if event == "PostToolUse" and tool == "Skill" and isinstance(tool_input, dict):
            skill = identifier(tool_input.get("skill"))
            if skill:
                emit(workspace, session, host, "skill.loaded", skill=skill, correlation=correlation)
    elif event in {"SubagentStart", "SubagentStop"}:
        correlation = opaque(payload.get("agent_id"))
        agent = identifier(payload.get("agent_type"))
        if correlation and agent:
            kind = "agent.started" if event == "SubagentStart" else "agent.completed"
            emit(workspace, session, host, kind, agent=agent, correlation=correlation)


def observe_gate(host, session, artifact):
    """Attach an independently verified gate receipt to an existing host stream."""
    import quality_gate

    workspace = Path(quality_gate.WORKSPACE).resolve()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,95}", session) or host not in {"opencode", "claude", "codex"}:
        return 2
    stream = Path(ep.EOS_HOME) / "evidence" / "nexus" / workspace_digest(workspace) / (session + ".jsonl")
    if not stream.is_file() or stream.is_symlink():
        return 2
    try:
        with stream.open("rb") as source:
            head = json.loads(source.readline(16384))
        if not isinstance(head, dict) or head.get("host") != host:
            return 2
        before = quality_gate.sha256_file(artifact)
        verified = quality_gate.verify_artifact(artifact)["ok"] and quality_gate.sha256_file(artifact) == before
    except (OSError, ValueError):
        verified = False
    if not verified:
        emit(workspace, session, host, "evidence.recorded", mechanism="quality-gate-verification-failed")
        return 1
    emit(workspace, session, host, "evidence.recorded", mechanism="quality-gate-unbound", artifact_sha256=before)
    return 0


def main():
    if len(sys.argv) == 5 and sys.argv[1] == "gate":
        return observe_gate(*sys.argv[2:])
    if len(sys.argv) != 2 or sys.argv[1] not in {"claude", "codex"}:
        return
    try:
        raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
        if len(raw) > MAX_HOOK_BYTES:
            return
        payload = json.loads(raw)
        if isinstance(payload, dict):
            observe_hook(sys.argv[1], payload)
    except (OSError, ValueError, RecursionError):
        return


if __name__ == "__main__":
    sys.exit(main())
