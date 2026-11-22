"""Probes must not wait out timeouts for absent services.\nHTTP calls are patched; no real network I/O occurs."""
import unittest
from unittest import mock

import comfy_client
import os

import config
import scheduler


class GlancesAskOnlyHostsThatRunIt(unittest.TestCase):
    def test_bighonker_is_never_dialled(self):
        with mock.patch("httpx.get") as get:
            self.assertIsNone(scheduler._glances(scheduler.BRAIN, "cpu"))
        get.assert_not_called()

    def test_softserve_is_still_asked(self):
        # A Glances URL is a setting: the test names the box's endpoint rather than trusting a default.
        with mock.patch.dict(os.environ, {config._roster_device(scheduler.FAILOVER)["prefix"] + "_GLANCES":
                                          "http://glances.test:61208/api/4"}), \
                mock.patch("httpx.get") as get:
            get.return_value.json.return_value = {"total": 12.0}
            self.assertEqual(scheduler._glances(scheduler.FAILOVER, "cpu"), {"total": 12.0})
        get.assert_called_once()

    def test_an_unconfigured_box_is_asked_nothing(self):
        """No Glances setting means no live metrics from that box: ask nothing, never wait."""
        with mock.patch.dict(os.environ, {config._roster_device(scheduler.FAILOVER)["prefix"] + "_GLANCES": ""}), \
                mock.patch("httpx.get") as get:
            self.assertIsNone(scheduler._glances(scheduler.FAILOVER, "cpu"))
        get.assert_not_called()


class RenderProbeConnectIsShort(unittest.TestCase):
    def test_connect_timeout_is_the_measured_bound_and_read_stays(self):
        seen = {}

        def fake(url, timeout=None, **kw):
            seen["timeout"] = timeout
            raise OSError("dropped")
        with mock.patch("httpx.get", side_effect=fake), \
                mock.patch("p20_queue.gpu_status_any", return_value=(None, None)):
            comfy_client.render_busy()
        t = seen["timeout"]
        self.assertLessEqual(t.connect, comfy_client.RENDER_PROBE_CONNECT_S)
        self.assertEqual(t.read, 3.0)


if __name__ == "__main__":
    unittest.main()
