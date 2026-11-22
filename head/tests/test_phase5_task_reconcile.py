import os
os.environ.setdefault("EMBED_ENABLED", "0")
import unittest
import app


class TaskReconcileTests(unittest.TestCase):
    def test_close_claim_with_active_task_notes(self):
        active = [{"id": "t1234567xyz", "objective": "Add feature X", "status": "active"}]
        note = app._reconcile_task_claims("I marked the task complete and we're done.", active)
        self.assertTrue(note)
        self.assertIn("t1234567", note)
        self.assertIn("Add feature X", note)

    def test_close_claim_no_active_task_no_note(self):
        self.assertEqual(app._reconcile_task_claims("I marked the task complete.", []), "")

    def test_active_task_no_claim_no_note(self):
        active = [{"id": "t1", "objective": "x", "status": "active"}]
        self.assertEqual(app._reconcile_task_claims("Here is the plan; still working.", active), "")

    def test_various_close_phrasings_fire(self):
        active = [{"id": "t1", "objective": "x", "status": "active"}]
        for s in ["task is now complete", "finished the task", "task done", "marked the task as finished"]:
            self.assertTrue(app._reconcile_task_claims(s, active), s)


if __name__ == "__main__":
    unittest.main()