"""Tests isolate state by honoring HAI_NOTES_DIR to avoid polluting the real vault."""
import unittest
from pathlib import Path

APP = Path("app.py").read_text(encoding="utf-8")
NOTES_SRC = Path("notes.py").read_text(encoding="utf-8")


class NotesEnvOverrideTests(unittest.TestCase):
    def test_notes_honors_hai_notes_dir(self):
        self.assertIn('os.getenv("HAI_NOTES_DIR")', NOTES_SRC)
        self.assertIn("if _NOTES_BASE else", NOTES_SRC)   # falls back to the real vault when unset


class WorkLogStampsSeconds(unittest.TestCase):
    def test_a_line_carries_its_second(self):
        import re, shutil, tempfile
        from unittest import mock
        import notes
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        with mock.patch.object(notes, "NOTES", d):
            notes.log("stage probe")
        body = "".join(p.read_text(encoding="utf-8") for p in d.glob("*-orchestrator.md"))
        self.assertRegex(body, re.compile(r"^- \d\d:\d\d:\d\d CT — stage probe$", re.M))


class SaveContributeRelayNudgeTests(unittest.TestCase):
    def test_prompt_tells_model_to_relay_outcome(self):
        self.assertIn("ALWAYS tell the user the outcome", APP)
        self.assertIn('submitted it for " + config.OWNER_S + " review', APP)


if __name__ == "__main__":
    unittest.main()
