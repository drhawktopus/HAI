"""#37 TALK TO IT WHILE IT WORKS (requirement 2, "like Claude Code"): a message sent to a RUNNING run is delivered at
its next natural point - the start of a model turn, or instead of ending when the model has just answered - as a user
message the model must take into account; it is shown live (a `user_message` event), saved with the answer, and only
the run's owner (or an admin) may send one. A run that has ended answers 404 so the page sends a new message instead."""
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import cancellation  # noqa: E402
import history  # noqa: E402
import live_runs  # noqa: E402
import ollama_client  # noqa: E402
import run_ledger  # noqa: E402


class _TmpRoot(unittest.TestCase):
    def setUp(self):
        p = patch.object(app, "_auto_title_worker", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)
        self.tmp = tempfile.mkdtemp()
        self._root, self._hist = run_ledger.ROOT, history.HISTORY_DIR
        run_ledger.ROOT = pathlib.Path(self.tmp) / "hai-runs"
        history.HISTORY_DIR = pathlib.Path(self.tmp) / "history"
        live_runs._clear_for_tests()
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation._clear_for_tests()
        cancellation.unbind()
        with ollama_client._INFLIGHT_LOCK:
            ollama_client._INBOX.clear()
            ollama_client._DELIVERED.clear()

    def tearDown(self):
        run_ledger.ROOT, history.HISTORY_DIR = self._root, self._hist
        shutil.rmtree(self.tmp, ignore_errors=True)
        live_runs._clear_for_tests()
        cancellation.QueryRunRegistry.reset_for_tests()
        cancellation._clear_for_tests()
        with ollama_client._INFLIGHT_LOCK:
            ollama_client._INBOX.clear()
            ollama_client._DELIVERED.clear()


class DeliveredAtTheNextNaturalPoint(_TmpRoot):
    def _run(self, led, script, on_call=None):
        """The real chat() loop under a ledger; `script` = model replies in order; on_call(n) runs before reply n."""
        seq, self.seen, deltas = list(script), [], []

        def fake_once(host, model, messages, *a, **k):
            if on_call:
                on_call(len(self.seen))
            self.seen.append([dict(m) for m in messages])
            return seq.pop(0)

        with patch.object(ollama_client, "_chat_once", fake_once), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", lambda fn, args, *a, **k: "result"), \
                ollama_client.delta_sink(deltas.append), \
                ollama_client.run_ledger(led):
            out = ollama_client.chat("h", "m", [{"role": "user", "content": "go"}],
                                     [{"function": {"name": "search_docs"}}])
        self.assertFalse(seq, "unused model replies: the loop ended earlier than the script")
        return out, deltas

    @staticmethod
    def _tool_turn():
        return {"message": {"role": "assistant", "tool_calls": [{"function": {"name": "search_docs", "arguments": {}}}]}}

    @staticmethod
    def _answer(text):
        return {"message": {"role": "assistant", "content": text}}

    def test_a_message_sent_mid_run_reaches_the_model_at_the_next_turn(self):
        led = run_ledger.RunLedger.create("m1", "stream", {})
        post = lambda n: ollama_client.post_to_run("m1", "use the other file", "Alex") if n == 0 else None
        (answer, _), deltas = self._run(led, [self._tool_turn(), self._answer("done with the other file")],
                                        on_call=post)
        self.assertEqual(answer, "done with the other file")
        said = [m for m in self.seen[1] if m.get("role") == "user" and "use the other file" in m.get("content", "")]
        self.assertEqual(len(said), 1, "the second model turn must carry the message exactly once")
        self.assertIn("while you were working", said[0]["content"])
        self.assertEqual([d["text"] for d in deltas if d.get("kind") == "user_message"], ["use the other file"])
        delivered, late = ollama_client.take_run_messages("m1")
        self.assertEqual([d["text"] for d in delivered], ["use the other file"])
        self.assertEqual(late, [])

    def test_a_message_sent_while_it_answers_keeps_the_run_going(self):
        led = run_ledger.RunLedger.create("m2", "stream", {})
        post = lambda n: ollama_client.post_to_run("m2", "also add a test", "Alex") if n == 0 else None
        (answer, _), _ = self._run(led, [self._answer("first answer"), self._answer("answer with the test")],
                                   on_call=post)
        self.assertEqual(answer, "answer with the test", "the run must not end on the answer the message missed")

    def test_no_message_no_change(self):
        led = run_ledger.RunLedger.create("m3", "stream", {})
        (answer, _), deltas = self._run(led, [self._tool_turn(), self._answer("plain")])
        self.assertEqual(answer, "plain")
        self.assertFalse([d for d in deltas if d.get("kind") == "user_message"])

    def test_a_message_for_another_run_is_not_delivered_here(self):
        led = run_ledger.RunLedger.create("m4", "stream", {})
        ollama_client.post_to_run("someone-else", "not for you", "Alex")
        (answer, _), _ = self._run(led, [self._answer("mine")])
        self.assertEqual(answer, "mine")
        self.assertEqual(ollama_client.take_run_messages("someone-else")[1][0]["text"], "not for you")


class TheEndpoint(_TmpRoot):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        self.c = TestClient(app.app)
        self.owner = sorted(app.config.ADMIN_USERS)[0]

    def test_a_running_run_takes_the_message_and_every_page_sees_it_pending(self):
        run_ledger.RunLedger.create("live-1", "stream", {"q": {"user": self.owner, "chat_id": "c1"}})
        r = self.c.post("/query/message", json={"user": self.owner, "cancel_id": "live-1", "text": "stop and summarise"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["queued"])
        rows = history.pending(self.owner, "c1")
        self.assertEqual([(x["content"], x.get("interjection")) for x in rows], [("stop and summarise", True)])
        self.assertEqual(ollama_client._INBOX["live-1"][0]["text"], "stop and summarise")

    def test_no_such_run_is_404_so_the_page_sends_a_new_message(self):
        r = self.c.post("/query/message", json={"user": self.owner, "cancel_id": "gone", "text": "hello"})
        self.assertEqual(r.status_code, 404)

    def test_someone_elses_run_is_refused(self):
        run_ledger.RunLedger.create("live-2", "stream", {"q": {"user": self.owner, "chat_id": "c1"}})
        r = self.c.post("/query/message", json={"user": "NotTheOwner", "cancel_id": "live-2", "text": "hi"})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("live-2", ollama_client._INBOX)

    def test_an_empty_message_is_refused(self):
        run_ledger.RunLedger.create("live-3", "stream", {"q": {"user": self.owner, "chat_id": "c1"}})
        self.assertEqual(self.c.post("/query/message", json={"user": self.owner, "cancel_id": "live-3",
                                                              "text": "  "}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
