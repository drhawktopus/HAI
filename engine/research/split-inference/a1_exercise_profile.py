r"""Where did one A1 exercise's time go? Per model call: output tokens, input (read + cached), stop reason, the call's
wall, write speed; compaction events; the harness row. Reads the eval logs of one order dir for one task.
    python - <order inspect dir> <task>          (inside tablet-foundry)
"""
import glob
import sys

from inspect_ai.log import read_eval_log

d, task = sys.argv[1], sys.argv[2]
files = [f for f in sorted(glob.glob(d + "/*.eval"))]
assert files, "no .eval under %s" % d
found = 0
for f in files:
    log = read_eval_log(f, resolve_attachments=True)
    for s in log.samples or []:
        if str(s.id) != task:
            continue
        found += 1
        row = (s.metadata or {}).get("a1_row") or {}
        print("== %s  log %s" % (task, f.split("/")[-1]))
        print("row: ended %s turns %s tests %s/%s wall %s tool_counts %s compactions %s" % (
            row.get("ended"), row.get("turns"), row.get("tests_passed"), row.get("tests_total"), row.get("wall_s"),
            row.get("tool_counts"), row.get("compactions")))
        calls, comp = [], 0
        for e in s.events or []:
            ev = getattr(e, "event", "")
            if ev == "compaction":
                comp += 1
                print("  COMPACTION %s -> %s tokens (%s)" % (getattr(e, "tokens_before", "?"),
                                                            getattr(e, "tokens_after", "?"),
                                                            (getattr(e, "metadata", {}) or {}).get("trigger")))
            if ev != "model" or not e.output:
                continue
            u = e.output.usage
            out = u.output_tokens if u else 0
            inp = ((u.input_tokens or 0) + (u.input_tokens_cache_read or 0)) if u else 0
            wall = getattr(e, "working_time", None) or 0
            calls.append((out, inp, e.output.stop_reason, round(wall, 1)))
        tot_out = sum(c[0] for c in calls)
        tot_wall = sum(c[3] for c in calls)
        print("model calls %d, compaction events %d, output tokens %d, model wall %.0f s (%.2f tok/s overall)" % (
            len(calls), comp, tot_out, tot_wall, tot_out / tot_wall if tot_wall else 0))
        big = sorted(calls, key=lambda c: -c[3])[:8]
        print("slowest calls (out, input, stop, wall_s):", big)
        print("input size first / max / last:", calls[0][1] if calls else None, max(c[1] for c in calls) if calls else None,
              calls[-1][1] if calls else None)
assert found, "task %r not in %s" % (task, d)
