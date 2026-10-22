#!/usr/bin/env python3
"""validate_workspace.py — control plane integrity audit (read-only).

Checks: AGENTS.md (+size/sections), PROJECT_INVENTORY.md (+Resolution Index marker),
CLAUDE.md/GEMINI.md, registry JSON validity, .codex/agents TOML, tools exist,
absolute-path leakage in canonical config, skills sub-audit. Exit 0 = PASS, 1 = FAIL.
"""
import json
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))

AGENTS_SECTIONS = [
    "## 1", "## 2", "## 3", "## 4", "## 5", "## 6", "## 7", "## 8", "## 9",
    "## 10", "## 11", "## 12", "## 13",
]
ABSOLUTE_PATTERNS = [
    r"(?<!\w)[a-zA-Z]:[\\/]",  # drive letter (URL scheme https:// gibi false-positive'ler elenir)
    r"[\\/]Users[\\/]",        # home
    r"\bOneDrive\b",
]

CANONICAL_FILES = [
    "AGENTS.md", "CLAUDE.md", "GEMINI.md",
    ".agents/core/FABLE_CORE.md", ".agents/core/GRAPH_CORE.md",
    ".agents/registry/PROJECT_PROFILES.json", ".agents/registry/AGENT_REGISTRY.json",
    ".agents/registry/COMPATIBILITY.json",
    ".agents/router/route_task.py", ".agents/router/validate_skills.py",
    ".agents/router/validate_workspace.py", ".agents/router/new_project.py",
    ".agents/router/route_tests.py",
    ".agents/tools/bootstrap.ps1", ".agents/tools/doctor.ps1",
    ".codex/agents/explorer.toml", ".codex/agents/architect.toml",
    ".codex/agents/implementer.toml", ".codex/agents/reviewer.toml",
    ".codex/agents/verifier.toml",
]

CODEX_AGENTS = ["explorer", "architect", "implementer", "reviewer", "verifier"]


def main():
    root = _DEFAULT_ROOT
    for a in sys.argv[1:]:
        if a.startswith("--root="):
            root = a.split("=", 1)[1]
    errors = []
    warnings = []

    # 1) AGENTS.md
    ag = os.path.join(root, "AGENTS.md")
    if not os.path.exists(ag):
        errors.append("AGENTS.md missing")
    else:
        size = os.path.getsize(ag)
        if size > 32768:
            errors.append(f"AGENTS.md {size} bytes > 32KiB (codex limit)")
        with open(ag, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        missing = [s for s in AGENTS_SECTIONS if s not in text]
        if missing:
            errors.append(f"AGENTS.md missing sections: {missing}")

    # 2) Inventory + Resolution Index marker
    inv = os.path.join(root, "PROJECT_INVENTORY.md")
    if not os.path.exists(inv):
        errors.append("PROJECT_INVENTORY.md missing")
    else:
        with open(inv, "r", encoding="utf-8", errors="replace") as f:
            itext = f.read()
        if "AI Workspace Resolution Index" not in itext:
            errors.append("PROJECT_INVENTORY.md missing 'AI Workspace Resolution Index' section")

    # 3) CLAUDE.md / GEMINI.md
    cl = os.path.join(root, "CLAUDE.md")
    if os.path.exists(cl):
        with open(cl, "r", encoding="utf-8", errors="replace") as f:
            ct = f.read()
        if "@AGENTS.md" not in ct:
            errors.append("CLAUDE.md missing '@AGENTS.md' import")
    else:
        errors.append("CLAUDE.md missing")
    if not os.path.exists(os.path.join(root, "GEMINI.md")):
        errors.append("GEMINI.md missing")

    # 4) Registry JSON validity
    for name, keys in [
        ("PROJECT_PROFILES.json", ["profiles", "resolution_rule"]),
        ("AGENT_REGISTRY.json", ["roles", "rules"]),
        ("COMPATIBILITY.json", ["runtime_matrix", "literal_translation"]),
    ]:
        p = os.path.join(root, ".agents", "registry", name)
        if not os.path.exists(p):
            errors.append(f"registry/{name} missing")
            continue
        try:
            data = json.load(open(p, "r", encoding="utf-8"))
            for k in keys:
                if k not in data:
                    errors.append(f"registry/{name} missing key '{k}'")
        except Exception as e:
            errors.append(f"registry/{name} invalid JSON: {e}")

    # 5) .codex/agents TOML
    for role in CODEX_AGENTS:
        p = os.path.join(root, ".codex", "agents", f"{role}.toml")
        if not os.path.exists(p):
            errors.append(f".codex/agents/{role}.toml missing")
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            t = f.read()
        for req in ["name", "description", "developer_instructions"]:
            if not re.search(rf"^{req}\s*=", t, re.M):
                errors.append(f".codex/agents/{role}.toml missing '{req}'")

    # 6) tools
    for t in ["bootstrap.ps1", "doctor.ps1"]:
        if not os.path.exists(os.path.join(root, ".agents", "tools", t)):
            errors.append(f".agents/tools/{t} missing")

    # 7) absolute path leakage (canonical files)
    # Not: validatörler kendi pattern tanım satırlarını içerir -> o satırlar taramaya dahil edilmez
    PATTERN_DEFS = re.compile(
        r"\[a-zA-Z\]:\[\\\\/\]|\[\\\\/\]Users\[\\\\/\]|\\bOneDrive\\b|-match|re\.(search|match|compile)\("
    )
    for rel in CANONICAL_FILES:
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue  # eksiklik yukarida raporlandi
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            t = f.read()
        for line_no, line in enumerate(t.splitlines(), 1):
            if PATTERN_DEFS.search(line):
                continue  # pattern tanim satiri (self-match onlenir)
            for pat in ABSOLUTE_PATTERNS:
                m = re.search(pat, line)
                if m:
                    errors.append(f"{rel}:{line_no}: absolute path pattern '{pat}' -> ...{line[max(0, m.start()-30):m.end()+20]}")

    # 8) skills sub-audit
    sys.path.insert(0, _SCRIPT_DIR)
    from validate_skills import scan as skills_scan
    rep = skills_scan(root, generate=False)
    if not rep["pass"]:
        errors.append(f"skills audit FAIL: {len(rep['errors'])} errors (run validate_skills.py)")
    # Show every warning. The previous [:20] cap hid the tail of the list, so a
    # run that reported "20 warnings" could never show whether the 21st was a
    # different, unfixed problem.
    warnings.extend([f"skills: {w}" for w in rep["warnings"]])

    out = {"root": root, "errors": errors, "warnings": warnings, "pass": len(errors) == 0}
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"workspace audit: pass={out['pass']} errors={len(errors)} warnings={len(warnings)}")
        for e in errors:
            print("ERROR:", e)
        for w in warnings:
            print("WARN :", w)
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
