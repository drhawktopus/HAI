"""Tests for the nursing Educator sub-mode (item-writing in Riley's blueprint format).

Verified statically (app pulls heavy deps): the mode is registered, picks no model, and its brief
encodes the FORMAT-SPEC essentials (Bloom's + NCLEX tagging, the blueprint tables, and the hard
no-fabricated-citation rule grounded via search_evidence)."""
import unittest
from pathlib import Path

APP = Path("app.py").read_text(encoding="utf-8")


class EducatorModeTests(unittest.TestCase):
    def test_registered(self):
        self.assertIn('NURSING_MODES = ("ebp", "tutor", "reference", "educator")', APP)
        self.assertIn('"educator": (', APP)

    def test_the_mode_is_the_brief_and_picks_no_model(self):
        self.assertNotIn('nmode in ("ebp", "educator")', APP)
        self.assertIn("NURSING_BASE + NURSING_MODE_BRIEFS[_nursing_mode(q_mode)]", APP)

    def test_brief_encodes_format_spec(self):
        brief = APP.split('"educator": (', 1)[1][:1800]  # the brief is ~1.4k chars
        for needle in ("Bloom's", "NCLEX", "mark the correct answer", "search_evidence", "never fabricate",
                       "Test Blueprint summary", "Cognitive Level", "blueprint outline"):
            self.assertIn(needle, brief, f"educator brief missing: {needle}")
        # Honors the step-first workflow like EBP.
        self.assertIn("do not dump a full test unprompted", brief)

    def test_ui_exposes_educator(self):
        self.assertIn('educator:"Educator"', APP)               # dropdown label
        self.assertIn("educator: \"Educator —", APP)            # MODE_INFO description
        self.assertIn("'ebp','tutor','reference','educator'", APP)  # default nursing mode list

    def test_modes_endpoint_lists_from_constant(self):
        # /modes exposes nursing_modes from the NURSING_MODES constant, so educator appears automatically.
        self.assertIn('"nursing_modes": list(NURSING_MODES)', APP)


class ModeGuidanceTests(unittest.TestCase):
    def test_descriptions_use_plain_language(self):
        # Task-framed, model-free, parallel "use this when…" wording for the nursing modes.
        for needle in ('reference: "Reference — use this when',
                       'tutor: "Tutor — use this when',
                       'ebp: "EBP Research — use this when',
                       'educator: "Educator — use this when'):
            self.assertIn(needle, APP)

    def test_deterministic_mode_suggestion_wired(self):
        # Model-independent: the orchestrator detects a mismatch and appends a tip (the 8B won't
        # reliably self-suggest), so it's NOT a prompt nudge in NURSING_BASE.
        self.assertIn("def _mode_suggestion(nmode, text):", APP)
        self.assertIn("_EDUCATOR_INTENT", APP)
        self.assertIn("_EBP_INTENT", APP)
        self.assertIn('diag["mode_hint"] = _mode_suggestion(nmode, t)', APP)
        self.assertIn('hint = diag.get("mode_hint")', APP)
        self.assertIn('result["answer"] = result["answer"].rstrip() + "\\n\\n" + hint', APP)


if __name__ == "__main__":
    unittest.main()
