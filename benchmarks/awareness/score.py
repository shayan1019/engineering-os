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
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scenarios import SCENARIOS  # noqa: E402

IGNORE = (".agents", ".eos-benchmark.json", "__pycache__", ".pytest_cache",
          ".mypy_cache", "node_modules", ".git", ".opencode", ".eos", "AGENTS.md")
WEIGHTS = {"discovery": 0.25, "judgment": 0.20, "correctness": 0.20,
           "self_correction": 0.05, "scope": 0.10, "regression": 0.10,
           "closure": 0.10}
FALSE_ALARM_PENALTY = 0.10


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_of(agent: dict, include_evidence: bool = True) -> str:
    chunks = []
    for finding in agent.get("findings") or []:
        if isinstance(finding, dict):
            chunks.append(str(finding.get("claim") or ""))
            if include_evidence:
                chunks.extend(str(item) for item in (finding.get("evidence") or []))
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


def claim_text(agent: dict) -> str:
    """Reasoning text only: evidence paths must not create a signal match."""
    return text_of(agent, include_evidence=False)


def agent_evidence(agent: dict) -> list:
    entries = []
    for finding in agent.get("findings") or []:
        if isinstance(finding, dict):
            entries.extend(str(item) for item in (finding.get("evidence") or []))
    return entries


EVIDENCE_RE = re.compile(r"^(?P<path>[^\s:]+?)(?::(?P<line>\d+))?$")
PATH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\\-]*\.(?:py|html|css|js|mjs|md|json|toml|yml|yaml)")


def evidence_parts(entry: str) -> tuple:
    value = str(entry or "").strip().replace("\\", "/")
    if not value or " " in value:
        return "", None
    match = EVIDENCE_RE.match(value)
    if not match:
        return "", None
    path = match.group("path").lstrip("./")
    line = int(match.group("line")) if match.group("line") else None
    return path, line


def evidence_ok(entries, tree: Path, allowed=None) -> bool:
    """A citation counts only when it names a real file (and a real line)."""
    allowed_norm = {str(item).replace("\\", "/").lstrip("./") for item in (allowed or [])}
    for entry in entries or []:
        path, line = evidence_parts(entry)
        if not path:
            continue
        if allowed_norm and not any(path == item or path.startswith(item.rstrip("/") + "/")
                                    for item in allowed_norm):
            continue
        target = tree / path
        if not target.is_file():
            continue
        if line is not None:
            try:
                lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            if line < 1 or line > len(lines) or not lines[line - 1].strip():
                continue
        return True
    return False


def extract_paths(text: str) -> list:
    found = []
    for match in PATH_RE.findall(text or ""):
        value = match.strip("`'\"()[]{}.,;:").replace("\\", "/").lstrip("./")
        if value and value not in found:
            found.append(value)
    return found


def _json_candidates(text: str):
    for match in re.finditer(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text or "", re.I):
        yield match.group(1)
    for start, char in enumerate(text or ""):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
            else:
                if current == '"':
                    in_string = True
                elif current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                    if depth == 0:
                        yield text[start:index + 1]
                        break


def extract_report(text: str):
    for candidate in reversed(list(_json_candidates(text or ""))):
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict) and ("findings" in value or "decision" in value):
            return value
    return None


def fallback_agent(text: str, blind_spots=None) -> dict:
    """A blocked run cannot write the report file; mine the final message."""
    report = extract_report(text)
    if report:
        return report
    paths = extract_paths(text)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]
    findings = []
    for paragraph in paragraphs:
        lowered = paragraph.lower()
        if blind_spots and not any(signal.lower() in lowered
                                   for item in blind_spots
                                   for signal in item.get("signals") or []):
            continue
        evidence = extract_paths(paragraph)
        findings.append({"severity": "major", "claim": paragraph[:1200],
                         "evidence": evidence or paths[:5]})
    if not findings and text:
        findings = [{"severity": "major", "claim": text[:1200], "evidence": paths[:5]}]
    return {
        "findings": findings,
        "decision": {"approach": text[:2000], "alternatives": [],
                     "assumptions": [], "why": text[:2000]},
        "self_correction": [],
        "scope": {}, "closure": {},
    }


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
    final_text_raw = str(meta.get("final_text") or "")
    final_text = final_text_raw.lower()
    blocked_hint = ""
    if not report_present and final_text and "blocked" in final_text:
        if any(word in final_text for word in ("approval", "critical", "guard", "write gate")):
            blocked_hint = "the guard blocked the write; the agent reported BLOCKED"
    if not agent and final_text_raw:
        # The final message is still engineering evidence. The structured file
        # is preferred, but a missing file must not erase the reasoning.
        agent = fallback_agent(final_text_raw, scenario.get("blind_spots") or [])
    text = claim_text(agent)
    full_text = text_of(agent)
    blind = scenario["blind_spots"]
    changed = changed_files(scenario["fixture"], tree)
    citations = agent_evidence(agent)
    matched = []
    for item in blind:
        if not any(signal.lower() in text for signal in item.get("signals") or []):
            continue
        if not evidence_ok(citations, tree, item.get("evidence") or []):
            continue
        matched.append(item)
    total_weight = sum(float(item.get("weight") or 1) for item in blind) or 1.0
    diagnosis = sum(float(item.get("weight") or 1) for item in matched) / total_weight
    discovery = diagnosis

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
    hits = sum(1 for signal in signals if signal in full_text)
    judgment = (0.4 if len(alternatives) >= 2 else 0.0)
    judgment += 0.2 if assumptions else 0.0
    judgment += 0.2 if why else 0.0
    judgment += 0.2 if hits >= minimum else 0.0

    checks = run_checks(scenario, tree)
    passed = [item for item in checks if item.get("ok")]
    application = len(passed) / max(1, len(checks))
    correctness = application
    regression = run_regression(scenario, tree)
    reg_passed = [item for item in regression if item.get("ok")]
    regression_score = (len(reg_passed) / len(regression)) if regression else 1.0

    changed = changed_files(scenario["fixture"], tree)
    violations = [item for item in changed
                  if not allowed(item, scenario.get("allowed_paths") or ["*"])]
    forbidden = [item for item in changed
                 if allowed(item, scenario.get("forbidden_paths") or [])]
    scope = 0.0 if forbidden else max(0.0, 1.0 - len(violations) / max(1, len(changed)))

    blocked_reason = ""
    if blocked_hint and not any(allowed(item, scenario.get("allowed_paths") or ["*"])
                                for item in changed):
        blocked_reason = blocked_hint

    self_entries = [item for item in agent.get("self_correction") or []
                    if isinstance(item, dict) and item.get("wrong") and item.get("fix")
                    and item.get("detected_by")
                    and evidence_ok(item.get("evidence") or [], tree)]
    self_correction = 1.0 if self_entries else 0.0

    closure = 1.0 if checks and all(item.get("ok") for item in run_checks(scenario, tree)) else 0.0

    authorization = "BLOCKED" if blocked_reason else ("ALLOWED" if changed else "UNKNOWN")

    overall = (WEIGHTS["discovery"] * diagnosis + WEIGHTS["judgment"] * judgment
               + WEIGHTS["correctness"] * application
               + WEIGHTS["self_correction"] * self_correction
               + WEIGHTS["scope"] * scope + WEIGHTS["regression"] * regression_score
               + WEIGHTS["closure"] * closure
               - FALSE_ALARM_PENALTY * false_alarm_rate)
    meta = meta or {}
    host_valid = meta.get("valid_host")
    invalid_reason = ""
    if host_valid is False:
        overall = 0.0
        invalid_reason = "host isolation failed: arm plugin or engine was not the loaded one"
    return {
        "schema": 1, "scenario": scenario.get("id"), "domain": scenario.get("domain"),
        "overall": round(max(0.0, min(1.0, overall)), 4),
        "host_valid": host_valid if host_valid is not None else True,
        "invalid_reason": invalid_reason,
        "discovery": round(discovery, 4),
        "diagnosis": round(diagnosis, 4),
        "judgment": round(judgment, 4),
        "correctness": round(correctness, 4),
        "application": round(application, 4),
        "authorization": authorization,
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
        "blocked": bool(blocked_reason),
        "blocked_reason": blocked_reason,
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
    print(f"  diagnosis={value['diagnosis']:.2f} application={value['application']:.2f} "
          f"authorization={value['authorization']} self_correction={value['self_correction']:.2f}")
    if value.get("blocked"):
        print(f"  BLOCKED: {value.get('blocked_reason')}")
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
    if old.get("blocked") or new.get("blocked"):
        print(f"  blocked        old={old.get('blocked')}  new={new.get('blocked')} "
              f"(delta is not a reasoning comparison)")
    for key in ("diagnosis", "application", "judgment", "self_correction",
                "scope", "regression", "closure"):
        print(f"  {key:14} old={old[key]:.2f}  new={new[key]:.2f}")
    print(f"  authorization  old={old['authorization']}  new={new['authorization']}")
    return 0


def cmd_adversarial(args) -> int:
    """A keyword-only answer must not earn discovery or correctness."""
    scenario = SCENARIOS.get(args.scenario)
    if not scenario:
        raise SystemExit(f"unknown scenario: {args.scenario}")
    root = Path(tempfile.mkdtemp(prefix=f"eos-adversarial-{args.scenario}-"))
    materialise(scenario, root)
    text = " ".join(signal for blind in scenario["blind_spots"]
                    for signal in blind.get("signals") or [])
    agent = {
        "findings": [{"severity": "blocker", "claim": text, "evidence": ["fake"]}],
        "decision": {"approach": text, "alternatives": ["a", "b"],
                     "assumptions": ["a"], "why": text},
        "self_correction": [{"wrong": "x", "detected_by": "y", "fix": "z",
                             "evidence": ["fake"]}],
        "scope": {}, "closure": {},
    }
    value = score_tree(scenario, root, agent, {})
    ok = value["discovery"] == 0.0 and value["correctness"] < 0.5
    print(f"ADVERSARIAL {args.scenario}: discovery={value['discovery']:.2f} "
          f"correctness={value['correctness']:.2f} overall={value['overall']:.2f} "
          f"{'PASS' if ok else 'FAIL'}")
    shutil.rmtree(root, ignore_errors=True)
    return 0 if ok else 1


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
    p = sub.add_parser("adversarial")
    p.add_argument("--scenario", required=True)
    p.set_defaults(func=cmd_adversarial)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
