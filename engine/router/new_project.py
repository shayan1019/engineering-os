#!/usr/bin/env python3
"""new_project.py — new project bootstrap route (interactive; --write registers profile).

Flow: goal -> overlap check -> kind -> path -> skill proposal -> agent roles ->
test strategy -> profile preview -> (--write) register -> verify.
"""
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))

KIND_MAP = {
    "ai-app": {"skills": ["ai-engineering", "frontend-ui-engineering", "code-quality"],
               "tests": "evals + unit", "domains": ["ai-engineering"]},
    "trading": {"skills": ["backtesting", "risk-management", "mql-developer", "mt5-trading"],
                "tests": "backtest + forward-test", "domains": ["trading"]},
    "webapp": {"skills": ["frontend-ui-engineering", "webapp-testing", "e2e-testing"],
               "tests": "e2e (playwright)", "domains": ["webdev"]},
    "research": {"skills": ["ai-research-reproduction", "ai-research-explore", "ai-engineering"],
                 "tests": "repro/audit", "domains": ["ml", "ai-engineering"]},
    "other": {"skills": [], "tests": "unit (pytest)", "domains": []},
}


def overlap_check(goal, root):
    profiles = json.load(open(os.path.join(root, ".agents", "registry", "PROJECT_PROFILES.json"), encoding="utf-8"))["profiles"]
    g = goal.lower()
    hits = []
    for p in profiles:
        aliases = [a.lower() for a in p.get("aliases", [])]
        doms = [d.lower() for d in p.get("domains", [])]
        score = sum(1 for a in aliases if a in g) + sum(1 for d in doms if d in g)
        if score:
            hits.append({"id": p["id"], "path": p["path"], "score": score})
    hits.sort(key=lambda h: -h["score"])
    return hits


def main():
    args = sys.argv[1:]
    write = "--write" in args
    root = _ROOT
    for a in args:
        if a.startswith("--root="):
            root = a.split("=", 1)[1]

    goal = input("Goal (bir cumle): ").strip()
    if not goal:
        print(json.dumps({"error": "goal required"}, ensure_ascii=False))
        sys.exit(2)

    hits = overlap_check(goal, root)
    print("\nOverlap check (mevcut profiller):", json.dumps(hits, ensure_ascii=False))
    if hits and hits[0]["score"] >= 2:
        print(f"UYARI: '{hits[0]['id']}' ile ciddi ortusme var. Devam mi? (y/n)")
        if input().strip().lower() != "y":
            sys.exit(0)

    name = input("Project id (kebab-case): ").strip()
    kind = input("Kind [ai-app/trading/webapp/research/other]: ").strip() or "other"
    path = input(f"Path (workspace relative, ornek ./{name}): ").strip() or f"./{name}"

    plan = {
        "goal": goal,
        "name": name,
        "kind": kind,
        "path": path,
        "domains": KIND_MAP.get(kind, KIND_MAP["other"])["domains"],
        "skills": KIND_MAP.get(kind, KIND_MAP["other"])["skills"],
        "test_strategy": KIND_MAP.get(kind, KIND_MAP["other"])["tests"],
        "agent_roles": ["explorer", "implementer", "verifier"],
        "profile_entry": {
            "id": name, "display": name, "path": path, "location": "workspace",
            "aliases": [name], "domains": KIND_MAP.get(kind, KIND_MAP["other"])["domains"],
            "preferred_skills": KIND_MAP.get(kind, KIND_MAP["other"])["skills"],
            "conditional_skills": [], "invariants": [], "verify": [],
        },
    }
    print("\nPLAN:", json.dumps(plan, ensure_ascii=False, indent=2))

    if write:
        reg = os.path.join(root, ".agents", "registry", "PROJECT_PROFILES.json")
        data = json.load(open(reg, encoding="utf-8"))
        if any(p["id"] == name for p in data["profiles"]):
            print(f"ERROR: profile '{name}' zaten var")
            sys.exit(1)
        data["profiles"].append(plan["profile_entry"])
        with open(reg, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"profile registered: {name} -> {path}")
        os.system(f'python "{os.path.join(root, ".agents", "router", "validate_workspace.py")}"')
    else:
        print("(kayit icin --write bayragi ile tekrar calistir)")


if __name__ == "__main__":
    main()
