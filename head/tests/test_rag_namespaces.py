"""Per-user RAG namespace isolation: each audience's corpus is walled off, and the user->namespace
resolver routes the right people to the right knowledge base.

Imports rag (needs rank_bm25), so this runs in the orchestrator container, not the Windows host.
"""
import pathlib
import tempfile
import unittest

import config
import rag


class NamespaceIsolationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self._tmp.name)
        self._home = base / "home"
        self._nurse = base / "nurse"
        self._home.mkdir()
        self._nurse.mkdir()
        # Several docs per namespace so the queried term is DISCRIMINATING — BM25 IDF goes negative
        # when a term appears in 100% of a corpus (e.g. a single-doc namespace), which would zero out
        # otherwise-valid hits. This mirrors the real corpora (many files).
        (self._home / "net.md").write_text(
            "# Networking\nthe caddy reverse proxy routes lan names to containers", encoding="utf-8")
        (self._home / "pihole.md").write_text("# Pihole\npihole blocks ads at the dns layer", encoding="utf-8")
        (self._home / "plex.md").write_text("# Plex\nplex streams movies to client devices", encoding="utf-8")
        (self._home / "wol.md").write_text("# Wake\nwake on lan sends a magic packet to a host", encoding="utf-8")
        (self._nurse / "pharm.md").write_text(
            "# Furosemide\nfurosemide is a loop diuretic used for edema and fluid overload", encoding="utf-8")
        (self._nurse / "anatomy.md").write_text("# Anatomy\nthe femur is the long bone of the thigh", encoding="utf-8")
        (self._nurse / "vitals.md").write_text("# Vitals\nblood pressure is recorded in millimeters", encoding="utf-8")
        (self._nurse / "ethics.md").write_text("# Ethics\nautonomy means respecting a patient choice", encoding="utf-8")
        rag.configure("t_home", [self._home])
        rag.configure("t_nurse", [self._nurse])

    def tearDown(self):
        self._tmp.cleanup()

    def test_home_query_finds_home_doc(self):
        hits = rag.search("caddy reverse proxy", k=4, namespace="t_home")
        self.assertTrue(any(h["source"] == "net.md" for h in hits))

    def test_nurse_query_finds_nurse_doc(self):
        hits = rag.search("furosemide diuretic", k=4, namespace="t_nurse")
        self.assertTrue(any(h["source"] == "pharm.md" for h in hits))

    def test_clinical_term_never_leaks_from_homelab(self):
        # The core isolation guarantee: a nursing term must not surface from the homelab namespace.
        hits = rag.search("furosemide diuretic", k=4, namespace="t_home")
        self.assertFalse(any(h["source"] == "pharm.md" for h in hits))

    def test_homelab_term_never_leaks_from_nursing(self):
        hits = rag.search("caddy reverse proxy", k=4, namespace="t_nurse")
        self.assertFalse(any(h["source"] == "net.md" for h in hits))

    def test_none_namespace_is_ungrounded(self):
        self.assertEqual(rag.search("furosemide diuretic", k=4, namespace=None), [])

    def test_unknown_namespace_is_empty(self):
        self.assertEqual(rag.search("anything here", k=4, namespace="does_not_exist"), [])


class NamespaceResolverTests(unittest.TestCase):
    """config.namespace_for_user routes each audience correctly (patched for determinism)."""

    def setUp(self):
        self._orig = (config.ADMIN_USERS, config.USER_NAMESPACE, config.DEFAULT_USER_NAMESPACE)
        config.ADMIN_USERS = {"Alex"}
        config.USER_NAMESPACE = {"Riley": "nursing"}
        config.DEFAULT_USER_NAMESPACE = None

    def tearDown(self):
        config.ADMIN_USERS, config.USER_NAMESPACE, config.DEFAULT_USER_NAMESPACE = self._orig

    def test_admin_gets_homelab(self):
        self.assertEqual(config.namespace_for_user("Alex"), "homelab")

    def test_explicit_mapping_wins(self):
        self.assertEqual(config.namespace_for_user("Riley"), "nursing")

    def test_casual_user_is_ungrounded(self):
        self.assertIsNone(config.namespace_for_user("Avery"))


if __name__ == "__main__":
    unittest.main()
