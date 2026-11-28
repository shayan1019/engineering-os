"""NEXUS read-only EOS evidence observer and private desktop stream."""
from __future__ import annotations

import argparse
import hashlib
import heapq
import hmac
import json
import math
import os
import re
import stat
import sys
import threading
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import BinaryIO

MAX_LINE = 16384
MAX_FILES = 64
MAX_SCAN_ENTRIES = 256
MAX_EVENTS = 500
MAX_SESSIONS = 256
MAX_PROJECTS = 16
MAX_REGISTRY = 1_000_000
MAX_REGISTRY_ITEMS = 96
MAX_GRAPH_BUILD_STATE = 16384
TAIL_BYTES = 65536
POLL_BYTES = 262144
LIVE_SECONDS = 60
HELLO_CONTEXT = b"eos-nexus-stdio-v1"
FAMILIES = ("tools", "skills")
ALLOWED_STATUS = {"ALLOW", "DENY", "AFTER", "USED", "SKIPPED", "CLOSED", "FAILED", "TOUCHED", "BLOCKED", "PASS", "FAIL"}

EVENT_STATES = {
    "session.started": "ACTIVE", "session.heartbeat": "ACTIVE",
    "session.resumed": "ACTIVE", "session.idle": "IDLE", "session.ended": "COMPLETED",
    "agent.started": "ACTIVE", "agent.completed": "COMPLETED",
    "agent.failed": "FAILED", "agent.denied": "DENIED",
    "skill.discovered": "DISCOVERED", "skill.selected": "SELECTED",
    "skill.loaded": "LOADED", "skill.used": "IN_USE", "skill.completed": "COMPLETED",
    "skill.rejected": "REJECTED", "skill.unavailable": "UNAVAILABLE",
    "tool.started": "ACTIVE", "tool.completed": "COMPLETED", "tool.failed": "FAILED",
    "tool.denied": "DENIED",
    "model.reported": "OBSERVED", "mcp.reported": "OBSERVED",
    "context.resolved": "OBSERVED", "query.routed": "OBSERVED",
    "graph.reported": "OBSERVED", "orchestrator.reported": "OBSERVED",
    "security.denied": "DENY", "security.allowed": "ALLOW",
    "gate.verified": "OBSERVED", "evidence.recorded": "OBSERVED",
    "promotion.recorded": "OBSERVED", "rollback.recorded": "OBSERVED",
    "telemetry.loss": "LOSS",
}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,99}", re.ASCII)
LOCAL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", re.ASCII)
# Known credential forms are excluded even when a producer places them in an ID.
CREDENTIAL = re.compile(
    r"(?:^|[:/.])(?:sk-(?:ant-|proj-|svcacct-)?[A-Za-z0-9_-]{12,}"
    r"|gh[pousr]_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{10,})"
    r"|^eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\."
    r"|^[A-Za-z0-9_-]{48,}$", re.ASCII,
)


def identifier(value: object, pattern: re.Pattern = IDENTIFIER) -> str | None:
    if isinstance(value, str) and pattern.fullmatch(value) and not CREDENTIAL.search(value):
        return value
    return None


def timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9TZ:+.\-]{10,40}", value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def duration(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 86400:
        return float(value)
    return None


def local_path(path: Path) -> Path:
    """Normalize lexically. Never follow a workspace-controlled junction."""
    normalized = Path(os.path.abspath(path))
    if str(normalized).startswith(("\\\\", "//")):
        raise OSError("non-local path")
    if os.name == "nt" and normalized.drive:
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(normalized.anchor) == 4:
            raise OSError("non-local drive")
    return normalized


def checked_stat(path: Path, *, directory: bool) -> os.stat_result:
    for parent in reversed(path.parents):
        parent_stat = parent.lstat()
        if is_redirect(parent_stat) or not stat.S_ISDIR(parent_stat.st_mode):
            raise OSError("redirected parent")
    info = path.lstat()
    if is_redirect(info) or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise OSError("unexpected file type")
    if not directory and info.st_nlink != 1:
        raise OSError("unexpected file links")
    return info


def is_redirect(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def open_checked(path: Path, expected: os.stat_result) -> BinaryIO:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    stream = os.fdopen(os.open(path, flags), "rb")
    actual = os.fstat(stream.fileno())
    if not os.path.samestat(expected, actual) or not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1:
        stream.close()
        raise OSError("changed file")
    return stream


def bounded_bytes(path: Path, limit: int) -> bytes:
    info = checked_stat(path, directory=False)
    if info.st_size > limit:
        raise ValueError("input exceeds limit")
    with open_checked(path, info) as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("input grew over limit")
    return payload


def list_candidates(directory: Path, suffix: str, issues: Counter, *, directories: bool = False) -> list[tuple]:
    try:
        checked_stat(directory, directory=True)
        with os.scandir(directory) as entries:
            batch = list(islice(entries, MAX_SCAN_ENTRIES + 1))
            if len(batch) > MAX_SCAN_ENTRIES:
                issues["directory_limit"] += 1
            candidates = []
            for entry in batch[:MAX_SCAN_ENTRIES]:
                name = entry.name
                if not name.endswith(suffix):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if is_redirect(info):
                        issues["redirect_skipped"] += 1
                    elif directories and stat.S_ISDIR(info.st_mode):
                        candidates.append((info.st_mtime_ns, Path(entry.path), info))
                    elif not directories and stat.S_ISREG(info.st_mode):
                        candidates.append((info.st_mtime_ns, Path(entry.path), info))
                except OSError:
                    issues["read_error"] += 1
    except FileNotFoundError:
        return []
    except OSError:
        issues["read_error"] += 1
        return []
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1].name), reverse=True)
    if len(candidates) > MAX_FILES:
        issues["file_limit"] += len(candidates) - MAX_FILES
    verified = []
    for stamp, path, _ in candidates[:MAX_FILES]:
        try:
            # Windows DirEntry.stat omits inode, device and link count.
            info = path.lstat()
            if is_redirect(info) or (not directories and info.st_nlink != 1):
                issues["redirect_skipped"] += 1
            else:
                verified.append((stamp, path, info))
        except OSError:
            issues["read_error"] += 1
    return verified


def registry_items(name: str, document: dict) -> list[str]:
    fields = {
        "AGENT_REGISTRY": ("roles",),
        "COMPATIBILITY": ("runtime_matrix",),
        "HOSTS": ("hosts",),
        "ORCHESTRATORS": ("providers",),
        "PROJECT_PROFILES": ("profiles", "projects"),
        "ROUTING_MAP": ("clusters",),
        "SKILL_INDEX": ("skills",),
    }
    items = set()
    for key in fields.get(name, ()):
        entries = document.get(key)
        if isinstance(entries, dict):
            entries = list(islice(entries.keys(), MAX_REGISTRY_ITEMS))
        if isinstance(entries, list):
            for entry in entries[:MAX_REGISTRY_ITEMS]:
                if isinstance(entry, dict):
                    entry = entry.get("id") or entry.get("name") or entry.get("runtime")
                item = identifier(entry, LOCAL_NAME)
                if item:
                    items.add(item)
    return sorted(items)


def read_registry(engine: Path) -> dict:
    engine = local_path(engine)
    result = {}
    required = ("AGENT_REGISTRY", "SKILL_INDEX", "HOSTS", "ORCHESTRATORS", "PROJECT_PROFILES")
    for name in required:
        result[name] = {"state": "unavailable"}
    for _, path, _ in list_candidates(engine / "registry", ".json", Counter()):
        name = identifier(path.stem, LOCAL_NAME)
        if not name:
            continue
        try:
            raw = bounded_bytes(path, MAX_REGISTRY)
            value = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(value, dict):
                continue
            counts = {
                key: len(v) for k, v in islice(value.items(), MAX_REGISTRY_ITEMS)
                if isinstance(v, (list, dict)) and (key := identifier(k, LOCAL_NAME))
            }
            result[name] = {"state": "discovered", "sha256": hashlib.sha256(raw).hexdigest(),
                            "counts": counts, "items": registry_items(name, value)}
            if name == "ORCHESTRATORS" and isinstance(value.get("providers"), list):
                result[name]["adapterOnly"] = sorted({item for row in value["providers"][:MAX_REGISTRY_ITEMS]
                    if isinstance(row, dict) and (item := identifier(row.get("id"), LOCAL_NAME))
                    and isinstance(row.get("execution"), dict) and row["execution"].get("protocol") is None})
        except (OSError, ValueError, RecursionError):
            result[name] = {"state": "unavailable"}
    installed_skills = []
    for _, skill, _ in list_candidates(engine / "skills", "", Counter(), directories=True):
        item = identifier(skill.name, LOCAL_NAME)
        if not item:
            continue
        try:
            checked_stat(skill / "SKILL.md", directory=False)
        except OSError:
            continue
        installed_skills.append(item)
    if installed_skills:
        index = result["SKILL_INDEX"]
        items = sorted(set(index.get("items", [])) | set(installed_skills))
        result["SKILL_INDEX"] = {**index, "state": "discovered", "items": items[:MAX_REGISTRY_ITEMS],
                                 "counts": {**index.get("counts", {}), "packaged": len(installed_skills)}}
    return result


@dataclass
class Cursor:
    inode: tuple[int, int]
    offset: int = 0
    pending: bytes = b""
    discarding: bool = False
    boundary: bytes = b""
    mtime: int = -1

    def restart(self, info: os.stat_result, issues: Counter) -> None:
        self.offset = max(0, info.st_size - TAIL_BYTES)
        self.pending = b""
        self.discarding = self.offset > 0
        self.boundary = b""
        if self.discarding:
            issues["older_history_truncated"] += 1

    def lines(self, path: Path, info: os.stat_result, allowance: int,
              issues: Counter) -> tuple[list[tuple[int, bytes]], int]:
        if info.st_size == self.offset and info.st_mtime_ns == self.mtime:
            return [], 0
        current = checked_stat(path, directory=False)
        if not os.path.samestat(info, current):
            raise OSError("changed file")
        with open_checked(path, current) as stream:
            if info.st_size < self.offset or (
                self.boundary and (
                    stream.seek(self.offset - len(self.boundary)) >= 0
                    and stream.read(len(self.boundary)) != self.boundary
                )
            ):
                issues["log_reset"] += 1
                self.restart(info, issues)
            if info.st_size - self.offset > allowance:
                self.offset = info.st_size - allowance
                self.boundary = b""
                self.pending = b""
                self.discarding = True
                issues["read_window_overrun"] += 1
            stream.seek(self.offset)
            payload = stream.read(min(allowance, max(0, info.st_size - self.offset)))
        if not payload:
            return [], 0
        origin = self.offset - len(self.pending)
        buffer = self.pending + payload
        self.offset += len(payload)
        self.mtime = info.st_mtime_ns
        self.boundary = (self.boundary + payload)[-24:]
        self.pending = b""
        complete = buffer.split(b"\n")
        final = complete.pop()
        lines = []
        for next_line in complete:
            if self.discarding:
                self.discarding = False
            elif len(next_line) > MAX_LINE:
                issues["oversized_event"] += 1
            elif next_line:
                lines.append((origin, next_line))
            origin += len(next_line) + 1
        if not self.discarding:
            if len(final) > MAX_LINE:
                issues["oversized_event"] += 1
                self.discarding = True
            else:
                self.pending = final
        return lines, len(payload)


def load_event(raw: bytes, issues: Counter) -> dict | None:
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        issues["malformed_event"] += 1
        return None
    if isinstance(value, dict):
        return value
    issues["malformed_event"] += 1
    return None


def legacy_event(value: dict, family: str, stem: str) -> dict:
    safe = {"session": re.sub(r"[^A-Za-z0-9_.-]", "_", stem)[:80], "family": family}
    stamp = timestamp(value.get("ts"))
    if stamp:
        safe["ts"] = stamp
    elapsed = duration(value.get("duration_seconds"))
    if elapsed is not None:
        safe["duration_seconds"] = elapsed
    for key in ("kind", "action", "tool", "status", "risk", "skill"):
        item = identifier(value.get(key), LOCAL_NAME)
        if item:
            safe[key] = item
    verdict = value.get("verdict")
    safe["verdict"] = verdict.upper() if isinstance(verdict, str) and verdict.upper() in ALLOWED_STATUS else "UNKNOWN"
    return safe


def read_events(workspace: Path, max_events: int = MAX_EVENTS) -> dict:
    root = local_path(workspace) / ".agents" / "evidence"
    result = deque(maxlen=min(max(1, max_events), MAX_EVENTS))
    issues: Counter = Counter()
    for family in FAMILIES:
        for _, path, info in reversed(list_candidates(root / family, ".jsonl", issues)):
            reader = Cursor((info.st_dev, info.st_ino))
            reader.restart(info, issues)
            try:
                lines, _ = reader.lines(path, info, TAIL_BYTES, issues)
            except OSError:
                issues["read_error"] += 1
                continue
            for _, raw in lines:
                value = load_event(raw, issues)
                if value is not None:
                    row = legacy_event(value, family, path.stem)
                    if not identifier(row["session"], LOCAL_NAME):
                        row["session"] = "unavailable"
                    result.append(row)
    return {"mode": "recorded", "events": sorted(result, key=lambda e: e.get("ts", "")),
            "issues": sorted(issues), "limit": MAX_EVENTS}


def workspace_digest(workspace: Path) -> str:
    name = str(workspace).replace("\\", "/").rstrip("/")
    if os.name == "nt":
        name = name.lower()
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Project:
    root: Path
    id: str
    label: str


def find_projects(eos_home: Path, explicit: list[Path], issues: Counter) -> list[Project]:
    projects = {}
    if len(explicit) > MAX_PROJECTS:
        issues["project_limit"] += 1
    roots = [(root, None) for root in explicit[:MAX_PROJECTS]]
    try:
        registry = json.loads(bounded_bytes(eos_home / "workspaces.json", MAX_REGISTRY).decode("utf-8-sig"))
        registered = registry.get("workspaces", []) if isinstance(registry, dict) else []
        if isinstance(registered, list):
            if len(registered) > MAX_PROJECTS:
                issues["project_limit"] += 1
            for entry in registered[:MAX_PROJECTS]:
                if (isinstance(entry, dict) and isinstance(entry.get("path"), str)
                        and Path(entry["path"]).is_absolute() and not entry.get("missing")):
                    roots.append((Path(entry["path"]), entry.get("name")))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, RecursionError):
        issues["workspace_registry_unavailable"] += 1
    for raw_path, label in roots:
        if len(projects) >= MAX_PROJECTS:
            issues["project_limit"] += 1
            break
        try:
            path = local_path(raw_path)
            checked_stat(path, directory=True)
        except (OSError, ValueError):
            issues["workspace_unavailable"] += 1
            continue
        project_id = workspace_digest(path)
        safe_label = project_label(label) or project_label(path.name)
        projects.setdefault(project_id, Project(path, project_id, safe_label or f"Project {len(projects) + 1}"))
    return list(projects.values())


def project_label(value: object) -> str | None:
    if (isinstance(value, str) and 1 <= len(value) <= 80 and not CREDENTIAL.search(value)
            and all(char.isalnum() or char in " ._-()" for char in value)):
        return value
    return None


def event_digest(*pieces: str) -> str:
    return hashlib.sha256("\0".join(pieces).encode("utf-8")).hexdigest()[:24]


def graphify_observation(project: Project, now: datetime) -> dict | None:
    """Project-scoped observation of Graphify's own completed build stamp."""
    folder = project.root / "graphify-out"
    try:
        graph = checked_stat(folder / "graph.json", directory=False)
        raw = bounded_bytes(folder / ".graphify_build_state.json", MAX_GRAPH_BUILD_STATE)
        build = json.loads(raw.decode("utf-8"))
        if not isinstance(build, dict) or type(build.get("graph_mtime_ns")) is not int or type(build.get("graph_size")) is not int:
            return None
        if graph.st_size <= 0 or build["graph_size"] != graph.st_size or build["graph_mtime_ns"] != graph.st_mtime_ns:
            return None
        source = build.get("scan_root")
        if not isinstance(source, str) or not Path(source).is_absolute() or local_path(Path(source)) != project.root:
            return None
        stamp = datetime.fromtimestamp(graph.st_mtime, timezone.utc)
        if stamp > now + timedelta(seconds=5):
            return None
    except (OSError, UnicodeError, ValueError, OverflowError, RecursionError):
        return None
    receipt = event_digest(project.id, str(graph.st_mtime_ns), str(graph.st_size))
    return {"id": receipt, "project": project.id, "host": "graphify", "kind": "graph.reported",
            "family": "graph", "status": "OBSERVED", "source": "graphify-build-state",
            "mechanism": "graphify-build", "ts": timestamp(stamp.isoformat()), "evidence": f"project:{receipt}"}


def typed_event(value: dict, issues: Counter) -> dict | None:
    kind = value.get("kind")
    stamp = timestamp(value.get("ts"))
    host = identifier(value.get("host"), LOCAL_NAME)
    if type(value.get("schema")) is not int or value.get("schema") != 1 or not stamp or not host:
        issues["invalid_host_event"] += 1
        return None
    if not isinstance(kind, str) or kind not in EVENT_STATES:
        issues["unsupported_host_event"] += 1
        return None
    family = kind.partition(".")[0]
    result = {"kind": kind, "ts": stamp, "host": host, "family": family,
              "status": EVENT_STATES[kind], "source": "host"}
    for key in ("agent", "parent", "skill", "tool", "provider", "model", "server", "query", "task",
                "correlation", "mechanism", "version"):
        field_pattern = IDENTIFIER if key == "model" else LOCAL_NAME
        name = identifier(value.get(key), field_pattern)
        if name and key == "model" and re.match(r"^[A-Za-z]:/", name):
            name = None
        if name:
            result[key] = name
    required = {"agent": "agent", "skill": "skill", "tool": "tool"}.get(family)
    if required and required not in result:
        issues["invalid_host_event"] += 1
        return None
    severity = value.get("severity")
    if isinstance(severity, str) and severity.upper() in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        result["severity"] = severity.upper()
    elapsed = duration(value.get("duration_seconds"))
    if elapsed is not None:
        result["duration_seconds"] = elapsed
    if family == "tool":
        for key in ("input_bytes", "output_bytes", "argument_count"):
            count = value.get(key)
            if type(count) is int and 0 <= count <= 100_000_000:
                result[key] = count
    if kind == "gate.verified":
        verdict = value.get("verdict")
        artifact = value.get("artifact_sha256")
        has_digest = isinstance(artifact, str) and re.fullmatch(r"[0-9a-fA-F]{64}", artifact)
        if has_digest:
            result["artifact_sha256"] = artifact.lower()
        if isinstance(verdict, str) and (verdict in {"FAIL", "BLOCKED"} or verdict == "PASS" and has_digest):
            result["status"] = verdict
            result["verdict"] = verdict
    if kind == "evidence.recorded":
        artifact = value.get("artifact_sha256")
        if isinstance(artifact, str) and re.fullmatch(r"[0-9a-fA-F]{64}", artifact):
            result["artifact_sha256"] = artifact.lower()
    if family == "security":
        result["verdict"] = result["status"]
    if family == "mcp" and value.get("connection_status") in ("connected", "pending", "disabled", "failed", "needs_auth"):
        result["connection_status"] = value["connection_status"]
    if family == "model":
        for key in ("input_tokens", "output_tokens"):
            count = value.get(key)
            if type(count) is int and 0 <= count <= 100_000_000:
                result[key] = count
        cost = value.get("cost")
        if type(cost) in (int, float) and 0 <= cost <= 1_000_000 and math.isfinite(cost):
            result["cost"] = cost
            currency = value.get("currency")
            if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency):
                result["currency"] = currency
    if kind == "telemetry.loss":
        count = value.get("lost")
        issues["producer_loss"] += count if type(count) is int and 0 < count <= 1_000_000 else 1
    return result


@dataclass
class Observer:
    engine: Path
    eos_home: Path
    explicit: list[Path]
    files: dict[Path, Cursor] = field(default_factory=dict)
    issues: Counter = field(default_factory=Counter)
    events: list = field(default_factory=list)
    seen: OrderedDict = field(default_factory=OrderedDict)
    sessions: OrderedDict = field(default_factory=OrderedDict)
    projects: list[Project] = field(default_factory=list)
    registry: dict = field(default_factory=dict)
    metadata_issues: Counter = field(default_factory=Counter)
    graphify: dict = field(default_factory=dict)
    refreshed: float = -math.inf
    sequence: int = 0

    def scan(self, now: datetime | None = None) -> dict:
        now = now or utc_now()
        future_limit = timestamp((now + timedelta(seconds=5)).isoformat())
        transient: Counter = Counter()
        monotonic_now = time.monotonic()
        if monotonic_now - self.refreshed >= 5:
            self.metadata_issues = Counter()
            self.projects = find_projects(self.eos_home, self.explicit, self.metadata_issues)
            self.registry = read_registry(self.engine)
            self.graphify = {project.id: observation for project in self.projects
                             if (observation := graphify_observation(project, now)) is not None}
            self.refreshed = monotonic_now
        if not self.projects:
            transient["no_projects"] += 1
        paths = set()
        for project in self.projects:
            dirs = [("host", self.eos_home / "evidence" / "nexus" / project.id)]
            dirs.extend((family, project.root / ".agents" / "evidence" / family) for family in FAMILIES)
            allowance = POLL_BYTES
            for source, folder in dirs:
                candidates = list_candidates(folder, ".jsonl", transient)
                for _, path, info in candidates:
                    paths.add(path)
                    if not allowance:
                        if path not in self.files or info.st_size != self.files[path].offset:
                            transient["poll_budget"] += 1
                        continue
                    cursor = self.files.get(path)
                    inode = (info.st_dev, info.st_ino)
                    if cursor is None or cursor.inode != inode:
                        if cursor is not None:
                            self.issues["log_reset"] += 1
                        cursor = self.files[path] = Cursor(inode)
                        cursor.restart(info, self.issues)
                    try:
                        lines, read = cursor.lines(path, info, min(TAIL_BYTES, allowance), self.issues)
                    except OSError:
                        transient["read_error"] += 1
                        continue
                    allowance -= read
                    for offset, raw in lines:
                        self.ingest(project.id, path, source, cursor, offset, raw, future_limit)
        self.files = {path: self.files[path] for path in paths if path in self.files}
        current_projects = {project.id for project in self.projects}
        sessions = [self.session_status(row, now) for row in self.sessions.values() if row["project"] in current_projects]
        modes = {row["project"] for row in sessions if row["mode"] == "live"}
        historical = {row["project"] for row in sessions}
        projects = [{"id": project.id, "label": project.label,
                     "mode": "live" if project.id in modes else ("stale" if project.id in historical else "disconnected"),
                     "graphify": self.graphify.get(project.id)}
                    for project in self.projects]
        combined = self.issues + self.metadata_issues + transient
        return {"schema": 1, "type": "snapshot", "mode": ("live" if modes else ("stale" if historical else "disconnected")),
                "timestamp": timestamp(now.isoformat()), "registries": self.registry, "projects": projects,
                "sessions": sessions,
                "events": [event for _, _, event in sorted(self.events) if event["project"] in current_projects],
                "issues": sorted(combined), "issueCounts": dict(combined), "limit": MAX_EVENTS}

    def ingest(self, project: str, path: Path, source: str, cursor: Cursor, offset: int, raw: bytes,
               future_limit: str) -> None:
        value = load_event(raw, self.issues)
        if value is None:
            return
        if source == "host":
            event = typed_event(value, self.issues)
            if not event:
                return
        else:
            event = legacy_event(value, source, path.stem)
            verdict = event["verdict"]
            # Legacy permission receipts may show a denial. A legacy skill
            # "USED" receipt can come from a pre-execution permission request.
            event["status"] = verdict if source == "tools" and verdict in {"ALLOW", "DENY"} else "UNKNOWN"
            event["verdict"] = event["status"]
            event["host"] = "opencode"
            event["source"] = "recorded"
        if event.get("ts", "") > future_limit:
            self.issues["future_event"] += 1
            if source == "host":
                return
            event.pop("ts", None)
        host = event["host"]
        session = event_digest(project, host, path.stem)
        reported_id = identifier(value.get("id"), LOCAL_NAME) if source == "host" else None
        signature = reported_id or event_digest(str(cursor.inode), str(offset), raw.hex())
        event_id = event_digest(project, session, source, signature)
        if event_id in self.seen:
            return
        self.seen[event_id] = None
        while len(self.seen) > MAX_EVENTS * 4:
            self.seen.popitem(last=False)
        self.sequence += 1
        event.update(id=event_id, project=project, session=session, sequence=self.sequence,
                     evidence=f"{source}:{event_id}")
        entry = (event.get("ts", ""), self.sequence, event)
        if len(self.events) == MAX_EVENTS:
            heapq.heappushpop(self.events, entry)
            self.issues["history_window_full"] = 1
        else:
            heapq.heappush(self.events, entry)
        session_state = self.sessions.setdefault(session, {"id": session, "project": project, "host": host,
                                                            "lastSeen": None, "lifecycleAt": None,
                                                            "lifecycleKind": None})
        stamp = event.get("ts")
        if stamp and (not session_state["lastSeen"] or stamp > session_state["lastSeen"]):
            session_state["lastSeen"] = stamp
        if source == "host" and event["family"] == "session" and (
            not session_state["lifecycleAt"] or stamp >= session_state["lifecycleAt"]
        ):
            session_state["lifecycleAt"] = stamp
            session_state["lifecycleKind"] = event["kind"]
        self.sessions.move_to_end(session)
        while len(self.sessions) > MAX_SESSIONS:
            self.sessions.popitem(last=False)
            self.issues["session_limit"] += 1

    @staticmethod
    def session_status(state: dict, now: datetime) -> dict:
        active = state["lifecycleKind"] in {"session.started", "session.heartbeat", "session.resumed"}
        mode = "stale"
        if active and state["lifecycleAt"]:
            age = now - datetime.fromisoformat(state["lifecycleAt"].replace("Z", "+00:00"))
            if -timedelta(seconds=5) <= age <= timedelta(seconds=LIVE_SECONDS):
                mode = "live"
        elif state["lifecycleKind"] == "session.ended":
            mode = "disconnected"
        return {"id": state["id"], "project": state["project"], "host": state["host"],
                "lastSeen": state["lastSeen"], "mode": mode}


def write_message(stream: BinaryIO, payload: dict) -> None:
    stream.write(json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii") + b"\n")
    stream.flush()


def observe_stdio(observer: Observer, incoming: BinaryIO, outgoing: BinaryIO, interval: float) -> int:
    raw = incoming.readline(1024)
    try:
        hello = json.loads(raw)
        token = hello.get("token") if isinstance(hello, dict) and hello.get("type") == "hello" else None
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
            return 2
        proof = hmac.new(bytes.fromhex(token), HELLO_CONTEXT, hashlib.sha256).hexdigest()
    except (ValueError, RecursionError):
        return 2
    write_message(outgoing, {"schema": 1, "type": "hello", "proof": proof})
    stopped = threading.Event()

    def await_eof() -> None:
        try:
            incoming.read(1)
        except OSError:
            pass
        finally:
            stopped.set()

    threading.Thread(target=await_eof, daemon=True, name="nexus-parent").start()
    previous = None
    heartbeat = 0.0
    while not stopped.is_set():
        result = observer.scan()
        comparable = {key: value for key, value in result.items() if key != "timestamp"}
        now = time.monotonic()
        if comparable != previous or now >= heartbeat:
            write_message(outgoing, result)
            previous = comparable
            heartbeat = now + 5.0
        stopped.wait(interval)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only, sanitized EOS evidence observer")
    parser.add_argument("--workspace", type=Path, action="append", default=[])
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--eos-home", type=Path, default=Path(os.environ.get("EOS_HOME") or Path.home() / ".eos"))
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--interval", type=float, default=0.75)
    args = parser.parse_args()
    try:
        engine = local_path(args.engine)
        eos_home = local_path(args.eos_home)
        if args.stdio:
            interval = min(5, max(0.25, args.interval))
            return observe_stdio(Observer(engine, eos_home, args.workspace), sys.stdin.buffer, sys.stdout.buffer, interval)
        if len(args.workspace) != 1:
            parser.error("snapshot mode requires exactly one --workspace")
        print(json.dumps({"schema": 1, "source": "local-evidence", "registries": read_registry(engine),
                          "recorded": read_events(args.workspace[0]), "live": "unknown"}, ensure_ascii=True))
        return 0
    except BrokenPipeError:
        return 0
    except (OSError, ValueError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
