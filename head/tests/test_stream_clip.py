"""Pins for the shared output-clipping rule (`stream_clip`) and `coding_client.compact`.

Ensures truncation preserves the final verdict/traceback (tail) and announces drops, while keeping
small payloads intact. Tests assert properties (length budgets, JSON validity, tail survival) rather
than source text, allowing implementation changes that still deliver the verdict."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coding_client
import stream_clip


def _unittest_shaped(n_chatter, verdict="Ran 1950 tests in 22.885s\n\nOK (skipped=10)\n"):
    """Output shaped like `python -m unittest -v`: chatter first, the ANSWER last."""
    chatter = "".join(f"test_thing_number_{i} (mod.Case.test_thing_number_{i}) ... ok\n"
                      for i in range(n_chatter))
    return chatter + verdict


class TestClipText(unittest.TestCase):
    def test_short_text_is_returned_untouched_and_unflagged(self):
        text = "small enough to survive whole"
        out, clipped = stream_clip.clip_text(text, 24000)
        self.assertEqual(out, text)
        self.assertFalse(clipped)

    def test_never_exceeds_the_limit(self):
        # Across a spread of budgets, including ones near the marker's own width.
        for limit in (50, 120, 400, 1000, 24000):
            for size in (10, 500, 5000, 200000):
                out, _ = stream_clip.clip_text("x" * size, limit)
                self.assertLessEqual(len(out), limit, f"limit={limit} size={size}")

    def test_keeps_BOTH_ends_not_just_the_head(self):
        text = "HEADMARKER" + ("m" * 50000) + "TAILMARKER"
        out, clipped = stream_clip.clip_text(text, 2000)
        self.assertTrue(clipped)
        self.assertIn("HEADMARKER", out)
        self.assertIn("TAILMARKER", out)   # the half the old head-cut destroyed

    def test_says_how_much_it_dropped(self):
        out, clipped = stream_clip.clip_text("y" * 100000, 1000)
        self.assertTrue(clipped)
        self.assertIn("dropped from the middle", out)
        # the announced number must be a real count, not a placeholder
        digits = [int(tok) for tok in out.replace("[", " ").split() if tok.isdigit()]
        self.assertTrue(any(d > 90000 for d in digits), f"no plausible drop count in {out[:200]!r}")

    def test_a_real_shaped_unittest_verdict_survives_the_clip(self):
        """THE pin. Chatter first, verdict last — the verdict must come through."""
        text = _unittest_shaped(4000)
        self.assertGreater(len(text), 24000)  # positive control: this really does need clipping
        out, clipped = stream_clip.clip_text(text, 24000)
        self.assertTrue(clipped)
        self.assertIn("Ran 1950 tests", out)
        self.assertIn("OK (skipped=10)", out)

    def test_a_failure_tail_survives_the_clip(self):
        text = _unittest_shaped(
            4000,
            verdict="FAIL: test_the_one_that_broke (mod.Case.test_the_one_that_broke)\n"
                    "Traceback (most recent call last):\n  AssertionError: 3 != 4\n\n"
                    "Ran 1950 tests in 22.9s\n\nFAILED (failures=1)\n",
        )
        out, _ = stream_clip.clip_text(text, 24000)
        for needle in ("test_the_one_that_broke", "Traceback", "AssertionError", "FAILED"):
            self.assertIn(needle, out)

    def test_degrades_to_the_TAIL_when_the_budget_is_tiny(self):
        # Too small for head+marker+tail: keep the answer end, never the chatter end.
        out, clipped = stream_clip.clip_text("A" * 500 + "THEANSWER", 12)
        self.assertTrue(clipped)
        self.assertIn("ANSWER", out)

    def test_zero_or_negative_budget_is_empty_but_flagged(self):
        out, clipped = stream_clip.clip_text("something", 0)
        self.assertEqual(out, "")
        self.assertTrue(clipped)


class TestClipBytes(unittest.TestCase):
    def test_never_exceeds_the_limit(self):
        for limit in (60, 500, 128000):
            out, _ = stream_clip.clip_bytes(b"z" * 400000, limit)
            self.assertLessEqual(len(out), limit)

    def test_keeps_both_ends_and_stays_decodable(self):
        raw = b"HEAD" + (b"\xc3\xa9" * 40000) + b"TAIL"   # multibyte middle
        out, clipped = stream_clip.clip_bytes(raw, 3000)
        self.assertTrue(clipped)
        self.assertIn(b"HEAD", out)
        self.assertIn(b"TAIL", out)
        out.decode("utf-8", errors="replace")   # must not raise even if a seam splits a char

    def test_a_real_shaped_unittest_verdict_survives(self):
        raw = _unittest_shaped(6000).encode()
        self.assertGreater(len(raw), 128000)    # positive control
        out, _ = stream_clip.clip_bytes(raw, 128000)
        self.assertIn(b"Ran 1950 tests", out)
        self.assertIn(b"OK (skipped=10)", out)


class TestCompact(unittest.TestCase):
    def test_small_payload_is_plain_json_unchanged(self):
        payload = {"returncode": 0, "stdout": "fine", "stderr": ""}
        out = coding_client.compact(payload)
        self.assertEqual(json.loads(out), payload)

    def test_big_test_result_stays_VALID_JSON(self):
        payload = {"argv": ["python", "-m", "unittest"], "returncode": 1, "timed_out": False,
                   "stdout": "", "stderr": _unittest_shaped(5000),
                   "stdout_truncated": False, "stderr_truncated": True}
        out = coding_client.compact(payload)
        parsed = json.loads(out)          # the old string-cut produced invalid JSON
        self.assertEqual(parsed["returncode"], 1)

    def test_the_verdict_reaches_the_caller(self):
        """What HAI actually receives must answer 'did it pass, and what broke?'"""
        payload = {"argv": ["python", "-m", "unittest", "-v"], "returncode": 1,
                   "timed_out": False, "stdout": "",
                   "stderr": _unittest_shaped(
                       5000,
                       verdict="FAIL: test_broke (m.C.test_broke)\nTraceback (most recent call "
                               "last):\n  AssertionError: nope\n\nRan 1950 tests\n\n"
                               "FAILED (failures=1)\n"),
                   "stdout_truncated": False, "stderr_truncated": True}
        out = coding_client.compact(payload)
        self.assertGreater(len(json.dumps(payload)), 24000)   # positive control
        for needle in ("Ran 1950 tests", "FAILED", "test_broke", "AssertionError"):
            self.assertIn(needle, out, f"{needle!r} did not survive compact()")

    def test_metadata_survives_whole(self):
        payload = {"argv": ["python"], "returncode": 3, "timed_out": True,
                   "stdout": "o" * 60000, "stderr": "e" * 60000,
                   "stdout_truncated": True, "stderr_truncated": True}
        parsed = json.loads(coding_client.compact(payload))
        self.assertEqual(parsed["returncode"], 3)
        self.assertTrue(parsed["timed_out"])
        self.assertTrue(parsed["stderr_truncated"])   # the RUNNER's own flag, not ours

    def test_clipping_is_announced_per_stream(self):
        payload = {"returncode": 0, "stdout": "o" * 60000, "stderr": "e" * 60000}
        parsed = json.loads(coding_client.compact(payload))
        self.assertTrue(parsed.get("stdout_clipped_for_tool_reply"))
        self.assertTrue(parsed.get("stderr_clipped_for_tool_reply"))

    def test_respects_the_limit(self):
        payload = {"returncode": 0, "stdout": "o" * 200000, "stderr": "e" * 200000}
        self.assertLessEqual(len(coding_client.compact(payload, limit=24000)), 24000)

    def test_payload_with_no_stream_fields_still_bounded(self):
        payload = {"notes": ["n" * 1000 for _ in range(100)]}
        out = coding_client.compact(payload, limit=5000)
        self.assertLessEqual(len(out), 5000 + 60)   # + the fallback marker


if __name__ == "__main__":
    unittest.main()
