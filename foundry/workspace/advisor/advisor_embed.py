"""Phase 8 triage, Kirk's RETRIEVAL signal (the 07-25 design, PROJECT-BACKLOG § "TOOL SELECTION should be RETRIEVAL":
embed the request, score it against a cached set; weak or bunched scores = unsure). This payload only EMBEDS: one
vector per item from this box's ollama, delivered whole, so every decision rule over them (nearest measured tasks,
weights, the unsure test) is a pure function in the reduce and can be examined without re-running the box.

    python advisor_embed.py <items.json> <out_dir> [--model nomic-embed-text] [--host http://127.0.0.1:11434]

items.json: [{"id": ..., "req": "<text>"}]. Deliverable: <out_dir>/embeddings.json {model, dims, rows: [{id, vec,
prompt_tokens}], refused: [{id, status, why}] (inputs the model would not take whole - never silently cut), wall_s}.
Progress: <out_dir>/progress/.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


CTX = {"nomic-embed-text": 8192}    # the model's trained window (nomic-embed-text v1.5), not a number picked here


def ctx_for(model):
    """The window for a model name with or without its tag (`nomic-embed-text:latest` is the same model)."""
    name = model[:-len(":latest")] if model.endswith(":latest") else model
    assert name in CTX, "no recorded window for %s - add the model's trained window, never guess one" % model
    return CTX[name]


def embed(host, model, text, timeout=600):
    # truncate False: inputs exceeding the model window are rejected; do not switch to True (silently truncates)
    body = json.dumps({"model": model, "input": text, "truncate": False,
                       "options": {"num_ctx": ctx_for(model)}}).encode("utf-8")
    r = urllib.request.Request(host.rstrip("/") + "/api/embed", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        out = json.load(resp)
    vecs = out.get("embeddings") or []
    if len(vecs) != 1 or not vecs[0]:
        raise ValueError("embed returned %d vectors for one input" % len(vecs))
    return vecs[0], out.get("prompt_eval_count")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("items")
    ap.add_argument("out_dir")
    ap.add_argument("--model", default="nomic-embed-text")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("order_name", nargs="?", default="")   # the foundry appends the order's name to every payload
    a = ap.parse_args(argv)
    items = json.load(open(a.items, encoding="utf-8"))
    assert items and all("req" in it and "id" in it for it in items), "items need id + req"
    os.makedirs(os.path.join(a.out_dir, "progress"), exist_ok=True)
    t0, rows, refused, err = time.time(), [], [], None
    try:
        for i, it in enumerate(items):
            try:
                vec, ptok = embed(a.host, a.model, it["req"])
            except urllib.error.HTTPError as e:      # one input the model refuses (too long) - recorded, run continues
                refused.append({"id": it["id"], "status": e.code, "why": e.read()[:300].decode("utf-8", "replace")})
                print("[embed] %d/%d %s REFUSED %s" % (i + 1, len(items), it["id"], refused[-1]["why"][:120]), flush=True)
                continue
            rows.append({"id": it["id"], "vec": vec, "prompt_tokens": ptok})
            open(os.path.join(a.out_dir, "progress", "%s.done" % re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(it["id"]))),
                 "w").close()
            print("[embed] %d/%d %s dims %d tokens %s" % (i + 1, len(items), it["id"], len(vec), ptok), flush=True)
    except (OSError, ValueError) as e:              # the box or the model failing is not a property of one input
        err = "%s: %s" % (type(e).__name__, str(e)[:300])
    dims = sorted({len(r["vec"]) for r in rows})
    with open(os.path.join(a.out_dir, "embeddings.json"), "w", encoding="utf-8") as fh:
        json.dump({"model": a.model, "num_ctx": ctx_for(a.model), "dims": dims, "rows": rows,
                   "refused": refused, "of": len(items), "error": err, "wall_s": round(time.time() - t0, 1)}, fh)
    print("[embed] %s embedded %d/%d refused %d dims %s error %s" % (
        a.model, len(rows), len(items), len(refused), dims, err), flush=True)
    return 0 if err is None and len(rows) + len(refused) == len(items) and len(dims) == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
