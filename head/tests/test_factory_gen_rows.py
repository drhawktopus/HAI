"""#37: Factory order `gen` state must appear on the tile using the same formatter as standard orders. The factory emits `gen` on run-state; the desk filters to only the fields the formatter reads to ensure identical rendering."""
import datetime
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import app
import scheduler

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


def _iso(seconds_ago):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=seconds_ago)).isoformat()


class _FakeQueue:
    @staticmethod
    def gpu_status_hosts():
        return {}

    @staticmethod
    def gpu_status(host=None):
        return {}


# What the foundry states mid-order: 37 engine calls, one generating now, read 941 tok/s, 40,000 of 47,104 tokens.
GEN = {"read": 941.5, "turn": 37, "turn_label": "call", "tool": "generating", "tool_running": True,
       "ctx": [40000, 47104]}


def _tile(gen, seconds_ago=20, state="compute", phase="compute(gpu): a1 · 93.4 tok/s"):
    rec = {"host": "mothership", "name": "a1-x", "state": state, "step": 3, "total_steps": 34,
           "phase": phase, "ts": _iso(seconds_ago)}
    if gen is not None:
        rec["gen"] = gen
    with mock.patch.object(app, "_fleet_run_all", return_value={"mothership": rec}), \
         mock.patch.object(app, "_rescue_all", return_value={}), \
         mock.patch.dict(sys.modules, {"p20_queue": _FakeQueue}):
        return app._factory_view("mothership")


class OrderRows(unittest.TestCase):
    def test_a_live_order_shows_speed_step_and_context(self):
        t = _tile(GEN)
        self.assertEqual(t["tok_s"], "write 93 · read 942 tok/s", "write is the rate the phase carries")
        self.assertEqual(t["step"], "call 37 · generating…", "no tool time when the factory did not time it")
        self.assertEqual(t["context"], "40k / 47k (85%)")

    def test_a_stated_write_rate_is_used(self):
        self.assertEqual(_tile(dict(GEN, write=88.0))["tok_s"], "write 88 · read 942 tok/s")

    def test_a_timed_step_shows_its_time(self):
        """Tolkien times each call in-process, so its rows carry the time like Kirk's."""
        t = _tile(dict(GEN, tool="between calls", tool_s=4.25, tool_running=False))
        self.assertEqual(t["step"], "call 37 · between calls 4.2 s")

    def test_no_gen_keeps_the_phase_rate_and_blank_rows(self):
        t = _tile(None)
        self.assertEqual((t["tok_s"], t["step"], t["context"]), ("93.4 tok/s", "", ""))

    def test_a_stale_or_paused_record_shows_no_rows(self):
        stale = _tile(GEN, seconds_ago=7200)
        self.assertEqual((stale["step"], stale["context"]), ("", ""), "a stale record is not live work")
        paused = _tile(GEN, state="paused")
        self.assertEqual((paused["step"], paused["context"]), ("", ""), "a parked order is not working")


class TheDeskKeepsOnlyWhatTheFormatterReads(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for p in (mock.patch.object(app, "FLEET_RUN_STATE", os.path.join(self.tmp, "frs.json")),
                  mock.patch.object(scheduler, "claim", lambda *a, **k: None),
                  mock.patch.object(scheduler, "release", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)
        self.c = TestClient(app.app)

    def _post(self, gen):
        body = {"host": scheduler.LEAD_BOX, "name": "a1-x", "state": "compute", "phase": "x"}
        if gen is not ...:
            body["gen"] = gen
        r = self.c.post("/fleet/run-state", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return app._fleet_run_all()[scheduler.LEAD_BOX]

    def test_known_fields_are_kept_and_junk_is_dropped(self):
        rec = self._post(dict(GEN, secret="x", tool_running="true", ctx=[5, 0], turn_label="item"))
        self.assertEqual(rec["gen"], {"read": 941.5, "turn": 37, "tool": "generating"},
                         "a string bool, a zero-cap context and an unknown label are left out, never guessed")

    def test_null_and_absent_gen_are_not_errors(self):
        """The pydantic trap (a bare `dict = None` 422s an explicit null): an older factory and a null both pass."""
        self.assertNotIn("gen", self._post(None))
        self.assertNotIn("gen", self._post(...))

    def test_a_good_context_is_kept(self):
        self.assertEqual(self._post(GEN)["gen"]["ctx"], [40000, 47104])


if __name__ == "__main__":
    unittest.main()
