import json
import tempfile
import unittest
from pathlib import Path

from runner import CodingRunner, ConflictError, PolicyError


class ProjectDriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        ws = root / "workspace"; ws.mkdir()
        (ws / ".git").mkdir()
        self.registry = root / "registry.json"
        self.registry.write_text(json.dumps({"workspaces": [{"id": "fixture", "path": str(ws),
            "read": True, "write": True, "git": False, "commands": {}}]}), encoding="utf-8")
        self.state = root / "state"
        self.runner = CodingRunner(self.registry, self.state)

    def tearDown(self): self.temp.cleanup()

    def nodes(self):
        return [
            {"id": "foundation", "objective": "build foundation"},
            {"id": "integration", "objective": "wire integration", "depends_on": ["foundation"]},
        ]

    def test_advances_in_dependency_order_and_completes(self):
        project = self.runner.project_start("fixture", "two-step goal", self.nodes())
        project = self.runner.project_advance(project["id"])
        self.assertEqual(project["current_node"], "foundation")
        first = project["nodes"][0]["task_id"]
        self.runner.task_update(first, status="complete")
        project = self.runner.project_advance(project["id"])
        self.assertEqual(project["current_node"], "integration")
        self.runner.task_update(project["nodes"][1]["task_id"], status="complete")
        self.assertEqual(self.runner.project_advance(project["id"])["status"], "complete")

    def test_persists_and_resumes_blocked_child(self):
        project = self.runner.project_start("fixture", "goal", self.nodes())
        project = self.runner.project_advance(project["id"])
        task_id = project["nodes"][0]["task_id"]
        self.runner.task_update(task_id, status="blocked")
        restarted = CodingRunner(self.registry, self.state)
        self.assertEqual(restarted.project_get(project["id"])["status"], "blocked")
        resumed = restarted.project_resume(project["id"])
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(restarted.task_get(task_id)["status"], "active")

    def test_rejects_cycle_and_unknown_dependency(self):
        with self.assertRaisesRegex(PolicyError, "cycle"):
            self.runner.project_start("fixture", "goal", [
                {"id": "a", "objective": "a", "depends_on": ["b"]},
                {"id": "b", "objective": "b", "depends_on": ["a"]},
            ])
        with self.assertRaisesRegex(PolicyError, "invalid dependency"):
            self.runner.project_start("fixture", "goal", [
                {"id": "a", "objective": "a", "depends_on": ["missing"]},
            ])


    def test_rejects_second_active_project_for_workspace(self):
        first = self.runner.project_start("fixture", "first", self.nodes())
        with self.assertRaisesRegex(ConflictError, first["id"]):
            self.runner.project_start("fixture", "second", self.nodes())

if __name__ == "__main__": unittest.main()