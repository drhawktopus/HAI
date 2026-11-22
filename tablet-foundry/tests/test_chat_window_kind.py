"""`chat.window` order kind: one llama-server kept up between HAI's turns, Kirk's items answered from the window folder, the server stopped on every route. No process, socket, or tablet is touched: the server and the turn are stubbed."""
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

import tablet_foundry as tf
import tolkien_llm as tv

GOOD = {"kind": "chat.window", "name": "hai-win-rigby-q-1", "model": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        "items_dir": "/ssdshared/tabfoundry-jobs/chat-windows/hai-win-rigby-q-1", "ctx": 8192, "idle_ttl": 300,
        "gpu": "gtx1060", "fit_target": 512, "build": "cuda"}

_STATE = mock.patch.object(tf, "STATE_DIR", None)


def setUpModule():
    _STATE.new = tempfile.mkdtemp(prefix="window_state_")
    _STATE.start()


def tearDownModule():
    _STATE.stop()
    shutil.rmtree(_STATE.new, ignore_errors=True)


class Validation(unittest.TestCase):
    def test_a_good_window_spec_has_no_defects_and_runs_on_rigbys_lane(self):
        self.assertEqual(tf.spec_defects(GOOD), [])
        self.assertEqual(tv.lane_of_spec(GOOD), "rigby")

    def test_defects_are_named(self):
        for bad in (dict(GOOD, model="/etc/passwd"), dict(GOOD, items_dir=r"\\share\x"), dict(GOOD, idle_ttl=0),
                    dict(GOOD, gpu="rtx9090"), {k: v for k, v in GOOD.items() if k != "items_dir"}):
            self.assertTrue(tf.spec_defects(bad), bad)


class Dispatch(unittest.TestCase):
    """The live proof's seam: tablet_runner already had a `build_window_order` (the map.window kind), so the chat window's
    builder of the same name was shadowed and the dispatch called the wrong one (TypeError: missing hosts and caps).
    Driven through the REAL dispatch (_build_order), only the builder's work stubbed."""

    def test_a_chat_window_order_reaches_its_own_builder_on_rigbys_lane(self):
        import json as _json
        import tablet_runner as tr
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "hai-win-rigby-q-1.job.json")
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(GOOD, f)
        calls = []
        with mock.patch.object(tr, "build_chat_window_order", side_effect=lambda p, s, watch=None: calls.append(s["kind"])), \
                mock.patch.object(tr, "_wait_for_cards"), \
                mock.patch.object(tr, "lane_name", return_value="rigby"):
            tr._build_order(path)
        self.assertEqual(calls, ["chat.window"])

    def test_the_window_keeps_rigbys_claim_current_from_its_keeper_thread(self):
        """The keeper must write using the specific claim lane (RIGBY) rather than the fleet lane to ensure updates land in the correct claim context."""
        import tablet_runner as tr
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        writes = []

        def record(spec=None, phase="", done=0, total=0, work=None, name=None):
            writes.append((tf.lane_name(), threading.current_thread().name.startswith("window-claim"), phase, done))

        def fake_window(spec, log, stop_event, tick=None, on_turn=None):
            on_turn(1)
            time.sleep(0.3)                                       # the keeper beats at least once meanwhile
            return {"served": 1, "load_s": 1.0, "why_ended": "closed by the lead"}, None
        with mock.patch.object(tr, "write_current", side_effect=record), \
                mock.patch.object(tr, "WINDOW_CLAIM_S", 0.05), \
                mock.patch.object(tr, "publish"), mock.patch.object(tr, "beat"), mock.patch.object(tr, "log"), \
                mock.patch.object(tr, "retire_order", return_value={}), \
                mock.patch.object(tv, "run_window", side_effect=fake_window), \
                tf.as_lane("rigby"):
            tr.build_chat_window_order(os.path.join(d, "w.job.json"), dict(GOOD, items_dir=d))
        keeper = [w for w in writes if w[1]]
        self.assertTrue(keeper, "the keeper restated the claim")
        self.assertTrue(all(lane == "rigby" for lane, *_ in keeper), keeper)
        self.assertIn(("rigby", False, tf.WINDOW_WAITING, 1), writes, "the served turn restated it at once")
        n = len(writes)
        time.sleep(0.2)
        self.assertEqual(len(writes), n, "no keeper write after the order ended")

    def test_no_two_builders_share_a_name(self):
        import re
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tablet_runner.py"),
                   encoding="utf-8").read()
        names = re.findall(r"^def (\w+)\(", src, re.M)
        self.assertEqual(sorted(n for n in set(names) if names.count(n) > 1), [], "a later def shadows the earlier")


class Serving(unittest.TestCase):
    """Test the integration seam: real `run_window` and `helper_window.serve` against a stubbed server and mocked wire (`infer_ollama._chat_openai`)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.spec = dict(GOOD, items_dir=self.d)
        self.owner(10 ** 12)                                          # an owner beating far in the future

    def owner(self, t):
        with open(os.path.join(self.d, "OWNER.json"), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": t}, f)

    def item(self, iid, **kw):
        with open(os.path.join(self.d, iid + ".item.json"), "w", encoding="utf-8") as f:
            json.dump(dict({"id": iid, "messages": [{"role": "user", "content": "hi"}], "stream": True}, **kw), f)

    def row(self, iid):
        with open(os.path.join(self.d, iid + ".out.json"), encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def wire(answer="hello", chunks=3, done="stop", wait=0.0):
        """A stand-in for the engine's OpenAI stream: `chunks` chunks relayed to the window, then the answer."""
        def chat(base, messages, options, **kw):
            time.sleep(wait)
            for i in range(chunks):
                if kw.get("on_chunk"):
                    kw["on_chunk"]({"choices": [{"delta": {"content": "w%d " % i}}]})
            return {"message": {"content": answer}, "model": "m", "done_reason": done}
        return chat

    def run_window(self, chat=None, sleep=None, stop_event=None, **kw):
        def close_after(*a, **k):
            open(os.path.join(self.d, "CLOSE"), "w").close()          # the lead ends the window after one pass
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.5)) as start, \
                mock.patch.object(tv, "stop_server") as stop, \
                mock.patch.object(tv.HW.IO, "_chat_openai", side_effect=chat or self.wire()):
            out = tv.run_window(self.spec, lambda *a: None, sleep=sleep or close_after, stop_event=stop_event, **kw)
        return out, start, stop

    def test_an_item_is_answered_streamed_and_the_server_stopped(self):
        self.item("t00001-a")
        (summary, err), _start, stop = self.run_window()
        self.assertIsNone(err)
        self.assertEqual((summary["served"], summary["why_ended"], summary["load_s"]), (1, "closed by the lead", 1.5))
        row = self.row("t00001-a")
        self.assertEqual((row["answer"], row["error"], row["stream_chunks"]), ("hello", "", 3))
        with open(os.path.join(self.d, "t00001-a.stream.jsonl"), encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 4, "the attempt line, then every chunk as it came")
        stop.assert_called_once_with("proc")

    def test_the_window_launches_the_rows_whole_placement_exactly_as_an_llm_order(self):
        """The window's launch is derived from rigby_server_kwargs using the llm order's configuration and validation rules."""
        std = {"spec_type": "draft-mtp", "draft_max": 2, "ubatch": 2048, "parallel": 1, "op_offload_min_batch": 256,
               "engine_env": {"LAB_CUDA_GRAPHS_PRE_VOLTA": 1}, "moe_cache_mib": 1024}
        self.spec = dict(self.spec, model="Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", fit_target=1024, **std)
        self.assertEqual(tf.spec_defects(dict(self.spec, items_dir="/ssdshared/x")), [])
        for bad in (dict(ubatch=0), dict(moe_cache_mib=1024, build="hotstore"), dict(engine_env={"EVIL": 1}),
                    dict(draft_max=2, spec_type=None)):        # an explicit null turns the profile's MTP off
            self.assertTrue(tf.spec_defects(dict(self.spec, items_dir="/ssdshared/x", **bad)), bad)
        (summary, err), start, _stop = self.run_window()
        kw = start.call_args.kwargs
        self.assertEqual({k: kw[k] for k in tv.rigby_server_kwargs(self.spec)}, tv.rigby_server_kwargs(self.spec))
        for k, v in std.items():
            self.assertEqual(kw[k], v, k)
        self.assertEqual(kw["slot_save_path"], tv.slots_dir(), "the engine writes the chats' states where the window keeps them")
        self.assertEqual(summary["launch"]["spec_type"], "draft-mtp", "the summary says what it launched")

    def test_a_failed_server_start_is_reported_and_still_cleaned_up(self):
        with mock.patch.object(tv, "start_server", side_effect=RuntimeError("llama-server exited rc=1")), \
                mock.patch.object(tv, "stop_server") as stop:
            summary, err = tv.run_window(self.spec, lambda *a: None, sleep=lambda s: None)
        self.assertIn("llama-server exited", err)
        stop.assert_called_once()
        self.assertTrue(os.path.exists(os.path.join(self.d, "CLOSE")), "Kirk reads the ended window from CLOSE")

    def test_a_window_whose_owner_is_already_silent_never_loads_its_engine(self):
        self.owner(0)
        (summary, err), start, stop = self.run_window()
        self.assertIsNone(err)
        self.assertIn("silent", summary["why_ended"])
        start.assert_not_called()
        stop.assert_called_once_with(None)
        self.assertTrue(os.path.exists(os.path.join(self.d, "CLOSE")))

    def test_idle_past_its_rule_while_kirk_marks_it_idle_ends_it(self):
        open(os.path.join(self.d, "IDLE"), "w").close()
        t = [time.time()]                    # the IDLE mark's age is read off the file: the clock starts at the wall's
        self.spec = dict(self.spec, idle_ttl=30)
        (summary, err), _start, stop = self.run_window(sleep=lambda s: t.__setitem__(0, t[0] + 10), clock=lambda: t[0])
        self.assertIsNone(err)
        self.assertTrue(summary["why_ended"].startswith("idle"), summary)
        stop.assert_called_once_with("proc")

    def test_each_served_turn_reaches_on_turn(self):
        """The window's turn count must reach the runner, which states it in rigby's lane claim."""
        self.item("t00001-a")
        self.item("t00002-b")
        seen = []
        (summary, err), _s, _t = self.run_window(on_turn=seen.append)
        self.assertIsNone(err)
        self.assertEqual(seen, [1, 2])

    def test_the_turn_in_flight_is_read_from_the_folder(self):
        """What the runner states while a turn is answered: the item waiting for its row, and its stream's lines."""
        self.assertEqual(tf.window_turn_in_flight(self.d), (False, 0), "an empty folder: waiting for a turn")
        self.item("t00001-a")
        self.assertEqual(tf.window_turn_in_flight(self.d), (True, 0), "taken, nothing streamed yet")
        with open(os.path.join(self.d, "t00001-a.stream.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"attempt": "x"}\n{"c": 1}\n{"c": 2}\n')
        self.assertEqual(tf.window_turn_in_flight(self.d), (True, 3))
        with open(os.path.join(self.d, "t00001-a.out.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertEqual(tf.window_turn_in_flight(self.d), (False, 0), "answered: waiting again")
        self.assertEqual(tf.window_turn_in_flight(os.path.join(self.d, "missing")), (None, None))

    def test_the_grid_follows_the_launch(self):
        """`-ub U`, and `-b U` only above the default 2048 (start_server's argv): what the real profile's ubatch means."""
        self.assertEqual(tv.read_grid(2048), (2048, 2048))
        self.assertEqual(tv.read_grid(4096), (4096, 4096))
        self.assertEqual(tv.read_grid(None), (2048, 512))


class TheGpuBoxesWindowProgram(unittest.TestCase):
    """This suite verifies the engine integration for the shared window program.
    Quick resume, hot start, and park behaviors are pinned against the
    single implementation in the foundry suite (RigbyRunsThisWindowProgram)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.spec = dict(GOOD, items_dir=self.d)
        with open(os.path.join(self.d, "OWNER.json"), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": 10 ** 12}, f)

    def item(self, iid, **kw):
        with open(os.path.join(self.d, iid + ".item.json"), "w", encoding="utf-8") as f:
            json.dump(dict({"id": iid, "conv": "alex/A", "messages": [{"role": "user", "content": "hi"}],
                            "stream": True}, **kw), f)

    def row(self, iid):
        with open(os.path.join(self.d, iid + ".out.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_rigby_has_no_window_program_of_its_own(self):
        self.assertFalse([n for n in ("WindowSlots", "_window_turn", "_window_owner_gone") if hasattr(tv, n)],
                         "a second copy of the window program is drift: rigby runs helper_window's")
        seen = {}

        def serve(a, items_dir, prog_dir, partial, meter, **kw):
            seen.update(a=a, **kw)
            return [], "closed by the lead"
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), mock.patch.object(tv, "stop_server"), \
                mock.patch.object(tv.HW, "serve", side_effect=serve):
            summary, err = tv.run_window(self.spec, lambda *a: None)
        self.assertIsNone(err)
        a = seen["a"]
        self.assertEqual((a.llama, a.host, a.read_grid, a.num_ctx),
                         (True, "http://127.0.0.1:%d" % tv.SERVER_PORT,
                          tv.read_grid(tv.rigby_server_kwargs(tv.launch_spec(self.spec)).get("ubatch")), 8192))
        self.assertIsInstance(seen["conv_slots"], tv.HW.ConvSlots)
        self.assertIsInstance(seen["conv_slots"].hot, tv.HW.HotStarts)
        self.assertEqual((seen["poll_s"], seen["card_waiting"](), seen["idle_ttl"]), (tv.WINDOW_POLL_S, False, 300))

    def test_chat_states_follow_the_fleets_rule_once_the_array_is_mounted(self):
        """Five slots per user plus the array on every box; until the array is mounted, retain only the newest 50."""
        sd = os.path.join(self.d, "slots")
        os.makedirs(sd)
        for i in range(7):
            name = "conv-m-8192-%03d.bin" % i
            open(os.path.join(sd, name), "wb").close()
            with open(os.path.join(sd, name + ".json"), "w", encoding="utf-8") as f:
                json.dump({"user": "alex", "t": 1000 + i}, f)
        with mock.patch.object(tv, "RIGBY_ARRAY", os.path.join(self.d, "not-mounted")):
            keep, fetch, rule = tv._chat_state_keeping(sd, "conv-m-8192-")
            keep("alex")
        self.assertIn("not mounted", rule)
        self.assertEqual((len(os.listdir(sd)), fetch("x", 1.0)), (14, "absent"), "under 50: all kept, nothing fetched")
        array = os.path.join(self.d, "array")
        os.makedirs(array)
        with mock.patch.object(tv, "RIGBY_ARRAY", array):
            keep, _fetch, rule = tv._chat_state_keeping(sd, "conv-m-8192-")
            keep("alex")
        self.assertEqual(rule, "five per user + the array")
        self.assertEqual(sorted(f for f in os.listdir(os.path.join(array, "rigby")) if f.endswith(".bin")),
                         ["conv-m-8192-000.bin", "conv-m-8192-001.bin"], "the oldest two moved, never deleted")

    def serve_one(self, chat, stop_event=None, save=None):
        saved = save if save is not None else []

        def save_slot(a, name=None, slot_dir=None, **_kw):
            saved.append(name)
            return {"n_saved": 900, "pending_token": 7}
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), \
                mock.patch.object(tv, "stop_server", side_effect=lambda p: saved.append("stop")), \
                mock.patch.object(tv.HW.IO, "_chat_openai", side_effect=chat), \
                mock.patch.object(tv.HW, "save_slot", side_effect=save_slot):
            out = tv.run_window(self.spec, lambda *a: None, stop_event=stop_event,
                                sleep=lambda s: open(os.path.join(self.d, "CLOSE"), "w").close())
        return out, saved

    @staticmethod
    def wire(wait=0.0, chunks=3):
        def chat(base, messages, options, **kw):
            for i in range(chunks):
                time.sleep(wait)
                kw["on_chunk"]({"choices": [{"delta": {"content": "w%d " % i}}]})
            return {"message": {"content": "done"}, "model": "m", "done_reason": "stop"}
        return chat

    def test_the_chat_is_saved_before_the_server_stops(self):
        """The seam: a chat's state must be written while the engine still holds it."""
        self.item("t00001-a")
        (_summary, err), saved = self.serve_one(self.wire())
        self.assertIsNone(err)
        self.assertEqual(saved, [tv.HW.conv_slot_file(GOOD["model"], 8192, "alex/A"), "stop"])

    def test_a_stop_slip_parks_a_turn_its_caller_marked_and_the_window_ends(self):
        """The GPU factories' YIELD: a stop slip parks a cut_ok turn mid-answer, saves it as it stands and ends."""
        self.item("t00001-a", cut_ok=True)
        ev = threading.Event()
        ev.set()
        (summary, err), saved = self.serve_one(self.wire(wait=0.4), stop_event=ev)
        self.assertIsNone(err)
        row = self.row("t00001-a")
        self.assertEqual((row["cut"], row["error"], row["cut_saved"], row["cut_mid_answer"]),
                         ("the factory asked for the card", "", True, True))
        self.assertTrue(summary["why_ended"].startswith("yielded"), summary)

    def test_a_turn_not_marked_cut_ok_finishes_then_the_stop_slip_ends_the_window(self):
        self.item("t00001-a")
        ev = threading.Event()
        ev.set()
        (summary, err), _saved = self.serve_one(self.wire(wait=0.4), stop_event=ev)
        row = self.row("t00001-a")
        self.assertEqual((row["answer"], row["error"], row.get("cut")), ("done", "", None))
        self.assertTrue(summary["why_ended"].startswith("yielded between turns"), summary)

    def test_a_hung_engine_is_the_windows_error_not_the_order_takers_end(self):
        """helper_window says a hung engine with SystemExit(2), which holds a GPU box's order; inside Tolkien's order taker
        it must be this window's error, never the process's exit."""
        self.item("t00001-a")

        def silent(base, messages, options, **kw):
            raise TimeoutError("no token for 600 s")
        with mock.patch.object(tv.HW.IO, "_silence", return_value=True), \
                mock.patch.object(tv.HW.IO, "_engine_alive", return_value=False):
            (summary, err), saved = self.serve_one(silent)
        self.assertIn("engine hung", err)
        self.assertEqual(saved[-1], "stop", "the server is stopped on this route too")


if __name__ == "__main__":
    unittest.main()
