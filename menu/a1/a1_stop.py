"""A1 EARLY STOP: end an arm once it can no longer come within run-to-run noise of its baseline on solves.

Rule (plan field `stop_rule` = {"baseline": <order>, "baseline_dir": <delivered dir>, "margin": 2,
"exclude": [...non-discriminating exercises]}): after each exercise, if
    arm_solved_so_far + exercises_still_to_run < baseline_solved - margin
then even a perfect finish leaves the arm clearly worse, so it stops. Both sides count only the plan's exercises
minus `exclude`. `margin` is MEASURED, not chosen: four runs of the 3.8 on this same 10-exercise set scored 5, 6, 7, 6
(34-A1 spec, "the lead's run-to-run variance"), spread 2.

Never stops an arm for being clearly BETTER (the crown and Phase 7 want the full magnitude and cost of a win), never
without a delivered baseline (rule off, logged), and is not used for behaviour arms or replicates (their measure is
not solves vs a baseline / their point is a full fresh run - enforced at submit)."""
import json
import os

MARGIN = 2
MARGIN_SOURCE = "34-A1 spec: four 3.8 runs on the spot set scored 5, 6, 7, 6 (spread 2)"


def make_rule(baseline, unc, exclude):
    """The plan's `stop_rule` against one baseline order, or several (a list: their rows are pooled - e.g. the 3.8's
    spot-set run + its hard-7 run as ONE baseline for a challenger run on both sets), each delivered under
    `unc\\delivered` (one definition: submit_a1 and the queued-plan amender both call this)."""
    names = baseline if isinstance(baseline, (list, tuple)) else [baseline]
    return {"baseline": ",".join(names), "baseline_dir": [unc + "\\delivered\\" + n for n in names], "margin": MARGIN,
            "exclude": sorted(exclude), "margin_source": MARGIN_SOURCE}


def baseline_solved(baseline_dir, exercises, exclude):
    """Solves of the delivered baseline(s) on the scored exercises, or None when any has not delivered (rule off).
    `baseline_dir` is one dir or a list; a later baseline's row for the same exercise overrides an earlier one."""
    rows = []
    for d in (baseline_dir if isinstance(baseline_dir, (list, tuple)) else [baseline_dir]):
        try:
            with open(os.path.join(d, "results.json"), encoding="utf-8") as f:
                rows += json.load(f).get("rows") or []
        except (OSError, ValueError):
            return None
    scored = set(exercises) - set(exclude or ())
    got = {r.get("exercise_dir"): bool(r.get("passed")) for r in rows}
    if not scored <= set(got):
        return None                      # a baseline missing scored exercises cannot decide anything
    return sum(1 for e in scored if got[e])


def decide(rows, exercises, rule, base=None):
    """None to continue, else the stop record. `rows` = {exercise: row} answered so far; `base` = baseline solves
    (looked up from the rule when None)."""
    exclude = set(rule.get("exclude") or ())
    scored = [e for e in exercises if e not in exclude]
    if base is None:
        base = baseline_solved(rule["baseline_dir"], exercises, exclude)
    if base is None:
        return None
    done = [e for e in scored if e in rows]
    solved = sum(1 for e in done if rows[e].get("passed"))
    remaining = len(scored) - len(done)
    best_case = solved + remaining
    margin = int(rule.get("margin", 2))
    if best_case < base - margin:
        return {"why": "clearly worse than its baseline: even solving all %d remaining gives %d < %d - %d" % (
                    remaining, best_case, base, margin),
                "solved": solved, "answered": len(done), "remaining": remaining, "best_case": best_case,
                "baseline": rule.get("baseline"), "baseline_solved": base, "margin": margin,
                "not_run": [e for e in exercises if e not in rows]}
    return None
