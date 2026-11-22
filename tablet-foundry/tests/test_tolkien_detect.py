"""Tests for Tolkien's detect shim and router.\n\nPinned behaviors:\n- Returns non-empty prediction lists on success; never returns 200 with an empty list on failure,\n  to prevent Frigate from mistaking broken fleets for quiet ones.\n- Distributes requests across nodes; prevents collapse onto a single node.\n- Removes dead nodes from rotation and forgives transient failures to avoid emptying the fleet.\n- Returns distinct error messages for asleep, cooling, and error states.\n\nUses unittest (not pytest) because the deploy gate uses `unittest discover`.\nMocks forward requests; these tests pin routing logic, not detection accuracy."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as TF


def _fake_forward(fail_hosts=(), calls=None):
    """Stand-in for urlopen: succeeds unless the target host is in fail_hosts."""
    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def read(self):
            return json.dumps(self._p).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        host = next((h for h, (ip, _s) in TF.TABLET_NET.items() if ip in url), "?")
        if calls is not None:
            calls.append((host, getattr(req, "data", None)))
        if host in fail_hosts:
            raise OSError("connection refused")
        return _Resp({"success": True, "predictions": [{"label": "cat", "confidence": 0.9}]})

    return fake


class DetectRouterTests(unittest.TestCase):
    def setUp(self):
        with TF._DETECT_ROUTE_LK:
            TF._DETECT_ROUTE.update({"live": [], "ts": 0.0, "rr": 0,
                                     "cool": {}, "sent": {}, "fail": {}})
        # every node's detector looks live, and no real probe runs
        p = mock.patch.object(TF, "detect_live_nodes",
                              side_effect=lambda force=False: sorted(TF.TABLET_NET))
        p.start()
        self.addCleanup(p.stop)
        k = mock.patch.object(TF, "DETECT_KEY", "")
        k.start()
        self.addCleanup(k.stop)

    def _patch_forward(self, **kw):
        p = mock.patch.object(TF.urllib.request, "urlopen", _fake_forward(**kw))
        p.start()
        self.addCleanup(p.stop)

    def test_spreads_across_the_fleet(self):
        calls = []
        self._patch_forward(calls=calls)
        for _ in range(len(TF.TABLET_NET)):
            status, _obj = TF.detect_route(b"body", "multipart/form-data; boundary=x")
            self.assertEqual(status, 200)
        hosts = {h for h, _b in calls}
        self.assertEqual(len(hosts), len(TF.TABLET_NET),
                         "router did not use the whole fleet: %s" % hosts)

    def test_body_is_forwarded_verbatim(self):
        """A re-encode is a second multipart implementation that can disagree with the first."""
        calls = []
        self._patch_forward(calls=calls)
        body = (b"--x\r\nContent-Disposition: form-data; name=\"image\"\r\n\r\n"
                b"\xff\xd8JPEG\r\n--x--\r\n")
        TF.detect_route(body, "multipart/form-data; boundary=x")
        self.assertTrue(calls)
        self.assertEqual(calls[0][1], body)

    def test_names_the_serving_node(self):
        """A bad prediction has to be traceable to the fellow that made it."""
        self._patch_forward()
        _s, obj = TF.detect_route(b"b", "ct")
        self.assertIn(obj["node"], TF.TABLET_NET)

    def test_failing_node_is_cooled_and_request_still_served(self):
        """One dead fellow must cost one retry, not the request."""
        dead = sorted(TF.TABLET_NET)[0]
        self._patch_forward(fail_hosts=(dead,))
        served = []
        for _ in range(12):
            status, obj = TF.detect_route(b"b", "ct", attempts=3)
            if status == 200:
                served.append(obj["node"])
        self.assertTrue(served, "every request failed because of one dead node")
        self.assertNotIn(dead, served[-5:], "dead node stayed in rotation")
        self.assertGreaterEqual(TF._DETECT_ROUTE["fail"].get(dead, 0), 1)

    def test_cooldown_expires_so_the_fleet_recovers(self):
        """A router that never forgives ends up with an empty fleet."""
        self._patch_forward()
        host = sorted(TF.TABLET_NET)[0]
        TF._detect_mark(host, False)
        with TF._DETECT_ROUTE_LK:
            self.assertGreater(TF._DETECT_ROUTE["cool"][host], 0)
            TF._DETECT_ROUTE["cool"][host] = 0        # simulate the cooldown having elapsed
        seen = set()
        for _ in range(12):
            _s, obj = TF.detect_route(b"b", "ct")
            seen.add(obj.get("node"))
        self.assertIn(host, seen, "node never returned to rotation after its cooldown")

    def test_never_200_with_empty_predictions_when_the_fleet_is_down(self):
        """THE load-bearing one: an empty list and an empty room must never look the same."""
        self._patch_forward(fail_hosts=tuple(TF.TABLET_NET))
        status, obj = TF.detect_route(b"b", "ct", attempts=3)
        self.assertEqual(status, 503, "fail-open: a dead fleet answered %s" % status)
        self.assertFalse(obj.get("predictions"))
        self.assertIs(obj.get("success"), False)

    def test_failure_reasons_are_distinguishable(self):
        """Asleep, cooling and erroring are three diagnoses, not one word."""
        self._patch_forward(fail_hosts=tuple(TF.TABLET_NET))
        _s, erroring = TF.detect_route(b"b", "ct", attempts=2)
        with mock.patch.object(TF, "detect_live_nodes", side_effect=lambda force=False: []):
            _s, asleep = TF.detect_route(b"b", "ct")
        self.assertNotEqual(erroring["error"], asleep["error"])
        self.assertIn("live detector", asleep["error"])

    def test_detect_key_fails_loudly_rather_than_401_per_frame(self):
        """The proxy cannot inject an api_key into a body it deliberately does not parse."""
        with mock.patch.object(TF, "DETECT_KEY", "secret"):
            status, obj = TF.detect_route(b"b", "ct")
        self.assertEqual(status, 503)
        self.assertIn("TABLET_DETECT_KEY", obj["error"])

    def test_status_surface_reports_what_it_measured(self):
        self._patch_forward()
        TF.detect_route(b"b", "ct")
        st = TF.detect_route_status()
        self.assertLessEqual({"live", "age", "cooling", "sent", "failed"}, set(st))
        self.assertGreaterEqual(sum(st["sent"].values()), 1)

    def test_cold_cache_is_not_reported_as_a_dead_fleet(self):
        """A container that has served nothing must not look like five dead tablets.

        The first version returned the raw epoch as `age` and an empty `live`, i.e. 56-year-old
        data about a fleet that is actually fine. 'Never asked' and 'asked, nobody answered' are
        different states and a passive cache read must say which one it is."""
        with TF._DETECT_ROUTE_LK:
            TF._DETECT_ROUTE.update({"live": [], "ts": 0.0, "rr": 0,
                                     "cool": {}, "sent": {}, "fail": {}})
        st = TF.detect_route_status()
        self.assertIs(st["probed"], False)
        self.assertIsNone(st["age"], "a cold cache reported a numeric age")
        self.assertIs(st["fresh"], False)
        self.assertIn("not empty", st["note"] or "")

    def test_a_stale_cache_says_so(self):
        """`live` from twenty minutes ago is a memory, not a measurement."""
        self._patch_forward()
        TF.detect_route(b"b", "ct")
        with TF._DETECT_ROUTE_LK:
            TF._DETECT_ROUTE["ts"] = TF.time.time() - (TF.DETECT_ROUTE_TTL + 60)
        st = TF.detect_route_status()
        self.assertIs(st["probed"], True)
        self.assertIs(st["fresh"], False)
        self.assertGreater(st["age"], TF.DETECT_ROUTE_TTL)


class PauseSwitch(unittest.TestCase):
    """The pause is signaled by a marker file to persist across agent respawns and reboots."""

    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp(prefix="detpause_")
        self.addCleanup(__import__("shutil").rmtree, self.d, True)
        self.p = [mock.patch.object(TF, "DETECT_PAUSE", os.path.join(self.d, "detect.paused"))]
        for p in self.p:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.p])
        self.saved = dict(TF._DETECT)
        self.addCleanup(lambda: TF._DETECT.update(self.saved))

    def test_a_paused_node_reports_the_detector_unavailable_by_name(self):
        TF.detect_pause("cameras paused")
        ok, why = TF.detect_available()
        self.assertFalse(ok)
        self.assertIn("paused by Tolkien", why)

    def test_a_pause_never_hides_a_missing_binary(self):
        TF.detect_pause("x")
        with mock.patch.object(TF, "DETECT_BIN", os.path.join(self.d, "no_such_ncnn_detect")):
            ok, why = TF.detect_available()
        self.assertFalse(ok)
        self.assertIn("paused by Tolkien", why)
        self.assertIn("ALSO missing detector binary", why)
        bin_ = os.path.join(self.d, "ncnn_detect")
        for p in (bin_, os.path.join(self.d, "p"), os.path.join(self.d, "w")):
            open(p, "w").close()
        with mock.patch.multiple(TF, DETECT_BIN=bin_, DETECT_PARAM=os.path.join(self.d, "p"),
                                 DETECT_WEIGHTS=os.path.join(self.d, "w")):
            self.assertIn("ready to resume", TF.detect_available()[1])

    def test_pause_stops_a_running_detector_and_leaves_the_marker(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        TF._DETECT["proc"] = proc
        out = TF.detect_pause("x")
        proc.terminate.assert_called_once()
        self.assertTrue(out["stopped"])
        self.assertIsNone(TF._DETECT["proc"])
        self.assertTrue(os.path.exists(TF.DETECT_PAUSE))

    def test_resume_clears_the_marker_and_starts_the_detector(self):
        TF.detect_pause("x")
        TF._DETECT["listener"] = True
        TF._DETECT["proc"] = None
        with mock.patch.object(TF, "_detect_spawn") as spawn:
            out = TF.detect_resume()
        spawn.assert_called_once()
        self.assertFalse(os.path.exists(TF.DETECT_PAUSE))
        self.assertTrue(out["started"])

    def test_resume_on_an_agent_that_booted_paused_starts_the_listener_too(self):
        TF.detect_pause("x")
        TF._DETECT["listener"] = False
        with mock.patch.object(TF, "detect_serve", return_value=(True, "ok")) as serve:
            TF.detect_resume()
        serve.assert_called_once()

    def test_the_agent_routes_need_the_token(self):
        self.addCleanup(mock.patch.dict(os.environ, {"TABLET_FELLOW_TOKEN": "tok-test"}).stop)
        mock.patch.dict(os.environ, {"TABLET_FELLOW_TOKEN": "tok-test"}).start()
        self.assertEqual(TF._fellow_app("POST", "/detect/pause", {}, "wrong")[0], 401)
        code, obj = TF._fellow_app("POST", "/detect/pause", {"why": "cameras paused"}, "tok-test")
        self.assertEqual(code, 200)
        self.assertFalse(obj["detector"]["available"])
        with mock.patch.object(TF, "_detect_spawn"):
            TF._DETECT["listener"] = True
            code, obj = TF._fellow_app("POST", "/detect/resume", {}, "tok-test")
        self.assertEqual(code, 200)
        self.assertFalse(os.path.exists(TF.DETECT_PAUSE))

    def test_tolkiens_switch_calls_every_named_fellow(self):
        calls = []
        with mock.patch.object(TF, "fellow_call", side_effect=lambda h, path, body=None, timeout=None:
                               calls.append((h, path)) or (True, {"ok": True})):
            out = TF.detect_switch(["frodo", "sam"], pause=True, why="cameras paused")
        self.assertEqual(sorted(calls), [("frodo", "/detect/pause"), ("sam", "/detect/pause")])
        self.assertEqual(set(out), {"frodo", "sam"})


class ShimTests(unittest.TestCase):
    """The HTTP door itself: the layer that was wrong last time, tested where it now lives."""

    def setUp(self):
        with TF._DETECT_ROUTE_LK:
            TF._DETECT_ROUTE.update({"live": [], "ts": 0.0, "rr": 0,
                                     "cool": {}, "sent": {}, "fail": {}})
        p = mock.patch.object(TF, "detect_live_nodes",
                              side_effect=lambda force=False: sorted(TF.TABLET_NET))
        p.start()
        self.addCleanup(p.stop)
        k = mock.patch.object(TF, "DETECT_KEY", "")
        k.start()
        self.addCleanup(k.stop)

    def test_detection_post_is_served_and_names_the_node(self):
        with mock.patch.object(TF.urllib.request, "urlopen", _fake_forward()):
            status, obj = TF._detect_head_app(
                "POST", "/v1/vision/detection", b"body", "multipart/form-data; boundary=x")
        self.assertEqual(status, 200)
        self.assertIn(obj["node"], TF.TABLET_NET)
        self.assertTrue(obj["predictions"])

    def test_a_dead_fleet_gets_503_from_the_shim_too(self):
        """The never-200-with-an-empty-list rule has to hold at THIS hop as well."""
        with mock.patch.object(TF.urllib.request, "urlopen",
                               _fake_forward(fail_hosts=tuple(TF.TABLET_NET))):
            status, obj = TF._detect_head_app("POST", "/v1/vision/detection", b"b", "ct")
        self.assertEqual(status, 503)
        self.assertFalse(obj.get("predictions"))

    def test_health_identifies_itself_as_the_head_shim(self):
        status, obj = TF._detect_head_app("GET", "/health", b"", "")
        self.assertEqual(status, 200)
        self.assertEqual(obj["role"], "tolkien-detect-shim")
        self.assertEqual(obj["port"], TF.DETECT_HEAD_PORT)

    def test_unknown_path_404s_rather_than_pretending(self):
        status, _obj = TF._detect_head_app("GET", "/nope", b"", "")
        self.assertEqual(status, 404)



if __name__ == "__main__":
    unittest.main()
