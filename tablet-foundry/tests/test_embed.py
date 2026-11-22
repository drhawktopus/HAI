import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as tf
import tolkien_llm as tv

TEXTS = [{"id": "q01", "text": "search_query: what is McCoy"}, {"id": "q02", "text": "search_query: weather this week"}]
GOOD = {"kind": "embed", "name": "e1", "model": "nomic-embed-text-v1.5-F16.gguf", "texts": TEXTS, "rounds": 2,
        "min_loop_s": 0, "gpu": "gtx1060", "fit_target": 512, "build": "cuda",
        "deliver_to": "/ssdshared/tabfoundry-jobs/campaigns/embed-20261023/e1_rigby/out.json"}

_STATE = mock.patch.object(tf, "STATE_DIR", None)


def setUpModule():
    import tempfile
    _STATE.new = tempfile.mkdtemp(prefix="embed_state_")
    _STATE.start()


def tearDownModule():
    import shutil
    _STATE.stop()
    shutil.rmtree(_STATE.new, ignore_errors=True)


class Validation(unittest.TestCase):
    def test_a_good_embed_spec_has_no_defects_and_the_desk_knows_the_kind(self):
        self.assertEqual(tf.spec_defects(GOOD), [])
        self.assertEqual(tv.embed_defects(dict(GOOD, gpu=None, fit_target=None, build=None)), [], "CPU form")

    def test_defects_are_named(self):
        for bad in (dict(GOOD, model="/etc/passwd"), dict(GOOD, texts=[]), dict(GOOD, texts=[TEXTS[0], TEXTS[0]]),
                    dict(GOOD, texts=[{"id": "x"}]), dict(GOOD, rounds=0), dict(GOOD, rounds=True),
                    dict(GOOD, min_loop_s=-1), dict(GOOD, gpu="rtx4090"), dict(GOOD, fit_target=None),
                    dict(GOOD, gpu=None), dict(GOOD, build="cuda", gpu="gt730"), dict(GOOD, build="vulkan9"),
                    dict(GOOD, where="gandalf"), dict(GOOD, ctx=8), {k: v for k, v in GOOD.items() if k != "deliver_to"}):
            self.assertTrue(tf.spec_defects(bad), bad)

    def test_the_lane_is_rigbys_and_the_730_is_touched_only_when_placed_on_it(self):
        self.assertEqual(tv.lane_of_spec(GOOD), "rigby")
        self.assertFalse(tv.spec_uses_gt730(GOOD), "a CUDA placement is the 1060's")
        self.assertTrue(tv.spec_uses_gt730(dict(GOOD, gpu="gt730", build=None)))
        self.assertFalse(tv.spec_uses_gt730(dict(GOOD, gpu=None, fit_target=None, build=None)), "CPU: no card")


class ServerFlags(unittest.TestCase):
    def test_extra_flags_ride_last_on_the_server_argv_and_default_to_none(self):
        a = tv.server_argv("/models/m.gguf", 2048, extra=["--embeddings"])
        self.assertEqual(a[-1], "--embeddings")
        self.assertNotIn("--embeddings", tv.server_argv("/models/m.gguf", 2048))


def _reply(vec=(0.1, 0.2, 0.3), tokens=7):
    return {"data": [{"embedding": list(vec)}], "usage": {"prompt_tokens": tokens}}


class RunEmbed(unittest.TestCase):
    def run_it(self, spec=GOOD, calls=None, stop_event=None):
        calls = calls if calls is not None else []
        seen = {}
        progress = []

        def fake_start(model_path, ctx, rpc, log_path, stop_event=None, tick=None, **kw):
            seen.update(model_path=model_path, ctx=ctx, **kw)
            return "proc", 1.2

        def fake_call(text, timeout=120):
            calls.append(text)
            if isinstance(calls, list) and len(calls) == 99:
                raise RuntimeError("never")
            return _reply()
        with mock.patch.object(tv, "start_server", side_effect=fake_start), \
             mock.patch.object(tv, "stop_server") as stop, \
             mock.patch.object(tv, "_embed_call", side_effect=fake_call):
            out, err = tv.run_embed(spec, lambda s: None, lambda i, n, w: progress.append((i, n, w)), stop_event)
        return out, err, seen, stop, progress

    def test_the_delivery_is_the_gpu_payloads_shape_with_the_measured_load_and_the_vectors(self):
        out, err, seen, stop, progress = self.run_it()
        self.assertIsNone(err)
        self.assertEqual((out["host"], out["model"], out["dims"]), ("rigby", GOOD["model"], 3))
        self.assertEqual(out["placement"]["cold"], True)
        self.assertEqual(out["placement"]["load_s"], 1.2, "the per-order server's start to /health is its cold load")
        self.assertEqual((out["placement"]["gpu"], out["placement"]["fit_target"], out["placement"]["build"]),
                         ("gtx1060", 512, "cuda"))
        self.assertEqual(len(out["rows"]), 4, "2 rounds x 2 texts")
        self.assertEqual(sorted(out["vectors"]), ["q01", "q02"])
        self.assertEqual(out["rows"][0]["prompt_eval_count"], 7)
        s = out["summary"]
        self.assertEqual((s["calls"], s["rounds"], s["errors"], s["loop_j"], s["meter"]), (4, 2, 0, None, None))
        self.assertIsNotNone(s["s_per_call_median"])
        self.assertIn("J unmetered", s["verdict"])
        self.assertEqual(progress[-1][:2], (4, 4))
        stop.assert_called_once_with("proc")

    def test_the_server_is_started_with_embeddings_on_the_named_card_and_build(self):
        out, err, seen, stop, progress = self.run_it()
        self.assertEqual(seen["extra"], ["--embeddings"])
        self.assertEqual((seen["gpu"], seen["fit_target"], seen["build"]), ("gtx1060", 512, "cuda"))
        self.assertEqual(seen["ctx"], tv.EMBED_DEFAULT_CTX)
        self.assertTrue(seen["model_path"].endswith(GOOD["model"]))

    def test_a_reply_without_an_embedding_is_an_error_row_and_the_order_errors(self):
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), \
             mock.patch.object(tv, "stop_server") as stop, \
             mock.patch.object(tv, "_embed_call", return_value={"data": []}):
            out, err = tv.run_embed(dict(GOOD, rounds=1), lambda s: None, lambda i, n, w: None)
        self.assertEqual(err, "no embedding call succeeded")
        self.assertEqual(out["summary"]["errors"], 2)
        stop.assert_called_once()

    def test_a_server_that_fails_to_load_is_reported_and_nothing_is_measured(self):
        with mock.patch.object(tv, "start_server", side_effect=RuntimeError("llama-server exited rc=1")), \
             mock.patch.object(tv, "stop_server") as stop, \
             mock.patch.object(tv, "_embed_call") as call:
            out, err = tv.run_embed(GOOD, lambda s: None, lambda i, n, w: None)
        self.assertIn("llama-server exited", err)
        self.assertEqual(out["rows"], [])
        call.assert_not_called()
        stop.assert_called_once_with(None)

    def test_a_stop_slip_ends_the_loop_and_still_stops_the_server(self):
        ev = threading.Event()
        calls = []

        def fake_call(text, timeout=120):
            calls.append(text)
            ev.set()                       # the slip lands after the first call
            return _reply()
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), \
             mock.patch.object(tv, "stop_server") as stop, \
             mock.patch.object(tv, "_embed_call", side_effect=fake_call):
            out, err = tv.run_embed(GOOD, lambda s: None, lambda i, n, w: None, ev)
        self.assertIn("stopped by slip", err)
        self.assertEqual(len(calls), 1)
        self.assertTrue(out["summary"].get("stopped"))
        stop.assert_called_once_with("proc")

    def test_min_loop_s_keeps_measuring_past_the_marked_rounds(self):
        clock = [1000.0]

        def now():
            clock[0] += 5.0
            return clock[0]
        with mock.patch.object(tv, "start_server", return_value=("proc", 1.0)), \
             mock.patch.object(tv, "stop_server"), \
             mock.patch.object(tv, "_embed_call", return_value=_reply()), \
             mock.patch.object(tv.time, "time", now):
            out, err = tv.run_embed(dict(GOOD, rounds=1, min_loop_s=30), lambda s: None, lambda i, n, w: None)
        self.assertIsNone(err)
        self.assertGreater(out["summary"]["rounds"], 1, "the loop outlasts the marked rounds until min_loop_s")
        self.assertEqual(out["summary"]["calls"], len(out["rows"]))


if __name__ == "__main__":
    unittest.main()
