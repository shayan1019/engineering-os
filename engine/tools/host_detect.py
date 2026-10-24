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
    {"id": "cursor", "binary": "cursor", "alt": ["cursor-agent", "Cursor"], "config_dir": ".cursor",
     "caps": {"subagents": True, "skills": True, "permissions": True, "mcp": True,
              "skill_discovery": True, "native_activation": True},
     "adapter": "engine/tools/cursor_host.py"},
    {"id": "blackbox", "binary": "blackbox-cli", "alt": ["blackbox"], "config_dir": ".blackbox",
     "caps": {"subagents": True, "skills": True, "permissions": False, "mcp": True,
              "skill_discovery": True, "native_activation": True},
     "adapter": "engine/tools/blackbox_host.py"},
    {"id": "codex", "binary": "codex", "alt": [], "config_dir": ".codex",
     "caps": {"subagents": False, "skills": True, "permissions": False, "mcp": True,
              "skill_discovery": True, "native_activation": False},
     "adapter": "engine/tools/codex_host.py"},
    {"id": "gemini", "binary": "gemini", "alt": ["gemini-cli"], "config_dir": ".gemini",
     "caps": {"subagents": True, "skills": True, "permissions": False, "mcp": True,
              "skill_discovery": True, "native_activation": True},
     "adapter": "engine/tools/gemini_host.py"},
]


COMMON_PATHS = {
    "cursor": [
        r"C:\Users\{user}\AppData\Local\Programs\Cursor\resources\app\bin\cursor.cmd",
        r"C:\Users\{user}\AppData\Local\Programs\Cursor\resources\app\bin\cursor",
        r"C:\Program Files\Cursor\resources\app\bin\cursor.cmd",
    ],
    "blackbox": [
        r"C:\Users\{user}\AppData\Local\hermes\node\blackbox-cli.cmd",
        r"C:\Users\{user}\AppData\Local\hermes\node\blackbox-cli",
    ],
}


def find_binary(host: dict) -> str | None:
    for name in [host["binary"]] + host.get("alt", []):
        path = shutil.which(name)
        if path:
            return path
    # Check common installation paths
    user = os.environ.get("USERNAME", "")
    for template in COMMON_PATHS.get(host["id"], []):
        path = template.format(user=user)
        if os.path.isfile(path):
            return path
    return None


def get_version(binary: str) -> str:
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=10)
        return (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else "unknown"
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def _validate_evidence(host_id: str, evidence_file: str) -> bool:
    """Validate that an evidence file contains real verification data."""
    if not os.path.isfile(evidence_file):
        return False
    try:
        with open(evidence_file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    # Required fields for a valid evidence record
    required = {"host", "version", "verified_at", "test_scope"}
    if not required.issubset(data.keys()):
        return False
    if data.get("host") != host_id:
        return False
    if not data.get("test_scope"):
        return False
    if not data.get("verified_at"):
        return False
    return True


def detect() -> list[dict]:
    results = []
    for host in HOSTS:
        binary = find_binary(host)
        present = binary is not None
        # Adapter paths are relative to the repository root (parent of ENGINE_ROOT)
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        adapter_rel = host.get("adapter", "") or ""
        adapter_path = os.path.join(repo_root, adapter_rel) if adapter_rel else ""
        adapter_ready = os.path.isfile(adapter_path) if adapter_rel else False
        # Determine status based on evidence, not just presence
        if not present:
            status = "BLOCKED"
        elif host["id"] == "opencode":
            evidence_file = os.path.join(os.path.expanduser("~"), ".eos", "evidence", "host-verification", "opencode-live.json")
            if _validate_evidence("opencode", evidence_file):
                status = "LIVE_VERIFIED"
            else:
                status = "ADAPTER_READY"
        elif host["id"] == "claude":
            evidence_file = os.path.join(os.path.expanduser("~"), ".eos", "evidence", "host-verification", "claude-live.json")
            if _validate_evidence("claude", evidence_file):
                status = "LIVE_VERIFIED"
            else:
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
