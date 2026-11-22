"""Verify that split window admission is blocked if the RAM requirement exceeds available free RAM or if RAM state is unknown."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
import scheduler

from _no_live_probes import start_no_live_probes, stop_no_live_probes

MODEL = "qwen3.6:35b-a3b-ud-q3kxl-mtp"
POLICY = {"offload_ok": [["bighonker", MODEL]], "offload_ram": {"bighonker": {MODEL: {"host_mib": 10746}}}}


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = scheduler.GPU_LOAD_DIR
        scheduler.GPU_LOAD_DIR = os.path.join(self._tmp, "gpu-load")

    def tearDown(self):
        scheduler.GPU_LOAD_DIR = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def push(self, ram, loaded=()):
        return scheduler.report_gpu_load("bighonker", 0.0, 15.0, 1061.0, list(loaded), "test", ram=ram)


class TheBoxReportsItsRam(_Base):
    def test_a_pushed_reading_is_stored_and_read_where_glances_is_absent(self):
        rec = self.push({"free_mb": 20480, "total_mb": 32700})
        self.assertEqual(rec["ram"], {"free_mb": 20480.0, "total_mb": 32700.0})
        self.assertAlmostEqual(scheduler.ram_free_gb("bighonker"), 20480 * 1048576 / 1e9, places=3)

    def test_a_malformed_or_missing_reading_is_unknown(self):
        self.push({"free_mb": "lots"})
        self.assertIsNone(scheduler.ram_free_gb("bighonker"))
        self.push(None)
        self.assertIsNone(scheduler.ram_free_gb("bighonker"))

    def test_a_stale_report_is_unknown(self):
        self.push({"free_mb": 20480, "total_mb": 32700})
        with mock.patch.object(scheduler, "_report_age_s", return_value=10 ** 6):
            self.assertIsNone(scheduler.ram_free_gb("bighonker"))

    def test_the_desk_endpoint_carries_it(self):
        with mock.patch.object(scheduler, "report_gpu_load", return_value={"accounting": {}, "ts": "fixture"}) as rep:
            TestClient(app_module.app).post("/fleet/gpu-load", json={
                "host": "bighonker", "util_pct": 0, "ram": {"free_mb": 1, "total_mb": 2}})
        self.assertEqual(rep.call_args.kwargs["ram"], {"free_mb": 1, "total_mb": 2})


class TheSplitWindowChecksItsRamSide(_Base):
    def ctx(self, **pol):
        with mock.patch.object(scheduler, "policy_data", return_value=dict(POLICY, **pol)):
            return scheduler.window_ctx("bighonker", MODEL, 65536)

    def test_enough_free_ram_above_the_reserve_opens_the_full_window(self):
        self.push({"free_mb": 20480, "total_mb": 32700})
        n, why = self.ctx()
        self.assertEqual(n, 65536, why)

    def test_too_little_free_ram_refuses_it_saying_the_numbers(self):
        self.push({"free_mb": 12000, "total_mb": 32700})          # 12000 - 3072 reserve < 10746
        n, why = self.ctx()
        self.assertIsNone(n)
        self.assertIn("10746 MiB", why)

    def test_unknown_ram_or_an_unmeasured_footprint_refuses_never_guesses(self):
        self.assertIsNone(self.ctx()[0], "no report at all")
        self.push({"free_mb": 20480, "total_mb": 32700})
        self.assertIsNone(self.ctx(offload_ram={})[0], "the RAM side was never measured")

    def test_already_loaded_needs_no_new_ram(self):
        self.push({"free_mb": 4000, "total_mb": 32700}, loaded=[MODEL])
        self.assertEqual(self.ctx()[0], 65536)

    def test_a_pair_off_the_list_is_untouched(self):
        with mock.patch.object(scheduler, "policy_data", return_value=dict(POLICY, offload_ok=[])):
            self.assertEqual(scheduler.window_ctx("bighonker", MODEL, 65536), (65536, "no memory profile"))


if __name__ == "__main__":
    unittest.main()
