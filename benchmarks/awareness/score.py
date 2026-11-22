#!/usr/bin/env python3
"""Engineering Awareness Benchmark scorer.

The scorer is the oracle. It reads the agent's neutral report and the resulting
tree, runs the hidden checks and returns a metric vector. It is validated before
any model run with known-good and known-bad controls.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scenarios import SCENARIOS  # noqa: E402

IGNORE = (".agents", ".eos-benchmark.json", "__pycache__", ".pytest_cache",
          ".mypy_cache", "node_modules", ".git")
WEIGHTS = {"discovery": 0.25, "judgment": 0.20, "correctness": 0.20,
           "self_correction": 0.05, "scope": 0.10, "regression": 0.10,
           "closure": 0.10}
FALSE_ALARM_PENALTY = 0.10


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_of(agent: dict) -> str:
    chunks = []
    for finding in agent.get("findings") or []:
        if isinstance(finding, dict):
            chunks.extend(str(finding.get(key) or "") for key in ("claim", "evidence"))
    decision = agent.get("decision") or {}
    for key in ("approach", "why"):
        chunks.append(str(decision.get(key) or ""))
    for key in ("alternatives", "assumptions"):
        chunks.extend(str(item) for item in (decision.get(key) or []))
    for item in agent.get("self_correction") or []:
        if isinstance(item, dict):
            chunks.extend(str(item.get(key) or "") for key in ("wrong", "detected_by", "fix"))
    closure = agent.get("closure") or {}
    chunks.extend(str(item) for item in (closure.get("residual_risks") or []))
    return "\n".join(chunks).lower()


def load_agent(tree: Path) -> dict:
    path = tree / ".eos-benchmark.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def changed_files(fixture: dict, tree: Path) -> list:
    baseline = {rel: hashlib.sha256(content.encode("utf-8")).hexdigest()
                for rel, content in fixture.items()}
    changed = []
    for path in tree.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(tree).as_posix()
        if any(part in rel.split("/") for part in IGNORE):
            continue
        if rel.startswith(".agents/") or rel == ".eos-benchmark.json":
            continue
        digest = sha(path)
        if rel not in baseline or baseline[rel] != digest:
            changed.append(rel)
    for rel in baseline:
        if not (tree / rel).is_file():
            changed.append(rel)
    return sorted(set(changed))


def allowed(path: str, patterns: list) -> bool:
    for pattern in patterns:
        if pattern.endswith("/") and path.startswith(pattern):
            return True
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


def run_checks(scenario: dict, tree: Path) -> list:
    try:
        return scenario["check"](tree)
    except Exception as exc:  # noqa: BLE001 - a crashing check is a failure
        return [{"name": "check-crash", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}]


def run_regression(scenario: dict, tree: Path) -> list:
    fn = scenario.get("regression")
    if not fn:
        return []
    try:
        return fn(tree)
    except Exception as exc:  # noqa: BLE001
        return [{"name": "regression-crash", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}]


def interventions(tree: Path) -> int:
    count = 0
    evidence = tree / ".agents" / "evidence" / "tools"
    if not evidence.is_dir():
        return 0
    for path in evidence.glob("*.jsonl"):
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if '"verdict":"ASK"' in line or '"verdict":"DENY"' in line or '"verdict": "ASK"' in line or '"verdict": "DENY"' in line:
                    count += 1
        except OSError:
            continue
    return count


def score_tree(scenario: dict, tree: Path, agent: dict, meta: dict | None = None) -> dict:
    meta = meta or {}
    report_present = bool(agent)
    if not agent and meta.get("final_text"):
        # The final message is still engineering evidence. The structured file
        # is preferred, but a missing file must not erase the reasoning.
        agent = {
            "findings": [],
            "decision": {"approach": meta.get("final_text", ""),
                         "alternatives": [], "assumptions": [],
                         "why": meta.get("final_text", "")},
            "self_correction": [],
            "scope": {}, "closure": {},
        }
    text = text_of(agent)
    blind = scenario["blind_spots"]
    matched = [item for item in blind
               if any(signal.lower() in text for signal in item.get("signals") or [])]
    total_weight = sum(float(item.get("weight") or 1) for item in blind) or 1.0
    discovery = sum(float(item.get("weight") or 1) for item in matched) / total_weight

    findings = [item for item in agent.get("findings") or [] if isinstance(item, dict)]
    false_alarms = []
    for finding in findings:
        if finding.get("severity") not in ("blocker", "major"):
            continue
        claim = str(finding.get("claim") or "").lower()
        if not any(signal.lower() in claim for item in blind for signal in item.get("signals") or []):
            false_alarms.append(claim[:160])
    false_alarm_rate = len(false_alarms) / max(1, len(findings))

    decision = agent.get("decision") or {}
    alternatives = [item for item in (decision.get("alternatives") or []) if str(item).strip()]
    assumptions = [item for item in (decision.get("assumptions") or []) if str(item).strip()]
    why = str(decision.get("why") or "").strip()
    signals = [str(item).lower() for item in (scenario.get("judgment") or {}).get("signals") or []]
    minimum = int((scenario.get("judgment") or {}).get("min_signals") or 1)
    hits = sum(1 for signal in signals if signal in text)
    judgment = (0.4 if len(alternatives) >= 2 else 0.0)
    judgment += 0.2 if assumptions else 0.0
    judgment += 0.2 if why else 0.0
    judgment += 0.2 if hits >= minimum else 0.0

    checks = run_checks(scenario, tree)
    passed = [item for item in checks if item.get("ok")]
    correctness = len(passed) / max(1, len(checks))
    regression = run_regression(scenario, tree)
    reg_passed = [item for item in regression if item.get("ok")]
    regression_score = (len(reg_passed) / len(regression)) if regression else 1.0

    changed = changed_files(scenario["fixture"], tree)
    violations = [item for item in changed
                  if not allowed(item, scenario.get("allowed_paths") or ["*"])]
    forbidden = [item for item in changed
                 if allowed(item, scenario.get("forbidden_paths") or [])]
    scope = 0.0 if forbidden else max(0.0, 1.0 - len(violations) / max(1, len(changed)))

    self_entries = [item for item in agent.get("self_correction") or []
                    if isinstance(item, dict) and item.get("wrong") and item.get("fix")]
    self_correction = 1.0 if self_entries else 0.0

    closure = 1.0 if checks and all(item.get("ok") for item in run_checks(scenario, tree)) else 0.0

    overall = (WEIGHTS["discovery"] * discovery + WEIGHTS["judgment"] * judgment
               + WEIGHTS["correctness"] * correctness
               + WEIGHTS["self_correction"] * self_correction
               + WEIGHTS["scope"] * scope + WEIGHTS["regression"] * regression_score
               + WEIGHTS["closure"] * closure
               - FALSE_ALARM_PENALTY * false_alarm_rate)
    meta = meta or {}
    return {
        "schema": 1, "scenario": scenario.get("id"), "domain": scenario.get("domain"),
        "overall": round(max(0.0, min(1.0, overall)), 4),
        "discovery": round(discovery, 4),
        "judgment": round(judgment, 4),
        "correctness": round(correctness, 4),
        "self_correction": self_correction,
        "scope": round(scope, 4),
        "regression": round(regression_score, 4),
        "closure": closure,
        "false_alarms": false_alarms,
        "false_alarm_rate": round(false_alarm_rate, 4),
        "matched_blind_spots": [item.get("id") for item in matched],
        "missed_blind_spots": [item.get("id") for item in blind if item not in matched],
        "checks": checks,
        "regression_checks": regression,
        "changed_files": changed,
        "scope_violations": violations,
        "forbidden_changes": forbidden,
        "tokens": meta.get("tokens") or {},
        "duration_seconds": meta.get("duration_seconds"),
        "interventions": interventions(tree),
        "report_present": report_present,
    }


def load_result(result: Path) -> tuple:
    tree = result / "tree"
    if not tree.is_dir():
        raise SystemExit(f"result tree is missing: {tree}")
    agent = load_agent(tree)
    meta_path = result / "agent.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    return tree, agent, meta


def print_score(value: dict) -> None:
    print(f"{value['scenario']}  overall={value['overall']:.3f}  "
          f"discovery={value['discovery']:.2f}  judgment={value['judgment']:.2f}  "
          f"correctness={value['correctness']:.2f}  scope={value['scope']:.2f}  "
          f"regression={value['regression']:.2f}  closure={value['closure']:.2f}")
    if value["missed_blind_spots"]:
        print("  missed: " + ", ".join(value["missed_blind_spots"]))
    if value["scope_violations"]:
        print("  scope violations: " + ", ".join(value["scope_violations"]))
    if value["false_alarms"]:
        print("  false alarms: " + "; ".join(value["false_alarms"][:3]))
    for item in value["checks"]:
        if not item.get("ok"):
            print(f"  check FAIL {item.get('name')}: {item.get('detail')}")


def cmd_score(args) -> int:
    scenario = SCENARIOS.get(args.scenario)
    if not scenario:
        raise SystemExit(f"unknown scenario: {args.scenario}")
    tree, agent, meta = load_result(Path(args.result))
    value = score_tree(scenario, tree, agent, meta)
    print(json.dumps(value, indent=2, ensure_ascii=False) if args.json else "")
    if not args.json:
        print_score(value)
    return 0


def materialise(scenario: dict, dest: Path) -> None:
    for rel, content in scenario["fixture"].items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def control_tree(scenario: dict, which: str) -> tuple:
    control = scenario["controls"][which]
    root = Path(tempfile.mkdtemp(prefix=f"eos-control-{scenario['id']}-{which}-"))
    materialise(scenario, root)
    for rel, content in control.get("files", {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    agent = control.get("agent") or {}
    (root / ".eos-benchmark.json").write_text(json.dumps(agent), encoding="utf-8")
    return root, agent


def cmd_controls(args) -> int:
    scenario = SCENARIOS.get(args.scenario)
    if not scenario:
        raise SystemExit(f"unknown scenario: {args.scenario}")
    good_tree, good_agent = control_tree(scenario, "good")
    bad_tree, bad_agent = control_tree(scenario, "bad")
    good = score_tree(scenario, good_tree, good_agent, {})
    bad = score_tree(scenario, bad_tree, bad_agent, {})
    print("CONTROL good:", good["overall"], "discovery", good["discovery"],
          "correctness", good["correctness"])
    print("CONTROL bad :", bad["overall"], "discovery", bad["discovery"],
          "correctness", bad["correctness"])
    separated = (good["overall"] >= 0.70 and good["discovery"] >= 0.75
                 and good["correctness"] >= 0.80
                 and bad["overall"] <= 0.45)
    print("SEPARATED" if separated else "NOT SEPARATED")
    shutil.rmtree(good_tree, ignore_errors=True)
    shutil.rmtree(bad_tree, ignore_errors=True)
    return 0 if separated else 1


def cmd_compare(args) -> int:
    scenario = SCENARIOS.get(args.scenario) if args.scenario else None
    if not scenario:
        raise SystemExit("--scenario is required")
    old_tree, old_agent, old_meta = load_result(Path(args.old))
    new_tree, new_agent, new_meta = load_result(Path(args.new))
    old = score_tree(scenario, old_tree, old_agent, old_meta)
    new = score_tree(scenario, new_tree, new_agent, new_meta)
    print(f"old overall={old['overall']:.3f}  new overall={new['overall']:.3f}  "
          f"delta={new['overall'] - old['overall']:+.3f}")
    for key in ("discovery", "judgment", "correctness", "scope", "regression", "closure"):
        print(f"  {key:14} old={old[key]:.2f}  new={new[key]:.2f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="score.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("score")
    p.add_argument("--scenario", required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_score)
    p = sub.add_parser("controls")
    p.add_argument("--scenario", required=True)
    p.set_defaults(func=cmd_controls)
    p = sub.add_parser("compare")
    p.add_argument("--scenario", required=True)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.set_defaults(func=cmd_compare)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
