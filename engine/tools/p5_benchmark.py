#!/usr/bin/env python3
"""P5 mechanical benchmark (validity pass).

It runs the real EOS invariants against real fixtures. It does not call a model
and it does not claim general bug detection. Scenarios marked `oracle` prove
that the fixture's own oracle rejects the broken version and accepts the fixed
version; EOS is measured on map, gate, impact and ownership behaviour.

Every artifact carries a scenario version, the source commit, the host and a
content seal. P6 refuses to trend artifacts it cannot verify.

Commands:
  run [--json] [--workspace <dir>] [--out <file>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
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
SCENARIO_VERSION = "2026-11-21.2"
TOOLS = os.path.dirname(os.path.abspath(__file__))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def seal(value: dict) -> str:
    body = {key: item for key, item in value.items()
            if key not in ("content_sha256", "artifact_sha256")}
    return _hash(body)


def identity(value: dict) -> str:
    """Result identity for de-duplication: the same scenarios, commit and
    scorecard produce the same id even when the run time and durations differ."""
    card = dict(value.get("scorecard") or {})
    for key in ("duration_seconds", "false_block_names"):
        card.pop(key, None)
    scenarios = [{key: item.get(key) for key in ("name", "kind", "ok", "observed")}
                 for item in value.get("scenarios") or []]
    return _hash({"scenario_version": value.get("scenario_version"),
                  "commit": (value.get("source") or {}).get("commit"),
                  "scenarios": scenarios, "scorecard": card})


def source_meta() -> dict:
    root = os.path.dirname(ep.ENGINE_ROOT)

    def run(args):
        try:
            return subprocess.run(["git", "-C", root, *args], capture_output=True,
                                  text=True, timeout=15).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {"commit": run(["rev-parse", "HEAD"]),
            "branch": run(["rev-parse", "--abbrev-ref", "HEAD"]),
            "engine": os.path.basename(root)}


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


def create_map(root: str, project: str, project_path: str = "") -> int:
    return pm.cmd_create(argparse.Namespace(project=project, workspace=root,
                                            force=False, project_path=project_path))


def approve(root: str, project: str, rel: str) -> dict:
    return ad.prepare(root, project, f"p5 {rel}", [rel], approve=True, use_impact=False)


def gate(root: str, project: str, rel: str) -> dict:
    return wg.check(root, os.path.join(root, rel), "p5", project)


def map_value(root: str, project: str) -> dict:
    return pm.read_json(pm.store_path(root, project))


def run_oracle(root: str, rel: str) -> tuple:
    path = os.path.join(root, rel)
    child = subprocess.run([sys.executable, os.path.basename(path)],
                           cwd=os.path.dirname(path), capture_output=True,
                           text=True, timeout=120)
    return child.returncode, (child.stdout + child.stderr).strip()


def scenario_new_project_healthy() -> dict:
    root = make_workspace([("empty", "proj")])
    write_file(root, "proj/app.txt", "hello\n")
    create_map(root, "empty")
    decision = approve(root, "empty", "proj/app.txt")
    result = gate(root, "empty", "proj/app.txt")
    value = map_value(root, "empty")
    return {"ok": result["allow"] is True and decision.get("verdict") == "READY",
            "want_map": "CURRENT", "kind": "healthy",
            "expected": "a registered new project with a current map is allowed",
            "observed": {"decision": decision.get("verdict"), "allow": result["allow"],
                         "map_state": result.get("map_state"),
                         "coverage_state": value.get("coverage_state")}}


def scenario_new_project_unregistered() -> dict:
    root = make_workspace([])
    write_file(root, "proj/app.py", "x = 1\n")
    created = create_map(root, "discovered", os.path.join(root, "proj"))
    value = map_value(root, "discovered")
    result = wg.check(root, os.path.join(root, "proj", "app.py"), "p5")
    reasons = " ".join(result.get("reasons") or [])
    return {"ok": created == 0 and value.get("sources", {}).get("count", 0) >= 1
                  and result["allow"] is False and "inferred" in reasons,
            "want_map": "CURRENT", "kind": "negative",
            "expected": "an unregistered project is mapped by path but never silently gated",
            "observed": {"created": created, "sources": value.get("sources", {}).get("count"),
                         "allow": result["allow"], "reasons": result.get("reasons"),
                         "map_state": pm.verify("discovered", root)["state"]}}


def scenario_missing_decision_negative() -> dict:
    root = make_workspace([("empty", "proj")])
    write_file(root, "proj/app.txt", "hello\n")
    create_map(root, "empty")
    result = gate(root, "empty", "proj/app.txt")
    return {"ok": result["allow"] is False, "want_map": "CURRENT", "kind": "negative",
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
            "want_map": "STALE", "kind": "negative",
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
    return {"ok": result["allow"] is True, "want_map": "CURRENT", "kind": "healthy",
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
            "want_map": "CURRENT", "kind": "healthy",
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
            "want_map": "CURRENT", "kind": "healthy",
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
    return {"ok": "get_user" in symbols, "want_map": "CURRENT", "kind": "healthy",
            "expected": "an exported route symbol is listed as a contract",
            "observed": {"contracts": symbols,
                         "coverage_state": value.get("coverage_state"),
                         "map_state": pm.verify("api", root)["state"]}}


def scenario_security_secret_negative() -> dict:
    root = make_workspace([("sec", "proj")])
    path = write_file(root, "proj/config.py", 'AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    findings = ss.scan_file(path, True)
    return {"ok": len(findings) > 0, "want_map": None, "kind": "negative",
            "expected": "a hard-coded AWS key is detected",
            "observed": {"findings": [item["kind"] for item in findings]}}


def scenario_security_placeholder_healthy() -> dict:
    root = make_workspace([("sec", "proj")])
    path = write_file(root, "proj/config.py", 'API_KEY = "your-key-here"\n')
    findings = ss.scan_file(path, True)
    return {"ok": len(findings) == 0, "want_map": None, "kind": "healthy",
            "expected": "a placeholder is not reported as a secret",
            "observed": {"findings": [item["kind"] for item in findings]}}


AUTH_ORACLE = """\
from auth import can_access
cases = [
    ("admin", {"role": "admin"}, True),
    ("viewer", {"role": "viewer"}, False),
    ("anonymous", {}, False),
]
bad = [name for name, user, expected in cases if can_access(user, "doc") != expected]
print("PASS" if not bad else "FAIL", ",".join(bad))
raise SystemExit(0 if not bad else 1)
"""


def auth_run(root: str, source: str) -> tuple:
    write_file(root, "proj/auth.py", source)
    write_file(root, "proj/auth_oracle.py", AUTH_ORACLE)
    return run_oracle(root, "proj/auth_oracle.py")


def scenario_security_auth_broken() -> dict:
    root = make_workspace([("auth", "proj")])
    code, output = auth_run(root, "def can_access(user, resource):\n    return True\n")
    return {"ok": code != 0 and "FAIL" in output, "want_map": None, "kind": "negative",
            "expected": "an authorization oracle rejects a missing role check",
            "observed": {"exit": code, "output": output}}


def scenario_security_auth_healthy() -> dict:
    root = make_workspace([("auth", "proj")])
    code, output = auth_run(
        root, 'def can_access(user, resource):\n    return user.get("role") == "admin"\n')
    return {"ok": code == 0 and "PASS" in output, "want_map": None, "kind": "healthy",
            "expected": "the authorization oracle accepts a correct role check",
            "observed": {"exit": code, "output": output}}


MATH_ORACLE = """\
import math
from formula import area
checks = [
    ("circle unit", abs(area(1) - math.pi) < 1e-9),
    ("scaling", abs(area(2) - 4 * math.pi) < 1e-9),
    ("zero boundary", abs(area(0)) < 1e-12),
    ("square units", abs(area(3) / area(1) - 9) < 1e-9),
]
bad = [name for name, ok in checks if not ok]
print("PASS" if not bad else "FAIL", ",".join(bad))
raise SystemExit(0 if not bad else 1)
"""

BROKEN_FORMULA = "def area(r):\n    return 2 * 3.14159 * r\n"
FIXED_FORMULA = "import math\n\ndef area(r):\n    return math.pi * r * r\n"


def math_fixture() -> str:
    root = make_workspace([("math", "proj")])
    write_file(root, "proj/formula.py", BROKEN_FORMULA)
    write_file(root, "proj/math_oracle.py", MATH_ORACLE)
    write_file(root, "proj/tests/test_formula.py",
               "from formula import area\n\ndef test_area():\n    assert area(1) > 3\n")
    create_map(root, "math")
    return root


def scenario_math_oracle_broken() -> dict:
    root = math_fixture()
    broken_code, broken_out = run_oracle(root, "proj/math_oracle.py")
    report = im.build("math", root, ["formula.py"], "", "", 40)
    tests = [item.get("path") for item in report.get("tests") or []]
    return {"ok": broken_code != 0 and "FAIL" in broken_out
                  and "tests/test_formula.py" in tests,
            "want_map": "CURRENT", "kind": "negative",
            "expected": "a wrong formula fails the oracle and its test is affected",
            "observed": {"exit": broken_code, "output": broken_out, "tests": tests,
                         "coverage_state": map_value(root, "math").get("coverage_state"),
                         "map_state": pm.verify("math", root)["state"]}}


def scenario_math_oracle_healthy() -> dict:
    root = math_fixture()
    write_file(root, "proj/formula.py", FIXED_FORMULA)
    pm.update_files("math", root, ["proj/formula.py"])
    code, output = run_oracle(root, "proj/math_oracle.py")
    return {"ok": code == 0 and "PASS" in output, "want_map": "CURRENT", "kind": "healthy",
            "expected": "the corrected formula passes the same oracle",
            "observed": {"exit": code, "output": output,
                         "map_state": pm.verify("math", root)["state"]}}


def scenario_cross_process_ownership() -> dict:
    root = make_workspace([("race", "proj")])
    write_file(root, "proj/data.txt", "owned\n")
    create_map(root, "race")
    approve(root, "race", "proj/data.txt")
    ws.cmd_start(argparse.Namespace(project="race", task="own", session="proc-a",
                                     owner="a", new=False, workspace=root))
    child = subprocess.run(
        [sys.executable, os.path.join(TOOLS, "write_gate.py"), "check", "--json",
         "--workspace", root, "--project", "race", "--session", "proc-b",
         "--path", os.path.join(root, "proj", "data.txt")],
        capture_output=True, text=True, timeout=120)
    try:
        value = json.loads([line for line in child.stdout.splitlines() if line.strip()][-1])
    except (ValueError, IndexError):
        value = {"allow": True, "reasons": [child.stdout + child.stderr]}
    reasons = " ".join(value.get("reasons") or [])
    return {"ok": value.get("allow") is False and "owned by session proc-a" in reasons,
            "want_map": "CURRENT", "kind": "negative",
            "expected": "a second real process cannot write while another owns the task",
            "observed": {"allow": value.get("allow"), "reasons": value.get("reasons"),
                         "owner": value.get("owner"), "exit": child.returncode,
                         "map_state": pm.verify("race", root)["state"]}}


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
            "want_map": "CURRENT", "kind": "healthy",
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
            "want_map": "PROTECTED", "kind": "negative",
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
    return {"ok": codes.count(0) == 1, "want_map": None, "kind": "healthy",
            "expected": "two real processes never become the writer together",
            "observed": {"codes": codes,
                         "blocked": sum("BLOCKED" in text for text in outputs)}}


SCENARIOS = [
    ("new_project_healthy", scenario_new_project_healthy),
    ("new_project_unregistered", scenario_new_project_unregistered),
    ("missing_decision_negative", scenario_missing_decision_negative),
    ("python_stale_negative", scenario_python_stale_negative),
    ("python_healthy", scenario_python_healthy),
    ("web_partial_coverage", scenario_web_partial),
    ("desktop_unsupported", scenario_desktop_unsupported),
    ("backend_contract", scenario_backend_contract),
    ("security_secret_negative", scenario_security_secret_negative),
    ("security_placeholder_healthy", scenario_security_placeholder_healthy),
    ("security_auth_broken", scenario_security_auth_broken),
    ("security_auth_healthy", scenario_security_auth_healthy),
    ("math_oracle_broken", scenario_math_oracle_broken),
    ("math_oracle_healthy", scenario_math_oracle_healthy),
    ("cross_process_ownership", scenario_cross_process_ownership),
    ("multi_project_isolation", scenario_multi_project_isolation),
    ("nested_control_plane_negative", scenario_nested_control_plane_negative),
    ("concurrent_writer", scenario_concurrent_writer),
]


def run() -> dict:
    started = time.time()
    results = []
    for name, fn in SCENARIOS:
        begin = time.time()
        try:
            value = fn()
            results.append({
                "name": name, "kind": value.get("kind", "healthy"),
                "expected": value.get("expected", ""),
                "observed": value.get("observed", {}),
                "want_map": value.get("want_map"),
                "ok": bool(value.get("ok")),
                "duration_seconds": round(time.time() - begin, 3),
            })
        except Exception as exc:  # noqa: BLE001
            results.append({
                "name": name, "kind": "healthy", "expected": "",
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

    value = {
        "schema": SCHEMA, "kind": KIND, "created_at": utc_now(),
        "scenario_version": SCENARIO_VERSION,
        "source": source_meta(),
        "host": {"platform": platform.platform(),
                 "python": platform.python_version()},
        "scenarios": results,
        "scorecard": {
            "scenarios": len(results),
            "passed": sum(item["ok"] for item in results),
            "failed": sum(not item["ok"] for item in results),
            "negative": len(negative),
            "detected": len(detected),
            "detection_rate": round(len(detected) / len(negative), 3) if negative else 0.0,
            "false_pass": len(negative) - len(detected),
            "healthy": len(healthy),
            "false_block": len(false_block),
            "false_block_names": [item["name"] for item in false_block],
            "context_freshness": round(len(fresh) / len(freshness), 3) if freshness else 1.0,
            "map_coverage": coverage,
            "regression": "covered by the vnext_dev/vnext/regression suites, not re-run here",
            "duration_seconds": round(time.time() - started, 3),
            "tool_evidence": "not_applicable: fixtures have no host session",
            "skill_evidence": "not_applicable: fixtures have no host session",
            "model_calls": 0,
            "oracle_scenarios": [item["name"] for item in results
                                 if item["name"].startswith(("math_oracle",
                                                             "security_auth"))],
        },
    }
    value["content_sha256"] = identity(value)
    value["artifact_sha256"] = seal(value)
    return value


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
              f"v{value['scenario_version']}  {card['duration_seconds']}s")
        for item in value["scenarios"]:
            print(f"  {'PASS' if item['ok'] else 'FAIL'}  {item['name']}  "
                  f"[{item['duration_seconds']}s]")
        print(f"  artifact: {out}")
    return 0 if value["scorecard"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
