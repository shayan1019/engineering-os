#!/usr/bin/env python3
"""route_tests.py — regression tests for routing + relocation simulation.

6 routing cases (task spec #27) + path-independence check + absolute-path leakage scan.
Exit 0 = all pass, 1 = failure.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))

CASES = [
    {"id": "case1_bighero_entry_bug", "task": "BigHero'da entry bug var, sinyal gelmiyor",
     "expect": {"project": "bighero", "kg": False, "risk": "high", "policy": "approval_required"}},
    {"id": "case2_aura_terminal", "task": "AURA terminal mesajları kayboluyor",
     "expect": {"project": "aura", "kg": False, "risk": "medium", "policy": "verify_required"}},
    {"id": "case3_ups_excel", "task": "UPS'deki Excel dosyasındaki verileri analiz et",
     "expect": {"project": "ups", "kg": False, "skills": ["xlsx"]}},
    {"id": "case4_new_ai_project", "task": "yeni bir AI projesi kurmak istiyorum",
     "expect": {"project": None, "kg": False}},
    {"id": "case5_bighero_kg", "task": "BigHero trade verilerinden bilgi grafiği ve ontoloji çıkar",
     # Stage 7 documented delta: KG-required work now routes to fable-5-1 (internalizes
     # Knowledge/Evidence Graph natively) instead of force-adding graph-engineering as a
     # second authority. Pre-Stage-7 this asserted skills=["graph-engineering"].
     "expect": {"project": "bighero", "kg": True, "skills": ["fable-5-1"], "fable_signal": True}},
    {"id": "case6_aura_typo", "task": "AURA'da tek satır typo düzelt",
     "expect": {"project": "aura", "kg": False, "risk": "low", "policy": "auto",
                "fable_signal": False, "skills_not_preferred": ["fable-5-1"]}},
    # Stage 7 routing acceptance tests (A-E from the cutover instructions)
    {"id": "case7_trivial_no_force", "task": "merhaba nasılsın",
     "expect": {"project": None, "kg": False, "fable_signal": False,
                "skills_not_preferred": ["fable-5-1"]}},
    {"id": "case8_complex_multifile", "task": "bu projede çoklu dosya değişikliği yapıp regresyon olmadan teslim etmem lazım",
     "expect": {"project": None, "kg": False, "fable_signal": True, "skills": ["fable-5-1"]}},
    {"id": "case9_evidence_heavy", "task": "bu kararın kanıt zincirini ve provenance'ını göstermem gerekiyor",
     "expect": {"project": None, "kg": False, "fable_signal": True, "skills": ["fable-5-1"]}},
    {"id": "case10_explicit_fable_5_1", "task": "fable-5-1 ile bu işi yap",
     "expect": {"project": None, "kg": False, "primary": ["fable-5-1"]}},
    {"id": "case11_unrelated_domain_no_force", "task": "AURA terminal mesajları kayboluyor",
     # Same task as case2 in spirit: confirms fable-5-1's ai-engineering/memory catalog
     # domains alone (which DO overlap with AURA's profile domains, appearing in
     # skills.catalog as a loose candidate) do NOT promote it into skills.preferred/primary
     # without an actual signal — the router must not route by domain match alone.
     "expect": {"project": "aura", "kg": False, "fable_signal": False,
                "skills_not_preferred": ["fable-5-1"]}},
]

ABSOLUTE_PATTERNS = [r"(?<!\w)[a-zA-Z]:[\\/]", r"[\\/]Users[\\/]", r"\bOneDrive\b"]
CANONICAL_GLOBS = [
    "AGENTS.md", "CLAUDE.md", "GEMINI.md",
    ".agents/core/*.md", ".agents/registry/*.json", ".agents/registry/*.md",
    ".agents/router/*.py", ".agents/tools/*.ps1", ".codex/agents/*.toml",
]
PATTERN_DEFS = re.compile(
    r"\[a-zA-Z\]:\[\\\\/\]|\[\\\\/\]Users\[\\\\/\]|\\bOneDrive\\b|-match|re\.(search|match|compile)\("
)


def run_contract_check(root, contract_rel_path):
    """Runs validate_skills.py --check-contract=<path> and returns its exit code."""
    cmd = [sys.executable, os.path.join(root, ".agents", "router", "validate_skills.py"),
           f"--check-contract={contract_rel_path}", f"--root={root}"]
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", cwd=root)
    return r.returncode, r.stdout


def run_route(root, task, cwd=None):
    cmd = [sys.executable, os.path.join(root, ".agents", "router", "route_task.py"),
           "--task", task, "--root", root]
    if cwd:
        cmd += ["--cwd", cwd]
    # explicit utf-8 decode: route_task.py's JSON output may contain non-ASCII (Turkish)
    # text, and subprocess.run(text=True) otherwise falls back to the host locale codec
    # (cp1252 on some Windows consoles), which crashes on those bytes.
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def abs_leak_scan(root):
    leaks = []
    for pat in CANONICAL_GLOBS:
        for p in _glob(root, pat):
            try:
                lines = open(p, "r", encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for no, line in enumerate(lines, 1):
                if PATTERN_DEFS.search(line):
                    continue  # pattern tanim satiri (self-match onlenir)
                for a in ABSOLUTE_PATTERNS:
                    if re.search(a, line):
                        leaks.append(f"{os.path.relpath(p, root)}:{no}: {a}")
    return leaks


def _glob(root, pattern):
    import glob
    return glob.glob(os.path.join(root, pattern.replace("/", os.sep)))


def main():
    root = _ROOT
    fails = []
    results = []
    for case in CASES:
        out = run_route(root, case["task"])
        ok = out is not None
        details = []
        if ok:
            exp = case["expect"]
            if out["resolved"]["project_id"] != exp.get("project"):
                ok = False
                details.append(f"project={out['resolved']['project_id']} != {exp.get('project')}")
            if out["kg_required"] != exp.get("kg"):
                ok = False
                details.append(f"kg={out['kg_required']} != {exp.get('kg')}")
            if "risk" in exp and out["risk"]["level"] != exp["risk"]:
                ok = False
                details.append(f"risk={out['risk']['level']} != {exp['risk']}")
            if "policy" in exp and out["risk"]["policy"]["gate"] != exp["policy"]:
                ok = False
                details.append(f"policy={out['risk']['policy']['gate']} != {exp['policy']}")
            if "skills" in exp:
                have = set(out["skills"]["preferred"]) | set(out["skills"]["catalog"])
                for s in exp["skills"]:
                    if s not in have:
                        ok = False
                        details.append(f"skill '{s}' not in candidates")
            if "primary" in exp:
                for s in exp["primary"]:
                    if s not in out["skills"]["primary"]:
                        ok = False
                        details.append(f"skill '{s}' not in skills.primary")
            if "skills_not_preferred" in exp:
                for s in exp["skills_not_preferred"]:
                    if s in out["skills"]["preferred"]:
                        ok = False
                        details.append(f"skill '{s}' unexpectedly in preferred (should not be force-routed)")
            if "fable_signal" in exp and out["fable_5_1_signal"]["active"] != exp["fable_signal"]:
                ok = False
                details.append(f"fable_5_1_signal.active={out['fable_5_1_signal']['active']} != {exp['fable_signal']}")
        results.append({"case": case["id"], "pass": ok, "details": details})
        if not ok:
            fails.append(case["id"])
        print(f"{'PASS' if ok else 'FAIL'} {case['id']} {details}")

    # relocation simulation: copy control plane to temp root, re-run same cases
    tmp = tempfile.mkdtemp(prefix="opencode_reloc_")
    try:
        for rel in ["AGENTS.md", ".agents/core", ".agents/registry", ".agents/router"]:
            src = os.path.join(root, rel)
            dst = os.path.join(tmp, rel)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            elif os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        rel_ok = True
        for case in CASES:
            out = run_route(tmp, case["task"])
            w = run_route(root, case["task"])
            same = out is not None and w is not None and out["resolved"] == w["resolved"] \
                and out["kg_required"] == w["kg_required"] and out["risk"] == w["risk"]
            if not same:
                rel_ok = False
                print(f"FAIL relocation mismatch: {case['id']}")
        # leakage scan (workspace + relocated copy)
        leaks = abs_leak_scan(root)
        if leaks:
            rel_ok = False
            print("FAIL absolute-path leakage:", leaks)
        results.append({"case": "relocation_simulation", "pass": rel_ok, "details": leaks})
        print(f"{'PASS' if rel_ok else 'FAIL'} relocation_simulation (temp root: {tmp})")
        if not rel_ok:
            fails.append("relocation_simulation")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # fable-5-1 skill.contract.yaml — deterministic acceptance test (Stage 1 of the
    # fable-5-1 consolidation plan): a deliberately malformed contract must fail,
    # the real one must pass. Both are no-ops (skip, don't fail) if the package
    # doesn't exist yet, so this harness stays green for anyone running it before
    # fable-5-1 is introduced.
    malformed_fixture = ".agents/skills/fable-5-1/tests/fixtures/malformed-contract-example.yaml"
    real_contract = ".agents/skills/fable-5-1/skill.contract.yaml"
    if os.path.exists(os.path.join(root, malformed_fixture)):
        code, out = run_contract_check(root, malformed_fixture)
        ok = code != 0 and "ERROR:" in out
        results.append({"case": "fable_5_1_malformed_contract_fails", "pass": ok, "details": [] if ok else [out]})
        if not ok:
            fails.append("fable_5_1_malformed_contract_fails")
        print(f"{'PASS' if ok else 'FAIL'} fable_5_1_malformed_contract_fails (exit={code})")
    if os.path.exists(os.path.join(root, real_contract)):
        code, out = run_contract_check(root, real_contract)
        ok = code == 0
        results.append({"case": "fable_5_1_real_contract_passes", "pass": ok, "details": [] if ok else [out]})
        if not ok:
            fails.append("fable_5_1_real_contract_passes")
        print(f"{'PASS' if ok else 'FAIL'} fable_5_1_real_contract_passes (exit={code})")

    print(f"\nsummary: {len(results) - len(fails)}/{len(results)} passed; failed: {fails}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
