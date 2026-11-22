"""Ensure the homepage training card reports active status during GPU-bound runs,
preventing false 'idle' states when render workers are absent (e.g., deferred previews).

The card prioritizes render worker heartbeats for rich detail but falls back to
direct run state declarations (`POST /fleet/run-state`) when no worker is present."""
import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
import scheduler


class TrainingCardTests(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app_module.app)
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "fleet-run-state.json")
        self.p = mock.patch.object(app_module, "FLEET_RUN_STATE", self.path)
        self.p.start()
        self.p2 = mock.patch.object(app_module, "RESCUE_STATE",
                                    os.path.join(self.tmp, "rescue-state.json"))
        self.p2.start()
        scheduler.release(scheduler.LEAD_BOX, "gpu")

    def tearDown(self):
        self.p.stop()
        self.p2.stop()
        scheduler.release(scheduler.LEAD_BOX, "gpu")

    def _declare(self, **kw):
        body = {"host": scheduler.LEAD_BOX, "name": "concept_flux2_v2",
                "state": "training", "total_steps": 1000, "step": 375}
        body.update(kw)
        return self.client.post("/fleet/run-state", json=body)

    def _card(self):
        return self.client.get("/p20/training").json()

    def test_a_declared_run_shows_on_the_card(self):
        self._declare(eta="ETA: finish ~03:10 (625 steps @ 36.2s/it)")
        with mock.patch("p20_queue.gpu_status", return_value={}):
            c = self._card()
        self.assertEqual(c["state"], "training")
        self.assertEqual(c["job"], "concept_flux2_v2")
        self.assertEqual(c["progress"], "375 / 1000")
        self.assertEqual(c["pct"], 37.5)

    def test_the_regression_idle_during_a_live_run(self):
        """The exact failure: worker heartbeat empty, run live, card said idle."""
        self._declare()
        with mock.patch("p20_queue.gpu_status", return_value={}):
            self.assertNotEqual(self._card()["state"], "idle")

    def test_run_declaration_outranks_worker_card_when_both_live(self):
        """Run declaration outranks worker heartbeat; worker state moves to /p20/factory/<host>.worker."""
        self._declare()
        with mock.patch("p20_queue.gpu_status",
                        return_value={"state": "rendering", "job": "ladder", "detail": "ck500"}):
            c = self._card()
            f = self.client.get("/p20/factory/mothership").json()
        self.assertEqual(c["state"], "training")
        self.assertEqual(c["job"], "concept_flux2_v2")
        self.assertEqual(f["worker"], "rendering")

    def test_paused_run_reads_as_paused_not_idle(self):
        """STOP_NOW / crash / rescue recycle: the card must distinguish 'parked' from 'nothing here'."""
        self._declare(state="paused")
        with mock.patch("p20_queue.gpu_status", return_value={}):
            c = self._card()
        self.assertEqual(c["state"], "training-pause")
        self.assertEqual(c["job"], "concept_flux2_v2")

    def test_finished_run_releases_the_card_to_idle(self):
        self._declare(state="done")
        with mock.patch("p20_queue.gpu_status", return_value={}):
            self.assertEqual(self._card()["state"], "idle")

    def test_setup_phase_is_visible_not_a_frozen_counter(self):
        self._declare(step=0, phase="caching text embeddings")
        with mock.patch("p20_queue.gpu_status", return_value={}):
            c = self._card()
        self.assertEqual(c["state"], "warming")
        self.assertEqual(c["detail"], "caching text embeddings")
        self.assertIn("setup", c["progress"])
        self.assertEqual(c["job"], "concept_flux2_v2")

    def test_real_steps_supersede_the_setup_phase(self):
        """Once stepping, the phase is noise -- the step counter is the real signal."""
        self._declare(step=375, phase="")
        with mock.patch("p20_queue.gpu_status", return_value={}):
            c = self._card()
        self.assertEqual(c["state"], "training")
        self.assertEqual(c["progress"], "375 / 1000")

    def test_live_preview_shows_as_rendering_not_frozen_training(self):
        self._declare(state="rendering", step=250, phase="rendering preview at step 250")
        with mock.patch("p20_queue.gpu_status", return_value={}):
            c = self._card()
        self.assertEqual(c["state"], "rendering")
        self.assertIn("250", c["detail"])
        self.assertEqual(c["progress"], "250 / 1000")

    def test_rendering_KEEPS_the_card_claimed(self):
        """The defect this pairs with: publish_current_run only ran in TRAIN_WATCH, so a claim with a
        120 s TTL lapsed during a 20-minute render and the scheduler advertised a busy card as free.
        A render owns the card exactly as much as training does."""
        import scheduler as sch
        self._declare(state="rendering", step=250, ttl=2400)
        c = sch.active_claim(sch.LEAD_BOX, "gpu")
        self.assertIsNotNone(c, "a rendering card must stay claimed")
        self.assertIn("rendering", c["note"])
        busy, _why = sch.gpu_state(sch.LEAD_BOX)
        self.assertTrue(busy)

    def _rescue(self, **kw):
        body = {"host": "mothership", "alive": True, "pid": 123, "last": "prophylactic burst",
                "last_tflops": 76.3, "stalls_today": 0, "rescues_today": 0}
        body.update(kw)
        return self.client.post("/fleet/rescue-state", json=body)

    def test_daemon_reports_itself(self):
        self._rescue()
        c = self.client.get("/p20/rescue").json()
        self.assertEqual(c["state"], "watching")
        self.assertIn("prophylactic", c["detail"])
        self.assertEqual(c["tflops"], 76.3)

    def test_silence_reads_as_DOWN(self):
        """Test that a process which stopped reporting (staleness) reads as DOWN.

        The verdict is derived from measured silence, not inferred state.
        It must NOT assert "unprotected" or imply the system is unprotected,
        since /p20/rescue lacks card context to determine actual running status."""
        import datetime as _dt
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
        with open(app_module.RESCUE_STATE, "w", encoding="utf-8") as f:
            json.dump({"host": "mothership", "alive": True, "ts": old, "last": "all good"}, f)
        c = self.client.get("/p20/rescue").json()
        self.assertEqual(c["state"], "DOWN")
        self.assertIn("no report for", c["detail"])
        self.assertNotIn("UNPROTECTED", c["detail"])

    def test_daemon_silence_during_a_live_run_says_UNPROTECTED(self):
        """The scary sentence is EARNED when — and only when — a run exists to protect. The factory
        view knows whether a run is live and passes it to the verdict; daemon silence under a live
        training run must still say so at full volume."""
        import datetime as _dt
        self._declare()
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
        # _rescue_all() reads per-host files (rescue-state-<host>.json), not the aggregate path.
        per_host = app_module.RESCUE_STATE.replace("rescue-state.json", "rescue-state-mothership.json")
        with open(per_host, "w", encoding="utf-8") as f:
            json.dump({"host": "mothership", "alive": True, "ts": old, "last": "all good"}, f)
        with mock.patch("p20_queue.gpu_status", return_value={}):
            f2 = self.client.get("/p20/factory/mothership").json()
        self.assertEqual(f2["health"], "DOWN")
        self.assertIn("UNPROTECTED", f2["health_detail"])

    def test_daemon_silence_during_a_paused_run_does_not_shout(self):
        """A PARKED run names its order but is not stepping — the stall daemon has nothing to
        protect until it resumes, so daemon silence must not claim a run is at risk."""
        import datetime as _dt
        self._declare(state="paused")
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
        per_host = app_module.RESCUE_STATE.replace("rescue-state.json", "rescue-state-mothership.json")
        with open(per_host, "w", encoding="utf-8") as f:
            json.dump({"host": "mothership", "alive": True, "ts": old, "last": "all good"}, f)
        with mock.patch("p20_queue.gpu_status", return_value={}):
            f2 = self.client.get("/p20/factory/mothership").json()
        self.assertEqual(f2["health"], "DOWN")
        self.assertNotIn("UNPROTECTED", f2["health_detail"])

    def test_self_disabled_is_its_own_state(self):
        """The daemon disables itself after repeated failed rescues; that is not the same as DOWN."""
        self._rescue(disabled=True, last="3 FAST-burst rescues failed -> DISABLING")
        c = self.client.get("/p20/rescue").json()
        self.assertEqual(c["state"], "DISABLED")
        self.assertIn("DISABLING", c["detail"])

    def test_no_declaration_at_all_is_idle(self):
        with mock.patch("p20_queue.gpu_status", return_value={}):
            self.assertEqual(self._card()["state"], "idle")

    def test_declaration_survives_a_restart(self):
        """Persisted to disk on purpose: an orchestrator restart mid-run must not blank the card."""
        self._declare()
        self.assertTrue(os.path.exists(self.path))
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[scheduler.LEAD_BOX]["name"], "concept_flux2_v2")

    def test_a_captioning_job_is_visible_not_idle(self):
        """A captioning job must report state 'captioning', not 'idle'."""
        try:
            self._declare(name="recaption_v5_27b", state="captioning", step=41, total_steps=163,
                          phase="captioning train-v5", eta="ETA: finish ~04:29 (122 left @ 16.4s/img)")
            with mock.patch("p20_queue.gpu_status", return_value={}):
                c = self._card()
            self.assertEqual(c["state"], "captioning")
            self.assertEqual(c["job"], "recaption_v5_27b")
            self.assertEqual(c["progress"], "41 / 163")
            self.assertEqual(c["rate"], "16.4 s/img", "a per-image rate must survive to the card")
            self.assertEqual(c["finish"], "04:29")
        finally:
            self._declare(name="recaption_v5_27b", state="done")


class FactoryTileRateAndEta(unittest.TestCase):
    """Parser must handle 's/unit' and '8m50s' formats; rigby lane publishes no rate."""

    setUp, tearDown, _declare = TrainingCardTests.setUp, TrainingCardTests.tearDown, TrainingCardTests._declare

    def _tile(self):
        with mock.patch("p20_queue.gpu_status", return_value={}):
            return self.client.get("/p20/factory/%s" % scheduler.LEAD_BOX).json()

    def test_any_per_unit_rate_and_the_publishers_own_eta_reach_the_tile(self):
        try:
            self._declare(name="hardbench-fleet-vlm2x-9b-ss", state="compute", step=25, total_steps=72,
                          phase="compute(gpu): infer_ollama.py · 11.3 s/unit", eta="8m50s")
            t = self._tile()
            self.assertEqual(t["rate"], "11.3 s/unit")
            self.assertEqual(t["remaining"], "~8m50s left")
        finally:
            self._declare(name="hardbench-fleet-vlm2x-9b-ss", state="done")

    def test_a_rate_nobody_published_is_derived_from_observed_progress(self):
        import datetime
        app_module._PROGRESS_SEEN.clear()
        try:
            self._declare(name="rigby-run", state="compute", step=1, total_steps=27, phase="llm-rigby")
            self.assertEqual(self._tile()["rate"], "", "one observation is not a rate")
            order, step, when = app_module._PROGRESS_SEEN[scheduler.LEAD_BOX.lower()]
            app_module._PROGRESS_SEEN[scheduler.LEAD_BOX.lower()] = (order, step,
                                                                       when - datetime.timedelta(seconds=60))
            self._declare(name="rigby-run", state="compute", step=3, total_steps=27, phase="llm-rigby")
            t = self._tile()
            self.assertEqual(t["rate"].split()[1], "s/item")
            self.assertAlmostEqual(float(t["rate"].split()[0]), 30.0, delta=2.0)
            self.assertEqual(t["remaining"], "~12m left")
        finally:
            self._declare(name="rigby-run", state="done")
            app_module._PROGRESS_SEEN.clear()

    def test_a_new_order_starts_its_own_observation(self):
        app_module._PROGRESS_SEEN.clear()
        try:
            self._declare(name="old-run", state="compute", step=5, total_steps=10, phase="llm-rigby")
            self._tile()
            self._declare(name="new-run", state="compute", step=6, total_steps=10, phase="llm-rigby")
            self.assertEqual(self._tile()["rate"], "", "a different order's history is not this order's rate")
        finally:
            self._declare(name="new-run", state="done")
            app_module._PROGRESS_SEEN.clear()


if __name__ == "__main__":
    unittest.main()
