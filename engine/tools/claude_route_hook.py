#!/usr/bin/env python3
"""claude_route_hook.py — UserPromptSubmit adapter for Claude Code (P0 fix).

Not a session manager, not a new state machine: a thin wrapper that calls the
existing `.agents/router/route_task.py` on every prompt and injects its result
as additionalContext, so routing happens BEFORE the model acts instead of
depending on the model remembering to run it (AGENTS.md SS6 was advisory-only —
this makes it mechanical, inside Claude Code's own hook contract).

Mode (env FABLE_ROUTE_HOOK_MODE, default "warn"):
  warn    - router failure/timeout -> exit 0 with a visible warning context,
            prompt is NEVER blocked. This is the only mode wired up right now.
  enforce - (not implemented yet) would exit 2 on router failure per the
            soak-then-flip plan; do not enable until failure/timeout count
            over a real usage window is proven to be 0.

Scope guard: this hook is registered at user level (~/.claude/settings.json),
so it fires for EVERY Claude Code prompt on this machine, including projects
that have nothing to do with this control plane. If cwd falls outside this
workspace's parent directory, the hook is a silent no-op (no stdout at all) —
it must never inject noise into unrelated repos.
"""
import hashlib
import json
import os
import subprocess
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))          # .agents/tools
_WORKSPACE_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))   # workspace root
_ROUTER = os.path.join(_WORKSPACE_ROOT, ".agents", "router", "route_task.py")
_BOUNDARY = os.path.dirname(_WORKSPACE_ROOT)                       # parent of the workspace
_TELEMETRY = os.path.join(_WORKSPACE_ROOT, ".agents", "router", "route_hook_telemetry.jsonl")
_MODE = os.environ.get("FABLE_ROUTE_HOOK_MODE", "warn").strip().lower()
_SUBPROCESS_TIMEOUT_SEC = 5.0


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def _in_scope(cwd):
    if not cwd:
        return False
    try:
        cwd_n = _norm(os.path.abspath(cwd))
    except Exception:
        return False
    boundary_n = _norm(_BOUNDARY)
    return cwd_n == boundary_n or cwd_n.startswith(boundary_n + os.sep)


def _log_telemetry(record):
    record["ts"] = time.time()
    try:
        os.makedirs(os.path.dirname(_TELEMETRY), exist_ok=True)
        with open(_TELEMETRY, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=True) + "\n")
    except Exception:
        pass  # telemetry is best-effort, never blocks the hook


def _emit(additional_context):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    }, ensure_ascii=False))


def _summarize(route):
    resolved = route.get("resolved", {}) or {}
    risk = route.get("risk", {}) or {}
    policy = risk.get("policy", {}) or {}
    skills = route.get("skills", {}) or {}
    fable = route.get("fable_5_1_signal", {}) or {}
    lines = ["[FABLE ROUTE]"]
    lines.append("project: {}".format(resolved.get("project_id") or "none"))
    lines.append("risk: {} (gate={})".format(risk.get("level", "?"), policy.get("gate", "?")))
    if policy.get("gate") and policy.get("gate") != "auto":
        lines.append("policy: {}".format(policy.get("action", "")))
    lines.append("kg_required: {}".format(route.get("kg_required", False)))
    if fable.get("active"):
        lines.append("fable-5-1: active ({})".format(fable.get("reason", "")))
    primary = skills.get("primary") or []
    if primary:
        lines.append("primary skills: {}".format(", ".join(primary)))
    if resolved.get("project_id") is None:
        lines.append("note: no known project matched -> consider new_project.py route (AGENTS.md SS11)")
    return "\n".join(lines)


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        # Can't even parse the hook's own input — fail-open, silent (nothing
        # useful to report, and this is a Claude Code contract issue, not a
        # router issue).
        sys.exit(0)

    prompt = payload.get("prompt") or ""
    cwd = payload.get("cwd") or ""

    if not _in_scope(cwd):
        sys.exit(0)  # out-of-scope project: stay silent, no noise

    if not prompt.strip():
        sys.exit(0)

    prompt_sig = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    t0 = time.monotonic()
    status = "OK"
    route = None
    err_reason = None

    try:
        proc = subprocess.run(
            [sys.executable, _ROUTER, "--task", prompt, "--cwd", cwd],
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            status = "FAILED"
            err_reason = "router exit {}: {}".format(proc.returncode, (proc.stderr or proc.stdout)[:300])
        else:
            route = json.loads(proc.stdout)
            if "error" in route:
                status = "FAILED"
                err_reason = route["error"]
    except subprocess.TimeoutExpired:
        status = "TIMEOUT"
        err_reason = "router exceeded {}s".format(_SUBPROCESS_TIMEOUT_SEC)
    except Exception as exc:
        status = "FAILED"
        err_reason = "{}: {}".format(type(exc).__name__, exc)

    latency_ms = round((time.monotonic() - t0) * 1000, 1)

    telemetry = {
        "route_status": status,
        "route_latency_ms": latency_ms,
        "prompt_signature": prompt_sig,
        "mode": _MODE,
    }
    if route:
        telemetry["project"] = (route.get("resolved") or {}).get("project_id")
        telemetry["primary_skills"] = (route.get("skills") or {}).get("primary")
        telemetry["risk_level"] = (route.get("risk") or {}).get("level")
    if err_reason:
        telemetry["reason"] = err_reason
    _log_telemetry(telemetry)

    if status == "OK":
        _emit(_summarize(route))
        sys.exit(0)

    # warn mode (only mode implemented): never block, always visible.
    _emit(
        "[FABLE ROUTER WARNING]\n"
        "Routing failed ({}). Proceeding without route.\n"
        "reason={}".format(status, err_reason)
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
