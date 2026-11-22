"""Project 12 Phase 1: chat-driven self-engineering orchestration wiring."""
import unittest
from pathlib import Path
from unittest.mock import patch

import hostops
import app as orchestrator_app
import ollama_client

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")


class Phase1SelfEngineeringTests(unittest.TestCase):
    def test_structured_deploy_status_preserves_broker_payload(self):
        payload = {"job": "j1", "phase": "awaiting_promote", "awaiting_promote": True}
        with patch("hostops._request", return_value=payload):
            self.assertEqual(hostops.deploy_status_data("j1"), payload)

    def test_structured_deploy_status_fails_soft(self):
        with patch("hostops._request", side_effect=hostops.HostOpsError("offline")):
            self.assertIn("offline", hostops.deploy_status_data("j1")["error"])

    def test_self_engineering_prompt_is_bounded_and_gated(self):
        for required in (
            "SELF_ENGINEERING_INTENT",
            "SELF-ENGINEERING MODE (Project 12 Phase 4)",
            "orchestrator-real",
            "coding_task(isolate=false)",
            "run BOTH unit and compile profiles",
            "NEVER approve a pending action ",
            "Never bypass either confirmation gate",
        ):
            self.assertIn(required, APP)
        self.assertIn("q.user in config.ADMIN_USERS", APP)
        self.assertIn("or self_engineering", APP)

    def test_intent_matches_user_and_internal_continuations_only(self):
        intent = orchestrator_app.SELF_ENGINEERING_INTENT
        self.assertTrue(intent.search("Please add a read-only tool to Home AI"))
        self.assertTrue(intent.search("INTERNAL SELF-ENGINEERING CONTINUATION: deploy j1 is done"))
        self.assertFalse(intent.search("Turn off the office light"))
        self.assertFalse(intent.search("How is Home AI doing today?"))
    @patch("app.coding_client.task")
    @patch("app.pending_actions.list_pending")
    def test_continuation_stops_at_human_gate(self, pending, task):
        task.return_value = {"tasks": [{"workspace": "orchestrator-real", "status": "active"}]}
        pending.return_value = [{"kind": "self.deploy"}]
        self.assertFalse(orchestrator_app._self_engineering_needs_continuation("Alex"))
        pending.return_value = []
        self.assertTrue(orchestrator_app._self_engineering_needs_continuation("Alex"))

    def test_progress_state_ignores_timestamps_but_tracks_workflow_changes(self):
        task = {"id": "t1", "workspace": "orchestrator-real", "status": "active",
                "plan": ["one"], "notes": [], "updated_at": "old"}
        project = {"id": "p1", "workspace": "orchestrator-real", "status": "active",
                   "current_node": "a", "nodes": [{"id": "a", "status": "active", "task_id": "t1"}]}
        with patch("app.coding_client.task", return_value={"tasks": [task]}), \
             patch("app.coding_client.project_driver", return_value={"projects": [project]}), \
             patch("app.pending_actions.list_pending", return_value=[]):
            before = orchestrator_app._self_engineering_progress_state("Alex")
            task["updated_at"] = "new"
            self.assertEqual(before, orchestrator_app._self_engineering_progress_state("Alex"))
            task["notes"].append("made progress")
            self.assertNotEqual(before, orchestrator_app._self_engineering_progress_state("Alex"))

    def test_continuation_uses_progress_stall_bound(self):
        self.assertIn("SELF_ENGINEERING_MAX_PASSES", APP)
        self.assertIn("SELF_ENGINEERING_STALL_LIMIT", APP)
        self.assertNotIn("SELF_ENGINEERING_MAX_CONTINUATIONS", APP)
    def test_successful_json_tool_results_are_not_snags(self):
        self.assertIsNone(ollama_client._tool_snag('{"returncode": 0, "timed_out": false}'))
        self.assertIsNotNone(ollama_client._tool_snag('{"returncode": 1, "timed_out": false}'))
        self.assertIsNotNone(ollama_client._tool_snag('{"error": "nope"}'))

    def test_browser_continues_at_both_deploy_decision_points(self):
        self.assertIn('@app.get("/deploy/progress")', APP)
        self.assertIn("raise HTTPException(status_code=403", APP)
        self.assertIn("async function watchSelfDeploy(job, stage)", APP)
        self.assertIn("d.awaiting_promote", APP)
        self.assertIn("new Set(['done','failed','rolled_back','cancelled'])", APP)
        self.assertIn("res.kind==='self.deploy.promote'", APP)
        self.assertIn("Live cutover briefly restarts this server", APP)
        self.assertIn("reconnecting after the live restart", APP)
        self.assertIn("retry < 5", APP)


if __name__ == "__main__":
    unittest.main()