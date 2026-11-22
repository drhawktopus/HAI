r"""Per-call output length of every GPU A1 model call, per model - the evidence a GPU `turn_cap` must come from (doctrine
13a), the same question a1_turn_lengths.py answered for rigby. Reads every delivered A1 order's transcripts (turns carry
eval_count = output incl. thinking, done_reason, tool_calls). Prints per model: acting calls (tool call) vs text-only
calls, percentiles, the longest acting call, every no-tool call cut by length (runaway candidates).
    python a1_turn_lengths_gpu.py
"""
import glob
import json
import os
from collections import defaultdict

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))   # the engine folder
import engine_settings as E  # noqa: E402
ROOT = E.foundry_mirror("workspace", "a1", "delivered")
seen, acted, texted, runs = set(), defaultdict(list), defaultdict(list), defaultdict(list)
files = sorted(glob.glob(os.path.join(ROOT, "*", "results.json")))
assert files, "no delivered GPU A1 results"
for f in files:
    r = json.load(open(f, encoding="utf-8"))
    model, think = r.get("model"), r.get("think")
    for t in glob.glob(os.path.join(os.path.dirname(f), "transcripts", "*.json")):
        if t.endswith(".partial.json"):
            continue
        d = json.load(open(t, encoding="utf-8"))
        for i, turn in enumerate(d.get("turns") or []):
            if not turn or turn.get("replayed") or turn.get("eval_count") is None:
                continue
            key = (os.path.basename(os.path.dirname(f)).split(".")[0], os.path.basename(t), i)
            if key in seen:
                continue
            seen.add(key)
            m = "%s think=%s" % (model, think)
            n = int(turn["eval_count"])
            (acted if turn.get("tool_calls") else texted)[m].append(n)
            if not turn.get("tool_calls") and turn.get("done_reason") == "length":
                runs[m].append(n)


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p / 100.0 * len(v)))] if v else 0


print("calls read: %d" % len(seen))
for m in sorted(set(acted) | set(texted), key=lambda k: -len(acted[k])):
    a, x = acted[m], texted[m]
    if len(a) < 50:
        continue
    print("%-48s acting n=%5d p50 %6d p99 %6d p99.9 %6d max %6d | text-only n=%4d max %6d | no-tool length cuts %d %s"
          % (m[:48], len(a), pct(a, 50), pct(a, 99), pct(a, 99.9), max(a), len(x), max(x) if x else 0,
             len(runs[m]), sorted(runs[m])[:6]))
