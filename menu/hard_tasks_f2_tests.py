"""Hard Bench S10b — the LEAD WRITES THE CHECKS, not the answer (34-S10-Rigby-Lead-Fellowship-Design § S10b: "the
lead's first job is to supply what the tablets cannot: the checks"). The same 24 family-2 tasks as hard_tasks_f2.py;
the prompt shows the task exactly as family 2 does (description + ORIGINAL code) and then asks for the checks a
correct implementation must pass, in the lab's own check contract: a list of "lambda: <expr>" strings.

Contract, so `gen_hard_payload.py --pool f2tests` builds the payload like every other pool:
  TASKS[name] = {"kind": "tests", "prompt", "checks"} - the ONE on-box check is a SHAPE check: the answer's code block
      assigns TESTS, a list of >= 3 lambda strings that compile. Whether the checks are any GOOD is graded off-box by
      s10b_select.py through the family-2 payload's own score(): accept hard_tasks_f2.REFERENCE, reject its ORIGINAL,
      and select among the tablets' candidates (false pass / false fail against the hidden checks).
  REFERENCE[name] = the hidden checks themselves as a TESTS block (positive control: passes the shape check, and the
      family's own selftest already proves it accepts REFERENCE and rejects ORIGINAL).
  ORIGINAL[name] = hard_tasks_f2.ORIGINAL (a FUNCTION: the lead that solved the task instead of writing checks fails
      the shape check - the negative control, shown verbatim in the prompt as family 2 does).
"""
import ast

import hard_tasks_f2 as F2

# One check string, graded by the payload's score() in a child: the code block defined TESTS, a list of at least three
# strings that each evaluate (not call) to a callable - a lambda. A function answer has no TESTS and fails it.
SHAPE_CHECK = ("lambda: isinstance(TESTS, list) and len(TESTS) >= 3 and all(isinstance(t, str) and "
               "t.lstrip().startswith('lambda') and callable(eval(t)) for t in TESTS)")

INSTRUCTION = (
    "\n\nYOUR JOB IS NOT TO WRITE OR FIX THIS FUNCTION. Write the CHECKS that a correct `{f}` must pass, so that a "
    "wrong attempt is rejected. Return ONLY one ```python code block containing exactly one assignment:\n\n"
    "TESTS = [\n    \"lambda: {f}(...) == ...\",\n    \"lambda: {f}(...) == ...\",\n    \"lambda: {f}(...) == ...\",\n]\n\n"
    "a list of at least 3 strings; each string is a Python lambda with no arguments that calls `{f}` and evaluates to "
    "True on a correct implementation. Cover the normal case, the edge cases (empty input, one element, the boundary "
    "the description names) and every behaviour the description asks for. No function definition, no explanation, "
    "no example usage, nothing outside the list.")


def function_name(task):
    """The one function family 2's ORIGINAL defines for this task (the name the checks must call)."""
    names = sorted(F2._defined_names(F2.ORIGINAL[task]))
    assert len(names) == 1, (task, names)
    return names[0]


TASKS = {name: {"kind": "tests", "prompt": t["prompt"] + INSTRUCTION.format(f=function_name(name)),
                "checks": [SHAPE_CHECK]}
         for name, t in F2.TASKS.items()}
ORIGINAL = dict(F2.ORIGINAL)
REFERENCE = {name: "TESTS = [\n" + "".join("    %r,\n" % c for c in t["checks"]) + "]\n"
             for name, t in F2.TASKS.items()}


def parse_tests(code):
    """The TESTS list from an answer's code block, read by ast (model text is never exec'd here): a list of strings,
    or None when there is no such assignment or it is not a literal list of strings."""
    try:
        tree = ast.parse(code or "")
    except (SyntaxError, ValueError):
        return None
    found = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TESTS" for t in node.targets):
            try:
                found = ast.literal_eval(node.value)
            except (ValueError, SyntaxError, TypeError):
                return None
    if not isinstance(found, list) or not found or not all(isinstance(t, str) for t in found):
        return None
    return found


def _accepts(code, tests):
    """In-process verdict for THIS module's selftest only: every test True on `code`. The reducer grades through the
    payload's child-process score(), the one grader every hard-bench row went through."""
    ns = {}
    try:
        exec(code, ns)
    except Exception:                                                # noqa: BLE001
        return False
    for t in tests:
        try:
            if eval(t, ns)() is not True:
                return False
        except Exception:                                            # noqa: BLE001
            return False
    return True


def selftest():
    """(task, reason) failures; empty = the instrument is sound. The shape check is True on every REFERENCE and
    False on every ORIGINAL (a function answer); parse_tests round-trips the reference; the reference suite
    accepts family 2's REFERENCE and rejects its ORIGINAL (a selector that cannot tell right from wrong is a hole)."""
    bad = []
    if set(TASKS) != set(F2.TASKS) or set(REFERENCE) != set(TASKS) or set(ORIGINAL) != set(TASKS):
        bad.append(("<all>", "task sets differ from hard_tasks_f2"))
    for name, t in TASKS.items():
        if ORIGINAL[name] not in t["prompt"]:
            bad.append((name, "ORIGINAL text does not appear verbatim in the prompt"))
        if function_name(name) not in t["prompt"].rsplit("YOUR JOB", 1)[-1]:
            bad.append((name, "the instruction does not name the function"))
        ref_ns = {}
        exec(REFERENCE[name], ref_ns)                                 # our own text, not a model's
        for chk in t["checks"]:
            if eval(chk, ref_ns)() is not True:
                bad.append((name, "shape check is not True on REFERENCE"))
        orig_ns = {}
        exec(ORIGINAL[name], orig_ns)
        for chk in t["checks"]:
            try:
                ok = eval(chk, orig_ns)() is True
            except Exception:                                        # noqa: BLE001
                ok = False
            if ok:
                bad.append((name, "a function answer passes the shape check"))
        tests = parse_tests(REFERENCE[name])
        if tests != list(F2.TASKS[name]["checks"]):
            bad.append((name, "parse_tests does not round-trip the reference TESTS"))
            continue
        if not _accepts(F2.REFERENCE[name], tests):
            bad.append((name, "reference TESTS reject hard_tasks_f2.REFERENCE"))
        if _accepts(F2.ORIGINAL[name], tests):
            bad.append((name, "reference TESTS accept hard_tasks_f2.ORIGINAL"))
    return bad


if __name__ == "__main__":
    failures = selftest()
    print("tasks: %d | failures: %d" % (len(TASKS), len(failures)))
    for name, reason in failures:
        print("  FAIL", name, "-", reason)
    raise SystemExit(1 if failures else 0)
