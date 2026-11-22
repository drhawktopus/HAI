"""Tests for the save_to_corpus tool (self-serve 'save this to my corpus' — Slice 1 of the user-corpus
contributions project). Exercises the real write path (own writable namespace + provenance + reindex),
gating (no writable corpus), and static registration/dispatch."""
import shutil
import tempfile
import unittest
from pathlib import Path

import config
import rag
import tools

TOOLS_SRC = Path("tools.py").read_text(encoding="utf-8")
APP = Path("app.py").read_text(encoding="utf-8")


class SaveToCorpusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ns = "corpustest"
        self.user = "corpus-tester"
        self._orig_rag = dict(config.RAG_NAMESPACES)
        self._orig_user = dict(config.USER_NAMESPACE)
        config.RAG_NAMESPACES[self.ns] = [self.tmp]
        config.USER_NAMESPACE[self.user] = self.ns
        rag.configure(self.ns, [self.tmp])

    def tearDown(self):
        config.RAG_NAMESPACES.clear(); config.RAG_NAMESPACES.update(self._orig_rag)
        config.USER_NAMESPACE.clear(); config.USER_NAMESPACE.update(self._orig_user)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_saves_with_provenance_and_indexes(self):
        out = tools.run_tool(
            "save_to_corpus",
            {"title": "Debrief frameworks", "content": "ELT paired with Jeffries simulation theory works well."},
            "",
            user=self.user,
        )
        self.assertIn("Saved", out)
        files = list(Path(self.tmp).glob("*.md"))
        self.assertEqual(len(files), 1)
        md = files[0].read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# Debrief frameworks"))          # H1 = title (chunker uses it)
        self.assertIn("saved_by=corpus-tester", md)                     # provenance stamp
        self.assertIn("ELT paired with Jeffries", md)                   # the material itself
        # It was reindexed: the note is now among the namespace's chunks. (Assert on the index directly —
        # BM25 IDF is degenerate on a single-document corpus, so rag.search isn't a fair probe here.)
        chunk_texts = " ".join(t for _src, t in rag.chunks(self.ns))
        self.assertIn("Jeffries simulation", chunk_texts)

    def test_empty_content_rejected(self):
        out = tools.run_tool("save_to_corpus", {"title": "x", "content": "   "}, "", user=self.user)
        self.assertIn("Nothing to save", out)
        self.assertEqual(list(Path(self.tmp).glob("*.md")), [])

    def test_no_writable_corpus_is_graceful(self):
        # A user with NO personal corpus (ungrounded, not in ADMIN_UPLOAD_NAMESPACE) -> graceful message.
        out = tools.run_tool("save_to_corpus", {"title": "x", "content": "y"}, "", user="unprovisioned-uploader")
        self.assertIn("don't have a personal corpus", out)

    def test_admin_architect_saves_into_their_own_corpus(self):
        # Verify save_to_corpus targets the user's personal namespace, not the RAG grounding namespace.
        tmp = tempfile.mkdtemp()
        try:
            with unittest.mock.patch.dict(config.RAG_NAMESPACES, {"alex": [tmp]}):
                out = tools.run_tool("save_to_corpus", {"title": "Architect note", "content": "From the phone."}, "", user="Alex")
            self.assertIn("Saved", out)
            self.assertEqual(len(list(Path(tmp).glob("*.md"))), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_writes_only_to_callers_own_namespace(self):
        # Two users, two namespaces: a save by user A must never land in user B's corpus.
        tmp_b = tempfile.mkdtemp()
        try:
            config.RAG_NAMESPACES["corpustest_b"] = [tmp_b]
            config.USER_NAMESPACE["corpus-tester-b"] = "corpustest_b"
            rag.configure("corpustest_b", [tmp_b])
            tools.run_tool("save_to_corpus", {"title": "mine", "content": "A private note"}, "", user=self.user)
            self.assertEqual(len(list(Path(self.tmp).glob("*.md"))), 1)   # landed in A
            self.assertEqual(list(Path(tmp_b).glob("*.md")), [])          # NOT in B
        finally:
            shutil.rmtree(tmp_b, ignore_errors=True)

    def test_registered_in_both_tool_tiers_and_not_admin_gated(self):
        for tier in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            self.assertIn("save_to_corpus", [t["function"]["name"] for t in tier])
        self.assertNotIn("save_to_corpus", tools._ADMIN_TOOLS)   # self-serve, not gated

    def test_dispatch_and_prompt_nudge_wired(self):
        self.assertIn('if name == "save_to_corpus":', TOOLS_SRC)
        self.assertIn("save_to_corpus", APP)                    # nursing library brief nudges the model


if __name__ == "__main__":
    unittest.main()
