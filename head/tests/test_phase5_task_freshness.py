import os
os.environ.setdefault("EMBED_ENABLED", "0")
import datetime as dt
import unittest
from pathlib import Path
import app


def _iso(delta_seconds):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delta_seconds)).isoformat()


class TaskFreshnessTests(unittest.TestCase):
    def test_recent_task_is_fresh(self):
        self.assertTrue(app._task_is_fresh({"updated_at": _iso(-60)}))

    def test_stale_task_not_fresh(self):
        self.assertFalse(app._task_is_fresh({"updated_at": _iso(-3 * 3600)}))

    def test_missing_timestamp_is_fresh(self):
        self.assertTrue(app._task_is_fresh({"status": "active"}))

    def test_unparseable_timestamp_is_fresh(self):
        self.assertTrue(app._task_is_fresh({"updated_at": "not-a-date"}))

    def test_continuation_uses_freshness(self):
        src = Path("app.py").read_text(encoding="utf-8")
        block = src.split("def _self_engineering_needs_continuation(")[1].split("def _self_engineering_progress_state(")[0]
        self.assertIn("_task_is_fresh", block)


if __name__ == "__main__":
    unittest.main()