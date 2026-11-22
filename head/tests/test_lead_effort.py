"""Job-shaped calls use the crown effort (config.POLICY_TABLE roles.crown); all other calls to thinking models must send "xhigh". 
Do not send "high" or boolean flags: on Ollama 0.40.5, "high" yields identical tokens to medium, while "xhigh" is the only value that triggers the vendor's extra effort."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import config  # noqa: E402
import ollama_client  # noqa: E402


class _Crowned(unittest.TestCase):
    CROWN = {"box": "mothership", "model": "qwen3.8:27b", "think": "xhigh"}

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
        # /api/show, faked: every model in this file thinks except qwen3-coder; "unreachable" cannot be asked
        self.shows = []

        def fake_post(url, json=None, timeout=None):
            self.shows.append((url, json["model"]))
            if "unreachable" in url:
                raise OSError("no route")
            caps = ["completion", "tools"] + ([] if json["model"].startswith("qwen3-coder") else ["thinking"])
            return _Resp({"capabilities": caps})
        for p in (patch.object(ollama_client.httpx, "post", fake_post), patch.object(config, "THINK_CAPS_PROBE", True)):
            p.start()
            self.addCleanup(p.stop)
        ollama_client._CAN_THINK.clear()
        self.addCleanup(ollama_client._CAN_THINK.clear)


class _Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class LeadThinkTests(_Crowned):
    def test_a_job_on_the_crowns_model_and_box_gets_its_priced_effort(self):
        self.assertEqual(ollama_client.lead_think(config.HEAVY_HOST, "qwen3.8:27b", job=True), "xhigh")

    def test_everything_else_that_can_think_thinks(self):
        """Thinking off is never a default: plain chat, another model, another box all get it on."""
        self.assertIs(ollama_client.lead_think(config.HEAVY_HOST, "qwen3.8:27b", job=False), True)
        self.assertIs(ollama_client.lead_think(config.HEAVY_HOST, "qwen3.6:27b", job=True), True)
        self.assertIs(ollama_client.lead_think(config.BRAIN_HOST, "qwen3.8:27b", job=True), True)
        self.assertIs(ollama_client.lead_think(config.BRAIN_HOST, "qwen3.5:9b", job=False), True)

    def test_a_model_that_cannot_think_or_cannot_be_asked_gets_no_flag(self):
        self.assertIs(ollama_client.lead_think(config.BRAIN_HOST, "qwen3-coder:30b", job=False), False)
        self.assertIs(ollama_client.lead_think("http://unreachable:11434", "qwen3.5:9b", job=False), False)

    def test_the_capability_is_asked_once_per_model_and_box_but_unknown_is_asked_again(self):
        for _ in range(3):
            ollama_client.lead_think(config.BRAIN_HOST, "qwen3.5:9b", job=False)
            ollama_client.lead_think("http://unreachable:11434", "qwen3.5:9b", job=False)
        self.assertEqual(sum(1 for u, m in self.shows if "unreachable" not in u), 1)
        self.assertEqual(sum(1 for u, m in self.shows if "unreachable" in u), 3)

    def test_the_gate_knob_off_asks_no_box_and_sends_no_flag(self):
        with patch.object(config, "THINK_CAPS_PROBE", False):
            self.assertIsNone(ollama_client.can_think(config.BRAIN_HOST, "qwen3.5:9b"))
            self.assertIs(ollama_client.lead_think(config.BRAIN_HOST, "qwen3.5:9b", job=False), False)
        self.assertEqual(self.shows, [])

    def test_an_unreadable_table_still_thinks_at_the_plain_level(self):
        with patch.object(config, "POLICY_TABLE", os.path.join(self.tmp, "missing.json")):
            ollama_client._CROWN.update({"mtime": None, "crown": None})
            self.assertIs(ollama_client.lead_think(config.HEAVY_HOST, "qwen3.8:27b", job=True), True)

    def test_the_value_follows_the_table_not_a_constant(self):
        with open(self.table, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"crown": dict(self.CROWN, think="medium")}}, fh)
        os.utime(self.table, ns=(1, 1))     # a new mtime: the cache must re-read
        self.assertEqual(ollama_client.lead_think(config.HEAVY_HOST, "qwen3.8:27b", job=True), "medium")


class WhatChatSendsTests(_Crowned):
    def _sent(self, tools_list, job):
        seen = []

        def fake_once(host, model, messages, tools_list, num_ctx, keep_alive, timeout=None, **k):
            seen.append(k.get("think", False))
            return {"message": {"role": "assistant", "content": "an answer long enough"}}
        with patch.object(ollama_client, "_chat_once", fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl):
            with ollama_client.call_context(job=job):
                ollama_client.chat(config.HEAVY_HOST, "qwen3.8:27b", [{"role": "user", "content": "q"}],
                                   tools_list)
        return seen

    def test_a_think_hard_job_sends_xhigh_and_plain_chat_sends_plain_on(self):
        self.assertEqual(self._sent([], job=True), ["xhigh"])
        self.assertEqual(self._sent([], job=False), [True])

    def test_coding_tools_on_offer_do_not_make_plain_chat_a_job(self):
        """Offering coding tools must not classify plain chat as a job."""
        self.assertEqual(self._sent([{"function": {"name": "coding_read"}}], job=False), [True])

    def test_a_pin_above_off_on_a_model_that_cannot_think_sends_no_flag(self):
        seen = []

        def fake_once(host, model, messages, tools_list, num_ctx, keep_alive, timeout=None, **k):
            seen.append(k.get("think", False))
            return {"message": {"role": "assistant", "content": "an answer long enough"}}
        with patch.object(ollama_client, "_chat_once", fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl):
            with ollama_client.call_context(think_pin="max"):
                ollama_client.chat(config.BRAIN_HOST, "qwen3-coder:30b", [{"role": "user", "content": "q"}], [])
        self.assertEqual(seen, [False])

    def test_the_job_flag_is_set_by_the_lanes_common_entry(self):
        seen = []
        admin = sorted(config.ADMIN_USERS)[0]
        with patch.object(app, "_run_query_core_body",
                          lambda q, s, d: seen.append(getattr(ollama_client._CALL_CONTEXT, "job", False))), \
             patch.object(app.mccoy, "observe", lambda q: None):   # Phase 8 shadow wire: this test proves the
            # JOB flag only; its fixtures are live-shaped by design, so the wire must not dial McCoy from here.
            app._run_query_core(app.Q(text="x", user="test", effort_mode="think_hard"))
            app._run_query_core(app.Q(text="say hello in five words", user=admin))
            app._run_query_core(app.Q(text="SUPERVISED LOCAL CODING JOB: fix it", user="test"))
        self.assertEqual(seen, [True, False, True])
        self.assertFalse(getattr(ollama_client._CALL_CONTEXT, "job", False), "restored after the run")


if __name__ == "__main__":
    unittest.main()
