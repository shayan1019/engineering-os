#!/usr/bin/env python3
"""P6 metrics reader with evidence trust states.

Three different questions are kept apart:

  integrity      the file was not modified after it was written
  content match  the gate JSON agrees with the log it points at; the P5 result
                 hash agrees with the artifact content
  provenance     who produced the artifact (unsigned here)

Only integrity + content match produce VERIFIED. Provenance stays UNSIGNED
because no signing key is configured; VERIFIED never means "trusted author".

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
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402
import p5_benchmark as p5b  # noqa: E402

SCHEMA = 3
KIND = "p6_metrics"
VERIFIED = "VERIFIED"
PARTIAL = "PARTIAL"
UNVERIFIED = "UNVERIFIED"
NO_DATA = "NO_DATA"
HEADER = re.compile(r"^# EOS gate (\S+)/(\S+) (PASS|FAIL|BLOCKED|NOT_TESTED)\s*$")
BLOCK = re.compile(r"^\$ (.*?)  -> rc=(-?\d+) timeout=(True|False) ")


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


def parse_gate_log(path: str) -> dict:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return {}
    header = None
    if lines:
        match = HEADER.match(lines[0])
        if match:
            header = {"project": match.group(1), "suite": match.group(2),
                      "verdict": match.group(3)}
    blocks = []
    for line in lines:
        match = BLOCK.match(line)
        if match:
            blocks.append({"argv": match.group(1), "rc": int(match.group(2)),
                           "timed_out": match.group(3) == "True"})
    return {"header": header, "blocks": blocks}


def verify_gate(workspace: str, value: dict) -> tuple:
    if value.get("kind") != "quality_gate" or value.get("schema") != 1:
        return "wrong_schema", "schema"
    log = value.get("log") or {}
    path = str(log.get("path") or "")
    digest = str(log.get("sha256") or "")
    if not path or not digest:
        return "unverifiable", "no log reference"
    full = path if os.path.isabs(path) else os.path.join(workspace, path)
    if not os.path.isfile(full):
        return "missing_log", "log file is missing"
    if sha256_file(full) != digest.upper():
        return "tampered", "log hash mismatch"
    parsed = parse_gate_log(full)
    header = parsed.get("header")
    if not header:
        return "unverifiable", "log header is not parseable"
    if header["suite"] != str(value.get("suite")):
        return "content_mismatch", "log suite differs from the artifact"
    if header["verdict"] != str(value.get("verdict")):
        return "content_mismatch", "log verdict differs from the artifact"
    commands = value.get("commands") or []
    blocks = parsed.get("blocks") or []
    if len(blocks) != len(commands):
        return "content_mismatch", "command count differs from the log"
    for index, command in enumerate(commands):
        block = blocks[index]
        if block["argv"] != " ".join(command.get("argv") or []):
            return "content_mismatch", f"argv differs at command {index}"
        if block["rc"] != int(command.get("exit_code") or 0):
            return "content_mismatch", f"exit code differs at command {index}"
        if block["timed_out"] != bool(command.get("timed_out")):
            return "content_mismatch", f"timeout flag differs at command {index}"
    failures = list(value.get("failures") or [])
    by_name = {str(item.get("name")): item for item in commands}
    for name in failures:
        item = by_name.get(name)
        if not item or int(item.get("exit_code") or 0) == 0:
            return "content_mismatch", f"failure {name} has no failed command"
    if value.get("verdict") == "PASS" and failures:
        return "content_mismatch", "PASS with failures"
    if value.get("verdict") == "FAIL" and not failures:
        return "content_mismatch", "FAIL without a failed command"
    return "ok", "log and artifact agree"


def verify_p5(value: dict) -> tuple:
    if value.get("kind") != "p5_benchmark" or value.get("schema") != 1:
        return "wrong_schema", "schema"
    if not value.get("scenario_version"):
        return "legacy", "no scenario version"
    seal = str(value.get("artifact_sha256") or "")
    if not seal:
        return "unsealed", "no artifact seal"
    if seal.upper() != p5b.seal(value):
        return "tampered", "artifact seal mismatch"
    content = str(value.get("content_sha256") or "")
    if not content:
        return "unsealed", "no content hash"
    if content.upper() != p5b.identity(value):
        return "content_mismatch", "result content hash mismatch"
    return "ok", "seal and content hash agree"


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
    details = []
    suites = {}
    failures = {}
    latest_at = ""
    valid = 0
    for path in paths:
        value, state = read_json(path)
        detail = ""
        if state == "ok":
            state, detail = verify_gate(workspace, value)
        states[state] += 1
        details.append({"file": os.path.basename(path), "state": state, "detail": detail})
        if state != "ok":
            continue
        valid += 1
        suite = str(value.get("suite") or "unknown")
        created = str(value.get("created_at") or "")
        latest_at = max(latest_at, created)
        item = suites.setdefault(suite, {"runs": 0, "verdicts": Counter(),
                                         "durations": [], "last_at": ""})
        item["runs"] += 1
        item["verdicts"][str(value.get("verdict") or "UNKNOWN")] += 1
        item["durations"].append(sum(float(command.get("duration_seconds") or 0)
                                     for command in value.get("commands") or []))
        item["last_at"] = max(item["last_at"], created)
        for name in value.get("failures") or []:
            entry = failures.setdefault(str(name), {
                "count": 0, "last_at": "", "suites": set(), "baselines": set()})
            entry["count"] += 1
            entry["last_at"] = max(entry["last_at"], created)
            entry["suites"].add(suite)
            entry["baselines"].add(str((value.get("baseline") or {}).get("label") or ""))
    for item in suites.values():
        runs = item["runs"]
        item["pass_rate"] = round(item["verdicts"].get("PASS", 0) / runs, 3) if runs else 0.0
        item["avg_duration_seconds"] = round(sum(item["durations"]) / runs, 3) if runs else 0.0
        item["verdicts"] = dict(item["verdicts"])
        item.pop("durations", None)
    repeated = []
    for name, entry in sorted(failures.items(), key=lambda pair: (-pair[1]["count"],
                                                                   pair[0])):
        active = bool(latest_at) and entry["last_at"] >= latest_at
        repeated.append({
            "commands": [name], "count": entry["count"],
            "last_seen": entry["last_at"], "still_active": active,
            "affected_suites": sorted(entry["suites"]),
            "affected_baselines": sorted(item for item in entry["baselines"] if item),
            "category": classify(name),
        })
    return {"artifacts": len(paths), "valid": valid,
            "invalid": len(paths) - valid, "states": dict(states),
            "details": details, "suites": suites,
            "repeated_failures": repeated,
            "historical_failures": [item for item in repeated if not item["still_active"]]}


def p5_metrics(workspace: str, limit: int) -> dict:
    directory = os.path.join(workspace, ".agents", "evidence", "p5")
    paths = sorted(glob.glob(os.path.join(directory, "p5-*.json")),
                   key=lambda item: os.path.getmtime(item))[-limit:]
    states = Counter()
    details = []
    valid = []
    for path in paths:
        value, state = read_json(path)
        detail = ""
        if state == "ok":
            state, detail = verify_p5(value)
        states[state] += 1
        details.append({"file": os.path.basename(path), "state": state, "detail": detail})
        if state == "ok":
            valid.append(value)
    versions = sorted({str(item.get("scenario_version")) for item in valid})
    current_version = ""
    if valid:
        latest = max(valid, key=lambda item: str(item.get("created_at") or ""))
        current_version = str(latest.get("scenario_version"))
    current = []
    seen_runs = set()
    copies = 0
    historical = 0
    for value in valid:
        if str(value.get("scenario_version")) != current_version:
            historical += 1
            continue
        run_id = str(value.get("run_id") or "")
        if not run_id:
            run_id = "artifact:" + str(value.get("artifact_sha256"))
        if run_id in seen_runs:
            copies += 1
            continue
        seen_runs.add(run_id)
        current.append(value)
    current.sort(key=lambda item: str(item.get("created_at") or ""))
    comparable = len(versions) <= 1
    report = {"artifacts": len(paths), "valid": len(valid),
              "states": dict(states), "details": details,
              "versions": versions, "scenario_version": current_version,
              "comparable": comparable, "historical": historical,
              "copies_ignored": copies, "runs": len(current),
              "latest": {}, "previous": {}, "delta": {}}
    if not current:
        report["note"] = ("no verified P5 artifact for the current scenario version"
                          if valid else "no P5 artifact was found")
        return report
    latest = current[-1]
    card = latest.get("scorecard") or {}
    report["latest"] = {"at": latest.get("created_at"),
                        "run_id": latest.get("run_id"),
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
        if not repeated.get("still_active"):
            continue
        names = repeated.get("commands") or []
        items.append({
            "category": repeated.get("category") or classify(" ".join(names)),
            "source_problem": f"{', '.join(names)} fails in the current gate set",
            "last_seen": repeated.get("last_seen"),
            "affected_versions": repeated.get("affected_baselines") or [],
            "still_active": True,
            "evidence": {"commands": names, "count": repeated.get("count"),
                         "suites": repeated.get("affected_suites")},
            "action": "reproduce, add a regression test, then fix the cause",
            "verification": "the same gate must pass twice with the regression test present",
            "auto_apply": False, "requires_verification": True,
        })
    latest = p5_value.get("latest") or {}
    for name in latest.get("false_block_names") or []:
        items.append({
            "category": "false_block",
            "source_problem": f"a healthy benchmark scenario was blocked: {name}",
            "last_seen": latest.get("at"),
            "affected_versions": [latest.get("scenario_version")],
            "still_active": True,
            "evidence": {"scenario": name, "commit": latest.get("commit"),
                         "run_id": latest.get("run_id")},
            "action": "inspect the fixture and the gate; fix the fixture or the policy",
            "verification": "the scenario must pass and the negative scenarios must stay detected",
            "auto_apply": False, "requires_verification": True,
        })
    return items


def verdict_of(gates_value: dict, p5_value: dict) -> str:
    found = gates_value.get("artifacts", 0) + p5_value.get("artifacts", 0)
    if found == 0:
        return NO_DATA
    if gates_value.get("valid", 0) == 0 and p5_value.get("runs", 0) == 0:
        return UNVERIFIED
    gate_states = gates_value.get("states") or {}
    p5_states = p5_value.get("states") or {}
    if any(state in ("tampered", "content_mismatch")
           for state in list(gate_states) + list(p5_states)):
        return PARTIAL
    if p5_value.get("artifacts", 0) and p5_value.get("runs", 0) == 0:
        return PARTIAL
    if gates_value.get("invalid", 0):
        return PARTIAL
    return VERIFIED


def report(workspace: str, project: str, limit: int = 20) -> dict:
    gates_value = gates(workspace, project, limit)
    p5_value = p5_metrics(workspace, limit)
    return {"schema": SCHEMA, "kind": KIND, "created_at": utc_now(),
            "project": project, "workspace": workspace,
            "verdict": verdict_of(gates_value, p5_value),
            "evidence": {
                "integrity": ("OK" if not any(
                    state in ("tampered", "corrupt", "wrong_schema")
                    for state in list(gates_value["states"]) + list(p5_value["states"]))
                    else "PARTIAL"),
                "content_match": ("OK" if not any(
                    state == "content_mismatch"
                    for state in list(gates_value["states"]) + list(p5_value["states"]))
                    else "PARTIAL"),
                "provenance": "UNSIGNED",
                "trusted_provenance": False,
                "note": "unsigned artifacts; VERIFIED means integrity+content, not authorship",
            },
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
        print(f"P6: {value['verdict']}  evidence={value['evidence']['content_match']} "
              f"provenance={value['evidence']['provenance']}")
        print(f"  gates {value['gates']['valid']}/{value['gates']['artifacts']} valid, "
              f"p5 {value['p5']['runs']} run(s), copies={value['p5']['copies_ignored']}")
        for suite, item in sorted(value["gates"]["suites"].items()):
            print(f"  {suite:<12} runs={item['runs']:<3} pass={item['pass_rate']} "
                  f"verdicts={item['verdicts']}")
        for state, count in sorted(value["gates"]["states"].items()):
            if state != "ok":
                print(f"  gate state: {state} x{count}")
        for state, count in sorted(value["p5"]["states"].items()):
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
            print(f"  proposal [{item['category']}]: {item['source_problem']}")
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

    def gate_file(name, verdict, failed_name="", created="2026-01-01T00:00:00Z"):
        argv = ["python", "x.py"]
        rc = 0 if verdict == "PASS" else 1
        commands = [{"name": failed_name or "ok", "argv": argv, "exit_code": rc,
                     "timed_out": False, "duration_seconds": 1.0, "required": True}]
        failures = [failed_name] if verdict == "FAIL" else []
        log_name = f"{name}.log"
        log_text = (f"# EOS gate eos/unit {verdict}\n# baseline x\n\n"
                    f"$ python x.py  -> rc={rc} timeout=False 1.0s\nout\n")
        log_path = os.path.join(gates_dir, log_name)
        with open(log_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(log_text)
        value = {"schema": 1, "kind": "quality_gate", "created_at": created,
                 "project": "eos", "suite": "unit", "verdict": verdict,
                 "commands": commands, "failures": failures,
                 "log": {"path": f".agents/evidence/gates/eos/{log_name}",
                         "sha256": sha256_file(log_path)}}
        _write(os.path.join(gates_dir, f"{name}.json"), value)
        return value

    def p5_file(name, run_id, created, detected=0, version="v1", commit="c1"):
        value = {"schema": 1, "kind": "p5_benchmark", "created_at": created,
                 "run_id": run_id, "scenario_version": version,
                 "provenance": {"signed": False},
                 "source": {"commit": commit},
                 "scenarios": [{"name": "demo", "kind": "negative", "ok": detected == 1,
                                "observed": {}}],
                 "scorecard": {"scenarios": 1, "passed": detected,
                               "negative": 1, "detected": detected,
                               "detection_rate": float(detected),
                               "false_pass": 1 - detected, "false_block": 0,
                               "false_block_names": [],
                               "context_freshness": 1.0, "duration_seconds": 1.0}}
        value["content_sha256"] = p5b.identity(value)
        value["artifact_sha256"] = p5b.seal(value)
        _write(os.path.join(p5_dir, f"{name}.json"), value)
        return value

    empty = report(root, "eos")
    case("no artifacts is NO_DATA", empty["verdict"] == NO_DATA, empty["verdict"])
    case("NO_DATA exits non-zero", EXIT[NO_DATA] != 0, str(EXIT[NO_DATA]))

    old_fail = gate_file("gate-old-fail", "FAIL", "unit_test",
                         "2026-01-01T00:00:00Z")
    gate_file("gate-new-pass", "PASS", "", "2026-01-02T00:00:00Z")

    tampered = gate_file("gate-tampered", "FAIL", "unit_test", "2026-01-02T00:00:00Z")
    tampered["verdict"] = "PASS"
    _write(os.path.join(gates_dir, "gate-tampered.json"), tampered)

    log_broken = gate_file("gate-log-broken", "FAIL", "unit_test", "2026-01-02T00:00:00Z")
    with open(os.path.join(gates_dir, "gate-log-broken.log"), "a", encoding="utf-8") as handle:
        handle.write("edited\n")
    _write(os.path.join(gates_dir, "gate-log-broken.json"), log_broken)

    with open(os.path.join(gates_dir, "gate-corrupt.json"), "w", encoding="utf-8") as handle:
        handle.write("{not json")

    first = p5_file("p5-first", "run-1", "2026-01-02T00:00:00Z", 0)
    p5_file("p5-second", "run-2", "2026-01-02T00:01:00Z", 0)
    _write(os.path.join(p5_dir, "p5-copy.json"), first)
    tampered_p5 = p5_file("p5-tampered", "run-3", "2026-01-02T00:02:00Z", 0)
    tampered_p5["scorecard"]["detection_rate"] = 1.0
    _write(os.path.join(p5_dir, "p5-tampered.json"), tampered_p5)
    old_version = p5_file("p5-old", "run-0", "2026-01-01T00:00:00Z", 1, "v0", "c0")
    legacy = {"schema": 1, "kind": "p5_benchmark", "created_at": "2025-12-01T00:00:00Z",
              "run_id": "legacy", "scorecard": {"scenarios": 1, "passed": 1}}
    _write(os.path.join(p5_dir, "p5-legacy.json"), legacy)

    value = report(root, "eos")
    case("a gate JSON verdict change is caught",
         value["gates"]["states"].get("content_mismatch", 0) >= 1,
         value["gates"]["states"])
    case("an edited log is caught", value["gates"]["states"].get("tampered", 0) >= 1,
         value["gates"]["states"])
    case("a corrupt gate is separated", value["gates"]["states"].get("corrupt") == 1,
         value["gates"]["states"])
    case("the verdict is PARTIAL on a content mismatch", value["verdict"] == PARTIAL,
         value["verdict"])
    case("two real runs with the same result are both counted",
         value["p5"]["runs"] == 2, value["p5"]["runs"])
    case("a copied artifact is the only duplicate",
         value["p5"]["copies_ignored"] == 1, value["p5"]["copies_ignored"])
    case("an edited P5 result is caught",
         value["p5"]["states"].get("tampered", 0) >= 1, value["p5"]["states"])
    case("the legacy artifact is reported and excluded",
         value["p5"]["states"].get("legacy") == 1, value["p5"]["states"])
    case("the older scenario version is historical",
         value["p5"]["historical"] == 1, value["p5"]["historical"])
    case("different versions are not compared", value["p5"]["comparable"] is False,
         value["p5"]["comparable"])
    case("an old failure is not shown as active",
         all(item["still_active"] is False for item in value["gates"]["historical_failures"]),
         value["gates"]["historical_failures"])
    case("provenance is unsigned", value["evidence"]["provenance"] == "UNSIGNED",
         value["evidence"]["provenance"])
    case("VERIFIED never claims trusted provenance",
         value["evidence"]["trusted_provenance"] is False, "trusted")

    for name in ("gate-tampered", "gate-log-broken", "gate-corrupt"):
        os.remove(os.path.join(gates_dir, f"{name}.json"))
    os.remove(os.path.join(gates_dir, "gate-log-broken.log"))
    for name in ("p5-tampered", "p5-copy", "p5-old", "p5-legacy"):
        os.remove(os.path.join(p5_dir, f"{name}.json"))
    # Re-create the two real runs so the clean report has valid P5 data.
    p5_file("p5-first", "run-1", "2026-01-02T00:00:00Z", 0)
    p5_file("p5-second", "run-2", "2026-01-02T00:01:00Z", 0)

    clean = report(root, "eos")
    case("a valid gate and P5 runs verify", clean["verdict"] == VERIFIED, clean["verdict"])
    case("two real runs remain two runs", clean["p5"]["runs"] == 2, clean["p5"]["runs"])

    print(f"\n{passed}/{passed + failed} cases passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
