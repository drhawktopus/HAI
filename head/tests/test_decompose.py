import json
import unittest

import decompose
from decompose import (Leaf, PlanError, parse_plan, validate_plan, topo_order,
                       feedback_as_critique, emit_plan)


def _leaf(lid, deps=None, integration=False, acc="unit test passes", obj="do x",
           evidence=""):
    return Leaf(id=lid, title=lid, objective=obj, acceptance=acc,
                depends_on=deps or [], integration=integration, evidence=evidence)


def _plan_json(leaves):
    return json.dumps({"leaves": [
        {"id": lf.id, "title": lf.title, "objective": lf.objective,
         "acceptance": lf.acceptance, "evidence": lf.evidence,
         "depends_on": lf.depends_on, "integration": lf.integration}
        for lf in leaves]})


# A valid 3-leaf plan: l1, l2(dep l1), lI integration(dep l1,l2)
def _good_leaves():
    return [_leaf("l1", evidence="# snippet"),
            _leaf("l2", deps=["l1"], evidence="# snippet"),
            _leaf("lI", deps=["l1", "l2"], integration=True, acc="integration test green")]


class ParseTests(unittest.TestCase):
    def test_parse_valid(self):
        leaves = parse_plan(_plan_json(_good_leaves()))
        self.assertEqual([lf.id for lf in leaves], ["l1", "l2", "lI"])
        self.assertTrue(leaves[2].integration)

    def test_parse_fenced(self):
        leaves = parse_plan("```json\n" + _plan_json(_good_leaves()) + "\n```")
        self.assertEqual(len(leaves), 3)

    def test_parse_bad_json_raises(self):
        with self.assertRaises(PlanError):
            parse_plan("not json at all")

    def test_parse_no_leaves_raises(self):
        with self.assertRaises(PlanError):
            parse_plan(json.dumps({"leaves": []}))

    def test_parse_dedupes_ids(self):
        leaves = parse_plan(json.dumps({"leaves": [
            {"id": "l1", "acceptance": "t", "objective": "o"},
            {"id": "l1", "acceptance": "t", "objective": "o"}]}))
        self.assertEqual(len({lf.id for lf in leaves}), 2)

    def test_parse_strips_think_block(self):
        # qwen3 reasoning model wraps its JSON in a <think> block — must still parse.
        raw = "<think>let me plan this carefully...</think>\n" + _plan_json(_good_leaves())
        leaves = parse_plan(raw)
        self.assertEqual([lf.id for lf in leaves], ["l1", "l2", "lI"])

    def test_parse_unclosed_think_then_json(self):
        raw = "<think>reasoning that never closed... " + _plan_json(_good_leaves())
        # an unclosed <think> drops the trailing text too, so this is the harder case:
        # the JSON survives only if it precedes the tag.
        good = _plan_json(_good_leaves()) + "\n<think>still thinking when truncated"
        self.assertEqual(len(parse_plan(good)), 3)

    def test_parse_embedded_json_in_prose(self):
        raw = "Here is the corrected plan you asked for:\n\n" + _plan_json(_good_leaves()) + "\n\nDone."
        self.assertEqual(len(parse_plan(raw)), 3)

    def test_parse_fenced_block_inside_prose(self):
        raw = "Sure!\n```json\n" + _plan_json(_good_leaves()) + "\n```\nHope that helps."
        self.assertEqual(len(parse_plan(raw)), 3)


class ValidateTests(unittest.TestCase):
    def test_clean_plan_no_findings(self):
        self.assertEqual(validate_plan(_good_leaves()), [])

    def test_missing_acceptance(self):
        leaves = [_leaf("l1", acc=""), _leaf("lI", deps=["l1"], integration=True)]
        f = validate_plan(leaves)
        self.assertTrue(any("acceptance" in x["explanation"] for x in f))

    def test_unknown_dependency(self):
        leaves = [_leaf("l1", deps=["ghost"]), _leaf("lI", deps=["l1"], integration=True)]
        f = validate_plan(leaves)
        self.assertTrue(any("ghost" in x["explanation"] for x in f))

    def test_no_integration_leaf(self):
        leaves = [_leaf("l1"), _leaf("l2", deps=["l1"])]
        f = validate_plan(leaves)
        self.assertTrue(any("no integration leaf" in x["explanation"] for x in f))

    def test_two_integration_leaves(self):
        leaves = [_leaf("l1", integration=True), _leaf("lI", deps=["l1"], integration=True)]
        f = validate_plan(leaves)
        self.assertTrue(any("exactly one" in x["explanation"] for x in f))

    def test_integration_without_deps(self):
        leaves = [_leaf("l1"), _leaf("lI", integration=True)]
        f = validate_plan(leaves)
        self.assertTrue(any("cannot prove composition" in x["explanation"] for x in f))

    def test_cycle_is_reported(self):
        leaves = [_leaf("l1", deps=["l2"]), _leaf("l2", deps=["l1"]),
                  _leaf("lI", deps=["l1", "l2"], integration=True)]
        f = validate_plan(leaves)
        self.assertTrue(any("cycle" in x["explanation"].lower() for x in f))


class TopoTests(unittest.TestCase):
    def test_order_respects_deps(self):
        ordered = topo_order(_good_leaves())
        ids = [lf.id for lf in ordered]
        self.assertLess(ids.index("l1"), ids.index("l2"))
        self.assertEqual(ids[-1], "lI")

    def test_cycle_raises(self):
        with self.assertRaises(PlanError):
            topo_order([_leaf("a", deps=["b"]), _leaf("b", deps=["a"])])


class FeedbackTests(unittest.TestCase):
    def test_none_for_empty(self):
        self.assertIsNone(feedback_as_critique(""))
        self.assertIsNone(feedback_as_critique([]))

    def test_string_lines_become_findings(self):
        c = feedback_as_critique("split leaf 2\nadd a test for errors")
        self.assertEqual(c["verdict"], "changes_requested")
        self.assertEqual(len(c["findings"]), 2)
        self.assertEqual(c["findings"][0]["source"], "human")


def _emit(critic_seq, *, propose_seq=None, feedback=None, max_rounds=3,
          advisory_pass=False, dispute=None):
    """Drive emit_plan with scripted propose + critique outputs."""
    proposals = iter(propose_seq or [_plan_json(_good_leaves())] * max_rounds)
    critiques = iter(critic_seq)

    def propose(commission, prior, combined):
        return next(proposals)

    def critique(leaves):
        return next(critiques)

    return emit_plan(commission={"id": "ep1", "objective": "unify", "workspace": "orchestrator-real"},
                     propose=propose, critique=critique, feedback=feedback, max_rounds=max_rounds,
                     advisory_pass=advisory_pass, dispute=dispute)


_PASS = json.dumps({"verdict": "pass", "findings": []})
_CHANGES = json.dumps({"verdict": "changes_requested",
                       "summary": "x", "findings": [{"severity": "high", "explanation": "tighten leaf 2"}]})
_SUBCRIT = json.dumps({"verdict": "changes_requested", "summary": "x", "findings": [
    {"severity": "high", "explanation": "the anchor looks off"},
    {"severity": "low", "explanation": "line numbers drift"}]})
_CRIT = json.dumps({"verdict": "changes_requested", "summary": "x", "findings": [
    {"severity": "critical", "explanation": "leaf 2 breaks the interactive contract"}]})


class EmitTests(unittest.TestCase):
    def test_ready_when_model_passes_and_structural_clean(self):
        trail = _emit([_PASS])
        self.assertEqual(trail["verdict"], "ready")
        self.assertTrue(trail["structural_clean"])
        self.assertEqual(trail["ordered_ids"][-1], "lI")          # integration leaf last
        self.assertEqual(len(trail["leaves"]), 3)
        self.assertTrue(all(item["approved_overnight"] is False for item in trail["leaves"]))

    def test_structural_problem_forces_another_round_even_if_model_passes(self):
        # round 1 plan has NO integration leaf -> structural forces a refine; round 2 is good.
        bad = json.dumps({"leaves": [{"id": "l1", "acceptance": "t", "objective": "o"}]})
        trail = _emit([_PASS, _PASS], propose_seq=[bad, _plan_json(_good_leaves())])
        self.assertEqual(trail["verdict"], "ready")
        self.assertGreaterEqual(len(trail["rounds"]), 2)

    def test_human_feedback_folds_in_and_is_addressed(self):
        # model passes both rounds; feedback forces round 1 to changes_requested, round 2 clean.
        trail = _emit([_PASS, _PASS], feedback="please split leaf 2")
        self.assertEqual(trail["rounds"][0]["verdict"], "changes_requested")
        self.assertEqual(trail["verdict"], "ready")

    def test_needs_review_when_never_clean(self):
        trail = _emit([_CHANGES, _CHANGES, _CHANGES], max_rounds=3)
        self.assertEqual(trail["verdict"], "needs_review")
        self.assertEqual(len(trail["rounds"]), 3)
        self.assertEqual(len(trail["leaves"]), 3)                 # still surfaces best plan for review

    def test_needs_review_surfaces_open_findings(self):
        trail = _emit([_CHANGES, _CHANGES], max_rounds=2)
        self.assertEqual(trail["verdict"], "needs_review")
        self.assertTrue(trail.get("open_findings"))               # the unresolved findings, with text
        self.assertIn("tighten leaf 2", trail["open_findings"][0]["explanation"])

    def test_ready_plan_has_no_open_findings(self):
        trail = _emit([_PASS])
        self.assertEqual(trail["verdict"], "ready")
        self.assertFalse(trail.get("open_findings"))

    def test_recovers_from_bad_json_first_round(self):
        trail = _emit([_PASS], propose_seq=["garbage", _plan_json(_good_leaves())])
        self.assertEqual(trail["verdict"], "ready")


class DriverGateTests(unittest.TestCase):
    """The decomposed driver's plan-gate opt-ins: advisory pass (sub-critical model findings
    become warnings, never vetoes) and the dispute exchange (a refuted finding drops). The
    interactive contract - any finding blocks - is the default and stays pinned by EmitTests."""

    def test_advisory_pass_accepts_subcritical_findings_as_warnings(self):
        trail = _emit([_SUBCRIT], advisory_pass=True)
        self.assertEqual(trail["verdict"], "ready")
        self.assertTrue(trail["structural_clean"])
        self.assertEqual(len(trail["advisory_findings"]), 2)
        self.assertEqual(trail["rounds"][0]["advisory"], 2)
        self.assertFalse(trail.get("open_findings"))

    def test_advisory_pass_still_blocks_critical(self):
        trail = _emit([_CRIT, _CRIT, _CRIT], advisory_pass=True, max_rounds=3)
        self.assertEqual(trail["verdict"], "needs_review")
        self.assertTrue(any(f.get("severity") == "critical" for f in trail["open_findings"]))

    def test_advisory_pass_never_overrides_structural(self):
        # round 1 has no integration leaf (structural) while the model critic passes; advisory
        # mode must still force the refine round.
        bad = json.dumps({"leaves": [{"id": "l1", "acceptance": "t", "objective": "o"}]})
        trail = _emit([_PASS, _PASS], propose_seq=[bad, _plan_json(_good_leaves())],
                      advisory_pass=True)
        self.assertEqual(trail["verdict"], "ready")
        self.assertGreaterEqual(len(trail["rounds"]), 2)

    def test_advisory_pass_keeps_human_feedback_blocking(self):
        trail = _emit([_PASS, _PASS], feedback="please split leaf 2", advisory_pass=True)
        self.assertEqual(trail["rounds"][0]["verdict"], "changes_requested")
        self.assertEqual(trail["verdict"], "ready")

    def test_dispute_dropping_every_finding_yields_ready(self):
        trail = _emit([_SUBCRIT], dispute=lambda leaves, findings: [])
        self.assertEqual(trail["verdict"], "ready")
        self.assertEqual(trail["rounds"][0]["disputed_dropped"], 2)
        self.assertEqual(trail["rounds"][0]["findings"], 0)

    def test_dispute_survivors_still_block_without_advisory(self):
        trail = _emit([_SUBCRIT, _PASS], dispute=lambda leaves, findings: findings[1:])
        self.assertEqual(trail["rounds"][0]["disputed_dropped"], 1)
        self.assertEqual(trail["rounds"][0]["findings"], 1)
        self.assertEqual(trail["rounds"][0]["verdict"], "changes_requested")
        self.assertEqual(trail["verdict"], "ready")               # round 2 passes clean

    def test_dispute_failure_is_failsoft(self):
        def broken(leaves, findings):
            raise RuntimeError("adjudicator unreachable")
        trail = _emit([_SUBCRIT, _PASS], dispute=broken)
        self.assertNotIn("disputed_dropped", trail["rounds"][0])
        self.assertEqual(trail["rounds"][0]["findings"], 2)       # findings unchanged
        self.assertEqual(trail["verdict"], "ready")


class ReviseOnceTests(unittest.TestCase):
    def _commission(self):
        return {"id": "ep1", "title": "Unify", "objective": "unify onto deliberation",
                "integration_acceptance": "integration test green", "workspace": "orchestrator-real"}

    def test_single_pass_applies_feedback_and_validates(self):
        current = [it for it in []]  # start empty -> planner returns a good plan
        calls = {}

        def propose(commission, prior, combined):
            calls["combined"] = combined
            return _plan_json(_good_leaves())

        trail = decompose.revise_once(commission=self._commission(), current_leaves=current,
                                      propose=propose, feedback="split leaf 2")
        self.assertEqual(trail["verdict"], "ready")
        self.assertTrue(trail["structural_clean"])
        self.assertEqual(calls["combined"]["findings"][0]["source"], "human")   # feedback folded in

    def test_falls_back_to_current_on_bad_redraft(self):
        current = [lf.as_backlog_item(parent_id="ep1", workspace="orchestrator-real")
                   for lf in _good_leaves()]
        trail = decompose.revise_once(commission=self._commission(), current_leaves=current,
                                      propose=lambda e, p, c: "garbage", feedback="x")
        self.assertEqual(len(trail["leaves"]), 3)        # kept the prior plan, didn't lose it


class RenderTests(unittest.TestCase):
    def _trail(self):
        leaves = [lf.as_backlog_item(parent_id="ep1", workspace="orchestrator-real")
                  for lf in topo_order(_good_leaves())]
        return {"verdict": "ready", "structural_clean": True,
                "ordered_ids": [lf["id"] for lf in leaves], "leaves": leaves, "rounds": []}

    def test_render_has_feedback_marker_and_leaves(self):
        md = decompose.render_plan_md(self._trail(),
                                      commission={"id": "ep1", "title": "Unify", "objective": "o",
                                                  "integration_acceptance": "ia"})
        self.assertIn(decompose.FEEDBACK_MARKER, md)
        self.assertIn("l1", md)
        self.assertIn("[INTEGRATION]", md)

    def test_render_errored_round_is_not_a_clean_pass(self):
        trail = self._trail()
        trail["rounds"] = [
            {"round": 1, "verdict": "changes_requested", "findings": 7, "structural_findings": 1},
            {"round": 2, "error": "planner returned invalid JSON", "raw": "<think>hmm"},
        ]
        md = decompose.render_plan_md(trail, commission={"id": "ep1", "objective": "o"})
        self.assertIn("did not parse", md)
        self.assertIn("fell back to the prior plan", md)
        self.assertNotIn("round 2: None", md)          # the old misleading render is gone

    def test_render_lists_outstanding_findings(self):
        trail = self._trail()
        trail["verdict"] = "needs_review"
        trail["open_findings"] = [
            {"severity": "high", "explanation": "l2 reinvents cancellation.py",
             "recommended_action": "extend the existing _EVENTS registry", "source": "model"}]
        md = decompose.render_plan_md(trail, commission={"id": "ep1", "objective": "o"})
        self.assertIn("Outstanding findings", md)
        self.assertIn("reinvents cancellation.py", md)
        self.assertIn("FIX: extend the existing _EVENTS registry", md)

    def test_feedback_roundtrip(self):
        md = decompose.render_plan_md(self._trail(), commission={"id": "ep1", "objective": "o"})
        # nothing added yet -> no feedback
        self.assertEqual(decompose.parse_feedback_md(md), [])
        edited = md + "\n- split leaf 2\nadd an error test\n"
        self.assertEqual(decompose.parse_feedback_md(edited), ["split leaf 2", "add an error test"])

    def test_write_and_read_feedback(self):
        import tempfile
        d = tempfile.mkdtemp()
        commission = {"id": "ep1", "title": "Unify", "objective": "o", "integration_acceptance": "ia"}
        paths = decompose.write_plan(d, commission, self._trail())
        self.assertTrue(paths["md"].endswith("plan.md"))
        self.assertEqual(decompose.read_feedback(d, "ep1"), [])      # fresh plan, no feedback
        with open(paths["md"], "a", encoding="utf-8") as fh:
            fh.write("\n- make leaf 1 smaller\n")
        self.assertEqual(decompose.read_feedback(d, "ep1"), ["make leaf 1 smaller"])


class EvidenceTravelsWithTheLeaf(unittest.TestCase):
    """P1 of the auto-decompose driver: the map travels with the leaf, and the planner is told
    to budget-shape. A leaf without evidence forces another refine round (structural finding)."""

    def _leaf(self, lid, **kw):
        kw.setdefault("title", lid)
        kw.setdefault("objective", "do the thing")
        kw.setdefault("acceptance", "test proves it")
        return decompose.Leaf(id=lid, **kw)

    def test_parse_carries_evidence(self):
        plan = ('{"leaves": [{"id": "l1", "title": "t", "objective": "o", "acceptance": "a", '
                '"evidence": "def f(): pass  # app.py:10", "depends_on": [], '
                '"integration": false}]}')
        leaves = decompose.parse_plan(plan)
        self.assertEqual(leaves[0].evidence, "def f(): pass  # app.py:10")

    def test_missing_evidence_is_a_structural_finding(self):
        leaves = [self._leaf("l1", evidence=""),
                  self._leaf("l2", integration=True, depends_on=["l1"])]
        finds = decompose.validate_plan(leaves, strict_evidence=True)
        self.assertTrue(any("evidence" in f["explanation"] for f in finds))

    def test_integration_leaf_is_exempt_from_evidence(self):
        leaves = [self._leaf("l1", evidence="snippet  # x.py:1"),
                  self._leaf("l2", integration=True, depends_on=["l1"])]
        finds = decompose.validate_plan(leaves, strict_evidence=True)
        self.assertFalse(any("evidence" in f["explanation"] for f in finds))

    def test_default_validation_does_not_require_evidence(self):
        """Default validation does not require evidence; only the decomposed driver opts into that strictness."""
        leaves = [self._leaf("l1", evidence=""),
                  self._leaf("l2", integration=True, depends_on=["l1"])]
        finds = decompose.validate_plan(leaves)
        self.assertFalse(any("evidence" in f["explanation"] for f in finds))

    def test_backlog_item_carries_evidence(self):
        item = self._leaf("l1", evidence="snippet").as_backlog_item(parent_id="e", workspace="w")
        self.assertEqual(item["evidence"], "snippet")

    def test_plan_prompt_states_both_rules(self):
        self.assertIn("evidence", decompose.PLAN_SYSTEM)
        self.assertIn("25 tool calls", decompose.PLAN_SYSTEM)


if __name__ == "__main__":
    unittest.main()
