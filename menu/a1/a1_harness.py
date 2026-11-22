"""A1 agentic-build harness, phase 1 (Python).

A language model solves one Aider-polyglot-style exercise in a tool-using
loop; the result is graded with the exercise's OWN hidden unit tests, which
the model never sees.  Standard library only.

Fix 2: the harness process NEVER imports the exercise's test module or the
model's code.  Every test run happens in a short-lived child interpreter
(`CHILD`, below), so an infinite loop in a model's solution (the 3.8's
`forth` did exactly this) hangs one child -- and one child only -- which the
300-second timeout kills.  `run_tests` shows the model only the VISIBLE
subset of the acceptance tests; the final grade runs ALL of them and
records both halves.

Exercise layout (one folder per exercise):
    <folder>/
        .docs/introduction.md   } the brief, in this order, as Aider's benchmark sends it
        .docs/instructions.md   } (introduction and append are optional; Python's append
        .docs/instructions.append.md } carries the exception messages the tests assert)
        <name>.py               the stub the model must complete
        <name>_test.py          hidden unit tests (unittest; NEVER shown)
        .meta/example.py        reference solution (NEVER shown)

`name` is the Exercism file-stem: the folder name with every hyphen replaced
by an underscore (folder `food-chain` holds `food_chain.py` /
`food_chain_test.py`).  A missing hidden test file is an ERROR, never a quiet
"not passed": `run_task` reports `ended: "error"` (with a `reason`) and
`grade()` returns total 0 so a caller can tell an error apart from a real
failure.

Public interface (pinned):
    run_task(exercise_dir, chat_fn, mode="agentic", work_root=None) -> dict
    grade(workspace_dir, exercise_dir) -> (passed_n, total_n, output_text)
    grade_detail(workspace_dir, exercise_dir) -> (visible_passed, visible_total,
                                                 hidden_passed, hidden_total,
                                                 output_text)

`chat_fn(messages, tools) -> {"content": str, "tool_calls": [{"name", "arguments"}]}`
is injected; tests pass a fake.  No turn cap, no wall-clock limit (doctrine
13a); a run ends on `finish` or on the no-progress loop cutter.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

HIDDEN_FILE_SUFFIX = "_test.py"          # never copied, never listed, never readable
PY = [sys.executable]                    # the grader's interpreter argv
HIDDEN_DIR_NAME = ".meta"                # never copied, never listed, never readable


def _locked_dir(name: str) -> bool:
    """Every dot-folder but.docs is LOCKED - never copied, listed or read. Exercism ships solution write-ups in.approaches/ and.articles/ (pig-latin, robot-name, wordy); Aider's protocol never shows them, and an audit of 897
    A1 transcripts found 70 reads of them across 14 arms. Lock the area holding the key, not the tool."""
    return name == HIDDEN_DIR_NAME or (name.startswith(".") and name != DOCS_DIR_NAME)
DOCS_DIR_NAME = ".docs"                  # brief container; its contents ARE readable

NO_PROGRESS_REPEAT = 3                   # same tool + same args this many turns in a row
NO_PROGRESS_STALL = 8                    # consecutive turns with no file change and no new test result
# The first trip of either rule nudges the model (what it is doing, why it cannot work); only a trip after the nudges
# ends the exercise.
NO_PROGRESS_NUDGES = 1

AGENTIC_SYSTEM = (
    "You are solving a coding exercise in an isolated workspace. The brief is the full specification. "
    "The exercise's test file is NOT in the workspace: run_tests runs about half of its tests (the visible "
    "subset), and after you call finish your work is graded on all of them, including the hidden half. "
    "Passing the visible subset does not prove the hidden tests pass, so make the solution meet every "
    "requirement in the brief. Use the tools to read and write files and to run the visible tests. When you "
    "are confident the solution is correct, call finish with a short note.")
# agentic-hai: the same text, naming HAI's test tool and what it also runs. The asserted replace keeps the two in step.
_HAI_FROM = "run_tests runs about half of its tests (the visible subset)"
assert _HAI_FROM in AGENTIC_SYSTEM
AGENTIC_SYSTEM_HAI = AGENTIC_SYSTEM.replace(
    _HAI_FROM, "coding_test runs about half of its tests (the visible subset) and then any test_*.py files you "
               "write in the workspace").replace("to read and write files and to run the visible tests",
                                                 "to read, search and edit files and to run tests")

# Aider's benchmark prompts, verbatim.
AIDER_ADDENDUM = (
    "\n####\n\nUse the above instructions to modify the supplied files: {file_list}\n"
    "Don't change the names of existing functions or classes, as they may be referenced from other code like "
    "unit tests, etc.\nOnly use standard libraries, don't suggest installing any packages.\n")
AIDER_TEST_FAILURES = (
    "\n####\n\nSee the testing errors above.\nThe tests are correct, don't try and change them.\n"
    "Fix the code in {file_list} to resolve the errors.\n")

# A1-team DIRECTED arm: the organic arm showed a lead never reaches for a description-only tool (the 3.8,
# 0 delegations in 207 turns), so the pricing arm TELLS the lead to use the helper. The lead still decides the split
# (that overhead is what the charter prices); nothing forces a piece per function.
DIRECTED_BRIEF = (
    "You have a helper model on another machine: the delegate tool. Use it for self-contained pieces of this exercise "
    "- a function or class with a clear contract - while you integrate its answers, run the tests and finish. The "
    "helper cannot see your workspace, the brief or the tests, so put everything it needs into its brief: the exact "
    "behaviour, the signatures, and the code it must fit. Write what it returns into the workspace yourself and test "
    "it before you trust it.")
# MANDATORY arm: the lead must hand at least
# one piece to the helper. This prices helper pieces per worker and the lead's integration; the lead's willingness to
# delegate is already priced by the organic and directed arms (zero). Nothing is enforced by code: a lead that still
# does not call records 0 pieces.
MANDATORY_BRIEF = DIRECTED_BRIEF + (
    " You must delegate at least one self-contained function or class to the helper before you call finish, and "
    "integrate and test what it returns.")
TEAM_BRIEFS = {"directed": DIRECTED_BRIEF, "mandatory": MANDATORY_BRIEF}

# FINISH GATE. Phase 8 rule: the model proposes, code decides. `finish` is accepted only when the lead
# has written test_*.py files of its own and they passed, via coding_test, on the CURRENT workspace. Nothing is
# capped: a lead that keeps calling finish without tests trips the existing no-progress rules and is still graded.
FINISH_GATE_BRIEF = (
    "finish is accepted only after you have written test_*.py files covering every requirement in the brief and they "
    "pass, via coding_test, on the current code. Until then finish is refused and tells you why.")
FINISH_GATE_REASONS = {
    "no_tests": "finish refused: you have written no test_*.py files. Write tests for every requirement in the brief, "
                "run coding_test, and call finish when they pass.",
    "stale": "finish refused: your own tests have not passed on the current code (not run since your last change, or "
             "they failed). Run coding_test and fix what fails, then call finish.",
}
OWN_TESTS_MARK = "\n\n--- your own tests (test_*.py) ---\n"

# A1-REVIEW: when the lead's finish is accepted, a DIFFERENT model on
# another box reads the ARTIFACT - the brief, the lead's files, the last test output; never the lead's own write-up
# and names defects against the brief. The lead gets ONE repair turn (a user message carrying the findings), then
# its next accepted finish is final. Solo vs solo+review on the same tasks is the measurement.
REVIEW_BRIEF = (
    "You are reviewing another programmer's solution to a coding exercise. You cannot run anything; read carefully.\n\n"
    "## Exercise brief\n{brief}\n\n## The solution files as they stand\n{files}\n\n## Last test output the author saw\n"
    "{tests}\n\n## Your task\nList every defect against the brief's requirements: behaviour the code gets wrong, "
    "a case the brief names that the code does not handle, an error message or type that does not match the brief, "
    "a function or class the brief requires that is missing. For each defect name the function and a concrete input "
    "that would fail. Do not comment on style. If you find no defect, answer exactly: NO DEFECTS")
REVIEW_REPAIR_TEXT = (
    "A reviewer read your code and tests against the brief and reported:\n\n{findings}\n\nFix what is real, run "
    "coding_test, then call finish. This is your one repair round.")
REVIEW_CLEAN = "NO DEFECTS"

# BEHAVIOUR ARMS (A1 spec § "Work behaviour, scored by code"): one injected event per arm, scored deterministically, no
# judge model and no per-exercise answer key - the correction and
# the plan constraint are checkable on ANY exercise from its own stub.
BEHAVIOURS = ("recover", "correct", "plan")
_TEST_TOOLS = ("run_tests", "coding_test")
RECOVER_ERROR = ("ERROR: the test runner crashed before running any test (a transient fault on the test host). "
                 "Nothing was run.")
CORRECT_TEXT = ("A requirement changed (from the user): {stub} must also define `__all__`, a list naming every public "
                "(not underscore-prefixed) top-level function and class it defines. Everything else in the brief still "
                "applies.")
PLAN_BRIEF = ("Constraints for this task: do not create any new files, and keep every top-level function and class "
              "from the stub with exactly the same name and parameters.")

NUDGE_TEXT = (
    "You have made no progress for several turns (the same call again, or no change to the workspace). The test "
    "file is not in the workspace and cannot be read; run_tests only re-runs the visible subset. Write or change "
    "the solution file, or call finish if you are confident it is correct.")
assert "run_tests only re-runs" in NUDGE_TEXT
NUDGE_TEXT_HAI = NUDGE_TEXT.replace("run_tests only re-runs", "coding_test only re-runs")
# After a reply with no tool call: Inspect's react agent's continue message (its tool `submit` = our `finish`). Without
# it the next request ends on the model's own message - ollama turns that into a prefill continuation, llama-server
# refuses it (HTTP 400) - so the harness spoke a different protocol per backend.
CONTINUE_TEXT = ("Please proceed to the next step using your best judgement. If you believe you have completed the "
                 "task, please call the `finish()` tool.")
# RUNAWAY GUARD: a reply the chat_fn marks `runaway` was cut at the order's per-call turn cap without calling a tool. The reply is discarded (it holds no
# action and would fill the context), the model is told to act; the first cut nudges, a cut after that ends the
# exercise as "runaway", graded on the workspace as it stands.
RUNAWAY_NUDGES = 1
RUNAWAY_TEXT = ("Your last reply ran past {cap} tokens without calling a tool and was cut off and discarded. Do not "
                "deliberate further: call a tool now - read or search a file, edit the solution, run the tests, or "
                "call finish if you are confident it is correct.")

RESULT_PREFIX = "A1-RESULT "             # the child's final stdout line (mode "all")


class _LoopError(Exception):
    """A loop could not continue (the model call raised). Carries the turns reached and the cause up to run_task,
    which records both: an `error` row always says why."""

    def __init__(self, turns, cause):
        super().__init__("%s: %s" % (type(cause).__name__, cause))
        self.turns, self.cause = turns, cause

# Memory cap for the test child. the 34 reference solutions peak at 27.5 MB working set
# (paasio; median 18.6 MB), so 2 GiB is ~75x the largest legitimate peak and limits a runaway to 2 GiB on any box.
MEM_CAP_BYTES = 2 * 2 ** 30
MEMCAP_FAILED = "A1-MEMCAP-FAILED"

# The entire child-process program.  Run as:
#     PY + ["-c", CHILD, scratch, module, mode, str(MEM_CAP_BYTES)]
# The harness process never imports the exercise's test module or the model's
# code: everything below runs in this disposable child interpreter, under a
# memory cap applied BEFORE any of that code loads.
CHILD = '''\
import json, sys, unittest

scratch = sys.argv[1]
module = sys.argv[2]
mode = sys.argv[3]
mem_cap = int(sys.argv[4])


def _cap_memory(limit):
    if sys.platform == "win32":
        import ctypes, ctypes.wintypes as w

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class _Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes",
                                                        "WriteBytes", "OtherBytes")]

        class _Ext(ctypes.Structure):
            _fields_ = [("Basic", _Basic), ("Io", _Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k = ctypes.windll.kernel32
        k.CreateJobObjectW.restype = w.HANDLE
        k.GetCurrentProcess.restype = w.HANDLE
        k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        job = k.CreateJobObjectW(None, None)
        info = _Ext()
        info.Basic.LimitFlags = 0x100                      # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = limit
        return bool(job and k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
                    and k.AssignProcessToJobObject(job, k.GetCurrentProcess()))
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    return True


try:
    capped = _cap_memory(mem_cap)
except Exception:
    capped = False
if not capped:
    sys.stdout.write("A1-MEMCAP-FAILED" + chr(10))
    sys.exit(3)

sys.path.insert(0, scratch)
suite = unittest.defaultTestLoader.loadTestsFromName(module)

cases = []
def _flatten(s, out):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            _flatten(t, out)
        else:
            out.append(t)
_flatten(suite, cases)

# The test module did not import against the model's code (unittest wraps that as a fake `_FailedTest`): the
# error is the model's to see and fix, so it is never split or hidden; the grade reports it as a crash.
broken = [t for t in cases if t.__class__.__name__ == "_FailedTest"]
if broken:
    if mode == "visible":
        sys.stdout.write("A1-VISIBLE %d" % len(broken) + chr(10))
        sys.stdout.flush()
    unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(broken))
    if mode == "all":
        sys.stdout.write("A1-LOADFAIL" + chr(10))
    sys.exit(0)

ids = sorted(t.id() for t in cases)
visible = ids[0::2] if len(ids) > 1 else []

by_id = {t.id(): t for t in cases}
if mode == "visible":
    sys.stdout.write("A1-VISIBLE %d\\n" % len(visible))
    sys.stdout.flush()
    to_run = unittest.TestSuite([by_id[i] for i in visible])
    unittest.TextTestRunner(verbosity=2).run(to_run)
    sys.exit(0)

# mode == "all" -- collect pass/fail from the result's failures/errors
# lists (the pinned design's alternative); no subclass needed because
# TextTestRunner wraps a non-TextTestResult stream into a _WritelnResult
# proxy that drops write().
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(unittest.TestSuite([by_id[i] for i in ids]))
failed = set()
for t, _ in (result.failures or []) + (result.errors or []):
    failed.add(t.id())
passed = [i for i in ids if i not in failed]
sys.stdout.write("A1-RESULT " + json.dumps({"visible": visible, "passed": passed}) + "\\n")
sys.exit(0)
'''

# Child that runs the model's OWN test files (agentic-hai mode): the SAME memory-cap code as CHILD, sliced out of it so
# the two can never drift, then unittest discovery of test_*.py in a scratch copy of the workspace.
OWN_TESTS_CHILD = ("import sys, unittest\nscratch = sys.argv[1]\nmem_cap = int(sys.argv[2])\n\n"
                   + CHILD[CHILD.index("def _cap_memory"):CHILD.index("sys.path.insert(0, scratch)")]
                   + "sys.path.insert(0, scratch)\n"
                   + "suite = unittest.defaultTestLoader.discover(scratch, pattern='test_*.py', top_level_dir=scratch)\n"
                   + "unittest.TextTestRunner(verbosity=2).run(suite)\n")
CHILD_TIMEOUT_S = 300               # the test child's documented bound; the own-tests child shares it
# The model's own tests run code the model wrote while the hidden tests sit on the same disk: a test file that names
# the answer key or leaves the workspace is refused (counted on the row), never run.
OWN_TEST_FORBIDDEN = re.compile(r"_test\.py|polyglot|\.meta|example\.py|exercises|[A-Za-z]:[\\/]|\.\.[\\/]", re.I)

_TOOLS = [
    {"name": "list_files",
     "description": "List the files in the workspace (the exercise stub and any files you created). "
                    "Returns a newline-separated relative path listing.",
     "parameters": {}},
    {"name": "read_file",
     "description": "Read a file in the workspace. Returns its text content.",
     "parameters": {"path": "path relative to the workspace root"}},
    {"name": "write_file",
     "description": "Write (create or overwrite) a file in the workspace.",
     "parameters": {"path": "path relative to the workspace root",
                    "content": "full text of the file to write"}},
    {"name": "run_tests",
     "description": "Run the visible subset of this exercise's acceptance tests against "
                    "the current workspace. A one-test exercise keeps its only test hidden, "
                    "so you may see 'No visible tests'. Returns ONLY the unittest output "
                    "text (never the test source).",
     "parameters": {}},
    {"name": "finish",
     "description": "End the task. Provide a short note describing the result.",
     "parameters": {"note": "free-text summary of what you did"}},
]
# agentic-hai: HAI's production coding tools (orchestrator tools.py _CODING_* schemas), same names and descriptions
# verbatim, minus the `workspace` argument (an A1 item has exactly one workspace). Contracts follow the coding-runner
# (runner.py): read returns sha256; patch is an exact, sha-guarded, unique-match replacement; create refuses an
# existing file; file moves/deletes one sha-checked file; there is no arbitrary shell, as in production.
_TOOLS_HAI = [
    {"name": "coding_list",
     "description": "List files and directories in a registered workspace with bounded depth and result count.",
     "parameters": {}},
    {"name": "coding_read",
     "description": "Read a UTF-8 text file in a registered workspace. Returns sha256, required by coding_patch.",
     "parameters": {"path": "path relative to the workspace root"}},
    {"name": "coding_search",
     "description": "Search text in a registered workspace with ripgrep. Returns relative paths, line numbers, and "
                    "matching lines.",
     "parameters": {"query": "regular expression to search for", "glob": "optional filename glob, e.g. *.py"}},
    {"name": "coding_patch",
     "description": "Apply an exact text replacement. First call coding_read and pass its sha256. The runner rejects "
                    "stale, ambiguous, protected, or escaping edits.",
     "parameters": {"path": "file to edit", "old": "exact existing text", "new": "replacement text",
                    "expected_sha256": "sha256 from coding_read", "replace_all": "true to replace every match"}},
    {"name": "coding_create",
     "description": "Create one new UTF-8 text file in a writable registered workspace. Fails if the file exists, is "
                    "protected, or escapes the workspace.",
     "parameters": {"path": "new file path", "content": "full text of the new file"}},
    {"name": "coding_file",
     "description": "Move or delete one existing file inside a writable workspace. First call coding_read and pass its "
                    "exact sha256. Move rejects collisions. Delete is non-recursive. Both reject stale, protected, or "
                    "escaping paths.",
     "parameters": {"action": "move or delete", "path": "source (move) or target (delete)",
                    "destination": "required for move", "expected_sha256": "sha256 from coding_read"}},
    {"name": "coding_test",
     "description": "Run the visible subset of this exercise's acceptance tests, then every test_*.py file you wrote "
                    "in the workspace. Returns the unittest output of both.",
     "parameters": {}},
    _TOOLS[-1],                                                          # finish
]
# A1-team (34-A1 spec "Sub-agents"): offered ONLY when the order supplies a delegate_fn, so A1-solo and A1-team differ
# in this one tool. The helper sees nothing but the brief.
DELEGATE_TOOL = {"name": "delegate",
                 "description": "Hand ONE self-contained piece of the work to a helper model on another machine and "
                                "get its answer back as text. The helper cannot see your workspace, the brief or the "
                                "tests: put everything it needs into the brief (what to write, the exact behaviour, "
                                "the code it must fit). Anything it returns you must still write into the workspace "
                                "yourself.",
                 "parameters": {"brief": "the complete, self-contained request for the helper"}}
# ERRANDS: offered ONLY when the order supplies an errand_fn. The helper's
# model runs on another box; its tool calls run HERE, against this workspace, read-only.
ERRAND_TOOL = {"name": "errand",
               "description": "Send a helper on another machine on a small, bounded job about your workspace while you "
                              "keep working. Use it for a job that takes SEVERAL steps but has a short answer - e.g. "
                              "run the visible tests and report which fail and the line each one fails on; check every "
                              "caller of X for Y. If a single search or read would answer it, do that yourself instead: "
                              "an errand costs more than one tool call. The helper can list, search and read your files "
                              "and run the visible tests; it never changes a file. Returns an errand id at once; the "
                              "answer is posted to you as a message when it is ready.",
               "parameters": {"task": "the question, and the short answer you want back (a count, a location, yes/no)"}}
ERRAND_WAIT_TOOL = {"name": "errand_wait",
                    "description": "Wait for your open errands to report (use it when you need a report before you can "
                                   "go on). Returns their reports.",
                    "parameters": {}}
ERRAND_READONLY = ("list_files", "read_file", "run_tests", "coding_list", "coding_read", "coding_search", "coding_test")
ERRAND_LOOK_FIRST = ("You have not looked at the workspace yet, so you cannot know the answer. Use the tools to find "
                     "it (list or search first), then answer.")
ERRAND_ANSWER_NOW = "Reply now with the answer in plain text, using what the tools showed you."
ERRAND_SYSTEM = ("You answer one small question for a programmer about their workspace. You have NOT seen the "
                 "workspace: find the answer with the tools before you reply, never from a guess. You can only look: list, "
                 "search and read files and run the visible tests; you cannot change anything. Prefer a search over "
                 "reading whole files, and read a whole file only when the question needs its contents. When you have "
                 "the answer, reply in plain text (no tool call) with just the answer: the count, the file and line, "
                 "yes or no, plus one line of evidence. Do not paste file contents back.")


class _Errands:
    """The lead's open errands. Each runs in its own thread: the helper's turns go through `errand_fn(messages, tools)
    -> {"content", "tool_calls"[, "error"]}` (the placed helper window), its tool calls run against the workspace
    through the same dispatcher as the lead's, restricted to ERRAND_READONLY. An errand ends when the helper replies
    without a tool call (its report), on the loop cutter's no-progress rule (no new call for NO_PROGRESS_STALL turns),
    on a helper error, or when the lead's loop ends (stop)."""

    def __init__(self, ws, exercise_dir, lead_tools, errand_fn):
        import threading
        self.ws, self.exercise_dir, self.errand_fn = ws, exercise_dir, errand_fn
        names = {t["name"] for t in lead_tools}
        self.tools = [t for t in lead_tools if t["name"] in ERRAND_READONLY and t["name"] in names]
        self.lock = threading.Lock()
        self.done = threading.Condition(self.lock)
        self.records: List[Dict[str, Any]] = []
        self.ready: List[Dict[str, Any]] = []
        self.stopped = False

    def start(self, task: str) -> str:
        import threading
        with self.lock:
            rec = {"id": "e%d" % (len(self.records) + 1), "task": task, "status": "running", "turns": 0,
                   "calls": {}, "started": time.monotonic()}
            self.records.append(rec)
        threading.Thread(target=self._run, args=(rec,), daemon=True, name="errand-" + rec["id"]).start()
        return rec["id"]

    def open_ids(self) -> List[str]:
        with self.lock:
            return [r["id"] for r in self.records if r["status"] == "running"]

    def _finish(self, rec, status, report):
        with self.lock:
            if rec["status"] == "abandoned":          # stopped by the lead's end: whatever happened after is not a report
                self.done.notify_all()
                return
            rec.update(status=status, report=report, wall_s=round(time.monotonic() - rec["started"], 1))
            self.ready.append(rec)
            self.done.notify_all()

    def _run(self, rec):
        messages: List[Dict[str, Any]] = [{"role": "system", "content": ERRAND_SYSTEM},
                                          {"role": "user", "content": rec["task"]}]
        seen, stall, counts = set(), 0, {}
        try:
            while not self.stopped:
                resp = self.errand_fn(messages, self.tools) or {}
                rec["turns"] += 1
                if self.stopped:
                    break
                if resp.get("error"):
                    return self._finish(rec, "error", "ERROR: " + str(resp["error"])[:300])
                calls = resp.get("tool_calls") or []
                content = resp.get("content") or ""
                msg: Dict[str, Any] = {"role": "assistant", "content": content}
                if calls:
                    msg["tool_calls"] = [{"id": "e_%d" % (rec["turns"] * 100 + i), "type": "function",
                                          "function": {"name": c.get("name", ""), "arguments": c.get("arguments") or {}}}
                                         for i, c in enumerate(calls)]
                messages.append(msg)
                if not calls and not rec["calls"]:
                    # an answer that looked at nothing is a guess (decoy 10-10: qwen3:8b named a file and a function
                    # that do not exist without one tool call): one nudge to look, then it is flagged, never trusted
                    if not rec.get("nudged"):
                        rec["nudged"] = True
                        messages.append({"role": "user", "content": ERRAND_LOOK_FIRST})
                        continue
                    return self._finish(rec, "unlooked", "(the helper answered WITHOUT looking at the workspace - "
                                                         "treat as a guess, not a finding) " + content.strip()[:500])
                if not calls and not content.strip():
                    # looked, then replied with nothing (decoy 10-10: 28 tokens, no text, no parsable call)
                    if not rec.get("asked_answer"):
                        rec["asked_answer"] = True
                        messages.append({"role": "user", "content": ERRAND_ANSWER_NOW})
                        continue
                    return self._finish(rec, "no_answer", "(the helper looked but gave no answer)")
                if not calls:
                    return self._finish(rec, "done", content.strip())
                new = False
                for i, c in enumerate(calls):
                    name, args = c.get("name", ""), c.get("arguments") or {}
                    rec["calls"][name] = rec["calls"].get(name, 0) + 1
                    key = _key_of_call(name, args)
                    if key not in seen:
                        seen.add(key)
                        new = True
                    result = (_dispatch_tool(self.ws, name, args, self.exercise_dir, self.tools, counts)
                              if name in {t["name"] for t in self.tools}
                              else "ERROR: %s is not available on an errand (look-only tools)" % name)
                    messages.append({"role": "tool", "tool_call_id": "e_%d" % (rec["turns"] * 100 + i),
                                     "content": result})
                stall = 0 if new else stall + 1
                if stall >= NO_PROGRESS_STALL:
                    return self._finish(rec, "no_progress", "(the helper repeated itself and was stopped; last "
                                                            "words: %s)" % (content.strip()[:500] or "none"))
            self._finish(rec, "abandoned", "(the lead's work ended before this errand reported)")
        except Exception as e:                                              # noqa: BLE001
            self._finish(rec, "error", "ERROR: the errand failed (%s: %s)" % (type(e).__name__, str(e)[:200]))

    def collect(self) -> List[Dict[str, Any]]:
        with self.lock:
            out, self.ready = self.ready, []
        return out

    def wait_any(self) -> List[Dict[str, Any]]:
        """Block until at least one errand reports (or none is open). A STATUS wait, no clock."""
        with self.lock:
            while not self.ready and any(r["status"] == "running" for r in self.records):
                self.done.wait()
            out, self.ready = self.ready, []
        return out

    def stop(self):
        with self.lock:
            self.stopped = True
            for r in self.records:
                if r["status"] == "running":
                    r["status"] = "abandoned"

    def summary(self) -> List[Dict[str, Any]]:
        with self.lock:
            # one_call: the helper answered with at most one tool call - a job the lead should have done itself
            # (an `unlooked` errand made none and is counted by its status, not here)
            return [dict({k: r.get(k) for k in ("id", "task", "status", "turns", "calls", "wall_s")},
                         report_chars=len(r.get("report") or ""),
                         one_call=r.get("status") == "done" and sum((r.get("calls") or {}).values()) <= 1)
                    for r in self.records]


def _errand_reports_text(reps: List[Dict[str, Any]]) -> str:
    return "\n\n".join("Errand %s (%s) reported:\n%s" % (r["id"], r["status"], r.get("report") or "") for r in reps)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _safe_resolve(root: str, raw_path: str) -> Optional[str]:
    """Resolve *raw_path* against *root*; return absolute path or None if it escapes."""
    root_abs = os.path.abspath(root)
    candidate = os.path.abspath(os.path.join(root_abs, raw_path))
    if candidate == root_abs:
        return None
    # allow everything under root (including root itself handled by caller if needed)
    if not (candidate.startswith(root_abs + os.sep)):
        return None
    return candidate


def _is_hidden(path_abs: str, root_abs: str) -> bool:
    """True if *path_abs* (or any ancestor below root) is a hidden file or under a locked folder (_locked_dir)."""
    rel = os.path.relpath(path_abs, root_abs)
    parts = rel.split(os.sep)
    for part in parts:
        if part not in (".", "..") and _locked_dir(part):
            return True
    base = parts[-1]
    if base.endswith(HIDDEN_FILE_SUFFIX):
        return True
    return False


# A missing hash is not a changed file: told "file changed", the 3.8 re-read and retried the same call (10-14% of every
# tool call in the 10-07 solo arms). Same wording as HAI's coding runner.
NO_SHA_ERROR = "ERROR: expected_sha256 is required - pass the sha256 that coding_read returned for this file"


class _Workspace:
    """Path-safe file operations inside a single temp workspace root."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    # -- internal ------------------------------------------------------------
    def _resolve(self, raw_path: str) -> Optional[str]:
        ap = _safe_resolve(self.root, raw_path)
        if ap is None:
            return None
        if _is_hidden(ap, self.root):
            return None
        return ap

    # -- public tool surface -------------------------------------------------
    def list_files(self) -> str:
        entries: List[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            # prune hidden dirs in-place so os.walk never descends
            dirnames[:] = [d for d in dirnames if not _locked_dir(d)]
            rel_dir = os.path.relpath(dirpath, self.root)
            for fn in sorted(filenames):
                if fn.endswith(HIDDEN_FILE_SUFFIX):
                    continue
                rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                entries.append(rel)
            for d in sorted(dirnames):
                rel = d if rel_dir == "." else os.path.join(rel_dir, d)
                entries.append(rel + "/")
        entries.sort()
        return "\n".join(entries) if entries else "(empty)"

    def read_file(self, path: str) -> str:
        ap = self._resolve(path)
        if ap is None:
            return "ERROR: path not allowed in workspace"
        if not os.path.isfile(ap):
            return "ERROR: not a file: " + path
        with open(ap, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def write_file(self, path: str, content: str) -> str:
        ap = self._resolve(path)
        if ap is None:
            return "ERROR: path not allowed in workspace"
        d = os.path.dirname(ap)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(ap, "w", encoding="utf-8") as f:
            f.write(content)
        return "OK wrote " + path

    # -- HAI production-tool contracts (coding-runner runner.py), agentic-hai mode ----------------------------------
    @staticmethod
    def _sha(data: bytes) -> str:
        import hashlib
        return hashlib.sha256(data).hexdigest()

    def coding_read(self, path: str) -> str:
        ap = self._resolve(path)
        if ap is None or not os.path.isfile(ap):
            return "ERROR: path not allowed or not a file: " + str(path)
        data = open(ap, "rb").read()
        return "sha256: %s\n%s" % (self._sha(data), data.decode("utf-8", "replace"))

    def coding_search(self, query: str, glob: str = "") -> str:
        import fnmatch
        if not query or len(query) > 500:
            return "ERROR: query must contain 1-500 characters"
        try:
            rx = re.compile(query)
        except re.error as e:
            return "ERROR: invalid regular expression: %s" % e
        hits = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not _locked_dir(d)]
            for fn in sorted(filenames):
                ap = os.path.join(dirpath, fn)
                rel = os.path.relpath(ap, self.root).replace(os.sep, "/")
                if _is_hidden(ap, self.root) or (glob and not fnmatch.fnmatch(fn, glob)):
                    continue
                try:
                    lines = open(ap, encoding="utf-8").read().splitlines()
                except (UnicodeDecodeError, OSError):
                    continue
                hits += ["%s:%d: %s" % (rel, i, ln) for i, ln in enumerate(lines, 1) if rx.search(ln)]
        return "\n".join(hits[:500]) if hits else "(no matches)"

    def coding_patch(self, path: str, old: str, new: str, expected_sha256: str, replace_all=False) -> str:
        ap = self._resolve(path)
        if ap is None or not os.path.isfile(ap):
            return "ERROR: coding_patch path must be an existing text file"
        data = open(ap, "rb").read()
        if not expected_sha256:
            return NO_SHA_ERROR
        if self._sha(data) != expected_sha256:
            return "ERROR: file changed since it was read (sha256 mismatch) - call coding_read again"
        text = data.decode("utf-8")
        n = text.count(old) if old else 0
        if n == 0:
            return "ERROR: old text was not found"
        if not replace_all and n != 1:
            return "ERROR: old text is ambiguous (%d matches)" % n
        text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        open(ap, "wb").write(text.encode("utf-8"))
        return "OK patched %s (%d replacement(s)); new sha256: %s" % (path, n if replace_all else 1,
                                                                     self._sha(text.encode("utf-8")))

    def coding_create(self, path: str, content: str) -> str:
        ap = self._resolve(path)
        if ap is None:
            return "ERROR: path not allowed in workspace"
        if os.path.exists(ap):
            return "ERROR: file already exists"
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        open(ap, "wb").write((content or "").encode("utf-8"))
        return "OK created %s; sha256: %s" % (path, self._sha((content or "").encode("utf-8")))

    def coding_file(self, action: str, path: str, expected_sha256: str, destination: str = "") -> str:
        ap = self._resolve(path)
        if ap is None or not os.path.isfile(ap):
            return "ERROR: path not allowed or not a file: " + str(path)
        if not expected_sha256:
            return NO_SHA_ERROR
        if self._sha(open(ap, "rb").read()) != expected_sha256:
            return "ERROR: file changed since it was read (sha256 mismatch) - call coding_read again"
        if action == "delete":
            os.remove(ap)
            return "OK deleted " + path
        if action == "move":
            dst = self._resolve(destination or "")
            if dst is None:
                return "ERROR: destination not allowed in workspace"
            if os.path.exists(dst):
                return "ERROR: destination exists"
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(ap, dst)
            return "OK moved %s -> %s" % (path, destination)
        return "ERROR: action must be move or delete"


# ---------------------------------------------------------------------------
# exercise setup
# ---------------------------------------------------------------------------

def _exercise_name(exercise_dir: str) -> str:
    """File-stem for an exercise folder (Exercism convention).

    The folder name with every hyphen replaced by an underscore, so
    folder `food-chain` maps to `food_chain.py` / `food_chain_test.py`.
    Used everywhere a file name is derived from the folder (copying,
    hiding the test, grading, path refusals).
    """
    return os.path.basename(os.path.abspath(exercise_dir)).replace("-", "_")


def _setup_workspace(exercise_dir: str, work_root: Optional[str]) -> str:
    """Copy the exercise into a fresh temp workspace WITHOUT the hidden test
    file and WITHOUT .meta/.  Returns the workspace root path."""
    exercise_dir = os.path.abspath(exercise_dir)
    name = _exercise_name(exercise_dir)
    target = tempfile.mkdtemp(prefix="a1_" + name + "_",
                              dir=os.path.abspath(work_root) if work_root else None)

    for item in os.listdir(exercise_dir):
        src = os.path.join(exercise_dir, item)
        dst = os.path.join(target, item)
        if _locked_dir(item):
            continue
        if item.endswith(HIDDEN_FILE_SUFFIX) and os.path.isfile(src):
            continue
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    return target


# Aider's benchmark order: introduction (if any), instructions, then the track's append (Python's carries the exact
# exception messages the tests assert).
BRIEF_PARTS = ("introduction.md", "instructions.md", "instructions.append.md")


def _brief_text(workspace: str, name: str) -> str:
    parts = []
    for fname in BRIEF_PARTS:
        p = os.path.join(workspace, DOCS_DIR_NAME, fname)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                parts.append(f.read().strip())
    return "\n\n".join(parts)


def _stub_text(workspace: str, name: str) -> str:
    stub = os.path.join(workspace, name + ".py")
    if os.path.isfile(stub):
        with open(stub, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    return ""


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------

def _grade_run(workspace_dir: str, exercise_dir: str, mode: str) -> Tuple[Optional[int], Optional[int], str]:
    """Run *mode* ("visible" or "all") of the exercise's tests in a child.

    Returns (passed_n, total_n, output_text); passed_n and total_n are None
    when the child did not emit a parseable unittest summary (missing test
    file, timeout, crash).
    """
    workspace_dir = os.path.abspath(workspace_dir)
    exercise_dir = os.path.abspath(exercise_dir)
    name = _exercise_name(exercise_dir)
    test_src = os.path.join(exercise_dir, name + HIDDEN_FILE_SUFFIX)

    if not os.path.isfile(test_src):
        return (None, None,
                "ERROR: no hidden test file found for exercise " + name
                + " (expected " + name + HIDDEN_FILE_SUFFIX + " in "
                + exercise_dir + ")")

    # scratch copy of the CURRENT workspace (model's work), tests added on top
    parent = tempfile.mkdtemp(prefix="a1_grade_")
    scratch = os.path.join(parent, name)
    try:
        if os.path.isdir(workspace_dir):
            shutil.copytree(workspace_dir, scratch, dirs_exist_ok=True)
        else:
            os.makedirs(scratch, exist_ok=True)
        shutil.copy2(test_src, os.path.join(scratch, name + HIDDEN_FILE_SUFFIX))

        # the scratch dir goes on sys.path explicitly: an embeddable Python
        # (a `._pth` file, e.g. ai-toolkit's venv) adds neither the cwd nor
        # PYTHONPATH, and the child then cannot import the test module at all
        proc = subprocess.run(
            PY + ["-c", CHILD, scratch, name + HIDDEN_FILE_SUFFIX[:-3], mode, str(MEM_CAP_BYTES)],
            cwd=scratch,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "PYTHONPATH": scratch},
        )
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        out = "ERROR: test run timed out"
    except Exception as e:  # noqa: BLE001
        out = "ERROR: " + repr(e)
    finally:
        shutil.rmtree(parent, ignore_errors=True)

    if MEMCAP_FAILED in out:
        return (None, None, "ERROR: harness could not cap the test child's memory (%s); no test was run"
                % MEMCAP_FAILED)
    if "A1-LOADFAIL" in out:            # the tests could not even import the model's code: a crash, no count
        return (None, None, out)
    passed, total = _parse_unittest_counts(out)
    if total == 0:
        return (None, None, out)
    return (passed, total, out)


def grade(workspace_dir: str, exercise_dir: str) -> Tuple[int, int, str]:
    """Run ALL of the exercise's tests (mode "all") against a scratch copy of
    the workspace, in a child process.

    Returns (passed_n, total_n, output_text).  A missing hidden test file
    keeps total 0 with an ERROR line in the output, so a caller can tell it
    apart from a real failure.
    """
    passed, total, out = _grade_run(workspace_dir, exercise_dir, "all")
    return (0 if passed is None else passed, 0 if total is None else total, out)


_FAIL_HEAD = re.compile(r"^(FAIL|ERROR): (\S+) \((\S+)\)\s*$")


def _failing_tests(output: str) -> List[Dict[str, str]]:
    """Each failed or errored test in a unittest output: {"test": dotted id, "kind": FAIL|ERROR, "why": the exception
    line and what follows it (an assertEqual diff), joined}. Recording only - run after the final grade, never shown
    to the model."""
    out, lines = [], (output or "").splitlines()
    for i, ln in enumerate(lines):
        m = _FAIL_HEAD.match(ln)
        if not m:
            continue
        block = []
        for nxt in lines[i + 2:]:
            if nxt.startswith("=" * 20) or nxt.startswith("-" * 20):
                break
            block.append(nxt)
        # the traceback's frames are indented; the exception itself starts at column 0
        start = next((j for j, b in enumerate(block) if b.strip() and not b.startswith((" ", "Traceback"))), None)
        tail = [b.strip() for b in (block[start:] if start is not None else block) if b.strip()]
        out.append({"test": m.group(3), "kind": m.group(1), "why": " | ".join(tail)[:300]})
    return out


def grade_detail(workspace_dir: str, exercise_dir: str):
    """Run mode "all" and split the result into visible/hidden halves.

    Returns (visible_passed, visible_total, hidden_passed, hidden_total,
    output_text).  The split is deterministic (sorted test ids, visible =
    ids[0::2] when there is more than one test); see ``CHILD``.
    """
    passed, total, out = _grade_run(workspace_dir, exercise_dir, "all")
    if passed is None:
        return (0, 0, 0, 0, out)
    det = _parse_result_line(out)
    if det is None:
        # no A1-RESULT line (shouldn't happen) -- fall back to totals only
        return (0, 0, passed, total, out)
    visible_ids, passed_ids = det
    visible_total = len(visible_ids)
    visible_passed = sum(1 for i in visible_ids if i in passed_ids)
    hidden_total = total - visible_total
    hidden_passed = len(passed_ids) - visible_passed
    return (visible_passed, visible_total, hidden_passed, hidden_total, out)


def _parse_result_line(output: str):
    """Extract (visible_ids, passed_ids) from the child's A1-RESULT line."""
    for line in (output or "").splitlines():
        if line.startswith(RESULT_PREFIX):
            try:
                payload = json.loads(line[len(RESULT_PREFIX):].strip())
                return (list(payload.get("visible", [])),
                        set(payload.get("passed", [])))
            except (ValueError, KeyError):
                return None
    return None


def _own_test_files(workspace_dir: str) -> List[str]:
    root = os.path.abspath(workspace_dir)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _locked_dir(d)]
        out += [os.path.join(dirpath, f) for f in filenames if f.startswith("test_") and f.endswith(".py")]
    return sorted(out)


def _own_tests_output(workspace_dir: str, tool_counts: Dict[str, int]) -> str:
    """The model's OWN test files (test_*.py it wrote), run in a child under the test child's memory cap and time
    bound, in a scratch copy of the workspace (which never holds the hidden test file). A file that names the answer
    key or leaves the workspace is refused and counted, never run."""
    files = _own_test_files(workspace_dir)
    if not files:
        return "(you have written no test_*.py files)"
    bad = [os.path.relpath(f, workspace_dir) for f in files
           if OWN_TEST_FORBIDDEN.search(open(f, encoding="utf-8", errors="replace").read())]
    if bad:
        tool_counts["own_tests_refused"] = tool_counts.get("own_tests_refused", 0) + 1
        return ("REFUSED: your test files may only work inside the workspace (no absolute paths, no '..', no "
                "acceptance-test or reference files): %s" % ", ".join(bad))
    parent = tempfile.mkdtemp(prefix="a1_own_")
    scratch = os.path.join(parent, "ws")
    try:
        shutil.copytree(os.path.abspath(workspace_dir), scratch)
        try:
            proc = subprocess.run(PY + ["-c", OWN_TESTS_CHILD, scratch, str(MEM_CAP_BYTES)], cwd=scratch,
                                  capture_output=True, text=True, errors="replace", timeout=CHILD_TIMEOUT_S,
                                  env={**os.environ, "PYTHONPATH": scratch})
            out = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            out = "ERROR: your tests ran for %d s without finishing and were killed" % CHILD_TIMEOUT_S
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    if MEMCAP_FAILED in out:
        return "ERROR: the harness could not cap your tests' memory; they were not run"
    tool_counts["own_tests_runs"] = tool_counts.get("own_tests_runs", 0) + 1
    return out or "(no output)"


def _run_tests_output(workspace_dir: str, exercise_dir: str) -> str:
    """The text shown to the model for a ``run_tests`` call: the VISIBLE
    subset of the acceptance tests, run in a child, or the no-visible-tests
    notice for a one-test exercise (which keeps its only test hidden).
    Only the child's own `A1-VISIBLE 0` means "no visible tests": a child that
    crashed before counting (the model's file does not import) returns its
    traceback, so the model sees its own error."""
    _p, _t, out = _grade_run(workspace_dir, exercise_dir, "visible")
    lines = (out or "").splitlines()
    if "A1-VISIBLE 0" in lines:
        return ("No visible tests for this exercise; your work is graded on "
                "hidden tests after finish.")
    return "\n".join(l for l in lines if not l.startswith("A1-VISIBLE "))


def _parse_unittest_counts(output: str) -> Tuple[int, int]:
    """Best-effort parse of 'Ran N tests' and fail/error counts from unittest output."""
    import re
    total = 0
    failed = 0
    m = re.search(r"Ran (\d+) tests?", output)
    if m:
        total = int(m.group(1))
    # unittest prints 'FAILED (failures=N)' / 'FAILED (errors=M)' / 'FAILED (failures=N, errors=M)'
    fm = re.search(r"FAILED \(.*?failures=(\d+)", output)
    em = re.search(r"FAILED \(.*?errors=(\d+)", output)
    if fm:
        failed += int(fm.group(1))
    if em:
        failed += int(em.group(1))
    if "FAILED" in output and failed == 0:
        # 'FAILED' present but no explicit counts (older style) — treat all as failed
        failed = total
    passed = max(0, total - failed)
    return (passed, total)


# ---------------------------------------------------------------------------
# tool dispatch
# ---------------------------------------------------------------------------

def _dispatch_tool(ws: _Workspace, tool_name: str, args: Dict[str, Any],
                   exercise_dir: str, tools: Optional[List[Dict[str, Any]]] = None,
                   tool_counts: Optional[Dict[str, int]] = None, delegate_fn: Optional[Callable] = None) -> str:
    """Execute one tool call; returns the result text shown to the model. Only the tools OFFERED in this mode run."""
    offered = {t["name"] for t in (tools or _TOOLS)}
    if tool_name not in offered:
        return "ERROR: unknown tool: " + tool_name
    if tool_name == "delegate":
        brief = str(args.get("brief", "") or "").strip()
        if not brief:
            return "ERROR: delegate needs a non-empty brief"
        if delegate_fn is None:
            return "ERROR: no helper is available in this run"
        return delegate_fn(brief)
    if tool_name == "coding_list":
        return ws.list_files()
    if tool_name == "coding_read":
        return ws.coding_read(args.get("path", ""))
    if tool_name == "coding_search":
        return ws.coding_search(args.get("query", ""), args.get("glob", "") or "")
    if tool_name == "coding_patch":
        return ws.coding_patch(args.get("path", ""), args.get("old", ""), args.get("new", ""),
                               args.get("expected_sha256", ""), bool(args.get("replace_all", False)))
    if tool_name == "coding_create":
        return ws.coding_create(args.get("path", ""), args.get("content", ""))
    if tool_name == "coding_file":
        return ws.coding_file(args.get("action", ""), args.get("path", ""), args.get("expected_sha256", ""),
                              args.get("destination", "") or "")
    if tool_name == "coding_test":
        return (_run_tests_output(ws.root, exercise_dir) + OWN_TESTS_MARK
                + _own_tests_output(ws.root, tool_counts if tool_counts is not None else {}))
    if tool_name == "list_files":
        return ws.list_files()
    if tool_name == "read_file":
        path = args.get("path", "")
        return ws.read_file(path)
    if tool_name == "write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        return ws.write_file(path, content)
    if tool_name == "run_tests":
        # visible subset only, in a child; never the test source, never a
        # hidden test's name
        return _run_tests_output(ws.root, exercise_dir)
    if tool_name == "finish":
        # handled by the caller (loop); we return a marker
        return "__FINISH__"
    return "ERROR: unknown tool: " + tool_name


# ---------------------------------------------------------------------------
# agentic loop
# ---------------------------------------------------------------------------

@dataclass
class _TurnState:
    tool: str = ""
    args: Tuple = ()
    repeat: int = 0
    stall: int = 0
    nudges: int = 0
    runaways: int = 0                            # replies cut at the order's turn cap without a tool call
    seen: set = field(default_factory=set)       # workspace states reached so far
    looked: set = field(default_factory=set)     # (state, observation call) pairs already made


def _workspace_state(root: str) -> str:
    """Hash of everything the model can see in its workspace (hidden tests and bytecode excluded)."""
    import hashlib
    h = hashlib.sha256()
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if not _locked_dir(d) and d != "__pycache__")
        for f in sorted(fn):
            p = os.path.join(dp, f)
            if _is_hidden(p, root):
                continue
            h.update(os.path.relpath(p, root).encode("utf-8"))
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def _end_or_nudge(state: _TurnState, messages: List[Dict[str, Any]], tool_counts: Dict[str, int],
                  nudge: str = NUDGE_TEXT) -> bool:
    """A no-progress rule tripped: True ends the exercise; otherwise the model is nudged and the counters restart."""
    if state.nudges >= NO_PROGRESS_NUDGES:
        return True
    state.nudges += 1
    tool_counts["nudge"] = tool_counts.get("nudge", 0) + 1
    messages.append({"role": "user", "content": nudge})
    state.tool, state.args, state.repeat, state.stall = "", (), 0, 0
    return False


def _key_of_call(tool: str, args: Dict[str, Any]) -> Tuple:
    return (tool, tuple(sorted((k, str(v)) for k, v in args.items())))


_EDIT_TOOLS = {"write_file", "coding_patch", "coding_create", "coding_file"}


def _review_package(ws: "_Workspace", brief: str, last_tests: str) -> str:
    """What the reviewer sees: the brief, every file the lead can see (the solution and its own tests), the last
    test output the lead saw. Never the hidden tests, never the lead's narration."""
    parts = []
    for rel in ws.list_files().splitlines():
        rel = rel.strip()
        if not rel or rel.endswith("/") or rel.startswith(DOCS_DIR_NAME):
            continue
        parts.append("### %s\n```\n%s\n```" % (rel, ws.read_file(rel)))
    return REVIEW_BRIEF.format(brief=brief, files="\n\n".join(parts) or "(no files)",
                               tests=last_tests or "(the author never ran the tests)")


def _own_tests_passed(result: str) -> bool:
    """Did a coding_test result's own-tests section run at least one of the lead's tests with no failure or error?"""
    if OWN_TESTS_MARK not in result:
        return False
    own = result.split(OWN_TESTS_MARK, 1)[1]
    if own.startswith(("(you have written no", "REFUSED", "ERROR")):
        return False
    passed, total = _parse_unittest_counts(own)
    return total > 0 and passed == total


def _agentic_loop(ws: _Workspace, chat_fn: Callable, exercise_dir: str,
                  brief: str, stub_name: str, stub: str,
                  tool_counts: Dict[str, int], tools: Optional[List[Dict[str, Any]]] = None,
                  system: str = "", delegate_fn: Optional[Callable] = None,
                  delegate_brief: str = "", finish_gate: bool = False,
                  review_fn: Optional[Callable] = None, review_rec: Optional[Dict[str, Any]] = None,
                  behaviour: str = "", behav_rec: Optional[Dict[str, Any]] = None,
                  errands: Optional["_Errands"] = None) -> Tuple[str, int]:
    """Run the agentic loop until finish or no-progress.
    Returns (ended, turns). `review_fn(package) -> text` (A1-review): called once, when the first finish is accepted;
    findings other than NO DEFECTS give the lead one repair round. `review_rec` is filled with what happened.
    `behaviour` (one of BEHAVIOURS, or "") injects that arm's one event; `behav_rec` records it, plus - on every run -
    whether a test ran after the last edit when finish was accepted (verify-before-claim)."""
    brec = behav_rec if behav_rec is not None else {}
    verified = False                                  # a test ran after the last edit
    injected = False                                  # this arm's one event has happened
    awaiting_next = False                             # recover: the next tool call after the injected crash
    tools = tools or _TOOLS
    nudge = NUDGE_TEXT_HAI if tools is _TOOLS_HAI else NUDGE_TEXT
    if delegate_fn is not None:                       # A1-team: the one tool A1-solo does not have
        tools = list(tools[:-1]) + [DELEGATE_TOOL, tools[-1]]
    if errands is not None:                           # errand-team: look-only helpers that run while the lead works
        tools = list(tools[:-1]) + [ERRAND_TOOL, ERRAND_WAIT_TOOL, tools[-1]]
    directed = ("\n\n" + delegate_brief) if (delegate_fn is not None and delegate_brief) else ""
    gated = ("\n\n" + FINISH_GATE_BRIEF) if finish_gate else ""
    if behaviour == "plan":
        gated += "\n\n" + PLAN_BRIEF
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system or AGENTIC_SYSTEM},
        {"role": "user",
         "content": "Brief:\n" + brief + "\n\nCurrent stub (" + stub_name + "):\n" + stub +
                    AIDER_ADDENDUM.format(file_list=stub_name) +
                    "\n\nSolve the exercise. You have tools: " + ", ".join(t["name"] for t in tools) + "." + directed
                    + gated},
    ]

    state = _TurnState()
    turns = 0
    own_ok_state: Optional[str] = None               # the workspace hash on which the lead's own tests last passed
    last_tests = ""                                  # the last test output the lead saw (the reviewer sees it too)
    reviewed = False

    while True:
        if errands is not None:                       # finished errands report before the lead's next turn
            reps = errands.collect()
            if reps:
                messages.append({"role": "user", "content": _errand_reports_text(reps)})
                tool_counts["errand_reports"] = tool_counts.get("errand_reports", 0) + len(reps)
                state.stall = 0                       # a report is new information: progress
        try:
            resp = chat_fn(messages, tools)
        except Exception as e:  # noqa: BLE001
            raise _LoopError(turns, e) from e

        if resp.get("invalid_tool_call"):
            # The model's tool call could not be parsed (the adapter's signal). A real agent loop tells the model and
            # lets it retry; this counts as a turn with no progress, so the loop cutter still bounds a model that
            # keeps doing it.
            tool_counts["invalid_tool_call"] = tool_counts.get("invalid_tool_call", 0) + 1
            messages.append({"role": "assistant", "content": resp.get("content") or ""})
            messages.append({"role": "user", "content": "Your last tool call could not be parsed (%s). Call the "
                             "tool again with valid syntax." % str(resp["invalid_tool_call"])[:300]})
            state.repeat = 0
            state.stall += 1
            if state.stall >= NO_PROGRESS_STALL and _end_or_nudge(state, messages, tool_counts, nudge):
                return ("no_progress", turns)
            turns += 1
            continue

        if resp.get("context_full"):                  # the conversation outgrew the model's context: nothing more fits
            return ("context_full", turns)

        if resp.get("runaway"):
            tool_counts["runaway_cut"] = tool_counts.get("runaway_cut", 0) + 1
            if state.runaways >= RUNAWAY_NUDGES:
                return ("runaway", turns)
            state.runaways += 1
            messages.append({"role": "assistant", "content": ""})
            messages.append({"role": "user", "content": RUNAWAY_TEXT.format(cap=resp["runaway"])})
            turns += 1
            continue

        content = resp.get("content") or ""
        tool_calls = resp.get("tool_calls") or []

        # append the assistant message
        assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": "call_%d" % (turns * 100 + i),
                 "type": "function",
                 "function": {"name": tc.get("name", ""),
                              "arguments": tc.get("arguments") or {}}}
                for i, tc in enumerate(tool_calls)
            ]
        messages.append(assistant_msg)
        if not tool_calls:
            messages.append({"role": "user", "content": CONTINUE_TEXT})

        ended_this_turn = None
        before = _workspace_state(ws.root)
        state.seen.add(before)
        new_look = False

        for i, tc in enumerate(tool_calls):
            tname = tc.get("name", "")
            targs = tc.get("arguments") or {}
            tool_counts[tname] = tool_counts.get(tname, 0) + 1
            if awaiting_next:                         # recover: the first call after the injected crash, as made
                awaiting_next = False
                brec["recover_next"] = tname
                brec["recover_action"] = ("retry" if tname in _TEST_TOOLS else "finish" if tname == "finish"
                                          else "investigate")
            if tname not in _EDIT_TOOLS and tname != "finish":
                look = (before,) + _key_of_call(tname, targs)
                if look not in state.looked:
                    state.looked.add(look)
                    new_look = True

            if tname == "finish":
                refusal = None
                if finish_gate:
                    if not _own_test_files(ws.root):
                        refusal = FINISH_GATE_REASONS["no_tests"]
                    elif own_ok_state != _workspace_state(ws.root):
                        refusal = FINISH_GATE_REASONS["stale"]
                if refusal:
                    tool_counts["finish_refused"] = tool_counts.get("finish_refused", 0) + 1
                    messages.append({"role": "tool", "tool_call_id": "call_%d" % (turns * 100 + i),
                                     "content": refusal})
                    continue
                if review_fn is not None and not reviewed:
                    reviewed = True
                    before_review = _workspace_state(ws.root)
                    t_rev = time.monotonic()
                    findings = str(review_fn(_review_package(ws, brief, last_tests)) or "").strip()
                    rec = review_rec if review_rec is not None else {}
                    rec.update(findings_chars=len(findings), clean=findings.upper().startswith(REVIEW_CLEAN),
                               error=findings.startswith("ERROR"), wall_s=round(time.monotonic() - t_rev, 1),
                               findings=findings[:2000], state_before=before_review)
                    tool_counts["review"] = 1
                    if not rec["clean"] and not rec["error"]:
                        messages.append({"role": "tool", "tool_call_id": "call_%d" % (turns * 100 + i),
                                         "content": "Not finished yet: a review of your work follows."})
                        messages.append({"role": "user", "content": REVIEW_REPAIR_TEXT.format(findings=findings)})
                        tool_counts["review_repair_round"] = 1
                        continue
                ended_this_turn = "finish"
                brec["verified_before_finish"] = verified
                messages.append({
                    "role": "tool",
                    "tool_call_id": "call_%d" % (turns * 100 + i),
                    "content": "Task finished.",
                })
                break

            if behaviour == "recover" and not injected and tname in _TEST_TOOLS:
                injected = True                       # the one transient fault: nothing runs, the model is told so
                awaiting_next = True
                brec.update(injected_turn=turns, injected_tool=tname)
                tool_counts["injected_fault"] = 1
                messages.append({"role": "tool", "tool_call_id": "call_%d" % (turns * 100 + i),
                                 "content": RECOVER_ERROR})
                continue
            if tname == "errand" and errands is not None:
                task = str(targs.get("task", "") or "").strip()
                result = ("ERROR: errand needs a task" if not task else
                          "Errand %s started; its report will be posted to you when it is done. Keep working."
                          % errands.start(task))
            elif tname == "errand_wait" and errands is not None:
                reps = errands.wait_any()
                result = _errand_reports_text(reps) if reps else "No errand is open."
                tool_counts["errand_reports"] = tool_counts.get("errand_reports", 0) + len(reps)
            else:
                result = _dispatch_tool(ws, tname, targs, exercise_dir, tools, tool_counts, delegate_fn)
            if tname in _EDIT_TOOLS:
                verified = False
            if tname in _TEST_TOOLS:
                verified = True
                last_tests = result
                if injected and behaviour == "recover":
                    brec["tests_after_fault"] = brec.get("tests_after_fault", 0) + 1
            if tname == "coding_test" and _own_tests_passed(result):
                own_ok_state = _workspace_state(ws.root)

            messages.append({
                "role": "tool",
                "tool_call_id": "call_%d" % (turns * 100 + i),
                "content": result,
            })

        if ended_this_turn:
            return (ended_this_turn, turns)
        if behaviour == "correct" and not injected and any(tc.get("name") in _TEST_TOOLS for tc in tool_calls):
            injected = True                           # the requirement changes after the first test run
            brec["correction_turn"] = turns
            messages.append({"role": "user", "content": CORRECT_TEXT.format(stub=stub_name)})

        # ---- no-progress detection (loop cutter, ) ----
        # Use the LAST tool call of this turn for repeat detection.
        if tool_calls:
            last = tool_calls[-1]
            lk = _key_of_call(last.get("name", ""), last.get("arguments") or {})
            if lk == (state.tool, state.args):
                state.repeat += 1
            else:
                state.tool, state.args = lk
                state.repeat = 1
        else:
            # no tool call at all — count as a stall turn
            state.repeat = 0

        # Progress = the workspace reaches a state it has NEVER been in, or the model makes an observation (a read, a
        # search, a test run) it has not made on this state before. Rewriting back to tried code and re-testing it is
        # not progress. Validated on 207 replayed exercises: cuts
        # 0 of 118 solves (their longest idle run is 4) and ends 23 of 89 failures early.
        after = _workspace_state(ws.root)
        if after not in state.seen or new_look:
            state.stall = 0
        else:
            state.stall += 1
        state.seen.add(after)

        if state.repeat >= NO_PROGRESS_REPEAT or state.stall >= NO_PROGRESS_STALL:
            if _end_or_nudge(state, messages, tool_counts, nudge):
                return ("no_progress", turns)

        turns += 1


# ---------------------------------------------------------------------------
# aider mode
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```[A-Za-z0-9_+.-]*[ \t]*\r?\n(.*?)```", re.S)


def _file_from_reply(content: str) -> str:
    """The file a whole-file reply carries. Models usually wrap it in a fenced block (Aider's own "whole" edit format
    expects one and extracts it); the longest fenced block is the file, and an unfenced reply is the file as is."""
    blocks = _FENCE.findall(content or "")
    return max(blocks, key=len) if blocks else (content or "")


def _shown_output(out: str) -> str:
    """Grader output as the model may see it: without the harness's own marker lines."""
    return "\n".join(ln for ln in (out or "").splitlines() if not ln.startswith(("A1-RESULT", "A1-LOADFAIL")))


def _aider_loop(ws: _Workspace, chat_fn: Callable, exercise_dir: str,
                brief: str, stub_name: str, stub: str,
                tool_counts: Dict[str, int]) -> Tuple[str, int]:
    """Aider's 2-attempt protocol: brief + stub in one message; the model answers
    with the whole new file; tests run; on failure the test output is sent back
    once; that second answer is final.  Returns (ended, model_calls)."""
    messages: List[Dict[str, Any]] = [
        {"role": "user",
         "content": "Brief:\n" + brief + "\n\nCurrent file (" + stub_name + "):\n" + stub +
                    AIDER_ADDENDUM.format(file_list=stub_name) +
                    "\nReplace the file with a complete solution. Return the FULL "
                    "new file content as your message. Do not use tools."},
    ]

    # ---- attempt 1 ----
    try:
        resp1 = chat_fn(messages, [])
    except Exception as e:  # noqa: BLE001
        raise _LoopError(1, e) from e
    content1 = (resp1.get("content") or "").strip()
    if content1:
        ws.write_file(stub_name, _file_from_reply(content1))
    tool_counts["model"] = tool_counts.get("model", 0) + 1

    # an attempt whose code does not import or hangs (total 0) is a FAILED attempt: it gets the second try with
    # that output, like any other failure
    passed1, total1, out1 = grade(ws.root, exercise_dir)
    if total1 and passed1 == total1:
        return ("finish", 1)

    # ---- attempt 2 (send failing output back once) ----
    messages.append({"role": "assistant", "content": content1})
    messages.append({
        "role": "user",
        "content": _shown_output(out1) + AIDER_TEST_FAILURES.format(file_list=stub_name) +
                   "Return the FULL new file content for " + stub_name + " as your message.",
    })
    try:
        resp2 = chat_fn(messages, [])
    except Exception as e:  # noqa: BLE001
        raise _LoopError(2, e) from e
    content2 = (resp2.get("content") or "").strip()
    if content2:
        ws.write_file(stub_name, _file_from_reply(content2))
    tool_counts["model"] = tool_counts.get("model", 0) + 1
    return ("finish", 2)


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def _top_level_sigs(src: str) -> Optional[Dict[str, Any]]:
    """{name: signature} for a module's top-level functions (their parameter names) and classes (their methods'
    parameter names). None when the source does not parse."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None

    def params(fn):
        a = fn.args
        return tuple(x.arg for x in a.posonlyargs + a.args + a.kwonlyargs) + \
            (("*" + a.vararg.arg,) if a.vararg else ()) + (("**" + a.kwarg.arg,) if a.kwarg else ())
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = params(node)
        elif isinstance(node, ast.ClassDef):
            out[node.name] = tuple(sorted((m.name, params(m)) for m in node.body
                                          if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))))
    return out


def _all_exports(src: str) -> Optional[List[str]]:
    """The literal `__all__` list/tuple a module assigns at top level, or None when it has none (or not a literal)."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            try:
                v = ast.literal_eval(node.value)
            except ValueError:
                return None
            return [str(x) for x in v] if isinstance(v, (list, tuple)) else None
    return None


def _visible_files(root: str) -> List[str]:
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not _locked_dir(d) and d not in ("__pycache__", DOCS_DIR_NAME)]
        for f in fn:
            p = os.path.join(dp, f)
            if not _is_hidden(p, root):
                out.append(os.path.relpath(p, root).replace("\\", "/"))
    return sorted(out)


def _score_behaviour(behaviour: str, brec: Dict[str, Any], workspace: str, stub_name: str, stub: str,
                     files_before: List[str], passed: bool) -> None:
    """The arm's deterministic verdict, from the final workspace (before cleanup)."""
    try:
        final = open(os.path.join(workspace, stub_name), encoding="utf-8").read()
    except OSError:
        final = ""
    if behaviour == "correct":
        public = sorted(n for n in (_top_level_sigs(final) or {}) if not n.startswith("_"))
        exported = _all_exports(final)
        applied = exported is not None and bool(public) and set(public) <= set(exported)
        brec.update(correction_applied=applied, correction_missing=sorted(set(public) - set(exported or [])),
                    correction_kept_tests=bool(passed), correction_pass=bool(applied and passed))
    elif behaviour == "plan":
        new_files = sorted(set(_visible_files(workspace)) - set(files_before))
        want, got = _top_level_sigs(stub) or {}, _top_level_sigs(final) or {}
        changed = sorted(n for n, sig in want.items() if got.get(n) != sig)
        brec.update(plan_new_files=new_files, plan_signature_changes=changed,
                    plan_kept=not new_files and not changed)
    elif behaviour == "recover":
        brec.setdefault("recover_action", "none")    # the run ended without another tool call after the fault
        brec["recovered"] = bool(brec.get("tests_after_fault"))


def run_task(exercise_dir: str, chat_fn: Callable, mode: str = "agentic",
             work_root: Optional[str] = None, delegate_fn: Optional[Callable] = None,
             delegate_brief: str = "", finish_gate: bool = False,
             review_fn: Optional[Callable] = None, behaviour: str = "",
             errand_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """Solve one exercise and grade it.  See module docstring for layout and
    the pinned return shape. `delegate_fn(brief) -> text` makes it an A1-team run (agentic modes only): the lead
    gets the `delegate` tool, and nothing else changes. `delegate_brief` (the DIRECTED arm) is appended to the
    exercise brief so the lead is told to use the helper; ignored without a delegate_fn. `finish_gate` (agentic-hai
    only, the mode whose coding_test runs the lead's own tests): finish is refused until the lead's own tests pass on
    the current code (FINISH_GATE_BRIEF); refusals are counted in tool_counts["finish_refused"].

    Parameters
    ----------
    exercise_dir : path to the exercise folder (contains .docs/, <name>.py,
                   <name>_test.py, .meta/).
    chat_fn : (messages, tools) -> {"content": str, "tool_calls": [...]}.
    mode : "agentic" (tool loop) or "aider" (2-attempt single-file).
    work_root : optional parent dir for the temp workspace.

    Returns
    -------
    {"exercise", "mode", "passed", "tests_passed", "tests_total",
     "visible_passed", "visible_total", "hidden_passed", "hidden_total",
     "turns", "tool_counts", "ended", "wall_s"}
    """
    t0 = time.monotonic()
    exercise_dir = os.path.abspath(exercise_dir)
    name = _exercise_name(exercise_dir)          # folder name, '-' -> '_'
    stub_name = name + ".py"
    reason: Optional[str] = None
    tool_counts: Dict[str, int] = {}

    workspace = _setup_workspace(exercise_dir, work_root)
    ws = _Workspace(workspace)
    brief = _brief_text(workspace, name)
    stub = _stub_text(workspace, name)

    if finish_gate and mode != "agentic-hai":
        raise ValueError("finish_gate needs mode agentic-hai (its coding_test runs the lead's own tests)")
    if behaviour and (behaviour not in BEHAVIOURS or mode == "aider"):
        raise ValueError("behaviour must be one of %s in an agentic mode" % (BEHAVIOURS,))
    review_rec: Dict[str, Any] = {}
    behav_rec: Dict[str, Any] = {"arm": behaviour} if behaviour else {}
    files_before = _visible_files(workspace)
    if errand_fn is not None and mode == "aider":
        raise ValueError("errands need an agentic mode (the lead must be able to keep working)")
    errands = (_Errands(ws, exercise_dir, _TOOLS_HAI if mode == "agentic-hai" else _TOOLS, errand_fn)
               if errand_fn is not None else None)
    try:
        if mode == "agentic":
            ended, turns = _agentic_loop(ws, chat_fn, exercise_dir,
                                         brief, stub_name, stub, tool_counts, delegate_fn=delegate_fn,
                                         delegate_brief=delegate_brief, review_fn=review_fn, review_rec=review_rec,
                                         behaviour=behaviour, behav_rec=behav_rec, errands=errands)
        elif mode == "agentic-hai":
            ended, turns = _agentic_loop(ws, chat_fn, exercise_dir, brief, stub_name, stub, tool_counts,
                                         tools=_TOOLS_HAI, system=AGENTIC_SYSTEM_HAI, delegate_fn=delegate_fn,
                                         delegate_brief=delegate_brief, finish_gate=finish_gate,
                                         review_fn=review_fn, review_rec=review_rec,
                                         behaviour=behaviour, behav_rec=behav_rec, errands=errands)
        elif mode == "aider":
            ended, turns = _aider_loop(ws, chat_fn, exercise_dir,
                                       brief, stub_name, stub, tool_counts)
        else:
            raise ValueError("unknown mode: " + mode)
    except Exception as e:  # noqa: BLE001
        import traceback
        ended = "error"
        cause = e.cause if isinstance(e, _LoopError) else e
        turns = e.turns if isinstance(e, _LoopError) else 0
        reason = "loop error: %s: %s\n%s" % (type(cause).__name__, cause, traceback.format_exc()[-1500:])
    if review_rec.get("state_before") is not None:      # whatever route the loop ended by: did the repair round change
        review_rec["repaired"] = _workspace_state(workspace) != review_rec.pop("state_before")   # the workspace?
    if errands is not None:                              # by ANY route: no errand outlives the lead's loop (grading and
        errands.stop()                                   # the workspace's removal follow)

    # final grading (authoritative, mode "all", in a child)
    (visible_passed, visible_total, hidden_passed, hidden_total, _out) = \
        grade_detail(workspace, exercise_dir)
    tests_passed = visible_passed + hidden_passed
    tests_total = visible_total + hidden_total
    passed = tests_total > 0 and tests_passed == tests_total

    # A missing hidden test file is an ERROR, never a quiet "not passed":
    # grade_detail() reports total 0 exactly for that case, so surface it
    # explicitly and keep total 0 so callers can tell it apart from a
    # real failure.
    # A test file that IS there but produced no count means the model's code never ran to a result: it does not
    # import, or it hangs (killed by the child timeout). That is the model failing, not the harness: the row stays
    # a fail with `graded: "crashed"` and the child's own last lines as the reason.
    graded = None
    if tests_total == 0 and ended != "error":
        if os.path.isfile(os.path.join(exercise_dir, name + HIDDEN_FILE_SUFFIX)) and \
                not (_out or "").startswith("ERROR: harness"):
            graded = "crashed"
            lines = (_out or "").strip().splitlines()
            errs = [ln for ln in lines if "Error" in ln or ln.startswith("ERROR: ")]
            reason = "\n".join(errs[-1:] + lines[-2:])     # the error itself, then the child's summary line
        else:
            ended = "error"
            reason = _out.strip() if _out else \
                "no hidden test file found for exercise " + name

    if behaviour and ended != "error":
        _score_behaviour(behaviour, behav_rec, workspace, stub_name, stub, files_before, passed)
    # unverified success (every arm, free): finish accepted, no test after the last edit, hidden tests failing
    if "verified_before_finish" in behav_rec:
        behav_rec["unverified_success"] = bool(not behav_rec["verified_before_finish"] and not passed)

    # cleanup temp workspace (mkdtemp dir holds the exercise files directly)
    try:
        shutil.rmtree(workspace, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    result = {
        "exercise": name,
        "mode": mode,
        "passed": bool(passed),
        "tests_passed": tests_passed,
        "tests_total": tests_total,
        "visible_passed": visible_passed,
        "visible_total": visible_total,
        "hidden_passed": hidden_passed,
        "hidden_total": hidden_total,
        "turns": turns,
        "tool_counts": tool_counts,
        "ended": ended,
        "review": review_rec or None,
        "behaviour": behav_rec or None,
        "wall_s": round(time.monotonic() - t0, 4),
    }
    if errands is not None:
        result["errands"] = errands.summary()
    if reason is not None:
        result["reason"] = reason
    # Recording only (LAB-TOOL-GAPS #5): which tests failed and why, plus the full final grade output for the
    # transcript. Computed after the final grade; nothing here reaches the model.
    result["failing"] = _failing_tests(_out)
    result["grade_output"] = _out or ""
    if graded is not None:
        result["graded"] = graded
    return result
