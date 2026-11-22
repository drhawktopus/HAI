"""Silent calls are judged by box status (gpu_load), not clock timeouts, to prevent killing active long-context reads."""
import datetime
import socket
import threading
import time
import unittest
from unittest import mock

import httpx

import cancellation
import ollama_client

BUSY, FRESH, STREAK, CLOCK = 20.0, 180.0, 2, 240.0
MODEL = "qwen3.8:27b"


def _rec(now, util, age=5.0, loaded=(MODEL,), stale=False, ts=None):
    t = datetime.datetime.fromtimestamp(now - age, datetime.timezone.utc).isoformat() if ts is None else ts
    return {"util_pct": util, "ts": t, "ollama_loaded": list(loaded), "stale": stale}


class SilenceVerdict(unittest.TestCase):
    """The pure decision, one branch per test."""

    def v(self, silent_since, now, rec, seen=None, clock=CLOCK):
        seen = set() if seen is None else seen
        return ollama_client.silence_verdict(silent_since, now, rec, MODEL, seen, clock, BUSY, FRESH, STREAK), seen

    def test_a_busy_card_keeps_the_call_waiting_past_any_clock(self):
        now = 10_000.0
        seen = {"an-old-idle-stamp"}
        verdict, seen = self.v(now - 900, now, _rec(now, 97.0), seen)
        self.assertIsNone(verdict, "15 min silent, but the card is working on it: wait")
        self.assertEqual(seen, set(), "a busy report breaks an idle streak (consecutive, as in load accounting)")

    def test_idle_with_our_model_resident_ends_on_the_streak_th_report_since_the_silence(self):
        now, seen = 10_000.0, set()
        first, seen = self.v(now - 70, now - 60, _rec(now - 60, 1.0, age=1.0), seen)
        self.assertIsNone(first, "one idle report is not a demonstration")
        second, seen = self.v(now - 70, now, _rec(now, 1.0, age=1.0), seen)
        self.assertEqual(second, "idle")

    def test_the_same_idle_report_read_twice_counts_once(self):
        now, seen = 10_000.0, set()
        rec = _rec(now, 1.0, age=1.0)
        self.v(now - 70, now, rec, seen)
        verdict, _ = self.v(now - 70, now + 10, rec, seen)
        self.assertIsNone(verdict, "two polls of ONE push are one report, not a streak")

    def test_a_report_taken_before_the_silence_began_says_nothing(self):
        now = 10_000.0
        verdict, seen = self.v(now - 30, now, _rec(now, 0.0, age=60.0))
        self.assertIsNone(verdict)
        self.assertEqual(seen, set())

    def test_no_fresh_report_falls_back_to_the_clock_and_only_then(self):
        now = 10_000.0
        for rec in (None, _rec(now, 0.0, age=FRESH + 1), _rec(now, 0.0, stale=True), _rec(now, None)):
            self.assertIsNone(self.v(now - (CLOCK - 1), now, rec)[0], "could-not-tell is not a verdict before the clock")
            self.assertEqual(self.v(now - CLOCK, now, rec)[0], "blind")

    def test_an_idle_card_without_our_model_is_a_cold_load_not_a_stall(self):
        now = 10_000.0
        rec = _rec(now, 0.0, age=1.0, loaded=())
        self.assertIsNone(self.v(now - 100, now, rec)[0], "loading reads idle on the card: no verdict")
        self.assertEqual(self.v(now - CLOCK, now, rec)[0], "blind", "and the old clock still bounds it")


class _SilentServer:
    """Accepts one request, stays silent for `delay` seconds (None = forever), then answers like ollama's stream.
    Records whether the client hung up (recv returned b'') while it was waiting."""

    BODY = (b'{"message":{"content":"ok"}}\n'
            b'{"message":{"content":""},"done":true,"done_reason":"stop","eval_count":1}\n')

    def __init__(self, delay):
        self.delay, self.client_hung_up = delay, threading.Event()
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.url = "http://127.0.0.1:%d" % self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.srv.accept()
        conn.settimeout(0.2)
        start = time.time()
        while True:
            if self.delay is not None and time.time() - start >= self.delay:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nContent-Length: %d\r\n"
                             b"Connection: close\r\n\r\n" % len(self.BODY) + self.BODY)
                break
            try:
                if conn.recv(65536) == b"":
                    self.client_hung_up.set()
                    break
            except socket.timeout:
                continue
            except OSError:
                self.client_hung_up.set()
                break
            if time.time() - start > 30:
                break
        conn.close()
        self.srv.close()


class _LiveBase(unittest.TestCase):
    """Shared setup for the live-socket tests (no tests of its own)."""

    def setUp(self):
        self.patches = [mock.patch.object(ollama_client, "SILENCE_POLL_S", 0.1),
                        mock.patch.object(ollama_client, "_card_for", lambda host: "mothership")]
        self.logged = []
        self.patches.append(mock.patch.object(ollama_client.notes, "log", self.logged.append))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def _report(self, util, loaded=(MODEL,)):
        return lambda card: _rec(time.time(), util, age=0.0, loaded=loaded)


class LiveSilentCalls(_LiveBase):
    """The watch against a real socket: the wait happens BEFORE any header, exactly where ollama's long read sits."""

    def test_a_long_silent_read_on_a_busy_box_finishes_where_the_clock_would_have_killed_it(self):
        srv = _SilentServer(delay=1.5)
        with mock.patch.object(ollama_client, "_load_report", self._report(96.0)):
            out = ollama_client._chat_once_raw(srv.url, MODEL, [], [], 2048, "5m", timeout=0.5)
        self.assertEqual(out["message"]["content"], "ok")
        self.assertTrue(any(l.startswith("LONG READ") for l in self.logged), self.logged)

    def test_an_idle_box_ends_the_call_as_a_stall_and_the_server_sees_the_hang_up(self):
        srv = _SilentServer(delay=None)
        t0 = time.time()
        with mock.patch.object(ollama_client, "_load_report", self._report(1.0)):
            with self.assertRaises(ollama_client.ModelStalled) as cm:
                ollama_client._chat_once_raw(srv.url, MODEL, [], [], 2048, "5m", timeout=60)
        self.assertLess(time.time() - t0, 10, "ended by the box's evidence, not by the 60 s clock")
        self.assertIsInstance(cm.exception, httpx.ReadTimeout, "the retry logic still sees a timeout")
        self.assertTrue(srv.client_hung_up.wait(5), "ollama must SEE the disconnect, or it keeps working the request")
        self.assertTrue(any(l.startswith("MODEL CALL STALLED") for l in self.logged), self.logged)

    def test_a_box_with_no_report_gets_the_old_clock(self):
        srv = _SilentServer(delay=None)
        t0 = time.time()
        with mock.patch.object(ollama_client, "_load_report", lambda card: None):
            with self.assertRaises(httpx.ReadTimeout) as cm:
                ollama_client._chat_once_raw(srv.url, MODEL, [], [], 2048, "5m", timeout=0.6)
        self.assertNotIsInstance(cm.exception, ollama_client.ModelStalled, "could-not-tell is never called a stall")
        self.assertGreaterEqual(time.time() - t0, 0.6)
        self.assertTrue(any(l.startswith("MODEL CALL CLOCKED") for l in self.logged), self.logged)

    def test_stop_works_during_a_silent_read(self):
        srv = _SilentServer(delay=None)
        cid = "test-silence-watch-stop"
        cancellation.begin(cid)
        cancellation.bind(cid)
        self.assertIsNotNone(cancellation.current_event())
        threading.Timer(0.4, cancellation.cancel, args=(cid,)).start()
        t0 = time.time()
        try:
            with mock.patch.object(ollama_client, "_load_report", self._report(96.0)):
                with self.assertRaises(cancellation.Cancelled):
                    ollama_client._chat_once_raw(srv.url, MODEL, [], [], 2048, "5m", timeout=60)
        finally:
            cancellation.unbind()
            cancellation.end(cid)
        self.assertLess(time.time() - t0, 10, "before this, a Stop waited for the model's first byte")
        self.assertTrue(srv.client_hung_up.wait(5))


class NonStreamedHelperCallsAreSupervisedToo(_LiveBase):
    """post_supervised: the helper calls that kept their own clocks (action/claim/coding review, genetics, identity) -
    a non-streamed reply is silent from start to end, so the same status decides."""

    def test_a_busy_box_is_waited_on_past_the_helpers_clock(self):
        srv = _SilentServer(delay=1.5)
        with mock.patch.object(ollama_client, "_load_report", self._report(96.0)):
            r = ollama_client.post_supervised(srv.url + "/api/chat", json={"model": MODEL, "stream": False},
                                              timeout=0.5)
        self.assertEqual(r.status_code, 200)
        self.assertIn('"done":true', r.text)
        self.assertTrue(any(l.startswith("LONG READ") for l in self.logged), self.logged)

    def test_an_idle_box_ends_it_as_a_stall(self):
        srv = _SilentServer(delay=None)
        t0 = time.time()
        with mock.patch.object(ollama_client, "_load_report", self._report(1.0)):
            with self.assertRaises(ollama_client.ModelStalled):
                ollama_client.post_supervised(srv.url + "/api/chat", json={"model": MODEL, "stream": False},
                                              timeout=60)
        self.assertLess(time.time() - t0, 10)
        self.assertTrue(srv.client_hung_up.wait(5))

    def test_the_five_helpers_call_it(self):
        """claim_review performs no model calls; its logic is purely local (claim_sweep). Do not introduce model calls, as they cannot be tested in unit isolation and would break the assumption that this module is deterministic."""
        import inspect
        import action_review
        import claim_review
        import coding_review
        import genetics
        import identity
        for mod, n in ((action_review, 1), (claim_review, 0), (coding_review, 1), (genetics, 4), (identity, 1)):
            src = inspect.getsource(mod)
            self.assertEqual(src.count("post_supervised("), n, mod.__name__)
            self.assertNotRegex(src, r"httpx\.post\(\s*f?\"[^\"]*/api/chat", mod.__name__)


class ExplainIsSupervisedToo(unittest.TestCase):
    def test_the_handoff_explanation_goes_through_the_supervised_call(self):
        seen = {}

        def fake(host, model, messages, tools_list, num_ctx, keep_alive, timeout=None, think=False):
            seen["args"] = (host, model, len(messages), tools_list)
            return {"message": {"role": "assistant", "content": "Here is where it stands."}}
        with mock.patch.object(ollama_client, "_chat_once_raw", fake):
            out = ollama_client._explain([{"role": "user", "content": "x"}], "http://h", MODEL, 4096)
        self.assertEqual(out, "Here is where it stands.")
        self.assertEqual(seen["args"], ("http://h", MODEL, 2, []))


if __name__ == "__main__":
    unittest.main()
