#!/usr/bin/env python3
"""P5 mechanical benchmark.

This runs the real EOS invariants against real fixture projects. It does not
call a model and it does not claim that EOS "finds bugs" in general: it measures
the mechanical guarantees the runtime is responsible for.

Each scenario has a kind:

  healthy   EOS must allow the work and must not overclaim coverage.
  negative  EOS must refuse, block or report the fault.

Scorecard fields are computed from the scenarios, never asserted in advance.

Commands:
  run [--json] [--workspace <dir>] [--out <file>]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402
import auto_decision as ad  # noqa: E402
import impact as im  # noqa: E402
import project_map as pm  # noqa: E402
import secret_scan as ss  # noqa: E402
import work_state as ws  # noqa: E402
import write_gate as wg  # noqa: E402

SCHEMA = 1
KIND = "p5_benchmark"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_workspace(profiles: list) -> str:
    root = tempfile.mkdtemp(prefix="eos-p5-")
    registry = os.path.join(root, ".agents", "registry")
    os.makedirs(registry)
    with open(os.path.join(registry, "PROJECT_PROFILES.json"), "w",
              encoding="utf-8", newline="\n") as handle:
        json.dump({"profiles": [{"id": pid, "path": rel} for pid, rel in profiles]},
                  handle)
    for _pid, rel in profiles:
        os.makedirs(os.path.join(root, rel), exist_ok=True)
    return root


def write_file(root: str, rel: str, text: str) -> str:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def create_map(root: str, project: str) -> None:
    if pm.cmd_create(argparse.Namespace(project=project, workspace=root,
                                        force=False, project_path="")) != 0:
        raise RuntimeError(f"map create failed for {project}")


def approve(root: str, project: str, rel: str) -> dict:
    return ad.prepare(root, project, f"p5 {rel}", [rel], approve=True, use_impact=False)


def gate(root: str, project: str, rel: str) -> dict:
    return wg.check(root, os.path.join(root, rel), "p5", project)


def map_value(root: str, project: str) -> dict:
    return pm.read_json(pm.store_path(root, project))


def scenario_new_project_healthy() -> dict:
    root = make_workspace([("empty", "proj")])
    write_file(root, "proj/app.txt", "hello\n")
    create_map(root, "empty")
    decision = approve(root, "empty", "proj/app.txt")
    result = gate(root, "empty", "proj/app.txt")
    value = map_value(root, "empty")
    return {"ok": result["allow"] is True and decision.get("verdict") == "READY",
            "want_map": "CURRENT",
            "expected": "a new project with a current map is allowed",
            "observed": {"decision": decision.get("verdict"), "allow": result["allow"],
                         "map_state": result.get("map_state"),
                         "coverage_state": value.get("coverage_state")}}


def scenario_missing_map_negative() -> dict:
    root = make_workspace([("empty", "proj")])
    write_file(root, "proj/app.txt", "hello\n")
    result = gate(root, "empty", "proj/app.txt")
    return {"ok": result["allow"] is False,
            "want_map": "CURRENT",
            "expected": "a write without an approved decision is refused",
            "observed": {"allow": result["allow"],
                         "needs_decision": result.get("needs_decision"),
                         "map_state": result.get("map_state")}}


def scenario_python_stale_negative() -> dict:
    root = make_workspace([("py", "proj")])
    write_file(root, "proj/a.py", "def run():\n    return 1\n")
    create_map(root, "py")
    approve(root, "py", "proj/a.py")
    write_file(root, "proj/a.py", "def run():\n    return 2\n")
    result = gate(root, "py", "proj/a.py")
    return {"ok": result["allow"] is False and result.get("map_state") == "STALE",
            "want_map": "STALE",
            "expected": "an out-of-band source change makes the write fail on STALE",
            "observed": {"allow": result["allow"], "map_state": result.get("map_state"),
                         "coverage_state": map_value(root, "py").get("coverage_state"),
                         "reasons": result.get("reasons")}}


def scenario_python_healthy() -> dict:
    root = make_workspace([("py", "proj")])
    write_file(root, "proj/a.py", "def run():\n    return 1\n")
    create_map(root, "py")
    approve(root, "py", "proj/a.py")
    result = gate(root, "py", "proj/a.py")
    return {"ok": result["allow"] is True,
            "want_map": "CURRENT",
            "expected": "an unchanged python project with a decision is allowed",
            "observed": {"allow": result["allow"], "map_state": result.get("map_state"),
                         "coverage_state": map_value(root, "py").get("coverage_state")}}


def scenario_web_partial() -> dict:
    root = make_workspace([("web", "proj")])
    write_file(root, "proj/index.html", "<html></html>\n")
    write_file(root, "proj/app.js", "export function boot() { return 1 }\n")
    create_map(root, "web")
    value = map_value(root, "web")
    js = [item for item in value["modules"] if item["path"] == "app.js"][0]
    return {"ok": value["coverage_state"] != "FULL" and js["parse"] == "PARTIAL",
            "want_map": "CURRENT",
            "expected": "a JS adapter is PARTIAL and never full coverage",
            "observed": {"coverage_state": value["coverage_state"],
                         "js_parse": js["parse"],
                         "map_state": pm.verify("web", root)["state"],
                         "coverage": value.get("coverage")}}


def scenario_desktop_unsupported() -> dict:
    root = make_workspace([("desk", "proj")])
    write_file(root, "proj/MainWindow.cs", "class MainWindow {}\n")
    write_file(root, "proj/Program.cs", "class Program { static void Main() {} }\n")
    create_map(root, "desk")
    value = map_value(root, "desk")
    return {"ok": value["coverage_state"] == "PARTIAL"
                  and value["parsers"]["unsupported"] >= 1,
            "want_map": "CURRENT",
            "expected": "an unsupported desktop language is reported, not hidden",
            "observed": {"coverage_state": value["coverage_state"],
                         "parsers": value.get("parsers"),
                         "map_state": pm.verify("desk", root)["state"],
                         "unknowns": value.get("unknowns")}}


def scenario_backend_contract() -> dict:
    root = make_workspace([("api", "proj")])
    write_file(root, "proj/routes.py", "def get_user(user_id):\n    return user_id\n")
    create_map(root, "api")
    value = map_value(root, "api")
    symbols = [item.get("symbol") for item in value.get("contracts") or []]
    return {"ok": "get_user" in symbols,
            "want_map": "CURRENT",
            "expected": "an exported route symbol is listed as a contract",
            "observed": {"contracts": symbols,
                         "coverage_state": value.get("coverage_state"),
                         "map_state": pm.verify("api", root)["state"]}}


def scenario_security_secret_negative() -> dict:
    root = make_workspace([("sec", "proj")])
    path = write_file(root, "proj/config.py",
                      'AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    findings = ss.scan_file(path, True)
    return {"ok": len(findings) > 0,
            "want_map": None,
            "expected": "a hard-coded AWS key is detected",
            "observed": {"findings": [item["kind"] for item in findings]}}


def scenario_security_placeholder_healthy() -> dict:
    root = make_workspace([("sec", "proj")])
    path = write_file(root, "proj/config.py",
                      'API_KEY = "your-key-here"\n')
    findings = ss.scan_file(path, True)
    return {"ok": len(findings) == 0,
            "want_map": None,
            "expected": "a placeholder is not reported as a secret",
            "observed": {"findings": [item["kind"] for item in findings]}}


def scenario_math_impact() -> dict:
    root = make_workspace([("math", "proj")])
    write_file(root, "proj/formula.py", "def area(r):\n    return 3.14159 * r * r\n")
    write_file(root, "proj/tests/test_formula.py",
               "from formula import area\n\ndef test_area():\n    assert area(1) > 3\n")
    create_map(root, "math")
    report = im.build("math", root, ["formula.py"], "", "", 40)
    tests = [item.get("path") for item in report.get("tests") or []]
    return {"ok": "tests/test_formula.py" in tests,
            "want_map": "CURRENT",
            "expected": "changing the formula lists its test as affected",
            "observed": {"tests": tests, "verdict": report.get("verdict"),
                         "coverage_state": map_value(root, "math").get("coverage_state"),
                         "map_state": pm.verify("math", root)["state"]}}


def scenario_cross_process_impact() -> dict:
    root = make_workspace([("cross", "proj")])
    write_file(root, "proj/service.py", "def handle():\n    return 1\n")
    write_file(root, "proj/worker.py", "from service import handle\n")
    create_map(root, "cross")
    report = im.build("cross", root, ["service.py"], "", "", 40)
    callers = [edge.get("from") for edge in report.get("edges") or []]
    return {"ok": "worker.py" in callers,
            "want_map": "CURRENT",
            "expected": "a second process/module that imports the service is affected",
            "observed": {"edges": callers, "verdict": report.get("verdict"),
                         "coverage_state": map_value(root, "cross").get("coverage_state"),
                         "map_state": pm.verify("cross", root)["state"]}}


def scenario_multi_project_isolation() -> dict:
    root = make_workspace([("alpha", "proj_a"), ("beta", "proj_b")])
    write_file(root, "proj_a/a.py", "def a():\n    return 1\n")
    write_file(root, "proj_b/b.py", "def b():\n    return 2\n")
    create_map(root, "alpha")
    create_map(root, "beta")
    alpha = map_value(root, "alpha")
    leaked = [rel for rel in alpha["sources"]["files"] if rel.startswith("proj_b/")]
    result = gate(root, "beta", "proj_b/b.py")
    return {"ok": not leaked and result.get("project") == "beta",
            "want_map": "CURRENT",
            "expected": "maps and project inference stay isolated per project",
            "observed": {"leaked_sources": leaked, "inferred": result.get("project"),
                         "coverage_state": alpha.get("coverage_state"),
                         "map_state": result.get("map_state")}}


def scenario_nested_control_plane_negative() -> dict:
    root = make_workspace([("sub", "proj/sub")])
    write_file(root, "proj/sub/.agents/evidence/x.json", "{}\n")
    result = wg.check(root, os.path.join(root, "proj", "sub", ".agents",
                                         "evidence", "x.json"), "p5", "sub")
    return {"ok": result["allow"] is False and result.get("map_state") == "PROTECTED",
            "want_map": "PROTECTED",
            "expected": "a nested control-plane path is refused",
            "observed": {"allow": result["allow"], "map_state": result.get("map_state")}}


def scenario_concurrent_writer() -> dict:
    root = make_workspace([("race", "proj")])
    script = os.path.abspath(ws.__file__)
    children = []
    for session in ("p1", "p2"):
        children.append(subprocess.Popen(
            [sys.executable, script, "start", "--project", "race", "--task", "p5",
             "--session", session, "--owner", session, "--new", "--workspace", root],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
    outputs = [child.communicate(timeout=120)[0] for child in children]
    codes = [child.returncode for child in children]
    return {"ok": codes.count(0) == 1,
            "want_map": None,
            "expected": "two real processes never become the writer together",
            "observed": {"codes": codes,
                         "blocked": sum("BLOCKED" in text for text in outputs)}}


SCENARIOS = [
    ("new_project_healthy", "healthy", scenario_new_project_healthy),
    ("missing_decision_negative", "negative", scenario_missing_map_negative),
    ("python_stale_negative", "negative", scenario_python_stale_negative),
    ("python_healthy", "healthy", scenario_python_healthy),
    ("web_partial_coverage", "healthy", scenario_web_partial),
    ("desktop_unsupported", "healthy", scenario_desktop_unsupported),
    ("backend_contract", "healthy", scenario_backend_contract),
    ("security_secret_negative", "negative", scenario_security_secret_negative),
    ("security_placeholder_healthy", "healthy", scenario_security_placeholder_healthy),
    ("math_impact", "healthy", scenario_math_impact),
    ("cross_process_impact", "healthy", scenario_cross_process_impact),
    ("multi_project_isolation", "healthy", scenario_multi_project_isolation),
    ("nested_control_plane_negative", "negative", scenario_nested_control_plane_negative),
    ("concurrent_writer", "healthy", scenario_concurrent_writer),
]


def run() -> dict:
    started = time.time()
    results = []
    for name, kind, fn in SCENARIOS:
        begin = time.time()
        try:
            value = fn()
            results.append({
                "name": name, "kind": kind, "expected": value.get("expected", ""),
                "observed": value.get("observed", {}),
                "want_map": value.get("want_map"),
                "ok": bool(value.get("ok")),
                "duration_seconds": round(time.time() - begin, 3),
            })
        except Exception as exc:  # noqa: BLE001
            results.append({
                "name": name, "kind": kind, "expected": "",
                "observed": {"error": f"{type(exc).__name__}: {exc}"},
                "want_map": None, "ok": False,
                "duration_seconds": round(time.time() - begin, 3),
            })

    negative = [item for item in results if item["kind"] == "negative"]
    healthy = [item for item in results if item["kind"] == "healthy"]
    detected = [item for item in negative if item["ok"]]
    false_block = [item for item in healthy if not item["ok"]]
    freshness = [item for item in results if item["want_map"]]
    fresh = [item for item in freshness
             if item["observed"].get("map_state") == item["want_map"]]
    coverage = {}
    for item in results:
        state = item["observed"].get("coverage_state")
        if state:
            coverage[state] = coverage.get(state, 0) + 1

    scorecard = {
        "scenarios": len(results),
        "passed": sum(item["ok"] for item in results),
        "failed": sum(not item["ok"] for item in results),
        "negative": len(negative),
        "detected": len(detected),
        "detection_rate": round(len(detected) / len(negative), 3) if negative else 0.0,
        "false_pass": len(negative) - len(detected),
        "healthy": len(healthy),
        "false_block": len(false_block),
        "context_freshness": round(len(fresh) / len(freshness), 3) if freshness else 1.0,
        "map_coverage": coverage,
        "regression": "covered by the vnext_dev/vnext/regression suites, not re-run here",
        "duration_seconds": round(time.time() - started, 3),
        "tool_evidence": "not_applicable: fixtures have no host session",
        "skill_evidence": "not_applicable: fixtures have no host session",
        "model_calls": 0,
    }
    return {"schema": SCHEMA, "kind": KIND, "created_at": utc_now(),
            "scenarios": results, "scorecard": scorecard}


def main() -> int:
    parser = argparse.ArgumentParser(prog="p5_benchmark.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--workspace")
    p.add_argument("--out", default="")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command != "run":
        return 2
    value = run()
    workspace = ep.resolve_workspace(args.workspace)
    out = args.out or os.path.join(workspace, ".agents", "evidence", "p5",
                                   f"p5-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    if args.json:
        print(json.dumps(value, ensure_ascii=True))
    else:
        card = value["scorecard"]
        print(f"P5: {card['passed']}/{card['scenarios']} scenarios  "
              f"detection={card['detection_rate']}  false_pass={card['false_pass']}  "
              f"false_block={card['false_block']}  freshness={card['context_freshness']}  "
              f"{card['duration_seconds']}s")
        for item in value["scenarios"]:
            print(f"  {'PASS' if item['ok'] else 'FAIL'}  {item['name']}  "
                  f"[{item['duration_seconds']}s]")
        print(f"  artifact: {out}")
    return 0 if value["scorecard"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
