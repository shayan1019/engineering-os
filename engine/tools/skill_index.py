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

# (source_id, path, scope)
# Project roots belong to the project being operated on, never to the engine: an
# installed EOS must discover the skills of whatever workspace it is pointed at.
# `<engine>/skills` carries the skills EOS itself ships.
SOURCES = [
    ("project-opencode", os.path.join(WORKSPACE, ".opencode", "skills"), "project"),
    ("project-claude", os.path.join(WORKSPACE, ".claude", "skills"), "project"),
    ("project-agents", os.path.join(WORKSPACE, ".agents", "skills"), "project"),
    ("global-opencode", os.path.join(HOME, ".config", "opencode", "skills"), "global"),
    ("global-claude", os.path.join(HOME, ".claude", "skills"), "global"),
    ("global-agents", os.path.join(HOME, ".agents", "skills"), "global"),
]
if os.path.isdir(os.path.join(ep.ENGINE_ROOT, "skills")):
    SOURCES.append(("engine", os.path.join(ep.ENGINE_ROOT, "skills"), "engine"))

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
    for source_id, base, scope in SOURCES:
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            skill_dir = os.path.join(base, name)
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
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


def trust_of(source_id: str, skill_dir: str) -> str:
    if source_id == "project-agents":
        return "LOCAL_VERIFIED" if os.path.isfile(os.path.join(skill_dir, ".eos-verified")) else "LOCAL_UNREVIEWED"
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

    `eos skills resolve "BigHero'da entry bug var"` has to land on the BigHero
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
        skill_md = os.path.join(ROOT, e["path"])
        if os.path.isfile(skill_md):
            text = open(skill_md, encoding="utf-8", errors="replace").read()
            for m in re.finditer(r"\[[^\]]*\]\(([^)#]+)\)", text):
                target = m.group(1).strip()
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                resolved = os.path.normpath(os.path.join(os.path.dirname(skill_md), target))
                if not os.path.exists(resolved):
                    findings.append({"severity": "WARN", "kind": "BROKEN_LINK", "skill": e["name"], "detail": target})
    for c in index["collisions"]:
        if c["kind"] == "ACCIDENTAL_COLLISION":
            findings.append({"severity": "WARN", "kind": "ACCIDENTAL_COLLISION", "skill": c["name"],
                             "detail": f"{c['winner']} vs {c['shadowed']}"})
    return index, findings


def main() -> int:
    ap = argparse.ArgumentParser(prog="skill_index.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover"); d.add_argument("--json", action="store_true")
    r = sub.add_parser("resolve"); r.add_argument("--task", required=True); r.add_argument("--project", default="")
    r.add_argument("-k", type=int, default=3); r.add_argument("--rebuild", action="store_true")
    x = sub.add_parser("explain"); x.add_argument("--task", required=True); x.add_argument("--project", default="")
    x.add_argument("-k", type=int, default=3)
    q = sub.add_parser("doctor"); q.add_argument("--json", action="store_true")

    args = ap.parse_args()
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
