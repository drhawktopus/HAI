"""The desk->factory control channel (p20_queue controls + /p20/controls) — 10d delivery half.

Pins the properties that make the channel safe to bolt onto a live store: controls live in a
SUBDIRECTORY the flat jobs listing structurally cannot see (a pending control served to the
render worker as a job would be the second-producer-clobbers class one level up) · single-claim
with the same not-mine-move-on contract as order claims · host-targeted (another factory's
controls are invisible and unclaimable) · worker-token auth on both endpoints, fail-closed.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import p20_queue


class ControlStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="p20q-test-")
        self._patches = [
            mock.patch.object(p20_queue, "QUEUE_DIR", self.dir),
            mock.patch.object(p20_queue, "CONTROL_DIR", os.path.join(self.dir, "controls")),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_enqueue_and_pull_are_host_targeted(self):
        a = p20_queue.enqueue_control("mothership", "stop", "run_x", {"why": "wedged"})
        p20_queue.enqueue_control("bighonker", "requeue", "run_y")
        ms = p20_queue.pending_controls("mothership")
        self.assertEqual([c["id"] for c in ms], [a["id"]])
        self.assertEqual(len(p20_queue.pending_controls("bighonker")), 1)
        self.assertEqual(p20_queue.pending_controls("softserve"), [])

    def test_bad_action_and_missing_artifact_are_refused(self):
        with self.assertRaises(ValueError):
            p20_queue.enqueue_control("mothership", "detonate", "run_x")
        with self.assertRaises(ValueError):
            p20_queue.enqueue_control("mothership", "stop", "")
        with self.assertRaises(ValueError):
            p20_queue.enqueue_control("", "stop", "run_x")

    def test_claim_is_single_shot_and_not_mine_moves_on(self):
        rec = p20_queue.enqueue_control("mothership", "stop", "run_x")
        self.assertIsNone(p20_queue.claim_control(rec["id"], "bighonker"),
                          "another factory's claim must be a miss, not a steal")
        got = p20_queue.claim_control(rec["id"], "mothership")
        self.assertEqual(got["status"], "collected")
        self.assertIsNone(p20_queue.claim_control(rec["id"], "mothership"),
                          "a second claim (retry of ourselves) is a normal miss")
        self.assertEqual(p20_queue.pending_controls("mothership"), [])

    def test_controls_are_invisible_to_the_jobs_store(self):
        """THE isolation property: all_jobs()/pending() must never see a control — a control
        served to the render worker as a job is the class this subdirectory exists to prevent."""
        p20_queue.enqueue_control("mothership", "stop", "run_x")
        self.assertEqual(p20_queue.all_jobs(), [])
        self.assertEqual(p20_queue.pending(), [])

    def test_payload_rides_verbatim(self):
        rec = p20_queue.enqueue_control("mothership", "requeue", "run_x",
                                        {"why": "archived by a misfire"}, source="remedy:rem-1")
        got = p20_queue.pending_controls("mothership")[0]
        self.assertEqual(got["payload"], {"why": "archived by a misfire"})
        self.assertEqual(got["source"], "remedy:rem-1")
        self.assertEqual(rec["kind"], "factory_control")


class ControlEndpoints(unittest.TestCase):
    """Token auth + shapes, via the app's own TestClient harness."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import app
        cls.client = TestClient(app.app)

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="p20q-ep-test-")
        self._patches = [
            mock.patch.object(p20_queue, "QUEUE_DIR", self.dir),
            mock.patch.object(p20_queue, "CONTROL_DIR", os.path.join(self.dir, "controls")),
        ]
        for p in self._patches:
            p.start()
        import config
        self._token = config.P20_WORKER_TOKEN

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _hdr(self):
        return {"X-P20-Token": self._token} if self._token else {}

    def test_controls_endpoints_refuse_without_the_worker_token(self):
        r = self.client.get("/p20/controls?host=mothership")
        self.assertIn(r.status_code, (401, 403), r.text)
        r = self.client.post("/p20/controls/abc123/claim?host=mothership")
        self.assertIn(r.status_code, (401, 403), r.text)

    def test_pull_and_claim_roundtrip(self):
        if not self._token:
            self.skipTest("P20_WORKER_TOKEN unset in this env (auth fail-closed is pinned above)")
        rec = p20_queue.enqueue_control("mothership", "stop", "run_x", {"why": "decoy"})
        r = self.client.get("/p20/controls?host=mothership", headers=self._hdr())
        self.assertEqual(r.status_code, 200, r.text)
        ids = [c["id"] for c in r.json()["controls"]]
        self.assertIn(rec["id"], ids)
        r = self.client.post("/p20/controls/%s/claim?host=mothership" % rec["id"],
                             headers=self._hdr())
        self.assertTrue(r.json()["ok"], r.text)
        r = self.client.post("/p20/controls/%s/claim?host=mothership" % rec["id"],
                             headers=self._hdr())
        self.assertFalse(r.json()["ok"], "second claim must miss, not error")


if __name__ == "__main__":
    unittest.main()
