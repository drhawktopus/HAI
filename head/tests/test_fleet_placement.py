"""Tests for fleet placement logic.

The desk must honour its own placement decision and not recompute eligibility at handout.
Placement decisions must be persisted; filtering by capability alone is unsafe because
any capable box could otherwise collect work ruled out by policy (e.g., load balancing,
tier ordering). This ensures detection and action share one derivation.

All test classes in this module MUST inherit from unittest.TestCase. The CI suite runs
unittest, not pytest. Inheriting from (object) would cause these tests to be silently
ignored in CI, defeating the purpose of this file.
"""
import os
import shutil
import tempfile
import unittest


def _spec(name):
    return {"name": name, "mode": "compute", "payload": "x.py"}


class _QueueCase(unittest.TestCase):
    """Base: p20_queue pointed at a throwaway queue dir -- never the real one."""

    def setUp(self):
        import p20_queue
        self.q = p20_queue
        self._real_dir = p20_queue.QUEUE_DIR
        self._tmp = tempfile.mkdtemp(prefix="p20-queue-test-")
        p20_queue.QUEUE_DIR = self._tmp

    def tearDown(self):
        self.q.QUEUE_DIR = self._real_dir
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestFailedChainStepDoesNotStrandItsSuccessors(_QueueCase):

    def _chain(self):
        a = self.q.enqueue_factory_order(_spec("k256"), host="mothership")
        b = self.q.enqueue_factory_order(_spec("k1024"), host="mothership", after_ids=[a["id"]])
        c = self.q.enqueue_factory_order(_spec("k3072"), host="mothership", after_ids=[b["id"]])
        return a, b, c

    def test_a_failed_step_blocks_the_whole_downstream_chain(self):
        a, b, c = self._chain()
        self.q.mark(a["id"], "failed", error="boom")
        assert self.q.get(b["id"])["status"] == "blocked"
        assert self.q.get(c["id"])["status"] == "blocked", "step 3 must block too, not just step 2"

    def test_blocked_steps_leave_the_pending_queue(self):
        a, b, c = self._chain()
        self.q.mark(a["id"], "failed")
        assert [j["id"] for j in self.q.pending()] == []

    def test_a_cancelled_step_blocks_too(self):
        a, b, _ = self._chain()
        self.q.mark(a["id"], "cancelled")
        assert self.q.get(b["id"])["status"] == "blocked"

    def test_blocked_is_distinct_from_failed(self):
        """Marking a never-run step `failed` would blame something that was never defective, and
        deleting it would hide that the chain broke. The operator must be able to see both."""
        a, b, _ = self._chain()
        self.q.mark(a["id"], "failed")
        assert self.q.get(a["id"])["status"] == "failed"
        assert self.q.get(b["id"])["status"] == "blocked"
        assert "predecessor" in (self.q.get(b["id"]).get("error") or "")

    def test_a_done_step_releases_rather_than_blocks(self):
        """The happy path must be untouched -- this is the whole point of chains."""
        a, b, _ = self._chain()
        self.q.mark(a["id"], "done")
        assert self.q.get(b["id"])["status"] == "pending"
        assert self.q.blocked_by(self.q.get(b["id"])) == []

    def test_unrelated_pending_jobs_are_not_touched(self):
        a, _, _ = self._chain()
        loner = self.q.enqueue_factory_order(_spec("unrelated"), host="bighonker")
        self.q.mark(a["id"], "failed")
        assert self.q.get(loner["id"])["status"] == "pending"

    def test_a_cycle_cannot_hang_the_marker(self):
        """Malformed after_ids must not spin forever -- a store operation that hangs takes the desk
        with it."""
        x = self.q.enqueue_factory_order(_spec("x"), host="mothership")
        y = self.q.enqueue_factory_order(_spec("y"), host="mothership", after_ids=[x["id"]])
        bad = self.q.get(x["id"])
        bad["after_ids"] = [y["id"]]          # x now waits on y, which waits on x
        self.q._save(bad)
        self.q.mark(x["id"], "failed")             # must return, not recurse forever
        assert self.q.get(y["id"])["status"] == "blocked"


class TestPlacementIsPersisted(_QueueCase):
    def test_enqueue_records_the_placed_host(self):
        job = self.q.enqueue_factory_order(_spec("o1"), requires=["order.compute"], host="bighonker")
        assert job["host"] == "bighonker"

    def test_host_is_normalised(self):
        job = self.q.enqueue_factory_order(_spec("o2"), host="  BigHonker  ")
        assert job["host"] == "bighonker", "a host that differs only in case must not evade the gate"

    def test_absent_host_is_empty_not_missing(self):
        """Unplaced must be representable, since every render job is unplaced."""
        job = self.q.enqueue_factory_order(_spec("o3"))
        assert job["host"] == ""


class TestPlacedFor(_QueueCase):
    def test_a_placed_job_goes_only_to_its_host(self):
        jobs = [{"id": "a", "host": "bighonker"}, {"id": "b", "host": "mothership"}]
        assert [j["id"] for j in self.q.placed_for(jobs, "mothership")] == ["b"]
        assert [j["id"] for j in self.q.placed_for(jobs, "bighonker")] == ["a"]

    def test_the_exact_incident(self):
        """Mothership must not be offered an order placed on bighonker."""
        jobs = [{"id": "8aff632657e7", "order_name": "prolora_actcap_smoke_klein_bighonker",
                 "host": "bighonker", "requires": ["order.compute", "weights.klein-4b"]}]
        assert self.q.placed_for(jobs, "mothership") == []

    def test_unplaced_jobs_stay_available_to_anyone(self):
        """ADDITIVE: every render job and everything enqueued before this existed has no host and
        must behave exactly as before -- otherwise the fix silently stops the render queue."""
        jobs = [{"id": "r1"}, {"id": "r2", "host": ""}]
        for h in ("mothership", "bighonker", "softserve"):
            assert len(self.q.placed_for(jobs, h)) == 2

    def test_case_insensitive_on_both_sides(self):
        jobs = [{"id": "a", "host": "BigHonker"}]
        assert len(self.q.placed_for(jobs, "bighonker")) == 1

    def test_placement_and_capability_are_independent_gates(self):
        """Capability answers 'could this box build it', placement answers 'was it chosen'. A job
        can pass one and fail the other: a box can HAVE both capabilities and still be the wrong box."""
        jobs = [{"id": "a", "host": "bighonker", "requires": ["order.compute"]}]
        caps = ["order.compute", "weights.klein-4b"]
        assert len(self.q.buildable_by(jobs, caps)) == 1, "Mothership genuinely has the capability"
        assert self.q.placed_for(self.q.buildable_by(jobs, caps), "mothership") == [], \
            "...and must still not be offered it"
