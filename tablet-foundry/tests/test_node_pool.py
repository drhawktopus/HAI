"""Fleet orders must only consume the specific tablets/nodes they require, leaving the rest free for concurrent execution. Each tablet acts as an independent lane; multi-node work is coordinated across them."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry
import tablet_fulfil
import tablet_runner as TR

HOSTS = ["h1", "h2", "h3"]


def _reset_pool():
    with TR.NODES_LK:
        TR.NODE_HOLDER.clear()
    for d in (TR.LAUNCH_RESERVED, TR.STARTED, TR.BLOCKED, TR.FLEET_RUNNING, TR.LAUNCH_GEN, TR._FLEET_CLAIMS,
              TR._FLEET_KEYS):
        d.clear()


def _map(name, n_items, **kw):
    spec = {"kind": "map", "name": name, "payload": "/x/map_echo.py", "items": ["%s-%d" % (name, i)
                                                                                for i in range(n_items)],
            "control": {"item": "%s-control" % name, "expect": "echo:%s-control" % name}}
    spec.update(kw)
    return spec


class NodePoolBasics(unittest.TestCase):
    def setUp(self):
        _reset_pool()
        self.addCleanup(_reset_pool)
        p = mock.patch.object(TR, "log")
        p.start()
        self.addCleanup(p.stop)

    def test_take_is_all_or_none_and_give_back_frees_and_wakes(self):
        TR.take_nodes("a", ["h1", "h2"])
        with self.assertRaises(TR.NodesBusy) as cm:
            TR.take_nodes("b", ["h2", "h3"])
        self.assertEqual(cm.exception.wants, {"h2", "h3"})
        self.assertNotIn("h3", TR.NODE_HOLDER, "a refused take holds nothing")
        TR.take_nodes("a", ["h1"])                          # the holder may re-state its own tablets
        gen = TR.NODES_GEN[0]
        TR.NODES_FREED.clear()
        TR.give_back("a")
        self.assertEqual(TR.NODE_HOLDER, {})
        self.assertEqual(TR.NODES_GEN[0], gen + 1)
        self.assertTrue(TR.NODES_FREED.is_set())

    def test_unavailable_counts_other_holders_and_reservations_never_its_own(self):
        TR.take_nodes("a", ["h1"])
        TR.LAUNCH_RESERVED["b"] = {"h3"}
        self.assertEqual(TR.unavailable_to("b"), {"h1", "h3"})
        self.assertEqual(TR.unavailable_to("a"), set())

    def test_useful_nodes_is_one_per_batch_with_the_control_counted(self):
        self.assertEqual(tablet_fulfil.useful_nodes(_map("m", 1, batch=8)), 1)
        self.assertEqual(tablet_fulfil.useful_nodes(_map("m", 20, batch=8)), 3)        # 21 items incl. control
        self.assertEqual(tablet_fulfil.useful_nodes(_map("m", 7, batch=1)), 8)
        self.assertIsNone(tablet_fulfil.useful_nodes({"kind": "sweep", "name": "s", "items_list": "/no/such"}))

    def test_window_hosts_is_exactly_what_execute_window_selects(self):
        spec = {"arms": [{"name": "a", "distinct_workers": 1}, {"name": "b", "distinct_workers": 1}]}
        chosen = TR.window_hosts(spec, ["h1", "h2", "h3", "h4"])
        self.assertEqual(len(chosen), 2)
        remaining, picks = list(chosen), []
        for arm in spec["arms"]:
            got = TR._select_distinct_hosts(arm["name"], remaining, 1)
            remaining = [h for h in remaining if h not in got]
            picks += got
        self.assertEqual(picks, chosen, "re-selected from the held set, the window takes the same cohorts")


class BuildOrderTakesOnlyWhatItNeeds(unittest.TestCase):
    def setUp(self):
        _reset_pool()
        self.addCleanup(_reset_pool)
        tablet_fulfil._ORPHAN_THREADS.clear()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        health = {h: {"healthy": True, "alive": True, "why": "ok"} for h in HOSTS}
        self.ran = []

        def run_order(spec, hosts, **kw):
            with TR.NODES_LK:
                held = {h for h, k in TR.NODE_HOLDER.items() if k == spec["name"]}
            self.ran.append((list(hosts), held))
            return [], {}, 0.0, 0, []
        for p in (mock.patch.object(TR, "fleet_health", return_value=health),
                  mock.patch.object(TR, "hold_order"),
                  mock.patch.object(TR, "build_window_order"),
                  mock.patch.object(TR, "detect_capabilities", return_value=([], HOSTS)),
                  mock.patch.object(TR, "run_order", side_effect=run_order),
                  mock.patch.object(TR, "validate_control", return_value=(True, "ok")),
                  mock.patch.object(TR, "validate_complete", return_value=(True, "ok")),
                  mock.patch.object(TR, "retire_order", return_value="r.json"),
                  mock.patch.object(TR, "notify_desk"),
                  mock.patch.object(TR, "publish"),
                  mock.patch.object(TR, "write_current"),
                  mock.patch.object(TR, "log")):
            self.m = p.start()
            self.addCleanup(p.stop)

    def _write(self, spec):
        path = os.path.join(self.tmp, spec["name"] + ".tjob.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        return path

    def test_a_one_batch_order_holds_one_tablet_during_its_run(self):
        TR.build_order(self._write(_map("one", 1, batch=8)))
        hosts, held = self.ran[0]
        self.assertEqual(len(hosts), 1, "one batch = one tablet, never the fleet")
        self.assertEqual(set(hosts), held, "the tablets it runs on are the tablets it holds")

    def test_a_second_order_runs_on_the_free_tablets(self):
        TR.take_nodes("other", ["h1"])
        TR.build_order(self._write(_map("two", 1, batch=8)))
        self.assertNotIn("h1", self.ran[0][0])

    def test_every_tablet_held_elsewhere_waits_with_what_it_wants(self):
        TR.take_nodes("other", HOSTS)
        with self.assertRaises(TR.NodesBusy) as cm:
            TR.build_order(self._write(_map("three", 1, batch=8)))
        self.assertEqual(cm.exception.wants, set(HOSTS))
        TR.hold_order.assert_not_called()
        self.assertEqual(self.ran, [], "nothing ran")

    def test_a_cohort_takes_its_count_from_the_free_ones_or_waits(self):
        TR.take_nodes("other", ["h1"])
        TR.build_order(self._write(_map("coh", 2, batch=1, distinct_workers=2)))
        self.assertEqual(sorted(self.ran[0][0]), ["h2", "h3"])
        TR.give_back("coh")
        TR.take_nodes("other", ["h2"])
        with self.assertRaises(TR.NodesBusy):
            TR.build_order(self._write(_map("coh2", 2, batch=1, distinct_workers=2)))
        TR.hold_order.assert_not_called()

    def test_a_window_holds_exactly_its_cohorts(self):
        spec = {"kind": "map.window", "name": "win", "arms": [
            {"name": "a", "payload": "/x/map_echo.py", "batch": 1, "distinct_workers": 1, "items": ["x"],
             "control": {"item": "c", "expect": "echo:c"}}]}
        seen = []
        TR.build_window_order.side_effect = lambda path, spec, hosts, caps, watch=None: seen.append(
            (list(hosts), dict(TR.NODE_HOLDER)))
        TR.build_order(self._write(spec))
        hosts, held = seen[0]
        self.assertEqual(len(hosts), 1)
        self.assertEqual(held, {hosts[0]: "win"}, "during its run it holds exactly its cohort")
        self.assertEqual(TR.NODE_HOLDER, {}, "and gives it back when it ends")


class RunOneGivesBackByAnyRoute(unittest.TestCase):
    def setUp(self):
        _reset_pool()
        self.addCleanup(_reset_pool)
        for p in (mock.patch.object(TR, "log"), mock.patch.object(TR, "hold_order"),
                  mock.patch.object(TR, "StopWatch"), mock.patch.object(TR, "publish")):
            p.start()
            self.addCleanup(p.stop)

    def test_an_order_that_blows_up_still_gives_its_tablets_back(self):
        def boom(path, watch):
            TR.take_nodes("crash", ["h1", "h2"])
            raise RuntimeError("payload exploded")
        with mock.patch.object(TR, "build_order", side_effect=boom), \
                mock.patch.object(TR, "write_current"):
            TR.run_one("/q/crash.tjob.json", {})
        self.assertEqual(TR.NODE_HOLDER, {})

    def test_busy_tablets_record_a_block_instead_of_a_retry_clock(self):
        def busy(path, watch):
            raise TR.NodesBusy("all held", wants={"h1"})
        waiting = {}
        with mock.patch.object(TR, "build_order", side_effect=busy):
            TR.run_one("/q/busy.tjob.json", waiting)
        self.assertEqual(waiting, {})
        self.assertEqual(TR.BLOCKED["/q/busy.tjob.json"]["wants"], {"h1"})


class DispatchFillsTheFreeSpots(unittest.TestCase):
    def setUp(self):
        _reset_pool()
        self.addCleanup(_reset_pool)
        self.calls = []

    def _launch(self, refuse=()):
        def launch(path, waiting, reserved):
            self.calls.append((path, set(reserved)))
            if path in refuse:
                TR.BLOCKED[path] = {"wants": {path + "-tabs"}, "gen": TR.NODES_GEN[0], "at": time.time()}
                return False
            return True
        return launch

    def test_every_order_that_fits_starts_in_one_pass(self):
        n = TR.dispatch_fleet(["a", "b", "c"], {}, launch=self._launch())
        self.assertEqual(n, 3, "Kirk sends ten, Tolkien starts every one he has a spot for")

    def test_a_waiting_order_reserves_its_tablets_against_the_orders_behind_it(self):
        TR.dispatch_fleet(["big", "small"], {}, launch=self._launch(refuse=("big",)))
        self.assertEqual(self.calls[1], ("small", {"big-tabs"}))

    def test_a_refused_order_is_retried_on_a_give_back_not_before(self):
        TR.dispatch_fleet(["big"], {}, launch=self._launch(refuse=("big",)))
        TR.dispatch_fleet(["big", "small"], {}, launch=self._launch(refuse=("big",)))
        self.assertEqual([c[0] for c in self.calls], ["big", "small"], "nothing was given back: big not re-asked")
        self.assertEqual(self.calls[1][1], {"big-tabs"}, "and its reservation still holds")
        TR.give_back("someone")
        TR.dispatch_fleet(["big"], {}, launch=self._launch(refuse=("big",)))
        self.assertEqual(self.calls[-1][0], "big")

    def test_a_running_order_is_not_launched_twice(self):
        TR.FLEET_RUNNING["a"] = threading.current_thread()
        with mock.patch.object(TR, "order_stem", side_effect=lambda p: p):
            TR.dispatch_fleet(["a", "b"], {}, launch=self._launch())
        self.assertEqual([c[0] for c in self.calls], ["b"])

    def test_launch_returns_once_the_order_holds_its_tablets_and_it_keeps_running(self):
        gate = threading.Event()

        def fake_run_one(path, waiting):
            TR.take_nodes("long", ["h1"])
            gate.wait(5)
            TR.give_back("long")
        with mock.patch.object(TR, "run_one", side_effect=fake_run_one), mock.patch.object(TR, "log"):
            started = TR.launch_fleet_order("/q/long.tjob.json", {})
            self.assertTrue(started)
            self.assertIn("long", TR.FLEET_RUNNING, "still running beside the next order")
            gate.set()
            TR.FLEET_RUNNING.get("long", threading.Thread()).join(5) if "long" in TR.FLEET_RUNNING else None
        self.assertNotIn("long", TR.FLEET_RUNNING)


class FleetClaimListsEveryOrder(unittest.TestCase):
    def setUp(self):
        _reset_pool()
        self.addCleanup(_reset_pool)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.cur = os.path.join(d, "CURRENT_ORDER.json")
        for p in (mock.patch.object(TR, "CURRENT", self.cur), mock.patch.object(TR, "STATE_DIR", d)):
            p.start()
            self.addCleanup(p.stop)

    def _read(self):
        with open(self.cur, encoding="utf-8") as f:
            return json.load(f)

    def test_two_orders_one_claim_oldest_first_and_the_last_end_removes_it(self):
        TR._FLEET_KEYS.update(a="a-stem", b="b-stem")
        TR.take_nodes("a-stem", ["h1"])
        TR.write_current({"name": "a"}, "fulfilment", 0, 1)
        TR.write_current({"name": "b"}, "fulfilment", 0, 2)
        c = self._read()
        self.assertEqual([r["name"] for r in tablet_foundry.claim_orders(c)], ["a", "b"])
        self.assertEqual(c["name"], "a", "the oldest stays at the top level for single-order readers")
        self.assertEqual(c["orders"][0]["hosts"], ["h1"])
        TR.write_current(None, name="a")
        self.assertEqual([r["name"] for r in tablet_foundry.claim_orders(self._read())], ["b"])
        TR.write_current(None, name="b")
        self.assertFalse(os.path.exists(self.cur))

    def test_claim_orders_reads_the_old_single_order_claim_too(self):
        self.assertEqual(tablet_foundry.claim_orders({"name": "x"}), [{"name": "x"}])
        self.assertEqual(tablet_foundry.claim_orders({}), [])
        self.assertEqual(tablet_foundry.claim_orders(None), [])


class TileShowsEveryRunningOrder(unittest.TestCase):
    def setUp(self):
        self.sent = []
        for p in (mock.patch.dict(tablet_foundry._PUB, {"fleet": None, "rigby": None}),
                  mock.patch.object(tablet_foundry, "_post_run_state", side_effect=lambda **kw: self.sent.append(kw))):
            p.start()
            self.addCleanup(p.stop)

    def test_two_orders_one_line_and_an_ended_one_leaves(self):
        tablet_foundry.publish(name="a", state="running", step=1, total=2)
        tablet_foundry.publish(name="b", state="running", step=0, total=1)
        self.assertEqual(self.sent[-1]["name"], "2 orders: a + b")
        self.assertEqual((self.sent[-1]["step"], self.sent[-1]["total"]), (1, 3))
        tablet_foundry.publish(name="a", state="idle")
        self.assertEqual(self.sent[-1]["name"], "b")
        tablet_foundry.publish(name="b", state="idle")
        self.assertEqual(self.sent[-1]["state"], "idle")


class WatchdogWatchesEveryOrder(unittest.TestCase):
    def setUp(self):
        import tablet_watch as TW
        self.TW = TW
        TW._EXTRA.clear()
        self.addCleanup(TW._EXTRA.clear)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.cur = os.path.join(d, "CURRENT_ORDER.json")
        hb = os.path.join(d, "runner.heartbeat")
        with open(hb, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time() + 3600, "phase": "running"}, f)
        self.alerts = []
        for p in (mock.patch.object(TW, "CURRENT", self.cur), mock.patch.object(TW, "HEARTBEAT", hb),
                  mock.patch.object(TW, "STAND_DOWN", os.path.join(d, "absent")),
                  mock.patch.object(TW, "busy_beat", return_value=None),
                  mock.patch.object(TW, "alert", side_effect=lambda k, m, **kw: self.alerts.append((k, m))),
                  mock.patch.object(TW, "clear")):
            p.start()
            self.addCleanup(p.stop)

    def _claim(self, *rows):
        with open(self.cur, "w", encoding="utf-8") as f:
            json.dump(dict(rows[0], orders=list(rows)), f)

    def test_a_stalled_second_order_alerts_under_its_own_key(self):
        prog, motion = self.TW.Progress(), self.TW.WorkMotion()
        self._claim({"name": "a", "done": 1}, {"name": "b", "done": 5})
        self.TW.patrol(prog, self.TW.FleetReach(), motion)
        self.TW._EXTRA["b"][0].since -= self.TW.NO_PROGRESS_S + 5
        self._claim({"name": "a", "done": 2}, {"name": "b", "done": 5})
        ok, detail = self.TW.patrol(prog, self.TW.FleetReach(), motion)
        self.assertEqual([k for k, _ in self.alerts], ["stall-b"])
        self.assertIn("watching 'a'", detail)
        self.assertIn("ALERT: order 'b'", detail)


class BusyRigbyHandsAPieceToAFreeTablet(unittest.TestCase):
    def setUp(self):
        _reset_pool()
        self.addCleanup(_reset_pool)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "turn.tjob.json")
        spec = {"kind": "llm", "name": "turn", "payload": "/p/x.py", "tasks": ["t"], "replicates": 1,
                "placement": {"family": "chat-turn", "tier": "fellowship", "lens": "fastest"},
                "arms": ["rigby"], "model": "rigby.gguf", "ctx": 32768, "temperature": 0.7,
                "placement_row": {"box": "rigby", "model": "rigby.gguf", "skipped": []},
                "placement_fields": ["arms", "ctx", "model", "temperature"],
                "placement_options": [{"order": {"arms": ["warm"], "model": "tab-0.5b", "warm_nodes": 1,
                                                 "ctx": 16384},
                                       "row": {"box": "fellowship", "model": "tab-0.5b"}}]}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        self.pays = (True, "inside the band and faster")
        for p in (mock.patch.object(TR, "queued", return_value=[self.path]),
                  mock.patch.object(TR, "read_reservation", return_value=None),
                  mock.patch.object(TR, "_SCHED_TABLETS", {"h1": {}, "h2": {}}),
                  mock.patch.object(TR, "_move_pays", side_effect=lambda spec, opt, ahead: self.pays),
                  mock.patch.object(TR, "log")):
            p.start()
            self.addCleanup(p.stop)

    def _spec(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_idle_rigby_keeps_its_piece(self):
        self.assertEqual(TR.rebind_to_free_worker(), 0)
        self.assertEqual(self._spec()["arms"], ["rigby"])

    def test_busy_rigby_moves_the_queued_piece_to_the_tablets(self):
        with TR.RIGBY_LOCK:
            self.assertEqual(TR.rebind_to_free_worker(), 1)
        s = self._spec()
        self.assertEqual((s["arms"], s["model"], s["warm_nodes"], s["ctx"]), (["warm"], "tab-0.5b", 1, 16384))
        self.assertNotIn("temperature", s, "the rigby row's own fields go with it")
        self.assertEqual(s["placement_row"]["moved_from"]["box"], "rigby")
        self.assertEqual(s["placement_options"], [])
        self.assertEqual(TR.lane_of(self.path), "fleet", "it now runs in the fleet lane, on a tablet")

    def test_no_free_tablet_no_move(self):
        TR.take_nodes("other", ["h1", "h2"])
        with TR.RIGBY_LOCK:
            self.assertEqual(TR.rebind_to_free_worker(), 0)

    def test_the_piece_rigby_just_picked_is_never_moved(self):
        with mock.patch.object(TR, "RIGBY_RUNNING", ["turn"]), TR.RIGBY_LOCK:
            self.assertEqual(TR.rebind_to_free_worker(), 0)

    def test_a_move_that_does_not_pay_leaves_the_piece_with_rigby(self):
        self.pays = (False, "below the quality band")
        with TR.RIGBY_LOCK:
            self.assertEqual(TR.rebind_to_free_worker(), 0)
        self.assertEqual(self._spec()["arms"], ["rigby"])


class AMoveMustPayOnSpocksOwnWeighing(unittest.TestCase):
    """Fallback selection must prefer the lower-latency option even if slightly pricier, provided quality remains in-band."""

    SPEC = {"placement": {"family": "chat-turn", "tier": "fellowship", "lens": "fastest"},
            "placement_row": {"box": "rigby", "model": "r.gguf"}}
    OPT = {"row": {"box": "fellowship", "model": "t-0.5b"}}

    def _weigh(self, tablet_in_band, tablet_s, rigby_s=10.0):
        rig = {"box": "rigby", "entry": {"model": "r.gguf"}, "time_s": rigby_s}
        tab = {"box": "fellowship", "entry": {"model": "t-0.5b"}, "time_s": tablet_s}
        seen = {}

        def weigh(family, tier, interactive=True):
            seen.update(family=family, tier=tier, interactive=interactive)
            return ([rig, tab], []) if tablet_in_band else ([rig], [tab])
        return weigh, seen

    def test_below_the_quality_band_never_moves(self):
        weigh, seen = self._weigh(False, 1.0)
        ok, why = TR._move_pays(self.SPEC, self.OPT, 9, weigh=weigh)
        self.assertFalse(ok)
        self.assertIn("quality band", why)
        self.assertEqual(seen, {"family": "chat-turn", "tier": "fellowship", "interactive": True})

    def test_inside_the_band_but_not_faster_than_the_line_stays(self):
        weigh, _ = self._weigh(True, 26.0)                    # 0 ahead: wait = 2 x 10 s = 20 s < 26 s
        self.assertFalse(TR._move_pays(self.SPEC, self.OPT, 0, weigh=weigh)[0])

    def test_inside_the_band_and_faster_than_a_long_line_moves(self):
        weigh, _ = self._weigh(True, 26.0)                    # 2 ahead: wait = 4 x 10 s = 40 s > 26 s
        self.assertTrue(TR._move_pays(self.SPEC, self.OPT, 2, weigh=weigh)[0])

    def test_no_weighing_is_no_move(self):
        def weigh(*a, **k):
            raise OSError("table unreadable")
        self.assertFalse(TR._move_pays(self.SPEC, self.OPT, 9, weigh=weigh)[0])


if __name__ == "__main__":
    unittest.main()
