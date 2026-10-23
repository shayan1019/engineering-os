#!/usr/bin/env python3
"""Routing regression tests and relocation simulation for the EOS engine.

The tests build a temporary workspace with generic fixture projects and a
generic skill catalog. No real project name, user path, or private inventory is
required; the same engine must produce the same routing wherever it lives.

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
_ROOT = os.environ.get("EOS_WORKSPACE") or os.getcwd()

_ROUTER_MODULE = None


def router_module():
    """The router under test, imported once."""
    global _ROUTER_MODULE
    if _ROUTER_MODULE is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "route_task_under_test", os.path.join(_ENGINE, "router", "route_task.py"))
        _ROUTER_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_ROUTER_MODULE)
    return _ROUTER_MODULE


def library_names(root=None):
    """Skill names the router could actually choose from in this scope."""
    try:
        return {e.get("name", "") for e in router_module().catalog_skills(root or _ROOT)}
    except Exception:
        return set()


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def make_fixture():
    """A temporary workspace with three generic projects and a small catalog."""
    root = tempfile.mkdtemp(prefix="eos_route_")
    registry = os.path.join(root, ".agents", "registry")
    os.makedirs(registry, exist_ok=True)
    for name in ("demo", "notes", "datalab"):
        os.makedirs(os.path.join(root, name), exist_ok=True)

    profiles = {
        "version": "1",
        "generated": "fixture",
        "portability": "All paths are relative to the fixture root.",
        "resolution_rule": "alias, then cwd, then unresolved",
        "profiles": [
            {
                "id": "demo",
                "name": "Demo application",
                "aliases": ["demo"],
                "path": "demo",
                "domains": ["software", "testing"],
                "preferred_skills": ["tdd", "code-quality", "verification-before-completion"],
                "conditional_skills": ["fable-5-1"],
                "invariants": ["one writer", "verifier is not the writer"],
                "verify": ["python -m pytest"]
            },
            {
                "id": "notes",
                "name": "Notes workspace",
                "aliases": ["notes"],
                "path": "notes",
                "domains": ["documentation", "writing"],
                "preferred_skills": ["writing-plans", "documentation-and-adrs"],
                "verify": ["python -m mkdocs build"]
            },
            {
                "id": "datalab",
                "name": "Data lab",
                "aliases": ["datalab", "data lab"],
                "path": "datalab",
                "domains": ["data", "finance"],
                "preferred_skills": ["backtesting", "yahoo-finance"],
                "verify": ["python -m pytest tests/data"]
            }
        ],
        "workspace_index": {"root_children": [], "desktop_siblings": []}
    }
    write_json(os.path.join(registry, "PROJECT_PROFILES.json"), profiles)

    catalog = {
        "version": "1",
        "generated": "fixture",
        "entries": [
            {"name": "tdd", "domains": ["software", "testing"], "trust": "LOCAL_VERIFIED"},
            {"name": "test-driven-development", "domains": ["software", "testing"], "trust": "LOCAL_VERIFIED"},
            {"name": "code-quality", "domains": ["software", "testing"], "trust": "LOCAL_VERIFIED"},
            {"name": "verification-before-completion", "domains": ["software", "testing"], "trust": "LOCAL_VERIFIED"},
            {"name": "writing-plans", "domains": ["documentation", "writing"], "trust": "LOCAL_VERIFIED"},
            {"name": "documentation-and-adrs", "domains": ["documentation", "writing"], "trust": "LOCAL_VERIFIED"},
            {"name": "backtesting", "domains": ["data", "finance"], "trust": "LOCAL_VERIFIED"},
            {"name": "yahoo-finance", "domains": ["data", "finance"], "trust": "LOCAL_VERIFIED"},
            {"name": "fable-5-1", "domains": ["software", "methodology", "knowledge-graph"], "trust": "LOCAL_VERIFIED"},
            {"name": "graph-engineering", "domains": ["knowledge-graph"], "trust": "LOCAL_VERIFIED"},
            {"name": "ai-inventor", "domains": ["invention"], "trust": "LOCAL_VERIFIED"},
            {"name": "api-and-interface-design", "domains": ["api"], "trust": "LOCAL_VERIFIED"}
        ]
    }
    write_json(os.path.join(registry, "SKILL_CATALOG.json"), catalog)

    routing_map = {
        "version": "1",
        "clusters": [
            {
                "id": "testing",
                "primary": ["tdd", "test-driven-development"],
                "fallback": ["code-quality", "verification-before-completion"],
                "triggers": ["test", "tdd", "regression"]
            },
            {
                "id": "methodology",
                "primary": ["fable-5-1"],
                "fallback": [],
                "triggers": ["fable-5-1", "multi-file", "refactor", "evidence", "regression"]
            },
            {
                "id": "docs",
                "primary": ["writing-plans", "documentation-and-adrs"],
                "fallback": [],
                "triggers": ["documentation", "architecture note", "plan"]
            },
            {
                "id": "data",
                "primary": ["backtesting", "yahoo-finance"],
                "fallback": [],
                "triggers": ["backtest", "market data"]
            }
        ]
    }
    write_json(os.path.join(registry, "ROUTING_MAP.json"), routing_map)
    return root


def duplicate_source_check():
    """One skill file must be indexed once per discovery root."""
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
    """A verified badge must be evidence, not a filename."""
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
    {"id": "demo_test_route", "task": "demo project entry bug: write a regression test",
     "expect": {"project": "demo", "kg": False, "risk": "medium",
                "skills": ["tdd"], "agents": ["test-engineer"]}},
    {"id": "notes_architecture_docs", "task": "write architecture documentation for notes",
     "expect": {"project": "notes", "kg": False, "risk": "medium",
                "skills": ["writing-plans"]}},
    {"id": "datalab_backtest", "task": "backtest a strategy in datalab",
     "expect": {"project": "datalab", "kg": False, "risk": "medium",
                "skills": ["backtesting"]}},
    {"id": "knowledge_graph", "task": "build a knowledge graph and ontology for demo",
     "expect": {"project": "demo", "kg": True, "fable_signal": True,
                "skills": ["fable-5-1"]}},
    {"id": "high_risk_delete", "task": "delete the production database",
     "expect": {"project": None, "kg": False, "risk": "high",
                "policy": "approval_required"}},
    {"id": "unresolved_still_routes", "task": "FastAPI backend tenant isolation test",
     "expect": {"project": None, "kg": False, "risk": "medium",
                "primary": ["tdd"], "agents": ["test-engineer"]}},
    {"id": "low_risk_typo", "task": "fix a typo in the demo README",
     "expect": {"project": "demo", "kg": False, "risk": "low", "policy": "auto",
                "fable_signal": False, "skills_not_preferred": ["fable-5-1"]}},
    {"id": "trivial_no_force", "task": "hello, how are you",
     "expect": {"project": None, "kg": False, "fable_signal": False,
                "skills_not_preferred": ["fable-5-1"]}},
    {"id": "multi_file_methodology", "task": "multi-file refactor with regression evidence",
     "expect": {"project": None, "kg": False, "fable_signal": True,
                "skills": ["fable-5-1"]}},
    {"id": "explicit_methodology", "task": "use fable-5-1 to plan this work",
     "expect": {"project": None, "kg": False, "primary": ["fable-5-1"]}},
    {"id": "unrelated_domain_no_force", "task": "demo project entry bug",
     "expect": {"project": "demo", "kg": False, "fable_signal": False,
                "skills_not_preferred": ["fable-5-1"]}},
    {"id": "how_is_not_delete", "task": "how are skills loaded in a session",
     "expect": {"project": None, "kg": False, "risk": "medium",
                "policy": "verify_required", "fable_signal": False}}
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
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def abs_leak_scan(root, engine=None):
    """Scan program files for machine-specific absolute paths."""
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
                        continue
                    for a in ABSOLUTE_PATTERNS:
                        if re.search(a, line):
                            leaks.append(f"{os.path.relpath(p, engine)}:{no}: {a}")
    return leaks


def _glob(root, pattern):
    import glob
    return glob.glob(os.path.join(root, pattern.replace("/", os.sep)))


def main():
    root = make_fixture()
    library = library_names(root)
    fails = []
    results = []
    try:
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
                            details.append(f"skill '{s}' unexpectedly in preferred")
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

        # relocation simulation: copy the engine to a temp location and re-run the
        # same cases against the same fixture. The engine must produce identical
        # routing wherever it lives.
        tmp = tempfile.mkdtemp(prefix="eos_reloc_")
        try:
            tmp_engine = os.path.join(tmp, os.path.basename(_ENGINE))
            for rel in ["core", "registry", "router", "tools", "agents", "skills", "host-plugins"]:
                src = os.path.join(_ENGINE, rel)
                if os.path.isdir(src):
                    shutil.copytree(src, os.path.join(tmp_engine, rel))
            rel_ok = True
            for case in CASES:
                out = run_route(root, case["task"], engine=tmp_engine)
                w = run_route(root, case["task"])
                same = out is not None and w is not None and out["resolved"] == w["resolved"] \
                    and out["kg_required"] == w["kg_required"] and out["risk"] == w["risk"] \
                    and out["skills"] == w["skills"]
                if not same:
                    rel_ok = False
                    print(f"FAIL relocation mismatch: {case['id']}")
            leaks = abs_leak_scan(root)
            if leaks:
                rel_ok = False
                print("FAIL absolute-path leakage:", leaks)
            results.append({"case": "relocation_simulation", "pass": rel_ok, "details": leaks})
            print(f"{'PASS' if rel_ok else 'FAIL'} relocation_simulation")
            if not rel_ok:
                fails.append("relocation_simulation")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        # registry precedence: a workspace that carries its own .agents/registry is
        # the scope being routed for, so it must win over the engine's copy.
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

        # a deliberately malformed contract must fail, the real one must pass
        def skill_file(rel):
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
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\nsummary: {len(results) - len(fails)}/{len(results)} passed; failed: {fails}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
