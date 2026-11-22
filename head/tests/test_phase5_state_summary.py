import os
os.environ.setdefault("EMBED_ENABLED", "0")
import unittest
from pathlib import Path
import app


class SelfEngStateTests(unittest.TestCase):
    def test_state_shape_is_fail_soft(self):
        st = app._self_eng_state()
        self.assertIsInstance(st, dict)
        for k in ("active_tasks", "decisions", "active_projects", "last_deploy"):
            self.assertIn(k, st)
        self.assertIsInstance(st["active_tasks"], list)
        self.assertIsInstance(st["decisions"], list)
        self.assertIsInstance(st["active_projects"], list)

    def test_route_is_admin_gated(self):
        src = Path("app.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/self-eng/state")', src)
        block = src.split('def self_eng_state(')[1].split("return _self_eng_state()")[0]
        self.assertIn("config.ADMIN_USERS", block)
        self.assertIn("403", block)


if __name__ == "__main__":
    unittest.main()