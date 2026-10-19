#!/usr/bin/env python3
"""route_task.py — deterministic task router (stdlib only, no deps).

Usage:
  python route_task.py --task "BigHero'da entry bug var" [--cwd <dir>] [--root <workspace-root>] [--json]

Output: JSON {task, resolved, domains, kg_required, risk, fable_5_1_signal, skills, cores,
  delegation, verification, actions}
"""
import json
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))          # .agents/router
_DEFAULT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))     # workspace root

KG_ON = [
    "ontoloji", "ontology", "knowledge graph", "graphrag", "graph rag",
    "bilgi grafiği", "bilgi grafı", "entity", "relation extraction",
    "node-edge", "graph kur", "graph cikar", "graph çıkar", "graph kur",
    "memory graph", "semantic graph", "knowledge-graph",
]
KG_OFF_EXPLICIT = ["typo", "bug", "düzelt", "duzelt", "fix", "test", "ui", "layout", "yazım", "satır"]

RISK_HIGH = [
    "trading", "sinyal", "emir", "order", "deploy", "sil", "delete", "migrate",
    "taşı", "tasi", "credential", "şifre", "sifre", "password", "ödeme", "odeme",
    "production", "canlı", "canli", "live", "geri dönüşü olmayan", "irreversible",
    "toplu", "bulk", "sistem kaydı", "registry",
]
RISK_LOW = ["typo", "tek satır", "one-line", "format", "rename", "yazım", "yazim", "görsel düzelt"]

# Stage 7 cutover: a defensible signal for when fable-5-1 should become the methodology
# owner for a task — NOT a duplicate of fable-5-1's own internal execution-depth classifier
# (core/task-complexity-classifier.md decides ATOMIC/DIRECT/PLANNED/GRAPH/... once loaded;
# this only decides whether the router should route to fable-5-1 at all). Reuses kg_required
# and risk_level (already computed) as the two strongest signals, plus a small, tightly-scoped
# keyword list for the remaining "methodology depth" cases neither of those already covers.
FABLE_METHODOLOGY_ON = [
    "çoklu dosya", "coklu dosya", "multi-file", "multiple files",
    "refactor", "regresyon", "regression",
    "bağımlılık", "bagimlilik", "dependency", "dependencies",
    "plan ve uygula", "plan->execute", "adım adım doğrula", "adim adim dogrula",
    "kanıt", "kanit", "evidence", "provenance",
    "çoklu ajan", "coklu ajan", "multi-agent", "paralel worker", "parallel worker",
    "merge owner", "tek yazar", "writer ownership",
    "uzun soluklu", "long-horizon",
]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def norm(p):
    return os.path.normcase(os.path.normpath(p))


def resolve_project(task, cwd, root):
    profiles = load_json(os.path.join(root, ".agents", "registry", "PROJECT_PROFILES.json"))["profiles"]
    task_l = task.lower()
    for prof in profiles:
        for alias in prof.get("aliases", []):
            if alias.lower() in task_l:
                return prof, "alias"
    if cwd:
        cwd_n = norm(os.path.abspath(cwd))
        for prof in profiles:
            p = os.path.abspath(os.path.join(root, prof["path"]))
            if norm(p) == cwd_n or cwd_n.startswith(norm(p) + os.sep):
                return prof, "cwd"
    return None, "none"


def kg_required(task):
    t = task.lower()
    on = [w for w in KG_ON if w in t]
    if on:
        off = [w for w in KG_OFF_EXPLICIT if w in t]
        # kullanici grafik/ontoloji istegi acik -> KG acik; typo gibi basit islerle birlikte gelse de
        # "graph/ontoloji cikar" istegi once gelir
        if any(w in t for w in ["graph", "graf", "ontoloji", "entity"]) and off:
            return True, f"KG_ON keywords: {on} (explicit graph intent)"
        return True, f"KG_ON keywords: {on}"
    return False, "no KG trigger keywords"


def risk_level(task):
    t = task.lower()
    if any(w in t for w in RISK_HIGH):
        return "high", [w for w in RISK_HIGH if w in t]
    if any(w in t for w in RISK_LOW):
        return "low", [w for w in RISK_LOW if w in t]
    return "medium", []


# Structured risk.policy field (fable-5-1 Stage 5, policy/risk-contract.md). Additive only —
# reuses RISK_HIGH/RISK_LOW above as the sole classification vocabulary, no second keyword
# set. No "deny" tier: this control plane has no mechanism that can express or enforce one —
# see policy/risk-contract.md for why that's documented rather than invented.
_POLICY_GATE = {"high": "approval_required", "medium": "verify_required", "low": "auto"}
_POLICY_ACTION = {
    "approval_required": "İnsan onayı almadan irreversible/riskli adıma geçme (AGENTS.md §10).",
    "verify_required": "Bağımsız verifier/evidence olmadan PASS ilan etme (GRAPH_CORE §1.4/§1.7).",
    "auto": "Standart doğrulama akışı yeterli.",
}


def policy_for_risk(level):
    gate = _POLICY_GATE.get(level, "verify_required")
    return {
        "gate": gate,
        "action": _POLICY_ACTION[gate],
        "classification_strength": "keyword-match heuristic (RISK_HIGH/RISK_LOW) — not semantic risk analysis",
        "enforcement_strength": "checked-convention-only — no mechanical block in this workspace",
        "known_bypass_paths": [
            "model can ignore the label and act anyway",
            "no CI/process boundary intercepts tool calls here",
            "risk discovered mid-task is not retroactively reflected",
        ],
    }


def fable_5_1_signal(task, kg, risk):
    """Should fable-5-1 become the methodology owner for this task? Binary signal only —
    RISK_LOW-tagged trivial tasks (typo/one-line/format) never trigger it even if a weak
    methodology keyword also happens to match, since those tasks are ATOMIC by definition."""
    if risk == "low":
        return False, "risk=low (trivial, no methodology owner needed)"
    if kg:
        return True, "kg_required"
    if risk == "high":
        return True, "risk=high"
    t = task.lower()
    hit = [w for w in FABLE_METHODOLOGY_ON if w in t]
    if hit:
        return True, f"methodology keywords: {hit}"
    return False, "no signal"


def load_routing_map(root):
    p = os.path.join(root, ".agents", "registry", "ROUTING_MAP.json")
    if not os.path.exists(p):
        return None
    try:
        return load_json(p)
    except Exception:
        return None


def catalog_skills(root):
    cat = os.path.join(root, ".agents", "registry", "SKILL_CATALOG.json")
    if not os.path.exists(cat):
        return []
    data = load_json(cat)
    return data.get("entries", []) if isinstance(data, dict) else []


def catalog_match(catalog, domains):
    """Catalog domains -> skill adlari (minimum-yeterli aktivasyon icin aday havuzu)."""
    if not domains:
        return []
    dom = set(d.lower() for d in domains)
    hits = []
    for e in catalog:
        e_doms = set(d.lower() for d in e.get("domains", []))
        if e_doms & dom:
            hits.append(e["name"])
    return sorted(set(hits))


def apply_routing_map(task, skills, routing_map, available, installed):
    """Cakisan tetikleme kumelerinde TEK birincil sec.

    Tetik: gorev metninde cluster trigger'i VEYA kumenin 2+ uyesinin ayni anda
    aday havuzunda bulunmasi (belirsizlik durumu). Kazanan: primary'de KURULU olan
    ilk skill (installed); aday havuzunda yoksa enjekte edilir, havuzda varsa
    kaybedenler suppressed'a yazilir.
    Donen: (primary_listesi, suppressed_listesi, tetiklenen_kume_idleri)
    """
    if not routing_map:
        return [], [], []
    t = task.lower()
    primary_out = []
    suppressed = set()
    triggered_ids = []
    for cl in routing_map.get("clusters", []):
        candidates = [m for m in list(cl.get("primary", [])) + list(cl.get("fallback", []))]
        members = [m for m in candidates if m in installed]
        if not members:
            continue
        kw_hit = any(str(w).lower() in t for w in cl.get("triggers", []))
        ambiguity_hit = len([m for m in members if m in available]) >= 2
        if not (kw_hit or ambiguity_hit):
            continue
        triggered_ids.append(cl["id"])
        t_l2 = task.lower()
        explicit = [m for m in members if len(m) >= 4 and re.search(rf"\b{re.escape(m)}\b", t_l2)]
        pick = sorted(explicit)[0] if explicit else members[0]  # explicit > primary->fallback sirasi
        if pick not in primary_out:
            primary_out.append(pick)
        for m in members:
            if m != pick and m in available:
                suppressed.add(m)
    return primary_out, sorted(suppressed), triggered_ids


def _eos_skill_ranking(task, project_id, limit=3):
    """Rank the installed skill library for this task (EOS decision layer).

    OpenCode remains the loader; this only records which skills are competent
    for the task and why, so the choice is auditable.
    """
    try:
        tools_dir = os.path.join(_DEFAULT_ROOT, ".agents", "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import skill_index
        res = skill_index.resolve(task, project_id or "", limit)
        return [{"name": c["name"], "score": c["score"], "trust": c["trust"],
                 "reasons": c["reasons"]} for c in res["chosen"]]
    except Exception as exc:  # ranking must never break routing
        return [{"name": None, "score": 0, "trust": "UNAVAILABLE", "reasons": [f"skill_index unavailable: {exc}"]}]


def _select_agents(task, domains, risk, kg):
    """Smallest competent specialist set for this task.

    Architect and verifier are always on: a change without design or independent
    verification is not a delivery. Everything else is evidence-triggered.
    """
    t = task.lower()
    specialists, why = [], {}

    def add(name, reason):
        if name not in specialists:
            specialists.append(name)
            why[name] = reason

    add("architect", "design and blast-radius step")
    if re.search(r"ara[t\u015ft]?[i\u0131]r|arastir|ke[s\u015f]fet|kesfet|bul|nerede|hangi dosya|search|explore", t):
        add("explore", "research or codebase discovery requested")
    read_only = bool(re.search(r"analiz|denetle|incele|review|audit|rapor|report|sadece oku", t)) and \
        not re.search(r"ekle|duzelt|fix|implement|yaz|refactor|de[i\u011f]i[t\u015f]tir", t)
    if not read_only:
        add("implementer", "task requires a code or file change")
    if risk in ("medium", "high") or not read_only:
        add("reviewer", "independent adversarial review of the change")
    add("verifier", "PASS requires a mechanical gate artifact")
    if risk == "high" or re.search(r"g[u\u00fc]venlik|security|secret|credential|permission|auth|token|s[i\u0131]z", t):
        add("security-auditor", "security-sensitive surface or high risk")
    if re.search(r"\btest\b|tdd|coverage|regresyon|regression", t):
        add("test-engineer", "test authoring or regression proof requested")
    if re.search(r"performans|performance|benchmark|profil|latency|h[i\u0131]z|bellek", t):
        add("performance-engineer", "performance work requested")
    if kg or re.search(r"kan[i\u0131]t|provenance|evidence|veri|data|kaynak|do[g\u011f]rula|verify data", t):
        add("evidence-analyst", "claim or data provenance must be demonstrated")

    skipped = [a for a in ("implementer", "reviewer", "security-auditor", "test-engineer",
                           "performance-engineer", "evidence-analyst", "explore")
               if a not in specialists]
    return {"orchestrator": "chief-architect", "specialists": specialists,
            "rationale": why, "not_needed": skipped,
            "conditional": {"test-engineer": "test-engineer" in specialists,
                            "performance-engineer": "performance-engineer" in specialists}}


def main():
    args = sys.argv[1:]
    task = None
    cwd = None
    root = _DEFAULT_ROOT
    for i, a in enumerate(args):
        if a in ("--task", "--cwd", "--root"):
            if i + 1 < len(args):
                if a == "--task":
                    task = args[i + 1]
                elif a == "--cwd":
                    cwd = args[i + 1]
                elif a == "--root":
                    root = args[i + 1]
    if not task:
        print(json.dumps({"error": "task required"}, ensure_ascii=False))
        sys.exit(2)
    prof, src = resolve_project(task, cwd, root)
    kg, kg_reason = kg_required(task)
    risk, risk_kw = risk_level(task)

    domains = []
    skills = {"preferred": [], "catalog": [], "excluded": []}
    if prof:
        domains = prof.get("domains", [])
        skills["preferred"] = prof.get("preferred_skills", [])
        skills["catalog"] = catalog_match(catalog_skills(root), domains)
        skills["preferred"] = sorted(set(skills["preferred"]))

    catalog_entries = catalog_skills(root)
    catalog_names = {e.get("name", "") for e in catalog_entries}
    fable_active, fable_reason = fable_5_1_signal(task, kg, risk)
    if fable_active and "fable-5-1" in catalog_names:
        # Stage 7 canonical path: fable-5-1 owns methodology-depth work, including
        # KG-required work (it internalizes Knowledge/Evidence Graph natively) — this
        # replaces the pre-Stage-7 force-add of graph-engineering as a second authority.
        skills["preferred"] = sorted(set(skills["preferred"]) | {"fable-5-1"})
    elif kg:
        # Defensive fallback only: if this checkout doesn't have fable-5-1 in the catalog
        # (e.g. an older mirror), preserve the pre-Stage-7 behavior rather than silently
        # dropping KG-required routing.
        skills["preferred"] = sorted(set(skills["preferred"]) | {"graph-engineering"})

    routing_map = load_routing_map(root)
    available = set(skills["preferred"]) | set(skills["catalog"])
    installed = available | {e.get("name", "") for e in catalog_entries}
    map_primary, map_suppressed, triggered = apply_routing_map(task, skills, routing_map, available, installed)
    primary = list(map_primary)
    t_l = task.lower()
    for n in sorted(installed):
        if len(n) >= 4 and re.search(rf"\b{re.escape(n)}\b", t_l) and n not in primary:
            primary.append(n)
    for p in skills["preferred"]:
        if p not in set(map_suppressed) and p not in primary:
            primary.append(p)
    skills["primary"] = primary[:8]
    skills["suppressed"] = map_suppressed
    skills["ranked"] = _eos_skill_ranking(task, prof["id"] if prof else None)
    agents = _select_agents(task, domains, risk, kg)

    delegation = []
    if risk == "high":
        delegation = ["reviewer", "verifier"]
    elif risk == "medium":
        delegation = ["verifier"]
    # arastirma/kesif kelimeleri -> explorer
    if re.search(r"araştır|arastir|keşfet|kesfet|bul|nerede|hangi dosya|search", task, re.I):
        delegation = ["explorer"] + delegation

    risk_policy = policy_for_risk(risk)

    actions = ["load FABLE_CORE + GRAPH_CORE (core adapterlar)", "AGENTS.md kontratina uy"]
    if prof:
        actions.append("project: " + prof["id"] + " -> " + prof["path"])
        actions.extend(["invariant check: " + i for i in prof.get("invariants", [])])
        actions.extend(["verify: " + v for v in prof.get("verify", [])])
    else:
        actions.append("project tespit edilemedi -> new_project.py route")
    if risk_policy["gate"] != "auto":
        actions.append(risk_policy["action"])

    out = {
        "task": task,
        "resolved": {
            "project_id": prof["id"] if prof else None,
            "path": prof["path"] if prof else None,
            "legacy_path": prof.get("legacy_path") if prof else None,
            "source": src,
        },
        "domains": domains,
        "kg_required": kg,
        "kg_reason": kg_reason,
        "risk": {"level": risk, "keywords": risk_kw, "policy": risk_policy},
        "fable_5_1_signal": {"active": fable_active, "reason": fable_reason, "present_in_catalog": "fable-5-1" in catalog_names},
        "skills": skills,
        "routing": {
            "map_version": routing_map.get("version") if routing_map else None,
            "triggered_clusters": triggered,
        },
        "cores": ["FABLE_CORE", "GRAPH_CORE"],
        "agents": agents,
        "delegation": delegation,
        "verification": prof.get("verify", []) if prof else [],
        "actions": actions,
    }
    # Windows cp1252 stdout crash fix (2026-09-12): ensure_ascii=True keeps
    # output encodable on non-UTF8 consoles; content unchanged semantically.
    print(json.dumps(out, ensure_ascii=True, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
