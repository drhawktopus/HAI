"""Verifies that an overnight build run resumes correctly after an orchestrator restart:
- The in-flight item continues on its coding task.
- A running drive resumes from its ledger.
- A finished drive is not re-run.
- The run maintains its own clock.
- A deploy parks first, so only unplanned restarts count toward the crash-loop rule."""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import app
import overnight
import overnight_worker
import ollama_client
import run_ledger
from overnight import RunBudget, run_overnight


def _items(*ids):
    pool = [{"id": i, "title": "item %s" % i, "kind": "test-verifiable", "domain": "orchestrator"} for i in ids]
    return lambda done_ids: [it for it in pool if it["id"] not in done_ids]


class TheLoopResumes(unittest.TestCase):
    def test_the_item_in_flight_goes_first(self):
        order = []
        run_overnight(RunBudget(600), worker=lambda i, b: order.append(i["id"]) or {"outcome": "done"},
                      item_source=_items("a", "b", "c"), first_id="b")
        self.assertEqual(order, ["b", "a", "c"])

    def test_a_first_id_no_longer_eligible_is_ignored(self):
        order = []
        run_overnight(RunBudget(600), worker=lambda i, b: order.append(i["id"]) or {"outcome": "done"},
                      item_source=_items("a", "c"), first_id="b")
        self.assertEqual(order, ["a", "c"])

    def test_the_run_keeps_its_own_clock(self):
        st = run_overnight(RunBudget(600), worker=lambda i, b: {"outcome": "done"}, item_source=_items("a"),
                           budget_total_s=7200, spent_before_s=3000)
        self.assertEqual(st["budget_total_s"], 7200.0)
        self.assertGreaterEqual(st["spent_s"], 3000.0)
        self.assertLess(st["spent_s"], 3060.0)

    def test_the_item_start_checkpoint_names_it(self):
        saves = []
        run_overnight(RunBudget(600), worker=lambda i, b: {"outcome": "done"}, item_source=_items("a"),
                      on_state=lambda s: saves.append(json.loads(json.dumps(s))))   # as the file saves it
        self.assertEqual(saves[0]["current"]["id"], "a")
        self.assertEqual([r["id"] for r in saves[0]["items"]], [], "the item is in flight, not yet a record")
        self.assertIsNone(saves[-1]["current"])


class _StateDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ov = os.path.join(self.tmp, "overnight")
        os.makedirs(self.ov)
        for p in (mock.patch.object(app, "OVERNIGHT_STATE_DIR", self.ov),
                  mock.patch.object(app, "_DEPLOY_PARK_MARK", os.path.join(self.ov, "deploy-park.json")),
                  mock.patch.object(run_ledger, "ROOT", run_ledger.pathlib.Path(self.tmp) / "hai-runs"),
                  mock.patch.object(app.config, "OVERNIGHT_RESUME_AFTER_RESTART", True),
                  mock.patch.object(app, "_OVERNIGHT", {"thread": None, "state": {"run_id": "ovr-R1"}}),
                  mock.patch.object(app.notes, "log", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)

    def _write(self, name, obj):
        with open(os.path.join(self.ov, name), "w", encoding="utf-8") as f:
            json.dump(obj, f)


class TheLastRunIsTheRunState(_StateDir):
    def test_a_runs_other_files_never_read_as_the_last_run(self):
        """The adjudication request is written AFTER the final state and also starts "ovr-" and ends ".json"."""
        self._write("ovr-20261016T010000Z.json", {"run_id": "ovr-20261016T010000Z", "stop_reason": "exhausted"})
        time.sleep(0.02)
        self._write("ovr-20261016T010000Z.adjudicate-request.json", {"run_id": "ovr-20261016T010000Z", "items": []})
        self._write("inflight-ovr-20261016T010000Z.json", {"item_id": "x"})
        last = app._overnight_load_last()
        self.assertEqual(last.get("stop_reason"), "exhausted")
        self.assertFalse(overnight.is_incomplete(last), "a finished run must not look resumable")


class TheItemResumes(_StateDir):
    """The worker's resume decisions, with the item loop replaced by one pass: start, drive, verify, drive."""

    def _run(self, inflight=None, task_status="active"):
        if inflight:
            self._write("inflight-ovr-R1.json", inflight)
        calls = {"start": 0, "drives": []}

        def fake_work_item(item, budget, deps, **k):
            tid = deps.start_task(item)
            deps.drive(tid, item, budget)
            deps.verify(tid, item)
            deps.drive(tid, item, budget)
            calls["seen"] = app._ov_inflight_read("ovr-R1")
            return {"outcome": "done", "task_id": tid}

        def fake_start(it):
            calls["start"] += 1
            return "T-new"

        def fake_drive(tid, it, b, run_id=None, resumed=False):
            calls["drives"].append((tid, resumed))

        with mock.patch.object(overnight_worker, "work_item", fake_work_item), \
             mock.patch.object(app, "_ov_start_task", fake_start), \
             mock.patch.object(app, "_ov_drive", fake_drive), \
             mock.patch.object(app, "_ov_verify", lambda tid, it: {"passed": True}), \
             mock.patch.object(app.coding_client, "task", lambda *a, **k: {"id": "T-old", "status": task_status}), \
             mock.patch.object(overnight, "emit", lambda *a, **k: None):
            out = app._overnight_real_worker({"id": "i1", "title": "x"}, RunBudget(600))
        return out, calls

    def test_a_fresh_item_starts_its_own_task(self):
        out, calls = self._run()
        self.assertEqual((out["task_id"], calls["start"]), ("T-new", 1))
        self.assertEqual(calls["drives"], [("T-new", False), ("T-new", False)])
        self.assertFalse(os.path.exists(os.path.join(self.ov, "inflight-ovr-R1.json")), "cleared when it ends")

    def test_an_interrupted_drive_continues_on_the_same_task(self):
        out, calls = self._run({"item_id": "i1", "task_id": "T-old", "phase": "drive"})
        self.assertEqual((out["task_id"], calls["start"]), ("T-old", 0))
        self.assertEqual(calls["drives"][0], ("T-old", True))

    def test_a_finished_drive_is_not_run_again(self):
        out, calls = self._run({"item_id": "i1", "task_id": "T-old", "phase": "checking"})
        self.assertEqual(calls["drives"], [("T-old", True)], "the first drive is skipped; a retry still drives")

    def test_a_task_closed_meanwhile_starts_afresh(self):
        out, calls = self._run({"item_id": "i1", "task_id": "T-old", "phase": "drive"}, task_status="abandoned")
        self.assertEqual((out["task_id"], calls["start"]), ("T-new", 1))

    def test_another_items_record_is_not_this_items(self):
        out, calls = self._run({"item_id": "other", "task_id": "T-old", "phase": "checking"})
        self.assertEqual((out["task_id"], len(calls["drives"])), ("T-new", 2))

    def test_the_record_tracks_the_phase(self):
        _out, calls = self._run()
        self.assertEqual((calls["seen"]["task_id"], calls["seen"]["phase"]), ("T-new", "checking"))


class TheDriveIsCheckpointed(_StateDir):
    def _drive(self, resumed=False):
        seen = {}

        def core(q):
            seen["text"] = q.text
            led = getattr(ollama_client._CALL_CONTEXT, "run_ledger", None)
            seen["ledger"] = led.run_id if led is not None else None
            seen["exists"] = bool(led is not None and led.dir.exists())

        with mock.patch.object(app, "_run_query_core", core), \
             mock.patch.object(app, "_drive_seed_context", lambda *a: ""), \
             mock.patch.object(app, "_load_invariants", lambda: ""):
            app._ov_drive("T1", {"id": "i1", "objective": "o"}, RunBudget(3600), run_id="ovr-R1", resumed=resumed)
        return seen

    def test_every_turn_is_under_the_items_ledger_which_goes_at_the_end(self):
        seen = self._drive()
        self.assertEqual((seen["ledger"], seen["exists"]), ("ovd-ovr-R1-i1", True))
        self.assertFalse(app._ov_drive_ledger("ovr-R1", "i1").dir.exists(), "discarded: no rolling save")
        self.assertNotIn("RESUMED", seen["text"])

    def test_a_resumed_drive_with_its_ledger_continues_the_conversation(self):
        run_ledger.RunLedger.create("ovd-ovr-R1-i1", "overnight-drive", {"run_id": "ovr-R1", "item_id": "i1"})
        seen = self._drive(resumed=True)
        self.assertTrue(seen["exists"])
        self.assertNotIn("RESUMED", seen["text"], "the saved conversation carries on; no new instruction")

    def test_a_resumed_drive_without_a_ledger_is_told_to_read_its_work(self):
        self.assertIn("RESUMED", self._drive(resumed=True)["text"])


class TheResumedItemIsJudgedByLive(_StateDir):
    """A resumed item's own uncommitted edits make its workspace differ from live; the stale question for it is
    whether LIVE moved since it started (found by the live drill: a deploy's resume raced the deploy's own sync)."""
    OLD = {"app.py": "a1", "overnight.py": "o1"}
    NEW = {"app.py": "a1", "overnight.py": "o1", "embeddings.py": "e2"}

    def setUp(self):
        super().setUp()
        p = mock.patch.object(app.config, "OVERNIGHT_WORKSPACE_STALE_CHECK", True)
        p.start()
        self.addCleanup(p.stop)
        self._write("inflight-ovr-R1.json", {"item_id": "i1", "task_id": "T", "phase": "drive",
                                             "live_manifest": self.OLD})

    def test_its_own_edits_on_unchanged_live_are_not_stale(self):
        own = "workspace 'orchestrator-real' is STALE vs live: 1 source file(s) differ (app.py)"
        with mock.patch.object(overnight, "_source_manifest", lambda root: self.OLD), \
             mock.patch.object(overnight, "stale_reason", lambda ws: own):
            self.assertIsNone(app._make_stale_guard()({"id": "i1"}))

    def test_live_that_moved_under_it_is_held_with_the_reason(self):
        with mock.patch.object(overnight, "_source_manifest", lambda root: self.NEW), \
             mock.patch.object(overnight, "stale_reason", lambda ws: "STALE vs live"):
            why = app._make_stale_guard()({"id": "i1"})
        self.assertIn("a deploy changed live code while this item was in flight", why)

    def test_a_fresh_item_gets_the_plain_check(self):
        with mock.patch.object(overnight, "stale_reason", lambda ws: "STALE vs live"):
            self.assertEqual(app._make_stale_guard()({"id": "other"}), "STALE vs live")

    def _wait(self, ws_hashes, lags):
        seq = list(lags)
        slept = []
        with mock.patch.object(overnight, "_source_manifest", lambda root: self.NEW), \
             mock.patch.object(app, "_ov_ws_top_hashes", lambda ws: ws_hashes), \
             mock.patch.object(overnight, "workspace_lag", lambda ws: {"state": seq.pop(0) if seq else "stale"}):
            why = app._ov_wait_for_sync({"live_manifest": self.OLD}, 3600, sleep=lambda s: slept.append(s))
        return why, slept

    def test_an_item_with_no_edits_waits_for_the_deploys_sync(self):
        why, slept = self._wait(self.OLD, ["stale", "stale", "in_sync"])
        self.assertEqual((why, len(slept)), ("synced", 2))

    def test_an_item_with_edits_does_not_wait_for_a_sync_that_will_refuse(self):
        why, slept = self._wait({"app.py": "a-edited", "overnight.py": "o1"}, ["stale"])
        self.assertEqual((why, slept), ("own edits", []))

    def test_the_wait_is_bounded_by_the_runs_own_budget(self):
        with mock.patch.object(overnight, "_source_manifest", lambda root: self.NEW), \
             mock.patch.object(app, "_ov_ws_top_hashes", lambda ws: self.OLD):
            self.assertEqual(app._ov_wait_for_sync({"live_manifest": self.OLD}, 0), "budget spent")

    def test_the_item_records_live_as_it_was_when_it_started(self):
        # live unchanged since the item started (a crash restart, no deploy): the resumed item keeps its fingerprint
        self._write("inflight-ovr-R1.json", {"item_id": "i1", "task_id": "T", "phase": "drive",
                                             "live_manifest": self.NEW})
        with mock.patch.object(overnight, "_source_manifest", lambda root: self.NEW), \
             mock.patch.object(overnight_worker, "work_item",
                               lambda item, b, deps, **k: {"outcome": "done",
                                                           "seen": app._ov_inflight_read("ovr-R1")}), \
             mock.patch.object(app.coding_client, "task", lambda *a, **k: {"status": "active"}):
            resumed = app._overnight_real_worker({"id": "i1"}, RunBudget(600))["seen"]   # clears the record at its end
            fresh = app._overnight_real_worker({"id": "other"}, RunBudget(600))["seen"]
        self.assertEqual(fresh["live_manifest"], self.NEW, "a fresh item fingerprints live now")
        self.assertEqual(resumed["task_id"], "T", "a resumed item on unchanged live continues its own task")
        self.assertEqual(resumed["live_manifest"], self.NEW, "and keeps the fingerprint from its start")


class TheRunResumesAfterARestart(_StateDir):
    LAST = {"run_id": "ovr-R1", "active": True, "stop_reason": None, "dry_run": False, "max_seconds": 1800,
            "budget_total_s": 7200, "spent_s": 3000, "no_progress_limit": 3, "items": [{"id": "a"}],
            "current": {"id": "b"}}

    def _resume(self):
        started = []
        with mock.patch.object(app, "_overnight_start",
                               lambda req, **k: started.append((req, k)) or {"status": "started"}), \
             mock.patch.object(app, "_overnight_notify", lambda *a: None):
            r = app._resume_overnight_after_restart()
        return r, started

    def test_an_interrupted_run_starts_again_with_its_own_clock_and_no_dm(self):
        self._write("ovr-R1.json", self.LAST)
        r, started = self._resume()
        req, k = started[0]
        self.assertEqual((req.resume, req.dry_run, req.energy_gated), (True, False, False))
        self.assertEqual(k, {"keep_clock": True, "quiet": True})
        self.assertEqual(r["status"], "started")

    def test_two_unplanned_restarts_with_no_progress_end_the_run(self):
        self._write("ovr-R1.json", self.LAST)
        self._resume()
        r, started = self._resume()
        self.assertEqual((r["status"], started), ("refused", []))
        with open(os.path.join(self.ov, "ovr-R1.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual((st["stop_reason"], st["active"]), ("restart_loop", False))

    def test_back_to_back_deploys_are_not_a_crash_loop(self):
        self._write("ovr-R1.json", self.LAST)
        self._resume()
        app._deploy_park_mark(True)                       # the deploy parked before recreating
        r, started = self._resume()
        self.assertEqual(r["status"], "started")
        self.assertFalse(os.path.exists(app._DEPLOY_PARK_MARK), "the mark is used once")

    def test_finished_halted_and_dry_runs_stay_put(self):
        for last in (dict(self.LAST, active=False, stop_reason="exhausted"),
                     dict(self.LAST, active=False, stop_reason="halt"), dict(self.LAST, dry_run=True)):
            self._write("ovr-R1.json", last)
            r, started = self._resume()
            self.assertEqual((r, started), (None, []), last)

    def test_a_deploy_no_longer_lists_the_overnight_drive_as_lost(self):
        with mock.patch.object(app, "_overnight_status", lambda: {"active": True}):
            self.assertIs(app.runs_parked()["not_resumable"]["overnight_drive"], False)


if __name__ == "__main__":
    unittest.main()
