"""Tests for the deck live-runs source: AbsurdRegistry.list_active_all + absurd.active_runs_view.

The GET /runs/active endpoint is a thin admin-gated call over these two (admin gate is the proven
`user not in ADMIN_USERS -> 403` pattern), so the logic worth testing lives here and runs without
FastAPI/TestClient.
"""
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import absurd


class TestListActiveAll(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.reg = absurd.AbsurdRegistry(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_active_only_cross_user_newest_first(self):
        self.reg.create("r1", "alice", "t", "1h", 3600, status=absurd.RUNNING)
        self.reg.create("r2", "bob", "t", "1h", 3600, status=absurd.QUEUED)
        self.reg.create("r3", "alice", "t", "1h", 3600, status=absurd.DONE)
        self.reg.create("r4", "bob", "t", "1h", 3600, status=absurd.ERROR)
        active = self.reg.list_active_all()
        # only queued+running, across BOTH users, newest-first by _seq (r2 seq2 before r1 seq1)
        self.assertEqual([r["run_id"] for r in active], ["r2", "r1"])
        self.assertEqual({r["user"] for r in active}, {"alice", "bob"})

    def test_limit(self):
        for i in range(3):
            self.reg.create("a%d" % i, "alice", "t", "1h", 3600, status=absurd.RUNNING)
        self.assertEqual(len(self.reg.list_active_all(limit=2)), 2)

    def test_empty_registry(self):
        self.assertEqual(self.reg.list_active_all(), [])


class TestActiveRunsView(unittest.TestCase):
    def test_projection_excludes_text_and_shape(self):
        rec = {"run_id": "x", "user": "alice", "text": "SECRET PACKET", "status": "running",
               "last_status": "step 3/50"}
        v = absurd.active_runs_view([rec])[0]
        self.assertNotIn("text", v)
        self.assertEqual(set(v), {"run_id", "title", "status", "last_status", "user", "elapsed_s"})
        self.assertEqual(v["title"], "x")           # falls back to run_id when no title
        self.assertEqual(v["last_status"], "step 3/50")

    def test_title_used_when_present(self):
        v = absurd.active_runs_view([{"run_id": "x", "title": "Build the board", "status": "running"}])[0]
        self.assertEqual(v["title"], "Build the board")

    def test_elapsed_from_started_at(self):
        started = "2026-08-29T00:00:00+00:00"
        now = datetime.fromisoformat(started) + timedelta(seconds=90)
        v = absurd.active_runs_view([{"run_id": "x", "started_at": started, "status": "running"}], now=now)[0]
        self.assertEqual(v["elapsed_s"], 90)

    def test_elapsed_null_when_not_started_or_unparseable(self):
        now = datetime.now(timezone.utc)
        recs = [{"run_id": "x", "started_at": None, "status": "queued"},
                {"run_id": "y", "started_at": "not-a-date", "status": "running"}]
        v = absurd.active_runs_view(recs, now=now)
        self.assertIsNone(v[0]["elapsed_s"])
        self.assertIsNone(v[1]["elapsed_s"])


if __name__ == "__main__":
    unittest.main()
