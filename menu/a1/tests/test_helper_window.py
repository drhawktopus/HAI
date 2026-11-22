"""bin\\helper_window.py (the mirror's copy): items are answered as they appear, written beside them, priced by a live
meter; a half-written item is skipped until whole; CLOSE ends the window; the lead's desk status ends it too; an
HTTP error is a row error, not a crash. The model is faked (chat=); nothing touches ollama."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import urllib.error

HW = r"E:\SSDShared\mothership-build\helper_window.py"
_spec = importlib.util.spec_from_file_location("helper_window", HW)
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)


class FakeMeter:
    source = "nvidia-smi@4Hz"
    samples = [(0, 50.0)]

    def joules(self, t0, t1):
        return 7.5


def chat_ok(host, model, messages, options, think=False):
    return {"message": {"content": "ANS for " + messages[-1]["content"][:12]}, "model": model, "done_reason": "stop",
            "eval_count": 10, "eval_duration": 1e9, "prompt_eval_count": 5}


def write_item(d, iid, body, whole=True):
    p = os.path.join(d, iid + ".item.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        f.write(json.dumps(body) if whole else '{"id": "%s", "prompt": "half' % iid)
    os.replace(p + ".tmp", p)


class HelperWindow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.items = os.path.join(self.d, "items")
        os.makedirs(self.items)
        self.a = types.SimpleNamespace(host="h", model="qwen3:8b", num_ctx=17408, temperature=0.7, seed_base=1, think=False)
        self.prog = os.path.join(self.d, "out.progress")
        self.partial = os.path.join(self.d, "out.json.partial.jsonl")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_items_answered_as_they_appear_half_written_skipped_close_ends(self):
        n = [0]

        def sleep(s):
            n[0] += 1
            if n[0] == 1:
                write_item(self.items, "d1", {"id": "d1", "prompt": "first piece"})
            elif n[0] == 2:
                write_item(self.items, "d2", {}, whole=False)            # shown early by the share
            elif n[0] == 3:
                write_item(self.items, "d2", {"id": "d2", "prompt": "second piece"})
            elif n[0] == 4:
                open(os.path.join(self.items, "CLOSE"), "w").close()
        rows, why = W.serve(self.a, self.items, self.prog, self.partial, FakeMeter(), None, sleep=sleep,
                            clock=lambda: 0.0, chat=chat_ok)
        self.assertEqual(why, "closed by the lead")
        self.assertEqual([r["id"] for r in rows], ["d1", "d2"])
        self.assertEqual([r["error"] for r in rows], ["", ""], "the half-written item was waited for, not errored")
        out1 = json.load(open(os.path.join(self.items, "d1.out.json"), encoding="utf-8"))
        self.assertEqual(out1["answer"], "ANS for first piece")
        self.assertEqual(out1["gpu_j"], 7.5, "a live meter prices the row with the answer")
        self.assertEqual(out1["seed"], 2, "seed_base + round, as infer_ollama --rounds 1")
        self.assertEqual(sorted(os.listdir(self.prog)), ["d1.done", "d2.done"])
        self.assertEqual(len(open(self.partial, encoding="utf-8").read().splitlines()), 2)

    def test_the_leads_desk_status_ends_the_window(self):
        t = [0.0]

        def sleep(s):
            t[0] += 31.0

        def status():
            return "failed" if t[0] > 60 else "running"
        rows, why = W.serve(self.a, self.items, self.prog, self.partial, FakeMeter(), status, sleep=sleep,
                            clock=lambda: t[0], chat=chat_ok)
        self.assertEqual((rows, why), ([], "the lead's order is failed"))

    def test_a_restarted_window_keeps_what_it_answered_and_answers_only_the_new(self):
        # the factory's yield contract (Plex demand, a lost card): same {ORDER_OUT}, the partial file holds the rows
        with open(self.partial, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "d1", "answer": "old", "error": "", "wall_s": 1.0}) + "\n")
            f.write('{"id": "d2", "answer": "cut by the st')                    # the line the stop cut
        write_item(self.items, "d1", {"id": "d1", "prompt": "first"})
        with open(os.path.join(self.items, "d1.out.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "d1", "answer": "old"}, f)
        n = [0]

        def sleep(s):
            n[0] += 1
            if n[0] == 1:
                write_item(self.items, "d3", {"id": "d3", "prompt": "third"})
            else:
                open(os.path.join(self.items, "CLOSE"), "w").close()
        rows, why = W.serve(self.a, self.items, self.prog, self.partial, FakeMeter(), None, sleep=sleep,
                            clock=lambda: 0.0, chat=chat_ok)
        self.assertEqual([r["id"] for r in rows], ["d1", "d3"], "d1 kept from the partial, d3 new, the cut line dropped")
        self.assertEqual(rows[0]["answer"], "old", "a kept row is not re-asked")
        self.assertEqual(len(open(self.partial, encoding="utf-8").read().splitlines()), 3)

    def test_an_http_error_is_a_row_error(self):
        def chat_500(host, model, messages, options, think=False):
            raise urllib.error.HTTPError("u", 500, "boom", {}, None)
        row = W.answer_item(self.a, {"id": "d9", "prompt": "x"}, FakeMeter(), chat=chat_500)
        self.assertTrue(row["error"].startswith("HTTP 500"), row)
        self.assertEqual(row["answer"], "")

    def test_an_amd_meter_leaves_the_row_unpriced_until_close(self):
        class Amd(FakeMeter):
            source = "telemetry-csv"
        row = W.answer_item(self.a, {"id": "d1", "prompt": "x"}, Amd(), chat=chat_ok)
        self.assertIsNone(row["gpu_j"], "never 0: priced at close from the telemetry log")


class ErrandTurn(unittest.TestCase):
    """An ERRAND turn (Phase 7 "Two shapes of delegation"): the item carries the conversation and the tools; the reply's
    tool calls come back in the row for the lead's harness to run. A plain piece sends no tools at all."""

    def setUp(self):
        self.a = types.SimpleNamespace(host="h", model="qwen3:8b", num_ctx=17408, temperature=0.6, seed_base=1,
                                       think=False)
        self.seen = {}

    def chat(self, host, model, messages, options, think=False, **kw):
        self.seen.update(messages=messages, kw=kw)
        if kw.get("tools"):
            return {"message": {"content": "", "tool_calls": [{"function": {"name": "read_file",
                                                                             "arguments": {"path": "a.py"}}}]},
                    "model": model, "done_reason": "stop"}
        return chat_ok(host, model, messages, options, think)

    def test_messages_and_tools_reach_the_model_and_tool_calls_come_back(self):
        tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
        msgs = [{"role": "user", "content": "find X"}, {"role": "assistant", "content": "", "tool_calls": []},
                {"role": "tool", "content": "listing"}]
        row = W.answer_item(self.a, {"id": "e1", "system": "you run errands", "messages": msgs, "tools": tools},
                            FakeMeter(), chat=self.chat)
        self.assertEqual(self.seen["messages"][0], {"role": "system", "content": "you run errands"})
        self.assertEqual(self.seen["messages"][1:], msgs)
        self.assertEqual(self.seen["kw"], {"tools": tools})
        self.assertEqual(row["tool_calls"][0]["function"]["name"], "read_file")

    def test_a_plain_piece_sends_no_tools_and_has_no_tool_calls(self):
        row = W.answer_item(self.a, {"id": "d1", "prompt": "write f"}, FakeMeter(), chat=self.chat)
        self.assertEqual(self.seen["kw"], {})
        self.assertNotIn("tool_calls", row)
        self.assertTrue(row["answer"].startswith("ANS for write f"))


class LiveTurnsStream(unittest.TestCase):
    """A "stream": true item (a HAI chat turn in a window order) gets `<id>.stream.jsonl`: an attempt header, then every
    chunk the engine sent, in order; the .out.json row stays the answer and the end marker. Other items are untouched."""

    CHUNKS = [{"message": {"thinking": "hm "}, "done": False}, {"message": {"content": "hel"}, "done": False},
              {"message": {"content": "lo"}, "done": True, "done_reason": "stop"}]

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.items = os.path.join(self.d, "items")
        os.makedirs(self.items)
        self.a = types.SimpleNamespace(host="h", model="qwen3.8:27b", num_ctx=131072, temperature=None, seed_base=0,
                                       think="xhigh")
        self.kw = []

    def chat(self, host, model, messages, options, think=False, **kw):
        self.kw.append(sorted(kw))
        for c in self.CHUNKS:
            if kw.get("on_chunk"):
                kw["on_chunk"](c)
        return {"message": {"content": "hello", "thinking": "hm "}, "model": model, "done_reason": "stop"}

    def _serve(self, *bodies):
        n = [0]

        def sleep(s):
            n[0] += 1
            if n[0] == 1:
                for b in bodies:
                    write_item(self.items, b["id"], b)
            else:
                open(os.path.join(self.items, "CLOSE"), "w").close()
        return W.serve(self.a, self.items, os.path.join(self.d, "p"), os.path.join(self.d, "partial.jsonl"),
                       FakeMeter(), sleep=sleep, chat=self.chat)

    def _lines(self, iid):
        with open(os.path.join(self.items, iid + ".stream.jsonl"), encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def test_a_live_turn_streams_every_chunk_and_still_writes_its_row(self):
        rows, why = self._serve({"id": "t1", "messages": [{"role": "user", "content": "hi"}], "stream": True},
                                {"id": "p1", "prompt": "plain piece"})
        lines = self._lines("t1")
        self.assertEqual(set(lines[0]), {"attempt", "id"})
        self.assertEqual(lines[0]["id"], "t1")
        self.assertEqual(lines[1:], self.CHUNKS)
        self.assertEqual([r["id"] for r in rows], ["p1", "t1"])      # sorted names: p1 before t1
        with open(os.path.join(self.items, "t1.out.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["answer"], "hello")
        self.assertFalse(os.path.exists(os.path.join(self.items, "p1.stream.jsonl")))
        self.assertEqual(self.kw, [[], ["on_chunk"]], "only the live turn hands the engine a relay")
        with open(os.path.join(self.items, "t1.out.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["stream_chunks"], len(self.CHUNKS), "the reader's count of what to expect")
        with open(os.path.join(self.items, "p1.out.json"), encoding="utf-8") as f:
            self.assertNotIn("stream_chunks", json.load(f))

    def test_a_turn_names_its_own_effort_and_a_foreign_effort_or_model_is_refused(self):
        sent = []

        def chat(host, model, messages, options, think=False, **kw):
            sent.append(think)
            return {"message": {"content": "x"}, "model": model, "done_reason": "stop"}
        self.a.think = False
        W.answer_item(self.a, {"id": "t1", "prompt": "hi", "think": "xhigh"}, FakeMeter(), chat=chat)
        W.answer_item(self.a, {"id": "t2", "prompt": "hi"}, FakeMeter(), chat=chat)
        self.assertEqual(sent, ["xhigh", False], "the turn's effort, else the window's own")
        high = W.answer_item(self.a, {"id": "t3", "prompt": "hi", "think": "high"}, FakeMeter(), chat=chat)
        other = W.answer_item(self.a, {"id": "t4", "prompt": "hi", "model": "qwen3:8b"}, FakeMeter(), chat=chat)
        self.assertEqual(len(sent), 2, "neither refused turn reached the model")
        self.assertIn("'high'", high["error"])
        self.assertIn("qwen3:8b", other["error"])

    def test_the_turns_residency_rule_reaches_the_engine_and_a_foreign_window_size_is_refused(self):
        kws = []

        def chat(host, model, messages, options, think=False, **kw):
            kws.append(kw)
            return {"message": {"content": "x"}, "model": model, "done_reason": "stop"}
        W.answer_item(self.a, {"id": "t1", "prompt": "hi", "keep_alive": "2m"}, FakeMeter(), chat=chat)
        W.answer_item(self.a, {"id": "t2", "prompt": "hi", "options": {"num_ctx": 131072}}, FakeMeter(), chat=chat)
        bad = W.answer_item(self.a, {"id": "t3", "prompt": "hi", "options": {"num_ctx": 8192}}, FakeMeter(), chat=chat)
        self.assertEqual([k.get("keep_alive") for k in kws], ["2m", None], "Softserve's 2m (Plex first) is kept")
        self.assertIn("8192", bad["error"])

    def test_a_stop_marker_ends_the_generation_and_the_row_says_so(self):
        relayed = []

        def chat(host, model, messages, options, think=False, on_chunk=None, **kw):
            for i in range(100):
                on_chunk({"message": {"content": "x"}, "done": False})
                relayed.append(i)
                if i == 2:
                    open(os.path.join(self.items, "t1.cancel"), "w").close()
            return {"message": {"content": "never"}, "model": model, "done_reason": "stop"}
        orig = W.CANCEL_CHECK_S
        W.CANCEL_CHECK_S = 0.0
        try:
            row = W.answer_item(self.a, {"id": "t1", "prompt": "hi", "stream": True}, FakeMeter(), chat=chat,
                                stream_path=os.path.join(self.items, "t1.stream.jsonl"))
        finally:
            W.CANCEL_CHECK_S = orig
        self.assertIn("stopped", row["error"])
        self.assertLess(len(relayed), 5, "the engine stops streaming within a check of the Stop")

    def test_a_reanswered_turn_starts_its_stream_over_under_a_new_attempt(self):
        stale = os.path.join(self.items, "t1.stream.jsonl")
        with open(stale, "w", encoding="utf-8") as f:
            f.write(json.dumps({"attempt": "old", "id": "t1"}) + "\n" + json.dumps({"message": {"content": "x"}}) + "\n")
        W.answer_item(self.a, {"id": "t1", "prompt": "hi", "stream": True}, FakeMeter(), chat=self.chat,
                      stream_path=stale)
        lines = self._lines("t1")
        self.assertNotEqual(lines[0]["attempt"], "old")
        self.assertEqual(lines[1:], self.CHUNKS, "nothing of the earlier attempt survives")


class LiveOwnerAndYield(unittest.TestCase):
    """A HAI run's window: it ends when its owner's heartbeat goes silent past 3 x the declared period, and - only while
    the owner waits on its user (IDLE) - when card work waits at the desk. Could-not-tell never ends it."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.items = os.path.join(self.d, "items")
        os.makedirs(self.items)
        self.a = types.SimpleNamespace(host="h", model="qwen3.8:27b", num_ctx=131072, temperature=None, seed_base=0,
                                       think=False)
        self.now = [1000.0]

    def owner(self, t, period=10):
        with open(os.path.join(self.items, "OWNER.json"), "w", encoding="utf-8") as f:
            json.dump({"run": "r1", "period": period, "t": t}, f)

    def serve(self, card_waiting=None, max_polls=10):
        n = [0]

        def sleep(s):
            n[0] += 1
            self.now[0] += W.DESK_EVERY_S
            if n[0] >= max_polls:
                open(os.path.join(self.items, "CLOSE"), "w").close()
        return W.serve(self.a, self.items, os.path.join(self.d, "p"), os.path.join(self.d, "partial.jsonl"),
                       FakeMeter(), sleep=sleep, clock=lambda: self.now[0], chat=chat_ok, card_waiting=card_waiting)

    def test_a_silent_owner_ends_the_window_and_a_beating_one_does_not(self):
        self.owner(t=self.now[0])                      # period 10 s, beat never refreshed
        rows, why = self.serve()
        self.assertIn("silent", why)
        self.assertIn("r1", why)

    def test_an_unreadable_owner_is_no_verdict(self):
        with open(os.path.join(self.items, "OWNER.json"), "w") as f:
            f.write("{half")
        rows, why = self.serve(max_polls=3)
        self.assertEqual(why, "closed by the lead")

    def test_waiting_card_work_ends_an_idle_window_only(self):
        self.owner(t=10 ** 9)                          # a beat in the future: alive throughout
        rows, why = self.serve(card_waiting=lambda: True, max_polls=3)
        self.assertEqual(why, "closed by the lead", "mid-run (no IDLE: a tool may be running, B9) it holds")
        os.remove(os.path.join(self.items, "CLOSE"))   # the first window's end, not the second's
        open(os.path.join(self.items, "IDLE"), "w").close()
        rows, why = self.serve(card_waiting=lambda: True, max_polls=3)
        self.assertIn("yielded between turns", why)

    def test_a_live_window_with_no_owner_closes_and_a_lead_window_does_not(self):
        rows, why = self.serve(card_waiting=lambda: False, max_polls=3)       # --live, no OWNER.json
        self.assertIn("no owner", why)
        os.remove(os.path.join(self.items, "CLOSE")) if os.path.exists(os.path.join(self.items, "CLOSE")) else None
        rows, why = self.serve(card_waiting=None, max_polls=3)                # an A1 lead-order window
        self.assertEqual(why, "closed by the lead")

    def test_an_idle_engine_window_ends_after_its_engines_idle_rule_and_a_busy_one_does_not(self):
        # Strata does not unload itself like ollama: the window applies llama-swap's ttl 300 (the box's 5m keep_alive)
        self.owner(t=10 ** 9)
        idle = os.path.join(self.items, "IDLE")
        open(idle, "w").close()
        os.utime(idle, (self.now[0] - 400, self.now[0] - 400))
        n = [0]

        def sleep(s):
            n[0] += 1
            self.now[0] += W.DESK_EVERY_S
            if n[0] >= 3:
                open(os.path.join(self.items, "CLOSE"), "w").close()
        rows, why = W.serve(self.a, self.items, os.path.join(self.d, "p"), os.path.join(self.d, "partial.jsonl"),
                            FakeMeter(), sleep=sleep, clock=lambda: self.now[0], chat=chat_ok, idle_ttl=300)
        self.assertIn("idle", why)
        os.remove(idle)                                                 # a run is using it: never idle-closed
        os.remove(os.path.join(self.items, "CLOSE")) if os.path.exists(os.path.join(self.items, "CLOSE")) else None
        n[0] = 0
        rows, why = W.serve(self.a, self.items, os.path.join(self.d, "p"), os.path.join(self.d, "partial.jsonl"),
                            FakeMeter(), sleep=sleep, clock=lambda: self.now[0], chat=chat_ok, idle_ttl=300)
        self.assertEqual(why, "closed by the lead")

    def test_a_desk_that_cannot_be_asked_never_yields(self):
        self.owner(t=10 ** 9)
        open(os.path.join(self.items, "IDLE"), "w").close()
        rows, why = self.serve(card_waiting=lambda: None, max_polls=3)
        self.assertEqual(why, "closed by the lead")


class StrataWindows(unittest.TestCase):
    """A Flash-Next window (--strata auto): this box's strata_served.json names the plan; llama-swap's daily engine lets go
    first; the engine starts through strata_arm's lifecycle WITHOUT frontier mode (daily use) and stops by any route;
    turns ride the openai wire with Kirk's effort read back from chat_template_kwargs."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        from unittest import mock
        self.mock = mock

    def _served(self, rows):
        p = os.path.join(self.d, "strata_served.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        fake = types.SimpleNamespace(STRATA_SERVED=p, FLEET_HOST="mothership")
        return self.mock.patch.dict(sys.modules, {"p20_foundry": fake})

    def test_auto_resolves_this_boxes_plan_and_refuses_a_model_or_context_it_does_not_serve(self):
        with self._served({"mothership": {"flash": {"plan": "C:\\plans\\v045.json", "ctx": 262144}}}):
            self.assertEqual(W.strata_plan("auto", "flash", 262144), "C:\\plans\\v045.json")
            for model, ctx in (("flash", 131072), ("other", 262144)):
                with self.assertRaises(SystemExit) as cm:
                    W.strata_plan("auto", model, ctx)
                self.assertEqual(cm.exception.code, 2)
        self.assertEqual(W.strata_plan("D:\\x.json", "any", 1), "D:\\x.json", "an explicit plan passes through")

    def test_llama_swap_is_unloaded_only_when_something_runs(self):
        calls = []

        class R:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self.body

        def urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            calls.append(url.rsplit("/", 2)[-1] if "unload" in url else "running")
            return R(json.dumps({"running": state}).encode())
        for state in ([], [{"model": "flash", "state": "ready"}]):
            calls.clear()
            with self.mock.patch.object(W.urllib.request, "urlopen", urlopen), \
                    self.mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
                W.free_swap("http://x:11435")
            self.assertEqual(calls, ["running"] if not state else ["running", "unload"])

    def test_a_strata_turn_drops_the_ollama_window_options_and_reads_kirks_effort_back(self):
        a = types.SimpleNamespace(host="http://127.0.0.1:8080", strata_sampler={"top_p": 0.95}, model="flash",
                                  num_ctx=262144, temperature=None, seed_base=0, think=False)
        sent = []

        def openai(base, messages, opts, think=False, tools=None, model=None, on_chunk=None, **kw):
            sent.append((base, opts, think, tools, model, bool(on_chunk)))
            return {"message": {"content": "ok"}, "model": model, "done_reason": "stop"}
        with self.mock.patch.object(W.IO, "_chat_openai", openai):
            chat = W.strata_chat(a)
            for kw, want in (({"enable_thinking": False}, False), ({}, True),
                             ({"enable_thinking": True, "reasoning_effort": "xhigh"}, "xhigh")):
                W.answer_item(a, {"id": "t", "messages": [{"role": "user", "content": "hi"}], "chat_template_kwargs": kw,
                                  "tools": [{"type": "function"}]}, FakeMeter(), chat=chat)
                self.assertEqual(sent[-1][2], want)
        base, opts, _t, tools, model, _r = sent[0]
        self.assertEqual((base, opts, tools, model), ("http://127.0.0.1:8080", {"top_p": 0.95}, [{"type": "function"}],
                                                      "flash"), "no num_ctx/num_predict reach Strata")

    def test_a_live_window_without_an_owner_never_starts_an_engine(self):
        started = []
        with self.mock.patch.object(W, "strata_plan", lambda s, m, c: started.append("plan") or "plan.json"), \
                self.mock.patch.object(W.IO, "_strata_up", lambda a, frontier=True: started.append("up")), \
                self.mock.patch.object(W.IO, "_assert_resident", lambda *x, **k: started.append("load")), \
                self.mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
            for extra in (["--strata", "auto"], []):
                items = tempfile.mkdtemp(dir=self.d)
                rc = W.main([items, os.path.join(self.d, "out.json"), "--model", "flash", "--num-ctx", "262144",
                             "--live"] + extra)
                self.assertEqual(rc, 0)
                self.assertTrue(os.path.exists(os.path.join(items, "CLOSE")))
        self.assertEqual(started, [], "no plan read, no engine started, no model loaded")

    def test_the_engine_stops_by_any_route_and_never_enters_frontier_mode(self):
        events = []
        stopper = types.SimpleNamespace(stop=lambda proc, port: events.append(("stop", port)))

        def up(a, frontier=True):
            events.append(("up", frontier))
            a.host = "http://127.0.0.1:8080"
            return (stopper, "proc", 8080), {"engine": "strata"}

        def boom(*a, **k):
            raise RuntimeError("the window died")
        meter = types.SimpleNamespace(source=None, samples=[], stop=lambda: None, joules=lambda *x: None)
        os.makedirs(os.path.join(self.d, "items"))
        with open(os.path.join(self.d, "items", "OWNER.json"), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": __import__("time").time()}, f)   # a live owner
        with self.mock.patch.object(W, "strata_plan", lambda s, m, c: "plan.json"), \
                self.mock.patch.object(W, "free_swap", lambda: events.append("free_swap")), \
                self.mock.patch.object(W.IO, "_strata_up", up), \
                self.mock.patch.object(W.IO, "PowerMeter", return_value=meter), \
                self.mock.patch.object(W, "serve", boom):
            with self.assertRaises(RuntimeError):
                W.main([os.path.join(self.d, "items"), os.path.join(self.d, "out.json"), "--model", "flash",
                        "--num-ctx", "262144", "--live", "--strata", "auto"])
        self.assertEqual(events, ["free_swap", ("up", False), ("stop", 8080)])


class QuickResumeBetweenTurns(unittest.TestCase):
    """#34 B11b: a live window that yields its card saves ollama's runner slot; the next window on the box for the same
    model and context restores it once before answering - and nothing about it can make a turn worse."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.a = types.SimpleNamespace(model="qwen3.5:9b", num_ctx=16384, strata=None, live=True)
        self.calls = []

    def call(self, port, action, filename):
        self.calls.append((port, action, filename))
        return {"n_saved": 6529, "n_restored": 6529, "timings": {"save_ms": 300, "restore_ms": 150}}

    def test_a_yield_saves_under_the_windows_model_and_context(self):
        # lossless: the engine writes <name>.new, and only a whole file replaces the slot
        def engine_writes(port, action, filename):
            open(os.path.join(self.d, filename), "wb").close()
            return self.call(port, action, filename)
        r = W.save_slot(self.a, port_fn=lambda: 54910, call=engine_writes, slot_dir=self.d)
        self.assertEqual(self.calls, [(54910, "save", "win-qwen3.5-9b-16384.bin" + W.NEW)])
        self.assertEqual(r["n_saved"], 6529)
        self.assertEqual(os.listdir(self.d), ["win-qwen3.5-9b-16384.bin"], "the whole file replaced the slot")

    def test_a_save_the_engine_did_not_finish_keeps_the_last_good_state(self):
        open(os.path.join(self.d, "win-qwen3.5-9b-16384.bin"), "wb").write(b"last good")
        self.assertIsNone(W.save_slot(self.a, port_fn=lambda: 54910, call=self.call, slot_dir=self.d))
        self.assertEqual(open(os.path.join(self.d, "win-qwen3.5-9b-16384.bin"), "rb").read(), b"last good")

    def test_a_saved_slot_is_restored_once_and_retired(self):
        open(os.path.join(self.d, W.slot_file("qwen3.5:9b", 16384)), "wb").close()
        W.restore_slot(self.a, port_fn=lambda: 55032, call=self.call, slot_dir=self.d)
        self.assertEqual(self.calls, [(55032, "restore", "win-qwen3.5-9b-16384.bin")])
        self.assertFalse(os.listdir(self.d), "the slot file is spent")
        self.assertIsNone(W.restore_slot(self.a, port_fn=lambda: 55032, call=self.call, slot_dir=self.d))
        self.assertEqual(len(self.calls), 1, "nothing to restore the second time")
        other = types.SimpleNamespace(model="qwen3.5:9b", num_ctx=39936, strata=None)
        open(os.path.join(self.d, W.slot_file("qwen3.5:9b", 16384)), "wb").close()
        self.assertIsNone(W.restore_slot(other, port_fn=lambda: 1, call=self.call, slot_dir=self.d),
                          "another context is another window: its slot is not this one")

    def test_no_runner_or_a_failing_call_is_never_worse_and_strata_is_left_alone(self):
        def boom(*a):
            raise OSError("runner gone")
        self.assertIsNone(W.save_slot(self.a, port_fn=lambda: None, call=self.call))
        self.assertIsNone(W.save_slot(self.a, port_fn=lambda: 1, call=boom))
        open(os.path.join(self.d, W.slot_file("qwen3.5:9b", 16384)), "wb").close()
        self.assertIsNone(W.restore_slot(self.a, port_fn=lambda: 1, call=boom, slot_dir=self.d))
        self.assertFalse(os.listdir(self.d), "a restore that failed is retired too - never retried forever")
        strata = types.SimpleNamespace(model="flash", num_ctx=262144, strata="plan.json")
        open(os.path.join(self.d, W.slot_file("flash", 262144)), "wb").close()
        self.assertIsNone(W.save_slot(strata, port_fn=lambda: 1, call=self.call))
        self.assertIsNone(W.restore_slot(strata, port_fn=lambda: 1, call=self.call, slot_dir=self.d))
        self.assertEqual(self.calls, [])

    def test_main_restores_after_the_load_and_saves_only_when_it_yields(self):
        from unittest import mock
        meter = types.SimpleNamespace(source=None, samples=[], stop=lambda: None, joules=lambda *x: None)
        for why, want in (("yielded between turns: card work is waiting at the desk", ["load", "restore", "save"]),
                          ("closed by the lead", ["load", "restore"])):
            events = []
            items = tempfile.mkdtemp(dir=self.d)
            with open(os.path.join(items, "OWNER.json"), "w", encoding="utf-8") as f:
                json.dump({"run": "kirk:test", "period": 15, "t": __import__("time").time()}, f)
            with mock.patch.object(W.IO, "_assert_resident", lambda *x, **k: events.append("load") or {}), \
                    mock.patch.object(W, "restore_slot", lambda a, **k: events.append("restore")), \
                    mock.patch.object(W, "save_slot", lambda a, **k: events.append("save")), \
                    mock.patch.object(W.IO, "PowerMeter", return_value=meter), \
                    mock.patch.object(W, "serve", lambda *x, **k: ([], why)), \
                    mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
                W.main([items, os.path.join(self.d, "out.json"), "--model", "qwen3.5:9b", "--num-ctx", "16384", "--live"])
            self.assertEqual(events, want, why)


class LendingTheCard(unittest.TestCase):
    """#34 Phase 8: Kirk lends an idle live window's card to McCoy (LEND); the window saves the conversation, answers LENT,
    and reloads + restores before its next item - and nothing about it can save the wrong runner or make a turn worse."""

    def setUp(self):
        from unittest import mock
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.items = os.path.join(self.d, "items")
        os.makedirs(self.items)
        # a live window serves only while its owner beats (owner_verdict); the owner beats at the tests' clock, t = 0
        with open(os.path.join(self.items, W.OWNER), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": 0.0}, f)
        self.a = types.SimpleNamespace(host="h", model="qwen3.5:9b", num_ctx=16384, temperature=None, seed_base=0,
                                       think=False, strata=None, items_dir=self.items)
        self.events = []
        self.on_card = True
        for target, name, fn in (
                (W, "save_slot", lambda a, **k: self.events.append("save") or {"n_saved": 6529}),
                (W, "restore_slot", lambda a, **k: self.events.append("restore")),
                (W.IO, "_assert_resident", lambda *x, **k: self.events.append("load") or {}),
                (W.IO, "_residency", lambda host, model: (1, 1) if self.on_card else (None, None)),
                (W.IO, "_spill_reading", self.no_spill_reading)):
            p = mock.patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def no_spill_reading(self):
        raise AssertionError("a lend must not read the spill counters - /api/ps says which model holds the card")

    def mark(self, name, on=True):
        p = os.path.join(self.items, name)
        if on:
            open(p, "w").close()
        elif os.path.exists(p):
            os.remove(p)

    def chat(self, host, model, messages, options, think=False):
        self.events.append("answer")
        return chat_ok(host, model, messages, options, think)

    def serve(self, script, card_waiting=lambda: False):
        n = [0]

        def sleep(s):
            n[0] += 1
            (script.get(n[0]) or (lambda: self.mark("CLOSE")))()
        return W.serve(self.a, self.items, os.path.join(self.d, "p"), os.path.join(self.d, "partial.jsonl"),
                       FakeMeter(), None, sleep=sleep, clock=lambda: 0.0, chat=self.chat, card_waiting=card_waiting)

    def lent(self):
        return json.load(open(os.path.join(self.items, "LENT"), encoding="utf-8"))

    def test_idle_window_saves_answers_lent_and_takes_the_card_back_before_its_next_item(self):
        self.mark("IDLE")
        self.mark("LEND")
        seen = {}

        def turn():
            seen["lent"] = self.lent()
            self.mark("IDLE", False)
            write_item(self.items, "t2", {"id": "t2", "prompt": "next turn"})
        rows, why = self.serve({1: turn})
        self.assertEqual(seen["lent"]["saved"], True)
        self.assertEqual(seen["lent"]["n_saved"], 6529)
        self.assertEqual(self.events, ["save", "load", "restore", "answer"],
                         "saved at the lend; model back and slot restored BEFORE the next turn is answered")
        self.assertEqual([r["id"] for r in rows], ["t2"])
        self.assertFalse(os.path.exists(os.path.join(self.items, "LENT")))
        self.assertFalse(os.path.exists(os.path.join(self.items, "LEND")),
                         "both markers gone: a LEND a dead Kirk left is never answered twice")

    def test_a_window_in_use_does_not_lend(self):
        self.mark("LEND")
        self.serve({})
        self.assertEqual(self.events, [])
        self.assertFalse(os.path.exists(os.path.join(self.items, "LENT")), "the lend waits for the run to finish")

    def test_another_model_on_the_card_is_never_saved_as_this_conversation(self):
        self.on_card = False
        self.mark("IDLE")
        self.mark("LEND")
        seen = {}
        self.serve({1: lambda: seen.update(lent=self.lent())})
        self.assertNotIn("save", self.events)
        self.assertEqual(seen["lent"]["saved"], False)
        self.assertTrue(seen["lent"]["why"])

    def test_a_strata_window_refuses_and_a_batch_window_ignores_the_marker(self):
        self.a.strata = "plan.json"
        self.mark("IDLE")
        self.mark("LEND")
        seen = {}
        self.serve({1: lambda: seen.update(lent=self.lent())})
        self.assertIn("refused", seen["lent"])
        self.assertEqual(self.events, [])
        os.remove(os.path.join(self.items, "LENT"))
        os.remove(os.path.join(self.items, "CLOSE"))
        self.a.strata = None
        self.serve({}, card_waiting=None)
        self.assertEqual(self.events, [], "a helper window (no live owner) has no lender")
        self.assertFalse(os.path.exists(os.path.join(self.items, "LENT")))

    def test_a_yield_while_lent_does_not_save_the_borrowers_runner(self):
        from unittest import mock
        meter = types.SimpleNamespace(source=None, samples=[], stop=lambda: None, joules=lambda *x: None)
        with open(os.path.join(self.items, "OWNER.json"), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": __import__("time").time()}, f)
        self.mark("LENT")
        with mock.patch.object(W.IO, "PowerMeter", return_value=meter), \
                mock.patch.object(W, "serve", lambda *x, **k: ([], "yielded between turns: card work is waiting")), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
            W.main([self.items, os.path.join(self.d, "out.json"), "--model", "qwen3.5:9b", "--num-ctx", "16384",
                    "--live"])
        self.assertEqual(self.events, ["load", "restore"], "no save: the card holds McCoy's model, not this window's")


class ThinkLevelReachesTheWindow(unittest.TestCase):
    """A placed window's policy row may send `--think-level xhigh`; parse_known_args would otherwise drop it silently and
    the helper would run with thinking OFF."""

    def test_think_level_sets_think(self):
        from unittest import mock
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        seen = {}

        def serve(a, *rest, **kw):
            seen["think"] = a.think
            return [], "test"
        meter = types.SimpleNamespace(source=None, samples=[], stop=lambda: None, joules=lambda *x: None)
        with mock.patch.object(W.IO, "_assert_resident", return_value={"size_vram": 1, "size": 1}), \
                mock.patch.object(W.IO, "PowerMeter", return_value=meter), mock.patch.object(W, "serve", serve):
            W.main([os.path.join(d, "items"), os.path.join(d, "out.json"), "--model", "qwen3.5:9b",
                    "--think-level", "xhigh"])
        self.assertEqual(seen["think"], "xhigh")

    def test_the_window_marks_its_own_end_with_close_by_any_route(self):
        # a client waiting on the share (the Terminal-Bench relay) must see the window end, or it waits on as an orphan
        from unittest import mock
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        items = os.path.join(d, "items")
        meter = types.SimpleNamespace(source=None, samples=[], stop=lambda: None, joules=lambda *x: None)

        def boom(*a, **k):
            raise RuntimeError("the window died")
        with mock.patch.object(W.IO, "_assert_resident", return_value={"size_vram": 1, "size": 1}), \
                mock.patch.object(W.IO, "PowerMeter", return_value=meter), mock.patch.object(W, "serve", boom):
            with self.assertRaises(RuntimeError):
                W.main([items, os.path.join(d, "out.json"), "--model", "qwen3:8b"])
        self.assertTrue(os.path.exists(os.path.join(items, "CLOSE")), "an exception still leaves CLOSE behind")


if __name__ == "__main__":
    unittest.main()
