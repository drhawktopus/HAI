"""SKU-affinity lanes for matrix orders (run_order host_sku).

Tolkien owns which tablet is which lane; a cell tagged `<lane>~...` runs ONLY on that lane's
tablets, and a same-lane pool splits its cells by dynamic pull. These tests pin, in order:

- POSITIVE CONTROL: host_sku=None is the original fan-out -- the lane tag is ignored, so one host
  runs cells of every lane (proves the affinity path is genuinely opt-in, not always-on).
- THE PIN: with a host_sku map, every cell a host runs belongs to that host's lane, every lane is
  exercised (non-zero), and coverage is complete.
- THREE FATES: a lane whose only host is unhealthy leaves its cells UNCLAIMED (visible/VOID), never
  silently dropped -- only a property of the work-item may consume it.
- Every experimental lane names a real roster node; normal members need no benchmark lane.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry
import tablet_fulfil


def _lane(cell):
    return cell.split("~", 1)[0]


class _Recorder:
    """Patches the network layer so run_order executes in-process, recording which host ran which
    cells. Each host writes items.txt into its own stage dir, so there is no cross-host collision."""

    def __init__(self):
        self.by_host = {}
        self.lk = threading.Lock()

    def __enter__(self):
        def fake_ssh(host, cmd, timeout=0):
            return 0, ""

        def fake_scp(host, src, dst, timeout=0):
            return 0, ""

        def fake_scp_from(host, remote, local, timeout=0):
            wd = os.path.dirname(local)
            with open(os.path.join(wd, "items.txt"), encoding="utf-8") as f:
                names = [l.strip() for l in f if l.strip()]
            with self.lk:
                self.by_host.setdefault(host, []).extend(names)
            rows = [{"item": n, "result": "echo:%s" % n} for n in names]
            with open(local, "w", encoding="utf-8") as f:
                json.dump(rows, f)
            return 0, ""

        self._ps = [mock.patch.object(tablet_fulfil, "_ssh", fake_ssh),
                    mock.patch.object(tablet_fulfil, "_scp", fake_scp),
                    mock.patch.object(tablet_fulfil, "_scp_from", fake_scp_from)]
        for p in self._ps:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self._ps:
            p.stop()

    def ran(self, host):
        return self.by_host.get(host, [])

    def all_cells(self):
        out = []
        for v in self.by_host.values():
            out.extend(v)
        return out


ALL_TABLETS = ["aragorn", "gandalf", "frodo", "legolas", "merry", "pippin", "sam"]


class SkuAffinityTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p_state = mock.patch.object(tablet_fulfil, "STATE_DIR", self.tmp)
        self.p_state.start()

    def tearDown(self):
        self.p_state.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spec(self, cells, control_item):
        return {"kind": "map", "name": "t-sku", "batch": 1,
                "payload": "/x/echo.py", "items": list(cells),
                "control": {"item": control_item, "expect": "echo:%s" % control_item}}

    # --- POSITIVE CONTROL: opt-in, not always-on -------------------------------------------------

    def test_non_affinity_ignores_the_lane_tag(self):
        """host_sku=None -> original behavior: a single host runs cells of EVERY lane. If affinity
        were always-on, a B-host could never run an A-cell and this would leave them unclaimed."""
        cells = ["A~m~t~1", "B~m~t~2", "C~m~t~3", "sam~m~t~4"]
        spec = self._spec(cells, "A~m~t~ctl")
        with _Recorder() as rec:
            rows, errors, wall, expected, unclaimed = tablet_fulfil.run_order(
                spec, ["frodo"], host_sku=None)
        self.assertEqual(errors, {})
        self.assertEqual(unclaimed, [], "no lane restriction: frodo runs everything")
        self.assertEqual(sorted(rec.ran("frodo")), sorted(cells + ["A~m~t~ctl"]))
        ok, why = tablet_fulfil.validate_complete(rows, expected, unclaimed, key="item")
        self.assertTrue(ok, why)

    # --- THE PIN ---------------------------------------------------------------------------------

    def test_affinity_pins_each_cell_to_its_lane(self):
        host_sku = {h: tablet_foundry.TABLET_SKU[h] for h in ALL_TABLETS}
        cells = ["A~m~t~a1", "A~m~t~a2", "A~m~t~a3", "A~m~t~a4",
                 "B~m~t~b1", "B~m~t~b2", "B~m~t~b3", "B~m~t~b4",
                 "C~m~t~c1", "C~m~t~c2",
                 "sam~m~t~s1", "sam~m~t~s2"]
        spec = self._spec(cells, "A~m~t~ctl")
        with _Recorder() as rec:
            rows, errors, wall, expected, unclaimed = tablet_fulfil.run_order(
                spec, ALL_TABLETS, host_sku=host_sku)

        self.assertEqual(errors, {})
        self.assertEqual(unclaimed, [])
        # Every cell a host ran belongs to that host's lane -- the core invariant.
        for host in ALL_TABLETS:
            for cell in rec.ran(host):
                self.assertEqual(_lane(cell), host_sku[host],
                                 "%s (lane %s) ran cross-lane cell %s" % (host, host_sku[host], cell))
        # Every lane was exercised (non-zero) -- a pass that dealt zero cells is the trap.
        lanes_run = {_lane(c) for c in rec.all_cells()}
        self.assertEqual(lanes_run, {"A", "B", "C", "sam"})
        # sam ran its lane ALONE (its whole point: an isolated measurement, not pooled into B).
        self.assertTrue(all(_lane(c) == "sam" for c in rec.ran("sam")))
        self.assertEqual(sorted(rec.ran("sam")), ["sam~m~t~s1", "sam~m~t~s2"])
        # Coverage complete across the whole matrix.
        ok, why = tablet_fulfil.validate_complete(rows, expected, unclaimed, key="item")
        self.assertTrue(ok, why)
        self.assertEqual(expected, len(cells) + 1)

    def test_same_hardware_pool_splits_its_lane(self):
        """aragorn+gandalf share lane A: between them they run every A-cell, and no A-cell runs
        twice (each measured once). This is the 'split the work' allowance."""
        host_sku = {"aragorn": "A", "gandalf": "A"}
        cells = ["A~m~t~%d" % i for i in range(8)]
        spec = self._spec(cells, "A~m~t~ctl")
        with _Recorder() as rec:
            rows, errors, wall, expected, unclaimed = tablet_fulfil.run_order(
                spec, ["aragorn", "gandalf"], host_sku=host_sku)
        self.assertEqual(unclaimed, [])
        union = rec.all_cells()
        self.assertEqual(sorted(union), sorted(cells + ["A~m~t~ctl"]))
        self.assertEqual(len(union), len(set(union)), "no cell measured twice")

    # --- THREE FATES: a lane with no healthy host is VISIBLE, never silently dropped -------------

    def test_lane_without_a_healthy_host_surfaces_unclaimed(self):
        host_sku = {"aragorn": "A"}                       # only lane A is served
        cells = ["A~m~t~a1", "B~m~t~b1", "B~m~t~b2"]       # B has no host
        spec = self._spec(cells, "A~m~t~ctl")
        with _Recorder() as rec:
            rows, errors, wall, expected, unclaimed = tablet_fulfil.run_order(
                spec, ["aragorn"], host_sku=host_sku)
        self.assertEqual(sorted(unclaimed), ["B~m~t~b1", "B~m~t~b2"])
        self.assertEqual(sorted(rec.ran("aragorn")), ["A~m~t~a1", "A~m~t~ctl"])
        ok, why = tablet_fulfil.validate_complete(rows, expected, unclaimed, key="item")
        self.assertFalse(ok, "an unserved lane must fail coverage, not pass silently")

    # --- the lane table cannot name nodes outside the hardware roster -----------------------------

    def test_tablet_sku_is_a_subset_of_the_roster(self):
        try:
            import scheduler
        except Exception as e:                            # pragma: no cover
            raise unittest.SkipTest("scheduler not importable locally: %s" % e)
        self.assertLessEqual(set(tablet_foundry.TABLET_SKU), set(scheduler.TABLETS),
                             "an experimental lane may not name a tablet that is gone")


if __name__ == "__main__":
    unittest.main()
