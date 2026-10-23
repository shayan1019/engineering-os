#!/usr/bin/env python3
"""validate_skills.py — skill library audit (read-only; --generate writes SKILL_CATALOG.json).

Sources (priority order; first occurrence wins, later ones are shadowed):
  1. <root>/.agents/skills   (canonical — full error enforcement)
  2. <root>/.claude/skills   (workspace secondary — issues reported as warnings)
  3. ~/.agents/skills        (global user library — issues reported as warnings)

Checks: frontmatter (name/description), depth-1, duplicate names, broken local links,
runtime literals, openai.yaml schema (interface block), skill.contract.yaml (when present
in a skill directory — generic, applies to any skill, not just fable-5-1). Exit 0 = PASS,
1 = FAIL.

Standalone contract check (no full scan): `--check-contract=<path-to-skill.contract.yaml>`
validates that one file in isolation and exits 1 on any error — used by route_tests.py's
malformed-contract regression case.
"""
import hashlib
import json
import os
import re
import sys
from datetime import date

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_ROOT = os.path.dirname(_SCRIPT_DIR)          # .agents (workspace) or engine (installed)
# Only a fallback: the scope root is the project in hand, never the engine's parent.
_DEFAULT_ROOT = os.environ.get("EOS_WORKSPACE") or os.getcwd()


def registry_dir(root):
    """Where the program's registry lives: with the engine, not with the project."""
    for candidate in (os.path.join(_ENGINE_ROOT, "registry"),
                      os.path.join(root, ".agents", "registry")):
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(root, ".agents", "registry")

DOMAIN_KEYWORDS = {
    "trading": ["trading", "mt5", "mql", "backtest", "forex", "broker", "alpaca", "portfolio", "option", "risk", "sinyal", "pinescript", "pine"],
    "ai-engineering": ["llm", "rag", "agent", "prompt", "mcp", "eval", "ai engineering", "model", "claude", "gemini", "openai"],
    "data": ["data", "excel", "xlsx", "csv", "sql", "scraper", "api", "financial data", "quote", "screener"],
    "webdev": ["web", "react", "next", "frontend", "ui", "css", "tailwind", "component", "browser", "playwright", "storybook", "html"],
    "ml": ["machine learning", "deep learning", "training", "dataset", "lgbm", "lightgbm", "pytorch", "model"],
    "document": ["docx", "pdf", "pptx", "word", "presentation", "slides"],
    "qa-test": ["test", "qa", "testing", "e2e", "debug", "troubleshoot", "bug"],
    "design": ["design", "brand", "logo", "banner", "theme", "art"],
    "security": ["security", "vpn", "credential", "hardening"],
    "memory": ["memory", "lesson", "knowledge graph", "graphrag", "graph"],
    "video": ["video", "screen recording", "ffmpeg", "ocr"],
}

CLAUDE_LITERALS = [r'Skill\("', r"Skill\('", r"\bTask\(", r"/agents\b", r"claude -p", r"CLAUDE\.md", r"\bWrite\b.*tool"]

# skill.contract.yaml validation — generic, reusable by any skill that ships one.
CONTRACT_SCHEMA_VERSIONS = {"fable5.1/v1"}
EXECUTION_DEPTH_LEVELS = {"atomic", "direct", "planned", "graph", "multi_agent_graph", "evidence_graph"}
CAPABILITY_FLAGS = {
    "structured_output", "async_tools", "mid_turn_steering", "subagents",
    "persistent_state", "history_constraints", "parallel_tool_support",
}
REQUIRED_AUTHORITY_INVARIANTS = {
    "model_is_not_policy_authority", "writer_is_not_verifier",
    "parallelism_requires_disjoint_write_sets", "worker_is_not_merge_owner_unless_assigned",
    "model_saying_done_is_not_verified_completion",
}


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1]
    return v


def _parse_yaml_block(lines, i, indent):
    """Recursive-descent parser for one indentation level. `lines` = [(indent, content), ...]."""
    if i >= len(lines) or lines[i][0] != indent:
        return None, i
    if lines[i][1].startswith("- "):
        result = []
        while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
            result.append(_unquote(lines[i][1][2:]))
            i += 1
        return result, i
    result = {}
    while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
        content = lines[i][1]
        if ":" not in content:
            i += 1
            continue
        k, v = content.split(":", 1)
        k, v = k.strip(), v.strip()
        i += 1
        if v == "":
            if i < len(lines) and lines[i][0] > indent:
                child, i = _parse_yaml_block(lines, i, lines[i][0])
                result[k] = child
            else:
                result[k] = None
        else:
            result[k] = _unquote(v)
    return result, i


def parse_mini_yaml(text):
    """Minimal indentation-based YAML subset parser (maps/lists/scalars only, 2-space
    indents, no flow style/anchors/multiline scalars) — deliberately stdlib-only, same
    philosophy as parse_frontmatter() below. Sufficient for authored skill.contract.yaml
    files; not a general YAML parser."""
    raw = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        raw.append((len(ln) - len(ln.lstrip(" ")), stripped))
    if not raw:
        return {}
    value, _ = _parse_yaml_block(raw, 0, raw[0][0])
    return value


def check_contract(contract_path, skill_dir, root):
    """Validate one skill.contract.yaml. Returns (errors, warnings) — both plain message
    lists, caller decides how to route them (error vs warning depends on canonical/shadowed
    source, same as every other check in this file)."""
    errors, warnings = [], []
    if not os.path.exists(contract_path):
        return errors, warnings  # opt-in file; nothing to check if absent

    with open(contract_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    try:
        data = parse_mini_yaml(text)
    except Exception as e:
        errors.append(f"skill.contract.yaml: parse error: {e}")
        return errors, warnings
    if not isinstance(data, dict):
        errors.append("skill.contract.yaml: top level must be a mapping")
        return errors, warnings

    schema_version = data.get("schema_version")
    if not schema_version:
        errors.append("skill.contract.yaml: missing schema_version")
    elif schema_version not in CONTRACT_SCHEMA_VERSIONS:
        errors.append(f"skill.contract.yaml: unrecognized schema_version '{schema_version}'")

    skill = data.get("skill") or {}
    skill_id = skill.get("id")
    dir_name = os.path.basename(os.path.normpath(skill_dir))
    if not skill_id:
        errors.append("skill.contract.yaml: missing skill.id")
    elif skill_id != dir_name:
        errors.append(f"skill.contract.yaml: skill.id '{skill_id}' != directory '{dir_name}'")

    depth_model = data.get("execution_depth_model") or {}
    levels = depth_model.get("levels")
    if not isinstance(levels, list) or not levels:
        errors.append("skill.contract.yaml: execution_depth_model.levels missing/empty")
    else:
        lv_set = set(levels)
        if lv_set != EXECUTION_DEPTH_LEVELS:
            missing = EXECUTION_DEPTH_LEVELS - lv_set
            extra = lv_set - EXECUTION_DEPTH_LEVELS
            msg = "skill.contract.yaml: execution_depth_model.levels mismatch"
            if missing:
                msg += f" missing={sorted(missing)}"
            if extra:
                msg += f" unknown={sorted(extra)}"
            errors.append(msg)
    if depth_model.get("default") == "evidence_graph":
        errors.append("skill.contract.yaml: execution_depth_model.default must not be evidence_graph")

    cap_model = data.get("capability_model") or {}
    flags = cap_model.get("flags")
    if not isinstance(flags, list) or not flags:
        errors.append("skill.contract.yaml: capability_model.flags missing/empty")
        flags = []
    else:
        unknown = set(flags) - CAPABILITY_FLAGS
        if unknown:
            errors.append(f"skill.contract.yaml: capability_model.flags unknown values {sorted(unknown)}")

    authority = data.get("authority_model") or {}
    invariants = authority.get("invariants")
    if not isinstance(invariants, list) or not invariants:
        errors.append("skill.contract.yaml: authority_model.invariants missing/empty")
    else:
        missing_inv = REQUIRED_AUTHORITY_INVARIANTS - set(invariants)
        if missing_inv:
            errors.append(f"skill.contract.yaml: authority_model.invariants missing required {sorted(missing_inv)}")

    dnr = data.get("do_not_regress") or {}
    sources = dnr.get("source")
    if isinstance(sources, list):
        for s in sources:
            candidates = [
                os.path.join(root, s),
                os.path.join(root, ".agents", "skills", s),
                os.path.join(root, ".agents", "core", s),
                # A contract may point at material EOS ships inside the engine, which
                # is where it lives once installed (no workspace, no `.agents`).
                os.path.join(_ENGINE_ROOT, s),
                os.path.join(_ENGINE_ROOT, "skills", s),
                os.path.join(_ENGINE_ROOT, "core", s),
            ]
            if not any(os.path.exists(c) for c in candidates):
                errors.append(f"skill.contract.yaml: do_not_regress.source '{s}' does not resolve to an existing file/dir")

    if flags:
        fb_path = os.path.join(skill_dir, "runtime", "fallback-behavior.md")
        if not os.path.exists(fb_path):
            errors.append("skill.contract.yaml: capability_model.flags declared but runtime/fallback-behavior.md missing")
        else:
            with open(fb_path, "r", encoding="utf-8", errors="replace") as f:
                fb_text = f.read()
            missing_fb = [fl for fl in flags if fl not in fb_text]
            if missing_fb:
                errors.append(f"skill.contract.yaml: runtime/fallback-behavior.md missing fallback coverage for flags {missing_fb}")

    return errors, warnings


def parse_frontmatter(text):
    """Minimal YAML frontmatter parser (name/description/quoted values)."""
    if not text.startswith("---"):
        return None, {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None, {}
    d = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        d[k] = v
    return m.group(1), d


def extract_domains(text, name):
    t = (text + " " + name).lower()
    out = []
    for dom, kws in DOMAIN_KEYWORDS.items():
        if any(k in t for k in kws):
            out.append(dom)
    return out


# Sources that are deliberate internal mirrors of the canonical library. They
# are validated for their own integrity, but a name they share with the
# canonical source is expected rather than a duplicate finding.
MIRROR_SOURCES = {"engine-agents"}


def same_bytes(a, b):
    """True when two files hold identical bytes.

    A byte-identical copy sitting elsewhere on the discovery path is the same
    skill found twice, not a second definition of it. Any real drift changes
    the hash and the shadowing warning still fires.
    """
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return hashlib.sha256(fa.read()).digest() == hashlib.sha256(fb.read()).digest()
    except OSError:
        return False


def discover_sources(root):
    """Skill kaynakları öncelik sırasıyla. (source_id, skills_root, canonical)

    A source that resolves to the same physical directory as an earlier source
    is an adapter, not an independent copy -- `.claude/skills` is a junction
    onto `.agents/skills`. Scanning it again reported the whole library as
    duplicates and buried the findings that mattered.
    """
    declared = [
        ("workspace-agents", os.path.join(root, ".agents", "skills"), True),
        ("workspace-claude", os.path.join(root, ".claude", "skills"), False),
        ("global-agents", os.path.join(os.path.expanduser("~"), ".agents", "skills"), False),
    ]
    if os.path.isdir(os.path.join(_ENGINE_ROOT, "skills")):
        declared.append(("engine-agents", os.path.join(_ENGINE_ROOT, "skills"), False))

    sources = []
    seen_real = set()
    for source_id, skills_root, canonical in declared:
        if os.path.isdir(skills_root):
            real = os.path.normcase(os.path.realpath(skills_root))
            if real in seen_real:
                continue
            seen_real.add(real)
        sources.append((source_id, skills_root, canonical))
    return sources


def _scan_source(source_id, skills_root, root, canonical, seen_names, errors, warnings):
    """Tek kaynağı tara. Dönen: entries, source_stats, shadowed_list."""
    entries = []
    shadowed = []
    n_files = 0
    if not os.path.isdir(skills_root):
        return entries, {"source": source_id, "skills_root_rel": None, "files": 0}, shadowed

    files = []
    for dirpath, dirnames, filenames in os.walk(skills_root):
        if "backups" in dirpath or "quarantine" in dirpath:
            continue
        for fn in filenames:
            if fn == "SKILL.md":
                files.append(os.path.join(dirpath, fn))
    n_files = len(files)

    local_names = {}
    for p in sorted(files):
        rel = os.path.relpath(p, skills_root)
        parts = rel.split(os.sep)
        depth = len(parts)  # <name>/SKILL.md -> depth 2
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        fm, meta = parse_frontmatter(text)
        name = meta.get("name", "")
        desc = meta.get("description", "")
        skill_name = name or parts[0]
        # portable path: kaynak köküne göre, forward-slash, absolute yok
        port_path = os.path.relpath(p, skills_root).replace(os.sep, "/")
        entry = {"name": skill_name, "path": port_path, "dir": parts[0], "source": source_id,
                 "depth": depth, "description": desc[:200], "domains": [], "compat": "universal", "notes": []}

        def issue(msg, level=None):
            lvl = level or ("error" if canonical else "warning")
            tagged = f"[{source_id}] {rel}: {msg}"
            if lvl == "error":
                errors.append(tagged)
            else:
                warnings.append(tagged)
            entry["notes"].append(f"{lvl}:{msg}")

        if fm is None:
            issue("missing YAML frontmatter")
            entry["notes"].append("missing frontmatter")
        else:
            if not name:
                issue("missing frontmatter 'name'")
                entry["notes"].append("missing name")
            if not desc:
                issue("missing frontmatter 'description'")
                entry["notes"].append("missing description")
            if name and name != parts[0]:
                issue(f"name '{name}' != dir '{parts[0]}'", "warning")
                entry["notes"].append("name!=dir")
        if depth != 2:
            issue(f"depth {depth} != 1 (runtime discovery: gemini/opencode depth-1)")
            entry["notes"].append("depth!=1")
        # duplicate name (kaynak ici hata; kaynaklar arasi shadow -> uyarı)
        if skill_name in seen_names:
            first_src, first_rel, first_abs = seen_names[skill_name]
            msg = f"duplicate name '{skill_name}' shadowed by [{first_src}] {first_rel}"
            if canonical:
                errors.append(f"[{source_id}] {rel}: {msg}")
                entry["notes"].append("duplicate-shadowed-error")
            elif source_id in MIRROR_SOURCES:
                # A deliberate internal mirror (the engine ships its own copy).
                # It is still validated for its own integrity, but sharing a
                # name with the canonical source is by design, not a finding.
                entry["notes"].append("mirror-shadowed")
            elif same_bytes(p, first_abs):
                entry["notes"].append("identical-shadow")
            else:
                warnings.append(f"[{source_id}] {rel}: {msg}")
                entry["notes"].append("shadowed")
            shadowed.append({"name": skill_name, "source": source_id, "path": port_path,
                             "shadowed_by": {"source": first_src, "path": first_rel}})
            local_names.setdefault(skill_name, []).append(rel)
            continue
        seen_names[skill_name] = (source_id, port_path, p)
        local_names.setdefault(skill_name, []).append(rel)
        # broken local links (relative paths only).
        # Fenced blocks and inline code are stripped first: a snippet that
        # *documents* link syntax is not a link, and a template placeholder
        # such as `./rules/<name>.md` is never meant to resolve. Without this
        # the check produced mostly false positives and drowned the real ones.
        link_text = re.sub(r"```.*?```", "", text, flags=re.S)
        link_text = re.sub(r"~~~.*?~~~", "", link_text, flags=re.S)
        link_text = re.sub(r"`[^`\n]*`", "", link_text)
        for m in re.finditer(r"\]\(([^)#]+)(?:#[^)]*)?\)", link_text):
            target = m.group(1).strip()
            if target.startswith(("http", "#", "mailto:", "file:", "<")):
                continue
            if any(ch in target for ch in "<>{}"):
                continue
            target_p = os.path.normpath(os.path.join(os.path.dirname(p), target))
            if not os.path.exists(target_p):
                issue(f"broken link -> {target}", "warning")
                entry["notes"].append(f"broken-link:{target}")
        # runtime literals
        body = text
        if any(re.search(pat, body) for pat in CLAUDE_LITERALS):
            entry["compat"] = "claude-first"
            entry["notes"].append("claude-literals")
        # skill.contract.yaml (opt-in; any skill may ship one, not just fable-5-1)
        contract_path = os.path.join(os.path.dirname(p), "skill.contract.yaml")
        if os.path.exists(contract_path):
            c_errors, c_warnings = check_contract(contract_path, os.path.dirname(p), root)
            for msg in c_errors:
                issue(msg)
            for msg in c_warnings:
                issue(msg, "warning")
        entry["domains"] = extract_domains(desc + " " + name, skill_name)
        entries.append(entry)

    for nm, paths in sorted(local_names.items()):
        if len(paths) > 1:
            msg = f"duplicate name '{nm}' within source: {paths}"
            (errors if canonical else warnings).append(f"[{source_id}] {msg}")

    stats = {"source": source_id,
             "skills_root_rel": ".agents/skills" if canonical else None,
             "files": n_files}
    if not canonical:
        stats["skills_root_rel"] = ".claude/skills" if source_id == "workspace-claude" else "~/.agents/skills"
    return entries, stats, shadowed


def _scan_openai_yaml(sources, root, errors, warnings):
    for source_id, skills_root, canonical in sources:
        if not os.path.isdir(skills_root):
            continue
        for dirpath, dirnames, filenames in os.walk(skills_root):
            for fn in filenames:
                if fn != "openai.yaml":
                    continue
                p = os.path.join(dirpath, fn)
                rel = f"{source_id}/{os.path.relpath(p, skills_root)}".replace(os.sep, "/")
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    t = f.read()
                if "interface:" not in t:
                    msg = f"{rel}: missing 'interface:' block (old schema)"
                    (errors if canonical else warnings).append(msg)
                    continue
                sd = re.search(r"short_description:\s*(['\"]?)(.*?)\1\s*$", t, re.M)
                if sd:
                    v = sd.group(2)
                    if not (25 <= len(v) <= 64):
                        msg = f"{rel}: short_description len {len(v)} (need 25-64)"
                        (errors if canonical else warnings).append(msg)
                else:
                    msg = f"{rel}: short_description missing"
                    (errors if canonical else warnings).append(msg)


def scan(root, generate=False):
    errors = []
    warnings = []
    entries = []
    shadowed_all = []
    source_stats = []

    sources = discover_sources(root)
    seen_names = {}
    for source_id, skills_root, canonical in sources:
        e, stats, sh = _scan_source(source_id, skills_root, root, canonical, seen_names, errors, warnings)
        entries.extend(e)
        source_stats.append(stats)
        shadowed_all.extend(sh)

    _scan_openai_yaml(sources, root, errors, warnings)

    if generate:
        cat_path = os.path.join(registry_dir(root), "SKILL_CATALOG.json")
        payload = {
            "version": "2",
            "generated": date.today().isoformat(),
            "count": len(entries),
            "shadowed_count": len(shadowed_all),
            "sources": source_stats,
            "entries": entries,
        }
        if shadowed_all:
            payload["shadowed"] = shadowed_all
        with open(cat_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"catalog written: {cat_path} ({len(entries)} entries, {len(shadowed_all)} shadowed)")

    report = {
        "skills_total": sum(s["files"] for s in source_stats),
        "unique_entries": len(entries),
        "sources": source_stats,
        "errors": errors,
        "warnings": warnings,
        "pass": len(errors) == 0,
    }
    return report


def main():
    root = _DEFAULT_ROOT
    generate = False
    as_json = False
    check_contract_file = None
    for a in sys.argv[1:]:
        if a == "--generate":
            generate = True
        elif a == "--json":
            as_json = True
        elif a.startswith("--root="):
            root = a.split("=", 1)[1]
        elif a.startswith("--check-contract="):
            check_contract_file = a.split("=", 1)[1]

    if check_contract_file:
        skill_dir = os.path.dirname(os.path.abspath(check_contract_file))
        errs, warns = check_contract(check_contract_file, skill_dir, root)
        for e in errs:
            print("ERROR:", e)
        for w in warns:
            print("WARN :", w)
        print(f"contract_check errors={len(errs)} warnings={len(warns)}")
        sys.exit(0 if not errs else 1)

    rep = scan(root, generate=generate)
    if as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        src_line = ", ".join(f"{s['source']}={s['files']}" for s in rep["sources"])
        print(f"skills_total={rep['skills_total']} unique={rep['unique_entries']} ({src_line}) "
              f"errors={len(rep['errors'])} warnings={len(rep['warnings'])}")
        for e in rep["errors"]:
            print("ERROR:", e)
        for w in rep["warnings"]:
            print("WARN :", w)
    sys.exit(0 if rep["pass"] else 1)


if __name__ == "__main__":
    main()
