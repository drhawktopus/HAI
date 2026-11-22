"""A tool raising must not end the run.

Drive real chat() over real tools.run_tool; patching run_tool bypasses the logic under test."""
import json
import unittest
from unittest import mock

import cancellation
import coding_review
import ollama_client
import tools

REAL_MESSAGE = "'str' object has no attribute 'get'"


def _reply(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"message": msg, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 20}


class ACrashedToolReturnsToTheModel(unittest.TestCase):

    def _run(self, crash):
        seq = [_reply(tool_calls=[{"function": {"name": "coding_review", "arguments": {}}}]), _reply(content="all done")]
        seen = []

        def fake_once(host, model, messages, *a, **k):
            seen.append([dict(m) for m in messages])
            return seq.pop(0)

        with mock.patch.object(ollama_client, "_chat_once", side_effect=fake_once), \
                mock.patch.object(tools, "_run_tool_impl", side_effect=crash):
            answer, _esc = ollama_client.chat("http://h", "m", [{"role": "user", "content": "go"}], [], num_ctx=4096)
        return answer, seen[-1]

    def test_the_run_goes_on_to_its_answer(self):
        answer, messages = self._run(AttributeError(REAL_MESSAGE))
        self.assertEqual(answer, "all done")
        tool_rows = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_rows), 1, "the model must be handed the tool's result")
        said = json.loads(tool_rows[0]["content"])["error"]
        self.assertIn("coding_review", said)
        self.assertIn(REAL_MESSAGE, said)

    def test_a_crash_reads_as_a_snag_on_the_status_line(self):
        out = tools._tool_crash("coding_review", "x", AttributeError(REAL_MESSAGE))
        self.assertTrue(ollama_client._tool_snag(out))

    def test_the_crash_is_logged_with_its_traceback(self):
        with mock.patch.object(tools, "_run_tool_impl", side_effect=AttributeError(REAL_MESSAGE)), \
                mock.patch.object(tools.notes, "log") as log:
            tools.run_tool("coding_review", {}, "", user="x")
        crash_lines = [c.args[0] for c in log.call_args_list if c.args[0].startswith("TOOL CRASH")]
        self.assertEqual(len(crash_lines), 1)
        self.assertIn("Traceback", crash_lines[0])

    def test_a_logger_that_fails_cannot_replace_the_answer(self):
        with mock.patch.object(tools, "_run_tool_impl", side_effect=AttributeError(REAL_MESSAGE)), \
                mock.patch.object(tools.notes, "log", side_effect=OSError("disk full")):
            out = tools.run_tool("coding_review", {}, "", user="x")
        self.assertIn(REAL_MESSAGE, json.loads(out)["error"])


class TheOperatorsStopsStillStop(unittest.TestCase):
    """The catch must not swallow a stop: these two are how the operator ends or parks a run."""

    def test_cancelled_passes_through(self):
        with mock.patch.object(tools, "_run_tool_impl", side_effect=cancellation.Cancelled()):
            with self.assertRaises(cancellation.Cancelled):
                tools.run_tool("coding_review", {}, "", user="x")

    def test_a_suspended_run_passes_through(self):
        with mock.patch.object(tools, "_run_tool_impl", side_effect=ollama_client.RunSuspended()):
            with self.assertRaises(ollama_client.RunSuspended):
                tools.run_tool("coding_review", {}, "", user="x")


class FakeClient:
    def __init__(self, notes_):
        self.notes = notes_

    def workspace(self, *_a, **_k):
        return {"id": "w"}

    def task(self, *_a, **_k):
        return {"workspace": "w", "notes": self.notes}

    def git(self, *_a, **_k):
        return {"stdout": ""}


class ReviewReadsBothNoteShapes(unittest.TestCase):

    def _review(self, notes_):
        with mock.patch.object(coding_review, "_coding_client", return_value=FakeClient(notes_)):
            return coding_review.review_task("w", "t")

    def test_the_note_that_ended_the_run_no_longer_trips_it(self):
        # Byte for byte what the crashed task held. An empty diff is the next stop, which proves it got past the note check.
        out = self._review(["Repeated identical test failure; stopped automatic retries."])
        self.assertEqual(out, {"error": "there is no diff to review"})

    def test_a_review_marker_is_seen_in_the_dict_shape(self):
        out = self._review([{"timestamp": "t", "text": coding_review.REVIEW_MARKER + " done"}])
        self.assertIn("already used", out["error"])

    def test_a_review_marker_is_seen_in_the_string_shape(self):
        out = self._review([coding_review.REVIEW_MARKER + " done"])
        self.assertIn("already used", out["error"])


if __name__ == "__main__":
    unittest.main()
