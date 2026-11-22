"""HAI jobs run as desk orders that wait for their start time and predecessors, start via /query/stream, and end with their run, allowing chains to proceed without holding a Claude session."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import hai_jobs  # noqa: E402
import p20_queue  # noqa: E402
import run_ledger  # noqa: E402

QUERY = {"text": "do the thing", "user": "Alex", "background": "5m"}


class ShippingContext(unittest.TestCase):
    def test_the_runner_receives_existing_owner_and_chat_provenance(self):
        import coding_client
        with patch.object(coding_client,"_request",return_value={}) as send:
            coding_client.ship("hai-scratch","jobs/run-x",owner="Alex",chat_id="chat-a")
        self.assertEqual(send.call_args.kwargs["body"],{"workspace":"hai-scratch","path":"jobs/run-x","owner":"Alex","chat_id":"chat-a"})

    def test_legacy_non_job_shipping_has_the_same_request(self):
        import coding_client
        with patch.object(coding_client,"_request",return_value={}) as send:
            coding_client.ship("fixture","part")
        self.assertEqual(send.call_args.kwargs["body"],{"workspace":"fixture","path":"part"})


class _Desk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = patch.object(p20_queue, "QUEUE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.started = []

    def start(self, order):
        self.started.append(order["id"])
        return "run-" + order["id"]


class WaitsForItsTimeAndItsPredecessors(_Desk):
    def test_it_waits_until_its_start_time(self):
        j = hai_jobs.submit(QUERY, name="night", not_before=2000.0)
        self.assertEqual(hai_jobs.tick(self.start, now=1999.0), [])
        self.assertEqual(p20_queue.get(j["id"])["status"], "pending")
        self.assertEqual(hai_jobs.tick(self.start, now=2000.0), [j["id"]])
        got = p20_queue.get(j["id"])
        self.assertEqual((got["status"], got["run_id"]), ("running", "run-" + j["id"]))

    def test_a_chain_moves_on_only_when_its_predecessor_is_done(self):
        a = hai_jobs.submit(QUERY, name="a")
        b = hai_jobs.submit(QUERY, name="b", after=[a["id"]])
        self.assertEqual(hai_jobs.tick(self.start, now=1.0), [a["id"]], "b waits for a")
        self.assertEqual(hai_jobs.tick(self.start, now=2.0), [], "a still running: b still waits")
        hai_jobs.ended(a["id"], "local")
        self.assertEqual(hai_jobs.tick(self.start, now=3.0), [b["id"]])

    def test_a_failed_predecessor_blocks_the_rest_of_the_chain(self):
        a = hai_jobs.submit(QUERY, name="a")
        b = hai_jobs.submit(QUERY, name="b", after=[a["id"]])
        hai_jobs.tick(self.start, now=1.0)
        hai_jobs.ended(a["id"], "error")
        self.assertEqual(p20_queue.get(a["id"])["status"], "failed")
        self.assertEqual(p20_queue.get(b["id"])["status"], "blocked", "the desk's own rule: never released")
        self.assertEqual(hai_jobs.tick(self.start, now=2.0), [])

    def test_a_set_that_goes_on_after_a_failure_starts_its_next_run(self):
        a = hai_jobs.submit(QUERY, name="a", go_on_after_failure=True)
        b = hai_jobs.submit(QUERY, name="b", after=[a["id"]], go_on_after_failure=True)
        c = hai_jobs.submit(QUERY, name="c", after=[b["id"]], go_on_after_failure=True)
        hai_jobs.tick(self.start, now=1.0)
        hai_jobs.ended(a["id"], "error")
        self.assertEqual(p20_queue.get(b["id"])["status"], "pending", "released, not blocked")
        self.assertEqual(hai_jobs.tick(self.start, now=2.0), [b["id"]])
        hai_jobs.tick(self.start, now=3.0, alive=lambda rid: False)     # b's run gone without a result: failed
        self.assertEqual(p20_queue.get(b["id"])["status"], "failed")
        self.assertEqual(hai_jobs.tick(self.start, now=4.0), [c["id"]], "the chain still goes on")

    def test_a_stop_still_stops_the_rest_of_a_set_that_goes_on(self):
        a = hai_jobs.submit(QUERY, name="a", go_on_after_failure=True)
        b = hai_jobs.submit(QUERY, name="b", after=[a["id"]], go_on_after_failure=True)
        c = hai_jobs.submit(QUERY, name="c", after=[b["id"]], go_on_after_failure=True)
        hai_jobs.tick(self.start, now=1.0)
        hai_jobs.ended(a["id"], "cancelled")
        self.assertEqual([p20_queue.get(x["id"])["status"] for x in (b, c)], ["blocked", "blocked"])
        self.assertEqual(hai_jobs.tick(self.start, now=2.0), [])

    def test_a_budget_stop_is_a_finished_job_and_a_stop_is_a_cancel(self):
        for route, want in (("budget", "done"), ("cancelled", "cancelled"), ("local", "done"), ("error", "failed")):
            j = hai_jobs.submit(QUERY, name=route)
            hai_jobs.ended(j["id"], route)
            self.assertEqual(p20_queue.get(j["id"])["status"], want, route)


class ItIsNeverLostOrDoubled(_Desk):
    def test_it_is_started_once(self):
        j = hai_jobs.submit(QUERY, name="once")
        hai_jobs.tick(self.start, now=1.0)
        hai_jobs.tick(self.start, now=2.0)
        self.assertEqual(self.started, [j["id"]])

    def test_a_start_that_must_wait_stays_on_the_desk(self):
        j = hai_jobs.submit(QUERY, name="energy")

        def defer(order):
            raise hai_jobs.Wait("Energy prices are high right now")
        hai_jobs.tick(defer, now=1.0)
        got = p20_queue.get(j["id"])
        self.assertEqual(got["status"], "pending")
        self.assertIn("Energy", got["hold_reason"])

    def test_a_start_that_breaks_is_recorded_not_lost(self):
        j = hai_jobs.submit(QUERY, name="broken")

        def boom(order):
            raise ValueError("bad query")
        hai_jobs.tick(boom, now=1.0)
        self.assertIn("bad query", p20_queue.get(j["id"])["error"])

    def test_a_running_job_whose_run_is_gone_is_failed_with_the_reason(self):
        j = hai_jobs.submit(QUERY, name="lost")
        hai_jobs.tick(self.start, now=1.0)
        hai_jobs.tick(self.start, now=2.0, alive=lambda rid: False)
        self.assertIn("without a recorded result", p20_queue.get(j["id"])["error"])

    def test_no_factory_can_take_it(self):
        hai_jobs.submit(QUERY, name="kirk-only")
        every_cap = {"order.compute", "render.flux2", "render.browser", "selfeng.overnight", "chat.window"}
        self.assertEqual(p20_queue.buildable_by(p20_queue.dispatchable(True), every_cap), [],
                         "only Kirk takes a hai.job: no factory declares the capability")

    def test_cancel_a_waiting_job_and_report_a_running_one(self):
        a = hai_jobs.submit(QUERY, name="w", not_before=9e9)
        self.assertEqual(hai_jobs.cancel(a["id"])[0], "cancelled")
        b = hai_jobs.submit(QUERY, name="r")
        hai_jobs.tick(self.start, now=1.0)
        self.assertEqual(hai_jobs.cancel(b["id"])[0], "running", "the caller stops its run; the run's end marks it")


class KirkStartsAndEndsIt(_Desk):
    def setUp(self):
        super().setUp()
        self._root = run_ledger.ROOT
        run_ledger.ROOT = __import__("pathlib").Path(self.tmp) / "hai-runs"
        self.addCleanup(setattr, run_ledger, "ROOT", self._root)
        p = patch.object(app, "_auto_title_worker", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def test_the_runs_end_marks_its_order(self):
        j = hai_jobs.submit(dict(QUERY, ephemeral=True), name="real")
        q = app.Q(**dict(QUERY, ephemeral=True))
        q.desk_order = j["id"]
        with patch.object(app, "_run_query_core", lambda q, status, diag: {"route": "local", "answer": "done"}), \
                patch.object(app, "_background_notify", lambda *a: None):
            app._accept_turn(q, "cid-hj-1")
            app._stream_worker(q, "cid-hj-1")
        self.assertEqual(p20_queue.get(j["id"])["status"], "done")

    def test_kirk_starts_it_as_query_stream_would(self):
        j = hai_jobs.submit(dict(QUERY, ephemeral=True), name="start")
        seen = {}
        with patch.object(app, "_stream_worker", lambda q, cid: seen.update(order=q.desk_order, cid=cid, user=q.user)), \
                patch.object(app, "_admit_background", lambda q: None):
            rid = app._start_hai_job(p20_queue.get(j["id"]))
            for _ in range(200):
                if seen:
                    break
                __import__("time").sleep(0.01)
        self.assertEqual(seen, {"order": j["id"], "cid": rid, "user": "Alex"})

    def test_the_submit_door_puts_it_on_the_desk_under_its_user(self):
        out = app.hai_jobs_submit(app.HaiJobReq(query=dict(QUERY), name="door", not_before=123.0), request=None)
        got = p20_queue.get(out["id"])
        self.assertEqual(((got["spec"] or {})["query"]["user"], got["spec"]["not_before"], got["host"]),
                         ("Alex", 123.0, "kirk"))

    def test_the_submit_door_carries_go_on_after_failure(self):
        out = app.hai_jobs_submit(app.HaiJobReq(query=dict(QUERY), name="door", go_on_after_failure=True), request=None)
        self.assertIs(p20_queue.get(out["id"])["spec"]["go_on_after_failure"], True)
        out = app.hai_jobs_submit(app.HaiJobReq(query=dict(QUERY), name="door2"), request=None)
        self.assertIs(p20_queue.get(out["id"])["spec"]["go_on_after_failure"], False, "the desk's rule stays the default")


class AJobShipsItsFolderBeforeItsOrderEnds(_Desk):
    """The job folder leaves the shared workspace before the order status is marked, so chained jobs never see it."""

    def test_the_folder_ships_before_the_order_is_marked_and_the_result_is_kept(self):
        j = hai_jobs.submit(QUERY, name="ships", ship={"workspace": "hai-scratch", "path": "jobs/run-h"})
        seen = []

        def shipper(ws, path):
            seen.append((ws, path, p20_queue.get(j["id"])["status"]))
            return {"shipped_to": "/app/shipped/hai-scratch/x-jobs__run-h", "files": 3}
        hai_jobs.ended(j["id"], "local", shipper=shipper)
        got = p20_queue.get(j["id"])
        self.assertEqual(seen, [("hai-scratch", "jobs/run-h", "pending")], "shipped while the order was not yet marked")
        self.assertEqual((got["status"], got["shipped_files"]), ("done", 3))

    def test_a_failed_shipment_is_recorded_and_the_run_still_finished(self):
        j = hai_jobs.submit(QUERY, name="ship-fails", ship={"workspace": "hai-scratch", "path": "jobs/run-h"})
        hai_jobs.ended(j["id"], "local", shipper=lambda ws, path: {"error": "no shipping area is mounted"})
        got = p20_queue.get(j["id"])
        self.assertEqual(got["status"], "done")
        self.assertIn("no shipping area", got["ship_error"])

    def test_a_job_that_names_no_folder_ships_nothing(self):
        j = hai_jobs.submit(QUERY, name="no-ship")
        hai_jobs.ended(j["id"], "local", shipper=lambda ws, path: self.fail("nothing to ship"))
        self.assertEqual(p20_queue.get(j["id"])["status"], "done")


class ABlindJobHidesTheOpenTasksFirst(_Desk):
    """Blind desk jobs must hide open tasks themselves, as run_chain no longer does."""

    def setUp(self):
        super().setUp()
        import config
        p = patch.object(config, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def test_the_open_tasks_join_the_hide_list_and_the_rest_of_it_stays(self):
        import json
        import rag
        with open(rag.hide_file(), "w", encoding="utf-8") as f:
            json.dump({"words": ["ltt"], "tasks": ["old"]}, f)
        n = hai_jobs.hide_open_tasks(lambda: {"tasks": [{"id": "t1"}, {"id": "t2"}]})
        self.assertEqual(n, 3)
        with open(rag.hide_file(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"words": ["ltt"], "tasks": ["old", "t1", "t2"]})

    def test_a_list_that_fails_keeps_the_job_waiting_never_unblind(self):
        with self.assertRaises(hai_jobs.Wait):
            hai_jobs.hide_open_tasks(lambda: {"error": "coding runner unavailable"})


class WorktreeShipmentReceipts(_Desk):
    def test_blind_successor_waits_on_an_explicit_shipping_failure_without_reclassifying_parent(self):
        a=hai_jobs.submit(QUERY,name="a",ship={"workspace":"w","path":"jobs/a"})
        b=hai_jobs.submit(QUERY,name="b",after=[a['id']],blind=True,go_on_after_failure=True)
        hai_jobs.ended(a['id'],'local',shipper=lambda *_:{"error":"could not isolate source folder"})
        self.assertEqual(p20_queue.get(a['id'])['status'],'done')
        self.assertEqual(hai_jobs.tick(self.start),[])
        held=p20_queue.get(b['id']);self.assertEqual(held['status'],'pending');self.assertIn('artifact isolation',held['hold_reason'])
        self.assertIsNone(held.get('run_id'))
    def test_verified_shipping_releases_the_same_original_blind_successor(self):
        a=hai_jobs.submit(QUERY,name="a")
        b=hai_jobs.submit(QUERY,name="b",after=[a['id']],blind=True)
        p20_queue.mark(a['id'],'done',ship_error='previous failure')
        self.assertEqual(hai_jobs.tick(self.start),[])
        p20_queue.mark(a['id'],'done',ship_error='')
        self.assertEqual(hai_jobs.tick(self.start),[b['id']])

    def test_worktree_provenance_and_base_support_are_kept_on_the_existing_order(self):
        j=hai_jobs.submit(QUERY,name="isolated",ship={"workspace":"hai-scratch","path":"jobs/x"})
        receipt={"shipped_to":"/shipped/wt-task/x","files":20,"source_task_id":"task", "logical_workspace":"hai-scratch",
                 "source_workspace":"wt-task","supporting_base_shipment":{"shipped_to":"/shipped/hai-scratch/x","files":5}}
        hai_jobs.ended(j['id'],'local',shipper=lambda *_:receipt)
        order=p20_queue.get(j['id'])
        self.assertEqual(order['shipped_to'],receipt['shipped_to'])
        self.assertEqual(order['ship_provenance']['source_task_id'],'task')
        self.assertEqual(order['ship_provenance']['supporting_base_shipment']['files'],5)
    def test_partial_delivery_remains_an_error_with_its_preserved_receipt(self):
        j=hai_jobs.submit(QUERY,name="partial",ship={"workspace":"hai-scratch","path":"jobs/x"})
        done={"shipped_to":"/shipped/wt-task/x","files":20}
        hai_jobs.ended(j['id'],'local',shipper=lambda *_:{"error":"base isolation refused","completed_task_shipment":done})
        order=p20_queue.get(j['id']);self.assertIn('base isolation refused',order['ship_error']);self.assertEqual(order['partial_shipment'],done)

if __name__ == "__main__":
    unittest.main()
