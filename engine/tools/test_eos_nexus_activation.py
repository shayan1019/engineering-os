"""Exercise the runtime entry points and the host-event privacy boundary."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import eos_activate
import eos_discipline
import eos_host_hooks
import nexus_event


class ActivationTests(unittest.TestCase):
    def test_policy_is_identical_across_host_contexts(self):
        selected = {"skills": {"primary": ["fable-5-1"]}, "agents": {"specialists": ["verifier"]}}
        with patch.object(eos_activate, "route", return_value=selected):
            contexts = [eos_activate.build("verify scope", "", host, 2)
                        for host in ("opencode", "claude", "codex", "gemini", "unknown")]
        self.assertTrue(contexts[0]["route_available"])
        expected = contexts[0]["discipline"]
        self.assertEqual(expected["status"], "AVAILABLE")
        for context in contexts:
            self.assertEqual(context["discipline"], expected)
            self.assertEqual(context["specialists"], ["verifier"])
            self.assertEqual(context["skills"][0]["name"], "fable-5-1")
            self.assertIn(eos_discipline.render(expected), eos_activate.render(context))

    def test_router_emits_revision_without_copying_policy_into_json(self):
        router = Path(eos_activate.ep.ENGINE_ROOT) / "router" / "route_task.py"
        with tempfile.TemporaryDirectory() as workspace:
            output = subprocess.check_output([sys.executable, str(router), "--task", "explain the code",
                                              "--root", workspace], text=True, timeout=60)
        route = json.loads(output)
        self.assertEqual(route["discipline"]["revision"], 1)
        self.assertNotIn("text", route["discipline"])
        self.assertIn("fable_5_1_signal", route)

    def test_session_context_is_project_scoped(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            for host in ("claude", "codex"):
                for path, expected in ((root, True), (other, False)):
                    payload = io.BytesIO(json.dumps({"cwd": path, "hook_event_name": "SessionStart"}).encode())
                    output = io.StringIO()
                    with patch.dict(os.environ, {"EOS_NEXUS_WORKSPACE": root}), \
                         patch.object(sys, "stdin", io.TextIOWrapper(payload)), contextlib.redirect_stdout(output):
                        eos_host_hooks.context(host)
                    self.assertEqual("<eos-discipline revision=" in output.getvalue(), expected)

    def test_selection_and_load_depend_on_the_actual_hook(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as home:
            payload = {"cwd": workspace, "session_id": "active-A", "tool_name": "Skill",
                       "tool_use_id": "call-1", "tool_input": {"skill": "fable-5-1", "prompt": "canary-sensitive"}}
            with patch.dict(os.environ, {"EOS_NEXUS_WORKSPACE": workspace}), patch.object(nexus_event.ep, "EOS_HOME", home):
                for hook in ("PreToolUse", "PostToolUseFailure", "PostToolUse"):
                    nexus_event.observe_hook("claude", {**payload, "hook_event_name": hook})
            files = list(Path(home).rglob("*.jsonl"))
            self.assertEqual(len(files), 1)
            raw = files[0].read_text(encoding="utf-8")
            self.assertNotIn("canary-sensitive", raw)
            self.assertNotIn("active-A", raw)
            self.assertEqual([json.loads(line)["kind"] for line in raw.splitlines()],
                             ["tool.started", "tool.failed", "tool.completed", "skill.loaded"])

    def test_native_hook_writers_keep_complete_records(self):
        worker = (
            "import sys, nexus_event as event; "
            "[event.emit(sys.argv[1], 'claude-concurrent', 'claude', 'tool.completed', "
            "tool=f'{sys.argv[2]}-{i}') for i in range(25)]"
        )
        tools = Path(nexus_event.__file__).parent
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as home:
            children = [subprocess.Popen([sys.executable, "-B", "-c", worker, workspace, str(i)],
                                         cwd=tools, env={**os.environ, "EOS_HOME": home}) for i in range(4)]
            try:
                for child in children:
                    self.assertEqual(child.wait(timeout=20), 0)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
            journal, = Path(home).rglob("*.jsonl")
            records = [json.loads(line) for line in journal.read_text(encoding="ascii").splitlines()]
            self.assertEqual(len(records), 100)
            self.assertEqual({row["tool"] for row in records}, {f"{i}-{j}" for i in range(4) for j in range(25)})


if __name__ == "__main__":
    unittest.main()
