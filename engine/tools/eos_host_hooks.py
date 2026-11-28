"""Generate EOS-owned hook settings for a supported host session."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sys

import _eos_paths as ep
import eos_discipline
import nexus_event

EVENTS = {
    "claude": ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PreToolUse",
               "PostToolUse", "PostToolUseFailure", "PermissionDenied", "SubagentStart", "SubagentStop"),
    "codex": ("SessionStart", "SessionEnd", "UserPromptSubmit", "Stop", "PreToolUse", "PostToolUse",
              "SubagentStart", "SubagentStop"),
}


def quote(value, shell):
    if shell == "powershell":
        return "'" + value.replace("'", "''") + "'"
    return shlex.quote(value.replace("\\", "/") if os.name == "nt" else value)


def config(host, shell):
    here = Path(__file__).resolve().parent

    def command(script, *args):
        parts = [sys.executable, "-B", str(here / script), *args]
        return ("& " if shell == "powershell" else "") + " ".join(quote(part, shell) for part in parts)

    hooks = {
        event: [{"hooks": [{"type": "command", "command": command("nexus_event.py", host),
                            "async": True, "timeout": 3 if event == "SessionEnd" else 5}]}] for event in EVENTS[host]
    }
    hooks["SessionStart"][0]["hooks"].insert(0, {"type": "command", "timeout": 5,
                                              "command": command("eos_host_hooks.py", "context", host)})
    return {"hooks": hooks}


def context(host):
    raw = sys.stdin.buffer.read(nexus_event.MAX_HOOK_BYTES + 1)
    if len(raw) > nexus_event.MAX_HOOK_BYTES:
        return
    try:
        event = json.loads(raw)
        root = os.environ.get("EOS_NEXUS_WORKSPACE")
        cwd = event.get("cwd")
        if not root or not cwd or event.get("hook_event_name") != "SessionStart":
            return
        if not Path(cwd).resolve().is_relative_to(Path(root).resolve()):
            return
    except (AttributeError, OSError, ValueError, TypeError):
        return
    policy = eos_discipline.render(eos_discipline.describe(ep.ENGINE_ROOT))
    routes = "Use `eos route` for task-level project, skill and specialist selection when needed."
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                            "additionalContext": policy + "\n" + routes}}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("config", "context"))
    parser.add_argument("host", choices=EVENTS)
    parser.add_argument("--shell", choices=("bash", "powershell"), default="bash")
    args = parser.parse_args()
    if args.action == "config":
        print(json.dumps(config(args.host, args.shell)))
    else:
        context(args.host)


if __name__ == "__main__":
    main()
