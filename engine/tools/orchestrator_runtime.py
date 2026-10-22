#!/usr/bin/env python3
"""Bounded execution of a nested agent tree.

`orchestrator_registry.py` answers "is there a contract and a runtime". This
module answers the harder question: "did the nested delegation really run, and
did it stay inside its declared bounds". A provider is executable only when its
contract names `execution.protocol == eos-orchestrator/1`; a missing runtime or
a missing execution contract is `ADAPTER_ONLY` and nothing is spawned.

The protocol is deliberately small. The parent starts a worker process with a
JSON task on stdin and reads one JSON result from stdout. A worker may request
children; it starts them with the same protocol, so delegation is genuinely
nested (a child of a child), not a flat fan-out disguised as one. Every worker
must reserve a *step* and an *agent slot* in append-only ledgers under the run
directory before doing work; the parent verifies the ledgers afterwards. The
parent also kills the whole process tree on timeout or cancellation, so a
misbehaving provider cannot outlive its budget.

Enforced mechanically
---------------------
  max_depth     a worker at depth > max_depth refuses before doing any work
  max_agents    slots are reserved under a file lock; the (n+1)th is refused
  max_steps     the same ledger limits total worker executions, not just fan-out
  timeout       absolute deadline; the process tree is killed, result TIMEOUT
  cancellation  a cancel file is polled; result CANCELLED
  workspace     every requested cwd and write path must resolve inside the
                declared workspace; escape is refused before touching disk
  evidence      one JSON record per worker plus the aggregated run record

Limits that are *not* claimed: host permissions are not an OS sandbox, and a
worker that ignores the env/ledger can still write inside the workspace before
the parent kills it. The run record says `network_enforced: false` instead of
pretending otherwise.

Commands:
  run     --provider <id> --plan <file> [--workspace DIR] [--out DIR]
  worker  (internal protocol entry point; reads the task from stdin)
  selftest
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402
import orchestrator_registry as registry  # noqa: E402

PROTOCOL = "eos-orchestrator/1"
SCHEMA = 1
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600
MAX_SLEEP = 60
SAFE_ENV_KEYS = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
                 "HOME", "USERPROFILE", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS",
                 "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL")
IS_WINDOWS = os.name == "nt"


class OrchestratorError(Exception):
    pass


# ---------------------------------------------------------------- utilities
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: str):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path: str, value) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def inside(workspace: str, candidate: str) -> bool:
    workspace = os.path.normcase(os.path.realpath(workspace))
    candidate = os.path.normcase(os.path.realpath(candidate))
    try:
        return os.path.commonpath([workspace, candidate]) == workspace
    except ValueError:
        return False


def safe_rel(value, field: str, limit: int = 240) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OrchestratorError(f"{field} must be a non-empty string")
    if len(value) > limit:
        raise OrchestratorError(f"{field} is longer than {limit} characters")
    return value


@contextlib.contextmanager
def file_lock(path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path, "a+b")
    try:
        if IS_WINDOWS:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if IS_WINDOWS:
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def reserve(run_dir: str, kind: str, limit: int):
    """Reserve one of `limit` slots. Returns a token or None when exhausted."""
    directory = os.path.join(run_dir, kind)
    os.makedirs(directory, exist_ok=True)
    with file_lock(os.path.join(run_dir, kind + ".lock")):
        used = [n for n in os.listdir(directory) if not n.startswith(".")]
        if len(used) >= limit:
            return None
        token = f"{len(used) + 1:03d}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        with open(os.path.join(directory, token), "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
        return token


def count(run_dir: str, kind: str) -> int:
    directory = os.path.join(run_dir, kind)
    if not os.path.isdir(directory):
        return 0
    return len([n for n in os.listdir(directory) if not n.startswith(".")])


def worker_env(run_dir: str, depth: int, max_depth: int, workspace: str,
               deadline: float, max_agents: int, max_steps: int) -> dict:
    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    env.update({
        "EOS_ORCH_PROTOCOL": PROTOCOL,
        "EOS_ORCH_RUN": run_dir,
        "EOS_ORCH_DEPTH": str(depth),
        "EOS_ORCH_MAX_DEPTH": str(max_depth),
        "EOS_ORCH_MAX_AGENTS": str(max_agents),
        "EOS_ORCH_MAX_STEPS": str(max_steps),
        "EOS_ORCH_WORKSPACE": workspace,
        "EOS_ORCH_DEADLINE": f"{deadline:.3f}",
        "EOS_ORCH_CANCEL": os.path.join(run_dir, "CANCEL"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def spawn(argv, task: dict, env: dict, cwd: str, timeout: float):
    """Start one worker. Returns (result, stderr, timed_out, pid)."""
    payload = json.dumps(task, ensure_ascii=True).encode("utf-8")
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=cwd, env=env, **kwargs)
    except FileNotFoundError as exc:
        return {"protocol": PROTOCOL, "ok": False, "status": "ADAPTER_ONLY",
                "detail": f"worker not found: {exc}"}, "", False, None
    timed_out = False
    try:
        out, err = proc.communicate(payload, timeout=max(0.2, timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
    text = (out or b"").decode("utf-8", "replace").strip()
    stderr = (err or b"").decode("utf-8", "replace").strip()[-4000:]
    if timed_out:
        return ({"protocol": PROTOCOL, "ok": False, "status": "TIMEOUT",
                 "detail": f"worker exceeded {timeout:.1f}s and was killed"}, stderr, True, proc.pid)
    if not text:
        return ({"protocol": PROTOCOL, "ok": False, "status": "ERROR",
                 "detail": f"worker produced no result (exit {proc.returncode})"}, stderr,
                False, proc.pid)
    try:
        result = json.loads(text.splitlines()[-1])
    except ValueError:
        return ({"protocol": PROTOCOL, "ok": False, "status": "ERROR",
                 "detail": "worker output was not JSON"}, stderr, False, proc.pid)
    return result, stderr, False, proc.pid


# ------------------------------------------------------------------- worker
def worker_main(task_file: str | None) -> int:
    run_dir = os.environ.get("EOS_ORCH_RUN", "")
    result = {
        "protocol": PROTOCOL, "ok": False, "status": "ERROR", "agent": "",
        "pid": os.getpid(), "depth": int(os.environ.get("EOS_ORCH_DEPTH", "0") or 0),
        "started_at": utc_now(), "refusals": [], "children": [], "duration_seconds": 0.0,
    }
    started = time.time()
    try:
        if os.environ.get("EOS_ORCH_PROTOCOL") != PROTOCOL:
            raise OrchestratorError("missing or wrong EOS_ORCH_PROTOCOL")
        if not run_dir or not os.path.isdir(run_dir):
            raise OrchestratorError("missing run directory")
        raw = open(task_file, encoding="utf-8").read() if task_file else sys.stdin.read()
        task = json.loads(raw or "{}")
        if not isinstance(task, dict):
            raise OrchestratorError("task is not an object")
        result["agent"] = str(task.get("agent") or "worker")[:80]

        depth = int(os.environ.get("EOS_ORCH_DEPTH", "1"))
        max_depth = int(os.environ.get("EOS_ORCH_MAX_DEPTH", "1"))
        max_agents = int(os.environ.get("EOS_ORCH_MAX_AGENTS", "1"))
        max_steps = int(os.environ.get("EOS_ORCH_MAX_STEPS", "1"))
        deadline = float(os.environ.get("EOS_ORCH_DEADLINE", "0"))
        workspace = os.environ.get("EOS_ORCH_WORKSPACE", "")
        cancel_path = os.environ.get("EOS_ORCH_CANCEL", "")

        if depth > max_depth:
            result.update({"status": "REFUSED", "detail": "max_depth",
                           "refusals": [{"bound": "max_depth", "depth": depth,
                                         "limit": max_depth}]})
            return finish_worker(result, run_dir, started)

        if reserve(run_dir, "steps", max_steps) is None:
            result.update({"status": "REFUSED", "detail": "max_steps",
                           "refusals": [{"bound": "max_steps", "limit": max_steps}]})
            return finish_worker(result, run_dir, started)
        if reserve(run_dir, "slots", max_agents) is None:
            result.update({"status": "REFUSED", "detail": "max_agents",
                           "refusals": [{"bound": "max_agents", "limit": max_agents}]})
            return finish_worker(result, run_dir, started)

        if cancel_path and os.path.exists(cancel_path):
            result.update({"status": "CANCELLED", "detail": "cancel requested before start"})
            return finish_worker(result, run_dir, started)
        if deadline and time.time() >= deadline:
            result.update({"status": "TIMEOUT", "detail": "deadline reached before start"})
            return finish_worker(result, run_dir, started)

        cwd = workspace
        requested_cwd = task.get("cwd")
        if requested_cwd:
            cwd = os.path.normpath(os.path.join(workspace, str(requested_cwd)))
            if not inside(workspace, cwd):
                result.update({"status": "REFUSED", "detail": "workspace_escape",
                               "refusals": [{"bound": "workspace", "requested": str(requested_cwd)}]})
                return finish_worker(result, run_dir, started)
        os.chdir(cwd)
        result["cwd"] = cwd

        sleep_for = min(max(0.0, float(task.get("sleep_seconds") or 0)), MAX_SLEEP)
        sleep_until = time.time() + sleep_for
        while time.time() < sleep_until:
            if cancel_path and os.path.exists(cancel_path):
                result.update({"status": "CANCELLED", "detail": "cancel requested"})
                return finish_worker(result, run_dir, started)
            if deadline and time.time() >= deadline:
                result.update({"status": "TIMEOUT", "detail": "deadline reached"})
                return finish_worker(result, run_dir, started)
            time.sleep(min(0.1, max(0.0, sleep_until - time.time())))

        writes = task.get("write") or []
        if isinstance(writes, str):
            writes = [writes]
        written = []
        for rel in writes:
            rel = safe_rel(rel, "write")
            target = os.path.normpath(os.path.join(cwd, rel))
            if not inside(workspace, target):
                result.update({"status": "REFUSED", "detail": "workspace_escape",
                               "refusals": [{"bound": "workspace", "write": rel}],
                               "written": written})
                return finish_worker(result, run_dir, started)
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(str(task.get("content") or f"{result['agent']}\n"))
            written.append(os.path.relpath(target, workspace).replace(os.sep, "/"))
        result["written"] = written

        children = task.get("children") or []
        if not isinstance(children, list):
            raise OrchestratorError("children must be a list")
        if len(children) > max_agents:
            result["refusals"].append({"bound": "max_agents", "requested_children": len(children),
                                       "limit": max_agents})
        for index, child in enumerate(children[:max_agents]):
            if not isinstance(child, dict):
                result["children"].append({"status": "ERROR", "detail": "child is not an object"})
                continue
            child = dict(child)
            child.setdefault("agent", f"{result['agent']}.child{index + 1}")
            env = worker_env(run_dir, depth + 1, max_depth, workspace, deadline,
                             max_agents, max_steps)
            argv = [sys.executable, os.path.abspath(__file__), "worker"]
            remaining = max(0.2, deadline - time.time()) if deadline else DEFAULT_TIMEOUT
            child_result, child_err, _, child_pid = spawn(argv, child, env, cwd, remaining)
            child_result["pid"] = child_result.get("pid") or child_pid
            child_result["stderr"] = child_err[-1000:]
            result["children"].append(child_result)

        child_ok = all(c.get("ok") for c in result["children"])
        refusals = list(result["refusals"])
        for child in result["children"]:
            refusals.extend(child.get("refusals") or [])
        result["refusals"] = refusals
        timed_out = any(c.get("status") in ("TIMEOUT", "CANCELLED") for c in result["children"])
        if timed_out:
            result.update({"status": "TIMEOUT" if any(
                c.get("status") == "TIMEOUT" for c in result["children"]) else "CANCELLED",
                "detail": "a child did not finish"})
        elif refusals or not child_ok:
            result.update({"status": "PARTIAL", "detail": "a bound refused or a child failed"})
        else:
            result.update({"status": "OK", "ok": True})
        return finish_worker(result, run_dir, started)
    except Exception as exc:  # a worker must always answer in protocol shape
        result.update({"status": "ERROR", "detail": f"{type(exc).__name__}: {exc}",
                       "traceback": traceback.format_exc()[-2000:]})
        return finish_worker(result, run_dir, started)


def finish_worker(result: dict, run_dir: str, started: float) -> int:
    result["duration_seconds"] = round(time.time() - started, 3)
    try:
        if run_dir:
            folder = os.path.join(run_dir, "workers")
            os.makedirs(folder, exist_ok=True)
            name = f"{result.get('depth', 0):02d}-{os.getpid()}-{uuid.uuid4().hex[:6]}.json"
            write_json(os.path.join(folder, name), result)
    finally:
        print(json.dumps(result, ensure_ascii=True))
    return 0


# ------------------------------------------------------------------- parent
def contract_execution(provider: dict):
    problems = registry.contract_problems(provider)
    if problems:
        return None, "INVALID", "; ".join(problems)
    if not (provider.get("permissions") or {}).get("execute"):
        return None, "ADAPTER_ONLY", "the contract does not grant execute permission"
    execution = provider.get("execution") or {}
    if execution.get("protocol") != PROTOCOL:
        return None, "ADAPTER_ONLY", (
            f"no execution contract ({PROTOCOL}); the runtime was not asked to delegate")
    if execution.get("adapter") not in (None, "json-stdin"):
        return None, "ADAPTER_ONLY", f"unsupported execution adapter {execution.get('adapter')}"
    argv = list((provider.get("runtime") or {}).get("argv") or [])
    if not argv:
        return None, "ADAPTER_ONLY", "no runtime argv; cannot start a worker"
    if not os.path.isabs(argv[0]) and shutil.which(argv[0]) is None:
        return None, "ADAPTER_ONLY", f"'{argv[0]}' is not on PATH"
    if os.path.isabs(argv[0]) and not os.path.isfile(argv[0]):
        return None, "ADAPTER_ONLY", f"'{argv[0]}' does not exist"
    return argv, "READY", ""


def validate_plan(plan: dict, workspace: str):
    if not isinstance(plan, dict):
        raise OrchestratorError("plan is not an object")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise OrchestratorError("plan.tasks must be a non-empty list")
    if len(tasks) > 64:
        raise OrchestratorError("plan.tasks is limited to 64 root tasks")
    for task in tasks:
        if not isinstance(task, dict):
            raise OrchestratorError("every task must be an object")
        safe_rel(task.get("agent") or "worker", "task.agent", 80)
        if "write" in task:
            writes = task["write"] if isinstance(task["write"], list) else [task["write"]]
            for rel in writes:
                safe_rel(rel, "task.write")
    return tasks


def execute(provider: dict, plan: dict, workspace: str, out_dir: str,
            cancel_after: float | None = None) -> dict:
    started = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    result = {
        "schema": SCHEMA, "kind": "orchestrator_run", "run_id": run_id,
        "provider": provider.get("id"), "protocol": PROTOCOL,
        "created_at": utc_now(), "workspace": os.path.abspath(workspace),
        "verdict": "INVALID", "detail": "", "results": [], "bounds": {},
        "evidence_path": "", "network_enforced": False,
    }
    argv, state, detail = contract_execution(provider)
    if state != "READY":
        result.update({"verdict": state, "detail": detail})
        return result
    if not os.path.isdir(workspace):
        result.update({"verdict": "INVALID", "detail": f"workspace does not exist: {workspace}"})
        return result
    try:
        tasks = validate_plan(plan, workspace)
    except OrchestratorError as exc:
        result.update({"verdict": "INVALID", "detail": str(exc)})
        return result

    bounds = provider.get("bounds") or {}
    max_depth = int(bounds.get("max_depth") or 1)
    max_agents = int(bounds.get("max_agents") or 1)
    max_steps = int(bounds.get("max_steps") or max(1, max_agents * max_depth))
    timeout = min(float(bounds.get("timeout_seconds") or DEFAULT_TIMEOUT), MAX_TIMEOUT,
                  float(plan.get("timeout_seconds") or MAX_TIMEOUT))
    timeout = max(1.0, timeout)
    if int(plan.get("max_depth") or max_depth) < max_depth:
        max_depth = int(plan["max_depth"])
    if len(tasks) > max_agents:
        result.update({"verdict": "REFUSED", "detail": "more root tasks than max_agents",
                       "bounds": {"max_depth": max_depth, "max_agents": max_agents,
                                  "max_steps": max_steps, "timeout_seconds": timeout}})
        return result

    run_dir = os.path.join(os.path.abspath(out_dir), run_id)
    os.makedirs(os.path.join(run_dir, "workers"), exist_ok=True)
    write_json(os.path.join(run_dir, "plan.json"), plan)
    deadline = time.time() + timeout
    if cancel_after is not None:
        def _cancel():
            time.sleep(max(0.0, float(cancel_after)))
            open(os.path.join(run_dir, "CANCEL"), "w").close()
        threading.Thread(target=_cancel, daemon=True).start()

    env = worker_env(run_dir, 1, max_depth, os.path.abspath(workspace), deadline,
                     max_agents, max_steps)
    # The provider's argv is the full worker command. `worker` is appended so a
    # provider cannot silently reuse a probe command as a delegation boundary.
    worker_argv = argv + ["worker"]
    processes = []
    for task in tasks:
        proc_result, stderr, killed, pid = spawn(worker_argv, task, env, workspace,
                                                 max(0.2, deadline - time.time()))
        proc_result["stderr"] = stderr[-2000:]
        proc_result["pid"] = proc_result.get("pid") or pid
        proc_result["killed"] = killed
        processes.append(proc_result)

    result["bounds"] = {"max_depth": max_depth, "max_agents": max_agents,
                        "max_steps": max_steps, "timeout_seconds": timeout,
                        "agents_reserved": count(run_dir, "slots"),
                        "steps_reserved": count(run_dir, "steps"),
                        "cancelled": os.path.exists(os.path.join(run_dir, "CANCEL"))}
    result["results"] = processes
    result["duration_seconds"] = round(time.time() - started, 3)
    result["evidence_path"] = os.path.join(run_dir, "result.json")

    workers = []
    workers_dir = os.path.join(run_dir, "workers")
    if os.path.isdir(workers_dir):
        for name in sorted(os.listdir(workers_dir)):
            if name.endswith(".json"):
                try:
                    workers.append(load_json(os.path.join(workers_dir, name)))
                except (OSError, ValueError):
                    pass
    result["workers"] = workers

    refusals = []
    for proc_result in processes:
        refusals.extend(proc_result.get("refusals") or [])
    statuses = [p.get("status") for p in processes]
    over_agents = count(run_dir, "slots") > max_agents
    over_steps = count(run_dir, "steps") > max_steps
    # A worker that refused *because* it was too deep is proof of enforcement,
    # not a violation: it never executed. Only an executed worker past the
    # limit counts.
    over_depth = any(int(w.get("depth") or 0) > max_depth
                     and w.get("status") != "REFUSED" for w in workers)
    bound_violation = over_agents or over_steps or over_depth
    if bound_violation:
        result["verdict"] = "FAIL"
        result["detail"] = "a worker exceeded a declared bound"
    elif any(s in ("TIMEOUT",) for s in statuses):
        result["verdict"] = "TIMEOUT"
    elif any(s in ("CANCELLED",) for s in statuses) or result["bounds"]["cancelled"]:
        result["verdict"] = "CANCELLED"
    elif any(s == "ADAPTER_ONLY" for s in statuses):
        result["verdict"] = "ADAPTER_ONLY"
    elif any(s in ("ERROR",) for s in statuses) or not all(p.get("ok") for p in processes):
        result["verdict"] = "PARTIAL" if (refusals or any(s == "PARTIAL" for s in statuses)) else "FAIL"
    else:
        result["verdict"] = "PASS"
    result["refusals"] = refusals
    executed_depths = [int(w.get("depth") or 0) for w in workers
                       if w.get("status") != "REFUSED"]
    result["ledger"] = {"slots": count(run_dir, "slots"), "steps": count(run_dir, "steps"),
                        "max_worker_depth": max([int(w.get("depth") or 0) for w in workers] or [0]),
                        "max_executed_depth": max(executed_depths or [0])}
    result["ok"] = result["verdict"] == "PASS"
    write_json(os.path.join(run_dir, "result.json"), result)
    result["evidence_path"] = os.path.join(run_dir, "result.json")
    return result


def resolve_provider(provider_id: str):
    providers, problems = registry.resolve_providers()
    match = next((p for p in providers if p.get("id") == provider_id), None)
    if not match:
        raise OrchestratorError(f"unknown provider: {provider_id}")
    return match, problems


def cmd_run(args) -> int:
    try:
        provider, _ = resolve_provider(args.provider)
        plan = load_json(args.plan)
    except (OrchestratorError, OSError, ValueError) as exc:
        print(f"INVALID: {exc}")
        return 2
    workspace = os.path.abspath(args.workspace or os.getcwd())
    out = os.path.abspath(args.out or os.path.join(ep.USER_EVIDENCE, "orchestrators"))
    result = execute(provider, plan, workspace, out)
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "PASS" else (2 if result["verdict"] == "ADAPTER_ONLY" else 1)


# ----------------------------------------------------------------- selftest
def fixture_provider(work: str, max_agents: int, max_depth: int, max_steps: int,
                     timeout: int = 20) -> dict:
    return {
        "id": "fixture",
        "class": "MULTI_AGENT_ORCHESTRATOR",
        "skill": "fixture",
        "runtime": {"argv": [sys.executable, os.path.abspath(__file__)], "timeout_seconds": timeout},
        "execution": {"protocol": PROTOCOL, "adapter": "json-stdin"},
        "delegation": {"method": "fixture", "entry_point": "orchestrator_runtime.py",
                       "specialists": ["a", "b", "c"]},
        "permissions": {"write": True, "network": False, "execute": True,
                        "outside_workspace": False},
        "side_effects": [],
        "cost": {"billing": "none"},
        "bounds": {"timeout_seconds": timeout, "max_depth": max_depth,
                   "max_agents": max_agents, "max_steps": max_steps},
        "evidence": {"dir": work, "format": "json"},
    }


def selftest() -> int:
    tmp = tempfile.mkdtemp(prefix="eos_orch_runtime_")
    failures = []
    results = []

    def case(name, fn):
        workspace = os.path.join(tmp, name)
        os.makedirs(workspace, exist_ok=True)
        try:
            passed, detail = fn(workspace)
        except Exception as exc:
            passed, detail = False, f"{type(exc).__name__}: {exc}"
        results.append({"case": name, "pass": passed, "detail": detail})
        if not passed:
            failures.append(f"{name}: {detail}")

    def delegation(workspace):
        out = os.path.join(workspace, "out")
        provider = fixture_provider(workspace, 5, 3, 10, 20)
        plan = {"tasks": [
            {"agent": "root-a", "write": ["a.txt"], "children": [
                {"agent": "child-b", "write": ["nested/b.txt"]}]},
            {"agent": "root-c", "write": ["c.txt"]},
        ]}
        result = execute(provider, plan, workspace, out)
        pids = {p.get("pid") for p in result.get("workers", []) if p.get("pid")}
        files = [os.path.isfile(os.path.join(workspace, p))
                 for p in ("a.txt", "nested/b.txt", "c.txt")]
        ok = (result["verdict"] == "PASS" and len(pids) >= 3 and all(files)
              and result["ledger"]["steps"] == 3 and result["ledger"]["max_worker_depth"] == 2)
        return ok, (f"verdict={result['verdict']} pids={len(pids)} steps="
                    f"{result['ledger'].get('steps')} depth={result['ledger'].get('max_worker_depth')}")

    def max_agents(workspace):
        out = os.path.join(workspace, "out")
        provider = fixture_provider(workspace, 1, 3, 10, 20)
        plan = {"tasks": [{"agent": "root", "children": [
            {"agent": "c1"}, {"agent": "c2"}]}]}
        result = execute(provider, plan, workspace, out)
        refused = [r for r in result.get("refusals", []) if r.get("bound") == "max_agents"]
        ok = bool(refused) and result["verdict"] in ("PARTIAL", "REFUSED") \
            and result["ledger"]["slots"] <= 1
        return ok, f"verdict={result['verdict']} refusals={len(refused)} slots={result['ledger']['slots']}"

    def max_depth(workspace):
        out = os.path.join(workspace, "out")
        provider = fixture_provider(workspace, 5, 1, 10, 20)
        plan = {"tasks": [{"agent": "root", "children": [{"agent": "too-deep"}]}]}
        result = execute(provider, plan, workspace, out)
        refused = [r for r in result.get("refusals", []) if r.get("bound") == "max_depth"]
        ok = bool(refused) and result["ledger"]["max_executed_depth"] <= 1
        return ok, f"verdict={result['verdict']} depth_refusals={len(refused)} executed_depth={result['ledger']['max_executed_depth']}"

    def max_steps(workspace):
        out = os.path.join(workspace, "out")
        provider = fixture_provider(workspace, 5, 3, 1, 20)
        plan = {"tasks": [{"agent": "root", "children": [{"agent": "child"}]}]}
        result = execute(provider, plan, workspace, out)
        refused = [r for r in result.get("refusals", []) if r.get("bound") == "max_steps"]
        ok = bool(refused) and result["ledger"]["steps"] <= 1
        return ok, f"verdict={result['verdict']} budget_refusals={len(refused)} steps={result['ledger']['steps']}"

    def timeout(workspace):
        out = os.path.join(workspace, "out")
        provider = fixture_provider(workspace, 3, 2, 5, 1)
        plan = {"tasks": [{"agent": "slow", "sleep_seconds": 20}]}
        started = time.time()
        result = execute(provider, plan, workspace, out)
        elapsed = time.time() - started
        statuses = [p.get("status") for p in result.get("results", [])]
        ok = ("TIMEOUT" in statuses or result["verdict"] == "TIMEOUT") and elapsed < 10
        return ok, f"verdict={result['verdict']} statuses={statuses} elapsed={elapsed:.1f}s"

    def cancel(workspace):
        out = os.path.join(workspace, "out")
        provider = fixture_provider(workspace, 3, 2, 5, 30)
        plan = {"tasks": [{"agent": "long", "sleep_seconds": 20}]}
        started = time.time()
        result = execute(provider, plan, workspace, out, cancel_after=0.6)
        elapsed = time.time() - started
        statuses = [p.get("status") for p in result.get("results", [])]
        ok = (result["verdict"] in ("CANCELLED",) or "CANCELLED" in statuses) and elapsed < 10
        return ok, f"verdict={result['verdict']} statuses={statuses} elapsed={elapsed:.1f}s"

    def escape(workspace):
        out = os.path.join(workspace, "out")
        provider = fixture_provider(workspace, 3, 2, 5, 10)
        outside = os.path.join(tmp, "outside-escape.txt")
        plan = {"tasks": [{"agent": "escape", "cwd": "../", "write": ["outside-escape.txt"]}]}
        result = execute(provider, plan, workspace, out)
        refused = [r for r in result.get("refusals", []) if r.get("bound") == "workspace"]
        ok = bool(refused) and not os.path.exists(outside)
        return ok, f"verdict={result['verdict']} escape_refusals={len(refused)} file={os.path.exists(outside)}"

    def adapter_only(workspace):
        out = os.path.join(workspace, "out")
        provider = fixture_provider(workspace, 3, 2, 5, 10)
        provider["runtime"]["argv"] = [os.path.join(workspace, "no-such-worker")]
        plan = {"tasks": [{"agent": "root"}]}
        result = execute(provider, plan, workspace, out)
        spawned = os.path.isdir(out)
        ok = result["verdict"] == "ADAPTER_ONLY" and not spawned
        return ok, f"verdict={result['verdict']} spawned={spawned}"

    def missing_contract(workspace):
        out = os.path.join(workspace, "out")
        provider = fixture_provider(workspace, 3, 2, 5, 10)
        provider.pop("execution")
        plan = {"tasks": [{"agent": "root"}]}
        result = execute(provider, plan, workspace, out)
        ok = result["verdict"] == "ADAPTER_ONLY" and "execution contract" in result["detail"]
        return ok, f"verdict={result['verdict']} detail={result['detail'][:60]}"

    case("real_nested_delegation", delegation)
    case("max_agents_refused", max_agents)
    case("max_depth_refused", max_depth)
    case("max_steps_refused", max_steps)
    case("timeout_killed", timeout)
    case("cancellation", cancel)
    case("workspace_escape_refused", escape)
    case("missing_runtime_adapter_only", adapter_only)
    case("missing_execution_contract", missing_contract)

    shutil.rmtree(tmp, ignore_errors=True)
    for row in results:
        print(f"{'PASS' if row['pass'] else 'FAIL'} {row['case']}: {row['detail']}")
    if failures:
        print("FAIL orchestrator runtime selftest")
        return 1
    print(f"PASS orchestrator runtime selftest: {len(results)} real process-tree cases")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="orchestrator_runtime.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--provider", required=True)
    r.add_argument("--plan", required=True)
    r.add_argument("--workspace")
    r.add_argument("--out")
    r.set_defaults(func=cmd_run)
    w = sub.add_parser("worker")
    w.add_argument("--task-file")
    w.set_defaults(func=lambda a: worker_main(a.task_file))
    t = sub.add_parser("selftest")
    t.set_defaults(func=lambda _a: selftest())
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
