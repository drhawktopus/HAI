import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import app
import notes
import tools


class _FakeQueue:
    @staticmethod
    def gpu_status_hosts():
        return {"bighonker": ({"state": "free", "detail": "", "eta": ""}, 27000.0)}

    @staticmethod
    def gpu_status(host=None):
        return {}


class NameChangeFingerprint(unittest.TestCase):
    def setUp(self):
        self.logged = []
        self._orig = notes.log
        notes.log = lambda line: self.logged.append(str(line))

    def tearDown(self):
        notes.log = self._orig

    def _push(self, name, prev_name):
        req = app.RunStateReq(host="mothership", name=name, state="training")
        with mock.patch.object(app, "_fleet_run_all",
                               return_value={"mothership": {"name": prev_name}}), \
             mock.patch.object(app, "_fleet_run_write"):
            app.fleet_run_state(req)

    def test_name_change_logs_with_source(self):
        self._push("new_run", prev_name="old_run")
        lines = [l for l in self.logged if "RUNSTATE NAME-CHANGE" in l]
        self.assertEqual(len(lines), 1)
        self.assertIn("'old_run'->'new_run'", lines[0])
        self.assertIn("from=", lines[0])

    def test_same_name_is_silent(self):
        """POSITIVE CONTROL inverted: steady-state pushes must not spam the log."""
        self._push("same_run", prev_name="same_run")
        self._push("", prev_name="")
        self.assertEqual([l for l in self.logged if "NAME-CHANGE" in l], [])


class OvernightLastRun(unittest.TestCase):
    def test_restart_serves_the_durable_last_run_labeled_last(self):
        d = tempfile.mkdtemp()
        for stamp, tag in (("20260710T084431Z", "old"), ("20260815T152419Z", "new")):
            with open(os.path.join(d, "ovr-%s.json" % stamp), "w", encoding="utf-8") as fh:
                json.dump({"tag": tag}, fh)
        with mock.patch.object(app, "OVERNIGHT_STATE_DIR", d), \
             mock.patch.object(app, "_overnight_busy", return_value=False), \
             mock.patch.dict(app._OVERNIGHT, {"state": None, "current": None, "activity": []}):
            out = app._overnight_status()
        self.assertIsNone(out["current"], "the durable record must NEVER masquerade as current")
        self.assertEqual(out["last"]["tag"], "new", "newest ovr-* wins")
        self.assertIn("ovr-20260815T152419Z.json", out["last"]["file"])


class AsleepVsSilent(unittest.TestCase):
    def _summary(self, awake):
        with mock.patch.object(app, "_fleet_run_all", return_value={}), \
             mock.patch.object(app, "_rescue_all", return_value={}), \
             mock.patch.object(app, "_host_awake", return_value=awake), \
             mock.patch.dict(sys.modules, {"p20_queue": _FakeQueue}):
            return app.p20_factories()["factories"]["bighonker"]["summary"]

    def test_sleeping_host_is_designed_behavior_not_a_problem(self):
        s = self._summary(awake=False)
        self.assertIn("asleep", s)
        self.assertIn("designed", s)

    def test_up_but_silent_is_the_real_alarm(self):
        s = self._summary(awake=True)
        self.assertIn("host UP but worker not reporting", s)

    def test_no_instrument_keeps_the_plain_wording(self):
        s = self._summary(awake=None)
        self.assertIn("worker not reporting", s)
        self.assertNotIn("asleep", s)

    def test_fleet_status_problems_inherit_the_discrimination(self):
        with mock.patch.object(app, "_fleet_run_all", return_value={}), \
             mock.patch.object(app, "_rescue_all", return_value={}), \
             mock.patch.object(app, "_host_awake", return_value=False), \
             mock.patch.dict(sys.modules, {"p20_queue": _FakeQueue}):
            out = json.loads(tools._fleet_status("fleet"))
        self.assertIn("asleep", " ".join(out["problems"]))


if __name__ == "__main__":
    unittest.main()
