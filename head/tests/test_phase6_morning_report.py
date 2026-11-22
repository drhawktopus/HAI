import os
os.environ.setdefault("EMBED_ENABLED", "0")
import unittest
from pathlib import Path
import app


class MorningReportTests(unittest.TestCase):
    def test_report_renders_all_sections(self):
        r = app._self_eng_morning_report()
        self.assertIsInstance(r, str)
        for h in ("Self-Engineering State Report", "Active tasks", "Decision gates", "Active projects", "Last deploy"):
            self.assertIn(h, r)

    def test_route_is_admin_gated(self):
        src = Path("app.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/self-eng/report")', src)
        block = src.split("def self_eng_report(")[1].split("return {")[0]
        self.assertIn("config.ADMIN_USERS", block)
        self.assertIn("403", block)


if __name__ == "__main__":
    unittest.main()