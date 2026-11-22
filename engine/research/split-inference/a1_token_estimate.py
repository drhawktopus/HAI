"""How close is Inspect's token ESTIMATE (tiktoken o200k_base, what compaction uses on the GPU path where ollama reports
no cached count) to the REAL count for a Qwen model? Reads one tablet-head A1 Inspect log: per model call the true
input = usage.input_tokens + input_tokens_cache_read (llama-server's prompt_n + cache_n, which the lab provider
carries), against tiktoken over the same input messages + tool schemas. A threshold built on an estimate needs the
estimate's error measured.

    python - <glob of one order's inspect dir>        (inside tablet-foundry)"""
import asyncio
import glob
import sys

from inspect_ai.log import read_eval_log
from inspect_ai.model import get_model

# THE instrument compaction itself uses: Inspect's default Model.count_tokens / count_tool_tokens (the lab provider does
# not override them). A hand-rolled text join misses tool-call arguments (the code the model writes).
counter = get_model("mockllm/model")
files = sorted(glob.glob(sys.argv[1] + "/*.eval"))
assert files, "no .eval under %s" % sys.argv[1]
pairs = []
for f in files:
    log = read_eval_log(f, resolve_attachments=True)       # logs store big contents as attachment refs
    for s in log.samples or []:
        for e in s.events or []:
            if getattr(e, "event", "") != "model" or not e.output or not e.output.usage:
                continue
            u = e.output.usage
            true = (u.input_tokens or 0) + (u.input_tokens_cache_read or 0)
            est = asyncio.run(counter.count_tokens(e.input)) + asyncio.run(counter.count_tool_tokens(e.tools or []))
            pairs.append((true, est, bool(u.input_tokens_cache_read), len(e.tools or [])))
assert pairs, "no model calls with usage - wrong log"
with_cache = [p for p in pairs if p[2]]
print("calls %d (with a cached count %d)" % (len(pairs), len(with_cache)))
use = with_cache or pairs
ratios = sorted(e / t for t, e, _, _ in use if t)
print("estimate / true: min %.3f  median %.3f  max %.3f  (n %d)" % (ratios[0], ratios[len(ratios) // 2], ratios[-1],
                                                                     len(ratios)))
print("(Inspect's count already includes its own +10%% buffer)")
for t, e, c, nt in use[:6] + use[-4:]:
    print("  true %6d  est %6d  ratio %.3f  cached=%s tools=%d" % (t, e, e / t if t else 0, c, nt))
