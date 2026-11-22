"""Read yardsticks from delivered results (ladder.json / strata.json), never from memory.
Reports per engine: best WARM REPEATED-prompt rate (best warm gen, no prompt list) and
best VARIED-prompt median (median of arms with prompt lists), filtered to 'ok' arms only.
Usage: yardsticks.py [--json]"""
import glob
import json
import os
import sys

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "delivered")


def median(v):
    v = sorted(x for x in v if x)
    return v[len(v) // 2] if v else None


def rows():
    for p in glob.glob(os.path.join(D, "*", "ladder.json")):
        order = os.path.basename(os.path.dirname(p))
        try:
            d = json.load(open(p, encoding="utf-8"))
        except ValueError:
            continue
        for a in d.get("arms", []):
            if a.get("result") != "ok" or not a.get("gen_tok_s"):
                continue
            if "flash-next" not in str(a.get("gguf") or "").lower():     # this workspace also ran 27B comparisons
                continue
            g = a["gen_tok_s"]
            kind = "varied" if a.get("prompts") else "repeated"
            yield {"engine": "llama.cpp", "kind": kind, "order": order, "arm": a.get("name"),
                   "value": median(g) if kind == "varied" else max((x for x in g[1:] if x), default=None), "series": g}
    for p in glob.glob(os.path.join(D, "*", "strata.json")):
        order = os.path.basename(os.path.dirname(p))
        try:
            d = json.load(open(p, encoding="utf-8"))
        except ValueError:
            continue
        if d.get("result") != "ok":
            continue
        g = d.get("gen_tok_s") or []
        if len(g) > 1:
            yield {"engine": "strata", "kind": "repeated", "order": order, "arm": "gens",
                   "value": max((x for x in g[1:] if x), default=None), "series": g}
        v = [x.get("tok_s") for x in d.get("varied", [])]
        if v:
            yield {"engine": "strata", "kind": "varied", "order": order, "arm": "varied", "value": median(v), "series": v}


def best():
    out = {}
    for r in rows():
        if r["value"] is None:
            continue
        k = (r["engine"], r["kind"])
        if k not in out or r["value"] > out[k]["value"]:
            out[k] = r
    return out


if __name__ == "__main__":
    b = best()
    if "--json" in sys.argv:
        print(json.dumps({"%s/%s" % k: v for k, v in b.items()}, indent=1))
    else:
        for (eng, kind), r in sorted(b.items()):
            print("%-9s %-8s best %6.2f tok/s  <- %s / %s  %s" % (eng, kind, r["value"], r["order"], r["arm"],
                                                                 [round(x, 1) for x in r["series"] if x]))
