"""Policy 4: Softserve must not interpret its own resident model as Plex availability.

The gate must fail open (or treat GPU as available for Softserve) when the local model is resident but Plex is idle, preventing false 'gpu busy' rejections."""
import unittest
from unittest import mock

import app
import config

REAL_GATE = app.failover_available            # captured at import, before any module's live-probe patches start


class SoftservePlexGate(unittest.TestCase):
    def _gate(self, loaded, mem, util, activity):
        with mock.patch.object(app.ollama_client, "reachable", return_value=True), \
             mock.patch.object(app.ollama_client, "models_loaded", return_value=loaded), \
             mock.patch.object(app.ollama_client, "gpu_stats", return_value=(mem, util)), \
             mock.patch.object(app.plex, "activity", return_value=activity) as act:
            return REAL_GATE(), act

    def test_our_resident_model_with_plex_idle_is_available(self):
        (ok, why), _ = self._gate(["qwen3.5:9b"], 83, 0, {"transcodes": 0})
        self.assertTrue(ok, why)
        self.assertIn("qwen3.5:9b", why)

    def test_our_resident_model_during_a_transcode_is_not(self):
        (ok, why), _ = self._gate(["qwen3.5:9b"], 83, 0, {"transcodes": config.FAILOVER_MAX_TRANSCODES})
        self.assertFalse(ok)
        self.assertIn("transcode", why)

    def test_plex_unreadable_fails_closed(self):
        (ok, why), _ = self._gate(["qwen3.5:9b"], 83, 0, None)
        self.assertFalse(ok)
        self.assertIn("fail closed", why)

    def test_memory_with_nothing_of_ours_resident_is_still_busy(self):
        (ok, why), act = self._gate([], 83, 0, {"transcodes": 0})
        self.assertFalse(ok)
        self.assertIn("gpu busy", why)
        act.assert_not_called()

    def test_our_model_but_the_card_computing_is_still_busy(self):
        (ok, why), _ = self._gate(["qwen3.5:9b"], 83, config.FAILOVER_MAX_GPU_UTIL_PCT + 20, {"transcodes": 0})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
