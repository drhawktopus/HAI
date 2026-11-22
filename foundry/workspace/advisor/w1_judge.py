"""#34 Phase 7 B3: a SELECTOR for W1's reason / ops pieces, measured before adopted ([[34-Phase-7-Placement-Policy-Design]]
§ "The cascade arm": W1's reason (exact answer) and ops (multiple choice) tasks have no production selector - a lead
"check" there is the lead solving it - so "a decision model judging a worker's answer is the natural fit; measured
before adopted"). The payload half, on Softserve (the /v1/systemone host): every item is ONE worker's delivered W1
answer, and each decision model is asked ONE choice question - is the ANSWER's result correct for the QUESTION? - with
its own probabilities recorded. The judge never sees the grade: the desk keeps the hidden grades apart and scores the
judge offline (submit_w1_judge.py reduce).

    python w1_judge.py <items.json> <out_dir> --models tev1:0.8b,tev1,nimble:9b-q4_K_M [--host http://127.0.0.1:11434]

items.json: [{"id": ..., "question": ..., "answer": ...}]. Deliverables: <out_dir>/<model>/out.json (rows),
<out_dir>/summary.json (per model: answered, could-not-judge, wall, the footprint), progress markers in
<out_dir>/progress/ (one per item per model). An item the endpoint refuses (its 64 KiB request limit errors rather
than truncating) is a counted could-not-judge row, never a guess and never a cut answer.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import advisor_under_plex as AUP  # noqa: E402

INSTRUCTIONS = ("You check a worker's answer to a task. Read the QUESTION and the worker's ANSWER and decide whether the "
                "ANSWER's final result is correct for the QUESTION. Judge the result, not the style.")
CRITERIA = {"correct": "the final result the answer gives is right for the question",
            "wrong": "the final result is wrong or missing, or the answer does not address the question"}


def state(item):
    return "QUESTION:\n%s\n\nANSWER:\n%s" % (item["question"], item["answer"])


def ask(host, model, item, timeout=600):
    body = json.dumps({"model": model, "state": state(item),
                       "questions": {"verdict": {"type": "choice", "instructions": INSTRUCTIONS,
                                                 "criteria": CRITERIA}}}).encode("utf-8")
    r = urllib.request.Request(host.rstrip("/") + "/v1/systemone", data=body,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.load(resp)


def to_row(item, resp, wall, cold):
    v = (resp.get("answers") or {}).get("verdict") or {}
    if v.get("choice") not in CRITERIA:
        raise ValueError("systemone gave no verdict: %s" % json.dumps(resp)[:300])
    p = v.get("probabilities") or {}
    return {"id": item["id"], "choice": v["choice"], "p_correct": p.get("correct"), "probabilities": p,
            "confidence": v.get("confidence"), "wall_s": round(wall, 3), "cold": cold,
            "state_bytes": len(state(item).encode("utf-8")), "usage": resp.get("usage")}


def run_model(items, out_dir, model, host, kirk):
    os.makedirs(out_dir, exist_ok=True)
    prog = os.path.join(os.path.dirname(out_dir), "progress")
    os.makedirs(prog, exist_ok=True)
    s = AUP.Sampler(kirk)
    s.start()
    t0, rows, refused, err = time.time(), [], [], None
    try:
        for i, it in enumerate(items):
            t = time.time()
            try:
                rows.append(to_row(it, ask(host, model, it), time.time() - t, cold=(not rows)))
            except urllib.error.HTTPError as e:        # the endpoint's own refusal of THIS item (e.g. over 64 KiB)
                refused.append({"id": it["id"], "status": e.code, "why": e.read().decode("utf-8", "replace")[:300],
                                "state_bytes": len(state(it).encode("utf-8"))})
            # Marker files use the item's index instead of its ID, as IDs may contain characters invalid for filenames (e.g., '|').
            # Marker writing is best-effort; failures are caught and ignored to avoid disrupting the measurement loop.
            try:
                open(os.path.join(prog, "%s.%05d.done" % (model.replace(":", "_"), i)), "w").close()
            except OSError:
                pass
            if (i + 1) % 50 == 0 or i + 1 == len(items):
                print("[w1-judge] %s %d/%d judged %d refused %d" % (model, i + 1, len(items), len(rows),
                                                                    len(refused)), flush=True)
    except (urllib.error.URLError, ValueError, OSError) as e:
        err = "%s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        s.stop.set()
        s.join(5)
    with open(os.path.join(out_dir, "out.json"), "w", encoding="utf-8") as fh:
        json.dump({"model": model, "interface": "systemone", "instructions": INSTRUCTIONS, "criteria": CRITERIA,
                   "rows": rows, "refused": refused}, fh, indent=1)
    cell = {"model": model, "ok": err is None and len(rows) + len(refused) == len(items), "judged": len(rows),
            "refused": len(refused), "of": len(items), "error": err, "wall_s": round(time.time() - t0, 1),
            "during": s.summary()}
    print("[w1-judge] %s ok=%s judged %d refused %d of %d wall %.0fs" % (
        model, cell["ok"], len(rows), len(refused), len(items), cell["wall_s"]), flush=True)
    return cell


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("items")
    ap.add_argument("out_dir")
    ap.add_argument("--models", required=True)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--kirk", default=AUP.KIRK)
    ap.add_argument("order_name", nargs="?", default="")   # the foundry appends the order's name to every payload
    a = ap.parse_args(argv)
    items = json.load(open(a.items, encoding="utf-8"))
    assert items and all({"id", "question", "answer"} <= set(it) for it in items), "items need id + question + answer"
    assert not any("ok" in it or "grade" in it for it in items), "the judge must never see the grade"
    cells = [run_model(items, os.path.join(a.out_dir, m.replace(":", "_")), m, a.host, a.kirk)
             for m in a.models.split(",")]
    with open(os.path.join(a.out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"cells": cells, "interface": "systemone"}, fh, indent=1)
    return 0 if all(c["ok"] for c in cells) else 1


if __name__ == "__main__":
    sys.exit(main())
