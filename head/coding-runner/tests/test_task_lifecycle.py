import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from runner import CodingRunner, ConflictError, PolicyError


def _git(ws, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True, env=env)


class TaskLifecycleHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        ws = root / "workspace"; ws.mkdir()
        _git(ws, "init", "-q")
        (ws / "f.txt").write_text("hello", encoding="utf-8")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-q", "-m", "init")
        self.registry = root / "registry.json"
        self.registry.write_text(json.dumps({"workspaces": [{"id": "fixture", "path": str(ws),
            "read": True, "write": True, "git": True, "commands": {}}]}), encoding="utf-8")
        self.runner = CodingRunner(self.registry, root / "state")
        self.ws = ws

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_second_active_task(self):
        t1 = self.runner.task_start("fixture", "first")
        with self.assertRaisesRegex(ConflictError, t1["id"]):
            self.runner.task_start("fixture", "second")

    def test_dirty_workspace_blocks_task_start(self):
        (self.ws / "f.txt").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(PolicyError, "uncommitted"):
            self.runner.task_start("fixture", "x")

    def test_internal_start_bypasses_guards(self):
        self.runner.task_start("fixture", "first")
        t2 = self.runner.task_start("fixture", "second", _internal=True)
        self.assertEqual(t2["status"], "active")

    def test_abandon_restores_baseline_and_cancels(self):
        t = self.runner.task_start("fixture", "work")
        (self.ws / "f.txt").write_text("messy", encoding="utf-8")
        (self.ws / "junk.txt").write_text("junk", encoding="utf-8")
        res = self.runner.task_abandon(t["id"])
        self.assertEqual(res["status"], "cancelled")
        self.assertEqual(self.runner.task_get(t["id"])["status"], "cancelled")
        self.assertEqual((self.ws / "f.txt").read_text(encoding="utf-8"), "hello")
        self.assertFalse((self.ws / "junk.txt").exists())

    def test_abandon_idempotent_on_terminal(self):
        t = self.runner.task_start("fixture", "work")
        self.runner.task_abandon(t["id"])
        self.assertTrue(self.runner.task_abandon(t["id"]).get("already_terminal"))


if __name__ == "__main__":
    unittest.main()