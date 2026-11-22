"""A tool result must reach ollama's /api/chat as a string.

register-based tools (deploy, library_apply_*) return a pending-action payload DICT. Appended raw
to the messages, ollama rejects the whole turn — 'json: cannot unmarshal object into ...
messages.content of type string' — after the tool has already run and registered its side effect.
The loop must coerce a non-string result to text the model can still read."""
import json
import unittest
from unittest import mock

import ollama_client


def _reply(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"message": msg, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 20}


class ToolResultContentHelper(unittest.TestCase):

    def test_string_passes_through_unchanged(self):
        self.assertEqual(ollama_client._tool_result_content("hi"), "hi")

    def test_dict_becomes_json_and_preserves_fields(self):
        out = ollama_client._tool_result_content(
            {"action_token": "T", "preview": "would convert 2 refs"})
        self.assertIsInstance(out, str)
        self.assertEqual(json.loads(out)["preview"], "would convert 2 refs")

    def test_list_becomes_json(self):
        out = ollama_client._tool_result_content([1, 2, 3])
        self.assertEqual(json.loads(out), [1, 2, 3])

    def test_unserializable_still_returns_a_string(self):
        circular = {}
        circular["self"] = circular  # json.dumps raises even with default=str -> except branch
        self.assertIsInstance(ollama_client._tool_result_content(circular), str)


class ToolLoopFeedsStringsToOllama(unittest.TestCase):
    """Drive the real chat() loop with a scripted tool call whose tool returns a dict."""

    def _run(self, tool_result):
        # chat() copies its messages list internally, so capture what the SECOND _chat_once call
        # actually saw — that snapshot carries the appended tool-result message.
        seq = [
            _reply(tool_calls=[{"function": {"name": "faketool", "arguments": {}}}]),
            _reply(content="all done"),
        ]
        captured = []

        def fake_once(host, model, messages, *a, **k):
            captured.append([dict(m) for m in messages])
            return seq.pop(0)

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                mock.patch.object(ollama_client.tools, "run_tool", return_value=tool_result):
            answer, _esc = ollama_client.chat(
                "http://h", "m", [{"role": "user", "content": "go"}], [], num_ctx=4096)
        return answer, captured[-1]

    def test_dict_tool_result_reaches_ollama_as_string(self):
        answer, msgs = self._run(
            {"status": "pending", "action_token": "Klk5",
             "preview": "would convert 2 refs across 1 file"})
        self.assertEqual(answer, "all done")
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        self.assertTrue(tool_msgs, "the tool result must have been appended to the messages")
        for m in tool_msgs:
            self.assertIsInstance(m["content"], str,
                                  "ollama /api/chat rejects a non-string content")
        self.assertIn("would convert 2 refs", tool_msgs[0]["content"])

    def test_string_tool_result_is_untouched(self):
        _answer, msgs = self._run("plain tool text")
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(tool_msgs[0]["content"], "plain tool text")


if __name__ == "__main__":
    unittest.main()
