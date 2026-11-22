import json
import os
import tempfile
import time
import unittest
from unittest import mock

import app
import tools

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    # Ensure problem-watch tests never dial the real fleet.
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


def _tile(state="training", order="run_x", health="watching", stale=False):
    return {"state": state, "order": order, "health": health, "health_detail": "",
            "stale": stale}


class WatchRegistry(unittest.TestCase):
    def setUp(self):
        fd, self._file = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self._file)
        self._orig = app.FLEET_WATCH_FILE
        app.FLEET_WATCH_FILE = self._file
        self.dms = []
        self._dm = app._overnight_dm_send
        app._overnight_dm_send = lambda m: self.dms.append(m)
        app._AWAKE_CACHE.clear()          # the 60s awake cache must not leak between tests

    def tearDown(self):
        app.FLEET_WATCH_FILE = self._orig
        app._overnight_dm_send = self._dm
        if os.path.exists(self._file):
            os.unlink(self._file)

    def _arm(self, condition="done", tile=None, deadline=240):
        with mock.patch.object(app, "_factory_view", return_value=tile or _tile()):
            return app.fleet_watch_arm("mothership", condition, deadline, "test watch", "Alex")

    def test_arm_snapshots_the_baseline_from_the_first_reading(self):
        out = self._arm()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["baseline"]["state"], "training")
        self.assertEqual(out["status"], "watching")

    def test_bad_condition_and_empty_target_are_refused(self):
        self.assertFalse(self._arm(condition="explode")["ok"])
        self.assertFalse(app.fleet_watch_arm("", "done", 60, "", "Alex")["ok"])
        # A TRANSIENT read failure is NOT a bad target — a watch on a sleeping host must arm
        # (baseline None); the sibling test below carries the full first-good-reading behavior.
        with mock.patch.object(app, "_factory_view", side_effect=RuntimeError("asleep")):
            self.assertTrue(app.fleet_watch_arm("mothership", "done", 60, "", "Alex")["ok"])

    def test_done_watch_fires_when_the_run_ends_and_dms(self):
        out = self._arm(condition="done")
        with mock.patch.object(app, "_factory_view", return_value=_tile(state="idle", order="")):
            app._watch_tick()
        w = app._watch_all()[out["id"]]
        self.assertEqual(w["status"], "fired")
        self.assertIn("finished", w["verdict"])
        self.assertEqual(len(self.dms), 1)
        self.assertIn("FIRED", self.dms[0])

    def test_problem_watch_fires_on_daemon_down(self):
        out = self._arm(condition="problem")
        with mock.patch.object(app, "_factory_view",
                               return_value=_tile(state="training", health="DOWN")):
            app._watch_tick()
        self.assertEqual(app._watch_all()[out["id"]]["status"], "fired")

    def test_expiry_verdict_names_what_was_seen_instead(self):
        out = self._arm(condition="done", deadline=1)
        watches = app._watch_all()
        watches[out["id"]]["deadline_epoch"] = time.time() - 5
        app._watch_write(watches)
        with mock.patch.object(app, "_factory_view", return_value=_tile()):
            app._watch_tick()
        w = app._watch_all()[out["id"]]
        self.assertEqual(w["status"], "expired")
        self.assertIn("no 'done'", w["verdict"])
        self.assertIn("training", w["verdict"], "the deadline verdict must NAME the last reading")
        self.assertIn("EXPIRED", self.dms[0])

    def test_unreadable_source_at_deadline_is_inconclusive_never_fine(self):
        out = self._arm(condition="done", deadline=1)
        watches = app._watch_all()
        watches[out["id"]]["deadline_epoch"] = time.time() - 5
        app._watch_write(watches)
        with mock.patch.object(app, "_factory_view", side_effect=RuntimeError("deck gone")):
            app._watch_tick()
        w = app._watch_all()[out["id"]]
        self.assertEqual(w["status"], "inconclusive")
        self.assertIn("could not tell", w["verdict"])
        self.assertIn("INCONCLUSIVE", self.dms[0])

    def test_unreadable_arm_time_source_baselines_on_first_good_reading(self):
        with mock.patch.object(app, "_factory_view", side_effect=RuntimeError("asleep")):
            out = app.fleet_watch_arm("bighonker", "change", 240, "", "Alex")
        self.assertTrue(out["ok"])
        self.assertIsNone(out["baseline"])
        with mock.patch.object(app, "_factory_view", return_value=_tile(state="idle")):
            app._watch_tick()
        w = app._watch_all()[out["id"]]
        self.assertEqual(w["status"], "watching", "first good reading is the BASELINE, not a change")
        self.assertEqual(w["baseline"]["state"], "idle")


class WatchGating(unittest.TestCase):
    def test_fleet_watch_is_admin_only_and_fleet_status_is_free(self):
        self.assertFalse(tools._allowed("fleet_watch", "Riley"))
        self.assertTrue(tools._allowed("fleet_watch", "Alex"))
        self.assertTrue(tools._allowed("fleet_watch", "ClaudeSupervisor"))
        self.assertTrue(tools._allowed("fleet_status", "Riley"))

    def test_fleet_watch_offered_in_both_lists(self):
        for lst in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            self.assertIn("fleet_watch", {t["function"]["name"] for t in lst})

    def test_fleet_status_rides_the_core_floor(self):
        """The training-window rule: the supervisor model is the 9B exactly when the
        fleet-reading instrument must not vanish to narrowing or an index outage."""
        import tool_selection
        self.assertIn("fleet_status", tool_selection.CORE_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
