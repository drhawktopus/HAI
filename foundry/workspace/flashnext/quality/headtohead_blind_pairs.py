import json, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(os.path.dirname(HERE), "delivered")          # the workspace's delivered orders, beside this folder
SP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "blind-pairs")   # where the pairs and their key land
os.makedirs(SP, exist_ok=True)
src = {"FN": D + r"\flashnext-fn-answers.20261002\answers_fn-q2k-ctx131k.json",
       "27B": D + r"\flashnext-q27b-maxctx.20261002\answers_q27-ctx131k.json"}
prompts = json.load(open(os.path.join(HERE, "prompts-v1.json"), encoding="utf-8"))
plist = prompts.get("prompts", prompts) if isinstance(prompts, dict) else prompts
ptext = {p["id"]: p for p in plist}


def rows(p):
    d = json.load(open(p, encoding="utf-8"))
    r = d.get("rows") or d.get("answers") or d.get("results") or d
    return {x["id"]: x for x in (r if isinstance(r, list) else r.values())}


ans = {k: rows(v) for k, v in src.items()}
rng = random.Random(20261002)
key, out = {}, []
for pid in sorted(ans["FN"]):
    pair = ["FN", "27B"]
    rng.shuffle(pair)
    key[pid] = {"A": pair[0], "B": pair[1]}
    p = ptext.get(pid, {})
    out.append("#### %s\nPROMPT: %s\nGRADER KEY: %s\n\n--- ANSWER A ---\n%s\n\n--- ANSWER B ---\n%s\n" % (
        pid, p.get("prompt") or p.get("text"), json.dumps({k: v for k, v in p.items() if k not in ("id", "prompt", "text")}),
        ans[pair[0]][pid]["content"], ans[pair[1]][pid]["content"]))
open(SP + r"\blind_pairs.md", "w", encoding="utf-8").write("\n".join(out))
open(SP + r"\blind_key.json", "w", encoding="utf-8").write(json.dumps(key))
print("pairs", len(out), "chars", sum(len(o) for o in out))
