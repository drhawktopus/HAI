"""Blind head-to-head evaluation: Flash-Next vs 3.8-27B. Method: fixed-seed shuffling; grader sees only A/B labels until grades are finalized."""
import json, random

import os
Q = os.path.dirname(os.path.abspath(__file__))                   # this quality folder
D = os.path.join(os.path.dirname(Q), "delivered")                # the flashnext deliveries beside it
src = {"STRATA": D + r"\flashnext-strata-answers-ram64.20261017-2119_mothership\answers_strata-q2_0-ctx131k.json",
       "27B": D + r"\flashnext-q27b-maxctx.20261002\answers_q27-ctx131k.json"}
plist = json.load(open(Q + r"\prompts-v1.json", encoding="utf-8"))["prompts"]
ptext = {p["id"]: p for p in plist}


def rows(p):
    d = json.load(open(p, encoding="utf-8"))
    return {x["id"]: x for x in d["answers"]}


ans = {k: rows(v) for k, v in src.items()}
assert set(ans["STRATA"]) == set(ans["27B"]) == set(ptext), "both arms must answer every prompt"
rng = random.Random(20261017)
key, out = {}, []
for pid in [p["id"] for p in plist]:
    pair = ["STRATA", "27B"]
    rng.shuffle(pair)
    key[pid] = {"A": pair[0], "B": pair[1]}
    p = ptext[pid]
    out.append("#### %s\nPROMPT: %s\nGRADER KEY: %s\n\n--- ANSWER A ---\n%s\n\n--- ANSWER B ---\n%s\n" % (
        pid, p["prompt"], json.dumps({k: v for k, v in p.items() if k not in ("id", "prompt")}),
        ans[pair[0]][pid]["content"], ans[pair[1]][pid]["content"]))
open(Q + r"\headtohead-v2-blind-pairs.md", "w", encoding="utf-8").write("\n".join(out))
open(Q + r"\headtohead-v2-key.json", "w", encoding="utf-8").write(json.dumps(key))
print("pairs", len(out), "| key written (do not open before grading)")
