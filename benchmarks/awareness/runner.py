#!/usr/bin/env python3
"""Engineering Awareness Benchmark runner.

Materialises one scenario in an isolated temporary directory and runs one arm:

    old = the v1.0.1 engine tag
    new = the current engineering-os engine

The oracle lives in scenarios/ and is never copied into the fixture. Both arms
receive the same task, model, timeout and neutral output contract.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scenarios import SCENARIOS  # noqa: E402

ENGINE_REPO = HERE.parents[1]
NEW_ENGINE = ENGINE_REPO / "engine"
GLOBAL_EOS = Path(os.environ.get("EOS_GLOBAL_HOME", Path.home() / ".eos"))
RUNTIME_VERSION = "1.0.1"
DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
DEFAULT_OLD_TAG = "v1.0.1"
DEFAULT_NEW_TAG = "v1.0.2"

OUTPUT_CONTRACT = """

Reporting contract (same for every arm):
At the end, write `.eos-benchmark.json` in the project root with exactly this shape:
{
  "findings": [
    {"severity": "blocker|major|minor", "claim": "...", "evidence": ["path:line or command"]}
  ],
  "decision": {"approach": "...", "alternatives": ["..."], "assumptions": ["..."], "why": "..."},
  "self_correction": [{"wrong": "...", "detected_by": "...", "fix": "..."}],
  "scope": {"changed_files": ["..."], "not_changed": ["..."]},
  "closure": {"tests": ["..."], "residual_risks": ["..."]}
}
Use only this project directory. Run the existing tests. Do not modify files
outside the project. Your final message must summarise what you changed and what
you could not verify.
"""


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)],
                              capture_output=True, text=True)
        if done.returncode == 0:
            return
    shutil.copytree(src, dst, dirs_exist_ok=True)


def extract_tag(tag: str, dest: Path) -> Path:
    """Extract a git tag's tree into dest and return dest."""
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "-C", str(ENGINE_REPO), "archive", tag],
                          capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"cannot archive {tag}: {proc.stderr.decode('utf-8', 'replace')[:400]}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        archive.extractall(dest)
    return dest


def prepare_home(arm: str, root: Path, old_tag: str, new_tag: str) -> tuple:
    tag = old_tag if arm == "old" else new_tag
    version = tag.lstrip("v")
    engine = extract_tag(tag, root / f"{arm}-tree") / "engine"
    if not (engine / "tools" / "decision.py").is_file():
        raise SystemExit(f"engine is not usable: {engine}")
    home = root / f"eos-{arm}"
    version_dir = home / "versions" / version
    shutil.copytree(engine, version_dir / "engine", dirs_exist_ok=True)
    link_or_copy(GLOBAL_EOS / "versions" / RUNTIME_VERSION / "runtime", version_dir / "runtime")
    (version_dir / "VERSION").write_text(version + "\n", encoding="utf-8")
    # Full host isolation: each arm gets its own XDG config/data/state/cache.
    # The global config file and the auth/DB are copied, but the global plugin
    # directory is not; the arm plugins are installed into the fixture by the
    # arm engine's own scope installer.
    xdg = {
        "config": home / "xdg-config",
        "data": home / "xdg-data",
        "state": home / "xdg-state",
        "cache": home / "xdg-cache",
    }
    for path in xdg.values():
        path.mkdir(parents=True, exist_ok=True)
    host_config = Path.home() / ".config" / "opencode" / "opencode.jsonc"
    config_dir = xdg["config"] / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    if host_config.is_file():
        shutil.copyfile(host_config, config_dir / "opencode.jsonc")
    data_dir = xdg["data"] / "opencode"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("auth.json", "opencode.db", "opencode.db-shm", "opencode.db-wal"):
        source = Path.home() / ".local" / "share" / "opencode" / name
        if source.is_file():
            shutil.copyfile(source, data_dir / name)
    state_dir = xdg["state"] / "opencode"
    state_dir.mkdir(parents=True, exist_ok=True)
    for name in ("kv.json", "model.json"):
        source = Path.home() / ".local" / "state" / "opencode" / name
        if source.is_file():
            shutil.copyfile(source, state_dir / name)
    return home, version_dir, xdg


def install_workspace_plugins(engine: Path, work: Path) -> dict:
    """Install the arm's own plugins into the fixture, project-local."""
    script = engine / "tools" / "scope_install.py"
    if not script.is_file():
        return {"ok": False, "detail": f"scope installer missing: {script}"}
    proc = subprocess.run(
        [sys.executable, str(script), "install", "--mode", "workspace",
         "--root", str(work), "--apply", "--force"],
        capture_output=True, text=True, timeout=300,
    )
    return {"ok": proc.returncode == 0,
            "detail": (proc.stdout + proc.stderr).strip()[-1200:]}


def materialise(scenario: dict, dest: Path) -> None:
    for rel, content in scenario["fixture"].items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def parse_agent(stdout: str) -> dict:
    tokens = {"input": 0, "output": 0, "reasoning": 0,
              "cache_read": 0, "cache_write": 0}
    text, sessions, events = [], set(), 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events += 1
        session = event.get("sessionID")
        if session:
            sessions.add(session)
        part = event.get("part") or {}
        if part.get("type") == "text" and part.get("text"):
            text.append(part["text"])
        if event.get("type") == "step_finish":
            got = part.get("tokens") or {}
            for key in tokens:
                tokens[key] += int(got.get(key) or 0)
    return {"events": events, "sessions": sorted(sessions),
            "tokens": tokens, "final_text": "\n".join(text)}


def run(args) -> int:
    scenario = SCENARIOS.get(args.scenario)
    if not scenario:
        raise SystemExit(f"unknown scenario: {args.scenario}")
    stamp = utc_stamp()
    result = Path(args.out) if args.out else HERE / "results" / f"{args.scenario}-{args.arm}-{stamp}"
    result.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=f"eos-bench-{args.scenario}-{args.arm}-"))
    work = root / "work"
    materialise(scenario, work)
    prompt = scenario["task"].strip() + OUTPUT_CONTRACT
    (result / "prompt.txt").write_text(prompt, encoding="utf-8", newline="\n")
    home, version_dir, xdg = prepare_home(args.arm, root, args.old_tag, args.new_tag)
    plugin_install = install_workspace_plugins(version_dir / "engine", work)
    meta = {
        "scenario": args.scenario, "domain": scenario.get("domain"),
        "arm": args.arm, "model": args.model, "created_at": stamp,
        "work": str(work), "home": str(home), "version_dir": str(version_dir),
        "xdg": {name: str(path) for name, path in xdg.items()},
        "engine_tag": args.old_tag if args.arm == "old" else args.new_tag,
        "plugin_install": plugin_install,
    }
    (result / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if args.dry_run:
        print(f"DRY RUN: {result}")
        print(f"  work: {work}")
        print(f"  engine: {version_dir / 'engine'}")
        return 0

    opencode = shutil.which("opencode")
    if not opencode:
        raise SystemExit("opencode executable not found on PATH")
    env = {**os.environ, "EOS_HOME": str(home),
           "XDG_CONFIG_HOME": str(xdg["config"]),
           "XDG_DATA_HOME": str(xdg["data"]),
           "XDG_STATE_HOME": str(xdg["state"]),
           "XDG_CACHE_HOME": str(xdg["cache"]),
           "EOS_PYTHON": str(version_dir / "runtime" / "python" / "python.exe")}
    env.pop("OPENCODE_CONFIG_DIR", None)
    cmd = [opencode, "run", "--standalone", "--format", "json",
           "--agent", "build", "--model", args.model, prompt]
    try:
        proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=args.timeout)
    except subprocess.TimeoutExpired as exc:
        (result / "agent.stdout.jsonl").write_text(exc.stdout or "", encoding="utf-8")
        (result / "agent.stderr.log").write_text(exc.stderr or "", encoding="utf-8")
        meta["timeout"] = args.timeout
        (result / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"TIMEOUT: {result}")
        return 2
    (result / "agent.stdout.jsonl").write_text(proc.stdout, encoding="utf-8")
    (result / "agent.stderr.log").write_text(proc.stderr, encoding="utf-8")
    parsed = parse_agent(proc.stdout)
    guard = work / ".agents" / "evidence" / "tools" / "guard.jsonl"
    host_engine = ""
    if guard.is_file():
        for line in reversed(guard.read_text(encoding="utf-8", errors="replace").splitlines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("kind") == "capability":
                host_engine = str(event.get("engine") or "")
                break
    expected = str(version_dir / "engine").lower()
    host_ok = bool(host_engine and expected in host_engine.lower())
    meta.update({"returncode": proc.returncode, **parsed,
                 "host_engine": host_engine, "host_engine_ok": host_ok,
                 "valid_host": bool(plugin_install.get("ok") and host_ok)})
    (result / "agent.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    tree = result / "tree"
    if tree.exists():
        shutil.rmtree(tree)
    shutil.copytree(work, tree, ignore=shutil.ignore_patterns(".git", "node_modules",
                                                              "__pycache__"))
    print(f"RESULT: {result}")
    print(f"  rc={proc.returncode} tokens={parsed['tokens']} events={parsed['events']}")
    return 0 if proc.returncode == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="runner.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list")
    p.set_defaults(func=lambda _: (print("\n".join(sorted(SCENARIOS))), 0)[1])
    p = sub.add_parser("run")
    p.add_argument("--scenario", required=True)
    p.add_argument("--arm", choices=("old", "new"), required=True)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--out", default="")
    p.add_argument("--old-tag", default=DEFAULT_OLD_TAG)
    p.add_argument("--new-tag", default=DEFAULT_NEW_TAG)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=run)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
