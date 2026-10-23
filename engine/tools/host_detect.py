#!/usr/bin/env python3
"""host_detect.py ? detect supported AI coding hosts and report capabilities.

Usage:
  python engine/tools/host_detect.py [--json]

Reports: opencode, claude, cursor, blackbox.
Each host: present (bool), version, config_dir, capabilities.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

HOSTS = [
    {"id": "opencode", "binary": "opencode", "alt": [], "config_dir": ".opencode",
     "caps": {"subagents": True, "skills": True, "permissions": True, "mcp": True}},
    {"id": "claude", "binary": "claude", "alt": ["claude-code"], "config_dir": ".claude",
     "caps": {"subagents": True, "skills": True, "permissions": True, "mcp": True}},
    {"id": "cursor", "binary": "cursor", "alt": ["cursor-agent"], "config_dir": ".cursor",
     "caps": {"subagents": False, "skills": False, "permissions": False, "mcp": True}},
    {"id": "blackbox", "binary": "blackbox", "alt": [], "config_dir": ".blackbox",
     "caps": {"subagents": False, "skills": False, "permissions": False, "mcp": False}},
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
        adapter_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host-adapters", host["id"] + ".json")
    adapter_ready = os.path.isfile(adapter_path)
    results.append({
            "id": host["id"],
            "present": present,
            "binary": binary,
            "version": get_version(binary) if present else None,
            "config_dir": host["config_dir"],
            "capabilities": host["caps"] if present else {},
            "adapter_ready": adapter_ready,
            "status": ("DETECTED" if present else "BLOCKED"),
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
            print(f"{r['id']:10} {status:8} {r.get('version') or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
