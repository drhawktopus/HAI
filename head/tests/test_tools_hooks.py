"""The tool module's hooks with no extension loaded: each hook gives its default, so a head without extensions has no
subject buckets, no style-suppressed scene class, and refuses the review's subject workflow in plain words."""
import unittest
from unittest import mock

import config
import tools


class AHeadWithoutExtensions(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(tools, "_EXTENSIONS", [])
        p.start()
        self.addCleanup(p.stop)

    def test_a_hook_nobody_supplies_is_its_default(self):
        self.assertEqual(tools._ext_hook("NO_SUCH_HOOK", "default"), "default")
        self.assertIsNone(tools._ext_hook("NO_SUCH_HOOK"))

    def test_there_are_no_subject_buckets_and_no_style_suppressed_scene(self):
        self.assertEqual(tools._subjects()["buckets"], [])
        self.assertIsNone(tools._subjects()["classify"]("anything at all"))
        self.assertIsNone(tools._ext_hook("STYLE_SUPPRESSED_SCENE", tools._NEVER_MATCHES).search("any scene words"))

    def test_the_subject_workflow_says_so(self):
        admin = sorted(config.ADMIN_USERS)[0] if config.ADMIN_USERS else "admin"
        with mock.patch.object(config, "ADMIN_USERS", {admin}), \
                mock.patch.object(tools, "dataset_flags", lambda u: {"a.jpg": "keep"}), \
                mock.patch.object(tools, "dataset_caption_flags", lambda u: {}):
            self.assertIn("no subject buckets", tools.dataset_review(admin, {"action": "subjects"}))


if __name__ == "__main__":
    unittest.main()
