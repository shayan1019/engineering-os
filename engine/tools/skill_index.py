#!/usr/bin/env python3
"""EOS Skill Intelligence.

Indexes every skill OpenCode can discover, detects name collisions, classifies
them, and resolves a task to the smallest competent skill set with a machine
checkable explanation.

This tool never loads skill bodies into the model and never installs anything.
OpenCode remains the only skill loader.

Commands:
  discover   build/refresh .agents/registry/SKILL_INDEX.json
  resolve    task -> ranked skill set (top-k) with reasons
  explain    task -> full decision trace (chosen + rejected + why)
  doctor     structural/collision/link/trust findings
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

ROOT = ep.ENGINE_ROOT
INDEX_PATH = os.path.join(ep.REGISTRY, "SKILL_INDEX.json")
CATALOG_PATH = os.path.join(ep.REGISTRY, "SKILL_CATALOG.json")
PROFILES_PATH = os.path.join(ep.REGISTRY, "PROJECT_PROFILES.json")
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Task text arrives in Turkish and English. Domains are English, so bridge the
# high-frequency engineering vocabulary instead of guessing with embeddings.
TR_EN = {
    "islem": ["trade", "trading"], "işlem": ["trade", "trading"], "kayip": "loss", "kayıp": "loss", "zarar": "loss",
    "veri": "data", "koken": "provenance", "köken": "provenance", "kanit": "evidence", "kanitla": "evidence",
    "kanıt": "evidence", "kanıtla": "evidence", "dogrula": "verify", "duzelt": "fix", "düzelt": "fix", "hata": "bug", "arastir": "research",
    "araştır": "research", "guvenlik": "security", "güvenlik": "security", "test": "test",
    "mimari": "architecture", "tasarim": "design", "tasarım": "design", "performans": "performance",
    "hiz": "performance", "hız": "performance", "bellek": "memory", "bagimlilik": "dependency",
    "bağımlılık": "dependency", "surum": "version", "sürüm": "version", "yonlendirme": "routing",
    "yönlendirme": "routing", "hafiza": "memory", "hafıza": "memory", "izolasyon": "isolation",
    "tasima": "portability", "taşıma": "portability", "kurulum": "install", "derleme": "build",
    "kural": "contract", "sozlesme": "contract", "sözleşme": "contract",
}


def normalize(text: str) -> str:
    table = str.maketrans("şığüöçİŞĞÜÖÇ", "siguocISGUOC")
    return text.translate(table).lower()


HOME = os.path.expanduser("~")
WORKSPACE = ep.resolve_workspace()


def display_path(path: str) -> str:
    """Portable path form: workspace-relative, home-relative, never absolute.

    The registry is scanned for absolute-path leakage, and a snapshot must stay
    valid after the workspace moves. An absolute path violates both.
    """
    absolute = os.path.abspath(path)
    if absolute == WORKSPACE or absolute.startswith(WORKSPACE + os.sep):
        return os.path.relpath(absolute, WORKSPACE).replace(os.sep, "/")
    if absolute == ep.ENGINE_ROOT or absolute.startswith(ep.ENGINE_ROOT + os.sep):
        return "<engine>/" + os.path.relpath(absolute, ep.ENGINE_ROOT).replace(os.sep, "/")
    if absolute.startswith(HOME + os.sep):
        return "~/" + os.path.relpath(absolute, HOME).replace(os.sep, "/")
    return "<external>/" + os.path.basename(absolute)


def entry_abs(stored: str) -> str:
    """Invert display_path() for the forms this process can resolve.

    The index deliberately stores portable paths, so anything that needs the
    real file has to invert that form. Joining the engine root to a
    workspace-relative path (the previous behaviour in doctor) silently read
    the wrong file whenever the two roots differed.
    """
    if stored.startswith("<engine>/"):
        return os.path.join(ep.ENGINE_ROOT, stored[len("<engine>/"):])
    if stored.startswith("~/"):
        return os.path.join(HOME, stored[2:])
    if stored.startswith("<external>/"):
        return ""
    return os.path.join(WORKSPACE, stored)

# (source_id, path, scope)
# Project roots belong to the project being operated on, never to the engine: an
# installed EOS must discover the skills of whatever workspace it is pointed at.
# `<engine>/skills` carries the skills EOS itself ships.
def _dedupe_sources(candidates):
    """Drop a source whose directory an earlier source already covers.

    `.claude/skills` is a junction onto `.agents/skills`, and in the workspace
    layout ENGINE_ROOT *is* `.agents`, so the same library was indexed
    three times (633 entries). Every skill then collided with itself, which
    applied the name-collision penalty to the whole library and made the trust
    label meaningless. `project-agents` is listed first because it is the
    canonical source (AGENTS.md section 2); an adapter mirror must not win a
    tie by discovery order.
    """
    out, seen = [], set()
    for entry in candidates:
        _source_id, path, _scope = entry
        if os.path.isdir(path):
            real = os.path.normcase(os.path.realpath(path))
            if real in seen:
                continue
            seen.add(real)
        out.append(entry)
    return out


SOURCES = _dedupe_sources([
    ("project-agents", os.path.join(WORKSPACE, ".agents", "skills"), "project"),
    ("project-opencode", os.path.join(WORKSPACE, ".opencode", "skills"), "project"),
    ("project-claude", os.path.join(WORKSPACE, ".claude", "skills"), "project"),
    ("global-agents", os.path.join(HOME, ".agents", "skills"), "global"),
    ("global-opencode", os.path.join(HOME, ".config", "opencode", "skills"), "global"),
    ("global-claude", os.path.join(HOME, ".claude", "skills"), "global"),
])
if os.path.isdir(os.path.join(ep.ENGINE_ROOT, "skills")):
    SOURCES = _dedupe_sources(SOURCES + [("engine", os.path.join(ep.ENGINE_ROOT, "skills"), "engine")])

# Canonical source is .agents/skills (single authority per AGENTS.md section 2).
# .claude/skills is a generated mirror; .opencode/skills is a project-local override.
# Runtime load order is verified separately; this ordering is for EOS reporting.
PRECEDENCE = {"project-agents": 0, "project-opencode": 1, "project-claude": 2,
              "global-opencode": 3, "global-agents": 4, "global-claude": 5}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def parse_frontmatter(text: str):
    """Minimal, dependency-free YAML subset parser for SKILL.md frontmatter."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    raw, body = parts[1], parts[2]
    data, key, nested = {}, None, {}
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line[:1] not in (" ", "\t") and ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            v = v.strip()
            if v == "":
                data[key] = {}
                nested[key] = True
            else:
                data[key] = v.strip('"').strip("'")
                nested[key] = False
        elif key and line[:1] in (" ", "\t") and ":" in line:
            k, v = line.strip().split(":", 1)
            if nested.get(key):
                data[key][k.strip()] = v.strip().strip('"').strip("'")
    return data, body


def split_list(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value]
    return [p.strip() for p in re.split(r"[,;]", str(value)) if p.strip()]


def catalog_domains():
    """Curated domains already maintained in SKILL_CATALOG.json (single source)."""
    out = {}
    if not os.path.isfile(CATALOG_PATH):
        return out
    try:
        data = json.load(open(CATALOG_PATH, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for e in data.get("entries", []):
        out[str(e.get("name", ""))] = {
            "domains": [str(d).lower() for d in (e.get("domains") or [])],
            "compat": str(e.get("compat") or ""),
        }
    return out


def collect_entries():
    entries = []
    known = catalog_domains()
    # One file, one entry. A directory-level check is not enough: ~/.claude/skills
    # can hold a per-skill link onto ~/.agents/skills, so two distinct source
    # roots still resolve to the same SKILL.md. Left alone, that file collided
    # with itself and the same library was reported hundreds of times.
    seen_files = set()
    for source_id, base, scope in SOURCES:
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            skill_dir = os.path.join(base, name)
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            real_file = os.path.normcase(os.path.realpath(skill_md))
            if real_file in seen_files:
                continue
            seen_files.add(real_file)
            try:
                text = open(skill_md, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            fm, _body = parse_frontmatter(text)
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            name = str(fm.get("name") or name).strip()
            curated = known.get(name, {})
            domains = split_list(meta.get("domains") or meta.get("domain") or fm.get("domains"))
            for d in curated.get("domains", []):
                if d not in domains:
                    domains.append(d)
            entry = {
                "name": name,
                "dir_name": name,
                "path": display_path(skill_md),
                "source": source_id,
                "scope": scope,
                "precedence": PRECEDENCE.get(source_id, 99),
                "sha256": sha256_file(skill_md),
                "description": str(fm.get("description") or "").strip(),
                "license": str(fm.get("license") or ""),
                "compatibility": str(fm.get("compatibility") or ""),
                "domains": domains,
                "catalog_compat": curated.get("compat", ""),
                "requires": split_list(meta.get("requires") or fm.get("requires")),
                "autoinvoke": str(meta.get("opencode/autoinvoke", "true")).lower() != "false",
                "slash": str(meta.get("opencode/slash", "false")).lower() == "true",
                "trust": trust_of(source_id, skill_dir),
                "mtime": int(os.path.getmtime(skill_md)),
            }
            entries.append(entry)
    return entries


def verified_record(skill_dir: str):
    """(ok, reason) for a skill's `.eos-verified` badge.

    The badge used to be checked with `isfile` alone, so an empty file earned
    the +2.0 routing bonus that LOCAL_VERIFIED carries. It must now be a JSON
    record whose sha256 matches the SKILL.md on disk: editing the skill
    invalidates the badge instead of inheriting it, and a badge that records no
    check is a claim without evidence.
    """
    marker = os.path.join(skill_dir, ".eos-verified")
    if not os.path.isfile(marker):
        return False, "no .eos-verified marker"
    try:
        with open(marker, encoding="utf-8-sig") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        return False, f"unreadable .eos-verified marker: {exc}"
    if not isinstance(record, dict):
        return False, ".eos-verified is not a JSON object"
    checks = record.get("checks")
    if not isinstance(checks, list) or not checks:
        return False, ".eos-verified records no check"
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return False, "no SKILL.md to verify"
    if str(record.get("sha256", "")).upper() != sha256_file(skill_md):
        return False, ".eos-verified sha256 does not match SKILL.md"
    named = str(record.get("skill") or "")
    if named and named != os.path.basename(os.path.normpath(skill_dir)):
        return False, f".eos-verified names '{named}', not this directory"
    return True, "verified: " + ", ".join(str(c) for c in checks)


def trust_of(source_id: str, skill_dir: str) -> str:
    if source_id == "project-agents":
        ok, _ = verified_record(skill_dir)
        return "LOCAL_VERIFIED" if ok else "LOCAL_UNREVIEWED"
    if source_id.startswith("project"):
        return "LOCAL_UNREVIEWED"
    return "GLOBAL_UNREVIEWED"


def classify_collisions(entries):
    by_name = {}
    for e in entries:
        by_name.setdefault(e["name"], []).append(e)

    collisions = []
    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda e: e["precedence"])
        winner = group[0]
        same_bytes = len({e["sha256"] for e in group}) == 1
        sources = {e["source"] for e in group}
        mirrors = sources <= {"project-agents", "project-claude", "global-claude", "global-agents"}

        for loser in group[1:]:
            if same_bytes and mirrors:
                kind = "INTENTIONAL_OVERRIDE"
                reason = "identical bytes across adapter mirror roots (documented in AGENTS.md section 2)"
            elif same_bytes:
                kind = "ACCIDENTAL_COLLISION"
                reason = "identical bytes in unrelated sources; one copy is redundant"
            elif loser["mtime"] < winner["mtime"]:
                kind = "STALE_DUPLICATE"
                reason = "older copy shadowed by a newer definition with the same name"
            else:
                kind = "INTENTIONAL_OVERRIDE"
                reason = "different content; newer definition intentionally overrides this one"
            collisions.append({
                "name": name,
                "winner": winner["source"],
                "winner_path": winner["path"],
                "shadowed": loser["source"],
                "shadowed_path": loser["path"],
                "kind": kind,
                "reason": reason,
            })
    return collisions


def build_index():
    entries = collect_entries()
    collisions = classify_collisions(entries)
    kinds = {}
    for c in collisions:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    index = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": [{"id": s, "path": display_path(p), "exists": os.path.isdir(p)}
                    for s, p, _ in SOURCES],
        "count": len(entries),
        "unique_names": len({e["name"] for e in entries}),
        "collision_count": len(collisions),
        "collision_kinds": kinds,
        "entries": entries,
        "collisions": collisions,
    }
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(index, fh, indent=1)
    return index


def load_index(rebuild=False):
    if rebuild or not os.path.isfile(INDEX_PATH):
        return build_index()
    with open(INDEX_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def tokens(text: str):
    text = normalize(text)
    raw = {t for t in re.split(r"[^a-z0-9]+", text) if len(t) > 2}
    mapped = set()
    for t in raw:
        v = TR_EN.get(t)
        if isinstance(v, list):
            mapped.update(v)
        elif v:
            mapped.add(v)
    return raw | mapped


def score(entry, task_tokens, project_domains, preferred, name_dupes):
    reasons = []
    s = 0.0
    hits_project = [d for d in entry["domains"] if normalize(d) in project_domains]
    hits_task = [d for d in entry["domains"] if normalize(d) in task_tokens and normalize(d) not in project_domains]
    if hits_project:
        s += 3.0 * len(hits_project)
        reasons.append(f"project domain: {', '.join(sorted(hits_project))}")
    if hits_task:
        s += 1.5 * len(hits_task)
        reasons.append(f"task domain: {', '.join(sorted(hits_task))}")
    if entry["name"] in preferred:
        s += 2.0
        reasons.append("project preferred skill")
    name_hits = [t for t in tokens(entry["name"]) if t in task_tokens]
    if name_hits:
        s += 2.0 * len(name_hits)
        reasons.append(f"name match: {', '.join(sorted(name_hits))}")
    desc_hits = [t for t in tokens(entry["description"]) if t in task_tokens]
    if desc_hits:
        s += 0.5 * min(3, len(desc_hits))
        reasons.append(f"description match: {', '.join(sorted(desc_hits)[:3])}")
    if entry["trust"] == "LOCAL_VERIFIED":
        s += 2.0
        reasons.append("trust: LOCAL_VERIFIED")
    elif entry["trust"] == "LOCAL_UNREVIEWED":
        s += 0.5
        reasons.append("trust: LOCAL_UNREVIEWED")
    else:
        reasons.append(f"trust: {entry['trust']}")
    if not entry["autoinvoke"]:
        s -= 2.0
        reasons.append("penalty: autoinvoke disabled")
    if entry["name"] in name_dupes:
        s -= 1.0
        reasons.append("penalty: name collision")
    return s, reasons


def find_profile(project: str):
    if not project or not os.path.isfile(PROFILES_PATH):
        return None
    try:
        data = json.load(open(PROFILES_PATH, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    key = normalize(project)
    for p in data.get("profiles", []):
        names = [p.get("id", ""), p.get("display", "")] + list(p.get("aliases") or [])
        if any(normalize(str(n)) == key for n in names if n):
            return p
    return None


def infer_profile(task: str):
    """The project a task is about, when the task names it.

    `eos skills resolve "demo project entry bug"` has to land on the demo project
    profile, otherwise the ranking silently degrades to "no project". The same
    alias rule the router uses is applied here: a name that appears in the task
    text wins, and an ambiguous match is refused rather than guessed.
    """
    if not task or not os.path.isfile(PROFILES_PATH):
        return None
    try:
        data = json.load(open(PROFILES_PATH, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    text = task.lower()
    hits = []
    for p in data.get("profiles", []):
        names = [p.get("id", ""), p.get("display", "")] + list(p.get("aliases") or [])
        for n in names:
            n = str(n or "").lower()
            if len(n) < 3:
                continue
            if n in text:
                hits.append(p)
                break
    if len(hits) == 1:
        return hits[0]
    return None


def resolve(task: str, project: str = "", k: int = 3, rebuild: bool = False):
    index = load_index(rebuild)
    task_tokens = tokens(task)
    prof = find_profile(project) or infer_profile(task)
    project_domains = {normalize(str(d)) for d in (prof.get("domains") or [])} if prof else set()
    preferred = set(prof.get("preferred_skills") or []) if prof else set()
    seen, unique = set(), []
    for e in sorted(index["entries"], key=lambda x: x["precedence"]):
        if e["name"] in seen:
            continue
        seen.add(e["name"])
        unique.append(e)

    dupes = {c["name"] for c in index["collisions"]}
    scored = []
    for e in unique:
        s, reasons = score(e, task_tokens, project_domains, preferred, dupes)
        scored.append((s, e, reasons))
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    positive = [x for x in scored if x[0] > 0]
    chosen = positive[:k]
    rejected = scored[k:k + 5]
    return {
        "task": task,
        "project": (prof or {}).get("id") or (project or None),
        "project_domains": sorted(project_domains),
        "index_generated_at": index["generated_at"],
        "chosen": [{"name": e["name"], "score": round(s, 2), "source": e["source"],
                    "trust": e["trust"], "path": e["path"], "reasons": r} for s, e, r in chosen],
        "rejected_sample": [{"name": e["name"], "score": round(s, 2), "reasons": r} for s, e, r in rejected],
        "missing_capability": len(chosen) == 0,
        "candidates_considered": len(unique),
    }


def diagnose():
    index = load_index(rebuild=True)
    findings = []
    for e in index["entries"]:
        if e["name"] != e["dir_name"]:
            findings.append({"severity": "ERROR", "kind": "NAME_DIR_MISMATCH", "skill": e["name"],
                             "detail": f"frontmatter name '{e['name']}' != directory '{e['dir_name']}'"})
        if not NAME_RE.match(e["name"]):
            findings.append({"severity": "ERROR", "kind": "INVALID_NAME", "skill": e["name"],
                             "detail": "does not match ^[a-z0-9]+(-[a-z0-9]+)*$"})
        if not (1 <= len(e["description"]) <= 1024):
            findings.append({"severity": "ERROR", "kind": "DESCRIPTION_LENGTH", "skill": e["name"],
                             "detail": f"{len(e['description'])} chars (must be 1-1024)"})
        if not e["description"]:
            findings.append({"severity": "ERROR", "kind": "MISSING_DESCRIPTION", "skill": e["name"], "detail": ""})
        skill_md = entry_abs(e["path"])
        if e["source"] == "project-agents" and skill_md:
            # A badge that no longer matches its file is worse than no badge:
            # it would keep earning verified trust after the skill changed.
            marker = os.path.join(os.path.dirname(skill_md), ".eos-verified")
            if os.path.isfile(marker):
                ok, reason = verified_record(os.path.dirname(skill_md))
                if not ok:
                    findings.append({"severity": "WARN", "kind": "TRUST_MARKER_STALE",
                                     "skill": e["name"], "detail": reason})
        if skill_md and os.path.isfile(skill_md):
            text = open(skill_md, encoding="utf-8", errors="replace").read()
            # A snippet that *documents* link syntax is not a link, and a
            # template placeholder such as `./src/<pkg>/README.md` is never
            # meant to resolve. Scanning raw text reported three such
            # non-links as broken, which is how a warning becomes noise.
            text = re.sub(r"```.*?```", "", text, flags=re.S)
            text = re.sub(r"~~~.*?~~~", "", text, flags=re.S)
            text = re.sub(r"`[^`\n]*`", "", text)
            for m in re.finditer(r"\[[^\]]*\]\(([^)#]+)\)", text):
                target = m.group(1).strip()
                if target.startswith(("http://", "https://", "mailto:", "#", "<")):
                    continue
                if any(ch in target for ch in "<>{}"):
                    continue
                resolved = os.path.normpath(os.path.join(os.path.dirname(skill_md), target))
                if not os.path.exists(resolved):
                    findings.append({"severity": "WARN", "kind": "BROKEN_LINK", "skill": e["name"], "detail": target})
    for c in index["collisions"]:
        if c["kind"] == "ACCIDENTAL_COLLISION":
            findings.append({"severity": "WARN", "kind": "ACCIDENTAL_COLLISION", "skill": c["name"],
                             "detail": f"{c['winner']} vs {c['shadowed']}"})
    return index, findings


def verify_skill(name: str, extra_checks) -> int:
    """Pin a skill's current bytes as verified, with the evidence it rests on.

    Trust is worth +2.0 in routing, so the badge cannot be a filename. The
    skill's own contract check runs first when it ships one; a failing check
    writes nothing. `--check` records any verification EOS cannot run itself.
    """
    entry = None
    for e in collect_entries():
        if e["name"] == name and e["source"] == "project-agents":
            entry = e
            break
    if entry is None:
        print(f"BLOCKED: '{name}' is not a project skill under .agents/skills")
        return 2
    skill_md = entry_abs(entry["path"])
    skill_dir = os.path.dirname(skill_md)
    checks = []
    contract = os.path.join(skill_dir, "skill.contract.yaml")
    if os.path.isfile(contract):
        cmd = [sys.executable, os.path.join(ROOT, "router", "validate_skills.py"),
               f"--check-contract={os.path.relpath(contract, WORKSPACE)}",
               f"--root={WORKSPACE}"]
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", cwd=WORKSPACE)
        if proc.returncode != 0:
            print("BLOCKED: the skill's own contract check failed; nothing was written")
            print((proc.stdout or "").strip())
            return 1
        checks.append("skill.contract.yaml")
    checks.extend(extra_checks or [])
    if not checks:
        print("BLOCKED: nothing was checked; a badge with an empty check list is a claim without evidence")
        print("         pass --check <name> to record what this badge attests")
        return 2
    digest = sha256_file(skill_md)
    record = {
        "schema": 1,
        "skill": name,
        "sha256": digest,
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": checks,
    }
    with open(os.path.join(skill_dir, ".eos-verified"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, indent=1)
        fh.write("\n")
    build_index()
    print(f"verified {name}: {digest} checks={', '.join(checks)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="skill_index.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover"); d.add_argument("--json", action="store_true")
    r = sub.add_parser("resolve"); r.add_argument("--task", required=True); r.add_argument("--project", default="")
    r.add_argument("-k", type=int, default=3); r.add_argument("--rebuild", action="store_true")
    x = sub.add_parser("explain"); x.add_argument("--task", required=True); x.add_argument("--project", default="")
    x.add_argument("-k", type=int, default=3)
    q = sub.add_parser("doctor"); q.add_argument("--json", action="store_true")
    v = sub.add_parser("verify"); v.add_argument("name")
    v.add_argument("--check", action="append", default=[],
                   help="name of a check this badge attests (repeatable)")

    args = ap.parse_args()
    if args.cmd == "verify":
        return verify_skill(args.name, args.check)
    if args.cmd == "discover":
        idx = build_index()
        if args.json:
            print(json.dumps({k: idx[k] for k in ("count", "unique_names", "collision_count", "collision_kinds")}, indent=2))
        else:
            print(f"skills indexed : {idx['count']}")
            print(f"unique names   : {idx['unique_names']}")
            print(f"collisions     : {idx['collision_count']}  {idx['collision_kinds']}")
            print(f"index          : {os.path.relpath(INDEX_PATH, ROOT)}")
        return 0
    if args.cmd == "resolve":
        print(json.dumps(resolve(args.task, args.project, args.k, args.rebuild), indent=2))
        return 0
    if args.cmd == "explain":
        res = resolve(args.task, args.project, args.k)
        print(f"task: {res['task']}\nconsidered: {res['candidates_considered']} unique skills\n")
        if res["missing_capability"]:
            print("MISSING_CAPABILITY: no skill passed the relevance threshold")
            return 1
        for i, c in enumerate(res["chosen"], 1):
            print(f"{i}. {c['name']}  score={c['score']}  [{c['trust']}]  {c['source']}")
            for reason in c["reasons"]:
                print(f"     - {reason}")
        print("\nrejected (near miss):")
        for c in res["rejected_sample"]:
            print(f"  - {c['name']} score={c['score']}")
        return 0
    if args.cmd == "doctor":
        idx, findings = diagnose()
        if args.json:
            print(json.dumps({"index": {k: idx[k] for k in ("count", "collision_count", "collision_kinds")},
                              "findings": findings}, indent=2))
        else:
            errors = [f for f in findings if f["severity"] == "ERROR"]
            warns = [f for f in findings if f["severity"] == "WARN"]
            print(f"ERRORS {len(errors)} | WARNINGS {len(warns)}")
            for f in errors[:20]:
                print(f"  ERROR {f['kind']}: {f['skill']} {f['detail']}")
            kinds = {}
            for f in warns:
                kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
            print(f"  WARN summary: {kinds}")
        return 0 if not [f for f in findings if f["severity"] == "ERROR"] else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
