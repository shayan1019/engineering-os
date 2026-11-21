#!/usr/bin/env python3
"""Safe Git delivery for an approved, verified change.

`ship` is the only tool allowed to create a commit and push. It refuses unless:

  * a CLOSED decision exists for the active project
  * its gate artifact is a PASS and still hashes the same
  * every file is inside the decision scope and inside one Git repository
  * the branch matches `eos/<task-id>` and is not main/master
  * the staged set is exactly the decision's files (no unrelated user work)

It never force-pushes, never rewrites history, never pushes to main/master and
never deletes a branch. A push failure after a commit is reported with the
commit SHA; the commit is not reset.

Commands:
  plan  --project <id> --decision <edr> --branch eos/<task> [--workspace DIR]
  apply --project <id> --decision <edr> --branch eos/<task> [--push] [--message TEXT]
  selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402
import decision as edr  # noqa: E402

SCHEMA = 1
PROTECTED = {"main", "master", "trunk", "develop", "release"}
BRANCH_RE = re.compile(r"^eos/[A-Za-z0-9][A-Za-z0-9._-]{1,80}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def git(repo: str, args: list, timeout: int = 60) -> subprocess.CompletedProcess:
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME",
                                      "USERPROFILE", "APPDATA", "LOCALAPPDATA")
           if k in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=True, timeout=timeout, env=env)


def git_root(path: str) -> str:
    current = os.path.abspath(path)
    if os.path.isfile(current):
        current = os.path.dirname(current)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent


def decision_files(workspace: str, record: dict) -> tuple:
    files, problems = [], []
    for item in (record.get("change") or {}).get("files") or []:
        absolute = ep.load_stored_path(item, workspace)
        if not os.path.isfile(absolute):
            problems.append(f"changed file is missing: {item}")
            continue
        if not edr.covers(edr.scope_list(record), ep.store_path(absolute, workspace)):
            problems.append(f"changed file is outside the decision scope: {item}")
            continue
        files.append(absolute)
    if not files:
        problems.append("the decision records no changed file")
    return files, problems


def repo_for(files: list) -> tuple:
    roots = {git_root(path) for path in files}
    roots.discard("")
    if not roots:
        return "", "no Git repository contains the changed files"
    if len(roots) > 1:
        return "", "changed files span more than one Git repository: " + ", ".join(sorted(roots))
    return roots.pop(), ""


def build_plan(workspace: str, project: str, decision_id: str, branch: str) -> dict:
    problems = []
    if not BRANCH_RE.match(branch or ""):
        problems.append(f"branch must match eos/<task-id>: {branch!r}")
    if branch in PROTECTED:
        problems.append(f"refusing protected branch: {branch}")
    try:
        record = edr.load(workspace, project, decision_id)
    except edr.DecisionError as exc:
        return {"schema": SCHEMA, "verdict": "BLOCKED", "problems": [str(exc)], "files": []}
    problems.extend(edr.integrity(record))
    if record.get("state") != "CLOSED":
        problems.append(f"decision is {record.get('state')}, not CLOSED")
    gate = record.get("gate") or {}
    if not gate.get("ok") or gate.get("verdict") != "PASS":
        problems.append("decision has no verified PASS gate")
    gate_path = ep.load_stored_path(gate.get("path", ""), workspace)
    if not gate.get("path") or not os.path.isfile(gate_path):
        problems.append("gate artifact is missing")
    elif edr.sha256_file(gate_path) != gate.get("sha256"):
        problems.append("gate artifact changed after verification")

    files, file_problems = decision_files(workspace, record)
    problems.extend(file_problems)
    repo, repo_problem = repo_for(files)
    if repo_problem:
        problems.append(repo_problem)

    status = []
    if repo:
        result = git(repo, ["status", "--porcelain=v1"])
        if result.returncode != 0:
            problems.append(f"git status failed: {result.stderr.strip()[:300]}")
        else:
            status = [line for line in result.stdout.splitlines() if line.strip()]
        head = git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        exists = git(repo, ["rev-parse", "--verify", "--quiet", "refs/heads/" + branch]).returncode == 0
        if exists:
            problems.append(f"branch already exists: {branch}")
        remote = git(repo, ["remote", "get-url", "origin"]).stdout.strip()
    else:
        head, remote = "", ""

    return {
        "schema": SCHEMA, "verdict": "READY" if not problems else "BLOCKED",
        "project": project, "decision": decision_id, "branch": branch,
        "repo": repo, "head": head, "remote": remote,
        "gate": {"path": gate_path, "sha256": gate.get("sha256")},
        "files": [os.path.relpath(path, repo).replace("\\", "/") if repo else path
                  for path in files],
        "workspace_status": status,
        "problems": problems,
    }


TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")


def build_tag_plan(workspace: str, project: str, decision_id: str, tag: str) -> dict:
    problems = []
    if not TAG_RE.match(tag or ""):
        problems.append(f"tag is not a version: {tag!r}")
    try:
        record = edr.load(workspace, project, decision_id)
    except edr.DecisionError as exc:
        return {"schema": SCHEMA, "verdict": "BLOCKED", "problems": [str(exc)],
                "files": [], "repo": "", "head": "", "remote": ""}
    problems.extend(edr.integrity(record))
    if record.get("state") != "CLOSED":
        problems.append(f"decision is {record.get('state')}, not CLOSED")
    gate = record.get("gate") or {}
    if not gate.get("ok") or gate.get("verdict") != "PASS":
        problems.append("decision has no verified PASS gate")
    gate_path = ep.load_stored_path(gate.get("path", ""), workspace)
    if not gate.get("path") or not os.path.isfile(gate_path):
        problems.append("gate artifact is missing")
    elif edr.sha256_file(gate_path) != gate.get("sha256"):
        problems.append("gate artifact changed after verification")

    files, file_problems = decision_files(workspace, record)
    problems.extend(file_problems)
    repo, repo_problem = repo_for(files)
    if repo_problem:
        problems.append(repo_problem)

    head, remote = "", ""
    if repo:
        status = [line for line in git(repo, ["status", "--porcelain=v1"]).stdout.splitlines()
                  if line.strip() and not line.startswith("??")]
        if status:
            problems.append("tracked files are modified; commit the release first")
        head = git(repo, ["rev-parse", "HEAD"]).stdout.strip()
        if git(repo, ["rev-parse", "--verify", "--quiet", "refs/tags/" + tag]).returncode == 0:
            problems.append(f"tag already exists locally: {tag}")
        remote = git(repo, ["remote", "get-url", "origin"]).stdout.strip()
        if remote:
            found = git(repo, ["ls-remote", "--tags", "origin", "refs/tags/" + tag])
            if found.stdout.strip():
                problems.append(f"tag already exists on origin: {tag}")
    return {"schema": SCHEMA, "verdict": "READY" if not problems else "BLOCKED",
            "project": project, "decision": decision_id, "tag": tag, "repo": repo,
            "head": head, "remote": remote, "files": files, "problems": problems}


def cmd_tag(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    plan = build_tag_plan(workspace, args.project, args.decision, args.tag)
    if plan["verdict"] != "READY":
        print("BLOCKED: the tag plan is not ready")
        for problem in plan["problems"]:
            print(f"  - {problem}")
        return 1
    repo = plan["repo"]
    message = args.message or f"EOS {args.tag}"
    created = git(repo, ["tag", "-a", args.tag, "-m", message])
    if created.returncode != 0:
        print("BLOCKED: git tag failed")
        print((created.stdout + created.stderr).strip()[:800])
        return 1
    remote_sha = ""
    pushed = False
    if args.push:
        if not plan.get("remote"):
            print(f"BLOCKED: tag {args.tag} created locally, but no origin remote is configured")
            return 1
        push = git(repo, ["push", "origin", args.tag], timeout=180)
        if push.returncode != 0:
            print(f"BLOCKED: tag {args.tag} created locally, push failed")
            print((push.stdout + push.stderr).strip()[-500:])
            return 1
        remote = git(repo, ["ls-remote", "--tags", "origin"])
        for line in remote.stdout.splitlines():
            if not line.strip():
                continue
            sha, ref = line.split("\t", 1)
            if ref == f"refs/tags/{args.tag}^{{}}":
                remote_sha = sha
            elif not remote_sha and ref == f"refs/tags/{args.tag}":
                remote_sha = sha
        if remote_sha != plan["head"]:
            print(f"BLOCKED: remote tag SHA {remote_sha or '(missing)'} != local {plan['head']}")
            return 1
        pushed = True
    receipt = write_receipt(workspace, {
        "schema": SCHEMA, "kind": "ship_tag", "created_at": utc_now(),
        "project": args.project, "decision": args.decision, "tag": args.tag,
        "commit": plan["head"], "files": plan["files"], "pushed": pushed,
        "remote": plan.get("remote", ""), "remote_sha": remote_sha,
    })
    if args.json:
        print(json.dumps({"ok": True, "tag": args.tag, "commit": plan["head"],
                          "remote_sha": remote_sha, "receipt": receipt},
                         ensure_ascii=True))
    else:
        print(f"PASS: tag {args.tag} @ {plan['head'][:12]} (pushed={pushed})")
        print(f"  receipt: {ep.store_path(receipt, workspace)}")
    return 0


def cmd_plan(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    plan = build_plan(workspace, args.project, args.decision, args.branch)
    if args.json:
        print(json.dumps(plan, ensure_ascii=True))
    else:
        print(f"{plan['verdict']}: {plan.get('decision')} -> {plan.get('branch')}")
        if plan.get("repo"):
            print(f"  repo   : {plan['repo']}")
            print(f"  files  : {', '.join(plan['files'])}")
            print(f"  remote : {plan.get('remote') or '(none)'}")
        for problem in plan["problems"]:
            print(f"  - {problem}")
    return 0 if plan["verdict"] == "READY" else 1


def receipt_path(workspace: str, project: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(workspace, ".agents", "evidence", "git", project,
                        f"ship-{stamp}.json")


def write_receipt(workspace: str, value: dict) -> str:
    path = receipt_path(workspace, value["project"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    return path


def cmd_apply(args) -> int:
    workspace = ep.resolve_workspace(args.workspace)
    plan = build_plan(workspace, args.project, args.decision, args.branch)
    if plan["verdict"] != "READY":
        print("BLOCKED: the ship plan is not ready")
        for problem in plan["problems"]:
            print(f"  - {problem}")
        return 1
    repo = plan["repo"]
    files = plan["files"]

    checkout = git(repo, ["checkout", "-b", args.branch])
    if checkout.returncode != 0:
        print(f"BLOCKED: could not create branch {args.branch}")
        print(checkout.stderr.strip()[:500])
        return 1
    added = git(repo, ["add", "--", *files])
    if added.returncode != 0:
        print("BLOCKED: git add failed")
        print(added.stderr.strip()[:500])
        return 1
    staged = [line for line in git(repo, ["diff", "--cached", "--name-only"]).stdout.splitlines()
              if line.strip()]
    if sorted(staged) != sorted(files):
        print("BLOCKED: staged set is not exactly the decision files")
        print(f"  expected: {files}")
        print(f"  staged  : {staged}")
        return 1

    message = args.message or f"eos: {plan['decision']}"
    body = (f"EOS-Decision: {plan['decision']}\n"
            f"EOS-Gate: {os.path.basename(plan['gate']['path'])}\n"
            f"EOS-Branch: {args.branch}")
    commit = git(repo, ["commit", "-m", message, "-m", body])
    if commit.returncode != 0:
        print("BLOCKED: git commit failed")
        print((commit.stdout + commit.stderr).strip()[:800])
        return 1
    commit_sha = git(repo, ["rev-parse", "HEAD"]).stdout.strip()

    pushed = False
    push_output = ""
    remote_sha = ""
    if args.push:
        if not plan.get("remote"):
            print(f"BLOCKED: commit {commit_sha} created, but no origin remote is configured")
            write_receipt(workspace, {"schema": SCHEMA, "kind": "ship", "created_at": utc_now(),
                                      "project": args.project, "decision": args.decision,
                                      "branch": args.branch, "commit": commit_sha,
                                      "files": files, "pushed": False,
                                      "problems": ["push requested but no origin remote"]})
            return 1
        push = git(repo, ["push", "-u", "origin", args.branch], timeout=180)
        push_output = (push.stdout + push.stderr).strip()[-1000:]
        if push.returncode != 0:
            print(f"BLOCKED: commit {commit_sha} created, push failed")
            print(push_output[-500:])
            write_receipt(workspace, {"schema": SCHEMA, "kind": "ship", "created_at": utc_now(),
                                      "project": args.project, "decision": args.decision,
                                      "branch": args.branch, "commit": commit_sha,
                                      "files": files, "pushed": False,
                                      "problems": [f"push failed: {push_output[-500:]}"]})
            return 1
        remote = git(repo, ["ls-remote", "origin", f"refs/heads/{args.branch}"])
        remote_sha = remote.stdout.strip().split()[0] if remote.stdout.strip() else ""
        if remote_sha != commit_sha:
            print(f"BLOCKED: push returned 0, but remote SHA {remote_sha or '(missing)'} "
                  f"!= local {commit_sha}")
            write_receipt(workspace, {"schema": SCHEMA, "kind": "ship", "created_at": utc_now(),
                                      "project": args.project, "decision": args.decision,
                                      "branch": args.branch, "commit": commit_sha,
                                      "remote_sha": remote_sha, "files": files, "pushed": False,
                                      "problems": ["remote SHA does not match the commit"]})
            return 1
        pushed = True

    receipt = write_receipt(workspace, {
        "schema": SCHEMA, "kind": "ship", "created_at": utc_now(),
        "project": args.project, "decision": args.decision, "branch": args.branch,
        "commit": commit_sha, "files": files, "pushed": pushed,
        "remote": plan.get("remote", ""), "remote_sha": remote_sha,
        "push_output": push_output,
        "gate": plan["gate"], "problems": [],
    })
    print(f"PASS: {args.branch} @ {commit_sha[:12]} ({len(files)} file(s), pushed={pushed})")
    print(f"  receipt: {ep.store_path(receipt, workspace)}")
    return 0


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-ship-")
        os.makedirs(os.path.join(root, ".agents", "evidence"), exist_ok=True)
        git(root, ["init", "-q"])
        git(root, ["config", "user.email", "eos@example.invalid"])
        git(root, ["config", "user.name", "EOS Test"])
        open(os.path.join(root, "README.md"), "w").write("# fixture\n")
        git(root, ["add", "README.md"])
        assert git(root, ["commit", "-q", "-m", "init"]).returncode == 0
        return root

    def closed(root, path="src/app.py") -> str:
        os.makedirs(os.path.join(root, "src"), exist_ok=True)
        with open(os.path.join(root, path), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("print('ok')\n")
        args = argparse.Namespace(project="eos", task="ship it", risk="ATOMIC",
                                  scope=[path], suite="unit", actor="eos.test",
                                  approve=True, workspace=root)
        assert edr.cmd_open(args) == 0
        decision_id = edr.latest_decisions(root, "eos")[0]["id"]
        record = edr.load(root, "eos", decision_id)
        record["change"] = {"files": [path], "diff_sha256": ""}
        edr.save(root, record)
        assert edr.transition(root, record, "IMPLEMENTED", "implementer") == 0
        gate = edr._fixture_gate(root)
        assert edr.cmd_verify(argparse.Namespace(project="eos", id=decision_id, gate=gate,
                                                 max_age_min=None, actor="verifier",
                                                 workspace=root)) == 0
        record = edr.load(root, "eos", decision_id)
        assert edr.transition(root, record, "CLOSED", "verifier", "done") == 0
        return decision_id

    @case("protected branches are refused")
    def _():
        root = fixture()
        decision_id = closed(root)
        plan = build_plan(root, "eos", decision_id, "main")
        assert plan["verdict"] == "BLOCKED"
        assert any("protected" in problem for problem in plan["problems"])
        return "main refused"

    @case("a branch outside eos/<task> is refused")
    def _():
        root = fixture()
        decision_id = closed(root)
        plan = build_plan(root, "eos", decision_id, "feature/x")
        assert plan["verdict"] == "BLOCKED"
        return "branch pattern"

    @case("apply commits exactly the decision files")
    def _():
        root = fixture()
        decision_id = closed(root)
        open(os.path.join(root, "unrelated.txt"), "w").write("do not commit\n")
        args = argparse.Namespace(project="eos", decision=decision_id, branch="eos/t1",
                                  message="test", push=False, workspace=root)
        assert cmd_apply(args) == 0
        show = git(root, ["show", "--name-only", "--format=", "HEAD"]).stdout.split()
        assert show == ["src/app.py"], show
        assert "unrelated.txt" not in show
        return "scoped commit"

    @case("push goes to the configured remote branch")
    def _():
        root = fixture()
        remote = tempfile.mkdtemp(prefix="eos-remote-")
        git(remote, ["init", "--bare", "-q"])
        git(root, ["remote", "add", "origin", remote])
        decision_id = closed(root)
        args = argparse.Namespace(project="eos", decision=decision_id, branch="eos/t2",
                                  message="test", push=True, workspace=root)
        assert cmd_apply(args) == 0
        remote_sha = git(remote, ["rev-parse", "eos/t2"]).stdout.strip()
        local_sha = git(root, ["rev-parse", "HEAD"]).stdout.strip()
        assert remote_sha == local_sha
        return "pushed"

    @case("a release tag points at the pushed commit")
    def _():
        root = fixture()
        remote = tempfile.mkdtemp(prefix="eos-remote-tag-")
        git(remote, ["init", "--bare", "-q"])
        git(root, ["remote", "add", "origin", remote])
        decision_id = closed(root)
        apply_args = argparse.Namespace(project="eos", decision=decision_id,
                                        branch="eos/t3", message="test", push=True,
                                        workspace=root)
        assert cmd_apply(apply_args) == 0
        tag_args = argparse.Namespace(project="eos", decision=decision_id,
                                      tag="v1.0.0", message="release", push=True,
                                      json=False, workspace=root)
        assert cmd_tag(tag_args) == 0
        local_sha = git(root, ["rev-parse", "HEAD"]).stdout.strip()
        remote_sha = git(remote, ["rev-parse", "refs/tags/v1.0.0^{}"]).stdout.strip()
        assert remote_sha == local_sha, (remote_sha, local_sha)
        return "tag pushed"

    @case("an unclosed decision cannot ship")
    def _():
        root = fixture()
        os.makedirs(os.path.join(root, "src"), exist_ok=True)
        open(os.path.join(root, "src", "x.py"), "w").write("x\n")
        args = argparse.Namespace(project="eos", task="x", risk="ATOMIC",
                                  scope=["src/x.py"], suite="unit", actor="eos.test",
                                  approve=False, workspace=root)
        edr.cmd_open(args)
        decision_id = edr.latest_decisions(root, "eos")[0]["id"]
        plan = build_plan(root, "eos", decision_id, "eos/t3")
        assert plan["verdict"] == "BLOCKED"
        return "not closed"

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
    parser = argparse.ArgumentParser(prog="ship.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")
    common.add_argument("--project", default="eos")
    p = sub.add_parser("plan", parents=[common])
    p.add_argument("--decision", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("apply", parents=[common])
    p.add_argument("--decision", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--message", default="")
    p.add_argument("--push", action="store_true")
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser("tag", parents=[common])
    p.add_argument("--decision", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--message", default="")
    p.add_argument("--push", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tag)
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
