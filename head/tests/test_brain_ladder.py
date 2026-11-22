"""BigHonker (8GB) cannot co-reside with the VLM (5.62GB). Ensure the router avoids BigHonker when it is busy, preventing eviction and thrashing."""
import unittest
from unittest import mock

import app
import config

# Captured at import to bind the real function, as `setUpModule` stubs `app.failover_available` for the entire module.
_REAL_FAILOVER_AVAILABLE = app.failover_available


class BrainHostBusyTests(unittest.TestCase):
    """
    Tests for brain_host_busy().

    Verifies the function checks explicit claims (reservations), not VRAM residency.
    A model in VRAM cache is only considered "busy" if it holds an active claim;
    otherwise, warm cached models should not block allocation.
    """

    def tearDown(self):
        import scheduler
        scheduler.release(scheduler.BRAIN, "gpu")

    def _busy(self, model):
        with mock.patch("ollama_client.model_state", return_value=(model, "hot")):
            return app.brain_host_busy()

    def test_no_resident_model_makes_the_card_busy(self):
        """Not the brain, not a VLM, not an embed — residency reserves nothing."""
        for m in (config.BRAIN_MODEL, "qwen3-vl:8b", "nomic-embed-text:latest", "", "—"):
            self.assertFalse(self._busy(m), "%r must not reserve the card" % m)

    def test_only_an_explicit_claim_makes_it_busy(self):
        import scheduler
        self.assertFalse(app.brain_host_busy())
        scheduler.claim(scheduler.BRAIN, "gpu", "recaption-v3", ttl=60, note="vlm")
        self.assertTrue(app.brain_host_busy())
        scheduler.release(scheduler.BRAIN, "gpu")
        self.assertFalse(app.brain_host_busy())

    def test_an_expired_claim_frees_the_card(self):
        """Rolling TTL: a job that dies must not strand the chat default forever."""
        import scheduler, time
        scheduler.claim(scheduler.BRAIN, "gpu", "dead-job", ttl=1)
        self.assertTrue(app.brain_host_busy())
        time.sleep(1.2)
        self.assertFalse(app.brain_host_busy())

    def test_probe_failure_fails_SOFT(self):
        """Opposite trade from the GPU guards: chat is interactive, so unknown means available."""
        with mock.patch("scheduler.active_claim", side_effect=RuntimeError("probe down")):
            self.assertFalse(app.brain_host_busy())


# Disable live network probes in setUpModule to prevent test timeouts.
# place() calls gpu_health() and ollama_client.model_state(), which default
# to 4s REACH_TIMEOUT. Without mocking, tests fail if the fleet is idle/down.
# Overrides are allowed locally (e.g., BrainHostBusyTests) if needed.
from _no_live_probes import (start_no_live_probes, stop_no_live_probes,
                             start_no_wake_sleeps, stop_no_wake_sleeps)


def setUpModule():
    start_no_live_probes()
    # pick_brain() WoL-wakes a sleeping box and polls it with real sleeps (BRAIN_WAKE_POLL 2 s,
    # WAKE_POLL 5 s). These tests assert WHICH host routing picks, never how long it napped.
    start_no_wake_sleeps()


def tearDownModule():
    stop_no_wake_sleeps()
    stop_no_live_probes()


class LadderRoutingTests(unittest.TestCase):
    """Tests share a claim directory with PRODUCTION. Patches neutralize live claims
    to ensure placement outcomes are deterministic and not influenced by fleet state."""

    def setUp(self):
        import scheduler
        self._p = [mock.patch.object(scheduler, "active_claim", return_value=None),
                   mock.patch.object(scheduler, "training_busy", return_value=(False, ""))]
        for x in self._p:
            x.start()

    def tearDown(self):
        for x in self._p:
            x.stop()


    def test_busy_bighonker_falls_through_to_mothership(self):
        """The regression: a vision job on BigHonker must not be evicted by a chat request."""
        with mock.patch.object(app, "brain_host_busy", return_value=True), \
                mock.patch("ollama_client.reachable", return_value=True), \
                mock.patch("comfy_client.render_busy", return_value=False):
            host, _model, _ctx, _tools, label = app.pick_brain()
        self.assertEqual(host, config.HEAVY_HOST)
        self.assertIn("Mothership", label)

    def test_free_bighonker_is_still_preferred(self):
        with mock.patch.object(app, "brain_host_busy", return_value=False), \
                mock.patch("ollama_client.reachable", return_value=True):
            host, _model, _ctx, _tools, label = app.pick_brain()
        self.assertEqual(host, config.BRAIN_HOST)
        self.assertEqual(label, "BigHonker")

    def test_both_busy_reaches_softserve_when_plex_is_idle(self):
        with mock.patch.object(app, "brain_host_busy", return_value=True), \
                mock.patch("ollama_client.reachable", return_value=True), \
                mock.patch("comfy_client.render_busy", return_value=True), \
                mock.patch.object(app, "_ensure_lead", return_value=False), \
                mock.patch.object(app, "failover_available", return_value=(True, "idle")), \
                mock.patch.object(config, "WAKE_TIMEOUT", 0):
            host, _model, _ctx, _tools, label = app.pick_brain()
        self.assertEqual(host, config.FAILOVER_HOST)
        self.assertIn("Softserve", label)

    def test_softserve_skipped_when_serving_plex(self):
        """Softserve is the last resort, not a free-for-all: a live transcode still blocks it."""
        with mock.patch.object(app, "brain_host_busy", return_value=True), \
                mock.patch("ollama_client.reachable", return_value=True), \
                mock.patch("comfy_client.render_busy", return_value=True), \
                mock.patch.object(app, "_ensure_lead", return_value=False), \
                mock.patch.object(app, "failover_available", return_value=(False, "transcoding")), \
                mock.patch.object(config, "WAKE_TIMEOUT", 0):
            host, _model, _ctx, _tools, _label = app.pick_brain()
        self.assertIsNone(host)


class ChatFallbacksRespectClaims(unittest.TestCase):
    """The chat fallback path must use `scheduler.claim_for_url` to check availability, ensuring it respects the same claim invariants as the main scheduler."""

    def _claims(self, *held):
        import scheduler
        return lambda host, resource="gpu": ({"holder": "order-on-%s" % host, "expires": "2099-01-01T00:00:00+00:00"}
                                             if host in held and resource == "gpu" else None)

    def _pick(self, claims):
        import scheduler
        with mock.patch.object(scheduler, "active_claim", side_effect=claims), \
                mock.patch.object(scheduler, "training_busy", return_value=(False, "")), \
                mock.patch.object(app, "brain_host_busy", return_value=True), \
                mock.patch("ollama_client.reachable", return_value=True), \
                mock.patch("comfy_client.render_busy", return_value=False), \
                mock.patch.object(app, "_ensure_lead", return_value=True), \
                mock.patch.object(app, "failover_available", return_value=(True, "idle")), \
                mock.patch.object(config, "WAKE_TIMEOUT", 0):
            return app.pick_brain()

    def test_a_claimed_mothership_is_never_the_chat_fallback(self):
        import scheduler
        host, _m, _c, _t, label = self._pick(self._claims(scheduler.LEAD_BOX))
        self.assertNotEqual(host, config.HEAVY_HOST, label)
        self.assertEqual(host, config.FAILOVER_HOST, "the next box down, not the claimed card")

    def test_positive_control_an_unclaimed_mothership_is_still_used(self):
        host, _m, _c, _t, label = self._pick(self._claims())
        self.assertEqual(host, config.HEAVY_HOST, label)

    def test_the_recovery_list_and_softserve_honour_claims_too(self):
        import scheduler
        with mock.patch.object(scheduler, "active_claim", side_effect=self._claims(scheduler.LEAD_BOX,
                                                                                    scheduler.FAILOVER)), \
                mock.patch.object(app, "brain_host_busy", return_value=True), \
                mock.patch.object(app, "failover_available", _REAL_FAILOVER_AVAILABLE), \
                mock.patch("ollama_client.reachable", return_value=True):
            labels = [c[4] for c in app._brain_fallbacks(exclude_host="none")]
            ok, why = app.failover_available()
        self.assertFalse(any("Mothership" in l for l in labels), labels)
        self.assertFalse(ok)
        self.assertIn("claimed", why)

    def _recovery_labels(self, user=(None, ""), render=False):
        import scheduler
        with mock.patch.object(scheduler, "active_claim", return_value=None), \
                mock.patch.object(app, "brain_host_busy", return_value=True), \
                mock.patch.object(app, "failover_available", return_value=(False, "transcoding")), \
                mock.patch.object(scheduler, "user_on_card", return_value=user), \
                mock.patch("comfy_client.render_busy", return_value=render), \
                mock.patch("ollama_client.reachable", return_value=True):
            return [c[4] for c in app._brain_fallbacks(exclude_host="none")]

    def test_the_recovery_list_never_loads_onto_mothership_mid_game_or_mid_render(self):
        self.assertFalse(any("Mothership" in l for l in self._recovery_labels(
            user=("user", "a game holds the card (low free VRAM, no ollama model resident)"))))
        self.assertFalse(any("Mothership" in l for l in self._recovery_labels(render=True)))
        self.assertTrue(any("Mothership" in l for l in self._recovery_labels(user=("clear", "idle"))),
                        "positive control: an idle Mothership is still the recovery")


if __name__ == "__main__":
    unittest.main()
