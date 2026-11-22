"""POST /p20/order/cancel - the menu filler cancels an order its stop rule just ruled out (34-Hard-Bench-Order-Spec).

Not collected -> the desk cancels it. Collected -> one factory `cancel` control. Finished -> left alone.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app  # noqa: E402
import p20_queue  # noqa: E402
from test_desk_cancel_status import REAL_RECORD  # noqa: E402


class OrderCancel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="order-cancel-")
        self.old = (p20_queue.QUEUE_DIR, p20_queue.CONTROL_DIR)
        p20_queue.QUEUE_DIR = os.path.join(self.tmp.name, "queue")
        p20_queue.CONTROL_DIR = os.path.join(self.tmp.name, "controls")
        os.makedirs(p20_queue.QUEUE_DIR)
        self.notes = mock.patch.object(app.notes, "log")
        self.notes.start()

    def tearDown(self):
        self.notes.stop()
        p20_queue.QUEUE_DIR, p20_queue.CONTROL_DIR = self.old
        self.tmp.cleanup()

    def _put(self, status):
        rec = json.loads(REAL_RECORD)
        rec["status"] = status
        rec.pop("error", None)
        with open(os.path.join(p20_queue.QUEUE_DIR, rec["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(rec, f)
        return rec

    def _controls(self):
        d = p20_queue.CONTROL_DIR
        return [json.load(open(os.path.join(d, n), encoding="utf-8")) for n in os.listdir(d)] if os.path.isdir(d) else []

    def test_pending_order_is_cancelled_at_the_desk(self):
        rec = self._put("pending")
        out = app.p20_order_cancel(app.OrderCancelReq(id=rec["id"], why="stop rule: priced out"))
        self.assertEqual((out["ok"], out["status"], out["how"]), (True, "cancelled", "desk"))
        stored = p20_queue.get(rec["id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertIn("stop rule: priced out", stored["error"])
        self.assertEqual(self._controls(), [], "an uncollected order needs no factory control")

    def test_collected_order_gets_one_factory_cancel_control(self):
        rec = self._put("claimed")
        out = app.p20_order_cancel(app.OrderCancelReq(id=rec["id"], why="stop rule: cannot fit", source="menu_fill"))
        self.assertEqual((out["ok"], out["how"]), (True, "factory control"))
        ctls = self._controls()
        self.assertEqual(len(ctls), 1)
        c = ctls[0]
        self.assertEqual((c["host"], c["action"], c["order"]), ("bighonker", "cancel", rec["order_name"]))
        self.assertEqual(c["payload"]["why"], "stop rule: cannot fit")
        self.assertEqual(p20_queue.get(rec["id"])["status"], "claimed", "the factory's receipt sets the final status")

    def test_finished_order_is_left_alone(self):
        for st in ("done", "failed", "cancelled"):
            rec = self._put(st)
            out = app.p20_order_cancel(app.OrderCancelReq(id=rec["id"]))
            self.assertEqual((out["ok"], out["status"]), (True, st))
            self.assertEqual(p20_queue.get(rec["id"])["status"], st)
        self.assertEqual(self._controls(), [])

    def test_unknown_order_is_refused(self):
        out = app.p20_order_cancel(app.OrderCancelReq(id="nosuchorder1"))
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
