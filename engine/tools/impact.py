#!/usr/bin/env python3
"""Impact engine: what a change touches, with a confidence per claim.

This is deliberately not a "full dependency graph". It is the smallest honest
answer to the question a decision record must answer before a write:

  which files import this, which tests exercise it, which runtime components
  own it, which contracts leave the process, and what is still unknown?

Every edge carries a confidence and an evidence string. Anything the tool
cannot parse is listed under `unknowns`; it is never silently omitted. A graph
provider that is not installed (graphify) is reported MISSING, not simulated.

Usage:
  impact.py --project bighero --files python_ai/risk.py [--json]
  impact.py --project bighero --task "panel positions snapshot" [--limit 20]
  impact.py --project bighero --files a.py --topology registry/topology/bighero.json
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

REGISTRY = ep.REGISTRY
PROFILES = os.path.join(REGISTRY, "PROJECT_PROFILES.json")
TOPOLOGY_DIR = os.path.join(REGISTRY, "topology")

SKIP_DIRS = {
    ".git", ".venv", ".venv-win", ".venv-qc", "venv", "node_modules", "__pycache__",
    "site-packages", "dist", "build", "target", "graphify-out", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "backups", ".freebuff", ".sixth",
}
SKIP_PREFIXES = ("_backup", "_eos-backup", "_isolated/", "_quarantine/", "_capture_release/",
                 "reports/", "releases/", "artifacts/", "research/archive/",
                 "python_ai/research/archive/", "python_ai/reports/", "python_ai/releases/",
                 "python_ai/_capture_release/", "python_ai/_quarantine/",
                 ".claude", ".agents", ".opencode", ".github",
                 "installer/", "runtime/python/", "cli/target/", "host-plugins/",
                 "engine/runtime/", "engine/installer/")
GENERIC_SYMBOLS = {
    "main", "run", "test", "tests", "build", "render", "load", "save", "parse", "read",
    "write", "start", "stop", "setup", "close", "open", "update", "create", "delete",
    "config", "settings", "logger", "log", "now", "utc_now", "sha256_file", "sha256_bytes",
    "get", "set", "init", "reset", "check", "status", "report",
}
PY_EXT = (".py",)
JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte")
MQL_EXT = (".mq4", ".mq5", ".mqh")
MAX_FILE_BYTES = 2_000_000
MAX_FILES = 30000
STOPWORDS = {
    "panel", "panelde", "işlemler", "islemler", "görünmüyor", "gorunmuyor", "düzelt", "duzelt",
    "the", "and", "for", "with", "this", "that", "from", "into", "when", "what", "why",
    "bir", "ile", "için", "icin", "olan", "daha", "çok", "cok", "nasıl", "nasil", "hangi",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def profiles_path(workspace: str) -> str:
    """A workspace registry wins, exactly like project_map. Otherwise impact
    would refuse a project that the map already knows (found by the P5 math
    and cross-process fixtures)."""
    local = os.path.join(workspace, ".agents", "registry", "PROJECT_PROFILES.json")
    return local if os.path.isfile(local) else PROFILES


def project_root(project: str, workspace: str) -> tuple:
    path = profiles_path(workspace)
    if not os.path.isfile(path):
        raise SystemExit(f"BLOCKED: project registry missing: {path}")
    data = read_json(path)
    for item in data.get("profiles", []):
        if item.get("id") == project:
            rel = item.get("path") or ""
            return os.path.normpath(os.path.join(workspace, rel)), item
    # A project discovered from a marker (`auto-<folder>`) is not registered.
    # project_map already resolves it to the workspace root; impact must agree,
    # or a new folder is writable by the map and blocked by the decision engine.
    if project.startswith("auto-") and os.path.isdir(workspace):
        root = ep.auto_project_root(project, workspace)
        return root, {"id": project, "path": os.path.relpath(root, workspace).replace("\\", "/"),
                      "discovered": True}
    marker = os.path.join(workspace, ".eos", "project.json")
    if os.path.isfile(marker):
        try:
            identity = read_json(marker)
        except (OSError, json.JSONDecodeError):
            identity = {}
        if str(identity.get("id") or "") == project:
            return os.path.abspath(workspace), {"id": project, "path": ".", "identity": True}
    raise SystemExit(f"BLOCKED: unknown project '{project}' (not in PROJECT_PROFILES.json)")


def skipped(name: str) -> bool:
    return name in SKIP_DIRS or any(name.startswith(prefix) for prefix in SKIP_PREFIXES)


def walk_files(root: str) -> list:
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not skipped(d))
        rel_base = os.path.relpath(base, root).replace("\\", "/")
        if rel_base != "." and any((rel_base + "/").startswith(prefix) for prefix in SKIP_PREFIXES):
            dirs[:] = []
            continue
        for name in files:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            found.append(rel)
            if len(found) >= MAX_FILES:
                return found
    return found


_TEXT_CACHE = {}


def read_text(path: str) -> str:
    # The cache key includes mtime and size: an incremental update in the same
    # process must see the bytes the write just produced, not the previous
    # content (found in the 0.3.4 post-write test).
    try:
        stat = os.stat(path)
    except OSError:
        return ""
    key = (path, stat.st_mtime_ns, stat.st_size)
    if key in _TEXT_CACHE:
        return _TEXT_CACHE[key]
    try:
        if stat.st_size > MAX_FILE_BYTES:
            _TEXT_CACHE[key] = ""
            return ""
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            value = handle.read()
    except OSError:
        value = ""
    if len(_TEXT_CACHE) > 4096:
        _TEXT_CACHE.clear()
    _TEXT_CACHE[key] = value
    return value


def module_name(rel: str) -> str:
    text = rel[:-3] if rel.endswith(".py") else rel
    parts = [part for part in text.split("/") if part]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def python_index(root: str) -> dict:
    files = {}
    for rel in walk_files(root):
        if not rel.endswith(PY_EXT):
            continue
        text = read_text(os.path.join(root, rel))
        if not text:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            files[rel] = {"module": module_name(rel), "parse": "FAILED", "imports": [],
                          "defs": [], "dynamic": False}
            continue
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({"module": alias.name, "names": [], "line": node.lineno})
            elif isinstance(node, ast.ImportFrom):
                imports.append({"module": node.module or "", "names": [a.name for a in node.names],
                                "line": node.lineno, "level": node.level})
        defs = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs.append({"name": node.name, "kind": type(node).__name__, "line": node.lineno})
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        defs.append({"name": target.id, "kind": "constant", "line": node.lineno})
        dynamic = any(token in text for token in ("importlib", "__import__(", "getattr("))
        files[rel] = {"module": module_name(rel), "parse": "OK", "imports": imports,
                      "defs": defs, "dynamic": dynamic}
    return files


def js_imports(text: str) -> list:
    pattern = re.compile(r"""(?:from\s+|require\s*\(\s*)["']([^"']+)["']""")
    return sorted(set(match.group(1) for match in pattern.finditer(text)))


def resolve_js(from_rel: str, spec: str) -> str:
    if not spec.startswith("."):
        return ""
    base = os.path.dirname(from_rel)
    target = os.path.normpath(os.path.join(base, spec)).replace("\\", "/")
    for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.js"):
        if target + ext:
            return target + ext
    return target


def testish(rel: str) -> bool:
    name = os.path.basename(rel)
    return ("/tests/" in "/" + rel or name.startswith("test_") or name.endswith("_test.py")
            or name.endswith(".test.ts") or name.endswith(".test.tsx"))


def task_files(root: str, task: str, limit: int) -> tuple:
    words = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", task.lower())
             if w not in STOPWORDS]
    if not words:
        return [], []
    hits = {}
    files = walk_files(root)
    for rel in files:
        if not rel.endswith(PY_EXT + JS_EXT + MQL_EXT):
            continue
        text = read_text(os.path.join(root, rel)).lower()
        if not text:
            continue
        score = sum(text.count(word) for word in words)
        if score:
            hits[rel] = score
    ordered = sorted(hits.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [item[0] for item in ordered], words


def load_topology(project: str, explicit: str) -> tuple:
    path = explicit or os.path.join(TOPOLOGY_DIR, project + ".json")
    if not os.path.isfile(path):
        return None, path
    try:
        return read_json(path), path
    except (OSError, json.JSONDecodeError):
        return None, path


def component_for(topology: dict, rel: str) -> list:
    import fnmatch
    matched = []
    for component in (topology or {}).get("components", []):
        for pattern in component.get("paths", []):
            if "*" in pattern or "?" in pattern:
                if fnmatch.fnmatch(rel, pattern):
                    matched.append(component)
                    break
            elif rel == pattern or rel.startswith(pattern.rstrip("/") + "/"):
                matched.append(component)
                break
    return matched


def build(project: str, workspace: str, files: list, task: str, topology_path: str,
          symbol_limit: int) -> dict:
    root, profile = project_root(project, workspace)
    if not os.path.isdir(root) and not project.startswith("auto-"):
        raise SystemExit(f"BLOCKED: project path does not exist: {root}")

    notes = []
    if not files and task:
        files, words = task_files(root, task, 20)
        notes.append(f"files were inferred from task keywords: {', '.join(words[:10])} "
                     f"(confidence LOW; verify before approving)")

    normalised = []
    for item in files:
        rel = os.path.normpath(item.replace("\\", "/")).replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if os.path.isabs(rel):
            rel = os.path.relpath(rel, root).replace("\\", "/")
        normalised.append(rel)
    normalised = sorted(set(normalised))
    if not normalised:
        return {"schema": 1, "project": project, "verdict": "BLOCKED",
                "generated_at": utc_now(), "unknowns": ["no changed file given and task matched none"],
                "notes": notes, "files": [], "edges": [], "tests": [], "contracts": [],
                "components": [], "coverage": {"ast": "NONE", "lexical": "NONE", "topology": "MISSING"}}

    py = python_index(root)
    topology, topology_used = load_topology(project, topology_path)

    owners = {}
    for other_rel, other in py.items():
        for definition in other.get("defs", []):
            owners.setdefault(definition["name"], set()).add(other_rel)

    changed = []
    edges = []
    tests = []
    contracts = []
    components = []
    unknowns = []

    for rel in normalised:
        info = py.get(rel)
        entry = {"path": rel, "language": "python" if rel.endswith(PY_EXT) else
                 "js" if rel.endswith(JS_EXT) else "mql" if rel.endswith(MQL_EXT) else "other",
                 "exists": os.path.isfile(os.path.join(root, rel)),
                 "parse": (info or {}).get("parse", "NONE"),
                 "module": (info or {}).get("module", ""),
                 "public_symbols": [d["name"] for d in (info or {}).get("defs", [])
                                    if not d["name"].startswith("_")][:symbol_limit],
                 "dynamic_imports": bool((info or {}).get("dynamic"))}
        if not entry["exists"]:
            unknowns.append(f"changed file does not exist yet (new file): {rel}")
        if entry["language"] == "other":
            unknowns.append(f"language not parsed: {rel}")
        if entry["language"] == "mql":
            unknowns.append(f"MQL has no parser in this workspace; impact is lexical only: {rel}")
        if entry["dynamic_imports"]:
            unknowns.append(f"dynamic import/getattr present; static edges are incomplete: {rel}")
        changed.append(entry)

        matched = component_for(topology, rel)
        for comp in matched:
            components.append({
                "id": comp.get("id"), "kind": comp.get("kind"),
                "process": comp.get("process"), "entrypoints": comp.get("entrypoints", []),
                "confidence": comp.get("confidence", "UNKNOWN"),
                "evidence": comp.get("evidence", []),
            })

        if info and info.get("parse") == "FAILED":
            unknowns.append(f"Python AST parse failed: {rel}")
            continue

        if info:
            module = info["module"]
            tail = module.rsplit(".", 1)[-1]
            for other_rel, other in py.items():
                if other_rel == rel or other.get("parse") != "OK":
                    continue
                for imp in other.get("imports", []):
                    target = imp.get("module", "")
                    exact = target == module or target.startswith(module + ".")
                    tail_match = (target == tail and tail not in getattr(sys, "stdlib_module_names", set()))
                    from_import = (not target and tail in (imp.get("names") or []))
                    if exact or tail_match or from_import:
                        confidence = "HIGH" if exact else "MEDIUM"
                        edges.append({"from": other_rel, "to": rel, "kind": "import",
                                      "confidence": confidence,
                                      "evidence": f"{other_rel}:{imp.get('line')} imports "
                                                  f"{target or '.'} {','.join(imp.get('names') or [])}".strip()})
                        if testish(other_rel):
                            tests.append({"path": other_rel, "reason": f"imports {module}",
                                          "confidence": confidence, "evidence": f"{other_rel}:{imp.get('line')}"})
                        break
        elif entry["language"] == "js":
            text = read_text(os.path.join(root, rel))
            for other_rel in walk_files(root):
                if other_rel == rel or not other_rel.endswith(JS_EXT):
                    continue
                other_text = read_text(os.path.join(root, other_rel))
                for spec in js_imports(other_text):
                    if resolve_js(other_rel, spec).endswith(rel):
                        edges.append({"from": other_rel, "to": rel, "kind": "import",
                                      "confidence": "MEDIUM",
                                      "evidence": f"relative import {spec}"})
                        if testish(other_rel):
                            tests.append({"path": other_rel, "reason": f"imports {spec}",
                                          "confidence": "MEDIUM", "evidence": other_rel})
                        break

        # Symbol references. Only symbols defined once in the whole project are
        # scanned: a shared helper name (`utc_now`, `SCHEMA`) otherwise produces
        # an edge to every file and turns the report into noise.
        scan_symbols = [
            (definition["name"], definition["kind"]) for definition in (info or {}).get("defs", [])
            if definition["kind"] in ("FunctionDef", "AsyncFunctionDef", "ClassDef")
            and not definition["name"].startswith("_")
            and len(definition["name"]) >= 5
            and definition["name"] not in GENERIC_SYMBOLS
            and len(owners.get(definition["name"], set())) == 1
        ]
        for symbol, symbol_kind in scan_symbols[:12]:
            if symbol_kind == "ClassDef":
                pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
            elif symbol_kind == "constant":
                pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
            else:
                pattern = re.compile(r"\b" + re.escape(symbol) + r"\s*\(")
            refs = []
            for other_rel in py:
                if other_rel == rel:
                    continue
                text = read_text(os.path.join(root, other_rel))
                if text and pattern.search(text):
                    refs.append(other_rel)
            for other_rel in refs[:20]:
                edges.append({"from": other_rel, "to": rel, "kind": "reference",
                              "symbol": symbol, "confidence": "MEDIUM",
                              "evidence": f"lexical reference to {symbol}"})
                if testish(other_rel):
                    tests.append({"path": other_rel, "reason": f"references {symbol}",
                                  "confidence": "MEDIUM", "evidence": other_rel})

        # A public top-level name in an API-ish file is a contract surface.
        api_ish = any(token in rel.lower() for token in
                      ("route", "schema", "contract", "api", "endpoint", "bridge", "protocol"))
        for symbol in entry["public_symbols"]:
            if api_ish:
                contracts.append({"path": rel, "symbol": symbol, "kind": "exported_api",
                                  "confidence": "MEDIUM",
                                  "evidence": "public symbol in an api/route/schema/bridge file"})

    # Dedupe edges/tests while keeping the strongest confidence.
    strongest = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}
    unique = {}
    for edge in edges:
        key = (edge["from"], edge["to"], edge["kind"], edge.get("symbol", ""))
        if key not in unique or strongest[edge["confidence"]] > strongest[unique[key]["confidence"]]:
            unique[key] = edge
    edges = sorted(unique.values(), key=lambda e: (e["kind"], e["from"], e["to"]))
    test_unique = {}
    for test in tests:
        key = test["path"]
        if key not in test_unique or strongest[test["confidence"]] > strongest[test_unique[key]["confidence"]]:
            test_unique[key] = test
    tests = sorted(test_unique.values(), key=lambda t: t["path"])

    # Cross-process warning: two touched components that declare different
    # processes cannot share memory by construction. The tool cannot see the
    # code's intent, so it raises the question instead of answering it.
    processes = {c.get("process") for c in components if c.get("process")}
    cross_process = len(processes) > 1
    if cross_process:
        unknowns.append("changed files span more than one process; any in-memory object "
                        "shared between them is an invalid assumption")

    coverage = {
        "ast": "FULL" if py else "NONE",
        "lexical": "PARTIAL" if any(e["kind"] == "reference" for e in edges) else "NONE",
        "topology": "DECLARED" if topology else "MISSING",
        "graphify": "MISSING (provider not installed; this report does not use graph evidence)",
    }
    verdict = "READY"
    if unknowns or coverage["topology"] == "MISSING":
        verdict = "PARTIAL"
    if cross_process:
        verdict = "PARTIAL"

    return {
        "schema": 1,
        "project": project,
        "project_path": profile.get("path"),
        "generated_at": utc_now(),
        "workspace": ep.store_path(workspace, workspace),
        "verdict": verdict,
        "inputs": {"files": normalised, "task": task},
        "files": changed,
        "edges": edges,
        "tests": tests,
        "contracts": contracts,
        "components": sorted({(c["id"], c["kind"], c["process"]): c for c in components}.values(),
                             key=lambda c: str(c["id"])),
        "cross_process": cross_process,
        "topology": {"path": ep.store_path(topology_used, workspace) if topology else "",
                     "used": bool(topology)},
        "coverage": coverage,
        "unknowns": unknowns,
        "notes": notes,
    }


def render(report: dict) -> str:
    lines = [f"# Impact: {report['project']}  [{report['verdict']}]",
             f"# generated {report['generated_at']}  files={len(report['files'])} "
             f"edges={len(report['edges'])} tests={len(report['tests'])}"]
    for note in report.get("notes", []):
        lines.append(f"note: {note}")
    lines.append("\nfiles:")
    for item in report["files"]:
        lines.append(f"  {item['path']}  lang={item['language']} parse={item['parse']} "
                     f"symbols={','.join(item['public_symbols'][:6]) or '-'}")
    lines.append("\nedges:")
    for edge in report["edges"][:80]:
        symbol = f" ({edge['symbol']})" if edge.get("symbol") else ""
        lines.append(f"  {edge['from']} -> {edge['to']}  [{edge['kind']}{symbol}] "
                     f"{edge['confidence']}")
    if len(report["edges"]) > 80:
        lines.append(f"  ... {len(report['edges']) - 80} more")
    lines.append("\ntests:")
    for test in report["tests"][:40]:
        lines.append(f"  {test['path']}  [{test['confidence']}] {test['reason']}")
    if not report["tests"]:
        lines.append("  (none found; this is not proof that no test covers the change)")
    lines.append("\ncontracts:")
    for contract in report["contracts"][:40]:
        lines.append(f"  {contract['path']}::{contract['symbol']}  [{contract['kind']}]")
    lines.append("\ncomponents:")
    for component in report["components"]:
        lines.append(f"  {component['id']}  kind={component['kind']} process={component['process']} "
                     f"confidence={component['confidence']}")
    if report["cross_process"]:
        lines.append("  CROSS_PROCESS: changed files span more than one process")
    lines.append("\ncoverage: " + ", ".join(f"{k}={v}" for k, v in report["coverage"].items()))
    lines.append("\nunknowns:")
    for item in report["unknowns"] or ["(none recorded)"]:
        lines.append(f"  - {item}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="impact.py", description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--files", action="append", default=[])
    parser.add_argument("--task", default="")
    parser.add_argument("--topology", default="")
    parser.add_argument("--symbol-limit", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    workspace = ep.resolve_workspace(args.workspace)
    files = []
    for item in args.files:
        files.extend(part.strip() for part in item.split(",") if part.strip())
    report = build(args.project, workspace, files, args.task, args.topology, args.symbol_limit)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
    print(json.dumps(report, indent=2, ensure_ascii=True) if args.json else render(report))
    return 0 if report["verdict"] != "BLOCKED" else 1


if __name__ == "__main__":
    sys.exit(main())
