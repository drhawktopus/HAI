"""Generate plans for Strata vs llama.cpp comparison.

Strata arm omits warm-up prompts to avoid cache flattening; plateau is the median of the last 5 prompts."""
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, "plans")

mmap = json.load(open(os.path.join(P, "validate-mmap.20261001.json"), encoding="utf-8"))
ten = [a for a in mmap["arms"] if a["name"] == "MTP-t0-mmap-10p"][0]
assert len(ten["prompts"]) == 10, "the documented 10-prompt arm changed"

strata = json.load(open(os.path.join(P, "strata-q2_0.20261004.json"), encoding="utf-8"))
strata = copy.deepcopy(strata)
strata["note"] = ("Alex 2026-10-04 ('we do want numbers for strata'): Flash-Next Q2_0 on Strata to its plateau - no "
                  "repeated-prompt gens (repeats flatter a cache), the documented 10 varied prompts of "
                  "validate-mmap.20261001 MTP-t0-mmap-10p; plateau = median of the last 5. Control: "
                  "strata-plateau-control.20261004.json, same prompts, run right after.")
strata["gens"] = 0
strata["prompts"] = list(ten["prompts"])
json.dump(strata, open(os.path.join(P, "strata-plateau.20261004.json"), "w", encoding="utf-8"), indent=1)

ctl = copy.deepcopy(ten)
ctl["name"] = "MMAP-CTL-plateau"
plan = {"note": ("Alex 2026-10-04: same-day control for strata-plateau.20261004 - the documented best varied llama.cpp "
                 "cook, MTP-t0-mmap-10p from validate-mmap.20261001 (doc plateau ~12-14 tok/s), unchanged, on the same "
                 "10 prompts; plateau = median of the last 5."),
        "arms": [ctl]}
for k in ("unattended",):
    if k in mmap:
        plan[k] = mmap[k]
json.dump(plan, open(os.path.join(P, "strata-plateau-control.20261004.json"), "w", encoding="utf-8"), indent=1)
print("strata prompts", len(strata["prompts"]), "gens", strata["gens"], "| control arm", ctl["name"], "prompts",
      len(ctl["prompts"]), "load", ctl.get("load"), "bin", ctl["bin"])
