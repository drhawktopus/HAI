"""Wake path invariants:
1. Reachability checks the box directly, bypassing probe quiet windows.
2. A box accepting TCP but failing HTTP is a STALL: suppress WoL and ssh restart; report 'stalled' instead of 'did not wake'.
3. 'Ollama Serve' ssh recovery executes at most once per wake.
4. Overflow status uses `app.LEAD_UNAVAILABLE`."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import ollama_client  # noqa: E402


class DirectProbeIgnoresTheQuietWindow(unittest.TestCase):
    def test_quiet_window_does_not_answer_for_the_box(self):
        host = "http://192.0.2.76:11434"
        with mock.patch.object(ollama_client, "_PROBE_QUIET", {ollama_client._base(host): time.monotonic() + 60}):
            self.assertTrue(ollama_client.probe_quiet(host))             # the cached answer would be "could not ask"
            self.assertFalse(ollama_client.reachable(host))              # ... and reachable() says exactly that
            calls = []

            class _R:
                status_code = 200

            with mock.patch.object(ollama_client.httpx, "get", lambda url, timeout=None: calls.append(url) or _R()):
                self.assertTrue(ollama_client.reachable_direct(host))    # the direct probe ASKS
            self.assertEqual(len(calls), 1)
            self.assertIn("/api/version", calls[0])
            self.assertFalse(ollama_client.probe_quiet(host))            # and a real answer closes the window

    def test_a_direct_timeout_rearms_the_window_for_everyone_else(self):
        host = "http://192.0.2.77:11434"
        with mock.patch.object(ollama_client, "_PROBE_QUIET", {}), \
                mock.patch.object(ollama_client.httpx, "get", mock.Mock(side_effect=ollama_client.httpx.ReadTimeout("t"))):
            self.assertFalse(ollama_client.reachable_direct(host))
            self.assertTrue(ollama_client.probe_quiet(host))


class StalledBoxIsLeftAlone(unittest.TestCase):
    def setUp(self):
        self.said = []
        self.wol = []
        self.ssh = []
        p = [mock.patch.object(app.wol, "wake", lambda *_: self.wol.append(1)),
             mock.patch.object(app.time, "sleep", lambda *_: None),
             mock.patch.object(app.lead_ssh, "restart_ollama", lambda: self.ssh.append(1) or False),
             mock.patch.object(app.config, "WAKE_TIMEOUT", 0.05),
             mock.patch.object(app.config, "WAKE_POLL", 0)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def test_tcp_open_http_silent_is_a_stall_not_a_wake(self):
        with mock.patch.object(app.ollama_client, "reachable_direct", lambda *_: False), \
                mock.patch.object(app.ollama_client, "port_open", lambda *a, **k: True):
            self.assertFalse(app._ensure_lead(self.said.append))
        self.assertEqual(self.wol, [], "a box that answers TCP is awake: no WoL")
        self.assertEqual(self.ssh, [], "a stalled box gets no ssh at all")
        self.assertEqual(len(self.said), 1)
        self.assertIn("stall", self.said[0])
        self.assertNotIn("Waking", self.said[0])
        self.assertEqual(app.LEAD_UNAVAILABLE["why"], app.STALL_WHY)

    def test_asleep_box_gets_one_restart_attempt_per_wake_even_when_it_fails(self):
        polls = {"n": 0}

        def no(*_):
            polls["n"] += 1
            return False
        with mock.patch.object(app.ollama_client, "reachable_direct", no), \
                mock.patch.object(app.ollama_client, "port_open", lambda *a, **k: False), \
                mock.patch.object(app.time, "time", mock.Mock(side_effect=[0, 0, 0.01, 0.02, 0.03, 0.04, 1, 1, 1])):
            self.assertFalse(app._ensure_lead(self.said.append))
        self.assertGreaterEqual(polls["n"], 3, "the loop polled more than once")
        self.assertEqual(self.ssh, [1], "exactly one 'Ollama Serve' attempt per wake, not one per poll")
        self.assertEqual(self.wol, [1])
        self.assertEqual(app.LEAD_UNAVAILABLE["why"], "did not wake")

    def test_a_box_that_wakes_to_tcp_but_not_http_stops_the_wake_as_a_stall(self):
        ports = iter([False, False, True, True, True])
        with mock.patch.object(app.ollama_client, "reachable_direct", lambda *_: False), \
                mock.patch.object(app.ollama_client, "port_open", lambda *a, **k: next(ports)), \
                mock.patch.object(app.time, "time", mock.Mock(side_effect=[0, 0, 0.01, 0.02, 0.03, 0.04, 1, 1, 1])):
            self.assertFalse(app._ensure_lead(self.said.append))
        self.assertEqual(app.LEAD_UNAVAILABLE["why"], app.STALL_WHY)
        self.assertLessEqual(len(self.ssh), 1)
        self.assertTrue(any("stall" in s for s in self.said))


class TheOverflowStatusSaysWhatWasMeasured(unittest.TestCase):
    def test_mccoy_overflow_line_uses_the_recorded_reason(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
        # the one place the generic sentence used to live now reads the recorded reason (a wiring pin: the reason has
        # no other observable path to the stream from a unit test)
        self.assertIn('why = LEAD_UNAVAILABLE.get("why") or "did not wake"', src)
        self.assertIn('f"⚠ {config.LEAD_BOX_LABEL} {why} — answering on the 8 GB tier"', src)
        self.assertNotIn('"⚠ Mothership did not wake — answering on the 8 GB tier"', src)


if __name__ == "__main__":
    unittest.main()
