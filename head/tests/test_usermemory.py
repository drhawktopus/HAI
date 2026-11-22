"""Tests for durable per-user memory (continuity across 'New chat')."""
import pathlib
import tempfile
import unittest
from pathlib import Path

import usermemory

APP = Path("app.py").read_text(encoding="utf-8")
TOOLS = Path("tools.py").read_text(encoding="utf-8")


class UserMemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = usermemory.MEMORY_DIR
        usermemory.MEMORY_DIR = pathlib.Path(self.dir)

    def tearDown(self):
        usermemory.MEMORY_DIR = self._orig

    def test_add_load_roundtrip(self):
        self.assertEqual(usermemory.load("Riley"), "")
        self.assertTrue(usermemory.add("Riley", "Capstone is about emotionally focused simulation."))
        mem = usermemory.load("Riley")
        self.assertIn("Capstone is about emotionally focused simulation.", mem)
        self.assertIn("- (", mem)   # dated bullet

    def test_dedup(self):
        usermemory.add("Riley", "PICOT focuses on emotional preparedness.")
        usermemory.add("Riley", "PICOT focuses on emotional preparedness.")
        self.assertEqual(usermemory.load("Riley").count("PICOT focuses"), 1)

    def test_empty_ignored(self):
        self.assertFalse(usermemory.add("Riley", "   "))
        self.assertEqual(usermemory.load("Riley"), "")

    def test_cap_keeps_most_recent(self):
        orig = usermemory.MAX_CHARS
        usermemory.MAX_CHARS = 200
        try:
            for i in range(50):
                usermemory.add("Riley", f"fact number {i} with some padding text to take space")
            mem = usermemory.load("Riley")
            self.assertLessEqual(len(mem), 300)
            self.assertIn("fact number 49", mem)   # newest survives the cap
            self.assertNotIn("fact number 0 ", mem)  # oldest evicted
        finally:
            usermemory.MAX_CHARS = orig

    def test_clear(self):
        usermemory.add("Riley", "something durable")
        usermemory.clear("Riley")
        self.assertEqual(usermemory.load("Riley"), "")

    def test_user_isolation(self):
        usermemory.add("Riley", "riley-only fact")
        usermemory.add("Morgan", "morgan-only fact")
        self.assertIn("riley-only fact", usermemory.load("Riley"))
        self.assertNotIn("riley-only fact", usermemory.load("Morgan"))


class WiringTests(unittest.TestCase):
    def test_remember_tool_present_and_dispatched(self):
        self.assertIn('"name": "remember"', TOOLS)
        self.assertIn("_REMEMBER,", TOOLS)            # in BRAIN/HEAVY tool lists
        self.assertIn('if name == "remember":', TOOLS)
        self.assertIn("usermemory.add(user, note)", TOOLS)

    def test_injected_every_query(self):
        self.assertIn("_remembered = usermemory.load(q.user)", APP)
        self.assertIn("persist", APP.lower())

    def test_continuity_prompt_and_endpoints(self):
        self.assertIn("call the `remember` tool", APP)
        self.assertIn('@app.get("/memory")', APP)
        self.assertIn('@app.post("/memory/reset")', APP)

    def test_forget_memory_button(self):
        self.assertIn('id="forgetMem"', APP)
        self.assertIn("forgetMem.onclick", APP)
        self.assertIn("/memory/reset?user=", APP)

    def test_new_chat_reset_does_not_touch_memory(self):
        # The /history/reset handler clears history only; memory has its own /memory/reset.
        block = APP.split("def reset_history(", 1)[1].split("def ", 1)[0]
        self.assertIn("history.reset(user, chat or None)", block)   # #18: per-chat reset
        self.assertNotIn("usermemory", block)


if __name__ == "__main__":
    unittest.main()
