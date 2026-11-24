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


def temp_name(path: str) -> str:
    """A temporary name no other writer uses. A shared `<path>.tmp` made two
    tool calls of one session crash each other."""
    return f"{path}.{os.getpid()}.{os.urandom(3).hex()}.tmp"


def replace(tmp: str, path: str, timeout: float = 10.0) -> None:
    """`os.replace`, waiting for a reader or scanner that has the target open:
    Windows refuses the rename for those milliseconds."""
    deadline = time.time() + timeout
    delay = 0.01
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.time() >= deadline:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def open_text(path: str, encoding: str = "utf-8-sig", timeout: float = 5.0):
    """Open for reading, waiting out a concurrent `replace`."""
    deadline = time.time() + timeout
    while True:
        try:
            return open(path, encoding=encoding)
        except PermissionError:
            if time.time() >= deadline or os.path.isdir(path):
                raise
            time.sleep(0.02)


def pid_alive(pid) -> bool:
    """False only when the process is known to be gone."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            pass
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87
    code = wintypes.DWORD()
    ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel.CloseHandle(handle)
    return not ok or code.value == 259


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


# ------------------------------------------------------- unregistered projects
def auto_project_id(directory: str) -> str:
    """A stable id for a folder nobody registered. A name with no usable ASCII
    letter gets a digest, so it is never empty and two names never share one."""
    import hashlib
    import re
    name = os.path.basename(_abs(directory))
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._")
    if not name.isascii():
        slug = f"{slug[:40]}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}".strip("-")
    return ("auto-" + slug)[:64] if slug else ""


def too_broad(directory: str) -> bool:
    """A drive, the profile or a shared container is a place projects live in,
    never a project: treating it as one would authorise writes across it."""
    def key(path: str) -> str:
        return os.path.normcase(os.path.realpath(path))
    current = key(directory)
    home = os.path.expanduser("~")
    if os.path.dirname(current) == current:
        return True
    if key(home) == current or key(home).startswith(current.rstrip("\\/") + os.sep):
        return True
    broad = [os.path.join(home, item) for item in
             ("Desktop", "Documents", "Downloads", "OneDrive", "AppData", "AppData/Local",
              "AppData/Roaming", "AppData/Local/Temp", ".config", ".eos")]
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "TEMP", "TMP",
                 "PUBLIC", "TMPDIR"):
        if os.environ.get(name):
            broad.append(os.environ[name])
    if os.name != "nt":
        broad += ["/tmp", "/usr", "/etc", "/opt", "/var", "/home"]
    system = [key(os.environ[n]) for n in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)")
              if os.environ.get(n)]
    return (current in {key(item) for item in broad}
            or any(current.startswith(item + os.sep) for item in system))


def is_hub(workspace: str) -> bool:
    """A workspace with its own project registry contains projects, unless the
    registry says the workspace itself is one."""
    import json
    root = _abs(workspace)
    try:
        with open(os.path.join(root, ".agents", "registry", "PROJECT_PROFILES.json"),
                  encoding="utf-8-sig") as handle:
            profiles = json.load(handle).get("profiles") or []
    except (OSError, ValueError, AttributeError):
        return False
    return not any(_abs(os.path.join(root, str(item.get("path") or "."))) == root
                   for item in profiles)


def hub_child(workspace: str, path: str) -> str:
    """The first-level folder of a hub that owns `path`, or ""."""
    root, absolute = _abs(workspace), _abs(path)
    if not is_hub(root) or not absolute.startswith(root + os.sep):
        return ""
    parts = os.path.relpath(absolute, root).split(os.sep)
    child = os.path.join(root, parts[0])
    if parts[0].startswith(".") or (len(parts) == 1 and not os.path.isdir(child)):
        return ""
    return child


def auto_project_root(project: str, workspace: str) -> str:
    """The folder an `auto-` id names. A hub child may not exist yet: the first
    write creates it."""
    root = _abs(workspace)
    if not is_hub(root) or auto_project_id(root) == project:
        return root
    try:
        names = os.listdir(root)
    except OSError:
        names = []
    for name in sorted(names):
        child = os.path.join(root, name)
        if not name.startswith(".") and os.path.isdir(child) and auto_project_id(child) == project:
            return child
    return os.path.join(root, project[len("auto-"):])


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
