import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runner import CodingRunner, PolicyError


class DecisionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        workspace = root / "workspace"
        workspace.mkdir()
        registry = root / "registry.json"
        registry.write_text(json.dumps({"workspaces": [{
            "id": "fixture", "path": str(workspace), "read": True, "write": True,
            "git": False, "commands": {},
        }]}), encoding="utf-8")
        self.runner = CodingRunner(registry, root / "state")
        self.task = self.runner.task_start("fixture", "choose architecture")
        self.options = [
            {"id": "sidecar", "label": "Sidecar", "tradeoff": "More isolation; another service."},
            {"id": "socket", "label": "Docker socket", "tradeoff": "Simpler; grants privileged root access."},
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_create_blocks_task_and_assigns_deterministic_high_risk(self):
        decision = self.runner.decision_create(
            self.task["id"], "Which architecture should hold Docker access?",
            self.options, "sidecar", rationale="Prefer least privilege.", reversible=False)
        self.assertEqual(decision["risk"], "HIGH")
        self.assertIn("privileged infrastructure boundary", decision["risk_reasons"])
        task = self.runner.task_get(self.task["id"])
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(task["blocked_by_decision"], decision["id"])

    def test_second_create_reuses_pending_gate(self):
        first = self.runner.decision_create(self.task["id"], "Choose architecture", self.options, "sidecar")
        second = self.runner.decision_create(self.task["id"], "Different wording", self.options, "socket")
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(self.runner.decision_list()["decisions"]), 1)

    def test_resolve_records_choice_and_resumes_same_task(self):
        decision = self.runner.decision_create(self.task["id"], "Choose architecture", self.options, "sidecar")
        resolved = self.runner.decision_resolve(decision["id"], "sidecar", note="Alex chose isolation")
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["choice"], "sidecar")
        task = self.runner.task_get(self.task["id"])
        self.assertEqual(task["status"], "active")
        self.assertNotIn("blocked_by_decision", task)
        self.assertEqual(self.runner.decision_list()["decisions"], [])

    def test_escalation_packet_is_durable_evidence_and_never_sent(self):
        with patch.object(self.runner, "git_status", return_value={"stdout": " M app.py\n"}), \
             patch.object(self.runner, "git_diff", return_value={"stdout": "+change\n"}):
            packet = self.runner.escalation_packet_create(
                self.task["id"], "architecture_or_security", "Choose the safe service boundary",
                suggested_agent="codex")
        self.assertFalse(packet["sent"])
        self.assertTrue(packet["requires_user_agent_choice"])
        self.assertIn("SUPERVISED LOCAL CODING JOB", packet["handoff"])
        self.assertIn(self.task["id"], packet["handoff"])
        self.assertIn("no deployment", packet["handoff"])
        self.assertEqual(self.runner.escalation_packet_get(packet["id"])["id"], packet["id"])
        self.assertEqual(self.runner.task_get(self.task["id"])["status"], "blocked")
        selected = self.runner.escalation_packet_select(packet["id"], "codex", "background_review")
        self.assertEqual(selected["status"], "selected")
        self.assertEqual(selected["selected_agent"], "codex")
        self.assertFalse(selected["requires_user_agent_choice"])
        resolved = self.runner.escalation_packet_resolve(packet["id"], "returned", note="review complete")
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(self.runner.task_get(self.task["id"])["status"], "active")
    def test_resolve_rejects_unknown_choice(self):
        decision = self.runner.decision_create(self.task["id"], "Choose architecture", self.options, "sidecar")
        with self.assertRaisesRegex(PolicyError, "option id"):
            self.runner.decision_resolve(decision["id"], "surprise")


if __name__ == "__main__":
    unittest.main()