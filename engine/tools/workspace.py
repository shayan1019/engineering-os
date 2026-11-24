#!/usr/bin/env python3
"""Universal workspace discovery and scope isolation.

EOS is not tied to one project. A workspace is a directory the user chose
(Workspace installation) or the whole machine (Global installation). This tool
answers three questions deterministically:

  * what projects exist under the scope, and what technology each uses
  * is this directory inside the scope (real path, not a junction escape)
  * which project owns this directory

Nothing here starts a model, writes into a project, or guesses a technology it
did not see a marker for. A directory with no marker is `UNKNOWN`, not "web".

Commands:
  scope set --root <dir> [--mode workspace|global] [--host opencode]...
  scope show [--json]
  discover --root <dir> [--write] [--json] [--max-depth N]
  resolve --path <dir> [--json]
  status [--json]
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

SCHEMA = 1
SCOPE_NAME = ".eos"
MARKERS = {
    "package.json": "javascript",
    "tsconfig.json": "typescript",
    "vite.config.ts": "web",
    "next.config.js": "web",
    "next.config.mjs": "web",
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "setup.py": "python",
    "Pipfile": "python",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
    "pubspec.yaml": "dart",
    "AndroidManifest.xml": "android",
    "CMakeLists.txt": "cpp",
    "platformio.ini": "embedded",
    "docker-compose.yml": "container",
    "Dockerfile": "container",
    "index.html": "static_web",
}
EXT_MARKERS = {
    ".mq5": "mql", ".mq4": "mql", ".mqh": "mql",
    ".csproj": "dotnet", ".sln": "dotnet",
    ".ino": "embedded",
}
SKIP_DIRS = {
    ".git", ".venv", ".venv-win", ".venv-qc", "venv", "node_modules", "__pycache__",
    "site-packages", "dist", "build", "target", "graphify-out", ".pytest_cache",
    "backups", "_backup", ".eos", ".agents", ".opencode", ".claude", ".codex",
    "installer", "runtime",
}
SKIP_PREFIXES = ("_d6_", "_restore_", "_runtime_", "_fci_", "_sync_", "_ARCHIVE_",
                 "_backup", "_eos-backup")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: str):
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = ep.temp_name(path)
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    ep.replace(tmp, path)


def real(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def inside(root: str, path: str) -> bool:
    root_real = real(root)
    path_real = real(path)
    try:
        return os.path.commonpath([root_real, path_real]) == root_real
    except ValueError:
        return False


def scope_path(root: str) -> str:
    return os.path.join(root, SCOPE_NAME, "scope.json")


def global_scope_path() -> str:
    return os.path.join(ep.EOS_HOME, "scope.json")


def load_scope(root: str | None = None) -> dict:
    """Nearest scope wins: the project root, then EOS_HOME for global."""
    candidates = []
    if root:
        current = real(root)
        while True:
            candidates.append(os.path.join(current, SCOPE_NAME, "scope.json"))
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    candidates.append(global_scope_path())
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                data = read_json(candidate)
                if data.get("schema") == SCHEMA and data.get("mode") in ("workspace", "global"):
                    data["_source"] = candidate
                    return data
            except (OSError, json.JSONDecodeError):
                continue
    return {"schema": SCHEMA, "mode": "unconfigured", "root": "", "hosts": [],
            "_source": ""}


def slug(name: str, path: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "project"
    digest = hashlib.sha256(real(path).encode("utf-8")).hexdigest()[:8]
    return f"{base[:40]}-{digest}"


def git_root(path: str) -> str:
    current = real(path)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent


def has_tests(path: str) -> bool:
    for base, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(base, path)
        if rel.count(os.sep) > 2:
            dirs[:] = []
            continue
        for name in files:
            lower = name.lower()
            if lower.startswith("test_") or lower.endswith("_test.py") or lower.endswith(".test.ts"):
                return True
        if os.path.basename(base).lower() in ("test", "tests", "__tests__", "spec"):
            return True
    return False


def detect(path: str) -> dict:
    tech, markers = [], []
    try:
        entries = os.listdir(path)
    except OSError:
        return {"tech": [], "markers": [], "confidence": "UNKNOWN"}
    for name in entries:
        if name in MARKERS:
            markers.append(name)
            if MARKERS[name] not in tech:
                tech.append(MARKERS[name])
        for ext, label in EXT_MARKERS.items():
            if name.lower().endswith(ext) and label not in tech:
                markers.append(name)
                tech.append(label)
    if os.path.isfile(os.path.join(path, ".eos", "project.json")):
        markers.append(".eos/project.json")
    confidence = "HIGH" if tech else "UNKNOWN"
    return {"tech": tech, "markers": markers, "confidence": confidence}


def discover(root: str, max_depth: int = 3) -> dict:
    root = real(root)
    if not os.path.isdir(root):
        raise SystemExit(f"BLOCKED: workspace root does not exist: {root}")
    projects = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs
                         if d not in SKIP_DIRS and not d.startswith(".")
                         and not any(d.startswith(prefix) for prefix in SKIP_PREFIXES))
        depth = os.path.relpath(base, root).count(os.sep)
        if depth > max_depth:
            dirs[:] = []
            continue
        info = detect(base)
        if not info["tech"]:
            continue
        project = {
            "id": slug(os.path.basename(base) or "root", base),
            "name": os.path.basename(base) or "root",
            "path": os.path.relpath(base, root).replace("\\", "/"),
            "real_path": base,
            "tech": info["tech"],
            "markers": sorted(set(info["markers"])),
            "confidence": info["confidence"],
            "git_root": git_root(base),
            "has_tests": has_tests(base),
            "eos_managed": os.path.isfile(os.path.join(base, ".eos", "project.json")),
        }
        projects.append(project)
        # A project root owns its subtree; do not report nested modules as
        # separate projects unless they carry their own marker and no parent did.
        dirs[:] = [d for d in dirs if os.path.isfile(os.path.join(base, d, "package.json"))
                   or os.path.isfile(os.path.join(base, d, "pyproject.toml"))
                   or os.path.isfile(os.path.join(base, d, "Cargo.toml"))]
    projects.sort(key=lambda item: (item["path"].count("/"), item["path"]))
    return {"schema": SCHEMA, "root": root, "generated_at": utc_now(),
            "max_depth": max_depth, "projects": projects,
            "unknown": [p for p in os.listdir(root)
                        if os.path.isdir(os.path.join(root, p))
                        and p not in SKIP_DIRS and not p.startswith(".")
                        and not any(p.startswith(prefix) for prefix in SKIP_PREFIXES)
                        and not any(item["path"].split("/")[0] == p for item in projects)]}


def cmd_scope_set(args) -> int:
    root = real(args.root)
    if args.mode == "workspace":
        if not os.path.isdir(root):
            print(f"BLOCKED: root does not exist: {root}")
            return 2
        target = scope_path(root)
    else:
        target = global_scope_path()
    data = {
        "schema": SCHEMA,
        "mode": args.mode,
        "root": root if args.mode == "workspace" else "",
        "hosts": list(args.host or ["opencode"]),
        "created_at": utc_now(),
        "version": "0.3.0",
    }
    if os.path.isfile(target):
        backup = target + ".bak-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with open(target, encoding="utf-8-sig") as src, open(backup, "w", encoding="utf-8", newline="\n") as dst:
            dst.write(src.read())
    write_json(target, data)
    print(f"SCOPE: {args.mode} -> {target}")
    if args.mode == "workspace":
        print(f"  root: {root}")
    else:
        print("  applies to supported hosts; no filesystem-wide permission is granted")
    return 0


def cmd_scope_show(args) -> int:
    scope = load_scope(ep.resolve_workspace(args.workspace))
    if args.json:
        print(json.dumps(scope, ensure_ascii=True))
        return 0
    print(f"mode   : {scope.get('mode')}")
    print(f"root   : {scope.get('root') or '(global)'}")
    print(f"hosts  : {', '.join(scope.get('hosts') or []) or '(none)'}")
    print(f"source : {scope.get('_source') or '(none)'}")
    return 0


def cmd_discover(args) -> int:
    root = real(args.root or ep.resolve_workspace(args.workspace))
    result = discover(root, args.max_depth)
    if args.write:
        target = os.path.join(root, SCOPE_NAME, "projects.json")
        write_json(target, result)
        result["written"] = target
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"WORKSPACE: {result['root']}  projects={len(result['projects'])}")
        for project in result["projects"]:
            print(f"  {project['id']:<32} {','.join(project['tech']):<24} "
                  f"{project['path']}  tests={project['has_tests']} git={bool(project['git_root'])}")
        if result["unknown"]:
            print("  unknown (no marker): " + ", ".join(result["unknown"]))
        if result.get("written"):
            print(f"  written: {result['written']}")
    return 0


def resolve(path: str, workspace: str | None = None) -> dict:
    path = real(path)
    scope = load_scope(workspace or path)
    mode = scope.get("mode")
    if mode == "workspace":
        ok = inside(scope.get("root", ""), path)
        return {"in_scope": ok, "mode": mode, "root": scope.get("root"),
                "path": path, "reason": "" if ok else "outside the workspace root"}
    if mode == "global":
        return {"in_scope": True, "mode": mode, "root": "", "path": path,
                "reason": "global installation; supported hosts decide where EOS opens"}
    return {"in_scope": False, "mode": mode, "root": "", "path": path,
            "reason": "no EOS scope is configured"}


def cmd_resolve(args) -> int:
    result = resolve(args.path, args.workspace)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"{'IN_SCOPE' if result['in_scope'] else 'OUT_OF_SCOPE'}: {result['path']}")
        print(f"  mode: {result['mode']}")
        if result["reason"]:
            print(f"  reason: {result['reason']}")
    return 0 if result["in_scope"] else 1


def cmd_status(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    scope = load_scope(workspace)
    result = resolve(workspace, workspace)
    print(f"workspace: {workspace}")
    print(f"scope    : {scope.get('mode')} ({scope.get('_source') or 'none'})")
    print(f"in_scope : {result['in_scope']}")
    if scope.get("mode") == "workspace":
        projects = discover(scope.get("root", ""), 3)["projects"]
        print(f"projects : {len(projects)}")
    return 0 if result["in_scope"] else 1


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-ws-")
        for name, marker in (("web", "package.json"), ("py", "pyproject.toml"),
                             ("rust", "Cargo.toml")):
            path = os.path.join(root, name)
            os.makedirs(path)
            open(os.path.join(path, marker), "w").write("{}")
        os.makedirs(os.path.join(root, "no-marker"))
        outside = tempfile.mkdtemp(prefix="eos-out-")
        return root, outside

    @case("discovery finds marker projects and leaves the rest unknown")
    def _():
        root, _ = fixture()
        result = discover(root)
        assert len(result["projects"]) == 3, result["projects"]
        assert "no-marker" in result["unknown"], result["unknown"]
        tech = {p["name"]: p["tech"] for p in result["projects"]}
        assert tech["web"] == ["javascript"], tech
        assert tech["py"] == ["python"], tech
        assert tech["rust"] == ["rust"], tech
        return "3 projects"

    @case("project ids are stable and unique")
    def _():
        root, _ = fixture()
        first = {p["path"]: p["id"] for p in discover(root)["projects"]}
        second = {p["path"]: p["id"] for p in discover(root)["projects"]}
        assert first == second
        assert len(set(first.values())) == len(first)
        return "stable"

    @case("workspace scope rejects an outside real path")
    def _():
        root, outside = fixture()
        args = argparse.Namespace(root=root, mode="workspace", host=["opencode"])
        assert cmd_scope_set(args) == 0
        assert resolve(os.path.join(root, "web"))["in_scope"] is True
        assert resolve(outside)["in_scope"] is False
        return "isolated"

    @case("no scope means OUT_OF_SCOPE")
    def _():
        root, _ = fixture()
        result = resolve(root)
        assert result["in_scope"] is False
        assert result["mode"] == "unconfigured"
        return "denied"

    @case("scope write is atomic and keeps a backup")
    def _():
        root, _ = fixture()
        args = argparse.Namespace(root=root, mode="workspace", host=["opencode"])
        assert cmd_scope_set(args) == 0
        first = read_json(scope_path(root))
        assert first["mode"] == "workspace"
        assert cmd_scope_set(args) == 0
        backups = [n for n in os.listdir(os.path.join(root, ".eos")) if ".bak-" in n]
        assert backups, "no backup"
        return "backup kept"

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
    parser = argparse.ArgumentParser(prog="workspace.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")

    p = sub.add_parser("scope", parents=[common])
    scope_sub = p.add_subparsers(dest="action", required=True)
    s = scope_sub.add_parser("set")
    s.add_argument("--root", required=True)
    s.add_argument("--mode", choices=("workspace", "global"), default="workspace")
    s.add_argument("--host", action="append", default=[])
    s.set_defaults(func=cmd_scope_set)
    s = scope_sub.add_parser("show")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scope_show)

    p = sub.add_parser("discover", parents=[common])
    p.add_argument("--root", default="")
    p.add_argument("--max-depth", type=int, default=3)
    p.add_argument("--write", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("resolve", parents=[common])
    p.add_argument("--path", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("status", parents=[common])
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
