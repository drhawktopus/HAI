"""Test model tool selection across three static tool-block shapes:
A: full tool block per request.
B: core tools + use_tool (selected tools injected late).
C: core tools + find_tools + use_tool (BM25 search over rest of pool).

Usage: tool_shape_probe.py <fixture.json> {ORDER_OUT} --model M --ctx N [--think off|on|xhigh] [--temperature 0.6] [--seed 7] [--limit K]

Scores first real tool call against ground truth label (if present) or agreement with arm A (if no label). Does not execute tools. Writes {ORDER_OUT}\\shape.json after every row."""
import argparse
import json
import math
import os
import re
import sys
import time
import types
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
USE_TOOL ={"type": "function", "function": {
    "name": "use_tool", "description": "Call a tool that was described to you in this conversation, by its exact name, with its arguments.",
    "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "The tool's exact name"},
                                                    "arguments": {"type": "object", "description": "The tool's arguments"}},
                   "required": ["name", "arguments"]}}}
FIND_TOOLS = {"type": "function", "function": {
    "name": "find_tools", "description": "Find tools for a capability you do not see listed. Returns the definitions of the best matches; call them with use_tool.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "What you need to do, in a few plain words"}},
                   "required": ["query"]}}}
SYSTEM_B = ("\n\nBesides the tools listed above, tools chosen for this request are described at the end of the user's message. Call those with use_tool(name, arguments). "
            "If none is described and none is listed, say what you need rather than guessing.")
SYSTEM_C = ("\n\nBesides the tools listed above, many more exist: the media library (TV, movies, music, downloads, requests, Plex), containers and services, home devices, "
            "the machines and the fleet, images and renders, code and library work. Use find_tools(query) to find one, then call it with use_tool(name, arguments).")
WORD = re.compile(r"[a-z0-9]+")


def post(path, body):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=None) as r:         # no request clock (13a): the model answers or the order is stopped
        return json.loads(r.read().decode("utf-8", "replace"))


class Bm25:
    """A small BM25 over each tool's name and description: what a search over tool definitions is, no index or model behind it."""

    def __init__(self, tools):
        self.docs = {}
        for name, t in tools.items():
            f = t.get("function") or {}
            self.docs[name] = WORD.findall((name.replace("_", " ") + " " + (f.get("description") or "")).lower())
        self.avg = sum(len(d) for d in self.docs.values()) / max(1, len(self.docs))
        self.df = {}
        for d in self.docs.values():
            for w in set(d):
                self.df[w] = self.df.get(w, 0) + 1

    def top(self, query, k=5):
        q, scored = WORD.findall(query.lower()), []
        for name, d in self.docs.items():
            s = 0.0
            for w in q:
                tf = d.count(w)
                if tf:
                    idf = math.log(1 + (len(self.docs) - self.df[w] + 0.5) / (self.df[w] + 0.5))
                    s += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(d) / self.avg))
            if s > 0:
                scored.append((s, name))
        return [n for _s, n in sorted(scored, reverse=True)[:k]]


def compact(tools, names):
    return json.dumps([tools[n]["function"] for n in names if n in tools], ensure_ascii=False, separators=(",", ":"))


def run_arm(arm, req, fx, system, a, search):
    tools_all, core = fx["tools"], fx["core"]
    selected = req["selected"]
    text = req["text"]
    if arm == "A":
        block = [tools_all[n] for n in selected if n in tools_all]
        sys_text = system
        if req.get("note"):
            text = text + "\n\n" + req["note"]
    elif arm == "B":
        block = [tools_all[n] for n in core] + [USE_TOOL]
        sys_text = system + SYSTEM_B
        picks = [n for n in selected if n not in core]
        if picks:
            text = text + "\n\n[Tools chosen for this request]\n" + compact(tools_all, picks)
    else:
        block = [tools_all[n] for n in core] + [FIND_TOOLS, USE_TOOL]
        sys_text = system + SYSTEM_C
    msgs = [{"role": "system", "content": sys_text}, {"role": "user", "content": text}]
    row = {"arm": arm, "tool": None, "finds": 0, "steps": 0, "error": "", "first_prompt_tokens": None, "read_s": 0.0, "wall_s": 0.0, "queries": []}
    think = {"off": False, "on": True}.get(a.think, a.think)
    t0 = time.time()
    while row["steps"] < 5:
        row["steps"] += 1
        try:
            if a.strata:
                # the wire the menu's Strata orders and HAI's Flash-Next turns use; the plan's sampler lies under this order's own knobs, as infer_ollama._ask does it
                r = a.infer._chat_openai(a.host, msgs, dict(a.strata_sampler or {}, temperature=a.temperature, seed=a.seed), think=think, tools=block, model=a.model)
            else:
                r = post("/api/chat", {"model": a.model, "messages": msgs, "tools": block, "stream": False, "think": think, "keep_alive": "30m",
                                       "options": {"num_ctx": a.ctx, "temperature": a.temperature, "seed": a.seed}})
        except Exception as e:                                       # noqa: BLE001 - the row says why, the next row still runs
            row["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            break
        row["first_prompt_tokens"] = row["first_prompt_tokens"] or r.get("prompt_eval_count")
        row["read_s"] += (r.get("prompt_eval_duration") or 0) / 1e9
        msg = r.get("message") or {}
        calls = msg.get("tool_calls") or []
        if not calls:
            row["answer"] = (msg.get("content") or "")[:300]
            break
        fn = calls[0].get("function") or {}
        name, args = fn.get("name"), fn.get("arguments") or {}
        if not isinstance(args, dict):                               # the openai wire passes unparseable arguments through as text
            try:
                args = json.loads(args)
            except (TypeError, ValueError):
                args = {}
        if name == "use_tool":
            row["tool"], row["via"] = args.get("name"), "use_tool"
            row["args"] = args.get("arguments")
            break
        if name == "find_tools" and arm == "C":
            row["finds"] += 1
            q = str(args.get("query") or "")
            found = [n for n in search.top(q) if n not in core]
            row["queries"].append({"q": q[:120], "found": found})
            result = compact(tools_all, found) if found else "No tool matched that. Try other words."
            if a.strata:
                call_id = "call_%d" % row["steps"]
                msgs.append({"role": "assistant", "content": msg.get("content") or "",
                             "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
                msgs.append({"role": "tool", "tool_call_id": call_id, "content": result})
            else:
                msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                msgs.append({"role": "tool", "tool_name": "find_tools", "content": result})
            continue
        row["tool"], row["via"], row["args"] = name, "direct", args
        break
    row["wall_s"] = round(time.time() - t0, 2)
    row["read_s"] = round(row["read_s"], 2)
    return row


def verdict(row, req):
    want = req.get("expected")
    if want is None:
        return None
    return (row["tool"] in want) if want else (row["tool"] is None)


def summary(rows):
    out = {}
    for arm in "ABC":
        mine = [r for r in rows if r["arm"] == arm]
        scored = [r for r in mine if r.get("right") is not None]
        real = [r for r in mine if r["kind"] == "real"]
        a_by_text = {r["text"]: r for r in rows if r["arm"] == "A"}
        agree = [r for r in real if r["text"] in a_by_text and r["tool"] == a_by_text[r["text"]]["tool"]]
        out[arm] = {"rows": len(mine), "labelled": len(scored), "right": sum(1 for r in scored if r["right"]),
                    "real": len(real), "agree_with_A": len(agree), "errors": sum(1 for r in mine if r["error"]),
                    "mean_first_prompt_tokens": round(sum(r["first_prompt_tokens"] or 0 for r in mine) / max(1, len(mine))),
                    "mean_read_s": round(sum(r["read_s"] for r in mine) / max(1, len(mine)), 2),
                    "mean_wall_s": round(sum(r["wall_s"] for r in mine) / max(1, len(mine)), 2),
                    "mean_steps": round(sum(r["steps"] for r in mine) / max(1, len(mine)), 2)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    ap.add_argument("out")
    ap.add_argument("--model", required=True)
    ap.add_argument("--ctx", type=int, required=True)
    ap.add_argument("--think", default="off")
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--strata", default=None, help="a serving plan: start Flash-Next through Strata's one lifecycle, in the daily mode a chat window runs in, and stop it by any route")
    a, _rest = ap.parse_known_args()                             # the runner appends the order name: ignore it
    with open(a.fixture, encoding="utf-8") as f:
        fx = json.load(f)
    reqs = fx["requests"][:a.limit] if a.limit else fx["requests"]
    assert reqs and len(fx["core"]) > 5 and fx.get("system"), "the fixture is empty: this would measure nothing"
    search = Bm25({n: t for n, t in fx["tools"].items() if n not in fx["core"]})
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "shape.json")
    if not a.strata:
        return run_all(a, fx, reqs, search, path)
    root = os.environ.get("FOUNDRY_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(root, "bin"))
    import infer_ollama
    a.infer = infer_ollama
    up = types.SimpleNamespace(strata=a.strata, num_ctx=a.ctx, out=path, model=a.model)
    server, placement = infer_ollama._strata_up(up, frontier=False)
    a.host, a.strata_sampler = up.host, up.strata_sampler
    print("[probe] strata %s" % json.dumps(placement)[:300], flush=True)
    try:
        return run_all(a, fx, reqs, search, path)
    finally:
        server[0].stop(server[1], server[2])                     # stop is guaranteed not to raise so it runs in finally


def run_all(a, fx, reqs, search, path):
    rows = []
    total = len(reqs) * 3
    for i, req in enumerate(reqs):
        order = "ABC"[i % 3:] + "ABC"[:i % 3]
        for arm in order:
            row = run_arm(arm, req, fx, fx["system"], a, search)
            row.update(text=req["text"], kind=req.get("kind"), expected=req.get("expected"), selected=req.get("selected"))
            row["right"] = verdict(row, req)
            rows.append(row)
            print("[probe] %d/%d %s %s -> %s right=%s %.1fs" % (len(rows), total, arm, req["text"][:40].replace("\n", " "), row["tool"], row["right"], row["wall_s"]), flush=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"model": a.model, "ctx": a.ctx, "think": a.think, "temperature": a.temperature, "seed": a.seed, "summary": summary(rows), "rows": rows}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
    print("[probe] summary %s" % json.dumps(summary(rows)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
