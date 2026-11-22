"""The Deep Work tile reads /absurd/status's `current` block, not runs.0.
This avoids showing stale 'Executing' states from runs that died mid-execution."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import absurd  # noqa: E402
import app  # noqa: E402

DEAD = {"run_id": "abr-1", "status": "error", "phase": "executing", "progress": "leaf 3/5",
        "last_status": "Think Really Hard: building", "created_at": "2026-10-05T03:29:31+00:00",
        "finished_at": "2026-10-17T16:22:49+00:00"}


class AbsurdCurrent(unittest.TestCase):
    def test_a_finished_newest_run_reads_idle_with_its_outcome_and_day(self):
        c = app.absurd_current([DEAD])
        self.assertEqual(c["status"], "idle")
        self.assertEqual(c["phase"], "")
        self.assertEqual(c["progress"], "")
        self.assertEqual(c["last_status"], "last run: error · 2026-10-17")

    def test_the_day_is_central_time_not_utc(self):
        late = dict(DEAD, finished_at="2026-10-18T02:30:00+00:00")       # 21:30 CDT on the 17th
        self.assertEqual(app.absurd_current([late])["last_status"], "last run: error · 2026-10-17")

    def test_an_active_run_anywhere_in_the_list_is_current(self):
        live = dict(DEAD, run_id="abr-2", status=absurd.RUNNING, progress="leaf 1/4")
        c = app.absurd_current([DEAD, live])
        self.assertEqual((c["run_id"], c["status"], c["progress"]), ("abr-2", absurd.RUNNING, "leaf 1/4"))

    def test_every_active_status_counts_and_nothing_else(self):
        for st in absurd.ACTIVE_STATUSES:                       # one definition of live, shared with has_active()
            self.assertEqual(app.absurd_current([dict(DEAD, status=st)])["status"], st)
        self.assertEqual(app.absurd_current([dict(DEAD, status="done")])["status"], "idle")

    def test_no_runs_and_a_malformed_list_never_raise(self):
        self.assertEqual(app.absurd_current([])["last_status"], "no runs yet")
        self.assertEqual(app.absurd_current([None])["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
