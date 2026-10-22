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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))          # <engine>/router
_ENGINE = os.path.dirname(_SCRIPT_DIR)                           # .agents, or engine/ when installed
# The scope root only matters for cwd-based resolution; the routing cases below
# resolve by alias, which must be identical from any location.
_ROOT = os.environ.get("EOS_WORKSPACE") or os.getcwd()

_ROUTER_MODULE = None


def router_module():
    """The router under test, imported once.

    A test may import the unit it tests; re-implementing the router's registry
    resolution here would let the two drift and hide exactly the regression the
    registry_precedence check exists to catch.
    """
    global _ROUTER_MODULE
    if _ROUTER_MODULE is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "route_task_under_test", os.path.join(_ENGINE, "router", "route_task.py"))
        _ROUTER_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_ROUTER_MODULE)
    return _ROUTER_MODULE


def library_names():
    """Skill names the router could actually choose from in this scope."""
    try:
        return {e.get("name", "") for e in router_module().catalog_skills(_ROOT)}
    except Exception:
        return set()


def duplicate_source_check():
    """One skill file must be indexed once.

    `.claude/skills` is a junction onto `.agents/skills`, and in the workspace
    layout ENGINE_ROOT is `.agents`, so the same 210 skills were indexed three
    times. Every entry collided with itself: the name-collision penalty hit
    the whole library and the trust label became noise.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "skill_index_under_test", os.path.join(_ENGINE, "tools", "skill_index.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen, problems = {}, []
    for entry in mod.collect_entries():
        absolute = mod.entry_abs(entry["path"])
        if not absolute:
            continue
        key = os.path.normcase(os.path.realpath(absolute))
        if key in seen:
            problems.append(f"{entry['path']} also indexed as [{seen[key]}]")
        else:
            seen[key] = entry["source"]
    return problems


def trust_marker_check():
    """A verified badge must be evidence, not a filename.

    LOCAL_VERIFIED is worth +2.0 in routing. The badge used to be accepted on
    existence alone, so any empty file bought it. Each case below is a badge
    that must be refused.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "skill_index_under_test", os.path.join(_ENGINE, "tools", "skill_index.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    tmp = tempfile.mkdtemp(prefix="eos_trust_")
    problems = []
    try:
        skill_dir = os.path.join(tmp, "demo")
        os.makedirs(skill_dir)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        marker = os.path.join(skill_dir, ".eos-verified")
        with open(skill_md, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# demo\n")
        digest = mod.sha256_file(skill_md)

        def write_marker(payload):
            with open(marker, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload if isinstance(payload, str) else json.dumps(payload))

        good = {"schema": 1, "skill": "demo", "sha256": digest, "checks": ["unit"]}
        write_marker(good)
        if not mod.verified_record(skill_dir)[0]:
            problems.append("a matching badge was refused")

        with open(skill_md, "a", encoding="utf-8") as fh:
            fh.write("edited\n")
        if mod.verified_record(skill_dir)[0]:
            problems.append("a badge survived an edit to the skill")

        with open(skill_md, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# demo\n")
        write_marker({"schema": 1, "skill": "demo", "sha256": digest, "checks": []})
        if mod.verified_record(skill_dir)[0]:
            problems.append("a badge that records no check was accepted")

        write_marker({"schema": 1, "skill": "other", "sha256": digest, "checks": ["unit"]})
        if mod.verified_record(skill_dir)[0]:
            problems.append("a badge naming another skill was accepted")

        write_marker("{not json")
        if mod.verified_record(skill_dir)[0]:
            problems.append("an unparseable badge was accepted")

        write_marker("")
        if mod.verified_record(skill_dir)[0]:
            problems.append("an empty badge file was accepted")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return problems

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
    {"id": "case12_unresolved_project_still_routes", "task": "FastAPI backend icin tenant isolation testi yaz",
     # The project is unknown here, but the task itself states its intent ("testi
     # yaz"). Before this case skills.primary came back EMPTY -- the router's main
     # output was useless whenever no profile matched, even for a task this
     # specific. The fix is a trigger, not a domain guess: case11's rule still
     # holds, domain overlap alone never promotes a skill.
     # `tdd` only exists in the workspace library, so on a clean install the
     # name cannot be asserted; the specialist selection still can.
     "expect": {"project": None, "kg": False, "fable_signal": False,
                "primary_if_available": ["tdd"], "agents": ["test-engineer"]}},
    {"id": "case13_how_is_not_delete", "task": "hangi skill yuklendi, oturumda nasil yonetiliyor",
     # ASCII "nasil" (nasıl) contains the letters of "sil" (delete). Substring
     # matching made a question about skills an irreversible operation:
     # risk=high, policy=approval_required, fable-5-1 force-routed.
     "expect": {"project": None, "kg": False, "risk": "medium",
                "policy": "verify_required", "fable_signal": False}},
    {"id": "case14_inflected_delete_is_high", "task": "eski log dosyalarini silmek istiyorum",
     # The fix must not lose the true positive: Turkish inflects the verb.
     "expect": {"project": None, "kg": False, "risk": "high",
                "policy": "approval_required"}},
]

ABSOLUTE_PATTERNS = [r"(?<!\w)[a-zA-Z]:[\\/]", r"[\\/]Users[\\/]", r"\bOneDrive\b"]
CANONICAL_GLOBS = [
    "AGENTS.md", "CLAUDE.md", "GEMINI.md",
    "core/*.md", "registry/*.json", "registry/*.md", "router/*.py", "router/*.ps1",
]
PATTERN_DEFS = re.compile(
    r"\[a-zA-Z\]:\[\\\\/\]|\[\\\\/\]Users\[\\\\/\]|\\bOneDrive\\b|-match|re\.(search|match|compile)\("
)


def run_contract_check(root, contract_rel_path):
    """Runs validate_skills.py --check-contract=<path> and returns its exit code."""
    cmd = [sys.executable, os.path.join(_ENGINE, "router", "validate_skills.py"),
           f"--check-contract={contract_rel_path}", f"--root={root}"]
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", cwd=root)
    return r.returncode, r.stdout


def run_route(root, task, cwd=None, engine=None):
    engine = engine or _ENGINE
    cmd = [sys.executable, os.path.join(engine, "router", "route_task.py"),
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


def abs_leak_scan(root, engine=None):
    """Scan the program files for machine-specific absolute paths.

    The engine is scanned where it actually lives (`<engine>/core`, `<engine>/registry`,
    `<engine>/router`); `root` is only used to report paths readably and to pick up
    any workspace-level contract files.
    """
    engine = engine or _ENGINE
    leaks = []
    for base in [engine, root]:
        if not os.path.isdir(base):
            continue
        for pat in CANONICAL_GLOBS:
            for p in _glob(base, pat):
                try:
                    lines = open(p, "r", encoding="utf-8", errors="replace").read().splitlines()
                except OSError:
                    continue
                for no, line in enumerate(lines, 1):
                    if PATTERN_DEFS.search(line):
                        continue  # pattern tanim satiri (self-match onlenir)
                    for a in ABSOLUTE_PATTERNS:
                        if re.search(a, line):
                            leaks.append(f"{os.path.relpath(p, engine)}:{no}: {a}")
    return leaks


def _glob(root, pattern):
    import glob
    return glob.glob(os.path.join(root, pattern.replace("/", os.sep)))


def main():
    root = _ROOT
    library = library_names()
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
            have = set(out["skills"]["preferred"]) | set(out["skills"]["catalog"])
            if "skills" in exp:
                for s in exp["skills"]:
                    if s not in have:
                        ok = False
                        details.append(f"skill '{s}' not in candidates")
            if "primary" in exp:
                for s in exp["primary"]:
                    if s not in out["skills"]["primary"]:
                        ok = False
                        details.append(f"skill '{s}' not in skills.primary")
            if "primary_if_available" in exp:
                # A clean install ships the engine's own handful of skills, not
                # the user's library: asserting a name there would fail on a
                # machine that simply has no such skill. The agent selection is
                # always provable; the skill name is only provable when the
                # library that contains it exists.
                for s in exp["primary_if_available"]:
                    if s not in library:
                        details.append(f"skipped '{s}': not in this library")
                    elif s not in out["skills"]["primary"]:
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
            if "agents" in exp:
                for a in exp["agents"]:
                    if a not in out["agents"]["specialists"]:
                        ok = False
                        details.append(f"agent '{a}' not in agents.specialists")
        results.append({"case": case["id"], "pass": ok, "details": details})
        if not ok:
            fails.append(case["id"])
        print(f"{'PASS' if ok else 'FAIL'} {case['id']} {details}")

    # relocation simulation: copy the engine to a temp location and re-run the same
    # cases. The engine must produce identical routing wherever it lives; that is
    # exactly the property an installed product depends on, because its engine is
    # never under the workspace.
    tmp = tempfile.mkdtemp(prefix="eos_reloc_")
    try:
        tmp_engine = os.path.join(tmp, os.path.basename(_ENGINE))
        for rel in ["core", "registry", "router", "tools", "agents"]:
            src = os.path.join(_ENGINE, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(tmp_engine, rel))
        rel_ok = True
        for case in CASES:
            out = run_route(tmp, case["task"], engine=tmp_engine)
            w = run_route(root, case["task"])
            same = out is not None and w is not None and out["resolved"] == w["resolved"] \
                and out["kg_required"] == w["kg_required"] and out["risk"] == w["risk"]
            if not same:
                rel_ok = False
                print(f"FAIL relocation mismatch: {case['id']}")
        # leakage scan: program files must not record absolute machine paths
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

    # registry precedence: a workspace that carries its own .agents/registry is
    # the scope being routed for, so it must win over the engine's copy. When the
    # engine's thin registry wins, an installed engine silently routes over its
    # own handful of skills instead of the workspace library -- the deployed
    # 0.2.4-alpha index held 7 names against a 210-skill catalog, so every cluster
    # member looked uninstalled and skills.primary came back empty.
    prec_ok = True
    prec_detail = []
    try:
        mod = router_module()
        tmp_ws = tempfile.mkdtemp(prefix="eos_reg_")
        try:
            want = os.path.join(tmp_ws, ".agents", "registry")
            os.makedirs(want)
            got = mod.registry_dir(tmp_ws)
            prec_ok = os.path.normcase(got) == os.path.normcase(want)
            if not prec_ok:
                prec_detail.append(f"workspace registry ignored: got {got}, want {want}")
                print(f"FAIL registry_precedence: {prec_detail[-1]}")
        finally:
            shutil.rmtree(tmp_ws, ignore_errors=True)
    except Exception as exc:  # pragma: no cover - defensive
        prec_ok = False
        prec_detail.append(f"registry precedence check raised: {exc}")
        print(f"FAIL registry_precedence: {exc}")
    results.append({"case": "registry_precedence", "pass": prec_ok, "details": prec_detail})
    print(f"{'PASS' if prec_ok else 'FAIL'} registry_precedence")
    if not prec_ok:
        fails.append("registry_precedence")

    # one skill file, one index entry
    dup_problems = []
    dup_ok = True
    try:
        dup_problems = duplicate_source_check()
        dup_ok = not dup_problems
    except Exception as exc:  # pragma: no cover - defensive
        dup_ok = False
        dup_problems.append(f"duplicate source check raised: {exc}")
    results.append({"case": "one_entry_per_skill_file", "pass": dup_ok, "details": dup_problems[:5]})
    print(f"{'PASS' if dup_ok else 'FAIL'} one_entry_per_skill_file {dup_problems[:5]}")
    if not dup_ok:
        fails.append("one_entry_per_skill_file")

    # verified badge: existence is not evidence
    trust_problems = []
    trust_ok = True
    try:
        trust_problems = trust_marker_check()
        trust_ok = not trust_problems
    except Exception as exc:  # pragma: no cover - defensive
        trust_ok = False
        trust_problems.append(f"trust marker check raised: {exc}")
    results.append({"case": "trust_marker_requires_matching_bytes", "pass": trust_ok,
                    "details": trust_problems})
    print(f"{'PASS' if trust_ok else 'FAIL'} trust_marker_requires_matching_bytes {trust_problems}")
    if not trust_ok:
        fails.append("trust_marker_requires_matching_bytes")

    # fable-5-1 skill.contract.yaml — deterministic acceptance test (Stage 1 of the
    # fable-5-1 consolidation plan): a deliberately malformed contract must fail,
    # the real one must pass. Both are no-ops (skip, don't fail) if the package
    # doesn't exist yet, so this harness stays green for anyone running it before
    # fable-5-1 is introduced. Skills live with the workspace (`.agents/skills`) or
    # ship inside the engine, so both locations are searched.
    def skill_file(rel):
        """Return an absolute path to a skill package, wherever it lives.

        Skills belong to the workspace (`.agents/skills`) but EOS also ships the
        methodology packages inside the engine, so an installed product can run
        its own tests without any workspace.
        """
        for base, prefix in ((root, ".agents"), (_ENGINE, "")):
            candidate = os.path.join(base, prefix, rel) if prefix else os.path.join(base, rel)
            if os.path.exists(candidate):
                return candidate
        return None

    malformed = skill_file("skills/fable-5-1/tests/fixtures/malformed-contract-example.yaml")
    real = skill_file("skills/fable-5-1/skill.contract.yaml")
    if malformed:
        code, out = run_contract_check(root, malformed)
        ok = code != 0 and "ERROR:" in out
        results.append({"case": "fable_5_1_malformed_contract_fails", "pass": ok, "details": [] if ok else [out]})
        if not ok:
            fails.append("fable_5_1_malformed_contract_fails")
        print(f"{'PASS' if ok else 'FAIL'} fable_5_1_malformed_contract_fails (exit={code})")
    if real:
        code, out = run_contract_check(root, real)
        ok = code == 0
        results.append({"case": "fable_5_1_real_contract_passes", "pass": ok, "details": [] if ok else [out]})
        if not ok:
            fails.append("fable_5_1_real_contract_passes")
        print(f"{'PASS' if ok else 'FAIL'} fable_5_1_real_contract_passes (exit={code})")

    print(f"\nsummary: {len(results) - len(fails)}/{len(results)} passed; failed: {fails}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
