#!/usr/bin/env python3
"""EOS secret and sensitive-file scan.

Runs before anything is committed or pushed. It reports the file, line and a
redacted preview; it never prints a full secret.

Two classes of failure:
  INSECURE  a real credential pattern matched
  FORBIDDEN a file type that must never enter a repository (databases, logs,
            compiled EAs, .env, broker exports)

Usage:
  python .agents/tools/secret_scan.py [paths...] [--json] [--include-tests]
Exit codes: 0 = clean, 1 = findings, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

# What `eos scan` scans by default: the project in scope, not the engine and not
# whatever directory the interpreter happens to sit in.
ROOT = ep.resolve_workspace()

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".venv-win", "venv", "target",
             "dist", "build", ".pytest_cache", ".mypy_cache", "site-packages", ".next",
             # EOS's own generated data: gate logs, promotion records and baseline
             # payloads are evidence *about* the code, never part of it. Scanning
             # them would make the product fail on its own audit trail.
             "evidence", "backups", "_eos-backup-"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".7z", ".rar",
            ".exe", ".dll", ".pyd", ".so", ".dylib", ".woff", ".woff2", ".ttf", ".mp4", ".webm",
            ".graph", ".jsonl"}
MAX_BYTES = 4_000_000

FORBIDDEN_EXT = {".db", ".sqlite", ".sqlite3", ".ex5", ".ex4", ".hcc", ".log", ".env", ".pem",
                 ".key", ".pfx", ".p12", ".jks", ".keystore", ".mdf", ".ldf"}
FORBIDDEN_NAMES = {"credentials", "credentials.json", "id_rsa", "id_ed25519", ".npmrc",
                   ".pypirc", "netrc", ".netrc", "mt5.ini", "terminal.ini"}

PATTERNS = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret", re.compile(r"(?i)aws.{0,20}secret.{0,20}['\"]([A-Za-z0-9/+=]{40})['\"]")),
    ("github_token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("telegram_bot", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("generic_assignment", re.compile(
        r"(?i)\b(api[_-]?key|secret|passwd|password|access[_-]?token|auth[_-]?token|client[_-]?secret)"
        r"\s*[:=]\s*['\"]([^'\"\s]{12,})['\"]")),
]

# Values that are obviously placeholders, not secrets.
PLACEHOLDER_WORDS = ("your", "aqui", "tu_", "placeholder", "example", "dummy", "sample",
                     "fake", "changeme", "redacted", "xxxx", "notreal", "todo")
PLACEHOLDER = re.compile(r"(?i)^(x+|\*+|\.+|<.*>|\$\{.*\}|\[.*\])$")


def is_placeholder(value: str) -> bool:
    low = value.lower()
    if PLACEHOLDER.match(value):
        return True
    if "..." in value or "…" in value:
        return True
    if any(word in low for word in PLACEHOLDER_WORDS):
        return True
    if low.startswith(("os.environ", "process.env", "getenv", "env(", "sys.env")):
        return True
    if len(set(value)) <= 3:
        return True
    # Values that are themselves named like a key slot, e.g. TU_API_KEY_AQUI
    if re.search(r"(?i)(api[_-]?key|access[_-]?token|secret|password|passwd)", value):
        return True
    return False


def redact(value: str) -> str:
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:4] + "…" + value[-3:]


def scan_file(path: str, include_tests: bool):
    findings = []
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/") if path.startswith(ROOT) else path
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1]

    if ext in FORBIDDEN_EXT or name in FORBIDDEN_NAMES:
        findings.append({"severity": "FORBIDDEN", "kind": "sensitive_file", "file": rel,
                         "line": 0, "preview": f"forbidden file type/name: {name}"})
        return findings
    if not include_tests and ("/tests/" in "/" + rel.lower() or "/test/" in "/" + rel.lower()
                              or name.startswith("test_") or name.endswith("_test.py")):
        return findings
    if ext in SKIP_EXT or os.path.getsize(path) > MAX_BYTES:
        return findings

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return findings

    for lineno, line in enumerate(lines, 1):
        if len(line) > 2000:
            continue
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(1) if match.groups() else match.group(0)
                if kind == "generic_assignment" and is_placeholder(value):
                    continue
                findings.append({"severity": "INSECURE", "kind": kind, "file": rel,
                                 "line": lineno, "preview": redact(value)})
    return findings


def excluded(path: str, patterns) -> bool:
    import fnmatch
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/") if path.startswith(ROOT) else path
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(os.path.basename(path), pat)
               for pat in patterns)


def iter_targets(paths, patterns=()):
    for raw in paths:
        p = os.path.abspath(raw)
        if os.path.isfile(p):
            if not excluded(p, patterns):
                yield p
            continue
        if not os.path.isdir(p):
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                if not excluded(full, patterns):
                    yield full


def main() -> int:
    ap = argparse.ArgumentParser(prog="secret_scan.py")
    ap.add_argument("paths", nargs="*", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--exclude", action="append", default=[],
                    help="glob (relative to workspace root) to skip, repeatable")
    args = ap.parse_args()

    targets = args.paths or [ROOT]
    findings, scanned = [], 0
    for path in iter_targets(targets, args.exclude):
        scanned += 1
        findings.extend(scan_file(path, args.include_tests))

    errors = [f for f in findings if f["severity"] == "INSECURE"]
    forbidden = [f for f in findings if f["severity"] == "FORBIDDEN"]
    if args.json:
        print(json.dumps({"scanned": scanned, "findings": findings,
                          "summary": {"insecure": len(errors), "forbidden": len(forbidden)}}, indent=2))
    else:
        print(f"scanned {scanned} file(s)")
        for f in findings[:60]:
            print(f"  {f['severity']:<9} {f['kind']:<18} {f['file']}:{f['line']}  {f['preview']}")
        print(f"INSECURE {len(errors)} | FORBIDDEN {len(forbidden)}")
        print("PASS" if not findings else "FAIL")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
