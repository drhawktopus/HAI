"""Static and lightweight tests for reliability guardrails in app.py."""
import unittest
from pathlib import Path


APP_SOURCE = Path("app.py").read_text(encoding="utf-8")


class ReliabilityGuardrailTests(unittest.TestCase):
    def test_test_user_is_effectively_ephemeral(self):
        self.assertIn("def _effective_ephemeral(q):", APP_SOURCE)
        # Any test* user is ephemeral (plain `test` + grounded test-homelab/test-nursing/test-law).
        self.assertIn("config.is_test_user(getattr(q, \"user\", \"\"))", APP_SOURCE)
        self.assertIn("effective_ephemeral = _effective_ephemeral(q)", APP_SOURCE)
        self.assertIn("if _effective_ephemeral(q):", APP_SOURCE)
        self.assertIn("if effective_ephemeral else _compact_history", APP_SOURCE)
        self.assertIn('if effective_ephemeral else history.context_messages(', APP_SOURCE)   # #18: chat= arg added

    def test_compaction_prompt_forbids_invented_technical_facts(self):
        required = [
            "Preserve uncertainty",
            "Never infer, repair, rename, or correct technical facts",
            "Only include entity IDs",
            "model tags",
            "pull commands",
            "tool outcomes",
            "If sources disagree",
        ]
        for phrase in required:
            self.assertIn(phrase, APP_SOURCE)

    def test_system_prompt_requires_current_lookup_verification(self):
        required = [
            "SOURCE DISCIPLINE FOR CURRENT OR LITERAL FACTS",
            "exact/literal/current",
            "model tag",
            "web_search",
            "clearly say what is uncertain",
            "prefer the heavyweight",
        ]
        for phrase in required:
            self.assertIn(phrase, APP_SOURCE)


if __name__ == "__main__":
    unittest.main()
