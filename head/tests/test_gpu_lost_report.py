"""Tests cover GPU failure AFTER model load, which residency heuristics miss.
`size_vram` is static at load-time; a dying card reports healthy residency forever,
bypassing standard low-VRAM checks."""
import json
import os
import tempfile
import unittest
from unittest import mock

import scheduler

# gpu_health is the subject of this file, so skip it; only incidental probes are pinned.
from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes(skip=("gpu_health",))


def tearDownModule():
    stop_no_live_probes()


class GpuReportTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = mock.patch.object(scheduler, "GPU_STATE_DIR", self.tmp)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_pushed_lost_verdict_marks_the_card_unhealthy(self):
        scheduler.report_gpu_state(scheduler.BRAIN, False,
                                   "GPU is lost. Reboot the system to recover this GPU")
        ok, why = scheduler.gpu_health(scheduler.BRAIN)
        self.assertFalse(ok)
        self.assertIn("GPU LOST", why)

    def test_lost_verdict_beats_a_perfect_residency_reading(self):
        """The exact live case: ollama says 100% resident, nvidia-smi says the device is gone."""
        scheduler.report_gpu_state(scheduler.BRAIN, False, "GPU is lost")

        class _R:
            def read(self):
                return json.dumps({"models": [{"name": "qwen3.5:9b",
                                               "size": 5_580_000_000,
                                               "size_vram": 5_580_000_000}]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        resp = mock.Mock()                    # the shared quick probe (httpx) - the old urllib fake no longer applies
        resp.json.return_value = json.loads(_R().read().decode())
        with mock.patch("ollama_client.httpx.get", return_value=resp):
            ok, _why = scheduler.gpu_health(scheduler.BRAIN)
        self.assertFalse(ok, "a pushed LOST verdict must override healthy-looking residency")

    def test_healthy_verdict_does_not_mask_the_residency_check(self):
        """A stale/optimistic 'ok' must not suppress signal 2 -- both signals stay live."""
        scheduler.report_gpu_state(scheduler.BRAIN, True, "RTX 3070, P8, 41, 0 %")

        class _R:
            def read(self):
                return json.dumps({"models": [{"name": "qwen3-vl:8b",
                                               "size": 5_890_000_000,
                                               "size_vram": 0}]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        resp = mock.Mock()
        resp.json.return_value = json.loads(_R().read().decode())
        with mock.patch("ollama_client.httpx.get", return_value=resp):
            ok, why = scheduler.gpu_health(scheduler.BRAIN)
        self.assertFalse(ok)
        self.assertIn("CPU", why)

    def test_a_stale_LOST_report_still_counts_as_lost(self):
        """Asymmetric on purpose: a lost GPU does not heal without a reboot, and a reboot restarts
        the reporter -- which is what clears the record."""
        rec = scheduler.report_gpu_state(scheduler.BRAIN, False, "GPU is lost")
        rec["ts"] = "2026-07-20T01:00:00+00:00"
        with open(os.path.join(self.tmp, "%s.json" % scheduler.BRAIN), "w", encoding="utf-8") as f:
            json.dump(rec, f)
        ok, why = scheduler.gpu_health(scheduler.BRAIN)
        self.assertFalse(ok)
        self.assertIn("until reboot", why)

    def test_no_report_is_not_a_verdict(self):
        """The AMD box has no nvidia-smi and will never report. It must stay usable."""
        with mock.patch("ollama_client.httpx.get", side_effect=RuntimeError("no ollama")):
            ok, _why = scheduler.gpu_health(scheduler.LEAD_BOX)
        self.assertTrue(ok)

    def test_a_lost_card_is_excluded_from_gpu_placement(self):
        scheduler.report_gpu_state(scheduler.BRAIN, False, "GPU is lost")
        with mock.patch("ollama_client.httpx.get", side_effect=RuntimeError("no ollama")), \
                mock.patch.object(scheduler, "ram_free_gb", return_value=30.0), \
                mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0), \
                mock.patch.object(scheduler, "gpu_state", return_value=(False, "free")), \
                mock.patch.object(scheduler, "training_busy", return_value=(False, "")):
            hosts = [h for h, _s, _w in scheduler.place("vision", vram_gb=6.0)]
        self.assertNotIn(scheduler.BRAIN, hosts)

    def test_recovery_clears_it(self):
        """After a reboot the reporter posts healthy again and the card returns to service."""
        scheduler.report_gpu_state(scheduler.BRAIN, False, "GPU is lost")
        self.assertFalse(scheduler.gpu_health(scheduler.BRAIN)[0])
        scheduler.report_gpu_state(scheduler.BRAIN, True, "RTX 3070, P8, 39, 0 %")
        with mock.patch("ollama_client.httpx.get", side_effect=RuntimeError("no ollama")):
            self.assertTrue(scheduler.gpu_health(scheduler.BRAIN)[0])


if __name__ == "__main__":
    unittest.main()
