"""Desktop, DevOps and multi-agent scenarios for the Engineering Awareness Benchmark."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .backend import _run_threads


def _load(tree: Path, name: str):
    path = tree / (name + ".py")
    spec = importlib.util.spec_from_file_location(f"bench_{name}_{abs(hash(str(tree)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _regression_isolated(tree: Path) -> list:
    """Run the visible tests in a copy so test artefacts cannot pollute scope."""
    work = Path(tempfile.mkdtemp(prefix="bench-reg-"))
    ignore = shutil.ignore_patterns(".agents", "__pycache__", ".git", ".pytest_cache")
    shutil.copytree(tree, work / "repo", ignore=ignore)
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                          cwd=str(work / "repo"), capture_output=True, text=True, timeout=120)
    shutil.rmtree(work, ignore_errors=True)
    return [{"name": "visible tests", "ok": proc.returncode == 0,
             "detail": (proc.stderr or proc.stdout).strip()[-400:]}]


def _agent(claims, approach, why, changed):
    return {
        "findings": [{"severity": "major", "claim": claim, "evidence": [changed[0]]}
                     for claim in claims],
        "decision": {"approach": approach, "alternatives": ["minimal patch", "redesign"],
                     "assumptions": ["the fixture is the whole system"], "why": why},
        "self_correction": [{"wrong": "first attempt missed the failure path",
                             "detected_by": "ran the failure case", "fix": "moved the change into the critical path",
                             "evidence": [changed[0]]}],
        "scope": {"changed_files": changed, "not_changed": []},
        "closure": {"tests": ["python -m unittest discover -s tests"], "residual_risks": []},
    }


BAD_AGENT = {
    "findings": [{"severity": "minor", "claim": "Applied a small change", "evidence": ["file"]}],
    "decision": {"approach": "Patched the happy path", "alternatives": [], "assumptions": [], "why": ""},
    "self_correction": [],
    "scope": {"changed_files": [], "not_changed": []},
    "closure": {"tests": [], "residual_risks": []},
}


# ---------------------------------------------------------------- desktop
SETTINGS_SOURCE = '''STATE = {"value": "default", "dirty": False}


def set_value(path, value):
    STATE["value"] = value
    STATE["dirty"] = True


def on_close(path):
    if STATE["dirty"]:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(STATE["value"])
        STATE["dirty"] = False
'''

SETTINGS_GOOD = '''import os

STATE = {"value": "default", "dirty": False}


def _write(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.replace(tmp, path)
    STATE["dirty"] = False


def set_value(path, value):
    STATE["value"] = value
    STATE["dirty"] = True
    _write(path, value)


def on_close(path):
    _write(path, STATE["value"])
'''

SETTINGS_TEST = '''import unittest
from settings import set_value, on_close


class SettingsTest(unittest.TestCase):
    def test_close_writes(self):
        set_value("settings.txt", "x")
        on_close("settings.txt")
        with open("settings.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "x")


if __name__ == "__main__":
    unittest.main()
'''


def _check_settings(tree: Path) -> list:
    checks = []
    module = _load(tree, "settings")
    work = Path(tempfile.mkdtemp(prefix="bench-settings-"))
    path = str(work / "settings.txt")
    module.set_value(path, "crash-safe")
    checks.append({"name": "a set survives without a clean close", "ok":
                   os.path.isfile(path) and open(path, encoding="utf-8").read() == "crash-safe",
                   "detail": "set_value must persist before the app can crash"})
    module.set_value(path, "atomic-safe")
    checks.append({"name": "write-through replaces the file atomically", "ok":
                   os.path.isfile(path) and open(path, encoding="utf-8").read() == "atomic-safe"
                   and not os.path.exists(path + ".tmp"),
                   "detail": "a second set must replace the file and leave no temp file"})
    shutil.rmtree(work, ignore_errors=True)
    return checks


# ---------------------------------------------------------------- mobile/offline
SYNC_SOURCE = '''QUEUE = []


def enqueue(record):
    QUEUE.append(record)


def sync(sender):
    batch = list(QUEUE)
    QUEUE.clear()
    for record in batch:
        sender(record)
'''

SYNC_GOOD = '''QUEUE = []


def enqueue(record):
    QUEUE.append(record)


def sync(sender):
    while QUEUE:
        record = QUEUE[0]
        sender(record)
        QUEUE.pop(0)
'''

SYNC_TEST = '''import unittest
from sync import QUEUE, enqueue, sync


class SyncTest(unittest.TestCase):
    def test_success(self):
        QUEUE.clear()
        enqueue(1)
        sync(lambda record: None)
        self.assertEqual(QUEUE, [])


if __name__ == "__main__":
    unittest.main()
'''


def _check_sync(tree: Path) -> list:
    checks = []
    module = _load(tree, "sync")
    module.QUEUE[:] = [1, 2, 3]

    def fail_second(record):
        if record == 2:
            raise RuntimeError("network down")

    try:
        module.sync(fail_second)
    except RuntimeError:
        pass
    checks.append({"name": "a failed batch is not lost", "ok":
                   module.QUEUE == [2, 3],
                   "detail": f"queue={module.QUEUE!r}"})
    sent = []
    module.sync(sent.append)
    checks.append({"name": "retry sends each remaining record once", "ok":
                   sent == [2, 3] and module.QUEUE == [],
                   "detail": f"sent={sent!r} queue={module.QUEUE!r}"})
    return checks


# ---------------------------------------------------------------- installer
INSTALLER_SOURCE = '''import os
import shutil


def upgrade(root, version, fail=False):
    with open(os.path.join(root, "VERSION"), "w", encoding="utf-8") as handle:
        handle.write(version)
    shutil.rmtree(os.path.join(root, "data"), ignore_errors=True)
    if fail:
        raise RuntimeError("upgrade failed")
'''

INSTALLER_GOOD = '''import os
import shutil


def upgrade(root, version, fail=False):
    data = os.path.join(root, "data")
    backup = os.path.join(root, "data.bak")
    if os.path.isdir(data):
        shutil.copytree(data, backup, dirs_exist_ok=True)
    try:
        with open(os.path.join(root, "VERSION"), "w", encoding="utf-8") as handle:
            handle.write(version)
        if fail:
            raise RuntimeError("upgrade failed")
    except Exception:
        if os.path.isdir(backup):
            shutil.copytree(backup, data, dirs_exist_ok=True)
            shutil.rmtree(backup, ignore_errors=True)
        raise
    if os.path.isdir(backup):
        shutil.rmtree(backup, ignore_errors=True)
'''

INSTALLER_TEST = '''import os
import unittest
from installer import upgrade


class InstallerTest(unittest.TestCase):
    def test_upgrade(self):
        os.makedirs("data", exist_ok=True)
        with open(os.path.join("data", "user.txt"), "w", encoding="utf-8") as handle:
            handle.write("keep")
        upgrade(".", "2")
        self.assertTrue(os.path.isfile(os.path.join("data", "user.txt")))


if __name__ == "__main__":
    unittest.main()
'''


def _check_installer(tree: Path) -> list:
    checks = []
    module = _load(tree, "installer")
    work = Path(tempfile.mkdtemp(prefix="bench-installer-"))
    root = str(work)
    data = work / "data"
    data.mkdir(exist_ok=True)
    (data / "user.txt").write_text("keep", encoding="utf-8")
    module.upgrade(root, "2")
    checks.append({"name": "upgrade preserves user data", "ok":
                   (data / "user.txt").is_file() and (data / "user.txt").read_text() == "keep",
                   "detail": "data must survive an upgrade"})
    (data / "user.txt").write_text("keep", encoding="utf-8")
    try:
        module.upgrade(root, "3", fail=True)
    except RuntimeError:
        pass
    checks.append({"name": "a failed upgrade restores data", "ok":
                   (data / "user.txt").is_file() and (data / "user.txt").read_text() == "keep",
                   "detail": "rollback must restore the user data"})
    checks.append({"name": "rollback leaves no backup debris", "ok":
                   not (work / "data.bak").exists(),
                   "detail": "the backup directory must be cleaned after success"})
    shutil.rmtree(work, ignore_errors=True)
    return checks


# ---------------------------------------------------------------- cli args
CLI_SOURCE = '''BOOLEAN = set()


def parse(argv):
    flags, positionals = {}, []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg.startswith("--"):
            name = arg[2:]
            value = None
            if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                value = argv[index + 1]
                index += 2
            else:
                index += 1
            flags[name] = value
        else:
            positionals.append(arg)
            index += 1
    return flags, positionals
'''

CLI_GOOD = '''BOOLEAN = {"print", "force", "json", "dry-run", "help"}


def parse(argv):
    flags, positionals = {}, []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg.startswith("--"):
            name = arg[2:]
            value = None
            if name not in BOOLEAN and index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                value = argv[index + 1]
                index += 2
            else:
                index += 1
            flags[name] = value
        else:
            positionals.append(arg)
            index += 1
    return flags, positionals
'''

CLI_TEST = '''import unittest
from cli import parse


class CliTest(unittest.TestCase):
    def test_value_flag(self):
        flags, positionals = parse(["--output", "file", "dir"])
        self.assertEqual(flags["output"], "file")
        self.assertEqual(positionals, ["dir"])


if __name__ == "__main__":
    unittest.main()
'''


def _check_cli(tree: Path) -> list:
    module = _load(tree, "cli")
    flags, positionals = module.parse(["--print", "dir"])
    boolean_ok = flags.get("print") in (None, True, "") and positionals == ["dir"]
    flags, positionals = module.parse(["--output", "file", "dir"])
    value_ok = flags.get("output") == "file" and positionals == ["dir"]
    return [{"name": "boolean flags do not swallow positionals and value flags still work",
             "ok": boolean_ok and value_ok,
             "detail": f"boolean={boolean_ok} value={value_ok}"}]


# ---------------------------------------------------------------- multi-agent
COUNTER_SOURCE = '''def increment(path):
    with open(path, "r", encoding="utf-8") as handle:
        value = int(handle.read() or 0)
    value += 1
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(value))
'''

COUNTER_GOOD = '''import threading

LOCK = threading.Lock()


def increment(path):
    with LOCK:
        with open(path, "r", encoding="utf-8") as handle:
            value = int(handle.read() or 0)
        value += 1
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(value))
'''

COUNTER_TEST = '''import unittest
import tempfile
import os
from counter import increment


class CounterTest(unittest.TestCase):
    def test_increment(self):
        path = os.path.join(tempfile.mkdtemp(), "n.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("0")
        increment(path)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "1")


if __name__ == "__main__":
    unittest.main()
'''


def _check_counter(tree: Path) -> list:
    module = _load(tree, "counter")
    work = Path(tempfile.mkdtemp(prefix="bench-counter-"))
    path = str(work / "counter.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("0")
    _run_threads(lambda: module.increment(path), count=40)
    with open(path, encoding="utf-8") as handle:
        value = int(handle.read() or 0)
    shutil.rmtree(work, ignore_errors=True)
    return [{"name": "concurrent increments are not lost", "ok": value == 40,
             "detail": f"counter={value}"}]


CLAIMS_SOURCE = '''import json
import os


def claim(path, task, worker):
    data = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    if task in data:
        return False
    data[task] = worker
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return True
'''

CLAIMS_GOOD = '''import json
import os
import threading

LOCK = threading.Lock()


def claim(path, task, worker):
    with LOCK:
        data = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        if task in data:
            return False
        data[task] = worker
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return True
'''

CLAIMS_TEST = '''import unittest
import tempfile
import os
from claims import claim


class ClaimsTest(unittest.TestCase):
    def test_claim(self):
        path = os.path.join(tempfile.mkdtemp(), "claims.json")
        self.assertTrue(claim(path, "task", "w1"))
        self.assertFalse(claim(path, "task", "w2"))


if __name__ == "__main__":
    unittest.main()
'''


def _check_claims(tree: Path) -> list:
    module = _load(tree, "claims")
    work = Path(tempfile.mkdtemp(prefix="bench-claims-"))
    path = str(work / "claims.json")
    barrier = threading.Barrier(20)

    def attempt():
        barrier.wait()
        return module.claim(path, "task", "worker")

    results, errors = _run_threads(attempt, count=20)
    wins = sum(1 for item in results if item)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    ok = not errors and wins == 1 and data.get("task") == "worker"
    shutil.rmtree(work, ignore_errors=True)
    return [{"name": "only one worker wins and the claim is persisted", "ok": ok,
             "detail": f"wins={wins} errors={errors[:2]} data={data!r}"}]


def _scenario(sid, domain, task, fixture, blind, judgment, allowed, check, good_files, good_agent):
    return {
        "id": sid, "domain": domain, "task": task, "fixture": fixture,
        "blind_spots": blind, "judgment": judgment, "allowed_paths": allowed,
        "forbidden_paths": [], "check": check, "regression": _regression_isolated,
        "controls": {
            "good": {"files": good_files, "agent": good_agent},
            "bad": {"files": {}, "agent": BAD_AGENT},
        },
    }


SCENARIOS = {
    "desktop-lifecycle": _scenario(
        "desktop-lifecycle", "desktop",
        "Make sure a settings change survives an app crash, not only a clean close.",
        {"settings.py": SETTINGS_SOURCE, "tests/test_settings.py": SETTINGS_TEST,
         "README.md": "# Settings app\n\nThe app can crash before on_close runs.\n",
         "pyproject.toml": '[project]\nname = "acme-settings"\nversion = "0.1.0"\n'},
        [
            {"id": "crash-save", "weight": 1, "signals": ["crash", "autosave", "write-through", "set_value"], "evidence": ["settings.py"], "description": "A set must persist before a crash."},
            {"id": "atomic-write", "weight": 1, "signals": ["atomic", "temp", "replace", "tmp"], "evidence": ["settings.py"], "description": "A crash during write must not corrupt the file."},
        ],
        {"signals": ["crash", "atomic", "replace"], "min_signals": 1},
        ["settings.py", "tests/", "README.md", "pyproject.toml"], _check_settings,
        {"settings.py": SETTINGS_GOOD},
        _agent(["A settings change must be persisted in set_value because a crash can happen before on_close",
                "The write must be atomic: temp file plus os.replace"], "Write through on set and replace atomically.", "A crash before close would lose the value; a partial write would corrupt it.", ["settings.py"]),
    ),
    "mobile-offline": _scenario(
        "mobile-offline", "mobile",
        "Make offline sync safe when the network fails mid-batch.",
        {"sync.py": SYNC_SOURCE, "tests/test_sync.py": SYNC_TEST,
         "README.md": "# Offline queue\n\nRecords are sent to a remote API.\n",
         "pyproject.toml": '[project]\nname = "acme-sync"\nversion = "0.1.0"\n'},
        [
            {"id": "queue-loss", "weight": 1, "signals": ["queue", "lost", "clear", "failed"], "evidence": ["sync.py"], "description": "Clearing before send loses records on failure."},
            {"id": "retry", "weight": 1, "signals": ["retry", "once", "idempot", "remaining"], "evidence": ["sync.py"], "description": "Retry must send each record once."},
        ],
        {"signals": ["queue", "retry", "once"], "min_signals": 1},
        ["sync.py", "tests/", "README.md", "pyproject.toml"], _check_sync,
        {"sync.py": SYNC_GOOD},
        _agent(["Clearing the queue before sending loses records when the network fails",
                "A failed send must leave the record in the queue and retry it once"], "Send from the head and remove only after success.", "The failure path is the real path for an offline queue.", ["sync.py"]),
    ),
    "installer-upgrade": _scenario(
        "installer-upgrade", "devops",
        "Make upgrade safe: preserve user data and allow rollback if the new version fails.",
        {"installer.py": INSTALLER_SOURCE, "tests/test_installer.py": INSTALLER_TEST,
         "README.md": "# Installer\n\nUpgrade writes VERSION and manages a data directory.\n",
         "pyproject.toml": '[project]\nname = "acme-installer"\nversion = "0.1.0"\n'},
        [
            {"id": "preserve-data", "weight": 1, "signals": ["backup", "preserve", "data"], "evidence": ["installer.py"], "description": "User data must survive the upgrade."},
            {"id": "rollback", "weight": 1, "signals": ["rollback", "restore", "failure"], "evidence": ["installer.py"], "description": "A failed upgrade must restore the data."},
        ],
        {"signals": ["backup", "rollback", "restore"], "min_signals": 1},
        ["installer.py", "tests/", "README.md", "pyproject.toml"], _check_installer,
        {"installer.py": INSTALLER_GOOD},
        _agent(["The upgrade deletes the data directory; it must back it up first",
                "A failed upgrade must restore the backup and not leave the user without data"], "Back up data, write VERSION, restore on failure.", "The current code is destructive before it knows the upgrade succeeded.", ["installer.py"]),
    ),
    "cli-args": _scenario(
        "cli-args", "devops",
        "Fix the CLI so boolean flags do not swallow positional arguments.",
        {"cli.py": CLI_SOURCE, "tests/test_cli.py": CLI_TEST,
         "README.md": "# CLI\n\n--print and --force are boolean; --output takes a value.\n",
         "pyproject.toml": '[project]\nname = "acme-cli"\nversion = "0.1.0"\n'},
        [
            {"id": "boolean", "weight": 1, "signals": ["boolean", "flag", "positional", "swallow"], "evidence": ["cli.py"], "description": "Boolean flags must not consume the next positional."},
            {"id": "value", "weight": 1, "signals": ["value", "output", "consume"], "evidence": ["cli.py"], "description": "Value flags must still consume their value."},
        ],
        {"signals": ["boolean", "positional", "value"], "min_signals": 1},
        ["cli.py", "tests/", "README.md", "pyproject.toml"], _check_cli,
        {"cli.py": CLI_GOOD},
        _agent(["--print is boolean and must not consume the directory argument",
                "--output is a value flag and must still consume its value"], "Add a boolean flag set and only consume values for non-boolean flags.", "The current parser treats every long flag as value-taking, so the directory is lost.", ["cli.py"]),
    ),
    "shared-state-race": _scenario(
        "shared-state-race", "multiagent",
        "Make two workers increment shared state without lost updates.",
        {"counter.py": COUNTER_SOURCE, "tests/test_counter.py": COUNTER_TEST,
         "README.md": "# Shared counter\n\nTwo workers increment the same file.\n",
         "pyproject.toml": '[project]\nname = "acme-counter"\nversion = "0.1.0"\n'},
        [
            {"id": "lost-update", "weight": 1, "signals": ["lock", "race", "lost update", "atomic"], "evidence": ["counter.py"], "description": "Read-modify-write loses concurrent increments."},
        ],
        {"signals": ["lock", "atomic", "lost update"], "min_signals": 1},
        ["counter.py", "tests/", "README.md", "pyproject.toml"], _check_counter,
        {"counter.py": COUNTER_GOOD},
        _agent(["The read-modify-write races; concurrent workers lose increments",
                "The read and write must be one atomic critical section"], "Guard the read-modify-write with a lock.", "The shared file is the coordination point; without a lock two readers see the same value.", ["counter.py"]),
    ),
    "ownership-conflict": _scenario(
        "ownership-conflict", "multiagent",
        "Prevent two workers from claiming the same task.",
        {"claims.py": CLAIMS_SOURCE, "tests/test_claims.py": CLAIMS_TEST,
         "README.md": "# Task claims\n\nWorkers claim tasks in a shared JSON file.\n",
         "pyproject.toml": '[project]\nname = "acme-claims"\nversion = "0.1.0"\n'},
        [
            {"id": "atomic-claim", "weight": 1, "signals": ["lock", "atomic", "claim", "race"], "evidence": ["claims.py"], "description": "Check and write must be atomic."},
            {"id": "ownership", "weight": 1, "signals": ["owner", "worker", "conflict", "one"], "evidence": ["claims.py"], "description": "Only one worker may own a task."},
        ],
        {"signals": ["lock", "atomic", "owner"], "min_signals": 1},
        ["claims.py", "tests/", "README.md", "pyproject.toml"], _check_claims,
        {"claims.py": CLAIMS_GOOD},
        _agent(["The claim check and JSON write race; two workers can both see the task as free",
                "Only one worker may own a task; the claim must be atomic"], "Lock the check-and-write and persist the owner.", "Ownership is a shared-state invariant, not a best-effort check.", ["claims.py"]),
    ),
}
