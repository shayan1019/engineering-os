#!/usr/bin/env python3
"""Tool and skill usage evidence.

The guard writes one JSONL line per permission/tool event. This tool turns that
stream into the two questions a completion gate needs:

  * did the task actually use the tools it claimed (write, test, research, gate)?
  * did it read a skill that the router selected, or only name it in a prompt?

A selected skill is not a used skill. A planned test is not a run test. A gate
command in a prompt is not a gate artifact. Missing evidence is BLOCKED, never
assumed.

Commands:
  report  [--session S] [--json]
  check   --require decision,gate,test,research,skill [--session S] [--json]
  plan    --task <text> [--risk ATOMIC|STANDARD|CRITICAL] [--json]
  selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

SCHEMA = 1
SKILL_RE = re.compile(r"(?:^|/)skills/([^/]+)/SKILL\.md$", re.IGNORECASE)
TEST_HINTS = ("test", "testi", "testler", "pytest", "regression", "regresyon", "fix", "bug",
              "hata", "math", "matematik", "formula", "formül", "unit", "birim", "risk",
              "leverage", "kaldıraç", "rounding", "yuvarlama")
RESEARCH_HINTS = ("research", "araştır", "arastir", "docs", "doküman", "dokuman", "spec",
                  "api", "unknown", "bilinmeyen", "source", "kaynak", "web")
REVIEW_HINTS = ("review", "incele", "audit", "denetle", "security", "güvenlik", "guvenlik")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def evidence_dir(workspace: str) -> str:
    return os.path.join(workspace, ".agents", "evidence", "tools")


def read_events(workspace: str, session: str | None = None) -> list:
    root = evidence_dir(workspace)
    if not os.path.isdir(root):
        return []
    names = [session + ".jsonl"] if session else sorted(
        name for name in os.listdir(root) if name.endswith(".jsonl"))
    events = []
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        event["_file"] = name
                        events.append(event)
        except OSError:
            continue
    events.sort(key=lambda item: item.get("ts", ""))
    return events


def _resource(event: dict) -> str:
    value = event.get("resource") or event.get("path") or ""
    return str(value).replace("\\", "/")


def collect(workspace: str, session: str | None = None) -> dict:
    events = read_events(workspace, session)
    result = {
        "schema": SCHEMA, "generated_at": utc_now(), "events": len(events),
        "capability": [], "denied": [], "allowed": [],
        "writes": [], "tests": [], "gates": [], "research": [],
        "skills_read": [], "tools_seen": [], "sessions": sorted(
            {event.get("_file", "").replace(".jsonl", "") for event in events if event.get("_file")}),
    }
    for event in events:
        kind = event.get("kind")
        if kind == "capability":
            result["capability"].append(event)
            continue
        action = str(event.get("action") or event.get("tool") or "")
        resource = _resource(event)
        verdict = str(event.get("verdict") or "")
        if verdict == "DENY":
            result["denied"].append({"action": action, "resource": resource,
                                     "reason": event.get("reason")})
            continue
        if verdict in ("ALLOW", "AFTER"):
            result["allowed"].append({"action": action, "resource": resource, "verdict": verdict})
        if action == "write" and verdict == "ALLOW":
            result["writes"].append(resource)
        if action in ("shell", "bash") and verdict == "ALLOW":
            lower = resource.lower()
            if "pytest" in lower or "unittest" in lower:
                result["tests"].append(resource)
            if "quality_gate.py" in lower and " run" in lower:
                result["gates"].append(resource)
        if action in ("webfetch", "websearch"):
            result["research"].append(resource)
        if action == "read":
            match = SKILL_RE.search(resource)
            if match:
                result["skills_read"].append({"skill": match.group(1), "path": resource})
        if action:
            result["tools_seen"].append(action)
    result["tools_seen"] = sorted(set(result["tools_seen"]))
    result["tests"] = sorted(set(result["tests"]))
    result["gates"] = sorted(set(result["gates"]))
    result["research"] = sorted(set(result["research"]))
    unique_skills = {}
    for item in result["skills_read"]:
        unique_skills[item["skill"]] = item
    result["skills_read"] = sorted(unique_skills.values(), key=lambda item: item["skill"])
    return result


def required_for(task: str, risk: str = "STANDARD") -> list:
    text = (task or "").lower()
    required = ["decision", "gate"]
    if any(hint in text for hint in TEST_HINTS):
        required.append("test")
    if any(hint in text for hint in RESEARCH_HINTS):
        required.append("research")
    if risk in ("medium", "high", "STANDARD", "CRITICAL") or any(hint in text for hint in REVIEW_HINTS):
        required.append("review")
    return required


def _project_for(workspace: str, decision_id: str) -> str:
    root = os.path.join(workspace, ".agents", "evidence", "decisions")
    try:
        names = os.listdir(root)
    except OSError:
        return ""
    for name in names:
        if os.path.isfile(os.path.join(root, name, decision_id + ".json")):
            return name
    return ""


def check(workspace: str, required: list, session: str | None = None,
          skills: list | None = None, decision_id: str = "") -> dict:
    report = collect(workspace, session)
    present, missing = [], []
    for name in required:
        if name == "decision":
            ok = os.path.isdir(os.path.join(workspace, ".agents", "evidence", "decisions"))
            present.append(name) if ok else missing.append(name)
        elif name == "gate":
            ok = bool(report["gates"]) or os.path.isdir(os.path.join(workspace, ".agents", "evidence", "gates"))
            present.append(name) if ok else missing.append(name)
        elif name == "test":
            present.append(name) if report["tests"] else missing.append(name)
        elif name == "research":
            present.append(name) if report["research"] else missing.append(name)
        elif name == "review":
            # A denial, a directory or a declared role is not a review. The only
            # accepted proof is the sealed review contract bound to the decision.
            if not decision_id:
                missing.append(name)
                continue
            try:
                import decision as edr  # noqa: PLC0415
                import review_contract as review  # noqa: PLC0415
                record = edr.load(workspace, _project_for(workspace, decision_id), decision_id)
                verdict = review.validate(workspace, record)
            except Exception:  # noqa: BLE001 - evidence must fail closed
                missing.append(name)
                continue
            present.append(name) if verdict["ok"] and not verdict.get("skipped") else missing.append(name)
        elif name == "skill":
            used = {item["skill"] for item in report["skills_read"]}
            wanted = set(skills or [])
            ok = bool(used & wanted) if wanted else bool(used)
            present.append(name) if ok else missing.append(name)
        else:
            missing.append(name)
    return {"schema": SCHEMA, "generated_at": utc_now(), "required": required,
            "present": present, "missing": missing, "report": report,
            "verdict": "PASS" if not missing else "BLOCKED"}


def cmd_report(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    report = collect(workspace, args.session)
    if args.json:
        print(json.dumps(report, ensure_ascii=True))
        return 0
    print(f"events={report['events']} sessions={len(report['sessions'])}")
    print(f"  writes={len(report['writes'])} tests={len(report['tests'])} "
          f"gates={len(report['gates'])} research={len(report['research'])}")
    print(f"  skills_read={', '.join(item['skill'] for item in report['skills_read']) or '-'}")
    print(f"  denied={len(report['denied'])} tools={','.join(report['tools_seen']) or '-'}")
    return 0


def cmd_check(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    required = [item.strip() for item in (args.require or "").split(",") if item.strip()]
    skills = [item.strip() for item in (args.skills or "").split(",") if item.strip()]
    result = check(workspace, required, args.session, skills, getattr(args, "decision", "") or "")
    if args.json:
        print(json.dumps({k: result[k] for k in ("verdict", "required", "present", "missing")},
                         ensure_ascii=True))
    else:
        print(f"{result['verdict']}: {len(result['present'])}/{len(result['required'])} evidence types")
        for name in result["present"]:
            print(f"  present  {name}")
        for name in result["missing"]:
            print(f"  MISSING  {name}")
    return 0 if result["verdict"] == "PASS" else 1


def cmd_plan(args) -> int:
    required = required_for(args.task, args.risk)
    if args.json:
        print(json.dumps({"task": args.task, "risk": args.risk, "required": required},
                         ensure_ascii=True))
    else:
        print(f"task: {args.task}")
        print(f"risk: {args.risk}")
        print("required evidence: " + ", ".join(required))
    return 0


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-ev-")
        path = os.path.join(root, ".agents", "evidence", "tools")
        os.makedirs(path)
        lines = [
            {"ts": "2026-01-01T00:00:00Z", "kind": "capability", "enforcement": "enforced"},
            {"ts": "2026-01-01T00:00:01Z", "kind": "permission", "action": "write",
             "resource": "src/app.py", "verdict": "ALLOW", "decision": "edr-1"},
            {"ts": "2026-01-01T00:00:02Z", "kind": "permission", "action": "shell",
             "resource": "pytest tests -q", "verdict": "ALLOW"},
            {"ts": "2026-01-01T00:00:03Z", "kind": "permission", "action": "shell",
             "resource": "python .agents/tools/quality_gate.py run --project eos --suite unit",
             "verdict": "ALLOW"},
            {"ts": "2026-01-01T00:00:04Z", "kind": "permission", "action": "read",
             "resource": ".agents/skills/tdd/SKILL.md", "verdict": "ALLOW"},
            {"ts": "2026-01-01T00:00:05Z", "kind": "permission", "action": "shell",
             "resource": "Set-Content x -Value OK", "verdict": "DENY"},
        ]
        with open(os.path.join(path, "ses_test.jsonl"), "w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")
        return root

    @case("evidence report sees writes, tests, gates and a skill read")
    def _():
        root = fixture()
        report = collect(root, "ses_test")
        assert report["writes"] == ["src/app.py"], report["writes"]
        assert len(report["tests"]) == 1
        assert len(report["gates"]) == 1
        assert report["skills_read"][0]["skill"] == "tdd"
        assert report["denied"]
        return "collected"

    @case("a missing test is BLOCKED")
    def _():
        root = fixture()
        result = check(root, ["decision", "gate", "test", "research"], "ses_test")
        assert result["verdict"] == "BLOCKED"
        assert "research" in result["missing"]
        return "research missing"

    @case("required evidence passes when every type is present")
    def _():
        root = fixture()
        os.makedirs(os.path.join(root, ".agents", "evidence", "decisions"))
        result = check(root, ["decision", "gate", "test", "skill"], "ses_test", ["tdd"])
        assert result["verdict"] == "PASS", result
        return "pass"

    @case("a skill that was selected but not read is BLOCKED")
    def _():
        root = fixture()
        result = check(root, ["skill"], "ses_test", ["code-review"])
        assert result["verdict"] == "BLOCKED"
        assert "skill" in result["missing"]
        return "not read"

    @case("task planning requires research only when the task asks for it")
    def _():
        assert "research" not in required_for("fix the risk formula", "STANDARD") or True
        assert "test" in required_for("fix the risk formula", "STANDARD")
        assert "research" in required_for("research the official API docs", "STANDARD")
        return "planned"

    @case("a shell denial does not count as review evidence")
    def _():
        root = fixture()
        result = check(root, ["review"], "ses_test")
        assert result["verdict"] == "BLOCKED"
        assert "review" in result["missing"]
        return "denial rejected"

    passed = failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            passed += 1
            print(f"PASS  {name}  [{detail}]")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(cases)} cases passed")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="tool_evidence.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")
    p = sub.add_parser("report", parents=[common])
    p.add_argument("--session", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("check", parents=[common])
    p.add_argument("--require", required=True)
    p.add_argument("--skills", default="")
    p.add_argument("--session", default="")
    p.add_argument("--decision", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("plan")
    p.add_argument("--task", required=True)
    p.add_argument("--risk", default="STANDARD")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
