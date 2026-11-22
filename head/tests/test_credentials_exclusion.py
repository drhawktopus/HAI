"""Credentials exclusion pin.

Ensures `Reference/Credentials/` files are never served by general corpus tools.
Credential values are accessible ONLY via the gated `get_credential` tool;
all other readers receive pointers, not values.

Constraint:
Exclusion relies on non-recursive globbing (`glob`) in `rag._Namespace._load_chunks`
and `tools._user_corpus_files`. Since `Credentials/` is a subdirectory of indexed dirs,
switching to recursive globbing (`rglob`) or changing corpus roots would silently expose
credentials. This test suite verifies that guard remains intact.

Positive Controls:
Each exclusion test is paired with a control asserting the sentinel file IS findable
when guards are removed. If controls pass, the pin has gone blind.
"""
import os
import pathlib
import tempfile
import unittest

import config
import rag
import tools

# A value that appears NOWHERE in the lab. If this string ever comes back out of a reader, that
# reader reached into the locker. Real locker FILENAMES are used (see LOCKER_FILENAMES) so the pin
# matches the real tree's shape; no real secret is present in this file.
SENTINEL = "CREDPIN-SENTINEL-VALUE-DO-NOT-INDEX-8f3a21c7"

# Real locker filenames to exercise actual matching logic.
LOCKER_FILENAMES = [
    "camera-credentials.md",
    "network-credentials.md",
    "arr-stack-credentials.md",
]


class _LockerTree(unittest.TestCase):
    """A temp library shaped like the real one: ordinary docs in the corpus dir, a Credentials/
    SUBdirectory holding the sentinel."""

    def setUp(self):
        self.base = pathlib.Path(tempfile.mkdtemp())
        (self.base / "CURRENT-PROJECTS.md").write_text(
            "# Shared Current Projects\n\nOrdinary indexed content about the camera project.\n",
            encoding="utf-8")
        self.locker = self.base / "Credentials"
        self.locker.mkdir()
        for name in LOCKER_FILENAMES:
            (self.locker / name).write_text(
                f"# {name}\n\npassword: {SENTINEL}\n", encoding="utf-8")

        self._orig_ns = config.RAG_NAMESPACES
        self._orig_fetch = getattr(config, "FETCH_ONLY_DIRS", None)
        config.RAG_NAMESPACES = dict(config.RAG_NAMESPACES)
        config.RAG_NAMESPACES["homelab"] = [str(self.base)]

    def tearDown(self):
        config.RAG_NAMESPACES = self._orig_ns
        if self._orig_fetch is not None:
            config.FETCH_ONLY_DIRS = self._orig_fetch
        # Drop the temp-dir-backed namespace so no later test inherits it. NOTE: this leaves
        # "homelab" ABSENT rather than restored, and `rag.chunks()` does not rebuild — any later
        # test that reads the real index must `rag.reload("homelab")` first (RealLockerInForce does).
        rag._NAMESPACES.pop("homelab", None)


class SearchDocsExclusion(_LockerTree):
    """Tests that excluded files are absent from the index, not just search results.

    Checking search output is insufficient: a file may be indexed but ranked out (false
    negative for exclusion) or queries may return no hits due to BM25/IDF edge cases
    (e.g., duplicate corpora with embeddings disabled), creating false positives.

    Asserting index membership directly verifies the `glob`/`rglob` exclusion logic
    in `_load_chunks`, independent of downstream ranking behavior."""

    def _index(self, dirs):
        n = rag.configure("homelab", [str(d) for d in dirs])
        self.assertTrue(n, "instrument dead: the corpus indexed ZERO chunks, so finding no "
                           "credential proves nothing")
        return rag.chunks("homelab")

    def test_locker_content_is_NEVER_INDEXED(self):
        for src, text in self._index([self.base]):
            self.assertNotIn(SENTINEL, text, f"locker content is in the search index (source {src})")
            self.assertNotIn("credentials", src.lower())

    def test_POSITIVE_CONTROL_the_same_content_IS_indexed_when_the_dir_is_the_corpus(self):
        """Point the corpus AT the locker. The sentinel must appear in the index — proving the
        assertion above is capable of failing, rather than querying an empty corpus."""
        self.assertTrue(any(SENTINEL in text for _, text in self._index([self.locker])),
                        "POSITIVE CONTROL FAILED: the sentinel is not in the index even when the "
                        "locker IS the corpus, so the exclusion assertion above has no teeth")

    def test_search_also_never_serves_it(self):
        """Verify that sensitive docs are excluded from search results.

        This is a secondary belt-and-braces check. A null result is only valid evidence
        if the retrieval instrument is actually working (i.e., it can find known-present
        docs). If the primary positive control fails, we skip this test rather than
        passing falsely due to an empty/dead index."""
        self._index([self.base])
        alive = rag.search("shared current projects camera", k=10, namespace="homelab")
        if not any("Ordinary" in h["text"] or "camera" in h["text"].lower() for h in alive):
            self.skipTest("retrieval instrument returned nothing for a known-present document "
                          "(BM25 with no embeddings on a tiny corpus) — COULD NOT MEASURE. The "
                          "index-membership tests above carry the exclusion guarantee.")
        for query in ("password", "camera credentials", SENTINEL, "network"):
            for hit in rag.search(query, k=10, namespace="homelab"):
                self.assertNotIn(SENTINEL, hit["text"],
                                 f"locker content served by search_docs for query {query!r}")
                self.assertNotIn("Credentials", hit["source"])


class GetDocumentExclusion(_LockerTree):
    def test_locker_not_in_the_user_corpus_file_list(self):
        files = tools._user_corpus_files("Alex")
        self.assertTrue(files, "instrument dead: the corpus listed ZERO files")
        for f in files:
            self.assertNotEqual(f.parent.name, "Credentials")

    def test_get_document_refuses_every_real_locker_filename(self):
        for name in LOCKER_FILENAMES:
            for probe in (name, name.replace(".md", ""), "credentials"):
                out = tools._get_document("Alex", probe)
                self.assertNotIn(SENTINEL, out,
                                 f"get_document served locker content for {probe!r}")

    def test_POSITIVE_CONTROL_get_document_would_serve_it_if_listed(self):
        """Add the locker dir to the namespace. get_document must then serve the sentinel — proving
        the refusals above are real refusals, not a lookup that was always going to miss."""
        config.RAG_NAMESPACES["homelab"] = [str(self.base), str(self.locker)]
        out = tools._get_document("Alex", "camera-credentials.md")
        self.assertIn(SENTINEL, out,
                      "POSITIVE CONTROL FAILED: the locker file is unfetchable even when its "
                      "directory IS in the namespace, so the refusals above prove nothing")


class FetchOnlyDirsExclusion(_LockerTree):
    """Phase 0 adds `config.FETCH_ONLY_DIRS` — journals reachable by get_document but kept OUT of
    the BM25/embedding corpus (G1a option (b)). It is a NEW reader, so it gets the same pin.
    These run as soon as the attribute exists and are skipped-with-a-reason before that."""

    def _require_fetch_only(self):
        if not hasattr(config, "FETCH_ONLY_DIRS"):
            self.skipTest("config.FETCH_ONLY_DIRS not present yet (Phase 0 G1a not landed)")

    def test_fetch_only_dirs_never_expose_the_locker(self):
        self._require_fetch_only()
        config.FETCH_ONLY_DIRS = dict(config.FETCH_ONLY_DIRS)
        # The hostile case: someone points a fetch-only dir at the corpus PARENT, which contains
        # Credentials/. Non-recursive globbing must still keep the locker out.
        config.FETCH_ONLY_DIRS["homelab"] = [str(self.base)]
        for name in LOCKER_FILENAMES:
            self.assertNotIn(SENTINEL, tools._get_document("Alex", name))
        for f in tools._user_corpus_files("Alex"):
            self.assertNotEqual(f.parent.name, "Credentials")

    def test_fetch_only_dirs_stay_OUT_of_the_search_index(self):
        """The whole point of (b) over (a): fetchable by name, invisible to BM25."""
        self._require_fetch_only()
        config.FETCH_ONLY_DIRS = dict(config.FETCH_ONLY_DIRS)
        journal = self.base / "j"
        journal.mkdir()
        marker = "JOURNALMARKER-ded1f0"
        (journal / "2026-08-13-claude-hai.md").write_text(
            f"# journal\n\n{marker}\n", encoding="utf-8")
        config.FETCH_ONLY_DIRS["homelab"] = [str(journal)]

        # fetchable...
        self.assertIn(marker, tools._get_document("Alex", "2026-08-13-claude-hai.md"),
                      "fetch-only dir is not fetchable — option (b) is not wired")
        # ...but not indexed
        rag.configure("homelab", config.RAG_NAMESPACES["homelab"])
        for hit in rag.search(marker, k=10, namespace="homelab"):
            self.assertNotIn(marker, hit["text"],
                             "a fetch-only dir leaked into the BM25 index — that is option (a), "
                             "which was rejected")


class RealLockerInForce(unittest.TestCase):
    """The synthetic pins above prove the MECHANISM. This one proves the mechanism is pointed at the
    REAL tree — the only check that would catch a config change aiming a corpus at a locker parent.

    Requires the documentation tree to be mounted (true on Sundae/in-container where the suite
    baseline is measured; not on a bare dev checkout). It SKIPS WITH A REASON rather than passing
    when the tree is absent — an absent tree is 'could not measure', never 'measured clean'."""

    def setUp(self):
        self.locker = pathlib.Path(config.DOCS_DIR) / "Reference" / "Credentials"
        if not self.locker.is_dir():
            self.skipTest(f"documentation tree not mounted at {config.DOCS_DIR} — "
                          "COULD NOT MEASURE (this is not a pass)")
        # Force rebuild: prior tests clear 'homelab' state; rag.chunks() caches without reloading.
        # Without this, subsequent tests see an empty index and fail.
        rag.reload("homelab")

    def test_no_real_locker_file_is_reachable_by_any_corpus_reader(self):
        lockerfiles = sorted(self.locker.glob("*.md"))
        self.assertTrue(lockerfiles, f"instrument dead: {self.locker} holds no *.md, so finding "
                                     "none in the corpus proves nothing")

        corpus = tools._user_corpus_files("Alex")
        self.assertTrue(corpus, "instrument dead: Alex's corpus listed ZERO files")
        corpus_resolved = {p.resolve() for p in corpus}
        for lf in lockerfiles:
            self.assertNotIn(lf.resolve(), corpus_resolved,
                             f"{lf.name} is reachable by get_document — the locker is open")

        # And by name, the way a model would actually ask for it.
        for lf in lockerfiles:
            out = tools._get_document("Alex", lf.name)
            self.assertNotIn("[Full text of", out.split("\n")[0],
                             f"get_document served {lf.name}")

    def test_no_real_locker_file_is_in_the_search_index(self):
        indexed = {src for src, _ in rag.chunks("homelab")}
        self.assertTrue(indexed, "instrument dead: the homelab index is empty")
        for lf in sorted(self.locker.glob("*.md")):
            self.assertNotIn(lf.name, indexed, f"{lf.name} is in the BM25 index")


if __name__ == "__main__":
    unittest.main()
