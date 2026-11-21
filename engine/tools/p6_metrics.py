#!/usr/bin/env python3
"""P6 metrics reader with integrity verdicts.

It reads the artifacts EOS already produces and refuses to treat unverifiable
data as a result:

  gate artifact   trusted only when its log hash matches the log file
  P5 artifact     trusted only when its artifact seal matches
  legacy P5       no seal/version: reported, excluded from trends
  different P5 scenario versions are never compared

Verdicts: VERIFIED, PARTIAL, UNVERIFIED, NO_DATA. Only VERIFIED exits 0.

Commands:
  report [--workspace <dir>] [--project <id>] [--limit N] [--json] [--out F]
  selftest
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402
import p5_benchmark as p5b  # noqa: E402

SCHEMA = 2
KIND = "p6_metrics"
VERIFIED = "VERIFIED"
PARTIAL = "PARTIAL"
UNVERIFIED = "UNVERIFIED"
NO_DATA = "NO_DATA"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_json(path: str) -> tuple:
    try:
        with open(path, encoding="utf-8-sig") as handle:
            value = json.load(handle)
    except OSError:
        return {}, "unreadable"
    except json.JSONDecodeError:
        return {}, "corrupt"
    if not isinstance(value, dict):
        return {}, "wrong_schema"
    return value, "ok"


def verify_gate(workspace: str, value: dict) -> str:
    if value.get("kind") != "quality_gate" or value.get("schema") != 1:
        return "wrong_schema"
    log = value.get("log") or {}
    path = str(log.get("path") or "")
    digest = str(log.get("sha256") or "")
    if not path or not digest:
        return "unverifiable"
    full = path if os.path.isabs(path) else os.path.join(workspace, path)
    if not os.path.isfile(full):
        return "missing_log"
    if sha256_file(full) != digest.upper():
        return "tampered"
    return "ok"


def verify_p5(value: dict) -> str:
    if value.get("kind") != "p5_benchmark" or value.get("schema") != 1:
        return "wrong_schema"
    if not value.get("scenario_version"):
        return "legacy"
    seal = str(value.get("artifact_sha256") or "")
    if not seal:
        return "unsealed"
    if seal.upper() != p5b.seal(value):
        return "tampered"
    return "ok"


def classify(name: str) -> str:
    low = name.lower()
    if any(token in low for token in ("lock", "work_state", "concurrent", "owner")):
        return "concurrency"
    if any(token in low for token in ("guard", "activation", "router", "permission")):
        return "guard_or_routing"
    if any(token in low for token in ("map", "post_write", "project_map")):
        return "map_accuracy"
    if "p5" in low or "benchmark" in low:
        return "benchmark"
    return "gate"


def gates(workspace: str, project: str, limit: int) -> dict:
    directory = os.path.join(workspace, ".agents", "evidence", "gates", project)
    paths = sorted(glob.glob(os.path.join(directory, "gate-*.json")),
                   key=lambda item: os.path.getmtime(item))[-limit:]
    states = Counter()
    verdicts = Counter()
    suites = {}
    repeated = Counter()
    valid = 0
    for path in paths:
        value, state = read_json(path)
        if state == "ok":
            state = verify_gate(workspace, value)
        states[state] += 1
        if state != "ok":
            continue
        valid += 1
        suite = str(value.get("suite") or "unknown")
        item = suites.setdefault(suite, {"runs": 0, "verdicts": Counter(),
                                         "durations": [], "failures": []})
        item["runs"] += 1
        verdict = str(value.get("verdict") or "UNKNOWN")
        item["verdicts"][verdict] += 1
        verdicts[verdict] += 1
        item["durations"].append(sum(float(command.get("duration_seconds") or 0)
                                     for command in value.get("commands") or []))
        failed = list(value.get("failures") or [])
        if failed:
            item["failures"].append(sorted(failed))
            repeated[tuple(sorted(failed))] += 1
    for item in suites.values():
        runs = item["runs"]
        item["pass_rate"] = round(item["verdicts"].get("PASS", 0) / runs, 3) if runs else 0.0
        item["avg_duration_seconds"] = round(sum(item["durations"]) / runs, 3) if runs else 0.0
        item["verdicts"] = dict(item["verdicts"])
        item.pop("durations", None)
    return {"artifacts": len(paths), "valid": valid,
            "invalid": len(paths) - valid, "states": dict(states),
            "verdicts": dict(verdicts), "suites": suites,
            "repeated_failures": [
                {"commands": list(names), "count": count, "category": classify(" ".join(names))}
                for names, count in repeated.most_common(10)]}


def p5_metrics(workspace: str, limit: int) -> dict:
    directory = os.path.join(workspace, ".agents", "evidence", "p5")
    paths = sorted(glob.glob(os.path.join(directory, "p5-*.json")),
                   key=lambda item: os.path.getmtime(item))[-limit:]
    states = Counter()
    valid = []
    for path in paths:
        value, state = read_json(path)
        if state == "ok":
            state = verify_p5(value)
        states[state] += 1
        if state == "ok":
            valid.append(value)
    versions = sorted({str(item.get("scenario_version")) for item in valid})
    current_version = ""
    if valid:
        latest = max(valid, key=lambda item: str(item.get("created_at") or ""))
        current_version = str(latest.get("scenario_version"))
    current, seen, duplicates = [], set(), 0
    historical = 0
    for value in valid:
        if str(value.get("scenario_version")) != current_version:
            historical += 1
            continue
        key = str(value.get("content_sha256") or "")
        if key and key in seen:
            duplicates += 1
            continue
        seen.add(key)
        current.append(value)
    current.sort(key=lambda item: str(item.get("created_at") or ""))
    comparable = len(versions) <= 1
    report = {"artifacts": len(paths), "valid": len(valid),
              "states": dict(states), "versions": versions,
              "scenario_version": current_version, "comparable": comparable,
              "historical": historical, "duplicates_ignored": duplicates,
              "runs": len(current), "latest": {}, "previous": {}, "delta": {}}
    if not current:
        report["note"] = ("no verified P5 artifact for the current scenario version"
                          if valid else "no P5 artifact was found")
        return report
    latest = current[-1]
    card = latest.get("scorecard") or {}
    report["latest"] = {"at": latest.get("created_at"),
                        "scenario_version": latest.get("scenario_version"),
                        "commit": (latest.get("source") or {}).get("commit"),
                        "detection_rate": card.get("detection_rate", 0.0),
                        "false_pass": card.get("false_pass", 0),
                        "false_block": card.get("false_block", 0),
                        "false_block_names": card.get("false_block_names") or [],
                        "context_freshness": card.get("context_freshness", 0.0),
                        "duration_seconds": card.get("duration_seconds", 0.0)}
    if len(current) > 1:
        previous = current[-2].get("scorecard") or {}
        report["previous"] = {"at": current[-2].get("created_at"),
                              "detection_rate": previous.get("detection_rate", 0.0),
                              "false_pass": previous.get("false_pass", 0),
                              "false_block": previous.get("false_block", 0)}
        if comparable:
            report["delta"] = {
                key: round(float(report["latest"].get(key, 0)) -
                           float(report["previous"].get(key, 0)), 3)
                for key in ("detection_rate", "false_pass", "false_block")}
        else:
            report["delta_note"] = "versions differ; deltas are not reported"
    return report


def proposals(gates_value: dict, p5_value: dict) -> list:
    items = []
    for repeated in gates_value.get("repeated_failures") or []:
        names = repeated.get("commands") or []
        category = repeated.get("category") or classify(" ".join(names))
        items.append({
            "category": category,
            "evidence": {"commands": names, "count": repeated.get("count")},
            "hypothesis": f"{', '.join(names)} fails repeatedly in the {category} area.",
            "action": "reproduce the failure, add a regression test, then fix the cause",
            "verification": "the same gate must pass twice with the regression test present",
            "auto_apply": False, "requires_verification": True,
        })
    latest = p5_value.get("latest") or {}
    for name in latest.get("false_block_names") or []:
        items.append({
            "category": "false_block",
            "evidence": {"scenario": name, "commit": latest.get("commit")},
            "hypothesis": f"the benchmark blocked a healthy scenario: {name}.",
            "action": "inspect the fixture and the gate; fix the fixture or the policy",
            "verification": "the scenario must pass and the negative scenarios must stay detected",
            "auto_apply": False, "requires_verification": True,
        })
    return items


def verdict_of(gates_value: dict, p5_value: dict) -> str:
    found = gates_value.get("artifacts", 0) + p5_value.get("artifacts", 0)
    if found == 0:
        return NO_DATA
    gate_states = gates_value.get("states") or {}
    p5_states = p5_value.get("states") or {}
    if gates_value.get("valid", 0) == 0 and p5_value.get("runs", 0) == 0:
        return UNVERIFIED
    if gate_states.get("tampered") or p5_states.get("tampered"):
        return PARTIAL
    if p5_value.get("artifacts", 0) and p5_value.get("runs", 0) == 0:
        return PARTIAL
    if gates_value.get("invalid", 0) or any(
            state in ("corrupt", "wrong_schema") for state in p5_states):
        return PARTIAL
    return VERIFIED


def report(workspace: str, project: str, limit: int = 20) -> dict:
    gates_value = gates(workspace, project, limit)
    p5_value = p5_metrics(workspace, limit)
    return {"schema": SCHEMA, "kind": KIND, "created_at": utc_now(),
            "project": project, "workspace": workspace,
            "verdict": verdict_of(gates_value, p5_value),
            "gates": gates_value, "p5": p5_value,
            "proposals": proposals(gates_value, p5_value)}


EXIT = {VERIFIED: 0, PARTIAL: 2, UNVERIFIED: 3, NO_DATA: 4}


def _write(path: str, value: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(prog="p6_metrics.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("report")
    p.add_argument("--workspace")
    p.add_argument("--project", default="eos")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--out", default="")
    p.add_argument("--json", action="store_true")
    sub.add_parser("selftest")
    args = parser.parse_args()
    if args.command == "selftest":
        return selftest()
    workspace = ep.resolve_workspace(args.workspace)
    value = report(workspace, args.project, args.limit)
    out = args.out or os.path.join(workspace, ".agents", "evidence", "p6",
                                   f"metrics-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    _write(out, value)
    if args.json:
        print(json.dumps(value, ensure_ascii=True))
    else:
        print(f"P6: {value['verdict']}  (gates {value['gates']['valid']}/"
              f"{value['gates']['artifacts']} valid, p5 {value['p5']['runs']} verified run(s))")
        for suite, item in sorted(value["gates"]["suites"].items()):
            print(f"  {suite:<12} runs={item['runs']:<3} pass={item['pass_rate']} "
                  f"verdicts={item['verdicts']}")
        for state, count in sorted((value["gates"]["states"] or {}).items()):
            if state != "ok":
                print(f"  gate state: {state} x{count}")
        for state, count in sorted((value["p5"]["states"] or {}).items()):
            if state != "ok":
                print(f"  p5 state: {state} x{count}")
        if value["p5"].get("latest"):
            latest = value["p5"]["latest"]
            print(f"  p5 {value['p5']['scenario_version']} runs={value['p5']['runs']} "
                  f"detection={latest['detection_rate']} false_pass={latest['false_pass']} "
                  f"false_block={latest['false_block']}")
            if value["p5"].get("delta"):
                print(f"  p5 delta: {value['p5']['delta']}")
            elif value["p5"].get("delta_note"):
                print(f"  {value['p5']['delta_note']}")
        for item in value["proposals"]:
            print(f"  proposal [{item['category']}]: {item['hypothesis']}")
        print(f"  artifact: {out}")
    return EXIT.get(value["verdict"], 1)


def selftest() -> int:
    root = tempfile.mkdtemp(prefix="eos-p6-")
    gates_dir = os.path.join(root, ".agents", "evidence", "gates", "eos")
    p5_dir = os.path.join(root, ".agents", "evidence", "p5")
    os.makedirs(gates_dir)
    os.makedirs(p5_dir)
    passed = failed = 0

    def case(name, condition, detail=""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"PASS  {name}  [{detail}]")
        else:
            failed += 1
            print(f"FAIL  {name}: {detail}")

    empty = report(root, "eos")
    case("no artifacts is NO_DATA, never PASS", empty["verdict"] == NO_DATA,
         empty["verdict"])
    case("NO_DATA exits non-zero", EXIT[NO_DATA] != 0, str(EXIT[NO_DATA]))

    log_path = os.path.join(gates_dir, "gate-unit-1.log")
    with open(log_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("# log\n")
    gate_ok = {"schema": 1, "kind": "quality_gate", "created_at": "2026-01-01T00:00:00Z",
               "project": "eos", "suite": "unit", "verdict": "FAIL",
               "commands": [{"duration_seconds": 1.0}], "failures": ["unit_test"],
               "log": {"path": ".agents/evidence/gates/eos/gate-unit-1.log",
                       "sha256": sha256_file(log_path)}}
    _write(os.path.join(gates_dir, "gate-unit-1.json"), gate_ok)

    gate_bad = dict(gate_ok)
    gate_bad["log"] = {"path": ".agents/evidence/gates/eos/gate-unit-1.log",
                       "sha256": "0" * 64}
    _write(os.path.join(gates_dir, "gate-unit-2.json"), gate_bad)
    _write(os.path.join(gates_dir, "gate-unit-3.json"), {"broken": True})
    with open(os.path.join(gates_dir, "gate-unit-4.json"), "w", encoding="utf-8") as handle:
        handle.write("{not json")

    def p5_artifact(version: str, commit: str, passed_count: int, created: str) -> dict:
        value = {"schema": 1, "kind": "p5_benchmark", "created_at": created,
                 "scenario_version": version, "source": {"commit": commit},
                 "scenarios": [{"name": "demo", "kind": "healthy", "ok": passed_count == 1,
                                "observed": {}}],
                 "scorecard": {"scenarios": 1, "passed": passed_count,
                               "negative": 1, "detected": passed_count,
                               "detection_rate": float(passed_count),
                               "false_pass": 1 - passed_count, "false_block": 0,
                               "false_block_names": [],
                               "context_freshness": 1.0, "duration_seconds": 1.0}}
        value["content_sha256"] = p5b.identity(value)
        value["artifact_sha256"] = p5b.seal(value)
        return value

    current = p5_artifact("v1", "c1", 0, "2026-01-02T00:00:00Z")
    duplicate = p5_artifact("v1", "c1", 0, "2026-01-02T00:01:00Z")
    old = p5_artifact("v0", "c0", 1, "2026-01-01T00:00:00Z")
    legacy = {"schema": 1, "kind": "p5_benchmark", "created_at": "2025-12-01T00:00:00Z",
              "scorecard": {"scenarios": 1, "passed": 1}}
    for name, value in (("p5-current", current), ("p5-duplicate", duplicate),
                        ("p5-old", old), ("p5-legacy", legacy)):
        _write(os.path.join(p5_dir, f"{name}.json"), value)

    value = report(root, "eos")
    case("a tampered gate is not trusted",
         value["gates"]["states"].get("tampered") == 1, value["gates"]["states"])
    case("a corrupt artifact is separated from a wrong schema",
         value["gates"]["states"].get("corrupt") == 1, value["gates"]["states"])
    case("the verdict is PARTIAL when integrity fails", value["verdict"] == PARTIAL,
         value["verdict"])
    case("the duplicate run is counted once", value["p5"]["duplicates_ignored"] == 1,
         value["p5"]["duplicates_ignored"])
    case("the legacy artifact is reported and excluded",
         value["p5"]["states"].get("legacy") == 1, value["p5"]["states"])
    case("the older scenario version is historical",
         value["p5"]["historical"] == 1, value["p5"]["historical"])
    case("different versions are not compared", value["p5"]["comparable"] is False,
         value["p5"]["comparable"])
    case("a repeated failure becomes a proposal",
         any(item["category"] == "gate" for item in value["proposals"]),
         value["proposals"])
    case("no proposal is auto-applied",
         all(item["auto_apply"] is False for item in value["proposals"]), "auto_apply")

    os.remove(os.path.join(gates_dir, "gate-unit-2.json"))
    os.remove(os.path.join(gates_dir, "gate-unit-3.json"))
    os.remove(os.path.join(gates_dir, "gate-unit-4.json"))
    clean = report(root, "eos")
    case("a valid gate and a valid P5 run verify", clean["verdict"] == VERIFIED,
         clean["verdict"])

    print(f"\n{passed}/{passed + failed} cases passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
