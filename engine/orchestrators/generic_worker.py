#!/usr/bin/env python3
"""Generic EOS orchestrator worker template.

Implements eos-orchestrator/1 protocol: reads JSON task from stdin, writes
JSON result to stdout. Reserve slots/steps before doing work.

To create a provider-specific adapter, copy this file and replace the
execute_task() function with provider-specific logic.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../tools")
import _eos_paths as ep  # noqa: E402
import orchestrator_runtime as rt  # noqa: E402

PROTOCOL = rt.PROTOCOL


def execute_task(task: dict) -> dict:
    """Replace this with provider-specific logic.

    The generic worker does NOT claim success. It returns PARTIAL with a
    message that the adapter must be implemented. A real adapter must
    override this function and return PASS only when work actually completed.
    """
    return {
        "protocol": PROTOCOL,
        "ok": False,
        "status": "PARTIAL",
        "agent": str(task.get("agent", "worker"))[:80],
        "detail": "generic worker: adapter not implemented; override execute_task()",
        "children": [],
        "refusals": [],
    }


def main() -> int:
    # Reserve slots and steps
    run_dir = os.environ.get("EOS_ORCH_RUN", "")
    max_agents = int(os.environ.get("EOS_ORCH_MAX_AGENTS", "1"))
    max_steps = int(os.environ.get("EOS_ORCH_MAX_STEPS", "1"))
    depth = int(os.environ.get("EOS_ORCH_DEPTH", "1"))
    max_depth = int(os.environ.get("EOS_ORCH_MAX_DEPTH", "1"))

    if depth > max_depth:
        print(json.dumps({"protocol": PROTOCOL, "ok": False, "status": "REFUSED",
                          "refusals": [{"bound": "max_depth", "detail": f"depth {depth} > {max_depth}"}]}))
        return 0

    slot = rt.reserve(run_dir, "slots", max_agents)
    step = rt.reserve(run_dir, "steps", max_steps)
    if slot is None or step is None:
        bound = "max_agents" if slot is None else "max_steps"
        print(json.dumps({"protocol": PROTOCOL, "ok": False, "status": "REFUSED",
                          "refusals": [{"bound": bound, "detail": f"{bound} exhausted"}]}))
        return 0

    try:
        raw = sys.stdin.read()
        task = json.loads(raw or "{}")
        result = execute_task(task)
        result["depth"] = depth
        # Write worker record
        workers_dir = os.path.join(run_dir, "workers")
        os.makedirs(workers_dir, exist_ok=True)
        worker_file = os.path.join(workers_dir, f"{slot}.json")
        rt.write_json(worker_file, result)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"protocol": PROTOCOL, "ok": False, "status": "ERROR",
                          "detail": str(exc)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
