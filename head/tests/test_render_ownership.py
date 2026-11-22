"""Queued HAI renders own their card without exempting unnamed or stale GPU work."""
import unittest
from unittest import mock
import p20_queue
import scheduler as S


class RenderOwnership(unittest.TestCase):
    def setUp(self):
        self.status = {"host": S.LEAD_BOX, "state": "rendering",
                       "detail": "rendering job 82924ff33f5f"}
        self.job = {"id": "82924ff33f5f", "status": "pending", "initiator": "hai"}
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(p20_queue, "gpu_status", side_effect=lambda **kw: self.status).start()
        mock.patch.object(p20_queue, "get", side_effect=lambda jid: self.job).start()

    def test_real_render_reserves_its_card_without_trainer_state(self):
        with mock.patch.object(S, "_run_records", return_value={}):
            busy, why = S.training_busy(S.LEAD_BOX)
        self.assertTrue(busy)
        self.assertIn(self.job["id"], why)

    def test_render_explains_load_without_ollama(self):
        with mock.patch.object(S, "_run_records", return_value={}):
            self.assertIn("render order", S._accounted_by(S.LEAD_BOX))

    def test_stale_worker_does_not_account_for_load(self):
        self.status = None
        self.assertEqual(S.render_busy(S.LEAD_BOX), (False, ""))

    def test_wrong_host_does_not_own_this_card(self):
        self.assertFalse(S.render_busy(S.BRAIN)[0])

    def test_unknown_and_external_render_do_not_own_the_card(self):
        self.job = {}
        self.assertFalse(S.render_busy(S.LEAD_BOX)[0])
        self.status["detail"] = "rendering job foundry-preview"
        self.assertFalse(S.render_busy(S.LEAD_BOX)[0])

    def test_finished_cancelled_or_failed_order_does_not_own_the_card(self):
        for status in ("done", "failed", "cancelled"):
            self.job["status"] = status
            self.assertFalse(S.render_busy(S.LEAD_BOX)[0])

    def test_foreign_claim_or_nonrender_order_does_not_own_the_card(self):
        self.job["claimed_by"] = S.BRAIN
        self.assertFalse(S.render_busy(S.LEAD_BOX)[0])
        self.job.pop("claimed_by")
        self.job["kind"] = "factory_order"
        self.assertFalse(S.render_busy(S.LEAD_BOX)[0])

    def test_model_calls_wait_for_the_image_order(self):
        with mock.patch.object(S, "claim_for_url", return_value=None):
            owner = S.occupied_for(S.CARDS[S.LEAD_BOX]["url"], "qwen3.8:27b")
        self.assertIn(self.job["id"], owner["holder"])

    def test_factory_cannot_start_gpu_work_over_a_user_image(self):
        with mock.patch.object(S, "gpu_health", return_value=(True, "healthy")):
            why = S.order_host_unavailable(S.LEAD_BOX, "render_small")
        self.assertIn(self.job["id"], why)

    def test_cpu_work_is_not_blocked_by_an_image(self):
        self.assertEqual(S.order_host_unavailable(S.LEAD_BOX, "cpu"), "")
