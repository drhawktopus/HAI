"""Verify lossless HAI restart semantics: the engine persists state in the stream file, so the new process adopts the existing window without re-restoration or duplication. Ensures no window loss, correct adoption, and bit-identical continuation of in-progress turns."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import chat_window as CW  # noqa: E402
import config  # noqa: E402
import ollama_client  # noqa: E402
import run_ledger  # noqa: E402

HOST, MODEL, CTX = "bighonker", "qwen3.5:9b", 19968
URL = "http://10.0.0.167:11434"
CHUNKS = [{"message": {"content": "hel"}, "done": False}, {"message": {"content": "lo"}, "done": True, "done_reason": "stop"}]


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        for p in (mock.patch.object(config, "CHAT_WINDOWS_DIR", self.d),
                  mock.patch.object(config, "STATE_DIR", self.d),
                  mock.patch.object(config, "CHAT_WINDOWS_UNC", r"\\share\chat-windows"),
                  mock.patch.object(CW, "_ensure_beat", lambda: None),
                  mock.patch.object(CW, "_host_for", lambda url: HOST),
                  mock.patch.object(CW.Window, "desk_status", lambda self: "running"),
                  mock.patch.object(CW.Window, "ended", lambda self: os.path.exists(os.path.join(self.dir, CW.CLOSE))),
                  mock.patch("scheduler.window_ctx", lambda host, model, n: (n, "measured fit before load")),
                  mock.patch.object(CW, "await_card_freed", lambda *a, **k: True),
                  mock.patch.dict(CW._WINDOWS, clear=True),
                  mock.patch.dict(CW._HOLD, {"on": False})):
            p.start()
            self.addCleanup(p.stop)
        self.orders = []

    def intake(self, spec, requires, host):
        self.orders.append((spec, requires, host))
        return {"ok": True, "steps": [{"host": host, "id": "desk%d" % len(self.orders), "order": spec["name"]}]}

    def body(self, text="hi"):
        return {"model": MODEL, "messages": [{"role": "user", "content": text}], "stream": True, "think": True,
                "keep_alive": "5m", "options": {"num_ctx": CTX}}

    def answer_next(self, w, lines=CHUNKS, delay=0.01, finish=True):
        def run():
            for _ in range(300):
                names = sorted(f[:-len(CW.ITEM)] for f in os.listdir(w.dir) if f.endswith(CW.ITEM)
                               and not os.path.exists(os.path.join(w.dir, f[:-len(CW.ITEM)] + CW.OUT)))
                if names:
                    self.write_answer(w, names[-1], lines, delay, finish)
                    return
                time.sleep(0.01)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    @staticmethod
    def write_answer(w, iid, lines=CHUNKS, delay=0.0, finish=True):
        with open(os.path.join(w.dir, iid + CW.STREAM), "w", encoding="utf-8") as f:
            f.write(json.dumps({"attempt": "a1", "id": iid}) + "\n")
            for ln in lines:
                f.write(json.dumps(ln) + "\n")
                f.flush()
                time.sleep(delay)
        if finish:
            with open(os.path.join(w.dir, iid + CW.OUT), "w", encoding="utf-8") as f:
                json.dump({"id": iid, "answer": "hello", "error": ""}, f)

    def serve_turn(self, run="run:r1", text="hi"):
        """One real turn through the door, its answer written by a stand-in window -> the Window."""
        with CW.open_stream(URL + "/api/chat", self.body(text), None, run, intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.answer_next(w)
            [x for x in resp.iter_lines() if x]
        return w

    def restart(self):
        """A new Kirk process: it holds nothing in memory; what is on the share and at the desk stays."""
        CW._WINDOWS.clear()
        CW._HOLD["on"] = False

    def items(self, w):
        return sorted(f for f in os.listdir(w.dir) if f.endswith(CW.ITEM))


class TheWindowSaysWhatItIs(_Base):
    def test_identity_and_runs_are_on_the_share(self):
        w = self.serve_turn()
        with open(os.path.join(w.dir, CW.WINDOW), encoding="utf-8") as f:
            who = json.load(f)
        self.assertEqual((who["host"], who["model"], who["num_ctx"], who["desk_id"], who["name"]),
                         (HOST, MODEL, CTX, "desk1", w.name))
        with open(os.path.join(w.dir, CW.OWNER), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["users"], ["run:r1"])

    def test_a_parked_deploy_declares_the_longer_silence_at_once_and_a_called_off_one_takes_it_back(self):
        w = self.serve_turn()

        def period():
            with open(os.path.join(w.dir, CW.OWNER), encoding="utf-8") as f:
                return json.load(f)["period"]
        self.assertEqual(period(), CW.PERIOD_S)
        CW.deploy_hold(True)
        self.assertEqual(period(), CW.HOLD_PERIOD_S)
        CW.deploy_hold(False)
        self.assertEqual(period(), CW.PERIOD_S)


class StartupPicksThemBackUp(_Base):
    def test_the_same_window_comes_back_with_its_run(self):
        w = self.serve_turn()
        self.restart()
        self.assertEqual(CW.adopt_windows({"run:r1"}), [w.name])
        back = CW._WINDOWS[(HOST, MODEL, CTX)]
        self.assertEqual((back.name, back.desk_id, back.users, back.n), (w.name, "desk1", {"run:r1"}, 1))
        self.assertFalse(os.path.exists(os.path.join(w.dir, CW.IDLE)), "a run is coming back: the window is not idle")

    def test_a_plain_restart_announces_itself_and_46_seconds_of_silence_is_picked_up(self):
        """Ensure a plain restart is detected even when silence exceeds the limit by 1s due to timing drift."""
        w = self.serve_turn()

        def owner():
            with open(os.path.join(w.dir, CW.OWNER), encoding="utf-8") as f:
                return json.load(f)
        self.assertEqual(owner()["period"], CW.PERIOD_S)
        last = owner()["t"]
        self.restart()
        self.assertEqual(CW.adopt_windows({"run:r1"}, clock=lambda: last + 46), [],
                         "positive control: without the announcement this silence loses the window")
        CW._WINDOWS[(HOST, MODEL, CTX)] = w
        CW.going_down()
        self.assertEqual(owner()["period"], CW.HOLD_PERIOD_S)
        last = owner()["t"]
        self.restart()
        self.assertEqual(CW.adopt_windows({"run:r1"}, clock=lambda: last + 46), [w.name])

    def test_kirk_announces_on_every_shutdown(self):
        self.assertIn(app._announce_restart_to_windows, app.app.router.on_shutdown)
        with mock.patch.object(CW, "going_down") as going:
            app._announce_restart_to_windows()
        going.assert_called_once_with()

    def test_its_next_turn_is_served_by_that_window_with_no_second_order(self):
        w = self.serve_turn()
        self.restart()
        CW.adopt_windows({"run:r1"})
        self.serve_turn(text="next")
        self.assertEqual(len(self.orders), 1, "no new window was opened")
        self.assertEqual(len(self.items(w)), 2)

    def test_a_window_nobody_comes_back_to_goes_idle_and_its_unfinished_turn_ends(self):
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:gone", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            iid = self.items(w)[0][:-len(CW.ITEM)]
            self.write_answer(w, iid)
            [x for x in resp.iter_lines() if x]
        with open(os.path.join(w.dir, "t00002-x" + CW.ITEM), "w", encoding="utf-8") as f:
            json.dump({"model": MODEL, "messages": []}, f)
        self.restart()
        self.assertEqual(CW.adopt_windows(set()), [w.name])
        self.assertEqual(CW._WINDOWS[(HOST, MODEL, CTX)].users, set())
        self.assertTrue(os.path.exists(os.path.join(w.dir, CW.IDLE)))
        self.assertTrue(os.path.exists(os.path.join(w.dir, "t00002-x" + CW.CANCEL)))
        self.assertFalse(os.path.exists(os.path.join(w.dir, iid + CW.CANCEL)), "a finished turn is left alone")

    def test_what_is_not_picked_up(self):
        closed = self.serve_turn()
        open(os.path.join(closed.dir, CW.CLOSE), "w").close()
        self.restart()
        self.assertEqual(CW.adopt_windows({"run:r1"}), [], "a closed window")
        self.setUp_second_window("no-identity", identity=False)
        self.assertEqual(CW.adopt_windows({"run:r1"}), [], "no WINDOW.json: opened before this rule, the close path has it")
        self.setUp_second_window("silent", silent_for=CW.PERIOD_S * 3 + 5)
        self.assertEqual(CW.adopt_windows({"run:r1"}), [], "its owner is past the window's own limit: it ended itself")

    def setUp_second_window(self, tail, identity=True, silent_for=0.0):
        d = os.path.join(self.d, "hai-win-%s" % tail)
        os.makedirs(d)
        if identity:
            CW._write_json(os.path.join(d, CW.WINDOW), {"host": HOST, "model": MODEL, "num_ctx": CTX, "desk_id": "d9",
                                                        "strata": False, "opened": 1.0})
        CW._write_json(os.path.join(d, CW.OWNER), {"run": "kirk:x", "period": CW.PERIOD_S, "t": time.time() - silent_for,
                                                   "users": ["run:r1"]})

    def test_a_window_held_for_a_parked_deploy_outlives_a_gap_the_normal_beat_would_not(self):
        w = self.serve_turn()
        CW.deploy_hold(True)
        self.restart()
        later = time.time() + CW.PERIOD_S * 3 + 5
        self.assertEqual(CW.adopt_windows({"run:r1"}, clock=lambda: later), [w.name])

    def test_of_two_for_one_key_the_newest_wins(self):
        self.setUp_second_window("20261104-100000-aaaa")
        self.setUp_second_window("20261104-110000-bbbb")
        got = CW.adopt_windows({"run:r1"})
        self.assertEqual(got, ["hai-win-20261104-110000-bbbb"])

    def test_a_folder_it_cannot_read_never_stops_the_others(self):
        self.setUp_second_window("20261104-110000-good")
        d = os.path.join(self.d, "hai-win-20261104-120000-bad")
        os.makedirs(d)
        with open(os.path.join(d, CW.WINDOW), "w") as f:
            f.write("{not json")
        with open(os.path.join(d, CW.OWNER), "w") as f:
            f.write("{}")
        self.assertEqual(CW.adopt_windows({"run:r1"}), ["hai-win-20261104-110000-good"])

    def test_an_unreadable_window_folder_is_said_not_raised(self):
        with mock.patch.object(config, "CHAT_WINDOWS_DIR", os.path.join(self.d, "absent")):
            self.assertEqual(CW.adopt_windows({"run:r1"}), [])


class ARunGoesBackToItsOwn(_Base):
    def test_once_and_only_for_its_run(self):
        w = self.serve_turn()
        self.restart()
        CW.adopt_windows({"run:r1"})
        self.assertIsNone(CW.take_resumed("run:other"))
        self.assertIsNone(CW.take_resumed(None))
        self.assertEqual(CW.take_resumed("run:r1").name, w.name)
        self.assertIsNone(CW.take_resumed("run:r1"), "it is a first-turn decision, not a standing one")

    def test_a_window_that_ended_meanwhile_is_not_returned(self):
        w = self.serve_turn()
        self.restart()
        CW.adopt_windows({"run:r1"})
        open(os.path.join(w.dir, CW.CLOSE), "w").close()
        self.assertIsNone(CW.take_resumed("run:r1"))

    def test_the_serve_path_asks_before_any_triage(self):
        """Source-level pin for a seam with no cheap behavioural harness (the whole turn; test_rigby_window's precedent)."""
        import inspect
        src = inspect.getsource(app._run_query_core_body)
        self.assertLess(src.index("chat_window.take_resumed("), src.index("_mccoy_serve("))
        self.assertIn('"resumed on its own window"', src)

    def test_a_lead_window_is_the_standing_model_or_the_crown_on_mothership_only(self):
        crown = "qwen3.8-flash-next-strata-q2_0"
        with mock.patch.object(ollama_client, "lead_model", lambda host, model, job: crown):
            self.assertTrue(app._is_lead_window(mock.Mock(host="mothership", model=crown)))
            self.assertTrue(app._is_lead_window(mock.Mock(host="mothership", model=config.HEAVY_MODEL)))
            self.assertFalse(app._is_lead_window(mock.Mock(host="mothership", model="nemotron-3-nano:30b-iq4xs")))
            self.assertFalse(app._is_lead_window(mock.Mock(host="bighonker", model=crown)))

    def test_any_other_window_becomes_a_row_with_its_own_size(self):
        with mock.patch("row_pins.resolve", lambda rid: None):
            row = app._window_row(mock.Mock(host="rigby", model="m.gguf", num_ctx=32768))
            self.assertEqual((row["box"], row["model"], row["order"]["ctx"], row["order"]["model"]),
                             ("rigby", "m.gguf", 32768, "m.gguf"))
            self.assertNotIn("order", app._window_row(mock.Mock(host="bighonker", model="m", num_ctx=1)))

    def test_startup_order_is_claims_then_windows_then_resumes(self):
        src = open(app.__file__, encoding="utf-8").read()
        tail = src[src.rindex('if not os.getenv("HAI_NOTES_DIR"):'):]
        self.assertLess(tail.index("_release_previous_kirks_job_claims()"), tail.index("_pick_up_windows()"))
        self.assertLess(tail.index("_pick_up_windows()"), tail.index("_resume_interrupted_runs"))

    def test_startup_names_the_runs_that_are_really_coming_back(self):
        got = {}
        rows = [(mock.Mock(run_id="a"), {}), (mock.Mock(run_id="b"), {"suspended": True})]
        with mock.patch.object(app.run_ledger, "interrupted", lambda: rows), \
                mock.patch.object(CW, "adopt_windows", side_effect=lambda r: got.setdefault("r", r)):
            app._pick_up_windows()
        self.assertEqual(got["r"], {"run:a"}, "a suspended run waits on disk: it is not coming back now")

    def test_park_tells_the_windows(self):
        seen = []
        with mock.patch.object(ollama_client, "park_runs", lambda on: None), \
                mock.patch.object(app, "_deploy_park_mark", lambda on: None), \
                mock.patch.object(CW, "deploy_hold", seen.append), \
                mock.patch.object(app, "runs_parked", lambda: {}):
            app.runs_park(app.RunParkReq(user=sorted(config.ADMIN_USERS)[0], on=True))
        self.assertEqual(seen, [True])


class ATurnStillBeingWrittenIsFollowed(_Base):
    def adopted_with_item(self, text="hi", answered=False):
        """A window with the run's turn on it, as a restart found it."""
        with CW.open_stream(URL + "/api/chat", self.body(text), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            iid = self.items(w)[0][:-len(CW.ITEM)]
            resp.stream.finished = True          # the old process died: no cancel from it
        if answered:
            self.write_answer(w, iid)
        self.restart()
        CW.adopt_windows({"run:r1"})
        return w, iid

    def test_the_same_turn_reads_the_answer_already_being_written_with_no_second_item(self):
        w, iid = self.adopted_with_item()
        t = threading.Thread(target=self.write_answer, args=(w, iid, CHUNKS, 0.02), daemon=True)
        t.start()
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            got = [json.loads(x) for x in resp.iter_lines() if x]
        self.assertEqual(got, CHUNKS, "the bytes the model wrote, from the start")
        self.assertEqual(len(self.items(w)), 1, "not asked twice")
        self.assertFalse(os.path.exists(os.path.join(w.dir, iid + CW.CANCEL)))

    def test_a_turn_answered_while_kirk_was_away_is_read_not_asked_again(self):
        w, iid = self.adopted_with_item(answered=True)
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            got = [json.loads(x) for x in resp.iter_lines() if x]
        self.assertEqual(got, CHUNKS)
        self.assertEqual(len(self.items(w)), 1)

    def test_a_different_turn_is_asked_and_the_old_unfinished_one_ends(self):
        w, iid = self.adopted_with_item()
        with CW.open_stream(URL + "/api/chat", self.body("another question"), None, "run:r1", intake=self.intake) as resp:
            self.answer_next(w)
            got = [json.loads(x) for x in resp.iter_lines() if x]
        self.assertEqual(got, CHUNKS)
        self.assertEqual(len(self.items(w)), 2)
        self.assertTrue(os.path.exists(os.path.join(w.dir, iid + CW.CANCEL)), "nobody is coming back for it")

    def test_only_the_first_call_of_a_returning_run_claims(self):
        w, iid = self.adopted_with_item(answered=True)
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            [x for x in resp.iter_lines() if x]
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            self.answer_next(w)
            [x for x in resp.iter_lines() if x]
        self.assertEqual(len(self.items(w)), 2, "the same words asked later are a new turn")

    def test_a_turn_of_another_returning_run_is_left_for_it(self):
        with CW.open_stream(URL + "/api/chat", self.body("mine"), None, "run:r1", intake=self.intake) as a:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            a.stream.finished = True
        with CW.open_stream(URL + "/api/chat", self.body("theirs"), None, "run:r2", intake=self.intake) as b:
            b.stream.finished = True
        items = self.items(w)
        self.restart()
        CW.adopt_windows({"run:r1", "run:r2"})
        first = items[0][:-len(CW.ITEM)]
        second = items[1][:-len(CW.ITEM)]
        self.write_answer(w, first)
        with CW.open_stream(URL + "/api/chat", self.body("mine"), None, "run:r1", intake=self.intake) as resp:
            [x for x in resp.iter_lines() if x]
        self.assertFalse(os.path.exists(os.path.join(w.dir, second + CW.CANCEL)), "run:r2 has not looked yet")
        self.write_answer(w, second)
        with CW.open_stream(URL + "/api/chat", self.body("theirs"), None, "run:r2", intake=self.intake) as resp:
            self.assertEqual([json.loads(x) for x in resp.iter_lines() if x], CHUNKS)
        self.assertEqual(len(self.items(w)), 2)


class ATurnCanBeParkedMidAnswer(_Base):
    """A turn can be parked: the window cuts it, saves state, and reports `cut`. The caller treats `cut` as parked (not failed) and retries the same turn."""
    CONV = "ClaudeSupervisor/c1"

    def write_parked(self, w, iid, saved=True, mid_answer=True):
        with open(os.path.join(w.dir, iid + CW.STREAM), "w", encoding="utf-8") as f:
            f.write(json.dumps({"attempt": "a1", "id": iid}) + "\n" + json.dumps(CHUNKS[0]) + "\n")
        with open(os.path.join(w.dir, iid + CW.OUT), "w", encoding="utf-8") as f:
            json.dump({"id": iid, "answer": "", "error": "", "cut": "parked by its caller", "cut_saved": saved,
                       "cut_mid_answer": mid_answer, "stream_chunks": 1}, f)

    def parked_turn(self, run="run:r1", mid_answer=True, **kw):
        """One turn the stand-in window parks after its first chunk -> (the Window, the item id, the error raised)."""
        with self.assertRaises(CW.TurnParked) as cm:
            with CW.open_stream(URL + "/api/chat", self.body(), None, run, intake=self.intake, **kw) as resp:
                w = CW._WINDOWS[(HOST, MODEL, CTX)]
                iid = self.items(w)[-1][:-len(CW.ITEM)]
                self.write_parked(w, iid, mid_answer=mid_answer)
                [x for x in resp.iter_lines() if x]
        return w, iid, cm.exception

    def test_a_parked_row_is_read_as_parked_not_as_a_failure_or_an_answer(self):
        w, iid, exc = self.parked_turn()
        self.assertTrue(exc.parked)
        self.assertIsInstance(exc, CW.httpx.ReadError, "a caller that does not know it still sees a cut read")
        self.assertIn("the answer continues", str(exc))
        self.assertFalse(os.path.exists(os.path.join(w.dir, iid + CW.CANCEL)), "the turn is already over: no Stop is sent")

    def test_a_park_with_no_pending_token_says_the_answer_is_lost(self):
        _w, _iid, exc = self.parked_turn(mid_answer=False)
        self.assertIn("starts over", str(exc))
        self.assertNotIn("continues", str(exc))

    def test_the_parked_chats_state_is_recorded_where_it_was_saved(self):
        self.parked_turn(conv=self.CONV)
        self.assertEqual(CW.conv_home(self.CONV), (HOST, MODEL, CTX), "the run that comes back is placed toward this box")

    def test_a_suspend_parks_the_turn_the_run_is_reading_and_only_that(self):
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            iid = self.items(w)[0][:-len(CW.ITEM)]
            self.assertIsNone(CW.park_turn("run:other"), "another run has no turn in flight here")
            self.assertEqual(CW.park_turn("run:r1"), w.name)
            self.assertTrue(os.path.exists(os.path.join(w.dir, iid + CW.CUT)))
            resp.stream.finished = True
        self.assertIsNone(CW.park_turn("run:r1"), "between turns there is nothing to park: it stops at its next natural point")

    def test_a_park_asked_before_the_window_started_the_turn_takes_it_back_at_once(self):
        with self.assertRaises(CW.TurnParked) as cm:
            with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake, conv=self.CONV) as resp:
                w = CW._WINDOWS[(HOST, MODEL, CTX)]
                iid = self.items(w)[-1][:-len(CW.ITEM)]
                self.assertEqual(CW.park_turn("run:r1"), w.name)
                [x for x in resp.iter_lines() if x]              # nothing was ever written for this turn
        self.assertTrue(cm.exception.parked, "the chat loop treats it as a park: a suspended run stops here")
        self.assertIn("had not started", str(cm.exception))
        self.assertTrue(os.path.exists(os.path.join(w.dir, iid + CW.CANCEL)),
                        "the window answers a cancelled item without the engine or the chat's state when it reaches it")
        self.assertIsNone(CW.conv_home(self.CONV), "no state of this chat was saved in that window: its home does not move")

    def test_a_park_asked_after_the_window_started_the_turn_waits_for_the_windows_own_cut(self):
        with self.assertRaises(CW.TurnParked) as cm:
            with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake, conv=self.CONV) as resp:
                w = CW._WINDOWS[(HOST, MODEL, CTX)]
                iid = self.items(w)[-1][:-len(CW.ITEM)]
                self.write_parked(w, iid)                         # the window had started it and cut it mid-answer
                self.assertEqual(CW.park_turn("run:r1"), w.name)
                [x for x in resp.iter_lines() if x]
        self.assertIn("the answer continues", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(w.dir, iid + CW.CANCEL)), "a turn the window cut keeps its state: no Stop")
        self.assertEqual(CW.conv_home(self.CONV), (HOST, MODEL, CTX))

    def test_the_factory_may_park_only_a_turn_whose_caller_asks_it_again(self):
        def item_of(**kw):
            with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake, **kw) as resp:
                w = CW._WINDOWS[(HOST, MODEL, CTX)]
                resp.stream.finished = True
                with open(os.path.join(w.dir, self.items(w)[-1]), encoding="utf-8") as f:
                    return json.load(f)
        self.assertTrue(item_of(conv=self.CONV, cut_ok=True).get("cut_ok"))
        self.assertNotIn("cut_ok", item_of(conv=self.CONV), "a caller that would not ask again: the turn finishes")
        self.assertNotIn("cut_ok", item_of(cut_ok=True), "no chat state is kept for a call outside a chat")

    def test_a_parked_turn_is_asked_again_after_a_restart_never_reconnected_to(self):
        w, iid, _exc = self.parked_turn()
        self.restart()
        CW.adopt_windows({"run:r1"})
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            self.answer_next(w)
            got = [json.loads(x) for x in resp.iter_lines() if x]
        self.assertEqual(got, CHUNKS)
        self.assertEqual(len(self.items(w)), 2, "the same turn is a NEW item: the parked one's row is not an answer")


class TwoRunsBackOnOneWindow(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def item(self, iid, text):
        with open(os.path.join(self.dir, iid + CW.ITEM), "w", encoding="utf-8") as f:
            json.dump({"model": "m", "messages": [{"role": "user", "content": text}]}, f)

    def test_the_last_run_back_keeps_what_the_first_reconnected_to(self):
        for iid, text in (("t1", "A"), ("t2", "B"), ("t3", "nobody's")):
            self.item(iid, text)
        w = types.SimpleNamespace(dir=self.dir, name="w", to_claim={"run:a", "run:b"}, returning=2, claimed=set())
        self.assertEqual(CW._claim_item(w, {"model": "m", "messages": [{"role": "user", "content": "A"}]}, "run:a"), "t1")
        self.assertEqual(CW._claim_item(w, {"model": "m", "messages": [{"role": "user", "content": "B"}]}, "run:b"), "t2")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "t1" + CW.CANCEL)), "the first run's turn is kept")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "t2" + CW.CANCEL)))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "t3" + CW.CANCEL)), "a turn nobody came back for is still cancelled")


class TheToolListComesThrough(unittest.TestCase):
    TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("a", "b", "c", "d")]

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        p = mock.patch.object(ollama_client, "TOOL_BLOCK_DIR", os.path.join(self.root, "tool-blocks"))
        p.start()
        self.addCleanup(p.stop)
        ollama_client._STICKY_TOOLS.clear()
        self.addCleanup(ollama_client._STICKY_TOOLS.clear)

    def call(self, picked, reply=None, pool=None):
        sent = []
        self.full = []

        def once(host, model, messages, tools_list, *a, **k):
            sent.append([t["function"]["name"] for t in tools_list])
            self.full.append([dict(t["function"]) for t in tools_list])
            if reply is not None:
                raise reply
            return {"message": {"role": "assistant", "content": "an answer long enough"}, "prompt_eval_count": 9, "eval_count": 3}
        led = run_ledger.RunLedger("r1", root=self.root)
        led.begin({"q": {}}) if hasattr(led, "begin") and not led.dir.exists() else None
        with ollama_client.run_ledger(led), \
                mock.patch.object(ollama_client, "_chat_once", once), \
                mock.patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                mock.patch.object(ollama_client.tool_selection, "select",
                                  lambda tl, *a, **k: ([t for t in tl if t["function"]["name"] in picked], None)), \
                mock.patch.object(ollama_client, "can_think", lambda h, m: False):
            try:
                ollama_client.chat("http://h", "m", [{"role": "system", "content": "S"}, {"role": "user", "content": "q"}],
                                   list(pool or self.TOOLS), num_ctx=100000)
            except RuntimeError:
                pass
        return sent

    def test_a_resumed_call_sends_the_list_its_conversation_was_sent_not_a_fresh_pick(self):
        """Saved before the first turn; the process dies in that turn (the fake raises); the new one picks differently."""
        first = self.call({"a", "b"}, reply=RuntimeError("the process died"))
        self.assertEqual(first, [["a", "b"]])
        ollama_client._STICKY_TOOLS.clear()              # the new process's memory
        second = self.call({"c", "d"})
        self.assertEqual(second, [["a", "b"]], "the list renders first: a different one would re-read everything")
        self.assertEqual([ollama_client.held_tool_names(k) for k in ollama_client._STICKY_TOOLS], [["a", "b"]],
                         "and later turns keep it")

    def test_a_resumed_call_sends_the_text_it_was_sent_when_a_deploy_reworded_a_tool(self):
        """A held tool definition must remain frozen even if a deploy restarts the process and rewords the tool in the pool."""
        self.call({"a", "b"}, reply=RuntimeError("the process died"))
        ollama_client._STICKY_TOOLS.clear()              # the new process: memory gone, the held record on disk
        reworded = [dict(t) for t in self.TOOLS]
        reworded[0] = {"type": "function", "function": {"name": "a", "description": "NEW", "parameters": {}}}
        self.call({"c", "d"}, pool=reworded)
        self.assertEqual(self.full[-1][0], {"name": "a", "parameters": {}}, "the held text, not the new wording")


if __name__ == "__main__":
    unittest.main()
