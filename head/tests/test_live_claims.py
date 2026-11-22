"""Live HAI claims (`live` type) are visible to placement but do not trigger GPU occupation (`occupied_for`).

This ensures:
1. Live jobs block background models from loading the same box (via Spock/claim checks).
2. Live jobs do NOT block user chats or their own models from sharing the box (unlike `gpu` claims)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import config
import ollama_client
import scheduler

from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes(skip=("mark_live",))      # the live claim is this module's subject (claims go to a temp dir)


def tearDownModule():
    stop_no_live_probes()


class _ClaimDir(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        p = mock.patch.object(scheduler, "CLAIM_DIR", self.d)
        p.start()
        self.addCleanup(p.stop)


class AJobMarksItsBoxLive(_ClaimDir):
    def test_a_job_chat_call_claims_the_box_with_its_model_for_the_default_ttl(self):
        with ollama_client.call_context(job=True, user="Alex"):
            with ollama_client.track(config.HEAVY_HOST, "qwen3.8:27b", kind="chat"):
                pass
        live = scheduler.active_claim(scheduler.LEAD_BOX, scheduler.LIVE)
        self.assertEqual((live["holder"], live["note"]), ("hai-job:Alex", "qwen3.8:27b"))
        self.assertIsNone(scheduler.active_claim(scheduler.LEAD_BOX, "gpu"), "live is never a gpu claim")
        with ollama_client.call_context(job=True, user="Alex"):
            with ollama_client.track(config.BRAIN_HOST, "nomic-embed-text", kind="embed"):
                pass
        self.assertIsNone(scheduler.active_claim(scheduler.BRAIN, scheduler.LIVE), "only CHAT calls mark")

    def test_a_plain_chat_marks_its_box_live_for_the_boxes_own_keep_alive(self):
        # A plain chat marks the box live for keep_alive duration: the model remains resident (unlike job calls).
        import datetime
        with mock.patch.dict(config.KEEP_ALIVE_OVERRIDES, {config.BRAIN_HOST: "5m"}), \
                ollama_client.call_context(job=False, user="Avery"):
            with ollama_client.track(config.BRAIN_HOST, "qwen3.5:9b", kind="chat"):
                pass
        live = scheduler.active_claim(scheduler.BRAIN, scheduler.LIVE)
        self.assertEqual((live["holder"], live["note"]), ("hai-chat:Avery", "qwen3.5:9b"))
        left = (datetime.datetime.fromisoformat(live["expires"]) - datetime.datetime.now(datetime.timezone.utc))
        self.assertTrue(240 < left.total_seconds() <= 300, left)

    def test_keep_alive_zero_marks_nothing_and_forever_falls_back_to_the_default(self):
        self.assertEqual([ollama_client._residency_s(h) for h in ()], [])
        with mock.patch.object(config, "KEEP_ALIVE_OVERRIDES", {"a": "0", "b": "-1", "c": "2m", "d": "90s", "e": "x"}):
            self.assertEqual([ollama_client._residency_s(h) for h in "abcde"], [0.0, None, 120.0, 90.0, None])
        with mock.patch.dict(config.KEEP_ALIVE_OVERRIDES, {config.BRAIN_HOST: "0"}), \
                ollama_client.call_context(job=False, user="Avery"):
            with ollama_client.track(config.BRAIN_HOST, "qwen3.5:9b", kind="chat"):
                pass
        self.assertIsNone(scheduler.active_claim(scheduler.BRAIN, scheduler.LIVE), "nothing stays resident")

    def test_a_claim_that_cannot_be_written_never_fails_the_call(self):
        with mock.patch.object(scheduler, "claim", side_effect=OSError("read-only")):
            with ollama_client.call_context(job=True):
                with ollama_client.track(config.HEAVY_HOST, "qwen3.8:27b", kind="chat"):
                    ran = True
        self.assertTrue(ran)


class OccupiedFor(_ClaimDir):
    def test_a_live_job_refuses_a_second_model_and_shares_its_own(self):
        scheduler.claim(scheduler.LEAD_BOX, scheduler.LIVE, "hai-job:Alex", note="qwen3.8:27b")
        self.assertIsNone(scheduler.occupied_for(config.HEAVY_HOST, "qwen3.8:27b"), "the job's own model shares")
        self.assertEqual(scheduler.occupied_for(config.HEAVY_HOST, config.LEAD_BOX_BRAIN_MODEL)["holder"],
                         "hai-job:Alex", "a second model would evict the job's lead")
        self.assertIsNotNone(scheduler.occupied_for(config.HEAVY_HOST), "model unknown = refuse")
        self.assertIsNone(scheduler.occupied_for(config.BRAIN_HOST, config.BRAIN_MODEL), "another box is free")

    def test_a_gpu_claim_refuses_every_model(self):
        scheduler.claim(scheduler.LEAD_BOX, "gpu", "order-x")
        self.assertEqual(scheduler.occupied_for(config.HEAVY_HOST, "qwen3.8:27b")["holder"], "order-x")


class HousekeepingYieldsToALiveJob(_ClaimDir):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(scheduler, "gpu_state", return_value=(False, "free")),
                  mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
                  mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
                  mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
                  mock.patch.object(scheduler, "record_placement")):
            p.start()
            self.addCleanup(p.stop)

    def hosts(self, kind):
        return [h for h, _s, _w in scheduler.place(kind)]

    def test_review_skips_a_live_box_and_takes_it_when_the_job_is_gone(self):
        self.assertIn(scheduler.LEAD_BOX, self.hosts("review"), "positive control: the review's box when free")
        scheduler.claim(scheduler.LEAD_BOX, scheduler.LIVE, "hai-job:Alex", note="qwen3.8:27b")
        self.assertNotIn(scheduler.LEAD_BOX, self.hosts("review"))
        scheduler.release(scheduler.LEAD_BOX, scheduler.LIVE)
        self.assertIn(scheduler.LEAD_BOX, self.hosts("review"))

    def test_review_is_not_confined_to_the_big_card(self):
        """Review is any GPU compute order; the menu row picks the box."""
        hosts = self.hosts("review")
        self.assertIn(scheduler.BRAIN, hosts)
        self.assertEqual(scheduler.KINDS["review"]["vram_gb"], scheduler.KINDS["render_small"]["vram_gb"])

    def test_only_kinds_that_yield_are_touched(self):
        scheduler.claim(scheduler.LEAD_BOX, scheduler.LIVE, "hai-job:Alex", note="qwen3.8:27b")
        yielding = sorted(k for k, v in scheduler.KINDS.items() if v.get("yields_to_live"))
        self.assertEqual(yielding, ["advisor", "medical_review", "page", "review", "test"])
        self.assertIn(scheduler.LEAD_BOX, self.hosts("chat27b"), "the user's own chats keep the lead")


if __name__ == "__main__":
    unittest.main()
