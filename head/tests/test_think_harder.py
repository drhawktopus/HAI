"""Static + behavioral checks for the Think-Really-Hard verify-plan guardrails."""
import unittest
from pathlib import Path

import deliberation

APP = Path("app.py").read_text(encoding="utf-8")
DELIB = Path("deliberation.py").read_text(encoding="utf-8")


class ThinkHarderTests(unittest.TestCase):
    def test_first_pass_requires_verify_plan(self):
        self.assertIn("Do a deliberate verify-plan", APP)
        self.assertIn("GATHER EVIDENCE with the right tool", APP)
        self.assertIn("Never present an unverified", APP)

    def test_refinement_gathers_missing_evidence(self):
        # Assert against the constant; app wiring is tested separately.
        self.assertIn("missing evidence with the appropriate tool", deliberation.TH_REFINE_RULES)

    def test_critique_flags_unverified_claims(self):
        self.assertIn("asserted confidently WITHOUT evidence", DELIB)
        self.assertIn("action was performed without a tool result", DELIB)

    def test_refinement_reanswers_original_task(self):
        self.assertIn("ORIGINAL REQUEST (answer this completely", APP)
        self.assertIn("do NOT reply to these", APP)   # the user-turn [Internal QA only] scaffold
        self.assertIn("SILENT internal QA notes", deliberation.TH_REFINE_RULES)
        self.assertIn("do NOT reply to the critique", deliberation.TH_REFINE_RULES)

    def test_refinement_regression_guard_wired(self):
        # The regression guard rides in the improve() step of the shared converge engine now.
        self.assertIn("deliberation.refinement_regressed(refined, hold[\"answer\"])", APP)
        self.assertIn("Refinement rejected as a regression", APP)
        self.assertIn("improve=_th_improve, recritique=_th_recritique", APP)   # think-hard on the shared engine


class JournalVerdictTests(unittest.TestCase):
    """journal() must surface verdict so diagnostics no longer log verdict: null."""

    def _state(self):
        return deliberation.EffortState("think_hard", 600, 3)

    def test_journal_includes_verdict_and_findings_count(self):
        state = self._state()
        state.record("first_pass", "draft answer")
        state.record(
            "independent_critique",
            "summary",
            verdict="changes_requested",
            findings=[{"severity": "high"}, {"severity": "low"}],
        )
        journal = state.journal()
        critique = next(p for p in journal if p["phase"] == "independent_critique")
        self.assertEqual(critique["verdict"], "changes_requested")
        self.assertEqual(critique["findings"], 2)
        # The plain first pass has no verdict key (nothing to surface).
        first = next(p for p in journal if p["phase"] == "first_pass")
        self.assertNotIn("verdict", first)


class RefinementRegressionTests(unittest.TestCase):
    DRAFT = "A thorough, complete first-pass answer. " * 30  # ~1200 chars

    def test_narrated_stub_is_regression(self):
        # The exact failure mode: a narrated-action stub that never answers.
        stub = "Let me look at the actual services.yaml on Softserve. It lives at `/opt/"
        self.assertTrue(deliberation.refinement_regressed(stub, self.DRAFT))

    def test_empty_is_regression(self):
        self.assertTrue(deliberation.refinement_regressed("", self.DRAFT))
        self.assertTrue(deliberation.refinement_regressed("  ", self.DRAFT))

    def test_hard_collapse_is_regression(self):
        self.assertTrue(deliberation.refinement_regressed("Done.", self.DRAFT))

    def test_good_refinement_is_not_regression(self):
        good = ("Your services.yaml points the GPU tile at gpu.0.proc, but Glances returns "
                "gpu.0.gpu - update the field path and the tile will render. " * 3)
        self.assertFalse(deliberation.refinement_regressed(good, self.DRAFT))

    def test_short_but_complete_against_short_draft_not_regression(self):
        # When the draft itself is short, a similarly short refinement isn't a collapse.
        self.assertFalse(deliberation.refinement_regressed("Yes, port 61208.", "Maybe 61208?"))


if __name__ == "__main__":
    unittest.main()
