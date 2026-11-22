"""SPEED PROBE for rigby's llama-server: the agent's slow turn, isolated - rewrite code that is already in the context.

Why this shape: the agent reuses its prompt cache, so each
turn reads only its new tokens; the time goes to WRITING at long context (1,154 tokens in 279 s, 3,387 in 1,493 s)
while it rewrites files it was shown. This payload reproduces that at two context depths, so a placement or a
speculation setting is priced on the turn it is meant to speed up. Rows carry gen_tps / prompt_tps / draft
acceptance from the server's own timings (tolkien_llm.complete); the grade checks the rewritten functions still
compute what the originals did, so a fast wrong answer is not a win.

The grader and the known-positive control are the f2 coding payload's (one grader, loaded from the sibling file).
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("_f2_payload", os.path.join(_HERE, "map_swarm_code_hard_f2_v2.py"))
_f2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_f2)

score = _f2.score
CONTROL_TASKS = _f2.CONTROL_TASKS
TEMPERATURE = 0.6
CONTEXT = 32768                 # rigby's A1 orders run at 32,768: the probe's KV must fit where the agent's does
SUFFIX = "\nReturn ONLY the requested functions as Python code in a single ```python code block. No explanation."
CHARS_PER_TOKEN = 3.3           # Qwen tokenizer on this generated code (it agrees with the engine's prompt_n)
REWRITE_N = 20                  # ~60 tokens each: a ~1,200-token rewrite, the size of the agent's long turns
_PROBE_INPUT = [3, 10, 17, 4, 24, 31, 8]


def _fn(i):
    a, b, c = 3 + i % 5, i % 7, 11 + i % 13
    return ("def fn_%d(xs):\n"
            "    total = %d\n"
            "    for i, x in enumerate(xs):\n"
            "        if x %% %d == %d:\n"
            "            total += x * %d\n"
            "        else:\n"
            "            total -= i + %d\n"
            "    return total\n" % (i, i, a, b % a, c, b))


def _module(target_tokens):
    parts, i = [], 0
    while sum(len(p) for p in parts) < target_tokens * CHARS_PER_TOKEN:
        parts.append(_fn(i))
        i += 1
    return parts


def _task(target_tokens):
    parts = _module(target_tokens)
    n = len(parts)
    names = ["fn_%d" % k for k in range(n - REWRITE_N, n)]
    ns = {}
    exec("\n".join(parts), ns)                                        # expected values from the originals
    checks = ["lambda: %s(%r) == %r" % (nm, _PROBE_INPUT, ns[nm](_PROBE_INPUT)) for nm in names[::4]]
    prompt = ("Below is a Python module.\n\n```python\n%s```\n\nRewrite the last %d functions (%s through %s): keep "
              "each body exactly as it is and add a one-line docstring under each def that says what it computes."
              % ("\n".join(parts), REWRITE_N, names[0], names[-1]))
    return {"kind": "speed_rewrite", "depth_tokens": target_tokens, "prompt": prompt, "checks": checks}


TASKS = {"rewrite_2k": _task(2000), "rewrite_12k": _task(12000)}
