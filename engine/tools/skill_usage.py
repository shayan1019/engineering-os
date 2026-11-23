#!/usr/bin/env python3
"""Mechanical skill usage evidence.

Selection is not usage. A skill counts as used only when a real SKILL.md was
read (the OpenCode guard records the read) or when `used` verifies the file and
records its hash. A skipped skill needs an explicit reason.

Commands:
  used    --skill <name> --path <SKILL.md> [--session S] [--workspace W]
  skipped --skill <name> --reason <text> [--session S] [--workspace W]
  status  [--session S] [--workspace W] [--json]
  selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

SCHEMA = 1
KIND = "skill_usage"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_session(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return text[:80] or "unknown"


def evidence_path(workspace: str, session: str) -> str:
    return os.path.join(workspace, ".agents", "evidence", "skills",
                        safe_session(session) + ".jsonl")


def append_event(workspace: str, session: str, event: dict) -> str:
    path = evidence_path(workspace, session)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"ts": utc_now(), "schema": SCHEMA, "kind": KIND,
               "session": safe_session(session), **event}
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    return path


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def validate_skill(path: str, skill: str) -> tuple:
    absolute = os.path.realpath(os.path.abspath(path))
    if not os.path.isfile(absolute):
        return "", f"skill file not found: {path}"
    if os.path.basename(absolute).lower() != "skill.md":
        return "", f"not a SKILL.md: {path}"
    try:
        text = open(absolute, encoding="utf-8", errors="replace").read(4096)
    except OSError as exc:
        return "", f"skill file unreadable: {exc}"
    match = re.search(r"^name:\s*[\"']?([^\"'\r\n]+)", text, re.M)
    declared = (match.group(1).strip() if match else "")
    if declared and declared != skill:
        return "", f"skill name mismatch: file says {declared!r}, requested {skill!r}"
    if not declared and os.path.basename(os.path.dirname(absolute)) != skill:
        return "", "skill name is not declared and the directory does not match"
    return absolute, ""


def cmd_used(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    absolute, problem = validate_skill(args.path, args.skill)
    if problem:
        print(f"BLOCKED: {problem}")
        return 1
    event = {"verdict": "USED", "skill": args.skill,
             "path": os.path.relpath(absolute, workspace).replace("\\", "/"),
             "sha256": sha256_file(absolute), "source": "skill_usage"}
    path = append_event(workspace, args.session, event)
    print(json.dumps({"ok": True, "evidence": path, **event}, ensure_ascii=True)
          if args.json else f"USED: {args.skill} ({event['sha256'][:12]}) -> {path}")
    return 0


def cmd_skipped(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    reason = str(args.reason or "").strip()
    if not reason:
        print("BLOCKED: a skipped skill needs a reason")
        return 1
    event = {"verdict": "SKIPPED", "skill": args.skill,
             "reason": reason[:1000], "source": "skill_usage"}
    path = append_event(workspace, args.session, event)
    print(json.dumps({"ok": True, "evidence": path, **event}, ensure_ascii=True)
          if args.json else f"SKIPPED: {args.skill} -> {path}")
    return 0


def read_events(workspace: str, session: str) -> list:
    path = evidence_path(workspace, session)
    if not os.path.isfile(path):
        return []
    events = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def cmd_status(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    events = read_events(workspace, args.session)
    used = [item for item in events if item.get("verdict") == "USED"]
    skipped = [item for item in events if item.get("verdict") == "SKIPPED"]
    result = {"ok": True, "session": safe_session(args.session),
              "used": used, "skipped": skipped,
              "evidence": evidence_path(workspace, args.session)}
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"USED ({len(used)}): " + ", ".join(item.get("skill", "?") for item in used))
        for item in skipped:
            print(f"SKIPPED {item.get('skill', '?')}: {item.get('reason', '')}")
    return 0


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-skill-")
        skill = os.path.join(root, ".agents", "skills", "demo")
        os.makedirs(skill, exist_ok=True)
        with open(os.path.join(skill, "SKILL.md"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("---\nname: demo\ndescription: test\n---\nbody\n")
        return root

    @case("a real skill file records a hash")
    def _():
        root = fixture()
        args = argparse.Namespace(workspace=root, skill="demo",
                                  path=os.path.join(root, ".agents", "skills", "demo", "SKILL.md"),
                                  session="ses_skill", json=True)
        assert cmd_used(args) == 0
        events = read_events(root, "ses_skill")
        assert events and events[-1]["verdict"] == "USED"
        assert len(events[-1]["sha256"]) == 64
        return events[-1]["sha256"][:12]

    @case("a skipped skill requires a reason")
    def _():
        root = fixture()
        args = argparse.Namespace(workspace=root, skill="demo", reason="",
                                  session="ses_skill", json=True)
        assert cmd_skipped(args) == 1
        args.reason = "not relevant to this task"
        assert cmd_skipped(args) == 0
        events = read_events(root, "ses_skill")
        assert events[-1]["verdict"] == "SKIPPED"
        return events[-1]["reason"]

    @case("a wrong skill name is refused")
    def _():
        root = fixture()
        args = argparse.Namespace(workspace=root, skill="other",
                                  path=os.path.join(root, ".agents", "skills", "demo", "SKILL.md"),
                                  session="ses_skill", json=True)
        assert cmd_used(args) == 1
        return "name checked"

    @case("status separates used and skipped")
    def _():
        root = fixture()
        used = argparse.Namespace(workspace=root, skill="demo",
                                  path=os.path.join(root, ".agents", "skills", "demo", "SKILL.md"),
                                  session="ses_status", json=True)
        skipped = argparse.Namespace(workspace=root, skill="other",
                                     reason="not needed", session="ses_status", json=True)
        assert cmd_used(used) == 0
        assert cmd_skipped(skipped) == 0
        events = read_events(root, "ses_status")
        assert [item["verdict"] for item in events] == ["USED", "SKIPPED"]
        return "separated"

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
    parser = argparse.ArgumentParser(prog="skill_usage.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("used")
    p.add_argument("--skill", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--session", default=os.environ.get("OPENCODE_SESSION_ID", "") or "unknown")
    p.add_argument("--workspace", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_used)
    p = sub.add_parser("skipped")
    p.add_argument("--skill", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--session", default=os.environ.get("OPENCODE_SESSION_ID", "") or "unknown")
    p.add_argument("--workspace", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_skipped)
    p = sub.add_parser("status")
    p.add_argument("--session", default=os.environ.get("OPENCODE_SESSION_ID", "") or "unknown")
    p.add_argument("--workspace", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
