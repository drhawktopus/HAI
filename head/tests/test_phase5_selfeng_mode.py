import os
os.environ.setdefault("EMBED_ENABLED", "0")
import unittest
from pathlib import Path
import app


class SelfEngModeTests(unittest.TestCase):
    def test_state_has_busy_and_mode(self):
        st = app._self_eng_state()
        self.assertIn("busy", st)
        self.assertIsInstance(st["busy"], bool)
        self.assertIn("mode", st)
        self.assertIn("halt", st["mode"])
        self.assertIn("sandbox_only", st["mode"])

    def test_halt_checked_in_continuation(self):
        block = Path("app.py").read_text(encoding="utf-8").split(
            "def _self_engineering_needs_continuation(")[1].split("def _self_engineering_progress_state(")[0]
        self.assertIn('_SELF_ENG_MODE["halt"]', block)

    def test_dispatch_uses_sandbox_safe(self):
        block = Path("app.py").read_text(encoding="utf-8").split(
            "def _dispatch_lead(")[1].split("def _log_request_diag(")[0]
        self.assertIn("sandbox_safe", block)
        self.assertIn("heavy_tools", block)
        self.assertNotIn("tools.HEAVY_TOOLS,", block)

    def test_mode_routes_present(self):
        src = Path("app.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/self-eng/busy")', src)
        self.assertIn('@app.post("/self-eng/mode")', src)


if __name__ == "__main__":
    unittest.main()