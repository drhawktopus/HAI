"""Tests for per-user writing-voice capture + drafting injection."""
import pathlib
import tempfile
import unittest
from pathlib import Path

import voiceprofile

APP = Path("app.py").read_text(encoding="utf-8")

SAMPLE = ("# My Draft\n\n> Riley's own draft.\n\n(Insert Name Here)\n\n"
          "Within current nursing education standards, senior undergraduate nursing students preparing to "
          "enter professional practice are expected to demonstrate academic knowledge, clinical competence, "
          "and evidence-based practice skills. However, these students may have limited structured "
          "opportunities to practice professional responses to emotionally overwhelming situations.")


class VoiceProfileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = voiceprofile.VOICE_DIR
        voiceprofile.VOICE_DIR = pathlib.Path(self.dir)

    def tearDown(self):
        voiceprofile.VOICE_DIR = self._orig

    def test_capture_skips_metadata_keeps_prose(self):
        saved = voiceprofile.save_from_text("Riley", SAMPLE)
        self.assertTrue(saved.startswith("Within current nursing education standards"))
        self.assertNotIn("Insert Name", saved)
        self.assertNotIn("# My Draft", saved)
        self.assertEqual(voiceprofile.load("Riley"), saved)

    def test_cap(self):
        orig = voiceprofile.MAX_CHARS
        voiceprofile.MAX_CHARS = 200
        try:
            saved = voiceprofile.save_from_text("Riley", SAMPLE)
            self.assertLessEqual(len(saved), 210)
            self.assertGreater(len(saved), 80)
        finally:
            voiceprofile.MAX_CHARS = orig

    def test_too_short_rejected(self):
        self.assertEqual(voiceprofile.save_from_text("Riley", "Hi."), "")
        self.assertEqual(voiceprofile.load("Riley"), "")

    def test_clear(self):
        voiceprofile.save_from_text("Riley", SAMPLE)
        voiceprofile.clear("Riley")
        self.assertEqual(voiceprofile.load("Riley"), "")


class WritingSampleFromTheLibrary(unittest.TestCase):

    def setUp(self):
        from unittest.mock import patch
        import config
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = Path(self.tmp.name) / "lib"
        self.lib.mkdir()
        orig = voiceprofile.VOICE_DIR
        voiceprofile.VOICE_DIR = Path(self.tmp.name) / "voice"
        self.addCleanup(setattr, voiceprofile, "VOICE_DIR", orig)
        p = patch.object(config, "writable_corpus_dir",
                         lambda user: (str(self.lib), "jordan") if user == "Jordan" else (None, None))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_a_library_document_becomes_the_sample_by_name_with_or_without_md(self):
        (self.lib / "my-final-paper.md").write_text(SAMPLE, encoding="utf-8")
        for name in ("my-final-paper.md", "my-final-paper"):
            with self.subTest(name=name):
                ok, msg = voiceprofile.save_from_library("Jordan", name)
                self.assertTrue(ok, msg)
                self.assertTrue(voiceprofile.load("Jordan").startswith("Within current nursing education standards"))

    def test_only_the_users_own_library_and_never_a_path(self):
        outside = Path(self.tmp.name) / "secret.md"
        outside.write_text(SAMPLE, encoding="utf-8")
        for name in ("../secret.md", "..\\secret.md", str(outside)):
            with self.subTest(name=name):
                ok, msg = voiceprofile.save_from_library("Jordan", name)
                self.assertFalse(ok)
                self.assertIn("No document called", msg)
        self.assertEqual(voiceprofile.load("Jordan"), "")
        self.assertFalse(voiceprofile.save_from_library("Guest", "x.md")[0], "no private library, no sample")

    def test_too_little_prose_is_said_and_saves_nothing(self):
        (self.lib / "short.md").write_text("Hi.", encoding="utf-8")
        ok, msg = voiceprofile.save_from_library("Jordan", "short.md")
        self.assertFalse(ok)
        self.assertIn("too little prose", msg)
        self.assertEqual(voiceprofile.load("Jordan"), "")

    def test_doc_can_set_it_and_the_tool_is_offered(self):
        import json
        import tools
        (self.lib / "essay.md").write_text(SAMPLE, encoding="utf-8")
        out = json.loads(tools.run_tool("use_as_writing_sample", {"name": "essay.md"}, "", user="Jordan"))
        self.assertTrue(out["ok"], out)
        self.assertTrue(voiceprofile.load("Jordan"))
        for pool in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            self.assertIn("use_as_writing_sample", [t["function"]["name"] for t in pool])


class VoiceWiringTests(unittest.TestCase):
    def test_capture_on_draft_upload(self):
        self.assertIn('if intent == "draft" and indexed:', APP)   # an explicit library add with the old draft intent
        self.assertIn("voiceprofile.save_from_text(user, markdown)", APP)

    def test_injected_only_when_drafting_for_every_user(self):
        # Any user with a sample, not just nursing.
        self.assertIn("_DRAFTING_INTENT", APP)
        self.assertNotIn("nursing_mode and _DRAFTING_INTENT.search(low)", APP)
        self.assertIn("    if _DRAFTING_INTENT.search(low):\n        _voice = voiceprofile.load(q.user)", APP)
        self.assertIn("_voice = voiceprofile.load(q.user)", APP)
        self.assertIn("VOICE MATCHING", APP)
        # Style only — must not reuse the sample's content.
        self.assertIn("never reuse its specific content", APP)

    def test_endpoints(self):
        self.assertIn('@app.get("/voice")', APP)
        self.assertIn('@app.post("/voice/reset")', APP)
        self.assertIn('@app.post("/voice/sample")', APP)
        self.assertIn("tools.set_writing_sample(user, req.name)", APP)          # the toggle and the tool share one path
        self.assertIn("fetch('/voice/sample'", APP)                            # the chip toggle calls it at send


if __name__ == "__main__":
    unittest.main()
