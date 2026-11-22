import json
import unittest
from unittest import mock

import config
import notes
import ollama_client


class _FakeStream:
    """Minimal httpx.stream stand-in: yields the given lines, then ends."""

    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        return iter(self._lines)


def _chunk(content="", done=False, **extra):
    d = {"message": {"content": content}}
    if done:
        d.update({"done": True, "done_reason": "stop",
                  "prompt_eval_count": 10, "eval_count": 5})
    d.update(extra)
    return json.dumps(d)


class StreamDoneGuard(unittest.TestCase):
    def test_missing_done_chunk_raises_transient(self):
        """The whole defect: content arrives, the done chunk never does. Must raise, not return."""
        fake = _FakeStream([_chunk("Let me patch that next:")])
        with mock.patch.object(ollama_client, "_open_chat_stream", return_value=fake):
            with self.assertRaises(ValueError) as ctx:
                ollama_client._chat_once("http://h", "m", [], [], 8192, "5m")
        self.assertIn("without a done chunk", str(ctx.exception))

    def test_complete_stream_returns_metadata(self):
        """POSITIVE CONTROL: with the done chunk present, nothing raises and metadata is real."""
        fake = _FakeStream([_chunk("half "), _chunk("answer"), _chunk(done=True)])
        with mock.patch.object(ollama_client, "_open_chat_stream", return_value=fake):
            parsed = ollama_client._chat_once("http://h", "m", [], [], 8192, "5m")
        self.assertEqual(parsed["message"]["content"], "half answer")
        self.assertEqual(parsed["done_reason"], "stop")
        self.assertEqual(parsed["eval_count"], 5)


class TurnMetaAlwaysLogged(unittest.TestCase):
    def setUp(self):
        self.logged = []
        self._orig = notes.log
        notes.log = lambda line: self.logged.append(str(line))

    def tearDown(self):
        notes.log = self._orig

    def _reply(self, text):
        return {"message": {"content": text}, "done_reason": "stop",
                "prompt_eval_count": 100, "eval_count": 50}

    def test_long_answer_still_logs_metadata(self):
        """The 358/414-char instances sat ABOVE the short-answer bound and left no trail. A wordy
        pathology must not dodge the instrument: TURNMETA fires on every final message."""
        long_answer = "x" * (config.SHORTANS_CHARS + 200)
        with mock.patch.object(ollama_client, "_chat_once",
                               side_effect=lambda *a, **k: self._reply(long_answer)):
            ollama_client.chat("http://h", "m", [{"role": "user", "content": "hi"}], [], 8192)
        lines = [l for l in self.logged if "TURNMETA" in l]
        self.assertEqual(len(lines), 1, "exactly one TURNMETA per turn")
        self.assertIn("done_reason='stop'", lines[0])
        self.assertIn(f"answer_len={len(long_answer)}", lines[0])
        self.assertEqual([l for l in self.logged if "SHORTANS" in l], [],
                         "a healthy long answer must NOT trip the short-answer alarm")

    def test_short_answer_logs_both(self):
        """SHORTANS keeps its job as the alarm; TURNMETA rides along."""
        with mock.patch.object(ollama_client, "_chat_once",
                               side_effect=lambda *a, **k: self._reply("tiny")):
            ollama_client.chat("http://h", "m", [{"role": "user", "content": "hi"}], [], 8192)
        self.assertTrue([l for l in self.logged if "TURNMETA" in l])
        self.assertTrue([l for l in self.logged if "SHORTANS" in l])


if __name__ == "__main__":
    unittest.main()
