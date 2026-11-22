"""Can a small medical model work as an ERRAND helper (LLM-Project\\Subagent-Delegation-Published-Guidance.md; 34-Phase-7 "Two
shapes of delegation"): given a bounded job and a read-only evidence tool, does it look before it answers, and is the answer
right? A compute order's payload, so the load is on the deck. The tool is a real PubMed lookup. usage:
errand_probe.py <items.json> <out.json> --model M --num-ctx N [--think] [--rounds R] [--offload-ok ignored]"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "bin")))
import infer_ollama  # noqa: E402 - the factory's own call: streamed, with the lab's loop rule

BOX = "http://127.0.0.1:11434"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_STEPS = 5   # the tool-loop limit a non-job HAI call runs under (config.TOOL_LOOP_MAX)
TOOL = {"type": "function", "function": {
    "name": "search_evidence",
    "description": "Search PubMed for peer-reviewed articles on a clinical or research question. Returns real citations with abstracts.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
_last = [0.0]


def _get(url):
    wait = 0.4 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "homelab-errand-probe/1.0"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def search(query, n=4):
    try:
        q = urllib.parse.urlencode({"db": "pubmed", "term": query, "retmax": n, "retmode": "json", "sort": "relevance"})
        ids = json.loads(_get(EUTILS + "/esearch.fcgi?" + q))["esearchresult"]["idlist"]
        if not ids:
            return "No PubMed results for that query."
        root = ET.fromstring(_get(EUTILS + "/efetch.fcgi?" + urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})))
        out = []
        for a in root.findall(".//PubmedArticle"):
            pmid = a.findtext(".//PMID") or ""
            title = "".join(a.find(".//ArticleTitle").itertext()) if a.find(".//ArticleTitle") is not None else ""
            year = a.findtext(".//PubDate/Year") or a.findtext(".//PubDate/MedlineDate") or ""
            first = a.findtext(".//Author/LastName") or ""
            abstract = " ".join("".join(x.itertext()) for x in a.findall(".//AbstractText"))
            out.append("PMID %s | %s (%s) | %s\nAbstract: %s" % (pmid, first, year, title, abstract[:1500]))
        return "\n\n".join(out) or "No PubMed results for that query."
    except Exception as exc:  # noqa: BLE001
        return "search failed: %s" % type(exc).__name__


def chat(model, messages, tools, num_ctx, think, seed):
    """The lab's own streamed call: ended by the loop rule or silence, never a clock (first run: one turn ran an hour)."""
    return infer_ollama._chat(BOX, model, messages, {"num_ctx": num_ctx, "seed": seed}, think=think, tools=tools, keep_alive="5m")


def run_item(item, a, seed):
    msgs = [{"role": "system", "content": item["system"]}, {"role": "user", "content": item["prompt"]}]
    t0, queries, last, stopped = time.time(), [], {}, None
    for step in range(TOOL_STEPS + 1):
        try:
            last = chat(a.model, msgs, [TOOL] if step < TOOL_STEPS else None, a.num_ctx, a.think, seed)
        except Exception as exc:  # noqa: BLE001 - one row, recorded
            stopped = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            break
        if last.get("loop"):
            stopped = "loop"
            break
        m = last.get("message") or {}
        calls = m.get("tool_calls") or []
        if not calls:
            break
        msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        for c in calls:
            q = str(((c.get("function") or {}).get("arguments") or {}).get("query") or "")
            queries.append(q)
            msgs.append({"role": "tool", "content": search(q)})
    m = last.get("message") or {}
    text = re.sub(r"(?s)<think>.*?</think>", "", m.get("content") or "").strip()
    return {"id": item["id"], "answer": text, "queries": queries, "steps": len(queries), "wall_s": round(time.time() - t0, 1), "stopped": stopped,
            "done_reason": last.get("done_reason"), "prompt_eval_count": last.get("prompt_eval_count"),
            "eval_count": last.get("eval_count"), "thinking_chars": len(m.get("thinking") or "")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("items")
    ap.add_argument("out")
    ap.add_argument("--model", required=True)
    ap.add_argument("--num-ctx", type=int, required=True)
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--offload-ok", action="store_true")
    a, _ = ap.parse_known_args()      # the factory appends the order's name
    items = json.load(open(a.items, encoding="utf-8"))["items"]
    rows = []
    for r in range(a.rounds):
        for it in items:
            row = run_item(it, a, 7 + r)
            row["round"] = r + 1
            rows.append(row)
            json.dump({"model": a.model, "rows": rows}, open(a.out, "w", encoding="utf-8"))
            print("[errand] %s r%d %.0f s, %d lookups" % (row["id"], r + 1, row["wall_s"], row["steps"]), flush=True)
    ps = json.loads(urllib.request.urlopen(BOX + "/api/ps", timeout=15).read().decode())
    placed = next((m for m in ps.get("models", []) if m.get("name") == a.model), {})
    json.dump({"model": a.model, "rows": rows, "placement": {"size": placed.get("size"), "size_vram": placed.get("size_vram")}},
              open(a.out, "w", encoding="utf-8"))


if __name__ == "__main__":
    main()
