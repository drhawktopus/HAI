import unittest
from pathlib import Path
from unittest.mock import patch

import coding_client
import tools

ROOT = Path(__file__).resolve().parents[1]


class Phase2WiringTests(unittest.TestCase):
    @patch("coding_client._request")
    def test_client_start_and_advance(self, request):
        request.return_value = {"ok": True}
        nodes = [{"id": "a", "objective": "first"}]
        coding_client.project_driver("start", workspace="orchestrator-real", objective="goal", nodes=nodes)
        request.assert_called_with("POST", "/projects", body={"workspace": "orchestrator-real", "objective": "goal", "nodes": nodes})
        coding_client.project_driver("advance", project_id="f" * 32)
        request.assert_called_with("POST", "/projects/" + "f" * 32 + "/advance")

    @patch("coding_client._request")
    def test_client_recovers_active_project_instead_of_guessing_id(self, request):
        active = {"id": "a" * 32, "workspace": "orchestrator-real", "status": "active"}
        request.side_effect = [{"error": "not found"}, {"projects": [active]}]
        result = coding_client.project_driver(
            "get", workspace="orchestrator-real", project_id="f" * 32)
        self.assertEqual(result["id"], active["id"])
        self.assertEqual(result["recovered_from_project_id"], "f" * 32)
    def test_tool_schema_and_dispatch(self):
        names = [item["function"]["name"] for item in tools.CODING_TOOLS]
        self.assertIn("coding_project_driver", names)
        source = (ROOT / "tools.py").read_text(encoding="utf-8")
        self.assertIn('if name == "coding_project_driver":', source)
        # coding-runner/app.py is a SEPARATE build and isn't present inside the orchestrator container
        # (the suite is run there via `docker cp tests`). Only assert its routes where the source exists.
        runner_app = ROOT / "coding-runner" / "app.py"
        if runner_app.exists():
            app = runner_app.read_text(encoding="utf-8")
            for route in ('@app.post("/projects")', '@app.post("/projects/{project_id}/advance")', '@app.post("/projects/{project_id}/resume")'):
                self.assertIn(route, app)

    def test_phase_marker_and_brief(self):
        appsrc = (ROOT / "app.py").read_text(encoding="utf-8")
        # Phase number must match 06-SELF-ENGINEERING-AGENT-PLAN.md
        self.assertIn('"self_engineering_phase": 7', appsrc)          # /health declares latest shipped phase
        self.assertIn("coding_project_driver(start)", appsrc)          # brief drives multi-task projects
        self.assertIn("project", appsrc.lower())


if __name__ == "__main__": unittest.main()