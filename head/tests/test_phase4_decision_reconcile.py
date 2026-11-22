import os
os.environ.setdefault("EMBED_ENABLED", "0")
import unittest
import app


class DecisionReconcileTests(unittest.TestCase):
    def test_overclaim_appends_note(self):
        recorded = [{"id": "abc12345xyz", "question": "Overnight scope?", "resolved": True}]
        ans = "I resolved Gate #1, Gate #2, and Gate #3 for you."
        note = app._reconcile_decision_claims(ans, recorded)
        self.assertTrue(note)
        self.assertIn("actually contains", note)
        self.assertIn("abc12345", note)
        self.assertIn("Overnight scope?", note)

    def test_match_no_note(self):
        recorded = [{"id": "abc12345xyz", "question": "Overnight scope?", "resolved": True}]
        ans = "I created Gate #1 and recorded your choice."
        self.assertEqual(app._reconcile_decision_claims(ans, recorded), "")

    def test_no_claims_no_note(self):
        self.assertEqual(app._reconcile_decision_claims("Here is the plan; no gates yet.", []), "")

    def test_resolution_claim_empty_store(self):
        note = app._reconcile_decision_claims("Your decision gate is now resolved.", [])
        self.assertTrue(note)
        self.assertIn("none recorded", note)


if __name__ == "__main__":
    unittest.main()