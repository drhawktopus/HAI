"""Pins for triage_classify._assert_resident -- the residency gate both triage lanes share.

  resident        -> pass through, no calls
  absent          -> one clean load, then judge (absent is not occupied)
  spilled         -> evict (keep_alive 0) + clean reload, then judge
  still spilled   -> refuse (the card is genuinely occupied)
"""
import unittest
from unittest import mock

import triage_classify as C


class TestAssertResident(unittest.TestCase):
    def test_fully_resident_passes_without_touching_the_model(self):
        calls = []
        with mock.patch.object(C, "_residency", lambda h, m: (100, 100)), \
             mock.patch.object(C, "_chat", lambda *a, **k: calls.append(1)):
            C._assert_resident("http://x", "m", 4096)
        self.assertEqual(calls, [], "a healthy placement must not be reloaded")

    def test_absent_model_gets_one_clean_load_then_passes(self):
        # an absent model must be loaded and judged, never refused outright
        seq = [(None, None), (200, 200)]
        chats = []
        with mock.patch.object(C, "_residency", lambda h, m: seq.pop(0)), \
             mock.patch.object(C, "_chat", lambda *a, **k: chats.append(a)):
            C._assert_resident("http://x", "m", 4096)
        self.assertEqual(len(chats), 1, "exactly one clean load for an absent model")

    def test_spilled_model_is_evicted_then_reloaded(self):
        seq = [(50, 100), (200, 200)]
        posts, chats = [], []

        class _R:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(C, "_residency", lambda h, m: seq.pop(0)), \
             mock.patch.object(C, "_chat", lambda *a, **k: chats.append(a)), \
             mock.patch.object(C.urllib.request, "urlopen",
                               lambda req, timeout=0: posts.append(req) or _R()), \
             mock.patch.object(C.time, "sleep", lambda s: None):
            C._assert_resident("http://x", "m", 4096)
        self.assertEqual(len(posts), 1, "one eviction POST")
        self.assertEqual(len(chats), 1, "one clean reload")

    def test_still_spilled_after_reload_refuses_rc2(self):
        seq = [(None, None), (50, 100)]
        with mock.patch.object(C, "_residency", lambda h, m: seq.pop(0)), \
             mock.patch.object(C, "_chat", lambda *a, **k: None):
            with self.assertRaises(SystemExit) as cm:
                C._assert_resident("http://x", "m", 4096)
        self.assertIn("REFUSED", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
