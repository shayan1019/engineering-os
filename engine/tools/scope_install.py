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
    # The activated version, never "the newest directory": a rolled-back version
    # can still be on disk.
    try:
        active = str(read_json(os.path.join(ep.EOS_HOME, "config.json"))["active_version"] or "")
    except (OSError, ValueError, KeyError, TypeError):
        active = ""
    if active:
        candidates.append(os.path.join(ep.EOS_HOME, "versions", active, "engine",
                                       "host-plugins", "opencode"))
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
    tmp = ep.temp_name(path)
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    ep.replace(tmp, path)


def backup(path: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path + f".eos-bak-{stamp}"
    shutil.copyfile(path, target)
    return target


def global_root() -> str:
    """The directory OpenCode actually loads global plugins from on this OS.

    Explicit OPENCODE_CONFIG_DIR wins; otherwise the standard user config path
    is used. Never a path relative to the current working directory.
    """
    explicit = os.environ.get("OPENCODE_CONFIG_DIR")
    if explicit:
        return os.path.abspath(explicit)
    return os.path.join(os.path.expanduser("~"), ".config", "opencode")


def product_version() -> str:
    path = os.path.join(os.path.dirname(ep.ENGINE_ROOT), "VERSION")
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip() or "0.0.0-unknown"
    except OSError:
        return "0.0.0-unknown"


def engine_commit() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "-C", os.path.dirname(ep.ENGINE_ROOT),
                               "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def plugin_target(root: str, host: str, mode: str = "workspace") -> str:
    if host != "opencode":
        return ""
    if mode == "global":
        return os.path.join(root, "plugins")
    return os.path.join(root, ".opencode", "plugins")


def legacy_plugin_target(root: str, host: str, mode: str = "workspace") -> str:
    """V1 loaded `plugin/` (singular). Keep it working for existing installs."""
    if host != "opencode":
        return ""
    if mode == "global":
        return os.path.join(root, "plugin")
    return os.path.join(root, ".opencode", "plugin")


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
                # Only the canonical directory is a live host surface. Copying
                # the same plugin into both `plugins/` and `plugin/` lets a host
                # that scans both register two guards and two activations.
                canonical = plugin_target(root, host, mode)
                for name in ("eos-activation.js", "eos-guard.js"):
                    actions.append({"host": host, "action": "copy_plugin",
                                    "source": os.path.join(source, name),
                                    "target": os.path.join(canonical, name)})
                legacy = legacy_plugin_target(root, host, mode)
                if legacy and legacy != canonical:
                    for name in ("eos-activation.js", "eos-guard.js"):
                        target = os.path.join(legacy, name)
                        if os.path.isfile(target):
                            actions.append({"host": host, "action": "retire_legacy_plugin",
                                            "target": target,
                                            "source": os.path.join(source, name)})
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
        "hosts": value["hosts"], "created_at": utc_now(),
        "version": product_version(), "engine_commit": engine_commit(),
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
                # The host loads every .js file in this directory. A backup must
                # live outside it or the replacement would be a second plugin.
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_root = os.path.join(value["root"], ".eos", "backups",
                                           f"plugins-{stamp}")
                os.makedirs(backup_root, exist_ok=True)
                shutil.copyfile(action["target"],
                                os.path.join(backup_root,
                                             os.path.basename(action["target"])))
            shutil.copyfile(action["source"], action["target"])
            print(f"  plugin: {action['target']}")
        elif action["action"] == "retire_legacy_plugin":
            target = action["target"]
            if not os.path.isfile(target):
                continue
            # A backup inside the scanned plugin directory would itself be
            # loaded as a second plugin. Move it to scope-owned user data.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_root = os.path.join(value["root"], ".eos", "backups",
                                       f"legacy-plugins-{stamp}")
            os.makedirs(backup_root, exist_ok=True)
            destination = os.path.join(backup_root, os.path.basename(target))
            shutil.move(target, destination)
            try:
                os.rmdir(os.path.dirname(target))
            except OSError:
                pass
            print(f"  legacy: {target} -> {destination}")
        elif action["action"] == "upsert_block":
            changed = upsert_block(action["target"])
            print(f"  adapter: {action['target']} ({'updated' if changed else 'current'})")
        else:
            print(f"  {action['host']}: {action['status']} (nothing written)")
    return 0


def cmd_install(args) -> int:
    root = os.path.abspath(args.root) if args.root else (
        global_root() if args.mode == "global" else "")
    value = plan(args.mode, root, list(args.host or ["opencode"]))
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
    workspace_root = os.path.abspath(args.root) if args.root else ep.resolve_workspace(args.workspace)
    # The global scope lives under the host's config directory, never under the
    # workspace the command happens to run in.
    config_root = global_root()
    removed = []
    for host in args.host or ["opencode"]:
        if host == "opencode":
            directories = []
            for directory in (plugin_target(workspace_root, host, "workspace"),
                              legacy_plugin_target(workspace_root, host, "workspace"),
                              plugin_target(config_root, host, "global"),
                              legacy_plugin_target(config_root, host, "global")):
                if directory and directory not in directories:
                    directories.append(directory)
            for directory in directories:
                for name in ("eos-activation.js", "eos-guard.js"):
                    target = os.path.join(directory, name)
                    if os.path.isfile(target):
                        if args.apply:
                            os.remove(target)
                        removed.append(target)
        elif host in ("codex", "claude-code"):
            target = adapter_path(workspace_root, host)
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
    config_home = tempfile.mkdtemp(prefix="eos-opencode-config-")
    previous_config = os.environ.get("OPENCODE_CONFIG_DIR")
    os.environ["OPENCODE_CONFIG_DIR"] = config_home

    def case(name):
        def wrap(fn):
            cases.append((name, fn))
            return fn
        return wrap

    def fixture() -> str:
        root = tempfile.mkdtemp(prefix="eos-inst-")
        with open(os.path.join(root, "AGENTS.md"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Existing project rules\n")
        # A real project marker. Without one the guard cannot infer a project
        # and a downstream live host session is blocked for the wrong reason.
        with open(os.path.join(root, "package.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write('{"name": "eos-install-fixture", "version": "1.0.0"}\n')
        return root

    def legacy_backups(root: str) -> list:
        base = os.path.join(root, ".eos", "backups")
        found = []
        for current, _dirs, files in os.walk(base):
            for name in files:
                found.append(os.path.join(current, name))
        return found

    @case("workspace install writes a scope, canonical plugins and managed blocks")
    def _():
        root = fixture()
        value = plan("workspace", root, ["opencode", "codex"])
        assert not value["problems"], value["problems"]
        assert apply_plan(value) == 0
        assert os.path.isfile(os.path.join(root, ".eos", "scope.json"))
        assert os.path.isfile(os.path.join(root, ".opencode", "plugins", "eos-guard.js"))
        assert not os.path.isdir(os.path.join(root, ".opencode", "plugin"))
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
        # The host loads every .js file in this directory. A replacement backup
        # left here would be a second guard/activation, not a backup.
        assert sorted(os.listdir(os.path.join(root, ".opencode", "plugins"))) == [
            "eos-activation.js", "eos-guard.js"]
        return "idempotent"

    @case("install migrates the legacy plugin directory instead of duplicating it")
    def _():
        root = fixture()
        legacy = os.path.join(root, ".opencode", "plugin")
        os.makedirs(legacy)
        with open(os.path.join(legacy, "eos-guard.js"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write("// previous version\n")
        value = plan("workspace", root, ["opencode"])
        assert not value["problems"], value["problems"]
        assert any(action["action"] == "retire_legacy_plugin" for action in value["actions"])
        assert apply_plan(value) == 0
        assert os.path.isfile(os.path.join(root, ".opencode", "plugins", "eos-guard.js"))
        assert not os.path.isfile(os.path.join(legacy, "eos-guard.js"))
        assert any(path.endswith("eos-guard.js") for path in legacy_backups(root))
        return "migrated"

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
        assert not os.path.isfile(os.path.join(root, ".opencode", "plugins", "eos-guard.js"))
        return "clean"

    @case("an unknown host is ADAPTER_ONLY, not a fake success")
    def _():
        root = fixture()
        value = plan("workspace", root, ["blackbox"])
        assert value["actions"][0]["status"] == "ADAPTER_ONLY"
        return "adapter only"

    @case("global install writes the host config and uninstall clears it")
    def _():
        root = fixture()
        home = tempfile.mkdtemp(prefix="eos-home-")
        previous_home = ep.EOS_HOME
        ep.EOS_HOME = home
        try:
            value = plan("global", global_root(), ["opencode"])
            assert not value["problems"], value["problems"]
            assert value["scope_file"] == os.path.join(home, "scope.json"), value["scope_file"]
            assert apply_plan(value) == 0
            installed = os.path.join(config_home, "plugins", "eos-guard.js")
            assert os.path.isfile(installed)
            assert not os.path.isdir(os.path.join(config_home, "plugin"))
            assert cmd_uninstall(argparse.Namespace(root=root, workspace="",
                                                    host=["opencode"], apply=True)) == 0
            assert not os.path.isfile(installed)
        finally:
            ep.EOS_HOME = previous_home
            shutil.rmtree(home, ignore_errors=True)
        return "global clean"

    @case("global install does not claim filesystem-wide permission")
    def _():
        value = plan("global", global_root(), ["opencode"])
        assert not value["problems"], value["problems"]
        assert value["scope_file"].endswith("scope.json")
        return "global plan"

    passed = failed = 0
    try:
        for name, fn in cases:
            try:
                detail = fn()
                passed += 1
                print(f"PASS  {name}  [{detail}]")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    finally:
        if previous_config is None:
            os.environ.pop("OPENCODE_CONFIG_DIR", None)
        else:
            os.environ["OPENCODE_CONFIG_DIR"] = previous_config
        shutil.rmtree(config_home, ignore_errors=True)
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
