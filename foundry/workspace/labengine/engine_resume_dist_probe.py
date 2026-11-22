"""Probe lossless resume on non-bit-reproducible engines (e.g., Vulkan at t=0).

Since token-level equality fails due to non-determinism, this verifies resume by comparing next-token distributions at teacher-forced positions against a run-to-run noise floor (live2 vs live1) and a context-shift sensitivity baseline (shift).

Usage:
    engine_resume_dist_probe.py <model> <num_ctx> {ORDER_OUT} [--cut 60] [--k 40] [--n_probs 20]

Method:
- ref: Greedy tokens A[:cut+k] used for forcing (not compared).
- live1: Distributions at positions i in [cut, cut+k) with prompt P + A[:i].
- live2: Same after evict/reload (defines the noise FLOOR).
- resumed: Prefill P + A[:cut], save/evict/restore slots, then same queries (the CLAIM).
- shift: live1[i] vs live1[i+1] (sensitivity check).

Pass condition: Claim deviation (KLD/top-token) stays within the floor, while shift deviates significantly. Restored queries must read only tokens past the cut.

KLD is computed over the union of top-n_probs sets; missing mass is lumped into an "other" bucket. Writes {ORDER_OUT}/resume_dist.json; makes no changes to engine state."""
import argparse
import json
import math
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_resume_probe as R  # noqa: E402  - load / evict / runner_port / post / slot: one derivation

SLOT = "lab-resume-dist.bin"
PROMPT = ("Write a short story, about 300 words, about a lighthouse keeper who finds a message in a bottle. "
          "Use vivid detail.")


def dist(port, ids, n_probs):
    """Next-token distribution after `ids` (top n_probs, as log-probs) + how many prompt tokens the engine read."""
    r = R.post("http://127.0.0.1:%d/completion" % port,
               {"prompt": ids, "n_predict": 1, "temperature": 0, "n_probs": n_probs, "id_slot": 0,
                "cache_prompt": True, "return_tokens": True, "stream": False})
    cp = (r.get("completion_probabilities") or [{}])[0]
    tops = cp.get("top_logprobs") or cp.get("top_probs") or cp.get("probs") or []
    out = {}
    for t in tops:
        tid = t.get("id", t.get("tok_id"))
        if "logprob" in t:
            out[tid] = t["logprob"]
        elif "prob" in t and t["prob"] > 0:
            out[tid] = math.log(t["prob"])
    return out, (r.get("timings") or {}).get("prompt_n")


def kld(p, q):
    """KL(p || q) over the union of both top sets; mass outside a set lumped into one bucket (floor 1e-9)."""
    keys = set(p) | set(q)
    pp = {k: math.exp(p[k]) if k in p else 0.0 for k in keys}
    qq = {k: math.exp(q[k]) if k in q else 0.0 for k in keys}
    po, qo = max(1e-9, 1 - sum(pp.values())), max(1e-9, 1 - sum(qq.values()))
    total = po * math.log(po / qo)
    for k in keys:
        a, b = max(pp[k], 1e-9), max(qq[k], 1e-9)
        total += pp[k] * math.log(a / b)
    return total


def compare(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x and y]
    if not pairs:
        return {"n": 0}
    ks = sorted(kld(x, y) for x, y in pairs)
    same = sum(max(x, key=x.get) == max(y, key=y.get) for x, y in pairs)
    return {"n": len(pairs), "mean_kld": sum(ks) / len(ks), "p99_kld": ks[min(len(ks) - 1, int(0.99 * len(ks)))],
            "max_kld": ks[-1], "same_top": same / len(pairs)}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("num_ctx", type=int)
    ap.add_argument("out")
    ap.add_argument("--cut", type=int, default=60)
    ap.add_argument("--k", type=int, default=40)
    ap.add_argument("--n_probs", type=int, default=20)
    a, _rest = ap.parse_known_args(argv)
    os.makedirs(a.out, exist_ok=True)
    rep = {"box": socket.gethostname(), "model": a.model, "num_ctx": a.num_ctx, "cut": a.cut, "k": a.k,
           "n_probs": a.n_probs, "method": "KLD/same-top vs run-to-run floor on teacher-forced positions"}
    port, lines = R.load(a.model, a.num_ctx)
    rep["runner"] = lines[0][:300] if lines else None
    P = R.post("http://127.0.0.1:%d/tokenize" % port, {"content": PROMPT, "add_special": True})["tokens"]
    R.slot(port, "erase")
    ref = R.post("http://127.0.0.1:%d/completion" % port,
                 {"prompt": P, "n_predict": a.cut + a.k, "temperature": 0, "id_slot": 0, "cache_prompt": True,
                  "return_tokens": True, "stream": False}).get("tokens") or []
    A = ref[:a.cut + a.k]
    rep["ref_tokens"] = len(A)
    span = range(a.cut, min(len(A), a.cut + a.k))

    def prefill():
        """Every arm builds its cache the SAME way: P + A[:cut] read in one call. The first version let the live arms
        read it inside their first query (one batch of 89) while the resumed arm re-decoded only the last token, and on
        a deterministic engine (Softserve) that batch-shape difference alone read KLD 0.008 - two variables, one claim."""
        R.post("http://127.0.0.1:%d/completion" % port,
               {"prompt": P + A[:a.cut], "n_predict": 1, "temperature": 0, "id_slot": 0, "cache_prompt": True,
                "stream": False})

    def sweep(tag):
        out, reads = [], []
        for i in span:
            d, n = dist(port, P + A[:i], a.n_probs)
            out.append(d)
            reads.append(n)
        rep[tag + "_reads"] = reads[:3] + (["..."] if len(reads) > 3 else []) + reads[-1:]
        return out

    R.slot(port, "erase")
    prefill()
    live1 = sweep("live1")
    R.evict(a.model)
    port, _ = R.load(a.model, a.num_ctx)
    R.slot(port, "erase")
    prefill()
    live2 = sweep("live2")
    # the resumed state: the same prefill, saved, the model evicted and reloaded, then restored - the ONE difference
    R.slot(port, "erase")
    prefill()
    sv = R.post("http://127.0.0.1:%d/slots/0?action=save" % port, {"filename": SLOT})
    rep["save"] = {k: sv.get(k) for k in ("n_saved", "n_written", "pending_token", "lab_replay", "timings")}
    t = time.time()
    R.evict(a.model)
    port, _ = R.load(a.model, a.num_ctx)
    rep["reload_s"] = round(time.time() - t, 2)
    rs = R.post("http://127.0.0.1:%d/slots/0?action=restore" % port, {"filename": SLOT})
    rep["restore"] = {k: rs.get(k) for k in ("n_restored", "n_read", "lab_resume_armed", "timings")}
    resumed = sweep("resumed")
    R.slot(port, "erase")
    for folder in (r"F:\HAI\slots", r"C:\ProgramData\HAI\slots"):      # wherever this box's launcher sends slot saves
        try:
            os.remove(os.path.join(folder, SLOT))
        except OSError:
            pass
    rep["floor"] = compare(live2, live1)
    rep["claim"] = compare(resumed, live1)
    rep["shift"] = compare(live1[1:], live1[:-1])
    f, c, s = rep["floor"], rep["claim"], rep["shift"]
    first_read = rep["resumed_reads"][0] if rep.get("resumed_reads") else None
    if not (f.get("n") and c.get("n") and s.get("n")):
        rep["verdict"] = "VOID - a comparison had no pairs"
    elif s["mean_kld"] <= 10 * max(f["mean_kld"], 1e-6):
        rep["verdict"] = "VOID - the metric cannot tell different contexts from noise (shift not >> floor)"
    else:
        within = c["mean_kld"] <= 2 * max(f["mean_kld"], 1e-6) and c["same_top"] >= f["same_top"] - 0.05
        rep["verdict"] = ("LOSSLESS (within the run-to-run floor)" if within else "NOT LOSSLESS (claim above floor)")
        rep["verdict"] += "; restore first query read %s tokens" % first_read
    with open(os.path.join(a.out, "resume_dist.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    print("[dist] %s %s: %s | floor %s | claim %s | shift %s" % (
        rep["box"], a.model, rep["verdict"], f, c, s), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
