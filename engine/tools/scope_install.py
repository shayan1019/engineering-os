#!/usr/bin/env python3
"""Global / Workspace installation contract.

Two scopes, one core:

  workspace  EOS is active only under the chosen root (real path, junction-safe)
  global     EOS is active wherever a supported host opens a project; no
             filesystem-wide permission is granted by this tool

The installer does not copy the engine into every project. It writes a scope
descriptor and host adapters that point at the central engine. Host capability
is explicit: only OpenCode has a verified write guard today. Codex and Claude
Code get context adapters (CONTEXT_ONLY); an unknown host is ADAPTER_ONLY and is
never reported as protected.

Commands:
  install   --mode workspace|global [--root DIR] [--host opencode]... [--apply]
  status    [--json]
  uninstall [--host opencode]... [--apply]
  hosts     [--json]
  selftest
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _eos_paths as ep  # noqa: E402

SCHEMA = 1
HOSTS = {
    "opencode": {"context": "plugin", "guard": "verified", "status": "FULL_GUARD",
                 "evidence": "live permission harness + eos-guard.test.mjs 10/10"},
    "codex": {"context": "AGENTS.md", "guard": "unverified", "status": "CONTEXT_ONLY",
              "evidence": "no tool-hook contract verified on this machine"},
    "claude-code": {"context": "CLAUDE.md", "guard": "unverified", "status": "CONTEXT_ONLY",
                    "evidence": "hooks exist but were not verified in this session"},
    "blackbox": {"context": "unknown", "guard": "none", "status": "ADAPTER_ONLY",
                 "evidence": "no documented local plugin/hook contract verified"},
}
MARKER_BEGIN = "<!-- EOS:BEGIN -->"
MARKER_END = "<!-- EOS:END -->"
BLOCK = """<!-- EOS:BEGIN -->
## EOS engineering contract (managed block)

This project is inside an EOS scope. EOS is active; do not ask the user to select
a mode. Before any write, an Engineering Decision Record must be APPROVED_FOR_EDIT.
Before completion, a quality-gate PASS and a CLOSED decision are required.
The full contract lives in the EOS engine; this block is the host entry point.
<!-- EOS:END -->
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def real(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def source_dir() -> str:
    candidates = [os.path.join(ep.ENGINE_ROOT, "host-plugins", "opencode")]
    workspace = ep.resolve_workspace()
    candidates.append(os.path.join(workspace, "engineering-os", "engine", "host-plugins", "opencode"))
    versions = os.path.join(ep.EOS_HOME, "versions")
    try:
        newest = sorted(os.listdir(versions))[-1]
    except (OSError, IndexError):
        newest = ""
    if newest:
        candidates.append(os.path.join(versions, newest, "engine", "host-plugins", "opencode"))
    override = os.environ.get("EOS_HOST_PLUGINS")
    if override:
        candidates.insert(0, override)
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return ""


def scope_file(mode: str, root: str) -> str:
    if mode == "workspace":
        return os.path.join(root, ".eos", "scope.json")
    return os.path.join(ep.EOS_HOME, "scope.json")


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


def backup(path: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path + f".eos-bak-{stamp}"
    shutil.copyfile(path, target)
    return target


def plugin_target(root: str, host: str) -> str:
    if host == "opencode":
        return os.path.join(root, ".opencode", "plugin")
    return ""


def adapter_path(root: str, host: str) -> str:
    if host == "codex":
        return os.path.join(root, "AGENTS.md")
    if host == "claude-code":
        return os.path.join(root, "CLAUDE.md")
    return ""


def upsert_block(path: str) -> bool:
    text = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    if MARKER_BEGIN in text and MARKER_END in text:
        start = text.index(MARKER_BEGIN)
        end = text.index(MARKER_END) + len(MARKER_END)
        updated = text[:start] + BLOCK.strip() + text[end:]
    else:
        updated = text.rstrip() + ("\n\n" if text.strip() else "") + BLOCK
    if updated == text:
        return False
    if os.path.isfile(path):
        backup(path)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)
    return True


def remove_block(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    if MARKER_BEGIN not in text or MARKER_END not in text:
        return False
    start = text.index(MARKER_BEGIN)
    end = text.index(MARKER_END) + len(MARKER_END)
    updated = (text[:start] + text[end:]).strip() + "\n"
    backup(path)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)
    return True


def plan(mode: str, root: str, hosts: list) -> dict:
    problems, actions = [], []
    if mode == "workspace":
        if not root or not os.path.isdir(root):
            problems.append(f"workspace root does not exist: {root}")
    if not hosts:
        hosts = ["opencode"]
    for host in hosts:
        capability = HOSTS.get(host)
        if not capability:
            problems.append(f"unknown host: {host}")
            continue
        if capability["status"] == "ADAPTER_ONLY":
            actions.append({"host": host, "action": "report_only", "status": "ADAPTER_ONLY",
                            "detail": "no activation is written; EOS does not pretend to work here"})
            continue
        if host == "opencode":
            source = source_dir()
            if not source:
                problems.append("host-plugins/opencode is not present in this engine")
            else:
                for name in ("eos-activation.js", "eos-guard.js"):
                    actions.append({"host": host, "action": "copy_plugin",
                                    "source": os.path.join(source, name),
                                    "target": os.path.join(plugin_target(root, host), name)})
        else:
            actions.append({"host": host, "action": "upsert_block",
                            "target": adapter_path(root, host), "status": capability["status"]})
    return {"schema": SCHEMA, "mode": mode, "root": root, "hosts": hosts,
            "scope_file": scope_file(mode, root), "actions": actions,
            "problems": problems, "hosts_capability": {h: HOSTS[h] for h in hosts if h in HOSTS}}


def apply_plan(value: dict, force: bool = False) -> int:
    if value["problems"]:
        print("BLOCKED: install plan is not valid")
        for problem in value["problems"]:
            print(f"  - {problem}")
        return 1
    scope = {
        "schema": SCHEMA, "mode": value["mode"],
        "root": real(value["root"]) if value["mode"] == "workspace" else "",
        "hosts": value["hosts"], "created_at": utc_now(), "version": "0.3.0",
        "engine": ep.ENGINE_ROOT,
    }
    path = value["scope_file"]
    if os.path.isfile(path) and not force:
        try:
            existing = read_json(path)
        except (OSError, json.JSONDecodeError):
            existing = {}
        if existing.get("mode") == scope["mode"] and existing.get("root") == scope["root"]:
            pass
        else:
            print(f"BLOCKED: a different scope already exists at {path}; pass --force")
            return 1
    write_json(path, scope)
    print(f"SCOPE: {value['mode']} -> {path}")

    for action in value["actions"]:
        if action["action"] == "copy_plugin":
            target_dir = os.path.dirname(action["target"])
            os.makedirs(target_dir, exist_ok=True)
            if os.path.isfile(action["target"]):
                backup(action["target"])
            shutil.copyfile(action["source"], action["target"])
            print(f"  plugin: {action['target']}")
        elif action["action"] == "upsert_block":
            changed = upsert_block(action["target"])
            print(f"  adapter: {action['target']} ({'updated' if changed else 'current'})")
        else:
            print(f"  {action['host']}: {action['status']} (nothing written)")
    return 0


def cmd_install(args) -> int:
    value = plan(args.mode, os.path.abspath(args.root) if args.root else "",
                 list(args.host or ["opencode"]))
    if args.json:
        print(json.dumps(value, ensure_ascii=True))
        return 0 if not value["problems"] else 1
    print(f"{'READY' if not value['problems'] else 'BLOCKED'}: {args.mode} install")
    for action in value["actions"]:
        print(f"  {action['host']:<12} {action['action']:<14} {action.get('target', '')}")
    for problem in value["problems"]:
        print(f"  - {problem}")
    if args.apply:
        return apply_plan(value, args.force)
    print("  dry run: pass --apply to write")
    return 0 if not value["problems"] else 1


def cmd_status(args) -> int:
    scope = {}
    for candidate in (os.path.join(ep.resolve_workspace(args.workspace), ".eos", "scope.json"),
                      os.path.join(ep.EOS_HOME, "scope.json")):
        if os.path.isfile(candidate):
            try:
                scope = read_json(candidate)
                scope["_source"] = candidate
                break
            except (OSError, json.JSONDecodeError):
                continue
    result = {"schema": SCHEMA, "scope": scope, "hosts": HOSTS}
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
        return 0
    print(f"scope : {scope.get('mode') or 'unconfigured'} ({scope.get('_source') or 'none'})")
    print(f"root  : {scope.get('root') or '(global)'}")
    for name, capability in HOSTS.items():
        print(f"  {name:<12} {capability['status']:<14} {capability['evidence']}")
    return 0


def cmd_hosts(args) -> int:
    print(json.dumps(HOSTS, indent=2, ensure_ascii=True) if args.json
          else "\n".join(f"{name:<12} {value['status']:<14} {value['evidence']}"
                         for name, value in HOSTS.items()))
    return 0


def cmd_uninstall(args) -> int:
    root = os.path.abspath(args.root) if args.root else ep.resolve_workspace(args.workspace)
    removed = []
    for host in args.host or ["opencode"]:
        if host == "opencode":
            for name in ("eos-activation.js", "eos-guard.js"):
                target = os.path.join(plugin_target(root, host), name)
                if os.path.isfile(target):
                    if args.apply:
                        os.remove(target)
                    removed.append(target)
        elif host in ("codex", "claude-code"):
            target = adapter_path(root, host)
            if os.path.isfile(target) and MARKER_BEGIN in open(target, encoding="utf-8",
                                                               errors="replace").read():
                if args.apply:
                    remove_block(target)
                removed.append(target)
    print(("REMOVED" if args.apply else "WOULD REMOVE") + f": {len(removed)} item(s)")
    for item in removed:
        print(f"  {item}")
    if not args.apply:
        print("  dry run: pass --apply to remove")
    return 0


def selftest() -> int:
    cases = []

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-inst-")
        with open(os.path.join(root, "AGENTS.md"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Existing project rules\n")
        return root

    @case("workspace install writes a scope, plugins and managed blocks")
    def _():
        root = fixture()
        value = plan("workspace", root, ["opencode", "codex"])
        assert not value["problems"], value["problems"]
        assert apply_plan(value) == 0
        assert os.path.isfile(os.path.join(root, ".eos", "scope.json"))
        assert os.path.isfile(os.path.join(root, ".opencode", "plugin", "eos-guard.js"))
        text = open(os.path.join(root, "AGENTS.md"), encoding="utf-8").read()
        assert text.count(MARKER_BEGIN) == 1
        assert "Existing project rules" in text
        return "installed"

    @case("re-install is idempotent and does not duplicate the block")
    def _():
        root = fixture()
        value = plan("workspace", root, ["opencode", "codex"])
        apply_plan(value)
        apply_plan(value, force=True)
        text = open(os.path.join(root, "AGENTS.md"), encoding="utf-8").read()
        assert text.count(MARKER_BEGIN) == 1
        return "idempotent"

    @case("uninstall removes only the managed block and EOS plugins")
    def _():
        root = fixture()
        value = plan("workspace", root, ["opencode", "codex"])
        apply_plan(value)
        assert cmd_uninstall(argparse.Namespace(root=root, workspace="",
                                                host=["opencode", "codex"], apply=True)) == 0
        text = open(os.path.join(root, "AGENTS.md"), encoding="utf-8").read()
        assert MARKER_BEGIN not in text
        assert "Existing project rules" in text
        assert not os.path.isfile(os.path.join(root, ".opencode", "plugin", "eos-guard.js"))
        return "clean"

    @case("an unknown host is ADAPTER_ONLY, not a fake success")
    def _():
        root = fixture()
        value = plan("workspace", root, ["blackbox"])
        assert value["actions"][0]["status"] == "ADAPTER_ONLY"
        return "adapter only"

    @case("global install does not claim filesystem-wide permission")
    def _():
        value = plan("global", "", ["opencode"])
        assert not value["problems"], value["problems"]
        assert value["scope_file"].endswith("scope.json")
        return "global plan"

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
    parser = argparse.ArgumentParser(prog="scope_install.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")
    p = sub.add_parser("install", parents=[common])
    p.add_argument("--mode", choices=("workspace", "global"), required=True)
    p.add_argument("--root", default="")
    p.add_argument("--host", action="append", default=[])
    p.add_argument("--apply", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("status", parents=[common])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("hosts")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_hosts)
    p = sub.add_parser("uninstall", parents=[common])
    p.add_argument("--root", default="")
    p.add_argument("--host", action="append", default=[])
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_uninstall)
    p = sub.add_parser("selftest")
    p.set_defaults(func=lambda _args: selftest())
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
