"""#37: Rigby orders route through `tolkien_llm.complete()` or `chat()`, allowing the meter to capture call counts, read rates, and final context length. The Fellowship push transmits this data (`gen`) when Rigby's order is the active one."""
import http.server
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry  # noqa: E402
import tablet_runner  # noqa: E402
import tolkien_llm as tv  # noqa: E402

# The final chunk of a real llama-server stream carries `timings` (prompt_n, predicted_n, cache_n, per-second rates).
CHUNKS = [{"choices": [{"delta": {"content": "def add(a, b):"}}]},
          {"choices": [{"delta": {}, "finish_reason": "stop"}],
           "timings": {"prompt_n": 120, "predicted_n": 30, "cache_n": 850, "prompt_per_second": 200.0, "prompt_ms": 600.0,
                       "predicted_per_second": 6.0}}]


def _serve(test, props=True, release=None):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if not props or self.path != "/props":
                self.send_error(404)
                return
            body = json.dumps({"default_generation_settings": {"n_ctx": 8192}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i, c in enumerate(CHUNKS):
                if release is not None and i == 1:
                    release.wait(5)
                self.wfile.write(("data: %s\n\n" % json.dumps(c)).encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.shutdown)
    p = mock.patch.object(tv, "SERVER_PORT", srv.server_address[1])
    p.start()
    test.addCleanup(p.stop)


class TheMeterCountsEveryCall(unittest.TestCase):
    def setUp(self):
        tv.meter_reset()

    def test_a_finished_answer_gives_read_rate_and_context(self):
        _serve(self)
        tv.complete("write add", 1, 0.6)
        v = tv.meter_view(write=6.0)
        self.assertEqual((v["turn"], v["turn_label"], v["tool"], v["tool_running"]), (1, "call", "between calls", False))
        self.assertAlmostEqual(v["read"], 200.0)
        self.assertEqual(v["ctx"], [1000, 8192], "cached + read + written tokens, of the server's own n_ctx")
        self.assertIsNotNone(v["tool_s"], "rigby times its calls in-process")

    def test_a_tool_chat_counts_too(self):
        _serve(self)
        tv.chat([{"role": "user", "content": "x"}])
        tv.chat([{"role": "user", "content": "y"}])
        v = tv.meter_view()
        self.assertEqual((v["turn"], v["ctx"]), (2, [1000, 8192]))
        self.assertAlmostEqual(v["read"], 200.0, msg="read = prompt tokens over the prompt time")

    def test_an_answer_in_flight_is_generating(self):
        release = threading.Event()
        _serve(self, release=release)
        t = threading.Thread(target=tv.complete, args=("write add", 1, 0.6), daemon=True)
        t.start()
        try:
            for _ in range(200):
                if tv.meter_view()["tool_running"]:
                    break
                threading.Event().wait(0.01)
            v = tv.meter_view()
            self.assertEqual((v["turn"], v["tool"], v["tool_running"]), (1, "generating", True))
        finally:
            release.set()
            t.join(5)
        self.assertFalse(tv.meter_view()["tool_running"])

    def test_a_server_that_cannot_say_its_context_shows_none(self):
        _serve(self, props=False)
        tv.complete("write add", 1, 0.6)
        self.assertIsNone(tv.meter_view()["ctx"], "no context size measured: no context row, never a guess")

    def test_a_failed_call_still_ends(self):
        with mock.patch.object(tv.urllib.request, "urlopen", side_effect=OSError("refused")):
            with self.assertRaises(OSError):
                tv.complete("write add", 1, 0.6)
        v = tv.meter_view()
        self.assertEqual((v["turn"], v["tool_running"], v["tool"]), (1, False, "between calls"))

    def test_a_new_order_starts_from_nothing(self):
        _serve(self)
        tv.complete("write add", 1, 0.6)
        tv.meter_reset()
        v = tv.meter_view()
        self.assertEqual((v["turn"], v["tool"], v["read"], v["ctx"]), (None, None, None, None))


class TheTileGetsRigbysRows(unittest.TestCase):
    GEN = {"turn": 3, "turn_label": "call", "tool": "generating", "tool_running": True}

    def setUp(self):
        self.sent = []
        p = mock.patch.object(tablet_foundry, "_post_run_state", lambda **k: self.sent.append(k))
        p.start()
        self.addCleanup(p.stop)
        snap = {k: tablet_foundry._PUB.get(k) for k in ("fleet", "rigby")}
        self.addCleanup(tablet_foundry._PUB.update, snap)
        tablet_foundry._PUB.update(fleet=None, rigby=None)

    def _rigby(self, **k):
        with mock.patch.object(tablet_foundry, "lane_name", lambda: "rigby"):
            tablet_foundry.publish(name="a1-r", state="running", phase="llm-rigby", step=1, total=34, **k)

    def test_rigbys_order_on_the_tile_carries_its_rows(self):
        self._rigby(gen=self.GEN)
        self.assertEqual(self.sent[-1]["gen"], self.GEN)

    def test_rigbys_rows_never_ride_on_the_tablets_order(self):
        self._rigby(gen=self.GEN)
        with mock.patch.object(tablet_foundry, "lane_name", lambda: "fleet"):
            tablet_foundry.publish(name="ocr-batch", state="running", phase="ocr", step=5, total=90)
        self.assertEqual(self.sent[-1]["name"], "ocr-batch")
        self.assertIsNone(self.sent[-1].get("gen"), "the tile names the tablets' order: rigby's rows are not its rows")

    def test_the_write_row_is_the_rate_the_phase_carries(self):
        """One derivation: the live rate while answers stream, else the median of the recent answers."""
        self.assertEqual(tablet_runner.write_rate([4.0, 12.0, 6.0, 9.0]), 7.5)
        self.assertEqual(tablet_runner.write_rate([4.0], live=11.0), 11.0)
        self.assertEqual(tablet_runner.tps_phase("llm-rigby", [4.0, 12.0, 6.0, 9.0]), "llm-rigby · 7.5 tok/s")


if __name__ == "__main__":
    unittest.main()
