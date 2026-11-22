"""Test that a tool call whose arguments are incomplete does not crash the tool or the server."""
import json
import unittest
from unittest import mock

import coding_client
import ollama_client
import tools

# the real shape, from the window's own item: a string open when the window ended
CUT = '{"workspace":"hai-scratch","path":"box2/box2-a/body.html","new":"<!DOCTYPE html>\\n<html lang=\\"en\\">\\n    col += skin*diff2*vec3(0.85,0'


class ACutCall(unittest.TestCase):
    def test_whole_arguments_pass_through(self):
        self.assertEqual(ollama_client._call_arguments('{"path": "a"}', "stop"), {"path": "a"})
        self.assertEqual(ollama_client._call_arguments("", "stop"), {})

    def test_cut_arguments_become_a_shape_the_engine_can_read_back(self):
        a = ollama_client._call_arguments(CUT, "length")
        self.assertEqual(a, {ollama_client.CUT_CALL_KEY: len(CUT), "ended_at_the_window": True})
        wire = ollama_client._openai_messages([{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "coding_patch", "arguments": a}}]}])
        json.loads(wire[0]["tool_calls"][0]["function"]["arguments"])

    def test_it_does_not_run_and_says_why(self):
        with mock.patch.object(coding_client, "apply_patch", side_effect=AssertionError("a cut call ran"), create=True), \
                mock.patch.object(tools, "_run_tool_impl", side_effect=AssertionError("a cut call ran")):
            out = json.loads(tools.run_tool("coding_patch", ollama_client._call_arguments(CUT, "length"), "", user="Sam"))
        self.assertIn("cut off", out["error"])
        self.assertIn("context window", out["error"])
        self.assertIn("smaller pieces", out["error"])

    def test_a_whole_call_still_reaches_the_tool(self):
        with mock.patch.object(tools, "_run_tool_impl", return_value="ran") as impl:
            self.assertEqual(tools.run_tool("coding_read", {"path": "a"}, "", user="Sam"), "ran")
        impl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
