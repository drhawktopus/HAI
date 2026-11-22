r"""How much of each A1 prompt does llama-server actually RE-READ on rigby (prefix-cache reuse), on the standard order
(ub 2048)? Per model call: re-read = usage.input_tokens (llama-server's prompt_n - tokens processed this call); full =
Inspect's own count of the input (+ tools), the instrument measured at median 0.99 of true (a1_token_estimate.py).
Ideal reuse re-reads only what was appended since the previous call. The arsenal's open question (r39 reused 1,929
cached tokens, r40 at ub 2048 only 397 on the same prompts): does A1 pay a whole-prompt re-read each turn?

    python - <glob of one order's inspect dir>        (inside tablet-foundry)
"""
import asyncio
import glob
import sys

from inspect_ai.log import read_eval_log
from inspect_ai.model import get_model

counter = get_model("mockllm/model")
files = sorted(glob.glob(sys.argv[1] + "/*.eval"))
assert files, "no .eval under %s" % sys.argv[1]
rows = []
for f in files:
    log = read_eval_log(f, resolve_attachments=True)
    for s in log.samples or []:
        prev_full = None
        for e in s.events or []:
            if getattr(e, "event", "") != "model" or not e.output or not e.output.usage:
                continue
            reread = e.output.usage.input_tokens or 0
            full = asyncio.run(counter.count_tokens(e.input)) + asyncio.run(counter.count_tool_tokens(e.tools or []))
            appended = full - prev_full if prev_full is not None else full
            rows.append((s.id, full, appended, reread))
            prev_full = full
assert rows, "no model calls"
later = [r for r in rows if r[2] != r[1]]          # every call after a sample's first
print("calls %d (after each sample's first: %d)" % (len(rows), len(later)))
share = sorted(r[3] / r[1] for r in later if r[1])
print("re-read / full prompt: min %.3f  median %.3f  max %.3f" % (share[0], share[len(share) // 2], share[-1]))
waste = sorted((r[3] - max(r[2], 0)) for r in later)
print("re-read beyond what was appended (tokens): median %d  p90 %d  max %d" % (waste[len(waste) // 2],
                                                                               waste[int(0.9 * len(waste))], waste[-1]))
print("total re-read %d of %d prompt tokens sent (%.1f %%)" % (sum(r[3] for r in rows), sum(r[1] for r in rows),
                                                              100.0 * sum(r[3] for r in rows) / sum(r[1] for r in rows)))
for r in later[:5] + later[-5:]:
    print("  %-10s full %6d  appended %6d  re-read %6d" % r)
