"""Tolkien's share is ONE derivation: tolkien_llm's published model list and accepted engine files sit in the folder
tablet_foundry's outbox sits in, so a tablet head laid out differently (the public example mounts it at /jobs) moves
both together."""
import os
import unittest

import tablet_foundry
import tolkien_llm


class JobsRootFollowsTheOutbox(unittest.TestCase):
    def test_the_two_modules_name_the_same_share(self):
        self.assertEqual(tolkien_llm.JOBS_ROOT, os.path.dirname(tablet_foundry.OUTBOX.rstrip("/")))

    def test_what_tolkien_publishes_and_accepts_lives_in_that_share(self):
        if not os.environ.get("TOLKIEN_MODELS_MANIFEST"):
            self.assertTrue(tolkien_llm.MODELS_MANIFEST.startswith(tolkien_llm.JOBS_ROOT + "/"))
        self.assertTrue(tolkien_llm.ENGINE_ENV_PATH_ROOT.startswith(tolkien_llm.JOBS_ROOT + "/"))
        self.assertTrue(tolkien_llm.engine_env_path_ok(tolkien_llm.ENGINE_ENV_PATH_ROOT + "ranking.json"))
        self.assertFalse(tolkien_llm.engine_env_path_ok("/elsewhere/ranking.json"))


if __name__ == "__main__":
    unittest.main()
