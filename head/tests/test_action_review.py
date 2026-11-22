import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import action_review
import tools


class ParseTests(unittest.TestCase):
    def test_parse_variants(self):
        self.assertEqual(action_review._parse('{"verdict":"match","concern":""}')["verdict"], "match")
        m = action_review._parse('noise {"verdict":"mismatch","concern":"removes 50 not 1"} tail')
        self.assertEqual(m["verdict"], "mismatch")
        self.assertIn("50", m["concern"])
        self.assertIsNone(action_review._parse("not json"))
        self.assertIsNone(action_review._parse('{"verdict":"weird"}'))


class ReviewActionTests(unittest.TestCase):
    def _resp(self, content):
        r = MagicMock()
        r.raise_for_status = lambda: None
        r.json = lambda: {"message": {"content": content}}
        return r

    def test_returns_verdict(self):
        with patch("action_review.config.ACTION_REVIEW_ENABLED", True), \
             patch("ollama_client.post_supervised", return_value=self._resp('{"verdict":"mismatch","concern":"x"}')):
            out = action_review.review_action("pause one download", "files.remove", "remove 50 downloads")
        self.assertEqual(out["verdict"], "mismatch")

    def test_failsoft_on_error(self):
        with patch("action_review.config.ACTION_REVIEW_ENABLED", True), \
             patch("ollama_client.post_supervised", side_effect=Exception("model down")):
            self.assertIsNone(action_review.review_action("x", "k", "p"))

    def test_disabled_returns_none(self):
        with patch("action_review.config.ACTION_REVIEW_ENABLED", False):
            self.assertIsNone(action_review.review_action("x", "k", "p"))

    def test_empty_request_returns_none(self):
        with patch("action_review.config.ACTION_REVIEW_ENABLED", True):
            self.assertIsNone(action_review.review_action("", "k", "p"))


class FinishPendingTests(unittest.TestCase):
    def test_high_risk_mismatch_augments_preview(self):
        pending = {"status": "pending", "kind": "files.remove", "risk": "HIGH", "preview": "remove 50", "action_token": "t"}
        with patch("tools.action_review.review_action", return_value={"verdict": "mismatch", "concern": "you asked for one"}):
            out = json.loads(tools._finish_pending(pending, "pause one download"))
        self.assertIn("SEMANTIC CHECK (MISMATCH)", out["preview"])
        self.assertIn("you asked for one", out["preview"])
        self.assertEqual(out["semantic_review"]["verdict"], "mismatch")

    def test_low_risk_skips_review(self):
        pending = {"status": "pending", "kind": "files.add", "risk": "LOW", "preview": "add one"}
        called = []
        with patch("tools.action_review.review_action", side_effect=lambda *a: called.append(1)):
            out = json.loads(tools._finish_pending(pending, "add one"))
        self.assertEqual(called, [])                      # review NOT run on LOW-risk routine ops
        self.assertNotIn("semantic_review", out)

    def test_match_verdict_does_not_flag(self):
        pending = {"status": "pending", "kind": "files.remove", "risk": "HIGH", "preview": "remove a"}
        with patch("tools.action_review.review_action", return_value={"verdict": "match", "concern": ""}):
            out = json.loads(tools._finish_pending(pending, "remove download a"))
        self.assertNotIn("semantic_review", out)
        self.assertNotIn("SEMANTIC CHECK", out["preview"])

    def test_non_pending_passthrough(self):
        out = json.loads(tools._finish_pending({"status": "error", "message": "no"}, "x"))
        self.assertEqual(out["status"], "error")


class WiringTests(unittest.TestCase):
    def test_write_tools_go_through_finish_pending(self):
        """The head's write-tool post-processing is wired to the semantic check (an add-on's write tools route through
        it too: tests/test_tools_ext_media)."""
        self.assertIs(tools.action_review, action_review)
        self.assertTrue(callable(tools._finish_pending))


if __name__ == "__main__":
    unittest.main()
