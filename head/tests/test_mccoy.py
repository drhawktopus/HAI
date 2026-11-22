"""McCoy shadow-mode tests.

Verifies that shadow mode tags Auto turns without raising exceptions, and that
the front-door wire fires exactly once per run ID."""
import json
import os
import tempfile
import unittest
from unittest import mock

import app
import config
import mccoy


class FakeQ:
    """Duck-typed stand-in for app.py's Q dataclass; only the fields mccoy.special_route reads."""
    def __init__(self, **kw):
        self.text = kw.get("text", "")
        self.user = kw.get("user", "Alex")
        self.routing = kw.get("routing", "auto")
        self.effort_mode = kw.get("effort_mode", "normal")
        self.persona = kw.get("persona", "")
        self.mode = kw.get("mode", "")
        self.chat_id = kw.get("chat_id")
        self.absurd_max_seconds = kw.get("absurd_max_seconds")
        self.self_eng_max_passes = kw.get("self_eng_max_passes")
        self.external_agent = kw.get("external_agent")
        self.external_use = kw.get("external_use")
        self.ephemeral = kw.get("ephemeral", False)


class McCoyConfigBase(unittest.TestCase):
    """Isolate MCCOY_LOG_DIR to a temp dir for the lifetime of the test class."""
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mccoy-test-")
        self.addCleanup(self._tmp.cleanup)
        self._orig_log_dir = config.MCCOY_LOG_DIR
        config.MCCOY_LOG_DIR = self._tmp.name
        # Reset active users to empty set to prevent test pollution from live environment overrides
        p = mock.patch.object(config, "MCCOY_ACTIVE_USERS", set())
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        config.MCCOY_LOG_DIR = self._orig_log_dir

    def log_rows(self):
        """All log rows written so far (across every month file)."""
        rows = []
        for name in sorted(os.listdir(self._tmp.name)):
            if name.endswith(".jsonl"):
                with open(os.path.join(self._tmp.name, name), encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            rows.append(json.loads(line))
        return rows


class TestShadowHappyPath(McCoyConfigBase):
    def test_shadow_spawns_thread_and_writes_log_row(self):
        with mock.patch.object(mccoy, "triage", return_value={
                "tier": "fellowship", "family": "chat", "kind": "chat",
                "confidence": {"tier": 0.95, "family": 0.9, "kind": 0.85},
                "decided": True, "tier_if_used": "fellowship", "wall_s": 0.1}):
            t = mccoy.shadow("hows your day going", "Alex", routing="auto", chat_id="c1")
            self.assertIsNotNone(t, "shadow() must return the thread it spawned")
            # Join the worker thread BEFORE the mock is released, so it never sees the real triage()
            # (which would make a real httpx call to the live McCoy host).
            t.join(timeout=2)
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mode"], "shadow")
        self.assertEqual(rows[0]["tier"], "fellowship")
        self.assertEqual(rows[0]["user"], "Alex")
        self.assertEqual(rows[0]["chat_id"], "c1")
        self.assertNotIn("error", rows[0])

    def test_shadow_does_not_raise_into_caller(self):
        with mock.patch.object(mccoy, "triage", side_effect=RuntimeError("boom")):
            t = mccoy.shadow("hello", "Alex", routing="auto")
            self.assertIsNotNone(t)   # the thread was spawned; the failure happens inside it
            t.join(timeout=2)        # join before the mock is released; no real triage() call


class TestShadowGates(McCoyConfigBase):
    def test_off_mode_returns_none(self):
        with mock.patch.object(config, "MCCOY_MODE", "off"):
            with mock.patch.object(mccoy, "triage") as fake_triage:
                self.assertIsNone(mccoy.shadow("hello", "Alex", routing="auto"))
                fake_triage.assert_not_called()

    def test_non_auto_routing_returns_none(self):
        with mock.patch.object(mccoy, "triage") as fake_triage:
            self.assertIsNone(mccoy.shadow("hello", "Alex", routing="force_27b"))
            fake_triage.assert_not_called()

    def test_test_user_returns_none(self):
        with mock.patch.object(mccoy, "triage") as fake_triage:
            self.assertIsNone(mccoy.shadow("hello", "test-homelab", routing="auto"))
            fake_triage.assert_not_called()

    def test_empty_text_returns_none(self):
        with mock.patch.object(mccoy, "triage") as fake_triage:
            self.assertIsNone(mccoy.shadow("   ", "Alex", routing="auto"))
            fake_triage.assert_not_called()


class TestNetworkFailureSwallowed(McCoyConfigBase):
    def test_triage_failure_writes_error_row_and_does_not_raise(self):
        with mock.patch.object(mccoy, "triage", side_effect=OSError("conn refused")):
            t = mccoy.shadow("hello", "Alex", routing="auto")
            self.assertIsNotNone(t)
            t.join(timeout=2)   # join before the mock is released; no real triage() call
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("error", rows[0])
        self.assertIn("OSError", rows[0]["error"])
        self.assertNotIn("tier", rows[0])


class TestDepthQuestion(McCoyConfigBase):
    """Depth is always asked, but the code only acts on it at `scheduler.auto_needs` (the question set stays equal to the measured payload)."""

    def _ask_returns(self, depth="deep", conf=0.61):
        return ({"answers": {"tier": {"choice": "8gb", "confidence": 0.95}, "family": {"choice": "chat", "confidence": 0.8},
                             "kind": {"choice": "chat", "confidence": 0.9}, "depth": {"choice": depth, "confidence": conf}}},
                "softserve", "home", 0.0)

    def test_the_question_set_asks_depth_with_two_criteria(self):
        self.assertEqual(set(mccoy.QUESTIONS["depth"]["criteria"]), {"quick", "deep"})
        self.assertEqual(list(mccoy.QUESTIONS), ["tier", "family", "kind", "depth"])

    def test_triage_returns_depth_and_its_confidence(self):
        with mock.patch.object(mccoy, "_ask", return_value=self._ask_returns()):
            v = mccoy.triage("analyse the last month of placements")
        self.assertEqual((v["depth"], v["confidence"]["depth"], v["decided"], v["tier"]), ("deep", 0.61, True, "8gb"))

    def test_the_verdict_carries_depth(self):
        with mock.patch.object(mccoy, "_ask", return_value=self._ask_returns("quick", 0.52)), \
                mock.patch.object(mccoy, "active_for", return_value=True), \
                mock.patch.object(mccoy, "special_route", return_value=None):
            v = mccoy.verdict(FakeQ(text="how many in a bakers dozen?", user="Alex"))
        self.assertEqual((v["depth"], v["confidence"]["depth"], v["lane"]), ("quick", 0.52, "below"))


class TestTierLine(McCoyConfigBase):

    def _ask(self, tier_conf, kind_conf=0.85):
        return ({"answers": {"tier": {"choice": "fellowship", "confidence": tier_conf},
                             "family": {"choice": "chat", "confidence": 0.9}, "kind": {"choice": "chat", "confidence": kind_conf},
                             "depth": {"choice": "quick", "confidence": 0.9}}}, "softserve", "home", 0.0)

    def test_the_tier_is_decided_at_its_own_line(self):
        with mock.patch.object(config, "MCCOY_TIER_THRESHOLD", 0.80), mock.patch.object(config, "MCCOY_THRESHOLD", 0.90):
            with mock.patch.object(mccoy, "_ask", return_value=self._ask(0.82)):
                self.assertTrue(mccoy.triage("what year is it?")["decided"], "0.82 clears the 0.80 tier line")
            with mock.patch.object(mccoy, "_ask", return_value=self._ask(0.78)):
                self.assertFalse(mccoy.triage("what year is it?")["decided"], "below the tier line = the lead")

    def test_a_measurement_can_ask_a_candidate_wording_through_the_same_call(self):
        """Passing a candidate question set is the only way to exercise alternative wording without mutating live state."""
        cand = dict(mccoy.QUESTIONS, tier=dict(mccoy.QUESTIONS["tier"], instructions="candidate"))
        with mock.patch.object(mccoy, "_ask", return_value=self._ask(0.9)) as ask:
            mccoy.triage("ping", questions=cand)
            mccoy.triage("ping")
        self.assertIs(ask.call_args_list[0].args[1], cand)
        self.assertIs(ask.call_args_list[1].args[1], mccoy.QUESTIONS)

    def test_the_live_tier_wording_is_v2(self):
        self.assertTrue(mccoy.QUESTIONS["tier"]["instructions"].startswith("You do NOT answer the request"))
        self.assertIn("general knowledge", mccoy.QUESTIONS["tier"]["criteria"]["fellowship"])
        self.assertIn("OWN documentation", mccoy.QUESTIONS["tier"]["criteria"]["8gb"])
        self.assertEqual(list(mccoy.QUESTIONS["tier"]["criteria"]), list(mccoy.TIERS))


class TestAdvisorPlacement(McCoyConfigBase):
    """McCoy may run on any device; Spock places kind `advisor` based on load."""

    def test_the_call_goes_where_spock_places_it(self):
        resp = mock.Mock()
        resp.json.return_value = {"answers": {"tier": {"choice": mccoy.TIERS[0], "confidence": 0.95}}}
        placed = [("bighonker", 50.0, "home busy"), ("mothership", 10.0, "")]
        with mock.patch("scheduler.place", return_value=placed) as place, \
             mock.patch.object(mccoy.ollama_client, "post_supervised", return_value=resp) as post, \
             mock.patch.object(mccoy.ollama_client, "track"):
            out = mccoy.triage("hello")
        place.assert_called_once_with("advisor", lend=False, only=())
        self.assertTrue(post.call_args[0][0].startswith(__import__("scheduler").CARDS["bighonker"]["url"].rstrip("/")))
        self.assertEqual((out["host"], out["decided"]), ("bighonker", True))

    def test_he_stays_loaded_only_on_a_two_model_box(self):
        # Softserve runs two models; a resident McCoy would block the other slot.
        # All other hosts run one model and must unload McCoy after triage.
        resp = mock.Mock()
        resp.json.return_value = {"answers": {"tier": {"choice": mccoy.TIERS[0], "confidence": 0.95}}}
        for host, want in (("softserve", -1), ("bighonker", None), ("mothership", None)):
            with mock.patch("scheduler.place", return_value=[(host, 50.0, "")]), \
                 mock.patch.object(mccoy.ollama_client, "post_supervised", return_value=resp) as post, \
                 mock.patch.object(mccoy.ollama_client, "track"), \
                 mock.patch.object(mccoy.config, "MCCOY_RESIDENT_HOSTS", {"softserve"}):
                mccoy.triage("hello")
            self.assertEqual(post.call_args.kwargs["json"].get("keep_alive"), want, host)

    def test_no_card_is_a_deferred_row_not_an_error_and_never_raises(self):
        with mock.patch("scheduler.place", return_value=[]), \
             mock.patch.object(mccoy.ollama_client, "post_supervised") as post:
            with self.assertRaises(mccoy.AdvisorUnplaced):
                mccoy.triage("hello")
            post.assert_not_called()
            t = mccoy.shadow("what's the weather like", "Alex", routing="auto")
            t.join(timeout=2)
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("no card", rows[0]["deferred"])
        self.assertEqual(rows[0]["request"], "what's the weather like", "kept for a replay")
        self.assertNotIn("error", rows[0])


    def test_mccoys_own_call_never_marks_his_box_live(self):
        # the advisor kind yields to LIVE: a call that marked its own box would keep McCoy off his home for its keep_alive
        resp = mock.Mock()
        resp.json.return_value = {"answers": {"tier": {"choice": mccoy.TIERS[0], "confidence": 0.95}}}
        with mock.patch("scheduler.place", return_value=[("softserve", 600.0, "home")]), \
             mock.patch.object(mccoy.ollama_client, "post_supervised", return_value=resp), \
             mock.patch.object(mccoy.ollama_client, "_mark_live") as mark:
            mccoy.triage("hello")
            mark.assert_not_called()
            with mccoy.ollama_client.track("http://x:11434", "m"):
                pass
            mark.assert_called()                    # positive control: an ordinary chat call does mark its box

    def test_a_waiting_turn_borrows_the_card_and_a_borrow_that_fails_is_unplaced(self):
        import contextlib
        import chat_window
        resp = mock.Mock()
        resp.json.return_value = {"answers": {"tier": {"choice": mccoy.TIERS[0], "confidence": 0.95}}}
        events = []

        @contextlib.contextmanager
        def lend(w, holder, wait_s):
            events.append(("lend", w, holder))
            yield w
            events.append("returned")
        with mock.patch("scheduler.place", return_value=[("bighonker", -400.0, "borrows")]) as place, \
             mock.patch.object(chat_window, "lendable", return_value="win"), \
             mock.patch.object(chat_window, "lend", lend), \
             mock.patch.object(mccoy.ollama_client, "post_supervised",
                               side_effect=lambda *a, **k: events.append("call") or resp) as post:
            mccoy.triage("hello", lend=True)
            place.assert_called_with("advisor", lend=True, only=())
            self.assertEqual(events, [("lend", "win", "mccoy"), "call", "returned"])

            def refuse(*a, **k):
                raise chat_window.CardNotLent("a run took bighonker's window first")
            post.reset_mock()
            with mock.patch.object(chat_window, "lend", refuse), self.assertRaises(mccoy.AdvisorUnplaced):
                mccoy.triage("hello", lend=True)
            post.assert_not_called()
        with mock.patch("scheduler.place", return_value=[("bighonker", 50.0, "free")]), \
             mock.patch.object(chat_window, "lendable", side_effect=AssertionError("the shadow never lends")), \
             mock.patch.object(mccoy.ollama_client, "post_supervised", return_value=resp):
            mccoy.triage("hello")


class McCoyActive(McCoyConfigBase):
    """Build step 4 (HAI-Agent-Surface-Design): an ACTIVE user's Auto turn is served on McCoy's verdict - the lead
    unless he is sure it is below it ("unsure => the 3.8"); a busy lead overflows to the 8 GB tier (B4b's rule)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(config, "MCCOY_ACTIVE_USERS", {"Alex", "ClaudeSupervisor"})
        p.start()
        self.addCleanup(p.stop)

    def q(self, text="what's a good name for a cat", user="Alex", routing="auto"):
        return FakeQ(text=text, user=user, routing=routing)

    def verdict(self, **row):
        return mock.patch.object(mccoy, "triage", return_value=dict({"host": "softserve"}, **row))

    def test_star_makes_him_active_for_every_user_but_never_a_test_user(self):
        with mock.patch.object(config, "MCCOY_ACTIVE_USERS", {"*"}):
            self.assertTrue(mccoy.active_for("Avery"))
            self.assertTrue(mccoy.active_for("Riley"))
            self.assertFalse(mccoy.active_for("test"), "a ghost test user never runs McCoy")
            self.assertFalse(mccoy.active_for(""))
        with mock.patch.object(config, "MCCOY_ACTIVE_USERS", {"Alex"}):
            self.assertFalse(mccoy.active_for("Avery"), "a named list still means only those names")
        with mock.patch.object(config, "MCCOY_ACTIVE_USERS", set()):
            self.assertFalse(mccoy.active_for("Alex"), "empty = off (the gate's own setting)")

    def test_the_lane_follows_mccoys_sureness_and_he_may_borrow_a_card(self):
        with self.verdict(tier="fellowship", decided=True) as tri:
            self.assertEqual(mccoy.route(self.q()), "below")
            tri.assert_called_once_with("what's a good name for a cat", lend=True)
        with self.verdict(tier="lead", decided=True):
            self.assertEqual(mccoy.route(self.q()), "lead")
        with self.verdict(tier="8gb", decided=False):
            self.assertEqual(mccoy.route(self.q()), "lead", "unsure => the 3.8")
        with mock.patch.object(mccoy, "triage", side_effect=mccoy.AdvisorUnplaced("no card")):
            self.assertEqual(mccoy.route(self.q()), "lead", "no McCoy is unsure, never the cheap tier")
        rows = self.log_rows()
        self.assertEqual([(r["mode"], r["lane"]) for r in rows],
                         [("active", "below"), ("active", "lead"), ("active", "lead"), ("active", "lead")])

    def test_not_his_turn_keeps_todays_route(self):
        with self.verdict(tier="8gb", decided=True) as tri:
            self.assertIsNone(mccoy.route(self.q(user="Jordan")), "not an active user")
            self.assertIsNone(mccoy.route(self.q(routing="force_27b")), "a pin wins")
            self.assertIsNone(mccoy.route(self.q(text="   ")))
            tri.assert_not_called()
        with self.verdict(tier="8gb", decided=True):
            self.assertEqual(mccoy.route(self.q(user="ClaudeSupervisor")), "below",
                             "a machine user named in the active list is opted in")
        with mock.patch.object(mccoy, "shadow") as sh:
            mccoy.observe(self.q())
            sh.assert_not_called()                 # an active user's turn is triaged once, by route(), not shadowed

    def test_the_verdict_carries_what_spock_weighs_by(self):
        conf = {"tier": 0.93, "family": 0.33, "kind": 0.66}
        with self.verdict(tier="fellowship", decided=True, kind="chat", family="chat", confidence=conf):
            v = mccoy.verdict(self.q())
        self.assertEqual((v["lane"], v["tier"], v["decided"], v["kind"], v["family"], v["confidence"]),
                         ("below", "fellowship", True, "chat", "chat", conf))
        with self.verdict(tier="8gb", decided=False, kind="job", confidence={"tier": 0.4}):
            v = mccoy.verdict(self.q())
        self.assertEqual((v["lane"], v["tier"], v["decided"]), ("lead", "8gb", False), "unsure => the lead; his tier kept for the record")
        self.assertIsNone(mccoy.verdict(self.q(routing="force_27b")), "not his turn")

    def test_the_front_door_serves_the_lead_or_overflows_with_its_caveat(self):
        statuses, dispatched = [], []
        lead = lambda: dispatched.append(1) or {"route": "mothership"}   # noqa: E731
        LEAD_BOX = {"lane": "lead", "tier": "lead", "decided": True}
        with mock.patch.object(mccoy, "verdict", return_value=LEAD_BOX), \
             mock.patch("comfy_client.render_busy", return_value=False), \
             mock.patch("scheduler.occupied_for", return_value=None):
            self.assertEqual(app._mccoy_serve(self.q(), False, statuses.append, lead),
                             ({"route": "mothership"}, "lead", LEAD_BOX))
            self.assertEqual(app._mccoy_serve(self.q(), False, statuses.append, lambda: None), (None, "lead", LEAD_BOX),
                             "a lead that did not wake goes on to the 8 GB tier")
        with mock.patch.object(mccoy, "verdict", return_value=LEAD_BOX), \
             mock.patch("comfy_client.render_busy", return_value=False), \
             mock.patch("scheduler.occupied_for", return_value={"holder": "an order"}):
            self.assertEqual(app._mccoy_serve(self.q(), False, statuses.append, lead), (None, "lead", LEAD_BOX))
        self.assertEqual(len(dispatched), 1, "a held Mothership is never dispatched to")
        self.assertTrue(any("busy" in s for s in statuses), statuses)
        unavailable = {"route": "error", "message": "Mothership could not finish", "unavailable": True,
                       "detail": "held: model fit: no fresh card-memory report"}
        broken = {"route": "error", "message": "Mothership could not finish", "unavailable": False}
        with mock.patch.object(mccoy, "verdict", return_value=LEAD_BOX), \
             mock.patch("comfy_client.render_busy", return_value=False), \
             mock.patch("scheduler.occupied_for", return_value=None):
            self.assertEqual(app._mccoy_serve(self.q(), False, statuses.append, lambda: unavailable),
                             (None, "lead", LEAD_BOX), "a lead the desk would not take overflows, never an error")
            self.assertEqual(app._mccoy_serve(self.q(), False, statuses.append, lambda: broken), (broken, "lead", LEAD_BOX),
                             "a lead that FAILED while running is reported, not silently re-run elsewhere")
        self.assertTrue(any("cannot take" in s for s in statuses), statuses)
        # B4b: another run's turn generating in Mothership's window - this turn would wait in line behind it
        dispatched.clear()
        with mock.patch.object(mccoy, "verdict", return_value=LEAD_BOX), \
             mock.patch("comfy_client.render_busy", return_value=False), \
             mock.patch("scheduler.occupied_for", return_value=None), \
             mock.patch.object(config, "CHAT_WINDOWS", True), \
             mock.patch("chat_window.serving_others", return_value=True):
            self.assertEqual(app._mccoy_serve(self.q(), False, statuses.append, lead), (None, "lead", LEAD_BOX))
        self.assertEqual(dispatched, [], "a turn that would wait behind another user's is never dispatched there")
        BELOW = {"lane": "below", "tier": "8gb", "decided": True}
        with mock.patch.object(mccoy, "verdict", return_value=BELOW):
            self.assertEqual(app._mccoy_serve(self.q(), False, statuses.append, lead), (None, "below", BELOW))
        # Quick routing is a weight, not a pin: it is triaged and weighed (may land in 'lead' or 'below'); only an exact model pick (force_27b) bypasses McCoy entirely.
        with mock.patch.object(mccoy, "verdict", return_value=LEAD_BOX):
            self.assertEqual(app._mccoy_serve(self.q(routing="quick_8b"), True, statuses.append, lead),
                             (None, "below", dict(LEAD_BOX, lane="below", mode="quick")))
        with mock.patch.object(mccoy, "verdict") as r:
            self.assertEqual(app._mccoy_serve(self.q(routing="force_27b"), False, statuses.append, lead),
                             (None, None, None))
            r.assert_not_called()                  # the exact 27B pin never asks McCoy

    def test_a_below_lead_turn_is_served_on_the_weighed_row_not_the_standing_brain(self):
        """#34 area C step 1: the row that chose the box chooses its model and thinking setting; a Fellowship pick is
        recorded and passed over until the Fellowship chat form exists; a box busy for THAT model is skipped."""
        import scheduler
        band = [
            {"host": "fellowship", "box": "rigby", "entry": {"model": "Qwen2.5-1.5B", "think": "default"}, "eligible": False,
             "quality": 1.0, "time_s": 1.9, "j": 103.0, "role": "home"},
            {"host": "softserve", "box": "softserve", "entry": {"model": "qwen3:8b", "think": "off"}, "eligible": True,
             "quality": 1.0, "time_s": 2.0, "j": 297.0, "role": "overflow"},
            {"host": "bighonker", "box": "bighonker", "entry": {"model": "qwen3:8b", "think": "off"}, "eligible": True,
             "quality": 1.0, "time_s": 1.7, "j": 208.0, "role": "overflow"}]
        verdict = {"lane": "below", "tier": "fellowship", "decided": True, "kind": "chat",
                   "confidence": {"tier": 0.93, "kind": 0.6}}
        calls = []
        def occupied(url, model=None, takes_window=False):
            return {"holder": "a render"} if url == config.FAILOVER_HOST else None
        with mock.patch.object(scheduler, "place_weighed", side_effect=lambda *a, **k: (calls.append((a, k)) or ([], band))), \
             mock.patch("scheduler.occupied_for", side_effect=occupied), \
             mock.patch("ollama_client.reachable", return_value=True), \
             mock.patch.object(app, "brain_host_busy", return_value=False), \
             mock.patch("comfy_client.render_busy", return_value=False), \
             mock.patch.object(config, "NUM_CTX", 8192):
            got = app._placed_brain(self.q(), verdict, [{"role": "user", "content": "17 times 23?"}])
        self.assertEqual((got[0], got[1], got[5]), (config.BRAIN_HOST, "qwen3:8b", "off"),
                         "softserve's row was busy; BigHonker's row serves at ITS model and thinking setting")
        (args, kw), = calls
        self.assertEqual(args[:3], ("chat", "chat-turn", "fellowship"), "the tier names the menu family")
        self.assertTrue(kw["interactive"], "an unsure kind never makes a user wait")
        with mock.patch.object(scheduler, "place_weighed", return_value=([], [])):
            self.assertIsNone(app._placed_brain(self.q(), verdict, []), "nothing placed = the caller's cascade")


class GateRunsWithNobodyActive(unittest.TestCase):
    def test_the_gate_container_does_not_inherit_the_live_switch(self):
        # run-suite.sh empties MCCOY_ACTIVE_USERS at the container boundary to prevent the gate from inheriting production's override env where McCoy serves named users.
        if os.environ.get("HAI_TEST_ISOLATED") != "1" and os.environ.get("HAI_NOTES_DIR") != "/tmp/hai-test-notes":
            self.skipTest("not inside the deploy gate's container")
        self.assertEqual(os.environ.get("MCCOY_ACTIVE_USERS"), "")


class McCoyOnDemandDoor(unittest.TestCase):
    """POST /mccoy/triage: the lab's own callers reach McCoy in the process that holds Kirk's windows. Worker token."""

    def test_token_then_the_row_or_a_deferral(self):
        from fastapi.testclient import TestClient
        client = TestClient(app.app)
        with mock.patch.object(config, "P20_WORKER_TOKEN", "tok"), \
             mock.patch.object(mccoy, "triage", return_value={"tier": "lead", "host": "softserve"}) as tri:
            r = client.post("/mccoy/triage", json={"text": "hi"})
            self.assertEqual(r.status_code, 403, "no token, no McCoy")
            tri.assert_not_called()
            r = client.post("/mccoy/triage", json={"text": "hi", "lend": True, "only": ["bighonker"]},
                            headers={"X-P20-Token": "tok"})
            self.assertEqual(r.json(), {"tier": "lead", "host": "softserve"})
            tri.assert_called_once_with("hi", lend=True, only=("bighonker",), questions=None)
            tri.side_effect = mccoy.AdvisorUnplaced("no card can take the advisor right now")
            r = client.post("/mccoy/triage", json={"text": "hi"}, headers={"X-P20-Token": "tok"})
            self.assertEqual(r.json(), {"deferred": "no card can take the advisor right now"})


class AdvisorKind(unittest.TestCase):
    """Spock's `advisor` kind: home Softserve, coexists with Plex, another card when the home cannot take it."""

    def setUp(self):
        import scheduler
        from _no_live_probes import start_no_live_probes, stop_no_live_probes
        start_no_live_probes()      # place()'s warm-start tiebreak asks each box's /api/ps: never dial a box (zzz guard)
        self.addCleanup(stop_no_live_probes)
        self.s = scheduler
        for p in (mock.patch.object(scheduler, "gpu_state", return_value=(False, "free")),
                  mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
                  mock.patch.object(scheduler, "gpu_health", return_value=(True, "ok")),
                  mock.patch.object(scheduler, "active_claim", return_value=None),
                  mock.patch.object(scheduler, "ram_free_gb", return_value=30.0),
                  mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0),
                  mock.patch.object(scheduler, "record_placement"),
                  mock.patch.object(scheduler, "_plex_gate", return_value=(False, "1 Plex transcode"))):
            p.start()
            self.addCleanup(p.stop)

    def hosts(self):
        return [h for h, _s, _w in self.s.place("advisor")]

    def test_home_softserve_first_even_while_plex_transcodes(self):
        self.assertEqual(self.hosts()[0], self.s.FAILOVER)
        self.assertNotIn(self.s.FAILOVER, [h for h, _s, _w in self.s.place("chat")],
                         "positive control: an ordinary kind still waits for Plex")

    def test_a_busy_home_sends_it_to_another_card(self):
        busy = lambda h: (h == self.s.FAILOVER, "order running" if h == self.s.FAILOVER else "free")
        with mock.patch.object(self.s, "gpu_state", side_effect=busy):
            got = self.hosts()
        self.assertNotIn(self.s.FAILOVER, got)
        self.assertTrue(got, "another card takes it")


class TestSpecialRoutes(McCoyConfigBase):
    """Each special route must short-circuit with a 'skipped' row, model never called."""

    def _assert_skipped(self, q, expected_route):
        with mock.patch.object(mccoy, "triage") as fake_triage:
            skip = mccoy.special_route(q.text, q)
            self.assertEqual(skip, expected_route, "special_route must name the route")
            # and the caller's log_skip path writes the row
            mccoy.log_skip(q.user, skip, q.chat_id)
            fake_triage.assert_not_called()
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["skipped"], expected_route)
        self.assertNotIn("tier", rows[0], "a skipped row must not carry a triage tier")

    def test_a_nursing_persona_is_not_a_special_route(self):
        # A persona is the brief and the corpus, never a route.
        self.assertIsNone(mccoy.special_route("what is a PICC line", FakeQ(text="what is a PICC line", persona="nursing")))

    def test_a_nursing_submode_or_account_is_not_a_special_route(self):
        for q in (FakeQ(text="how do you titrate insulin", mode="tutor"),
                  FakeQ(text="how do you titrate insulin", mode="educator"),
                  FakeQ(text="how do you titrate insulin", user="Riley")):
            self.assertIsNone(mccoy.special_route(q.text, q), q)

    def test_self_engineering(self):
        # admin + the SELF_ENGINEERING_INTENT predicate (app.py's own regex).
        # The regex matches "self engineer" / "self-engineer" (space or hyphen) —
        # NOT "self_engineering" (underscore). Use a text that actually matches.
        q = FakeQ(text="fix the orchestrator and add a new endpoint", user="Alex")
        with mock.patch.object(config, "ADMIN_USERS", {"Alex"}), \
             mock.patch.object(mccoy, "triage") as fake_triage:
            skip = mccoy.special_route(q.text, q)
            fake_triage.assert_not_called()
        self.assertEqual(skip, "self-engineering")

    def test_absurd(self):
        self._assert_skipped(FakeQ(text="what is the meaning of life",
                                   absurd_max_seconds=600), "absurd")

    def test_p20_create(self):
        self._assert_skipped(FakeQ(text="make a photo of the robot"), "p20-create")

    def test_p20_status(self):
        self._assert_skipped(FakeQ(text="what's the status of order abc123"), "p20-status")

    def test_external_agent(self):
        self._assert_skipped(FakeQ(text="ask the weather agent for the forecast",
                                   external_agent="weather"), "external-agent")

    def test_machine_user(self):
        self._assert_skipped(FakeQ(text="run the nightly sweep", user="ClaudeSupervisor"), "machine")

    def test_pinned_routing(self):
        self._assert_skipped(FakeQ(text="explain this code", routing="force_27b"), "pinned-routing")

    def test_auto_chat_turn_is_not_special(self):
        # a plain Auto chat turn is NOT a special route: special_route returns None and the
        # shadow() call proceeds (the triage model would be called).
        q = FakeQ(text="hows your day going")
        with mock.patch.object(mccoy, "triage") as fake_triage:
            self.assertIsNone(mccoy.special_route(q.text, q))
            # and the shadow() call site would have proceeded (we don't run the real triage here)


class TestTiersShape(unittest.TestCase):
    """The question set is pinned: the tier criteria are the advisor's own labels."""
    def test_tiers_match_advisor_labels(self):
        self.assertEqual(mccoy.TIERS, ("tool", "fellowship", "8gb", "lead"))


class TestFrontDoorWire(McCoyConfigBase):
    """The wire on the LIVE front door (app._run_query_core, the #37 lanes' common entry).

    Phase 8's acceptance contract: once per run (a resume under the same run id must not shadow again),
    identical routing with the shadow on or off, and a shadow failure that never surfaces in the turn.
    The body is patched in every test - these prove the WIRE (fires once, no routing effect), not the
    routing cascade itself, which the rest of the suite covers."""

    def test_resumed_run_does_not_shadow_a_second_time(self):
        shadows = []
        body = lambda q, s, d: {"route": "quick", "answer": "hi"}
        with mock.patch.object(app, "_run_query_core_body", body), \
             mock.patch.object(mccoy, "shadow", side_effect=lambda *a, **k: shadows.append(a)):
            app._run_query_core(app.Q(text="tell me a short joke", user="Alex", cancel_id="run-1"))
            # The restart case: _resume_one rebuilds the Q from the ledger under the SAME run id.
            resumed = app.Q(text="tell me a short joke", user="Alex", cancel_id="run-1")
            app._run_query_core(resumed)
        self.assertEqual(len(shadows), 1, "a resumed run must not call mccoy.shadow a second time")

    def test_route_is_identical_with_shadow_on_and_off(self):
        def run(mode, rid):
            with mock.patch.object(config, "MCCOY_MODE", mode), \
                 mock.patch.object(app, "_run_query_core_body",
                                   lambda q, s, d: {"route": "quick", "answer": "same"}), \
                 mock.patch.object(mccoy, "shadow") as fake:
                q = app.Q(text="tell me a short joke", user="Alex", cancel_id=rid)
                return app._run_query_core(q), q.routing, fake.call_count
        on, routing_on, calls_on = run("shadow", "route-a")
        off, routing_off, calls_off = run("off", "route-b")
        self.assertEqual(on, off, "the shadow must not change the route or the result")
        self.assertEqual((routing_on, routing_off), ("auto", "auto"), "q.routing must be untouched")
        self.assertEqual(calls_on, 1, "shadow mode must have ridden the front door")
        self.assertEqual(calls_off, 0, "off mode must never triage")

    def test_shadow_failure_never_raises_into_the_turn(self):
        with mock.patch.object(app, "_run_query_core_body",
                               lambda q, s, d: {"route": "quick", "answer": "fine"}), \
             mock.patch.object(mccoy, "shadow", side_effect=RuntimeError("boom")):
            result = app._run_query_core(app.Q(text="tell me a short joke", user="Alex", cancel_id="run-err"))
        self.assertEqual(result, {"route": "quick", "answer": "fine"}, "the turn must complete unchanged")


if __name__ == "__main__":
    unittest.main()
