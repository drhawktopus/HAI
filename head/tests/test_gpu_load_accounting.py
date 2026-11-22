"""GPU load samples must map to a known owner (foundry run-state, scheduler claim, or in-flight Kirk call);
unexplained busy load triggers an UNACCOUNTED episode."""
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
import ollama_client
import scheduler

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


HOST = scheduler.LEAD_BOX


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_dir = scheduler.GPU_LOAD_DIR
        scheduler.GPU_LOAD_DIR = os.path.join(self._tmp, "gpu-load")
        self._orig_claims = scheduler.CLAIM_DIR
        scheduler.CLAIM_DIR = os.path.join(self._tmp, "claims")
        self._orig_stated = scheduler.STATED_RUN
        scheduler.STATED_RUN = os.path.join(self._tmp, "fleet-run-state.json")
        with ollama_client._INFLIGHT_LOCK:
            ollama_client._INFLIGHT.clear()
            ollama_client._LAST_DONE.clear()

    def tearDown(self):
        scheduler.GPU_LOAD_DIR = self._orig_dir
        scheduler.CLAIM_DIR = self._orig_claims
        scheduler.STATED_RUN = self._orig_stated
        with ollama_client._INFLIGHT_LOCK:
            ollama_client._INFLIGHT.clear()
            ollama_client._LAST_DONE.clear()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def push(self, util, **kw):
        return scheduler.report_gpu_load(HOST, util, kw.pop("power_w", 100.0), kw.pop("vram_used_mb", 1000.0),
                                         kw.pop("ollama_loaded", ["qwen3.6:27b"]), kw.pop("source", "test"), **kw)


class AccountingTests(_Base):

    def test_idle_card_is_idle_and_a_stale_sample_is_no_sample_never_idle(self):
        self.assertEqual(self.push(2.0)["accounting"]["state"], "idle")
        rec = scheduler.report_gpu_load(HOST, None, source="nvidia-smi timed out", stale=True)
        self.assertEqual(rec["accounting"]["state"], "no-sample")
        self.assertIn("no sample", scheduler.gpu_accounting_line(HOST))

    def test_busy_with_a_foundry_run_is_accounted(self):
        with mock.patch.object(scheduler, "training_busy", return_value=(True, "training run 'v1' owns this card")):
            acc = self.push(80.0)["accounting"]
        self.assertEqual(acc["state"], "accounted")
        self.assertIn("foundry order", acc["by"])

    def test_plex_transcoding_accounts_for_softserve_load_and_only_softserve(self):
        def push(host):
            return scheduler.report_gpu_load(host, 37.0, 75.0, 400.0, [], "test")
        with mock.patch.object(scheduler, "plex_transcoding", return_value=(False, 1, "1 Plex transcode(s)")):
            for _ in range(3):
                acc = push(scheduler.FAILOVER)["accounting"]
            self.assertEqual(acc["state"], "accounted")
            self.assertIn("Plex", acc["by"])
            for _ in range(2):
                other = push(HOST)["accounting"]
            self.assertEqual(other["state"], "unaccounted", "Plex explains Softserve's card, not another box's")
        with mock.patch.object(scheduler, "plex_transcoding", return_value=(False, 0, "0 Plex transcode(s)")):
            push(scheduler.FAILOVER)
            acc = push(scheduler.FAILOVER)["accounting"]
        self.assertEqual(acc["state"], "unaccounted", "no transcode: Softserve load with no owner still alarms")
        with mock.patch.object(scheduler, "plex_transcoding", return_value=(None, None, "tautulli unreachable")):
            acc = push(scheduler.FAILOVER)["accounting"]
        self.assertEqual(acc["state"], "unaccounted", "could not ask Plex is never 'Plex owns it'")

    def test_busy_with_a_scheduler_claim_is_accounted(self):
        scheduler.claim(HOST, "gpu", "render-worker", ttl=120, note="t")
        try:
            acc = self.push(80.0)["accounting"]
        finally:
            scheduler.release(HOST, "gpu")
        self.assertEqual(acc["state"], "accounted")
        self.assertIn("claim: render-worker", acc["by"])

    def test_busy_with_an_inflight_kirk_call_is_accounted_and_grace_after_it(self):
        url = scheduler.CARDS[HOST]["url"]
        with ollama_client.track(url, "qwen3.6:27b", purpose="eval_step coding-01 r1"):
            acc = self.push(80.0)["accounting"]
        self.assertEqual(acc["state"], "accounted")
        self.assertIn("Kirk call: eval_step coding-01", acc["by"])
        acc2 = self.push(80.0)["accounting"]          # the call just ended: grace window
        self.assertEqual(acc2["state"], "accounted")
        self.assertIn("ended", acc2["by"])

    def test_unaccounted_load_opens_an_episode_only_on_the_second_push_and_dms_once(self):
        a1 = self.push(85.0)["accounting"]
        self.assertEqual((a1["state"], a1["streak"], a1["episode"], a1["opened"]), ("suspect", 1, False, False))
        self.assertTrue(scheduler.gpu_accounting_line(HOST).startswith("suspect"))
        a2 = self.push(85.0)["accounting"]
        self.assertEqual((a2["state"], a2["streak"], a2["episode"], a2["opened"]), ("unaccounted", 2, True, True))
        a3 = self.push(85.0)["accounting"]
        self.assertEqual((a3["state"], a3["episode"], a3["opened"]), ("unaccounted", True, False), "one DM per episode")
        self.assertTrue(scheduler.gpu_accounting_line(HOST).startswith("UNACCOUNTED"))
        a4 = self.push(1.0)["accounting"]
        self.assertEqual((a4["state"], a4["episode"]), ("idle", False))
        self.assertTrue(a4.get("cleared"))

    def test_deck_line_never_invents(self):
        self.assertEqual(scheduler.gpu_accounting_line(HOST), "no load report")
        self.push(3.0)
        self.assertTrue(scheduler.gpu_accounting_line(HOST).startswith("idle"))
        with mock.patch.object(scheduler, "_report_age_s", return_value=scheduler.GPU_LOAD_FRESH_S + 60):
            self.assertIn("stale", scheduler.gpu_accounting_line(HOST))


class RigbyReportsItsLoad(_Base):
    """rigby's card pushes the same load report as the GPU boxes, so a rigby window being opened is a status Kirk's
    clock can see."""

    def fleet(self, busy, age_s=5, line=0):
        import datetime
        import json
        ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_s)).isoformat()
        with open(scheduler.STATED_RUN, "w", encoding="utf-8") as f:
            json.dump({scheduler.FELLOWSHIP_HOST: {"host": scheduler.FELLOWSHIP_HOST, "name": "", "state": "idle",
                                                   "ttl": 180, "ts": ts, "updated": ts,
                                                   "capacity": {"nodes": 8, "free_nodes": 8, "rigby_busy": busy,
                                                                "rigby_line": line}}}, f)

    def push(self, util, **kw):
        return scheduler.report_gpu_load(scheduler.TOLKIEN_BOX, util, 90.0, 4500.0, [], "nvidia-smi")

    def test_rigbys_lane_busy_in_tolkiens_report_accounts_for_its_load(self):
        self.fleet(busy=True, line=1)
        for _ in range(3):
            acc = self.push(95.0)["accounting"]
        self.assertEqual(acc["state"], "accounted")
        self.assertIn("rigby's lane is busy (1 in line)", acc["by"])

    def test_a_stale_report_explains_nothing_and_unowned_load_still_alarms(self):
        self.fleet(busy=True, age_s=900)
        self.push(95.0)
        self.assertEqual(self.push(95.0)["accounting"]["state"], "unaccounted", "a stale free-spot report owns nothing")
        self.fleet(busy=False)
        self.push(95.0)
        self.assertEqual(self.push(95.0)["accounting"]["state"], "unaccounted", "rigby free and its card busy: alarm")

    def test_a_kirk_call_to_rigbys_window_accounts_and_an_empty_address_matches_nothing(self):
        import config
        with ollama_client.track(config.TOLKIEN_WINDOW_URL, "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", purpose="chat"):
            acc = self.push(80.0)["accounting"]
        self.assertEqual(acc["state"], "accounted")
        self.assertIn("Kirk call", acc["by"])
        with ollama_client.track(scheduler.CARDS[HOST]["url"], "m", purpose="another box"):
            self.assertEqual(scheduler._accounted_by("nowhere"), "", "a box with no address is explained by no call")

    def test_the_endpoint_takes_rigbys_report_and_the_window_clock_reads_it(self):
        import chat_window
        client = TestClient(app_module.app)
        r = client.post("/fleet/gpu-load", json={"host": "rigby", "util_pct": 1.0, "power_w": 9.0,
                                                 "vram_used_mb": 0.0, "ollama_loaded": [], "source": "nvidia-smi"}).json()
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(chat_window._box_reporting(chat_window.TOLKIEN_BOX), "a fresh rigby report vouches for an opening window")


class EndpointTests(_Base):

    def setUp(self):
        super().setUp()
        self.client = TestClient(app_module.app)

    def test_endpoint_stores_reports_and_dms_on_open_only(self):
        sent = []
        with mock.patch.object(app_module, "_watch_dm", side_effect=lambda t: sent.append(t)):
            body = {"host": HOST, "util_pct": 90.0, "power_w": 300.0, "vram_used_mb": 20000.0,
                    "ollama_loaded": ["qwen3.6:27b"], "source": "test"}
            r1 = self.client.post("/fleet/gpu-load", json=body).json()
            r2 = self.client.post("/fleet/gpu-load", json=body).json()
            r3 = self.client.post("/fleet/gpu-load", json=body).json()
        self.assertTrue(r1["ok"] and r2["ok"] and r3["ok"])
        self.assertEqual([r["accounting"]["state"] for r in (r1, r2, r3)], ["suspect", "unaccounted", "unaccounted"])
        self.assertEqual(len(sent), 1, "exactly one DM per episode")
        self.assertIn("UNACCOUNTED GPU LOAD on %s" % HOST, sent[0])
        self.assertIn("qwen3.6:27b", sent[0])
        self.assertEqual(self.client.post("/fleet/gpu-load", json={"host": "nope", "util_pct": 1}).json().get("ok"), False)

    def test_factory_tile_carries_the_accounting_line(self):
        self.push(3.0)
        d = self.client.get("/p20/factory/%s" % HOST).json()
        self.assertTrue(str(d.get("accounting", "")).startswith("idle"), d.get("accounting"))
