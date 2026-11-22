"""B8: the post-run FETCH audit, on Sundae (where the pinned dataset lives). Terminal-Bench's tasks are public, tests and
reference solutions included, so a helper that fetched a page holding its task's own key saw the answer: that trial is
VOID, never scored ([[34-Phase-7-Placement-Policy-Design]] § "Two shapes": "every fetched page is also checked at grading
against that exercise's hidden tests and a row that fetched them is VOID").

    python3 fetch_audit.py <run dir> <dataset dir>      -> <run dir>/fetch_audit.json

A key line counts when it is distinctive (>= 30 characters once stripped) and comes from the task's `tests/` or
`solution/`; a trial LEAKS when any key line appears in any text its helpers fetched. A trial whose key has no such
line cannot be audited: that is an ERROR, never "clean". Every trial with an errands.json is listed (0 fetches too), so
an empty audit is distinguishable from a broken one.
"""
import glob
import json
import os
import sys

MIN_LINE = 30
KEY_DIRS = ("tests", "solution")


def key_lines(dataset, task):
    lines = set()
    for sub in KEY_DIRS:
        for p in glob.glob(os.path.join(dataset, task, sub, "**", "*"), recursive=True):
            if not os.path.isfile(p):
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            lines.update(l.strip() for l in text.splitlines() if len(l.strip()) >= MIN_LINE)
    return lines


def audit(run_dir, dataset):
    out = {}
    for f in sorted(glob.glob(os.path.join(run_dir, "jobs", "*", "*__*", "agent", "errands.json"))):
        trial = os.path.basename(os.path.dirname(os.path.dirname(f)))
        task = trial.split("__")[0]
        try:
            with open(f, encoding="utf-8") as fh:
                recs = json.load(fh)
        except (OSError, ValueError) as e:
            out[trial] = {"task": task, "error": "errands.json unreadable: %r" % (e,)}
            continue
        fetched = [x for r in recs for x in (r.get("fetches") or [])]
        entry = {"task": task, "fetches": len(fetched), "leak": False}
        if fetched:
            lines = key_lines(dataset, task)
            entry["key_lines"] = len(lines)
            if not lines:
                entry["error"] = "no distinctive key line for %s - the fetch cannot be audited (not clean)" % task
            hits = sorted(l for l in lines if any(l in (x.get("text") or "") for x in fetched))
            if hits:
                entry.update(leak=True, matched=hits[:3], urls=sorted({x.get("url") for x in fetched}))
        out[trial] = entry
    return out


def main(argv):
    run_dir, dataset = argv[0], argv[1]
    res = audit(run_dir, dataset)
    with open(os.path.join(run_dir, "fetch_audit.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print("FETCH AUDIT %d trial(s), %d with fetches, %d leaked, %d unauditable" % (
        len(res), sum(1 for e in res.values() if e.get("fetches")), sum(1 for e in res.values() if e.get("leak")),
        sum(1 for e in res.values() if e.get("error"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
