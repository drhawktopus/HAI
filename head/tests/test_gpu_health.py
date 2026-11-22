"""
Silent GPU failure: the card appears healthy (Status=OK) but falls back to system RAM,
causing ~20x slowdown without explicit errors. nvidia-smi may report 'GPU is lost' while
drivers remain silent. We detect this via memory residency: if ollama holds a model but
little/none of it resides on the GPU, the card is effectively gone. This check works for
both NVIDIA and AMD systems.
"""
import json
import unittest
from unittest import mock

import scheduler

# `gpu_health` is the subject; pinning it would mask failures. Only other probes are pinned to avoid timeouts.
from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes(skip=("gpu_health",))


def tearDownModule():
    stop_no_live_probes()


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ps(models):
    # gpu_health reads /api/ps via ollama_client's shared quick probe (httpx) with a quiet window to avoid rate-limiting
    r = mock.Mock()
    r.json.return_value = {"models": models}
    return mock.patch("ollama_client.httpx.get", return_value=r)


class GpuHealthTests(unittest.TestCase):

    def test_model_resident_on_the_card_is_healthy(self):
        with _ps([{"name": "qwen3-vl:8b", "size": 5_890_000_000, "size_vram": 5_890_000_000}]):
            ok, why = scheduler.gpu_health(scheduler.BRAIN)
        self.assertTrue(ok)

    def test_model_resident_at_zero_vram_is_LOST(self):
        """The exact BigHonker signature: 5.89 GB 'resident', 0.00 GB of it on the card."""
        with _ps([{"name": "qwen3-vl:8b", "size": 5_890_000_000, "size_vram": 0}]):
            ok, why = scheduler.gpu_health(scheduler.BRAIN)
        self.assertFalse(ok)
        self.assertIn("CPU", why)

    def test_partial_offload_is_NOT_flagged_as_lost(self):
        """72% resident is a spill — slow, but the card IS working. Different problem, different fix."""
        with _ps([{"name": "qwen3-vl:8b", "size": 6_210_000_000, "size_vram": 4_470_000_000}]):
            ok, _why = scheduler.gpu_health(scheduler.BRAIN)
        self.assertTrue(ok)

    def test_nothing_resident_is_not_a_verdict(self):
        """An idle card tells us nothing; do not cry wolf on an empty box."""
        with _ps([]):
            ok, _why = scheduler.gpu_health(scheduler.BRAIN)
        self.assertTrue(ok)

    def test_probe_failure_does_not_condemn_a_card(self):
        with mock.patch("ollama_client.httpx.get", side_effect=RuntimeError("unreachable")):
            ok, _why = scheduler.gpu_health(scheduler.BRAIN)
        self.assertTrue(ok)


class PlacementSkipsLostCardsTests(unittest.TestCase):
    """Routing onto a lost card is WORSE than not placing: it succeeds slowly instead of failing."""

    def setUp(self):
        self.p = [mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
                  mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
                  mock.patch.object(scheduler, "gpu_state", return_value=(False, "free")),
                  # Pin training_busy to prevent false-positive LOST card exclusion checks
                  mock.patch.object(scheduler, "training_busy", return_value=(False, ""))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_a_lost_card_is_excluded_from_placement(self):
        def health(host):
            return (False, "GPU NOT IN USE") if host == scheduler.BRAIN else (True, "ok")
        with mock.patch.object(scheduler, "gpu_health", side_effect=health):
            hosts = [h for h, _s, _w in scheduler.place("vision", vram_gb=6.0)]
        self.assertNotIn(scheduler.BRAIN, hosts)
        self.assertTrue(hosts, "the rest of the fleet must still be usable")

    def test_cpu_work_still_allowed_on_a_lost_card_host(self):
        """The GPU is gone; the CPU is fine. Do not write off the whole box."""
        def health(host):
            return (False, "GPU NOT IN USE") if host == scheduler.FAILOVER else (True, "ok")
        with mock.patch.object(scheduler, "gpu_health", side_effect=health):
            hosts = [h for h, _s, _w in scheduler.place("cpu")]
        self.assertIn(scheduler.FAILOVER, hosts)


if __name__ == "__main__":
    unittest.main()


class UnreachableIsNotHealthyTests(unittest.TestCase):
    """
        Verify that unreachable hosts are reported as "unreachable" rather than
        "ok", preventing false positive health status due to network isolation.

        `gpu_health` fails soft (returns ok on probe failure) so placement
        decisions don't condemn cards for transient issues. This test ensures
        the reporting layer distinguishes between a healthy card and an
        unreachable one, correcting the trade-off made by the health check.
    """

    def test_unreachable_host_is_reported_separately_from_lost(self):
        # Reachability is decided by the box's own configured URL, so the stub keys on the
        # fleet table rather than an address written in the test.
        honker = scheduler.CARDS[scheduler.BRAIN]["url"]
        with mock.patch.object(scheduler, "gpu_health", return_value=(True, "ok")), \
                mock.patch("ollama_client.reachable", side_effect=lambda u: u != honker):
            rep = scheduler.gpu_lost_report()
        self.assertFalse(rep[scheduler.BRAIN]["reachable"])
        self.assertEqual(rep[scheduler.BRAIN]["reason"], "unreachable")
        self.assertTrue(rep[scheduler.LEAD_BOX]["reachable"])

    def test_a_reachable_healthy_fleet_still_reads_clean(self):
        with mock.patch.object(scheduler, "gpu_health", return_value=(True, "ok")), \
                mock.patch("ollama_client.reachable", return_value=True):
            rep = scheduler.gpu_lost_report()
        self.assertTrue(all(v["reachable"] and v["healthy"] for v in rep.values()))

    def test_probe_error_does_not_cry_wolf(self):
        """Same discipline as everywhere else: cannot-tell is not a verdict."""
        with mock.patch.object(scheduler, "gpu_health", return_value=(True, "ok")), \
                mock.patch("ollama_client.reachable", side_effect=RuntimeError("boom")):
            rep = scheduler.gpu_lost_report()
        self.assertTrue(all(v["reachable"] for v in rep.values()))
