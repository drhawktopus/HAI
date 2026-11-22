"""User GENERAL modes are weights for Spock's weighing, not standalone routes. An EXACT model pick (the 27B) is a pin and bypasses the weighing."""
import json
import os
import tempfile
import unittest
from unittest import mock

import config
import scheduler


def _row(box, model, role, q, low, s, j, **kw):
    r = {"box": box, "model": model, "think": "off", "role": role, "quality": q, "quality_low": low,
         "s_per_solve": s, "j_per_solve": j, "load_s": 30.0}
    r.update(kw)
    return r


_LEAD = [_row("mothership", "qwen3.8:27b", "home", 1.0, 0.969, 2.4, 900),
         _row("bighonker", "qwen3.5:9b", "overflow", 0.80, 0.68, 1.9, 300),
         _row("softserve", "qwen3.5:9b", "overflow", 0.79, 0.67, 4.3, 400),
         _row("mothership", "qwen3-coder:30b", "home", 0.78, 0.66, 1.5, 250)]
# Two rows a Hard turn must choose between on quality: A is the tier's home and cheaper, B is better.
_HARD = [_row("bighonker", "model-a", "home", 0.97, 0.90, 2.0, 100),
         _row("mothership", "model-b", "overflow", 1.0, 0.95, 4.0, 500),
         _row("softserve", "model-c", "overflow", 0.85, 0.70, 1.0, 50)]
# A Fellowship tier whose home rows are rigby and a weak tablet model; a card row is overflow.
_FEL = [_row("rigby", "qwen2.5-1.5b", "home", 0.90, 0.85, 1.9, 60, order_overhead_s=0.0),
        _row("fellowship", "qwen2.5-coder-0.5b", "home", 0.30, 0.20, 1.0, 20, order_overhead_s=0.0),
        _row("bighonker", "qwen3:8b", "overflow", 1.0, 0.94, 1.7, 200)]


def _tiers(rows):
    return {"fastest": rows, "least_energy": rows, "best_quality": rows}


TABLE = {"families": {"chat-c1": {"interactive_line_s": 15.0, "tiers": {"lead": _tiers(_LEAD), "8gb": _tiers(_HARD)}},
                      "chat-turn": {"interactive_line_s": 17.0, "tiers": {"fellowship": _tiers(_FEL)}}}}


class ModeWeights(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.table = os.path.join(self.dir, "policy.json")
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(TABLE, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        self.cold = set()

        def residency(host, model, load_s, read_tok_s=None, context_tokens=0, loaded=None):
            if (host, model) in self.cold:
                return float(load_s or 0), "cold +%.0f s load" % float(load_s or 0)
            return 0.0, "resident"
        self.p = [mock.patch.object(scheduler, "residency_cost", side_effect=residency),
                  mock.patch("ollama_client.models_resident", return_value=[]),
                  mock.patch.object(scheduler, "fellowship_load",
                                    return_value={"ahead": 0, "free_nodes": 2, "why": "test"}),
                  mock.patch.object(scheduler, "_price_of_the_hour", return_value={
                      "usd_per_kwh": 0.11, "source": "placeholder", "hour": None, "curve": None, "band": "normal"}),
                  mock.patch.object(config, "QUICK_TIME_SLACK_S", 3.0),
                  mock.patch.object(config, "POLICY_TABLE", self.table)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        scheduler._POLICY_CACHE.update(key=None, data=None)

    def order(self, family, tier, mode=None, **kw):
        band, _rej = scheduler.weigh_rows(family, tier, True, mode=mode, **kw)
        return [(c["box"], c["entry"]["model"]) for c in band], band

    # --- Quick -------------------------------------------------------------------------------------------------
    def test_the_owners_example_a_better_model_about_as_fast_is_the_quick_answer(self):
        """The 3.8 at 2.4 s against a 9B at 1.9 s: "the difference there is minuscule" - quality decides."""
        order, band = self.order("chat-c1", "lead", mode="quick")
        self.assertEqual(order[0], ("mothership", "qwen3.8:27b"), band[0]["notes"])
        self.assertIn("quick: fast, as good as the best fast row - home and energy decide (2.4 s vs fastest 1.5 s, "
                      "slack 3 s)", band[0]["notes"])
        self.assertIn("quick: fast - quality decides (1.9 s vs fastest 1.5 s, slack 3 s)",
                      next(c for c in band if c["box"] == "bighonker")["notes"])

    def test_a_cold_lead_far_behind_loses_quick_to_a_fast_good_row(self):
        self.cold.add(("mothership", "qwen3.8:27b"))
        order, band = self.order("chat-c1", "lead", mode="quick")
        # the three fast rows (0.78 / 0.79 / 0.80) are statistically equal, so the charter decides: home, cheapest energy
        self.assertEqual(order[0], ("mothership", "qwen3-coder:30b"), band[0]["notes"])
        self.assertEqual(order[-1], ("mothership", "qwen3.8:27b"), "slower than the slack: time decides, it is last")

    def test_quick_trades_some_quality_the_plain_weighing_would_not(self):
        """Without the mode the lead tier's need is the 3.8's lower bound - only it qualifies, cold or not; Quick's need
        is the tier's weakest card home row, so the 8 GB rows compete on time."""
        plain, _b = self.order("chat-c1", "lead")
        quick, band = self.order("chat-c1", "lead", mode="quick")
        self.assertEqual(plain, [("mothership", "qwen3.8:27b")])
        self.assertEqual(len(quick), 4)
        self.assertAlmostEqual(band[0]["need"], 0.66)

    def test_a_tablet_row_never_sets_quicks_floor(self):
        """The Fellowship tier's weakest home row is a 0.30 tablet model: Quick must not drop to it."""
        order, band = self.order("chat-turn", "fellowship", mode="quick")
        self.assertNotIn(("fellowship", "qwen2.5-coder-0.5b"), order)
        self.assertEqual(order[0], ("bighonker", "qwen3:8b"),
                         "about as fast as rigby and better: Quick need not be the tablet chat")
        plain, _b = self.order("chat-turn", "fellowship")
        self.assertEqual(plain[0], ("rigby", "qwen2.5-1.5b"), "without the mode the tier's home leads, unchanged")

    def test_quick_keeps_the_cheapest_thesis_among_equals(self):
        """Rigby and BigHonker's 8B both score 1.0 and are about as fast: statistically equal, so the charter decides -
        the tier's home (rigby, the cheapest) keeps the work. Quick never hands a tie to the bigger box."""
        tied = json.loads(json.dumps(TABLE))
        tied["families"]["chat-turn"]["tiers"]["fellowship"] = _tiers(
            [dict(_FEL[0], quality=1.0, quality_low=0.90), _FEL[1], _FEL[2]])
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(tied, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        order, band = self.order("chat-turn", "fellowship", mode="quick")
        self.assertEqual(order[0], ("rigby", "qwen2.5-1.5b"), band[0]["notes"])

    def test_a_peak_price_still_weighs_energy_first_under_quick(self):
        with mock.patch.object(scheduler, "_price_of_the_hour", return_value={
                "usd_per_kwh": 0.20, "source": "psp", "hour": None, "curve": None, "band": "peak"}):
            order, band = self.order("chat-c1", "lead", mode="quick")
        self.assertEqual(order[0], ("mothership", "qwen3-coder:30b"), "slow and low binds every request")

    def test_the_walk_considers_every_row_the_need_only_ranks(self):
        """place_weighed returns the band first, followed by all rows below the need; no candidates are dropped."""
        with mock.patch.object(scheduler, "place", return_value=[(h, 1.0, "") for h in
                                                                  (scheduler.LEAD_BOX, scheduler.BRAIN,
                                                                   scheduler.FAILOVER)]):
            _ranked, walk = scheduler.place_weighed("chat", "chat-c1", "lead", interactive=True)
        models = [c["entry"]["model"] for c in walk]
        self.assertEqual(models[0], "qwen3.8:27b", "the band leads")
        self.assertEqual(sorted(models), sorted(r["model"] for r in _LEAD), "every row is in the walk")
        below = [c for c in walk if c.get("below_need")]
        self.assertTrue(below)
        self.assertEqual([c["quality"] for c in below], sorted((c["quality"] for c in below), reverse=True))

    # --- the owner keeps the best; everyone else gets the cheapest row the band allows ------------------------------
    def _tied_table(self):
        tied = json.loads(json.dumps(TABLE))
        tied["families"]["chat-c1"]["tiers"]["8gb"] = _tiers([
            _row("mothership", "big", "home", 1.0, 0.95, 3.0, 900),
            _row("bighonker", "small", "overflow", 1.0, 0.95, 2.0, 100)])
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(tied, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)

    def test_cost_first_puts_the_cheaper_tied_row_before_home(self):
        self._tied_table()
        owner, _b = self.order("chat-c1", "8gb")
        user, _b = self.order("chat-c1", "8gb", cost_first=True)
        self.assertEqual(owner[0], ("mothership", "big"), "the owner keeps home-first")
        self.assertEqual(user[0], ("bighonker", "small"), "every other user: the cheaper row of the same quality")

    def test_an_open_rigby_window_pays_no_desk_order(self):
        with mock.patch("chat_window.has_window", side_effect=lambda h, m: h == "rigby"):
            band, _r = scheduler.weigh_rows("chat-turn", "fellowship", True, window_cost=True)
        rig = next(c for c in band if c["box"] == "rigby")
        self.assertEqual(rig["first_s"], 0.0)
        self.assertIn("rigby window open", rig["notes"])

    # --- Hard --------------------------------------------------------------------------------------------------
    def test_hard_weighs_quality_first_inside_the_as_good_as_the_best_band(self):
        plain, _b = self.order("chat-c1", "8gb")
        hard, band = self.order("chat-c1", "8gb", mode="hard")
        self.assertEqual(plain[0], ("bighonker", "model-a"), "the plain weighing: home, then energy")
        self.assertEqual(hard[0], ("mothership", "model-b"), "Hard: the best quality wins")
        self.assertNotIn(("softserve", "model-c"), hard, "below the best row's lower bound")
        self.assertAlmostEqual(band[0]["need"], 0.95)

    def test_the_upper_bound_is_the_menus_own_interval(self):
        """Positive control: the same Wilson interval's LOWER side reproduces the menu's quality_low for the live rows
        (35B 0.95/60 -> 0.863, the 3.8 1.0/120 -> 0.969), so the upper side is the menu's interval too."""
        def low(p, n, z=1.96):
            den = 1 + z * z / n
            return (p + z * z / (2 * n) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / den
        self.assertAlmostEqual(low(0.95, 60), 0.863, places=3)
        self.assertAlmostEqual(low(1.0, 120), 0.969, places=3)
        self.assertAlmostEqual(scheduler.wilson_high(0.95, 60), 0.983, places=3)
        self.assertEqual(scheduler.wilson_high(0.9, None), 0.9, "no n: the point, no range claimed")

    def test_hard_keeps_a_row_whose_range_overlaps_the_best(self):
        live = json.loads(json.dumps(TABLE))
        live["families"]["chat-c1"]["tiers"]["8gb"] = _tiers([
            _row("mothership", "qwen3.8:27b", "home", 1.0, 0.969, 8.0, 900, n=120),
            _row("bighonker", "qwen3.6:35b-a3b", "overflow", 0.95, 0.863, 9.0, 300, n=60),
            _row("softserve", "qwen3.5:9b", "overflow", 0.793, 0.672, 4.0, 200, n=58)])
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(live, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        hard, _b = self.order("chat-c1", "8gb", mode="hard")
        self.assertEqual(hard, [("mothership", "qwen3.8:27b"), ("bighonker", "qwen3.6:35b-a3b")])

    def test_hard_is_deep_the_line_does_not_reject(self):
        slow = json.loads(json.dumps(TABLE))
        slow["families"]["chat-c1"]["tiers"]["8gb"] = _tiers([dict(_HARD[0]), dict(_HARD[1], s_per_solve=90.0)])
        with open(self.table, "w", encoding="utf-8") as f:
            json.dump(slow, f)
        scheduler._POLICY_CACHE.update(key=None, data=None)
        hard, band = self.order("chat-c1", "8gb", mode="hard")
        self.assertEqual(hard[0], ("mothership", "model-b"), "past the line, still the best answer")
        self.assertIn("deep: past the line stays in contention", band[0]["notes"])

    # --- the verdict ---------------------------------------------------------------------------------------------
    def test_mode_verdict(self):
        lead = {"lane": "lead", "tier": "lead", "decided": True, "confidence": {}}
        q = scheduler.mode_verdict(lead, quick=True)
        self.assertEqual((q["lane"], q["tier"], q["mode"]), ("below", "lead", "quick"),
                         "Quick weighs the lead's tier, so the lead competes on time")
        self.assertNotIn("mode", lead, "the original verdict is untouched")
        h = scheduler.mode_verdict(lead, hard=True)
        self.assertEqual((h["lane"], h["tier"], h["mode"]), ("lead", "lead", "hard"))
        self.assertIs(scheduler.mode_verdict(lead), lead)
        self.assertIsNone(scheduler.mode_verdict(None, quick=True), "McCoy did not decide: nothing to weigh")
        self.assertEqual(scheduler.mode_verdict(lead, quick=True, hard=True)["mode"], "quick")

    def test_place_weighed_records_the_mode_in_the_needs(self):
        seen = {}

        def fake_place(kind, **kw):
            seen.update(kw.get("needs") or {})
            return []
        with mock.patch.object(scheduler, "place", side_effect=fake_place):
            scheduler.place_weighed("chat", "chat-c1", "8gb", mode="hard")
        self.assertEqual(seen, {"deep": True, "prio": None, "mode": "hard"})


class QuickTurnsAreTriaged(unittest.TestCase):
    """Quick turns are triaged and weighed unless explicitly bypassed (force_27b)."""

    def _serve(self, routing, effort="normal"):
        import app
        q = app.Q(text="what is the capital of France", user="ClaudeSupervisor", routing=routing, effort_mode=effort)
        verdict = {"lane": "lead", "tier": "lead", "decided": True, "kind": "chat", "confidence": {}}
        with mock.patch("mccoy.verdict", return_value=verdict) as v:
            out = app._mccoy_serve(q, routing == "quick_8b", None, lambda: {"route": "lead"})
        return out, v.called

    def test_quick_is_triaged_and_sent_to_the_weighing(self):
        (result, lane, v), called = self._serve("quick_8b")
        self.assertTrue(called)
        self.assertIsNone(result, "not dispatched straight to the lead")
        self.assertEqual((lane, v["mode"]), ("below", "quick"))

    def test_hard_takes_the_lead_only_when_the_lead_weighs_best(self):
        import app
        lead = {"host": scheduler.LEAD_BOX, "entry": {"model": config.HEAVY_MODEL}, "eligible": True}
        small = {"host": scheduler.BRAIN, "entry": {"model": "qwen3:8b"}, "eligible": True}
        hard = {"mode": "hard"}
        with mock.patch("comfy_client.render_busy", return_value=False):
            self.assertTrue(app._hard_takes_the_lead(hard, ("f", "t", True, [lead, small])))
            self.assertFalse(app._hard_takes_the_lead(hard, ("f", "t", True, [small, lead])),
                             "an as-good 8B weighed first serves it")
            self.assertTrue(app._hard_takes_the_lead(hard, ("f", "t", True, [dict(small, eligible=False), lead])),
                            "the first ELIGIBLE row decides")
            self.assertFalse(app._hard_takes_the_lead({"mode": "quick"}, ("f", "t", True, [lead])))
            self.assertFalse(app._hard_takes_the_lead(hard, None))
        with mock.patch("comfy_client.render_busy", return_value=True):
            self.assertFalse(app._hard_takes_the_lead(hard, ("f", "t", True, [lead])), "a render holds the card")

    def test_mccoys_own_door_triages_quick_and_skips_an_exact_pin(self):
        """Mocks only the model call to ensure real verdict/special_route logic handles Quick routing.
        Prevents the seam where mocked verdicts hide live Quick turns taking the 1.0 cascade."""
        import types
        import mccoy

        def q(routing):
            return types.SimpleNamespace(text="why is the sky blue", user="Avery", routing=routing,
                                         effort_mode="normal", persona="general", mode="", chat_id=None)
        self.assertIsNone(mccoy.special_route("why is the sky blue", q("quick_8b")))
        self.assertEqual(mccoy.special_route("why is the sky blue", q("force_27b")), "pinned-routing")
        with mock.patch.object(config, "MCCOY_MODE", "active"), \
             mock.patch.object(mccoy, "active_for", return_value=True), \
             mock.patch.object(mccoy, "_log"), \
             mock.patch.object(mccoy, "triage", return_value={"tier": "lead", "decided": True, "kind": "chat"}):
            v = mccoy.verdict(q("quick_8b"))
        self.assertIsNotNone(v, "McCoy answers a Quick turn")
        self.assertEqual(scheduler.mode_verdict(v, quick=True)["lane"], "below")

    def _lead_advice(self, band):
        """McCoy advises the lead; Spock's weighing returns `band`. -> (result, lane, dispatched?)."""
        import app
        q = app.Q(text="explain this design", user="Avery", routing="auto")
        verdict = {"lane": "lead", "tier": "lead", "decided": True, "kind": "chat", "confidence": {}}
        dispatched = []
        with mock.patch("mccoy.verdict", return_value=verdict), \
                mock.patch("comfy_client.render_busy", return_value=False), \
                mock.patch.object(scheduler, "occupied_for", return_value=None), \
                mock.patch.object(config, "CHAT_WINDOWS", False):
            res, lane, _v = app._mccoy_serve(q, False, None, lambda: dispatched.append(1) or {"route": "lead"},
                                             weigh=lambda v: ("chat-c1", "lead", True, band))
        return res, lane, bool(dispatched)

    def test_mccoy_advises_and_spocks_lead_row_first_takes_the_lead(self):
        lead = {"host": scheduler.LEAD_BOX, "entry": {"model": config.HEAVY_MODEL}, "eligible": True}
        self.assertEqual(self._lead_advice([lead]), ({"route": "lead"}, "lead", True))

    def test_another_row_weighed_first_serves_instead_of_the_lead(self):
        rigby = {"host": scheduler.FELLOWSHIP_HOST, "entry": {"model": "q1.5"}, "eligible": True}
        lead = {"host": scheduler.LEAD_BOX, "entry": {"model": config.HEAVY_MODEL}, "eligible": True}
        self.assertEqual(self._lead_advice([rigby, lead]), (None, "weighed", False))

    def test_an_unplaceable_lead_leaves_the_weighing_below_without_mothership(self):
        lead = {"host": scheduler.LEAD_BOX, "entry": {"model": config.HEAVY_MODEL}, "eligible": False}
        self.assertEqual(self._lead_advice([lead]), (None, "lead", False))

    def test_a_weighed_row_asks_occupied_for_only(self):
        """The matrix proof skipped BigHonker's own qwen3:8b window because brain_host_busy() knows only the fixed brain
        model. One derivation: a row occupied_for clears is servable whatever brain_host_busy says."""
        import app
        c = {"host": scheduler.BRAIN, "entry": {"model": "qwen3:8b", "think": "off"}, "eligible": True}
        with mock.patch.object(app, "brain_host_busy", return_value=True), \
                mock.patch.object(scheduler, "occupied_for", return_value=None), \
                mock.patch("ollama_client.reachable", return_value=True), \
                mock.patch("comfy_client.render_busy", return_value=False):
            row = app._servable_gpu_row(app.Q(text="x", user="Avery"), c, 1, "chat-turn", "fellowship", True)
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "qwen3:8b")

    def test_an_exact_pin_skips_triage(self):
        (result, lane, v), called = self._serve("force_27b")
        self.assertFalse(called)
        self.assertIsNone(v)


if __name__ == "__main__":
    unittest.main()
