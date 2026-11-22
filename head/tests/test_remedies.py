import json
import os
import shutil
import tempfile
import time
import unittest

import remedies


class RemedyStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="remedies-test-")
        self.q = remedies.RemedyQueue(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _file(self, kind="stop", host="mothership", order="run_x", ttl=600):
        return self.q.submit(kind, host, order, "test verdict", "fleet-watch",
                             watch_id="w1", ttl_seconds=ttl)

    # --- the whitelist is the ceiling ---
    def test_non_whitelisted_kinds_are_refused_at_filing(self):
        for kind in ("wake", "kill", "deploy", "delete", ""):
            out = self.q.submit(kind, "mothership", "run_x", "", "x")
            self.assertIn("error", out, "kind %r must never be filable" % kind)
        self.assertEqual(self.q.list(None), [])

    def test_wake_is_deliberately_not_a_remedy_kind(self):
        self.assertNotIn("wake", remedies.REMEDY_KINDS)
        self.assertEqual(set(remedies.REMEDY_KINDS), {"stop", "requeue"})

    def test_artifact_must_be_named(self):
        self.assertIn("error", self.q.submit("stop", "", "run_x", "", "x"))
        self.assertIn("error", self.q.submit("stop", "mothership", "", "", "x"))

    # --- filing + dedup ---
    def test_identical_pending_proposal_is_reused_not_duplicated(self):
        a = self._file()
        b = self._file()
        self.assertEqual(a["id"], b["id"], "the 60s tick must not paper the queue with copies")
        self.assertEqual(len(self.q.list()), 1)

    def test_decided_proposal_does_not_block_a_fresh_filing(self):
        a = self._file()
        self.q.reject(a["id"], "Alex")
        b = self._file()
        self.assertNotEqual(a["id"], b["id"])

    # --- re-verify at execution (the bedtime ruling) ---
    def test_approve_executes_only_when_condition_still_present(self):
        rec = self._file()
        fired = []
        out = self.q.approve(rec["id"], "Alex",
                             verify=lambda: (True, "still wedged"),
                             execute=lambda: fired.append(1) or "queued")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["remedy"]["status"], "approved")
        self.assertEqual(fired, [1])

    def test_cleared_condition_resolves_self_and_fires_nothing(self):
        rec = self._file()
        out = self.q.approve(rec["id"], "Alex",
                             verify=lambda: (False, "order finished at 03:12"),
                             execute=lambda: self.fail("must not execute on a cleared condition"))
        self.assertTrue(out["ok"])
        self.assertEqual(out["remedy"]["status"], "resolved_self")
        self.assertIn("03:12", out["remedy"]["result"],
                      "the decline must SAY when/what cleared — never a silent no-op")

    def test_unreadable_deck_refuses_without_consuming_the_yes(self):
        """Could-not-tell is not 'go' AND not 'gone' (fail closed, retry allowed)."""
        rec = self._file()
        out = self.q.approve(rec["id"], "Alex", verify=lambda: (None, "deck gone"),
                             execute=lambda: self.fail("must not execute blind"))
        self.assertFalse(out["ok"])
        self.assertEqual(self.q.get(rec["id"])["status"], "pending",
                         "the approval must NOT be consumed")
        out2 = self.q.approve(rec["id"], "Alex", verify=lambda: (True, "back"),
                              execute=lambda: "queued")
        self.assertTrue(out2["ok"], "a retry after the source answers must succeed")

    def test_failed_execution_keeps_the_proposal_pending_for_retry(self):
        rec = self._file()

        def boom():
            raise RuntimeError("desk write failed")
        out = self.q.approve(rec["id"], "Alex", verify=lambda: (True, "ok"), execute=boom)
        self.assertFalse(out["ok"])
        self.assertEqual(self.q.get(rec["id"])["status"], "pending")
        self.assertIn("desk write failed", self.q.get(rec["id"])["last_error"])

    # --- TTL = the watch's horizon; a late yes is refused politely ---
    def test_expired_proposal_refuses_late_approval_and_says_when(self):
        rec = self._file(ttl=1)
        # age it past its horizon without sleeping
        with self.q._lock:
            self.q._items[rec["id"]]["expires_epoch"] = time.time() - 5
        out = self.q.approve(rec["id"], "Alex", verify=lambda: (True, ""),
                             execute=lambda: self.fail("an expired proposal must never fire"))
        self.assertFalse(out["ok"])
        self.assertIn("expired", out["error"])
        self.assertEqual(self.q.get(rec["id"])["status"], "expired")

    def test_listing_surfaces_expiry_as_a_typed_state(self):
        rec = self._file(ttl=1)
        with self.q._lock:
            self.q._items[rec["id"]]["expires_epoch"] = time.time() - 5
        self.assertEqual(self.q.list("expired")[0]["id"], rec["id"])
        self.assertEqual(self.q.list("pending"), [])

    # --- decisions are single-shot ---
    def test_double_decision_is_refused(self):
        rec = self._file()
        self.q.approve(rec["id"], "Alex", verify=lambda: (True, ""), execute=lambda: "queued")
        out = self.q.approve(rec["id"], "Alex", verify=lambda: (True, ""),
                             execute=lambda: self.fail("second yes must not fire twice"))
        self.assertFalse(out["ok"])
        self.assertIn("already decided", out["error"])
        self.assertFalse(self.q.reject(rec["id"], "Alex")["ok"])

    # --- durability: filed at 2 AM, answered at breakfast, container recreated between ---
    def test_store_survives_a_reload(self):
        rec = self._file()
        q2 = remedies.RemedyQueue(self.dir)
        back = q2.get(rec["id"])
        self.assertIsNotNone(back)
        self.assertEqual(back["status"], "pending")
        out = q2.approve(rec["id"], "Alex", verify=lambda: (True, ""), execute=lambda: "queued")
        self.assertTrue(out["ok"])

    def test_records_are_files_on_disk_not_memory(self):
        rec = self._file()
        path = os.path.join(self.dir, rec["id"] + ".json")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["kind"], "stop")


if __name__ == "__main__":
    unittest.main()
