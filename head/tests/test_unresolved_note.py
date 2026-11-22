"""Test contract for deliberation.unresolved_note().

Unresolved critiques must ship labeled; clean/converged cases MUST return None.
(Always-firing banners are noise and get ignored.)
"""
import unittest

import deliberation


def _conv(converged, stop_reason="stalled"):
    return {"converged": converged, "stop_reason": stop_reason, "rounds_run": 2,
            "best_findings": 2, "best_round": 1}


def _critique(verdict="changes_requested", n=2):
    return {"verdict": verdict,
            "findings": [{"severity": "high", "explanation": "finding %d text" % i}
                         for i in range(n)]}


class UnresolvedNote(unittest.TestCase):
    def test_stall_with_standing_critique_ships_the_banner(self):
        note = deliberation.unresolved_note(_conv(False), _critique(n=2))
        self.assertIsNotNone(note, "a stalled changes_requested exit MUST be labeled")
        self.assertIn("2 unresolved finding(s)", note)
        self.assertIn("finding 0 text", note)
        self.assertIn("finding 1 text", note)

    def test_converged_returns_none(self):
        """POSITIVE CONTROL inverted: the reviewer signed off — no banner, or the signal is noise."""
        self.assertIsNone(deliberation.unresolved_note(_conv(True, "converged"), _critique()))

    def test_clean_draft_never_ran_converge(self):
        self.assertIsNone(deliberation.unresolved_note(None, _critique(verdict="pass")))
        self.assertIsNone(deliberation.unresolved_note(None, _critique()))

    def test_final_verdict_pass_returns_none(self):
        """Stall on the ROUND count but a passing final critique = resolved; no banner."""
        self.assertIsNone(deliberation.unresolved_note(_conv(False), _critique(verdict="pass")))

    def test_empty_findings_returns_none(self):
        self.assertIsNone(deliberation.unresolved_note(_conv(False), _critique(n=0)))

    def test_cap_and_overflow_are_stated(self):
        note = deliberation.unresolved_note(_conv(False), _critique(n=5), cap=3)
        self.assertIn("5 unresolved finding(s)", note)
        self.assertIn("(+2 more in the stored trail)", note)

    def test_string_findings_survive(self):
        """The findings normalizer upstream can emit bare strings; the note must not crash on them."""
        conv, crit = _conv(False), {"verdict": "changes_requested", "findings": ["bare string finding"]}
        self.assertIn("bare string finding", deliberation.unresolved_note(conv, crit))


class UnresolvedVerdict(unittest.TestCase):
    """Tests T1-T5 for the new unresolved_verdict critical-severity gating."""

    # T1: converged review -> ("ship", None)
    def test_t1_converged_ships(self):
        action, text = deliberation.unresolved_verdict(_conv(True), _critique())
        self.assertEqual(action, "ship")
        self.assertIsNone(text)

    # T2: unconverged, findings all medium/low -> ("warn", text); wrapper equivalence
    def test_t2_warn_and_wrapper_equivalence(self):
        action, text = deliberation.unresolved_verdict(_conv(False), _critique(n=2))
        self.assertEqual(action, "warn")
        self.assertIsNotNone(text)
        # Wrapper equivalence: unresolved_note must return the same text
        note = deliberation.unresolved_note(_conv(False), _critique(n=2))
        self.assertEqual(note, text)

    # T3: unconverged with one critical among others -> ("refuse", message); critical listed first
    def test_t3_refuse_with_critical_first(self):
        findings = [
            {"severity": "high", "explanation": "high finding text"},
            {"severity": "critical", "explanation": "critical finding text"},
            {"severity": "low", "explanation": "low finding text"},
        ]
        critique = {"verdict": "changes_requested", "findings": findings}
        action, message = deliberation.unresolved_verdict(_conv(False), critique)
        self.assertEqual(action, "refuse")
        self.assertIn("1 unresolved CRITICAL", message)
        # Critical finding must appear before non-critical ones in the listing
        crit_pos = message.index("critical finding text")
        high_pos = message.index("high finding text")
        low_pos = message.index("low finding text")
        self.assertLess(crit_pos, high_pos)
        self.assertLess(crit_pos, low_pos)
        self.assertIn("The draft answer is preserved in the stored trail.", message)

    # T4: severity "Critical" (capitalized) still refuses — case-insensitive
    def test_t4_case_insensitive_critical(self):
        findings = [
            {"severity": "Critical", "explanation": "capitalized critical finding"},
        ]
        critique = {"verdict": "changes_requested", "findings": findings}
        action, message = deliberation.unresolved_verdict(_conv(False), critique)
        self.assertEqual(action, "refuse")
        self.assertIn("CRITICAL", message)

    # T5: unconverged with EMPTY findings -> ("ship", None)
    def test_t5_empty_findings_ship(self):
        action, text = deliberation.unresolved_verdict(_conv(False), _critique(n=0))
        self.assertEqual(action, "ship")
        self.assertIsNone(text)


class CallSiteTests(unittest.TestCase):
    """T6-T8: drive the REAL exit gate (app._apply_unresolved_gate) with canned
    converge/critique inputs — never a copy of its logic, so drift in the production
    block fails here."""

    def _simulate_exit(self, conv, critique, final_answer="draft answer text"):
        import app
        extras = {"work_journal": {}, "transcript": []}
        out = app._apply_unresolved_gate(final_answer, conv, critique, extras)
        return out, extras

    # T6: call-site warn path — answer ends with banner, unresolved_findings persisted
    def test_t6_warn_path_appends_banner_and_persists_findings(self):
        critique = _critique(n=2)
        answer, extras = self._simulate_exit(_conv(False), critique, "my answer")
        # Answer must end with the warning banner
        self.assertIn("unresolved finding(s)", answer)
        self.assertNotEqual(answer, "my answer")  # was modified
        self.assertTrue(answer.startswith("my answer"))
        # unresolved_findings persisted
        self.assertIn("unresolved_findings", extras)
        self.assertEqual(len(extras["unresolved_findings"]), 2)
        # No refused_draft in warn path
        self.assertNotIn("refused_draft", extras)

    # T7: call-site refuse path — final_answer IS refusal, both keys persisted
    def test_t7_refuse_path_replaces_answer_and_preserves_draft(self):
        findings = [
            {"severity": "critical", "explanation": "fatal flaw"},
            {"severity": "high", "explanation": "concerning issue"},
        ]
        critique = {"verdict": "changes_requested", "findings": findings}
        draft = "the original answer that must be preserved"
        answer, extras = self._simulate_exit(_conv(False), critique, draft)
        # final_answer IS the refusal message (not the draft appended-with-banner)
        self.assertIn("I can't stand behind this answer", answer)
        self.assertNotIn(draft, answer)
        # unresolved_findings persisted
        self.assertIn("unresolved_findings", extras)
        self.assertEqual(len(extras["unresolved_findings"]), 2)
        # refused_draft preserves the original answer
        self.assertEqual(extras["refused_draft"], draft)

    # T8: converged at call-site — answer untouched, no unresolved extras keys
    def test_t8_converged_no_extras(self):
        critique = _critique(n=2)
        original = "clean answer"
        answer, extras = self._simulate_exit(_conv(True), critique, original)
        # Answer completely unchanged
        self.assertEqual(answer, original)
        # No unresolved_findings or refused_draft in extras
        self.assertNotIn("unresolved_findings", extras)
        self.assertNotIn("refused_draft", extras)


if __name__ == "__main__":
    unittest.main()
