import unittest
from unittest import mock

import ollama_client


def _reply(content="", thinking=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"message": msg, "done_reason": "stop",
            "prompt_eval_count": 10, "eval_count": 20}


def _chat(thoughts=None, replies=None, tools_list=None):
    """Drive chat() with a scripted sequence of model replies."""
    seq = list(replies or [])

    def fake_once(*_a, **_k):
        return seq.pop(0)

    with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
            mock.patch.object(ollama_client, "_gate_for_render", create=True,
                              side_effect=lambda *a, **k: None):
        return ollama_client.chat("http://h", "m", [{"role": "user", "content": "hi"}],
                                  tools_list or [], num_ctx=4096, thoughts=thoughts)


class ThinkingCaptureTests(unittest.TestCase):

    def test_thinking_is_captured_on_a_plain_answer(self):
        trail = []
        answer, _esc = _chat(thoughts=trail,
                             replies=[_reply(content="the answer", thinking="let me weigh it up")])
        self.assertEqual(answer, "the answer")
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["thinking"], "let me weigh it up")

    def test_answer_is_not_replaced_by_thinking_when_content_exists(self):
        """The whole point of a separate panel: reasoning must never overwrite a real answer."""
        trail = []
        answer, _esc = _chat(thoughts=trail,
                             replies=[_reply(content="42", thinking="counting on fingers")])
        self.assertEqual(answer, "42")

    def test_empty_content_falls_back_to_thinking_AND_still_records_it(self):
        """The qwen3-vl signature. Returning nothing would be worse -- but the trail must still show
        this was reasoning, so the UI can say so instead of presenting a scratchpad as an answer."""
        trail = []
        answer, _esc = _chat(thoughts=trail,
                             replies=[_reply(content="", thinking="so the result is seven")])
        self.assertEqual(answer, "so the result is seven")
        self.assertEqual(len(trail), 1, "the fallback must not consume the trail entry")

    def test_no_thinking_produces_no_entries(self):
        """Non-thinking models must not gain an empty panel."""
        trail = []
        _chat(thoughts=trail, replies=[_reply(content="plain")])
        self.assertEqual(trail, [])

    def test_whitespace_only_thinking_is_ignored(self):
        trail = []
        _chat(thoughts=trail, replies=[_reply(content="plain", thinking="   \n  ")])
        self.assertEqual(trail, [])

    def test_omitting_the_accumulator_is_safe(self):
        """Nine existing call sites pass no thoughts= and must be completely unaffected."""
        answer, _esc = _chat(thoughts=None, replies=[_reply(content="fine", thinking="x")])
        self.assertEqual(answer, "fine")

    def test_entries_are_length_bounded(self):
        trail = []
        # >=2 chars deliberately: a shorter answer trips chat()'s truncated-answer guard, which is
        # a real protection against a degenerate stub and not something a test should route around.
        _chat(thoughts=trail, replies=[_reply(content="ok", thinking="z" * 5000)])
        self.assertLessEqual(len(trail[0]["thinking"]), 1200)


class ThinkingIsDisplayDataTests(unittest.TestCase):
    """Same discipline as the tool transcript: DATA for display, never re-fed to the model."""

    def test_trail_entries_carry_a_step_number(self):
        trail = []
        _chat(thoughts=trail, replies=[_reply(content="ok", thinking="reasoning")])
        self.assertIn("step", trail[0])


if __name__ == "__main__":
    unittest.main()
