import os
import time
import unittest

import overnight
from overnight import RunBudget, run_overnight


def _items(*ids):
    """A fresh eligible-item source over the given ids that honors done_ids exclusion."""
    pool = [{"id": i, "title": f"item {i}", "kind": "test-verifiable", "domain": "orchestrator"}
            for i in ids]
    return lambda done_ids: [it for it in pool if it["id"] not in done_ids]


class RunBudgetTests(unittest.TestCase):
    def test_clamps(self):
        b = RunBudget(max_seconds=5, no_progress_limit=0, max_items=0)
        self.assertEqual(b.max_seconds, 60)        # floor 60
        self.assertEqual(b.no_progress_limit, 1)   # floor 1
        self.assertEqual(b.max_items, 1)           # floor 1
        b2 = RunBudget(max_seconds=10 ** 9)
        self.assertEqual(b2.max_seconds, 24 * 3600)  # ceil 24h

    def test_expired_when_started_in_past(self):
        b = RunBudget(max_seconds=60)
        b.started_monotonic = time.monotonic() - 120
        self.assertTrue(b.expired())
        self.assertEqual(b.remaining_seconds, 0.0)


class RunLoopTests(unittest.TestCase):
    def test_empty_backlog_exhausted_immediately(self):
        st = run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                           item_source=_items())
        self.assertEqual(st["stop_reason"], "exhausted")
        self.assertEqual(st["items"], [])
        self.assertFalse(st["active"])

    def test_all_done_then_exhausted(self):
        st = run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done", "evidence": "ok"},
                           item_source=_items("a", "b", "c"))
        self.assertEqual(st["stop_reason"], "exhausted")
        self.assertEqual(st["done_count"], 3)
        self.assertEqual([r["id"] for r in st["items"]], ["a", "b", "c"])

    def test_fixture_done_never_ledgered_real_item_is(self):
        # Fixtures are re-armable (DONE does not call on_item_done).
        # Real items are permanent (DONE calls on_item_done).
        # Both enter done_ids to prevent re-running within the same run.
        ledgered = []
        def src(done_ids):
            avail = [{"id": "fx-1", "test_fixture": True}, {"id": "real-1"}]
            return [i for i in avail if i["id"] not in done_ids]
        st = run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                           item_source=src,
                           on_item_done=lambda iid, rid: ledgered.append(iid))
        self.assertEqual(st["done_count"], 2)
        self.assertEqual(ledgered, ["real-1"])
        fx = next(r for r in st["items"] if r["id"] == "fx-1")
        real = next(r for r in st["items"] if r["id"] == "real-1")
        self.assertTrue(fx.get("test_fixture"))
        self.assertNotIn("test_fixture", real)

    def test_render_run_summary_tags_fixture_items(self):
        state = {"run_id": "ovr-fx", "stop_reason": "exhausted", "done_count": 2, "bailed_count": 0,
                 "elapsed_seconds": 3.0, "items": [
                     {"id": "fx-item", "outcome": "done", "evidence": "green", "test_fixture": True},
                     {"id": "real-item", "outcome": "done", "evidence": "green"}]}
        lines = overnight.render_run_summary(state)
        fx_line = next(l for l in lines if "fx-item" in l)
        real_line = next(l for l in lines if "real-item" in l)
        self.assertIn("[fixture]", fx_line)
        self.assertNotIn("[fixture]", real_line)

    def test_run_start_populates_skipped_armed(self):
        # Source assertion: run_overnight calls backlog.skipped_armed at start and stores it.
        # We mock the item_source to return nothing (exhausted immediately), but skipped_armed
        # should still be populated from whatever backlog.skipped_armed returns with empty done_ids.
        st = run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                           item_source=_items())
        self.assertIn("skipped_armed", st)
        # With a fresh backlog (empty in test fixtures), skipped_armed is an empty list.
        self.assertIsInstance(st["skipped_armed"], list)

    def test_no_progress_stops_after_limit(self):
        # always bails -> stops after no_progress_limit items even though more remain
        st = run_overnight(RunBudget(60, no_progress_limit=2),
                           worker=lambda i, b: {"outcome": "bailed", "evidence": "nope"},
                           item_source=_items("a", "b", "c", "d", "e"))
        self.assertEqual(st["stop_reason"], "no_progress")
        self.assertEqual(len(st["items"]), 2)
        self.assertEqual(st["bailed_count"], 2)

    def test_done_resets_no_progress(self):
        # bail, bail, DONE (resets), bail, bail -> with limit 3 we never hit it; exhausted
        outcomes = iter(["bailed", "bailed", "done", "bailed", "bailed"])
        st = run_overnight(RunBudget(60, no_progress_limit=3),
                           worker=lambda i, b: {"outcome": next(outcomes)},
                           item_source=_items("a", "b", "c", "d", "e"))
        self.assertEqual(st["stop_reason"], "exhausted")
        self.assertEqual(len(st["items"]), 5)
        self.assertEqual(st["done_count"], 1)

    def test_worker_exception_becomes_error_and_counts_no_progress(self):
        def boom(item, budget):
            raise RuntimeError("kaboom")
        st = run_overnight(RunBudget(60, no_progress_limit=1),
                           worker=boom, item_source=_items("a", "b"))
        self.assertEqual(st["stop_reason"], "no_progress")
        self.assertEqual(st["items"][0]["outcome"], "error")
        self.assertIn("kaboom", st["items"][0]["evidence"])

    def test_invalid_outcome_coerced_to_error(self):
        st = run_overnight(RunBudget(60, no_progress_limit=1),
                           worker=lambda i, b: {"outcome": "bogus"},
                           item_source=_items("a", "b"))
        self.assertEqual(st["items"][0]["outcome"], "error")

    def test_max_items_cap(self):
        # never excludes? _items excludes by done_ids, and we add each -> but cap should trip first
        st = run_overnight(RunBudget(60, no_progress_limit=99, max_items=3),
                           worker=lambda i, b: {"outcome": "done"},
                           item_source=_items("a", "b", "c", "d", "e"))
        self.assertEqual(st["stop_reason"], "max_items")
        self.assertEqual(len(st["items"]), 3)

    def test_time_ceiling_stops(self):
        b = RunBudget(60)
        b.started_monotonic = time.monotonic() - 120   # already expired
        st = run_overnight(b, worker=lambda i, b: {"outcome": "done"},
                           item_source=_items("a", "b"))
        self.assertEqual(st["stop_reason"], "time")
        self.assertEqual(st["items"], [])

    def test_halt_check_stops(self):
        st = run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                           item_source=_items("a", "b"), halt_check=lambda: True)
        self.assertEqual(st["stop_reason"], "halt")
        self.assertEqual(st["items"], [])

    def test_halt_mid_run(self):
        calls = {"n": 0}
        def halt():
            calls["n"] += 1
            return calls["n"] > 2   # allow 2 iterations then halt
        st = run_overnight(RunBudget(60, no_progress_limit=99),
                           worker=lambda i, b: {"outcome": "done"},
                           item_source=_items("a", "b", "c", "d"), halt_check=halt)
        self.assertEqual(st["stop_reason"], "halt")
        self.assertEqual(len(st["items"]), 2)

    def test_dry_run_skips_all_and_exhausts(self):
        st = run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                           item_source=_items("a", "b"), dry_run=True)
        self.assertEqual(st["stop_reason"], "exhausted")
        self.assertTrue(all(r["outcome"] == "skipped" for r in st["items"]))
        self.assertEqual(st["done_count"], 0)

    def test_on_state_checkpoints_each_item_plus_final(self):
        saves = []
        run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                      item_source=_items("a", "b"), on_state=lambda s: saves.append(s))
        # per item: one at its start (#37: a restart knows what was in flight) and one at its end; + 1 final
        self.assertEqual(len(saves), 5)
        self.assertEqual([s["current"] and s["current"]["id"] for s in saves], ["a", None, "b", None, None])
        self.assertTrue(saves[-1]["stop_reason"])
        self.assertFalse(saves[-1]["active"])

    def test_item_source_error_ends_cleanly(self):
        def bad_source(done_ids):
            raise ValueError("backlog unreadable")
        st = run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                           item_source=bad_source)
        self.assertEqual(st["stop_reason"], "item_source_error")
        self.assertIn("backlog unreadable", st["error"])

    def test_done_ids_growth_prevents_reselection(self):
        seen = []
        src = _items("a", "b", "c")
        def tracking_source(done_ids):
            seen.append(set(done_ids))
            return src(done_ids)
        run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                      item_source=tracking_source)
        # done_ids grows monotonically as items complete
        self.assertEqual(seen[0], set())
        self.assertEqual(seen[1], {"a"})
        self.assertEqual(seen[2], {"a", "b"})


class DrivePassCapTests(unittest.TestCase):
    def test_tight_budget_one_pass_reserves_for_ladder(self):
        # 900s budget, 360/pass, reserve 300 -> usable 600 -> 1 pass (leaves room for the ladder)
        self.assertEqual(overnight.drive_pass_cap(
            900, est_pass_seconds=360, ladder_reserve_seconds=300, hard_cap=8), 1)

    def test_big_budget_hits_hard_cap(self):
        self.assertEqual(overnight.drive_pass_cap(
            10000, est_pass_seconds=360, ladder_reserve_seconds=300, hard_cap=8), 8)

    def test_always_at_least_one_even_when_no_room(self):
        self.assertEqual(overnight.drive_pass_cap(
            100, est_pass_seconds=360, ladder_reserve_seconds=300, hard_cap=8), 1)

    def test_scales_in_between(self):
        # 1860s, reserve 300 -> usable 1560 -> 1560//360 = 4
        self.assertEqual(overnight.drive_pass_cap(
            1860, est_pass_seconds=360, ladder_reserve_seconds=300, hard_cap=8), 4)

    def test_handles_zero_estimate_safely(self):
        # est clamped to >=1, never divides by zero
        self.assertGreaterEqual(overnight.drive_pass_cap(
            900, est_pass_seconds=0, ladder_reserve_seconds=300, hard_cap=8), 1)


class EventTests(unittest.TestCase):
    def test_run_emits_lifecycle_events(self):
        events = []
        overnight.set_event_sink(lambda phase, detail="": events.append(phase))
        try:
            run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                          item_source=_items("a"))
        finally:
            overnight.set_event_sink(None)
        for expected in ("run_start", "item_start", "item_end", "stop"):
            self.assertIn(expected, events)

    def test_emit_without_sink_is_safe(self):
        overnight.set_event_sink(None)
        overnight.emit("noop", "detail")   # must not raise

    def test_sink_exception_is_swallowed(self):
        def boom(phase, detail=""):
            raise RuntimeError("sink broke")
        overnight.set_event_sink(boom)
        try:
            overnight.emit("x", "y")   # must not propagate
        finally:
            overnight.set_event_sink(None)


class ShouldFireTests(unittest.TestCase):
    def _req(self, expires_at=10_000):
        return {"max_seconds": 3600, "expires_at": expires_at}

    def test_no_request(self):
        self.assertEqual(overnight.should_fire(None, now=1, busy=False, energy_ok=True, has_items=True),
                         (False, "no_request"))

    def test_all_conditions_met_fires(self):
        fire, reason = overnight.should_fire(self._req(), now=1, busy=False, energy_ok=True, has_items=True)
        self.assertTrue(fire)
        self.assertEqual(reason, "fire")

    def test_expired_takes_priority(self):
        # expired even though everything else is go
        fire, reason = overnight.should_fire(self._req(expires_at=100), now=200,
                                             busy=False, energy_ok=True, has_items=True)
        self.assertFalse(fire)
        self.assertEqual(reason, "expired")

    def test_busy_blocks(self):
        self.assertEqual(overnight.should_fire(self._req(), now=1, busy=True, energy_ok=True, has_items=True),
                         (False, "busy"))

    def test_no_items_blocks(self):
        self.assertEqual(overnight.should_fire(self._req(), now=1, busy=False, energy_ok=True, has_items=False),
                         (False, "no_eligible_items"))

    def test_expensive_waits(self):
        self.assertEqual(overnight.should_fire(self._req(), now=1, busy=False, energy_ok=False, has_items=True),
                         (False, "waiting_for_cheap"))

    # --- prefer_cheapest mode (opt-in "wait for the trough") ---
    def _pref_req(self, expires_at=10_000):
        return {"max_seconds": 3600, "expires_at": expires_at, "prefer_cheapest": True}

    def test_not_before_holds_then_fires(self):
        req = {"max_seconds": 3600, "expires_at": 10_000, "not_before": 500}
        # before the start time -> hold
        self.assertEqual(overnight.should_fire(req, now=100, busy=False, energy_ok=True, has_items=True),
                         (False, "await_time"))
        # at/after the start time -> fire (other conditions met)
        self.assertEqual(overnight.should_fire(req, now=600, busy=False, energy_ok=True, has_items=True),
                         (True, "fire"))

    def test_not_before_absent_ignored(self):
        self.assertEqual(overnight.should_fire(self._req(), now=1, busy=False, energy_ok=True, has_items=True),
                         (True, "fire"))

    def test_prefer_cheapest_not_in_window_defers(self):
        self.assertEqual(
            overnight.should_fire(self._pref_req(), now=1, busy=False, energy_ok=True,
                                  has_items=True, in_cheapest_window=False),
            (False, "await_cheapest"))

    def test_prefer_cheapest_in_window_fires(self):
        self.assertEqual(
            overnight.should_fire(self._pref_req(), now=1, busy=False, energy_ok=True,
                                  has_items=True, in_cheapest_window=True),
            (True, "fire"))

    def test_prefer_cheapest_absent_ignores_window(self):
        # Not opted in -> in_cheapest_window is ignored (both False and None fire normally).
        for icw in (False, None):
            self.assertEqual(
                overnight.should_fire(self._req(), now=1, busy=False, energy_ok=True,
                                      has_items=True, in_cheapest_window=icw),
                (True, "fire"))

    def test_prefer_cheapest_none_window_fires(self):
        # Opted in but the window couldn't be computed (None) -> fire as before (fail-open).
        self.assertEqual(
            overnight.should_fire(self._pref_req(), now=1, busy=False, energy_ok=True,
                                  has_items=True, in_cheapest_window=None),
            (True, "fire"))

    def test_prefer_cheapest_precedence(self):
        # expired / busy / no-items / expensive all still win over the cheapest-window hold.
        r = self._pref_req(expires_at=100)
        self.assertEqual(overnight.should_fire(r, now=200, busy=False, energy_ok=True,
                         has_items=True, in_cheapest_window=False)[1], "expired")
        r = self._pref_req()
        self.assertEqual(overnight.should_fire(r, now=1, busy=True, energy_ok=True,
                         has_items=True, in_cheapest_window=False)[1], "busy")
        self.assertEqual(overnight.should_fire(r, now=1, busy=False, energy_ok=True,
                         has_items=False, in_cheapest_window=False)[1], "no_eligible_items")
        self.assertEqual(overnight.should_fire(r, now=1, busy=False, energy_ok=False,
                         has_items=True, in_cheapest_window=False)[1], "waiting_for_cheap")


class IcwHelperTests(unittest.TestCase):
    """overnight._icw: current-hour-in-cheapest-window. Returns a REAL False out-of-window (the bug the
    hand-fix closed: the caller's inline version collapsed False->None so prefer_cheapest never deferred).
    Curve shape matches energy.price_curve: {current, current_hour(1-24), today[24], tomorrow[24]};
    cheapest_window scans FORWARD from current_hour, so a trough must sit AHEAD to be out-of-window."""

    def _curve(self, current_hour, trough=(5, 6, 7)):
        today = [0.10] * 24
        for h in trough:
            today[h - 1] = 0.02
        cur = today[current_hour - 1] if 1 <= current_hour <= 24 else None
        return {"current": cur, "current_hour": current_hour, "today": today, "tomorrow": [0.10] * 24}

    def test_in_window_true(self):
        # current hour 6 is inside the cheap trough -> the cheapest 1h window from now IS hour 6.
        self.assertIs(overnight._icw(self._curve(6), 1), True)

    def test_out_of_window_false_not_none(self):
        # current hour 1, trough ahead at 5-7 -> cheapest window is hour 5, so NOW (1) is out of it.
        self.assertIs(overnight._icw(self._curve(1), 1), False)

    def test_no_curve_or_hour_is_none(self):
        self.assertIsNone(overnight._icw(None, 1))
        self.assertIsNone(overnight._icw({"today": [], "tomorrow": [], "current_hour": None}, 1))


class ResumeAndSummaryTests(unittest.TestCase):
    def test_resume_seeds_done_ids_and_counts(self):
        seed = [{"id": "a", "outcome": "done"}, {"id": "b", "outcome": "bailed"}]
        st = run_overnight(RunBudget(60), worker=lambda i, b: {"outcome": "done"},
                           item_source=_items("a", "b", "c"),
                           resume_done_ids={"a", "b"}, seed_items=seed, run_id="ovr-keep")
        self.assertEqual(st["run_id"], "ovr-keep")
        self.assertTrue(st["resumed"])
        # a,b already attempted -> only c runs now; totals include the seeded done/bailed
        self.assertEqual([r["id"] for r in st["items"]], ["a", "b", "c"])
        self.assertEqual(st["done_count"], 2)     # seeded 'a' + new 'c'
        self.assertEqual(st["bailed_count"], 1)   # seeded 'b'
        self.assertEqual(st["stop_reason"], "exhausted")

    def test_done_ids_from_state(self):
        state = {"items": [{"id": "x"}, {"id": "y"}, {"no_id": 1}]}
        self.assertEqual(overnight.done_ids_from_state(state), {"x", "y"})

    def test_is_incomplete(self):
        self.assertTrue(overnight.is_incomplete({"active": True, "stop_reason": None}))
        self.assertTrue(overnight.is_incomplete({"active": False, "stop_reason": None}))
        self.assertFalse(overnight.is_incomplete({"active": False, "stop_reason": "exhausted"}))
        self.assertFalse(overnight.is_incomplete("not a dict"))

    def test_render_run_summary_facts(self):
        state = {"run_id": "ovr-1", "stop_reason": "exhausted", "done_count": 1, "bailed_count": 1,
                 "elapsed_seconds": 12.3, "items": [
                     {"id": "a", "outcome": "done", "evidence": "green",
                      "adversarial": {"verdict": "fixed"}},
                     {"id": "b", "outcome": "bailed", "evidence": "verifier never passed"}]}
        md = "\n".join(overnight.render_run_summary(state))
        self.assertIn("ovr-1", md)
        self.assertIn("1 done, 1 bailed", md)
        self.assertIn("adv:fixed", md)
        self.assertIn("**bailed**", md)

    def test_render_run_summary_shows_risk_tier(self):
        # An item carrying adversarial.risk.tier shows "risk:<tier>"; one without shows no "risk:" token.
        state = {"run_id": "ovr-risk", "stop_reason": "exhausted", "done_count": 2, "bailed_count": 0,
                 "elapsed_seconds": 5.0, "items": [
                     {"id": "tiered", "outcome": "done", "evidence": "green",
                      "adversarial": {"verdict": "pass", "risk": {"tier": "low", "reasons": ["tiny"]}}},
                     {"id": "untiered", "outcome": "done", "evidence": "green",
                      "adversarial": {"verdict": "pass"}}]}
        lines = overnight.render_run_summary(state)
        tiered = next(l for l in lines if "tiered" in l and "untiered" not in l)
        untiered = next(l for l in lines if "untiered" in l)
        self.assertIn("risk:low", tiered)
        self.assertNotIn("risk:", untiered)

    def test_render_run_summary_shows_escalation_token(self):
        # An item with adversarial.escalated shows "esc:<from>->@<to>"; one without shows no "esc:".
        state = {"run_id": "ovr-esc", "stop_reason": "exhausted", "done_count": 2, "bailed_count": 0,
                 "elapsed_seconds": 3.0, "items": [
                     {"id": "escalated_item", "outcome": "done", "evidence": "green",
                      "adversarial": {"verdict": "pass",
                                     "escalated": {"from": "floor", "to": "trio", "trigger": "critical"}}},
                     {"id": "no_escalation", "outcome": "done", "evidence": "green",
                      "adversarial": {"verdict": "pass"}}]}
        lines = overnight.render_run_summary(state)
        esc_line = next(l for l in lines if "escalated_item" in l)
        no_esc_line = next(l for l in lines if "no_escalation" in l)
        self.assertIn("esc:floor->", esc_line)
        self.assertIn("@trio", esc_line)
        self.assertNotIn("esc:", no_esc_line)

    def test_render_run_summary_shows_nudge_token(self):
        # Items with nudge counts render with "nudged:..." token; clean drives do not.
        state = {"run_id": "ovr-nudge", "stop_reason": "exhausted", "done_count": 3, "bailed_count": 0,
                 "elapsed_seconds": 9.0, "items": [
                     {"id": "itemsoft", "outcome": "done", "evidence": "green",
                      "timings": {"drive_tele": {"turns": 40, "nudges_soft": 1}}},
                     {"id": "itemboth", "outcome": "done", "evidence": "green",
                      "timings": {"drive_tele": {"turns": 48, "nudges_soft": 1, "nudges_hard": 1}}},
                     {"id": "itemclean", "outcome": "done", "evidence": "green",
                      "timings": {"drive_tele": {"turns": 12}}}]}
        lines = overnight.render_run_summary(state)
        soft_line = next(l for l in lines if "itemsoft" in l)
        both_line = next(l for l in lines if "itemboth" in l)
        clean_line = next(l for l in lines if "itemclean" in l)
        self.assertIn("nudged:soft", soft_line)
        self.assertIn("nudged:soft+hard", both_line)
        self.assertNotIn("nudged:", clean_line)

    def test_render_run_summary_shows_skipped_armed(self):
        # When state carries a non-empty skipped_armed, the summary shows the "skipped (already done)" line.
        state = {"run_id": "ovr-skip", "stop_reason": "exhausted", "done_count": 1, "bailed_count": 0,
                 "elapsed_seconds": 5.0, "items": [
                     {"id": "a", "outcome": "done", "evidence": "green"}],
                 "skipped_armed": [{"id": "bl-x", "why": "in done-ledger"},
                                   {"id": "bl-y", "why": "in done-ledger"}]}
        lines = overnight.render_run_summary(state)
        combined = "\n".join(lines)
        self.assertIn("Skipped (already done):", combined)
        self.assertIn("bl-x", combined)
        self.assertIn("bl-y", combined)

    def test_render_run_summary_omits_skipped_when_empty(self):
        # When skipped_armed is absent or empty, the summary has no "skipped" line.
        state = {"run_id": "ovr-noskip", "stop_reason": "exhausted", "done_count": 1, "bailed_count": 0,
                 "elapsed_seconds": 2.0, "items": [
                     {"id": "a", "outcome": "done", "evidence": "green"}],
                 "skipped_armed": []}
        combined = "\n".join(overnight.render_run_summary(state))
        self.assertNotIn("Skipped", combined)

    def test_render_run_summary_omits_skipped_when_missing(self):
        # State without skipped_armed key at all should not crash or show the line.
        state = {"run_id": "ovr-legacy", "stop_reason": "time", "done_count": 0, "bailed_count": 1,
                 "elapsed_seconds": 60.0, "items": [
                     {"id": "b", "outcome": "bailed", "evidence": "stuck"}]}
        combined = "\n".join(overnight.render_run_summary(state))
        self.assertNotIn("Skipped", combined)

    def test_render_run_summary_shows_scope_note(self):
        # An item with adversarial.scope_note (non-empty list) shows "scope-note:<syms>";
        # an item without the key renders with no "scope-note" token.
        state = {"run_id": "ovr-sn", "stop_reason": "exhausted", "done_count": 2, "bailed_count": 0,
                 "elapsed_seconds": 4.0, "items": [
                     {"id": "sn_item", "outcome": "done", "evidence": "green",
                      "adversarial": {"verdict": "pass",
                                     "scope_note": ["drive_tele", "coverage_gate"]}},
                     {"id": "no_sn", "outcome": "done", "evidence": "green",
                      "adversarial": {"verdict": "pass"}}]}
        lines = overnight.render_run_summary(state)
        sn_line = next(l for l in lines if "sn_item" in l)
        no_sn_line = next(l for l in lines if "no_sn" in l)
        self.assertIn("scope-note:drive_tele,coverage_gate", sn_line)
        self.assertNotIn("scope-note:", no_sn_line)


def _dep_items(spec):
    """Eligible source over (id, [deps], parent_id?) tuples, honoring done_ids exclusion."""
    pool = []
    for s in spec:
        iid, deps = s[0], s[1]
        parent = s[2] if len(s) > 2 else None
        pool.append({"id": iid, "title": f"item {iid}", "kind": "test-verifiable",
                     "domain": "orchestrator", "depends_on": list(deps), "parent_id": parent})
    return lambda done_ids: [it for it in pool if it["id"] not in done_ids]


class NextRunnableTests(unittest.TestCase):
    def test_picks_first_with_satisfied_deps(self):
        eligible = [{"id": "l2", "depends_on": ["l1"]}, {"id": "l1", "depends_on": []}]
        item, reason = overnight.next_runnable(eligible, completed_ids=set(), attempted_ids=set())
        self.assertEqual((item["id"], reason), ("l1", "ok"))

    def test_dep_satisfied_when_completed(self):
        eligible = [{"id": "l2", "depends_on": ["l1"]}]
        item, reason = overnight.next_runnable(eligible, completed_ids={"l1"}, attempted_ids={"l1"})
        self.assertEqual((item["id"], reason), ("l2", "ok"))

    def test_blocked_when_dep_bailed(self):
        # l1 attempted but NOT completed (bailed) -> l2 must not build on a failed foundation
        eligible = [{"id": "l2", "depends_on": ["l1"]}]
        item, reason = overnight.next_runnable(eligible, completed_ids=set(), attempted_ids={"l1"})
        self.assertEqual((item, reason), (None, "blocked"))

    def test_external_dep_treated_satisfied(self):
        eligible = [{"id": "l2", "depends_on": ["bl-external"]}]
        item, reason = overnight.next_runnable(eligible, completed_ids=set(), attempted_ids=set())
        self.assertEqual((item["id"], reason), ("l2", "ok"))

    def test_empty(self):
        self.assertEqual(overnight.next_runnable([], set(), set()), (None, "empty"))


class DependencyOrderingTests(unittest.TestCase):
    def test_runs_in_dependency_order(self):
        order = []
        src = _dep_items([("l3", ["l2"]), ("l2", ["l1"]), ("l1", [])])  # backlog order reversed
        st = run_overnight(RunBudget(60),
                           worker=lambda i, b: (order.append(i["id"]) or {"outcome": "done"}),
                           item_source=src)
        self.assertEqual(order, ["l1", "l2", "l3"])
        self.assertEqual(st["stop_reason"], "exhausted")
        self.assertEqual(st["done_count"], 3)

    def test_integration_leaf_runs_last(self):
        order = []
        src = _dep_items([("lI", ["l1", "l2"]), ("l1", []), ("l2", ["l1"])])
        run_overnight(RunBudget(60),
                      worker=lambda i, b: (order.append(i["id"]) or {"outcome": "done"}),
                      item_source=src)
        self.assertEqual(order[-1], "lI")
        self.assertLess(order.index("l1"), order.index("l2"))

    def test_bailed_dependency_blocks_dependents(self):
        def worker(item, b):
            return {"outcome": "bailed"} if item["id"] == "l1" else {"outcome": "done"}
        src = _dep_items([("l1", []), ("l2", ["l1"])])
        st = run_overnight(RunBudget(60, no_progress_limit=99), worker=worker, item_source=src)
        ran = [r["id"] for r in st["items"]]
        self.assertIn("l1", ran)
        self.assertNotIn("l2", ran)             # never ran a leaf on a bailed foundation
        self.assertEqual(st["stop_reason"], "blocked")
        self.assertEqual(st["blocked_ids"], ["l2"])

    def test_independent_items_still_all_run(self):
        seen = []
        src = _dep_items([("a", []), ("b", []), ("c", [])])
        st = run_overnight(RunBudget(60),
                           worker=lambda i, b: (seen.append(i["id"]) or {"outcome": "done"}),
                           item_source=src)
        self.assertEqual(sorted(seen), ["a", "b", "c"])
        self.assertEqual(st["stop_reason"], "exhausted")

    def test_resume_completed_deps_unblock(self):
        # a resumed run where l1 already completed (seeded) must let l2 run
        order = []
        src = _dep_items([("l2", ["l1"])])      # l1 not in this source -> only l2 pending
        st = run_overnight(RunBudget(60),
                           worker=lambda i, b: (order.append(i["id"]) or {"outcome": "done"}),
                           item_source=src, resume_done_ids={"l1"},
                           seed_items=[{"id": "l1", "outcome": "done"}])
        self.assertEqual(order, ["l2"])


class CommissionRollupTests(unittest.TestCase):
    def test_rollup_groups_by_parent(self):
        items = [{"id": "e-l1", "parent_id": "e", "outcome": "done"},
                 {"id": "e-l2", "parent_id": "e", "outcome": "bailed"},
                 {"id": "solo", "parent_id": None, "outcome": "done"}]
        lines = overnight.commission_rollup(items)
        self.assertEqual(len(lines), 1)                  # solo (no parent) excluded
        self.assertIn("`e`: 1/2 leaves done", lines[0])
        self.assertIn("incomplete", lines[0])

    def test_rollup_complete_commission_has_no_incomplete(self):
        items = [{"id": "e-l1", "parent_id": "e", "outcome": "done"},
                 {"id": "e-l2", "parent_id": "e", "outcome": "done"}]
        lines = overnight.commission_rollup(items)
        self.assertIn("2/2 leaves done", lines[0])
        self.assertNotIn("incomplete", lines[0])

    def test_summary_includes_rollup(self):
        state = {"run_id": "ovr-x", "stop_reason": "exhausted", "done_count": 2, "bailed_count": 0,
                 "items": [{"id": "e-l1", "parent_id": "e", "outcome": "done"},
                           {"id": "e-l2", "parent_id": "e", "outcome": "done"}]}
        md = "\n".join(overnight.render_run_summary(state))
        self.assertIn("**Commissions:**", md)
        self.assertIn("commission `e`", md)


class ScoreRunTests(unittest.TestCase):
    """#5: coding-correctness and review-convergence are scored on SEPARATE axes."""

    def test_separates_coding_from_review(self):
        items = [
            {"id": "a", "outcome": "done", "adversarial": {"verdict": "bail_human"}},   # code OK, review flagged
            {"id": "b", "outcome": "done", "adversarial": {"verdict": "fixed"}},         # code OK, review converged
            {"id": "c", "outcome": "done", "adversarial": {"verdict": "pass"}},          # code OK, review clean
            {"id": "d", "outcome": "bailed"},                                            # code failed, no ladder
            {"id": "e", "outcome": "done"},                                              # code OK, ladder didn't run
            {"id": "f", "outcome": "skipped"},                                           # dry-run: excluded
        ]
        s = overnight.score_run(items)
        self.assertEqual(s["items_executed"], 5)                     # skipped excluded from both axes
        self.assertEqual(s["coding"], {"correct": 4, "incorrect": 1, "hollow": 0})
        self.assertEqual(s["review"]["converged"], 2)                # fixed + pass
        self.assertEqual(s["review"]["flagged_for_human"], 1)        # bail_human (green but critics unhappy)
        self.assertEqual(s["review"]["not_reviewed"], 2)             # d (bailed) + e (done, no ladder)

    def test_benchmark_case_code_correct_review_flagged(self):
        # The unify benchmark: verifier green, adversarial bailed to human. Must read as CODE CORRECT,
        # review FLAGGED -- NOT a coding failure.
        s = overnight.score_run([{"id": "bench", "outcome": "done",
                                  "adversarial": {"verdict": "bail_human"}}])
        self.assertEqual(s["coding"]["correct"], 1)
        self.assertEqual(s["coding"]["incorrect"], 0)
        self.assertEqual(s["review"]["flagged_for_human"], 1)
        self.assertEqual(s["review"]["converged"], 0)

    def test_reverted_and_skipped_and_empty(self):
        items = [
            {"id": "a", "outcome": "bailed", "adversarial": {"verdict": "reverify_red"}},  # fix broke verifier
            {"id": "b", "outcome": "done", "adversarial": {"verdict": "skipped"}},          # ladder had no time
        ]
        s = overnight.score_run(items)
        self.assertEqual(s["review"]["reverted"], 1)
        self.assertEqual(s["review"]["skipped"], 1)
        self.assertEqual(s["coding"], {"correct": 1, "incorrect": 1, "hollow": 0})
        empty = overnight.score_run([])
        self.assertEqual(empty["items_executed"], 0)
        self.assertEqual(empty["coding"], {"correct": 0, "incorrect": 0, "hollow": 0})


class OnItemDoneHookTests(unittest.TestCase):
    """run_overnight(on_item_done=...): the persistent done-ledger hook fires exactly on DONE."""

    def test_fires_for_done_with_run_id(self):
        recorded = []
        run_overnight(RunBudget(max_seconds=600), lambda item, b: {"outcome": "done", "evidence": "ok"},
                      item_source=_items("a", "b"), run_id="ovr-t",
                      on_item_done=lambda i, r: recorded.append((i, r)))
        self.assertEqual(recorded, [("a", "ovr-t"), ("b", "ovr-t")])

    def test_does_not_fire_for_bailed_or_dry_run(self):
        recorded = []
        run_overnight(RunBudget(max_seconds=600), lambda item, b: {"outcome": "bailed", "evidence": "x"},
                      item_source=_items("a"), on_item_done=lambda i, r: recorded.append(i))
        run_overnight(RunBudget(max_seconds=600), lambda item, b: {"outcome": "done"},
                      item_source=_items("b"), dry_run=True,
                      on_item_done=lambda i, r: recorded.append(i))
        self.assertEqual(recorded, [])

    def test_hook_failure_never_breaks_the_run(self):
        def boom(i, r):
            raise RuntimeError("ledger unavailable")
        state = run_overnight(RunBudget(max_seconds=600), lambda item, b: {"outcome": "done"},
                              item_source=_items("a"), on_item_done=boom)
        self.assertEqual(state["done_count"], 1)
        self.assertEqual(state["items"][0]["outcome"], "done")


class AdjudicationRequestTests(unittest.TestCase):
    """build_adjudication_request: bailed items -> a self-contained request for the outside adjudicator."""

    def _state(self, **adv):
        return {"run_id": "ovr-x", "ended_at": "t1", "stop_reason": "exhausted", "active": False,
                "items": [{"id": "it-1", "title": "t", "workspace": "sample", "outcome": "done",
                           "adversarial": adv}]}

    def test_bail_human_item_is_requested(self):
        st = self._state(verdict="bail_human", reason="stalled_no_progress",
                         findings_outstanding=[{"severity": "critical", "explanation": "e"}],
                         restored_to_best={"best_round": 0})
        req = overnight.build_adjudication_request(st)
        self.assertEqual(req["run_id"], "ovr-x")
        self.assertEqual(len(req["items"]), 1)
        item = req["items"][0]
        self.assertEqual(item["workspace"], "sample")
        self.assertEqual(item["verdict"], "bail_human")
        self.assertEqual(item["findings_outstanding"][0]["severity"], "critical")
        self.assertIn("workspace_root", req)
        self.assertIn("ask", req)

    def test_tampering_flag_is_requested_even_when_fixed(self):
        st = self._state(verdict="fixed", test_tampering=[{"file": "tests/test_x.py"}])
        req = overnight.build_adjudication_request(st)
        self.assertEqual(len(req["items"]), 1)

    def test_clean_run_returns_none(self):
        self.assertIsNone(overnight.build_adjudication_request(self._state(verdict="fixed")))
        self.assertIsNone(overnight.build_adjudication_request(self._state(verdict="pass")))

    def test_incomplete_or_bad_state_returns_none(self):
        st = self._state(verdict="bail_human")
        st["active"] = True                       # still mid-flight -> not adjudicable yet
        self.assertIsNone(overnight.build_adjudication_request(st))
        self.assertIsNone(overnight.build_adjudication_request(None))

    def test_fmt_timings_with_data(self):
        t = {"drive": 300.0, "verify": 60.0, "critique": 120.0, "fix": 90.0}
        line = overnight._fmt_timings(t)
        self.assertIn("drive", line)
        self.assertIn("critique", line)
        self.assertIn("fixes", line)

    def test_fmt_timings_empty(self):
        self.assertEqual(overnight._fmt_timings({}), "")
        self.assertEqual(overnight._fmt_timings(None), "")

    def test_render_includes_timing_line_when_present(self):
        state = {"run_id": "ovr-t", "stop_reason": "exhausted", "done_count": 1, "bailed_count": 0,
                 "items": [{"id": "a", "outcome": "done", "evidence": "ok",
                            "timings": {"drive": 600.0, "verify": 30.0,
                                        "critique": 180.0, "fix": 120.0}}]}
        md = "\n".join(overnight.render_run_summary(state))
        self.assertIn("drive", md)
        self.assertIn("critique", md)

    def test_render_omits_timing_line_when_absent(self):
        state = {"run_id": "ovr-t", "stop_reason": "exhausted", "done_count": 1, "bailed_count": 0,
                 "items": [{"id": "a", "outcome": "done", "evidence": "ok"}]}
        lines = overnight.render_run_summary(state)
        # The per-item line should NOT contain the timing bullet (· drive/critique/fixes)
        item_lines = [l for l in lines if l.startswith("- `a`")]
        self.assertEqual(len(item_lines), 1)
        item_line = item_lines[0]
        # Timing summary would have " · drive" / " · critique" — none should appear
        self.assertNotIn(" · drive", item_line)
        self.assertNotIn(" · critique", item_line)
        self.assertNotIn(" · fixes", item_line)


class WorkspaceStaleTests(unittest.TestCase):
    """D0 staleness guard pure core: _source_manifest (dir -> hash map) + workspace_stale (decision).
    The production wiring (app._make_stale_guard) hashes live /app locally and fetches the workspace
    manifest + ahead flag from the runner; these pure pieces are where the logic lives."""

    def test_source_manifest_top_level_py_only(self):
        import hashlib
        import os
        import tempfile
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "a.py"), "w") as fh:
            fh.write("x = 1")
        with open(os.path.join(d, "keep.bak"), "w") as fh:      # .bak backups excluded
            fh.write("old")
        with open(os.path.join(d, "notes.txt"), "w") as fh:     # non-.py excluded
            fh.write("nope")
        os.makedirs(os.path.join(d, "tests"))
        with open(os.path.join(d, "tests", "t.py"), "w") as fh:  # nested tests/ excluded from manifest
            fh.write("y = 2")
        manifest = overnight._source_manifest(d)
        self.assertEqual(set(manifest), {"a.py"})
        self.assertEqual(manifest["a.py"], hashlib.sha256(b"x = 1").hexdigest())

    def test_source_manifest_missing_dir_is_empty(self):
        self.assertEqual(overnight._source_manifest("/no/such/dir/xyz"), {})

    def test_identical_manifests_not_stale(self):
        self.assertEqual(overnight.workspace_stale({"a.py": "h1"}, {"a.py": "h1"}, False), (False, []))

    def test_behind_when_differs_and_not_ahead(self):
        # live moved (a.py differs) and the workspace has no undeployed commits -> STALE, name the file.
        self.assertEqual(
            overnight.workspace_stale({"a.py": "LIVE"}, {"a.py": "OLD"}, False), (True, ["a.py"]))

    def test_ahead_workspace_allowed_despite_diffs(self):
        # undeployed committed work (ahead=True) is allowed even though bytes differ.
        behind, files = overnight.workspace_stale({"a.py": "LIVE"}, {"a.py": "NEW-WORK"}, True)
        self.assertFalse(behind)
        self.assertEqual(files, ["a.py"])

    def test_missing_file_on_one_side_counts_as_differing(self):
        behind, files = overnight.workspace_stale({"a.py": "h", "b.py": "h2"}, {"a.py": "h"}, False)
        self.assertTrue(behind)
        self.assertEqual(files, ["b.py"])


class ParseTestCountTests(unittest.TestCase):
    """Pure tests for overnight.parse_test_count."""

    def test_extracts_count_from_standard_summary(self):
        assert overnight.parse_test_count("rc=0 | Ran 985 tests in 88.34s ... OK") == 985

    def test_extracts_single_digit(self):
        assert overnight.parse_test_count("Ran 1 test in 0.01s") == 1

    def test_returns_none_when_no_phrase(self):
        assert overnight.parse_test_count("All good") is None

    def test_returns_none_on_empty_string(self):
        assert overnight.parse_test_count("") is None

    def test_returns_none_on_none(self):
        assert overnight.parse_test_count(None) is None


class HandFinishedWiringTests(unittest.TestCase):
    """Verify the app.py segments involved in test-count parsing and tele-trail driving."""

    @classmethod
    def setUpClass(cls):
        cls.APP = open("app.py", encoding="utf-8").read()

    def test_ov_verify_attaches_parsed_test_count(self):
        # bl-verify-testcount: _ov_verify parses the runner output and attaches "tests".
        body = self.APP.split("def _ov_verify(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("parse_test_count", body)
        self.assertIn('out["tests"] = count', body)

    def test_ov_drive_returns_telemetry(self):
        # bl-drive-tele-trail: _ov_drive collects via Q.drive_telemetry and returns {"telemetry": ...}.
        body = self.APP.split("def _ov_drive(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("drive_telemetry={}", body)
        self.assertIn('return {"telemetry": tele}', body)

    def test_dispatch_threads_telemetry_into_chat(self):
        # Both mothership chat passes hand chat() a fresh collector and merge it into the aggregate.
        self.assertIn("_merge_drive_tele(_drive_tele_agg, _pt)", self.APP)
        self.assertEqual(self.APP.count("telemetry=_pt"), 2)

    def test_merge_drive_tele_sums_passes_and_keeps_nudges(self):
        import app
        agg = {}
        app._merge_drive_tele(agg, {"turns": 50, "infer_s": 1.0, "tool_s": 2.0,
                                    "tools": {"coding_read": 3}, "nudges_soft": 1, "nudges_hard": 1})
        app._merge_drive_tele(agg, {"turns": 30, "infer_s": 0.5, "tool_s": 0.5,
                                    "tools": {"coding_read": 1}, "nudges_soft": 1})
        self.assertEqual(agg["turns"], 80)
        self.assertEqual(agg["nudges_soft"], 2)
        self.assertEqual(agg["nudges_hard"], 1)
        self.assertEqual(agg["tools"]["coding_read"], 4)

    def test_suite_mutations_detects_non_test_changes(self):
        # Treat non-test file changes/additions/deletions as mutations; ignore tests/ (handled elsewhere)
        pre = {"app.py": "aaa", "overnight.py": "bbb", "tests/test_x.py": "ttt", "gone.py": "ggg"}
        post = {"app.py": "MUTATED", "overnight.py": "bbb", "tests/test_x.py": "CHANGED",
                "new.py": "nnn"}
        self.assertEqual(overnight.suite_mutations(pre, post), ["app.py", "gone.py", "new.py"])

    def test_suite_mutations_clean_and_empty(self):
        same = {"app.py": "aaa", "tests/test_x.py": "ttt"}
        self.assertEqual(overnight.suite_mutations(same, dict(same)), [])
        self.assertEqual(overnight.suite_mutations(None, None), [])

    def test_ov_verify_wires_production_tamper_guard(self):
        # Fingerprint before + after the suite; detection fails verification fail-closed.
        body = self.APP.split("def _ov_verify(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("OVERNIGHT_SUITE_TAMPER_GUARD", body)
        self.assertIn("suite_mutations", body)
        self.assertIn("PRODUCTION-TAMPER", body)
        self.assertIn('"passed": False', body)

    def test_production_tamper_guard_default_on(self):
        import config
        self.assertTrue(config.OVERNIGHT_SUITE_TAMPER_GUARD)

    def test_start_response_carries_run_id_and_conditional_resume_from(self):
        body = self.APP.split("def _overnight_start(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"run_id": run_id,', body)
        self.assertIn('"resume_from": run_id if resumed else None}', body)
        self.assertNotIn('"resume_from": run_id}', body)

    def test_merge_drive_tele_keeps_absent_nudges_absent(self):
        # A drive that never nudged must not grow zero-valued keys (the report renders on presence).
        import app
        agg = {}
        app._merge_drive_tele(agg, {"turns": 10, "infer_s": 1.0, "tool_s": 1.0, "tools": {}})
        self.assertNotIn("nudges_soft", agg)
        self.assertNotIn("nudges_hard", agg)


class RunIdImmediateTests(unittest.TestCase):

    def test_new_run_id_public_alias_and_shape(self):
        import re
        rid = overnight.new_run_id()
        self.assertRegex(rid, r"^ovr-\d{8}T\d{6}Z$")
        self.assertIs(overnight.new_run_id, overnight._new_run_id)

    def test_overnight_start_seeds_real_id_and_threads_it(self):
        app_src = open("app.py", encoding="utf-8").read()
        self.assertIn("run_id = overnight.new_run_id()", app_src)
        self.assertIn('_OVERNIGHT["state"] = {"run_id": run_id, "active": True}', app_src)
        # the placeholder seed is gone for good
        self.assertNotIn('"run_id": run_id or "(starting)"', app_src)


class HeartbeatTests(unittest.TestCase):

    def test_render_heartbeat_sorted_busiest_first(self):
        s = overnight.render_heartbeat(20, {"coding_search": 5, "coding_read": 8})
        self.assertEqual(s, "turn 20 · coding_read 8 · coding_search 5")

    def test_render_heartbeat_empty_tools(self):
        self.assertEqual(overnight.render_heartbeat(7, {}), "turn 7")
        self.assertEqual(overnight.render_heartbeat(7, None), "turn 7")

    def test_config_default(self):
        import config
        self.assertEqual(int(os.getenv("CODING_HEARTBEAT_TURNS", "10")), config.CODING_HEARTBEAT_TURNS)

    def test_app_wiring(self):
        app_src = open("app.py", encoding="utf-8").read()
        drive_body = app_src.split("def _ov_drive(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("drive_heartbeat=True", drive_body)
        fix_body = app_src.split("def anchor_and_fix(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("drive_heartbeat=True", fix_body)          # fix loop pulses too
        self.assertIn("drive_telemetry=_ft", fix_body)           # ...and finally collects telemetry
        self.assertEqual(app_src.count("heartbeat=_hb"), 2)      # both mothership chat calls
        self.assertIn("render_heartbeat", app_src)               # pulses render through the pure helper


class HollowScoreTests(unittest.TestCase):
    """Phase B3 honest scoreboard: guard-caught lying-greens (empty diff / no demanded tests /
    spec-named targets untouched) get their own counter, separate from plain red bails."""

    ITEMS = [
        {"outcome": "done", "adversarial": {"verdict": "pass"}},
        {"outcome": "bailed",
         "adversarial": {"verdict": "bail_human", "reason": "acceptance_scope_missed"}},
        {"outcome": "bailed", "adversarial": {"verdict": "bail_human", "reason": "hollow_no_tests"}},
        {"outcome": "bailed", "evidence": "verifier never passed"},   # honest red, NOT hollow
        {"outcome": "skipped"},                                       # dry-run: excluded entirely
    ]

    def test_score_run_counts_hollow_separately(self):
        s = overnight.score_run(self.ITEMS)
        self.assertEqual(s["items_executed"], 4)
        self.assertEqual(s["coding"], {"correct": 1, "incorrect": 3, "hollow": 2})

    def test_no_changes_produced_counts_as_hollow(self):
        s = overnight.score_run([{"outcome": "bailed",
                                  "adversarial": {"verdict": "bail_human",
                                                  "reason": "no_changes_produced"}}])
        self.assertEqual(s["coding"]["hollow"], 1)

    def test_bail_human_for_other_reasons_is_not_hollow(self):
        # A critic-stall bail_human is a review outcome, not a lying green.
        s = overnight.score_run([{"outcome": "bailed",
                                  "adversarial": {"verdict": "bail_human", "reason": "stalled"}}])
        self.assertEqual(s["coding"]["hollow"], 0)

    def test_render_names_hollow_count(self):
        state = {"run_id": "r-hollow", "stop_reason": "completed", "active": False,
                 "done_count": 1, "bailed_count": 3, "items": self.ITEMS}
        text = "\n".join(overnight.render_run_summary(state))
        self.assertIn("(2 hollow)", text)

    def test_render_omits_hollow_when_zero(self):
        state = {"run_id": "r-clean", "stop_reason": "completed", "active": False,
                 "done_count": 1, "bailed_count": 0,
                 "items": [{"outcome": "done", "adversarial": {"verdict": "pass"}}]}
        text = "\n".join(overnight.render_run_summary(state))
        self.assertNotIn("hollow", text)


class TimingsAttemptsPreservationTests(unittest.TestCase):

    def test_timings_and_attempts_persisted_when_present(self):
        timings_data = {
            "drive": 45.2,
            "verify": 12.8,
            "review": 30.0,
            "drive_tele": {"files_read": 5, "patches_applied": 2, "turns": 18},
        }
        attempts_data = [{"outcome": "verify_red", "detail": "test failed"},
                         {"outcome": "done", "detail": "green"}]

        def worker_with_metrics(item, budget):
            return {
                "outcome": "done",
                "evidence": "all green",
                "timings": timings_data,
                "attempts": attempts_data,
            }

        st = run_overnight(RunBudget(60), worker=worker_with_metrics,
                           item_source=_items("a", "b"))
        self.assertEqual(len(st["items"]), 2)
        for rec in st["items"]:
            self.assertIn("timings", rec)
            self.assertIn("attempts", rec)
            self.assertEqual(rec["timings"], timings_data)
            self.assertEqual(rec["attempts"], attempts_data)
            # drive_tele sub-dict preserved intact
            self.assertIn("drive_tele", rec["timings"])
            self.assertEqual(rec["timings"]["drive_tele"]["turns"], 18)

    def test_timings_and_attempts_absent_when_worker_omits_them(self):
        """An item whose worker result lacks timings/attempts persists without those keys."""

        def worker_without_metrics(item, budget):
            return {"outcome": "done", "evidence": "ok"}

        st = run_overnight(RunBudget(60), worker=worker_without_metrics,
                           item_source=_items("x"))
        self.assertEqual(len(st["items"]), 1)
        rec = st["items"][0]
        self.assertNotIn("timings", rec)
        self.assertNotIn("attempts", rec)

    def test_mixed_timings_present_only_on_some_items(self):
        """First item has timings/attempts, second does not — each preserves its own shape."""
        results = iter([
            {"outcome": "done", "evidence": "ok", "timings": {"drive": 10}, "attempts": [1]},
            {"outcome": "bailed", "evidence": "nope"},
        ])

        def mixed_worker(item, budget):
            return next(results)

        st = run_overnight(RunBudget(60), worker=mixed_worker,
                           item_source=_items("p", "q"))
        p_rec = st["items"][0]
        q_rec = st["items"][1]
        self.assertIn("timings", p_rec)
        self.assertIn("attempts", p_rec)
        self.assertNotIn("timings", q_rec)
        self.assertNotIn("attempts", q_rec)


if __name__ == "__main__":
    unittest.main()
