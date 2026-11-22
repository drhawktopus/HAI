import copy
import datetime
import json
import os
import sys
import time
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
HERE = os.path.dirname(os.path.abspath(__file__))


def post(path, body, timeout=1800):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def chat(item, messages, tools):
    body = {"model": item["model"], "messages": messages, "stream": False, "think": item.get("think", False),
            "options": dict(item.get("options") or {}, num_predict=48, temperature=0, seed=1)}
    if tools:
        body["tools"] = tools
    t0 = time.time()
    r = post("/api/chat", body)
    return r, {"read_s": round((r.get("prompt_eval_duration") or 0) / 1e9, 3), "prompt_tokens": r.get("prompt_eval_count"),
               "wall_s": round(time.time() - t0, 2)}


def main():
    out = [a for a in sys.argv[1:] if not a.startswith("--")][0]
    item_p = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--item=")), os.path.join(HERE, "turn2_item.json"))
    os.makedirs(out, exist_ok=True)
    item = json.load(open(item_p, encoding="utf-8"))
    base, tools = item["messages"], item.get("tools") or []
    res = {"item": os.path.basename(item_p), "model": item["model"], "tools": len(tools), "start":
           datetime.datetime.now().isoformat(timespec="seconds"), "arms": {}}

    post("/api/generate", {"model": item["model"], "keep_alive": 0}, timeout=120)        # cold: nothing cached
    time.sleep(3)
    r0, res["cold_base"] = chat(item, base, tools)
    answer = (r0.get("message") or {}).get("content") or "ok"

    sel_i = next((i for i, m in enumerate(base) if m["role"] == "system" and "[TOOL SELECTION]" in m["content"]), None)
    variants = {"identical": (base, tools), "tools_order": (base, list(reversed(tools))), "tools_dropped": (base, tools[:-1])}
    if sel_i is not None:
        m = copy.deepcopy(base)
        m[sel_i]["content"] = m[sel_i]["content"].replace("The best matches", "The closest matches")
        variants["selection_msg"] = (m, tools)
    variants["append_turn"] = (base + [{"role": "assistant", "content": answer},
                                       {"role": "user", "content": "And one more setting, in one sentence?"}], tools)
    hai = [copy.deepcopy(x) for x in base if x["role"] == "system"]
    if sel_i is not None:
        hai[-1]["content"] = hai[-1]["content"].replace("The best matches", "The closest matches")
    hai += [{"role": "assistant", "content": answer}, {"role": "user", "content": "And one more setting, in one sentence?"}]
    variants["hai_next_turn"] = (hai, tools[:-1])

    for name, (msgs, tls) in variants.items():
        chat(item, base, tools)                       # prime: the base is the cache's content
        _, res["arms"][name] = chat(item, msgs, tls)
        print("[prefix] %-14s read %6.3f s  prompt %s" % (name, res["arms"][name]["read_s"],
                                                          res["arms"][name]["prompt_tokens"]), flush=True)
    res["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print("[prefix] RESULT %s" % json.dumps(res), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
