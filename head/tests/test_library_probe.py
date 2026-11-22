"""Pins for library_probe.py -- the Obsidian-restructure retrieval acceptance gate (#30).

rag.search is injected (no live index in a unit suite). Pins: expected page found within
top-k passes with its rank; not-in-top-k fails; multi-expect; empty set refuses.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library_probe as P


def _fake(mapping):
    """mapping: question -> list of source basenames, in retrieval order."""
    def search(q, k, namespace):
        return [{"source": s} for s in mapping.get(q, [])[:k]]
    return search


class TestProbe(unittest.TestCase):
    def test_expected_page_in_topk_passes_with_rank(self):
        rep = P.probe(
            [{"question": "how do I set up USB networking?",
              "expect": "Fellowship-USB-Networking.md"}],
            k=4,
            search_fn=_fake({"how do I set up USB networking?":
                             ["Other.md", "Fellowship-USB-Networking.md", "Z.md"]}))
        self.assertEqual(rep["passed"], 1)
        self.assertEqual(rep["results"][0]["rank"], 2)

    def test_not_retrieved_fails(self):
        rep = P.probe(
            [{"question": "q", "expect": "Wanted.md"}],
            k=4, search_fn=_fake({"q": ["A.md", "B.md"]}))
        self.assertEqual(rep["failed"], 1)
        self.assertIsNone(rep["results"][0]["rank"])
        self.assertFalse(rep["results"][0]["pass"])

    def test_beyond_k_is_a_fail(self):
        # expected page exists but ranks 5th; k=4 must not count it
        rep = P.probe(
            [{"question": "q", "expect": "Deep.md"}],
            k=4, search_fn=_fake({"q": ["A.md", "B.md", "C.md", "D.md", "Deep.md"]}))
        self.assertEqual(rep["failed"], 1)

    def test_multi_expect_any_match_passes(self):
        rep = P.probe(
            [{"question": "q", "expect": ["A.md", "B.md"]}],
            k=4, search_fn=_fake({"q": ["Z.md", "B.md"]}))
        self.assertTrue(rep["results"][0]["pass"])
        self.assertEqual(rep["results"][0]["rank"], 2)

    def test_summary_counts(self):
        rep = P.probe(
            [{"question": "q1", "expect": "A.md"},
             {"question": "q2", "expect": "B.md"}],
            k=4, search_fn=_fake({"q1": ["A.md"], "q2": ["X.md"]}))
        self.assertEqual((rep["passed"], rep["failed"], rep["total"]), (1, 1, 2))


class TestCLI(unittest.TestCase):
    def test_empty_probe_set_refuses(self):
        import io
        import json
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "ps.json")
        io.open(p, "w", encoding="utf-8").write(json.dumps({"probes": []}))
        with self.assertRaises(SystemExit) as cm:
            P.main([p])
        self.assertIn("empty", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
