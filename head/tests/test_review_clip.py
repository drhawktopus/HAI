"""clip_for_review: raise the review-diff cap AND, when it must clip, tell the critic the truncation is
OURS (so it stops raising false 'incomplete diff / truncated file / duplication remains' findings)."""
import unittest

import deliberation


class ClipForReviewTests(unittest.TestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(deliberation.clip_for_review("abc", 100), "abc")

    def test_at_limit_unchanged(self):
        s = "x" * 50
        self.assertEqual(deliberation.clip_for_review(s, 50), s)   # no note when it fits exactly

    def test_over_limit_clips_and_adds_truncation_note(self):
        out = deliberation.clip_for_review("y" * 200, 50)
        self.assertTrue(out.startswith("y" * 50))
        self.assertIn("REVIEW NOTE", out)
        self.assertIn("truncated to 50 chars", out)
        self.assertIn("do NOT report it as incomplete", out)   # the anti-false-flag guard

    def test_none_and_empty(self):
        self.assertEqual(deliberation.clip_for_review(None, 10), "")
        self.assertEqual(deliberation.clip_for_review("", 10), "")


if __name__ == "__main__":
    unittest.main()
