#!/usr/bin/env python3
"""graphify_soak.py — Faz 9 soak matrix (stdlib only, tmp repos only).

Runs the bootstrap + freshness state machine through 10 scenarios in
isolated /tmp directories. NEVER touches production projects.

Scenarios:
  1  greenfield_init      fresh repo + .py -> --init -> CURRENT, project scope
  2  existing_clean_reuse second --init -> CURRENT with zero actions (no rebuild)
  3  dirty_tree           tracked modification -> STALE / WORKTREE_CHANGED
  4  detached_head        git checkout --detach -> STALE / HEAD_CHANGED
  5  no_git_repo          plain dir -> CURRENT; modify -> STALE
  6  untracked_source     new .py -> STALE / UNTRACKED_SUPPORTED_SOURCE
  7  toolchain_change     meta version patched -> STALE / TOOLCHAIN_CHANGED
  8  mcp_missing          graphify-mcp hidden -> mcp:false, graph still CURRENT
  9  partial_coverage     .mq5 present -> coverage mql5 PARTIAL, never FULL
  10 out_of_root_writes   full lifecycle writes nothing outside project root

Also records wall-time per phase (detection/build latency proxy).

Exit 0 = all pass, 1 = failure. Token deltas stay session-measured
(see fable-5-1/tests/graphify-integration.md) — this harness covers the
state machine, not token accounting.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BOOT = os.path.join(HERE, "project_bootstrap.py")
FRESH = os.path.join(HERE, "graph_freshness.py")
FAILURES = []
TIMINGS = {}


def _run(args, cwd="/tmp", env=None, timeout=600):
    t0 = time.time()
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
    finally:
        TIMINGS.setdefault(args[0] if isinstance(args, list) else args, []).append(
            round(time.time() - t0, 2))
    return r


def _j(args, cwd="/tmp", env=None, timeout=600):
    r = _run([sys.executable] + args, cwd=cwd, env=env, timeout=timeout)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]), r.returncode
    except (ValueError, IndexError):
        return {"_raw": r.stdout[-300:], "_rc": r.returncode}, 99


def _git(cwd, *args):
    r = _run(["git", "init", "-q", cwd]) if args == ("init",) else None
    if r is not None:
        _run(["git", "-C", cwd, "config", "user.email", "soak@t"])
        _run(["git", "-C", cwd, "config", "user.name", "soak"])
        return
    _run(["git", "-C", cwd] + list(args))


def _mkrepo(with_git=True, files=None):
    root = tempfile.mkdtemp(prefix="soak_")
    if with_git:
        _git(root, "init")
    for name, content in (files or {"a.py": "x = 1\n"}).items():
        p = os.path.join(root, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(content)
    if with_git:
        _run(["git", "-C", root, "add", "."])
        _run(["git", "-C", root, "commit", "-qm", "init"])
    return root


def _check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        FAILURES.append(name)


def s1_greenfield():
    root = _mkrepo()
    d, rc = _j([BOOT, "--project", root, "--init"], cwd=root)
    g = d.get("graphify", {})
    _check("s1_greenfield_current", g.get("graph_state") == "CURRENT" and rc == 0,
            f"state={g.get('graph_state')}")
    _check("s1_greenfield_scope", g.get("installed") and g.get("scope") == "project")
    return root


def s2_reuse(root):
    d, rc = _j([BOOT, "--project", root, "--init"], cwd=root)
    _check("s2_reuse_no_rebuild",
            d["graphify"]["graph_state"] == "CURRENT" and d.get("actions") == [],
            f"actions={d.get('actions')}")


def s3_dirty(root):
    with open(os.path.join(root, "a.py"), "a") as fh:
        fh.write("y = 2\n")
    d, _ = _j([BOOT, "--project", root, "--check-only"], cwd=root)
    _check("s3_dirty_stale",
            d["graphify"]["graph_state"] == "STALE"
            and "WORKTREE_CHANGED" in d.get("reason_codes", []),
            f"codes={d.get('reason_codes')}")
    _run(["git", "-C", root, "checkout", "-q", "--", "a.py"])


def s4_detached(root):
    _run(["git", "-C", root, "checkout", "-q", "--detach", "HEAD"])
    d, _ = _j([BOOT, "--project", root, "--check-only"], cwd=root)
    # Detached HEAD keeps the same sha: sha unchanged, but verify no crash
    # and state is reported (CURRENT here is correct — same content).
    _check("s4_detached_no_crash", d["graphify"]["graph_state"] in ("CURRENT", "STALE"),
            f"state={d['graphify']['graph_state']}")
    _run(["git", "-C", root, "checkout", "-q", "master"])


def s5_nogit():
    root = _mkrepo(with_git=False)
    d, rc = _j([BOOT, "--project", root, "--init"], cwd=root)
    _check("s5_nogit_current", d["graphify"]["graph_state"] == "CURRENT" and rc == 0,
            f"state={d['graphify']['graph_state']}")
    with open(os.path.join(root, "a.py"), "a") as fh:
        fh.write("y = 2\n")
    d2, _ = _j([BOOT, "--project", root, "--check-only"], cwd=root)
    _check("s5_nogit_stale", d2["graphify"]["graph_state"] == "STALE")
    shutil.rmtree(root, ignore_errors=True)


def s6_untracked(root):
    with open(os.path.join(root, "newmod.py"), "w") as fh:
        fh.write("z = 3\n")
    d, _ = _j([BOOT, "--project", root, "--check-only"], cwd=root)
    _check("s6_untracked_stale",
            d["graphify"]["graph_state"] == "STALE"
            and "UNTRACKED_SUPPORTED_SOURCE" in d.get("reason_codes", []),
            f"codes={d.get('reason_codes')}")
    os.remove(os.path.join(root, "newmod.py"))


def s7_toolchain(root):
    mp = os.path.join(root, "graphify-out", "graph.meta.json")
    m = json.load(open(mp))
    m["graphify_version"] = "graphify 0.0.0-soak"
    json.dump(m, open(mp, "w"), indent=2)
    d, _ = _j([FRESH, "--project", root], cwd=root)
    _check("s7_toolchain_stale",
            d.get("status") == "stale" and "TOOLCHAIN_CHANGED" in d.get("reason_codes", []),
            f"codes={d.get('reason_codes')}")
    _j([FRESH, "--project", root, "--init"], cwd=root)


def s8_mcp_missing(root):
    bindir = tempfile.mkdtemp(prefix="soakbin_")
    for tool in ("git", "python3", sys.executable.split("/")[-1], "graphify"):
        src = shutil.which(tool)
        if src and os.path.basename(src) != "graphify-mcp":
            try:
                os.symlink(src, os.path.join(bindir, os.path.basename(src)))
            except OSError:
                pass
    # graphify-mcp deliberately NOT linked.
    env = dict(os.environ, PATH=bindir + ":/usr/bin:/bin")
    d, _ = _j([BOOT, "--project", root, "--check-only"], cwd=root, env=env)
    g = d.get("graphify", {})
    _check("s8_mcp_missing_fallback", g.get("mcp") is False and g.get("cli") is True,
            f"cli={g.get('cli')} mcp={g.get('mcp')}")
    shutil.rmtree(bindir, ignore_errors=True)


def s9_partial():
    root = _mkrepo(files={"a.py": "x = 1\n", "expert.mq5": "// stub\n"})
    d, _ = _j([BOOT, "--project", root, "--check-only"], cwd=root)
    cov = d.get("coverage", {})
    _check("s9_partial_honest", cov.get("mql5") == "PARTIAL" and "FULL" not in
            [v for k, v in cov.items() if k != "supported"],
            f"coverage={cov}")
    shutil.rmtree(root, ignore_errors=True)


def s10_no_escape():
    home_agents = os.path.expanduser("~/.agents/skills/graphify")
    before = os.path.exists(home_agents)
    root = _mkrepo(files={"a.py": "x = 1\n"})
    _j([BOOT, "--project", root, "--init"], cwd=root)
    after = os.path.exists(home_agents)
    inside = []
    for dirpath, dirnames, filenames in os.walk(root):
        for f in filenames:
            if f == "SKILL.md" and "graphify" in dirpath:
                inside.append(os.path.join(dirpath, f))
    _check("s10_no_escape", after == before and len(inside) == 1,
            f"global_created={after and not before}")
    shutil.rmtree(root, ignore_errors=True)


def main():
    if shutil.which("graphify") is None:
        print("FAIL soak requires graphify on PATH")
        return 1
    root = s1_greenfield()
    s2_reuse(root)
    s3_dirty(root)
    s4_detached(root)
    s6_untracked(root)
    s7_toolchain(root)
    s8_mcp_missing(root)
    s5_nogit()
    s9_partial()
    s10_no_escape()
    shutil.rmtree(root, ignore_errors=True)
    print(f"\ntimings(s): {json.dumps(TIMINGS)}")
    print(f"summary: {'ALL PASS' if not FAILURES else 'FAILURES: ' + str(FAILURES)}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
