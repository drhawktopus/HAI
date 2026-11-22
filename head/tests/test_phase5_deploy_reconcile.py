import os
os.environ.setdefault("EMBED_ENABLED", "0")
import unittest
import app


class DeployClaimReconcileTests(unittest.TestCase):
    def test_claim_with_non_done_phase_notes(self):
        note = app._reconcile_deploy_claims("It's deployed and live now.",
                                            {"phase": "rolled_back", "job": "J9"})
        self.assertTrue(note)
        self.assertIn("rolled_back", note)
        self.assertIn("J9", note)

    def test_claim_with_done_phase_no_note(self):
        self.assertEqual(app._reconcile_deploy_claims("Deployment complete.", {"phase": "done", "job": "J1"}), "")

    def test_no_claim_no_note(self):
        self.assertEqual(app._reconcile_deploy_claims("Still building the change.", {"phase": "canary_build"}), "")

    def test_non_dict_status_no_note(self):
        self.assertEqual(app._reconcile_deploy_claims("deployed to live", None), "")


if __name__ == "__main__":
    unittest.main()