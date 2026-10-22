#!/usr/bin/env python3
"""EOS orchestrator registry: discover, check and probe nested orchestrators.

An orchestrator is any provider that can delegate work to more than one agent.
EOS does not implement one and does not pretend to have one; it keeps a bounded
contract for each provider so that a task can be delegated without the caller
having to guess what the provider may do.

Capability classes (a name is not a capability)
-----------------------------------------------
  INSTRUCTION             prose guidance only; no execution surface
  TOOL_PROVIDER           ships tools (usually MCP) but no agents
  SPECIALIST_PROVIDER     ships agent definitions
  AGENT_ORCHESTRATOR      delegates to subagents
  MULTI_AGENT_ORCHESTRATOR coordinates several agents (swarm/graph/team)
  MEMORY_PROVIDER         persists state across sessions
  WORKFLOW_PROVIDER       runs a declared pipeline of steps

Discovery reads two real artifacts and never guesses from a skill's name:

  * `<engine>/registry/ORCHESTRATORS.json` -- the text contract EOS maintains;
  * any skill package that ships `orchestrator.contract.json` -- the provider's
    own declaration, recorded with its path as evidence.

A declared provider with no runtime on PATH is reported `ADAPTER_ONLY`: the
contract is known, execution is not available, and nothing is run. A probe is a
bounded version call with a hard timeout, never a task. `test --self-test` proves
all three verdicts against fixtures that ship with the engine.

Commands:
  discover   list providers with class, evidence and runtime status
  doctor     validate the contracts and report findings
  test       probe each available runtime, or run the built-in fixtures
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

REGISTRY = os.path.join(ep.REGISTRY, "ORCHESTRATORS.json")

CLASSES = {
    "INSTRUCTION", "TOOL_PROVIDER", "SPECIALIST_PROVIDER", "AGENT_ORCHESTRATOR",
    "MULTI_AGENT_ORCHESTRATOR", "MEMORY_PROVIDER", "WORKFLOW_PROVIDER",
}
ORCHESTRATING = {"AGENT_ORCHESTRATOR", "MULTI_AGENT_ORCHESTRATOR", "WORKFLOW_PROVIDER"}
REQUIRED = ("id", "class", "skill", "runtime", "delegation", "permissions",
            "side_effects", "cost", "bounds", "evidence")
MAX_PROBE_SECONDS = 30
EXECUTION_PROTOCOL = "eos-orchestrator/1"


def load_declared():
    """Providers EOS maintains by hand, plus any skill that declares itself."""
    providers, problems = [], []
    if os.path.isfile(REGISTRY):
        try:
            with open(REGISTRY, encoding="utf-8-sig") as fh:
                data = json.load(fh)
            providers.extend(data.get("providers") or [])
        except (OSError, ValueError) as exc:
            problems.append(f"ORCHESTRATORS.json unreadable: {exc}")

    # A skill package may ship its own contract. That path is the evidence: a
    # provider cannot be "discovered" by having a suggestive name.
    try:
        import skill_index
        entries = skill_index.load_index()
    except Exception as exc:  # discovery must not fail because the index is stale
        problems.append(f"skill index unavailable: {exc}")
        entries = []
    for entry in entries:
        try:
            skill_dir = os.path.dirname(skill_index.entry_abs(entry["path"]))
        except Exception:
            continue
        contract = os.path.join(skill_dir, "orchestrator.contract.json")
        if not os.path.isfile(contract):
            continue
        try:
            with open(contract, encoding="utf-8-sig") as fh:
                declared = json.load(fh)
        except (OSError, ValueError) as exc:
            problems.append(f"{entry['name']}: orchestrator.contract.json unreadable: {exc}")
            continue
        declared.setdefault("id", entry["name"])
        declared["declared_by"] = entry["path"]
        providers.append(declared)
    return providers, problems


def classes_of(providers):
    seen = {}
    for p in providers:
        seen[p.get("class", "UNKNOWN")] = seen.get(p.get("class", "UNKNOWN"), 0) + 1
    return seen


def runtime_state(provider):
    """(status, detail) for the provider's runtime.

    ADAPTER_ONLY is not a failure: the contract is usable, the execution is not
    available on this machine, and EOS must not simulate it.
    """
    runtime = provider.get("runtime") or {}
    argv = runtime.get("argv")
    if not argv:
        binary = runtime.get("binary")
        if not binary:
            return "ADAPTER_ONLY", "no runtime declared"
        located = shutil.which(binary)
        if not located:
            return "ADAPTER_ONLY", f"'{binary}' not on PATH"
        argv = [located] + list(runtime.get("probe") or ["--version"])
    return "RUNTIME_PRESENT", " ".join(argv)


def contract_problems(provider):
    problems = []
    for field in REQUIRED:
        if field not in provider:
            problems.append(f"missing '{field}'")
    if provider.get("class") not in CLASSES:
        problems.append(f"unknown class '{provider.get('class')}'")
    bounds = provider.get("bounds") or {}
    for field in ("timeout_seconds", "max_depth", "max_agents"):
        value = bounds.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"bounds.{field} must be a positive integer")
    permissions = provider.get("permissions") or {}
    for field in ("write", "network", "execute", "outside_workspace"):
        if not isinstance(permissions.get(field), bool):
            problems.append(f"permissions.{field} must be a boolean")
    if not isinstance(provider.get("side_effects"), list):
        problems.append("side_effects must be a list")
    if not isinstance(provider.get("evidence"), dict) or not provider["evidence"].get("dir"):
        problems.append("evidence.dir is required")
    if not provider.get("runtime") or not (provider["runtime"].get("binary")
                                           or provider["runtime"].get("argv")):
        problems.append("runtime.binary or runtime.argv is required")
    return problems


def resolve_providers():
    providers, problems = load_declared()
    by_id = {}
    for p in providers:
        pid = str(p.get("id") or "")
        if pid in by_id:
            problems.append(f"duplicate provider id '{pid}'")
            continue
        by_id[pid] = p
    return list(by_id.values()), problems


def execution_ready(provider):
    """Whether the contract can be *invoked*, not merely probed."""
    execution = provider.get("execution") or {}
    if execution.get("protocol") != EXECUTION_PROTOCOL:
        return False, "no execution contract"
    return True, execution.get("adapter") or "json-stdin"


def cmd_discover(as_json: bool) -> int:
    providers, problems = resolve_providers()
    rows = []
    for p in providers:
        status, detail = runtime_state(p)
        executable, execution_detail = execution_ready(p)
        rows.append({
            "id": p.get("id"),
            "class": p.get("class"),
            "skill": p.get("skill"),
            "status": status,
            "execution": "EXECUTABLE" if executable else "ADAPTER_ONLY",
            "execution_detail": execution_detail,
            "runtime": detail,
            "depth": (p.get("bounds") or {}).get("max_depth"),
            "timeout_seconds": (p.get("bounds") or {}).get("timeout_seconds"),
            "declared_by": p.get("declared_by") or "registry/ORCHESTRATORS.json",
        })
    if as_json:
        print(json.dumps({"providers": rows, "classes": classes_of(providers),
                          "problems": problems}, indent=2))
        return 0 if not problems else 1
    if not rows:
        print("no orchestrator providers declared")
        return 0
    print(f"{'id':18} {'class':26} {'runtime':16} {'execution':12} {'depth':6} timeout")
    for r in rows:
        print(f"{r['id']:18} {str(r['class']):26} {r['status']:16} "
              f"{r['execution']:12} {str(r['depth']):6} {r['timeout_seconds']}s")
    for p in problems:
        print(f"  FINDING {p}")
    print(f"\n{len(rows)} provider(s). Classes: "
          + ", ".join(f"{k}={v}" for k, v in sorted(classes_of(providers).items())))
    print("ADAPTER_ONLY means the contract is known and nothing was executed. "
          "EXECUTABLE means a bounded invocation contract exists; it does not mean a task ran.")
    return 0 if not problems else 1


def cmd_doctor(as_json: bool) -> int:
    providers, findings = resolve_providers()
    errors = []
    for p in providers:
        for problem in contract_problems(p):
            errors.append(f"{p.get('id')}: {problem}")
    findings = findings + errors
    if as_json:
        print(json.dumps({"providers": len(providers), "findings": findings,
                          "pass": not errors}, indent=2))
    else:
        print(f"providers {len(providers)} | findings {len(findings)}")
        for f in findings:
            print(f"  {f}")
        print("PASS" if not errors else "FAIL")
    return 0 if not errors else 1


def probe(argv, timeout):
    """Bounded version call. Never a task: output is capped and the process dies
    at the timeout instead of being waited on."""
    try:
        proc = subprocess.run(argv, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except FileNotFoundError:
        return "ADAPTER_ONLY", "runtime disappeared before the probe"
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"probe exceeded {timeout}s"
    except OSError as exc:
        return "FAIL", f"probe could not start: {exc}"
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return "FAIL", f"probe exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
    return "PROBE_OK", out[:200]


def run_provider(p):
    problems = contract_problems(p)
    if problems:
        return "INVALID", "; ".join(problems)
    runtime = p["runtime"]
    argv = runtime.get("argv")
    if not argv:
        located = shutil.which(runtime.get("binary"))
        if not located:
            return "ADAPTER_ONLY", f"'{runtime.get('binary')}' not on PATH"
        argv = [located] + list(runtime.get("probe") or ["--version"])
    timeout = min(int(runtime.get("timeout_seconds") or MAX_PROBE_SECONDS), MAX_PROBE_SECONDS)
    verdict, detail = probe(argv, timeout)
    if verdict != "PROBE_OK":
        return verdict, detail
    executable, why = execution_ready(p)
    if not executable:
        return "ADAPTER_ONLY", (
            f"runtime answered a version probe, but {why}; nothing was delegated")
    return "PROBE_OK", (
        f"runtime answered a version probe and declares {EXECUTION_PROTOCOL}; "
        f"no task was run by `test` (use `orchestrators run`)")


def cmd_test(as_json: bool, provider_id, self_test: bool) -> int:
    if self_test:
        return self_test_run(as_json)
    providers, problems = resolve_providers()
    if provider_id:
        providers = [p for p in providers if p.get("id") == provider_id]
        if not providers:
            print(f"unknown provider: {provider_id}")
            return 2
    rows = []
    for p in providers:
        verdict, detail = run_provider(p)
        rows.append({"id": p.get("id"), "verdict": verdict, "detail": detail})
    if as_json:
        print(json.dumps({"results": rows, "problems": problems}, indent=2))
    else:
        for r in rows:
            print(f"{r['verdict']:14} {r['id']}: {r['detail']}")
        for p in problems:
            print(f"FINDING {p}")
        print("\nADAPTER_ONLY = contract known, nothing executed. PROBE_OK = the runtime "
              "answered a version probe; a task still needs `orchestrators run`.")
    failed = [r for r in rows if r["verdict"] in ("FAIL", "TIMEOUT", "INVALID", "UNKNOWN")]
    return 0 if not failed and not problems else 1


def self_test_run(as_json: bool) -> int:
    """Prove every verdict against fixtures that need no installed provider.

    This is what makes the ADAPTER_ONLY claim trustworthy: the same function
    that reports it must also be able to report PROBE_OK, TIMEOUT and INVALID
    when the fixture really behaves that way, and the real bounded delegation
    fixture must run processes rather than only declare a contract.
    """
    tmp = tempfile.mkdtemp(prefix="eos_orch_")
    base = {
        "class": "AGENT_ORCHESTRATOR",
        "skill": "fixture",
        "runtime": {},
        "execution": {"protocol": EXECUTION_PROTOCOL, "adapter": "json-stdin"},
        "delegation": {"method": "fixture", "entry_point": "fixture", "specialists": ["a", "b"]},
        "permissions": {"write": False, "network": False, "execute": True,
                        "outside_workspace": False},
        "side_effects": [],
        "cost": {"billing": "none"},
        "bounds": {"timeout_seconds": 5, "max_depth": 1, "max_agents": 2},
        "evidence": {"dir": tmp, "format": "json"},
    }

    def with_runtime(pid, argv, timeout=5):
        item = json.loads(json.dumps(base))
        item["id"] = pid
        item["runtime"] = {"argv": argv, "timeout_seconds": timeout}
        return item

    cases = [
        ("probe_ok", "PROBE_OK", with_runtime(
            "fixture-ok", [sys.executable, "-c", "print('fixture 1.0')"])),
        ("adapter_only", "ADAPTER_ONLY", with_runtime(
            "fixture-missing", [os.path.join(tmp, "no-such-binary")])),
        ("timeout", "TIMEOUT", with_runtime(
            "fixture-slow", [sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)),
        ("invalid", "INVALID", {k: v for k, v in with_runtime(
            "fixture-invalid", [sys.executable, "-c", "pass"]).items() if k != "bounds"}),
    ]
    no_contract = with_runtime("fixture-probe-only",
                               [sys.executable, "-c", "print('fixture 1.0')"])
    no_contract.pop("execution")
    cases.append(("probe_is_not_execution", "ADAPTER_ONLY", no_contract))
    results, failures = [], []
    for name, expected, provider in cases:
        verdict, detail = run_provider(provider)
        ok = verdict == expected
        results.append({"case": name, "expected": expected, "got": verdict, "pass": ok,
                        "detail": detail})
        if not ok:
            failures.append(f"{name}: expected {expected}, got {verdict} ({detail})")
    extra = contract_problems(json.loads(json.dumps(base)))
    if not extra:
        failures.append("a contract with no runtime was reported as valid")
    if not any("runtime" in f for f in extra):
        failures.append("the missing-runtime finding was not reported")

    # A contract table is not a delegation proof. Run the real bounded fixture
    # here too, so this command cannot pass on names, probes or prose.
    import contextlib
    import io
    try:
        import orchestrator_runtime
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rc = orchestrator_runtime.selftest()
        detail = [line for line in buffer.getvalue().splitlines()
                  if line.startswith(("PASS orchestrator runtime", "FAIL"))]
        results.append({"case": "delegation_fixture", "expected": "PASS",
                        "got": "PASS" if rc == 0 else "FAIL", "pass": rc == 0,
                        "detail": detail[-1] if detail else "no selftest output"})
        if rc != 0:
            failures.append("real delegation fixture failed")
    except Exception as exc:
        results.append({"case": "delegation_fixture", "expected": "PASS", "got": "ERROR",
                        "pass": False, "detail": f"{type(exc).__name__}: {exc}"})
        failures.append(f"real delegation fixture raised: {type(exc).__name__}: {exc}")

    if as_json:
        print(json.dumps({"results": results, "failures": failures,
                          "pass": not failures}, indent=2))
    else:
        for r in results:
            print(f"{'PASS' if r['pass'] else 'FAIL'} {r['case']}: {r['got']}")
        print("PASS orchestrator contract self-test" if not failures else "FAIL " + "; ".join(failures))
    try:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass
    return 0 if not failures else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="orchestrator_registry.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover"); d.add_argument("--json", action="store_true")
    q = sub.add_parser("doctor"); q.add_argument("--json", action="store_true")
    t = sub.add_parser("test")
    t.add_argument("--json", action="store_true")
    t.add_argument("--provider", default="")
    t.add_argument("--self-test", dest="self_test", action="store_true")
    args = ap.parse_args()
    if args.cmd == "discover":
        return cmd_discover(args.json)
    if args.cmd == "doctor":
        return cmd_doctor(args.json)
    return cmd_test(args.json, args.provider, args.self_test)


if __name__ == "__main__":
    sys.exit(main())
