"""Effort is the model's own level; the user's pick is a pin that overrides the job rule for the turn. Distinct from the work axis (Answer vs Think hard)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import config  # noqa: E402
import ollama_client  # noqa: E402


class PinnedThink(unittest.TestCase):
    def test_the_3_8_gets_its_own_levels_and_max_is_xhigh(self):
        for level, want in (("off", False), ("low", "low"), ("medium", "medium"), ("max", "xhigh")):
            self.assertEqual(ollama_client.pinned_think("qwen3.8:27b", level), want, level)

    def test_a_model_without_levels_thinks_at_its_maximum_for_any_level_but_off(self):
        """The 10-07 audit: only the qwen3.8 family has levels; every other thinking model's `on` is its maximum."""
        for level in ("low", "medium", "max"):
            self.assertIs(ollama_client.pinned_think("qwen3.5:9b", level), True, level)
        self.assertIs(ollama_client.pinned_think("qwen3.5:9b", "off"), False)

    def test_auto_or_anything_unknown_is_no_pin(self):
        for level in (None, "auto", "", "xhigh", "high", "MAX"):
            self.assertIsNone(ollama_client.pinned_think("qwen3.8:27b", level), level)


class _Crowned(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        table = os.path.join(self.tmp, "policy.json")
        with open(table, "w", encoding="utf-8") as fh:
            json.dump({"roles": {"crown": {"box": "mothership", "model": "qwen3.8:27b", "think": "xhigh"}}}, fh)
        p = mock.patch.object(config, "POLICY_TABLE", table)
        p.start()
        self.addCleanup(p.stop)
        ollama_client._CROWN.update({"mtime": None, "crown": None})
        self.addCleanup(ollama_client._CROWN.update, {"mtime": None, "crown": None})
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(ollama_client, "can_think", lambda host, model: True)   # never ask a real box
        p.start()
        self.addCleanup(p.stop)

    def sent(self, model, job, pin):
        seen = []

        def fake_once(host, model, messages, tools_list, num_ctx, keep_alive, timeout=None, **k):
            seen.append(k.get("think", False))
            return {"message": {"role": "assistant", "content": "an answer long enough"}}
        with mock.patch.object(ollama_client, "_chat_once", fake_once), \
                mock.patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl):
            with ollama_client.call_context(job=job, think_pin=pin):
                ollama_client.chat(config.HEAVY_HOST, model, [{"role": "user", "content": "q"}], [])
        return seen


class WhatChatSendsWithAPin(_Crowned):
    def test_the_pinned_3_8_on_plain_chat_thinks_at_max(self):
        """The defect: pinned 3.8 + Normal ran thinking OFF. A pin of Max is xhigh on plain chat."""
        self.assertEqual(self.sent("qwen3.8:27b", job=False, pin=None), [True], "unpinned plain chat: plain on")
        self.assertEqual(self.sent("qwen3.8:27b", job=False, pin="max"), ["xhigh"])

    def test_the_pin_wins_over_the_job_rule_both_ways(self):
        self.assertEqual(self.sent("qwen3.8:27b", job=True, pin="off"), [False], "a pinned Off is honoured on a job")
        self.assertEqual(self.sent("qwen3.8:27b", job=True, pin="medium"), ["medium"])
        self.assertEqual(self.sent("qwen3.8:27b", job=True, pin=None), ["xhigh"], "no pin: the job rule as before")

    def test_the_pin_ends_with_its_block(self):
        with ollama_client.call_context(think_pin="max"):
            self.assertEqual(ollama_client._CALL_CONTEXT.think_pin, "max")
        self.assertIsNone(getattr(ollama_client._CALL_CONTEXT, "think_pin", None), "a pooled thread keeps no pin")


class TheRequestCarriesThePin(unittest.TestCase):
    def seen_pin(self, level):
        got = {}

        def body(q, status=None, diag=None):
            got["pin"] = getattr(ollama_client._CALL_CONTEXT, "think_pin", None)
            return {"route": "local", "answer": "ok"}
        with mock.patch.object(app, "_run_query_core_body", body):
            app._run_query_core(app.Q(text="hi", user="test", think_level=level))
        return got["pin"]

    def test_a_level_reaches_every_call_in_the_turn_and_garbage_does_not(self):
        self.assertEqual(self.seen_pin("max"), "max")
        self.assertEqual(self.seen_pin("off"), "off")
        self.assertIsNone(self.seen_pin(None))
        self.assertIsNone(self.seen_pin("xhigh"), "only the page's levels are a pin")


class ThePinStoresEffort(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(app.config, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.c = TestClient(app.app)

    def pin(self):
        return self.c.get("/prefs/pin", params={"user": "test"}).json()["pin"]

    def test_an_effort_pin_alone_is_a_pin_and_follows_the_account(self):
        self.c.post("/prefs/pin", json={"user": "test", "model": "auto", "effort": "normal", "think": "max"})
        self.assertEqual(self.pin()["think"], "max")

    def test_only_all_three_on_auto_clear_it(self):
        self.c.post("/prefs/pin", json={"user": "test", "model": "force_27b", "effort": "normal", "think": "max"})
        self.c.post("/prefs/pin", json={"user": "test", "model": "auto", "effort": "normal", "think": "max"})
        self.assertIsNotNone(self.pin(), "Effort still pinned")
        self.c.post("/prefs/pin", json={"user": "test", "model": "auto", "effort": "normal", "think": "auto"})
        self.assertIsNone(self.pin())

    def test_an_unknown_level_is_stored_as_auto(self):
        self.c.post("/prefs/pin", json={"user": "test", "model": "force_27b", "effort": "normal", "think": "ultra"})
        self.assertEqual(self.pin()["think"], "auto")


class ThePageHasBothAxes(unittest.TestCase):

    def test_effort_is_the_models_levels_and_work_is_how_long(self):
        html = app.CHAT_HTML
        for level in ("auto", "off", "low", "medium", "max"):
            self.assertIn('<option value="%s">' % level, html.split('id="thinkSel"', 1)[1].split("</select>", 1)[0])
        self.assertIn('<span class="axl">Work</span>', html)
        self.assertIn('<option value="think_hard">Think hard</option>', html)
        self.assertIn("think_level:", html, "the pin is sent with the request")
        self.assertIn("set Model and Effort to Auto and Work to Answer", html)


if __name__ == "__main__":
    unittest.main()
