import unittest
from unittest import mock

import notes
import ollama_client
import turn_stall
from test_stream_done_guard import _FakeStream, _chunk


class TestObserveStall(unittest.TestCase):

    def setUp(self):
        turn_stall._reset_for_tests()

    def test_exact_stall_signature_detected(self):
        """The measured signature: supervised, stop, 16 tokens, read-only tools, announces."""
        result = turn_stall.observe(
            answer="Creating the module now:",
            tool_names=["coding_task"],
            step=1, done_reason="stop", eval_tokens=16, supervised=True,
        )
        self.assertEqual(result["verdict"], "stalled")
        self.assertIn("16", result["why"])

    def test_normal_supervised_with_write_tool_is_ok(self):
        """A supervised turn that actually wrote something is not a stall."""
        result = turn_stall.observe(
            answer="Creating the module now:",
            tool_names=["coding_task", "coding_create"],
            step=1, done_reason="stop", eval_tokens=16, supervised=True,
        )
        self.assertEqual(result["verdict"], "ok")

    def test_unsupervised_never_policed(self):
        """Identical shape outside a supervised job -> ok. Ordinary chat is never policed."""
        result = turn_stall.observe(
            answer="Creating the module now:",
            tool_names=["coding_task"],
            step=1, done_reason="stop", eval_tokens=16, supervised=False,
        )
        self.assertEqual(result["verdict"], "ok")

    def test_eval_tokens_none_is_ok(self):
        """Missing metadata is could-not-measure, which is never a definite verdict."""
        result = turn_stall.observe(
            answer="Creating the module now:",
            tool_names=["coding_task"],
            step=1, done_reason="stop", eval_tokens=None, supervised=True,
        )
        self.assertEqual(result["verdict"], "ok")

    def test_announce_without_trailing_colon_stalled(self):
        """The announce pattern catches what the trailing-colon rule alone would miss."""
        result = turn_stall.observe(
            answer="Let me create the tests",
            tool_names=["coding_task"],
            step=1, done_reason="stop", eval_tokens=16, supervised=True,
        )
        self.assertEqual(result["verdict"], "stalled")

    def test_long_answer_high_tokens_is_ok(self):
        """A substantive answer ending in ':' is not a stall — the token bound is load-bearing."""
        long_ans = ("Turn-stall detection checks five conditions: supervised context, done_reason "
                    "stop, low eval tokens under sixty, no write-shaped tool calls, and an "
                    "announcement pattern in the answer text. This covers all axes:")
        result = turn_stall.observe(
            answer=long_ans, tool_names=["coding_task"],
            step=1, done_reason="stop", eval_tokens=500, supervised=True,
        )
        self.assertEqual(result["verdict"], "ok")


class TestConsecutiveStalls(unittest.TestCase):

    def setUp(self):
        turn_stall._reset_for_tests()

    def test_consecutive_increments_and_resets(self):
        """The counter feeds the nudge ladder, so it must count stalls and forget on success."""
        self.assertEqual(turn_stall.consecutive_stalls(), 0)
        turn_stall.observe("X:", ["coding_task"], 1, "stop", 16, True)
        self.assertEqual(turn_stall.consecutive_stalls(), 1)
        turn_stall.observe("Y:", ["coding_task"], 2, "stop", 10, True)
        self.assertEqual(turn_stall.consecutive_stalls(), 2)
        turn_stall.observe("Z:", ["coding_task", "coding_create"], 3, "stop", 16, True)
        self.assertEqual(turn_stall.consecutive_stalls(), 0)

    def test_reset_for_tests_zeroes(self):
        turn_stall.observe("X:", ["coding_task"], 1, "stop", 16, True)
        self.assertEqual(turn_stall.consecutive_stalls(), 1)
        turn_stall._reset_for_tests()
        self.assertEqual(turn_stall.consecutive_stalls(), 0)


class TestNudgeText(unittest.TestCase):

    def test_nudge_one_and_two_differ(self):
        """A retry must change something that matters — including what it says."""
        n1 = turn_stall.nudge_text(1)
        n2 = turn_stall.nudge_text(2)
        self.assertTrue(len(n1) > 0)
        self.assertTrue(len(n2) > 0)
        self.assertNotEqual(n1, n2)

    def test_nudge_three_is_empty(self):
        """Bounded: the third attempt hands off to a human instead of looping."""
        self.assertEqual(turn_stall.nudge_text(3), "")


class TestTurnStallIntegration(unittest.TestCase):
    """A broken detector must never be able to break chat."""

    def test_chat_survives_observe_explosion(self):
        self.logged = []
        self._orig = notes.log
        notes.log = lambda line: self.logged.append(str(line))
        fake = _FakeStream([_chunk("hello world"), _chunk(done=True)])
        try:
            with mock.patch.object(ollama_client, "_open_chat_stream", return_value=fake):
                with mock.patch("builtins.__import__") as mock_import:
                    def importer(name, *a, **k):
                        if name == "turn_stall":
                            import types
                            mod = types.ModuleType("turn_stall")
                            mod.observe = lambda **kw: (_ for _ in ()).throw(
                                RuntimeError("detector broken"))
                            return mod
                        raise ImportError(name)
                    mock_import.side_effect = importer
                    ans, esc = ollama_client.chat(
                        "http://h", "m", [{"role": "user", "content": "hi"}], [], 8192)
            self.assertEqual(ans, "hello world")
            self.assertIsNone(esc)
        finally:
            notes.log = self._orig


if __name__ == "__main__":
    unittest.main()
