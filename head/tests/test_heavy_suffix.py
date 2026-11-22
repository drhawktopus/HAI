"""Static checks for the gated Mothership system suffix (_heavy_suffix) in app.py.

Backlog E: the coding + supervised-job briefs used to ride on EVERY Mothership call
(HEAVY_CONTINUATION was one monolithic string). Now that Mothership is the default
responder they must only attach when the request needs them, so ordinary chat does not
pay ~1.6k chars of coding boilerplate."""
import unittest
from pathlib import Path

APP = Path("app.py").read_text(encoding="utf-8")


class HeavySuffixGatingTests(unittest.TestCase):
    def test_briefs_are_separate_constants(self):
        self.assertIn("HEAVY_CONTINUATION = (", APP)
        self.assertIn("CODING_MODE_BRIEF = (", APP)
        self.assertIn("SUPERVISED_JOB_BRIEF = (", APP)

    def test_identity_brief_no_longer_carries_coding_text(self):
        # The identity brief must not itself contain the coding/supervised sections.
        start = APP.index("HEAVY_CONTINUATION = (")
        end = APP.index("CODING_MODE_BRIEF = (")
        identity = APP[start:end]
        self.assertIn("INTERNAL HANDOFF", identity)
        self.assertNotIn("CODING MODE", identity)
        self.assertNotIn("SUPERVISED LOCAL CODING JOB MODE", identity)

    def test_helper_gates_on_intent(self):
        # Test behavior (returned strings), not source code text; internal refactors (e.g. moving briefs) must not break this.
        import app
        plain = app._heavy_suffix("what is the weather tomorrow")
        self.assertEqual(plain, app.HEAVY_CONTINUATION, "ordinary chat pays for the identity brief only")
        coding = app._heavy_suffix("please refactor this function")
        self.assertTrue(coding.startswith(app.HEAVY_CONTINUATION))
        self.assertIn(app.CODING_MODE_BRIEF, coding)
        self.assertNotIn(app.SUPERVISED_JOB_BRIEF, coding)
        job = app._heavy_suffix("SUPERVISED LOCAL CODING JOB\nGoal: tidy the notes")
        self.assertIn(app.SUPERVISED_JOB_BRIEF, job)

    def test_supervised_job_pattern_exists(self):
        self.assertIn('SUPERVISED_JOB = re.compile(r"SUPERVISED LOCAL CODING JOB"', APP)

    def test_all_append_sites_use_helper_not_raw_constant(self):
        # No code path may append the bare identity constant; every Mothership
        # system-prompt build must go through the gating helper.
        self.assertNotIn('"content"] += HEAVY_CONTINUATION', APP)
        # Call sites: think-hard first pass + refinement, and _dispatch_lead (which now
        # serves the force / warm-preference / escalation paths), plus the def = 4 total.
        self.assertEqual(APP.count('_heavy_suffix('), 3 + 1)


if __name__ == "__main__":
    unittest.main()
