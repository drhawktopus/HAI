"""Stage planner: sizes a pipelined bench run using measured costs to account for mixed tensor types (e.g., Q6_K layers cost ~1.5x per MiB). The optimizer minimizes `(chunks + stages - 1) * slowest_stage`, constrained by actual free memory. `rates` are fitted per device/tensor type from delivered bench results; `plan` assigns contiguous layer runs to stages.

Usage: python tolkien_plan.py <model.gguf> [--p 2048] [--ub 64] [--tablets a,b,...]"""
import argparse
import glob
import json
import os
import sys
from functools import lru_cache

import tablet_foundry as tf
import tolkien_llm as tv

MIN_TYPE_SHARE = 0.01        # a type under 1 % of a class's MiB is folded into its blended rate, not fitted alone
# Rates are the WEIGHT cost, fitted from short reads; a long prompt's attention cost is attn_ms, measured separately.
# Round 16's 32K rows put 3.3 s per chunk on a 365 MiB attention layer - fitted as weight cost, that is ~7x too high.
RATE_MAX_PROMPT = 4096


def device_class(dev):
    if dev == "card":
        return dev
    if tv.is_rigby(dev):
        return "rigby"
    from tablet_control import GROUPS
    for g, members in GROUPS.items():
        if g.startswith("class-") and dev in members:
            return g[len("class-"):].upper()
    return None


def _mix_of(units, mix):
    out = {}
    for u in units:
        for t, v in mix[u].items():
            out[t] = out.get(t, 0.0) + v
    return out


def samples_from_rows(rows, model_path, ub=None):
    """[(class, {type: MiB}, ms per token)] from delivered bench rows of this model: each traced stage but the last,
    and rigby-alone runs (all units, ms per token = 1000 / tok/s)."""
    n_layer, mix = tv.unit_mix(model_path)
    name = os.path.basename(model_path)
    out = []
    for r in rows:
        if r.get("model") != name or not r.get("n_ubatch") or (r.get("n_prompt") or 0) > RATE_MAX_PROMPT:
            continue
        # a reading run of at least one whole chunk from an empty cache: writing rows pass one token per step (round
        # 18's stage figures, divided by the chunk size, read every class ~30 % fast) and depth rows read slower
        if r.get("n_gen") or r.get("n_depth") or (r.get("n_prompt") or 0) < r["n_ubatch"] \
                or (ub is not None and r["n_ubatch"] != ub):
            continue
        if r.get("stages") and r.get("trace"):
            st = r["stages"]
            units = tv.stage_units([s["ts"] for s in st], n_layer)
            shared = sum(1 for s in st if tv.is_rigby(s["dev"])) > 1
            for s, us in list(zip(st, units))[:-1]:
                t = r["trace"].get(s["dev"])
                cls = device_class(s["dev"])
                # a rigby stage that shares rigby's cores with its other stages does not measure rigby's rate
                if t and t.get("median_ms") and cls not in (None, "card") and not (shared and cls == "rigby"):
                    out.append((cls, _mix_of(us, mix), t["median_ms"] / float(r["n_ubatch"])))
        elif r.get("where") == "rigby" and not r.get("stages") and r.get("avg_ts") and r.get("gpu_layers") is None:
            out.append(("rigby", _mix_of(range(n_layer + 1), mix), 1000.0 / r["avg_ts"]))
    return out


def _solve(a, b):
    """Least squares a.x = b by the normal equations (a few unknowns); None when singular."""
    k = len(a[0])
    m = [[sum(a[r][i] * a[r][j] for r in range(len(a))) for j in range(k)] +
         [sum(a[r][i] * b[r] for r in range(len(a)))] for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(k):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][k] / m[i][i] for i in range(k)]


def fit_rates(samples):
    """{class: {type: ms per MiB per token, "*": blended}}. Types are fitted jointly per class when the samples can
    tell them apart (enough distinct mixes, a positive solution); otherwise the class gets only its blended rate."""
    out = {}
    for cls in sorted({s[0] for s in samples}):
        mine = [s for s in samples if s[0] == cls]
        total = sum(sum(m.values()) for _c, m, _ms in mine)
        rates = {"*": sum(ms for _c, _m, ms in mine) / total, "_n": len(mine)}
        shares = {}
        for _c, m, _ms in mine:
            for t, v in m.items():
                shares[t] = shares.get(t, 0.0) + v
        types = sorted(t for t, v in shares.items() if v / total >= MIN_TYPE_SHARE)
        if len(mine) >= len(types) >= 1:
            a = [[m.get(t, 0.0) for t in types] for _c, m, _ms in mine]     # minor types: under 1 %, left out
            x = _solve(a, [ms for _c, _m, ms in mine])
            if x and all(v > 0 for v in x):
                rates.update(dict(zip(types, x)))
        out[cls] = rates
    return out


def unit_cost(rates, cls, mix_u):
    """ms per token for one unit on a class; a type without its own fitted rate takes the class's blended rate."""
    r = rates[cls]
    return sum(v * (r[t] if t in r else r["*"]) for t, v in mix_u.items())


def stage_need(mix, kv_mib, n_ctx):
    """need(i, j) -> MiB units i..j-1 take as one tablet stage: tolkien_llm.stage_need_mib (weights + KV + growth at
    n_ctx), the preflight's own function; n_ctx None = weights + KV only."""
    w, kv = [0.0], [0.0]
    for u in range(len(mix)):
        w.append(w[-1] + sum(mix[u].values()))
        kv.append(kv[-1] + kv_mib[u])

    def need(i, j):
        if n_ctx is None:
            return w[j] - w[i] + kv[j] - kv[i]
        return tv.stage_need_mib(w[j] - w[i], kv[j] - kv[i], n_ctx)
    return need


def plan(mix, rates, devices, n_chunks, mem_cap=None, ub=64, last="rigby", kv_mib=None, attn_ms=None, n_ctx=None):
    """Plan the optimal device assignment for `mix` to minimize total execution time.

    Args:
        mix: Sequence of layer units to execute.
        rates: Execution rates for devices.
        devices: Mapping of device names to their class.
        n_chunks: Number of chunks in the sequence.
        mem_cap: Optional mapping of device name to MiB limit (weights + KV cache).
        ub: Upper bound on stages (default 64).
        last: Device class that must hold the final stage (output unit).
        kv_mib: Per-unit KV cache size in MiB.
        attn_ms: Extra milliseconds per chunk for attention layers, keyed by device class.
        n_ctx: Context length.

    Returns:
        Dict containing 'stages' (list of (device, units) tuples), 'ms_per_chunk' (list of stage costs),
        'slowest' (max stage cost), and 'objective' (total time estimate)."""
    mem_cap = mem_cap or {}
    U = len(mix)
    names = sorted(devices, key=lambda d: (devices[d], d))
    classes = sorted({devices[d] for d in names if devices[d] in rates})
    by_cls = {c: [d for d in names if devices[d] == c] for c in classes}
    kv_mib = kv_mib or [0.0] * U
    attn_ms = attn_ms or {}
    pre = {c: [0.0] for c in classes}
    for c in classes:
        for u in range(U):
            extra = attn_ms.get(c, 0.0) if kv_mib[u] > 0 else 0.0
            pre[c].append(pre[c][-1] + ub * unit_cost(rates, c, mix[u]) + extra)
    need = stage_need(mix, kv_mib, n_ctx)
    cap = {c: min((mem_cap.get(d) for d in by_cls[c] if d in mem_cap), default=None) for c in classes}
    tail = last if last in classes else None

    @lru_cache(maxsize=None)
    def best(i, used):
        if i == U:
            return {0: (0.0, ())}
        res = {}
        for ci, c in enumerate(classes):
            if used[ci] >= len(by_cls[c]):
                continue
            nxt = tuple(n + (1 if k == ci else 0) for k, n in enumerate(used))
            for j in range(i + 1, U + 1):
                if cap[c] is not None and need(i, j) > cap[c]:
                    break
                if tail is not None and (j == U) != (c == tail):
                    continue                       # the tail class holds exactly the final stage
                if j == U and j - i < 2:
                    continue                       # tv.OUTPUT_ALONE: the output unit never stands alone
                cost = pre[c][j] - pre[c][i]
                for s, (p, segs) in best(j, nxt).items():
                    cand = (max(cost, p), ((c, i, j),) + segs)
                    if s + 1 not in res or cand[0] < res[s + 1][0]:
                        res[s + 1] = cand
        return res

    options = best(0, tuple(0 for _ in classes))
    if not options:
        return None
    s, (p, segs) = min(options.items(), key=lambda kv: (n_chunks + kv[0] - 1) * kv[1][0])
    left = {c: list(by_cls[c]) for c in classes}
    stages, per = [], {}
    for c, i, j in segs:
        d = left[c].pop(0)
        stages.append({"dev": d, "ts": j - i})
        per[d] = round(pre[c][j] - pre[c][i])
    return {"stages": stages, "stage_ms": per, "slowest_ms": round(p), "n_stages": s,
            "objective_ms": round((n_chunks + s - 1) * p)}


def plan_shared(mix, rates, devices, n_chunks, mem_cap=None, ub=64, kv_mib=None, attn_ms=None, iters=40,
                max_stages=tv.MAX_STAGES, n_ctx=None):
    """Like plan, but rigby takes EVERY unit no tablet holds, as several rigby stages (rigby, rigby2, ...) between
    the tablets' runs. Its stages share one CPU, so the pace is max(slowest tablet run, rigby's TOTAL per chunk).
    The output unit stays on rigby. Bisects the pace T: a T is reachable when tablet runs costing <= T can take
    enough of rigby's work that rigby's remainder is <= T. Hop costs between stages are not modelled."""
    mem_cap = mem_cap or {}
    kv_mib = kv_mib or [0.0] * len(mix)
    attn_ms = attn_ms or {}
    U = len(mix)
    tabs = {d: c for d, c in devices.items() if c in rates and c != "rigby"}
    classes = sorted(set(tabs.values()))
    by_cls = {c: sorted((d for d in tabs if tabs[d] == c), key=lambda d: (-mem_cap.get(d, 0), d)) for c in classes}

    def unit_ms(c, u):
        return ub * unit_cost(rates, c, mix[u]) + (attn_ms.get(c, 0.0) if kv_mib[u] > 0 else 0.0)
    pre = {c: [0.0] for c in classes}
    for c in classes:
        for u in range(U):
            pre[c].append(pre[c][-1] + unit_ms(c, u))
    rig = [unit_ms("rigby", u) for u in range(U)]
    rig_pre = [0.0]
    for u in range(U):
        rig_pre.append(rig_pre[-1] + rig[u])
    need = stage_need(mix, kv_mib, n_ctx)
    cap = {c: min((mem_cap.get(d) for d in by_cls[c] if d in mem_cap), default=None) for c in classes}

    NONE = (-1.0, ())

    def solve(T):
        @lru_cache(maxsize=None)
        def best(i, used, gaps, on_rigby):
            """Max rigby work removed from unit i on; gaps = rigby stages opened so far; on_rigby = unit i-1 was
            rigby's. Every stage is a backend: tablets used + rigby stages must stay within max_stages."""
            if i >= U - 1:                                   # the output unit is rigby's
                g = gaps + (0 if on_rigby else 1)
                return (0.0, ()) if sum(used) + g <= max_stages else NONE
            g = gaps + (0 if on_rigby else 1)
            res = best(i + 1, used, g, True) if sum(used) + g <= max_stages else NONE
            for ci, c in enumerate(classes):
                if used[ci] >= len(by_cls[c]):
                    continue
                nxt = tuple(n + (1 if k == ci else 0) for k, n in enumerate(used))
                for j in range(i + 1, U - 1):                # never the output unit, nor the layer before it
                    if cap[c] is not None and need(i, j) > cap[c]:
                        break
                    if pre[c][j] - pre[c][i] > T:
                        break
                    r2, p2 = best(j, nxt, gaps, False)
                    if r2 < 0:
                        continue
                    r2 += rig_pre[j] - rig_pre[i]
                    if r2 > res[0]:
                        res = (r2, ((c, i, j),) + p2)
            return res
        return best(0, tuple(0 for _ in classes), 0, False)

    lo, hi = 0.0, rig_pre[U]
    chosen = (0.0, ())
    for _ in range(iters):
        mid = (lo + hi) / 2
        removed, segs = solve(mid)
        if removed >= 0 and rig_pre[U] - removed <= mid:
            hi, chosen = mid, (removed, segs)
        else:
            lo = mid
    removed, segs = chosen
    left = {c: list(by_cls[c]) for c in classes}
    stages, per, at, gaps = [], {}, 0, []
    for c, i, j in segs:
        if i > at:
            gaps.append(len(stages))
            stages.append({"dev": None, "ts": i - at})
        d = left[c].pop(0)
        stages.append({"dev": d, "ts": j - i})
        per[d] = round(pre[c][j] - pre[c][i])
        at = j
    gaps.append(len(stages))
    stages.append({"dev": None, "ts": U - at})
    for k, g in enumerate(gaps):
        stages[g]["dev"] = "rigby" if k == len(gaps) - 1 else "rigby%d" % (k + 2)
    rigby_ms = round(rig_pre[U] - removed)
    slowest = max([rigby_ms] + list(per.values()))
    return {"stages": stages, "stage_ms": per, "rigby_total_ms": rigby_ms, "slowest_ms": slowest,
            "n_stages": len(stages), "objective_ms": round((n_chunks + len(stages) - 1) * slowest),
            "rigby_alone_ms_per_chunk": round(rig_pre[U])}


def delivered_rows():
    rows = []
    for p in glob.glob(os.path.join(tf.OUTBOX, "*.results.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        rows += [r for r in d.get("rows") or [] if isinstance(r, dict) and r.get("test") == "bench"]
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="size a pipelined bench run from measured stage costs")
    ap.add_argument("model", help="GGUF file name in the models mount")
    ap.add_argument("--p", type=int, default=2048)
    ap.add_argument("--ub", type=int, default=64)
    ap.add_argument("--tablets", default="", help="comma list (default: every wire tablet with a class)")
    ap.add_argument("--attn-ms", default="", help="class=ms,... extra ms per chunk per attention layer at --p, "
                                                  "measured (e.g. round 16 at 32K)")
    ap.add_argument("--shared-rigby", action="store_true", help="rigby takes every gap between tablet runs "
                                                                     "(several rigby stages on one CPU)")
    ap.add_argument("--max-stages", type=int, default=tv.MAX_STAGES, help="stage cap for --shared-rigby")
    a = ap.parse_args(argv)
    attn_ms = {k: float(v) for k, v in (x.split("=") for x in a.attn_ms.split(",") if x)}
    path = os.path.join(tv.MODELS, a.model)
    n_layer, mix = tv.unit_mix(path)
    samples = samples_from_rows(delivered_rows(), path, ub=a.ub)
    rates = fit_rates(samples)
    print("rates (ms per MiB per token; '*' blended, _n samples) fitted from this model's delivered runs:")
    for c, r in sorted(rates.items()):
        print("  %-6s %s" % (c, json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})))
    tabs = [t for t in (a.tablets.split(",") if a.tablets else tf.WIRE_OCTET) if device_class(t) in rates]
    devices = dict({t: device_class(t) for t in tabs}, rigby="rigby")
    avail = tv.tablet_mem_avail(set(tabs))
    blind = sorted(t for t in tabs if t not in avail)
    for t in blind:
        devices.pop(t)
    mem_cap = {t: avail[t] - tv.STAGE_RESERVE_MIB for t in tabs if t in avail}
    if blind:
        print("left out (no fresh free-memory reading - could not tell): %s" % ", ".join(blind))
    missing = sorted({c for c in devices.values() if c not in rates})
    if missing:
        print("no measured rate for class %s - those devices are left out" % ", ".join(missing))
        devices = {d: c for d, c in devices.items() if c in rates}
    n_chunks = -(-a.p // a.ub)
    n_ctx = tv.bench_n_ctx({"p": [a.p]})
    kv = tv.unit_kv_mib(path, n_ctx)
    if a.shared_rigby:
        out = plan_shared(mix, rates, devices, n_chunks, mem_cap=mem_cap, ub=a.ub, kv_mib=kv, attn_ms=attn_ms,
                          max_stages=min(a.max_stages, tv.MAX_STAGES), n_ctx=n_ctx)
        print(json.dumps(dict(out, n_chunks=n_chunks), indent=1))
        return 0
    out = plan(mix, rates, devices, n_chunks, mem_cap=mem_cap, ub=a.ub, kv_mib=kv, attn_ms=attn_ms, n_ctx=n_ctx)
    if out is None:
        print("no plan fits")
        return 1
    alone = round(a.ub * sum(unit_cost(rates, "rigby", m) for m in mix)) if "rigby" in rates else None
    print(json.dumps(dict(out, n_chunks=n_chunks, rigby_alone_ms_per_chunk=alone), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
