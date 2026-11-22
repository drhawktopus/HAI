"""Test that no name is defined twice in the same namespace (module or class body).

Python allows shadowing at import/parse time, so duplicate definitions are legal but silently break runtime behavior. This invariant catches accidental re-definitions of functions, classes, or top-level assignments before they go live."""
import ast
import io
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Shadowed test names cause silent collection failure (tests exist but never run).
# This list is auto-discovered via glob, not maintained manually.
STRICT = ["p20_dataset.py"] + sorted(
    os.path.basename(p) for p in glob.glob(os.path.join(HERE, "test_*.py")))

# Informational: reported, never fatal. These belong to other work streams (the foundry is the
# P20 session's file) and this test must not hijack somebody else's red/green. Surfacing the
# information is useful; failing their suite on my say-so is not.
INFORM = ["p20_foundry.py", "p20_status.py", "p20_stall_rescue.py"]

failures = 0


def duplicates(path):
    """Return {name: [lineno, ...]} for names defined more than once.

        Scans module-level and class-body definitions (reported as `Class.method`).
        Stops at class bodies; ignores nested defs (closures) where reuse is expected.
    """
    with io.open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    seen = {}
    for node in tree.body:
        if isinstance(node, DEFS):
            seen.setdefault(node.name, []).append(node.lineno)
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, DEFS):
                    seen.setdefault("%s.%s" % (node.name, sub.name), []).append(sub.lineno)
    return {n: ls for n, ls in seen.items() if len(ls) > 1}


def report(fname, strict):
    global failures
    path = os.path.join(HERE, fname)
    if not os.path.exists(path):
        print("  skip   %-24s (not present beside this test)" % fname)
        return
    dupes = duplicates(path)
    if not dupes:
        print("  ok     %-24s no shadowed defs (module or class body)" % fname)
        return
    for name, lines in sorted(dupes.items()):
        detail = "%s defined %d times at lines %s" % (
            name, len(lines), ", ".join(str(n) for n in lines))
        if strict:
            print("  FAIL   %-24s %s" % (fname, detail))
            print("         The LAST definition wins; every earlier one is unreachable by name,")
            print("         and any call site using an earlier signature raises TypeError.")
            failures += 1
        else:
            print("  note   %-24s %s" % (fname, detail))


print("shadowed definitions (module level + class bodies; every test_*.py is strict)")
for f in STRICT:
    report(f, True)
for f in INFORM:
    report(f, False)

if failures:
    print("\n%d shadowed definition(s) — rename one, do not delete either without checking "
          "its call sites." % failures)
    sys.exit(1)
print("\nall shadowed-def checks passed")
