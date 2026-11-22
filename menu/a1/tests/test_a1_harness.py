"""Acceptance tests for the A1 agentic-build harness (phase 1, Python).

The workspace's `unit` profile runs `python -m unittest discover -s tests -t tests -v`,
so this file puts the workspace root on sys.path before importing a1_harness.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from typing import Any, Dict, List

# make the workspace root importable regardless of discovery top-level dir
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import a1_harness  # noqa: E402


# ---------------------------------------------------------------------------
# exercise fixture
# ---------------------------------------------------------------------------

# Unique strings so criterion 4/6 assertions are unambiguous.
HIDDEN_MARKER = "HIDDEN-TEST-MARKER-9f3c"          # must never leak to the model
EXAMPLE_MARKER = "EXAMPLE-REF-MARKER-1a7e"          # must never leak to the model

INSTRUCTIONS = (
    "# Adder\n\n"
    "Implement `add(a, b)` so that it returns the sum of its two integer arguments.\n"
    "It must also work when either argument is a float.\n"
)

STUB = "def add(a, b):\n    pass\n"

HIDDEN_TEST = (
    "import unittest\n"
    "\n"
    "from adder import add\n"
    "\n"
    "UNIQUE_SENTINEL = '" + HIDDEN_MARKER + "'\n"
    "\n"
    "class AdderTests(unittest.TestCase):\n"
    "    def test_add_ints(self):\n"
    "        self.assertEqual(add(2, 3), 5)\n"
    "\n"
    "    def test_add_floats(self):\n"
    "        self.assertAlmostEqual(add(1.5, 2.25), 3.75)\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)

EXAMPLE = (
    "# reference solution\n"
    "REF = '" + EXAMPLE_MARKER + "'\n"
    "\n"
    "def add(a, b):\n"
    "    return a + b\n"
)

WRONG_SOLUTION = "def add(a, b):\n    return a - b\n"


def make_exercise(base: str) -> str:
    """Create the synthetic `adder/` exercise under *base*; returns its path."""
    ex = os.path.join(base, "adder")
    os.makedirs(os.path.join(ex, ".docs"), exist_ok=True)
    os.makedirs(os.path.join(ex, ".meta"), exist_ok=True)
    with open(os.path.join(ex, ".docs", "instructions.md"), "w", encoding="utf-8") as f:
        f.write(INSTRUCTIONS)
    with open(os.path.join(ex, "adder.py"), "w", encoding="utf-8") as f:
        f.write(STUB)
    with open(os.path.join(ex, "adder_test.py"), "w", encoding="utf-8") as f:
        f.write(HIDDEN_TEST)
    with open(os.path.join(ex, ".meta", "example.py"), "w", encoding="utf-8") as f:
        f.write(EXAMPLE)
    return ex


# Synthetic HYPHENATED exercise (folder `two-words`, files `two_words.py` /
# `two_words_test.py`) — mirrors the Exercism convention where the folder
# name uses a hyphen but the Python files use underscores.
TWO_WORDS_MARKER = "TWO-WORDS-HIDDEN-7c2e"

TWO_WORDS_INSTRUCTIONS = (
    "# Two Words\n\n"
    "Implement `double_word(a, b)` so that it returns `a` followed by a space\n"
    "followed by `b`, then the total of the lengths of the two words.\n"
)

TWO_WORDS_STUB = "def double_word(a, b):\n    pass\n"

TWO_WORDS_TEST = (
    "import unittest\n"
    "\n"
    "from two_words import double_word\n"
    "\n"
    "UNIQUE_SENTINEL = '" + TWO_WORDS_MARKER + "'\n"
    "\n"
    "class TwoWordsTests(unittest.TestCase):\n"
    "    def test_two_words(self):\n"
    "        self.assertEqual(double_word('ab', 'cd'), 'ab cd 4')\n"
    "\n"
    "    def test_longer(self):\n"
    "        self.assertEqual(double_word('hello', 'world'), 'hello world 10')\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)

TWO_WORDS_EXAMPLE = (
    "# reference solution\n"
    "def double_word(a, b):\n"
    "    return a + ' ' + b + ' ' + str(len(a) + len(b))\n"
)

TWO_WORDS_WRONG = "def double_word(a, b):\n    return a + b\n"


def make_two_words_exercise(base: str) -> str:
    """Create the synthetic `two-words/` exercise under *base*."""
    ex = os.path.join(base, "two-words")
    os.makedirs(os.path.join(ex, ".docs"), exist_ok=True)
    os.makedirs(os.path.join(ex, ".meta"), exist_ok=True)
    with open(os.path.join(ex, ".docs", "instructions.md"), "w", encoding="utf-8") as f:
        f.write(TWO_WORDS_INSTRUCTIONS)
    with open(os.path.join(ex, "two_words.py"), "w", encoding="utf-8") as f:
        f.write(TWO_WORDS_STUB)
    with open(os.path.join(ex, "two_words_test.py"), "w", encoding="utf-8") as f:
        f.write(TWO_WORDS_TEST)
    with open(os.path.join(ex, ".meta", "example.py"), "w", encoding="utf-8") as f:
        f.write(TWO_WORDS_EXAMPLE)
    return ex


def make_no_test_exercise(base: str) -> str:
    """Create an exercise folder with NO hidden test file (only stub + .meta)."""
    ex = os.path.join(base, "no-test-exercise")
    os.makedirs(os.path.join(ex, ".docs"), exist_ok=True)
    os.makedirs(os.path.join(ex, ".meta"), exist_ok=True)
    with open(os.path.join(ex, ".docs", "instructions.md"), "w", encoding="utf-8") as f:
        f.write(INSTRUCTIONS)
    with open(os.path.join(ex, "no_test_exercise.py"), "w", encoding="utf-8") as f:
        f.write(STUB)
    with open(os.path.join(ex, ".meta", "example.py"), "w", encoding="utf-8") as f:
        f.write(EXAMPLE)
    return ex


# ---------------------------------------------------------------------------
# fake models
# ---------------------------------------------------------------------------

def fake_agentic_success(exercise_dir: str) -> Dict[str, Any]:
    """Scripted model: list, read brief, read stub, write correct solution, finish."""
    example_path = os.path.join(exercise_dir, ".meta", "example.py")
    with open(example_path, "r", encoding="utf-8") as f:
        solution = f.read()
    plan = [
        {"name": "list_files", "arguments": {}},
        {"name": "read_file", "arguments": {"path": "adder.py"}},
        {"name": "write_file", "arguments": {"path": "adder.py", "content": solution}},
        {"name": "finish", "arguments": {"note": "implemented add()"}},
    ]
    it = iter(plan)

    def chat_fn(messages, tools):
        return {"content": "", "tool_calls": [next(it)]}

    return chat_fn


def fake_agentic_wrong() -> Dict[str, Any]:
    """Writes a wrong solution, then finishes."""
    plan = [
        {"name": "write_file", "arguments": {"path": "adder.py", "content": WRONG_SOLUTION}},
        {"name": "finish", "arguments": {"note": "done (wrong on purpose)"}},
    ]
    it = iter(plan)

    def chat_fn(messages, tools):
        return {"content": "", "tool_calls": [next(it)]}

    return chat_fn


def fake_agentic_loop() -> Dict[str, Any]:
    """Repeats read_file("adder.py") forever — must be cut by no_progress."""
    def chat_fn(messages, tools):
        return {"content": "",
                "tool_calls": [{"name": "read_file", "arguments": {"path": "adder.py"}}]}
    return chat_fn


def fake_agentic_runtests() -> Dict[str, Any]:
    """Writes a wrong solution, runs the hidden tests once, then finishes."""
    plan = [
        {"name": "write_file", "arguments": {"path": "adder.py", "content": WRONG_SOLUTION}},
        {"name": "run_tests", "arguments": {}},
        {"name": "finish", "arguments": {"note": "done"}},
    ]
    it = iter(plan)

    def chat_fn(messages, tools):
        return {"content": "", "tool_calls": [next(it)]}

    return chat_fn


def fake_aider(first: str, second: str):
    """Returns *first* then *second* as whole-file answers (2-attempt protocol)."""
    seq = [first, second]

    def chat_fn(messages, tools):
        return {"content": seq.pop(0), "tool_calls": []}

    return chat_fn


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class A1HarnessTests(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="a1_test_base_")
        self.exercise_dir = make_exercise(self.base)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # -- criterion 1 ---------------------------------------------------------
    def test_agentic_correct_solution_passes(self):
        result = a1_harness.run_task(self.exercise_dir,
                                     fake_agentic_success(self.exercise_dir))
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["ended"], "finish")
        self.assertEqual(result["tests_passed"], 2)
        self.assertEqual(result["tests_total"], 2)
        self.assertEqual(result["exercise"], "adder")
        self.assertEqual(result["mode"], "agentic")
        self.assertIn("write_file", result["tool_counts"])
        self.assertIn("finish", result["tool_counts"])
        self.assertGreaterEqual(result["turns"], 1)

    # -- criterion 2 ---------------------------------------------------------
    def test_agentic_wrong_solution_fails(self):
        result = a1_harness.run_task(self.exercise_dir, fake_agentic_wrong())
        self.assertFalse(result["passed"], result)
        self.assertEqual(result["ended"], "finish")
        self.assertLess(result["tests_passed"], result["tests_total"])
        self.assertEqual(result["tests_total"], 2)

    # -- criterion 3 ---------------------------------------------------------
    def test_agentic_repeated_call_ends_no_progress(self):
        result = a1_harness.run_task(self.exercise_dir, fake_agentic_loop())
        self.assertEqual(result["ended"], "no_progress", result)
        # 3 identical calls trip the cutter, which nudges once; 3 more after the nudge end it (no turn cap, no clock)
        self.assertEqual(result["tool_counts"].get("read_file"), 3 * (a1_harness.NO_PROGRESS_NUDGES + 1))
        self.assertEqual(result["tool_counts"].get("nudge"), a1_harness.NO_PROGRESS_NUDGES)
        self.assertFalse(result["passed"])

    # -- criterion 4 ---------------------------------------------------------
    def test_hidden_files_never_exposed(self):
        collected: List[str] = []
        plan = [
            {"name": "read_file", "arguments": {"path": "adder_test.py"}},
            {"name": "read_file", "arguments": {"path": ".meta/example.py"}},
            {"name": "read_file", "arguments": {"path": "../x"}},
            {"name": "list_files", "arguments": {}},
            {"name": "finish", "arguments": {"note": "done"}},
        ]
        it = iter(plan)

        def chat_fn(messages, tools):
            return {"content": "", "tool_calls": [next(it)]}

        result = a1_harness.run_task(self.exercise_dir, chat_fn)
        # re-run the same pokes directly against a live workspace and inspect text
        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            ws = a1_harness._Workspace(ws_root)
            for call in plan[:-1]:
                collected.append(a1_harness._dispatch_tool(
                    ws, call["name"], call["arguments"], self.exercise_dir))
            listing = ws.list_files()
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

        blob = "\n".join(collected)
        self.assertNotIn(HIDDEN_MARKER, blob, "hidden test source leaked")
        self.assertNotIn(EXAMPLE_MARKER, blob, "reference example leaked")
        self.assertNotIn("HIDDEN-TEST-MARKER", blob)
        for part in listing.splitlines():
            self.assertNotIn("_test.py", part, "hidden test listed: %r" % part)
            self.assertNotIn(".meta", part, "meta dir listed: %r" % part)
        self.assertIn("adder.py", listing)
        self.assertEqual(result["ended"], "finish")

    # -- criterion 5 ---------------------------------------------------------
    def test_aider_wrong_then_correct(self):
        with open(os.path.join(self.exercise_dir, ".meta", "example.py"),
                  "r", encoding="utf-8") as f:
            solution = f.read()
        chat_fn = fake_aider(WRONG_SOLUTION, solution)
        result = a1_harness.run_task(self.exercise_dir, chat_fn, mode="aider")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["ended"], "finish")
        self.assertEqual(result["tool_counts"].get("model"), 2,
                         "expected exactly 2 model calls, got %r" % result["tool_counts"])

    def test_aider_wrong_twice_fails(self):
        chat_fn = fake_aider(WRONG_SOLUTION, WRONG_SOLUTION)
        result = a1_harness.run_task(self.exercise_dir, chat_fn, mode="aider")
        self.assertFalse(result["passed"], result)
        self.assertEqual(result["tool_counts"].get("model"), 2)
        self.assertLess(result["tests_passed"], result["tests_total"])

    # -- criterion 6 ---------------------------------------------------------
    def test_run_tests_output_never_contains_test_source(self):
        plan = [
            {"name": "write_file", "arguments": {"path": "adder.py", "content": WRONG_SOLUTION}},
            {"name": "run_tests", "arguments": {}},
            {"name": "finish", "arguments": {"note": "done"}},
        ]
        it = iter(plan)

        def chat_fn(messages, tools):
            return {"content": "", "tool_calls": [next(it)]}

        # run once for shape, then inspect the tool output directly
        a1_harness.run_task(self.exercise_dir, chat_fn)

        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            ws = a1_harness._Workspace(ws_root)
            ws.write_file("adder.py", WRONG_SOLUTION)
            out = a1_harness._dispatch_tool(ws, "run_tests", {}, self.exercise_dir)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

        self.assertIsInstance(out, str)
        self.assertNotIn(HIDDEN_MARKER, out, "run_tests leaked the test source")
        # a unique literal source line of the test file must not appear
        self.assertNotIn("UNIQUE_SENTINEL = ", out, "run_tests leaked test source line")
        # NOTE: with fix 2, run_tests shows only the VISIBLE subset (one of
        # the two tests for this exercise), and never the hidden test's
        # method name (see A1VisibleHiddenSplitTests below).
        self.assertIn("Ran 1 test", out)  # visible subset only

    # -- extra: grade() direct ------------------------------------------------
    def test_grade_direct(self):
        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            ws = a1_harness._Workspace(ws_root)
            ws.write_file("adder.py", WRONG_SOLUTION)
            passed, total, out = a1_harness.grade(ws_root, self.exercise_dir)
            self.assertEqual(total, 2)
            self.assertEqual(passed, 0)
            self.assertIn("Ran 2 tests", out)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

    def test_grade_direct_visible_hidden_split(self):
        """Fix 2: grade_detail splits the result into visible/hidden halves."""
        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            with open(os.path.join(self.exercise_dir, ".meta", "example.py"),
                      "r", encoding="utf-8") as f:
                solution = f.read()
            ws = a1_harness._Workspace(ws_root)
            ws.write_file("adder.py", solution)
            vp, vt, hp, ht, out = a1_harness.grade_detail(
                ws_root, self.exercise_dir)
            self.assertEqual((vp, vt, hp, ht), (1, 1, 1, 1),
                             "reference solution must split 1/1: %r" % ((vp, vt, hp, ht),))
            self.assertIn("Ran 2 tests", out)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Hyphenated exercise (Exercism convention): folder `two-words` holds
# `two_words.py` / `two_words_test.py`.
# ---------------------------------------------------------------------------

class A1HyphenatedExerciseTests(unittest.TestCase):
    """The harness must derive file names from the folder name with
    every hyphen replaced by an underscore, everywhere a file name is
    derived from the folder (copying, hiding, grading, path refusals).
    """

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="a1_test_base_two_words_")
        self.exercise_dir = make_two_words_exercise(self.base)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _read_example(self) -> str:
        with open(os.path.join(self.exercise_dir, ".meta", "example.py"),
                  "r", encoding="utf-8") as f:
            return f.read()

    def test_hyphenated_correct_solution_passes(self):
        """The scripted-correct fake (reads .meta/example.py and writes
        two_words.py) must pass every hidden test of the hyphenated
        exercise."""
        solution = self._read_example()
        plan = [
            {"name": "list_files", "arguments": {}},
            {"name": "read_file", "arguments": {"path": "two_words.py"}},
            {"name": "write_file",
             "arguments": {"path": "two_words.py", "content": solution}},
            {"name": "finish", "arguments": {"note": "implemented double_word()"}},
        ]
        it = iter(plan)

        def chat_fn(messages, tools):
            return {"content": "", "tool_calls": [next(it)]}

        result = a1_harness.run_task(self.exercise_dir, chat_fn)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["ended"], "finish", result)
        self.assertEqual(result["tests_passed"], 2)
        self.assertEqual(result["tests_total"], 2)
        # The exercise name reported is the underscored stem, not the folder.
        self.assertEqual(result["exercise"], "two_words")
        self.assertIn("write_file", result["tool_counts"])
        self.assertIn("finish", result["tool_counts"])

    def test_hyphenated_wrong_solution_fails(self):
        """A wrong solution written to two_words.py must fail the hidden
        tests (grade() total 2, passed < total)."""
        plan = [
            {"name": "write_file",
             "arguments": {"path": "two_words.py", "content": TWO_WORDS_WRONG}},
            {"name": "finish", "arguments": {"note": "done (wrong on purpose)"}},
        ]
        it = iter(plan)

        def chat_fn(messages, tools):
            return {"content": "", "tool_calls": [next(it)]}

        result = a1_harness.run_task(self.exercise_dir, chat_fn)
        self.assertFalse(result["passed"], result)
        self.assertEqual(result["ended"], "finish", result)
        self.assertLess(result["tests_passed"], result["tests_total"], result)
        self.assertEqual(result["tests_total"], 2)

    def test_hyphenated_hidden_test_never_readable(self):
        """The model must never be able to read two_words_test.py or
        .meta/example.py, and list_files must not surface either."""
        plan = [
            {"name": "read_file", "arguments": {"path": "two_words_test.py"}},
            {"name": "read_file", "arguments": {"path": ".meta/example.py"}},
            {"name": "read_file", "arguments": {"path": "../x"}},
            {"name": "list_files", "arguments": {}},
            {"name": "finish", "arguments": {"note": "done"}},
        ]
        it = iter(plan)

        def chat_fn(messages, tools):
            return {"content": "", "tool_calls": [next(it)]}

        a1_harness.run_task(self.exercise_dir, chat_fn)

        # Re-run the same pokes directly against a live workspace.
        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            ws = a1_harness._Workspace(ws_root)
            collected = [
                a1_harness._dispatch_tool(
                    ws, call["name"], call["arguments"], self.exercise_dir)
                for call in plan[:-1]
            ]
            listing = ws.list_files()
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

        blob = "\n".join(collected)
        self.assertNotIn(TWO_WORDS_MARKER, blob, "hidden test source leaked")
        self.assertNotIn("UNIQUE_SENTINEL =", blob, "test source line leaked")
        for part in listing.splitlines():
            self.assertNotIn("_test.py", part, "hidden test listed: %r" % part)
            self.assertNotIn(".meta", part, "meta dir listed: %r" % part)
        # The stub IS visible.
        self.assertIn("two_words.py", listing)
        # And the hidden test file is NOT in the workspace at all.
        self.assertFalse(os.path.isfile(os.path.join(ws_root, "two_words_test.py")))


# ---------------------------------------------------------------------------
# Missing hidden test file = ERROR, not a quiet "not passed"
# ---------------------------------------------------------------------------

class A1VisibleHiddenSplitTests(unittest.TestCase):
    """Fix 2: the harness process never imports the exercise's test module.

    The split is deterministic (sorted ids, visible = ids[0::2] when there
    is more than one test, else empty), covers every test exactly once, and
    leaves at least one hidden test even for a one-test exercise.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="a1_test_base_split_")
        self.exercise_dir = make_exercise(self.base)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _split(self, exercise_dir, solution: str = ""):
        ws_root = a1_harness._setup_workspace(exercise_dir, self.base)
        try:
            ws = a1_harness._Workspace(ws_root)
            if solution:
                stub = os.path.basename(exercise_dir).replace("-", "_") + ".py"
                ws.write_file(stub, solution)
            vp, vt, hp, ht, out = a1_harness.grade_detail(ws_root, exercise_dir)
            det = a1_harness._parse_result_line(out)
            self.assertIsNotNone(det, "A1-RESULT line missing from: %r" % out)
            visible, passed = det
            return visible, passed, (vp, vt, hp, ht), out
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

    # -- (a) deterministic, covers every test once, >=1 hidden -------------
    def test_split_deterministic_and_complete(self):
        with open(os.path.join(self.exercise_dir, ".meta", "example.py"),
                  "r", encoding="utf-8") as f:
            solution = f.read()
        visible, passed, counts, _out = self._split(self.exercise_dir, solution)
        # exactly the two tests of the adder exercise
        self.assertEqual(sorted(visible),
                         ["adder_test.AdderTests.test_add_floats"])
        vp, vt, hp, ht = counts
        self.assertEqual((vp, vt, hp, ht), (1, 1, 1, 1))
        # run twice: identical split (deterministic)
        visible2, passed2, counts2, _out2 = self._split(self.exercise_dir, solution)
        self.assertEqual(visible, visible2)
        self.assertEqual(counts, counts2)
        # covers every test exactly once: visible + hidden == total, disjoint
        all_ids = visible + ["adder_test.AdderTests.test_add_ints"]
        self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_split_three_tests_two_visible_one_hidden(self):
        ex = self._make_three_test_exercise()
        with open(os.path.join(ex, ".meta", "example.py"),
                  "r", encoding="utf-8") as f:
            solution = f.read()
        visible, passed, (vp, vt, hp, ht), out = self._split(ex, solution)
        # ids: test_a, test_b, test_c (sorted) -> visible = test_a, test_c
        self.assertEqual(sorted(visible), ["three_test.ThreeTests.test_a",
                                           "three_test.ThreeTests.test_c"])
        self.assertEqual((vp, vt, hp, ht), (2, 2, 1, 1),
                         "reference passes all 3: %r %r" % ((vp, vt, hp, ht), out))

    def test_split_one_test_keeps_it_hidden(self):
        ex = self._make_one_test_exercise()
        # grade_detail: no visible tests, one hidden (untouched stub -> fails)
        visible, passed, (vp, vt, hp, ht), out = self._split(ex, "")
        self.assertEqual(visible, [], "one-test exercise keeps its only test hidden")
        self.assertEqual((vp, vt, hp, ht), (0, 0, 0, 1),
                         "one hidden test, failing: %r %r" % ((vp, vt, hp, ht), out))
        # run_tests must surface the no-visible notice, not the test name
        ws_root = a1_harness._setup_workspace(ex, self.base)
        try:
            out2 = a1_harness._run_tests_output(ws_root, ex)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)
        self.assertIn("No visible tests for this exercise", out2)
        self.assertNotIn("test_only", out2)

    def _make_three_test_exercise(self):
        ex = os.path.join(self.base, "three")
        os.makedirs(os.path.join(ex, ".docs"), exist_ok=True)
        os.makedirs(os.path.join(ex, ".meta"), exist_ok=True)
        with open(os.path.join(ex, ".docs", "instructions.md"), "w", encoding="utf-8") as f:
            f.write("# Three\n\nImplement `triple`.\n")
        with open(os.path.join(ex, "three.py"), "w", encoding="utf-8") as f:
            f.write("def triple(x):\n    pass\n")
        with open(os.path.join(ex, "three_test.py"), "w", encoding="utf-8") as f:
            f.write(
                "import unittest\n\n"
                "from three import triple\n\n"
                "class ThreeTests(unittest.TestCase):\n"
                "    def test_a(self):\n        self.assertEqual(triple(1), 3)\n"
                "    def test_b(self):\n        self.assertEqual(triple(2), 6)\n"
                "    def test_c(self):\n        self.assertEqual(triple(3), 9)\n"
            )
        with open(os.path.join(ex, ".meta", "example.py"), "w", encoding="utf-8") as f:
            f.write("def triple(x):\n    return x * 3\n")
        return ex

    def _make_one_test_exercise(self):
        ex = os.path.join(self.base, "one")
        os.makedirs(os.path.join(ex, ".docs"), exist_ok=True)
        os.makedirs(os.path.join(ex, ".meta"), exist_ok=True)
        with open(os.path.join(ex, ".docs", "instructions.md"), "w", encoding="utf-8") as f:
            f.write("# One\n\nImplement `only`.\n")
        with open(os.path.join(ex, "one.py"), "w", encoding="utf-8") as f:
            f.write("def only(x):\n    pass\n")
        with open(os.path.join(ex, "one_test.py"), "w", encoding="utf-8") as f:
            f.write(
                "import unittest\n\n"
                "from one import only\n\n"
                "class OneTests(unittest.TestCase):\n"
                "    def test_only(self):\n        self.assertEqual(only(1), 42)\n"
            )
        with open(os.path.join(ex, ".meta", "example.py"), "w", encoding="utf-8") as f:
            f.write("def only(x):\n    return 42\n")
        return ex

    def test_a_solution_that_does_not_import_shows_its_error(self):
        # nemotron-3-nano:4b wrote files of literal "\n" text; the visible run then crashes on import
        # before it can count tests. The model must see its SyntaxError, never "No visible tests".
        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            a1_harness._Workspace(ws_root).write_file("adder.py", "def add(a, b):\\n    return a + b\n")
            out = a1_harness._run_tests_output(ws_root, self.exercise_dir)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)
        self.assertNotIn("No visible tests", out)
        self.assertIn("SyntaxError", out)
        self.assertNotIn("A1-VISIBLE", out, "the harness's own count line is not shown to the model")

    # -- (b) run_tests output never names a hidden test ---------------------
    def test_run_tests_output_never_names_hidden_test(self):
        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            with open(os.path.join(self.exercise_dir, ".meta", "example.py"),
                      "r", encoding="utf-8") as f:
                solution = f.read()
            ws = a1_harness._Workspace(ws_root)
            ws.write_file("adder.py", solution)
            out = a1_harness._run_tests_output(ws_root, self.exercise_dir)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

        # the hidden test is test_add_ints (visible is test_add_floats)
        self.assertIn("test_add_floats", out, "visible test should be shown: %r" % out)
        self.assertNotIn("test_add_ints", out,
                         "run_tests output must never name a hidden test: %r" % out)
        self.assertNotIn(HIDDEN_MARKER, out)

    # -- (c) visible passes, hidden fails -----------------------------------
    def test_visible_pass_hidden_fail(self):
        # pass test_add_floats (visible), fail test_add_ints (hidden):
        # return a+b only when both args are floats.
        tricky = (
            "def add(a, b):\n"
            "    if isinstance(a, float) or isinstance(b, float):\n"
            "        return a + b\n"
            "    raise ValueError('integers not supported here')\n"
        )
        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            ws = a1_harness._Workspace(ws_root)
            ws.write_file("adder.py", tricky)
            passed, total, out = a1_harness.grade(ws_root, self.exercise_dir)
            self.assertEqual(total, 2)
            self.assertEqual(passed, 1, out)
            vp, vt, hp, ht, dout = a1_harness.grade_detail(ws_root, self.exercise_dir)
            self.assertEqual((vt, ht), (1, 1), "split must be 1/1: %r" % ((vp, vt, hp, ht),))
            self.assertEqual(vp, 1, "visible test passes: %r" % dout)
            self.assertLess(hp, ht, "hidden test must fail: %r %r" % ((vp, vt, hp, ht), dout))

            # and through the full run_task loop
            plan = [
                {"name": "write_file", "arguments": {"path": "adder.py", "content": tricky}},
                {"name": "finish", "arguments": {"note": "done"}},
            ]
            it = iter(plan)

            def chat_fn(messages, tools):
                return {"content": "", "tool_calls": [next(it)]}

            result = a1_harness.run_task(self.exercise_dir, chat_fn)
            self.assertFalse(result["passed"], result)
            self.assertEqual(result["tests_passed"], 1, result)
            self.assertEqual(result["tests_total"], 2, result)
            self.assertLess(result["hidden_passed"], result["hidden_total"], result)
            self.assertEqual(result["visible_passed"], 1, result)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

    # -- (d) reference passes all; stub fails (existing controls) -----------
    def test_reference_passes_all_and_stub_fails(self):
        with open(os.path.join(self.exercise_dir, ".meta", "example.py"),
                  "r", encoding="utf-8") as f:
            solution = f.read()

        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            ws = a1_harness._Workspace(ws_root)
            ws.write_file("adder.py", solution)
            vp, vt, hp, ht, out = a1_harness.grade_detail(ws_root, self.exercise_dir)
            self.assertEqual((vp, vt, hp, ht), (1, 1, 1, 1), out)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

        # untouched stub: fails every test
        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            vp, vt, hp, ht, out = a1_harness.grade_detail(ws_root, self.exercise_dir)
            self.assertEqual((vp, vt, hp, ht), (0, 1, 0, 1),
                             "untouched stub must fail all: %r %r" % ((vp, vt, hp, ht), out))
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

    # -- (e) harness process never imports the exercise test module ---------
    def test_test_module_not_in_harness_sys_modules(self):
        before = [m for m in sys.modules if m.endswith("_test") or "adder_test" in m]
        self.assertEqual(before, [],
                         "baseline: no exercise test module imported: %r" % before)

        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            with open(os.path.join(self.exercise_dir, ".meta", "example.py"),
                      "r", encoding="utf-8") as f:
                solution = f.read()
            ws = a1_harness._Workspace(ws_root)
            ws.write_file("adder.py", solution)
            # run_tests (visible child)
            out_vis = a1_harness._run_tests_output(ws_root, self.exercise_dir)
            self.assertIn("Ran 1 test", out_vis)
            # final grade (all child)
            vp, vt, hp, ht, out_all = a1_harness.grade_detail(ws_root, self.exercise_dir)
            self.assertEqual((vp, vt, hp, ht), (1, 1, 1, 1), out_all)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

        after = [m for m in sys.modules if m.endswith("_test") or "adder_test" in m]
        self.assertEqual(after, [],
                         "exercise test module must NOT be in harness sys.modules: %r" % after)
        self.assertNotIn("adder_test", sys.modules)
        self.assertNotIn("one_test", sys.modules)
        self.assertNotIn("three_test", sys.modules)


class A1MissingTestFileTests(unittest.TestCase):
    """An exercise folder with no hidden test file must surface as
    `ended == "error"` with a reason, and grade() must keep total 0 so
    a caller can tell it apart from a real failure.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="a1_test_base_notest_")
        self.exercise_dir = make_no_test_exercise(self.base)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_grade_missing_test_returns_zero_total_and_error(self):
        ws_root = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            passed, total, out = a1_harness.grade(ws_root, self.exercise_dir)
            self.assertEqual(passed, 0)
            self.assertEqual(total, 0, "missing test file must keep total 0")
            self.assertIn("ERROR", out)
            self.assertIn("no hidden test file", out)
        finally:
            shutil.rmtree(ws_root, ignore_errors=True)

    def test_run_task_missing_test_ends_error_with_reason(self):
        # A scripted model that "finishes" — but the final grade finds no
        # hidden test file, so the run must end as an ERROR with a reason.
        plan = [
            {"name": "write_file",
             "arguments": {"path": "no_test_exercise.py", "content": "def add(a,b): return a+b\n"}},
            {"name": "finish", "arguments": {"note": "done"}},
        ]
        it = iter(plan)

        def chat_fn(messages, tools):
            return {"content": "", "tool_calls": [next(it)]}

        result = a1_harness.run_task(self.exercise_dir, chat_fn)
        self.assertEqual(result["ended"], "error", result)
        self.assertFalse(result["passed"])
        self.assertEqual(result["tests_passed"], 0)
        self.assertEqual(result["tests_total"], 0)
        self.assertIn("reason", result, "run_task must surface the reason")
        self.assertIn("no hidden test file", result["reason"])
        self.assertIn("ERROR", result["reason"])


class A1FailureLabelTests(unittest.TestCase):
    """`error` means the harness could not measure. A model whose code does not import (or hangs) FAILED, with the
    cause recorded; a crash inside the loop is an error WITH its reason."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="a1_test_base_labels_")
        self.exercise_dir = make_exercise(self.base)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_code_that_does_not_import_is_a_fail_not_an_error(self):
        plan = iter([{"name": "write_file", "arguments": {"path": "adder.py", "content": "def add(a, b:\n"}},
                     {"name": "finish", "arguments": {"note": "done"}}])
        result = a1_harness.run_task(self.exercise_dir, lambda m, t: {"content": "", "tool_calls": [next(plan)]})
        self.assertEqual(result["ended"], "finish", result)
        self.assertFalse(result["passed"])
        self.assertEqual(result.get("graded"), "crashed")
        self.assertIn("SyntaxError", result.get("reason", ""))

    def test_aider_first_attempt_that_does_not_import_gets_its_second_try(self):
        with open(os.path.join(self.exercise_dir, ".meta", "example.py"), encoding="utf-8") as f:
            good = f.read()
        replies = iter([{"content": "def add(a, b:\n"}, {"content": good}])
        result = a1_harness.run_task(self.exercise_dir, lambda m, t: next(replies), mode="aider")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["turns"], 2)

    def test_a_solution_that_eats_memory_fails_inside_its_own_child(self):
        # Mothership froze during an A1 run; the grader child had a time limit but no memory limit.
        # Cap lowered for the test so it allocates ~3x the cap and must hit MemoryError, not the box.
        hog = "def add(a, b):\n    blob = bytearray(600 * 2**20)\n    return a + b\n"
        plan = iter([{"name": "write_file", "arguments": {"path": "adder.py", "content": hog}},
                     {"name": "finish", "arguments": {"note": "done"}}])
        with mock.patch.object(a1_harness, "MEM_CAP_BYTES", 200 * 2 ** 20):
            result = a1_harness.run_task(self.exercise_dir, lambda m, t: {"content": "", "tool_calls": [next(plan)]})
        self.assertFalse(result["passed"], result)
        self.assertEqual(result["ended"], "finish")
        self.assertGreater(result["tests_total"], 0, "the child ran and reported; the hog failed its tests")

    def test_the_child_refuses_to_run_uncapped(self):
        child = a1_harness.CHILD.replace("capped = _cap_memory(mem_cap)", "capped = False")
        self.assertNotEqual(child, a1_harness.CHILD, "the decoy must actually change the child")
        with mock.patch.object(a1_harness, "CHILD", child):
            ws = a1_harness._setup_workspace(self.exercise_dir, self.base)
            try:
                p, t, out = a1_harness._grade_run(ws, self.exercise_dir, "all")
            finally:
                shutil.rmtree(ws, ignore_errors=True)
        self.assertIsNone(t)
        self.assertIn("could not cap", out)

    def test_a_missing_name_is_shown_to_the_model_and_graded_as_a_crash(self):
        # the 3.8 on go-counting: its file lacked a name the tests import; unittest wrapped the
        # ImportError as one fake test, the split hid it, and run_tests said "No visible tests".
        ws = a1_harness._setup_workspace(self.exercise_dir, self.base)
        try:
            a1_harness._Workspace(ws).write_file("adder.py", "def subtract(a, b):\n    return a - b\n")
            shown = a1_harness._run_tests_output(ws, self.exercise_dir)
        finally:
            shutil.rmtree(ws, ignore_errors=True)
        self.assertNotIn("No visible tests", shown)
        self.assertIn("ImportError", shown)
        plan = iter([{"name": "write_file", "arguments": {"path": "adder.py",
                                                          "content": "def subtract(a, b):\n    return a - b\n"}},
                     {"name": "finish", "arguments": {"note": "done"}}])
        result = a1_harness.run_task(self.exercise_dir, lambda m, t: {"content": "", "tool_calls": [next(plan)]})
        self.assertFalse(result["passed"])
        self.assertEqual(result.get("graded"), "crashed", result)
        self.assertIn("ImportError", result.get("reason", ""))

    def test_aider_extracts_the_file_from_a_fenced_reply(self):
        # Before an aider run: models wrap whole-file replies in a fence; written raw, every fenced answer would be a
        # SyntaxError and the run would measure nothing.
        with open(os.path.join(self.exercise_dir, ".meta", "example.py"), encoding="utf-8") as f:
            good = f.read()
        fenced = "Here is the solution:\n\n```python\n" + good + "```\n\nThis passes the tests."
        replies = iter([{"content": fenced}])
        result = a1_harness.run_task(self.exercise_dir, lambda m, t: next(replies), mode="aider")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["turns"], 1)
        self.assertEqual(a1_harness._file_from_reply("def f(): pass"), "def f(): pass", "unfenced = the file")

    def test_aider_second_attempt_never_shows_harness_markers(self):
        seen = []
        replies = iter([{"content": "def add(a, b):\n    return 0\n"}, {"content": "def add(a, b):\n    return 0\n"}])

        def chat_fn(messages, tools):
            seen.append(messages[-1]["content"])
            return next(replies)
        a1_harness.run_task(self.exercise_dir, chat_fn, mode="aider")
        self.assertEqual(len(seen), 2)
        self.assertIn("FAILED", seen[1])
        self.assertNotIn("A1-RESULT", seen[1])

    def test_a_malformed_tool_call_is_handed_back_not_fatal(self):
        # nemotron-3-nano:30b lost 4 of its first 5 exercises to one unparseable tool call each.
        with open(os.path.join(self.exercise_dir, ".meta", "example.py"), encoding="utf-8") as f:
            good = f.read()
        seen = []
        replies = iter([{"invalid_tool_call": "ollama: XML syntax error on line 3"},
                        {"content": "", "tool_calls": [{"name": "write_file",
                                                        "arguments": {"path": "adder.py", "content": good}}]},
                        {"content": "", "tool_calls": [{"name": "finish", "arguments": {"note": "done"}}]}])

        def chat_fn(messages, tools):
            seen.append(messages[-1]["content"])
            return next(replies)
        result = a1_harness.run_task(self.exercise_dir, chat_fn)
        self.assertTrue(result["passed"], result)
        self.assertIn("could not be parsed", seen[1])
        self.assertEqual(result["tool_counts"].get("invalid_tool_call"), 1)

    def test_endless_malformed_calls_end_on_no_progress(self):
        result = a1_harness.run_task(self.exercise_dir, lambda m, t: {"invalid_tool_call": "ollama: XML syntax error"})
        self.assertEqual(result["ended"], "no_progress")
        self.assertEqual(result["tool_counts"]["invalid_tool_call"],
                         a1_harness.NO_PROGRESS_STALL * (a1_harness.NO_PROGRESS_NUDGES + 1))
        self.assertEqual(result["tool_counts"]["nudge"], a1_harness.NO_PROGRESS_NUDGES)

    def test_a_crash_in_the_loop_records_why(self):
        def chat_fn(messages, tools):
            raise RuntimeError("adapter blew up on a malformed tool call")
        result = a1_harness.run_task(self.exercise_dir, chat_fn)
        self.assertEqual(result["ended"], "error")
        self.assertIn("loop error: RuntimeError: adapter blew up", result.get("reason", ""))


class A1ProtocolV21Tests(unittest.TestCase):
    """v2.1: the brief is Aider's (introduction + instructions + append), the prompts say what run_tests really runs and
    carry Aider's own texts, and the loop cutter nudges once before it ends an exercise."""

    INTRO = "INTRO-PART-3b1f"
    APPEND = "APPEND-PART-ValueError-message-8d2c"

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="a1_v21_")
        self.exercise_dir = make_exercise(self.base)
        docs = os.path.join(self.exercise_dir, ".docs")
        with open(os.path.join(docs, "introduction.md"), "w", encoding="utf-8") as f:
            f.write(self.INTRO + "\n")
        with open(os.path.join(docs, "instructions.append.md"), "w", encoding="utf-8") as f:
            f.write(self.APPEND + "\n")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _first_user_message(self, mode):
        seen = []

        def chat_fn(messages, tools):
            seen.append([dict(m) for m in messages])
            if mode == "aider":
                return {"content": EXAMPLE, "tool_calls": []}
            return {"content": "", "tool_calls": [{"name": "finish", "arguments": {}}]}

        a1_harness.run_task(self.exercise_dir, chat_fn, mode=mode)
        return seen[0]

    def test_brief_carries_all_three_parts_in_aiders_order(self):
        for mode in ("agentic", "aider"):
            user = [m for m in self._first_user_message(mode) if m["role"] == "user"][0]["content"]
            i, n, a = user.find(self.INTRO), user.find("# Adder"), user.find(self.APPEND)
            self.assertTrue(0 <= i < n < a, (mode, i, n, a))

    def test_brief_without_optional_parts_is_just_the_instructions(self):
        ex = make_two_words_exercise(self.base)
        self.assertEqual(a1_harness._brief_text(ex, "two_words"), TWO_WORDS_INSTRUCTIONS.strip())

    def test_prompts_carry_aiders_addendum_and_say_what_run_tests_runs(self):
        msgs = self._first_user_message("agentic")
        system = [m for m in msgs if m["role"] == "system"][0]["content"]
        self.assertIn("NOT in the workspace", system)
        self.assertIn("visible", system)
        self.assertNotIn("run the hidden tests", system)
        for mode in ("agentic", "aider"):
            user = [m for m in self._first_user_message(mode) if m["role"] == "user"][0]["content"]
            self.assertIn("Don't change the names of existing functions or classes", user, mode)
            self.assertIn("Only use standard libraries", user, mode)

    def test_aider_second_attempt_uses_aiders_failure_text_with_the_full_output(self):
        seen = []
        seq = [WRONG_SOLUTION, EXAMPLE]

        def chat_fn(messages, tools):
            seen.append([dict(m) for m in messages])
            return {"content": seq.pop(0), "tool_calls": []}

        result = a1_harness.run_task(self.exercise_dir, chat_fn, mode="aider")
        self.assertTrue(result["passed"], result)
        retry = seen[1][-1]["content"]
        self.assertIn("The tests are correct, don't try and change them.", retry)
        self.assertIn("Fix the code in adder.py to resolve the errors.", retry)
        self.assertIn("test_add_ints", retry)            # the full grading output, as Aider sends it
        self.assertNotIn("A1-RESULT", retry)

    def test_a_nudged_model_that_then_writes_the_solution_passes(self):
        """Positive control for the nudge: the model hunts with run_tests until nudged, then writes and finishes."""
        nudged = []

        def chat_fn(messages, tools):
            if any(m["role"] == "user" and m["content"] == a1_harness.NUDGE_TEXT for m in messages):
                nudged.append(True)
                if len(nudged) == 1:
                    return {"content": "", "tool_calls": [
                        {"name": "write_file", "arguments": {"path": "adder.py", "content": EXAMPLE}}]}
                return {"content": "", "tool_calls": [{"name": "finish", "arguments": {}}]}
            return {"content": "", "tool_calls": [{"name": "run_tests", "arguments": {}}]}

        result = a1_harness.run_task(self.exercise_dir, chat_fn)
        self.assertEqual(result["ended"], "finish", result)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["tool_counts"].get("nudge"), 1)
        self.assertEqual(result["tool_counts"].get("run_tests"), a1_harness.NO_PROGRESS_REPEAT)


class A1HaiToolsTests(unittest.TestCase):
    """agentic-hai: the model gets HAI's production coding tools (coding_list/read/search/patch/create/file/test,
    same names and descriptions) instead of the minimal set; coding_test also runs the model's own test_*.py files."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="a1_hai_")
        self.exercise_dir = make_exercise(self.base)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    @staticmethod
    def scripted(steps):
        """steps: list of callables (messages) -> one tool call dict; lets a step use an earlier tool result."""
        it = iter(steps)
        seen = {"tools": None, "outputs": []}

        def chat_fn(messages, tools):
            seen["tools"] = [t["name"] for t in tools]
            if messages and messages[-1]["role"] == "tool":
                seen["outputs"].append(messages[-1]["content"])
            return {"content": "", "tool_calls": [next(it)(messages)]}
        return chat_fn, seen

    @staticmethod
    def last_sha(messages):
        for m in reversed(messages):
            if m["role"] == "tool" and str(m["content"]).startswith("sha256: "):
                return m["content"].split()[1]
        return ""

    def test_read_then_sha_guarded_patch_solves_and_offers_only_hai_tools(self):
        chat_fn, seen = self.scripted([
            lambda m: {"name": "coding_read", "arguments": {"path": "adder.py"}},
            lambda m: {"name": "coding_patch", "arguments": {"path": "adder.py", "old": "    pass",
                                                             "new": "    return a + b",
                                                             "expected_sha256": self.last_sha(m)}},
            lambda m: {"name": "coding_test", "arguments": {}},
            lambda m: {"name": "finish", "arguments": {}},
        ])
        r = a1_harness.run_task(self.exercise_dir, chat_fn, mode="agentic-hai")
        self.assertTrue(r["passed"], r)
        self.assertEqual(seen["tools"], [t["name"] for t in a1_harness._TOOLS_HAI])
        self.assertNotIn("write_file", seen["tools"])
        self.assertTrue(seen["outputs"][1].startswith("OK patched"), seen["outputs"])

    def test_patch_refuses_a_stale_sha_and_an_ambiguous_match(self):
        ws = a1_harness._Workspace(a1_harness._setup_workspace(self.exercise_dir, None))
        try:
            self.assertIn("sha256 mismatch", ws.coding_patch("adder.py", "pass", "x", "0" * 64))
            # a MISSING hash says so - it is not a changed file (10-07: the false "changed" cost 10-14% of tool calls)
            self.assertEqual(ws.coding_patch("adder.py", "pass", "x", ""), a1_harness.NO_SHA_ERROR)
            self.assertEqual(ws.coding_file("delete", "adder.py", ""), a1_harness.NO_SHA_ERROR)
            sha = ws.coding_read("adder.py").split()[1]
            ws.coding_create("dup.py", "a\na\n")
            dup_sha = ws.coding_read("dup.py").split()[1]
            self.assertIn("ambiguous (2 matches)", ws.coding_patch("dup.py", "a", "b", dup_sha))
            self.assertIn("1 replacement", ws.coding_patch("adder.py", "pass", "return a + b", sha))
            self.assertIn("already exists", ws.coding_create("adder.py", "x"))
        finally:
            shutil.rmtree(ws.root, ignore_errors=True)

    def test_coding_test_runs_the_models_own_tests_and_never_shows_the_hidden_ones(self):
        own = ("import unittest\nfrom adder import add\n\nclass Mine(unittest.TestCase):\n"
               "    def test_my_edge_case(self):\n        self.assertEqual(add(-1, 1), 0)\n")
        chat_fn, seen = self.scripted([
            lambda m: {"name": "coding_create", "arguments": {"path": "test_mine.py", "content": own}},
            lambda m: {"name": "coding_test", "arguments": {}},
            lambda m: {"name": "finish", "arguments": {}},
        ])
        r = a1_harness.run_task(self.exercise_dir, chat_fn, mode="agentic-hai")
        out = seen["outputs"][-1]
        self.assertIn("--- your own tests", out)
        self.assertIn("test_my_edge_case", out)
        self.assertNotIn(HIDDEN_MARKER, out)
        self.assertEqual(r["tool_counts"].get("own_tests_runs"), 1)

    def test_an_own_test_that_names_the_answer_key_is_refused_not_run(self):
        sneaky = "import unittest\nopen('adder_test.py').read()\n"
        chat_fn, seen = self.scripted([
            lambda m: {"name": "coding_create", "arguments": {"path": "test_peek.py", "content": sneaky}},
            lambda m: {"name": "coding_test", "arguments": {}},
            lambda m: {"name": "finish", "arguments": {}},
        ])
        r = a1_harness.run_task(self.exercise_dir, chat_fn, mode="agentic-hai")
        self.assertIn("REFUSED", seen["outputs"][-1])
        self.assertEqual(r["tool_counts"].get("own_tests_refused"), 1)

    def test_a_minimal_tool_is_unknown_in_hai_mode_and_hai_tools_unknown_in_minimal_mode(self):
        ws = a1_harness._Workspace(a1_harness._setup_workspace(self.exercise_dir, None))
        try:
            self.assertIn("unknown tool", a1_harness._dispatch_tool(ws, "write_file", {"path": "x.py", "content": ""},
                                                                    self.exercise_dir, a1_harness._TOOLS_HAI, {}))
            self.assertIn("unknown tool", a1_harness._dispatch_tool(ws, "coding_read", {"path": "adder.py"},
                                                                    self.exercise_dir, a1_harness._TOOLS, {}))
        finally:
            shutil.rmtree(ws.root, ignore_errors=True)

    def test_the_own_tests_child_carries_the_same_memory_cap(self):
        """Positive control for the shared cap: an own test that tries to allocate past MEM_CAP_BYTES must fail."""
        ws = a1_harness._Workspace(a1_harness._setup_workspace(self.exercise_dir, None))
        try:
            ws.coding_create("test_big.py", "import unittest\nclass B(unittest.TestCase):\n"
                                            "    def test_big(self):\n        b = bytearray(3 * 2 ** 30)\n")
            out = a1_harness._own_tests_output(ws.root, {})
        finally:
            shutil.rmtree(ws.root, ignore_errors=True)
        self.assertIn("MemoryError", out)


class A1RewriteLoopIsNotProgress(unittest.TestCase):
    """a model rewrote its file and re-ran the tests for 4.6 h on one exercise; every rewrite counted as
    progress. Progress is now a NEVER-seen workspace state or a new observation of the current one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, script):
        it = iter(script)

        def chat_fn(messages, tools):
            try:
                return {"content": "", "tool_calls": [next(it)]}
            except StopIteration:
                return {"content": "", "tool_calls": [{"name": "finish", "arguments": {}}]}
        return a1_harness.run_task(self.ex, chat_fn, mode="agentic", work_root=self.tmp)

    def test_cycling_between_two_tried_versions_is_cut(self):
        a = "def double_word(a, b):\n    return 'A'\n"
        b = "def double_word(a, b):\n    return 'B'\n"
        w = lambda c: {"name": "write_file", "arguments": {"path": "two_words.py", "content": c}}
        t = {"name": "run_tests", "arguments": {}}
        row = self._run([w(a), t, w(b), t] + [w(a), w(b)] * 40)
        self.assertEqual(row["ended"], "no_progress")
        self.assertLess(row["turns"], 40, "the loop must end long before the script runs out")

    def test_new_code_every_turn_is_never_cut(self):
        w = lambda i: {"name": "write_file", "arguments": {"path": "two_words.py",
                       "content": "def double_word(a, b):\n    return %r\n" % ("try%d" % i)}}
        row = self._run([w(i) for i in range(20)])
        self.assertEqual(row["ended"], "finish", "20 distinct versions are exploration, not a loop")

    def test_reading_new_files_on_unchanged_code_is_progress(self):
        reads = [{"name": "read_file", "arguments": {"path": p}}
                 for p in ["two_words.py", ".docs/instructions.md", "missing1", "missing2", "missing3",
                           "missing4", "missing5", "missing6", "missing7", "missing8"]]
        row = self._run(reads)
        self.assertEqual(row["ended"], "finish", "ten different observations of one state are not idle")


class A1NoToolCallReplyContinues(unittest.TestCase):
    """v2.2: a reply with no tool call is answered with Inspect's continue message, so a request never
    ends on the model's own message (ollama made that a prefill continuation; llama-server refused it with HTTP 400)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_request_ends_on_a_user_or_tool_message(self):
        for mode, tool in (("agentic", "list_files"), ("agentic-hai", "coding_list")):
            replies = iter([{"content": "I will write the solution now.", "tool_calls": []},
                            {"content": "", "tool_calls": [{"name": tool, "arguments": {}}]},
                            {"content": "", "tool_calls": [{"name": "finish", "arguments": {}}]}])
            seen = []

            def chat_fn(messages, tools, replies=replies, seen=seen):
                seen.append([dict(m) for m in messages])
                return next(replies)
            a1_harness.run_task(self.ex, chat_fn, mode=mode, work_root=self.tmp)
            self.assertEqual(len(seen), 3, mode)
            for req in seen:
                self.assertNotEqual(req[-1]["role"], "assistant", mode)
            self.assertEqual(seen[1][-1], {"role": "user", "content": a1_harness.CONTINUE_TEXT}, mode)
            self.assertEqual(seen[1][-2]["content"], "I will write the solution now.", "the reply itself is kept")

    def test_text_only_replies_still_end_on_no_progress(self):
        row = a1_harness.run_task(self.ex, lambda m, t: {"content": "thinking...", "tool_calls": []},
                                  mode="agentic", work_root=self.tmp)
        self.assertEqual(row["ended"], "no_progress")
        self.assertGreater(row["tests_total"], 0, "graded, never a 0/0")


class A1RunawayGuard(unittest.TestCase):
    """The runaway guard: a reply the chat_fn marks `runaway` (cut at the order's turn cap without a tool
    call) is discarded and the model told to act; the first cut nudges, the next ends the exercise as "runaway",
    graded. Measured on rigby: every acting turn <= 14,888 tokens, every runaway >= 25,447."""

    CAP = 20480

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)
        self.solution = open(os.path.join(self.ex, ".meta", "example.py"), encoding="utf-8").read()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, replies):
        it, seen = iter(replies), []

        def chat_fn(messages, tools):
            seen.append([dict(m) for m in messages])
            return next(it)
        return a1_harness.run_task(self.ex, chat_fn, mode="agentic", work_root=self.tmp), seen

    def test_one_cut_nudges_then_the_model_acts_and_passes(self):
        row, seen = self._run([
            {"content": "", "tool_calls": [], "runaway": self.CAP},
            {"content": "", "tool_calls": [{"name": "write_file",
                                            "arguments": {"path": "two_words.py", "content": self.solution}}]},
            {"content": "", "tool_calls": [{"name": "finish", "arguments": {}}]}])
        self.assertEqual((row["ended"], row["passed"]), ("finish", True), row)
        self.assertEqual(row["tool_counts"].get("runaway_cut"), 1)
        after = seen[1]
        self.assertEqual(after[-1], {"role": "user", "content": a1_harness.RUNAWAY_TEXT.format(cap=self.CAP)})
        self.assertEqual(after[-2], {"role": "assistant", "content": ""}, "the runaway text is discarded")
        for req in seen:
            self.assertNotEqual(req[-1]["role"], "assistant", "llama-server refuses a request ending on the model")

    def test_a_second_cut_ends_the_exercise_as_runaway_and_it_is_still_graded(self):
        row, seen = self._run([{"content": "", "tool_calls": [], "runaway": self.CAP}] * 2)
        self.assertEqual(row["ended"], "runaway")
        self.assertEqual(row["tool_counts"].get("runaway_cut"), a1_harness.RUNAWAY_NUDGES + 1)
        self.assertEqual(len(seen), a1_harness.RUNAWAY_NUDGES + 1)
        self.assertGreater(row["tests_total"], 0, "graded on the workspace as it stands, never a 0/0")
        self.assertFalse(row["passed"])

    def test_a_conversation_that_outgrew_the_context_ends_graded_not_as_an_error(self):
        """(resumed react, 41 turns): the next request no longer fit the 65,536 context; that ended the
        exercise as an engine error (void, re-run). It is the model filling its own context: end it, grade it."""
        row, seen = self._run([
            {"content": "", "tool_calls": [{"name": "write_file",
                                            "arguments": {"path": "two_words.py", "content": self.solution}}]},
            {"content": "", "tool_calls": [], "context_full": True}])
        self.assertEqual(row["ended"], "context_full")
        self.assertTrue(row["passed"], "graded on the workspace as it stands")
        self.assertEqual(len(seen), 2)

    def test_a_cut_does_not_spend_the_no_progress_nudge(self):
        row, _ = self._run([{"content": "", "tool_calls": [], "runaway": self.CAP}]
                           + [{"content": "", "tool_calls": [{"name": "read_file",
                                                              "arguments": {"path": "two_words.py"}}]}] * 6)
        self.assertEqual(row["ended"], "no_progress")
        self.assertEqual((row["tool_counts"].get("runaway_cut"), row["tool_counts"].get("nudge")),
                         (1, a1_harness.NO_PROGRESS_NUDGES))


class A1FinishGate(unittest.TestCase):
    """The finish gate: with `finish_gate`, finish is refused until the lead's own test_*.py files have
    passed via coding_test on the CURRENT workspace; refusals are counted; a lead that never complies trips the
    no-progress rules and is still graded. Without the gate nothing changes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)
        self.solution = open(os.path.join(self.ex, ".meta", "example.py"), encoding="utf-8").read()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, calls, finish_gate=True, mode="agentic-hai"):
        """A scripted lead. `{SHA}` in an argument is filled from the last coding_read result (HAI's patch contract)."""
        import re
        results, first_user, last_sha = [], [], [""]
        it = iter(calls)

        def chat_fn(messages, tools):
            if not first_user:
                first_user.append(next(m["content"] for m in messages if m.get("role") == "user"))
            if messages and messages[-1].get("role") == "tool":
                results.append(messages[-1]["content"])
                m = re.search(r"[0-9a-f]{64}", messages[-1]["content"])
                if m:
                    last_sha[0] = m.group(0)
            try:
                name, args = next(it)
            except StopIteration:
                name, args = "finish", {}
            args = {k: (v.replace("{SHA}", last_sha[0]) if isinstance(v, str) else v) for k, v in args.items()}
            return {"content": "", "tool_calls": [{"name": name, "arguments": args}]}
        row = a1_harness.run_task(self.ex, chat_fn, mode=mode, work_root=self.tmp, finish_gate=finish_gate)
        return row, results, first_user[0]

    def _solve(self):
        """The calls that replace the stub with the reference solution under HAI's sha-guarded file contract (read for
        the sha, delete, create - a patch's `old` text would have to match the file's CRLF bytes on Windows)."""
        return [("coding_read", {"path": "two_words.py"}),
                ("coding_file", {"action": "delete", "path": "two_words.py", "expected_sha256": "{SHA}"}),
                ("coding_create", {"path": "two_words.py", "content": self.solution})]

    GOOD_TEST = ("import unittest\nfrom two_words import double_word\n\n\nclass T(unittest.TestCase):\n"
                 "    def test_joins(self):\n        self.assertEqual(double_word('a', 'b'), 'a b 2')\n")
    BAD_TEST = ("import unittest\nfrom two_words import double_word\n\n\nclass T(unittest.TestCase):\n"
                "    def test_wrong(self):\n        self.assertEqual(double_word('a', 'b'), 'ba')\n")

    def test_without_the_gate_finish_ends_at_once(self):
        row, results, first_user = self._run(self._solve(), finish_gate=False)
        self.assertEqual(row["ended"], "finish")
        self.assertTrue(row["passed"], results)
        self.assertNotIn("finish_refused", row["tool_counts"])
        self.assertNotIn(a1_harness.FINISH_GATE_BRIEF, first_user)

    def test_finish_is_refused_until_own_tests_pass_on_the_current_code(self):
        row, results, first_user = self._run(self._solve() + [
            ("finish", {}),                                                   # no tests yet -> refused
            ("coding_create", {"path": "test_two_words.py", "content": self.GOOD_TEST}),
            ("finish", {}),                                                   # written, not run -> refused
            ("coding_test", {}),
            ("finish", {}),                                                   # passed on this code -> accepted
        ])
        self.assertTrue(first_user.endswith(a1_harness.FINISH_GATE_BRIEF))
        refusals = [r for r in results if r.startswith("finish refused")]
        self.assertEqual(len(refusals), 2, results)
        self.assertIn("no test_*.py", refusals[0])
        self.assertIn("not passed on the current code", refusals[1])
        self.assertEqual(row["tool_counts"]["finish_refused"], 2)
        self.assertEqual(row["ended"], "finish")
        self.assertTrue(row["passed"], row)

    def test_a_change_after_the_passing_run_reopens_the_gate(self):
        row, results, _ = self._run(self._solve() + [
            ("coding_create", {"path": "test_two_words.py", "content": self.GOOD_TEST}),
            ("coding_test", {}),
            ("coding_create", {"path": "notes.txt", "content": "touched"}),   # the workspace changed since the pass
            ("finish", {}),                                                   # -> refused (stale)
            ("coding_test", {}),
            ("finish", {}),
        ])
        self.assertEqual(row["tool_counts"]["finish_refused"], 1)
        self.assertEqual(row["ended"], "finish")

    def test_failing_own_tests_keep_the_gate_shut_and_a_stubborn_lead_is_cut_but_graded(self):
        row, results, _ = self._run(self._solve() + [
            ("coding_create", {"path": "test_two_words.py", "content": self.BAD_TEST}),
            ("coding_test", {}),
        ] + [("finish", {})] * 12)
        self.assertGreaterEqual(row["tool_counts"]["finish_refused"], 3)
        self.assertEqual(row["ended"], "no_progress", "the existing loop cutter bounds a lead that will not comply")
        self.assertTrue(row["passed"], "graded on the hidden tests regardless of how it ended")

    def test_the_gate_needs_the_mode_whose_coding_test_runs_own_tests(self):
        with self.assertRaises(ValueError):
            self._run([], finish_gate=True, mode="agentic")


class A1DelegateTool(unittest.TestCase):
    """A1-team: A1-solo and A1-team differ ONLY in the `delegate` tool, offered only when the order
    supplies a delegate_fn; the helper's text comes back as the tool result."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, replies, delegate_fn=None, mode="agentic-hai", delegate_brief=""):
        offered, results = [], []
        self.first_user = []
        it = iter(replies)

        def chat_fn(messages, tools):
            offered.append([t["name"] for t in tools])
            if not self.first_user:
                self.first_user.append(next(m["content"] for m in messages if m.get("role") == "user"))
            if messages and messages[-1].get("role") == "tool":
                results.append(messages[-1]["content"])
            return next(it)
        row = a1_harness.run_task(self.ex, chat_fn, mode=mode, work_root=self.tmp, delegate_fn=delegate_fn,
                                  delegate_brief=delegate_brief)
        return row, offered, results

    def test_directed_brief_reaches_the_lead_only_in_a_team_run(self):
        # the DIRECTED arm: the lead is told to use the helper; solo never sees that text, and a team
        # run without the brief (the organic arm) does not either
        finish = [{"content": "", "tool_calls": [{"name": "finish", "arguments": {}}]}]
        for mode in ("agentic", "agentic-hai"):
            self._run(list(finish), delegate_fn=lambda b: "x", delegate_brief=a1_harness.DIRECTED_BRIEF, mode=mode)
            self.assertTrue(self.first_user[0].endswith(a1_harness.DIRECTED_BRIEF), mode)
            self.assertIn("You have tools:", self.first_user[0])
            self._run(list(finish), delegate_fn=lambda b: "x", mode=mode)
            self.assertNotIn(a1_harness.DIRECTED_BRIEF, self.first_user[0], "organic team run: no directive")
            self._run(list(finish), delegate_brief=a1_harness.DIRECTED_BRIEF, mode=mode)
            self.assertNotIn(a1_harness.DIRECTED_BRIEF, self.first_user[0], "solo: a brief without a helper is ignored")

    def test_solo_offers_no_delegate(self):
        for mode in ("agentic", "agentic-hai"):
            _, offered, _ = self._run([{"content": "", "tool_calls": [{"name": "finish", "arguments": {}}]}],
                                      mode=mode)
            self.assertNotIn("delegate", offered[0], mode)

    def test_team_offers_delegate_and_returns_the_helpers_text(self):
        briefs = []

        def helper(brief):
            briefs.append(brief)
            return "def double_word(a, b):\n    return a + b\n"
        good = "def double_word(a, b):\n    return a + b\n"
        row, offered, results = self._run(
            [{"content": "", "tool_calls": [{"name": "delegate", "arguments": {"brief": "write double_word"}}]},
             {"content": "", "tool_calls": [{"name": "write_file",
                                              "arguments": {"path": "two_words.py", "content": good}}]},
             {"content": "", "tool_calls": [{"name": "finish", "arguments": {}}]}],
            delegate_fn=helper, mode="agentic")
        self.assertIn("delegate", offered[0])
        self.assertEqual(offered[0][-1], "finish", "finish stays the last tool")
        self.assertEqual(briefs, ["write double_word"])
        self.assertEqual(results[0], "def double_word(a, b):\n    return a + b\n")
        self.assertEqual(row["tool_counts"].get("delegate"), 1)

    def test_an_empty_brief_is_refused_without_calling_the_helper(self):
        called = []
        _, _, results = self._run(
            [{"content": "", "tool_calls": [{"name": "delegate", "arguments": {"brief": "  "}}]},
             {"content": "", "tool_calls": [{"name": "finish", "arguments": {}}]}],
            delegate_fn=lambda b: called.append(b) or "x", mode="agentic")
        self.assertEqual(called, [])
        self.assertTrue(results[0].startswith("ERROR: delegate needs"))


class A1Review(unittest.TestCase):
    """A1-review: when the lead's finish is accepted, `review_fn` gets the ARTIFACT - the brief,
    the lead's files, the last test output it saw; never the hidden tests, never the lead's narration - and its
    findings give the lead ONE repair round; NO DEFECTS or an ERROR let the finish stand. Asked once per exercise."""
    NARRATION = "NARRATION-5f1c the lead's own account of its work"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)
        self.packages, self.user_msgs = [], []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def reviewer(self, verdicts):
        it = iter(verdicts)

        def fn(package):
            self.packages.append(package)
            return next(it)
        return fn

    def _run(self, calls, review_fn, mode="agentic", finish_gate=False):
        import re
        results, last_sha = [], [""]
        it = iter(calls)

        def chat_fn(messages, tools):
            # every tool result and user message the lead has seen so far (the loop appends to ONE list; the final
            # "Task finished." is appended after the last call and so is never seen here)
            self.user_msgs[:] = [m["content"] for m in messages if m.get("role") == "user"]
            results[:] = [m["content"] for m in messages if m.get("role") == "tool"]
            m = re.search(r"[0-9a-f]{64}", results[-1]) if results else None
            if m:
                last_sha[0] = m.group(0)
            try:
                name, args = next(it)
            except StopIteration:
                name, args = "finish", {}
            args = {k: (v.replace("{SHA}", last_sha[0]) if isinstance(v, str) else v) for k, v in args.items()}
            return {"content": self.NARRATION, "tool_calls": [{"name": name, "arguments": args}]}
        row = a1_harness.run_task(self.ex, chat_fn, mode=mode, work_root=self.tmp, review_fn=review_fn,
                                  finish_gate=finish_gate)
        return row, results

    def test_findings_give_one_repair_round_and_the_package_is_the_artifact(self):
        findings = "double_word('a', 'b') returns 'ab'; the brief wants 'a b 2' (the lengths are missing)"
        row, results = self._run([
            ("write_file", {"path": "two_words.py", "content": TWO_WORDS_WRONG}),
            ("finish", {}),                                                   # accepted -> reviewed -> repair round
            ("write_file", {"path": "two_words.py", "content": TWO_WORDS_EXAMPLE}),
            ("finish", {}),                                                   # final
        ], self.reviewer([findings]))
        self.assertEqual(row["ended"], "finish")
        self.assertTrue(row["passed"], row)
        self.assertEqual(len(self.packages), 1, "the reviewer is asked once")
        self.assertEqual(row["tool_counts"]["review"], 1)
        self.assertEqual(row["tool_counts"]["review_repair_round"], 1)
        self.assertEqual((row["review"]["clean"], row["review"]["error"], row["review"]["repaired"]),
                         (False, False, True))
        self.assertEqual(row["review"]["findings"], findings)
        self.assertNotIn("state_before", row["review"])
        self.assertTrue(any(r.startswith("Not finished yet") for r in results), results)
        repair = [u for u in self.user_msgs if findings in u]
        self.assertEqual(len(repair), 1, "the findings reach the lead as ONE user message")
        self.assertIn("your one repair round", repair[0])
        pkg = self.packages[0]
        self.assertIn("Implement `double_word(a, b)`", pkg, "the brief")
        self.assertIn("### two_words.py", pkg)
        self.assertIn("return a + b", pkg, "the solution as it stood at finish")
        self.assertIn("(the author never ran the tests)", pkg)
        self.assertNotIn(TWO_WORDS_MARKER, pkg, "the hidden tests never reach the reviewer")
        self.assertNotIn(self.NARRATION, pkg, "the lead's own account never reaches the reviewer")

    def test_no_defects_lets_the_finish_stand(self):
        row, results = self._run([("write_file", {"path": "two_words.py", "content": TWO_WORDS_EXAMPLE}),
                                  ("finish", {})], self.reviewer(["NO DEFECTS"]))
        self.assertEqual(row["ended"], "finish")
        self.assertEqual((row["review"]["clean"], row["review"]["repaired"]), (True, False))
        self.assertNotIn("review_repair_round", row["tool_counts"])
        self.assertFalse(any(r.startswith("Not finished yet") for r in results))
        self.assertEqual(len(self.user_msgs), 1, "no repair message after a clean review")

    def test_a_reviewer_error_never_blocks_the_finish(self):
        row, results = self._run([("write_file", {"path": "two_words.py", "content": TWO_WORDS_EXAMPLE}),
                                  ("finish", {})], self.reviewer(["ERROR: the helper order is held (plex)"]))
        self.assertEqual(row["ended"], "finish")
        self.assertEqual((row["review"]["clean"], row["review"]["error"]), (False, True))
        self.assertNotIn("review_repair_round", row["tool_counts"])

    def test_the_second_finish_is_final_even_with_more_findings_in_store(self):
        row, _ = self._run([("write_file", {"path": "two_words.py", "content": TWO_WORDS_WRONG}),
                            ("finish", {}), ("finish", {})], self.reviewer(["wrong", "still wrong"]))
        self.assertEqual(row["ended"], "finish")
        self.assertEqual(len(self.packages), 1)
        self.assertFalse(row["passed"])
        self.assertFalse(row["review"]["repaired"], "finished again without touching the workspace")

    def test_a_lead_cut_after_findings_is_graded_and_the_review_still_recorded(self):
        row, _ = self._run([("write_file", {"path": "two_words.py", "content": TWO_WORDS_WRONG}), ("finish", {})]
                           + [("list_files", {})] * 12, self.reviewer(["wrong"]))
        self.assertEqual(row["ended"], "no_progress")
        self.assertEqual((row["review"]["clean"], row["review"]["repaired"]), (False, False))
        self.assertNotIn("state_before", row["review"])
        self.assertEqual(row["tests_total"], 2, "graded on the hidden tests regardless")

    def test_the_package_carries_the_last_test_output_and_the_leads_own_tests(self):
        own = ("import unittest\nfrom two_words import double_word\n\n\nclass T(unittest.TestCase):\n"
               "    def test_joins(self):\n        self.assertEqual(double_word('a', 'b'), 'a b 2')\n")
        row, _ = self._run([
            ("coding_read", {"path": "two_words.py"}),
            ("coding_file", {"action": "delete", "path": "two_words.py", "expected_sha256": "{SHA}"}),
            ("coding_create", {"path": "two_words.py", "content": TWO_WORDS_EXAMPLE}),
            ("coding_create", {"path": "test_two_words.py", "content": own}),
            ("coding_test", {}),
            ("finish", {}),
        ], self.reviewer(["NO DEFECTS"]), mode="agentic-hai", finish_gate=True)
        self.assertEqual(row["ended"], "finish")
        pkg = self.packages[0]
        self.assertIn("### test_two_words.py", pkg)
        self.assertIn(a1_harness.OWN_TESTS_MARK.strip(), pkg, "the last coding_test output, own tests included")
        self.assertIn("test_joins", pkg)
        self.assertNotIn("(the author never ran the tests)", pkg)

    def test_with_the_gate_the_review_waits_for_an_accepted_finish(self):
        row, results = self._run([("write_file", {"path": "two_words.py", "content": TWO_WORDS_WRONG}),
                                  ("finish", {})] + [("list_files", {})] * 12,
                                 self.reviewer(["wrong"]), mode="agentic-hai", finish_gate=True)
        self.assertEqual(row["tool_counts"]["finish_refused"], 1)
        self.assertEqual(self.packages, [], "a refused finish is not a finish: no review")
        self.assertIsNone(row["review"])

    def test_without_a_reviewer_the_row_says_so(self):
        row, _ = self._run([("write_file", {"path": "two_words.py", "content": TWO_WORDS_EXAMPLE})], None)
        self.assertIsNone(row["review"])
        self.assertNotIn("review", row["tool_counts"])


class A1FailingTestsRecording(unittest.TestCase):
    """LAB-TOOL-GAPS #5: each row names the tests that failed and why, from the FINAL grade (recording only)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _grade(self, code):
        ws = a1_harness._setup_workspace(self.ex, self.tmp)
        with open(os.path.join(ws, "two_words.py"), "w", encoding="utf-8") as f:
            f.write(code)
        vp, vt, hp, ht, out = a1_harness.grade_detail(ws, self.ex)
        return vp + hp, vt + ht, a1_harness._failing_tests(out)

    def test_a_wrong_solution_names_every_failing_test_with_its_assertion(self):
        passed, total, failing = self._grade("def double_word(a, b):\n    return 'nope'\n")
        self.assertEqual(total, 2)
        self.assertEqual(len(failing), total - passed)
        self.assertEqual(sorted(f["test"].rsplit(".", 1)[-1] for f in failing), ["test_longer", "test_two_words"])
        self.assertTrue(all(f["kind"] == "FAIL" and "AssertionError" in f["why"] for f in failing), failing)

    def test_a_correct_solution_records_no_failures(self):
        passed, total, failing = self._grade(TWO_WORDS_EXAMPLE)
        self.assertEqual((passed, total), (2, 2))
        self.assertEqual(failing, [])

    def test_an_error_is_named_as_error(self):
        _, _, failing = self._grade("def double_word(a, b):\n    raise KeyError('x')\n")
        self.assertEqual({f["kind"] for f in failing}, {"ERROR"})
        self.assertTrue(all("KeyError" in f["why"] for f in failing), failing)


class A1Behaviour(unittest.TestCase):
    """The behaviour arms (A1 spec § "Work behaviour, scored by code"): one injected event each, scored from the run
    and the final workspace; plus verify-before-finish recorded on every run. A scripted lead plays each case."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)
        self.solution = open(os.path.join(self.ex, ".meta", "example.py"), encoding="utf-8").read()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, calls, behaviour="", mode="agentic-hai"):
        import re
        tool_results, users, last_sha = [], [], [""]
        it = iter(calls)

        def chat_fn(messages, tools):
            users[:] = [m["content"] for m in messages if m.get("role") == "user"]
            tool_results[:] = [m["content"] for m in messages if m.get("role") == "tool"]
            for m in reversed(messages):
                if m.get("role") == "tool":
                    s = re.search(r"[0-9a-f]{64}", m["content"])
                    if s:
                        last_sha[0] = s.group(0)
                        break
            try:
                name, args = next(it)
            except StopIteration:
                name, args = "finish", {}
            args = {k: (v.replace("{SHA}", last_sha[0]) if isinstance(v, str) else v) for k, v in args.items()}
            return {"content": "", "tool_calls": [{"name": name, "arguments": args}]}
        row = a1_harness.run_task(self.ex, chat_fn, mode=mode, work_root=self.tmp, behaviour=behaviour)
        return row, tool_results, users

    def _write(self, content):
        return [("coding_read", {"path": "two_words.py"}),
                ("coding_file", {"action": "delete", "path": "two_words.py", "expected_sha256": "{SHA}"}),
                ("coding_create", {"path": "two_words.py", "content": content})]

    # -- verify-before-finish, every run --
    def test_finish_after_a_test_on_the_final_code_is_verified(self):
        row, _, _ = self._run(self._write(self.solution) + [("coding_test", {}), ("finish", {})])
        self.assertTrue(row["passed"])
        self.assertEqual(row["behaviour"], {"verified_before_finish": True, "unverified_success": False})

    def test_finishing_wrong_code_without_testing_it_is_unverified_success(self):
        row, _, _ = self._run(self._write(TWO_WORDS_WRONG) + [("finish", {})])
        self.assertFalse(row["passed"])
        self.assertIs(row["behaviour"]["unverified_success"], True)
        # an edit AFTER the last test makes the test stale: still unverified
        row, _, _ = self._run([("coding_test", {})] + self._write(TWO_WORDS_WRONG) + [("finish", {})])
        self.assertIs(row["behaviour"]["verified_before_finish"], False)

    # -- recover --
    def test_recover_injects_one_fault_and_scores_a_retry(self):
        row, results, _ = self._run(self._write(self.solution) + [("coding_test", {}), ("coding_test", {}),
                                                                    ("finish", {})], behaviour="recover")
        self.assertIn(a1_harness.RECOVER_ERROR, results)
        self.assertEqual(results.count(a1_harness.RECOVER_ERROR), 1)             # one fault, never two
        b = row["behaviour"]
        self.assertEqual((b["arm"], b["recover_action"], b["recovered"]), ("recover", "retry", True))
        self.assertEqual(row["tool_counts"]["injected_fault"], 1)
        self.assertTrue(row["passed"])

    def test_recover_scores_finishing_straight_after_the_fault(self):
        row, _, _ = self._run(self._write(self.solution) + [("coding_test", {}), ("finish", {})],
                              behaviour="recover")
        self.assertEqual((row["behaviour"]["recover_action"], row["behaviour"]["recovered"]), ("finish", False))

    # -- correct --
    def test_correction_arrives_after_the_first_test_and_is_scored_from_the_code(self):
        fixed = self.solution + "\n__all__ = ['double_word']\n"
        row, _, users = self._run(self._write(self.solution) + [("coding_test", {})] + self._write(fixed)
                                  + [("coding_test", {}), ("finish", {})], behaviour="correct")
        correction = a1_harness.CORRECT_TEXT.format(stub="two_words.py")
        self.assertEqual(users.count(correction), 1)
        b = row["behaviour"]
        self.assertEqual((b["correction_applied"], b["correction_kept_tests"], b["correction_pass"]), (True, True, True))

    def test_ignoring_the_correction_fails_it_by_name(self):
        row, _, _ = self._run(self._write(self.solution) + [("coding_test", {}), ("finish", {})], behaviour="correct")
        b = row["behaviour"]
        self.assertEqual((b["correction_applied"], b["correction_missing"], b["correction_pass"]),
                         (False, ["double_word"], False))
        self.assertTrue(row["passed"])            # the plain grade is unchanged: the arm is scored apart

    # -- plan --
    def test_plan_constraint_is_in_the_brief_and_kept_when_respected(self):
        row, _, users = self._run(self._write(self.solution) + [("finish", {})], behaviour="plan")
        self.assertIn(a1_harness.PLAN_BRIEF, users[0])
        self.assertEqual(row["behaviour"]["plan_kept"], True)

    def test_plan_breaks_are_named_new_files_and_signature_changes(self):
        changed = "def double_word(a, b, sep=' '):\n    return a + sep + b + sep + str(len(a) + len(b))\n"
        row, _, _ = self._run(self._write(changed) + [("coding_create", {"path": "helpers.py", "content": "X = 1\n"}),
                                                      ("finish", {})], behaviour="plan")
        b = row["behaviour"]
        self.assertEqual((b["plan_new_files"], b["plan_signature_changes"], b["plan_kept"]),
                         (["helpers.py"], ["double_word"], False))

    def test_no_arm_injects_nothing_and_a_bad_arm_is_refused(self):
        row, results, users = self._run(self._write(self.solution) + [("coding_test", {}), ("finish", {})])
        self.assertNotIn(a1_harness.RECOVER_ERROR, results)
        self.assertNotIn(a1_harness.PLAN_BRIEF, users[0])
        self.assertNotIn("arm", row["behaviour"])
        with self.assertRaises(ValueError):
            a1_harness.run_task(self.ex, lambda m, t: {"content": "", "tool_calls": []}, mode="agentic-hai",
                                work_root=self.tmp, behaviour="telepathy")


class A1SolutionWriteupsAreLocked(unittest.TestCase):
    """Exercism ships solution write-ups in.approaches/ and.articles/ (pig-latin, robot-name, wordy). An audit of 897
    A1 transcripts found 70 reads of them. Every dot-folder but.docs is locked: not copied, not listed,
    not readable, not searchable - the area holding the key, never the tool."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)
        for d in (".approaches/on-the-fly", ".articles/perf"):
            os.makedirs(os.path.join(self.ex, d))
            with open(os.path.join(self.ex, d, "snippet.txt"), "w", encoding="utf-8") as f:
                f.write("SOLUTION_SNIPPET_XYZ\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_listing_reading_and_searching_never_reach_a_write_up(self):
        for mode, calls in (("agentic-hai", [("coding_list", {}), ("coding_read", {"path": ".approaches/on-the-fly/snippet.txt"}),
                                             ("coding_read", {"path": ".articles\\perf\\snippet.txt"}),
                                             ("coding_search", {"query": "SOLUTION_SNIPPET_XYZ"})]),
                            ("agentic", [("list_files", {}), ("read_file", {"path": ".approaches/on-the-fly/snippet.txt"})])):
            seen = []
            plan = iter([{"name": n, "arguments": a} for n, a in calls] + [{"name": "finish", "arguments": {}}])

            def chat_fn(messages, tools):
                if messages[-1].get("role") == "tool":
                    seen.append(messages[-1]["content"])
                return {"content": "", "tool_calls": [next(plan)]}
            a1_harness.run_task(self.ex, chat_fn, mode=mode, work_root=self.tmp)
            self.assertEqual(len(seen), len(calls), mode)
            blob = "\n".join(seen)
            self.assertNotIn("SOLUTION_SNIPPET_XYZ", blob, mode)
            self.assertNotIn(".approaches", seen[0], mode)
            self.assertNotIn(".articles", seen[0], mode)
            self.assertIn("two_words.py", seen[0], "positive control: the listing does show the stub (%s)" % mode)

    def test_the_brief_folder_stays_readable(self):
        self.assertFalse(a1_harness._locked_dir(".docs"))
        self.assertTrue(a1_harness._locked_dir(".meta"))
        self.assertTrue(a1_harness._locked_dir(".approaches"))
        self.assertFalse(a1_harness._locked_dir("src"))


class A1ErrandTool(unittest.TestCase):
    """ERRANDS: look-only helpers that run while the lead keeps
    working. Offered only with an errand_fn; the helper's tool calls run against THIS workspace, read-only."""

    def setUp(self):
        import threading
        self.tmp = tempfile.mkdtemp()
        self.ex = make_two_words_exercise(self.tmp)
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()                      # never leave a test's errand thread blocked
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lead(self, script):
        """A scripted lead: each turn returns the next tool call; records what it saw."""
        self.offered, self.seen = [], []
        it = iter(script)

        def chat_fn(messages, tools):
            self.offered.append([t["name"] for t in tools])
            self.seen.append([dict(m) for m in messages])
            step = next(it)
            if callable(step):
                step = step()
            return {"content": "", "tool_calls": [step]}
        return chat_fn

    def reader(self, helper_tools):
        """A helper that reads the stub, then reports what it read."""
        def errand_fn(messages, tools):
            helper_tools.append([t["name"] for t in tools])
            if messages[-1]["role"] != "tool":
                return {"content": "", "tool_calls": [{"name": "coding_read", "arguments": {"path": "two_words.py"}}]}
            return {"content": "REPORT: " + messages[-1]["content"][:60], "tool_calls": []}
        return errand_fn

    def test_solo_offers_no_errand_and_errand_team_offers_errand_without_delegate(self):
        finish = {"name": "finish", "arguments": {}}
        a1_harness.run_task(self.ex, self.lead([finish]), mode="agentic-hai", work_root=self.tmp)
        self.assertNotIn("errand", self.offered[0])
        row = a1_harness.run_task(self.ex, self.lead([finish]), mode="agentic-hai", work_root=self.tmp,
                                  errand_fn=self.reader([]))
        self.assertIn("errand", self.offered[0])
        self.assertIn("errand_wait", self.offered[0])
        self.assertNotIn("delegate", self.offered[0])
        self.assertEqual(row["errands"], [])

    def test_the_helper_looks_in_the_lead_workspace_with_look_only_tools_and_reports(self):
        helper_tools = []
        row = a1_harness.run_task(self.ex, self.lead([
            {"name": "errand", "arguments": {"task": "what does the stub define?"}},
            {"name": "errand_wait", "arguments": {}},
            {"name": "finish", "arguments": {}}]), mode="agentic-hai", work_root=self.tmp,
            errand_fn=self.reader(helper_tools))
        self.assertEqual(set(helper_tools[0]), {"coding_list", "coding_read", "coding_search", "coding_test"})
        # the report reaches the lead by either route: posted before its next turn, or as errand_wait's result
        waited = "\n".join(str(m.get("content") or "") for m in self.seen[2])
        self.assertEqual(waited.count("Errand e1 (done) reported:"), 1, "delivered exactly once")
        self.assertIn("REPORT:", waited)
        self.assertIn("sha256", waited, "the helper's read ran against the real workspace")
        self.assertEqual(row["errands"][0]["status"], "done")
        self.assertEqual(row["errands"][0]["calls"], {"coding_read": 1})
        self.assertTrue(row["errands"][0]["one_call"], "one read = a job the lead should have done itself")
        self.assertEqual(row["tool_counts"]["errand_reports"], 1)

    def test_the_lead_keeps_working_while_the_errand_runs_and_the_report_is_posted_to_it(self):
        state = {"helper_started": False}

        def slow_helper(messages, tools):
            state["helper_started"] = True
            self.release.wait()                 # the helper is busy until the lead has worked
            if messages[-1]["role"] != "tool":
                return {"content": "", "tool_calls": [{"name": "coding_list", "arguments": {}}]}
            return {"content": "REPORT: found it", "tool_calls": []}

        def list_then_release():
            self.assertTrue(state["helper_started"] or True)
            return {"name": "coding_list", "arguments": {}}

        def release_then_wait():
            self.release.set()
            return {"name": "errand_wait", "arguments": {}}
        row = a1_harness.run_task(self.ex, self.lead([
            {"name": "errand", "arguments": {"task": "find X"}},
            list_then_release,
            release_then_wait,
            {"name": "finish", "arguments": {}}]), mode="agentic-hai", work_root=self.tmp, errand_fn=slow_helper)
        started = self.seen[1][-1]["content"]
        self.assertIn("Errand e1 started", started, "the errand call returned at once")
        self.assertNotIn("reported", "".join(m["content"] for m in self.seen[2] if m["role"] == "tool"),
                         "the lead's own call ran while the errand had not reported")
        self.assertIn("REPORT: found it", self.seen[3][-1]["content"])
        self.assertEqual(row["errands"][0]["status"], "done")

    def test_a_write_tool_is_refused_on_an_errand_and_the_file_is_unchanged(self):
        seen_results = []

        def writer(messages, tools):
            if messages[-1]["role"] != "tool":
                return {"content": "", "tool_calls": [{"name": "coding_create",
                                                       "arguments": {"path": "evil.py", "content": "x"}}]}
            seen_results.append(messages[-1]["content"])
            return {"content": "could not write", "tool_calls": []}
        a1_harness.run_task(self.ex, self.lead([
            {"name": "errand", "arguments": {"task": "write evil.py"}},
            {"name": "errand_wait", "arguments": {}},
            lambda: {"name": "coding_list", "arguments": {}},
            {"name": "finish", "arguments": {}}]), mode="agentic-hai", work_root=self.tmp, errand_fn=writer)
        self.assertIn("not available on an errand", seen_results[0])
        self.assertNotIn("evil.py", self.seen[3][-1]["content"], "the lead's listing shows no file from the helper")

    def test_an_errand_still_running_when_the_lead_finishes_is_abandoned_not_reported(self):
        def stuck(messages, tools):
            self.release.wait()
            return {"content": "too late", "tool_calls": []}
        row = a1_harness.run_task(self.ex, self.lead([
            {"name": "errand", "arguments": {"task": "find X"}},
            {"name": "finish", "arguments": {}}]), mode="agentic-hai", work_root=self.tmp, errand_fn=stuck)
        self.assertEqual(row["errands"][0]["status"], "abandoned")
        self.assertEqual(row["errands"][0]["report_chars"], 0)

    def test_an_answer_that_looked_at_nothing_is_nudged_once_then_flagged_never_trusted(self):
        # the 10-10 decoy: qwen3:8b named a file and function that do not exist without one tool call
        def guesser(messages, tools):
            return {"content": "The file is exercise.py and it defines solve.", "tool_calls": []}
        row = a1_harness.run_task(self.ex, self.lead([
            {"name": "errand", "arguments": {"task": "which function does the stub define?"}},
            {"name": "errand_wait", "arguments": {}},
            lambda: {"name": "finish", "arguments": {}}]), mode="agentic-hai", work_root=self.tmp, errand_fn=guesser)
        seen = "\n".join(str(m.get("content") or "") for m in self.seen[2])
        self.assertIn("(unlooked)", seen)
        self.assertIn("WITHOUT looking", seen)
        self.assertEqual(row["errands"][0]["status"], "unlooked")
        self.assertEqual(row["errands"][0]["turns"], 2, "nudged once, then flagged")

    def test_a_nudged_helper_that_then_looks_is_a_real_report(self):
        def late_looker(messages, tools):
            if messages[-1]["role"] == "user" and messages[-1]["content"] == a1_harness.ERRAND_LOOK_FIRST:
                return {"content": "", "tool_calls": [{"name": "coding_list", "arguments": {}}]}
            if messages[-1]["role"] == "tool":
                return {"content": "REPORT: two_words.py", "tool_calls": []}
            return {"content": "probably solve()", "tool_calls": []}
        row = a1_harness.run_task(self.ex, self.lead([
            {"name": "errand", "arguments": {"task": "which file is the stub?"}},
            {"name": "errand_wait", "arguments": {}},
            lambda: {"name": "finish", "arguments": {}}]), mode="agentic-hai", work_root=self.tmp,
            errand_fn=late_looker)
        self.assertEqual(row["errands"][0]["status"], "done")
        self.assertIn("REPORT: two_words.py", "\n".join(str(m.get("content") or "") for m in self.seen[2]))

    def test_a_helper_that_looked_then_said_nothing_is_asked_once_then_flagged(self):
        def mute(messages, tools):
            if not any(m["role"] == "tool" for m in messages):
                return {"content": "", "tool_calls": [{"name": "coding_list", "arguments": {}}]}
            return {"content": "", "tool_calls": []}
        row = a1_harness.run_task(self.ex, self.lead([
            {"name": "errand", "arguments": {"task": "which file is the stub?"}},
            {"name": "errand_wait", "arguments": {}},
            lambda: {"name": "finish", "arguments": {}}]), mode="agentic-hai", work_root=self.tmp, errand_fn=mute)
        self.assertEqual(row["errands"][0]["status"], "no_answer")
        self.assertEqual(row["errands"][0]["turns"], 3, "looked, asked once to answer, then flagged")

    def test_errands_need_an_agentic_mode(self):
        with self.assertRaises(ValueError):
            a1_harness.run_task(self.ex, lambda m, t: {"content": ""}, mode="aider", work_root=self.tmp,
                                errand_fn=self.reader([]))


if __name__ == "__main__":
    unittest.main()
