"""a1_validity: one void/valid verdict for both A1 paths, fed REAL row shapes copied from delivered runs."""
import hashlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import a1_validity as V  # noqa: E402

RIGBY_COPY = r"E:\SSDShared\tabfoundry-jobs\payloads\a1\a1_validity.py"

# rigby R1 (gate-rev-fe-gt730, 10-08), wordy and react: both ended on the engine's HTTP 400
RIGBY_400 = {"task": "wordy", "ok": False, "ended": "error", "finish_reason": "stop", "cap_hits": 0, "turns": 47,
             "error": "loop error: HTTPError: HTTP Error 400: Bad Request\n^^^^^^\n  File ..."}
RIGBY_RUNAWAY = {"task": "react", "ok": False, "ended": "error", "finish_reason": "length", "cap_hits": 1, "turns": 32,
                 "error": "loop error: HTTPError: HTTP Error 400: Bad Request"}
RIGBY_TOOLCALL = {"task": "bowling", "ok": False, "ended": "error", "finish_reason": "stop", "cap_hits": 0, "turns": 20,
                  "error": "loop error: ValidationError: 1 validation error for ToolCall"}
# rigby R-team, react: a real fail (the remove-twice hidden test), the loop ended on the model's own finish
RIGBY_FAIL = {"task": "react", "ok": False, "ended": "finish", "finish_reason": "stop", "cap_hits": 0, "turns": 29,
              "error": None}
# Mothership 10-08 baseline: wordy pass, react fail, both finished cleanly
GPU_PASS = {"exercise": "wordy", "passed": True, "ended": "finish", "turns": 6, "capped_turns": []}
GPU_FAIL = {"exercise": "react", "passed": False, "ended": "finish", "turns": 14, "capped_turns": []}
GPU_LOOP_CUT = {"exercise": "sgf_parsing", "passed": True, "ended": "finish", "turns": 65, "capped_turns": ["loop"]}


class Verdict(unittest.TestCase):
    def test_every_crash_class_seen_on_rigby_is_void(self):
        for row in (RIGBY_400, RIGBY_RUNAWAY, RIGBY_TOOLCALL):
            self.assertIsNotNone(V.verdict(row), row["task"])
        self.assertIn("HTTP Error 400", V.verdict(RIGBY_400))
        self.assertIn("ToolCall", V.verdict(RIGBY_TOOLCALL))

    def test_a_real_fail_and_a_real_pass_are_valid(self):
        for row in (RIGBY_FAIL, GPU_PASS, GPU_FAIL):
            self.assertIsNone(V.verdict(row), row)

    def test_a_loop_nudge_is_not_a_cap(self):
        # the loop cutter nudged sgf-parsing and it went on to finish: progress, not a limit
        self.assertIsNone(V.verdict(GPU_LOOP_CUT))

    def test_a_length_cut_turn_is_void(self):
        self.assertIn("limit", V.verdict(dict(GPU_PASS, capped_turns=["length"])))

    def test_a_runaway_that_filled_the_models_whole_context_is_scored_not_void(self):
        """the owner: hitting its own ceiling where the champion succeeded is not a sandbag. Ornith's react:
        13,082 prompt + 249,062 generated = its tested 262,144."""
        self.assertIsNone(V.verdict(dict(GPU_FAIL, capped_turns=["length"], length_at_ceiling=1)))
        self.assertIn("limit", V.verdict(dict(GPU_FAIL, capped_turns=["length", "length"], length_at_ceiling=1)),
                      "a second cut BELOW the ceiling is still a limit we imposed")
        self.assertIn("limit", V.verdict(dict(GPU_FAIL, capped_turns=["length"], length_at_ceiling=0)))

    def test_at_ceiling_counts_only_length_cuts_that_filled_the_tested_context(self):
        """One derivation for the live order and the reducer. Ornith's real cut calls fill 262,144
        exactly; a cut at 200k is a limit we imposed; a `stop` turn with huge counts is no cut at all."""
        turns = [{"done_reason": "length", "prompt_eval_count": 22226, "eval_count": 239918},   # dot-dsl, = ceiling
                 {"done_reason": "length", "prompt_eval_count": 13082, "eval_count": 249062},   # react, = ceiling
                 {"done_reason": "length", "prompt_eval_count": 10000, "eval_count": 190000},   # below: a limit of ours
                 {"done_reason": "stop", "prompt_eval_count": 200000, "eval_count": 70000}]
        self.assertEqual(V.at_ceiling(turns, 262144), 2)
        # the 3.8's real cut turns at 122,880: 1-4 tokens short of num_ctx, and
        # all 20 of them were the context being full - a few tokens of slack, never a cut below the ceiling
        q38 = [{"done_reason": "length", "prompt_eval_count": 108400, "eval_count": 14479},   # 122,879
               {"done_reason": "length", "prompt_eval_count": 122855, "eval_count": 21},      # 122,876
               {"done_reason": "length", "prompt_eval_count": 121948, "eval_count": 931},     # 122,879
               {"done_reason": "length", "prompt_eval_count": 100000, "eval_count": 22000}]   # 122,000: ours
        self.assertEqual(V.at_ceiling(q38, 122880), 3)
        self.assertEqual(V.at_ceiling(turns, 0), 0, "no tested ceiling known: nothing can be at it")
        self.assertEqual(V.at_ceiling([], 262144), 0)
        self.assertEqual(V.at_ceiling([{"done_reason": "length"}], 262144), 0, "missing counts never read as a fill")

    def test_rigby_cap_hits_at_its_tested_max_are_scored_and_below_it_void(self):
        at_max = dict(RIGBY_FAIL, finish_reason="length", cap_hits=1, length_at_ceiling=1)
        self.assertIsNone(V.verdict(at_max))
        self.assertIn("1 cap hit", V.verdict(dict(at_max, cap_hits=2)), "one hit beyond what the ceiling explains")
        self.assertIsNotNone(V.verdict(dict(at_max, length_at_ceiling=0)))
        self.assertIsNotNone(V.verdict(dict(RIGBY_FAIL, finish_reason="priced_out")))

    def test_no_turns_is_void(self):
        self.assertIsNotNone(V.verdict(dict(GPU_PASS, turns=0, tool_counts={}, model_calls=0)))

    def test_a_solve_that_finishes_in_its_first_reply_is_scored(self):
        """Softserve qwen3:8b wordy: patch + test + finish in ONE reply reads turns 0 (a turn counts once it
        completes) - it was voided as 'never took a turn'. Acting is a tool call or a returned model call."""
        one_shot = dict(GPU_FAIL, turns=0, model_calls=1, tool_counts={"coding_patch": 1, "coding_test": 1, "finish": 1})
        self.assertIsNone(V.verdict(one_shot))
        self.assertIsNone(V.verdict(dict(RIGBY_FAIL, turns=0, tool_counts={"finish": 1})), "rigby rows carry no model_calls")

    def test_a_runaway_guard_cut_is_scored_not_void(self):
        """the guard's turn cap sits above every acting turn ever measured on rigby (14,888 tokens) and below
        every runaway (25,447+), so a cut there answers for the model. GPU rows record it as done_reason "runaway",
        rigby rows keep it out of cap_hits; an exercise ended by a second cut is graded."""
        self.assertIsNone(V.verdict(dict(GPU_FAIL, capped_turns=[], ended="runaway")))
        self.assertIsNone(V.verdict(dict(RIGBY_FAIL, ended="runaway", runaway_cuts=2)))
        self.assertIn("limit", V.verdict(dict(GPU_FAIL, capped_turns=["length"], ended="runaway")),
                      "a context cut beside a guard cut is still a limit")

    def test_a_review_that_never_ran_is_void(self):
        """B5: all 10 reviews came back as this payload (BigHonker could not load the reviewer) while the
        order read result ok / errors 0. A review arm measures the review, so the row is void; a clean or a findings
        review is scored."""
        failed = {"findings_chars": 39, "clean": False, "error": True, "wall_s": 24.5,
                  "findings": "ERROR: the helper order ended failed ()", "repaired": False}
        self.assertIsNone(V.verdict(GPU_FAIL), "the base fixture must be valid, or this proves nothing")
        self.assertIn("review helper error", V.verdict(dict(GPU_FAIL, review=failed)))
        self.assertIsNone(V.verdict(dict(GPU_FAIL, review=dict(failed, error=False, findings="looks fine", clean=True))))
        self.assertIsNone(V.verdict(dict(GPU_FAIL, review=None)), "a non-review arm has no review to fail")


class RunFlags(unittest.TestCase):
    def test_harness_changed_mid_run_is_flagged(self):
        rows = [dict(RIGBY_FAIL, harness_sha256="321b1d71aa"), dict(RIGBY_FAIL, harness_sha256="cee5ef60bb")]
        self.assertTrue(any("harness changed" in f for f in V.run_flags(rows)))

    def test_a_directed_arm_that_never_delegates_is_flagged_after_three(self):
        team = {"mode": "directed"}
        rows = [dict(RIGBY_FAIL, team=team, delegations=[]) for _ in range(3)]
        self.assertTrue(any("delegated nothing" in f for f in V.run_flags(rows)))
        rows[1]["delegations"] = [{"k": 1}]
        self.assertEqual(V.run_flags(rows), [])

    def test_a_clean_run_has_no_flags(self):
        self.assertEqual(V.run_flags([dict(GPU_PASS, harness_sha256="cee5ef60")] * 4), [])

    def test_lead_alone_is_the_one_derivation_for_the_flag_and_the_stop(self):
        """:
        the arm stops on the same test the ALERT uses. Organic arms measure the choice; void rows are the instrument."""
        three = [dict(RIGBY_FAIL, delegations=[]) for _ in range(3)]
        for mode in ("directed", "mandatory"):
            self.assertTrue(V.lead_alone(three, {"mode": mode}), mode)
        for team in ({"mode": "organic"}, {}, None):
            self.assertFalse(V.lead_alone(three, team), team)
        self.assertFalse(V.lead_alone(three[:2], {"mode": "directed"}), "two is not yet three")
        voided = [dict(RIGBY_FAIL, delegations=[], void="engine/harness error")] + three[:2]
        self.assertFalse(V.lead_alone(voided, {"mode": "directed"}), "a void row is not the lead's choice")


class OneDerivation(unittest.TestCase):
    def test_rigby_carries_the_same_bytes(self):
        self.assertTrue(os.path.exists(RIGBY_COPY), "rigby's copy is missing - cannot prove the paths agree")
        a = hashlib.sha256(open(os.path.join(HERE, "a1_validity.py"), "rb").read()).hexdigest()
        b = hashlib.sha256(open(RIGBY_COPY, "rb").read()).hexdigest()
        self.assertEqual(a, b, "rigby's a1_validity drifted from the GPU path's")


if __name__ == "__main__":
    unittest.main()
