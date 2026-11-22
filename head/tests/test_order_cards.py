"""An order a conversation starts appears in that conversation, live from the deck."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import history  # noqa: E402
import ollama_client  # noqa: E402
import p20_queue  # noqa: E402
import tablet_orders  # noqa: E402


class _TmpQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for target, attr, value in ((p20_queue, "QUEUE_DIR", os.path.join(self.tmp, "queue")),
                                    (history, "HISTORY_DIR", pathlib.Path(self.tmp) / "history"),
                                    (tablet_orders, "SHARE", os.path.join(self.tmp, "tabjobs"))):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, ollama_client._CALL_CONTEXT, "run_orders", None)


class AnnounceTests(_TmpQueue):
    def test_an_order_placed_on_a_chat_run_shows_in_that_conversation(self):
        seen = []
        ollama_client._CALL_CONTEXT.run_orders = []
        with ollama_client.delta_sink(seen.append):
            job = p20_queue.enqueue_factory_order({"name": "caption-sweep.20261016", "kind": "map"})
        self.assertEqual(seen, [{"kind": "order", "id": job["id"], "label": "caption-sweep.20261016"}])
        self.assertEqual(ollama_client._CALL_CONTEXT.run_orders, [job["id"]])

    def test_an_order_placed_anywhere_else_announces_nothing(self):
        seen = []
        ollama_client._CALL_CONTEXT.run_orders = None
        with ollama_client.delta_sink(seen.append):
            p20_queue.enqueue_factory_order({"name": "desk-own", "kind": "map"})
        self.assertEqual(seen, [])

    def test_a_tablet_order_is_announced_too(self):
        seen = []
        ollama_client._CALL_CONTEXT.run_orders = []
        with patch.object(tablet_orders, "payload_names", lambda: ["echo"]), \
                ollama_client.delta_sink(seen.append):
            out = tablet_orders.submit_map("probe", "echo", ["a", "b"], "ctl", "ctl")
        self.assertTrue(out.startswith("SUBMITTED"), out)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0]["id"].startswith("tablet:probe."))

    def test_a_control_that_repeats_an_item_is_refused_before_it_runs(self):
        """card-proof.20261016-150444: control 'ping-1' was also an item -> coverage 2/3 -> VOID after running."""
        with patch.object(tablet_orders, "payload_names", lambda: ["map_echo"]):
            out = tablet_orders.submit_map("p", "map_echo", ["ping-1", "ping-2"], "ping-1", "ping-1")
            ok = tablet_orders.submit_map("p", "map_echo", ["ping-1", "ping-2"], "ping-ctl", "ping-ctl")
        self.assertIn("is also one of the items", out)
        self.assertTrue(ok.startswith("SUBMITTED"), ok)
        self.assertEqual(len(os.listdir(os.path.join(tablet_orders.SHARE, "inbox"))), 1, "only the good order landed")

    def test_a_raising_hook_never_fails_the_order(self):
        def boom(job):
            raise RuntimeError("viewer broke")
        with patch.object(p20_queue, "ENQUEUE_HOOKS", [boom]):
            job = p20_queue.enqueue_factory_order({"name": "x", "kind": "map"})
        self.assertEqual(p20_queue.get(job["id"])["status"], "pending")


class CardTests(_TmpQueue):
    def test_the_card_follows_the_order_to_its_end(self):
        job = p20_queue.enqueue_factory_order({"name": "o1", "kind": "map"})
        c = app._order_card(job["id"])
        self.assertEqual((c["label"], c["status"], c["terminal"]), ("o1", "pending", False))
        p20_queue.claim(job["id"], "bighonker")
        live = {"order": "o1", "progress": "40 / 100", "rate": "", "tok_s": "write 57 tok/s",
                "remaining": "~3m left", "detail": "mapping"}
        with patch.object(app, "_factory_view", lambda h: dict(live) if h == "bighonker" else {}):
            c = app._order_card(job["id"])
        self.assertEqual((c["host"], c["progress"], c["tok_s"], c["remaining"]),
                         ("bighonker", "40 / 100", "write 57 tok/s", "~3m left"))
        p20_queue.mark(job["id"], "done")
        self.assertTrue(app._order_card(job["id"])["terminal"])

    def test_a_render_card_carries_no_prompt_and_no_person(self):
        job = p20_queue.enqueue("Alex", "personX", "a secret prompt", "/l.safetensors", "trig",
                                use="self", consent_reason="test")
        c = app._order_card(job["id"])
        self.assertEqual(c["label"], "render")
        blob = json.dumps(c)
        self.assertNotIn("a secret prompt", blob)
        self.assertNotIn("personX", blob)

    def test_a_tablet_card_reads_waiting_accepted_and_shipped(self):
        """The live proof (10-16) showed 'pending' while the counter said 'nothing in the letter slot' (= accepted)."""
        said = {"pending": "in the letter slot (not yet accepted): x.tjob.json",
                "accepted": "nothing in the letter slot and no shipped results for 'x'. If an order was accepted ...",
                "done": "x.results.json: usable=True VOID=False control=True coverage=2/2 rows=2 wall=3s"}
        for want, text in said.items():
            with patch.object(tablet_orders, "order_status", lambda name, t=text: t):
                c = app._order_card("tablet:x")
            self.assertEqual(c["status"], want, text)
            self.assertEqual(c["terminal"], want == "done")

    def test_unknown_order(self):
        from fastapi.testclient import TestClient
        self.assertFalse(TestClient(app.app).get("/orders/card/nope").json()["ok"])

    def test_the_saved_answer_keeps_its_order_cards(self):
        ollama_client._CALL_CONTEXT.run_orders = ["abc123def456"]
        with patch.object(app, "_auto_title_worker", lambda *a, **k: None):
            q = app.Q(text="make it", user="CardsUser")
            q.chat_id = history.resolve_chat("CardsUser", None)
            app._persist(q, "make it", "Placed the order.")
        self.assertEqual(history.load("CardsUser", q.chat_id)[-1].get("orders"), ["abc123def456"])


if __name__ == "__main__":
    unittest.main()
