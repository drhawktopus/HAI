"""Softserve does not hold its own claim to block a turn.

The Plex gate must check the model the order *would* serve, not the model currently
hosting the session. A live claim for a different model (e.g., 9B) must not prevent
a pinned turn for this model (e.g., 8B) from running."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import config  # noqa: E402
import scheduler  # noqa: E402

NINE = "qwen3.5:9b"
WINDOW = {"mode": "compute", "load": "gpu", "payload": "{FOUNDRY_ROOT}\\bin\\helper_window.py",
          "args": ["\\\\share\\w", "{ORDER_OUT}\\out.json", "--model", NINE, "--num-ctx", "47104", "--live"]}


class TheGateAsksAboutTheOrdersOwnModel(unittest.TestCase):
    def test_a_claim_for_the_model_being_served_is_not_somebody_else(self):
        asked = []

        def claimed(url, model=None):
            asked.append(model)
            return None if model == NINE else {"holder": "hai-chat:ClaudeSupervisor"}     # the run's own 9B claim
        with mock.patch.object(app, "_card_claimed", claimed), \
                mock.patch("ollama_client.reachable", return_value=True), \
                mock.patch("ollama_client.models_loaded", return_value=["qwen3:8b"]):
            self.assertTrue(app.failover_available(NINE)[0], "its own claim does not hold its own window")
            ok, why = app.failover_available()
        self.assertFalse(ok, "asked about the 8B, the same claim still holds the card")
        self.assertIn("claimed", why)
        self.assertEqual(asked, [NINE, config.FAILOVER_MODEL])

    def test_the_desk_hands_the_window_orders_model_to_the_gate(self):
        seen = []

        def gate(model=None):
            seen.append(model)
            return False, "1 Plex transcode(s)"
        with mock.patch.object(scheduler, "_plex_gate", gate), \
                mock.patch.object(scheduler, "plex_transcoding", return_value=(False, 0, "")), \
                mock.patch.object(scheduler, "gpu_health", return_value=(True, "")), \
                mock.patch.object(scheduler, "render_busy", return_value=(False, "")):
            why = scheduler.order_host_unavailable(scheduler.FAILOVER, "render_small", WINDOW)
            scheduler.order_host_unavailable(scheduler.FAILOVER, "render_small")
        self.assertEqual(seen, [NINE, None], "an order with no model asks as before")
        self.assertIn("Plex first", why, "and Plex still comes first")


if __name__ == "__main__":
    unittest.main()
