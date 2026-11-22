import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import ollama_client  # noqa: E402

FN = "qwen3.8-flash-next-strata-q2_0"
# A llama-swap engine is a setting (SWAP_MODEL + LEAD_BOX_SWAP), never a literal in the code.
# The wire tests name their own swap base instead of relying on a lab-specific default.
SWAP_BASE = "http://swap.test:8080"


def _swap_config():
    """One swap row: the swap model, on the heavy box, at a test base URL."""
    return patch.object(config, "SWAP_MODELS", {FN: {"host": config.HEAVY_HOST, "base": SWAP_BASE}})


class _Table(unittest.TestCase):
    CROWN = {"box": "mothership", "model": FN, "think": "xhigh"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.table = os.path.join(self.tmp, "policy.json")
        with open(self.table, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"crown": self.CROWN}}, fh)
        p = patch.object(config, "POLICY_TABLE", self.table)
        p.start()
        self.addCleanup(p.stop)
        ollama_client._CROWN.update({"mtime": None, "crown": None})
        self.addCleanup(ollama_client._CROWN.update, {"mtime": None, "crown": None})
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(lambda: setattr(ollama_client._CALL_CONTEXT, "think_pin", None))


class LeadModel(_Table):
    def test_a_job_to_the_crowns_box_on_its_standing_model_runs_on_the_crown(self):
        self.assertEqual(ollama_client.lead_model(config.HEAVY_HOST, config.HEAVY_MODEL, job=True), FN)

    def test_plain_chat_keeps_the_standing_model(self):
        self.assertEqual(ollama_client.lead_model(config.HEAVY_HOST, config.HEAVY_MODEL, job=False), config.HEAVY_MODEL)

    def test_a_deliberate_other_model_or_another_box_is_left_alone(self):
        self.assertEqual(ollama_client.lead_model(config.HEAVY_HOST, "medgemma:27b", job=True), "medgemma:27b")
        self.assertEqual(ollama_client.lead_model(config.BRAIN_HOST, config.BRAIN_MODEL, job=True), config.BRAIN_MODEL)

    def test_the_users_effort_pin_keeps_its_model(self):
        ollama_client._CALL_CONTEXT.think_pin = "max"
        self.assertEqual(ollama_client.lead_model(config.HEAVY_HOST, config.HEAVY_MODEL, job=True), config.HEAVY_MODEL)

    def test_the_crown_on_the_standing_model_changes_nothing(self):
        with open(self.table, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"crown": dict(self.CROWN, model=config.HEAVY_MODEL)}}, fh)
        os.utime(self.table, ns=(1, 1))
        self.assertEqual(ollama_client.lead_model(config.HEAVY_HOST, config.HEAVY_MODEL, job=True), config.HEAVY_MODEL)

    def test_the_crowns_effort_reaches_the_crowns_model(self):
        self.assertEqual(ollama_client.lead_think(config.HEAVY_HOST, FN, job=True), "xhigh")

    def test_the_swap_model_has_its_tested_context(self):
        self.assertEqual(config.ctx_for(config.HEAVY_HOST, FN, None), 262144)


class Wire(unittest.TestCase):
    def test_swap_base_only_for_the_model_on_its_box(self):
        with _swap_config():
            self.assertTrue(config.swap_base(config.HEAVY_HOST, FN))
            self.assertIsNone(config.swap_base(config.BRAIN_HOST, FN))
            self.assertIsNone(config.swap_base(config.HEAVY_HOST, config.HEAVY_MODEL))
            self.assertEqual(config.swap_box_base(config.HEAVY_HOST), config.swap_base(config.HEAVY_HOST, FN))
            self.assertIsNone(config.swap_box_base(config.BRAIN_HOST))

    def test_think_maps_to_strata_template_kwargs(self):
        self.assertEqual(ollama_client.swap_think_kwargs(False), {"enable_thinking": False})
        self.assertEqual(ollama_client.swap_think_kwargs("xhigh"), {"enable_thinking": True, "reasoning_effort": "xhigh"})
        self.assertEqual(ollama_client.swap_think_kwargs(True), {})

    def test_tool_turns_get_ids_and_json_arguments(self):
        msgs = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": "", "thinking": "hm",
                 "tool_calls": [{"function": {"name": "a", "arguments": {"x": 1}}},
                                {"function": {"name": "b", "arguments": {}}}]},
                {"role": "tool", "content": "ra"}, {"role": "tool", "content": {"k": 2}}]
        out = ollama_client._openai_messages(msgs)
        calls = out[1]["tool_calls"]
        self.assertEqual([c["function"]["arguments"] for c in calls], ['{"x": 1}', "{}"])
        self.assertEqual([out[2]["tool_call_id"], out[3]["tool_call_id"]], [calls[0]["id"], calls[1]["id"]])
        self.assertNotIn("thinking", out[1])
        self.assertIsInstance(out[3]["content"], str)


class _Resp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self):
        return iter(self._lines)

    def read(self):
        return b""


def _sse(obj):
    return "data: " + json.dumps(obj)


class SwapTurn(unittest.TestCase):
    """The streamed turn on the swap wire: live deltas, merged tool-call fragments, ollama's result shape."""

    def _run(self, lines):
        sent = {}

        @contextmanager
        def fake_open(url, body, clock_s, watch=None):
            sent.update(url=url, body=body)
            yield _Resp(lines)
        with _swap_config(), \
                patch.object(ollama_client, "_open_chat_stream", fake_open), \
                patch.object(ollama_client, "_free_swap") as freed:
            out = ollama_client._chat_once_raw(config.HEAVY_HOST, FN, [{"role": "user", "content": "hi"}],
                                               [{"type": "function", "function": {"name": "t"}}], 262144, "5m",
                                               timeout=30, think="xhigh")
        return out, sent, freed

    def test_answer_thinking_and_counts_come_back_in_ollamas_shape(self):
        out, sent, freed = self._run([
            _sse({"choices": [{"delta": {"reasoning_content": "think "}}]}),
            _sse({"choices": [{"delta": {"content": "Hello"}}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 3},
                  "timings": {"prompt_n": 9, "predicted_n": 3}}),
            "data: [DONE]"])
        self.assertTrue(sent["url"].endswith("/v1/chat/completions"))
        self.assertEqual(sent["body"]["model"], FN)
        self.assertEqual(sent["body"]["chat_template_kwargs"], {"enable_thinking": True, "reasoning_effort": "xhigh"})
        self.assertNotIn("options", sent["body"])
        self.assertEqual(out["message"]["content"], "Hello")
        self.assertEqual(out["message"]["thinking"], "think ")
        self.assertEqual((out["prompt_eval_count"], out["eval_count"], out["done_reason"]), (9, 3, "stop"))
        freed.assert_not_called()            # the swap model's own call never frees llama-swap

    def test_tool_call_fragments_merge(self):
        out, _s, _f = self._run([
            _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "t", "arguments": '{"a"'}}]}}]}),
            _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': 1}'}}]}}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            "data: [DONE]"])
        self.assertEqual(out["message"]["tool_calls"], [{"function": {"name": "t", "arguments": {"a": 1}}}])

    def test_a_stream_cut_before_finish_is_retryable(self):
        with self.assertRaises(ValueError):
            self._run([_sse({"choices": [{"delta": {"content": "Hel"}}]})])


class SwapSilenceIsStatus(unittest.TestCase):
    """Llama-swap models are absent from Ollama's status list, so the silence watchdog must read llama-swap's internal state directly rather than inferring it from a timer."""
    CLOCK = 240.0

    def _v(self, swap_state, util, silent_for=10_000.0, seen=None):
        import datetime
        now = 1_000_000.0
        rec = {"ts": datetime.datetime.fromtimestamp(now - 5, datetime.timezone.utc).isoformat(),   # as reports carry it
               "util_pct": util, "ollama_loaded": [], "stale": False}
        seen = set() if seen is None else seen
        return ollama_client.silence_verdict(now - silent_for, now, rec, FN, seen, self.CLOCK, 20.0, 180.0, 2,
                                             swap_state=swap_state), seen

    def test_loading_waits_past_any_clock(self):
        self.assertIsNone(self._v("starting", util=1)[0])

    def test_ready_on_a_busy_card_waits(self):
        self.assertIsNone(self._v("ready", util=95)[0])

    def test_ready_on_an_idle_card_is_a_stall_by_status(self):
        seen = {"earlier"}
        self.assertEqual(self._v("ready", util=1, seen=seen)[0], "idle")

    def test_swap_state_reads_running(self):
        class R:
            def __init__(self, rows):
                self.rows = rows

            def json(self):
                return {"running": self.rows}
        with _swap_config():
            with patch.object(ollama_client.httpx, "get", return_value=R([{"model": FN, "state": "starting"}])):
                self.assertEqual(ollama_client._swap_state(config.HEAVY_HOST, FN), "starting")
            with patch.object(ollama_client.httpx, "get", return_value=R([])):
                self.assertEqual(ollama_client._swap_state(config.HEAVY_HOST, FN), "absent")
            with patch.object(ollama_client.httpx, "get", side_effect=OSError("x")):
                self.assertEqual(ollama_client._swap_state(config.HEAVY_HOST, FN), "unknown")
            self.assertIsNone(ollama_client._swap_state(config.HEAVY_HOST, config.HEAVY_MODEL))


class GateIsolation(unittest.TestCase):
    def test_inside_the_gate_llama_swap_is_unreachable(self):
        """The gate (HAI_STATE_DIR=/tmp/gate-state) must point llama-swap at a dead loopback port (run-suite.sh
        -e LEAD_BOX_SWAP): a call to the lead in any test would otherwise unload a live Flash-Next."""
        if os.environ.get("HAI_TEST_ISOLATED") != "1" and not os.environ.get("HAI_STATE_DIR", "").startswith("/tmp/gate"):
            self.skipTest("not inside the gate")
        for row in config.SWAP_MODELS.values():
            self.assertTrue(row["base"].startswith("http://127.0.0.1:"), row["base"])


class FreeSwap(unittest.TestCase):
    def test_an_ollama_call_on_the_swap_box_frees_a_running_engine(self):
        class R:
            def json(self):
                return {"running": [{"model": FN, "state": "ready"}]}
        with _swap_config(), \
                patch.object(ollama_client.httpx, "get", return_value=R()) as g, \
                patch.object(ollama_client.httpx, "post") as p:
            ollama_client._free_swap(config.HEAVY_HOST)
        g.assert_called_once()
        self.assertTrue(p.call_args[0][0].endswith("/api/models/unload"))

    def test_nothing_running_posts_nothing_and_other_boxes_ask_nothing(self):
        class R:
            def json(self):
                return {"running": []}
        with _swap_config(), \
                patch.object(ollama_client.httpx, "get", return_value=R()) as g, \
                patch.object(ollama_client.httpx, "post") as p:
            ollama_client._free_swap(config.HEAVY_HOST)
            ollama_client._free_swap(config.BRAIN_HOST)
        self.assertEqual(g.call_count, 1)
        p.assert_not_called()

    def test_cannot_ask_goes_ahead(self):
        with _swap_config(), patch.object(ollama_client.httpx, "get", side_effect=OSError("refused")):
            ollama_client._free_swap(config.HEAVY_HOST)     # no raise

    def test_a_box_with_no_swap_engine_is_not_asked(self):
        """Nothing configured means no llama-swap on the box: free_swap asks nothing at all."""
        with patch.object(config, "SWAP_MODELS", {}), \
                patch.object(ollama_client.httpx, "get") as g:
            ollama_client._free_swap(config.HEAVY_HOST)
        g.assert_not_called()


if __name__ == "__main__":
    unittest.main()
