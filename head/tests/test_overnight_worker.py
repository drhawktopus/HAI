import unittest

import overnight_worker
from overnight_worker import WorkerDeps, work_item


class _Budget:
    """Minimal budget stub with a settable remaining_seconds."""
    def __init__(self, remaining=100):
        self.remaining_seconds = remaining


def _deps(*, verify_results, start_id="t1", record=None):
    """Build WorkerDeps with a verify() that yields successive results, recording lifecycle calls."""
    rec = record if record is not None else {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
    results = iter(verify_results)

    def start_task(item):
        rec["started"] += 1
        return start_id

    def drive(task_id, item, budget):
        rec["drove"] += 1

    def verify(task_id, item):
        return next(results)

    def finish_task(task_id):
        rec["finished"] += 1

    def bail_task(task_id):
        rec["bailed"] += 1

    return WorkerDeps(start_task, drive, verify, finish_task, bail_task), rec


class WorkItemTests(unittest.TestCase):
    def test_verifier_pass_first_try_is_done_and_finishes(self):
        deps, rec = _deps(verify_results=[{"passed": True, "summary": "24 tests ok"}])
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec, {"started": 1, "drove": 1, "finished": 1, "bailed": 0})
        self.assertEqual(out["tests"]["summary"], "24 tests ok")

    def test_retry_then_pass(self):
        deps, rec = _deps(verify_results=[{"passed": False, "summary": "1 fail"},
                                          {"passed": True, "summary": "green"}])
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps, max_attempts=2)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec["drove"], 2)
        self.assertEqual(rec["finished"], 1)
        self.assertEqual(rec["bailed"], 0)

    def test_never_passes_bails_and_reverts(self):
        deps, rec = _deps(verify_results=[{"passed": False, "summary": "red"},
                                          {"passed": False, "summary": "still red"}])
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps, max_attempts=2)
        self.assertEqual(out["outcome"], "bailed")
        self.assertEqual(rec["bailed"], 1)        # workspace reverted
        self.assertEqual(rec["finished"], 0)
        self.assertEqual(len(out["attempts"]), 2)

    def test_homelab_item_bails_without_touching_coding(self):
        rec = {"started": 0}
        deps, _ = _deps(verify_results=[], record=rec)
        out = work_item({"id": "h", "domain": "homelab"}, _Budget(), deps)
        self.assertEqual(out["outcome"], "bailed")
        self.assertIn("morning-review", out["evidence"])
        self.assertEqual(rec["started"], 0)       # never created a coding task

    def test_start_task_failure_is_error(self):
        def start_task(item):
            raise RuntimeError("single-active guard")
        deps = WorkerDeps(start_task, lambda *a: None, lambda *a: {"passed": True},
                          lambda *a: None, lambda *a: None)
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        self.assertEqual(out["outcome"], "error")
        self.assertIn("single-active guard", out["evidence"])

    def test_drive_exception_counts_as_attempt_then_bails(self):
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}

        def drive(task_id, item, budget):
            rec["drove"] += 1
            raise RuntimeError("model timeout")

        deps = WorkerDeps(lambda i: "t1", drive, lambda *a: {"passed": True},
                          lambda t: rec.__setitem__("finished", rec["finished"] + 1),
                          lambda t: rec.__setitem__("bailed", rec["bailed"] + 1))
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps, max_attempts=2)
        self.assertEqual(out["outcome"], "bailed")
        self.assertEqual(rec["drove"], 2)         # both attempts tried to drive
        self.assertEqual(rec["finished"], 0)
        self.assertEqual(rec["bailed"], 1)

    def test_halt_mid_item_bails(self):
        deps, rec = _deps(verify_results=[{"passed": True}])
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps,
                        halt_check=lambda: True)
        self.assertEqual(out["outcome"], "bailed")
        self.assertIn("halted", out["evidence"])
        self.assertEqual(rec["bailed"], 1)
        self.assertEqual(rec["drove"], 0)         # halted before driving

    def test_out_of_time_mid_item_bails(self):
        deps, rec = _deps(verify_results=[{"passed": True}])
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(remaining=0), deps)
        self.assertEqual(out["outcome"], "bailed")
        self.assertIn("out of time", out["evidence"])
        self.assertEqual(rec["drove"], 0)


class ReviewIntegrationTests(unittest.TestCase):
    def test_review_pass_marks_done_with_trail(self):
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, _ = _deps(verify_results=[{"passed": True, "summary": "green"}], record=rec)
        deps.review = lambda tid, item, b: {"verdict": "pass", "tier": "decorrelated"}
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(out["adversarial"]["verdict"], "pass")
        self.assertEqual(rec["finished"], 1)
        self.assertEqual(rec["bailed"], 0)

    def test_review_reverify_red_reverts_to_bailed(self):
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, _ = _deps(verify_results=[{"passed": True, "summary": "green"}], record=rec)
        deps.review = lambda tid, item, b: {"verdict": "reverify_red", "reason": "fix_broke_verifier"}
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        self.assertEqual(out["outcome"], "bailed")
        self.assertEqual(rec["finished"], 0)
        self.assertEqual(rec["bailed"], 1)
        self.assertEqual(out["adversarial"]["verdict"], "reverify_red")

    def test_review_exception_does_not_block_done(self):
        deps, rec = _deps(verify_results=[{"passed": True, "summary": "green"}])
        def boom(tid, item, b):
            raise RuntimeError("review offline")
        deps.review = boom
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")          # tests green => still done
        self.assertEqual(out["adversarial"]["verdict"], "skipped")
        self.assertEqual(rec["finished"], 1)

    def test_timings_no_wrapper_critique_with_ladder_timings(self):
        # When the review trail carries its own timings dict, the worker should NOT
        # ALSO add a separate wrapper-level "critique" timing — that would accumulate
        # ladder's critique time with an outer wall-clock measurement, double-counting.
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, _ = _deps(verify_results=[{"passed": True, "summary": "green"}], record=rec)
        ladder_critique = 12.345
        deps.review = lambda tid, item, b: {
            "verdict": "pass",
            "tier": "decorrelated",
            "timings": {"critique": ladder_critique},
        }
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        t = out["timings"]
        # critique key should equal the ladder's value, not be inflated by a wrapper add
        self.assertAlmostEqual(t["critique"], ladder_critique, places=1)

    def test_timings_contains_drive_and_verify(self):
        # Every completed item must record drive and verify stage timings.
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, _ = _deps(verify_results=[{"passed": True, "summary": "green"}], record=rec)
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        t = out["timings"]
        self.assertIn("drive", t)
        self.assertGreaterEqual(t["drive"], 0.0)
        self.assertIn("verify", t)
        self.assertGreaterEqual(t["verify"], 0.0)


class AcceptanceDemandPureTests(unittest.TestCase):
    """The acceptance-delta guard's pure helpers: demand detection + diff test-coverage detection."""

    def test_demands_tests_true_on_authoring_language(self):
        for txt in ("Unit tests with temp dirs assert: behind->refusal",
                    "A new test exercises the real production path",
                    "a new tests/test_x.py asserts both functions",
                    "add a regression test covering the edge case",
                    "a passing test that captures the bug"):
            self.assertTrue(overnight_worker._acceptance_demands_tests(txt), txt)

    def test_demands_tests_false_on_boilerplate(self):
        # 'suite stays green' / 'existing tests pass' must NOT trigger — a false positive bails a
        # legitimate item.
        for txt in ("the orchestrator suite stays green",
                    "existing tests pass and returncode is 0",
                    "read-back confirms the config value", "", None):
            self.assertFalse(overnight_worker._acceptance_demands_tests(txt), repr(txt))

    def test_diff_adds_tests_detects_added_test_defs_and_files(self):
        self.assertTrue(overnight_worker._diff_adds_tests(
            "+++ b/tests/test_x.py\n+def test_foo():\n+    assert True"))
        self.assertTrue(overnight_worker._diff_adds_tests("+    async def test_bar(self):"))

    def test_diff_adds_tests_false_when_no_test_coverage(self):
        self.assertFalse(overnight_worker._diff_adds_tests("+def helper():\n+    return 1"))
        self.assertFalse(overnight_worker._diff_adds_tests("-def test_removed():"))  # removed != added
        self.assertFalse(overnight_worker._diff_adds_tests(""))


def _deps_with_diff(diff_text, *, record=None, raise_on_diff=False):
    """WorkerDeps that passes the verifier first try, no review, with a get_diff returning diff_text
    (or raising, to exercise fail-open)."""
    deps, rec = _deps(verify_results=[{"passed": True, "summary": "green"}], record=record)

    def get_diff(task_id):
        if raise_on_diff:
            raise RuntimeError("runner down")
        return diff_text

    deps.get_diff = get_diff
    return deps, rec


class AcceptanceDeltaGuardTests(unittest.TestCase):
    """The DONE-path acceptance-delta guard: a verifier-green item whose acceptance names new tests
    but whose diff adds none is refused DONE and flagged for a human (the bl-run-start-staleness-guard
    hollow-pass class)."""

    HOLLOW_ITEM = {"id": "h", "domain": "orchestrator",
                   "acceptance": "Unit tests with temp dirs assert the behavior. Suite stays green."}
    NO_TEST_DIFF = "+++ b/overnight.py\n+def workspace_stale(a, b):\n+    return False, []"
    WITH_TEST_DIFF = ("+++ b/overnight.py\n+def workspace_stale(a, b):\n+    return False, []\n"
                      "+++ b/tests/test_overnight.py\n+def test_workspace_stale():\n+    assert True")

    def test_hollow_pass_is_bailed_and_flagged(self):
        deps, rec = _deps_with_diff(self.NO_TEST_DIFF)
        out = work_item(self.HOLLOW_ITEM, _Budget(), deps)
        self.assertEqual(out["outcome"], "bailed")
        self.assertEqual(rec["bailed"], 1)       # inert work reverted
        self.assertEqual(rec["finished"], 0)     # never marked DONE
        self.assertEqual(out["adversarial"]["verdict"], "bail_human")
        self.assertEqual(out["adversarial"]["reason"], "hollow_no_tests")
        self.assertTrue(out["adversarial"]["findings_outstanding"])

    def test_real_tests_added_completes(self):
        deps, rec = _deps_with_diff(self.WITH_TEST_DIFF)
        out = work_item(self.HOLLOW_ITEM, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec["finished"], 1)
        self.assertEqual(rec["bailed"], 0)

    def test_acceptance_without_test_demand_is_not_gated(self):
        item = {"id": "n", "domain": "orchestrator",
                "acceptance": "read-back confirms the value; the suite stays green"}
        deps, rec = _deps_with_diff(self.NO_TEST_DIFF)
        out = work_item(item, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")   # no test demand -> guard does not fire
        self.assertEqual(rec["finished"], 1)

    def test_kill_switch_get_diff_none_skips_guard(self):
        # Production wires get_diff only when the check is enabled; None = disabled = never bails.
        deps, rec = _deps(verify_results=[{"passed": True, "summary": "green"}])
        self.assertIsNone(deps.get_diff)
        out = work_item(self.HOLLOW_ITEM, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec["finished"], 1)

    def test_diff_fetch_error_fails_open(self):
        # A get_diff that raises must NOT bail a genuinely-green item.
        deps, rec = _deps_with_diff("", raise_on_diff=True)
        out = work_item(self.HOLLOW_ITEM, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec["finished"], 1)


class EmptyDiffGuardTests(unittest.TestCase):
    """Guard against empty diffs: if the drive wrote nothing, bail rather than marking DONE."""

    ITEM = {"id": "e", "domain": "orchestrator", "acceptance": "read-back confirms the value"}  # no test demand

    def test_empty_diff_bails(self):
        deps, rec = _deps_with_diff("")
        out = work_item(self.ITEM, _Budget(), deps)
        self.assertEqual(out["outcome"], "bailed")
        self.assertEqual(out["adversarial"]["reason"], "no_changes_produced")
        self.assertEqual(rec["bailed"], 1)
        self.assertEqual(rec["finished"], 0)

    def test_whitespace_only_diff_bails(self):
        deps, rec = _deps_with_diff("   \n \t\n")
        self.assertEqual(work_item(self.ITEM, _Budget(), deps)["outcome"], "bailed")

    def test_fetch_error_none_does_not_bail(self):
        # get_diff returns None (fetch error) -> fail open -> DONE, never bail a good item on a glitch.
        deps, rec = _deps(verify_results=[{"passed": True, "summary": "green"}])
        deps.get_diff = lambda tid: None
        out = work_item(self.ITEM, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec["finished"], 1)

    def test_real_diff_completes(self):
        deps, rec = _deps_with_diff("diff --git a/x.py b/x.py\n+x = 1\n")
        out = work_item(self.ITEM, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec["finished"], 1)


class ScopeGatePureTests(unittest.TestCase):
    """Phase B1 pure helpers: diff-touched files + spec-demanded-but-untouched targets."""

    def test_diff_touched_files_basenames(self):
        d = "+++ b/app.py\n+x = 1\n+++ b/tests/test_app.py\n+def test_x(): pass\n"
        self.assertEqual(overnight_worker._diff_touched_files(d), {"app.py", "test_app.py"})
        self.assertEqual(overnight_worker._diff_touched_files(""), set())
        self.assertEqual(overnight_worker._diff_touched_files(None), set())

    def test_missed_scope_reports_untouched_targets(self):
        item = {"objective": "Wire drive telemetry into _dispatch_lead in app.py",
                "acceptance": "the item record shows drive_tele"}
        diff = "+++ b/overnight_worker.py\n+timings[\"drive_tele\"] = meta\n"
        missed = overnight_worker._missed_scope(item, diff)
        self.assertIn("app.py", missed)                  # named file never touched
        self.assertIn("_dispatch_lead", missed)    # named symbol absent from the diff
        self.assertNotIn("drive_tele", missed)           # appears in the diff text -> covered

    def test_missed_scope_covered_by_wiring(self):
        item = {"objective": "Wire drive telemetry into _dispatch_lead in app.py",
                "acceptance": "the item record shows drive_tele"}
        diff = ("+++ b/overnight_worker.py\n+timings[\"drive_tele\"] = meta\n"
                "+++ b/app.py\n+    _dispatch_lead(q)\n")
        self.assertEqual(overnight_worker._missed_scope(item, diff), [])

    def test_dotted_ref_covered_by_bare_attr(self):
        # spec says overnight.parse_test_count; a diff line using just parse_test_count( covers it.
        item = {"objective": "Call overnight.parse_test_count in the verify path",
                "acceptance": ""}
        diff = "+++ b/app.py\n+    count = parse_test_count(out)\n"
        self.assertEqual(overnight_worker._missed_scope(item, diff), [])

    def test_negated_target_not_demanded(self):
        item = {"objective": "Add the aggregator helper to overnight_worker.py. "
                             "Do not modify _dispatch_lead or app.py.",
                "acceptance": ""}
        diff = "+++ b/overnight_worker.py\n+def _agg(x):\n+    return x\n"
        self.assertEqual(overnight_worker._missed_scope(item, diff), [])

    def test_comment_mention_does_not_cover_symbol(self):
        item = {"objective": "Wire drive telemetry into _dispatch_lead in app.py",
                "acceptance": ""}
        diff = "+++ b/app.py\n+    # _dispatch_lead will pick this up later\n+x = 1\n"
        self.assertIn("_dispatch_lead", overnight_worker._missed_scope(item, diff))

    def test_new_test_file_spec_causes_no_scope_miss(self):
        item = {"objective": "Fix rolling_mean in fixtures/report_stats.py",
                "acceptance": "add regression tests in a new tests/test_fixture_report_stats.py"}
        diff = ("+++ b/fixtures/report_stats.py\n"
                " def rolling_mean(values, window):\n"
                "+        start = max(0, i + 1 - window)\n"
                "+++ b/tests/test_fixture_report_stats.py\n"
                "+def test_rolling_mean_trailing(): pass\n")
        self.assertEqual(overnight_worker._missed_scope(item, diff), [])

    def test_string_literal_still_covers_symbol(self):
        # Data-key wiring lives in string literals (timings["drive_tele"]) — strings must keep
        # counting as coverage or the B1.1 false-positive class comes straight back.
        item = {"objective": "Surface the drive stats on the item record",
                "acceptance": "the item record shows drive_tele"}
        diff = '+++ b/overnight_worker.py\n+timings["drive_tele"] = meta\n'
        self.assertEqual(overnight_worker._missed_scope(item, diff), [])

    def test_symbol_free_spec_never_gates(self):
        self.assertEqual(overnight_worker._missed_scope(
            {"objective": "make the report tidier", "acceptance": "reads well"}, "+++ b/x.py\n+1\n"), [])

    # Symbol coverage requires the NON-TEST portion of the diff; test-file mentions do not satisfy
    # production-wiring demands.

    def test_nontest_diff_strips_test_chunks(self):
        diff = ("diff --git a/app.py b/app.py\n+++ b/app.py\n+real_wiring()\n"
                "diff --git a/tests/test_app.py b/tests/test_app.py\n+++ b/tests/test_app.py\n"
                "+run_overnight_mentioned_in_test()\n"
                "diff --git a/overnight.py b/overnight.py\n+++ b/overnight.py\n+helper()\n")
        out = overnight_worker._nontest_diff(diff)
        self.assertIn("real_wiring", out)
        self.assertIn("helper", out)
        self.assertNotIn("run_overnight_mentioned_in_test", out)
        self.assertEqual(overnight_worker._nontest_diff(""), "")

    def test_is_test_path(self):
        self.assertTrue(overnight_worker._is_test_path("tests/test_app.py"))
        self.assertTrue(overnight_worker._is_test_path("tests/_patch_app.py"))   # test-DIR tooling
        self.assertTrue(overnight_worker._is_test_path("test_energy.py"))
        self.assertFalse(overnight_worker._is_test_path("app.py"))
        self.assertFalse(overnight_worker._is_test_path("overnight_worker.py"))  # 'test' not a dir part

    def test_symbol_only_in_test_files_is_still_missed(self):
        item = {"objective": "Pass the id through to run_overnight in app.py", "acceptance": ""}
        diff = ("diff --git a/app.py b/app.py\n+++ b/app.py\n+rid = new_run_id()\n"
                "diff --git a/tests/test_x.py b/tests/test_x.py\n+++ b/tests/test_x.py\n"
                "+def test_run_overnight_gets_id():\n+    assert run_overnight\n")
        missed = overnight_worker._missed_scope(item, diff)
        self.assertIn("run_overnight", missed)           # test mention does NOT count as wiring
        self.assertNotIn("app.py", missed)               # the file IS touched (file check unchanged)

    def test_symbol_in_production_still_covers(self):
        item = {"objective": "Pass the id through to run_overnight in app.py", "acceptance": ""}
        diff = "diff --git a/app.py b/app.py\n+++ b/app.py\n+overnight.run_overnight(b, w, run_id=rid)\n"
        self.assertEqual(overnight_worker._missed_scope(item, diff), [])


class AcceptanceScopeGateTests(unittest.TestCase):
    """
    Test _missed_scope gate logic:
    - If a verifier-green diff misses spec targets, allow one retry.
    - If the gap persists after retry, flag for human review.
    """

    ITEM = {"id": "s", "domain": "orchestrator",
            "objective": "Aggregate drive telemetry and wire it through _dispatch_lead in app.py",
            "acceptance": "the run record shows the aggregate"}
    HOLLOW_DIFF = "+++ b/overnight_worker.py\n+def _agg(one):\n+    return one\n"
    WIRED_DIFF = (HOLLOW_DIFF
                  + "+++ b/app.py\n+    _dispatch_lead(q)\n")

    def _gated_deps(self, rec, diff_by_pass, verify_greens=2):
        """Deps whose get_diff answer depends on how many drives have happened, and whose drive
        records each objective it was given (to assert the SCOPE GAP feedback round)."""
        objectives = []

        def start_task(item):
            rec["started"] += 1
            return "t1"

        def drive(task_id, item, budget):
            rec["drove"] += 1
            objectives.append(str(item.get("objective") or ""))

        def verify(task_id, item):
            return {"passed": True, "summary": "green"}

        deps = WorkerDeps(start_task, drive, verify,
                          lambda t: rec.__setitem__("finished", rec["finished"] + 1),
                          lambda t: rec.__setitem__("bailed", rec["bailed"] + 1))
        deps.get_diff = lambda tid: diff_by_pass(rec["drove"])
        return deps, objectives

    def test_scope_gap_forces_feedback_fix_round_then_done(self):
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, objectives = self._gated_deps(
            rec, lambda drove: self.HOLLOW_DIFF if drove == 1 else self.WIRED_DIFF)
        out = work_item(self.ITEM, _Budget(), deps, max_attempts=2)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec["drove"], 2)                    # one forced fix round
        self.assertEqual(rec["finished"], 1)
        self.assertEqual(rec["bailed"], 0)
        # The retry drive got the mechanical gap spelled out, on an item COPY (original untouched).
        self.assertIn("SCOPE GAP", objectives[1])
        self.assertIn("app.py", objectives[1])
        self.assertIn("_dispatch_lead", objectives[1])
        self.assertNotIn("SCOPE GAP", self.ITEM["objective"])
        self.assertEqual(out["attempts"][0]["scope_missed"],
                         ["app.py", "_dispatch_lead"])

    def test_persistent_scope_gap_bails_flagged_for_human(self):
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, objectives = self._gated_deps(rec, lambda drove: self.HOLLOW_DIFF)
        out = work_item(self.ITEM, _Budget(), deps, max_attempts=2)
        self.assertEqual(out["outcome"], "bailed")
        self.assertEqual(rec["drove"], 2)                    # the fix round WAS attempted
        self.assertEqual(rec["bailed"], 1)                   # hollow work reverted
        self.assertEqual(rec["finished"], 0)
        self.assertEqual(out["adversarial"]["verdict"], "bail_human")
        self.assertEqual(out["adversarial"]["reason"], "acceptance_scope_missed")
        self.assertTrue(out["adversarial"]["findings_outstanding"])
        self.assertIn("_dispatch_lead", out["evidence"])

    def test_single_attempt_budget_bails_without_fix_round(self):
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, _ = self._gated_deps(rec, lambda drove: self.HOLLOW_DIFF)
        out = work_item(self.ITEM, _Budget(), deps, max_attempts=1)
        self.assertEqual(out["outcome"], "bailed")
        self.assertEqual(rec["drove"], 1)                    # no attempt budget for a fix round
        self.assertEqual(out["adversarial"]["reason"], "acceptance_scope_missed")

    def test_gate_only_miss_with_clean_review_is_done_with_note(self):
        # B1.2 corroboration: review converged CLEAN + only the gate's substring heuristic
        # complains -> heuristic false positive (the bl-persist-item-timings class) -> DONE with a
        # visible scope_note; the fix round is still forced first.
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, objectives = self._gated_deps(rec, lambda drove: self.HOLLOW_DIFF)
        deps.review = lambda tid, item, b: {"verdict": "pass", "tier": "pair"}
        out = work_item(self.ITEM, _Budget(), deps, max_attempts=2)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec["drove"], 2)                     # the fix round WAS still forced
        self.assertEqual(rec["finished"], 1)
        self.assertEqual(rec["bailed"], 0)
        self.assertEqual(out["adversarial"]["scope_note"], ["app.py", "_dispatch_lead"])
        self.assertEqual(out["adversarial"]["verdict"], "pass")
        self.assertIn("SCOPE GAP", objectives[1])             # feedback round happened

    def test_gate_miss_with_unhappy_review_still_bails(self):
        # Corroborated refusal: review did NOT converge (bail_human) + gate miss -> bail as before.
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, _ = self._gated_deps(rec, lambda drove: self.HOLLOW_DIFF)
        deps.review = lambda tid, item, b: {"verdict": "bail_human", "reason": "stalled"}
        out = work_item(self.ITEM, _Budget(), deps, max_attempts=2)
        self.assertEqual(out["outcome"], "bailed")
        self.assertEqual(out["adversarial"]["reason"], "acceptance_scope_missed")
        self.assertEqual(rec["bailed"], 1)
        self.assertEqual(rec["finished"], 0)

    def test_covered_scope_completes_first_pass(self):
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, _ = self._gated_deps(rec, lambda drove: self.WIRED_DIFF)
        out = work_item(self.ITEM, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(rec["drove"], 1)
        self.assertEqual(rec["finished"], 1)

    def test_diff_fetch_error_fails_open(self):
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}
        deps, _ = self._gated_deps(rec, lambda drove: None)   # fetch error contract: None
        out = work_item(self.ITEM, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")              # fail open, never bail on a glitch
        self.assertEqual(rec["finished"], 1)


class HeartbeatChatLoopTests(unittest.TestCase):
    """bl-drive-heartbeat: the chat tool loop pulses the optional callback every
    CODING_HEARTBEAT_TURNS turns with (turn, tool counts); no callback = byte-identical behavior."""

    FINAL = "done: the change is implemented and the whole unit profile is green."

    def _run_chat(self, heartbeat, tool_turns=12):
        import ollama_client
        from unittest.mock import patch
        # Distinct file paths per turn: identical consecutive calls are treated as idleness and terminate the loop.
        tool_msgs = [{"message": {"role": "assistant", "tool_calls": [
            {"function": {"name": "coding_read", "arguments": {"path": "f%d.py" % i}}}]}} for i in range(tool_turns)]
        final_msg = {"message": {"role": "assistant", "content": self.FINAL}}
        seq = iter(tool_msgs + [final_msg])
        with patch.object(ollama_client, "_chat_once", lambda *a, **k: next(seq)), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", lambda *a, **k: "ok"):
            return ollama_client.chat("h", "m", [{"role": "user", "content": "x"}],
                                      [{"function": {"name": "coding_read"}}], heartbeat=heartbeat)

    def test_heartbeat_fires_at_interval_with_tool_counts(self):
        import config
        pulses = []
        answer, esc = self._run_chat(lambda turn, tl: pulses.append((turn, tl)))
        self.assertEqual(answer, self.FINAL)
        self.assertIsNone(esc)
        self.assertEqual([p[0] for p in pulses], [config.CODING_HEARTBEAT_TURNS])
        # at the pulse, CODING_HEARTBEAT_TURNS-1 tool turns have completed
        self.assertEqual(pulses[0][1], {"coding_read": config.CODING_HEARTBEAT_TURNS - 1})

    def test_no_callback_behavior_unchanged(self):
        answer, esc = self._run_chat(None)
        self.assertEqual(answer, self.FINAL)
        self.assertIsNone(esc)

    def test_zero_interval_disables(self):
        import config
        from unittest.mock import patch
        pulses = []
        with patch.object(config, "CODING_HEARTBEAT_TURNS", 0):
            answer, _ = self._run_chat(lambda turn, tl: pulses.append(turn))
        self.assertEqual(answer, self.FINAL)
        self.assertEqual(pulses, [])

    def test_pulse_exception_never_breaks_the_loop(self):
        def boom(turn, tl):
            raise RuntimeError("pulse sink offline")
        answer, esc = self._run_chat(boom)
        self.assertEqual(answer, self.FINAL)   # fail-soft: author loop completed anyway
        self.assertIsNone(esc)


class TranscriptChatLoopTests(unittest.TestCase):

    def test_transcript_collects_clipped_tool_events(self):
        import ollama_client
        from unittest.mock import patch
        tool_msg = {"message": {"role": "assistant",
                                "tool_calls": [{"function": {"name": "web_search",
                                                             "arguments": {"q": "x" * 500}}}]}}
        final_msg = {"message": {"role": "assistant", "content": "done: collected."}}
        seq = iter([tool_msg, tool_msg, final_msg])
        trail = []
        with patch.object(ollama_client, "_chat_once", lambda *a, **k: next(seq)), \
                patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                patch.object(ollama_client.tools, "run_tool", lambda *a, **k: "R" * 999):
            answer, esc = ollama_client.chat("h", "m", [{"role": "user", "content": "x"}],
                                             [{"function": {"name": "web_search"}}],
                                             transcript=trail)
        self.assertEqual(answer, "done: collected.")
        self.assertIsNone(esc)
        self.assertEqual(len(trail), 2)
        ev = trail[0]
        self.assertEqual(ev["tool"], "web_search")
        self.assertLessEqual(len(ev["args"]), 180)     # args clipped
        self.assertLessEqual(len(ev["result"]), 220)   # result clipped
        self.assertIn("ms", ev)
        self.assertEqual(ev["step"], 1)


class PlanFirstDrivePromptTests(unittest.TestCase):

    def test_drive_prompt_is_plan_first(self):
        app_src = open("app.py", encoding="utf-8").read()
        drive_body = app_src.split("def _ov_drive(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("PLAN FIRST", drive_body)
        self.assertIn("IN THE SAME turn", drive_body)                 # plan must not stall a pass
        self.assertIn("do not merely describe the next step", drive_body)


class DriveTelemetryPassthroughTests(unittest.TestCase):

    def test_drive_telemetry_present_is_stored(self):
        # When the drive dep returns {"telemetry": {...}}, work_item stores it under
        # timings["drive_tele"].
        tele_data = {"turns": 3, "infer_s": 4.2, "tool_s": 1.1,
                     "tools": {"coding_read": 2, "coding_patch": 1}}
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}

        def start_task(item):
            rec["started"] += 1
            return "t1"

        def drive_with_tele(task_id, item, budget):
            rec["drove"] += 1
            return {"telemetry": tele_data}

        deps = WorkerDeps(start_task, drive_with_tele,
                          lambda *a: {"passed": True, "summary": "green"},
                          lambda t: rec.__setitem__("finished", 1),
                          lambda t: rec.__setitem__("bailed", 1))
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertIn("drive_tele", out["timings"])
        self.assertEqual(out["timings"]["drive_tele"], tele_data)
        self.assertEqual(rec["finished"], 1)

    def test_drive_telemetry_absent_does_not_add_key(self):
        # When the drive dep returns None (the existing contract), work_item does NOT
        # add a "drive_tele" key to timings. Existing behavior is preserved.
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}

        def start_task(item):
            rec["started"] += 1
            return "t1"

        def drive_no_tele(task_id, item, budget):
            rec["drove"] += 1
            return None  # existing mock contract

        deps = WorkerDeps(start_task, drive_no_tele,
                          lambda *a: {"passed": True, "summary": "green"},
                          lambda t: rec.__setitem__("finished", 1),
                          lambda t: rec.__setitem__("bailed", 1))
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertNotIn("drive_tele", out["timings"])

    def test_drive_returns_empty_dict_does_not_add_key(self):
        # When drive returns {} (no telemetry key), the key is not added.
        rec = {"started": 0, "drove": 0, "finished": 0, "bailed": 0}

        def start_task(item):
            rec["started"] += 1
            return "t1"

        def drive_empty(task_id, item, budget):
            rec["drove"] += 1
            return {}

        deps = WorkerDeps(start_task, drive_empty,
                          lambda *a: {"passed": True, "summary": "green"},
                          lambda t: rec.__setitem__("finished", 1),
                          lambda t: rec.__setitem__("bailed", 1))
        out = work_item({"id": "a", "domain": "orchestrator"}, _Budget(), deps)
        self.assertEqual(out["outcome"], "done")
        self.assertNotIn("drive_tele", out["timings"])


if __name__ == "__main__":
    unittest.main()
