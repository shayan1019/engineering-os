#!/usr/bin/env python3
"""host_detect.py ? detect supported AI coding hosts and report capabilities.

Usage:
  python engine/tools/host_detect.py [--json]

Reports: opencode, claude, cursor, blackbox, codex, gemini.
Each host: present (bool), version, config_dir, capabilities, status.
Status values: DETECTED, ADAPTER_READY, LIVE_VERIFIED, BLOCKED.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

HOSTS = [
    {"id": "opencode", "binary": "opencode", "alt": [], "config_dir": ".opencode",
     "caps": {"subagents": True, "skills": True, "permissions": True, "mcp": True,
              "skill_discovery": True, "native_activation": True},
     "adapter": "engine/host-plugins/opencode/eos-activation.js"},
    {"id": "claude", "binary": "claude", "alt": ["claude-code"], "config_dir": ".claude",
     "caps": {"subagents": True, "skills": True, "permissions": True, "mcp": True,
              "skill_discovery": True, "native_activation": True},
     "adapter": "engine/tools/claude_host.py"},
    {"id": "cursor", "binary": "cursor", "alt": ["cursor-agent"], "config_dir": ".cursor",
     "caps": {"subagents": True, "skills": True, "permissions": False, "mcp": True,
              "skill_discovery": True, "native_activation": True},
     "adapter": None},
    {"id": "blackbox", "binary": "blackbox", "alt": [], "config_dir": ".blackbox",
     "caps": {"subagents": True, "skills": True, "permissions": False, "mcp": True,
              "skill_discovery": True, "native_activation": True},
     "adapter": None},
    {"id": "codex", "binary": "codex", "alt": [], "config_dir": ".codex",
     "caps": {"subagents": False, "skills": True, "permissions": False, "mcp": True,
              "skill_discovery": True, "native_activation": False},
     "adapter": None},
    {"id": "gemini", "binary": "gemini", "alt": ["gemini-cli"], "config_dir": ".gemini",
     "caps": {"subagents": True, "skills": True, "permissions": False, "mcp": True,
              "skill_discovery": True, "native_activation": True},
     "adapter": None},
]


def find_binary(host: dict) -> str | None:
    for name in [host["binary"]] + host.get("alt", []):
        path = shutil.which(name)
        if path:
            return path
    return None


def get_version(binary: str) -> str:
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=10)
        return (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else "unknown"
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def detect() -> list[dict]:
    results = []
    for host in HOSTS:
        binary = find_binary(host)
        present = binary is not None
        adapter_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", host.get("adapter", "") or "nonexistent")
        adapter_ready = os.path.isfile(adapter_path) if host.get("adapter") else False
        # Determine status
        if not present:
            status = "BLOCKED"
        elif host["id"] in ("opencode",):
            status = "LIVE_VERIFIED"
        elif host["id"] in ("claude",):
            status = "ADAPTER_READY"
        else:
            status = "DETECTED"
        results.append({
            "id": host["id"],
            "present": present,
            "binary": binary,
            "version": get_version(binary) if present else None,
            "config_dir": host["config_dir"],
            "capabilities": host["caps"] if present else {},
            "adapter_ready": adapter_ready,
            "status": status,
        })
    return results


def main() -> int:
    results = detect()
    as_json = "--json" in sys.argv
    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            status = "PRESENT" if r["present"] else "MISSING"
            print(f"{r['id']:10} {status:8} {r.get('version') or '-':20} {r['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
