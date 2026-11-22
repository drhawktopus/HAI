import json
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from runner import CodingRunner


class AutoCancelStaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        ws = root / "ws"; ws.mkdir()
        reg = root / "registry.json"
        reg.write_text(json.dumps({"workspaces": [{"id": "fixture", "path": str(ws),
            "read": True, "write": True, "git": False, "commands": {}}]}), encoding="utf-8")
        self.runner = CodingRunner(reg, root / "state")

    def tearDown(self):
        self.temp.cleanup()

    def _backdate(self, task_id, days):
        p = self.runner._task_path(task_id)
        task = json.loads(p.read_text(encoding="utf-8"))
        task["updated_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
        p.write_text(json.dumps(task), encoding="utf-8")

    def test_stale_standalone_task_auto_cancels(self):
        t = self.runner.task_start("fixture", "old work")
        self._backdate(t["id"], 2)
        self.runner.task_list()
        self.assertEqual(self.runner.task_get(t["id"])["status"], "cancelled")

    def test_fresh_task_not_cancelled(self):
        t = self.runner.task_start("fixture", "fresh")
        self.runner.task_list()
        self.assertEqual(self.runner.task_get(t["id"])["status"], "active")

    def test_project_linked_stale_task_not_cancelled(self):
        t = self.runner.task_start("fixture", "linked")
        pid = "proj123"
        (self.runner.projects_dir / f"{pid}.json").write_text(json.dumps({
            "id": pid, "workspace": "fixture", "status": "active",
            "nodes": [{"id": "n1", "task_id": t["id"], "status": "active"}]}), encoding="utf-8")
        self._backdate(t["id"], 2)
        self.runner.task_list()
        self.assertEqual(self.runner.task_get(t["id"])["status"], "active")


if __name__ == "__main__":
    unittest.main()