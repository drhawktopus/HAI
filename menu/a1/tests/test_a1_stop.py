"""A1 early stop (a1_stop): end an arm once it cannot come within run-to-run noise of its baseline on solves."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import a1_stop as S  # noqa: E402

EX = ["wordy", "sgf-parsing", "react", "bowling", "paasio", "rest-api", "poker", "robot-name", "transpose", "zipper"]


def rows(passed):
    return {e: {"exercise_dir": e, "passed": p} for e, p in passed.items()}


class EarlyStop(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.rule = {"baseline": "solo", "baseline_dir": self.d, "margin": 2, "exclude": ["paasio"]}

    def baseline(self, solved_scored):
        """A delivered baseline that solved the first `solved_scored` scored exercises (paasio failed, as it does)."""
        scored = [e for e in EX if e != "paasio"]
        rs = [{"exercise_dir": e, "passed": i < solved_scored} for i, e in enumerate(scored)]
        rs.append({"exercise_dir": "paasio", "passed": False})
        with open(os.path.join(self.d, "results.json"), "w", encoding="utf-8") as f:
            json.dump({"rows": rs}, f)

    def test_stops_once_even_a_perfect_finish_stays_clearly_behind(self):
        self.baseline(9)                                     # the 3.8 solo at xhigh: 9 of 9 scored
        r = rows({"wordy": False, "sgf-parsing": False, "react": False, "bowling": False, "paasio": False,
                  "rest-api": False})                        # 0 of 5 scored, 4 left: best case 4 < 9 - 2
        v = S.decide(r, EX, self.rule)
        self.assertIsNotNone(v)
        self.assertEqual((v["best_case"], v["baseline_solved"], v["remaining"]), (4, 9, 4))
        self.assertEqual(v["not_run"], ["poker", "robot-name", "transpose", "zipper"])

    def test_inside_the_noise_it_keeps_going(self):
        self.baseline(9)
        r = rows({"wordy": False, "sgf-parsing": False, "react": True})   # best case 1 + 6 = 7, not < 7
        self.assertIsNone(S.decide(r, EX, self.rule), "7 vs 9 is within the measured spread of 2: keep running")

    def test_never_stops_for_being_better(self):
        self.baseline(3)
        r = rows({e: True for e in EX[:8]})
        self.assertIsNone(S.decide(r, EX, self.rule))

    def test_no_delivered_baseline_means_the_rule_is_off(self):
        r = rows({e: False for e in EX[:9]})
        self.assertIsNone(S.decide(r, EX, self.rule))

    def test_the_excluded_exercise_counts_on_neither_side(self):
        self.baseline(9)
        r = rows({"paasio": False})
        self.assertIsNone(S.decide(r, EX, self.rule), "a paasio miss alone decides nothing")
        self.assertEqual(S.baseline_solved(self.d, EX, ["paasio"]), 9)

    def test_two_baselines_pool_into_one(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        with open(os.path.join(self.d, "results.json"), "w", encoding="utf-8") as f:
            json.dump({"rows": [{"exercise_dir": e, "passed": True} for e in EX[:5]]}, f)
        with open(os.path.join(other, "results.json"), "w", encoding="utf-8") as f:
            json.dump({"rows": [{"exercise_dir": e, "passed": e != "zipper"} for e in EX[5:]]}, f)
        self.assertEqual(S.baseline_solved([self.d, other], EX, ["paasio"]), 8)
        self.assertIsNone(S.baseline_solved([self.d, os.path.join(other, "nope")], EX, ["paasio"]),
                          "one baseline not delivered = the rule stays off")
        rule = S.make_rule(["a", "b"], r"\\x", ["paasio"])
        self.assertEqual((rule["baseline"], len(rule["baseline_dir"])), ("a,b", 2))

    def test_a_baseline_missing_a_scored_exercise_decides_nothing(self):
        with open(os.path.join(self.d, "results.json"), "w", encoding="utf-8") as f:
            json.dump({"rows": [{"exercise_dir": "wordy", "passed": True}]}, f)
        self.assertIsNone(S.baseline_solved(self.d, EX, ["paasio"]))


if __name__ == "__main__":
    unittest.main()
