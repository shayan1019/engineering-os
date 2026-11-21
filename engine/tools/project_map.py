#!/usr/bin/env python3
"""Persistent project map.

This is a persisted snapshot of the same evidence `impact.py` computes, not a
second source of truth. It adds what a single task cannot carry:

  * module, import, test, contract and component indexes
  * source hashes plus Git HEAD and dirty state
  * coverage, confidence and UNKNOWN
  * an incremental update that keeps unchanged entries

A map is `CURRENT`, `STALE`, `MISSING` or `INVALID`. `eos_activate` must not
claim the project is known when the map is not CURRENT. A missing map is
created from real files; a corrupt map is rejected, never trusted.

Commands:
  create  --project <id> [--force]
  verify  --project <id> [--json]
  update  --project <id>
  excerpt --project <id> [--max-lines N]
  status  --project <id> [--json]
  selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402
import impact as impact_tool  # noqa: E402

SCHEMA = 1
KIND = "project_map"
MAX_FILES = 5000
SRC_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mq4", ".mq5", ".mqh",
           ".go", ".rs", ".java", ".cs", ".c", ".cpp", ".h", ".dart", ".ino")
SKIP = {".git", ".venv", ".venv-win", ".venv-qc", "venv", "node_modules", "__pycache__",
        "site-packages", "dist", "build", "target", "graphify-out", ".pytest_cache",
        "backups", ".agents", ".eos", ".opencode", ".claude", ".codex"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def map_hash(value: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in value.items() if k != "map_sha256"})).hexdigest().upper()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_json(path: str):
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    os.replace(tmp, path)


def registry_dir(workspace: str) -> str:
    local = os.path.join(workspace, ".agents", "registry")
    return local if os.path.isdir(local) else ep.REGISTRY


def resolve_root(project: str, workspace: str, explicit_path: str = "") -> tuple:
    """Find a project root for registered and discovered projects alike."""
    if explicit_path:
        root = os.path.normpath(os.path.join(workspace, explicit_path))
        if os.path.isdir(root):
            return root, {"id": project, "path": explicit_path}
        raise SystemExit(f"BLOCKED: project path does not exist: {root}")
    path = os.path.join(registry_dir(workspace), "PROJECT_PROFILES.json")
    if os.path.isfile(path):
        data = read_json(path)
        for profile in data.get("profiles", []):
            if profile.get("id") == project:
                return os.path.normpath(os.path.join(workspace, profile.get("path", ""))), profile
    for candidate in (os.path.join(workspace, ".agents", "registry", "PROJECTS.local.json"),
                      os.path.join(workspace, ".eos", "projects.json")):
        if not os.path.isfile(candidate):
            continue
        try:
            data = read_json(candidate)
        except (OSError, json.JSONDecodeError):
            continue
        projects = data.get("projects") if isinstance(data, dict) else data
        for item in projects or []:
            if item.get("id") == project:
                root = os.path.normpath(os.path.join(workspace, item.get("path", "")))
                if os.path.isdir(root):
                    return root, item
    raise SystemExit(f"BLOCKED: unknown project '{project}' (not registered or discovered)")


def project_root(project: str, workspace: str, explicit_path: str = "") -> tuple:
    return resolve_root(project, workspace, explicit_path)


def store_path(workspace: str, project: str) -> str:
    return os.path.join(workspace, ".agents", "evidence", "map", project, "PROJECT_MAP.json")


def git_state(root: str) -> dict:
    import subprocess
    def run(args):
        try:
            return subprocess.run(["git", "-C", root, *args], capture_output=True,
                                  text=True, timeout=15).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    head = run(["rev-parse", "HEAD"])
    branch = run(["rev-parse", "--abbrev-ref", "HEAD"])
    dirty = bool(run(["status", "--porcelain=v1"]))
    return {"head": head, "branch": branch, "dirty": dirty}


def source_files(root: str) -> list:
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("_backup"))
        for name in files:
            if name.lower().endswith(SRC_EXT):
                found.append(os.path.relpath(os.path.join(base, name), root).replace("\\", "/"))
                if len(found) >= MAX_FILES:
                    return found
    return sorted(found)


JS_IMPORT_RE = re.compile(r"""(?:from\s+|require\s*\(\s*)["']([^"']+)["']""")
JS_DEF_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let)\s+([A-Za-z_$][\w$]*)", re.M)
MQL_INCLUDE_RE = re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]', re.M)
MQL_DEF_RE = re.compile(r"^\s*(?:void|int|uint|long|ulong|double|float|bool|string|datetime)\s+"
                        r"([A-Za-z_]\w*)\s*\(", re.M)


def js_index(root: str, files: list) -> dict:
    index = {}
    for rel in files:
        if not rel.lower().endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte")):
            continue
        text = impact_tool.read_text(os.path.join(root, rel))
        imports = [{"module": spec, "names": []} for spec in sorted(set(JS_IMPORT_RE.findall(text)))]
        defs = [{"name": name, "kind": "symbol"} for name in sorted(set(JS_DEF_RE.findall(text)))]
        index[rel] = {"module": rel, "parse": "OK" if text else "EMPTY",
                      "imports": imports, "defs": defs,
                      "dynamic": "import(" in text or "require(" in text}
    return index


def mql_index(root: str, files: list) -> dict:
    index = {}
    for rel in files:
        if not rel.lower().endswith((".mq4", ".mq5", ".mqh")):
            continue
        text = impact_tool.read_text(os.path.join(root, rel))
        includes = [{"module": item, "names": []} for item in sorted(set(MQL_INCLUDE_RE.findall(text)))]
        defs = [{"name": name, "kind": "function"} for name in sorted(set(MQL_DEF_RE.findall(text)))]
        index[rel] = {"module": rel, "parse": "PARTIAL", "imports": includes, "defs": defs,
                      "dynamic": False}
    return index


def resolve_js(root: str, from_rel: str, spec: str) -> str:
    if not spec.startswith("."):
        return ""
    base = os.path.dirname(from_rel)
    target = os.path.normpath(os.path.join(base, spec)).replace("\\", "/")
    for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.js"):
        candidate = target + ext
        if os.path.isfile(os.path.join(root, candidate)):
            return candidate
    return ""


def build(project: str, workspace: str, previous: dict | None = None,
          project_path: str = "") -> dict:
    root, profile = project_root(project, workspace, project_path)
    if not os.path.isdir(root):
        raise SystemExit(f"BLOCKED: project path does not exist: {root}")
    files = source_files(root)
    hashes = {}
    for rel in files:
        try:
            hashes[rel] = {"sha256": sha256_file(os.path.join(root, rel)),
                           "bytes": os.path.getsize(os.path.join(root, rel))}
        except OSError:
            hashes[rel] = {"sha256": "", "bytes": 0}

    py = impact_tool.python_index(root)
    js = js_index(root, files)
    mql = mql_index(root, files)
    index = {**py, **js, **mql}
    modules = []
    module_to_file = {}
    for rel, info in index.items():
        module_to_file[info.get("module", rel)] = rel
    edges = []
    unknowns = []
    tests = []
    contracts = []
    for rel, info in sorted(index.items()):
        module = info.get("module", "")
        parser = ("python-ast" if rel in py else
                  "js-regex-v1" if rel in js else
                  "mql-lexical-v1" if rel in mql else "none")
        modules.append({
            "path": rel, "module": module, "parser": parser, "parse": info.get("parse"),
            "imports": [{"module": item.get("module"), "names": item.get("names") or []}
                        for item in (info.get("imports") or [])],
            "defs": [{"name": item.get("name"), "kind": item.get("kind")}
                     for item in (info.get("defs") or [])],
        })
        if info.get("parse") == "PARTIAL":
            unknowns.append(f"lexical/partial parser only: {rel}")
        elif info.get("parse") not in ("OK", "EMPTY"):
            unknowns.append(f"parse failed: {rel}")
        if info.get("dynamic"):
            unknowns.append(f"dynamic import/getattr: {rel}")
        tail = module.rsplit(".", 1)[-1] if module else ""
        for imp in info.get("imports") or []:
            target = imp.get("module") or ""
            target_file = module_to_file.get(target)
            if not target_file and target.startswith("."):
                target_file = resolve_js(root, rel, target)
            if not target_file and target:
                target_tail = target.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[-1]
                for candidate, candidate_file in module_to_file.items():
                    if candidate.endswith("." + target_tail) or candidate == target_tail:
                        target_file = candidate_file
                        break
            if target_file and target_file != rel:
                edges.append({"from": rel, "to": target_file, "kind": "import",
                              "confidence": "HIGH" if target_file == module_to_file.get(target) else "MEDIUM",
                              "evidence": f"{rel}: imports {target}"})
        if impact_tool.testish(rel):
            tests.append({"path": rel, "reason": "test file"})
        api_ish = any(token in rel.lower() for token in
                      ("route", "schema", "contract", "api", "endpoint", "bridge", "protocol"))
        if api_ish:
            for item in info.get("defs") or []:
                if not str(item.get("name", "")).startswith("_"):
                    contracts.append({"path": rel, "symbol": item.get("name"),
                                      "kind": "exported_api", "confidence": "MEDIUM"})

    topology, topology_used = impact_tool.load_topology(project, "")
    components = []
    if topology:
        for component in topology.get("components", []):
            components.append({"id": component.get("id"), "kind": component.get("kind"),
                               "process": component.get("process"),
                               "confidence": component.get("confidence", "UNKNOWN")})
    else:
        unknowns.append("runtime topology is missing for this project")

    coverage = {
        "ast": "FULL" if py else "NONE",
        "js": "PARTIAL" if js else "NONE",
        "mql": "PARTIAL" if mql else "NONE",
        "lexical": "PARTIAL",
        "topology": "DECLARED" if topology else "MISSING",
        "graphify": "MISSING (provider not installed)",
    }
    supported = set(py) | set(js) | set(mql)
    unsupported = [rel for rel in files if rel not in supported]
    if unsupported:
        unknowns.append(f"{len(unsupported)} source file(s) have no parser adapter")
    if not files:
        coverage_state = "NONE"
    elif not unsupported and not unknowns and topology:
        coverage_state = "FULL"
    else:
        coverage_state = "PARTIAL"
    if len(files) >= MAX_FILES:
        unknowns.append(f"source file list truncated at {MAX_FILES}")

    map_value = {
        "schema": SCHEMA, "kind": KIND, "project": project,
        "project_path": profile.get("path", ""),
        "generated_at": utc_now(),
        "mode": "incremental" if previous else "full",
        "previous_map_sha256": (previous or {}).get("map_sha256", ""),
        "git": git_state(root),
        "sources": {"count": len(files), "files": hashes},
        "modules": modules,
        "edges": edges,
        "tests": tests,
        "contracts": contracts,
        "components": components,
        "parsers": {
            "python-ast": len(py), "js-regex-v1": len(js), "mql-lexical-v1": len(mql),
            "unsupported": len(unsupported),
        },
        "coverage": coverage,
        "coverage_state": coverage_state,
        "unknowns": unknowns,
        "dirty_files": [],
        "dirty_at": "",
        "topology": {"used": bool(topology),
                     "path": ep.store_path(topology_used, workspace) if topology else ""},
    }
    if previous:
        # Keep the previous map identity for a traceable incremental update.
        map_value["created_at"] = previous.get("created_at") or map_value["generated_at"]
    else:
        map_value["created_at"] = map_value["generated_at"]
    map_value["map_sha256"] = map_hash(map_value)
    return map_value


def verify(project: str, workspace: str) -> dict:
    path = store_path(workspace, project)
    if not os.path.isfile(path):
        return {"state": "MISSING", "project": project, "path": path,
                "reasons": ["no project map has been created"]}
    try:
        value = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"state": "INVALID", "project": project, "path": path,
                "reasons": [f"map is unreadable: {exc}"]}
    if value.get("schema") != SCHEMA or value.get("kind") != KIND:
        return {"state": "INVALID", "project": project, "path": path,
                "reasons": ["wrong schema or kind"]}
    if map_hash(value) != value.get("map_sha256"):
        return {"state": "INVALID", "project": project, "path": path,
                "reasons": ["map hash mismatch (edited outside this tool)"]}
    root, _ = project_root(project, workspace, value.get("project_path", ""))
    try:
        current_git = git_state(root)
    except (OSError, SystemExit) as exc:
        return {"state": "INVALID", "project": project, "path": path,
                "reasons": [f"cannot read git state: {exc}"]}
    reasons = []
    recorded_git = value.get("git") or {}
    if recorded_git.get("head") != current_git.get("head"):
        reasons.append("git HEAD changed")
    if bool(recorded_git.get("dirty")) != bool(current_git.get("dirty")):
        reasons.append("worktree dirty state changed")
    if value.get("dirty_files"):
        reasons.append(f"post-write dirty files: {', '.join(value['dirty_files'][:10])}")
    recorded_files = set((value.get("sources") or {}).get("files", {}))
    current_files = set(source_files(root))
    new_files = sorted(current_files - recorded_files)
    missing_files = sorted(recorded_files - current_files)
    if new_files:
        reasons.append(f"new files since the map: {', '.join(new_files[:10])}")
    if missing_files:
        reasons.append(f"deleted/renamed files since the map: {', '.join(missing_files[:10])}")
    changed = [f"new: {rel}" for rel in new_files[:20]] + \
              [f"missing: {rel}" for rel in missing_files[:20]]
    for rel, meta in (value.get("sources") or {}).get("files", {}).items():
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            changed.append(f"missing: {rel}")
            continue
        try:
            if sha256_file(full) != meta.get("sha256"):
                changed.append(f"changed: {rel}")
        except OSError:
            changed.append(f"unreadable: {rel}")
    if changed:
        reasons.extend(changed[:20])
    state = "CURRENT" if not reasons else "STALE"
    return {"state": state, "project": project, "path": path, "reasons": reasons,
            "changed_files": changed[:50], "map": value}


def excerpt(project: str, workspace: str, max_lines: int = 30) -> str:
    result = verify(project, workspace)
    lines = [f"map: {result['state']} ({result.get('path')})"]
    if result["state"] in ("MISSING", "INVALID", "STALE"):
        lines.extend(f"  reason: {reason}" for reason in result.get("reasons", [])[:5])
        return "\n".join(lines[:max_lines])
    value = result["map"]
    lines.append(f"  git: {value['git'].get('branch')} @ {value['git'].get('head', '')[:10]} "
                 f"dirty={value['git'].get('dirty')}")
    lines.append(f"  sources: {value['sources']['count']} files  modules={len(value['modules'])} "
                 f"edges={len(value['edges'])} tests={len(value['tests'])} "
                 f"contracts={len(value['contracts'])}")
    lines.append("  coverage: " + ", ".join(f"{k}={v}" for k, v in value["coverage"].items()))
    components = [f"{c['id']}({c.get('process')})" for c in value["components"][:6]]
    if components:
        lines.append("  components: " + ", ".join(components))
    if value["contracts"]:
        lines.append("  contracts: " + ", ".join(
            f"{c['path']}::{c['symbol']}" for c in value["contracts"][:6]))
    if value["unknowns"]:
        lines.append("  unknowns:")
        lines.extend(f"    - {item}" for item in value["unknowns"][:5])
    return "\n".join(lines[:max_lines])


def mark_dirty(project: str, workspace: str, files: list) -> dict:
    """Mark changed files without a full rebuild. The next preflight/write gate
    sees a non-empty dirty set and refreshes before a risky write."""
    path = store_path(workspace, project)
    if not os.path.isfile(path):
        return {"ok": False, "reason": "map missing"}
    value = read_json(path)
    if map_hash(value) != value.get("map_sha256"):
        return {"ok": False, "reason": "map corrupt"}
    root, _ = project_root(project, workspace, value.get("project_path", ""))
    dirty = set(value.get("dirty_files") or [])
    for raw in files:
        absolute = os.path.realpath(os.path.abspath(os.path.join(workspace, raw)))
        if not (absolute == os.path.realpath(root) or
                absolute.startswith(os.path.realpath(root) + os.sep)):
            continue
        dirty.add(os.path.relpath(absolute, root).replace("\\", "/"))
    value["dirty_files"] = sorted(dirty)
    value["dirty_at"] = utc_now()
    value["map_sha256"] = map_hash(value)
    write_json(path, value)
    epoch = os.path.join(workspace, ".agents", "state", "context.epoch")
    os.makedirs(os.path.dirname(epoch), exist_ok=True)
    with open(epoch, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(utc_now() + " " + str(len(value["dirty_files"])) + "\n")
    return {"ok": True, "dirty_files": value["dirty_files"]}


def ensure(project: str, workspace: str, project_path: str = "") -> dict:
    result = verify(project, workspace)
    if result["state"] == "MISSING":
        value = build(project, workspace, None, project_path)
        write_json(store_path(workspace, project), value)
        result = verify(project, workspace)
    elif result["state"] == "STALE":
        value = build(project, workspace, result.get("map"), project_path)
        write_json(store_path(workspace, project), value)
        result = verify(project, workspace)
    return result


def cmd_ensure(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    result = ensure(args.project, workspace, args.project_path)
    print(f"{result['state']}: {args.project} ({result.get('path')})")
    for reason in result.get("reasons", [])[:10]:
        print(f"  - {reason}")
    return 0 if result["state"] == "CURRENT" else 1


def cmd_touch(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    result = mark_dirty(args.project, workspace, args.file or [])
    if not result.get("ok"):
        print(f"BLOCKED: {result.get('reason')}")
        return 1
    print(f"DIRTY: {args.project} ({len(result['dirty_files'])} file(s))")
    return 0


def cmd_create(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    path = store_path(workspace, args.project)
    if os.path.isfile(path) and not args.force:
        result = verify(args.project, workspace)
        print(f"{result['state']}: map already exists at {path}")
        for reason in result.get("reasons", [])[:5]:
            print(f"  - {reason}")
        return 0 if result["state"] == "CURRENT" else 1
    value = build(args.project, workspace, None, getattr(args, "project_path", ""))
    write_json(path, value)
    print(f"MAP: {args.project} {value['mode']} ({value['sources']['count']} sources, "
          f"{len(value['edges'])} edges, {len(value['unknowns'])} unknowns)")
    print(f"  path: {path}")
    return 0


def cmd_update(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    path = store_path(workspace, args.project)
    previous = None
    if os.path.isfile(path):
        try:
            previous = read_json(path)
            if map_hash(previous) != previous.get("map_sha256"):
                print("BLOCKED: existing map is corrupt; use create --force")
                return 1
        except (OSError, json.JSONDecodeError):
            print("BLOCKED: existing map is unreadable; use create --force")
            return 1
    value = build(args.project, workspace, previous, getattr(args, "project_path", ""))
    write_json(path, value)
    print(f"MAP: {args.project} updated ({value['mode']})")
    return 0


def cmd_verify(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    result = verify(args.project, workspace)
    if args.json:
        print(json.dumps({k: result[k] for k in ("state", "project", "path", "reasons")},
                         ensure_ascii=True))
    else:
        print(f"{result['state']}: {args.project}")
        for reason in result.get("reasons", [])[:20]:
            print(f"  - {reason}")
    return 0 if result["state"] == "CURRENT" else 1


def cmd_excerpt(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    print(excerpt(args.project, workspace, args.max_lines))
    return 0


def cmd_status(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    result = verify(args.project, workspace)
    print(json.dumps({k: result[k] for k in ("state", "project", "path", "reasons")},
                     ensure_ascii=True) if args.json else
          f"{result['state']}: {args.project} ({result['path']})")
    return 0 if result["state"] == "CURRENT" else 1


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-map-")
        registry = os.path.join(root, ".agents", "registry")
        os.makedirs(registry)
        with open(os.path.join(registry, "PROJECT_PROFILES.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"profiles": [{"id": "demo", "path": "proj"}]}, handle)
        project = os.path.join(root, "proj")
        os.makedirs(os.path.join(project, "tests"))
        with open(os.path.join(project, "b.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("def helper():\n    return 1\n")
        with open(os.path.join(project, "a.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("from b import helper\n\ndef run():\n    return helper()\n")
        with open(os.path.join(project, "tests", "test_a.py"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write("from a import run\n\ndef test_run():\n    assert run() == 1\n")
        return root

    @case("a missing map is created from real files")
    def _():
        root = fixture()
        assert cmd_create(argparse.Namespace(project="demo", workspace=root, force=False)) == 0
        result = verify("demo", root)
        assert result["state"] == "CURRENT", result
        assert result["map"]["sources"]["count"] == 3, result["map"]["sources"]
        assert len(result["map"]["edges"]) >= 1, result["map"]["edges"]
        return "created"

    @case("a changed source makes the map STALE")
    def _():
        root = fixture()
        cmd_create(argparse.Namespace(project="demo", workspace=root, force=False))
        with open(os.path.join(root, "proj", "a.py"), "a", encoding="utf-8") as handle:
            handle.write("\n# changed\n")
        result = verify("demo", root)
        assert result["state"] == "STALE", result
        return "stale"

    @case("an incremental update restores CURRENT")
    def _():
        root = fixture()
        cmd_create(argparse.Namespace(project="demo", workspace=root, force=False))
        with open(os.path.join(root, "proj", "a.py"), "a", encoding="utf-8") as handle:
            handle.write("\n# changed\n")
        assert cmd_update(argparse.Namespace(project="demo", workspace=root)) == 0
        assert verify("demo", root)["state"] == "CURRENT"
        return "current"

    @case("an unregistered project is mapped from an explicit path")
    def _():
        root = fixture()
        assert cmd_create(argparse.Namespace(project="newproj", workspace=root, force=False,
                                            project_path="proj")) == 0
        result = verify("newproj", root)
        assert result["state"] == "CURRENT", result
        assert result["map"]["project_path"] == "proj"
        return "discovered"

    @case("a new file in a dirty tree makes the map STALE")
    def _():
        root = fixture()
        cmd_create(argparse.Namespace(project="demo", workspace=root, force=False,
                                      project_path=""))
        with open(os.path.join(root, "proj", "c.py"), "w", encoding="utf-8") as handle:
            handle.write("def new_one():\n    return 1\n")
        result = verify("demo", root)
        assert result["state"] == "STALE", result
        assert any("new files" in reason for reason in result["reasons"]), result["reasons"]
        return "new file"

    @case("post-write touch marks dirty and blocks CURRENT")
    def _():
        root = fixture()
        cmd_create(argparse.Namespace(project="demo", workspace=root, force=False,
                                      project_path=""))
        assert mark_dirty("demo", root, ["proj/a.py"])["ok"] is True
        result = verify("demo", root)
        assert result["state"] == "STALE", result
        assert any("dirty" in reason for reason in result["reasons"]), result["reasons"]
        return "dirty"

    @case("a tampered map is INVALID")
    def _():
        root = fixture()
        cmd_create(argparse.Namespace(project="demo", workspace=root, force=False))
        path = store_path(root, "demo")
        data = read_json(path)
        data["modules"].append({"path": "evil.py"})
        write_json(path, data)
        assert verify("demo", root)["state"] == "INVALID"
        return "rejected"

    @case("an unknown project is BLOCKED, not guessed")
    def _():
        root = fixture()
        try:
            build("nope", root)
        except SystemExit:
            return "blocked"
        raise AssertionError("unknown project was accepted")

    passed = failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            passed += 1
            print(f"PASS  {name}  [{detail}]")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(cases)} cases passed")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="project_map.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")
    p = sub.add_parser("create", parents=[common])
    p.add_argument("--project", required=True)
    p.add_argument("--project-path", default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_create)
    p = sub.add_parser("update", parents=[common])
    p.add_argument("--project", required=True)
    p.add_argument("--project-path", default="")
    p.set_defaults(func=cmd_update)
    p = sub.add_parser("ensure", parents=[common])
    p.add_argument("--project", required=True)
    p.add_argument("--project-path", default="")
    p.set_defaults(func=cmd_ensure)
    p = sub.add_parser("touch", parents=[common])
    p.add_argument("--project", required=True)
    p.add_argument("--file", action="append", default=[])
    p.set_defaults(func=cmd_touch)
    p = sub.add_parser("verify", parents=[common])
    p.add_argument("--project", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)
    p = sub.add_parser("excerpt", parents=[common])
    p.add_argument("--project", required=True)
    p.add_argument("--max-lines", type=int, default=30)
    p.set_defaults(func=cmd_excerpt)
    p = sub.add_parser("status", parents=[common])
    p.add_argument("--project", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
