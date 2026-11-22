SUPERVISED LOCAL CODING JOB — A1 harness, fix 2: the model must not see the grading tests

Workspace: `sample`, HEAD `f55e620` (supervisor sync: the grader now runs its child as
`PY + ["-c", "import sys; sys.path.insert(0, sys.argv.pop(1)); import unittest; unittest.main(module=None)", scratch, "-v", <module>]`
— keep that form and the `PY` constant; it is why grading works under an embeddable Python).
Governing spec: Z:\Documentation\LLM-Project\34-A1-Agentic-Build-Family-Spec.md — read § "What A1 is" IN FULL before
writing; its rule: "The visible/hidden split is the point: visible tests let a model check itself; hidden ones keep
it honest."

What the supervisor found: the first live A1 run (qwen3.8:27b, 34 Python exercises) solved 33/34. The model's
`run_tests` tool calls `grade(ws.root, exercise_dir)` — it runs the exercise's HIDDEN `<name>_test.py` and returns the
full unittest -v output (test names, assertion messages, expected values). So nothing is hidden: the model iterates
against the grader as often as it likes, and the condition saturates (33/34 cannot separate strong models).

The fix, pinned:
1. Split each exercise's tests deterministically: load `<name>_test.py` with `unittest.defaultTestLoader`, take the
   test ids in the loader's (sorted) order, VISIBLE = ids at even positions (0, 2, 4, …), HIDDEN = ids at odd positions.
   An exercise with exactly one test: that test is HIDDEN and the visible set is empty.
2. `run_tests` runs ONLY the visible ids (same child-process form, filtering the loaded suite to the visible ids) and
   returns that output. With no visible tests it returns a one-line note saying so. Its output must never contain a
   hidden test's name.
3. The final grade after `finish` runs ALL tests, as today. The row keeps `tests_passed`/`tests_total` (all tests) and
   adds `visible_passed`, `visible_total`, `hidden_passed`, `hidden_total`. `passed` = every test passes.
4. Update the `run_tests` tool description to say it runs the visible subset of the acceptance tests.
5. Aider mode (`_aider_loop`) is unchanged.

Acceptance (tests in `tests/test_a1_harness.py`, synthetic exercises as the existing tests do):
a. The split is deterministic, covers every test exactly once, and leaves at least one hidden test (including the
   one-test case).
b. `run_tests` output never contains any hidden test's name (assert on the actual returned text).
c. A scripted solution that passes the visible tests but fails a hidden one ends with `passed == False` and
   `hidden_passed < hidden_total`.
d. The reference solution passes all tests; the untouched stub fails (the existing control still holds).
e. All 27 existing tests in the workspace stay green, unchanged.

Constraints: standard library only; touch only `a1_harness.py` and `tests/test_a1_harness.py`; no turn caps or clock
limits added. Before your final report run the full `unit` profile and quote its summary line.
Final report: the unit command and its full summary, the files changed, the added test count, any open question.
