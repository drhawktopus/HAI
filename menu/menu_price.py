"""Project 34 price line - the pure pieces: the reference a ceiling is built from, and how a row is classed.

Rule ([[34-Hard-Bench-Order-Spec]] "Price ceilings"): an answer is priced out once it has cost PRICE_X times the
cheapest measured solve of that task family on the SAME box. Everything here is per box: one box's rows never set
another box's reference.

The cheapest solve is a COLD solve. A row whose prompt came out of the server's prompt cache read almost none of its
prompt, so its wall time prices a cache hit, not the task. A row is warm when the prompt tokens it actually processed are
under WARM_FRACTION of the task's cold size - the largest prompt read for that (box, family, task). Measured on rigby's
260 delivered result files (13,819 rows with a count): the ratio to cold size is bimodal - 10,377 rows under 0.25
(prompt_tokens 1 or 4, or a shared prefix) and 3,442 rows at 0.65 or more, none between - so 0.5 sits in the empty gap.
ollama's GPU rows report the whole prompt on every row (7,675 rows: every ratio is >= 0.9 except 144 vision rows whose
group mixed 1x and 2x images), so no GPU row reads warm and the rule changes nothing there; it is applied anyway so
the day ollama reports a cache hit the reference does not move.
A solved row whose prompt count is missing is left out and counted: "could not tell" is not "cold".

row_status() is the ONE classifier for a delivered row: priced_out (a stated cost measurement), void (it hit the context
limit: it measured the cap, ) or answered. The reducers, the menu build and the tests all call it."""
import collections
import glob
import os
import re

WARM_FRACTION = 0.5

_GENERATION = re.compile(r"\.\d{8}-\d{6}$")


def split_acknowledged(names, ack):
    """(unacknowledged, {name: why}) for the orders the desk calls done but the menu found no result for. `ack` maps an
    order-name PREFIX to the reason a person acknowledged the gap (menu_unread_ack.json), so a known gap is stated once and
    only a NEW one is flagged - a line that is always red trains the reader to skip it."""
    open_, known = [], {}
    for n in sorted(set(names)):
        why = next((w for pre, w in sorted(ack.items(), key=lambda kv: -len(kv[0])) if n.startswith(pre)), None)
        if why is None:
            open_.append(n)
        else:
            known[n] = why
    return open_, known


def delivered_out(out):
    """The order's delivered result: the NEWEST generation of `out` that exists, or None. A factory that delivers an
    order twice (a deploy's pause + requeue, a re-run) keeps one name per artifact: the second delivery lands in
    `<order dir>.<YYYYmmdd-HHMMSS>\\out.json` beside the first, and submission.json still names the first. Reading only
    that path dropped every requeued cell from the menu. Readers pick the newest explicitly; a generation without out.json (stopped mid-run) is skipped."""
    if not out:
        return None
    d, name = os.path.split(out)
    gens = [p for p in [out] + glob.glob(glob.escape(d) + ".*" + os.sep + name)
            if p == out or _GENERATION.search(os.path.dirname(p))]
    live = [p for p in gens if os.path.exists(p)]
    return max(live, key=os.path.getmtime) if live else None


def prompt_read(row):
    """Prompt tokens the server actually processed for this row (rigby: prompt_tokens; ollama: prompt_eval_count)."""
    for k in ("prompt_tokens", "prompt_eval_count"):
        if row.get(k) is not None:
            return row[k]
    return None


def gen_tokens(row):
    for k in ("gen_tokens", "eval_count"):
        if row.get(k) is not None:
            return row[k]
    return None


def row_status(row, ctx=None):
    """'priced_out' | 'void' | 'answered'. priced_out wins: a row cut at the price line is a measurement even if the
    cut landed beside a context limit. `void` = done_reason/finish_reason "length", or prompt + generated tokens at the
    context (the fleet reducer's own test, ctx - 8)."""
    if row.get("priced_out") or "priced_out" in (row.get("done_reason"), row.get("finish_reason")):
        return "priced_out"
    if "length" in (row.get("done_reason"), row.get("finish_reason")):
        return "void"
    if ctx and (prompt_read(row) or 0) + (gen_tokens(row) or 0) >= int(ctx) - 8:
        return "void"
    return "answered"


def cold_sizes(rows):
    """(box, fam, task, model) -> the largest prompt read for it. Scoped by the caller's rows, so a box defines its own.
    Per MODEL: a prompt's token count is the model's own (qwen3-vl spends ~1,100 tokens on an image the qwen3.5 reads in
    ~180), so one model's cold size can never make another model's honest read look warm."""
    cold = {}
    for r in rows:
        pt = r.get("prompt_tokens")
        if pt:
            k = (r["box"], r["fam"], r["task"], r.get("model") or "")
            cold[k] = max(cold.get(k, 0), pt)
    return cold


def is_warm(prompt_tokens, cold):
    return prompt_tokens is not None and bool(cold) and prompt_tokens < WARM_FRACTION * cold


def reference(rows, exclude_warm=True):
    """rows: dicts {box, fam, task, model, think?, wall_s, ok, prompt_tokens, priced_out?}.
    -> (cheapest {(box, fam, task): seconds}, stats {(box, fam): Counter(solved, warm, unknown_prompt, used)}).

    The cheapest SOLVE is the cheapest ARM's cost per solve: for each arm (model x thinking) on the task, the wall of
    ALL its cold answers divided by the ones it got right - the charter's "per solved" metric, failures charged to the
    solves. the owner the line must come "from the intelligent one". Taking the single fastest correct answer let
    an arm that answers in 0.4 s and is wrong 64% of the time (Nemotron 30B, thinking off, C1) set a 4 s line that
    priced out its own 97%-correct thinking arm. A price-cut row is the line's own effect: it sits on neither side.
    Counted: every solved row of the family, the ones left out as warm / unknown, and the solves that entered a price."""
    rows = list(rows)
    cold = cold_sizes(rows)
    stats = collections.defaultdict(collections.Counter)
    arm_wall = collections.defaultdict(float)
    arm_solved = collections.Counter()
    for r in rows:
        if r.get("priced_out") or not r.get("wall_s") or str(r.get("task", "")).startswith("control"):
            continue
        k = (r["box"], r["fam"], r["task"])
        s = stats[(r["box"], r["fam"])]
        if r.get("ok"):
            s["solved"] += 1
        pt = r.get("prompt_tokens")
        if exclude_warm:
            if pt is None:
                s["unknown_prompt"] += 1 if r.get("ok") else 0
                continue
            if is_warm(pt, cold.get(k + (r.get("model") or "",))):
                s["warm"] += 1 if r.get("ok") else 0
                continue
        arm = k + (r.get("model") or "", str(r.get("think")))
        arm_wall[arm] += float(r["wall_s"])
        if r.get("ok"):
            s["used"] += 1
            arm_solved[arm] += 1
    cheapest = {}
    for arm, n in arm_solved.items():
        k = arm[:3]
        cheapest[k] = min(cheapest.get(k, float("inf")), arm_wall[arm] / n)
    return cheapest, stats


def order_ceiling(cheapest, box, fam, tasks, price_x):
    """One value per order: price_x times the worst of the order's per-task cheapest solves on this box. None when no
    task in the order has a measured solve on this box - there is no price to state."""
    priced = [cheapest[(box, fam, t)] for t in tasks if (box, fam, t) in cheapest]
    return round(price_x * max(priced), 1) if priced else None
