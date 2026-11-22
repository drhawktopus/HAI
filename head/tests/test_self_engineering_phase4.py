import unittest
from unittest.mock import patch

import tools


class Phase4DecisionWiringTests(unittest.TestCase):
    def test_decision_tool_is_heavy_and_admin_only(self):
        heavy = {item["function"]["name"] for item in tools.HEAVY_TOOLS}
        self.assertIn("coding_decision", heavy)
        self.assertIn("coding_escalation", heavy)
        self.assertFalse(any(item["function"]["name"] == "coding_decision"
                             for item in tools.tools_for(tools.HEAVY_TOOLS, "Guest")))
        self.assertFalse(any(item["function"]["name"] == "coding_escalation"
                             for item in tools.tools_for(tools.HEAVY_TOOLS, "Guest")))
        self.assertTrue(any(item["function"]["name"] == "coding_decision"
                            for item in tools.tools_for(tools.HEAVY_TOOLS, "Alex")))

    def test_phase4_brief_preserves_locked_authority(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn('"self_engineering_phase": 7', source)
        self.assertIn("genuine user-owned architecture or security choice", source)
        self.assertIn("Never create a gate for routine reversible implementation details", source)
        self.assertIn('Only call coding_decision(resolve) after " + config.OWNER + " explicitly chooses', source)
        self.assertIn("do not send it or choose Claude/Codex yourself", source)
    @patch("tools.coding_client.decision")
    def test_non_admin_cannot_resolve_gate(self, decision):
        result = tools.run_tool("coding_decision", {"action": "resolve", "decision_id": "a" * 32,
                                                     "choice": "sidecar"}, "choose", user="Guest")
        self.assertIn("Permission denied", result)
        decision.assert_not_called()


if __name__ == "__main__":
    unittest.main()