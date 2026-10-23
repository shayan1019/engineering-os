#!/usr/bin/env python3
"""project_bootstrap.py — universal per-project Graphify bootstrap (stdlib only).

EOS project bootstrap. Makes repository-graph discovery project-local and optional:
the graph provider is never global.

  GLOBAL (this control plane): methodology, bootstrap/discovery logic,
                               capability detection.
  PROJECT-LOCAL: Graphify skill, graph index, graph.meta.json,
                 .graphifyignore, freshness state.

Never performs a global install, never touches AGENTS.md/CLAUDE.md/GEMINI.md,
never builds on shell init — bootstrap runs lazily on the first Fable coding
task that needs project intelligence (see runtime/project-bootstrap.md).

Usage:
  python3 project_bootstrap.py --project <dir> [--check-only] [--init] [--update]

  --check-only   cheap detection only (no install, no build). Safe anywhere.
  --init         check + project-scope install if missing + build/update the
                 graph if MISSING/STALE + write freshness snapshot.
  --update       check + rebuild the graph only if STALE (no install).

Output: one JSON object on stdout. Exit 0 = bootstrap reached a usable state
(CURRENT graph, or cleanly absent with Fable unaffected). Exit 1 = graph
wanted but unavailable (reason in JSON; Fable continues without it).
Exit 2 = usage error.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

CODE_EXTS = {
    ".py", ".ts", ".mts", ".cts", ".js", ".jsx", ".tsx", ".mjs",
    ".go", ".rs", ".java", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".cu", ".cuh", ".rb", ".cs", ".kt", ".kts", ".scala", ".php",
    ".swift", ".lua", ".zig", ".ps1", ".psm1", ".psd1", ".ex", ".exs",
    ".m", ".mm", ".jl", ".vue", ".svelte", ".astro", ".groovy",
    ".gradle", ".dart", ".v", ".sv", ".svh", ".sql", ".sh", ".bash",
    ".json", ".tf", ".tfvars", ".hcl",
}
UNPARSED_EXTS = {".mq5", ".mqh", ".mq4", ".ex4", ".ex5"}

SKIP_DIRS = {"graphify-out", ".git", "__pycache__", ".venv", "venv",
             "node_modules", ".mypy_cache", ".pytest_cache", "dist", "build",
             "target", ".agents/skills/graphify"}

ROOT_MARKERS = {".git", "pyproject.toml", "package.json", "go.mod",
                "Cargo.toml", "requirements.txt", ".agents", "pom.xml",
                "build.gradle", "composer.json", "Gemfile"}

SCAN_CAP = 8000  # max files walked for language detection (bounded, cheap)
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_root(start):
    cur = os.path.abspath(start)
    while True:
        try:
            entries = set(os.listdir(cur))
        except OSError:
            entries = set()
        if entries & ROOT_MARKERS:
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start)
        cur = parent


def _detect_languages(root):
    supported, unsupported = set(), set()
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith(".venv"))
        for f in sorted(filenames):
            ext = os.path.splitext(f)[1].lower()
            if ext in CODE_EXTS:
                supported.add(ext)
            elif ext in UNPARSED_EXTS:
                unsupported.add(ext)
            n += 1
            if n >= SCAN_CAP:
                break
        if n >= SCAN_CAP:
            break
    return sorted(supported), sorted(unsupported), n >= SCAN_CAP


def _coverage(supported, unsupported):
    cov = {}
    if supported:
        cov["supported"] = "FULL"
    for ext in unsupported:
        if ext in (".mq5", ".mqh", ".mq4"):
            cov["mql5"] = "PARTIAL"
        else:
            cov[ext.lstrip(".")] = "PARTIAL"
    return cov


def _hash_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _run(args, cwd, timeout):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _freshness(root):
    script = os.path.join(_TOOLS_DIR, "graph_freshness.py")
    if not os.path.exists(script):
        return {"graph_state": "UNKNOWN", "reasons": ["freshness_checker_missing"],
                "reason_codes": ["UNKNOWN"]}
    r = _run([sys.executable, script, "--project", root], root, 120)
    if r is None or not r.stdout.strip():
        return {"graph_state": "UNKNOWN", "reasons": ["freshness_checker_failed"],
                "reason_codes": ["UNKNOWN"]}
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except ValueError:
        return {"graph_state": "UNKNOWN", "reasons": ["freshness_bad_output"],
                "reason_codes": ["UNKNOWN"]}
    state = "CURRENT" if d.get("status") == "current" else "STALE"
    if not os.path.exists(os.path.join(root, "graphify-out", "graph.json")):
        state = "MISSING"
    return {"graph_state": state, "reasons": d.get("reasons", []),
            "reason_codes": d.get("reason_codes", [])}


def _do_install(root, result):
    """Project-scope skill install only. Returns True on success."""
    before = {f: _hash_file(os.path.join(root, f)) for f in INSTRUCTION_FILES}
    r = _run(["graphify", "install", "--project", "--platform", "agents"],
             root, 180)
    ok = (r is not None and r.returncode == 0 and os.path.exists(
        os.path.join(root, ".agents", "skills", "graphify", "SKILL.md")))
    after = {f: _hash_file(os.path.join(root, f)) for f in INSTRUCTION_FILES}
    untouched = all(before.get(f) == after.get(f) for f in INSTRUCTION_FILES
                    if before.get(f) is not None or after.get(f) is not None)
    # Guard: installer must never rewrite instruction files. Report only —
    # never auto-restore (that would be a destructive write on suspicion).
    result["agents_files_untouched"] = untouched
    result["install_log"] = (r.stdout[-500:] + r.stderr[-500:]
                             if r is not None else "graphify binary failed to run")
    return ok


def _do_build(root, result, update_only=False):
    if update_only:
        cmd = ["graphify", "update", root]
    else:
        cmd = ["graphify", "extract", ".", "--code-only"]
    r = _run(cmd, root, 900)
    ok = (r is not None and r.returncode == 0 and os.path.exists(
        os.path.join(root, "graphify-out", "graph.json")))
    result["build_log"] = ((r.stdout[-800:] + r.stderr[-800:])
                           if r is not None else "graphify binary failed to run")
    if ok:
        fr = _run([sys.executable,
                   os.path.join(_TOOLS_DIR, "graph_freshness.py"),
                   "--project", root, "--init"], root, 180)
        ok = fr is not None and fr.returncode == 0
    return ok


def main(argv):
    project, check_only, init, update = None, False, False, False
    i = 0
    while i < len(argv):
        if argv[i] == "--project" and i + 1 < len(argv):
            project = argv[i + 1]
            i += 2
        elif argv[i] == "--check-only":
            check_only = True
            i += 1
        elif argv[i] == "--init":
            init = True
            i += 1
        elif argv[i] == "--update":
            update = True
            i += 1
        else:
            print(json.dumps({"error": f"unknown_arg: {argv[i]}"}))
            return 2
    if not project or not os.path.isdir(project):
        print(json.dumps({"error": "project_dir_required"}))
        return 2

    root = _find_root(project)
    supported, unsupported, truncated = _detect_languages(root)
    cli = shutil.which("graphify") is not None
    mcp = shutil.which("graphify-mcp") is not None
    installed = os.path.exists(
        os.path.join(root, ".agents", "skills", "graphify", "SKILL.md"))

    result = {
        "project_root": root,
        "graphify": {
            "installed": installed,
            "scope": "project" if installed else None,
            "graph_state": None,
            "cli": cli,
            "mcp": mcp,
        },
        "coverage": _coverage(supported, unsupported),
        "languages_supported": supported,
        "languages_unparsed": unsupported,
        "scan_truncated": truncated,
        "actions": [],
    }

    if not cli:
        result["graphify"]["graph_state"] = "UNAVAILABLE"
        result["actions"].append("graphify_cli_missing: Fable continues without repo graph")
        print(json.dumps(result, ensure_ascii=True))
        return 1 if (init or update) else 0

    fresh = _freshness(root)
    result["graphify"]["graph_state"] = fresh["graph_state"]
    result["freshness_reasons"] = fresh["reasons"]
    result["reason_codes"] = fresh.get("reason_codes", [])

    if check_only:
        print(json.dumps(result, ensure_ascii=True))
        return 0

    if init and not installed:
        if _do_install(root, result):
            result["graphify"]["installed"] = True
            result["graphify"]["scope"] = "project"
            result["actions"].append("installed project-scope skill (no AGENTS injection, no global)")
        else:
            result["actions"].append("install_failed: Fable continues without repo graph")
            print(json.dumps(result, ensure_ascii=True))
            return 1
        if not result.get("agents_files_untouched", True):
            result["actions"].append("WARNING: installer touched instruction files (reported, not restored)")

    state = result["graphify"]["graph_state"]
    if init and state in ("MISSING", "STALE"):
        if _do_build(root, result):
            result["actions"].append("graph built + freshness snapshot written")
        else:
            result["actions"].append("build_failed: Fable continues without repo graph")
            print(json.dumps(result, ensure_ascii=True))
            return 1
    elif update and state == "STALE":
        if _do_build(root, result, update_only=True):
            result["actions"].append("graph updated + freshness snapshot written")
        else:
            result["actions"].append("update_failed: Fable continues with stale-graph fallback")
            print(json.dumps(result, ensure_ascii=True))
            return 1

    fresh = _freshness(root)
    result["graphify"]["graph_state"] = fresh["graph_state"]
    result["freshness_reasons"] = fresh["reasons"]
    result["reason_codes"] = fresh.get("reason_codes", [])
    print(json.dumps(result, ensure_ascii=True))
    return 0 if fresh["graph_state"] == "CURRENT" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
