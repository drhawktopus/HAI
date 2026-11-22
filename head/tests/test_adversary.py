import unittest

import adversary
from adversary import AdversaryTier, select_adversary, run_ladder


def _tiers():
    return [
        AdversaryTier("floor", "m27", 120, False),
        AdversaryTier("decorrelated", "m35", 240, True),
        AdversaryTier("coder", "m30c", 300, True),
        AdversaryTier("max", "m70", 600, True),
    ]


class _Budget:
    def __init__(self, remaining):
        self.remaining_seconds = remaining


class TierPoolTests(unittest.TestCase):
    def test_single_model_back_compat(self):
        t = AdversaryTier("floor", "m27", 120, False)
        self.assertEqual(t.models, ["m27"])           # a single-model tier exposes a 1-item pool
        self.assertEqual(t.model, "m27")

    def test_tiers_from_config_parses_pool(self):
        tiers = adversary.tiers_from_config([
            {"name": "pair", "models": ["a", "b"], "est_seconds": 240, "swap": True}])
        self.assertEqual(len(tiers), 1)
        self.assertEqual(tiers[0].models, ["a", "b"])
        self.assertEqual(tiers[0].model, "a")          # primary == first of the pool

    def test_tiers_from_config_back_compat_single(self):
        tiers = adversary.tiers_from_config([
            {"name": "floor", "model": "m27", "est_seconds": 120, "swap": False}])
        self.assertEqual(tiers[0].models, ["m27"])

    def test_tiers_from_config_skips_modelless(self):
        self.assertEqual(adversary.tiers_from_config([{"name": "x", "est_seconds": 1}]), [])


class CriticTimeoutTests(unittest.TestCase):
    def test_fast_tier_gets_at_least_base(self):
        # a fast tier (est 120) still gets the base adversarial timeout, not its tiny estimate
        floor = AdversaryTier("floor", "m27", 120, False)
        self.assertEqual(adversary.critic_timeout(floor, 9999, 600), 600.0)

    def test_heavy_tier_extends_to_estimate(self):
        # the 70b (est 1200) gets its big window when the budget affords it
        big = AdversaryTier("max", "m70", 1200, True)
        self.assertEqual(adversary.critic_timeout(big, 9999, 600), 1200.0)

    def test_never_exceeds_remaining_budget(self):
        big = AdversaryTier("max", "m70", 1200, True)
        self.assertEqual(adversary.critic_timeout(big, 800, 600), 800.0)

    def test_floor_at_least_one_second(self):
        big = AdversaryTier("max", "m70", 1200, True)
        self.assertEqual(adversary.critic_timeout(big, 0, 600), 1.0)


class SelectTests(unittest.TestCase):
    def test_no_tiers(self):
        self.assertIsNone(select_adversary(9999, [], fix_reserve=180, margin=60))

    def test_huge_budget_picks_richest(self):
        t = select_adversary(10000, _tiers(), fix_reserve=180, margin=60)
        self.assertEqual(t.name, "max")

    def test_mid_budget_picks_richest_that_fits_full_round(self):
        # fits floor(120+240) and decorrelated(240+240=480) but not coder(300+240=540)
        t = select_adversary(500, _tiers(), fix_reserve=180, margin=60)
        self.assertEqual(t.name, "decorrelated")

    def test_tight_budget_floor_critique_only(self):
        # can't afford floor+fix+margin(360) but can afford floor critique+margin(180)
        t = select_adversary(200, _tiers(), fix_reserve=180, margin=60)
        self.assertEqual(t.name, "floor")

    def test_no_time_returns_none(self):
        self.assertIsNone(select_adversary(100, _tiers(), fix_reserve=180, margin=60))

    def test_force_runs_floor_even_when_tight(self):
        t = select_adversary(100, _tiers(), fix_reserve=180, margin=60, force=True)
        self.assertEqual(t.name, "floor")


def _ladder(critic_seq, *, reverify_seq=None, budget=10000, max_rounds=2, fix_calls=None,
            stall_on_no_progress=False, stall_window=1, checkpoint=None, restore=None, dispute=None,
            diff="diff", **kw):
    """Helper to run the ladder with scripted critic + reverify results."""
    critiques = iter(critic_seq)
    reverifies = iter(reverify_seq or [])
    fixes = fix_calls if fix_calls is not None else []

    def critic(tier, _diff):
        return next(critiques)

    def anchor_and_fix(findings, budget):
        fixes.append(len(findings))
        return {"fixed": len(findings)}

    def reverify():
        return next(reverifies)

    return run_ladder(
        item={"id": "a"}, budget=_Budget(budget), tiers=_tiers(),
        critic=critic, anchor_and_fix=anchor_and_fix, reverify=reverify,
        get_diff=lambda: diff, fix_reserve=180, margin=60, max_rounds=max_rounds,
        stall_on_no_progress=stall_on_no_progress, stall_window=stall_window,
        checkpoint=checkpoint, restore=restore, dispute=dispute, **kw,
    )


def _low(*explanations):
    """A changes_requested critique whose findings are all LOW severity (dispute-eligible)."""
    return {"verdict": "changes_requested",
            "findings": [{"severity": "low", "explanation": e} for e in explanations]}


class _Snapshotter:
    """A scripted checkpoint/restore pair: checkpoint() hands out ckpt0, ckpt1, ... (ckpt0 == the
    pre-adversarial verified state); restore() records which id it was reset to."""

    def __init__(self):
        self.n = 0
        self.created = []
        self.restored = []

    def checkpoint(self):
        cid = "ckpt%d" % self.n
        self.n += 1
        self.created.append(cid)
        return cid

    def restore(self, cid):
        self.restored.append(cid)
        return {"restored_commit": cid}


def _cr(n):
    """A changes_requested critique with n high-severity findings."""
    return {"verdict": "changes_requested", "findings": [{"severity": "high"}] * n}


class LadderTests(unittest.TestCase):
    def test_clean_pass_no_fix(self):
        trail = _ladder([{"verdict": "pass", "findings": []}])
        self.assertEqual(trail["verdict"], "pass")
        self.assertEqual(trail["rounds"], [])

    def test_changes_then_fixed_clean(self):
        fixes = []
        trail = _ladder(
            [{"verdict": "changes_requested", "findings": [{"severity": "high"}]},
             {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}], fix_calls=fixes)
        self.assertEqual(trail["verdict"], "fixed")
        self.assertEqual(len(trail["rounds"]), 1)
        self.assertEqual(fixes, [1])

    def test_fix_breaks_verifier_is_reverify_red(self):
        trail = _ladder(
            [{"verdict": "changes_requested", "findings": [{"severity": "high"}]}],
            reverify_seq=[{"passed": False, "summary": "new test red"}])
        self.assertEqual(trail["verdict"], "reverify_red")
        self.assertEqual(trail["reason"], "fix_broke_verifier")

    def test_still_unhappy_at_cap_bails_human(self):
        # critic always asks for changes, reverify always green -> exhaust rounds -> bail_human
        trail = _ladder(
            [{"verdict": "changes_requested", "findings": [{"severity": "high"}]}] * 3,
            reverify_seq=[{"passed": True}, {"passed": True}], max_rounds=2)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["reason"], "still_changes_requested_at_cap")
        self.assertEqual(len(trail["rounds"]), 2)

    def test_overnight_drives_past_legacy_cap_to_convergence(self):
        # 5 findings walked down 5->3->1->0 over THREE fix rounds. Legacy cap=2 would bail at 1;
        # overnight (stall guard + generous ceiling) keeps going while findings drop -> FIXED.
        fixes = []
        trail = _ladder(
            [_cr(5), _cr(3), _cr(1), {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}] * 3, max_rounds=12,
            stall_on_no_progress=True, fix_calls=fixes)
        self.assertEqual(trail["verdict"], "fixed")
        self.assertEqual(len(trail["rounds"]), 3)          # ran past the old 2-round cap
        self.assertEqual(fixes, [5, 3, 1])

    def test_overnight_bails_when_findings_stall(self):
        # findings stop dropping (2 -> 2): genuinely stuck -> bail_human, do NOT burn the ceiling.
        trail = _ladder(
            [_cr(2), _cr(2), _cr(2)],
            reverify_seq=[{"passed": True}] * 3, max_rounds=12,
            stall_on_no_progress=True)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["reason"], "stalled_no_progress")
        self.assertEqual(trail["stalled_at"], 2)
        self.assertEqual(len(trail["rounds"]), 1)          # bailed right after the first non-improving round

    def test_overnight_hard_ceiling_still_bounds(self):
        # pathological monotonic drip that never reaches pass; ceiling stops the loop.
        seq = [_cr(n) for n in range(20, 0, -1)]            # 20,19,...,1  (always changes_requested)
        trail = _ladder(seq, reverify_seq=[{"passed": True}] * 20, max_rounds=3,
                        stall_on_no_progress=True)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["reason"], "still_changes_requested_at_cap")
        self.assertEqual(len(trail["rounds"]), 3)

    def test_no_time_for_floor_skips(self):
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(50), tiers=_tiers(),
            critic=lambda t, d: {"verdict": "pass"}, anchor_and_fix=lambda f, b: None,
            reverify=lambda: {"passed": True}, get_diff=lambda: "d",
            fix_reserve=180, margin=60)
        self.assertEqual(trail["verdict"], "skipped")
        self.assertEqual(trail["reason"], "no_time_for_floor")

    def test_critic_error_skips_safely(self):
        def boom(tier, diff):
            raise RuntimeError("critic offline")
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(),
            critic=boom, anchor_and_fix=lambda f, b: None,
            reverify=lambda: {"passed": True}, get_diff=lambda: "d",
            fix_reserve=180, margin=60)
        self.assertEqual(trail["verdict"], "skipped")
        self.assertIn("critic_error", trail["reason"])

    def test_out_of_time_before_fix_round_bails(self):
        # budget afol floor critique only; first critique asks for changes but no time to fix
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(200), tiers=_tiers(),
            critic=lambda t, d: {"verdict": "changes_requested", "findings": [{"severity": "high"}]},
            anchor_and_fix=lambda f, b: self.fail("should not fix without time"),
            reverify=lambda: {"passed": True}, get_diff=lambda: "d",
            fix_reserve=180, margin=60)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["reason"], "out_of_time")


class StallWindowTests(unittest.TestCase):
    """#2: patience (stall_window) tolerates a noisy re-critique blip and keeps the best round."""

    def test_window_tolerates_single_blip_then_converges(self):
        # 5 -> 3 -> (blip) 6 -> 1 -> pass. With window=1 the round-2 blip would bail; window=2 rides
        # through it and the run converges. Proves a noisy count draw no longer sinks an on-track run.
        fixes = []
        trail = _ladder(
            [_cr(5), _cr(3), _cr(6), _cr(1), {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}] * 4, max_rounds=12,
            stall_on_no_progress=True, stall_window=2, fix_calls=fixes)
        self.assertEqual(trail["verdict"], "fixed")
        self.assertEqual(len(trail["rounds"]), 4)       # did NOT bail at the round-2 blip
        self.assertEqual(fixes, [5, 3, 6, 1])

    def test_same_blip_bails_at_window_1(self):
        # Control: the SAME sequence with the old single-round stall bails at the blip.
        trail = _ladder(
            [_cr(5), _cr(3), _cr(6), _cr(1), {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}] * 4, max_rounds=12,
            stall_on_no_progress=True, stall_window=1)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["reason"], "stalled_no_progress")

    def test_window_keeps_best_on_genuine_stall(self):
        # 5 -> 3 -> 6 -> 7: never recovers. Bails after `window` non-improving rounds and reports the
        # BEST reached (3 at round 1), not the noisy 7 it happened to stop on.
        trail = _ladder(
            [_cr(5), _cr(3), _cr(6), _cr(7)],
            reverify_seq=[{"passed": True}] * 3, max_rounds=12,
            stall_on_no_progress=True, stall_window=2)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["reason"], "stalled_no_progress")
        self.assertEqual(trail["stalled_at"], 3)        # keep-best, not 7
        self.assertEqual(trail["best_round"], 1)
        self.assertEqual(len(trail["rounds"]), 3)


class FindingsPersistenceTests(unittest.TestCase):
    """#1: the actual critic finding TEXT is persisted (initial + each re-critique + what's outstanding)."""

    @staticmethod
    def _cr_text(*findings):
        return {"verdict": "changes_requested",
                "findings": [{"severity": "high", "explanation": e, "recommended_action": a}
                             for (e, a) in findings]}

    def test_findings_log_and_outstanding_are_recorded(self):
        # initial 2 findings -> 1 -> 1 (stall at window=1). The log must carry the real text at each
        # stage, and findings_outstanding must be the still-open finding when it bailed.
        trail = _ladder(
            [self._cr_text(("E1", "A1"), ("E2", "A2")),
             self._cr_text(("E3", "A3")),
             self._cr_text(("E3", "A3"))],
            reverify_seq=[{"passed": True}] * 2, max_rounds=12,
            stall_on_no_progress=True, stall_window=1)
        self.assertEqual(trail["verdict"], "bail_human")
        log = trail["findings_log"]
        self.assertEqual([e["stage"] for e in log], ["initial", "recritique_1", "recritique_2"])
        self.assertEqual(log[0]["count"], 2)
        self.assertEqual(log[0]["findings"][0]["explanation"], "E1")
        self.assertEqual(log[0]["findings"][0]["recommended_action"], "A1")
        # what a human must act on at bail: the outstanding finding text, not just a count
        self.assertEqual(trail["findings_outstanding"], log[-1]["findings"])
        self.assertEqual(trail["findings_outstanding"][0]["explanation"], "E3")

    def test_clean_pass_still_logs_the_initial_critique(self):
        trail = _ladder([{"verdict": "pass", "findings": []}])
        self.assertEqual(trail["verdict"], "pass")
        self.assertEqual(len(trail["findings_log"]), 1)
        self.assertEqual(trail["findings_log"][0]["stage"], "initial")


class KeepBestRestoreTests(unittest.TestCase):
    """#2 (with restore): on a bail that ended on a worse round, the workspace is hard-reset to the
    best round's checkpoint (round 0 = the pre-adversarial verified state)."""

    def test_restores_best_round_checkpoint_on_stall(self):
        # 5 -> 3(best,round1) -> 6 -> 7 : stalls at window=2; must restore round 1's snapshot (ckpt1).
        snap = _Snapshotter()
        trail = _ladder(
            [_cr(5), _cr(3), _cr(6), _cr(7)],
            reverify_seq=[{"passed": True}] * 3, max_rounds=12,
            stall_on_no_progress=True, stall_window=2,
            checkpoint=snap.checkpoint, restore=snap.restore)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["best_round"], 1)
        self.assertEqual(snap.created, ["ckpt0", "ckpt1", "ckpt2", "ckpt3"])  # round0 + 3 accepted rounds
        self.assertEqual(snap.restored, ["ckpt1"])
        self.assertEqual(trail["restored_to_best"]["best_round"], 1)
        self.assertEqual(trail["restored_to_best"]["checkpoint"], "ckpt1")

    def test_restores_round0_when_every_fix_regresses(self):
        # 5 -> 6 -> 7 : nothing ever beats the pre-adversarial state -> restore ckpt0 (the clean solution).
        snap = _Snapshotter()
        trail = _ladder(
            [_cr(5), _cr(6), _cr(7)],
            reverify_seq=[{"passed": True}] * 2, max_rounds=12,
            stall_on_no_progress=True, stall_window=2,
            checkpoint=snap.checkpoint, restore=snap.restore)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["best_round"], 0)
        self.assertEqual(snap.restored, ["ckpt0"])
        self.assertEqual(trail["restored_to_best"]["best_round"], 0)

    def test_no_restore_on_convergence(self):
        # A run that converges keeps its final state; restore must NOT fire.
        snap = _Snapshotter()
        trail = _ladder(
            [_cr(5), _cr(3), {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}] * 2, max_rounds=12,
            stall_on_no_progress=True, stall_window=2,
            checkpoint=snap.checkpoint, restore=snap.restore)
        self.assertEqual(trail["verdict"], "fixed")
        self.assertEqual(snap.restored, [])
        self.assertNotIn("restored_to_best", trail)

    def test_report_only_without_hooks(self):
        # No checkpoint/restore hooks -> keep-best is report-only, no crash, best still recorded.
        trail = _ladder(
            [_cr(5), _cr(3), _cr(6), _cr(7)],
            reverify_seq=[{"passed": True}] * 3, max_rounds=12,
            stall_on_no_progress=True, stall_window=2)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["best_round"], 1)
        self.assertNotIn("restored_to_best", trail)


class LedgerTests(unittest.TestCase):
    """#4: the accepted-dispute ledger keys + filter (pure)."""

    def test_key_is_casefolded_explanation_prefix(self):
        f1 = {"severity": "low", "explanation": "The Foo is bar"}
        f2 = {"severity": "low", "explanation": "the foo is BAR"}   # same key
        f3 = {"severity": "low", "explanation": "a different thing"}
        k = adversary._ledger_key(f1)
        self.assertEqual(k, adversary._ledger_key(f2))
        self.assertNotEqual(k, adversary._ledger_key(f3))

    def test_apply_ledger_drops_matches(self):
        f1 = {"severity": "low", "explanation": "nit one"}
        f3 = {"severity": "low", "explanation": "nit three"}
        self.assertEqual(adversary._apply_ledger([f1, f3], {adversary._ledger_key(f1)}), [f3])
        self.assertEqual(adversary._apply_ledger([f1], set()), [f1])   # empty ledger = no-op


def _seq_critic(seq):
    """A critic() that returns each scripted critique in turn (raises StopIteration if overrun)."""
    it = iter(seq)

    def critic(tier, diff):
        return next(it)
    return critic


class TestTamperDetectTests(unittest.TestCase):
    """#3: detect_test_tampering (pure) — editing an EXISTING test is gaming; adding a NEW one is fine."""

    def test_modified_existing_test_flagged(self):
        diff = ("diff --git a/tests/test_loops.py b/tests/test_loops.py\n"
                "--- a/tests/test_loops.py\n+++ b/tests/test_loops.py\n"
                "@@ -1 +1 @@\n-    self.assertEqual(r, 2)\n+    self.assertEqual(r, 3)\n")
        hits = adversary.detect_test_tampering(diff)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "tests/test_loops.py")
        self.assertEqual(hits[0]["removed_lines"], 1)

    def test_new_test_file_not_flagged(self):
        diff = ("diff --git a/tests/test_integration.py b/tests/test_integration.py\n"
                "new file mode 100644\n--- /dev/null\n+++ b/tests/test_integration.py\n"
                "@@ -0,0 +1 @@\n+def test_x(): pass\n")
        self.assertEqual(adversary.detect_test_tampering(diff), [])

    def test_renamed_existing_test_flagged(self):
        diff = ("diff --git a/tests/test_loops.py b/tests/test_loops2.py\n"
                "similarity index 95%\nrename from tests/test_loops.py\nrename to tests/test_loops2.py\n")
        hits = adversary.detect_test_tampering(diff)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["renamed"])

    def test_non_test_code_change_not_flagged(self):
        diff = ("diff --git a/loops.py b/loops.py\n--- a/loops.py\n+++ b/loops.py\n"
                "@@ -1 +1 @@\n-x = 1\n+x = 2\n")
        self.assertEqual(adversary.detect_test_tampering(diff), [])


class CriticalRecurrenceTests(unittest.TestCase):
    """#2: _max_critical_recurrence (pure) — how many distinct critiques re-raised the same critical."""

    def test_counts_distinct_stages_for_same_key(self):
        log = [
            {"stage": "initial", "findings": [{"severity": "critical", "explanation": "bug A"}]},
            {"stage": "recritique_1", "findings": [{"severity": "critical", "explanation": "bug A"}]},
            {"stage": "recritique_2", "findings": [{"severity": "high", "explanation": "bug A"},
                                                   {"severity": "low", "explanation": "nit"}]},
        ]
        self.assertEqual(adversary._max_critical_recurrence(log), 3)

    def test_ignores_low_and_medium(self):
        log = [{"stage": "s1", "findings": [{"severity": "low", "explanation": "x"},
                                            {"severity": "medium", "explanation": "y"}]},
               {"stage": "s2", "findings": [{"severity": "low", "explanation": "x"}]}]
        self.assertEqual(adversary._max_critical_recurrence(log), 0)


class LadderHardeningTests(unittest.TestCase):
    """Hardening rules: crit failures escalate to bail/tamper flags; critical recurrence triggers flag."""

    def test_critic_pool_failure_flags_human_not_fixed(self):
        # #1/#4: a re-critique where the whole pool fails must NOT default to "fixed" -> bail_human,
        # and the pool is retried critic_retries times first.
        calls = {"n": 0}

        def critic(tier, diff):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"verdict": "changes_requested",
                        "findings": [{"severity": "high", "explanation": "x"}]}
            raise RuntimeError("all critics in the pool failed")

        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(), critic=critic,
            anchor_and_fix=lambda f, b: {"fixed": len(f)}, reverify=lambda: {"passed": True},
            get_diff=lambda: "diff", fix_reserve=180, margin=60, max_rounds=12,
            stall_on_no_progress=True, critic_retries=1)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertTrue(trail["reason"].startswith("recritique_failed"))
        self.assertEqual(calls["n"], 3)   # initial + recritique attempt + 1 retry

    def test_test_tampering_flags_human_even_on_clean_pass(self):
        # #3: critic finds nothing (would be "pass"), but the diff rewrote an existing test -> bail_human.
        diff = ("diff --git a/tests/test_loops.py b/tests/test_loops.py\n"
                "--- a/tests/test_loops.py\n+++ b/tests/test_loops.py\n"
                "@@ -1 +1 @@\n-    self.assertEqual(r, 2)\n+    self.assertEqual(r, 3)\n")
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(),
            critic=lambda t, d: {"verdict": "pass", "findings": []},
            anchor_and_fix=lambda f, b: None, reverify=lambda: {"passed": True},
            get_diff=lambda: diff, fix_reserve=180, margin=60, tamper_guard=True)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["reason"], "test_tampering_flag_human")
        self.assertTrue(trail["test_tampering"])

    def test_tamper_guard_off_leaves_pass(self):
        # Without the guard, the same clean pass is unchanged (default-off -> back-compat).
        diff = ("diff --git a/tests/test_loops.py b/tests/test_loops.py\n"
                "--- a/tests/test_loops.py\n+++ b/tests/test_loops.py\n@@ -1 +1 @@\n-a\n+b\n")
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(),
            critic=lambda t, d: {"verdict": "pass", "findings": []},
            anchor_and_fix=lambda f, b: None, reverify=lambda: {"passed": True},
            get_diff=lambda: diff, fix_reserve=180, margin=60)
        self.assertEqual(trail["verdict"], "pass")

    def test_recurring_critical_downgrades_converged_to_human(self):
        # #2: the SAME critical is re-raised across 3 critiques, then drops to 0. Even though it would
        # "converge", a critical that recurred that many times was argued away, not fixed -> bail_human.
        crit = {"severity": "critical", "explanation": "stall detection regression"}
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(),
            critic=_seq_critic([
                {"verdict": "changes_requested", "findings": [crit]},
                {"verdict": "changes_requested", "findings": [crit]},
                {"verdict": "changes_requested", "findings": [crit]},
                {"verdict": "pass", "findings": []}]),
            anchor_and_fix=lambda f, b: {"fixed": len(f)}, reverify=lambda: {"passed": True},
            get_diff=lambda: "diff", fix_reserve=180, margin=60, max_rounds=12,
            stall_on_no_progress=True, stall_window=5, critical_recur_limit=3)
        self.assertEqual(trail["max_critical_recurrence"], 3)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["reason"], "recurring_critical_flag_human")

    def test_single_round_critical_still_converges(self):
        # A critical fixed in ONE round (recurrence 1) converges normally -> fixed, not downgraded.
        crit = {"severity": "critical", "explanation": "a real bug"}
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(),
            critic=_seq_critic([
                {"verdict": "changes_requested", "findings": [crit]},
                {"verdict": "pass", "findings": []}]),
            anchor_and_fix=lambda f, b: {"fixed": len(f)}, reverify=lambda: {"passed": True},
            get_diff=lambda: "diff", fix_reserve=180, margin=60, max_rounds=12,
            stall_on_no_progress=True, critical_recur_limit=3)
        self.assertEqual(trail["verdict"], "fixed")
        self.assertEqual(trail["max_critical_recurrence"], 1)


class MaterialityGateTests(unittest.TestCase):
    """Materiality gate: convergence blocks only on material (critical/high) findings; medium/low are
    advisory so a rich review converges instead of churning on nitpicks."""

    def test_medium_only_remaining_converges(self):
        # A high finding is fixed; only a medium nitpick remains -> converges (material=crit/high).
        high = {"severity": "high", "explanation": "real issue"}
        med = {"severity": "medium", "explanation": "docstring nit"}
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(),
            critic=_seq_critic([
                {"verdict": "changes_requested", "findings": [high, med]},
                {"verdict": "changes_requested", "findings": [med]}]),  # only medium left
            anchor_and_fix=lambda f, b: {"fixed": len(f)}, reverify=lambda: {"passed": True},
            get_diff=lambda: "diff", fix_reserve=180, margin=60, max_rounds=12,
            stall_on_no_progress=True, materiality_severities=("critical", "high"))
        self.assertEqual(trail["verdict"], "fixed")

    def test_initial_medium_only_is_pass(self):
        # An initial critique of only medium/low findings converges immediately under materiality.
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(),
            critic=lambda t, d: {"verdict": "changes_requested",
                                 "findings": [{"severity": "medium", "explanation": "nit"},
                                              {"severity": "low", "explanation": "tiny"}]},
            anchor_and_fix=lambda f, b: None, reverify=lambda: {"passed": True},
            fix_reserve=180, margin=60, get_diff=lambda: "diff",
            materiality_severities=("critical", "high"))
        self.assertEqual(trail["verdict"], "pass")

    def test_high_finding_still_blocks(self):
        # A material (high) finding that never clears is NOT converged -> bail_human at the cap.
        high = {"severity": "high", "explanation": "x"}
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(),
            critic=lambda t, d: {"verdict": "changes_requested", "findings": [high]},
            anchor_and_fix=lambda f, b: {"fixed": len(f)}, reverify=lambda: {"passed": True},
            get_diff=lambda: "diff", fix_reserve=180, margin=60, max_rounds=2,
            materiality_severities=("critical", "high"))
        self.assertEqual(trail["verdict"], "bail_human")

    def test_materiality_off_medium_still_blocks(self):
        # Back-compat: without the gate (default), a medium finding still blocks convergence.
        trail = run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=_tiers(),
            critic=lambda t, d: {"verdict": "changes_requested",
                                 "findings": [{"severity": "medium", "explanation": "nit"}]},
            anchor_and_fix=lambda f, b: {"fixed": len(f)}, reverify=lambda: {"passed": True},
            get_diff=lambda: "diff", fix_reserve=180, margin=60, max_rounds=2)
        self.assertEqual(trail["verdict"], "bail_human")


class DisputeTests(unittest.TestCase):
    """#4: LOW findings can be argued away (author disputes, critic quorum adjudicates); MEDIUM+ never."""

    def test_all_low_upheld_becomes_pass(self):
        def dispute(tier, low):
            return {"accepted": [{"finding": f, "rationale": "nit"} for f in low], "rejected": []}
        trail = _ladder([_low("nit A", "nit B")], dispute=dispute, stall_on_no_progress=True)
        self.assertEqual(trail["verdict"], "pass")            # everything argued away -> no fix loop
        self.assertEqual(trail["findings_initial"], 2)        # RAW count still recorded
        self.assertEqual(len(trail["disputes"]), 1)
        self.assertEqual(trail["disputes"][0]["stage"], "initial")
        self.assertEqual(len(trail["disputes"][0]["upheld"]), 2)

    def test_low_upheld_high_still_fixed(self):
        def dispute(tier, low):
            return {"accepted": [{"finding": low[0], "rationale": "nit"}], "rejected": []}
        trail = _ladder(
            [{"verdict": "changes_requested",
              "findings": [{"severity": "high", "explanation": "real bug"},
                           {"severity": "low", "explanation": "a nit"}]},
             {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}], dispute=dispute, stall_on_no_progress=True, max_rounds=12)
        self.assertEqual(trail["verdict"], "fixed")           # low argued away, high fixed
        self.assertEqual(trail["findings_initial"], 2)

    def test_ledger_drops_reraised_low_without_re_disputing(self):
        calls = {"n": 0}

        def dispute(tier, low):
            calls["n"] += 1
            return {"accepted": [{"finding": low[0], "rationale": "nit"}], "rejected": []}
        trail = _ladder(
            [{"verdict": "changes_requested",
              "findings": [{"severity": "high", "explanation": "bug"},
                           {"severity": "low", "explanation": "the nit"}]},
             {"verdict": "changes_requested", "findings": [{"severity": "low", "explanation": "the nit"}]}],
            reverify_seq=[{"passed": True}], dispute=dispute, stall_on_no_progress=True, max_rounds=12)
        self.assertEqual(trail["verdict"], "fixed")   # re-raised low dropped by the ledger -> converged
        self.assertEqual(calls["n"], 1)               # dispute called ONCE; the ledger handled the re-raise

    def test_rejected_dispute_keeps_finding(self):
        def dispute(tier, low):
            return {"accepted": [], "rejected": [{"finding": low[0], "rationale": "weak"}]}
        trail = _ladder(
            [_low("stubborn nit"), _low("stubborn nit")],
            reverify_seq=[{"passed": True}], dispute=dispute,
            stall_on_no_progress=True, stall_window=1, max_rounds=12)
        self.assertEqual(trail["verdict"], "bail_human")   # not argued away, not resolved -> stalls
        self.assertEqual(trail["disputes"], [])            # nothing upheld

    def test_medium_and_high_are_never_offered_to_dispute(self):
        seen = []

        def dispute(tier, low):
            seen.extend(low)
            return {"accepted": [], "rejected": []}
        trail = _ladder([_cr(2), _cr(2)], reverify_seq=[{"passed": True}], dispute=dispute,
                        stall_on_no_progress=True, stall_window=1)
        self.assertEqual(seen, [])                          # high findings never disputable
        self.assertEqual(trail["verdict"], "bail_human")

    def test_no_hook_is_unchanged(self):
        trail = _ladder([_cr(2), _cr(2)], reverify_seq=[{"passed": True}],
                        stall_on_no_progress=True, stall_window=1)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["disputes"], [])


_TEST_EDIT_DIFF = (
    "diff --git a/tests/test_x.py b/tests/test_x.py\n"
    "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
    "-    assert old_contract()\n+    assert new_contract()\n"
)
_TWO_EDIT_DIFF = _TEST_EDIT_DIFF + (
    "diff --git a/tests/test_y.py b/tests/test_y.py\n"
    "--- a/tests/test_y.py\n+++ b/tests/test_y.py\n"
    "-    assert something()\n+    pass\n"
)


class AuthorizedTestEditTests(unittest.TestCase):

    def test_authorized_edit_does_not_flag(self):
        trail = _ladder([{"verdict": "pass", "findings": []}], diff=_TEST_EDIT_DIFF,
                        tamper_guard=True, tamper_allowed=("tests/test_x.py",))
        self.assertEqual(trail["verdict"], "pass")                       # no tamper bail
        self.assertNotIn("test_tampering", trail)
        self.assertEqual(trail["authorized_test_edits"][0]["file"], "tests/test_x.py")

    def test_function_scoped_grant_matches_its_file(self):
        trail = _ladder([{"verdict": "pass", "findings": []}], diff=_TEST_EDIT_DIFF,
                        tamper_guard=True,
                        tamper_allowed=("tests/test_x.py::test_old_contract",))
        self.assertEqual(trail["verdict"], "pass")
        self.assertIn("authorized_test_edits", trail)

    def test_edit_outside_grant_still_flags(self):
        trail = _ladder([{"verdict": "pass", "findings": []}], diff=_TWO_EDIT_DIFF,
                        tamper_guard=True, tamper_allowed=("tests/test_x.py",))
        self.assertEqual(trail["verdict"], "bail_human")                 # test_y.py not granted
        self.assertEqual(trail["reason"], "test_tampering_flag_human")
        self.assertEqual([t["file"] for t in trail["test_tampering"]], ["tests/test_y.py"])
        self.assertEqual([t["file"] for t in trail["authorized_test_edits"]], ["tests/test_x.py"])

    def test_no_grant_is_unchanged(self):
        trail = _ladder([{"verdict": "pass", "findings": []}], diff=_TEST_EDIT_DIFF,
                        tamper_guard=True)
        self.assertEqual(trail["verdict"], "bail_human")                 # legacy behavior intact


class KeepGreenOnReverifyRedTests(unittest.TestCase):
    """Test that re-verification failure retains the best GREEN checkpoint instead of bailing."""

    def test_broken_fix_restores_green_and_flags(self):
        snap = _Snapshotter()
        trail = _ladder(
            [_cr(2)],                                # initial critique -> fix round
            reverify_seq=[{"passed": False}],        # the fix BREAKS the verifier
            checkpoint=snap.checkpoint, restore=snap.restore,
            stall_on_no_progress=True)
        self.assertEqual(trail["verdict"], "bail_human")
        self.assertEqual(trail["reason"], "fix_broke_verifier_kept_green")
        self.assertEqual(snap.restored, ["ckpt0"])   # round 0 = the pre-adversarial verified state
        self.assertEqual(trail["restored_to_best"]["best_round"], 0)

    def test_no_hooks_keeps_legacy_reverify_red(self):
        trail = _ladder([_cr(2)], reverify_seq=[{"passed": False}], stall_on_no_progress=True)
        self.assertEqual(trail["verdict"], "reverify_red")               # caller reverts, as before
        self.assertEqual(trail["reason"], "fix_broke_verifier")

    def test_failed_restore_falls_back_to_reverify_red(self):
        snap = _Snapshotter()

        def bad_restore(cid):
            raise RuntimeError("restore unavailable")
        trail = _ladder([_cr(2)], reverify_seq=[{"passed": False}],
                        checkpoint=snap.checkpoint, restore=bad_restore,
                        stall_on_no_progress=True)
        self.assertEqual(trail["verdict"], "reverify_red")               # safe fallback: revert path
        self.assertIn("restore_error", trail)


class DisputeSeverityTests(unittest.TestCase):

    def test_high_offered_and_upheld_when_widened(self):
        seen = []

        def dispute(tier, findings):
            seen.extend(findings)
            return {"accepted": [{"finding": f, "rationale": "hallucinated"} for f in findings],
                    "rejected": []}
        trail = _ladder(
            [{"verdict": "changes_requested",
              "findings": [{"severity": "critical", "explanation": "phantom regression"}]}],
            dispute=dispute, stall_on_no_progress=True,
            dispute_severities=("low", "medium", "high", "critical"))
        self.assertEqual(len(seen), 1)                        # the critical WAS offered
        self.assertEqual(trail["verdict"], "pass")            # upheld -> argued away honestly
        self.assertEqual(trail["disputes"][0]["upheld"][0]["severity"], "critical")

    def test_rejected_high_dispute_still_blocks(self):
        def dispute(tier, findings):
            return {"accepted": [], "rejected": [{"finding": findings[0], "rationale": "weak"}]}
        trail = _ladder([_cr(1), _cr(1)], reverify_seq=[{"passed": True}], dispute=dispute,
                        stall_on_no_progress=True, stall_window=1,
                        dispute_severities=("low", "medium", "high", "critical"))
        self.assertEqual(trail["verdict"], "bail_human")      # quorum said no -> finding stands


def _anchored_critique(severity, evidence, explanation="claimed defect"):
    return {"verdict": "changes_requested",
            "findings": [{"severity": severity, "explanation": explanation,
                          **({"evidence": evidence} if evidence is not None else {})}]}


class EvidenceAnchorFnTests(unittest.TestCase):
    """apply_evidence_anchor: the pure quote-verification gate."""

    CORPUS = "def f(x):\n    return x + 1   # increment\n"

    def test_verbatim_quote_keeps_severity(self):
        c = _anchored_critique("critical", ["return x + 1"])
        out = adversary.apply_evidence_anchor(c, self.CORPUS)
        self.assertEqual(out["findings"][0]["severity"], "critical")
        self.assertNotIn("unanchored", out["findings"][0])

    def test_fabricated_quote_downgrades_and_tags(self):
        c = _anchored_critique("critical", ["recritique() is called unconditionally"])
        out = adversary.apply_evidence_anchor(c, self.CORPUS)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "medium")
        self.assertEqual(f["claimed_severity"], "critical")
        self.assertTrue(f["unanchored"])

    def test_missing_evidence_downgrades(self):
        out = adversary.apply_evidence_anchor(_anchored_critique("high", None), self.CORPUS)
        self.assertEqual(out["findings"][0]["severity"], "medium")

    def test_whitespace_normalized_match(self):
        # quote reflowed across lines/indentation still matches the source
        c = _anchored_critique("high", ["def f(x): return x + 1"])
        out = adversary.apply_evidence_anchor(c, self.CORPUS)
        self.assertEqual(out["findings"][0]["severity"], "high")

    def test_too_short_quote_downgrades(self):
        out = adversary.apply_evidence_anchor(_anchored_critique("critical", ["x + 1"]), self.CORPUS)
        self.assertEqual(out["findings"][0]["severity"], "medium")

    def test_string_evidence_accepted(self):
        c = _anchored_critique("high", "return x + 1")
        out = adversary.apply_evidence_anchor(c, self.CORPUS)
        self.assertEqual(out["findings"][0]["severity"], "high")

    def test_medium_low_pass_through_unchecked(self):
        c = _anchored_critique("medium", None)
        out = adversary.apply_evidence_anchor(c, self.CORPUS)
        self.assertEqual(out["findings"][0]["severity"], "medium")
        self.assertNotIn("unanchored", out["findings"][0])

    def test_empty_corpus_is_noop(self):
        c = _anchored_critique("critical", None)
        self.assertIs(adversary.apply_evidence_anchor(c, ""), c)


class OmissionExemptionTests(unittest.TestCase):
    """Omission findings cite a spec requirement absent from the diff.
    The cited symbol must exist in objective/acceptance criteria
    and be verifiably missing from the bare diff."""

    DIFF = "+++ b/overnight_worker.py\n+def _agg(one):\n+    return one\n"
    SPEC = ("_dispatch_lead", "app.py")

    def _omission(self, severity="high",
                  explanation="acceptance requires wiring _dispatch_lead but the diff never touches it"):
        return _anchored_critique(severity, None, explanation=explanation)

    def test_spec_grounded_omission_keeps_severity(self):
        out = adversary.apply_evidence_anchor(self._omission(), self.DIFF,
                                              spec_symbols=self.SPEC, spec_corpus=self.DIFF)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "high")
        self.assertTrue(f["omission"])
        self.assertNotIn("unanchored", f)

    def test_symbol_present_in_diff_denies_exemption(self):
        # The "omitted" symbol IS in the diff -> the omission claim is false -> downgrade as before.
        diff = self.DIFF + "+++ b/app.py\n+    _dispatch_lead(q)\n"
        out = adversary.apply_evidence_anchor(self._omission(), diff,
                                              spec_symbols=self.SPEC, spec_corpus=diff)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "medium")
        self.assertTrue(f["unanchored"])

    def test_comment_mention_does_not_deny_exemption(self):
        # Omission judgment relies on comment-stripped diff; symbols in comments only do not satisfy requirements.
        diff = self.DIFF + "+++ b/app.py\n+    # _dispatch_lead wiring TBD\n"
        out = adversary.apply_evidence_anchor(self._omission(), diff,
                                              spec_symbols=self.SPEC, spec_corpus=diff)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "high")
        self.assertTrue(f["omission"])

    def test_symbol_not_in_spec_denies_exemption(self):
        # Critic invents a requirement the spec never made -> no exemption.
        out = adversary.apply_evidence_anchor(
            self._omission(explanation="the diff never wires fabricated_symbol"), self.DIFF,
            spec_symbols=self.SPEC, spec_corpus=self.DIFF)
        self.assertEqual(out["findings"][0]["severity"], "medium")

    def test_no_spec_symbols_is_plain_anchor(self):
        out = adversary.apply_evidence_anchor(self._omission(), self.DIFF)
        self.assertEqual(out["findings"][0]["severity"], "medium")   # pre-B2 behavior unchanged

    def test_anchored_finding_untouched_by_exemption(self):
        c = _anchored_critique("critical", ["def _agg(one):"])
        out = adversary.apply_evidence_anchor(c, self.DIFF,
                                              spec_symbols=self.SPEC, spec_corpus=self.DIFF)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "critical")
        self.assertNotIn("omission", f)

    def test_ladder_exempts_against_bare_diff_not_widened_corpus(self):
        # The review packet (anchor_corpus widening) may legitimately CONTAIN the omitted symbol
        # (a call-site line) — that must not defeat a true omission finding. run_ladder passes the
        # BARE diff as spec_corpus.
        omission = {"verdict": "changes_requested",
                    "findings": [{"severity": "high",
                                  "explanation": "spec requires wiring _dispatch_lead; "
                                                 "the diff never touches it"}]}
        trail = _ladder([omission, {"verdict": "pass", "findings": []}],
                        reverify_seq=[{"passed": True}],
                        diff="+def _agg(one):\n+    return one\n",
                        evidence_anchor=True,
                        spec_symbols=("_dispatch_lead",),
                        anchor_corpus=lambda d: d + "\napp.py:2131: _dispatch_lead(q)")
        self.assertEqual(trail["verdict"], "fixed")           # the finding BLOCKED and forced a fix
        logged = trail["findings_log"][0]["findings"][0]
        self.assertEqual(logged["severity"], "high")          # never downgraded
        self.assertTrue(logged.get("omission"))               # tagged for the human reading the log


class SeverityGroundingFnTests(unittest.TestCase):
    """apply_severity_grounding: a critical/high must NAME the criterion/invariant it violates."""

    def test_grounded_critical_kept(self):
        c = {"findings": [{"severity": "critical", "explanation": "boom",
                           "violated": "acceptance: must reject a stale workspace"}]}
        out = adversary.apply_severity_grounding(c)
        self.assertEqual(out["findings"][0]["severity"], "critical")
        self.assertNotIn("ungrounded", out["findings"][0])

    def test_ungrounded_critical_downgrades_and_tags(self):
        c = {"findings": [{"severity": "critical", "explanation": "this is ugly"}]}
        out = adversary.apply_severity_grounding(c)
        f = out["findings"][0]
        self.assertEqual(f["severity"], "medium")
        self.assertEqual(f["claimed_severity"], "critical")
        self.assertTrue(f["ungrounded"])

    def test_empty_violated_downgrades(self):
        c = {"findings": [{"severity": "high", "explanation": "x", "violated": "   "}]}
        self.assertEqual(adversary.apply_severity_grounding(c)["findings"][0]["severity"], "medium")

    def test_medium_low_pass_through(self):
        c = {"findings": [{"severity": "medium", "explanation": "nit"}]}
        out = adversary.apply_severity_grounding(c)
        self.assertEqual(out["findings"][0]["severity"], "medium")
        self.assertNotIn("ungrounded", out["findings"][0])

    def test_empty_severities_is_noop(self):
        c = {"findings": [{"severity": "critical", "explanation": "x"}]}
        self.assertIs(adversary.apply_severity_grounding(c, severities=()), c)

    def test_ladder_downgrades_ungrounded_critical_to_advisory(self):
        # An ungrounded critical in the initial critique, with materiality on critical/high, must be
        # downgraded to advisory so the item converges to a pass instead of churning on it.
        trail = _ladder(
            [{"verdict": "changes_requested",
              "findings": [{"severity": "critical", "explanation": "no rule cited"}]}],
            severity_grounding=True, materiality_severities=("critical", "high"), max_rounds=1)
        self.assertEqual(trail["verdict"], "pass")   # the lone ungrounded critical became advisory

    def test_ladder_keeps_grounded_critical_actionable(self):
        # A grounded critical stays critical -> the ladder enters the fix loop for it.
        fixes = []
        trail = _ladder(
            [{"verdict": "changes_requested",
              "findings": [{"severity": "critical", "explanation": "real", "violated": "acceptance X"}]},
             {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}], fix_calls=fixes,
            severity_grounding=True, materiality_severities=("critical", "high"), max_rounds=3)
        self.assertTrue(fixes)                        # a fix round ran for the grounded critical
        self.assertEqual(trail["verdict"], "fixed")


class EvidenceAnchorLadderTests(unittest.TestCase):
    """run_ladder(evidence_anchor=True): unanchored critical/high are advisory under materiality —
    a hallucinated regression can no longer block convergence — and stay visible/tagged in the log."""

    DIFF = "+    n = recritique()\n+    rec[\"findings_after\"] = n\n"

    def test_unanchored_critical_converges_as_advisory(self):
        c = _anchored_critique("critical", ["code that is not in the diff at all"])
        trail = _ladder([c], diff=self.DIFF, stall_on_no_progress=True,
                        evidence_anchor=True, materiality_severities=("critical", "high"))
        self.assertEqual(trail["verdict"], "pass")            # phantom critical -> advisory -> converged
        logged = trail["findings_log"][0]["findings"][0]
        self.assertEqual(logged["severity"], "medium")        # downgraded…
        self.assertEqual(logged["claimed_severity"], "critical")   # …but the claim stays visible
        self.assertTrue(logged["unanchored"])

    def test_anchored_critical_still_blocks(self):
        c = _anchored_critique("critical", ['rec["findings_after"] = n'])
        trail = _ladder([c, c], diff=self.DIFF, reverify_seq=[{"passed": True}],
                        stall_on_no_progress=True, stall_window=1,
                        evidence_anchor=True, materiality_severities=("critical", "high"))
        self.assertEqual(trail["verdict"], "bail_human")      # real quote -> finding counts -> stall path

    def test_anchor_off_is_unchanged(self):
        c = _anchored_critique("critical", ["not in the diff"])
        trail = _ladder([c, c], diff=self.DIFF, reverify_seq=[{"passed": True}],
                        stall_on_no_progress=True, stall_window=1,
                        evidence_anchor=False, materiality_severities=("critical", "high"))
        self.assertEqual(trail["verdict"], "bail_human")

    def test_unanchored_does_not_trip_recurrence_guard(self):
        # the same phantom critical re-raised across critiques must not trigger the
        # anti-rationalization bail once downgraded (it is no longer critical/high in the log)
        c = _anchored_critique("critical", ["fabricated quote about the code"])
        trail = _ladder([c], diff=self.DIFF, stall_on_no_progress=True,
                        evidence_anchor=True, materiality_severities=("critical", "high"),
                        critical_recur_limit=1)
        self.assertEqual(trail["verdict"], "pass")
        # converged at the initial critique -> the recurrence guard never counted a critical/high
        self.assertFalse(trail.get("max_critical_recurrence"))


class TimingsTests(unittest.TestCase):
    """Phase 6B-3: timings dict records wall-clock seconds per stage."""

    def test_full_ladder_has_critique_and_fix_keys(self):
        # A ladder that runs critique -> fix -> re-critique should have critique + fix timings
        fixes = []
        trail = _ladder(
            [{"verdict": "changes_requested", "findings": [{"severity": "high"}]},
             {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}], fix_calls=fixes)
        timings = trail.get("timings")
        self.assertIsInstance(timings, dict)
        # KEY PRESENCE is the contract (the stage was timed). The value is a monotonic delta that can be
        # exactly 0.0 on a coarse-resolution clock (Windows) for a near-instant mocked stage -> >= 0.0.
        self.assertIn("critique", timings)
        self.assertGreaterEqual(timings["critique"], 0.0)
        self.assertIn("fix", timings)
        self.assertGreaterEqual(timings["fix"], 0.0)

    def test_zero_fix_round_has_no_fix_key(self):
        # A clean pass with no fix rounds should NOT have a fix timing
        trail = _ladder([{"verdict": "pass", "findings": []}])
        timings = trail.get("timings")
        self.assertIsInstance(timings, dict)
        self.assertIn("critique", timings)
        self.assertNotIn("fix", timings)

    def test_multiple_fix_rounds_accumulate(self):
        # Two fix rounds should both contribute to the fix timing
        fixes = []
        trail = _ladder(
            [{"verdict": "changes_requested", "findings": [{"severity": "high"}]},
             {"verdict": "changes_requested", "findings": [{"severity": "high"}]},
             {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}, {"passed": True}],
            fix_calls=fixes, max_rounds=3)
        timings = trail.get("timings")
        self.assertIn("fix", timings)
        self.assertGreaterEqual(timings["fix"], 0.0)   # >= 0: monotonic delta may be 0.0 on a coarse clock

    def test_fix_timing_grows_with_more_rounds(self):
        fixes_2 = []
        trail_2 = _ladder(
            [{"verdict": "changes_requested", "findings": [{"severity": "high"}]},
             {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}],
            fix_calls=fixes_2, max_rounds=3)

        fixes_3 = []
        trail_3 = _ladder(
            [{"verdict": "changes_requested", "findings": [{"severity": "high"}]},
             {"verdict": "changes_requested", "findings": [{"severity": "high"}]},
             {"verdict": "pass", "findings": []}],
            reverify_seq=[{"passed": True}, {"passed": True}],
            fix_calls=fixes_3, max_rounds=3)

        self.assertEqual(len(fixes_2), 1)
        self.assertEqual(len(fixes_3), 2)      # more rounds ran more fixes — the real invariant
        self.assertGreaterEqual(trail_2["timings"]["fix"], 0.0)
        self.assertGreaterEqual(trail_3["timings"]["fix"], 0.0)

    def test_dispute_timing_present_when_hook_used(self):
        disputes_handled = []
        def dispute(tier, findings):
            disputes_handled.append(findings)
            # Accept all low findings so they get ledgered away → no fix rounds needed
            return {"accepted": [{"finding": f, "rationale": "nit"} for f in findings]}
        trail = _ladder(
            [{"verdict": "changes_requested",
              "findings": [{"severity": "low", "explanation": "x"}]}],
            reverify_seq=[], dispute=dispute, max_rounds=1)
        timings = trail.get("timings")
        self.assertIn("dispute", timings)
        # All findings upheld → verdict becomes pass
        self.assertEqual(trail["verdict"], "pass")


class EscalateOnSignalTests(unittest.TestCase):
    """D1 escalate-on-signal: a REDUCED-tier run whose initial critique surfaces a MATERIAL critical/high
    promotes to the richer critic pool for the fix + re-critique loop — so a real defect never gets fewer
    eyes than a high-risk item would. This is the guardrail that makes low-tiering safe."""

    FLOOR = AdversaryTier("floor", "m27", 120, False)                              # 1 critic
    TRIO = AdversaryTier("trio", "m27", 360, True, models=["m27", "m35", "m30c"])  # 3 critics

    def _run(self, critic_seq, *, escalate_tiers, tiers=None, reverify_seq=None, max_rounds=3, seen=None):
        critiques = iter(critic_seq)
        reverifies = iter(reverify_seq or [{"passed": True}])

        def critic(tier, _diff):
            if seen is not None:
                seen.append(tier.name)
            return next(critiques)

        def anchor_and_fix(findings, budget):
            return {"fixed": len(findings)}

        def reverify():
            return next(reverifies)

        return run_ladder(
            item={"id": "a"}, budget=_Budget(10000), tiers=tiers or [self.FLOOR],
            critic=critic, anchor_and_fix=anchor_and_fix, reverify=reverify,
            get_diff=lambda: "diff", fix_reserve=180, margin=60, max_rounds=max_rounds,
            stall_on_no_progress=True, stall_window=1,
            escalate_tiers=escalate_tiers, escalate_severities=("critical", "high"))

    def test_critical_on_reduced_tier_escalates_to_richer_pool(self):
        seen = []
        trail = self._run(
            [{"verdict": "changes_requested", "findings": [{"severity": "critical", "explanation": "boom"}]},
             {"verdict": "pass", "findings": []}],
            escalate_tiers=[self.FLOOR, self.TRIO], reverify_seq=[{"passed": True}], seen=seen)
        self.assertIn("escalated", trail)
        self.assertEqual(trail["escalated"]["from"], "floor")
        self.assertEqual(trail["escalated"]["to"], "trio")
        self.assertIn("critical", trail["escalated"]["trigger"])
        self.assertIn("trio", trail["tiers_run"])
        # initial critique ran on the floor; the RE-critique ran on the escalated trio
        self.assertEqual(seen[0], "floor")
        self.assertEqual(seen[1], "trio")

    def test_no_escalation_without_escalate_tiers(self):
        trail = self._run(
            [{"verdict": "changes_requested", "findings": [{"severity": "critical"}]},
             {"verdict": "pass", "findings": []}],
            escalate_tiers=None, reverify_seq=[{"passed": True}])
        self.assertNotIn("escalated", trail)

    def test_no_escalation_when_target_not_richer(self):
        # escalate_tiers offers only the same-size floor -> nothing richer to promote to.
        trail = self._run(
            [{"verdict": "changes_requested", "findings": [{"severity": "critical"}]},
             {"verdict": "pass", "findings": []}],
            escalate_tiers=[self.FLOOR], reverify_seq=[{"passed": True}])
        self.assertNotIn("escalated", trail)

    def test_no_escalation_on_clean_pass(self):
        trail = self._run([{"verdict": "pass", "findings": []}],
                          escalate_tiers=[self.FLOOR, self.TRIO])
        self.assertNotIn("escalated", trail)

    def test_no_escalation_on_non_material_finding(self):
        # A medium finding is actionable (fix loop runs) but NOT in escalate_severities -> no promotion.
        trail = self._run(
            [{"verdict": "changes_requested", "findings": [{"severity": "medium", "explanation": "nit"}]},
             {"verdict": "pass", "findings": []}],
            escalate_tiers=[self.FLOOR, self.TRIO], reverify_seq=[{"passed": True}])
        self.assertNotIn("escalated", trail)


if __name__ == "__main__":
    unittest.main()
