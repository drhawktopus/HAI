"""Stop slips (`<order>.stop.json`) and control slips (`<order>.ctl.json`) for the Fellowship.

The GPU factory's customer vocabulary, ported: a customer stops / holds / releases / cancels /
reprioritises an order by dropping a slip in the INBOX, never by renaming files on the floor.

Isolation: every test redirects the modules' OWN path constants (INBOX, QUEUE_DIR, ARCHIVE_DIR,
STATE_DIR, RESULTS_DIR, OUTBOX, CURRENT) into a temp dir and replaces the notify / publish /
fleet-desk calls, then asserts the redirect took before doing anything. Nothing here can reach the
live inbox, queue or DM relay.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as TF
import tablet_fulfil as TFU
import tablet_runner as TR

RUN = "hardbench-code-matrix-3b.20260927-135123"


def _map_spec(name, items=("a", "b", "c"), batch=1):
    return {"kind": "map", "name": name, "payload": "/x/map_echo.py", "batch": batch,
            "items": list(items), "control": {"item": "ctl", "expect": "echo:ctl"}}


class SlipTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slips-")
        self.dirs = {d: os.path.join(self.tmp, d)
                     for d in ("inbox", "queue", "archive", "state", "results", "outbox")}
        for p in self.dirs.values():
            os.makedirs(p)
        self.dm = mock.MagicMock(name="notify_desk")
        self.fleet = mock.MagicMock(name="report_fleet_result")
        patches = [
            mock.patch.object(TF, "INBOX", self.dirs["inbox"]),
            mock.patch.object(TF, "QUEUE_DIR", self.dirs["queue"]),
            mock.patch.object(TF, "ARCHIVE_DIR", self.dirs["archive"]),
            mock.patch.object(TF, "STATE_DIR", self.dirs["state"]),
            mock.patch.object(TF, "RESULTS_DIR", self.dirs["results"]),
            mock.patch.object(TF, "OUTBOX", self.dirs["outbox"]),
            mock.patch.object(TF, "notify_desk", self.dm),
            mock.patch.object(TF, "report_fleet_result", self.fleet),
            mock.patch.object(TF, "publish", return_value=None),
            mock.patch.object(TF, "_SLIP_SEEN", {}),
            mock.patch.object(TF, "_RECENT_RETIRED", {}),
            mock.patch.object(TF, "_INBOX_DOWN", {"since": None, "why": None}),
            mock.patch.object(TF, "HEARTBEAT", os.path.join(self.dirs["state"], "runner.heartbeat")),
            mock.patch.object(TR, "QUEUE_DIR", self.dirs["queue"]),
            mock.patch.object(TR, "ARCHIVE_DIR", self.dirs["archive"]),
            mock.patch.object(TR, "STATE_DIR", self.dirs["state"]),
            mock.patch.object(TR, "RESULTS_DIR", self.dirs["results"]),
            mock.patch.object(TR, "CURRENT", os.path.join(self.dirs["state"], "cur.json")),
            mock.patch.object(TR, "CURRENT_RIGBY", os.path.join(self.dirs["state"], "cur.rigby.json")),
            mock.patch.object(TR, "notify_desk", self.dm),
            mock.patch.object(TR, "report_fleet_result", self.fleet),
            mock.patch.object(TR, "publish", return_value=None),
            mock.patch.object(TR, "beat", return_value=None),
            mock.patch.object(TR, "ship_to_outbox", return_value=[]),
            mock.patch.object(TFU, "STATE_DIR", self.dirs["state"]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for mod, attr in ((TF, "INBOX"), (TF, "QUEUE_DIR"), (TF, "ARCHIVE_DIR"),
                          (TR, "QUEUE_DIR"), (TR, "ARCHIVE_DIR"), (TR, "RESULTS_DIR"),
                          (TR, "CURRENT"), (TR, "CURRENT_RIGBY"), (TFU, "STATE_DIR")):
            self.assertTrue(getattr(mod, attr).startswith(self.tmp),
                            "%s.%s not redirected - refusing to run against a live path"
                            % (mod.__name__, attr))

    # helpers
    def queue(self, stem, spec=None, held=False):
        p = os.path.join(self.dirs["queue"], stem + (".tjob.json.HELD" if held else ".tjob.json"))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(spec if spec is not None else _map_spec(stem), f)
        return p

    def slip(self, fname, body=None):
        p = os.path.join(self.dirs["inbox"], fname)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(body if body is not None else {}, f)
        return p

    def ls(self, d):
        return sorted(os.listdir(self.dirs[d]))

    def dms(self):
        return [c.args[0] for c in self.dm.call_args_list]


class ParseSlipTests(unittest.TestCase):

    def test_stop_slip_names_its_order_by_file_name(self):
        slip, refusal = TF.parse_slip("orderA.stop.json", {"why": "wrong model"})
        self.assertEqual(refusal, "")
        self.assertEqual((slip["action"], slip["target"], slip["stops"], slip["why"]),
                         ("stop", "orderA", True, "wrong model"))

    def test_ctl_uses_the_gpu_factory_field_names(self):
        slip, refusal = TF.parse_slip("x.ctl.json", {"action": "priority", "order": "orderB",
                                                      "value": 5, "why": "urgent"})
        self.assertEqual(refusal, "")
        self.assertEqual((slip["target"], slip["value"], slip["why"]), ("orderB", 5.0, "urgent"))

    def test_short_prefix_is_refused_by_name(self):
        slip, refusal = TF.parse_slip("bulk.ctl.json", {"action": "hold", "prefix": "hardben"})
        self.assertIsNone(slip)
        self.assertIn("shorter than 8", refusal)

    def test_empty_prefix_is_refused(self):
        slip, refusal = TF.parse_slip("bulk.stop.json", {"prefix": ""})
        self.assertIsNone(slip)
        self.assertIn("over-broad", refusal)

    def test_a_pause_stop_carries_its_mark_and_a_plain_stop_does_not(self):
        self.assertIs(TF.parse_slip("o.stop.json", {"why": "deploy", "pause": True})[0]["pause"], True)
        self.assertIs(TF.parse_slip("o.stop.json", {"why": "wrong model"})[0]["pause"], False)

    def test_unknown_ctl_action_is_refused(self):
        slip, refusal = TF.parse_slip("x.ctl.json", {"action": "pause"})
        self.assertIsNone(slip)
        self.assertIn("unknown action", refusal)


class StopSlipTests(SlipTestBase):

    def test_stop_queued_order_retires_it_timestamped_with_cause(self):
        self.queue("orderA")
        self.queue("orderB")
        self.slip("orderA.stop.json", {"why": "wrong payload"})
        self.assertIsNone(TF.process_slips())
        self.assertEqual(self.ls("queue"), ["orderB.tjob.json"])
        arch = [f for f in self.ls("archive") if f.startswith("orderA.tjob.json")]
        self.assertEqual(len(arch), 1, self.ls("archive"))
        self.assertRegex(arch[0], r"^orderA\.tjob\.json\.stopped-\d{8}-\d{6}$")
        with open(os.path.join(self.dirs["archive"], arch[0]), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["_stopped"]["cause"], "wrong payload")
        self.assertEqual(rec["_stopped"]["state"], "queued")
        self.assertEqual(self.ls("inbox"), [], "the slip must be consumed")
        self.assertTrue(any(f.startswith("orderA.stop.json.done-") for f in self.ls("archive")))
        self.assertEqual(self.dms(), ["STOPPED orderA (queued): wrong payload"])

    def test_stop_held_order_retires_it(self):
        self.queue("orderH", held=True)
        self.slip("orderH.stop.json", {"why": "abandoned"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), [])
        self.assertTrue(any(f.startswith("orderH.tjob.json.stopped-") for f in self.ls("archive")))
        self.assertEqual(self.dms(), ["STOPPED orderH (held): abandoned"])

    def test_stop_unknown_order_archives_the_slip_and_says_so(self):
        self.queue("orderA")
        self.slip("ghost.stop.json")
        TF.process_slips()
        self.assertEqual(self.ls("queue"), ["orderA.tjob.json"])
        self.assertEqual(self.ls("inbox"), [])
        self.assertTrue(any(f.startswith("ghost.stop.json.unknown-") for f in self.ls("archive")))
        self.assertEqual(len(self.dms()), 1)
        self.assertIn("no queued, running or held order matches", self.dms()[0])

    def test_stop_for_the_running_order_is_left_for_the_runner(self):
        self.slip("orderR.stop.json", {"why": "enough"})
        hit = TF.process_slips(running={"stem": "orderR", "name": "orderR"})
        self.assertIsNotNone(hit)
        self.assertEqual(hit["slip"], "orderR.stop.json")
        self.assertEqual(self.ls("inbox"), ["orderR.stop.json"], "must NOT be consumed mid-run")
        self.assertEqual(self.dms(), [])

    def test_finished_running_stop_routes_with_one_dm(self):
        self.slip("orderR.stop.json", {"why": "enough"})
        TF.process_slips(finished={"slip": "orderR.stop.json", "stem": "orderR",
                                   "name": "orderR", "results": "orderR.X.results.json",
                                   "rows": 3})
        self.assertEqual(self.ls("inbox"), [])
        self.assertEqual(self.dms(), [
            "STOPPED orderR (running): enough -- shipped 3 finished row(s) flagged stopped:true "
            "-> orderR.X.results.json"])

    def test_a_desk_order_that_is_stopped_reports_failed_to_the_desk(self):
        spec = _map_spec("orderD")
        spec["fleet_order_id"] = "abc123"
        self.queue("orderD", spec)
        self.slip("orderD.stop.json", {"why": "not needed"})
        TF.process_slips()
        self.fleet.assert_called_once()
        args = self.fleet.call_args.args
        self.assertEqual(args[0]["fleet_order_id"], "abc123")
        self.assertFalse(args[1], "a stopped order must hold its chained successors")

    def test_late_stop_for_a_just_finished_order_says_it_finished(self):
        TF.note_retired("orderF", "/x/orderF.20260927-100000.results.json")
        self.slip("orderF.stop.json")
        TF.process_slips()
        self.assertIn("already finished", self.dms()[0])


class ControlSlipTests(SlipTestBase):

    def test_hold_parks_a_queued_order_as_HELD(self):
        self.queue("orderA")
        self.slip("orderA.ctl.json", {"action": "hold", "why": "later"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), ["orderA.tjob.json.HELD"])
        self.assertEqual(TF.queued(), [])
        self.assertEqual(self.dms(), ["HELD orderA (queued): later"])

    def test_release_readmits_a_held_order(self):
        self.queue("orderA", held=True)
        self.slip("orderA.ctl.json", {"action": "release"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), ["orderA.tjob.json"])
        self.assertEqual([os.path.basename(p) for p in TF.queued()], ["orderA.tjob.json"])
        self.assertEqual(self.dms(), ["RELEASED orderA (held): no cause given"])

    def test_release_refuses_to_clobber_a_queued_order_of_the_same_name(self):
        self.queue("orderA")
        self.queue("orderA", held=True)
        self.slip("orderA.ctl.json", {"action": "release"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), ["orderA.tjob.json", "orderA.tjob.json.HELD"])
        self.assertIn("nothing changed", self.dms()[0])

    def test_cancel_is_the_same_as_a_stop(self):
        self.queue("orderA")
        self.slip("anything.ctl.json", {"action": "cancel", "order": "orderA", "why": "dup"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), [])
        self.assertTrue(any(f.startswith("orderA.tjob.json.stopped-") for f in self.ls("archive")))
        self.assertEqual(self.dms(), ["STOPPED orderA (queued): dup"])

    def test_priority_moves_an_order_to_the_front(self):
        for s in ("order-1", "order-2", "order-3"):
            self.queue(s)
        self.assertEqual(os.path.basename(TF.queued()[0]), "order-1.tjob.json")
        self.slip("order-3.ctl.json", {"action": "priority", "why": "urgent"})
        TF.process_slips()
        self.assertEqual([os.path.basename(p) for p in TF.queued()],
                         ["order-3.tjob.json", "order-1.tjob.json", "order-2.tjob.json"])
        self.assertEqual(self.dms(), ["PRIORITY order-3 -> 1 (front of the queue): urgent"])

    def test_hold_of_the_running_order_is_refused_with_the_stop_hint(self):
        self.slip("orderR.ctl.json", {"action": "hold"})
        hit = TF.process_slips(running={"stem": "orderR", "name": "orderR"})
        self.assertIsNone(hit, "a hold must never stop a running order")
        self.assertEqual(self.ls("inbox"), [])
        self.assertIn("orderR.stop.json", self.dms()[0])


class PrefixSlipTests(SlipTestBase):

    def test_prefix_stop_retires_every_window_of_a_run_with_one_dm(self):
        for i in range(2, 6):
            self.queue("%s.win.%02d" % (RUN, i), held=True)
        self.queue("%s.win.06" % RUN)
        self.queue("unrelated-order")
        self.slip("stop-3b.stop.json", {"prefix": RUN, "why": "3B arm abandoned"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), ["unrelated-order.tjob.json"])
        stopped = [f for f in self.ls("archive") if ".tjob.json.stopped-" in f]
        self.assertEqual(len(stopped), 5, stopped)
        self.assertEqual(self.dms(), ["STOPPED prefix %s: 5 order(s) (1 queued, 4 held): "
                                      "3B arm abandoned" % RUN])

    def test_prefix_hold_parks_queued_windows_mid_run_but_not_the_running_one(self):
        self.queue("%s.win.02" % RUN)
        self.queue("%s.win.03" % RUN)
        self.slip("hold-3b.ctl.json", {"action": "hold", "prefix": RUN})
        hit = TF.process_slips(running={"stem": "%s.win.01" % RUN, "name": "%s.win.01" % RUN})
        self.assertIsNone(hit)
        self.assertEqual(self.ls("queue"), ["%s.win.02.tjob.json.HELD" % RUN,
                                            "%s.win.03.tjob.json.HELD" % RUN])
        self.assertEqual(len(self.dms()), 1)
        self.assertIn("HELD prefix %s: 2 order(s) (2 queued)" % RUN, self.dms()[0])

    def test_over_broad_prefix_is_refused_and_touches_nothing(self):
        self.queue("%s.win.02" % RUN)
        self.slip("bulk.ctl.json", {"action": "cancel", "prefix": "hardben"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), ["%s.win.02.tjob.json" % RUN])
        self.assertTrue(any(f.startswith("bulk.ctl.json.refused-") for f in self.ls("archive")))
        self.assertEqual(len(self.dms()), 1)
        self.assertTrue(self.dms()[0].startswith("REFUSED bulk.ctl.json: prefix 'hardben'"),
                        self.dms()[0])

    def test_dry_run_reports_and_changes_nothing(self):
        for i in range(2, 5):
            self.queue("%s.win.%02d" % (RUN, i), held=True)
        before = self.ls("queue")
        self.slip("dry.ctl.json", {"action": "cancel", "prefix": RUN, "dry_run": True})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), before)
        self.assertTrue(any(f.startswith("dry.ctl.json.dryrun-") for f in self.ls("archive")))
        self.assertEqual(self.dms(), ["DRY-RUN cancel prefix %s: would affect 3 order(s) "
                                      "(3 held) - nothing changed" % RUN])


class RequeueSlipTests(SlipTestBase):
    """`requeue` puts a stopped / finished order back in the queue under its own name, from its newest archived spec,
    so it resumes from its checkpoint - the GPU factories' requeue slip."""

    def archive(self, stem, spec, suffix, age_s=0):
        p = os.path.join(self.dirs["archive"], "%s.tjob.json.%s" % (stem, suffix))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        t = os.path.getmtime(p) - age_s
        os.utime(p, (t, t))
        return p

    def test_requeue_restores_the_newest_generation_without_its_stop_record(self):
        self.archive("orderA", dict(_map_spec("orderA"), items=["old"]), "20260101-000000", age_s=3600)
        self.archive("orderA", dict(_map_spec("orderA"), _stopped={"slip": "s"}), "stopped-20261002-082417")
        with open(os.path.join(self.dirs["state"], "orderA.checkpoint.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"task": "a"}\n{"task": "b"}\n')
        self.slip("orderA.ctl.json", {"action": "requeue", "why": "after the deploy"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), ["orderA.tjob.json"])
        with open(os.path.join(self.dirs["queue"], "orderA.tjob.json"), encoding="utf-8") as f:
            spec = json.load(f)
        self.assertEqual(spec["items"], ["a", "b", "c"], "the NEWEST generation, not the old one")
        self.assertNotIn("_stopped", spec)
        self.assertEqual(spec["_requeued"]["from"], "orderA.tjob.json.stopped-20261002-082417")
        self.assertEqual(len([f for f in self.ls("archive") if f.startswith("orderA.tjob.json.")]), 2,
                         "the archived generations stay where they are")
        self.assertEqual(self.dms(), ["REQUEUED orderA (from orderA.tjob.json.stopped-20261002-082417, "
                                      "checkpoint 2 row(s)): after the deploy"])
        self.assertEqual([os.path.basename(p) for p in TF.queued()], ["orderA.tjob.json"])

    def test_requeue_of_an_order_already_queued_changes_nothing(self):
        self.queue("orderA")
        self.archive("orderA", _map_spec("orderA"), "stopped-1")
        self.slip("orderA.ctl.json", {"action": "requeue"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), ["orderA.tjob.json"])
        self.assertEqual(self.dms(), ["REQUEUE orderA: it is already queued or held - nothing changed"])

    def test_requeue_of_the_running_order_changes_nothing(self):
        self.archive("orderA", _map_spec("orderA"), "stopped-1")
        self.slip("orderA.ctl.json", {"action": "requeue"})
        TF.process_slips(running={"stem": "orderA", "name": "orderA"})
        self.assertEqual(self.ls("queue"), [])
        self.assertEqual(self.dms(), ["REQUEUE orderA: it is running now - nothing changed"])

    def test_requeue_with_no_archived_spec_says_so(self):
        self.slip("orderZ.ctl.json", {"action": "requeue"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), [])
        self.assertTrue(any(f.startswith("orderZ.ctl.json.refused-") for f in self.ls("archive")))
        self.assertIn("no archived spec named orderZ.tjob.json.*", self.dms()[0])

    def test_requeue_never_matches_a_longer_name_sharing_the_prefix(self):
        self.archive("orderA-2", _map_spec("orderA-2"), "stopped-1")
        self.slip("orderA.ctl.json", {"action": "requeue"})
        TF.process_slips()
        self.assertEqual(self.ls("queue"), [])

    def test_requeue_refuses_a_prefix(self):
        slip, refusal = TF.parse_slip("x.ctl.json", {"action": "requeue", "prefix": RUN})
        self.assertIsNone(slip)
        self.assertIn("never a prefix", refusal)


class OneDmPerSlipTests(SlipTestBase):

    def test_every_slip_is_exactly_one_dm(self):
        for i in range(10):
            self.queue("%s.win.%02d" % (RUN, i))
        self.queue("orderX")
        self.slip("a.ctl.json", {"action": "hold", "prefix": RUN})
        self.slip("orderX.stop.json")
        self.slip("ghost.ctl.json", {"action": "release"})
        TF.process_slips()
        self.assertEqual(self.dm.call_count, 3, self.dms())
        self.assertEqual(self.ls("inbox"), [])

    def test_unreadable_slip_gets_grace_then_one_refusal(self):
        with open(os.path.join(self.dirs["inbox"], "half.stop.json"), "w") as f:
            f.write("{\"why\": ")
        self.queue("half")
        TF.process_slips()
        self.assertEqual(self.ls("inbox"), ["half.stop.json"], "a half-written slip waits")
        self.assertEqual(self.dms(), [])
        TF._SLIP_SEEN["half.stop.json"] -= TF.SLIP_GRACE_S + 1
        TF.process_slips()
        self.assertEqual(self.ls("inbox"), [])
        self.assertEqual(self.ls("queue"), ["half.tjob.json"], "an unreadable slip changes nothing")
        self.assertEqual(len(self.dms()), 1)
        self.assertIn("unreadable", self.dms()[0])


class ItemPathNeverTakesSlipsTests(SlipTestBase):

    def test_drain_inbox_accepts_orders_and_leaves_slips(self):
        self.slip("orderA.stop.json")
        self.slip("orderA.ctl.json", {"action": "hold"})
        with open(os.path.join(self.dirs["inbox"], "orderB.tjob.json"), "w",
                  encoding="utf-8") as f:
            json.dump(_map_spec("orderB"), f)
        self.assertEqual(TF.drain_inbox(), 1)
        self.assertEqual(self.ls("queue"), ["orderB.tjob.json"])
        self.assertEqual(self.ls("inbox"), ["orderA.ctl.json", "orderA.stop.json"])

    def test_queued_never_lists_slips_or_held_orders(self):
        self.queue("orderA")
        self.queue("orderB", held=True)
        for f in ("orderA.stop.json", "orderA.ctl.json"):
            with open(os.path.join(self.dirs["queue"], f), "w") as fh:
                fh.write("{}")
        self.assertEqual([os.path.basename(p) for p in TF.queued()], ["orderA.tjob.json"])


def _fake_network(on_run=None):
    """Patch the line's transport so run_order runs in-process. `on_run(host)` fires as each
    batch's payload 'executes' -- the moment a real stop slip would land mid-run."""
    def fake_ssh(host, cmd, timeout=0):
        if "python" in cmd and on_run:
            on_run(host)
        return 0, ""

    def fake_scp(host, src, dst, timeout=0):
        return 0, ""

    def fake_scp_from(host, remote, local, timeout=0):
        wd = os.path.dirname(local)
        with open(os.path.join(wd, "items.txt"), encoding="utf-8") as f:
            names = [l.strip() for l in f if l.strip()]
        with open(local, "w", encoding="utf-8") as f:
            json.dump([{"item": n, "result": "echo:%s" % n} for n in names], f)
        return 0, ""

    return [mock.patch.object(TFU, "_ssh", fake_ssh), mock.patch.object(TFU, "_scp", fake_scp),
            mock.patch.object(TFU, "_scp_from", fake_scp_from)]


class RunningStopTests(SlipTestBase):

    def _patched(self, patches):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_stopwatch_fires_only_for_its_own_order(self):
        self.slip("other.stop.json")
        w = TR.StopWatch("mine", "mine")
        w.check()
        self.assertFalse(w.event.is_set())
        self.slip("mine.stop.json", {"why": "halt"})
        w.check()
        self.assertTrue(w.event.is_set())
        self.assertEqual(w.slip["slip"], "mine.stop.json")
        self.assertIn("mine.stop.json", self.ls("inbox"), "the runner routes it after shipping")

    def test_line_stops_dealing_and_reports_undealt_items(self):
        ev = threading.Event()
        runs = []

        def on_run(host):
            runs.append(host)
            ev.set()                     # the stop lands while the FIRST batch is on the tablet
        self._patched(_fake_network(on_run))
        spec = _map_spec("orderS", items=["a", "b", "c", "d"], batch=1)
        rows, errors, _wall, expected, unclaimed = TFU.run_order(spec, ["nodeA"], stop_event=ev)
        self.assertEqual(len(runs), 1, "no batch may be dealt after the stop")
        self.assertEqual(len(rows), 1, "the in-flight batch finishes and its row is kept")
        self.assertEqual(expected, 5)
        self.assertEqual(len(unclaimed), 4, "undealt work is reported, never dropped")
        ok, why = TFU.validate_complete(rows, expected, unclaimed, key="item", stopped=True)
        self.assertFalse(ok)
        self.assertIn("STOPPED by a customer slip", why, "a stop must not be narrated as "
                                                         "retired nodes")
        _ok, why = TFU.validate_complete(rows, expected, unclaimed, key="item")
        self.assertIn("nodes retired", why)

    def test_stop_during_a_running_window_ships_stopped_results_with_finished_rows(self):
        stem = "%s.win.07" % RUN
        spec = {"kind": "map.window", "name": stem, "require_arch": "aarch64", "arms": [{
            "name": stem + ".arm", "payload": "/x/map_echo.py", "batch": 1,
            "distinct_workers": 2, "items": ["x1", "x2"],
            "control": {"item": "ctl", "expect": "echo:ctl"}}]}
        path = self.queue(stem, spec)
        watch = TR.StopWatch(stem, stem)
        lock = threading.Lock()
        # Barrier ensures both threads are in-flight before the stop signal lands;
        # prevents the test from accidentally measuring thread start-up latency.
        both_in_flight = threading.Barrier(2, timeout=30)

        def on_run(host):
            both_in_flight.wait()
            with lock:
                if not os.path.exists(os.path.join(self.dirs["inbox"], stem + ".stop.json")):
                    self.slip(stem + ".stop.json", {"why": "decoy stop"})
                watch.check()            # the watcher's poll lands mid-batch
        self._patched(_fake_network(on_run))
        res = TR.build_window_order(path, spec, ["nodeA", "nodeB"], caps=[], watch=watch)

        with open(res, encoding="utf-8") as f:
            out = json.load(f)
        self.assertTrue(out["stopped"])
        self.assertEqual(out["stopped_by"], stem + ".stop.json")
        self.assertEqual(out["stop_cause"], "decoy stop")
        arm = out["arms"][0]
        self.assertEqual(sorted(r["item"] for r in arm["rows"]), ["x1", "x2"],
                         "both in-flight pinned items finished and shipped")
        self.assertEqual(arm["never_measured"], ["ctl"], "the undealt control is reported")
        self.assertIn("STOPPED by a customer slip", arm["coverage"])
        self.assertFalse(out["usable"], "a stopped window is never usable")
        self.assertEqual(self.ls("queue"), [])
        self.assertTrue(any(f.startswith(stem + ".tjob.json.stopped-")
                            for f in self.ls("archive")), self.ls("archive"))
        self.assertEqual(self.ls("inbox"), [], "the stop slip is routed once the window ships")
        self.assertEqual(len(self.dms()), 1, "one DM per stop, and no WINDOW VOID DM besides it")
        self.assertTrue(self.dms()[0].startswith("STOPPED %s (running): decoy stop -- shipped 2 "
                                                 "finished row(s) flagged stopped:true" % stem),
                        self.dms()[0])

    def test_an_order_that_is_not_stopped_ships_stopped_false(self):
        path = self.queue("orderN")
        res = TR.retire_order(path, {"name": "orderN"}, rows=[{"item": "x"}], errors={},
                              wall=1.0, control_ok=True, control_why="ok", expected=1,
                              unclaimed=[])
        with open(res, encoding="utf-8") as f:
            self.assertIs(json.load(f)["stopped"], False)
        self.assertTrue(any(f.startswith("orderN.tjob.json.") and "stopped" not in f
                            for f in self.ls("archive")))

    def test_a_pause_slip_reaches_the_stop_record_retire_order_reads(self):
        """The seam: slip file -> process_slips' hit -> StopWatch -> stopped_record, as a running order sees it."""
        self.queue("orderR")
        self.slip("orderR.stop.json", {"why": "fleet_deploy x: stop for a deploy", "pause": True})
        hits = TF.process_slips(running=[{"stem": "orderR", "name": "orderR"}])
        self.assertIn("orderR", hits, hits)
        w = TR.StopWatch("orderR", "orderR")
        w.slip = hits["orderR"]
        self.assertIs(w.stopped_record()["pause"], True)

    def test_a_paused_order_tells_the_desk_nothing_and_a_stopped_one_its_verdict(self):
        """A paused stop reports no verdict to the desk; a finished stop reports its verdict."""
        path = self.queue("orderZ")
        TR.retire_order(path, {"name": "orderZ", "fleet_order_id": "j1"}, rows=[], errors={}, wall=1.0, control_ok=True,
                        control_why="ok", complete=False, complete_why="1/2 answers",
                        stopped={"slip": "orderZ.stop.json", "why": "fleet_deploy x: stop for a deploy", "pause": True})
        self.fleet.assert_not_called()
        path = self.queue("orderY")
        TR.retire_order(path, {"name": "orderY", "fleet_order_id": "j2"}, rows=[], errors={}, wall=1.0, control_ok=True,
                        control_why="ok", complete=False, complete_why="1/2 answers",
                        stopped={"slip": "orderY.stop.json", "why": "wrong model", "pause": False})
        self.assertEqual(self.fleet.call_count, 1, "a customer's stop still reports")
        self.assertIs(self.fleet.call_args.args[1], False)

    def test_a_placed_order_ships_its_key_and_the_row_tolkien_picked(self):
        """Asserts the delivery result includes the selected worker (key) and row, as the customer reads results rather than the queue."""
        row = {"box": "fellowship", "model": "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M",
               "skipped": [{"box": "rigby", "why": "gtx1060 is not visible to the cuda build"}]}
        key = {"family": "coding-f2", "tier": "fellowship", "lens": "least_energy"}
        path = self.queue("orderP")
        res = TR.retire_order(path, {"name": "orderP", "placement": key, "placement_row": row}, rows=[], errors={},
                              wall=1.0, control_ok=True, control_why="ok", expected=0, unclaimed=[])
        with open(res, encoding="utf-8") as f:
            out = json.load(f)
        self.assertEqual((out["placement"], out["placement_row"]), (key, row))
        path = self.queue("orderQ")
        res = TR.retire_order(path, {"name": "orderQ"}, rows=[], errors={}, wall=1.0, control_ok=True,
                              control_why="ok", expected=0, unclaimed=[])
        with open(res, encoding="utf-8") as f:
            self.assertNotIn("placement_row", json.load(f), "an unplaced order carries no placement")


class InboxUnreachable(SlipTestBase):
    """An unreachable inbox must fail, not be treated as empty; 'cannot-tell' is not 'no orders'."""

    def _gone(self):
        shutil.rmtree(self.dirs["inbox"])

    def _heartbeat(self):
        TF.beat("idle")
        with open(TF.HEARTBEAT, encoding="utf-8") as f:
            return json.load(f)

    def test_a_missing_inbox_is_logged_once_and_stated_on_the_heartbeat(self):
        self._gone()
        with mock.patch.object(TF, "log") as log:
            self.assertEqual(TF.drain_inbox(), 0)
            self.assertEqual(TF._inbox_slips(), [])
            TF.drain_inbox()
        said = [c.args[0] for c in log.call_args_list if "UNREACHABLE" in c.args[0]]
        self.assertEqual(len(said), 1, "one line when it goes down, not one per poll: %s" % said)
        hb = self._heartbeat()
        self.assertIsNotNone(hb["inbox_down_since"])
        self.assertEqual(hb["inbox_error"], "FileNotFoundError")

    def test_the_inbox_coming_back_is_logged_and_clears_the_heartbeat(self):
        self._gone()
        TF.drain_inbox()
        os.makedirs(self.dirs["inbox"])
        self.slip("x.tjob.json", _map_spec("x"))
        with mock.patch.object(TF, "log") as log:
            self.assertEqual(TF.drain_inbox(), 1, "orders waiting in the inbox are taken once it is readable")
        self.assertTrue(any("reachable again" in c.args[0] for c in log.call_args_list))
        hb = self._heartbeat()
        self.assertIsNone(hb["inbox_down_since"])

    def test_an_empty_readable_inbox_is_not_unreachable(self):
        TF.drain_inbox()
        self.assertIsNone(self._heartbeat()["inbox_down_since"])


class WatchdogReportsTheInbox(unittest.TestCase):
    def setUp(self):
        import tablet_watch as TW
        self.TW = TW
        self.hb = os.path.join(tempfile.mkdtemp(prefix="hb-"), "runner.heartbeat")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.hb), True)
        self.alerts = []
        for p in (mock.patch.object(TW, "HEARTBEAT", self.hb),
                  mock.patch.object(TW, "alert", side_effect=lambda k, m, **kw: self.alerts.append(k)),
                  mock.patch.object(TW, "clear")):
            p.start()
            self.addCleanup(p.stop)

    def _beat(self, since, why="FileNotFoundError"):
        import time
        with open(self.hb, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "inbox_down_since": since, "inbox_error": why}, f)

    def test_a_readable_inbox_says_nothing(self):
        self._beat(None, None)
        self.assertEqual(self.TW.patrol_inbox(), "")
        self.assertEqual(self.alerts, [])

    def test_a_brief_outage_shows_on_the_tile_but_does_not_dm(self):
        import time
        self._beat(time.time() - 5)
        self.assertIn("INBOX UNREACHABLE", self.TW.patrol_inbox())
        self.assertEqual(self.alerts, [])

    def test_a_lasting_outage_dms(self):
        import time
        self._beat(time.time() - self.TW.INBOX_CONFIRM_S - 5)
        self.assertIn("FileNotFoundError", self.TW.patrol_inbox())
        self.assertEqual(self.alerts, ["inbox"])

    def test_no_heartbeat_is_not_an_inbox_verdict(self):
        self.assertEqual(self.TW.patrol_inbox(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
