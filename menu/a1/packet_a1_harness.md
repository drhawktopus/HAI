SUPERVISED LOCAL CODING JOB — A1 agentic-build harness, phase 1 (Python)

Objective
Workspace: `sample` (the coding runner's disposable sandbox; its `unit` command is
`python -m unittest discover -s tests -t tests -v`). The supervising session ports the reviewed files into the lab's
hard-bench folder afterwards; nothing here is deployed.
Write `a1_harness.py` (standard library only) and `tests/test_a1_harness.py` in this workspace. The harness lets a
language model solve one coding exercise in a tool-using loop and grades the result with the exercise's own unit tests,
which the model never sees. It will later run as a factory order on the GPU boxes; here it is built and proven offline
with a fake model. Spec: Z:\Documentation\LLM-Project\34-A1-Agentic-Build-Family-Spec.md (read it in full first).

Exercise layout (Aider polyglot, Python): a folder `<name>/` holding `.docs/instructions.md` (the brief),
`<name>.py` (the stub the model must complete), `<name>_test.py` (the hidden unit tests, unittest), and
`.meta/example.py` (a reference solution — never shown to the model).

Interface (pin exactly)
- `run_task(exercise_dir, chat_fn, mode="agentic", work_root=None) -> dict`
  - copies the exercise into a fresh temp workspace WITHOUT `<name>_test.py` and WITHOUT `.meta/`;
  - `chat_fn(messages, tools) -> {"content": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}` is
    injected (ollama-style tool calling is wired in later; tests pass a fake);
  - tools offered to the model: `list_files()`, `read_file(path)`, `write_file(path, content)`,
    `run_tests()` (runs the hidden tests against the current workspace and returns ONLY the unittest output text,
    never the test source), `finish(note)`;
  - mode "agentic": the loop runs until `finish` or until the model makes NO PROGRESS (the same tool call with the same
    arguments 3 times in a row, or 8 consecutive turns with no file change and no new test result); there is NO turn
    cap and NO wall-clock limit;
  - mode "aider": the brief + stub go in one message; the model answers with the whole new file; tests run; on
    failure the test output is sent back once; that second answer is final (Aider's 2-attempt protocol);
  - returns {"exercise", "mode", "passed" (all hidden tests pass), "tests_passed", "tests_total", "turns",
    "tool_counts", "ended" ("finish" | "no_progress" | "error"), "wall_s"}.
- `grade(workspace_dir, exercise_dir) -> (passed_n, total_n, output_text)`: copies the hidden test file into a scratch
  copy of the workspace and runs `python -m unittest` in a child process with the workspace as cwd, no network use.
- Path safety: `read_file`/`write_file`/`list_files` refuse any path outside the temp workspace and never expose
  `<name>_test.py` or `.meta/`.

Acceptance criteria (tests/test_a1_harness.py must pass with the workspace's allow-listed test command)
1. A synthetic exercise built inside the test (e.g. `adder/` with instructions, a stub `def add(a, b): pass`, a
   2-test `adder_test.py`, `.meta/example.py`): a fake chat_fn scripted to write the example and call finish ->
   `passed` True, `ended` "finish".
2. A fake chat_fn that writes wrong code and finishes -> `passed` False, `tests_passed` < `tests_total`.
3. A fake chat_fn that repeats `read_file("adder.py")` forever -> `ended` "no_progress" (and the test terminates).
4. `read_file("adder_test.py")`, `read_file(".meta/example.py")`, `read_file("../x")` and `list_files()` never
   reveal the hidden test or the example (assert on the returned text).
5. Mode "aider": wrong first answer + correct second answer -> `passed` True with exactly 2 model calls; wrong twice
   -> `passed` False.
6. `run_tests()` output never contains the test file's source text (assert a unique string from it is absent).

Constraints
- Standard library only. Touch only `a1_harness.py` and `tests/test_a1_harness.py`.
- The `unit` command runs with `tests/` as the top-level directory, so `tests/test_a1_harness.py` must put the
  workspace root on `sys.path` before importing `a1_harness`. The workspace's existing tests must stay green.
- No turn caps, no clock limits on the model (lab doctrine: no arbitrary limits); "no progress" ends a stuck loop.
- Do not invent extra features; anything you believe is missing goes in the final report, not in the code.

Stop and report (do not improvise) if: the workspace's allow-listed test command cannot run
`tests/test_a1_harness.py`; or a criterion above contradicts another.

Final report
The test command and its full output (counts), the files changed, and any open question.
