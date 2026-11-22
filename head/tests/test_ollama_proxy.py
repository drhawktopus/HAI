"""Placement-aware ollama proxy.

The properties worth protecting are all about NOT becoming a second scheduler:

  * placement must come from `scheduler.place`, never from a local ranking
  * no host available must FAIL CLOSED, not fall back to a favourite
  * a transport failure must NOT silently retry elsewhere — a hidden failover turns "mothership is
    broken" into "the cameras are slow", which is undiagnosable
  * the caller must be able to tell which box answered
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ollama_proxy as op  # noqa: E402
import scheduler  # noqa: E402


class FakePlace(object):
    def __init__(self, ranked):
        self.ranked, self.calls = ranked, []

    def __call__(self, kind, **kw):
        self.calls.append((kind, kw))
        return self.ranked


class PlacementIsDelegated(unittest.TestCase):
    def setUp(self):
        self._place = scheduler.place
        op._place_cache.update({"at": 0.0, "host": None, "why": ""})   # cold cache per test

    def tearDown(self):
        scheduler.place = self._place

    def test_uses_the_scheduler_and_the_existing_vision_kind(self):
        f = FakePlace([(scheduler.LEAD_BOX, 91.0, "idle")])
        scheduler.place = f
        host, why = op.choose_host()
        self.assertEqual(host, scheduler.LEAD_BOX)
        self.assertEqual(f.calls[0][0], "vision",
                         "must reuse the existing `vision` kind, not invent one")

    def test_takes_the_scheduler_top_choice_not_its_own_preference(self):
        scheduler.place = FakePlace([(scheduler.BRAIN, 70.0, "ok"),
                                     (scheduler.FAILOVER, 99.0, "also ok")])
        host, _ = op.choose_host()
        self.assertEqual(host, scheduler.BRAIN,
                         "must honour place() ordering, not re-rank")

    def test_no_host_fails_CLOSED(self):
        scheduler.place = FakePlace([])
        host, why = op.choose_host()
        self.assertIsNone(host)
        self.assertIn("no host", why.lower())

    def test_a_scheduler_explosion_is_reported_not_swallowed(self):
        def boom(kind, **kw):
            raise RuntimeError("scheduler exploded")
        scheduler.place = boom
        host, why = op.choose_host()
        self.assertIsNone(host)
        self.assertIn("scheduler.place failed", why)


class ForwardBehaviour(unittest.TestCase):
    def setUp(self):
        self._place = scheduler.place
        op._place_cache.update({"at": 0.0, "host": None, "why": ""})   # cold cache per test

    def tearDown(self):
        scheduler.place = self._place

    def test_unplaceable_request_returns_503_and_never_calls_out(self):
        scheduler.place = FakePlace([])
        code, out = op.forward("/api/chat", {"model": "x"})
        self.assertEqual(code, 503)
        self.assertIn("could not place", out["error"])

    def test_unknown_host_has_no_invented_address(self):
        scheduler.place = FakePlace([("atlantis", 50.0, "?")])
        code, out = op.forward("/api/chat", {"model": "x"})
        self.assertEqual(code, 503)
        self.assertIn("no ollama base URL", out["error"])


class PlacementIsCached(unittest.TestCase):
    """Ensure `scheduler.place` results are cached.

    Unmocked `place()` probes hosts live (~11s latency), which is
    correctness-critical for request paths. This test verifies that
    caching prevents repeated slow probes during execution."""

    def setUp(self):
        self._place = scheduler.place
        op._place_cache.update({"at": 0.0, "host": None, "why": ""})

    def tearDown(self):
        scheduler.place = self._place
        op._place_cache.update({"at": 0.0, "host": None, "why": ""})

    def test_a_second_call_does_not_re_ask_the_scheduler(self):
        f = FakePlace([(scheduler.LEAD_BOX, 90.0, "idle")])
        scheduler.place = f
        op.choose_host()
        op.choose_host()
        op.choose_host()
        self.assertEqual(len(f.calls), 1, "placement re-probed on every call — 11 s each")

    def test_the_cached_answer_says_it_is_cached(self):
        scheduler.place = FakePlace([(scheduler.LEAD_BOX, 90.0, "idle")])
        op.choose_host()
        _host, why = op.choose_host()
        self.assertIn("cached", why, "a cached placement must be visible as cached")

    def test_force_fresh_bypasses_the_cache(self):
        f = FakePlace([(scheduler.LEAD_BOX, 90.0, "idle")])
        scheduler.place = f
        op.choose_host()
        op.choose_host(force_fresh=True)
        self.assertEqual(len(f.calls), 2)

    def test_an_exclude_is_never_served_from_cache(self):
        """A caller excluding a host wants a DIFFERENT answer; handing back the cached one would
        silently ignore the exclusion."""
        f = FakePlace([(scheduler.LEAD_BOX, 90.0, "idle")])
        scheduler.place = f
        op.choose_host()
        op.choose_host(exclude=(scheduler.LEAD_BOX,))
        self.assertEqual(len(f.calls), 2)


class AddressBookIsSingular(unittest.TestCase):
    """An earlier draft invented `scheduler.HOST_IPS` and fell back to `<host>.lan` — a second
    address book that drifts the first time an IP changes."""

    def test_known_hosts_resolve_to_the_config_urls(self):
        import config
        self.assertEqual(op._base_url(scheduler.BRAIN), config.BRAIN_HOST)
        self.assertEqual(op._base_url(scheduler.LEAD_BOX), config.HEAVY_HOST)
        self.assertEqual(op._base_url(scheduler.FAILOVER), config.FAILOVER_HOST)

    def test_unknown_host_resolves_to_None_rather_than_a_guess(self):
        self.assertIsNone(op._base_url("atlantis"))


class RoutesExist(unittest.TestCase):
    def test_the_ollama_surface_frigate_needs_is_mounted(self):
        """Test endpoints required by OllamaClient to initialize provider and handle requests."""
        paths = {r.path for r in op.router.routes}
        for p in ("/ollama/api/show",       # OllamaClient._init_provider -> client.show(model)
                  "/ollama/api/chat",       # OllamaClient._send (vision requests)
                  "/ollama/api/generate",   # non-chat callers
                  "/ollama/api/tags"):      # capability listing / our own diagnostics
            self.assertIn(p, paths)

    def test_there_is_a_diagnostic_for_where_it_would_go(self):
        self.assertIn("/ollama/where", {r.path for r in op.router.routes})


class GuardsAreActuallyLoadBearing(unittest.TestCase):
    def setUp(self):
        self._place = scheduler.place
        op._place_cache.update({"at": 0.0, "host": None, "why": ""})   # cold cache per test

    def tearDown(self):
        scheduler.place = self._place

    def test_removing_fail_closed_would_be_caught(self):
        scheduler.place = FakePlace([])
        code, _ = op.forward("/api/chat", {})
        self.assertNotEqual(code, 200,
                            "empty placement produced a success — fail-closed is not load-bearing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
