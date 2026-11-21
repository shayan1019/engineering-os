#!/usr/bin/env python3
"""P6 metrics reader.

Read-only aggregation of the artifacts EOS already produces:

  .agents/evidence/gates/<project>/gate-*.json   one per quality-gate run
  .agents/evidence/p5/p5-*.json                  one per P5 benchmark run

It reports trends and repeated failure causes. It never runs a gate, never
changes a verdict and never treats "no runs" as a pass.

Commands:
  report [--workspace <dir>] [--project <id>] [--limit N] [--json] [--out F]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

SCHEMA = 1
KIND = "p6_metrics"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def gate_metrics(workspace: str, project: str, limit: int) -> dict:
    directory = os.path.join(workspace, ".agents", "evidence", "gates", project)
    paths = sorted(glob.glob(os.path.join(directory, "gate-*.json")),
                   key=lambda item: os.path.getmtime(item))
    paths = paths[-limit:]
    suites = {}
    for path in paths:
        value = read_json(path)
        if value.get("kind") != "quality_gate":
            continue
        suite = str(value.get("suite") or "unknown")
        item = suites.setdefault(suite, {"runs": 0, "pass": 0, "fail": 0,
                                         "durations": [], "failures": [],
                                         "last_verdict": "", "last_at": ""})
        item["runs"] += 1
        verdict = str(value.get("verdict") or "")
        if verdict == "PASS":
            item["pass"] += 1
        else:
            item["fail"] += 1
        total = sum(float(command.get("duration_seconds") or 0)
                    for command in value.get("commands") or [])
        item["durations"].append(total)
        failed = list(value.get("failures") or [])
        if failed:
            item["failures"].append(sorted(failed))
        item["last_verdict"] = verdict
        item["last_at"] = value.get("created_at") or ""
    for item in suites.values():
        runs = item["runs"]
        item["pass_rate"] = round(item["pass"] / runs, 3) if runs else 0.0
        item["avg_duration_seconds"] = round(
            sum(item["durations"]) / len(item["durations"]), 3) if item["durations"] else 0.0
        item.pop("durations", None)
    repeated = Counter(tuple(names) for item in suites.values()
                       for names in item["failures"])
    return {"artifacts": len(paths), "suites": suites,
            "repeated_failures": [
                {"commands": list(names), "count": count}
                for names, count in repeated.most_common(10)]}


def p5_metrics(workspace: str, limit: int) -> dict:
    directory = os.path.join(workspace, ".agents", "evidence", "p5")
    paths = sorted(glob.glob(os.path.join(directory, "p5-*.json")),
                   key=lambda item: os.path.getmtime(item))[-limit:]
    runs = []
    for path in paths:
        value = read_json(path)
        card = value.get("scorecard") or {}
        if not card:
            continue
        runs.append({"at": value.get("created_at") or "",
                     "detection_rate": card.get("detection_rate", 0.0),
                     "false_pass": card.get("false_pass", 0),
                     "false_block": card.get("false_block", 0),
                     "context_freshness": card.get("context_freshness", 0.0),
                     "duration_seconds": card.get("duration_seconds", 0.0)})
    if not runs:
        return {"runs": 0, "latest": {}, "previous": {}, "delta": {},
                "note": "no P5 benchmark artifact was found"}
    latest = runs[-1]
    previous = runs[-2] if len(runs) > 1 else {}
    delta = {key: round(float(latest.get(key, 0)) - float(previous.get(key, 0)), 3)
             for key in ("detection_rate", "false_pass", "false_block",
                         "context_freshness", "duration_seconds")} if previous else {}
    return {"runs": len(runs), "latest": latest, "previous": previous,
            "delta": delta,
            "average": {key: round(sum(float(item.get(key, 0)) for item in runs) / len(runs), 3)
                        for key in ("detection_rate", "false_pass", "false_block",
                                    "context_freshness", "duration_seconds")}}


def report(workspace: str, project: str, limit: int = 20) -> dict:
    return {"schema": SCHEMA, "kind": KIND, "created_at": utc_now(),
            "project": project, "workspace": workspace,
            "gates": gate_metrics(workspace, project, limit),
            "p5": p5_metrics(workspace, limit)}


def main() -> int:
    parser = argparse.ArgumentParser(prog="p6_metrics.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("report")
    p.add_argument("--workspace")
    p.add_argument("--project", default="eos")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--out", default="")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command != "report":
        return 2
    workspace = ep.resolve_workspace(args.workspace)
    value = report(workspace, args.project, args.limit)
    out = args.out or os.path.join(workspace, ".agents", "evidence", "p6",
                                   f"metrics-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    if args.json:
        print(json.dumps(value, ensure_ascii=True))
    else:
        gates = value["gates"]
        print(f"P6: {gates['artifacts']} gate artifact(s)")
        for suite, item in sorted(gates["suites"].items()):
            print(f"  {suite:<12} runs={item['runs']:<3} pass={item['pass']:<3} "
                  f"fail={item['fail']:<3} rate={item['pass_rate']} "
                  f"last={item['last_verdict']}")
        for item in gates["repeated_failures"]:
            print(f"  repeated: {item['count']}x {', '.join(item['commands'])}")
        p5 = value["p5"]
        if p5["runs"]:
            print(f"  p5 runs={p5['runs']} detection={p5['latest']['detection_rate']} "
                  f"false_pass={p5['latest']['false_pass']} "
                  f"false_block={p5['latest']['false_block']} "
                  f"freshness={p5['latest']['context_freshness']}")
            if p5.get("delta"):
                print(f"  p5 delta: {p5['delta']}")
        else:
            print("  p5: no artifact")
        print(f"  artifact: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
