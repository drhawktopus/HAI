"""Pinned rows bypass re-derivation and rerouting, retain their exact model (no crown swap), wait on busy boxes, and launch via the specific chat window defined by the row."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import chat_window  # noqa: E402
import config  # noqa: E402
import ollama_client  # noqa: E402
import row_pins  # noqa: E402

RIGBY_ORDER = {"model": "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", "ctx": 32768, "gpu": "vulkan"}
TABLE = {
    "roles": {"crown": {"box": "mothership", "model": "qwen3.8-flash-next-strata-q2_0", "think": "xhigh"}},
    "families": {
        "chat-c1": {"tiers": {
            "lead": {"best_quality": [{"box": "mothership", "model": "qwen3.8:27b", "think": "on"},
                                      {"box": "mothership", "model": "qwen3.8-flash-next-strata-q2_0", "think": "xhigh"}]},
            "rigby": {"best_quality": [{"box": "rigby", "model": "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", "think": "off",
                                        "order": RIGBY_ORDER}]},
            "tablets": {"best_quality": [{"box": "tablets", "model": "qwen2.5:1.5b", "think": "off"}]},
            "8gb": {"best_quality": [{"box": "bighonker", "model": "qwen3.6:35b-a3b-ud-q3kxl-mtp", "think": "on"},
                                     {"box": "bighonker", "model": "nemotron-3-nano:4b", "think": "on"}]}}},
        "coding-f1": {"tiers": {"8gb": {"best_quality": [{"box": "softserve", "model": "qwen3.5:9b", "think": "off"}]}}},
        "vlm": {"tiers": {"8gb": {"best_quality": [{"box": "bighonker", "model": "qwen3-vl:8b", "think": "off"}]}}},
    },
}


class _Users(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name, val in (("STATE_DIR", self.tmp), ("ADMIN_USERS", {"Alex"}), ("POWER_USERS", {"Pat"})):
            p = mock.patch.object(config, name, val)
            p.start()
            self.addCleanup(p.stop)


class TheRowsAreTheMenus(unittest.TestCase):
    def test_crown_first_then_best_quality_one_per_box_and_model(self):
        ids = [r["id"] for r in row_pins.rows(TABLE)]
        self.assertEqual(ids, ["mothership/qwen3.8-flash-next-strata-q2_0", "mothership/qwen3.8:27b",
                               "rigby/Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", "bighonker/qwen3.6:35b-a3b-ud-q3kxl-mtp",
                               "bighonker/nemotron-3-nano:4b", "softserve/qwen3.5:9b"])

    def test_tablets_and_other_families_are_not_pinnable(self):
        boxes_models = {(r["box"], r["model"]) for r in row_pins.rows(TABLE)}
        self.assertNotIn(("tablets", "qwen2.5:1.5b"), boxes_models)   # Tolkien places the tablets (checklist C5)
        self.assertNotIn(("bighonker", "qwen3-vl:8b"), boxes_models)  # a vision row is not a chat brain

    def test_a_rigby_row_keeps_its_own_launch_fields_and_labels_are_short(self):
        rows = {r["id"]: r for r in row_pins.rows(TABLE)}
        self.assertEqual(rows["rigby/Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"]["order"], RIGBY_ORDER)
        self.assertEqual(rows["mothership/qwen3.8:27b"]["label"], "Qwen 3.8 27B · Mothership")
        self.assertIsNone(rows["mothership/qwen3.8:27b"]["order"])


class TheSlashCommand(unittest.TestCase):
    def test_a_box_alone_takes_its_first_row_and_words_pick_another(self):
        row, rest, err = row_pins.parse_slash("/on mothership write it", TABLE)
        self.assertEqual((row["model"], rest, err), ("qwen3.8-flash-next-strata-q2_0", "write it", None))
        row, rest, _ = row_pins.parse_slash("/on mothership:27b  write it\nplease", TABLE)
        self.assertEqual((row["model"], rest), ("qwen3.8:27b", "write it\nplease"))

    def test_mistakes_are_said(self):
        self.assertIn("Unknown box", row_pins.parse_slash("/on tablets hi", TABLE)[2])
        self.assertIn("Nothing to send", row_pins.parse_slash("/on rigby", TABLE)[2])
        self.assertIn("No menu row", row_pins.parse_slash("/on softserve:coder hi", TABLE)[2])

    def test_anything_else_passes_through(self):
        for text in ("on mothership hi", "/only this", "hello /on rigby x"):
            self.assertEqual(row_pins.parse_slash(text, TABLE), (None, text, None))


class WhichPinTheTurnTakes(_Users):
    def test_message_beats_the_chat_which_beats_the_page(self):
        row_pins.set_chat_pin("Alex", "c1", "bighonker/nemotron-3-nano:4b")
        page = "softserve/qwen3.5:9b"
        row, text, src, err = row_pins.for_turn("Alex", "/on rigby hi", page, "c1", TABLE)
        self.assertEqual((row["box"], text, src, err), ("rigby", "hi", "message", None))
        row, _, src, _ = row_pins.for_turn("Alex", "hi", page, "c1", TABLE)
        self.assertEqual((row["id"], src), ("bighonker/nemotron-3-nano:4b", "chat"))
        row, _, src, _ = row_pins.for_turn("Alex", "hi", page, "c2", TABLE)
        self.assertEqual((row["id"], src), (page, "page"))
        self.assertEqual(row_pins.for_turn("Alex", "hi", None, "c2", TABLE), (None, "hi", None, None))

    def test_a_resumed_on_turn_still_beats_the_chat(self):
        row_pins.set_chat_pin("Alex", "c1", "bighonker/nemotron-3-nano:4b")
        row, _, src, _ = row_pins.for_turn("Alex", "hi", "softserve/qwen3.5:9b", "c1", TABLE, once=True)
        self.assertEqual((row["id"], src), ("softserve/qwen3.5:9b", "message"))

    def test_power_users_may_and_everyone_else_is_told_no(self):
        self.assertEqual(row_pins.for_turn("Pat", "/on rigby hi", None, None, TABLE)[2], "message")
        for text, page in (("/on rigby hi", None), ("hi", "softserve/qwen3.5:9b")):
            row, out, src, err = row_pins.for_turn("Avery", text, page, None, TABLE)
            self.assertEqual((row, out, src), (None, text, None))
            self.assertIn("admins and power users", err)

    def test_a_chat_pin_left_by_someone_no_longer_allowed_is_not_served(self):
        row_pins.set_chat_pin("Avery", "c1", "softserve/qwen3.5:9b")
        self.assertEqual(row_pins.for_turn("Avery", "hi", None, "c1", TABLE), (None, "hi", None, None))

    def test_a_row_gone_from_the_menu_is_said_never_guessed(self):
        self.assertIn("not on the menu", row_pins.for_turn("Alex", "hi", "softserve/gone:1b", None, TABLE)[3])

    def test_chat_pins_round_trip_and_unpin(self):
        self.assertEqual(row_pins.set_chat_pin("Alex", "c1", "rigby/x"), "rigby/x")
        self.assertEqual(row_pins.chat_pin("Alex", "c1"), "rigby/x")
        self.assertIsNone(row_pins.set_chat_pin("Alex", "c1", ""))
        self.assertIsNone(row_pins.chat_pin("Alex", "c1"))


class APinKeepsItsExactModel(unittest.TestCase):
    """lead_model swaps a JOB call on Mothership's standing model to the crown; an exact pin to the 27B keeps the 27B."""
    def test_the_crown_swap_is_off_under_an_exact_pin(self):
        crown = {"box": "mothership", "model": "qwen3.8-flash-next-strata-q2_0"}
        with mock.patch.object(ollama_client, "_crown", lambda: crown):
            self.assertEqual(ollama_client.lead_model(config.HEAVY_HOST, config.HEAVY_MODEL, True), crown["model"])
            with ollama_client.call_context(exact_model=True):
                self.assertEqual(ollama_client.lead_model(config.HEAVY_HOST, config.HEAVY_MODEL, True),
                                 config.HEAVY_MODEL)
            self.assertEqual(ollama_client.lead_model(config.HEAVY_HOST, config.HEAVY_MODEL, True), crown["model"],
                             "the flag ends with its block")


class ServingAPin(_Users):
    Q = mock.Mock(user="Alex", persona="")

    def test_rigby_goes_through_tolkiens_window_with_its_own_fields_and_tools(self):
        row = row_pins.resolve("rigby/Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", TABLE)
        with mock.patch.object(chat_window, "tolkien_window_enabled_for", return_value=True):
            (url, model, ctx, tl, label, think), order = app._pinned_brain(self.Q, row)
        # order must carry the row's menu cell (family, tier); missing keys break resolution
        self.assertEqual((url, model, ctx, order), (config.TOLKIEN_WINDOW_URL, RIGBY_ORDER["model"], 32768,
                                                    dict(RIGBY_ORDER, family="chat-c1", tier="rigby")))
        self.assertTrue(tl, "a pinned rigby turn has HAI's tools")
        import tools
        self.assertIn("escalate_to_mothership", [t["function"]["name"] for t in tools.BRAIN_TOOLS], "positive control")
        self.assertNotIn("escalate_to_mothership", [t.get("function", {}).get("name") for t in tl])

    def test_an_effort_pin_reaches_rigbys_engine(self):
        """rigby's server takes --reasoning from these fields, so a row priced with thinking off stayed off whatever the pin said."""
        row = row_pins.resolve("rigby/Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", TABLE)
        with mock.patch.object(chat_window, "tolkien_window_enabled_for", return_value=True):
            def serve(level):
                return app._pinned_brain(mock.Mock(user="Alex", persona="", think_level=level), row)
            (_, _, ctx, _, _, _), order = serve("max")
            self.assertEqual((ctx, order["ctx"], order["reasoning"]), (32768, 32768, "on"), "effort turns thinking on; the window stays the menu's")
            (_, _, ctx, _, _, _), order = serve("medium")
            self.assertEqual((ctx, order["reasoning"]), (32768, "on"))
            (_, _, ctx, _, _, _), order = serve("off")
            self.assertEqual((ctx, order["reasoning"]), (32768, "off"))
            (_, _, ctx, _, _, _), order = serve(None)
            self.assertEqual((ctx, order), (32768, dict(RIGBY_ORDER, family="chat-c1", tier="rigby")), "no pin: the row as priced")

    def test_a_rigby_job_gets_the_measured_job_window_and_a_chat_keeps_the_rows(self):
        row = row_pins.resolve("rigby/Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", TABLE)
        with mock.patch.object(chat_window, "tolkien_window_enabled_for", return_value=True):
            (_, _, ctx, _, _, _), order = app._pinned_brain(mock.Mock(user="Alex", persona="", background="3h", think_level="max"), row)
            self.assertEqual((ctx, order["ctx"]), (config.TOLKIEN_JOB_CTX, config.TOLKIEN_JOB_CTX))
            (_, _, ctx, _, _, _), order = app._pinned_brain(mock.Mock(user="Alex", persona="", background=None, think_level="max"), row)
            self.assertEqual((ctx, order["ctx"]), (32768, 32768))

    def test_rigby_off_for_the_account_is_said(self):
        row = row_pins.resolve("rigby/Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf", TABLE)
        with mock.patch.object(chat_window, "tolkien_window_enabled_for", return_value=False):
            self.assertIn("rigby's chat window is not on", app._pinned_brain(self.Q, row))

    def test_a_busy_box_is_waited_for_with_a_notice_then_served(self):
        import scheduler
        row = row_pins.resolve("bighonker/nemotron-3-nano:4b", TABLE)
        held = iter([{"holder": "a factory order"}, {"holder": "a factory order"}, None])
        said, slept = [], []
        with mock.patch.object(scheduler, "occupied_for", lambda *a, **k: next(held)), \
                mock.patch.object(ollama_client, "reachable", return_value=True):
            out, order = app._pinned_brain(self.Q, row, status=said.append, sleep=slept.append)
        self.assertEqual((out[0], out[1], order), (scheduler.CARDS["bighonker"]["url"], "nemotron-3-nano:4b", None))
        self.assertEqual(len(slept), 2)
        self.assertEqual(len([s for s in said if "your pin waits" in s]), 1, "the notice is said once, not per poll")

    def test_an_unreachable_box_is_said_and_never_swapped(self):
        row = row_pins.resolve("softserve/qwen3.5:9b", TABLE)
        with mock.patch.object(ollama_client, "reachable", return_value=False):
            msg = app._pinned_brain(self.Q, row)
        self.assertIn("not moved to another box", msg)

    def _served(self, **kw):
        import scheduler
        row = row_pins.resolve("mothership/qwen3.8:27b", TABLE)
        with mock.patch.object(app, "_ensure_lead", return_value=True), \
                mock.patch.object(scheduler, "occupied_for", lambda *a, **k: None):
            (url, model, ctx, tl, label, think), order = app._pinned_brain(self.Q, row, **kw)
        return [t["function"]["name"] for t in tl]

    def test_a_pin_has_the_full_toolset_the_coding_tools_included(self):
        """Pinned coding jobs receive the brain's toolset (excluding escalation) plus all coding tools."""
        import tools
        names = self._served()
        for n in ("coding_workspace", "coding_create", "coding_patch", "coding_test", "coding_task", "coding_review"):
            self.assertIn(n, names)
        self.assertNotIn("escalate_to_mothership", names)
        brain = {t["function"]["name"] for t in tools.BRAIN_TOOLS}
        coding = {t["function"]["name"] for t in tools.CODING_TOOLS}
        self.assertEqual(set(names), (brain - {"escalate_to_mothership"}) | coding, "nothing else was added or lost")

    def test_a_run_back_on_its_window_keeps_the_pool_it_started_with(self):
        import tools
        self.assertEqual(self._served(resumed=True, pinned=False), [t["function"]["name"] for t in tools.BRAIN_TOOLS])
        self.assertIn("coding_create", self._served(resumed=True, pinned=True), "a pinned job keeps its coding tools")

    def test_a_pinned_coding_request_gets_the_coding_brief(self):
        msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "x"}]
        job = ("Workspace for this job: the hai-scratch coding workspace, folder ltt-jarvis/x/ (create it; put every "
               "file you make there).\n\nBuild a page.")
        out = app._turn_pin_briefs(msgs, job)
        self.assertIn("CODING MODE:", out[0]["content"])
        self.assertNotIn("INTERNAL HANDOFF", out[0]["content"], "a pin is the user's model, not the lead's handoff")
        self.assertEqual((msgs[0]["content"], out[1]), ("S", msgs[1]), "the caller's messages are not changed")
        self.assertIs(app._turn_pin_briefs(msgs, "what is the weather tomorrow"), msgs, "an ordinary chat adds nothing")
        self.assertEqual(app._heavy_suffix(job), app.HEAVY_CONTINUATION + app._work_briefs(job), "one derivation")

    def test_a_lab_order_on_mothership_is_waited_for_before_the_card_is_judged(self):
        """Must wait for the lab order before judging the card, as the usable check misidentifies a full card without an ollama model as a game."""
        import scheduler
        row = row_pins.resolve("mothership/qwen3.8:27b", TABLE)
        held = iter([{"holder": "hai-win-left-by-the-previous-kirk"}, None, None, None])
        seen = []

        def occupied(*a, **k):
            h = next(held)
            seen.append("held" if h else "free")
            return h

        def ensure(status=None):
            seen.append("ensure")
            return True
        with mock.patch.object(app, "_ensure_lead", ensure), mock.patch.object(scheduler, "occupied_for", occupied):
            out, order = app._pinned_brain(self.Q, row, sleep=lambda s: None)
        self.assertEqual(seen[:3], ["held", "free", "ensure"], "waited for the holder, then judged the freed card")
        self.assertEqual(out[1], "qwen3.8:27b")

    def test_a_mothership_that_will_not_wake_is_said(self):
        row = row_pins.resolve("mothership/qwen3.8:27b", TABLE)
        with mock.patch.object(app, "_ensure_lead", return_value=False), \
                mock.patch.dict(app.LEAD_UNAVAILABLE, {"why": "did not wake"}):
            self.assertIn("Mothership did not wake", app._pinned_brain(self.Q, row))


class TheTurnIsWired(unittest.TestCase):
    """Source-level pins for a seam with no cheap behavioural harness (the whole turn, as test_rigby_window does): the
    pin is read before McCoy, McCoy and the weighing are skipped, the pinned box serves at its exact model, and a failed
    pinned call returns instead of walking the fallbacks."""
    def test_the_core_body_order(self):
        import inspect
        src = inspect.getsource(app._run_query_core_body)
        for before, after in (("_turn_pin(q)", "_mccoy_serve("), ('(None, "pinned", None) if (_pin or _resume_row)', "_mccoy_serve("),
                              ("_pinned_brain(q, _pin or _resume_row, status", "pick_brain()"),
                              ("_turn_pin_briefs(messages, t) if _pin else messages", "PINNED {b_name} failed (not rerouted)"),
                              ("exact_model=bool(_pin or _resume_row)", "_band_fallbacks("),
                              ("PINNED {b_name} failed (not rerouted)", "_brain_fallbacks(")):
            self.assertIn(before, src)
            self.assertLess(src.index(before), src.index(after), before)


class ThePage(_Users):
    def test_the_rows_reach_only_admins_and_power_users(self):
        from fastapi.testclient import TestClient
        c = TestClient(app.app)
        with mock.patch.object(row_pins, "_table", lambda: TABLE):
            self.assertEqual(c.get("/models/pins", params={"user": "Avery"}).json(), {"allowed": False, "rows": []})
            got = c.get("/models/pins", params={"user": "Pat"}).json()
            self.assertTrue(got["allowed"])
            self.assertEqual(got["rows"][0]["id"], "mothership/qwen3.8-flash-next-strata-q2_0")
            self.assertEqual(c.post("/prefs/chat-pin", json={"user": "Avery", "chat_id": "c", "row": "x"}).status_code, 403)
            self.assertEqual(c.post("/prefs/chat-pin", json={"user": "Alex", "chat_id": "c",
                                                             "row": "softserve/qwen3.5:9b"}).json()["row"],
                             "softserve/qwen3.5:9b")
            # the account pin keeps a row only for an account that may pin it
            self.assertEqual(c.post("/prefs/pin", json={"user": "Avery", "model": "row:softserve/qwen3.5:9b",
                                                        "effort": "normal", "think": "auto"}).json()["pin"], None)
            self.assertEqual(c.post("/prefs/pin", json={"user": "Alex", "model": "row:softserve/qwen3.5:9b",
                                                        "effort": "normal", "think": "auto"}).json()["pin"]["model"],
                             "row:softserve/qwen3.5:9b")

    def test_the_page_sends_the_row_and_offers_this_chat(self):
        html = app.CHAT_HTML
        for needle in ('id="pinChat"', "pin_row:(pinRowVal||undefined)", "loadPinRows();", "/models/pins?user=",
                       "applyChatPin()"):
            self.assertIn(needle, html)


if __name__ == "__main__":
    unittest.main()
