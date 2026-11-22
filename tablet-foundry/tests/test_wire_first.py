"""USB wire first, WiFi as the backup: node_ip, the ssh fallback, and the agent-call fallback.

No real sockets or subprocesses: the probe socket, sh_argv and urlopen are replaced. The fallback may
run only when the wire connection was never made, so a command that started is never run twice."""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as tf

_WIRE = mock.patch.object(tf, "WIRE_PREFIX", "192.0.2.")    # the wire subnet is a setting; these tests use a documentation one


def setUpModule():
    _WIRE.start()


def tearDownModule():
    _WIRE.stop()


class _Sock:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def __call__(self, *a, **k):
        return self

    def settimeout(self, t):
        pass

    def connect_ex(self, addr):
        self.calls += 1
        self.addr = addr
        return 0 if self.answer else 111

    def close(self):
        pass


class NodeIp(unittest.TestCase):
    def setUp(self):
        tf._ROUTE.clear()
        self._probe = mock.patch.object(tf, "ROUTE_PROBE", True)
        self._probe.start()
        self.addCleanup(self._probe.stop)

    def test_wire_when_the_port_answers_there(self):
        s = _Sock(True)
        with mock.patch.object(tf.socket, "socket", s):
            self.assertEqual(tf.node_ip("frodo", 8022), ("192.0.2.11", "wire"))
        self.assertEqual(s.addr, ("192.0.2.11", 8022))

    def test_wifi_when_the_wire_does_not_answer(self):
        with mock.patch.object(tf.socket, "socket", _Sock(False)):
            self.assertEqual(tf.node_ip("frodo", 8022), (tf.TABLET_NET["frodo"][0], "wifi"))

    def test_the_verdict_is_remembered_then_refreshed(self):
        s = _Sock(True)
        with mock.patch.object(tf.socket, "socket", s):
            tf.node_ip("frodo", 8022)
            tf.node_ip("frodo", 8022)
            self.assertEqual(s.calls, 1)
            tf.node_ip("frodo", 8022, refresh=True)
            self.assertEqual(s.calls, 2)

    def test_every_rostered_node_has_a_wire_octet(self):
        self.assertEqual(set(tf.WIRE_OCTET), set(tf.TABLET_NET))
        self.assertEqual(len(set(tf.WIRE_OCTET.values())), len(tf.WIRE_OCTET))


class SshFallback(unittest.TestCase):
    def setUp(self):
        tf._ROUTE.clear()
        self._probe = mock.patch.object(tf, "ROUTE_PROBE", True)
        self._probe.start()
        self.addCleanup(self._probe.stop)
        tf._ROUTE[("frodo", 8022)] = ("192.0.2.11", "wire", 1e12)

    def run_ssh(self, replies):
        calls = []

        def fake(argv, timeout=300):
            calls.append(argv)
            return replies[len(calls) - 1]
        with mock.patch.object(tf, "sh_argv", side_effect=fake), \
             mock.patch.object(tf.socket, "socket", _Sock(False)):
            rc, out = tf._ssh("frodo", "echo hi")
        return rc, out, calls

    def test_refused_on_the_wire_retries_once_over_wifi(self):
        rc, out, calls = self.run_ssh([(255, "ssh: connect to host 192.0.2.11 port 8022: Connection refused"),
                                       (0, "hi")])
        self.assertEqual((rc, out), (0, "hi"))
        self.assertEqual(len(calls), 2)
        self.assertIn("192.0.2.11", calls[0])
        self.assertIn(tf.TABLET_NET["frodo"][0], calls[1])
        self.assertEqual(tf._ROUTE[("frodo", 8022)][1], "wifi")      # pinned for the TTL

    def test_a_command_that_started_is_never_run_twice(self):
        rc, out, calls = self.run_ssh([(255, "Connection to 192.0.2.11 closed by remote host.")])
        self.assertEqual(rc, 255)
        self.assertEqual(len(calls), 1)

    def test_no_clock_means_keepalives_on_both_routes(self):
        """timeout=None (a batch with no stated clock) must carry SSH keepalives, or a dead link hangs forever."""
        calls, timeouts = [], []

        def fake(argv, timeout=300):
            calls.append(argv)
            timeouts.append(timeout)
            return (255, "ssh: connect to host 192.0.2.11 port 8022: Connection refused") if len(calls) == 1 else (0, "ok")
        with mock.patch.object(tf, "sh_argv", side_effect=fake), \
             mock.patch.object(tf.socket, "socket", _Sock(False)):
            tf._ssh("frodo", "work", timeout=None)
        self.assertEqual(len(calls), 2)
        for argv in calls:
            self.assertIn("ServerAliveInterval=30", argv)
        self.assertEqual(timeouts, [None, None])
        calls.clear()
        with mock.patch.object(tf, "sh_argv", side_effect=lambda a, timeout=300: (calls.append(a), (0, ""))[1]), \
             mock.patch.object(tf.socket, "socket", _Sock(False)):
            tf._ssh("frodo", "quick", timeout=60)
        self.assertNotIn("ServerAliveInterval=30", calls[0], "short calls keep their own clock, no keepalive needed")

    def test_success_on_the_wire_is_one_call(self):
        rc, out, calls = self.run_ssh([(0, "hi")])
        self.assertEqual(len(calls), 1)
        self.assertIn("192.0.2.11", calls[0])


class FellowFallback(unittest.TestCase):
    def setUp(self):
        tf._ROUTE.clear()
        self._probe = mock.patch.object(tf, "ROUTE_PROBE", True)
        self._probe.start()
        self.addCleanup(self._probe.stop)
        tf._ROUTE[("frodo", tf.FELLOW_PORT)] = ("192.0.2.11", "wire", 1e12)

    def test_connect_failure_on_the_wire_retries_over_wifi(self):
        urls = []

        def fake(req, data=None, timeout=None):
            urls.append(req.full_url)
            if len(urls) == 1:
                raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))
            return io.BytesIO(json.dumps({"ok": True}).encode())
        with mock.patch.object(tf.urllib.request, "urlopen", side_effect=fake):
            ok, obj = tf.fellow_call("frodo", "/health", token="t")
        self.assertTrue(ok)
        self.assertIn("192.0.2.11", urls[0])
        self.assertIn(tf.TABLET_NET["frodo"][0], urls[1])

    def test_an_http_error_is_not_retried(self):
        urls = []

        def fake(req, data=None, timeout=None):
            urls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b'{"ok": false}'))
        with mock.patch.object(tf.urllib.request, "urlopen", side_effect=fake):
            ok, obj = tf.fellow_call("frodo", "/run", body={"x": 1}, token="t")
        self.assertFalse(ok)
        self.assertEqual(len(urls), 1)


if __name__ == "__main__":
    unittest.main()
