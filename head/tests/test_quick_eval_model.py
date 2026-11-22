"""The quick tier uses a measurement-only model swap: ghost test users may run another model through the real pipeline, but a real user's request must never carry this swap into production."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import config  # noqa: E402


class QuickEvalModelTests(unittest.TestCase):
    def test_a_ghost_test_user_on_the_brains_box_gets_the_named_model(self):
        q = app.Q(text="x", user="test-homelab", eval_model=" qwen3:8b ")
        self.assertEqual(app.quick_eval_model(q, config.BRAIN_HOST), "qwen3:8b")
        self.assertEqual(app.quick_eval_model(q, config.BRAIN_HOST + "/"), "qwen3:8b")

    def test_a_real_user_never_gets_it(self):
        for user in sorted(config.ADMIN_USERS)[:1] + ["Avery", "", "contest"]:
            q = app.Q(text="x", user=user, eval_model="qwen3:8b")
            self.assertIsNone(app.quick_eval_model(q, config.BRAIN_HOST), user)

    def test_another_box_or_no_name_changes_nothing(self):
        self.assertIsNone(app.quick_eval_model(app.Q(text="x", user="test", eval_model="qwen3:8b"),
                                               config.FAILOVER_HOST))
        self.assertIsNone(app.quick_eval_model(app.Q(text="x", user="test"), config.BRAIN_HOST))
        self.assertIsNone(app.quick_eval_model(app.Q(text="x", user="test", eval_model="  "), config.BRAIN_HOST))


if __name__ == "__main__":
    unittest.main()
