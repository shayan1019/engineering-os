#!/usr/bin/env python3
"""Single source of truth for EOS path resolution.

EOS ships in two shapes and both must work identically:

  workspace (development)   <workspace>/.agents/{tools,router,registry,core,eos}
  repo / installed product  <prefix>/engine/{tools,router,registry,core,agents}

`ENGINE_ROOT` is the directory that directly contains ``tools/``, ``router/``
and ``registry/``. Defining the root that way means one rule covers both layouts,
and an installed copy stops depending on a particular directory name.

Everything that is *not* program files is resolved here too, so no tool has to
guess:

  registry, agents, selftest   -> inside ENGINE_ROOT (versioned, read-only)
  evidence, baselines, state   -> under EOS_HOME (user data, survives upgrade)
  the project being operated on -> WORKSPACE (explicit, env, or cwd)

An existing development workspace keeps its evidence and baselines where they
already are: silently relocating a record that a gate artifact points at would
break verification, so the legacy location wins when it exists.
"""
from __future__ import annotations

import os
import time
from typing import Optional

ENGINE_TOKEN = "%ENGINE%"


def lock_handle(fd: int) -> None:
    """Take a non-blocking exclusive OS lock on one byte of `fd`.

    The OS releases it when the process exits, so a crash cannot leave a lock
    that a later session must guess about. `-1` byte from the current position
    is not portable; locking byte 0 is.
    """
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_handle(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def acquire_file_lock(path: str, timeout: float = 15.0) -> int:
    """Open `path` and hold an exclusive OS lock. Returns the file descriptor.

    The lock file is never deleted: deleting a lock path that another process
    has already opened would let a third process create and lock a new file at
    the same time.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.time() + max(0.0, float(timeout))
    while True:
        try:
            lock_handle(fd)
            return fd
        except OSError:
            if time.time() >= deadline:
                os.close(fd)
                raise TimeoutError(f"lock is held: {path}")
            time.sleep(0.05)


def is_control_plane(rel: str) -> bool:
    """True when the path is EOS control-plane state, at any depth.

    A workspace can contain independent projects; `engineering-os/.agents/evidence`
    is as much control-plane state as `.agents/evidence` at the workspace root.
    Matching by segment also covers Windows separators and `./` prefixes.
    """
    parts = [part for part in str(rel or "").replace("\\", "/").split("/")
             if part and part != "."]
    for index, part in enumerate(parts):
        following = parts[index + 1] if index + 1 < len(parts) else ""
        if part == ".agents" and following in ("evidence", "state"):
            return True
        if part == ".opencode" and following == "plugin":
            return True
        if part == ".eos":
            return True
    return False


def _abs(path: str) -> str:
    return os.path.abspath(path)


TOOLS_DIR = os.path.dirname(_abs(__file__))
ENGINE_ROOT = os.path.dirname(TOOLS_DIR)


def _first_dir(*candidates: str) -> str:
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return candidate
    return candidates[-1] if candidates else ""


# ------------------------------------------------------------- program files
REGISTRY = os.path.join(ENGINE_ROOT, "registry")
EOS_DIR = os.path.join(ENGINE_ROOT, "eos")
AGENTS_CANONICAL = _first_dir(
    os.path.join(EOS_DIR, "agents"), os.path.join(ENGINE_ROOT, "agents")
)
SELFTEST_DIR = _first_dir(
    os.path.join(EOS_DIR, "selftest"), os.path.join(ENGINE_ROOT, "selftest")
)

# ------------------------------------------------------------------ user data
EOS_HOME = os.environ.get("EOS_HOME") or os.path.join(os.path.expanduser("~"), ".eos")
STATE_DIR = os.path.join(EOS_HOME, "state")
USER_EVIDENCE = os.path.join(EOS_HOME, "evidence")


def evidence_dir(name: str, legacy_rel: str) -> str:
    override = os.environ.get("EOS_EVIDENCE_DIR")
    if override:
        return os.path.join(_abs(override), name)
    legacy = os.path.join(ENGINE_ROOT, legacy_rel)
    if os.path.isdir(legacy):
        return legacy
    return os.path.join(USER_EVIDENCE, name)


GATE_EVIDENCE = evidence_dir("gates", os.path.join("evidence", "gates"))
PROMOTION_EVIDENCE = evidence_dir("promotions", os.path.join("evidence", "promotions"))
GRAPH_EVIDENCE = evidence_dir("graph", os.path.join("evidence", "graph"))


def backups_dir() -> str:
    """Baseline snapshots. A gate without a baseline refuses to run, so this
    must find one wherever it legitimately lives, and never invent a path."""
    for candidate in (
        os.environ.get("EOS_BASELINES"),
        os.path.join(ENGINE_ROOT, "backups"),
        os.path.join(EOS_HOME, "baselines"),
    ):
        if not candidate or not os.path.isdir(candidate):
            continue
        try:
            names = os.listdir(candidate)
        except OSError:
            continue
        if any(n.startswith("baseline_") for n in names):
            return candidate
    return os.path.join(EOS_HOME, "baselines")


BACKUPS = backups_dir()


# ------------------------------------------------------- the project in scope
def resolve_workspace(explicit: Optional[str] = None) -> str:
    """The project tree an operation is about: never the engine, never EOS_HOME."""
    if explicit:
        return _abs(explicit)
    env = os.environ.get("EOS_WORKSPACE")
    if env:
        return _abs(env)
    return os.getcwd()


def resolve_project_path(path: str, workspace: str) -> str:
    """GATES.json paths are workspace-relative, except that `%ENGINE%` marks a
    fixture that ships with the product (the eos self-test suites)."""
    if not path:
        return workspace
    if path.startswith(ENGINE_TOKEN):
        rest = path[len(ENGINE_TOKEN):].lstrip("/\\")
        return os.path.normpath(os.path.join(ENGINE_ROOT, rest))
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(workspace, path))


def store_path(path: str, workspace: str) -> str:
    """Record a path portably: relative when inside the workspace, absolute when
    it is not (evidence legitimately lives under EOS_HOME)."""
    absolute = _abs(path)
    try:
        rel = os.path.relpath(absolute, workspace)
    except ValueError:
        return absolute.replace(os.sep, "/")
    if rel.startswith(".."):
        return absolute.replace(os.sep, "/")
    return rel.replace(os.sep, "/")


def load_stored_path(stored: str, workspace: str) -> str:
    if not stored:
        return ""
    if os.path.isabs(stored):
        return os.path.normpath(stored)
    return os.path.normpath(os.path.join(workspace, stored))
