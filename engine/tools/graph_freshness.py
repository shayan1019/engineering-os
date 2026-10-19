#!/usr/bin/env python3
"""Graphify freshness and portability audit.

The graph cache is written by an external tool (graphify) and pins the absolute
path of the machine that built it. Moving the workspace therefore leaves a cache
that silently answers questions about a directory that no longer exists.

This tool never trusts the cache. It compares the recorded build identity with
the current project identity and reports one of:
  FRESH | STALE_RELOCATED | STALE_HEAD | STALE_SOURCES | PARTIAL_COVERAGE

MQL5 coverage is reported explicitly: graphify 0.9.66 in this workspace does not
index .mq5/.mqh, so any MQL impact claim must be marked PARTIAL and backed by the
lexical fallback this tool can emit (--fallback).

Commands:
  report    --project <id> [--json]
  fallback  --project <id>
  invalidate --project <id> [--confirm]     (destructive; requires --confirm)
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

ROOT = ep.ENGINE_ROOT
PROFILES = os.path.join(ep.REGISTRY, "PROJECT_PROFILES.json")
EVIDENCE = ep.GRAPH_EVIDENCE
MQL_EXT = (".mq5", ".mqh", ".mq4", ".mqh")
SRC_EXT = (".py", ".mq5", ".mqh", ".ts", ".tsx", ".js", ".rs", ".go", ".java", ".cs", ".c", ".cpp", ".h")
SKIP_DIRS = {"node_modules", ".git", "target", "dist", "build", "__pycache__", ".venv", ".venv-win",
             "venv", "graphify-out", ".pytest_cache", "site-packages", ".mypy_cache"}
MQL_DEF = re.compile(r"^\s*(?:void|int|uint|long|ulong|short|ushort|char|uchar|bool|double|float|"
                     r"string|datetime|color|template)\b[^;=()]*?\b([A-Za-z_]\w*)\s*\(", re.M)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def project_root(project: str) -> str:
    data = json.load(open(PROFILES, encoding="utf-8"))
    for p in data.get("profiles", []):
        if p.get("id") == project:
            return os.path.normpath(os.path.join(ROOT, p.get("path", "")))
    candidate = os.path.join(ROOT, project)
    if os.path.isdir(candidate):
        return candidate
    raise SystemExit(f"BLOCKED: unknown project '{project}'")


def norm(p: str) -> str:
    return p.replace("\\", "/").rstrip("/").lower()


def git_head(path: str):
    if not os.path.isdir(os.path.join(path, ".git")):
        return None
    try:
        out = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def walk_sources(path: str):
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.lower().endswith(SRC_EXT):
                yield os.path.join(dirpath, name)


def report(project: str) -> dict:
    root = project_root(project)
    gout = os.path.join(root, "graphify-out")
    findings, limits = [], []

    if not os.path.isdir(gout):
        return {"project": project, "verdict": "NO_GRAPH", "graph_dir": None,
                "findings": ["no graphify-out directory"], "limits": limits}

    meta_path = os.path.join(gout, "graph.meta.json")
    graph_path = os.path.join(gout, "graph.json")
    marker_path = os.path.join(gout, ".graphify_root")
    meta = {}
    if os.path.isfile(meta_path):
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(f"graph.meta.json unreadable: {exc}")
    else:
        findings.append("graph.meta.json missing")

    recorded_root = None
    if os.path.isfile(marker_path):
        recorded_root = open(marker_path, encoding="utf-8", errors="replace").read().strip()
    else:
        findings.append(".graphify_root missing (build path unknown)")

    relocated = False
    if recorded_root:
        recorded_exists = os.path.isdir(recorded_root)
        same = norm(recorded_root) == norm(root)
        relocated = (not same) and (not recorded_exists or os.path.basename(norm(recorded_root)) == os.path.basename(norm(root)))
        if not same:
            findings.append(
                f"graph was built at '{recorded_root}' which "
                + ("does not exist on this machine" if not recorded_exists else "differs from the current project path")
                + f"; current project is '{root}'"
            )

    head = git_head(root)
    recorded_head = meta.get("graph_build_head")
    stale_head = bool(head and recorded_head and head != recorded_head)
    if head is None:
        limits.append("project is not a git repository: recorded build head cannot be verified")
    elif not recorded_head:
        limits.append("graph.meta.json has no graph_build_head")
    elif stale_head:
        findings.append(f"graph head {recorded_head[:12]} != current HEAD {head[:12]}")

    graph_mtime = os.path.getmtime(graph_path) if os.path.isfile(graph_path) else 0
    newer, newest = 0, 0
    for src in walk_sources(root):
        m = os.path.getmtime(src)
        newest = max(newest, m)
        if graph_mtime and m > graph_mtime + 1:
            newer += 1
    stale_sources = newer > 0
    if stale_sources:
        findings.append(f"{newer} source file(s) changed after graph.json was written")

    languages = meta.get("indexed_languages") or []
    mql_files = [p for p in walk_sources(root) if p.lower().endswith(MQL_EXT)]
    mql_covered = any(ext in languages for ext in (".mq5", ".mqh", ".mq4"))
    if mql_files and not mql_covered:
        findings.append(f"{len(mql_files)} MQL source file(s) exist but graphify indexed languages are "
                        f"{languages}: MQL impact claims must be PARTIAL")

    coverage = meta.get("coverage")
    if coverage and coverage != "full":
        findings.append(f"graph declares coverage='{coverage}'")

    if relocated:
        verdict = "STALE_RELOCATED"
    elif stale_head:
        verdict = "STALE_HEAD"
    elif stale_sources:
        verdict = "STALE_SOURCES"
    elif coverage == "partial" or (mql_files and not mql_covered):
        verdict = "PARTIAL_COVERAGE"
    else:
        verdict = "FRESH"

    return {
        "project": project,
        "verdict": verdict,
        "graph_dir": os.path.relpath(gout, ROOT).replace(os.sep, "/"),
        "recorded_root": recorded_root,
        "current_root": os.path.relpath(root, ROOT).replace(os.sep, "/"),
        "recorded_head": recorded_head,
        "current_head": head,
        "generated_at": meta.get("generated_at"),
        "coverage": coverage,
        "indexed_languages": languages,
        "indexed_file_count": meta.get("indexed_file_count"),
        "source_files_newer_than_graph": newer,
        "graph_age_hours": round((datetime.now(timezone.utc).timestamp() - graph_mtime) / 3600, 1) if graph_mtime else None,
        "mql_file_count": len(mql_files),
        "mql_covered": mql_covered,
        "findings": findings,
        "limits": limits,
        "graph_usable_for": ["python", "typescript", "javascript", "powershell", "shell", "json", "toml"]
        if "coverage" in meta else [],
    }


def fallback(project: str) -> int:
    """Lexical MQL map: the explicit, honest substitute for a missing graph."""
    root = project_root(project)
    entries, defs = [], {}
    for src in sorted(walk_sources(root)):
        if not src.lower().endswith(MQL_EXT):
            continue
        rel = os.path.relpath(src, root).replace(os.sep, "/")
        try:
            text = open(src, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        entries.append({"path": rel, "sha256": sha256_file(src), "bytes": os.path.getsize(src)})
        for name in MQL_DEF.findall(text):
            defs.setdefault(name, []).append(rel)

    os.makedirs(EVIDENCE, exist_ok=True)
    out = os.path.join(EVIDENCE, f"{project}-mql-fallback.json")
    payload = {
        "kind": "mql_lexical_fallback",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": project,
        "why": "graphify does not index MQL5; semantic impact analysis is unavailable",
        "method": "regex over .mq5/.mqh for function definitions and file hashes",
        "confidence": "PARTIAL",
        "limits": [
            "definition sites only; call sites and runtime dispatch are not resolved",
            "preprocessor branches and macros are not evaluated",
            "results must not be reported as full coverage",
        ],
        "files": entries,
        "definitions": {k: sorted(set(v)) for k, v in sorted(defs.items())},
    }
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2)
    print(f"PARTIAL: wrote MQL fallback for {project}: {len(entries)} file(s), {len(defs)} definition(s)")
    print(f"  artifact: {os.path.relpath(out, ROOT).replace(os.sep, '/')}")
    return 0


def invalidate(project: str, confirm: bool) -> int:
    root = project_root(project)
    gout = os.path.join(root, "graphify-out")
    if not os.path.isdir(gout):
        print(f"NO_OP: no graphify-out for {project}")
        return 0
    plan = [
        f"move {os.path.relpath(os.path.join(gout, 'cache'), ROOT)} -> cache.stale-<timestamp>",
        f"move {os.path.relpath(os.path.join(gout, '.graphify_root'), ROOT)} -> .graphify_root.stale-<timestamp>",
        "reindex with the project's own graphify command (not invented by EOS)",
    ]
    if not confirm:
        print("BLOCKED: destructive step requires --confirm")
        for step in plan:
            print("  plan: " + step)
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for name in ("cache", ".graphify_root"):
        src = os.path.join(gout, name)
        if os.path.exists(src):
            dst = f"{src}.stale-{stamp}"
            os.replace(src, dst)
            print(f"  moved {name} -> {os.path.basename(dst)}")
    print("PASS: stale cache invalidated; reindex with the project's own command")
    for step in plan[2:]:
        print("  next: " + step)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="graph_freshness.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report"); r.add_argument("--project", required=True); r.add_argument("--json", action="store_true")
    f = sub.add_parser("fallback"); f.add_argument("--project", required=True)
    i = sub.add_parser("invalidate"); i.add_argument("--project", required=True); i.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    if args.cmd == "report":
        rep = report(args.project)
        if args.json:
            print(json.dumps(rep, indent=2))
        else:
            print(f"verdict: {rep['verdict']}")
            for k in ("graph_dir", "recorded_root", "current_root", "generated_at", "coverage",
                      "source_files_newer_than_graph", "graph_age_hours", "mql_file_count", "mql_covered"):
                if rep.get(k) is not None:
                    print(f"  {k}: {rep[k]}")
            for f_ in rep["findings"]:
                print(f"  finding: {f_}")
            for l_ in rep["limits"]:
                print(f"  limit: {l_}")
        return 0 if rep["verdict"] == "FRESH" else 1
    if args.cmd == "fallback":
        return fallback(args.project)
    return invalidate(args.project, args.confirm)


if __name__ == "__main__":
    sys.exit(main())
