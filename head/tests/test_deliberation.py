import json
import unittest
from unittest.mock import patch

import deliberation


class DeliberationTests(unittest.TestCase):
    def test_effort_state_enforces_pass_limit(self):
        state = deliberation.EffortState("think_hard", 300, 2)
        state.record("first_pass", "draft")
        state.record("critique", "review")
        self.assertFalse(state.can_start_pass())
        with self.assertRaises(deliberation.DeliberationError):
            state.record("refine", "too many")

    def test_effort_state_reports_budget(self):
        state = deliberation.EffortState("think_hard", 300, 3)
        state.started_monotonic = 100.0
        with patch("deliberation.time.monotonic", return_value=145.0):
            self.assertEqual(state.elapsed_seconds, 45)
            self.assertEqual(state.remaining_seconds, 255)

    def test_parse_critique_normalizes_fail(self):
        result = deliberation.parse_critique(json.dumps({
            "verdict": "fail",
            "summary": "A bug remains.",
            "findings": [{
                "severity": "high",
                "description": "Zero is not handled.",
                "recommendation": "Add a guard.",
            }],
        }))
        self.assertEqual(result["verdict"], "changes_requested")
        self.assertEqual(result["findings"][0]["recommended_action"], "Add a guard.")

    def test_parse_critique_keeps_evidence_quotes(self):
        result = deliberation.parse_critique(json.dumps({
            "verdict": "changes_requested",
            "findings": [
                {"severity": "critical", "explanation": "regression", "evidence": "if x is None: return"},
                {"severity": "high", "explanation": "leak", "evidence": ["conn = open()", "  ", 7]},
                {"severity": "low", "explanation": "style"},
            ],
        }))
        self.assertEqual(result["findings"][0]["evidence"], ["if x is None: return"])
        self.assertEqual(result["findings"][1]["evidence"], ["conn = open()"])   # blanks/non-str dropped
        self.assertNotIn("evidence", result["findings"][2])                      # absent stays absent

    def test_parse_critique_keeps_violated_grounding(self):
        # The severity-grounding field ("violated"/"criterion"/"requirement") survives normalization so
        # the ladder's grounding gate can read it; absent/blank stays absent.
        result = deliberation.parse_critique(json.dumps({
            "verdict": "changes_requested",
            "findings": [
                {"severity": "critical", "explanation": "x", "violated": "acceptance: rejects stale"},
                {"severity": "high", "explanation": "y", "criterion": "invariant: identity pinning"},
                {"severity": "high", "explanation": "z", "violated": "   "},
                {"severity": "medium", "explanation": "w"},
            ],
        }))
        self.assertEqual(result["findings"][0]["violated"], "acceptance: rejects stale")
        self.assertEqual(result["findings"][1]["violated"], "invariant: identity pinning")  # alias
        self.assertNotIn("violated", result["findings"][2])                                 # blank dropped
        self.assertNotIn("violated", result["findings"][3])                                 # absent stays absent

    def test_deep_trace_compacts_to_budget(self):
        state = deliberation.EffortState("think_hard", 300, 3)
        state.record("first_pass", "x" * 5_000, log="y" * 5_000)
        packet = deliberation.build_deep_trace(
            objective="Do the task",
            state=state,
            final_answer="z" * 5_000,
            max_chars=2_500,
        )
        self.assertTrue(packet["compacted"])
        self.assertLess(len(packet["final_answer"]), 2_000)

    def test_external_choice_requires_explicit_mode(self):
        result = deliberation.external_choice("claude")
        self.assertTrue(result["choice_required"])
        self.assertEqual(len(result["choices"]), 4)
        selected = deliberation.external_choice("codex", "background_review")
        self.assertEqual(selected["execution_authority"], "review_only")


class ExtractJsonTests(unittest.TestCase):
    """The shared robust extractor + parse_critique tolerance for reasoning-model wrapping."""

    def test_plain_json(self):
        self.assertEqual(deliberation.extract_json('{"a": 1}'), {"a": 1})

    def test_strips_think_block(self):
        self.assertEqual(deliberation.extract_json('<think>hmm let me see</think>\n{"a": 1}'), {"a": 1})

    def test_unclosed_think_then_json_before_it(self):
        self.assertEqual(deliberation.extract_json('{"a": 1}\n<think>still thinking...'), {"a": 1})

    def test_fenced_block_in_prose(self):
        self.assertEqual(deliberation.extract_json('Sure:\n```json\n{"a": 1}\n```\ndone'), {"a": 1})

    def test_embedded_object_in_prose(self):
        self.assertEqual(deliberation.extract_json('Here you go: {"a": 1} hope that helps'), {"a": 1})

    def test_raises_on_no_json(self):
        with self.assertRaises(ValueError):
            deliberation.extract_json("no json at all here")

    def test_parse_critique_survives_think_wrapping(self):
        # the EXACT failure mode that killed the qwen3.6:35b-a3b decorrelated critic tier
        raw = ('<think>The diff has a race condition and a missing guard. Let me write it up.</think>\n'
               '{"verdict": "changes_requested", "findings": '
               '[{"severity": "high", "explanation": "race condition on shared state"}]}')
        result = deliberation.parse_critique(raw)
        self.assertEqual(result["verdict"], "changes_requested")
        self.assertEqual(result["findings"][0]["severity"], "high")

    def test_parse_critique_embedded_in_prose(self):
        raw = 'My review:\n```json\n{"verdict": "pass", "findings": []}\n```'
        self.assertEqual(deliberation.parse_critique(raw)["verdict"], "pass")

    def test_parse_critique_bare_findings_array(self):
        # the deepseek-r1:70b failure: a top-level findings ARRAY instead of the {verdict,findings} object
        raw = '[{"severity": "high", "explanation": "lock-ordering deadlock"}]'
        result = deliberation.parse_critique(raw)
        self.assertEqual(result["verdict"], "changes_requested")
        self.assertEqual(result["findings"][0]["severity"], "high")

    def test_parse_critique_empty_array_is_pass(self):
        self.assertEqual(deliberation.parse_critique("[]")["verdict"], "pass")

    def test_parse_critique_scalar_json_raises(self):
        with self.assertRaises(deliberation.DeliberationError):
            deliberation.parse_critique("42")


class MergeDecorrelatedTests(unittest.TestCase):
    def _crit(self, *explanations):
        return {"verdict": "changes_requested",
                "findings": [{"severity": "high", "explanation": e} for e in explanations]}

    def test_union_of_distinct_findings(self):
        a = self._crit("SQL injection in find_job")
        b = self._crit("lock-ordering deadlock between push_left/right")
        m = deliberation.merge_decorrelated([a, b])
        self.assertEqual(m["verdict"], "changes_requested")
        self.assertEqual(len(m["findings"]), 2)        # both kept -> higher recall

    def test_dedupes_near_identical(self):
        # two critics wording a bug the SAME way for the first 80 chars -> counted once
        prefix = "SQL injection in find_job: the name parameter is interpolated directly into the query"
        a = self._crit(prefix)
        b = self._crit(prefix + " — use parameterized queries instead")
        m = deliberation.merge_decorrelated([a, b])
        self.assertEqual(len(m["findings"]), 1)

    def test_keeps_differently_worded_same_bug(self):
        # decorrelated critics usually WORD it differently -> kept (under-dedupe = robust, more eyes)
        a = self._crit("SQL injection in find_job")
        b = self._crit("The query in find_job is exploitable via string interpolation")
        m = deliberation.merge_decorrelated([a, b])
        self.assertEqual(len(m["findings"]), 2)

    def test_empty_pool_is_pass(self):
        self.assertEqual(deliberation.merge_decorrelated([])["verdict"], "pass")
        both_clean = [{"verdict": "pass", "findings": []}, {"verdict": "pass", "findings": []}]
        self.assertEqual(deliberation.merge_decorrelated(both_clean)["verdict"], "pass")

    def test_skips_non_dict_critique(self):
        m = deliberation.merge_decorrelated([None, self._crit("real bug"), "junk"])
        self.assertEqual(len(m["findings"]), 1)

    def test_caps_findings(self):
        big = self._crit(*[f"distinct finding number {i}" for i in range(50)])
        m = deliberation.merge_decorrelated([big], max_findings=10)
        self.assertEqual(len(m["findings"]), 10)


class ConvergeTests(unittest.TestCase):
    """The SHARED convergence loop (deliberation.converge) that unifies think-hard's refinement loop and
    the overnight adversarial ladder. Pure — all model/verify/budget calls injected."""

    def _run(self, *, initial, critiques, improves=None, can=True, **kw):
        crit_iter = iter(critiques)
        imp_iter = iter(improves or [])

        def recritique():
            return next(crit_iter, None)

        def improve(prev):
            return next(imp_iter, {"accepted": True})

        can_fn = can if callable(can) else (lambda: can)
        return deliberation.converge(initial_findings=initial, can_continue=can_fn,
                                     improve=improve, recritique=recritique, **kw)

    def test_converges_when_recritique_clean(self):
        r = self._run(initial=[1, 2, 3],
                      critiques=[{"verdict": "changes_requested", "findings": [1, 2]},
                                 {"verdict": "pass", "findings": []}])
        self.assertEqual(r["stop_reason"], deliberation.CONVERGED)
        self.assertTrue(r["converged"])
        self.assertEqual(r["rounds_run"], 2)

    def test_stalls_when_findings_dont_drop(self):
        r = self._run(initial=[1, 2],
                      critiques=[{"verdict": "changes_requested", "findings": [1, 2]}])
        self.assertEqual(r["stop_reason"], deliberation.STALLED)
        self.assertFalse(r["converged"])

    def test_rejected_improvement_stops(self):
        # think-hard regression OR overnight reverify-red both surface here via accepted=False.
        r = self._run(initial=[1, 2],
                      critiques=[{"verdict": "changes_requested", "findings": [1]}],
                      improves=[{"accepted": False, "reason": "reverify_red"}])
        self.assertEqual(r["stop_reason"], "reverify_red")
        self.assertEqual(r["rounds_run"], 1)
        self.assertFalse(r["rounds"][-1]["accepted"])

    def test_recritique_unavailable_accepts(self):
        r = self._run(initial=[1, 2], critiques=[None])   # recritique returns None
        self.assertEqual(r["stop_reason"], deliberation.RECRITIQUE_UNAVAILABLE)

    def test_budget_stops_before_any_round(self):
        r = self._run(initial=[1, 2], critiques=[], can=False)
        self.assertEqual(r["stop_reason"], deliberation.BUDGET)
        self.assertEqual(r["rounds_run"], 0)

    def test_max_rounds_ceiling(self):
        crits = [{"verdict": "changes_requested", "findings": list(range(n))} for n in (5, 4, 3, 2, 1)]
        r = self._run(initial=list(range(6)), critiques=crits, max_rounds=2)
        self.assertEqual(r["stop_reason"], deliberation.MAX_ROUNDS)
        self.assertEqual(r["rounds_run"], 2)

    def test_drives_findings_to_zero_over_multiple_rounds(self):
        # The commission's acceptance shape: a scripted multi-pass run drives a seeded set of findings to zero.
        crits = [{"verdict": "changes_requested", "findings": [1, 2]},
                 {"verdict": "changes_requested", "findings": [1]},
                 {"verdict": "pass", "findings": []}]
        r = self._run(initial=[1, 2, 3], critiques=crits)
        self.assertEqual(r["stop_reason"], deliberation.CONVERGED)
        self.assertEqual(r["rounds_run"], 3)
        self.assertEqual([rec["findings"] for rec in r["rounds"]], [3, 2, 1])  # strictly dropping

    def test_on_round_called_each_round(self):
        seen = []
        self._run(initial=[1, 2], critiques=[{"verdict": "pass", "findings": []}],
                  on_round=lambda rec: seen.append(rec["round"]))
        self.assertEqual(seen, [1])


if __name__ == "__main__":
    unittest.main()
