"""#2: deliberation.converge patience (stall_window) + keep-best tracking.

converge is the SHARED think-hard / overnight-ladder engine, so these lock the new behavior at the
engine level (independent of the ladder wiring): stall_window=1 is byte-identical to the original
single-round stall; a larger window tolerates a noisy re-critique blip; and the best (fewest-findings)
round is always remembered so a caller can keep that result instead of a later regression.
"""
import unittest

import deliberation


def _findings(n):
    return [{"severity": "high", "explanation": "f%d" % i} for i in range(n)]


def _recritique_seq(counts):
    """Yield critiques with the given finding counts; a count of 0 is a clean 'pass'."""
    it = iter(counts)

    def _rc():
        n = next(it)
        return {"verdict": "pass" if n == 0 else "changes_requested", "findings": _findings(n)}
    return _rc


def _run(initial, counts, *, stall_window=1, stall_stops=True, max_rounds=20):
    return deliberation.converge(
        initial_findings=_findings(initial),
        can_continue=lambda: True,
        improve=lambda prev_n: {"accepted": True},
        recritique=_recritique_seq(counts),
        max_rounds=max_rounds, stall_stops=stall_stops, stall_window=stall_window,
    )


class ConvergePatienceTests(unittest.TestCase):
    def test_window_1_stalls_on_first_non_drop_backcompat(self):
        # initial 3 -> 2 -> 2 : the original behavior bails on the first non-dropping round.
        r = _run(3, [2, 2], stall_window=1)
        self.assertEqual(r["stop_reason"], deliberation.STALLED)
        self.assertEqual(r["rounds_run"], 2)
        self.assertEqual(r["final_findings"], 2)
        self.assertEqual(r["best_findings"], 2)
        self.assertEqual(r["best_round"], 1)

    def test_window_2_rides_through_a_blip_to_convergence(self):
        # 5 -> 3 -> (blip) 6 -> 1 -> pass. window=1 would bail at the blip; window=2 converges.
        r = _run(5, [3, 6, 1, 0], stall_window=2)
        self.assertTrue(r["converged"])
        self.assertEqual(r["stop_reason"], deliberation.CONVERGED)
        self.assertEqual(r["rounds_run"], 4)
        self.assertEqual(r["best_findings"], 0)
        self.assertEqual(r["best_round"], 4)

    def test_window_2_bails_after_two_consecutive_non_improving_and_keeps_best(self):
        # 5 -> 3 -> 6 -> 7 : two non-improving rounds in a row -> stall, but best (3 @ round 1) kept.
        r = _run(5, [3, 6, 7], stall_window=2)
        self.assertEqual(r["stop_reason"], deliberation.STALLED)
        self.assertEqual(r["rounds_run"], 3)
        self.assertEqual(r["best_findings"], 3)
        self.assertEqual(r["best_round"], 1)
        self.assertEqual(r["final_findings"], 3)   # keep-best, not the 7 it bailed on

    def test_streak_resets_on_improvement(self):
        # 5 -> 4 -> 5(blip) -> 3 -> 4(blip) -> 2 -> pass : blips are isolated, streak never hits 2.
        r = _run(5, [4, 5, 3, 4, 2, 0], stall_window=2)
        self.assertTrue(r["converged"])
        self.assertEqual(r["best_findings"], 0)

    def test_stall_stops_false_never_stalls(self):
        # legacy interactive mode: no stall guard -> runs until it converges (or ceiling/budget).
        r = _run(3, [3, 3, 0], stall_window=1, stall_stops=False)
        self.assertTrue(r["converged"])
        self.assertEqual(r["stop_reason"], deliberation.CONVERGED)


if __name__ == "__main__":
    unittest.main()
