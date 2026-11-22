"""Behavioral tests for RAG auto-reload on doc change (rag.maybe_reload).

Imports rag (needs rank_bm25), so this runs in the orchestrator container, not the Windows host.
Uses a throwaway namespace via rag.configure() so it never touches the real corpora.
"""
import os
import pathlib
import tempfile
import time
import unittest

import config
import rag


class AutoReloadTests(unittest.TestCase):
    NS = "test_autoreload"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._dir = pathlib.Path(self._tmp)
        self._orig_interval = config.RAG_RELOAD_CHECK_INTERVAL
        (self._dir / "a.md").write_text("# Title\nhello world reference content", encoding="utf-8")
        config.RAG_RELOAD_CHECK_INTERVAL = 0  # disable throttle for most tests
        rag.configure(self.NS, [self._dir])

    def tearDown(self):
        config.RAG_RELOAD_CHECK_INTERVAL = self._orig_interval

    def test_noop_when_unchanged(self):
        self.assertFalse(rag.maybe_reload(self.NS))

    def test_reloads_when_new_file_added(self):
        # Assert against the reloaded chunks directly — BM25 scoring is unreliable on a tiny
        # test corpus (IDF -> 0/negative when a term is in all/most docs), but the real corpus is fine.
        (self._dir / "b.md").write_text("# Networking\ndetails about vlans and subnets", encoding="utf-8")
        self.assertTrue(rag.maybe_reload(self.NS))
        self.assertIn("b.md", [src for src, _ in rag.chunks(self.NS)])

    def test_reloads_when_file_edited(self):
        p = self._dir / "a.md"
        p.write_text("# Title\nhello world reference content\nnew keyword zebrafish", encoding="utf-8")
        # The suite runs in ~10ms, so the rewrite can land in the same millisecond as setUp and the
        # mtime-rounded signature wouldn't move. Force a distinct mtime so the change is detected.
        future = time.time() + 10
        os.utime(p, (future, future))
        self.assertTrue(rag.maybe_reload(self.NS))
        self.assertTrue(any("zebrafish" in txt for _, txt in rag.chunks(self.NS)))

    def test_aside_returns_at_once_and_the_new_index_arrives_behind_it(self):
        """On the turn path the rebuild is started and left running: the current search reads the old index, the next one reads the new one."""
        import threading
        from unittest import mock
        (self._dir / "b.md").write_text("# Networking\ndetails about vlans and subnets", encoding="utf-8")
        hold, real = threading.Event(), rag._Namespace.load

        def slow(ns):
            hold.wait(10)                       # a rebuild that takes a while
            return real(ns)
        with mock.patch.object(rag._Namespace, "load", slow):
            self.assertTrue(rag.maybe_reload(self.NS, aside=True), "a rebuild was started")
            self.assertNotIn("b.md", [src for src, _ in rag.chunks(self.NS)], "the turn that noticed did not wait")
            self.assertFalse(rag.maybe_reload(self.NS, aside=True), "one rebuild at a time")
            hold.set()
            for _ in range(500):
                if "b.md" in [src for src, _ in rag.chunks(self.NS)]:
                    break
                time.sleep(0.01)
        self.assertIn("b.md", [src for src, _ in rag.chunks(self.NS)], "the new index arrived behind it")

    def test_throttle_blocks_recheck(self):
        config.RAG_RELOAD_CHECK_INTERVAL = 9999
        rag.maybe_reload(self.NS)  # within the throttle window
        (self._dir / "c.md").write_text("# C\nthrottled content here", encoding="utf-8")
        self.assertFalse(rag.maybe_reload(self.NS))  # still throttled -> skipped


class ASearchDuringAReloadSeesOneWholeIndex(unittest.TestCase):
    """A search running while a reload embeds must read the index as it was, whole."""

    def _indexes(self):
        import grounding
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / "a.md").write_text("# Alpha\nalpha beta gamma content here", encoding="utf-8")
        (tmp / "a.py").write_text("def alpha_beta():\n    return 'alpha beta gamma'\n", encoding="utf-8")
        # filler, so 'alpha' is rare and BM25's IDF stays positive (a one-document corpus scores every term 0)
        for name, body in (("clouds", "rain weather wind storm"), ("music", "guitar drums song"),
                           ("garden", "flowers soil water plant"), ("ocean", "waves sand beach tide")):
            (tmp / (name + ".md")).write_text("# %s\n%s content" % (name.title(), body), encoding="utf-8")
            (tmp / (name + ".py")).write_text("def %s_thing():\n    return '%s'\n" % (name, body), encoding="utf-8")
        return ((rag._Namespace("test_midreload", [tmp]), lambda ns: ns.search("alpha beta", ["alpha", "beta"], 3)),
                (grounding._CodeIndex([str(tmp)]), lambda ns: ns.search("alpha beta", 3)))

    def test_a_reload_that_starts_while_a_search_waits_on_its_embedding_does_not_end_the_search(self):
        """The live interleaving: the search has checked the embeddings and waits on its query's embedding; meanwhile a
        reload begins (the old load()'s first steps: new chunks in, embeddings None). The old search then read
        self.embeddings again and raised; the search now holds its one snapshot."""
        from unittest import mock
        import grounding
        vec = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]          # noqa: E731
        with mock.patch.object(rag.embeddings, "embed_corpus", lambda name, texts: vec(texts)), \
                mock.patch.object(grounding.embeddings, "available", lambda: True):
            for ns, search in self._indexes():
                self.assertIsNotNone(ns.embeddings, "the first load embedded")

                def query(text, ns=ns):
                    ns.chunks = ns.chunks + ns.chunks      # a reload has begun while this search waited
                    ns.embeddings = None
                    return [1.0, 0.0, 0.0]
                with mock.patch.object(rag.embeddings, "embed_query", query):
                    hits = search(ns)
                self.assertTrue(hits, type(ns).__name__)

    def test_a_reload_publishes_the_new_index_only_once_it_is_whole(self):
        from unittest import mock
        import grounding
        watching, seen = [], []

        def corpus(name, texts):
            if watching:                                 # mid-reload: the published index is still the old, whole one
                seen.append(watching[0]._index[2] is not None)
            return [[1.0, 0.0, 0.0] for _ in texts]
        with mock.patch.object(rag.embeddings, "embed_corpus", corpus), \
                mock.patch.object(grounding.embeddings, "available", lambda: True):
            for ns, _search in self._indexes():
                watching[:] = [ns]
                ns.load()
        self.assertEqual(seen, [True, True])


if __name__ == "__main__":
    unittest.main()
