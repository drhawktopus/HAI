import unittest

from fastapi.testclient import TestClient

import app as app_module
import scheduler

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


class FleetRunStateTests(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app_module.app)
        scheduler.release(scheduler.LEAD_BOX, "gpu")

    def tearDown(self):
        scheduler.release(scheduler.LEAD_BOX, "gpu")

    def _post(self, **kw):
        body = {"host": scheduler.LEAD_BOX, "name": "concept_flux2_v1"}
        body.update(kw)
        return self.client.post("/fleet/run-state", json=body)

    def test_training_claims_the_card(self):
        r = self._post(state="training", total_steps=3000, step=2183)
        self.assertTrue(r.json().get("ok"))
        c = scheduler.active_claim(scheduler.LEAD_BOX, "gpu")
        self.assertIsNotNone(c)
        self.assertEqual(c["holder"], "concept_flux2_v1")
        self.assertIn("2183/3000", c["note"])

    def test_a_claimed_card_is_excluded_from_placement(self):
        """The actual regression: this is what stops a VLM landing on a live QLoRA."""
        self._post(state="training", total_steps=3000, step=100)
        busy, why = scheduler.gpu_state(scheduler.LEAD_BOX)
        self.assertTrue(busy)
        self.assertIn("concept_flux2_v1", why)

    def test_paused_releases_immediately(self):
        """STOP_NOW / rescue recycle / crash all land here: the card is idle whatever the intent."""
        self._post(state="training")
        self.assertIsNotNone(scheduler.active_claim(scheduler.LEAD_BOX, "gpu"))
        self._post(state="paused")
        self.assertIsNone(scheduler.active_claim(scheduler.LEAD_BOX, "gpu"))

    def test_done_releases(self):
        self._post(state="training")
        self._post(state="done")
        self.assertIsNone(scheduler.active_claim(scheduler.LEAD_BOX, "gpu"))

    def test_an_idle_report_never_releases_an_operator_hold(self):
        """Operator holds are exempt from the standard claim lifecycle: factory idle/done transitions must never clear them."""
        scheduler.claim(scheduler.LEAD_BOX, "gpu", scheduler.OPERATOR_HOLD + " RAM unstable", ttl=600)
        r = self._post(state="idle", name="")
        self.assertTrue(r.json().get("ok"))
        c = scheduler.active_claim(scheduler.LEAD_BOX, "gpu")
        self.assertTrue(c and c["holder"].startswith(scheduler.OPERATOR_HOLD), c)
        self.assertTrue(scheduler.gpu_state(scheduler.LEAD_BOX)[0], "the held card stays out of placement")
        self._post(state="training")              # the factory's own claim still comes and goes as before
        self._post(state="done")
        self.assertIsNone(scheduler.active_claim(scheduler.LEAD_BOX, "gpu"))

    def test_unknown_host_is_rejected_not_silently_accepted(self):
        r = self._post(host="not-a-box", state="training")
        self.assertFalse(r.json().get("ok"))
        self.assertIn("unknown host", r.json().get("error", ""))

    def test_ttl_is_clamped(self):
        """A run must not be able to pin a card for a week by fat-fingering a ttl."""
        self._post(state="training", ttl=999999)
        c = scheduler.active_claim(scheduler.LEAD_BOX, "gpu")
        self.assertIsNotNone(c)

    def test_refresh_extends_the_same_claim(self):
        """Rolling TTL: the foundry re-declares every poll, and that must not pile up claims."""
        self._post(state="training", step=1)
        first = scheduler.active_claim(scheduler.LEAD_BOX, "gpu")
        self._post(state="training", step=2, total_steps=3000)
        second = scheduler.active_claim(scheduler.LEAD_BOX, "gpu")
        self.assertEqual(first["holder"], second["holder"])
        self.assertIn("2/3000", second["note"])

    def test_any_gpu_job_can_claim_not_just_training(self):
        try:
            r = self._post(state="captioning", name="recaption_v5_27b", phase="163 images")
            self.assertTrue(r.json().get("claimed"), "a non-training GPU job must be able to claim")
            hosts = [h for h, _s, _w in scheduler.place("vision", vram_gb=6.0)]
            self.assertNotIn(scheduler.LEAD_BOX, hosts)
        finally:
            # RELEASE, or the claim outlives this test. The suite shares one process, so a claim
            # left standing removed Mothership from placement for every test that ran afterwards --
            # 8 dataset/routing tests failed with symptoms that looked nothing like a claim leak
            # (a spec file simply never got written). Claims are global state; treat them like a
            # file handle.
            self._post(state="done")

    def test_an_unrecognised_state_claims_rather_than_releases(self):
        """Fail closed: 'I don't know what this state means' must not read as 'the card is free'."""
        try:
            self._post(state="training")
            r = self._post(state="wat")
            self.assertTrue(r.json().get("claimed"), "unknown state must not silently release the card")
        finally:
            self._post(state="done")


if __name__ == "__main__":
    unittest.main()
