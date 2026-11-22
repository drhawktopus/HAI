"""llama-server (Strata / llama-swap) reports the whole prompt as prompt_eval_count; timings.prompt_n is only new tokens, while timings.cache_n is the cached prefix. Compaction weighs the sum."""
import json
import unittest
from unittest.mock import patch

import ollama_client


class _FakeSSE:
    def __init__(self, lines):
        self.lines = lines
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b""

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for ln in self.lines:
            yield ln


def _stream(timings=None, usage=None):
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]}),
             "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                                    "timings": timings or {}, "usage": usage or {}}),
             "data: [DONE]"]
    return _FakeSSE(lines)


class SwapPromptCountTests(unittest.TestCase):
    def _turn(self, fake):
        with patch("ollama_client._open_chat_stream", return_value=fake):
            return ollama_client._chat_once_swap("http://swap", "http://h", "m", [{"role": "user", "content": "q"}],
                                                 [], 65536, 60, False)

    def test_cached_prefix_counts_toward_the_prompt(self):
        r = self._turn(_stream({"prompt_n": 600, "cache_n": 9000, "predicted_n": 40,
                                "prompt_ms": 100.0, "predicted_ms": 900.0}))
        self.assertEqual(r["prompt_eval_count"], 9600)
        self.assertEqual(r["prompt_eval_cached_count"], 9000)
        self.assertEqual(r["eval_count"], 40)
        self.assertEqual(r["message"]["content"], "ok")

    def test_without_cache_n_the_old_fields_stand(self):
        r = self._turn(_stream({"prompt_n": 700, "predicted_n": 5}, usage={"prompt_tokens": 7000}))
        self.assertEqual(r["prompt_eval_count"], 7000)
        r = self._turn(_stream({"prompt_n": 700, "predicted_n": 5}))
        self.assertEqual(r["prompt_eval_count"], 700)
        self.assertIsNone(r["prompt_eval_cached_count"])


if __name__ == "__main__":
    unittest.main()
