SUPERVISED LOCAL CODING JOB — A1 harness, fix 2 (second attempt): visible tests for the model, hidden tests for the grade, ALL in the child process

Workspace: `sample`, HEAD `f55e620`, clean (your previous attempt `abr-1791166957-f178fa` is archived and reverted).
Governing spec: Z:\Documentation\LLM-Project\34-A1-Agentic-Build-Family-Spec.md — read § "What A1 is" IN FULL first.
Your tool loop no longer has a 50-turn cap (deployed 2026-10-05 02:57Z): it ends when you answer, when you repeat
calls without new work, or at this job's time budget. Run the tests as often as you need.

Why the previous attempt was set aside: it loaded the exercise's test module INSIDE the harness process, which then
had to fight `sys.modules` collisions and redact names. That also runs model-written code in the harness itself, so an
infinite loop in a model's solution (the 3.8's `forth` did exactly this tonight) would hang the whole order instead of
one child. **The harness process must never import an exercise's test module or the model's code.**

The design, pinned:
1. Replace the inline `-c` program in `grade()` with a module-level constant `CHILD` (a Python source string) run as
   `PY + ["-c", CHILD, scratch, module, mode]`, `mode` = `"visible"` or `"all"`. CHILD:
   - inserts `scratch` at `sys.path[0]`;
   - loads `module` with `unittest.defaultTestLoader.loadTestsFromName(module)` and flattens the suite to test cases;
   - `ids = sorted(t.id() for t in cases)`; VISIBLE = `ids[0::2]` when `len(ids) > 1`, else empty (a one-test exercise
     keeps its only test hidden);
   - mode `"visible"`: runs ONLY the visible cases with `unittest.TextTestRunner(verbosity=2)`;
   - mode `"all"`: runs every case the same way, then prints ONE final stdout line
     `A1-RESULT ` + `json.dumps({"visible": [...visible ids], "passed": [...ids that passed]})`
     (collect pass/fail with a small `unittest.TextTestResult` subclass or from the result's failures/errors lists).
   Keep the `PY` constant and the existing `cwd`, `timeout` and `env` arguments exactly as they are.
2. `grade(workspace_dir, exercise_dir)` keeps its signature and return `(passed, total, output)`; it runs mode `"all"`.
   Add `grade_detail(...)` (or extend the parse) that reads the `A1-RESULT` line and returns
   `visible_passed, visible_total, hidden_passed, hidden_total`.
3. `run_tests` runs mode `"visible"` and returns that child's output. With no visible tests it returns one line:
   `No visible tests for this exercise; your work is graded on hidden tests after finish.`
4. The final grade after `finish` uses mode `"all"`; the row keeps `tests_passed`/`tests_total` and adds
   `visible_passed`, `visible_total`, `hidden_passed`, `hidden_total`. `passed` = every test passes.
5. Update the `run_tests` tool description: it runs the visible subset of the acceptance tests.
6. Aider mode (`_aider_loop`) unchanged.

Acceptance (in `tests/test_a1_harness.py`, synthetic exercises as the existing tests build them):
a. The split is deterministic, covers every test exactly once, and leaves at least one hidden test (incl. one-test).
b. `run_tests` output never contains a hidden test's method name (assert on the returned text).
c. A solution that passes the visible tests and fails a hidden one ends `passed == False`, `hidden_passed < hidden_total`.
d. The reference passes all; the untouched stub fails (the existing control).
e. After `run_tests` and the final grade, the exercise's test module name is NOT in the harness's `sys.modules`.
f. All existing tests stay green, unchanged.

Constraints: standard library only; touch only `a1_harness.py` and `tests/test_a1_harness.py`; no turn caps or clock
limits added; do not edit `a1_ollama.py` or its tests. Before the final report run the full `unit` profile and quote
its summary line. Final report: the unit command and its full summary, the files changed, the added test count.
