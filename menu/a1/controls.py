"""Positive + negative control for the A1 grader on the REAL task set (34 Python exercises, Aider polyglot): for each
exercise, the reference solution (.meta/example.py as <name>.py) must pass ALL hidden tests, and the untouched stub must
NOT. Any exercise that fails either control is listed - a grader that cannot separate the two measures nothing.
Usage: controls.py [exercises_root]"""
import glob
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a1_harness as H  # noqa: E402

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                          "polyglot-benchmark", "python", "exercises", "practice")


def workspace(ex, use_example):
    name = os.path.basename(ex).replace("-", "_")
    d = tempfile.mkdtemp(prefix="a1ctl-")
    for f in os.listdir(ex):
        p = os.path.join(ex, f)
        if os.path.isfile(p) and not f.endswith("_test.py"):
            shutil.copy2(p, d)
    if use_example:
        shutil.copy2(os.path.join(ex, ".meta", "example.py"), os.path.join(d, name + ".py"))
    return d


exs = sorted(glob.glob(os.path.join(ROOT, "*")))
assert exs, "no exercises found under %s - the control measured nothing" % ROOT
bad_pos, bad_neg = [], []
for ex in exs:
    for use_example, bucket in ((True, bad_pos), (False, bad_neg)):
        d = workspace(ex, use_example)
        try:
            p, t, out = H.grade(d, ex)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        # a stub must be GRADED and fail - a bare 0/0 means nothing was graded, which is a broken control; 0/0 with
        # the child's A1-LOADFAIL marker means the tests ran and could not import the stub, which IS a failure
        ok = (t > 0 and p == t) if use_example else ((t > 0 and p < t) or "A1-LOADFAIL" in out)
        if not ok:
            bucket.append((os.path.basename(ex), p, t, out[-300:].replace("\n", " | ")))
print("exercises %d | reference passes all: %d/%d | stub fails: %d/%d" % (
    len(exs), len(exs) - len(bad_pos), len(exs), len(exs) - len(bad_neg), len(exs)))
for name, p, t, tail in bad_pos:
    print("  REFERENCE DID NOT PASS: %s %d/%d  %s" % (name, p, t, tail))
for name, p, t, tail in bad_neg:
    print("  STUB DID NOT FAIL: %s %d/%d" % (name, p, t))
