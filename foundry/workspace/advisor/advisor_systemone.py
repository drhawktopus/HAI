"""Phase 8 advisor (decision-model arm): scores requests via Ollama to extract probability and confidence for thresholding.\\n\\nUsage: python advisor_systemone.py <items.json> <out_dir> --models tev1:0.8b [options]\\nInput: JSON list of {\\"id\\": str, \\"req\\": str}. Output: Per-model results, summary, and progress markers in <out_dir>."""
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

TIER_CRITERIA = {
    "tool": "a lab tool or plain code does it (render an image, wake or reboot a machine, check a job's progress or "
            "status, approve or propose a lab action, run a named lab tool, look up who can be rendered)",
    "fellowship": "simple chat a small model answers instantly: greetings, small talk, one-line trivia, a ping",
    "8gb": "a short factual answer from the lab's own documentation or records: a lookup or a brief summary",
    "lead": "real work or anything needing judgment: coding, multi-step research, writing help, plans and designs, legal "
            "or other high-stakes reasoning - and ANY request you are unsure about",
}
# Tiers are sorted by difficulty, not domain family. Live-status asks must route to "tool" (not "lead"). If you change tier wording or mapping, re-measure against the replay set before shipping.
TIER_CRITERIA_DIFFICULTY = {
    "tool": "a lab tool or plain code does it: render an image, wake or reboot a machine, approve or propose a lab action, "
            "run a named lab tool, look up who can be rendered, or read the LIVE state of the lab (what a machine or job "
            "is doing, its progress or speed, whether anyone is watching Plex, whether a device is visible)",
    "fellowship": "easy: greetings, small talk, one-line trivia, a ping, or a tiny self-contained snippet",
    "8gb": "moderate: a short factual answer from the lab's own documentation or records, a brief summary, or a small "
           "function written from a clear and complete specification",
    "lead": "hard: work over many steps, design or planning, judgment or high stakes, debugging or changing code it has "
            "not seen, open-ended research or long writing - and ANY request you are unsure about",
}
QUESTION_VERSIONS = {"v1": TIER_CRITERIA, "difficulty": TIER_CRITERIA_DIFFICULTY}

FAMILY_CRITERIA = {"chat": "chat", "coding": "coding", "ops": "ops (running the lab's machines and services)",
                   "image": "image", "research": "research", "writing": "writing"}
KIND_CRITERIA = {"chat": "answered in one reply, at most one tool call",
                 "job": "many steps, tools, or work that runs as an order"}
INSTRUCTIONS = {
    "tier": "You are the triage advisor for a home AI lab. Decide who should handle this ONE user request (you do not "
            "answer it).",
    "family": "Which family of work is this request?",
    "kind": "Is this request a chat or a job?",
}


def questions(version="v1"):
    return {"tier": {"type": "choice", "instructions": INSTRUCTIONS["tier"], "criteria": QUESTION_VERSIONS[version]},
            "family": {"type": "choice", "instructions": INSTRUCTIONS["family"], "criteria": FAMILY_CRITERIA},
            "kind": {"type": "choice", "instructions": INSTRUCTIONS["kind"], "criteria": KIND_CRITERIA}}


def ask(host, model, req, timeout=600, version="v1"):
    body = json.dumps({"model": model, "state": req, "questions": questions(version)}).encode("utf-8")
    r = urllib.request.Request(host.rstrip("/") + "/v1/systemone", data=body,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.load(resp)


def to_row(item, resp, wall, cold):
    """One graded-shape row: `answer` is the JSON advisor_tasks.grade reads; the typed extras ride beside it."""
    ans = resp.get("answers") or {}
    pick = {k: (ans.get(k) or {}).get("choice") for k in ("tier", "family", "kind")}
    missing = [k for k, v in pick.items() if v is None]
    if missing:
        raise ValueError("systemone answered without %s: %s" % (missing, json.dumps(resp)[:300]))
    return {"id": item["id"], "answer": json.dumps(dict(pick, sure="")), "wall_s": round(wall, 3), "cold": cold,
            "probabilities": {k: (ans.get(k) or {}).get("probabilities") for k in pick},
            "confidence": {k: (ans.get(k) or {}).get("confidence") for k in pick},
            "usage": resp.get("usage")}


def run_model(items, out_dir, model, host, kirk, version="v1"):
    os.makedirs(out_dir, exist_ok=True)
    prog = os.path.join(os.path.dirname(out_dir), "progress")
    os.makedirs(prog, exist_ok=True)
    s = AUP.Sampler(kirk)
    s.start()
    t0, rows, err = time.time(), [], None
    try:
        for i, it in enumerate(items):
            t = time.time()
            resp = ask(host, model, it["req"], version=version)
            rows.append(to_row(it, resp, time.time() - t, cold=(i == 0)))
            open(os.path.join(prog, "%s.%s.done" % (model.replace(":", "_"), it["id"])), "w").close()
            print("[advisor-s1] %s %d/%d %s %.2fs" % (model, i + 1, len(items), it["id"], rows[-1]["wall_s"]),
                  flush=True)
    except (urllib.error.URLError, ValueError, OSError) as e:
        err = "%s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        s.stop.set()
        s.join(5)
    with open(os.path.join(out_dir, "out.json"), "w", encoding="utf-8") as fh:
        json.dump({"model": model, "interface": "systemone", "question": version, "rows": rows}, fh, indent=1)
    cell = {"model": model, "interface": "systemone", "question": version, "ok": err is None and len(rows) == len(items),
            "answered": len(rows), "of": len(items), "error": err, "wall_s": round(time.time() - t0, 1),
            "during": s.summary()}
    print("[advisor-s1] %s ok=%s answered %d/%d wall %.0fs during=%s" % (
        model, cell["ok"], len(rows), len(items), cell["wall_s"], json.dumps(cell["during"])[:160]), flush=True)
    return cell


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("items")
    ap.add_argument("out_dir")
    ap.add_argument("--models", required=True)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--kirk", default=AUP.KIRK)
    ap.add_argument("--question", default="v1", choices=sorted(QUESTION_VERSIONS))
    ap.add_argument("order_name", nargs="?", default="")   # the foundry appends the order's name to every payload
    a = ap.parse_args(argv)
    items = json.load(open(a.items, encoding="utf-8"))
    assert items and all("req" in it and "id" in it for it in items), "items need id + req"
    cells = [run_model(items, os.path.join(a.out_dir, m.replace(":", "_")), m, a.host, a.kirk, version=a.question)
             for m in a.models.split(",")]
    with open(os.path.join(a.out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"cells": cells, "interface": "systemone"}, fh, indent=1)
    return 0 if all(c["ok"] for c in cells) else 1


if __name__ == "__main__":
    sys.exit(main())
