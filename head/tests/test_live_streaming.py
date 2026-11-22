"""Live streaming invariants: chunks must stream as they arrive (cuts discarded); feed is a single authoritative log with lossless delta merging; user messages are stored on accept, isolated from the model transcript, and persist across routes; chat state is fixed on accept; locked users cannot access other users' runs."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import cancellation  # noqa: E402
import history  # noqa: E402
import live_runs  # noqa: E402
import ollama_client  # noqa: E402


def _lines(resp_text):
    return [json.loads(x) for x in resp_text.splitlines() if x.strip()]


class _FakeStream:
    """httpx.stream stand-in: yields ndjson lines like ollama's /api/chat."""

    status_code = 200

    def __init__(self, chunks):
        self._lines = [json.dumps(c) for c in chunks]

    def iter_lines(self):
        yield from self._lines

    def read(self):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DeltaSinkTests(unittest.TestCase):
    def setUp(self):
        cancellation.unbind()

    def _raw(self, chunks):
        got = []
        with patch.object(ollama_client, "_open_chat_stream", lambda *a, **k: _FakeStream(chunks)):
            with ollama_client.delta_sink(got.append):
                try:
                    out = ollama_client._chat_once_raw("http://h:11434", "m", [], [], 2048, "5m")
                except ValueError as exc:
                    out = exc
        return out, got

    def test_chunks_stream_as_they_arrive_and_the_turn_closes(self):
        out, got = self._raw([
            {"message": {"content": "Hel"}},
            {"message": {"content": "lo"}},
            {"message": {"tool_calls": [{"function": {"name": "search_docs", "arguments": {"q": "x"}}}]}},
            {"done": True, "done_reason": "stop", "model": "m"},
        ])
        self.assertEqual(out["message"]["content"], "Hello")
        kinds = [d["kind"] for d in got]
        self.assertEqual(kinds, ["turn", "content", "content", "tool_call", "turn_end"])
        self.assertEqual(len({d["turn"] for d in got}), 1, "every event of one call carries one turn id")
        self.assertEqual(got[3]["name"], "search_docs")
        self.assertEqual(json.loads(got[3]["args"]), {"q": "x"})
        self.assertEqual(got[-1]["done_reason"], "stop")

    def test_a_cut_stream_is_discarded_for_viewers_and_still_raises_for_the_retry(self):
        out, got = self._raw([{"message": {"content": "partial"}}])   # no done chunk
        self.assertIsInstance(out, ValueError)
        self.assertEqual(got[-1]["kind"], "discard", "a viewer must not take a cut attempt as the answer")

    def test_a_raising_sink_never_reaches_the_model_call(self):
        def boom(_):
            raise RuntimeError("viewer broke")
        with patch.object(ollama_client, "_open_chat_stream",
                          lambda *a, **k: _FakeStream([{"message": {"content": "ok"}}, {"done": True}])):
            with ollama_client.delta_sink(boom):
                out = ollama_client._chat_once_raw("h", "m", [], [], 2048, "5m")
        self.assertEqual(out["message"]["content"], "ok")

    def test_the_sink_is_per_thread_and_restored(self):
        seen_other = []

        def other():
            seen_other.append(getattr(ollama_client._CALL_CONTEXT, "delta_sink", None))
        outer = []
        with ollama_client.delta_sink(outer.append):
            t = threading.Thread(target=other)
            t.start()
            t.join()
            with ollama_client.delta_sink(None):
                ollama_client._delta({"kind": "x"})
            ollama_client._delta({"kind": "y"})
        ollama_client._delta({"kind": "z"})
        self.assertEqual(seen_other, [None], "another thread must not stream into this run")
        self.assertEqual([d["kind"] for d in outer], ["y"])

    def test_the_tool_loop_streams_each_tool_result(self):
        calls = {"n": 0}

        def fake_once(host, model, messages, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"message": {"role": "assistant", "tool_calls": [
                    {"function": {"name": "search_docs", "arguments": {"q": "x"}}}]}}
            return {"message": {"role": "assistant", "content": "the answer"}}
        got = []
        with patch.object(ollama_client, "_chat_once", fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", lambda fn, args, *a, **k: "R" * 5000):
            with ollama_client.delta_sink(got.append):
                answer, esc = ollama_client.chat("h", "m", [{"role": "user", "content": "q"}],
                                                 [{"function": {"name": "search_docs"}}])
        self.assertEqual(answer, "the answer")
        res = [d for d in got if d["kind"] == "tool_result"]
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "search_docs")
        self.assertEqual(res[0]["chars"], 5000, "the viewer is told the full size")
        self.assertEqual(len(res[0]["result"]), ollama_client._LIVE_RESULT_PREVIEW)


class LiveRunsFeedTests(unittest.TestCase):
    def setUp(self):
        live_runs._clear_for_tests()

    def tearDown(self):
        live_runs._clear_for_tests()

    def test_deltas_merge_only_until_a_reader_has_seen_them(self):
        live_runs.open_feed("c1", "U", "chat", "hi")
        live_runs.push("c1", {"type": "delta", "kind": "content", "turn": 1, "text": "a"})
        live_runs.push("c1", {"type": "delta", "kind": "content", "turn": 1, "text": "b"})
        first, done, _ = live_runs.read("c1", 0)
        self.assertEqual([e.get("text") for e in first], ["ab"])
        live_runs.push("c1", {"type": "delta", "kind": "content", "turn": 1, "text": "c"})
        live_runs.push("c1", {"type": "delta", "kind": "content", "turn": 2, "text": "d"})
        rest, _, _ = live_runs.read("c1", first[-1]["seq"] + 1)
        self.assertEqual([e.get("text") for e in rest], ["c", "d"])
        whole, _, _ = live_runs.read("c1", 0)   # a viewer joining late gets every byte, in order
        self.assertEqual("".join(e.get("text", "") for e in whole), "abcd")
        self.assertEqual([e["seq"] for e in whole], list(range(len(whole))))

    def test_finish_ends_the_feed_and_later_events_are_ignored(self):
        live_runs.open_feed("c2", "U", "chat", "hi")
        live_runs.push("c2", {"type": "status", "message": "Working"})
        live_runs.finish("c2", {"route": "local", "answer": "A"})
        live_runs.push("c2", {"type": "status", "message": "late"})
        events, done, feed = live_runs.read("c2", 0)
        self.assertTrue(done)
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(feed.last_status, "Working")
        self.assertNotIn("c2", live_runs.live_ids())

    def test_active_is_per_user_and_chat_and_purge_keeps_running_feeds(self):
        live_runs.open_feed("a", "U", "c1", "x")
        live_runs.open_feed("b", "U", "c2", "y")
        live_runs.open_feed("c", "V", "c1", "z")
        live_runs.finish("b", {})
        self.assertEqual([r["cancel_id"] for r in live_runs.active("U")], ["a"])
        self.assertEqual([r["cancel_id"] for r in live_runs.active("U", "c2")], [])
        far = live_runs.get("b").finished_at + app.config.GRACE_TTL_SEC + 1
        live_runs.purge(now=far)
        self.assertIsNone(live_runs.get("b"))
        self.assertIsNotNone(live_runs.get("a"), "a run still working is never purged")

    def test_a_restored_feed_continues_where_the_viewer_was(self):
        """Replay must maintain sequence continuity: a restored feed resumes from the last acknowledged sequence without rewinding or duplicating prior deltas."""
        import asyncio
        import tempfile as _tf
        path = os.path.join(_tf.mkdtemp(), "feed.json")
        live_runs.open_feed("s1", "U", "c", "q")
        live_runs.push("s1", {"type": "delta", "kind": "turn", "turn": 1})
        live_runs.push("s1", {"type": "delta", "kind": "content", "turn": 1, "text": "first message"})
        live_runs.snapshot("s1", path)                       # the checkpoint before the next model turn
        seen, _, _ = live_runs.read("s1", 0)
        last_seen = seen[-1]["seq"]
        live_runs._clear_for_tests()                          # the process dies
        feed = live_runs.open_feed("s1", "U", "c", "q", restore_from=path)
        self.assertEqual((feed.epoch, feed.restored_upto), (1, last_seen))
        live_runs.push("s1", {"type": "delta", "kind": "turn", "turn": 2})
        live_runs.finish("s1", {"route": "local", "answer": "x"})

        async def lines(after, epoch):
            return [json.loads(l) async for l in app._feed_lines("s1", after, epoch)]
        resumed = asyncio.run(lines(last_seen + 1, 0))
        self.assertEqual([e["type"] for e in resumed], ["meta", "delta", "result"])
        self.assertEqual(resumed[1]["seq"], last_seen + 1, "seq numbers continue across the restart")
        self.assertNotIn("rewind", [e["type"] for e in resumed])
        fresh = asyncio.run(lines(0, None))                   # a page opened after the restart sees the whole run
        self.assertEqual([e.get("text") for e in fresh if e.get("kind") == "content"], ["first message"])

    def test_unknown_run_reads_as_ended(self):
        events, done, feed = live_runs.read("nope", 0)
        self.assertEqual((events, done, feed), ([], True, None))


class _HistoryTmp(unittest.TestCase):
    def setUp(self):
        # _persist triggers the auto-titler on a chat's first exchange; mock it out to prevent real LLM calls from interfering with test timing and assertions.
        p = patch.object(app, "_auto_title_worker", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)
        self.tmp = tempfile.mkdtemp()
        self._orig = history.HISTORY_DIR
        history.HISTORY_DIR = pathlib.Path(self.tmp)
        live_runs._clear_for_tests()
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation._clear_for_tests()

    def tearDown(self):
        history.HISTORY_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)
        live_runs._clear_for_tests()
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation._clear_for_tests()


class PendingTurnTests(_HistoryTmp):
    def test_accepted_message_is_visible_but_never_in_model_context(self):
        chat = history.begin_turn("U", None, "build me a thing", "cid1")
        self.assertEqual([p["content"] for p in history.pending("U", chat)], ["build me a thing"])
        self.assertEqual(history.load("U", chat), [])
        _summary, ctx = history.context_messages("U", chat)
        self.assertNotIn("build me a thing", json.dumps(ctx), "the running turn carries its own message")
        title = [c for c in history.list_chats("U")["chats"] if c["id"] == chat][0]["title"]
        self.assertEqual(title, history.title_clip("build me a thing"), "placeholder title on accept")

    def test_end_turn_removes_only_its_row_and_dead_rows_go_on_the_next_turn(self):
        chat = history.begin_turn("U", None, "one", "c1")
        history.begin_turn("U", chat, "two", "c2", live_ids={"c1"})
        self.assertEqual([p["cancel_id"] for p in history.pending("U", chat)], ["c1", "c2"])
        history.end_turn("U", chat, "c1")
        self.assertEqual([p["cancel_id"] for p in history.pending("U", chat)], ["c2"])
        history.begin_turn("U", chat, "three", "c3", live_ids=set())   # c2's run was lost
        self.assertEqual([p["cancel_id"] for p in history.pending("U", chat)], ["c3"])

    def test_reset_and_delete_take_the_pending_file_with_them(self):
        chat = history.begin_turn("U", None, "x", "c1")
        history.reset("U", chat)
        self.assertEqual(history.pending("U", chat), [])
        chat2 = history.new_chat("U")["id"]
        history.begin_turn("U", chat2, "y", "c2")
        history.delete_chat("U", chat2)
        trash = list((history.HISTORY_DIR / "U" / ".trash").glob("*.pending.json"))
        self.assertEqual(len(trash), 1, "soft delete moves it, never destroys it")


class StreamEndpointTests(_HistoryTmp):
    """The /query/stream contract through the real endpoint: meta -> status -> delta -> result, the
    message persisted on accept, and the pending row gone on every route."""

    USER = "LiveStreamUser"   # NOT a test* user: those are ephemeral and persist nothing

    def _post(self, core):
        from fastapi.testclient import TestClient
        with patch.object(app, "_run_query_core", core):
            resp = TestClient(app.app).post("/query/stream", json={"text": "hello there", "user": self.USER})
        self.assertEqual(resp.status_code, 200)
        return _lines(resp.text)

    def test_contract_and_accept_time_persist(self):
        seen = {}

        def core(q, status, diag):
            seen["pending"] = [p["content"] for p in history.pending(self.USER, q.chat_id)]
            seen["live"] = [r["text"] for r in live_runs.active(self.USER)]
            status("Thinking")
            ollama_client._delta({"kind": "turn", "turn": 7, "model": "m"})
            ollama_client._delta({"kind": "content", "turn": 7, "text": "Hi"})
            app._persist(q, q.text.strip(), "Hi!")
            return {"route": "local", "answer": "Hi!"}
        events = self._post(core)
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "meta")
        self.assertEqual(types[-1], "result")
        self.assertIn("status", types)
        self.assertIn("delta", types)
        self.assertEqual([e["kind"] for e in events if e["type"] == "delta"], ["turn", "content"])
        self.assertEqual(events[-1]["result"]["answer"], "Hi!")
        self.assertEqual(seen["pending"], ["hello there"], "the message is written BEFORE the work starts")
        self.assertEqual(seen["live"], ["hello there"], "a viewer can find the run while it works")
        chat = events[0]["chat_id"]
        self.assertEqual(history.pending(self.USER, chat), [])
        self.assertEqual([m["content"] for m in history.load(self.USER, chat)], ["hello there", "Hi!"],
                         "one copy of the pair, no leftover pending row")

    def test_error_and_stop_routes_clear_the_pending_row(self):
        def broken(q, status, diag):
            raise RuntimeError("boom")

        def stopped(q, status, diag):
            raise cancellation.Cancelled()
        for core, route in ((broken, "error"), (stopped, "cancelled")):
            events = self._post(core)
            self.assertEqual(events[-1]["result"]["route"], route)
            self.assertEqual(history.pending(self.USER, events[0]["chat_id"]), [])
            self.assertEqual(live_runs.active(self.USER), [])

    def test_a_run_that_ends_without_an_answer_keeps_the_message_marked_unfinished(self):
        """Verify that runs ending in error or cancellation retain the message with an unfinished status, ensuring no pending rows are dropped or orphaned."""
        def broken(q, status, diag):
            raise RuntimeError("boom")

        def stopped(q, status, diag):
            raise cancellation.Cancelled()
        for core, route in ((broken, "error"), (stopped, "cancelled")):
            events = self._post(core)
            msgs = history.load(self.USER, events[0]["chat_id"])
            self.assertEqual([m["role"] for m in msgs[-2:]], ["user", "assistant"], msgs)
            self.assertEqual(msgs[-2]["content"], "hello there")
            self.assertTrue(msgs[-1].get("unfinished"), msgs[-1])
            self.assertEqual(msgs[-1].get("route"), route)
            self.assertTrue(msgs[-1]["content"].startswith("⚠ Not finished."), msgs[-1]["content"])

    def test_the_answer_lands_in_the_chat_it_was_sent_to(self):
        def core(q, status, diag):
            history.new_chat(self.USER)    # the user switches chats on another device mid-run
            app._persist(q, q.text.strip(), "answer")
            return {"route": "local", "answer": "answer"}
        events = self._post(core)
        origin = events[0]["chat_id"]
        self.assertEqual([m["content"] for m in history.load(self.USER, origin)], ["hello there", "answer"])
        self.assertNotEqual(history.list_chats(self.USER)["active"], origin)
        self.assertEqual(history.load(self.USER, None), [], "nothing leaked into the newly active chat")

    def test_the_run_happens_even_if_the_sender_never_reads_a_byte(self):
        """An accepted message must get its answer: before #37 the worker started only when the response
        body started streaming, so a client gone before the first chunk meant the request never ran - and
        now it would also leave a pending message that never ends."""
        import asyncio
        ran = threading.Event()

        def core(q, status, diag):
            ran.set()
            app._persist(q, q.text.strip(), "answered")
            return {"route": "local", "answer": "answered"}

        class _Req:
            headers = {}

        async def go():
            q = app.Q(text="nobody listens", user=self.USER)
            await app.query_stream(q, _Req())          # the response body is never iterated
            for _ in range(200):
                feed = live_runs.get(q.cancel_id)
                if feed is not None and feed.done:
                    break
                await asyncio.sleep(0.02)
            return q
        with patch.object(app, "_run_query_core", core):
            q = asyncio.run(go())
        self.assertTrue(ran.is_set(), "the work ran with no reader")
        self.assertTrue(live_runs.get(q.cancel_id).done)
        self.assertEqual(history.pending(self.USER, q.chat_id), [])
        self.assertEqual([m["content"] for m in history.load(self.USER, q.chat_id)], ["nobody listens", "answered"])

    def test_history_reports_the_pending_turn_with_its_liveness(self):
        from fastapi.testclient import TestClient
        chat = history.begin_turn(self.USER, None, "lost one", "dead")
        live_runs.open_feed("alive", self.USER, chat, "working one")
        history.begin_turn(self.USER, chat, "working one", "alive", live_ids={"dead", "alive"})
        d = TestClient(app.app).get("/history", params={"user": self.USER}).json()
        self.assertEqual([(p["content"], p["live"]) for p in d["pending"]],
                         [("lost one", False), ("working one", True)])


class WatchEndpointTests(_HistoryTmp):
    def test_watch_replays_the_run_and_ends_on_the_result(self):
        from fastapi.testclient import TestClient
        live_runs.open_feed("w1", "Owner", "c", "q")
        live_runs.push("w1", {"type": "delta", "kind": "content", "turn": 1, "text": "abc"})
        live_runs.finish("w1", {"route": "local", "answer": "abc"})
        events = _lines(TestClient(app.app).get("/query/watch", params={"cancel_id": "w1"}).text)
        self.assertEqual([e["type"] for e in events], ["meta", "delta", "result"])
        tail = _lines(TestClient(app.app).get("/query/watch", params={"cancel_id": "w1", "after": 1}).text)
        self.assertEqual([e["type"] for e in tail], ["meta", "result"])

    def test_a_locked_user_cannot_watch_someone_elses_run(self):
        from fastapi.testclient import TestClient
        live_runs.open_feed("w2", "Owner", "c", "q")     # still working
        client = TestClient(app.app)
        other = "LockedViewer"
        self.assertNotIn(other, app.config.ADMIN_USERS)
        self.assertEqual([r["cancel_id"] for r in client.get("/query/live", params={"user": "Owner"}).json()["runs"]],
                         ["w2"], "positive control: the owner's run is listed")
        self.assertEqual(client.get("/query/watch", params={"cancel_id": "w2"},
                                    headers={"x-hai-user": other}).status_code, 404)
        self.assertEqual(client.get("/query/live", params={"user": "Owner"},
                                    headers={"x-hai-user": other}).json()["runs"], [])
        self.assertEqual(client.get("/query/watch", params={"cancel_id": "nope"}).status_code, 404)


class KirkRateOnTheTileTests(unittest.TestCase):
    """The boxes show their rate whether or not an order is running: the desk adds Kirk's own calls to the tile and
    keeps the factory's lane state."""

    URL = "http://kirk-test-host:11434"

    def setUp(self):
        ollama_client._GEN.pop(self.URL, None)
        self._hosts = dict(app._LLM_HOSTS)
        app._LLM_HOSTS["mothership"] = self.URL

    def tearDown(self):
        ollama_client._GEN.pop(self.URL, None)
        app._LLM_HOSTS.clear()
        app._LLM_HOSTS.update(self._hosts)

    def test_rate_is_live_while_streaming_then_the_median_of_answers(self):
        chunks = [{"message": {"content": "t"}}] * 25 + [
            {"done": True, "eval_count": 300, "eval_duration": int(10e9)}]
        import itertools
        ticks = itertools.count()      # never exhausts: any other caller of time.time() just takes a tick
        clock = (100.0 + n * 0.1 for n in ticks)
        seen = []

        class _S(_FakeStream):
            def iter_lines(self_inner):
                for line in _FakeStream.iter_lines(self_inner):
                    seen.append(ollama_client.generation_rate(self.URL))
                    yield line
        with patch.object(ollama_client, "_open_chat_stream", lambda *a, **k: _S(chunks)), \
                patch.object(ollama_client.time, "time", lambda: next(clock)):
            ollama_client._chat_once_raw(self.URL, "m", [], [], 2048, "5m")
        self.assertEqual(seen[0], (None, ""), "no rate before the first token")
        self.assertEqual(seen[-1][1], "live", "a rate while it streams")
        self.assertEqual(ollama_client.generation_rate(self.URL), (30.0, "recent"),
                         "after the call: eval_count / eval_duration (300 tokens / 10 s)")
        ollama_client._gen_note(self.URL, eval_count=10, eval_duration=int(1e9))   # under GEN_MIN_TOKENS
        self.assertEqual(ollama_client.generation_rate(self.URL), (30.0, "recent"), "a tiny answer is skipped")

    def _tile(self, view, rows=(), ended=None):
        snap = {"rows": [dict(r, host=self.URL) for r in rows], "by_host": {}}
        with patch.object(app, "_factory_view", lambda h: dict(view)), \
                patch.object(app.ollama_client, "inflight_snapshot", lambda: snap), \
                patch.object(app.ollama_client, "last_call_end", lambda u: ended):
            return app.p20_factory("mothership")

    def test_kirk_on_a_free_card_shows_busy_with_its_speed(self):
        ollama_client._gen_note(self.URL, eval_count=900, eval_duration=int(10e9))
        v = self._tile({"host": "mothership", "state": "free", "order": "", "tok_s": ""},
                       rows=[{"model": "qwen3.8:27b", "kind": "chat", "purpose": "think_hard", "user": "Alex"}])
        self.assertEqual(v["state"], "busy")
        self.assertEqual(v["lane"], "free", "the factory's own lane state is kept")
        self.assertEqual(v["detail"], "HAI · think_hard for Alex · qwen3.8:27b")
        self.assertEqual(v["tok_s"], "write 90 tok/s")

    def test_between_calls_the_card_stays_as_reported_with_the_last_speed(self):
        ollama_client._gen_note(self.URL, eval_count=900, eval_duration=int(10e9))
        import time as _t
        v = self._tile({"host": "mothership", "state": "free", "order": "", "tok_s": ""}, ended=_t.time() - 12)
        self.assertEqual(v["state"], "free")
        self.assertEqual(v["tok_s"], "write 90 tok/s")
        self.assertTrue(v["detail"].startswith("HAI's last call ended"))
        quiet = self._tile({"host": "mothership", "state": "free", "order": "", "tok_s": "", "detail": ""},
                           ended=_t.time() - 3600)
        self.assertEqual((quiet["state"], quiet["tok_s"], quiet["detail"]), ("free", "", ""),
                         "nothing running: blank, never an invented rate")

    def test_agent_rows_read_speed_step_and_context(self):
        ollama_client._gen_note(self.URL, eval_count=600, eval_duration=int(10e9),        # write 60 tok/s
                                prompt_count=46000, prompt_duration=int(20e9),            # read 2,300 tok/s
                                num_ctx=122880)
        ollama_client._gen_step(self.URL, turn=12)
        ollama_client._gen_step(self.URL, tool="coding_test", tool_ms=8400)
        v = self._tile({"host": "mothership", "state": "free", "order": "", "tok_s": ""},
                       rows=[{"model": "qwen3.8:27b", "kind": "chat", "purpose": "think_hard", "user": "Alex"}])
        self.assertEqual(v["tok_s"], "write 60 · read 2,300 tok/s")
        self.assertEqual(v["step"], "turn 12 · coding_test 8.4 s")
        self.assertEqual(v["context"], "47k / 123k (38%)")
        ollama_client._gen_step(self.URL, tool="coding_test")                             # a tool still running
        self.assertTrue(self._tile({"host": "mothership", "state": "free", "order": ""},
                                   rows=[{"model": "m", "kind": "chat", "purpose": "p", "user": "u"}])["step"]
                        .endswith("…"))

    def test_read_speed_counts_only_tokens_actually_read(self):
        """Read speed (tok/s) divides elapsed time by read tokens only (uncached tail), while the context gauge counts the full prompt (cached + read)."""
        ollama_client._gen_note(self.URL, eval_count=600, eval_duration=int(10e9),
                                prompt_count=38696, prompt_cached=38191, prompt_duration=int(1.0e9),   # 505 read in 1 s
                                num_ctx=122880)
        v = self._tile({"host": "mothership", "state": "free", "order": "", "tok_s": ""},
                       rows=[{"model": "qwen3.8:27b", "kind": "chat", "purpose": "chat", "user": "Alex"}])
        self.assertEqual(v["tok_s"], "write 60 · read 505 · reload 38,696 tok/s")
        self.assertEqual(v["context"], "39k / 123k (32%)")

    def test_reload_is_shown_beside_read_only_when_the_cache_served_part_of_the_prompt(self):
        """Reload rate is displayed beside read throughput, never replacing it. It is absent if no cache was used."""
        ollama_client._gen_note(self.URL, eval_count=600, eval_duration=int(10e9),
                                prompt_count=4000, prompt_cached=0, prompt_duration=int(8.0e9), num_ctx=39936)
        v = self._tile({"host": "mothership", "state": "free", "order": "", "tok_s": ""},
                       rows=[{"model": "qwen3.5:9b", "kind": "chat", "purpose": "chat", "user": "Alex"}])
        self.assertEqual(v["tok_s"], "write 60 · read 500 tok/s")

    def test_an_order_owns_the_tile(self):
        v = self._tile({"host": "mothership", "state": "compute", "order": "o1", "tok_s": "50 tok/s",
                        "detail": "x"}, rows=[{"model": "m", "kind": "chat", "purpose": "p", "user": "u"}])
        self.assertEqual((v["state"], v["tok_s"], v["detail"]), ("compute", "50 tok/s", "x"))


if __name__ == "__main__":
    unittest.main()
