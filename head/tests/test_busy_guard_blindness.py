"""Test that the GPU busy-guard defaults to BUSY (not free) when telemetry is missing or stale.

This covers the collision class where a second context poisons a live one:
if we cannot verify availability, assume it is occupied. However, a stale
BUSY signal must expire if it becomes implausible."""
import unittest
from unittest import mock

import comfy_client


class RenderBusyStalenessTests(unittest.TestCase):
    """render_busy() must not be disarmed by a dead reporter."""

    def _run(self, status, age):
        # ComfyUI itself unreachable: isolates the heartbeat branch, and is the real-world pairing
        # (a dead worker usually means nothing is answering on 8189 either).
        with mock.patch.object(comfy_client, "httpx") as hx:
            hx.get.side_effect = RuntimeError("comfyui unreachable")
            with mock.patch("p20_queue.gpu_status_any", return_value=(status, age)):
                return comfy_client.render_busy()

    def test_fresh_training_is_busy(self):
        self.assertTrue(self._run({"state": "training"}, 30))

    def test_briefly_stale_training_is_STILL_busy(self):
        """The original regression: a lagging reporter must not read as a free card."""
        self.assertTrue(self._run({"state": "training"}, 600))

    def test_stale_rendering_is_still_busy(self):
        self.assertTrue(self._run({"state": "rendering"}, 900))

    def test_long_stale_busy_defers_to_the_run_POINTER(self):
        """Stale busy status (>15m) defers to CURRENT_RUN.json pointer.

        Without this check, a killed worker could keep the card marked busy
        for hours (up to 24h), blocking the scheduler. We probe the pointer
        to confirm if the run is genuinely live."""
        with mock.patch("comfy_client._training_pointer_live", return_value=True):
            self.assertTrue(self._run({"state": "training"}, 2400))
            self.assertTrue(self._run({"state": "finishing"}, 5000))
        with mock.patch("comfy_client._training_pointer_live", return_value=False):
            self.assertFalse(self._run({"state": "training"}, 2400),
                             "no live run pointer -> a dead reporter must not pin the card")

    def test_pointer_probe_failure_keeps_the_card_busy(self):
        """Fail-SAFE here (unlike the chat router): freeing a card on a failed probe risks a
        collision with a real training run, which is the expensive direction to be wrong in."""
        with mock.patch("comfy_client._training_pointer_live", side_effect=RuntimeError("probe")):
            self.assertTrue(self._run({"state": "training"}, 2400))

    def test_ancient_heartbeat_does_not_block_forever(self):
        """Past 24h the file is an artefact, not a claim — honouring it would be its own outage.
        The longest real run here is ~15h."""
        self.assertFalse(self._run({"state": "training"}, 86401))

    def test_stale_free_stays_fail_soft(self):
        """A dead worker whose last word was 'free' must not pin Mothership off-limits."""
        self.assertFalse(self._run({"state": "free"}, 9999))

    def test_no_heartbeat_ever_is_fail_soft(self):
        self.assertFalse(self._run(None, None))


class GpuStatusAnyTests(unittest.TestCase):
    """gpu_status() keeps its freshness contract; gpu_status_any() exposes the raw claim."""

    def test_display_path_still_hides_stale(self):
        import p20_queue
        with mock.patch.object(p20_queue, "gpu_status_any",
                               return_value=({"state": "training"}, 900)):
            self.assertIsNone(p20_queue.gpu_status(max_age_secs=360))

    def test_display_path_returns_fresh(self):
        import p20_queue
        st = {"state": "training"}
        with mock.patch.object(p20_queue, "gpu_status_any", return_value=(st, 10)):
            self.assertEqual(p20_queue.gpu_status(max_age_secs=360), st)

    def test_missing_file_is_none(self):
        import p20_queue
        with mock.patch.object(p20_queue, "gpu_status_any", return_value=(None, None)):
            self.assertIsNone(p20_queue.gpu_status())


class TrainingPointerTests(unittest.TestCase):
    """The stale-heartbeat cross-check asks the run the factory DECLARES, not a file that no longer
    exists (the old pointer read State/current-run.json plus an unreachable Windows path, so it answered
    'no run' for every stale heartbeat — the false-free direction the guard exists to prevent)."""

    def test_declared_training_run_is_live(self):
        with mock.patch("scheduler.stated_run", return_value={"host": "mothership", "name": "r", "state": "training"}):
            self.assertTrue(comfy_client._training_pointer_live())

    def test_declared_idle_or_paused_is_not_live(self):
        with mock.patch("scheduler.stated_run", return_value={"host": "mothership", "name": "", "state": "idle"}):
            self.assertFalse(comfy_client._training_pointer_live())
        with mock.patch("scheduler.stated_run", return_value=None):
            self.assertFalse(comfy_client._training_pointer_live())

    def test_unreadable_state_fails_safe_to_live(self):
        with mock.patch("scheduler.stated_run", side_effect=RuntimeError("state unreadable")):
            self.assertTrue(comfy_client._training_pointer_live())


if __name__ == "__main__":
    unittest.main()
