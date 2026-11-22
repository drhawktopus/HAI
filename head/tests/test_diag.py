"""Tests for the concise per-request diagnostic log (notes.diag + app wiring)."""
import json
import tempfile
import unittest
from pathlib import Path

import notes

APP = Path("app.py").read_text(encoding="utf-8")


class DiagWriterTests(unittest.TestCase):
    def setUp(self):
        self._orig = notes.DIAG
        notes.DIAG = Path(tempfile.mkdtemp()) / "diag"

    def tearDown(self):
        notes.DIAG = self._orig

    def test_diag_writes_one_json_line(self):
        notes.diag({"user": "Test", "route": "heavyweight", "ctx": {"tok_est": 1234}})
        files = list(notes.DIAG.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        rec = json.loads(files[0].read_text(encoding="utf-8").strip())
        self.assertIn("ts", rec)
        self.assertEqual(rec["user"], "Test")
        self.assertEqual(rec["ctx"]["tok_est"], 1234)


class DiagWiringTests(unittest.TestCase):
    def test_run_query_split_and_logged(self):
        self.assertIn("def _run_query_core(q: Q, status=None, diag=None):", APP)
        self.assertIn("def _log_request_diag(q, result, diag, elapsed):", APP)
        self.assertIn("result = _run_query_core(q, status, diag)", APP)
        self.assertIn("notes.diag(rec)", APP)

    def test_context_breakdown_captured(self):
        self.assertIn('diag["ctx"] = {', APP)
        self.assertIn('"tok_est": total_chars // 4', APP)


if __name__ == "__main__":
    unittest.main()
