import json
import unittest
from pathlib import Path

import deliberation

APP = Path("app.py").read_text(encoding="utf-8")

SECRET = "hunter2-NOT-FOR-REPLAY"


def _step(tool, args="{}", result="ok", n=1, ms=5):
    return {"step": n, "tool": tool, "args": args, "result": result, "ms": ms}


class EvidenceBlockContract(unittest.TestCase):
    def test_populated_trail_shows_tool_names_args_and_results(self):
        out = deliberation.evidence_block([
            _step("get_document", '{"filename": "CURRENT-PROJECTS.md"}', "STAGE 2 payload v5 ..."),
            _step("search_docs", '{"query": "tablet decoder"}', "hwdec wedges at 125 frames", n=2),
        ])
        for fragment in ("get_document", "CURRENT-PROJECTS.md", "STAGE 2 payload v5",
                         "search_docs", "hwdec wedges at 125 frames"):
            self.assertIn(fragment, out)
        self.assertIn("2 of 2", out)

    def test_EMPTY_and_UNAVAILABLE_are_DIFFERENT_verdicts(self):
        """'No tools were called' and 'no trail was captured' are different evidence. Collapsing them
        hands the critic a confident verdict off a broken instrument."""
        empty = deliberation.evidence_block([])
        unavailable = deliberation.evidence_block(None)
        self.assertNotEqual(empty, unavailable)
        self.assertIn("EMPTY", empty)
        self.assertIn("UNAVAILABLE", unavailable)
        # An empty trail licenses strictness; an absent instrument must NOT.
        self.assertIn("fabrication", empty)
        self.assertIn("proves nothing", unavailable)

    def test_empty_trail_tells_the_critic_a_described_search_is_fabricated(self):
        """The exact 08-11 failure: HAI said 'I searched the ... directory' having called nothing."""
        self.assertIn("search", deliberation.evidence_block([]).lower())


class CredentialsNeverReplayed(unittest.TestCase):
    """Rider 1: the G2b widening must not become a new path for a secret. A trail entry from
    `get_credential` carries a credential VALUE; it is not replayed into a review inference."""

    def test_get_credential_result_is_redacted(self):
        out = deliberation.evidence_block([_step("get_credential", '{"name": "camera"}', SECRET)])
        self.assertNotIn(SECRET, out)
        self.assertIn("REDACTED", out)
        # the CALL is still visible — the critic should know a credential was fetched
        self.assertIn("get_credential", out)

    def test_POSITIVE_CONTROL_the_same_value_IS_shown_for_a_normal_tool(self):
        """Proves the redaction is doing the work, not that the value was never going to appear."""
        out = deliberation.evidence_block([_step("search_docs", "{}", SECRET)])
        self.assertIn(SECRET, out)


class Bounding(unittest.TestCase):
    """Evidence is bounded by the critic's window (budget_chars), not by a fixed step count or character cap."""

    def test_a_long_result_reaches_the_critic_WHOLE_when_the_window_holds_it(self):
        out = deliberation.evidence_block([_step("get_document", "{}", "x" * 50_000)], budget_chars=200_000)
        self.assertIn("x" * 50_000, out)
        self.assertNotIn("truncated", out)

    def test_steps_beyond_the_window_are_left_out_and_the_omission_is_STATED_not_silent(self):
        steps = [_step("search_docs", "{}", "r" * 1_000, n=i) for i in range(40)]
        out = deliberation.evidence_block(steps, budget_chars=10_000)
        self.assertIn("of 40", out)
        self.assertIn("did not fit the critic's window", out)
        # and it must not let the critic read the window as proof the calls did not happen
        self.assertIn("NOT evidence", out)

    def test_the_total_never_exceeds_the_budget(self):
        steps = [_step("get_document", "{}", "y" * 5_000, n=i) for i in range(20)]
        out = deliberation.evidence_block(steps, budget_chars=20_000)
        body = out.split("\n", 2)[-1]
        self.assertLessEqual(len(body), 20_000)

    def test_forty_steps_all_reach_a_critic_whose_window_holds_them(self):
        """The 12-step cap is gone: a 42-step run (the 10-19 handoff loop) is seen whole when it fits."""
        steps = [_step("get_document", "{}", "doc %d" % i, n=i) for i in range(42)]
        out = deliberation.evidence_block(steps, budget_chars=1_000_000)
        self.assertIn("42 of 42", out)


class ChatCritiqueSystemTeachesTheTrail(unittest.TestCase):
    def test_critic_is_told_unfamiliarity_is_not_a_finding(self):
        """The 08-11 critic wrote 'as an external reviewer, I cannot verify these claims about the
        user's private environment' and returned changes_requested anyway."""
        sys_prompt = deliberation.CHAT_CRITIQUE_SYSTEM.lower()
        self.assertIn("evidence trail", sys_prompt)
        self.assertIn("private homelab", sys_prompt)
        self.assertIn("is not evidence against", sys_prompt)

    def test_critic_is_told_not_to_reward_vagueness(self):
        """The failure mode the fix must not create: an answer made 'safe' by hedging away detail."""
        self.assertIn("Do NOT reward vagueness", deliberation.CHAT_CRITIQUE_SYSTEM)

    def test_chat_rules_EXTEND_the_shared_critic_rather_than_replacing_it(self):
        self.assertTrue(deliberation.CHAT_CRITIQUE_SYSTEM.startswith(deliberation.CRITIQUE_SYSTEM))

    def test_the_SHARED_constant_stays_clean_for_the_CODE_lanes(self):
        """CRITIQUE_SYSTEM has four consumers; only two are the chat path. The other two — the
        adversarial DIFF review and the plan critic — are already evidence-grounded and receive no
        tool trail, so promising them one would be false. Widening a shared constant for half its
        consumers is how a fix becomes a regression."""
        self.assertNotIn("EVIDENCE TRAIL", deliberation.CRITIQUE_SYSTEM)
        self.assertNotIn("private homelab", deliberation.CRITIQUE_SYSTEM)


class RegressionPin_CriticFalseAbsence(unittest.TestCase):
    """Regression pin for the critic-bug that falsely claimed tool calls were absent from the
    evidence trail when they were present (runs abr-1788577587-5c8a1f and abr-1788581198-b6dbf2).

    Cherry-picked from HAI's own fix (checkpoint 487f609). Two parts:
    1. Verify evidence_block renders tablet_map status results with searchable indicators
       (usable=True, VOID=False, coverage=N/N) so the critic CAN find them.
    2. Verify CHAT_CRITIQUE_SYSTEM now instructs the critic to scan all entries before
       claiming a tool call is absent."""

    def test_tablet_map_status_trail_renders_searchable_evidence(self):
        """Synthetic reduction of run abr-1788577587-5c8a1f: three tablet_map action=status
        calls showing usable=True VOID=False for orders A/B/C. The critic must be able to
        find 'usable', 'VOID=False', and coverage indicators in the rendered block."""
        trail = [
            _step("tablet_map", "{'action': 'status', 'name': 'warmup-c01-a'}",
                  "warmup-c01-a.results.json: usable=True VOID=False control=True coverage=16/16 measured rows=16 wall=8s"),
            _step("tablet_map", "{'action': 'status', 'name': 'warmup-c01-b'}",
                  "warmup-c01-b.results.json: usable=True VOID=False treatment=True coverage=16/16 measured rows=16 wall=9s", n=2),
            _step("tablet_map", "{'action': 'status', 'name': 'warmup-c01-c'}",
                  "warmup-c01-c.results.json: usable=True VOID=False control=True coverage=16/16 measured rows=16 wall=8s", n=3),
        ]
        out = deliberation.evidence_block(trail)
        self.assertIn("tablet_map", out)
        self.assertIn("warmup-c01-a", out)
        self.assertIn("warmup-c01-b", out)
        self.assertIn("warmup-c01-c", out)
        self.assertIn("usable=True", out)
        self.assertIn("VOID=False", out)
        self.assertIn("coverage=16/16", out)
        self.assertIn("rows=16", out)
        self.assertIn("3 of 3", out)

    def test_critic_prompt_requires_trail_scan_before_absence_claim(self):
        """The fix: CHAT_CRITIQUE_SYSTEM now explicitly instructs the critic to scan every
        entry before claiming a tool call is absent, and to match on substance not exact phrasing."""
        prompt = deliberation.CHAT_CRITIQUE_SYSTEM
        self.assertIn("BEFORE claiming", prompt)
        self.assertIn("scan every", prompt.lower())
        self.assertIn("substance", prompt.lower())

    def test_critic_prompt_treats_omitted_calls_as_happened(self):
        """When the header states some calls were omitted for length, the critic must not
        flag their absence."""
        prompt = deliberation.CHAT_CRITIQUE_SYSTEM
        self.assertIn("omitted", prompt.lower())


class CodeSideFalseAbsenceValidator(unittest.TestCase):
    """The code-side sanity check that catches false-absence findings the prompt-strengthening
    (RegressionPin_CriticFalseAbsence, above) probabilistically reduces but cannot eliminate.
    The critic is an LLM; a hard fix requires programmatic validation of its absence claims."""

    # --- direct helper coverage ---

    def test_asserts_absence_matches_the_recurring_recheck_phrasings(self):
        """Every phrasing observed in the runs that reproduced this bug must be recognized as
        an absence claim. Extend this list, don't loosen it, if a NEW phrasing shows up."""
        for phrase in (
            "the evidence trail does not contain any tool call proving Order C completed",
            "The Evidence Trail contains NO tool calls reading tests/test_evidence_trail.py",
            "there is NO tool call in the Evidence Trail for running tests",
            "no such tool call appears",
            "the tool call is not present in the trail",
        ):
            self.assertTrue(deliberation._finding_asserts_absence(phrase),
                            f"missed absence phrasing: {phrase!r}")

    def test_asserts_absence_does_not_match_neutral_findings(self):
        """A finding without an absence phrase must NOT be flagged as false-absence."""
        for phrase in (
            "The responder returned wrong numbers for order C.",
            "Test coverage is incomplete.",
            "The wall_s value is suspicious.",
            "",
        ):
            self.assertFalse(deliberation._finding_asserts_absence(phrase),
                             f"false-positive absence match: {phrase!r}")

    # --- validator + filter behavior ---

    def _real_world_trail(self):
        """A high-fidelity reduction of the trail from run abr-1788577587-5c8a1f (the run whose
        recheck-3 asserted 'trail does not contain any tool call proving Order C completed')."""
        return [
            _step("tablet_map", "{'action': 'submit', 'name': 'warmup-c01-c', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'warmup-c01-c.20260904-220726'", n=1),
            _step("tablet_map", "{'action': 'status', 'name': 'warmup-c01-c'}",
                  "warmup-c01-c.20260904-220726.20260904-220820.results.json: usable=True VOID=False "
                  "control=True coverage=16/16 measured rows=16 wall=8s", n=2),
        ]

    def test_drops_finding_claiming_absence_of_tool_present_in_trail(self):
        """False-absence, tool named: 'no tablet_map call proving X' when tablet_map is in the trail."""
        trail = self._real_world_trail()
        finding = {"severity": "critical",
                   "explanation": "The evidence trail does not contain any tablet_map call proving completion."}
        contradicted, reason = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertTrue(contradicted, "false-absence claim naming tablet_map should be caught")
        self.assertIn("tablet_map", reason)

    def test_validator_documents_its_precision_gap_paraphrased_findings_pass_through(self):
        """HONEST LIMITATION: when the finding paraphrases without naming the tool or quoting a
        specific outcome, the code-side validator lets it through. Those cases rely on the
        prompt-side strengthening in CHAT_CRITIQUE_SYSTEM (RegressionPin_CriticFalseAbsence).
        This test PINS the gap so a future rewrite doesn't accidentally start dropping paraphrased
        findings (an over-drop would silently discard real feedback)."""
        trail = self._real_world_trail()
        finding = {"severity": "critical",
                   "explanation": "The evidence trail does not contain any tool call proving Order C completed."}
        contradicted, _ = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertFalse(contradicted,
                         "code-side validator must NOT try to catch paraphrased absence claims — "
                         "that job belongs to the prompt-side fix (fail-open on ambiguity)")

    def test_drops_finding_quoting_specific_trail_output(self):
        """The critic sometimes quotes a specific outcome and asserts its absence. If the quote
        appears in the trail verbatim, the finding is refuted."""
        trail = self._real_world_trail()
        finding = {"severity": "critical",
                   "explanation": ("The trail does not contain 'coverage=16/16' or any evidence "
                                   "of successful completion.")}
        contradicted, reason = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertTrue(contradicted, "quoted evidence present in trail should refute the finding")
        self.assertIn("coverage=16/16", reason)

    def test_keeps_finding_when_trail_genuinely_lacks_the_tool(self):
        """False-absence is high-precision: a finding claiming absence of a tool that IS NOT
        in the trail must be kept. This is the anti-over-drop guardrail."""
        trail = self._real_world_trail()
        finding = {"severity": "high",
                   "explanation": "The trail does not contain any coding_test call proving the tests passed."}
        contradicted, _ = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertFalse(contradicted, "a genuinely-absent tool (coding_test) must NOT be dropped")

    def test_keeps_finding_that_makes_no_absence_claim(self):
        """A finding without an absence phrasing is never touched by the validator."""
        trail = self._real_world_trail()
        finding = {"severity": "medium",
                   "explanation": "The responder overstated the wall_s figure by 2 seconds."}
        contradicted, _ = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertFalse(contradicted)

    def test_validator_is_fail_open_when_transcript_is_none(self):
        """No transcript = no validation. Preserves behavior for callers that don't pass one
        (medical critic, adversary pool, decompose critic)."""
        finding = {"explanation": "The trail does not contain any tool call for X."}
        contradicted, _ = deliberation._finding_contradicted_by_trail(finding, None)
        self.assertFalse(contradicted)

    def test_validator_is_fail_open_on_empty_transcript(self):
        """An empty transcript with an absence claim about it is legitimate — the claim is TRUE."""
        finding = {"explanation": "The trail does not contain any tool call."}
        contradicted, _ = deliberation._finding_contradicted_by_trail(finding, [])
        self.assertFalse(contradicted)

    def test_filter_partitions_findings_into_kept_and_dropped(self):
        trail = self._real_world_trail()
        findings = [
            {"severity": "critical",
             "explanation": "The trail does not contain any tablet_map call proving completion."},   # DROP (tool named)
            {"severity": "medium",
             "explanation": "The wall_s value is inconsistent with the timestamps."},                # KEEP (no absence claim)
            {"severity": "high",
             "explanation": "The trail contains no coding_test call."},                              # KEEP (genuinely absent)
        ]
        kept, dropped = deliberation.filter_false_absence_findings(findings, trail)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["severity"], "critical")
        self.assertIn("_dropped_reason", dropped[0])

    # --- integration with parse_critique ---

    def test_parse_critique_drops_false_absence_findings_when_transcript_is_given(self):
        """End-to-end: a critic response asserting absence of a present tool call must be
        rewritten by parse_critique when a transcript is supplied."""
        trail = self._real_world_trail()
        critic_json = (
            '{"verdict": "changes_requested", "summary": "Missing evidence", "findings": ['
            '{"severity": "critical", "explanation": "The evidence trail does not contain any '
            'tablet_map call proving completion."}]}'
        )
        result = deliberation.parse_critique(critic_json, transcript=trail)
        # All findings were false-absence -> verdict downgrades to pass, findings list empty
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["findings"], [])
        self.assertIn("false-absence", result["summary"].lower())

    def test_parse_critique_keeps_real_findings_alongside_dropped_ones(self):
        """Mixed input: one false-absence finding (dropped) + one real finding (kept). The
        real finding survives; the verdict remains changes_requested."""
        trail = self._real_world_trail()
        critic_json = (
            '{"verdict": "changes_requested", "summary": "Two issues", "findings": ['
            '{"severity": "critical", "explanation": "The trail does not contain any tablet_map '
            'call proving completion."},'
            '{"severity": "medium", "explanation": "The wall_s value is inconsistent."}]}'
        )
        result = deliberation.parse_critique(critic_json, transcript=trail)
        self.assertEqual(result["verdict"], "changes_requested")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["severity"], "medium")

    def test_parse_critique_without_transcript_preserves_prior_behavior(self):
        """Callers that don't pass a transcript (medical, adversary, decompose) see zero change."""
        critic_json = (
            '{"verdict": "changes_requested", "summary": "Missing evidence", "findings": ['
            '{"severity": "critical", "explanation": "The evidence trail does not contain any '
            'tablet_map call proving completion."}]}'
        )
        result = deliberation.parse_critique(critic_json)  # no transcript
        self.assertEqual(result["verdict"], "changes_requested")
        self.assertEqual(len(result["findings"]), 1)


class RegressionPin_CriticBareToolAbsence(unittest.TestCase):
    """Regression pin: the v1 validator missed bare tool-absence phrasings (e.g., 'there is no `fleet_status` or equivalent') because they lacked 'tool call' scaffolding. The fix extends _ABSENCE_PATTERNS with dynamic tool-name checks to catch these cases."""

    def _fleet_status_trail(self):
        return [
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-a', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'phase5-warmup-c01-a.20260905-082802'", n=1),
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-b', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'phase5-warmup-c01-b.20260905-082802'", n=1),
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-c', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'phase5-warmup-c01-c.20260905-082802'", n=1),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-b.20260905-082802'}",
                  "nothing in the letter slot and no shipped results for 'phase5-warmup-c01-b.20260905-082802'.", n=2),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-c.20260905-082802'}",
                  "nothing in the letter slot and no shipped results for 'phase5-warmup-c01-c.20260905-082802'.", n=2),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-b.20260905-082802'}",
                  "phase5-warmup-c01-b.20260905-082802.20260905-082917.results.json: usable=True VOID=False "
                  "control=True coverage=11/11 measured rows=11 wall=8s", n=3),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-c.20260905-082802'}",
                  "phase5-warmup-c01-c.20260905-082802.20260905-082947.results.json: usable=True VOID=False "
                  "control=True coverage=16/16 measured rows=16 wall=8s", n=4),
            _step("fleet_status", '{"target": "fellowship"}',
                  '{"host": "fellowship", "state": "idle", "health": "watching", "health_detail": '
                  '"idle; 11/11 nodes healthy (aragorn, boromir, faramir, frodo, gandalf, gimli, '
                  'legolas, merry, pippin, sam, smeagol)"}', n=5),
        ]

    def test_bare_absence_of_fleet_status_is_caught(self):
        """Catches 'no fleet_status' claims when the trail actually contains the status data.

        Addresses a previous regression where absence detectors missed the contradiction
        because the negative claim lacked explicit 'tool call' scaffolding."""
        trail = self._fleet_status_trail()
        finding = {"severity": "critical", "explanation": (
            "The responder claims Criterion 4 passed with 'Live fleet_status(target=fellowship)... "
            "11/11 nodes healthy'. However, there is no `fleet_status` or equivalent to substantiate.")}
        contradicted, reason = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertTrue(contradicted, "bare 'no `fleet_status`' claim must be dropped")
        self.assertIn("fleet_status", reason)

    def test_bare_absence_without_backticks_is_caught(self):
        """'no fleet_status call', 'no fleet_status invocation', 'without any fleet_status'."""
        trail = self._fleet_status_trail()
        for phrase in (
            "there is no fleet_status call anywhere in the trail",
            "no fleet_status invocation was made",
            "the responder made no fleet_status request",
            "without any fleet_status action to back it up",
            "missing fleet_status call for Criterion 4",
            "cannot find any fleet_status entry",
            "fails to call fleet_status for the criterion",
        ):
            finding = {"severity": "high", "explanation": phrase}
            contradicted, reason = deliberation._finding_contradicted_by_trail(finding, trail)
            self.assertTrue(contradicted, f"missed bare absence phrasing: {phrase!r}")
            self.assertIn("fleet_status", reason)

    def test_bare_absence_of_tablet_map_is_caught(self):
        trail = self._fleet_status_trail()
        finding = {"severity": "critical",
                   "explanation": "there is no `tablet_map` or equivalent to prove submission"}
        contradicted, reason = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertTrue(contradicted)
        self.assertIn("tablet_map", reason)

    def test_bare_absence_stays_high_precision_for_a_genuinely_missing_tool(self):
        """The critic says 'no `coding_test` call'; coding_test is NOT in the trail; the
        finding stays. Guardrail: an over-broad bare-absence pattern would drop this."""
        trail = self._fleet_status_trail()
        finding = {"severity": "high", "explanation": "there is no `coding_test` or equivalent"}
        contradicted, _ = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertFalse(contradicted,
                         "a genuinely-absent tool must not be dropped by bare-absence check")

    def test_bare_absence_does_not_false_positive_on_prose_mentioning_a_tool(self):
        """A finding that says 'the tablet_map results are inconsistent' is NOT an
        absence claim — it must not be caught by the bare-absence check."""
        trail = self._fleet_status_trail()
        for phrase in (
            "the tablet_map results are inconsistent with the coverage stated",
            "the fleet_status target argument is wrong",
            "tablet_map was called but the args are malformed",
        ):
            finding = {"severity": "medium", "explanation": phrase}
            contradicted, _ = deliberation._finding_contradicted_by_trail(finding, trail)
            self.assertFalse(contradicted, f"false-positive on non-absence phrase: {phrase!r}")


class RegressionPin_CriticCherryPicksEarlyStatus(unittest.TestCase):
    """Pin: critic must not flag early pending polls as failures if later trail entries show orders landing (usable=True)."""

    def _cherry_pick_trail(self):
        return [
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-b', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'phase5-warmup-c01-b.20260905-082802'", n=1),
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-c', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'phase5-warmup-c01-c.20260905-082802'", n=1),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-b.20260905-082802'}",
                  "nothing in the letter slot and no shipped results for 'phase5-warmup-c01-b.20260905-082802'.", n=2),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-c.20260905-082802'}",
                  "nothing in the letter slot and no shipped results for 'phase5-warmup-c01-c.20260905-082802'.", n=2),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-b.20260905-082802'}",
                  "phase5-warmup-c01-b.20260905-082802.20260905-082917.results.json: usable=True VOID=False "
                  "control=True coverage=11/11 measured rows=11 wall=8s", n=3),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-c.20260905-082802'}",
                  "phase5-warmup-c01-c.20260905-082802.20260905-082947.results.json: usable=True VOID=False "
                  "control=True coverage=16/16 measured rows=16 wall=8s", n=4),
        ]

    def test_cherry_pick_finding_about_order_b_is_dropped(self):
        """A finding claiming a failure for an order is invalid if the trail contains a later successful entry for that same order."""
        trail = self._cherry_pick_trail()
        finding = {"severity": "critical", "explanation": (
            "The responder reports Order B (phase5-warmup-c01-b) as having n_measured=11, "
            "usable=True, and control_passed=True. However, the evidence trail shows three "
            "separate status checks for phase5-warmup-c01-b.20260905-082802 returning "
            "'nothing in the letter slot and no shipped results', indicating it had not "
            "completed. The responder fabricated the 'passed' metrics.")}
        contradicted, reason = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertTrue(contradicted, "cherry-pick finding for order B must be dropped")
        self.assertIn("phase5-warmup-c01-b", reason)

    def test_cherry_pick_finding_about_order_c_is_dropped(self):
        trail = self._cherry_pick_trail()
        finding = {"severity": "critical", "explanation": (
            "the status checks for order phase5-warmup-c01-c.20260905-082802 returned "
            "'nothing in the letter slot and no shipped results', indicating it had "
            "not completed or failed to produce results at the time of checking.")}
        contradicted, reason = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertTrue(contradicted, "cherry-pick finding for order C must be dropped")
        self.assertIn("phase5-warmup-c01-c", reason)

    def test_cherry_pick_stays_high_precision_when_trail_shows_no_success(self):
        """If the trail genuinely has NO success entry for the order id — only pending
        polls — the finding stays. This is the guardrail against silently dropping
        real failures."""
        trail = [
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-d', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'phase5-warmup-c01-d.20260905-090000'", n=1),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-d.20260905-090000'}",
                  "nothing in the letter slot and no shipped results for 'phase5-warmup-c01-d.20260905-090000'.", n=2),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-d.20260905-090000'}",
                  "nothing in the letter slot and no shipped results for 'phase5-warmup-c01-d.20260905-090000'.", n=3),
        ]
        finding = {"severity": "critical", "explanation": (
            "phase5-warmup-c01-d.20260905-090000 returned 'nothing in the letter slot' twice; "
            "the responder should not have claimed success.")}
        contradicted, _ = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertFalse(contradicted,
                         "an order that genuinely never landed must not be dropped as a cherry-pick")

    def test_cherry_pick_ignores_finding_without_pending_marker(self):
        """A finding not quoting a pending-status phrase is not a cherry-pick — never fires."""
        trail = [
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-b'}",
                  "SUBMITTED order 'phase5-warmup-c01-b.20260905-082802'", n=1),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-b'}",
                  "phase5-warmup-c01-b.20260905-082802.20260905-082917.results.json: usable=True "
                  "VOID=False control=True coverage=11/11", n=2),
        ]
        finding = {"severity": "medium", "explanation": (
            "Order phase5-warmup-c01-b (n_measured=11) has a wall_s value inconsistent "
            "with the reported timing.")}
        contradicted, _ = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertFalse(contradicted,
                         "cherry-pick check must not fire without a pending-status marker in the finding")

    def test_cherry_pick_ignores_finding_without_an_order_id(self):
        """A pending-status quote without a concrete order id could be about anything;
        cherry-pick check requires an id to bind subject-to-trail-step."""
        trail = self._cherry_pick_trail()
        finding = {"severity": "high", "explanation": (
            "Several tablet_map calls returned 'nothing in the letter slot' at some point.")}
        contradicted, _ = deliberation._finding_contradicted_by_trail(finding, trail)
        self.assertFalse(contradicted,
                         "cherry-pick check must not fire without an order id in the finding")


class LiveDecoy_Full2026_09_05Critique(unittest.TestCase):
    """End-to-end pin using the EXACT critic JSON shape from run abr-1788614127-7cc405,
    driven through parse_critique with the trail. All three surfaced findings must be
    dropped and the verdict must downgrade to pass. This is the LIVE-DECOY test the
    v1 fix skipped — 'a guard is not verified until a live decoy proves it'."""

    def _full_trail(self):
        return [
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-a', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'phase5-warmup-c01-a.20260905-082802'", n=1),
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-b', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'phase5-warmup-c01-b.20260905-082802'", n=1),
            _step("tablet_map", "{'action': 'submit', 'name': 'phase5-warmup-c01-c', 'payload': 'map_echo'}",
                  "SUBMITTED tablet map order 'phase5-warmup-c01-c.20260905-082802'", n=1),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-a.20260905-082802'}",
                  "phase5-warmup-c01-a.20260905-082802.20260905-082855.results.json: usable=True VOID=False "
                  "control=True coverage=6/6 measured rows=6 wall=5s", n=2),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-b.20260905-082802'}",
                  "phase5-warmup-c01-b.20260905-082802.20260905-082917.results.json: usable=True VOID=False "
                  "control=True coverage=11/11 measured rows=11 wall=8s", n=3),
            _step("tablet_map", "{'action': 'status', 'name': 'phase5-warmup-c01-c.20260905-082802'}",
                  "phase5-warmup-c01-c.20260905-082802.20260905-082947.results.json: usable=True VOID=False "
                  "control=True coverage=16/16 measured rows=16 wall=8s", n=4),
            _step("fleet_status", '{"target": "fellowship"}',
                  '{"host":"fellowship","state":"idle","health":"watching","health_detail":'
                  '"idle; 11/11 nodes healthy"}', n=5),
        ]

    def test_all_three_surfaced_findings_are_dropped_verdict_downgrades_to_pass(self):
        trail = self._full_trail()
        critic_json = json.dumps({
            "verdict": "changes_requested",
            "summary": "Unresolved critical findings",
            "findings": [
                {"severity": "critical", "explanation":
                    "The responder reports Order B (phase5-warmup-c01-b) as having n_measured=11, "
                    "usable=True, and control_passed=True. However, the evidence trail shows three "
                    "separate status checks for phase5-warmup-c01-b.20260905-082802 returning "
                    "'nothing in the letter slot and no shipped results', indicating it had not "
                    "completed."},
                {"severity": "critical", "explanation":
                    "The responder reports Order C (phase5-warmup-c01-c) as having n_measured=16, "
                    "usable=True, and control_passed=True. However, tablet_map status checks for "
                    "phase5-warmup-c01-c.20260905-082802 repeatedly returned 'nothing in the letter "
                    "slot and no shipped results'."},
                {"severity": "critical", "explanation":
                    "The responder claims Criterion 4 passed with 'Live fleet_status(target=fellowship)"
                    "... 11/11 nodes healthy'. However, there is no `fleet_status` or equivalent to "
                    "substantiate this claim."},
            ],
        })
        result = deliberation.parse_critique(critic_json, transcript=trail)
        self.assertEqual(result["verdict"], "pass",
                         "all three findings were validator-refutable; verdict must downgrade")
        self.assertEqual(result["findings"], [],
                         "all three findings were validator-refutable; findings list must be empty")
        self.assertIn("false-absence", result["summary"].lower())

    def test_a_real_finding_mixed_in_still_survives(self):
        """Anti-over-drop: add one genuinely-un-refutable finding to the same critique
        and confirm it rides through while the three false ones are dropped."""
        trail = self._full_trail()
        critic_json = json.dumps({
            "verdict": "changes_requested",
            "summary": "Mixed",
            "findings": [
                {"severity": "critical", "explanation":
                    "there is no `fleet_status` or equivalent to substantiate this claim"},
                {"severity": "medium", "explanation":
                    "The wall_s figures suggest 21 seconds of total activity, but the order was "
                    "submitted 45 minutes ago — the responder should reconcile the gap."},
            ],
        })
        result = deliberation.parse_critique(critic_json, transcript=trail)
        self.assertEqual(result["verdict"], "changes_requested")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["severity"], "medium")


class ChatCriticCallSitesAreWired(unittest.TestCase):
    """Source-text assertions, used deliberately and narrowly. app.py imports FastAPI and the
    think-hard path is a live-model call, so the PROPERTY worth pinning — 'no chat critic call site
    was left unwired' — is not reachable behaviourally here. These are counts, not spellings: a third
    chat critic added later without the trail trips them."""

    def test_every_chat_critique_prompt_carries_the_evidence_block(self):
        """Both critics take their evidence through ONE helper (_critic_evidence: the library notes + the tool trail,
        sized from the critic's own window) - the first pass and the re-check, each with the answer it judges."""
        wired = APP.count("_critic_evidence(") - 1          # minus the def
        self.assertEqual(wired, 2,
                         f"expected the first-pass critic AND the re-check critic to be wired; "
                         f"found {wired} call site(s)")
        self.assertEqual(APP.count("_tr_steps, relevant)"), 2,
                         "each critic call must pass the trail AND the library excerpts the responder was handed")
        self.assertEqual(APP.count("deliberation.evidence_block("), 1, "only the helper builds the trail block")
        self.assertEqual(APP.count("deliberation.reference_block("), 1, "only the helper builds the notes block")

    def test_every_chat_parse_critique_call_passes_the_transcript(self):
        """The code-side false-absence validator only fires when parse_critique is given the trail.
        A future chat critic that forgets to pass transcript=_tr_steps silently regresses to the
        prompt-only fix, which is exactly the shape of the bug we're guarding against."""
        wired = APP.count("parse_critique(raw_critique, transcript=_tr_steps)") + \
                APP.count("parse_critique(raw_recheck, transcript=_tr_steps)")
        self.assertEqual(wired, 2,
                         f"expected the first-pass parse AND the re-check parse to receive the "
                         f"trail; found {wired} wired site(s)")

    def test_chat_critics_use_the_chat_constant_and_the_code_lanes_do_not(self):
        # 2 chat sites (first pass + re-check), 2 code sites (diff review + plan critic); +1 for _critic_room_chars,
        # which counts the chat critic's system prompt against the window it sizes.
        self.assertEqual(APP.count("deliberation.CHAT_CRITIQUE_SYSTEM"), 3)
        self.assertEqual(APP.count("deliberation.CRITIQUE_SYSTEM"), 2)


if __name__ == "__main__":
    unittest.main()
