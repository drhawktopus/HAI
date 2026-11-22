"""rigby is a unit like each tablet: orders that need rigby alone run in their own lane beside the tablet work;
Voltron (rigby + all tablets) holds rigby too; slips and intake stay single-writer across both lanes."""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tablet_foundry as TF
import tablet_runner as TR
from test_tablet_slips import SlipTestBase

RIGBY = {"kind": "llm", "name": "r1", "model": "m.gguf", "payload": "/p.py", "tasks": ["t"], "arms": ["rigby"]}
POOLED = dict(RIGBY, name="v1", arms=["rigby", "voltron"])


def in_rigby_lane(fn):
    """Run fn in a thread marked as the rigby lane; return its result or re-raise its exception."""
    out = {}

    def body():
        TF.LANE.name = "rigby"
        try:
            out["r"] = fn()
        except BaseException as e:                                   # noqa: BLE001
            out["e"] = e
    t = threading.Thread(target=body)
    t.start()
    t.join(10)
    if "e" in out:
        raise out["e"]
    return out.get("r")


class FirstPickAfterTheSlipPass(unittest.TestCase):
    """The rigby lane must wait for the first slip pass before reading the queue, ensuring requeue slips are routed to the new taker before picking begins."""

    def tearDown(self):
        TR.FIRST_SLIP_PASS.set()

    def test_the_lane_does_not_read_the_queue_until_the_first_slip_pass(self):
        reads = []
        TR.FIRST_SLIP_PASS.clear()

        def queued():
            reads.append(1)
            raise SystemExit      # ends the lane thread here: no live loop outlives the mocks
        with mock.patch.object(TR, "queued", side_effect=queued), \
                mock.patch("tolkien_llm.publish_models"), \
                mock.patch.object(TR, "read_reservation", return_value=None):
            t = threading.Thread(target=TR.rigby_lane, args=(0.01,), daemon=True)
            t.start()
            t.join(0.3)
            self.assertEqual(reads, [], "picked before the slips were routed")
            self.assertTrue(t.is_alive())
            TR.FIRST_SLIP_PASS.set()
            t.join(5)
            self.assertFalse(t.is_alive())
        self.assertEqual(reads, [1], "the first pick comes right after the slip pass")


class Lanes(SlipTestBase):
    def test_only_rigby_alone_work_goes_to_the_rigby_lane(self):
        self.assertEqual(TR.lane_of(self.queue("r1", RIGBY)), "rigby")
        self.assertEqual(TR.lane_of(self.queue("v1", POOLED)), "fleet")          # Voltron needs the tablets
        self.assertEqual(TR.lane_of(self.queue("m1")), "fleet")
        self.assertEqual(TR.lane_of(self.queue("old", dict(RIGBY, kind="voltron", arms=["local"]))), "rigby")

    def test_an_unreadable_spec_goes_to_the_fleet_lane_which_holds_it_named(self):
        p = os.path.join(self.dirs["queue"], "bad.tjob.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(TR.lane_of(p), "fleet")


class TheGT730IsOneCard(SlipTestBase):
    """#36 C (10-14): a warm order may hold rigby's GT 730 as one of its nodes while rigby's lane runs beside it - so a
    rigby order placed ON the 730 waits for the card, and a 1060-only order does not."""

    def test_an_order_on_the_730_waits_and_a_1060_order_runs_beside_the_warm_node(self):
        import tolkien_llm as tv
        on_730 = dict(RIGBY, name="r730", gpu_layers=99)
        on_1060 = dict(RIGBY, name="r1060", gpu="gtx1060", fit_target=1024, build="cuda")
        self.assertTrue(tv.gt730_try_acquire("warm-order"))
        try:
            p730 = self.queue("r730", on_730)
            # both cards visible: this test is about the 730's claim (card visibility launches a binary - stubbed)
            with mock.patch.object(TR, "build_llm_order") as built, mock.patch.object(TR, "log"), \
                    mock.patch.object(tv, "card_usable", return_value=(True, "")):
                with self.assertRaises(TR.LaneBusy) as cm:
                    in_rigby_lane(lambda: TR.build_order(p730))
                built.assert_not_called()
                self.assertIn("warm-order", str(cm.exception))
                self.assertIn("r730.tjob.json", self.ls("queue"), "left queued untouched")
                p1060 = self.queue("r1060", on_1060)
                in_rigby_lane(lambda: TR.build_order(p1060))
                built.assert_called_once()
            self.assertEqual(TR.RIGBY_HOLDER[0], "", "rigby's claim released")
        finally:
            tv.gt730_release()
        self.assertTrue(tv.gt730_try_acquire("after"), "the 1060 order never took the 730")
        tv.gt730_release()


class ACardOffTheBusWaits(SlipTestBase):
    """A dropped card causes orders to wait queued and untouched (not fail or ship void).
    The desk receives exactly one DM per drop event, and the order executes automatically once the card is detected again."""

    def keeper(self, **rec):
        """card_keeper.json as rigby's host keeper writes it: a live heartbeat plus whatever decision fields are given."""
        import time
        with open(os.path.join(self.dirs["state"], TR.CARD_KEEPER), "w", encoding="utf-8") as f:
            json.dump(dict({"checked": time.time() - 10, "period_s": 120}, **rec), f)

    def test_a_dropped_card_waits_dms_once_per_drop_and_runs_when_it_is_back(self):
        import tolkien_llm as tv
        seen = [False]
        on_1060 = dict(RIGBY, name="r1060", gpu="gtx1060", fit_target=1024, build="cuda")
        p = self.queue("r1060", on_1060)
        self.keeper()
        TR._CARDS_DOWN.clear()
        with mock.patch.object(TR, "build_llm_order") as built, mock.patch.object(TR, "log"), \
                mock.patch.object(TR, "notify_desk") as dm, \
                mock.patch.object(tv, "card_usable",
                                  side_effect=lambda g, b: (True, "") if seen[0] else (False, "%s not visible" % g)):
            for _ in range(2):
                with self.assertRaises(TR.LaneBusy) as cm:
                    in_rigby_lane(lambda: TR.build_order(p))
                self.assertIn("gtx1060 not visible", str(cm.exception))
            built.assert_not_called()
            self.assertIn("r1060.tjob.json", self.ls("queue"), "left queued untouched - never held, never voided")
            self.assertEqual(dm.call_count, 1, "one DM per drop, not one per retry")
            self.assertIn("reboots rigby", dm.call_args[0][0])
            seen[0] = True                                         # rigby rebooted: the card is back
            in_rigby_lane(lambda: TR.build_order(p))
            built.assert_called_once()
            seen[0] = False                                        # a new drop after recovery is a new DM
            with self.assertRaises(TR.LaneBusy):
                in_rigby_lane(lambda: TR.build_order(self.queue("r1060b", dict(on_1060, name="r1060b"))))
            self.assertEqual(dm.call_count, 2)
        TR._CARDS_DOWN.clear()

    def test_the_keepers_marker_is_written_while_an_order_waits_and_removed_when_the_card_is_back(self):
        import json
        import tolkien_llm as tv
        seen = [False]
        on_1060 = dict(RIGBY, name="r1060", gpu="gtx1060", fit_target=1024, build="cuda")
        p = self.queue("r1060", on_1060)
        mark = os.path.join(self.dirs["state"], TR.CARD_WANTED)
        self.keeper()
        TR._CARDS_DOWN.clear()
        with mock.patch.object(TR, "build_llm_order"), mock.patch.object(TR, "log"), \
                mock.patch.object(TR, "notify_desk") as dm, \
                mock.patch.object(tv, "card_usable",
                                  side_effect=lambda g, b: (True, "") if seen[0] else (False, "%s not visible" % g)):
            with self.assertRaises(TR.LaneBusy):
                in_rigby_lane(lambda: TR.build_order(p))
            with open(mark, encoding="utf-8") as f:
                m = json.load(f)
            self.assertEqual((m["order"], m["cards"], m["period_s"]), ("r1060", ["gtx1060"], TR.LANE_BUSY_RETRY_S))
            since = m["since"]
            with self.assertRaises(TR.LaneBusy):
                in_rigby_lane(lambda: TR.build_order(p))
            with open(mark, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["since"], since, "a retry refreshes the marker, never restarts the drop")
            self.assertIn("reboots rigby once both lanes are idle", dm.call_args[0][0])
            seen[0] = True
            in_rigby_lane(lambda: TR.build_order(p))
            self.assertFalse(os.path.exists(mark), "the card is back: nothing is waiting on it")
        TR._CARDS_DOWN.clear()

    def test_mid_reboot_tolkien_waits_for_the_keepers_verdict_then_dms_its_reason_once(self):
        """After the keeper's reboot Tolkien is back before the keeper's first run, so the record still says "rebooting":
        no DM then (the keeper's verdict is one run away), one DM when it gives up - quoting the keeper's own reason."""
        import tolkien_llm as tv
        p = self.queue("r1060", dict(RIGBY, name="r1060", gpu="gtx1060", fit_target=1024, build="cuda"))
        TR._CARDS_DOWN.clear()
        self.keeper(rebooted_at=1759990000, rebooted_from_boot="boot-1")
        with mock.patch.object(TR, "build_llm_order"), mock.patch.object(TR, "log"), \
                mock.patch.object(TR, "notify_desk") as dm, \
                mock.patch.object(tv, "card_usable", return_value=(False, "gtx1060 not visible")):
            with self.assertRaises(TR.LaneBusy):
                in_rigby_lane(lambda: TR.build_order(p))
            dm.assert_not_called()
            self.keeper(rebooted_at=1759990000, rebooted_from_boot="boot-1", gave_up=1760000000,
                        gave_up_why="still missing in the boot after the keeper's reboot")
            for _ in range(2):
                with self.assertRaises(TR.LaneBusy):
                    in_rigby_lane(lambda: TR.build_order(p))
        self.assertEqual(dm.call_count, 1, "one DM for the keeper's verdict")
        self.assertIn("STILL MISSING", dm.call_args[0][0])
        self.assertIn("still missing in the boot after the keeper's reboot", dm.call_args[0][0])
        TR._CARDS_DOWN.clear()

    def test_a_silent_keeper_is_named_and_never_silences_the_dm(self):
        """A keeper that stopped running cannot say so: the DM reads its heartbeat - never "the keeper reboots rigby"
        when no keeper has checked in, and a keeper that died mid-reboot does not hold the DM back."""
        import time
        self.assertIn("SILENT (never checked in)", TR.keeper_line({}, time.time()))
        old = {"checked": time.time() - 400, "period_s": 120, "rebooted_at": 1759990000}
        self.assertIn("SILENT (last check 7 min ago)", TR.keeper_line(old, time.time()))
        self.assertEqual(TR.keeper_phase(old, time.time()), "drop", "a dead keeper mid-reboot must not silence the DM")
        live = dict(old, checked=time.time() - 30)
        self.assertEqual(TR.keeper_phase(live, time.time()), "rebooting")
        self.assertIn("reboots rigby", TR.keeper_line(live, time.time()))

    def test_an_order_that_uses_no_card_never_launches_a_lookup(self):
        import tolkien_llm as tv
        p = self.queue("rcpu", dict(RIGBY, name="rcpu"))
        with mock.patch.object(TR, "build_llm_order") as built, mock.patch.object(TR, "log"), \
                mock.patch.object(tv, "card_usable") as cu:
            in_rigby_lane(lambda: TR.build_order(p))
        cu.assert_not_called()
        built.assert_called_once()


class RigbyIsOneUnit(SlipTestBase):
    def test_a_second_claim_waits_and_names_the_holder(self):
        with TR.rigby_claim("r1"):
            with self.assertRaises(TR.LaneBusy) as cm:
                with TR.rigby_claim("v1"):
                    pass
        self.assertIn("r1", str(cm.exception))
        with TR.rigby_claim("v1"):                                   # released on the way out
            pass

    def test_the_claim_is_released_when_the_order_raises(self):
        with self.assertRaises(ValueError):
            with TR.rigby_claim("r1"):
                raise ValueError("boom")
        with TR.rigby_claim("r2"):
            pass

    def test_voltron_waits_while_the_rigby_lane_holds_rigby_and_is_never_held(self):
        path = self.queue("v1", POOLED)
        health = {h: {"healthy": True, "alive": True, "why": "ok"} for h in ("frodo", "sam")}
        with TR.rigby_claim("r1"), \
             mock.patch.object(TR, "fleet_health", return_value=health), \
             mock.patch.object(TR, "build_llm_order") as built, \
             mock.patch.object(TR, "log"):
            with self.assertRaises(TR.LaneBusy):
                TR.build_order(path)
        built.assert_not_called()
        self.assertIn("v1.tjob.json", self.ls("queue"))              # untouched, still queued

    def test_a_rigby_order_waits_while_voltron_holds_rigby(self):
        path = self.queue("r1", RIGBY)
        with TR.rigby_claim("v1"), mock.patch.object(TR, "build_llm_order") as built, \
             mock.patch.object(TR, "log"):
            with self.assertRaises(TR.LaneBusy):
                in_rigby_lane(lambda: TR.build_order(path))
        built.assert_not_called()


class DraftHostPlacement(SlipTestBase):
    """#36: a rigby order whose draft runs on one named tablet gets exactly that tablet, and waits without it."""
    DRAFTED = dict(RIGBY, name="d1", draft_model="s.gguf", draft_host="gandalf")

    def _build(self, healthy):
        path = self.queue("d1", self.DRAFTED)
        health = {h: {"healthy": h in healthy, "alive": True, "why": "ok"} for h in ("frodo", "gandalf")}
        with mock.patch.object(TR, "fleet_health", return_value=health), \
             mock.patch.object(TR, "try_repair", side_effect=lambda h: health[h]), \
             mock.patch.object(TR, "build_llm_order") as built, mock.patch.object(TR, "log"):
            try:
                TR.build_order(path)
            except TR.LaneBusy:
                pass
        return built

    def test_the_drafted_order_is_dealt_its_one_tablet(self):
        built = self._build({"frodo", "gandalf"})
        self.assertEqual(built.call_args[0][2], ["gandalf"])

    def test_a_class_b_drafter_is_judged_by_the_draft_model_not_the_target(self):
        spec = dict(self.DRAFTED, model="Qwen2.5-14B-Instruct-Q4_K_M.gguf",
                    draft_model="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf", draft_host="frodo")
        kept, dropped = TR._apply_class_rules("d1", spec, ["frodo"])
        self.assertEqual((kept, dropped), (["frodo"], []))
        self.assertTrue(TR._apply_class_rules("d1", dict(spec, draft_host=None), ["frodo"])[1],
                        "positive control: the same 14B on frodo as a non-drafted order is dropped")

    def test_the_drafted_order_waits_while_its_tablet_is_unhealthy(self):
        built = self._build({"frodo"})
        built.assert_not_called()
        self.assertIn("d1.tjob.json", self.ls("queue"))


class BenchPlacement(SlipTestBase):
    """#36 bench orders: rigby-only runs take rigby's lane; a run on a tablet waits until that tablet is healthy."""
    RIGBY_ONLY = {"kind": "bench", "name": "b1", "runs": [
        {"id": "r", "where": "rigby", "model": "m.gguf", "p": [512]}]}
    WITH_TABLET = {"kind": "bench", "name": "b2", "runs": [
        {"id": "r", "where": "rigby", "model": "m.gguf", "p": [512]},
        {"id": "g", "where": "gandalf", "link_mb": 64}]}

    def test_a_rigby_only_bench_runs_in_rigbys_lane(self):
        path = self.queue("b1", self.RIGBY_ONLY)
        self.assertEqual(TR.lane_of(path), "rigby")
        with mock.patch.object(TR, "build_bench_order") as built, \
             mock.patch.object(TR, "fleet_health") as health, mock.patch.object(TR, "log"):
            TR.build_order(path)
        built.assert_called_once()
        health.assert_not_called()

    def test_a_bench_on_an_unhealthy_tablet_waits(self):
        path = self.queue("b2", self.WITH_TABLET)
        health = {h: {"healthy": h != "gandalf", "alive": True, "why": "ok"} for h in ("frodo", "gandalf")}
        with mock.patch.object(TR, "fleet_health", return_value=health), \
             mock.patch.object(TR, "try_repair", side_effect=lambda h: health[h]), \
             mock.patch.object(TR, "build_bench_order") as built, mock.patch.object(TR, "log"):
            try:
                TR.build_order(path)
            except TR.LaneBusy:
                pass
        built.assert_not_called()
        self.assertIn("b2.tjob.json", self.ls("queue"))


class ClaimsPerLane(SlipTestBase):
    def test_each_lane_writes_and_clears_only_its_own_claim(self):
        TR.write_current({"name": "win.01"}, "window", 1, 4)
        in_rigby_lane(lambda: TR.write_current({"name": "r1"}, "llm-rigby", 2, 31))
        with open(TR.CURRENT, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["name"], "win.01")
        with open(TR.CURRENT_RIGBY, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["name"], "r1")
        in_rigby_lane(lambda: TR.write_current(None))                # rigby finishing never clears the fleet claim
        self.assertTrue(os.path.exists(TR.CURRENT))
        self.assertFalse(os.path.exists(TR.CURRENT_RIGBY))


class TileShowsBothLanes(unittest.TestCase):
    def setUp(self):
        self.sent = []
        p = [mock.patch.object(TF, "_post_run_state", side_effect=lambda **kw: self.sent.append(kw)),
             mock.patch.dict(TF._PUB, {"fleet": None, "rigby": None})]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def test_rigby_work_rides_beside_a_running_tablet_order(self):
        TF.publish(name="win.07", state="running", phase="window", step=3, total=12)
        in_rigby_lane(lambda: TF.publish(name="r1", state="running", phase="llm-rigby", step=5, total=31))
        last = self.sent[-1]
        self.assertEqual(last["name"], "win.07")
        self.assertIn("rigby: r1 5/31", last["phase"])

    def test_rigby_work_alone_is_shown_as_rigby(self):
        TF.publish(name="", state="idle", caps=["tablet_cpu"])
        in_rigby_lane(lambda: TF.publish(name="r1", state="running", phase="llm-rigby", step=5, total=31))
        last = self.sent[-1]
        self.assertEqual((last["name"], last["state"], last["step"]), ("rigby: r1", "running", 5))
        self.assertEqual(last["caps"], ["tablet_cpu"])               # the fleet's capabilities still ride along

    def test_rigby_going_idle_clears_its_part(self):
        TF.publish(name="win.07", state="running", phase="window", step=3, total=12)
        in_rigby_lane(lambda: TF.publish(name="r1", state="running", phase="llm-rigby", step=5, total=31))
        in_rigby_lane(lambda: TF.publish(state="idle"))
        self.assertNotIn("rigby", self.sent[-1]["phase"])


class SlipsAcrossLanes(SlipTestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(TR.StopWatch.ACTIVE.clear)

    def test_a_hold_never_treats_the_other_lanes_running_order_as_queued(self):
        self.queue("r1", RIGBY)                                      # running in the rigby lane
        self.slip("hold-r.ctl.json", {"action": "hold", "order": "r1"})
        TF.process_slips(running=[{"stem": "r1", "name": "r1"}, {"stem": "win.01", "name": "win.01"}])
        self.assertIn("r1.tjob.json", self.ls("queue"), "a running order's spec is never renamed by a hold")

    def test_a_stop_reaches_the_lane_whose_order_it_names(self):
        fleet = TR.StopWatch("win.01", "win.01")
        rig = TR.StopWatch("r1", "r1")
        TR.StopWatch.ACTIVE.update({"win.01": fleet, "r1": rig})
        self.slip("r1.stop.json", {"why": "enough"})
        with mock.patch.object(TR, "drain_inbox", return_value=0):
            fleet.check()                                            # routed from the OTHER lane's watch
        self.assertTrue(rig.event.is_set())
        self.assertFalse(fleet.event.is_set())

    def test_intake_happens_during_a_run_so_a_cancel_can_find_the_order(self):
        w = TR.StopWatch("win.01", "win.01")
        with open(os.path.join(self.dirs["inbox"], "late.tjob.json"), "w", encoding="utf-8") as f:
            json.dump(RIGBY, f)
        w.check()
        self.assertIn("late.tjob.json", self.ls("queue"))

    def test_intake_is_single_writer(self):
        seen = []
        with mock.patch.object(TF, "_drain_inbox", side_effect=lambda: seen.append(TF._INTAKE_LK.locked()) or 0):
            TF.drain_inbox()
        self.assertEqual(seen, [True])


class LlmOrderBeats(unittest.TestCase):
    def test_every_answer_and_the_model_load_write_the_heartbeat(self):
        import tolkien_llm as tv
        beats = []

        def fake_arm(arm, spec, mod, work, hosts, log, progress, stop_event=None, adb_sh=None, tick=None,
                     streaming=None):
            tick("loading 3s")
            streaming(arm, "t", 1, 250)
            progress(arm, 1, 2, {"task": "control_add", "replicate": 1, "ok": True, "ran": True, "arm": arm})
            return [{"arm": arm, "task": "control_add", "ok": True, "ran": True}], {"arm": arm}
        mod = mock.Mock(spec=["TASKS", "CONTROL_TASKS"], TASKS={"t": {}}, CONTROL_TASKS={"control_add": {}})
        with mock.patch.object(tv, "load_scorer", return_value=(mod, "/tmp")), \
             mock.patch.object(tv, "run_arm", side_effect=fake_arm), \
             mock.patch.object(TR, "beat", side_effect=beats.append), \
             mock.patch.object(TR, "retire_order", return_value="/r/x.json"), \
             mock.patch.object(TR, "write_current"), mock.patch.object(TR, "publish"), \
             mock.patch.object(TR, "notify_desk"), mock.patch.object(TR, "log"):
            TR.build_llm_order("/q/r1.tjob.json", RIGBY, [], adb_sh=lambda *a, **k: "")
        self.assertTrue(any("loading" in b for b in beats), beats)
        self.assertTrue(any("1/2" in b for b in beats), beats)
        self.assertTrue(any("250 tokens" in b for b in beats), "an answer in flight beats too: %s" % beats)


class RetireClearsOnlyItsOwnStaging(unittest.TestCase):
    """Retire must only remove staging dirs belonging to the retiring lane; it must not touch dirs owned by other active windows."""

    def setUp(self):
        import shutil
        import tempfile
        import tablet_fulfil as TFU
        self.TFU = TFU
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.stages = ["stage_hardbench-rigby-8.20260928-abc-def-rigby",
                       "stage_cal.win.05.arm-29a06992-e46b0e29-sam",
                       "stage_cal.win.1.arm-1111-2222-frodo",
                       "stage_cal.win.10.arm-3333-4444-gimli"]
        for s in self.stages:
            os.makedirs(os.path.join(self.d, s))

    def left(self):
        return sorted(os.listdir(self.d))

    def test_a_finishing_rigby_order_leaves_a_running_windows_staging_alone(self):
        self.assertEqual(self.TFU.clear_stage_dirs(self.d, "hardbench-rigby-8.20260928"), 1)
        self.assertIn("stage_cal.win.05.arm-29a06992-e46b0e29-sam", self.left())

    def test_a_window_clears_its_arms_staging(self):
        self.assertEqual(self.TFU.clear_stage_dirs(self.d, "cal.win.05"), 1)
        self.assertNotIn("stage_cal.win.05.arm-29a06992-e46b0e29-sam", self.left())

    def test_win_1_never_clears_win_10(self):
        self.TFU.clear_stage_dirs(self.d, "cal.win.1")
        self.assertIn("stage_cal.win.10.arm-3333-4444-gimli", self.left())
        self.assertNotIn("stage_cal.win.1.arm-1111-2222-frodo", self.left())

    def test_the_name_rule_matches_the_one_do_batch_uses(self):
        # one derivation: a staging dir is named from _safe_stage_name, and clearing matches on the same function
        long_name = "x" * 90 + ".arm"
        self.assertEqual(self.TFU._safe_stage_name(long_name), "x" * 80)
        os.makedirs(os.path.join(self.d, "stage_%s-aaaa-bbbb-sam" % ("x" * 80)))
        self.assertEqual(self.TFU.clear_stage_dirs(self.d, "x" * 90), 1)


class WatchdogWatchesTheRigbyLane(unittest.TestCase):
    def setUp(self):
        import tempfile
        import tablet_watch as TW
        self.TW = TW
        self.claim = os.path.join(tempfile.mkdtemp(), "cur.rigby.json")
        self.alerts = []
        for p in (mock.patch.object(TW, "CURRENT_RIGBY", self.claim),
                  mock.patch.object(TW, "alert", side_effect=lambda k, m, **kw: self.alerts.append(k)),
                  mock.patch.object(TW, "clear")):
            p.start()
            self.addCleanup(p.stop)

    def _claim(self, name, done, work=None):
        with open(self.claim, "w", encoding="utf-8") as f:
            json.dump({"name": name, "done": done, "total": 31, "work": work}, f)

    def test_a_long_answer_still_streaming_is_advancing_not_frozen(self):
        prog = self.TW.Progress()
        self._claim("r1", 1, 400)
        self.TW.patrol_rigby(prog)
        prog.since -= self.TW.NO_PROGRESS_S + 5
        self._claim("r1", 1, 28935)
        self.assertIn("advancing", self.TW.patrol_rigby(prog))
        self.assertEqual(self.alerts, [])

    def test_an_answer_whose_tokens_stopped_arriving_is_frozen(self):
        prog = self.TW.Progress()
        self._claim("r1", 1, 28935)
        self.TW.patrol_rigby(prog)
        prog.since -= self.TW.NO_PROGRESS_S + 5
        self.assertIn("ALERT", self.TW.patrol_rigby(prog))
        self.assertEqual(self.alerts, ["stall-rigby"])

    def test_no_rigby_order_says_nothing(self):
        self.assertEqual(self.TW.patrol_rigby(self.TW.Progress()), "")

    def test_a_chat_window_waiting_for_its_next_turn_is_not_stalled(self):
        """Ensure `WINDOW_WAITING` status is not flagged as a stall, preventing false positives for active windows."""
        import tablet_foundry as tf
        prog = self.TW.Progress()
        with open(self.claim, "w", encoding="utf-8") as f:
            json.dump({"name": "hai-win-rigby-x", "phase": tf.WINDOW_WAITING, "done": 7, "total": 0, "work": None}, f)
        self.TW.patrol_rigby(prog)
        prog.since = (prog.since or 0) - self.TW.NO_PROGRESS_S - 5
        self.assertIn("waiting for a turn", self.TW.patrol_rigby(prog))
        self.assertEqual(self.alerts, [])

    def test_a_chat_window_answering_is_judged_like_any_answer(self):
        """The waiting exemption is narrow: a turn in flight whose stream stops still alerts (class 12a)."""
        prog = self.TW.Progress()
        with open(self.claim, "w", encoding="utf-8") as f:
            json.dump({"name": "hai-win-rigby-x", "phase": "window", "done": 7, "total": 0, "work": 500}, f)
        self.TW.patrol_rigby(prog)
        prog.since -= self.TW.NO_PROGRESS_S + 5
        self.assertIn("ALERT", self.TW.patrol_rigby(prog))
        self.assertEqual(self.alerts, ["stall-rigby"])

    def test_an_advancing_rigby_order_is_watched(self):
        prog = self.TW.Progress()
        self._claim("r1", 3)
        self.TW.patrol_rigby(prog)
        self._claim("r1", 4)
        self.assertIn("advancing", self.TW.patrol_rigby(prog))
        self.assertEqual(self.alerts, [])

    def test_a_frozen_rigby_order_alerts_on_its_own_key(self):
        prog = self.TW.Progress()
        self._claim("r1", 3)
        self.TW.patrol_rigby(prog)
        prog.since -= self.TW.NO_PROGRESS_S + 5
        self.assertIn("ALERT", self.TW.patrol_rigby(prog))
        self.assertEqual(self.alerts, ["stall-rigby"])


class InheritedClaims(unittest.TestCase):
    """Both lane claims are cleared (and logged) at startup because holding the singleton lock implies no previous order can run."""

    def test_both_lane_claims_are_cleared_and_logged(self):
        import tempfile
        d = tempfile.mkdtemp()
        fleet, rigby = os.path.join(d, "CURRENT_ORDER.json"), os.path.join(d, "CURRENT_ORDER.rigby.json")
        for p in (fleet, rigby):
            with open(p, "w") as f:
                json.dump({"name": "gone-order", "done": 0, "total": 35}, f)
        logs = []
        with mock.patch.object(TR, "CURRENT", fleet), mock.patch.object(TR, "CURRENT_RIGBY", rigby), \
             mock.patch.object(TR, "log", logs.append):
            TR.clear_inherited_claims()
        self.assertFalse(os.path.exists(fleet) or os.path.exists(rigby))
        self.assertEqual(len(logs), 2)
        self.assertTrue(all("gone-order" in ln for ln in logs), logs)

    def test_no_claims_is_quiet(self):
        import tempfile
        d = tempfile.mkdtemp()
        logs = []
        with mock.patch.object(TR, "CURRENT", os.path.join(d, "a")), \
             mock.patch.object(TR, "CURRENT_RIGBY", os.path.join(d, "b")), mock.patch.object(TR, "log", logs.append):
            TR.clear_inherited_claims()
        self.assertEqual(logs, [])


if __name__ == "__main__":
    unittest.main()
