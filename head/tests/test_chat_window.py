"""chat_window.py - HAI turns through a factory WINDOW order (#34 chat-as-order; [[34-CHAT-FACTORY-CONTRACT-AUDIT]]
§ "Shape review"). The desk is faked (intake=) and the window is a thread writing the files helper_window.py writes,
so nothing here places an order or reaches a model."""
import datetime
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

import httpx

import chat_window as CW
import config
import ollama_client
from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule():
    start_no_live_probes()      # place() reaches model_state / models_loaded once a box stays eligible: never dial it


def tearDownModule():
    stop_no_live_probes()

HOST, MODEL, CTX = "bighonker", "qwen3.5:9b", 19968
URL = "http://10.0.0.167:11434"
CHUNKS = [{"message": {"thinking": "hm "}, "done": False}, {"message": {"content": "hel"}, "done": False},
          {"message": {"content": "lo"}, "done": True, "done_reason": "stop"}]


class AWindowBeingOpened(unittest.TestCase):

    def beats(self, status, reporting, chunks=False):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        w = mock.MagicMock(dir=d, host=HOST)
        w.name = "win"
        w.desk_status.return_value = status
        watch = mock.MagicMock(verdict=None, detail=None)
        polls = [0]

        def sleep(_s):
            polls[0] += 1
            if polls[0] == 3 * int(CW.PERIOD_S / CW.POLL_S):
                with open(os.path.join(d, "t1" + CW.OUT), "w", encoding="utf-8") as f:
                    json.dump({"error": "ended by the test", "stream_chunks": 0}, f)
        if chunks:
            with open(os.path.join(d, "t1" + CW.STREAM), "w", encoding="utf-8") as f:
                f.write(json.dumps({"keepalive": True}) + "\n")
        with mock.patch.object(CW, "_box_reporting", return_value=reporting):
            with self.assertRaises(httpx.ReadError):
                list(CW._Stream(w, "t1", watch, sleep=sleep, sse=True))
        return watch.beat.call_count

    def test_the_factory_opening_it_is_a_status_while_the_box_reports(self):
        self.assertGreaterEqual(self.beats("claimed", True), 2)
        self.assertGreaterEqual(self.beats("running", True), 2)

    def test_a_silent_box_gets_no_such_grace(self):
        self.assertEqual(self.beats("claimed", False), 0)

    def test_once_the_engine_has_spoken_only_its_own_rows_count(self):
        self.assertEqual(self.beats("running", True, chunks=True), 1, "the one row, not the desk's status")

    def test_box_reporting_reads_the_real_report_shape(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        fresh = {"ts": now.isoformat(), "util_pct": 0.0, "stale": False}
        old = dict(fresh, ts=(now - datetime.timedelta(hours=1)).isoformat())
        for rec, want in ((fresh, True), (old, False), (dict(fresh, stale=True), False), (None, False), ({"util_pct": 1}, False)):
            with mock.patch("scheduler.gpu_load", return_value=rec):
                self.assertIs(CW._box_reporting(HOST), want, rec)


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        for p in (mock.patch.object(config, "CHAT_WINDOWS_DIR", self.d),
                  mock.patch.object(config, "STATE_DIR", self.d),           # first-output records stay in the test
                  mock.patch.object(config, "CHAT_WINDOWS_UNC", r"\\share\chat-windows"),
                  mock.patch.object(CW, "_ensure_beat", lambda: None),
                  mock.patch.object(CW, "_host_for", lambda url: HOST),
                  mock.patch.object(CW.Window, "desk_status", lambda self: "running"),
                  # the card fits the full window unless a test says otherwise
                  mock.patch("scheduler.window_ctx", lambda host, model, n: (n, "measured fit before load")),
                  # no box reports here: the wait for a closed idle window's card (await_card_freed) has its own tests
                  mock.patch.object(CW, "await_card_freed", lambda *a, **k: True),
                  mock.patch.dict(CW._WINDOWS, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.orders = []

    def intake(self, spec, requires, host):
        self.orders.append((spec, requires, host))
        return {"ok": True, "steps": [{"host": host, "id": "desk%d" % len(self.orders), "order": spec["name"]}]}

    def body(self, **kw):
        b = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": True, "think": "xhigh",
             "keep_alive": "5m", "options": {"num_ctx": CTX}}
        b.update(kw)
        return b

    def window_answers(self, w, lines, row=None, attempt="a1", delay=0.05):
        """A fake helper_window: wait for the item, write the stream lines, then the row."""
        def run():
            item = None
            for _ in range(200):
                names = sorted(f[:-len(".item.json")] for f in os.listdir(w.dir) if f.endswith(".item.json")
                               and not os.path.exists(os.path.join(w.dir, f[:-len(".item.json")] + ".out.json")))
                if names:
                    item = names[0]                     # the first item not yet answered (a window serves many turns)
                    break
                time.sleep(0.01)
            with open(os.path.join(w.dir, item + ".stream.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"attempt": attempt, "id": item}) + "\n")
                for ln in lines:
                    f.write(json.dumps(ln) + "\n")
                    f.flush()
                    time.sleep(delay)
            with open(os.path.join(w.dir, item + ".out.json"), "w", encoding="utf-8") as f:
                json.dump(row or {"id": item, "answer": "hello", "error": ""}, f)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t


class OpenPlacesAPinnedWindowOrder(_Base):
    def test_the_window_is_a_desk_order_pinned_to_the_chosen_box(self):
        w = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        spec, requires, host = self.orders[0]
        self.assertEqual(host, HOST)
        self.assertEqual(requires, ["order.compute", "model." + MODEL])
        self.assertTrue(spec["payload"].endswith("helper_window.py"))
        self.assertEqual(spec["args"][2:], ["--model", MODEL, "--num-ctx", str(CTX), "--live", "--idle-ttl", "300"],
                         "an ollama window carries the box's keep_alive as its idle rule (5m = 300 s)")
        self.assertTrue(spec["args"][0].startswith("\\\\share\\chat-windows\\hai-win-bighonker-"))
        self.assertEqual(spec["priority"], 100)
        with open(os.path.join(w.dir, "OWNER.json"), encoding="utf-8") as f:
            owner = json.load(f)
        self.assertEqual(owner["period"], CW.PERIOD_S)
        self.assertTrue(os.path.exists(os.path.join(w.dir, "IDLE")), "nobody is using it yet")

    def test_one_window_per_box_model_context_shared_until_it_ends(self):
        a = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        b = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        self.assertIs(a, b)
        self.assertEqual(len(self.orders), 1)
        open(os.path.join(a.dir, "CLOSE"), "w").close()
        c = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        self.assertIsNot(c, a)
        self.assertEqual(len(self.orders), 2, "an ended window is replaced by a new order")

    def test_a_held_order_is_cancelled_and_the_turn_fails_over(self):
        cancelled = []
        with mock.patch.object(CW, "_cancel", lambda i, why: cancelled.append(i)):
            with self.assertRaises(httpx.ConnectError) as cm:
                CW.window_for(HOST, MODEL, CTX, intake=lambda *a: {"ok": True, "held": True, "id": "h1",
                                                                   "why": ["a person is on the card"]})
        self.assertEqual(cancelled, ["h1"])
        self.assertIn("a person is on the card", str(cm.exception))
        self.assertEqual(CW._WINDOWS, {})

    def test_the_heartbeat_exists_before_the_order_and_a_failed_cancel_is_logged(self):
        seen = []

        def intake(spec, requires, host):
            seen.append(os.path.exists(os.path.join(self.d, spec["name"], "OWNER.json")))
            return {"ok": True, "held": True, "id": "h1", "why": ["Plex first"]}
        logged = []
        with mock.patch("app.p20_order_cancel", lambda req: {"ok": False, "error": "no host"}), \
                mock.patch.object(CW.notes, "log", logged.append):
            with self.assertRaises(httpx.ConnectError):
                CW.window_for(HOST, MODEL, CTX, intake=intake)
        self.assertEqual(seen, [True], "a window placed later must find a beat nobody refreshes, and end itself")
        self.assertTrue(any("could not cancel h1" in line for line in logged))

    def test_a_window_order_is_rush_so_a_live_turn_never_waits_for_cheap_power(self):
        got = {}

        def p20_order(req):
            got["rush"], got["only"] = req.rush, req.only
            return {"ok": False, "error": "stop here"}
        with mock.patch("app.p20_order", p20_order):
            with self.assertRaises(httpx.ConnectError):
                CW.window_for(HOST, MODEL, CTX)
        self.assertEqual(got, {"rush": True, "only": [HOST]})

    def test_two_windows_opened_in_the_same_second_never_share_a_folder(self):
        """Folders for windows opened in the same second must remain distinct; the second-level resolution in the timestamp is insufficient to guarantee uniqueness."""
        with mock.patch.object(CW.time, "strftime", lambda *a: "20261022-120000"):
            a = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
            b = CW.window_for(HOST, MODEL, CTX * 2, intake=self.intake)
        self.assertNotEqual(a.dir, b.dir)
        self.assertNotEqual(a.name, b.name)
        self.assertTrue(a.name.startswith("hai-win-") and b.name.startswith("hai-win-"), "the retire sweep's prefix")

    def test_a_declared_split_window_carries_offload_ok_and_no_other_does(self):
        """--offload-ok is only valid for models in the offload list; this flag must not be declared for others as downstream checks rely on it."""
        with mock.patch("scheduler.offload_ok", lambda host, model: model == "big:a3b"):
            CW.window_for(HOST, "big:a3b", 65536, intake=self.intake)
            CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        split, plain = self.orders[0][0]["args"], self.orders[1][0]["args"]
        self.assertIn("--offload-ok", split)
        self.assertNotIn("--offload-ok", plain)

    def test_a_box_with_a_window_clock_declares_it_and_no_other_box_does(self):
        """Boxes listed in WINDOW_GPU_CLOCK receive a floor; unlisted boxes do not."""
        with mock.patch.object(CW.config, "WINDOW_GPU_CLOCK", {HOST: {"floor": 1965}}):
            CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        with mock.patch.object(CW.config, "WINDOW_GPU_CLOCK", {"some-other-box": {"floor": 1965}}):
            CW.window_for(HOST, MODEL, CTX * 2, intake=self.intake)
        self.assertEqual(self.orders[0][0].get("gpu_clock"), {"floor": 1965})
        self.assertNotIn("gpu_clock", self.orders[1][0])

    def test_bighonker_holds_a_floor_by_default(self):
        self.assertEqual(CW.config.WINDOW_GPU_CLOCK.get("bighonker"), {"floor": 1965})

    def test_a_refused_order_fails_over(self):
        with self.assertRaises(httpx.ConnectError):
            CW.window_for(HOST, MODEL, CTX, intake=lambda *a: {"ok": False, "error": "no factory can build it"})


class ATurnReadsTheWindowsOwnLines(_Base):
    def test_the_engines_lines_come_back_unchanged_and_the_item_carries_the_turn(self):
        with CW.open_stream(URL + "/api/chat", self.body(tools=[{"type": "function"}]), None, "run:r1",
                            intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, CHUNKS)
            got = [json.loads(x) for x in resp.iter_lines() if x]
        self.assertEqual(got, CHUNKS)
        with open([os.path.join(w.dir, f) for f in os.listdir(w.dir) if f.endswith(".item.json")][0],
                  encoding="utf-8") as f:
            item = json.load(f)
        self.assertEqual((item["model"], item["think"], item["keep_alive"], item["options"], item["stream"]),
                         (MODEL, "xhigh", "5m", {"num_ctx": CTX}, True))
        self.assertEqual(item["tools"], [{"type": "function"}])
        self.assertEqual(resp.extensions["factory_order_id"], "desk1")

    def test_a_window_sized_to_the_card_opens_and_sends_ITS_context(self):
        """Both the scheduler and the turn must use the window size measured by the door; if the turn re-requests the full context, ollama reloads the model at the unsized limit."""
        with mock.patch("scheduler.window_ctx", lambda host, model, n: (CTX // 2, "measured fit before load")):
            with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
                w = CW._WINDOWS[(HOST, MODEL, CTX // 2)]
                self.window_answers(w, CHUNKS)
                [x for x in resp.iter_lines() if x]
        spec = self.orders[0][0]
        self.assertEqual(spec["args"][spec["args"].index("--num-ctx") + 1], str(CTX // 2))
        with open([os.path.join(w.dir, f) for f in os.listdir(w.dir) if f.endswith(".item.json")][0],
                  encoding="utf-8") as f:
            self.assertEqual(json.load(f)["options"], {"num_ctx": CTX // 2})

    def test_a_turn_names_its_chat_so_the_window_keeps_that_chats_state(self):
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake, conv="alex/abc") as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, CHUNKS)
            [x for x in resp.iter_lines() if x]
        with open([os.path.join(w.dir, f) for f in os.listdir(w.dir) if f.endswith(".item.json")][0],
                  encoding="utf-8") as f:
            self.assertEqual(json.load(f)["conv"], "alex/abc")
        self.assertEqual(CW.conv_home("alex/abc"), (HOST, MODEL, CTX),
                         "a finished turn records where the chat's state lives (quick resume placement)")

    def test_a_chat_this_box_saved_tells_the_window_to_look_on_the_array_and_prices_the_re_read(self):
        """Verify that a chat saved by this box's configuration uses the local array's measured read rate to decide whether copying state back is faster than re-reading from source."""
        def turn():
            with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake,
                                conv="alex/abc") as resp:
                w = CW._WINDOWS[(HOST, MODEL, CTX)]
                fake = self.window_answers(w, CHUNKS)
                [x for x in resp.iter_lines() if x]
            fake.join(5)          # the reader ends at the done chunk; the fake's row lands after it - before the next turn
            newest = sorted(f for f in os.listdir(w.dir) if f.endswith(".item.json"))[-1]
            with open(os.path.join(w.dir, newest), encoding="utf-8") as f:
                return json.load(f)
        with mock.patch("scheduler.read_rate", lambda box, model: 2842.5 if (box, model) == (HOST, MODEL) else None):
            first, second = turn(), turn()
        self.assertNotIn("state_here", first, "a chat no box has served yet: the array is never asked")
        self.assertEqual((second.get("state_here"), second.get("read_tok_s")), (True, 2842.5))

    def test_every_chat_turn_carries_the_read_rate_and_how_much_of_its_tool_list_is_shared(self):
        """A new chat's first turn prices restore against re-read, and the window cuts the shared start where the core tools end."""
        import tool_selection
        core = sorted(tool_selection.CORE_TOOL_NAMES)[:2]
        tools = [{"type": "function", "function": {"name": n}} for n in core + ["not_a_core_tool", core[0]]]
        with mock.patch("scheduler.read_rate", lambda box, model: 2842.5):
            with CW.open_stream(URL + "/api/chat", self.body(tools=tools), None, "run:r1", intake=self.intake,
                                conv="alex/abc") as resp:
                w = CW._WINDOWS[(HOST, MODEL, CTX)]
                self.window_answers(w, CHUNKS)
                [x for x in resp.iter_lines() if x]
        with open([os.path.join(w.dir, f) for f in os.listdir(w.dir) if f.endswith(".item.json")][0],
                  encoding="utf-8") as f:
            item = json.load(f)
        self.assertEqual((item["read_tok_s"], item["hot_tools"]), (2842.5, 2))
        self.assertNotIn("state_here", item, "a chat no box has served yet: the array is never asked")

    def test_the_shared_count_stops_at_the_first_tool_not_every_chat_carries(self):
        import tool_selection
        core = sorted(tool_selection.CORE_TOOL_NAMES)
        fn = lambda n: {"type": "function", "function": {"name": n}}          # noqa: E731
        self.assertEqual(CW.shared_tool_count([fn(core[0]), fn("x"), fn(core[1])]), 1)
        self.assertEqual(CW.shared_tool_count([fn("x"), fn(core[0])]), 0, "nothing shared: the window builds nothing")
        self.assertEqual(CW.shared_tool_count([fn(core[0]), fn(core[1])]), 2)
        self.assertEqual(CW.shared_tool_count([{"type": "function"}]), 0, "a tool with no name is not known to be shared")

    def test_the_real_selection_puts_the_shared_tools_first_on_every_request(self):
        """What the count stands on: if a real list did not lead with them, every chat's shared start would be empty."""
        import tool_selection
        import tools as tools_mod
        counts = []
        for prompt in ("what did Claude task you with this morning?", "research the FSR 4 SDK's inputs and read its docs"):
            selected, _note = tool_selection.select(tools_mod.HEAVY_TOOLS, "Alex", prompt, 4096)
            self.assertLess(len(selected), len(tools_mod.HEAVY_TOOLS), "fixture is wrong if no narrowing happened")
            counts.append(CW.shared_tool_count(selected))
            self.assertEqual(selected[:counts[-1]], selected[:counts[0]], "one shared start for every request")
        self.assertGreater(counts[0], 5, "most of the core floor leads the list")

    def test_typing_in_a_chat_asks_its_boxs_open_window_to_prefetch(self):
        """Verifies that typing in a chat triggers prefetch via the window holding the chat's state, rather than generic scheduling."""
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake, conv="alex/abc") as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            fake = self.window_answers(w, CHUNKS)
            [x for x in resp.iter_lines() if x]
        fake.join(5)
        with mock.patch("scheduler.read_rate", lambda box, model: 2842.5):
            got = CW.prefetch("alex/abc")
        self.assertTrue(got["asked"], got)
        asks = [f for f in os.listdir(w.dir) if f.endswith(CW.FETCH)]
        self.assertEqual(len(asks), 1)
        with open(os.path.join(w.dir, asks[0]), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"conv": "alex/abc", "read_tok_s": 2842.5})
        self.assertFalse(CW.prefetch("alex/never")["asked"], "a chat no box holds asks nothing")

    def test_the_page_asks_for_a_prefetch_on_the_first_keystroke(self):
        import app
        self.assertIn("/chats/prefetch?user=", app.CHAT_HTML)
        self.assertIn("_prefetchedChat === activeChat", app.CHAT_HTML, "once per chat per page")

    def test_a_turn_outside_a_chat_names_none(self):
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, CHUNKS)
            [x for x in resp.iter_lines() if x]
        with open([os.path.join(w.dir, f) for f in os.listdir(w.dir) if f.endswith(".item.json")][0],
                  encoding="utf-8") as f:
            self.assertNotIn("conv", json.load(f))

    def test_no_measured_window_fits_is_unavailable_and_places_nothing(self):
        with mock.patch("scheduler.window_ctx", lambda host, model, n: (None, "model fit: ... does not fit before load")):
            with self.assertRaises(CW.WindowUnavailable) as cm:
                with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake):
                    pass
        self.assertTrue(cm.exception.unavailable, "the chat loop must not retry this box")
        self.assertEqual(self.orders, [])

    def test_an_open_window_between_the_sizes_is_joined_not_reopened(self):
        """The desktop going up and down must not reload the model each turn: a smaller window already open for this
        model is joined while the full one does not fit."""
        small = CW.window_for(HOST, MODEL, CTX // 2, intake=self.intake)
        with mock.patch("scheduler.window_ctx", lambda host, model, n: (CTX // 4, "measured fit before load")):
            self.assertEqual(CW.sized_ctx(HOST, MODEL, CTX), CTX // 2)
        self.assertIs(CW._WINDOWS[(HOST, MODEL, CTX // 2)], small)
        with mock.patch("scheduler.window_ctx", side_effect=RuntimeError("table unreadable")):
            self.assertEqual(CW.sized_ctx(HOST, MODEL, CTX), CTX, "could-not-size leaves the desk's check to decide")

    def test_this_models_own_open_window_is_joined_when_the_card_reads_full(self):
        full = lambda host, model, n: (None, "declared split needs 10710 MiB of RAM: 9009 free - 3072 reserve")
        with mock.patch("scheduler.window_ctx", full):
            own = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
            self.assertEqual(CW.sized_ctx(HOST, MODEL, CTX), CTX, "Kirk's own open window is the model in place")
            open(os.path.join(own.dir, CW.CLOSE), "w").close()
            with self.assertRaises(CW.WindowUnavailable):          # a window that has ended holds nothing
                CW.sized_ctx(HOST, MODEL, CTX)

    def test_another_run_holding_the_window_is_busy_for_others_until_that_run_ends(self):
        """A run holds the window from its first turn to its end, not just while generating; this prevents new turns from queuing behind another run's background work."""
        self.assertFalse(CW.serving_others(HOST, "run:r2"))
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.assertTrue(CW.serving_others(HOST, "run:r2"), "another run's turn is generating")
            self.assertFalse(CW.serving_others(HOST, "run:r1"), "a run never waits behind itself")
            self.assertFalse(CW.serving_others("mothership", "run:r2"), "only the box the run holds")
            self.window_answers(w, CHUNKS)
            list(resp.iter_lines())
        self.assertTrue(CW.serving_others(HOST, "run:r2"),
                        "BETWEEN its turns (tools running) the run still holds the window - the live proof's case")
        CW.release("run:r1")
        self.assertFalse(CW.serving_others(HOST, "run:r2"), "free once the run that held it ended")

    def test_a_turn_the_window_started_over_is_discarded(self):
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, CHUNKS[:1] + [{"attempt": "a2", "id": "x"}] + CHUNKS)
            with self.assertRaises(httpx.ReadError):
                list(resp.iter_lines())

    def test_exact_media_decoder_rejection_reaches_the_agent_loop_as_typed_input_error(self):
        from model_input_error import MediaInputRejected
        error = 'RuntimeError: openai server HTTP 400: ' + json.dumps({'error': {
            'code': 400, 'message': 'Failed to load image or audio file', 'type': 'invalid_request_error'}})
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, [], row={"error": error, "stream_chunks": 0})
            with self.assertRaises(MediaInputRejected):list(resp.iter_lines())

    def test_media_rejection_after_any_output_keeps_the_existing_failure_path(self):
        from model_input_error import MediaInputRejected
        error = 'RuntimeError: openai server HTTP 400: ' + json.dumps({'error': {
            'code': 400, 'message': 'Failed to load image or audio file', 'type': 'invalid_request_error'}})
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, [{"done":False,"message":{"content":"partial"}}], row={"error":error,"stream_chunks":1})
            with self.assertRaises(httpx.ReadError) as exc:list(resp.iter_lines())
            self.assertNotIsInstance(exc.exception,MediaInputRejected)

    def test_a_refused_row_and_an_ended_window_are_read_errors(self):
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, [], row={"error": "effort 'high' is not one this window sends as itself"})
            with self.assertRaises(httpx.ReadError) as cm:
                list(resp.iter_lines())
        self.assertIn("'high'", str(cm.exception))
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r2", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            open(os.path.join(w.dir, "CLOSE"), "w").close()
            with self.assertRaises(httpx.ReadError):
                list(resp.iter_lines())

    def test_a_stop_leaves_the_window_a_cancel_marker_and_a_finished_turn_does_not(self):
        import cancellation
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, CHUNKS)
            list(resp.iter_lines())
        self.assertEqual([f for f in os.listdir(w.dir) if f.endswith(".cancel")], [])
        with mock.patch.object(cancellation, "check", side_effect=cancellation.Cancelled()):
            with self.assertRaises(cancellation.Cancelled):
                with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
                    list(resp.iter_lines())
        self.assertEqual(len([f for f in os.listdir(w.dir) if f.endswith(".cancel")]), 1)

    def test_a_tail_the_client_has_not_received_is_not_read_as_data(self):
        # CIFS shows a grown file as NUL bytes until data arrives; _read_from must stop at the first NUL
        p = os.path.join(self.d, "s.jsonl")
        with open(p, "wb") as f:
            f.write(b'{"a": 1}\n{"b": 2}\n{"c"' + b"\x00" * 40)
        self.assertEqual(CW._read_from(p, 0), b'{"a": 1}\n{"b": 2}\n')
        self.assertEqual(CW._read_from(p, 9), b'{"b": 2}\n')
        self.assertEqual(CW._read_from(os.path.join(self.d, "absent"), 0), b"")

    def test_a_turn_without_a_done_chunk_ends_when_the_rows_count_is_read(self):
        cut = [{"message": {"content": "a"}, "done": False}, {"message": {"content": "b"}, "done": False}]
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, cut, row={"error": "", "stream_chunks": 2})
            got = [json.loads(x) for x in resp.iter_lines() if x]
        self.assertEqual(got, cut, "every chunk the window relayed, then the end - the caller sees no done chunk")

    def test_a_turn_the_loop_rule_ended_says_so_after_its_chunks(self):
        """A turn with done_reason "loop" raises TurnLooped instead of being treated as a transient stream error."""
        cut = [{"message": {"thinking": "a"}, "done": False}, {"message": {"thinking": "a"}, "done": False}]
        got = []
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            self.window_answers(w, cut, row={"error": "", "done_reason": "loop", "stream_chunks": 2})
            with self.assertRaises(CW.TurnLooped) as cm:
                for x in resp.iter_lines():
                    if x:
                        got.append(json.loads(x))
        self.assertEqual(got, cut, "the chunks are relayed first: the live feed shows what was written")
        self.assertTrue(cm.exception.looped)
        self.assertIsInstance(cm.exception, httpx.ReadError)

    def test_a_turn_without_its_measured_context_is_refused_before_any_order(self):
        with self.assertRaises(httpx.ConnectError):
            with CW.open_stream(URL + "/api/chat", self.body(options={}), None, "run:r1", intake=self.intake):
                pass
        self.assertEqual(self.orders, [])

    def test_a_supervision_verdict_ends_the_read(self):
        watch = mock.Mock(verdict="idle", detail="no output while loaded on an idle card")
        with CW.open_stream(URL + "/api/chat", self.body(), watch, "run:r1", intake=self.intake) as resp:
            with self.assertRaises(httpx.ReadError) as cm:
                list(resp.iter_lines())
        self.assertIn("idle card", str(cm.exception))


class NewWindowsMeasureTheirFirstOutput(_Base):
    """#34 checklist B1 + the 10-23 overflow finding (a cold-window turn took 37.9 s on a 12.0 s price): a NEW ollama
    window's first turn records the seconds from its open to the first output Kirk read, with the engine's own load and
    prompt read; Spock charges the median, the load left out, on a box with no window for the model."""

    def _turn(self, done):
        with CW.open_stream(URL + "/api/chat", self.body(), None, "run:r1", intake=self.intake) as resp:
            w = CW._WINDOWS[(HOST, MODEL, CTX)]
            if w.n == 1:
                w.opened -= 10                     # the window opened 10 s before its first output
            item = [f for f in os.listdir(w.dir) if f.startswith("t%05d" % w.n) and f.endswith(".item.json")][0]
            item = item[:-len(".item.json")]
            with open(os.path.join(w.dir, item + ".stream.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"attempt": "a1", "id": item}) + "\n")
                for ln in CHUNKS[:-1] + [done]:
                    f.write(json.dumps(ln) + "\n")
            with open(os.path.join(w.dir, item + ".out.json"), "w", encoding="utf-8") as f:
                json.dump({"id": item, "answer": "hello", "error": ""}, f)
            list(resp.iter_lines())
        return w

    def test_the_record_says_what_was_already_in_the_engine_not_the_prompts_size_as_what_was_read(self):
        """Assert the log uses (prompt_eval_count - prompt_eval_cached_count) rather than the total count."""
        said = []
        with mock.patch.object(CW.notes, "log", lambda line: said.append(line)):
            self._turn(dict(CHUNKS[-1], load_duration=0, prompt_eval_duration=21.9e9, prompt_eval_count=13813,
                            prompt_eval_cached_count=5120))
        row = json.load(open(CW._first_path(), encoding="utf-8"))[HOST][-1]
        self.assertEqual((row["prompt_n"], row["cached_n"]), (13813, 5120))
        line = [s for s in said if "first output" in s][-1]
        self.assertIn("read 8693 of the prompt's 13813 tokens in 21.9 s, 5120 already in the engine", line)

    def test_the_first_turn_of_a_new_window_is_recorded_and_a_later_turn_is_not(self):
        done = dict(CHUNKS[-1], load_duration=2e9, prompt_eval_duration=1e9, prompt_eval_count=9418)
        self._turn(done)
        self._turn(done)
        rows = json.load(open(CW._first_path(), encoding="utf-8"))[HOST]
        self.assertEqual(len(rows), 1, "only a NEW window's first turn pays the open")
        self.assertEqual((rows[0]["model"], rows[0]["load_s"], rows[0]["prompt_s"], rows[0]["prompt_n"]),
                         (MODEL, 2.0, 1.0, 9418))
        self.assertGreaterEqual(rows[0]["first_s"], 10.0)
        s, n = CW.first_output_s(HOST)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(s, rows[0]["first_s"] - 2.0, places=2)

    def test_the_last_twenty_are_kept_and_the_median_leaves_the_load_out(self):
        w = CW.Window(HOST, MODEL, CTX, "n", self.d, "d1")
        for i in range(25):
            w.opened = 100.0
            CW.record_first(w, 110.0 + i, {"load_duration": 4e9})
        rows = json.load(open(CW._first_path(), encoding="utf-8"))[HOST]
        self.assertEqual(len(rows), 20)
        xs = sorted(r["first_s"] - 4.0 for r in rows)
        self.assertEqual(CW.first_output_s(HOST), (xs[10], 20))

    def test_nothing_seen_on_a_box_is_no_number(self):
        self.assertEqual(CW.first_output_s("softserve"), (None, 0))

    def test_every_turn_records_its_first_visible_output_and_the_menus_median_leaves_new_windows_out(self):
        """Measure first visible output; menu median excludes new windows."""
        done = dict(CHUNKS[-1], load_duration=0, prompt_eval_duration=5e8, prompt_eval_count=900)
        self._turn(done)
        self._turn(done)
        self._turn(done)
        rows = json.load(open(CW._turns_path(), encoding="utf-8"))[HOST]
        self.assertEqual([r["first_turn"] for r in rows], [True, False, False])
        self.assertEqual({r["model"] for r in rows}, {MODEL})
        self.assertTrue(all(r["first_s"] >= 0 and r["prompt_n"] == 900 for r in rows))
        s, n = CW.turn_first_output_s(HOST, MODEL)
        self.assertEqual(n, 2, "the new window's first turn is not a warm-window number")
        self.assertEqual(CW.turn_first_output_s(HOST, "qwen3:8b"), (None, 0), "another model: unmeasured")
        self.assertEqual(len(json.load(open(CW._first_path(), encoding="utf-8"))[HOST]), 1, "new-window record unchanged")

    def test_the_turn_store_keeps_the_last_fifty_per_host(self):
        w = CW.Window(HOST, MODEL, CTX, "n", self.d, "d1")
        for i in range(55):
            CW.record_turn(w, 100.0, 100.0 + 0.1 * i, {"load_duration": 0}, first_turn=(i == 0))
        rows = json.load(open(CW._turns_path(), encoding="utf-8"))[HOST]
        self.assertEqual(len(rows), CW.TURN_KEEP)
        s, n = CW.turn_first_output_s(HOST, MODEL)
        self.assertEqual(n, CW.TURN_KEEP)
        self.assertAlmostEqual(s, 0.1 * (55 - CW.TURN_KEEP + CW.TURN_KEEP // 2), places=1)

    def test_has_window_names_a_live_window_for_that_model(self):
        self.assertFalse(CW.has_window(HOST, MODEL))
        CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        self.assertTrue(CW.has_window(HOST, MODEL))
        self.assertFalse(CW.has_window(HOST, "qwen3:8b"))


class IdleOnlyWhenNoRunUsesIt(_Base):
    def test_a_run_holds_the_window_across_its_calls_until_it_ends(self):
        w = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        idle = os.path.join(w.dir, "IDLE")
        CW.use(w, "run:r1")
        CW.use(w, "run:r2")
        self.assertFalse(os.path.exists(idle))
        CW.release("run:r1")
        self.assertFalse(os.path.exists(idle), "another run still uses it")
        CW.release("run:r2")
        self.assertTrue(os.path.exists(idle))

    def test_a_run_moving_to_another_model_on_the_same_box_lets_go_of_its_window_there(self):
        a = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        CW.use(a, "run:r1")
        with CW.open_stream(URL + "/api/chat", self.body(model="qwen3:8b", options={"num_ctx": 8192}), None,
                            "run:r1", intake=self.intake):
            pass
        self.assertTrue(os.path.exists(os.path.join(a.dir, "IDLE")),
                        "the first window must be free to yield, or the second waits behind it forever")

    def test_the_run_ledger_block_releases_its_run(self):
        released = []
        with mock.patch.object(config, "CHAT_WINDOWS", True), mock.patch.object(CW, "release", released.append):
            with ollama_client.run_ledger(None):
                key = ollama_client._CALL_CONTEXT.window_run
        self.assertTrue(key.startswith("run:"))
        self.assertEqual(released, [key])


class StrataTurnsRideAWindowToo(_Base):
    """Flash-Next (the agentic crown) is served by Strata through llama-swap. A turn to llama-swap's base becomes a
    `--strata auto` window at the model's measured context; its chunks come back as `data:` lines ending [DONE]."""
    SWAP = "http://10.0.0.76:11435"
    FLASH = "qwen3.8-flash-next-strata-q2_0"
    OAI = [{"choices": [{"delta": {"reasoning_content": "plan "}, "finish_reason": None}]},
           {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
           {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}]

    def setUp(self):
        super().setUp()
        p = mock.patch.object(CW, "_host_for", lambda url: "mothership")
        p.start()
        self.addCleanup(p.stop)

    def test_a_swap_url_is_a_strata_turn_and_an_ollama_or_unknown_one_is_not(self):
        with mock.patch.dict(config.SWAP_MODELS, {self.FLASH: {"host": config.HEAVY_HOST, "base": self.SWAP}}):
            self.assertTrue(CW.is_swap_url(self.SWAP + "/v1/chat/completions"))
            self.assertFalse(CW.is_swap_url(config.HEAVY_HOST + "/v1/chat/completions"))
            self.assertFalse(CW.is_swap_url(self.SWAP + "/api/chat"))

    def test_the_window_is_placed_strata_at_the_measured_context_and_replays_the_openai_wire(self):
        body = {"model": self.FLASH, "messages": [{"role": "user", "content": "hi"}], "stream": True,
                "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "xhigh"},
                "tools": [{"type": "function"}]}
        with mock.patch.dict(config.SWAP_MODELS, {self.FLASH: {"host": config.HEAVY_HOST, "base": self.SWAP}}), \
                mock.patch.object(config, "ctx_for", lambda h, m, d: 262144 if m == self.FLASH else d):
            with CW.open_stream(self.SWAP + "/v1/chat/completions", body, None, "run:r1", intake=self.intake) as resp:
                w = CW._WINDOWS[("mothership", self.FLASH, 262144)]
                self.window_answers(w, self.OAI, row={"error": "", "stream_chunks": 3})
                lines = [x for x in resp.iter_lines() if x]
        spec = self.orders[0][0]
        self.assertEqual(spec["args"][-3:], ["--live", "--strata", "auto"])
        self.assertIn("262144", spec["args"])
        self.assertEqual(lines[:-1], ["data: " + json.dumps(o) for o in self.OAI])
        self.assertEqual(lines[-1], "data: [DONE]")
        with open([os.path.join(w.dir, f) for f in os.listdir(w.dir) if f.endswith(".item.json")][0],
                  encoding="utf-8") as f:
            item = json.load(f)
        self.assertEqual(item["chat_template_kwargs"], {"enable_thinking": True, "reasoning_effort": "xhigh"})
        self.assertNotIn("options", item, "Strata serves its tested context: no num_ctx rides the turn")

    def test_a_strata_turn_asked_again_may_be_parked_by_the_factory_too(self):
        body = {"model": self.FLASH, "messages": [{"role": "user", "content": "hi"}], "stream": True}

        def item_of(**kw):
            with mock.patch.dict(config.SWAP_MODELS, {self.FLASH: {"host": config.HEAVY_HOST, "base": self.SWAP}}), \
                    mock.patch.object(config, "ctx_for", lambda h, m, d: 262144 if m == self.FLASH else d):
                with CW.open_stream(self.SWAP + "/v1/chat/completions", body, None, "run:r1", intake=self.intake, **kw) as resp:
                    w = CW._WINDOWS[("mothership", self.FLASH, 262144)]
                    resp.stream.finished = True
                    path = sorted(f for f in os.listdir(w.dir) if f.endswith(".item.json"))[-1]
                    with open(os.path.join(w.dir, path), encoding="utf-8") as f:
                        return json.load(f)
        self.assertTrue(item_of(conv="u/c1", cut_ok=True).get("cut_ok"))
        self.assertNotIn("cut_ok", item_of(conv="u/c1"), "a caller that would not ask again: the turn finishes")


class EndedWindowsLoseTheirFolders(_Base):
    """Window items hold whole conversations on the shared jobs share; the record is HAI's history. Kirk - the caller
    that sees the end - removes an ENDED window's folder, by its own name, and only that."""

    def _folder(self, name, owner_t=None, close=False):
        d = os.path.join(self.d, name)
        os.makedirs(d)
        if owner_t is not None:
            with open(os.path.join(d, "OWNER.json"), "w", encoding="utf-8") as f:
                json.dump({"run": "kirk:" + name, "period": 15.0, "t": owner_t}, f)
        if close:
            open(os.path.join(d, "CLOSE"), "w").close()
        open(os.path.join(d, "t00001-x.item.json"), "w").close()
        return d

    def test_closed_and_silent_windows_go_live_ones_and_could_not_tell_stay(self):
        now = 10_000.0
        closed = self._folder("hai-win-a", owner_t=now, close=True)
        silent = self._folder("hai-win-b", owner_t=now - 46)            # past 3 x 15 s
        beating = self._folder("hai-win-c", owner_t=now - 20)
        unreadable = self._folder("hai-win-d")                          # no OWNER: could not tell
        other = self._folder("not-a-window", close=True)
        held = CW.window_for(HOST, MODEL, CTX, intake=self.intake)      # this process's own, beating
        with mock.patch.object(CW.notes, "log"):
            CW.sweep_ended(clock=lambda: now)
        self.assertEqual([os.path.exists(p) for p in (closed, silent, beating, unreadable, other, held.dir)],
                         [False, False, True, True, True, True])

    def test_retire_never_leaves_its_own_folder(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with mock.patch.object(CW.notes, "log"):
            CW._retire("hai-win-x/../../" + os.path.basename(outside), "test")
            CW._retire("other", "test")
        self.assertTrue(os.path.isdir(outside))


class KirksOwnWindowIsNotAForeignJob(_Base):
    """A turn for the window's own model joins it; any other model, or a window this process does not hold, reads busy."""

    def setUp(self):
        super().setUp()
        import scheduler
        self.S = scheduler
        self.w = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        self.claim = {"host": HOST, "resource": "gpu", "holder": self.w.name + "_bighonker", "note": ""}

    def test_held_on_names_this_processes_open_window_only(self):
        self.assertEqual(CW.held_on(HOST), MODEL)
        self.assertIsNone(CW.held_on("mothership"))
        open(os.path.join(self.w.dir, "CLOSE"), "w").close()
        self.assertIsNone(CW.held_on(HOST), "an ended window is nobody's")

    def test_occupied_for_lets_the_windows_model_join_and_refuses_any_other(self):
        url = self.S.CARDS[HOST]["url"]
        with mock.patch.object(self.S, "claim_for_url", lambda u, r="gpu": self.claim if r == "gpu" else None), \
                mock.patch.object(self.S, "render_busy", lambda h: (False, "")):
            self.assertIsNone(self.S.occupied_for(url, MODEL))
            self.assertEqual(self.S.occupied_for(url, "nomic-embed-text"), self.claim)
            self.assertEqual(self.S.occupied_for(url, None), self.claim)
            open(os.path.join(self.w.dir, "CLOSE"), "w").close()
            self.assertEqual(self.S.occupied_for(url, MODEL), self.claim, "not ours any more: an order like any other")

    def test_chat_placement_keeps_a_box_whose_only_holder_is_kirks_own_window(self):
        import scheduler as S
        with mock.patch.object(S, "gpu_state", lambda h: (True, "claimed by " + self.claim["holder"])), \
                mock.patch.object(S, "training_busy", lambda h: (True, "compute")), \
                mock.patch.object(S, "gpu_health", lambda h: (True, "ok")), \
                mock.patch.object(S, "_plex_gate", lambda: (True, "idle")), \
                mock.patch.object(S, "ram_free_gb", lambda h: 64.0), \
                mock.patch.object(S, "cpu_load_pct", lambda h: 0.0):
            hosts = [h for h, _s, _w in S.place("chat")]
            self.assertIn(HOST, hosts)
            self.assertNotIn("mothership", hosts, "a box held by another order stays out")
            self.assertNotIn(HOST, [h for h, _s, _w in S.place("vision")], "only chat's own turns join the window")

    def test_mccoy_borrows_kirks_idle_window_only_when_asked_and_every_free_card_wins(self):
        import scheduler as S
        busy = {HOST, "mothership", "softserve"}
        live = {"host": HOST, "resource": S.LIVE, "holder": "hai-chat:Alex", "note": MODEL}
        with mock.patch.object(S, "gpu_state", lambda h: (h in busy, "claimed" if h in busy else "free")), \
                mock.patch.object(S, "training_busy", lambda h: (False, "")), \
                mock.patch.object(S, "gpu_health", lambda h: (True, "ok")), \
                mock.patch.object(S, "active_claim", lambda h, r: live if (h == HOST and r == S.LIVE) else None), \
                mock.patch.object(S, "ram_free_gb", lambda h: 64.0), \
                mock.patch.object(S, "cpu_load_pct", lambda h: 0.0), \
                mock.patch.object(S, "record_placement"):
            self.assertEqual(S.place("advisor"), [], "positive control: every card busy, nothing to borrow unasked")
            got = S.place("advisor", lend=True)
            self.assertEqual([h for h, _s, _w in got], [HOST], "only Kirk's own idle window lends; its live claim "
                                                              "is the conversation it saves")
            self.assertIn("borrows Kirk's idle chat window", got[0][2])
            CW.use(self.w, "run-1")
            self.assertEqual(S.place("advisor", lend=True), [], "a run between tool calls never lends (B9)")
            CW.release("run-1")
            busy.discard("softserve")
            self.assertEqual([h for h, _s, _w in S.place("advisor", lend=True)][0], "softserve",
                             "a free card always outranks a borrowed one")

    def test_brain_host_busy_is_false_for_kirks_own_brain_window_only(self):
        import app
        with mock.patch.object(self.S, "active_claim", lambda h, r: self.claim), \
                mock.patch.object(config, "BRAIN_MODEL", MODEL):
            self.assertFalse(app.brain_host_busy())
            open(os.path.join(self.w.dir, "CLOSE"), "w").close()
            self.assertTrue(app.brain_host_busy())


class LendingTheCard(_Base):
    """Tests the LEND/LENT handshake: window saves state and signals LENT."""

    def setUp(self):
        super().setUp()
        self.w = CW.window_for(HOST, MODEL, CTX, intake=self.intake)
        self.lend_p, self.lent_p = os.path.join(self.w.dir, "LEND"), os.path.join(self.w.dir, "LENT")

    def window_lends(self, body):
        def run():
            for _ in range(500):
                if os.path.exists(self.lend_p):
                    CW._write_json(self.lent_p, body)
                    return
                time.sleep(0.01)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def test_lendable_is_this_processes_idle_ollama_window_only(self):
        self.assertIs(CW.lendable(HOST), self.w)
        self.assertIsNone(CW.lendable("mothership"))
        CW.use(self.w, "run-1")
        self.assertIsNone(CW.lendable(HOST), "a run is using it")
        CW.release("run-1")
        self.w.strata = True
        self.assertIsNone(CW.lendable(HOST), "a Strata window has no ollama slot to lend")
        self.w.strata = False
        open(os.path.join(self.w.dir, "CLOSE"), "w").close()
        self.assertIsNone(CW.lendable(HOST), "an ended window is nobody's")

    def test_lend_waits_for_lent_and_withdraws_lend_by_any_route(self):
        self.window_lends({"t": 1, "saved": True, "n_saved": 6529})
        with CW.lend(self.w, "mccoy", wait_s=5):
            self.assertTrue(os.path.exists(self.lent_p), "the call runs only after the window saved")
        self.assertFalse(os.path.exists(self.lend_p))
        self.assertTrue(os.path.exists(self.lent_p), "LENT stays until the window's next item takes the card back")
        os.remove(self.lent_p)
        self.window_lends({"t": 1, "saved": True})
        with self.assertRaises(ValueError):
            with CW.lend(self.w, "mccoy", wait_s=5):
                raise ValueError("the borrower's call failed")
        self.assertFalse(os.path.exists(self.lend_p), "withdrawn when the borrower's call raises too")

    def test_a_refusal_a_run_or_silence_is_card_not_lent(self):
        self.window_lends({"t": 1, "refused": "a Strata window runs its own engine"})
        with self.assertRaises(CW.CardNotLent):
            with CW.lend(self.w, "mccoy", wait_s=5):
                self.fail("never borrowed")
        self.assertFalse(os.path.exists(self.lend_p) or os.path.exists(self.lent_p), "a refusal leaves nothing")
        CW.use(self.w, "run-1")
        with self.assertRaises(CW.CardNotLent):
            with CW.lend(self.w, "mccoy", wait_s=5):
                self.fail("never borrowed")
        CW.release("run-1")
        t = [0.0]
        with self.assertRaises(CW.CardNotLent):
            with CW.lend(self.w, "mccoy", wait_s=2, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + 1)):
                self.fail("never borrowed")
        self.assertFalse(os.path.exists(self.lend_p), "a silent window keeps no LEND")


class OnlyWhenSwitchedOn(_Base):
    def test_off_by_default_and_only_for_ollama_chat(self):
        seen = []

        class Stream:
            def __init__(self, *a, **k):
                seen.append(a[1])

            def __enter__(self):
                return "window"

            def __exit__(self, *a):
                return False
        with mock.patch.object(CW, "open_stream", Stream), \
                mock.patch.object(ollama_client.httpx, "Client", side_effect=RuntimeError("direct")):
            with mock.patch.object(config, "CHAT_WINDOWS", True), mock.patch.object(config, "CHAT_WINDOWS_USERS", {"*"}):
                with ollama_client._open_chat_stream(URL + "/api/chat", {"model": MODEL}, 5) as r:
                    self.assertEqual(r, "window")
                with self.assertRaises(RuntimeError):
                    with ollama_client._open_chat_stream(URL + "/v1/chat/completions", {}, 5):
                        pass
            with mock.patch.object(config, "CHAT_WINDOWS", False), self.assertRaises(RuntimeError):
                with ollama_client._open_chat_stream(URL + "/api/chat", {"model": MODEL}, 5):
                    pass
        self.assertEqual(len(seen), 1)

    def test_the_gate_runs_with_the_switch_off_and_the_share_redirected(self):
        # run-suite.sh empties HAI_CHAT_WINDOWS and moves HAI_CHAT_WINDOWS_DIR: the gate container inherits production's env, where the switch is on for named users, and the default dir is the LIVE share
        if os.environ.get("HAI_TEST_ISOLATED") != "1" and os.environ.get("HAI_NOTES_DIR") != "/tmp/hai-test-notes":
            self.skipTest("not inside the deploy gate's container")
        self.assertEqual(config.CHAT_WINDOWS_USERS, set())
        self.assertFalse(config.CHAT_WINDOWS_DIR.startswith("/tabjobs"))

    def test_the_switch_widens_by_named_user(self):
        with mock.patch.object(config, "CHAT_WINDOWS_USERS", {"ClaudeSupervisor"}):
            self.assertTrue(CW.enabled_for("ClaudeSupervisor"))
            self.assertFalse(CW.enabled_for("Alex"))
            self.assertFalse(CW.enabled_for(None))
        with mock.patch.object(config, "CHAT_WINDOWS_USERS", {"*"}):
            self.assertTrue(CW.enabled_for("Alex"))
        with mock.patch.object(config, "CHAT_WINDOWS_USERS", set()):
            self.assertFalse(CW.enabled_for("ClaudeSupervisor"))


class IdleRuleFollowsTheEngine(_Base):
    """Ollama windows use the box's own keep_alive as the idle rule; a missing or negative value means no idle rule applies."""

    def test_keep_alive_strings_become_seconds_and_never_means_no_rule(self):
        self.assertEqual(CW.keep_alive_seconds("5m"), 300)
        self.assertEqual(CW.keep_alive_seconds("2m"), 120)
        self.assertEqual(CW.keep_alive_seconds("90s"), 90)
        self.assertEqual(CW.keep_alive_seconds("1h"), 3600)
        self.assertEqual(CW.keep_alive_seconds("45"), 45)
        self.assertIsNone(CW.keep_alive_seconds("-1"), "never unloads = no idle rule")
        self.assertIsNone(CW.keep_alive_seconds("forever"))
        self.assertEqual(CW.keep_alive_seconds("0"), int(CW.PERIOD_S), "unload at once = one owner beat")

    def test_the_rule_is_the_boxs_own_keep_alive(self):
        with mock.patch.object(config, "KEEP_ALIVE", "5m"), \
             mock.patch.object(config, "KEEP_ALIVE_OVERRIDES", {config.FAILOVER_HOST: "2m"}), \
             mock.patch.object(CW, "_url_for", lambda host: config.FAILOVER_HOST if host == "softserve" else URL):
            self.assertEqual(CW._idle_ttl_for("softserve"), 120, "Softserve's own 2m, the value its turns send")
            self.assertEqual(CW._idle_ttl_for(HOST), 300)

    def test_a_strata_window_keeps_its_engines_own_rule_and_carries_no_ttl(self):
        with mock.patch.object(CW, "_url_for", lambda host: URL):
            CW.window_for(HOST, "qwen3.8-flash-next-strata-q2_0", 262144, intake=self.intake, strata=True)
        spec = self.orders[0][0]
        self.assertIn("--strata", spec["args"])
        self.assertNotIn("--idle-ttl", spec["args"])


if __name__ == "__main__":
    unittest.main()
