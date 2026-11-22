"""Enforces that a second open order is rejected if it shares the same name.

Uses the real handler stack and a temporary store to verify the record state, including box suffixes."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app  # noqa: E402
import p20_queue  # noqa: E402
import scheduler  # noqa: E402


def placed(spec, need, **_kw):
    """A composer answer as it really looks for a pinned order: the spec's name gains the box suffix."""
    return {"ok": True, "orders": {"mothership": dict(spec, name=spec["name"] + "_mothership")}, "why": ["test"]}


class OneOpenOrderPerName(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="one-name-")
        self.old = (p20_queue.QUEUE_DIR, p20_queue.CONTROL_DIR)
        p20_queue.QUEUE_DIR = os.path.join(self.tmp.name, "queue")
        p20_queue.CONTROL_DIR = os.path.join(self.tmp.name, "controls")
        os.makedirs(p20_queue.QUEUE_DIR)
        for p in (mock.patch.object(app.notes, "log"), mock.patch.object(scheduler, "compose_orders", side_effect=placed)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        p20_queue.QUEUE_DIR, p20_queue.CONTROL_DIR = self.old
        self.tmp.cleanup()

    def _order(self, name):
        return app.p20_order(app.FactoryOrderReq(spec={"name": name, "mode": "compute"}, requires=["order.compute"], only=["mothership"]))

    def _only_job(self):
        jobs = [j for j in p20_queue.all_jobs() if j.get("kind") == "factory_order"]
        self.assertEqual(len(jobs), 1)
        return jobs[0]

    def test_a_second_order_under_an_open_name_is_refused(self):
        self.assertTrue(self._order("probe.1")["ok"])
        job = self._only_job()
        self.assertEqual(job["order_name"], "probe.1_mothership", "the record must carry the box suffix or this test proves nothing")
        out = self._order("probe.1")
        self.assertFalse(out["ok"])
        self.assertIn("already open", out["error"])
        self.assertIn(job["id"], out["error"])
        self._only_job()

    def test_a_different_name_is_accepted(self):
        self.assertTrue(self._order("probe.1")["ok"])
        self.assertTrue(self._order("probe.2")["ok"])

    def test_a_name_that_only_starts_the_same_is_accepted(self):
        self.assertTrue(self._order("probe")["ok"])
        self.assertTrue(self._order("probe_mothership2")["ok"])

    def test_a_claimed_order_still_holds_its_name(self):
        self.assertTrue(self._order("probe.1")["ok"])
        p20_queue.mark(self._only_job()["id"], "claimed", claimed_by="mothership")
        self.assertFalse(self._order("probe.1")["ok"])

    def test_a_name_comes_free_once_its_order_ends(self):
        for end in ("done", "failed", "cancelled"):
            with self.subTest(end):
                name = "probe.%s" % end
                self.assertTrue(self._order(name)["ok"])
                job = next(j for j in p20_queue.all_jobs() if j.get("order_name") == name + "_mothership" and j.get("status") == "pending")
                p20_queue.mark(job["id"], end)
                self.assertTrue(self._order(name)["ok"], "a retry after the order ended is honest work")


if __name__ == "__main__":
    unittest.main()
