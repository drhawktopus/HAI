SUPERVISED LOCAL CODING JOB — A1 harness, fix 1 (found by the real-exercise control)

Workspace: `sample` (same as the previous job; `a1_harness.py` and `tests/test_a1_harness.py` at commit 7b84284).

What the supervisor measured: a control over the REAL task set (34 Python exercises of Aider polyglot) graded each
exercise twice with `grade()` — once with `.meta/example.py` copied in as `<name>.py` (must pass every hidden test),
once with the untouched stub (must not). Result: reference passes 14/34; all 20 failures are exercises whose folder
name has a hyphen, e.g. folder `food-chain` holding `food_chain.py`, `food_chain_test.py`, `.meta/example.py`.
`grade()` reported `ERROR: no hidden test file found for exercise food-chain` and returned (0, 0, ...).

Fix, exactly:
1. The exercise name used for file names is the folder name with every `-` replaced by `_` (Exercism convention):
   folder `food-chain` -> `food_chain.py`, `food_chain_test.py`. Apply it everywhere a file name is derived from the
   folder (copying, hiding the test, grading, path refusals).
2. A missing hidden test file is an ERROR, never a quiet "not passed": `run_task` must return `ended: "error"` with
   the reason, and `grade()` must keep returning total 0 so a caller can tell it apart from a real failure.
3. Add tests: (a) a synthetic HYPHENATED exercise (folder `two-words`, files `two_words.py`, `two_words_test.py`,
   `.meta/example.py`) — the scripted-correct fake passes, the wrong one fails, and `two_words_test.py` is never
   readable by the model; (b) an exercise folder with no test file -> `ended == "error"`.

Constraints: unchanged from the previous job (standard library only; touch only `a1_harness.py` and
`tests/test_a1_harness.py`; no turn caps or clock limits; existing tests stay green).

Final report: the `unit` test command and its full output, the files changed, any open question.
