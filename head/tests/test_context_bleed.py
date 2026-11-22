"""Static checks for the context-bleed / stale-action guardrails in app.py.

Behavioral verification (actually exercising a compacted summary + topic shift against a
live model) lives in bleed_harness.py, which needs the running orchestrator and is not part
of this offline suite."""
import unittest
from pathlib import Path

APP = Path("app.py").read_text(encoding="utf-8")


class ContextBleedGuardrailTests(unittest.TestCase):
    def test_system_has_stay_on_current_message(self):
        self.assertIn("STAY ON THE CURRENT MESSAGE", APP)
        self.assertIn("never resurface, repeat, or re-announce", APP)

    def test_system_forbids_untaken_actions(self):
        self.assertIn("NEVER CLAIM UNTAKEN ACTIONS", APP)
        self.assertIn("called the matching tool in THIS turn", APP)

    def test_system_stay_on_task(self):
        self.assertIn("STAY ON TASK", APP)

    def test_summary_injection_reframed_as_background(self):
        self.assertIn("Background memory from earlier in THIS conversation", APP)
        self.assertIn("do NOT repeat or", APP)
        # the old framing must be gone
        self.assertNotIn("Durable memory from earlier in this conversation", APP)

    def test_compaction_marks_actions_closed(self):
        self.assertIn("Record completed actions in the past tense as finished and closed", APP)
        self.assertIn("Keep distinct topics", APP)


if __name__ == "__main__":
    unittest.main()
