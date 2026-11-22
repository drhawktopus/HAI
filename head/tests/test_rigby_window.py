"""Tests for Rigby chat window integration.

Serves rows via config.TOLKIEN_WINDOW_URL with OpenAI-style turns; falls back to Fellowship turns.
Does not place orders or reach live models."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import chat_window as CW
import config
import ollama_client
import scheduler

ROW = {"model": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "ctx": 32768, "temperature": 0.7, "gpu": "gtx1060",
       "fit_target": 512, "build": "cuda", "arms": ["rigby"]}
CAND = {"host": scheduler.FELLOWSHIP_HOST, "box": "rigby", "entry": {"model": ROW["model"], "order": ROW}}


class TheAddress(unittest.TestCase):
    def test_rigby_speaks_the_openai_wire_at_its_own_address(self):
        url = config.TOLKIEN_WINDOW_URL + "/v1/chat/completions"
        self.assertEqual(config.swap_base(config.TOLKIEN_WINDOW_URL, ROW["model"]), config.TOLKIEN_WINDOW_URL)
        self.assertTrue(CW.is_swap_url(url))
        self.assertEqual(CW._host_for(url), CW.TOLKIEN_BOX)
        self.assertTrue(config.TOLKIEN_WINDOW_URL.startswith("http://127.0.0.1:"), "a bypassed call is refused at once")

    def test_it_is_off_until_a_state_file_names_the_user(self):
        import json
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with mock.patch.object(config, "STATE_DIR", d), mock.patch.object(CW, "enabled_for", return_value=True):
            self.assertFalse(CW.tolkien_window_enabled_for("ClaudeSupervisor"), "absent file = off")
            with open(os.path.join(d, "rigby-window.json"), "w", encoding="utf-8") as f:
                json.dump({"users": ["ClaudeSupervisor"]}, f)
            self.assertTrue(CW.tolkien_window_enabled_for("ClaudeSupervisor"))
            self.assertFalse(CW.tolkien_window_enabled_for("Avery"), "widened by name")
            with open(os.path.join(d, "rigby-window.json"), "w", encoding="utf-8") as f:
                json.dump({"users": ["*"]}, f)
            self.assertTrue(CW.tolkien_window_enabled_for("Avery"))


class OpeningTheWindow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        for p in (mock.patch.object(config, "CHAT_WINDOWS_DIR", self.d),
                  mock.patch.object(CW, "_ensure_beat", lambda: None),
                  mock.patch.dict(CW._WINDOWS, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.sent = []

    def intake(self, tspec):
        self.sent.append(tspec)
        return {"ok": True, "steps": [{"host": scheduler.FELLOWSHIP_HOST, "id": "desk1"}]}

    def test_a_chat_window_order_with_the_rows_own_settings(self):
        with CW.tolkien_order(ROW):
            w = CW.window_for(CW.TOLKIEN_BOX, ROW["model"], 32768, intake=self.intake, strata=True)
        t = self.sent[0]
        self.assertEqual((t["kind"], t["model"], t["ctx"], t["gpu"], t["fit_target"], t["build"], t["temperature"]),
                         ("chat.window", ROW["model"], 32768, "gtx1060", 512, "cuda", 0.7))
        self.assertEqual(t["items_dir"], config.CHAT_WINDOWS_TOLKIEN.rstrip("/") + "/" + w.name,
                         "the folder as Tolkien mounts it")
        self.assertTrue(t["idle_ttl"] > 0)
        self.assertTrue(w.strata and w.host == CW.TOLKIEN_BOX and w.desk_id == "desk1")

    def test_the_desk_gets_the_weighed_family_and_tier(self):
        """The live proof was refused without them: the desk ranks a Fellowship form by family and tier."""
        import app
        seen = {}

        def p20_order(req):
            seen.update(family=req.family, tier=req.tier, only=list(req.only),
                        fel_kind=req.fellowship["spec"]["kind"], fel_requires=req.fellowship["requires"])
            return {"ok": True, "steps": [{"host": scheduler.FELLOWSHIP_HOST, "id": "desk9"}]}
        with mock.patch.object(app, "p20_order", side_effect=p20_order), \
                CW.tolkien_order(dict(ROW, family="chat-turn", tier="fellowship")):
            CW.window_for(CW.TOLKIEN_BOX, ROW["model"], 32768, strata=True)
        self.assertEqual(seen, {"family": "chat-turn", "tier": "fellowship", "only": [scheduler.FELLOWSHIP_HOST],
                                "fel_kind": "chat.window", "fel_requires": ["rigby.llm"]})

    def test_a_held_or_misplaced_order_is_unavailable(self):
        held = lambda t: {"held": True, "id": "x", "why": ["rigby busy"]}
        with mock.patch.object(CW, "_cancel"), self.assertRaises(CW.WindowUnavailable):
            CW._open_tolkien_window("n1", self.d, ROW["model"], 8192, held, 0.0)
        elsewhere = lambda t: {"ok": True, "steps": [{"host": scheduler.BRAIN, "id": "d"}]}
        with self.assertRaises(CW.WindowUnavailable):
            CW._open_tolkien_window("n2", self.d, ROW["model"], 8192, elsewhere, 0.0)


class ServingTheRow(unittest.TestCase):
    def q(self):
        import app
        return app.Q(text="hi", user="ClaudeSupervisor", routing="auto")

    def serve(self, chat=None, on=True, cand=CAND):
        import app
        with mock.patch.object(CW, "tolkien_window_enabled_for", return_value=on), \
                mock.patch.object(ollama_client, "chat", side_effect=chat or (lambda *a, **k: ("hello", None))) as c:
            return app._tolkien_window_turn(self.q(), [{"role": "user", "content": "hi"}], cand, "hi"), c

    def test_off_or_not_rigby_never_calls_it(self):
        out, c = self.serve(on=False)
        self.assertIsNone(out)
        c.assert_not_called()
        out, c = self.serve(cand=dict(CAND, box="fellowship"))
        self.assertIsNone(out)

    def test_on_it_answers_through_the_window_with_the_bare_turn(self):
        out, c = self.serve()
        self.assertEqual(out["answer"], "hello")
        args = c.call_args[0]
        self.assertEqual((args[0], args[1]), (config.TOLKIEN_WINDOW_URL, ROW["model"]))
        self.assertIn("hi", args[2][0]["content"])
        self.assertEqual(args[3], [], "no tools: the row was priced bare")

    def test_a_window_that_cannot_serve_falls_back(self):
        def fail(*a, **k):
            raise ollama_client.ChatError(config.TOLKIEN_WINDOW_URL, ROW["model"], "the window is failed before any output",
                                          unavailable=True)
        out, _c = self.serve(chat=fail)
        self.assertIsNone(out, "the whole-order Fellowship turn serves it")


class OneFellowshipDoor(unittest.TestCase):
    """_serve_fellowship: rigby's window first (when on and the row is rigby's), else the whole-order turn - the one door
    for the band's first row, for the walk down the band when no card row could serve (the matrix proof's 2nd turn
    skipped a free rigby and ended "No host reachable"), and for the fallback after a failed card row."""
    W = ("chat-turn", "fellowship", True, [])

    def test_the_window_serves_first(self):
        import app
        with mock.patch.object(app, "_tolkien_window_turn", return_value={"answer": "a", "label": "w", "model": "m"}), \
                mock.patch("fellowship_turn.answer_turn") as whole:
            self.assertEqual(app._serve_fellowship(None, [], CAND, self.W, "hi")["label"], "w")
        whole.assert_not_called()

    def test_else_the_whole_order_turn(self):
        import app
        with mock.patch.object(app, "_tolkien_window_turn", return_value=None), \
                mock.patch("fellowship_turn.answer_turn", return_value={"answer": "b", "label": "o", "model": "m"}):
            self.assertEqual(app._serve_fellowship(None, [], CAND, self.W, "hi")["label"], "o")

    def test_no_candidate_serves_nothing(self):
        import app
        self.assertIsNone(app._serve_fellowship(None, [], None, self.W, "hi"))

    def test_the_walk_reaches_the_fellowship_row_when_no_card_row_serves(self):
        """Source-level pin for a seam with no cheap behavioural harness (the whole turn): after _placed_brain finds
        nothing, the band's Fellowship row is tried before pick_brain."""
        import inspect
        import app
        src = inspect.getsource(app._run_query_core_body)
        i_placed = src.index("_placed = _placed_brain(")
        i_walk = src.index("fellowship_turn.servable(c)", i_placed)
        i_ladder = src.index("pick_brain()", i_placed)
        self.assertLess(i_walk, i_ladder)


if __name__ == "__main__":
    unittest.main()
