#!/usr/bin/env python3
"""Enterprise readiness self-test for a source checkout.

This is deliberately stdlib-only and bounded. It checks the invariants that a
clean GitHub checkout must preserve before a release is called enterprise
ready: plugin parity, portable paths, bundled-runtime discovery and isolated
plugin installation.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "host-plugins" / "opencode"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def check(name: str, condition: bool, detail: str = "") -> tuple[str, bool, str]:
    return name, bool(condition), detail


def resolver_parity() -> tuple[str, bool, str]:
    a = (PLUGIN / "eos-activation.js").read_text(encoding="utf-8")
    g = (PLUGIN / "eos-guard.js").read_text(encoding="utf-8")
    begin, end = "// ENGINE-RESOLVER BEGIN", "// ENGINE-RESOLVER END"
    try:
        ab = a.split(begin, 1)[1].split(end, 1)[0]
        gb = g.split(begin, 1)[1].split(end, 1)[0]
    except (IndexError, ValueError):
        return check("resolver_parity", False, "resolver markers missing")
    return check("resolver_parity", ab == gb, "" if ab == gb else "activation and guard resolver blocks differ")


def clean_runtime_discovery() -> tuple[str, bool, str]:
    # The plugin's exported resolver must select a runtime shipped inside a
    # checkout before consulting a machine PATH alias.
    node = "node.exe" if os.name == "nt" else "node"
    if subprocess.run([node, "--version"], capture_output=True).returncode != 0:
        return check("clean_runtime_discovery", False, "node runtime unavailable")
    with tempfile.TemporaryDirectory(prefix="eos-enterprise-") as raw:
        workspace = Path(raw)
        (workspace / ".agents" / "tools").mkdir(parents=True)
        (workspace / "runtime" / "python").mkdir(parents=True)
        for name in ("decision.py", "eos_activate.py"):
            (workspace / ".agents" / "tools" / name).write_text("# fixture\n", encoding="utf-8")
        runtime = workspace / "runtime" / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_text("fixture", encoding="utf-8")
        probe = workspace / "probe.mjs"
        plugin = (PLUGIN / "eos-activation.js").as_uri()
        probe.write_text(
            f'import plugin from "{plugin}";\n'
            'const r = plugin.resolveEngine(process.argv[2]);\n'
            'process.stdout.write(JSON.stringify({source:r.source, python:r.python}));\n',
            encoding="utf-8",
        )
        run = subprocess.run([node, str(probe), str(workspace)], capture_output=True, text=True, timeout=10)
        if run.returncode != 0:
            return check("clean_runtime_discovery", False, run.stderr.strip()[:300])
        try:
            result = json.loads(run.stdout)
        except json.JSONDecodeError:
            return check("clean_runtime_discovery", False, run.stdout[:300])
        return check("clean_runtime_discovery", result.get("source") == "workspace-bundled", str(result))


def portable_paths() -> tuple[str, bool, str]:
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".yaml", ".yml", ".toml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "C:\\Users\\" in text or "/Users/" in text:
            offenders.append(str(path.relative_to(ROOT)))
    return check("portable_paths", not offenders, ", ".join(offenders[:5]))


def required_surfaces() -> tuple[str, bool, str]:
    files = [
        ROOT / "tools" / "decision.py",
        ROOT / "tools" / "eos_activate.py",
        ROOT / "tools" / "plugin_install.py",
        PLUGIN / "eos-activation.js",
        PLUGIN / "eos-guard.js",
    ]
    missing = [str(p.relative_to(ROOT)) for p in files if not p.is_file()]
    return check("required_surfaces", not missing, ", ".join(missing))


def main() -> int:
    checks = [required_surfaces(), resolver_parity(), portable_paths(), clean_runtime_discovery()]
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    passed = sum(ok for _, ok, _ in checks)
    print(f"enterprise_selftest: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

