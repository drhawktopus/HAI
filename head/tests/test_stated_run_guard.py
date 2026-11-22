"""Test that declared runs own their cards and stopped ones do not.

Asymmetry constraint: defaulting unknown state to BUSY is safer than FREE.
- Marking a busy card as FREE corrupts active training (high cost).
- Marking a free card as BUSY only wastes scheduling time (low cost)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import scheduler

# `training_busy` is the subject under test and must remain unpinned in `tests/_no_live_probes.py`.
from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


def _write(tmp, **fields):
    rec = {"name": "concept_flux2_v1", "host": scheduler.LEAD_BOX,
           "total_steps": 3000, "updated": "2026-07-26T15:15:39"}
    rec.update(fields)
    p = os.path.join(tmp, "current-run.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    return p


class StatedRunTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_training_owns_the_card(self):
        p = _write(self.tmp, state="training")
        busy, why = scheduler.training_busy(scheduler.LEAD_BOX, p)
        self.assertTrue(busy)
        self.assertIn("concept_flux2_v1", why)

    def test_paused_frees_the_card(self):
        """STOP_NOW, a rescue recycle and a crash all land here: the run is current, the card isn't."""
        p = _write(self.tmp, state="paused")
        busy, _why = scheduler.training_busy(scheduler.LEAD_BOX, p)
        self.assertFalse(busy)

    def test_a_stale_heartbeat_does_NOT_free_a_training_card(self):
        """A 14-hour run does not end because its reporter died. Staleness is not a liveness test --
        that inference is the disease the stated run exists to cure."""
        p = _write(self.tmp, state="training", updated="2026-07-20T01:00:00")
        busy, _why = scheduler.training_busy(scheduler.LEAD_BOX, p)
        self.assertTrue(busy)

    def test_missing_state_field_is_treated_as_busy(self):
        p = _write(self.tmp)          # no state key at all
        busy, why = scheduler.training_busy(scheduler.LEAD_BOX, p)
        self.assertTrue(busy)
        # The BEHAVIOUR assertion above is the point of this test and has always passed. The
        # message was reworded to name the normalised value ("state 'unstated'") instead of the
        # older "no state", which is strictly clearer -- so the assertion is updated to the current
        # wording rather than the code being changed back to satisfy a stale string match.
        self.assertIn("unstated", why)

    def test_unknown_state_is_treated_as_busy(self):
        p = _write(self.tmp, state="reticulating")
        busy, _why = scheduler.training_busy(scheduler.LEAD_BOX, p)
        self.assertTrue(busy)

    def test_a_run_on_another_host_does_not_block_this_one(self):
        p = _write(self.tmp, state="training", host=scheduler.BRAIN)
        busy, _why = scheduler.training_busy(scheduler.LEAD_BOX, p)
        self.assertFalse(busy)

    def test_absent_pointer_is_not_a_verdict(self):
        """No declared run = say nothing; the other guards (health, claims, gpu_state) still apply."""
        busy, _why = scheduler.training_busy(scheduler.LEAD_BOX,
                                             os.path.join(self.tmp, "nope.json"))
        self.assertFalse(busy)

    def test_unreadable_pointer_is_not_a_verdict(self):
        p = os.path.join(self.tmp, "current-run.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        self.assertFalse(scheduler.training_busy(scheduler.LEAD_BOX, p)[0])


class PlacementRespectsTheStatedRunTests(unittest.TestCase):
    """The regression itself: place() offered a training card as an option."""

    def setUp(self):
        self.p = [mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
                  mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
                  mock.patch.object(scheduler, "gpu_health", return_value=(True, "ok")),
                  mock.patch.object(scheduler, "gpu_state", return_value=(False, "free"))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_a_training_card_is_excluded_from_gpu_placement(self):
        def tb(host, path=None):
            return (True, "training") if host == scheduler.LEAD_BOX else (False, "")
        with mock.patch.object(scheduler, "training_busy", side_effect=tb):
            hosts = [h for h, _s, _w in scheduler.place("vision", vram_gb=6.0)]
        self.assertNotIn(scheduler.LEAD_BOX, hosts)
        self.assertTrue(hosts, "the rest of the fleet must still be usable")

    def test_a_paused_run_leaves_the_card_available(self):
        """Today's actual state: STOP_NOW'd at 2183/3000, card genuinely idle, recaption ran there."""
        with mock.patch.object(scheduler, "training_busy", return_value=(False, "")):
            hosts = [h for h, _s, _w in scheduler.place("vision", vram_gb=6.0)]
        self.assertIn(scheduler.LEAD_BOX, hosts)

    def test_cpu_work_still_allowed_on_a_training_host(self):
        """The GPU is committed; the CPU is not. Do not write off the whole box."""
        def tb(host, path=None):
            return (True, "training") if host == scheduler.LEAD_BOX else (False, "")
        with mock.patch.object(scheduler, "training_busy", side_effect=tb):
            hosts = [h for h, _s, _w in scheduler.place("cpu")]
        self.assertIn(scheduler.LEAD_BOX, hosts)


class StatedRunIsWiredToTheWriterTests(unittest.TestCase):
    """The gate must read the file the declaration is actually WRITTEN to.

    Every test above passes an explicit `path`, so all of them passed for a full day while the
    production default pointed at `current-run.json` -- a filename nothing writes. `stated_run()`
    returned None on every real call and policy 12 never fired once. Mocking the input is exactly
    what let a dead gate look healthy, so these two tests deliberately assert the WIRING instead.
    """

    def test_default_path_is_the_file_the_push_handler_writes(self):
        import app
        self.assertEqual(scheduler.STATED_RUN, app.FLEET_RUN_STATE,
                         "scheduler reads a different file than /fleet/run-state writes - "
                         "the stated-run guard is dead in production")

    def test_the_per_host_map_shape_is_understood(self):
        """What the push handler really stores: {host: rec}, not a bare record."""
        p = os.path.join(self.tmp, "fleet-run-state.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({scheduler.LEAD_BOX: {"name": "concept_flux2_v6", "host": scheduler.LEAD_BOX,
                                              "state": "training", "step": 315}}, f)
        busy, why = scheduler.training_busy(scheduler.LEAD_BOX, p)
        self.assertTrue(busy, "a host-keyed map must be understood, not just a single record")
        self.assertIn("concept_flux2_v6", why)
        self.assertEqual((False, ""), scheduler.training_busy(scheduler.BRAIN, p))

    def setUp(self):
        self.tmp = tempfile.mkdtemp()


if __name__ == "__main__":
    unittest.main()
