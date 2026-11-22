"""qwen3.8:27b leaks reasoning into `content` despite think=False. Fixture must stay byte-verbatim with tests/think_leak_replies.json to preserve the raw leak signature."""
import json
import os
import unittest
from unittest.mock import patch

import ollama_client

FIX = json.load(open(os.path.join(os.path.dirname(__file__), "think_leak_replies.json"), encoding="utf-8"))


class _FakeStream:
    def __init__(self, lines):
        self.lines, self.status_code = lines, 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        yield from self.lines


def _stream(text, piece=17):
    """The reply as ollama streams it: content in small chunks, then the done chunk."""
    lines = [json.dumps({"message": {"content": text[i:i + piece]}, "done": False}).encode()
             for i in range(0, len(text), piece)]
    lines.append(json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop",
                             "model": "qwen3.8:27b", "eval_count": 9}).encode())
    return _FakeStream(lines)


class ThinkLeak(unittest.TestCase):
    def test_fixture_is_real_and_shows_the_leak(self):
        self.assertEqual(len(FIX["leaked"]), 2)
        for r in FIX["leaked"]:
            self.assertIn("</think>", r["answer"], "positive control: the fixture must carry the leak")

    def test_each_real_leak_leaves_only_the_answer_in_content(self):
        for r in FIX["leaked"]:
            with patch("ollama_client._open_chat_stream", return_value=_stream(r["answer"])):
                out = ollama_client._chat_once_raw("http://h", "qwen3.8:27b", [], [], 8192, "5m")
            content = out["message"]["content"]
            self.assertNotIn("</think>", content, r["id"])
            self.assertTrue(out["think_leaked"], r["id"])
            verdict = json.loads(content)                      # the user-visible reply is the one clean verdict
            self.assertIn(verdict["verdict"], ("genuine_error", "correct_but_misgraded", "formatting_only", "other"))
            self.assertTrue(out["message"].get("thinking"), "the reasoning is kept, not discarded: " + r["id"])

    def test_a_clean_reply_is_untouched(self):
        r = FIX["clean"][0]
        with patch("ollama_client._open_chat_stream", return_value=_stream(r["answer"])):
            out = ollama_client._chat_once_raw("http://h", "qwen3.8:27b", [], [], 8192, "5m")
        self.assertEqual(out["message"]["content"], r["answer"])
        self.assertFalse(out["think_leaked"])
        self.assertNotIn("thinking", out["message"])

    def test_split_keeps_real_thinking_and_drops_an_opening_tag(self):
        c, t, leaked = ollama_client.split_leaked_thinking("<think>\nplan\n</think>\n\nanswer", "earlier")
        self.assertEqual((c, leaked), ("answer", True))
        self.assertEqual(t, "earlier\nplan")


if __name__ == "__main__":
    unittest.main()
