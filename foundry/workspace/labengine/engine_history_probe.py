"""Deterministic repeat probe: verify engine output is identical across state changes.

Usage:
  engine_history_probe.py <model> <num_ctx> {ORDER_OUT} [--n 160] [--temperature 0.6] [--seed 7] [--probs 8]

This isolates determinism from resume machinery. For each case, the slot is erased and prompt cache disabled before re-running request P with a fixed seed.

Cases:
  A1  Initial request on fresh engine (reference).
  A2  Immediate repeat on same server.
  A3  Repeat after server processed a different request.
  A4  Repeat after model eviction and reload.

Each result is compared token-by-token and logprob-by-logprob against A1. A deviation in A4 invalidates the run (instrument failure); a deviation in A3 is a finding.

Writes: {ORDER_OUT}/history.json
Side effects: None (no installs, no state changes)."""
import argparse
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the foundry runs payloads isolated (-I): no script dir on the path
import engine_resume_probe as E  # noqa: E402

P_TEXT = ("Write a short story, about 300 words, about a lighthouse keeper who finds a message in a bottle. "
          "Use vivid detail.")
Q_TEXT = "List twelve facts about the planet Jupiter, one per line, each under fifteen words."


def ask(port, ids, a):
    E.slot(port, "erase")
    r = E.post("http://127.0.0.1:%d/completion" % port,
               {"prompt": ids, "n_predict": a.n, "temperature": a.temperature, "seed": a.seed, "id_slot": 0,
                "cache_prompt": False, "return_tokens": True, "stream": False, "n_probs": a.probs, "post_sampling_probs": False})
    t = r.get("timings") or {}
    return {"tokens": r.get("tokens") or [], "numbers": E.numbers(r),
            "read": {k: t.get(k) for k in ("prompt_n", "cache_n", "predicted_n", "draft_n", "draft_n_accepted")},
            "tokens_cached": r.get("tokens_cached")}


def versus(ref, x):
    same = x["tokens"] == ref["tokens"]
    first = next((i for i, (p, q) in enumerate(zip(x["tokens"], ref["tokens"])) if p != q), None)
    return dict(E.compare_numbers(ref["numbers"], x["numbers"]), same=same, first_different_token=first,
                tokens=len(x["tokens"]), read=x["read"], tokens_cached=x["tokens_cached"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("num_ctx", type=int)
    ap.add_argument("out")
    ap.add_argument("--n", type=int, default=160)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--probs", type=int, default=8)
    a, _extra = ap.parse_known_args(argv)        # the foundry appends the order name after the payload's own arguments
    os.makedirs(a.out, exist_ok=True)
    rep = {"box": socket.gethostname(), "model": a.model, "num_ctx": a.num_ctx, "n": a.n, "temperature": a.temperature,
           "seed": a.seed, "library_env": os.environ.get("OLLAMA_LLM_LIBRARY")}
    E.evict(a.model)                                   # never land on the runner the last order left
    port, lines = E.load(a.model, a.num_ctx)
    rep["runner"] = lines[0][:1800] if lines else None
    P = E.post("http://127.0.0.1:%d/tokenize" % port, {"content": P_TEXT, "add_special": True})["tokens"]
    Q = E.post("http://127.0.0.1:%d/tokenize" % port, {"content": Q_TEXT, "add_special": True})["tokens"]
    rep["prompt_tokens"] = {"P": len(P), "Q": len(Q)}
    A1 = ask(port, P, a)
    A2 = ask(port, P, a)
    other = ask(port, Q, a)
    A3 = ask(port, P, a)
    E.evict(a.model)
    port, _lines = E.load(a.model, a.num_ctx)
    A4 = ask(port, P, a)
    rep["reference"] = {"tokens": len(A1["tokens"]), "read": A1["read"], "rows_with_numbers": sum(1 for x in A1["numbers"] if x["top"])}
    rep["other_answer_tokens"] = len(other["tokens"])
    rep["arms"] = {"repeat_right_away": versus(A1, A2), "after_a_different_answer": versus(A1, A3),
                   "after_a_fresh_load": versus(A1, A4)}
    control = rep["arms"]["after_a_fresh_load"]["same"]
    history = rep["arms"]["repeat_right_away"]["same"] and rep["arms"]["after_a_different_answer"]["same"]
    rep["verdict"] = ("VOID - the engine does not repeat itself even from a fresh load, so this run says nothing about history"
                      if not control else
                      "REPEATS ITSELF across its own history" if history else "DOES NOT REPEAT ITSELF across its own history")
    E.evict(a.model)                                   # leave the card as it was found: nothing resident
    with open(os.path.join(a.out, "history.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    print(rep["verdict"])
    for k, v in rep["arms"].items():
        print(k, {x: v[x] for x in ("same", "first_different_token", "first_moved", "largest_move", "rows_with_numbers", "read")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
