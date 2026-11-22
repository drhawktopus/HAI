"""Operator cancels are recorded as `cancelled`, never `failed`."""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app  # noqa: E402
import p20_queue  # noqa: E402

# Real record copied from the live queue: an order the operator cancelled, which the old desk stored as `failed`.
# Verbatim except one substitution: the lab's share address became the documentation address 10.0.0.204 (six places,
# all in paths the test never reads). The hash pins these bytes.
# sha256 of the record below: a live desk record, verbatim except that the owner's name is the role
REAL_RECORD_SHA256 = "bd387061b72447b60a7bf8744195e0403ef0e4a21c4ca960808d8c74d5fa3398"
REAL_RECORD = r'''{
 "id": "000748ab95a6",
 "kind": "factory_order",
 "order_name": "hardbench-fleet-grid-bi-vl8b-f1-t00-off-s7-vl8b.20260929-182205_bighonker",
 "spec": {
  "name": "hardbench-fleet-grid-bi-vl8b-f1-t00-off-s7-vl8b.20260929-182205_bighonker",
  "mode": "compute",
  "load": "gpu",
  "initiator": "ClaudeSupervisor",
  "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py",
  "args": [
   "\\\\10.0.0.204\\SSDShared\\tabfoundry-jobs\\campaigns\\hard-bench-20260929\\fleet-grid-bi-vl8b-f1-t00-off-s7\\items.20260929-182205.json",
   "{ORDER_OUT}\\out.json",
   "--model",
   "qwen3-vl:8b",
   "--rounds",
   "3",
   "--num-ctx",
   "5120",
   "--temperature",
   "0.0",
   "--seed-base",
   "7",
   "--grade",
   "\\\\10.0.0.204\\SSDShared\\tabfoundry-jobs\\payloads\\map_swarm_code_hard.py"
  ],
  "progress_glob": "{ORDER_OUT}\\out.progress\\*.done",
  "progress_total": 33,
  "deliver_to": "\\\\10.0.0.204\\SSDShared\\tabfoundry-jobs\\campaigns\\hard-bench-20260929\\fleet-grid-bi-vl8b-f1-t00-off-s7",
  "notify": "failures",
  "caption_model": "aha2025/llama-joycaption-beta-one-hf-llava:Q4_K_M",
  "caption_workers": 4,
  "python_exe": "F:\\comfy-bench\\venv\\Scripts\\python.exe",
  "requires": [
   "order.compute",
   "model.qwen3-vl:8b"
  ],
  "placed_by": "scheduler.compose_orders",
  "placed_on": "bighonker",
  "pinned_to": [
   "bighonker"
  ]
 },
 "requires": [
  "order.compute",
  "model.qwen3-vl:8b"
 ],
 "rush": false,
 "after_ids": [],
 "step": "",
 "host": "bighonker",
 "status": "failed",
 "route": {
  "template": {
   "name": "hardbench-fleet-grid-bi-vl8b-f1-t00-off-s7-vl8b.20260929-182205",
   "mode": "compute",
   "load": "gpu",
   "initiator": "ClaudeSupervisor",
   "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py",
   "args": [
    "\\\\10.0.0.204\\SSDShared\\tabfoundry-jobs\\campaigns\\hard-bench-20260929\\fleet-grid-bi-vl8b-f1-t00-off-s7\\items.20260929-182205.json",
    "{ORDER_OUT}\\out.json",
    "--model",
    "qwen3-vl:8b",
    "--rounds",
    "3",
    "--num-ctx",
    "5120",
    "--temperature",
    "0.0",
    "--seed-base",
    "7",
    "--grade",
    "\\\\10.0.0.204\\SSDShared\\tabfoundry-jobs\\payloads\\map_swarm_code_hard.py"
   ],
   "progress_glob": "{ORDER_OUT}\\out.progress\\*.done",
   "progress_total": 33,
   "deliver_to": "\\\\10.0.0.204\\SSDShared\\tabfoundry-jobs\\campaigns\\hard-bench-20260929\\fleet-grid-bi-vl8b-f1-t00-off-s7",
   "notify": "failures"
  },
  "kind": "",
  "only": [
   "bighonker"
  ],
  "exclude": [],
  "items": null
 },
 "initiator": "fleet-desk",
 "created": "2026-09-29T23:22:03.666498+00:00",
 "claimed_by": "bighonker",
 "claimed_at": "2026-09-30T00:38:25.555034+00:00",
 "updated": "2026-10-02T15:07:49.675403+00:00",
 "error": "cancelled on bighonker by operator slip: 2026-10-02 P34 arm stop rule (the owner): qwen3-vl:8b coding CANNOT FIT on this box - it ignores think:false and its answers end at the 5,120 tested max; see menu_f"
}'''


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="desk-cancel-")
        self.old_queue = p20_queue.QUEUE_DIR
        p20_queue.QUEUE_DIR = self.tmp.name
        self.hook = mock.patch("campaign_step.on_order_result")
        self.hook_mock = self.hook.start()
        self.auth = mock.patch.object(app, "_p20_auth", lambda _req: None)
        self.auth.start()

    def tearDown(self):
        self.auth.stop()
        self.hook.stop()
        p20_queue.QUEUE_DIR = self.old_queue
        self.tmp.cleanup()

    def _real(self, status="claimed"):
        rec = json.loads(REAL_RECORD)
        rec["status"] = status
        rec.pop("error", None)
        p20_queue._save(rec)
        return rec

    def _receipt(self, job_id, status, error=""):
        return asyncio.run(app.p20_job_result(None, job_id, status=status, error=error))


class RealFixtureIsVerbatim(unittest.TestCase):
    def test_fixture_bytes_are_the_live_record(self):
        self.assertEqual(hashlib.sha256(REAL_RECORD.encode("utf-8")).hexdigest(), REAL_RECORD_SHA256)
        rec = json.loads(REAL_RECORD)
        self.assertEqual((rec["kind"], rec["status"]), ("factory_order", "failed"))
        self.assertTrue(rec["error"].startswith("cancelled on bighonker by operator slip: "))


class OneDerivationOfCancel(unittest.TestCase):
    def test_the_real_stored_record_is_a_cancel(self):
        rec = json.loads(REAL_RECORD)
        self.assertTrue(p20_queue.is_operator_cancel(rec["status"], rec["error"]))

    def test_explicit_status_is_a_cancel(self):
        self.assertTrue(p20_queue.is_operator_cancel("cancelled", ""))
        self.assertTrue(p20_queue.is_operator_cancel("CANCELLED", None))

    def test_real_failures_and_stops_are_not_cancels(self):
        for status, err in (("failed", "exited rc=1: urllib.error.HTTPError: HTTP Error 500"),
                            ("failed", "stopped by operator slip"),
                            ("failed", ""),
                            ("failed", "the operator said cancelled on bighonker by operator slip"),
                            ("failed", "cancelled on bighonker without the marker"),
                            ("done", "cancelled on bighonker by operator slip: x"),
                            ("", "cancelled on bighonker by operator slip: x")):
            self.assertFalse(p20_queue.is_operator_cancel(status, err), (status, err))


class ResultEndpointRecordsCancels(_Base):
    def test_explicit_cancelled_receipt_is_recorded_as_cancelled(self):
        rec = self._real()
        out = self._receipt(rec["id"], "cancelled", "cancelled on bighonker by operator slip: stop rule")
        self.assertEqual(out, {"ok": True, "status": "cancelled"})
        got = p20_queue.get(rec["id"])
        self.assertEqual(got["status"], "cancelled")
        self.assertIn("by operator slip", got["error"])

    def test_legacy_failed_plus_cancel_text_is_recorded_as_cancelled(self):
        rec = self._real()
        err = json.loads(REAL_RECORD)["error"]
        out = self._receipt(rec["id"], "failed", err)
        self.assertEqual(out, {"ok": True, "status": "cancelled"})
        got = p20_queue.get(rec["id"])
        self.assertEqual(got["status"], "cancelled")
        self.assertEqual(got["error"], err[:500])

    def test_a_real_failure_stays_failed(self):
        rec = self._real()
        out = self._receipt(rec["id"], "failed", "exited rc=1: urllib.error.HTTPError: HTTP Error 500")
        self.assertEqual(out, {"ok": True, "status": "failed"})
        self.assertEqual(p20_queue.get(rec["id"])["status"], "failed")

    def test_a_stop_slip_stays_failed(self):
        rec = self._real()
        out = self._receipt(rec["id"], "failed", "stopped by operator slip")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(p20_queue.get(rec["id"])["status"], "failed")

    def test_done_is_still_done(self):
        rec = self._real()
        self.assertEqual(self._receipt(rec["id"], "done"), {"ok": True, "status": "done"})
        self.assertEqual(p20_queue.get(rec["id"])["status"], "done")

    def test_a_cancel_still_blocks_its_successors(self):
        rec = self._real()
        p20_queue._save({"id": "succ00000001", "kind": "factory_order", "status": "pending",
                         "after_ids": [rec["id"]], "created": "2026-10-02T00:00:00+00:00"})
        self._receipt(rec["id"], "cancelled", "cancelled on bighonker by operator slip: x")
        self.assertEqual(p20_queue.get("succ00000001")["status"], "blocked")

    def test_the_campaign_hook_still_sees_a_non_done_result(self):
        rec = self._real()
        self._receipt(rec["id"], "cancelled", "cancelled on bighonker by operator slip: x")
        self.assertEqual(self.hook_mock.call_count, 1)
        self.assertIs(self.hook_mock.call_args.kwargs["ok"], False, "fail-closed: a cancel is not a success")


class CancelAtTheDesk(_Base):
    """POST /p20/order/cancel: cancels orders stuck in `pending` or `held` that lack a valid host/factory link."""

    def _cancel(self, job_id):
        return app.p20_order_cancel(app.OrderCancelReq(id=job_id, why="test", source="test"))

    def test_a_held_order_is_cancelled_at_the_desk(self):
        rec = self._real(status="held")
        rec["host"] = ""
        p20_queue._save(rec)
        self.assertEqual(self._cancel(rec["id"]), {"ok": True, "status": "cancelled", "how": "desk"})
        self.assertEqual(p20_queue.get(rec["id"])["status"], "cancelled")

    def test_a_pending_order_still_is_and_a_finished_one_is_left_alone(self):
        rec = self._real(status="pending")
        self.assertEqual(self._cancel(rec["id"])["how"], "desk")
        done = self._real(status="done")
        self.assertEqual(self._cancel(done["id"])["how"], "already terminal - left alone")


if __name__ == "__main__":
    unittest.main()
