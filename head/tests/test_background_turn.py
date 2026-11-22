"""Background turn: same agent turn, user's own time budget. Replaces the Absurd option."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import config  # noqa: E402
import ollama_client  # noqa: E402
import run_ledger  # noqa: E402


class AdmissionTests(unittest.TestCase):
    def test_a_known_choice_sets_the_budget_and_makes_the_turn_job_shaped(self):
        q = app.Q(text="dig into this", user=sorted(config.ADMIN_USERS)[0], background="4h")
        app._admit_background(q)
        self.assertEqual(q.background_seconds, 14400)
        self.assertTrue(app._is_job(q), "job-shaped exactly as the Absurd run it replaces")

    def test_no_background_means_no_budget_even_if_the_client_sent_one(self):
        q = app.Q(text="hi", user="Avery", background_seconds=999999)
        app._admit_background(q)
        self.assertIsNone(q.background_seconds)
        self.assertFalse(app._is_job(q))

    def test_an_unknown_choice_is_refused(self):
        with self.assertRaises(HTTPException) as cm:
            app._admit_background(app.Q(text="x", user="Alex", background="forever"))
        self.assertEqual(cm.exception.status_code, 400)

    def test_energy_deferral_applies_to_users_who_are_not_exempt(self):
        with patch.object(config, "ABSURD_ENERGY_UNBOUNDED_USERS", {"Alex"}), \
                patch.object(app.energy, "is_runnable_now", lambda: (False, {"price": 99})):
            with self.assertRaises(HTTPException) as cm:
                app._admit_background(app.Q(text="x", user="Avery", background="1h"))
            self.assertEqual(cm.exception.status_code, 409)
            q = app.Q(text="x", user="Alex", background="1h")
            app._admit_background(q)                                   # exempt: runs regardless
            self.assertEqual(q.background_seconds, 3600)


class BudgetNoteTests(unittest.TestCase):
    """The loop's clock ending a user-budgeted run must say so in the answer itself."""

    def _run(self, label):
        def fake_once(*a, **k):
            return {"message": {"role": "assistant", "tool_calls": [
                {"function": {"name": "coding_read", "arguments": {"path": "x"}}}]}}
        with patch.object(ollama_client, "_chat_once", fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", lambda *a, **k: "contents"), \
                patch.object(ollama_client, "_explain", lambda *a, **k: "Here is where the work stands."), \
                ollama_client.call_context(loop_deadline=time.monotonic() - 1, budget_label=label):
            # the budget is already spent: the loop ends on its deadline (not "no new work"), in both cases
            return ollama_client.chat("h", "m", [{"role": "user", "content": "go"}],
                                      [{"function": {"name": "coding_read"}}])

    def test_a_background_run_stopped_by_its_budget_says_so(self):
        answer, _ = self._run("4 hours")
        self.assertTrue(answer.startswith("Here is where the work stands."))
        self.assertIn("Stopped at your 4 hours budget: this is where the work stood, not a finished answer.", answer)

    def test_a_background_run_ended_by_the_tool_step_cap_says_so(self):
        """Main session's honesty note: a plain tool loop ends on its turn cap, not the clock - it must say that too."""
        def fake_once(*a, **k):
            return {"message": {"role": "assistant", "tool_calls": [
                {"function": {"name": "search_docs", "arguments": {"q": "x"}}}]}}
        with patch.object(ollama_client, "_chat_once", fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", lambda *a, **k: "contents"), \
                patch.object(ollama_client, "_explain", lambda *a, **k: "Partial findings."), \
                patch.object(config, "TOOL_LOOP_MAX", 2), \
                ollama_client.call_context(budget_label="1 hour"):
            answer, _ = ollama_client.chat("h", "m", [{"role": "user", "content": "go"}],
                                           [{"function": {"name": "search_docs"}}])
        self.assertIn("Stopped at the tool-step limit (2 steps), inside your 1 hour budget", answer)

    def test_without_a_user_budget_the_answer_is_untouched(self):
        answer, _ = self._run(None)
        self.assertEqual(answer, "Here is where the work stands.")


class HardLimitTests(unittest.TestCase):
    """Soft mode lets the in-flight turn finish; Hard mode cuts the turn at the deadline."""

    def test_soft_is_the_default(self):
        self.assertFalse(app.Q(text="x", user="Alex", background="3h").background_hard)

    def test_the_deadline_cuts_the_turn_in_flight(self):
        import cancellation
        cid = "hard-test-1"
        ev = cancellation.begin(cid)
        try:
            app._hard_stop(cid)
            self.assertTrue(ev.is_set(), "the run's own cancel event is what cuts a model call mid-turn")
            self.assertIn(cid, app._HARD_STOPPED)
        finally:
            app._HARD_STOPPED.discard(cid)
            cancellation.end(cid)

    def test_a_hard_stop_reads_as_the_budget_and_a_user_stop_as_a_stop(self):
        import cancellation

        def raise_cancel(*a, **k):
            raise cancellation.Cancelled()
        q = app.Q(text="x", user="Alex", background="3h", background_hard=True, cancel_id="hard-test-2")
        with patch.object(app, "_run_query_core", raise_cancel):
            app._HARD_STOPPED.add("hard-test-2")
            try:
                out = app._run_query(q)
            finally:
                app._HARD_STOPPED.discard("hard-test-2")
            self.assertEqual(out["route"], "budget")
            self.assertIn("hard 3 hours limit", out["answer"])
            self.assertEqual(app._run_query(q)["route"], "cancelled", "a user's Stop is still a Stop")


class NotifyTests(unittest.TestCase):
    USER = "BgUser"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._root = run_ledger.ROOT
        run_ledger.ROOT = __import__("pathlib").Path(self.tmp) / "hai-runs"
        self.addCleanup(setattr, run_ledger, "ROOT", self._root)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = patch.object(app, "_auto_title_worker", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def test_a_background_turn_notifies_when_it_lands_and_a_plain_turn_does_not(self):
        sent = []
        core = lambda q, status, diag: {"route": "local", "answer": "done"}
        with patch.object(app, "_run_query_core", core), \
                patch.object(app, "_background_notify", lambda q, cid, r, e: sent.append((cid, r["answer"]))):
            q = app.Q(text="long work", user=self.USER, ephemeral=True, background="1h")
            q.background_seconds = 3600
            app._accept_turn(q, "cid-bg-1")
            app._stream_worker(q, "cid-bg-1")
            plain = app.Q(text="hi", user=self.USER, ephemeral=True)
            app._accept_turn(plain, "cid-bg-2")
            app._stream_worker(plain, "cid-bg-2")
        self.assertEqual(sent, [("cid-bg-1", "done")])

    def test_a_chat_turn_names_its_conversation_for_the_windows_and_lets_go_after(self):
        """Window turns carry the conversation ID; pooled threads must not leak it to the next run."""
        import ollama_client
        seen = []
        core = lambda q, status, diag: seen.append(getattr(ollama_client._CALL_CONTEXT, "window_conv", None)) or \
            {"route": "local", "answer": "ok"}
        with patch.object(app, "_run_query_core", core):
            q = app.Q(text="hi", user=self.USER, ephemeral=True, chat_id="chat123")
            app._accept_turn(q, "cid-conv-1")
            app._stream_worker(q, "cid-conv-1")
            plain = app.Q(text="hi", user=self.USER, ephemeral=True)
            app._accept_turn(plain, "cid-conv-2")
            app._stream_worker(plain, "cid-conv-2")
        self.assertEqual(seen, ["%s/chat123" % self.USER, None])
        self.assertIsNone(getattr(ollama_client._CALL_CONTEXT, "window_conv", None))


if __name__ == "__main__":
    unittest.main()
