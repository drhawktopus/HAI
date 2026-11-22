"""Hard Bench — the LEAD WRITES THE CHECKS for W1's 8 CODE tasks (the #34 Phase 7 cascade arm's selector;
[[34-Phase-7-Placement-Policy-Design]] § "The cascade arm"). Same contract as hard_tasks_f2_tests.py (S10b): the prompt
shows the W1 task's spec and asks for the checks a correct implementation must pass, as a list of "lambda: <expr>"
strings, so `gen_hard_payload.py --pool w1tests` builds the payload like every other pool.

  TASKS[name]     = {"kind": "tests", "prompt", "checks": [SHAPE_CHECK]} - the ONE on-box check is the SHAPE check
                    (TESTS is a list of >= 3 lambda strings that compile). Whether the checks are any GOOD is graded
                    off-box by cascade_select.py through the W1 payload's own score(): accept W1 REFERENCE, reject W1
                    NEGATIVE, and select among the tiers' existing candidates (false pass / false fail vs the hidden checks).
  REFERENCE[name] = the hidden checks themselves as a TESTS block (positive control for the shape check).
  ORIGINAL[name]  = W1's REFERENCE code: a lead that SOLVES the task instead of writing checks fails the shape check
                    (the negative control).
"""
import re

import hard_tasks_f2_tests as F2T
import hard_tasks_w1 as W1

SHAPE_CHECK = F2T.SHAPE_CHECK
parse_tests = F2T.parse_tests
CODE_TASKS = sorted(t for t in W1.TASKS if t.startswith("code_"))

INSTRUCTION = (
    "\n\nYOUR JOB IS NOT TO WRITE OR FIX THIS CODE. Write the CHECKS that a correct implementation of {names} must "
    "pass, so that a wrong attempt is rejected. Return ONLY one ```python code block containing exactly one "
    "assignment:\n\nTESTS = [\n    \"lambda: ...\",\n    \"lambda: ...\",\n    \"lambda: ...\",\n]\n\n"
    "a list of at least 3 strings; each string is a Python lambda with no arguments that uses {names} and evaluates to "
    "True on a correct implementation. Cover the normal case, the edge cases and every behaviour the description asks "
    "for. A check may build objects and call several methods inside one expression. No implementation, no explanation, "
    "no example usage, nothing outside the list.")


def public_names(code):
    """The top-level functions and classes a reference solution defines (the names the checks must use)."""
    names = set(re.findall(r"^(?:def|class) ([A-Za-z]\w*)", code, re.M))
    assert names, "no public top-level name in the reference"
    return sorted(names)


def _spec(prompt):
    """The W1 prompt without its answer-format sentence (the check prompt carries its own format)."""
    cut = prompt.find("Use exactly one fenced code block")
    return prompt[:cut].rstrip() if cut > 0 else prompt


TASKS = {name: {"kind": "tests",
                "prompt": _spec(W1.TASKS[name]["prompt"]) + INSTRUCTION.format(
                    names=", ".join("`%s`" % n for n in public_names(W1.REFERENCE[name]))),
                "checks": [SHAPE_CHECK]}
         for name in CODE_TASKS}
ORIGINAL = {name: W1.REFERENCE[name] for name in CODE_TASKS}
REFERENCE = {name: "TESTS = [\n" + "".join("    %r,\n" % c for c in W1.TASKS[name]["checks"]) + "]\n"
             for name in CODE_TASKS}


def selftest():
    """[] when sound: 8 tasks; every prompt asks for checks and names its symbols; the hidden checks parse as TESTS and
    pass the shape check's own rules; the W1 REFERENCE code is NOT a TESTS answer (the negative control)."""
    bad = []
    if len(TASKS) != 8:
        bad.append(("*", "expected 8 W1 code tasks, got %d" % len(TASKS)))
    for name in CODE_TASKS:
        p = TASKS[name]["prompt"]
        if "YOUR JOB IS NOT TO WRITE" not in p or "Use exactly one fenced code block" in p:
            bad.append((name, "prompt is not a check-writing prompt"))
        tests = parse_tests(REFERENCE[name].replace("TESTS = ", "TESTS = ", 1))
        if not tests or len(tests) < 3 or not all(t.lstrip().startswith("lambda") for t in tests):
            bad.append((name, "hidden checks do not form a valid TESTS block"))
        if parse_tests(ORIGINAL[name]) is not None:
            bad.append((name, "the reference CODE parses as TESTS - the negative control is broken"))
    return bad


if __name__ == "__main__":
    print(selftest() or "selftest OK: %d tasks" % len(TASKS))
