"""Unverified work is preserved as checkpoints marked UNVERIFIED rather than deleted, to allow potential recovery. In-flight tasks in the shared workspace are left untouched."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import app


class KeepUnverified(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for p in (mock.patch.object(app, "OVERNIGHT_STATE_DIR", self.tmp),
                  mock.patch.object(app, "_OVERNIGHT", {"thread": None, "state": {"run_id": "ovr-R1"}}),
                  mock.patch.object(app.notes, "log", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)

    def _client(self, fail_checkpoint=False):
        def checkpoint(action, task_id=None, note=None, **k):
            self.calls.append(("checkpoint", task_id, note))
            if fail_checkpoint:
                raise RuntimeError("runner down")
            return {"id": "cp-" + task_id}

        def task(action, task_id=None, **k):
            self.calls.append((action, task_id))
            return {"status": "cancelled"}
        return mock.patch.multiple(app.coding_client, checkpoint=checkpoint, task=task)

    def test_each_task_is_checkpointed_marked_then_abandoned(self):
        with self._client():
            tail = app._keep_unverified(["T1"], "the tests never passed on it")
        self.assertEqual([c[0] for c in self.calls], ["checkpoint", "abandon"], "kept BEFORE the workspace is reset")
        self.assertIn("UNVERIFIED", self.calls[0][2])
        self.assertIn("not deleted", self.calls[0][2])
        self.assertIn("cp-T1", tail)

    def test_the_overnight_item_in_flight_is_left_to_its_own_lane(self):
        app._ov_inflight_write("ovr-R1", item_id="i1", task_id="T-ov", phase="drive")
        with self._client():
            self.assertEqual(app._keep_unverified(["T-ov"], "x"), "")
        self.assertEqual(self.calls, [])

    def test_a_keep_that_fails_abandons_nothing(self):
        with self._client(fail_checkpoint=True):
            self.assertEqual(app._keep_unverified(["T1"], "x"), "")
        self.assertNotIn(("abandon", "T1"), self.calls, "never reset work that was not saved first")

    def test_the_gate_is_wired_to_it(self):
        self.assertIs(app.ollama_client.UNVERIFIED_HOOK, app._keep_unverified)


class ASupersededItemRestartsWithoutUndoingTheSync(unittest.TestCase):
    """A deploy moved live under an interrupted overnight item: it restarts on a new task, and the old task is closed
    by STATUS - an abandon would hard-reset the workspace to its pre-deploy baseline and undo the deploy's sync."""

    def test_old_task_closed_by_status_and_a_new_task_started(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        calls = []
        app_task = lambda action, task_id=None, **k: calls.append((action, task_id, k.get("status"))) or {
            "id": "T-old", "status": "active"}
        seen = {}
        with mock.patch.object(app, "OVERNIGHT_STATE_DIR", tmp), \
             mock.patch.object(app, "_OVERNIGHT", {"thread": None, "state": {"run_id": "ovr-R1"}}), \
             mock.patch.object(app.notes, "log", lambda *a, **k: None), \
             mock.patch.object(app.config, "OVERNIGHT_RESUME_AFTER_RESTART", True), \
             mock.patch.object(app.coding_client, "task", app_task), \
             mock.patch.object(app, "_ov_start_task", lambda it: "T-new"), \
             mock.patch.object(app.overnight, "_source_manifest", lambda root: {"app.py": "new"}), \
             mock.patch.object(app.overnight_worker, "work_item",
                               lambda item, b, deps, **k: seen.update(tid=deps.start_task(item)) or {"outcome": "done"}):
            app._ov_inflight_write("ovr-R1", item_id="i1", task_id="T-old", phase="drive",
                                   live_manifest={"app.py": "old"})
            app._overnight_real_worker({"id": "i1"}, app.overnight.RunBudget(600))
        self.assertEqual(seen["tid"], "T-new", "the item restarts on a new task on the new code")
        self.assertIn(("update", "T-old", "cancelled"), calls)
        self.assertNotIn("abandon", [c[0] for c in calls], "an abandon would undo the deploy's sync")


if __name__ == "__main__":
    unittest.main()
