"""Tests for hybrid (BM25 + embedding) retrieval.

The fusion + fallback logic is exercised deterministically by injecting a canned embedding matrix and
monkeypatching the query embedder (no network / no real model). `embeddings`/`config` are checked
directly; the app-side gate change is verified statically. Run the suite with EMBED_ENABLED=0 so the
other BM25 tests don't touch a live embed host."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

import config
import embeddings
import rag

APP = Path("app.py").read_text(encoding="utf-8")


class SemanticConfigTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(config.EMBED_MODEL, "nomic-embed-text")
        self.assertEqual(config.RRF_K, 60)
        self.assertEqual(config.EMBED_MIN_SIM, 0.55)
        # Embed hosts reuse the brain hosts (always-on Softserve is the reliable backstop).
        self.assertIn(config.FAILOVER_HOST, config.EMBED_HOSTS)
        self.assertLess(config.EMBED_CONNECT_TIMEOUT, config.EMBED_TIMEOUT)


class EmbeddingsCacheTests(unittest.TestCase):
    def test_key_is_stable_and_text_specific(self):
        self.assertEqual(embeddings._key("hello"), embeddings._key("hello"))
        self.assertNotEqual(embeddings._key("hello"), embeddings._key("world"))

    def test_unavailable_returns_none(self):
        orig = config.EMBED_ENABLED
        config.EMBED_ENABLED = False
        try:
            self.assertIsNone(embeddings.embed_corpus("x", ["a", "b"]))
            self.assertIsNone(embeddings.embed_query("a"))
        finally:
            config.EMBED_ENABLED = orig


class HybridSearchTests(unittest.TestCase):
    def setUp(self):
        self._enabled = config.EMBED_ENABLED
        config.EMBED_ENABLED = False  # keep configure()/load() off the network
        self.dir = tempfile.mkdtemp()
        Path(self.dir, "alpha.md").write_text(
            "# Alpha Topic\nThe quick brown fox jumps zorptastic over the lazy dog today.", encoding="utf-8")
        Path(self.dir, "beta.md").write_text(
            "# Beta Topic\nA totally different sentence about widgets and gadgets here.", encoding="utf-8")
        # Filler docs so 'zorptastic' is rare (1 of many) and BM25 IDF stays positive (tiny corpora -> IDF 0).
        for name, body in (("gamma", "clouds rain weather wind storm"), ("delta", "music guitar drums song"),
                           ("epsilon", "garden flowers soil water plant"), ("zeta", "ocean waves sand beach tide")):
            Path(self.dir, name + ".md").write_text(f"# {name.title()} Topic\n{body} content.", encoding="utf-8")
        rag.configure("t_sem", [self.dir])
        self.ns = rag._ns("t_sem")
        self.assertGreaterEqual(len(self.ns.chunks), 2)

    def tearDown(self):
        config.EMBED_ENABLED = self._enabled

    def _idx(self, needle):
        return next(i for i, c in enumerate(self.ns.chunks) if needle in c[1])

    def test_semantic_rescues_a_keyword_miss(self):
        beta_i = self._idx("widgets")
        # Inject embeddings: every chunk on axis 0 except beta on axis 1; query aligned with beta.
        mat = np.zeros((len(self.ns.chunks), 4), dtype=np.float32)
        mat[:, 0] = 1.0
        mat[beta_i] = [0, 1, 0, 0]
        self.ns.embeddings = mat
        orig = embeddings.embed_query
        embeddings.embed_query = lambda t: [0.0, 1.0, 0.0, 0.0]
        try:
            res = self.ns.search("zorptastic", ["zorptastic"], k=5)  # token only in alpha
        finally:
            embeddings.embed_query = orig
        srcs = {r["source"] for r in res}
        self.assertIn("alpha.md", srcs)   # BM25 hit
        self.assertIn("beta.md", srcs)    # semantic-only rescue (no BM25 match for 'zorptastic')
        beta = next(r for r in res if r["source"] == "beta.md")
        self.assertGreater(beta["sim"], 0.9)   # strongly similar
        self.assertEqual(beta["score"], 0.0)   # but zero BM25 — would have been gated out before

    def test_bm25_only_when_embeddings_absent(self):
        self.ns.embeddings = None
        res = self.ns.search("zorptastic", ["zorptastic"], k=5)
        srcs = {r["source"] for r in res}
        self.assertIn("alpha.md", srcs)
        self.assertNotIn("beta.md", srcs)   # no semantic layer -> beta not pulled
        self.assertTrue(all(r["sim"] is None for r in res))


class GateWiringTests(unittest.TestCase):
    def test_app_gate_accepts_semantic(self):
        # A chunk passes if BM25 clears RAG_MIN_SCORE OR cosine clears EMBED_MIN_SIM.
        self.assertIn('c["score"] >= config.RAG_MIN_SCORE or (c.get("sim") or 0) >= config.EMBED_MIN_SIM', APP)

    def test_rag_search_passes_query(self):
        rag_src = Path("rag.py").read_text(encoding="utf-8")
        self.assertIn("def search(self, query, qtoks, k):", rag_src)
        self.assertIn("config.RRF_K", rag_src)
        self.assertIn("embeddings.embed_query(query)", rag_src)


if __name__ == "__main__":
    unittest.main()
