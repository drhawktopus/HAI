"""Tests cover two resume edge-cases triggered by a mid-job deploy:
1. Brief derivation: The brief is re-derived from the saved conversation; it must not lock onto a transient user check-in message (e.g., status update) and discard the actual task.
2. Concurrency: A resumed run must wait for any existing 'held' status (e.g., from a previous process) to clear before proceeding, rather than immediately failing or overflowing."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import chat_window  # noqa: E402
import config  # noqa: E402
import ollama_client  # noqa: E402


class AMidJobMessageIsNeverTheRequest(unittest.TestCase):
    TASK = "Design a 3D-printable cookie cutter in the shape of the LTT Labs logo. Deliver a print-ready STL file."

    def convo(self):
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": self.TASK},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "coding_create"}}]},
                {"role": "tool", "content": "ok"}]
        ollama_client.post_to_run("r-brief", "how far along are you?", "ClaudeSupervisor")
        slot = mock.Mock(key="r-brief", ledger=mock.Mock(run_id="r-brief"))
        self.assertEqual(ollama_client._drain_inbox(slot, msgs), 1)
        msgs.append({"role": "assistant", "content": "about halfway"})
        return msgs

    def test_the_brief_skips_a_delivered_message(self):
        msgs = self.convo()
        self.assertTrue(msgs[4]["content"].startswith(ollama_client._INBOX_NOTE), "the delivered message is marked")
        self.assertIs(ollama_client._current_brief(msgs), msgs[1])
        # a resume works on COPIES of the saved messages: the brief is still found, by content, not by identity
        copies = [dict(m) for m in msgs]
        self.assertEqual(ollama_client._current_brief(copies)["content"], self.TASK)

    def test_compaction_keeps_the_task_not_the_check_in(self):
        msgs = self.convo() + [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "x"}}]}]
        with mock.patch.object(ollama_client, "_chat_once",
                               return_value={"message": {"content": "summary of the work"}}):
            out, did = ollama_client._compact_messages("h", "m", msgs, 1000, "5m", 10,
                                                       brief=ollama_client._current_brief(msgs))
        self.assertTrue(did)
        kept = [m["content"] for m in out if m.get("role") == "user"]
        self.assertEqual(kept[0], self.TASK, "the task survives compaction verbatim")
        self.assertFalse(any(k.startswith(ollama_client._INBOX_NOTE) for k in kept[:1]))


class APreviousKirksWindowIsWaitedOut(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(config, "CHAT_WINDOWS_DIR", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        self.name = "hai-win-mothership-qwen3.8-flash-next-strata-q2-0-20261104-150354-ccaa"
        os.mkdir(os.path.join(self.tmp.name, self.name))
        self.claim = {"host": "mothership", "resource": "gpu", "holder": self.name + "_mothership"}

    def test_orphan_is_recognised_and_ours_is_not(self):
        self.assertEqual(chat_window.orphan_window(self.claim["holder"]), self.name)
        self.assertIsNone(chat_window.orphan_window("some-factory-order_mothership"))
        live = mock.Mock(name="w")
        live.name = self.name
        with mock.patch.dict(chat_window._WINDOWS, {("mothership", "m", 1): live}):
            self.assertIsNone(chat_window.orphan_window(self.claim["holder"]), "this process's own live window")

    def test_the_lead_closes_it_waits_and_is_then_dispatched(self):
        holds = iter([self.claim, self.claim, None])
        said, sleeps = [], []
        out = app._await_orphan_window(self.claim, lambda: next(holds), said.append,
                                       clock=lambda: 0.0, sleep=sleeps.append)
        self.assertIsNone(out, "the card is free once the leftover lets go")
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, self.name, chat_window.CLOSE)))
        self.assertTrue(sleeps, "it waited on status")
        self.assertTrue(any("from before a restart" in s for s in said), said)

    def test_a_strangers_claim_is_returned_untouched(self):
        other = {"host": "mothership", "resource": "gpu", "holder": "p20-train-v12_mothership"}
        self.assertIs(app._await_orphan_window(other, lambda: other, None), other)

    def test_a_leftover_that_never_lets_go_overflows_at_the_reporters_bound(self):
        t = iter(range(0, 100000, 100))
        out = app._await_orphan_window(self.claim, lambda: self.claim, None,
                                       clock=lambda: float(next(t)), sleep=lambda *_: None)
        self.assertEqual(out, self.claim, "past 3 reporter budgets the caller overflows as before")


class McCoyWaitsForTheLeftover(unittest.TestCase):
    def test_the_front_door_dispatches_the_lead_after_the_leftover_lets_go(self):
        import types
        import mccoy
        LEAD_BOX = {"lane": "lead", "tier": "lead", "decided": True}
        holder = "hai-win-mothership-qwen3.8-flash-next-x_mothership"
        claims = iter([{"holder": holder}, {"holder": holder}, None, None])
        dispatched = []
        q = types.SimpleNamespace(text="design a cookie cutter", user="ClaudeSupervisor", routing="auto",
                                  effort_mode=None, chat_id="c1")
        with mock.patch.object(mccoy, "verdict", return_value=LEAD_BOX), \
             mock.patch("comfy_client.render_busy", return_value=False), \
             mock.patch("scheduler.occupied_for", side_effect=lambda *a, **k: next(claims)), \
             mock.patch.object(chat_window, "orphan_window", side_effect=lambda h: holder if h == holder else None), \
             mock.patch.object(chat_window, "close_orphan", return_value=True), \
             mock.patch.object(app.time, "sleep", lambda *_: None), \
             mock.patch.object(config, "CHAT_WINDOWS", False):
            out = app._mccoy_serve(q, False, lambda *_: None,
                                   lambda: dispatched.append(1) or {"route": "mothership"})
        self.assertEqual(out[0], {"route": "mothership"}, "served on the lead, not overflowed")
        self.assertEqual(dispatched, [1])


if __name__ == "__main__":
    unittest.main()
