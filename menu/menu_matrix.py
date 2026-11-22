"""#34 the MODEL MATRIX - every config the priced menu measured, one row each, on the lab's own rules.

    python menu_matrix.py # writes model-matrix.csv + model-matrix.md beside policy.json
    python menu_matrix.py --out-dir DIR # elsewhere (tests, scratch)

the owner "a multi axial matrix where every model on every winning config has a place for quality, speed, cost
like the 3.6 35B A3B ranked for each host box it lives on... almost a point cloud of dots for where each combo/config
sits". This is that table, built as a THIRD output of the menu chain (menu_standings -> menu_policy -> menu_matrix), so it
refreshes with the menu and never needs Claude.

ONE DERIVATION, not a second one (): a config is ACCEPTED or REFUSED by menu_policy.classify (the
filler's and the owner's stop verdicts, VOID, oracle, never-solved, thinking-off-loses), is priced by menu_policy.entries
(rigby's energy at its placement watts), carries the same stamps Spock's table carries (cold load, re-read, desk-order
overhead and its joules, first visible output), and is ranked with menu_policy.LENS_KEYS - the keys that pick each box's
winner in policy.json. So a rank here and a placement there cannot disagree.

THREE LENSES, NEVER A FOURTH (checklist A2): quality = the 95% Wilson LOWER BOUND of the solve rate (B4), speed =
seconds per SOLVED answer (failures charged to the solves), cost = joules per solved answer (literal electricity, B3).
No combined score is made: a config has a rank on each lens and a Pareto flag (nothing else on that box and job is at
least as good on all three and better on one). The fastest lens only ranks configs inside the interactive line (10x the
family's fleet-fastest solve), exactly as policy.json does.

THE UNIT IS THE (box, job, config). A model appears once per setting measured (thinking, temperature, build). The
`model_best_*` columns flag a model's best config per lens on a box and job, which is the "winning config" view.
Quality is only comparable WITHIN a job: each job has its own test, and many settings sit at 100%.

MODEL IDENTITY is by NAME (the IDENTITY table), not by weights hash: the same model is listed under an ollama tag on a
GPU box and a.gguf file on rigby, and the Fellowship rows carry the tablet class and a warm flag inside the name. A
model string the table does not know keeps its raw name and is counted as `unmapped` in the log and the page header
(fail OPEN so a new model never stops the refresh, LOUD so it is not missed).

Not in this table: the embed family (one model everywhere, priced separately in policy.json) and the A1 role/lead rows
(`roles` in policy.json)."""
import collections
import csv
import datetime
import hashlib
import os
import re
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                       # noqa: BLE001
        pass

import menu_policy as P  # noqa: E402 - the lab's derivation, imported, never copied

MENU = os.path.dirname(P.STANDINGS)
CHAT1 = os.path.join(MENU, "chat1_standings.csv")
STOCK = "stock tag"

# raw model string (lower case, class/warm suffix removed) -> (model, build). By NAME: see the module docstring.
IDENTITY = {
    "gemma3:27b": ("Gemma 3 27B", STOCK),
    "hf.co/openbmb/minicpm-v-4.6-gguf:q4_k_m": ("MiniCPM-V 4.6", "Q4_K_M (hf.co openbmb)"),
    "minicpm-v:4.6-q4km": ("MiniCPM-V 4.6", "Q4_K_M (ollama tag)"),
    "ii-medical:8b-q4km": ("II-Medical 8B", "Q4_K_M"),
    "minicpm5-1b-q4_k_m.gguf": ("MiniCPM5 1B", "Q4_K_M"),
    "minicpm5:1b-q4km": ("MiniCPM5 1B", "Q4_K_M"),
    "nemotron-3-nano:30b-iq4xs": ("Nemotron-3-Nano 30B", "IQ4_XS"),
    "nemotron-3-nano:4b": ("Nemotron-3-Nano 4B", STOCK),
    "nomic-embed-text-v1.5-f16.gguf": ("nomic-embed-text 1.5", "F16"),
    "ornith-1.5:35b-a3b-q4km": ("Ornith 1.5 35B-A3B", "Q4_K_M"),
    "qwen2.5-0.5b-instruct-q4_k_m.gguf": ("Qwen2.5 0.5B Instruct", "Q4_K_M"),
    "qwen2.5-1.5b-instruct-q4_k_m": ("Qwen2.5 1.5B Instruct", "Q4_K_M"),
    "qwen2.5-1.5b-instruct-q4_k_m.gguf": ("Qwen2.5 1.5B Instruct", "Q4_K_M"),
    "qwen2.5-7b-instruct-q4_k_m.gguf": ("Qwen2.5 7B Instruct", "Q4_K_M"),
    "qwen2.5-14b-instruct-q4_k_m.gguf": ("Qwen2.5 14B Instruct", "Q4_K_M"),
    "qwen2.5-coder-0.5b-instruct-q4_k_m": ("Qwen2.5-Coder 0.5B Instruct", "Q4_K_M"),
    "qwen2.5-coder-0.5b-instruct-q4_k_m.gguf": ("Qwen2.5-Coder 0.5B Instruct", "Q4_K_M"),
    "qwen2.5-coder-1.5b-instruct-q4_k_m": ("Qwen2.5-Coder 1.5B Instruct", "Q4_K_M"),
    "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf": ("Qwen2.5-Coder 1.5B Instruct", "Q4_K_M"),
    "qwen2.5-coder-3b-instruct-q4_k_m.gguf": ("Qwen2.5-Coder 3B Instruct", "Q4_K_M"),
    "qwen3-coder:30b": ("Qwen3-Coder 30B", STOCK),
    "qwen3-vl:8b": ("Qwen3-VL 8B", STOCK),
    "qwen3.5-0.8b-q4_k_m": ("Qwen3.5 0.8B", "Q4_K_M"),
    "qwen3.5-2b-q4_k_m": ("Qwen3.5 2B", "Q4_K_M"),
    "qwen3.5:9b": ("Qwen3.5 9B", STOCK),
    "qwen3.6-35b-a3b-mtp-ud-q3_k_xl.gguf": ("Qwen3.6 35B-A3B", "UD-Q3_K_XL + MTP"),
    "qwen3.6-35b-a3b-ud-q3_k_xl.gguf": ("Qwen3.6 35B-A3B", "UD-Q3_K_XL"),
    "qwen3.6:27b": ("Qwen3.6 27B", STOCK),
    "qwen3.6:35b-a3b": ("Qwen3.6 35B-A3B", STOCK),
    "qwen3.6:35b-a3b-ud-q3kxl-mtp": ("Qwen3.6 35B-A3B", "UD-Q3_K_XL + MTP"),
    "qwen3.8-27b-q4_k_m.gguf": ("Qwen3.8 27B", "Q4_K_M"),
    "qwen3.8-flash-next-strata-q2_0": ("Qwen3.8 Flash-Next", "Strata Q2_0"),
    "qwen3.8:27b": ("Qwen3.8 27B", STOCK),
    "qwen3.8:27b-mtp-q4_k_m": ("Qwen3.8 27B", "Q4_K_M + MTP"),
    "qwen3:14b": ("Qwen3 14B", STOCK),
    "qwen3:8b": ("Qwen3 8B", STOCK),
    "smolvlm2-500m (1x images)": ("SmolVLM2 500M", "1x images"),
}
# the Fellowship's chat-turn rows put the tablet class and a warm flag inside the model string:
# "Qwen3.5-2B-Q4_K_M @class A (warm)"
SUFFIX = re.compile(r"^(?P<base>.*?)(?: @class (?P<cls>\S+))?(?P<warm> \(warm\))?$")

BOX_ORDER = ("mothership", "bighonker", "softserve", "rigby", "fellowship")
LENS_COL = {"best_quality": "quality", "fastest": "speed", "least_energy": "cost"}

COLUMNS = ["model", "build", "box", "tiers", "family", "think", "temperature", "tablet_class", "warm", "n", "seeds",
           "quality", "quality_low", "spread_min", "spread_max", "s_per_solve", "first_out_s", "j_per_solve", "j_source",
           "j_order", "load_s", "read_tok_s", "order_overhead_s", "status", "accepted", "why_not",
           "rank_quality", "of_quality", "rank_speed", "of_speed", "rank_cost", "of_cost", "model_best_quality",
           "model_best_speed", "model_best_cost", "pareto", "shape", "as_measured", "caveats"]


def identity(raw):
    """{model, build, as_measured, tablet_class, warm, mapped} for a standings model string."""
    m = SUFFIX.match(raw or "")
    base = (m.group("base") if m else raw) or ""
    hit = IDENTITY.get(base.strip().lower())
    return {"model": hit[0] if hit else base.strip(), "build": hit[1] if hit else "unmapped", "as_measured": raw,
            "tablet_class": (m.group("cls") or "") if m else "", "warm": "yes" if m and m.group("warm") else "",
            "mapped": hit is not None}


def tiers_of(box):
    return [t for t, homes in P.TIER_HOMES.items() if box in homes]


def _ranks(pool, key):
    """{id(e): competition rank} - equal keys share a rank."""
    out, prev, pos = {}, object(), 0
    for i, e in enumerate(sorted(pool, key=key), 1):
        k = key(e)
        if k != prev:
            pos, prev = i, k
        out[id(e)] = pos
    return out


def _dominates(a, b):
    ka = (a["quality_low"], -a["s_per_solve"], -a["j_per_solve"])
    kb = (b["quality_low"], -b["s_per_solve"], -b["j_per_solve"])
    return all(x >= y for x, y in zip(ka, kb)) and ka != kb


def build_matrix(rows, stops, stamp=None, spreads=None):
    """One dict per input row - accepted and refused alike. `stamp(pseudo_policy)` adds the price stamps (None = none)."""
    verdicts = P.classify(rows, stops)
    every = []
    for fam, (acc, refused, _ff) in verdicts.items():
        for e in acc:
            e["accepted"], e["why_not"] = True, ""
        for e in refused:
            e["accepted"], e["why_not"] = False, e.get("why", "")
        every += acc + refused
    assert len(every) == len(rows), "classify lost or invented rows: %d in, %d out" % (len(rows), len(every))
    if stamp:
        stamp({"matrix": {"tiers": {"matrix": {"all": every}}}})        # the stamps' own shape: policy[fam].tiers[t][lens]
    spreads = spreads or {}
    for e in every:
        sp = spreads.get((e["box"], e["family"], e["shape"], e["model"], e["think"], e["temperature"])) or {}
        e["seeds"], e["spread_min"], e["spread_max"] = sp.get("seeds", ""), sp.get("spread_min", ""), sp.get("spread_max", "")
        e.update(identity(e["model"]))
        e["tiers"] = "+".join(tiers_of(e["box"]))
        est = [c for c in e.get("caveats", []) if c.startswith("energy = wall x")]
        e["j_source"] = "estimated" if est else ("measured" if e["j_per_solve"] is not None else "")
        for k in ("rank_quality", "of_quality", "rank_speed", "of_speed", "rank_cost", "of_cost",
                  "model_best_quality", "model_best_speed", "model_best_cost", "pareto"):
            e[k] = ""

    pools = collections.defaultdict(list)
    for e in every:
        if e["accepted"]:
            pools[(e["box"], e["family"])].append(e)
    for (box, fam), es in pools.items():
        line = P.interactive_line(verdicts[fam][2])
        per_lens = {
            "fastest": [e for e in es if line is None or e["s_per_solve"] <= line],
            "least_energy": [e for e in es if e["j_per_solve"] is not None],
            "best_quality": es}
        for lens, pool in per_lens.items():
            r = _ranks(pool, P.LENS_KEYS[lens])
            for e in pool:
                e["rank_" + LENS_COL[lens]] = r[id(e)]
                e["of_" + LENS_COL[lens]] = len(pool)
            by_model = collections.defaultdict(list)
            for e in pool:
                by_model[e["model"]].append(e)
            for mem in by_model.values():
                best = min(P.LENS_KEYS[lens](e) for e in mem)
                for e in mem:
                    if P.LENS_KEYS[lens](e) == best:
                        e["model_best_" + LENS_COL[lens]] = "yes"
        full = [e for e in es if e["j_per_solve"] is not None]
        for e in full:
            e["pareto"] = "no" if any(_dominates(o, e) for o in full if o is not e) else "yes"
    order = {b: i for i, b in enumerate(BOX_ORDER)}
    every.sort(key=lambda e: (e["model"].lower(), e["build"], order.get(e["box"], 9), e["family"],
                              e["rank_quality"] if e["rank_quality"] != "" else 1e9))
    return every


def load_rows(standings=P.STANDINGS, chat1=CHAT1):
    with open(standings, "rb") as f:
        raw = f.read()
    base = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    assert base, "zero standings rows - the source is broken, not empty"
    rows = list(base)
    if os.path.exists(chat1):
        with open(chat1, encoding="utf-8") as f:
            rows += list(csv.DictReader(f))
    spreads = {(r["box"], r["family"], r["shape"], r["model"], r["think"], r["temperature"]): r for r in base}
    return rows, spreads, hashlib.sha256(raw).hexdigest()[:16]


def real_stamp(policy):
    """The stamps menu_policy.main applies, in its order, from the same readers."""
    oh = P.fellowship_overheads()
    P.stamp_load(policy, P.load_prices())
    reads = P.read_prices()
    reads.update(P.rigby_read_prices())
    P.stamp_read(policy, reads)
    P.stamp_fellowship(policy, oh)
    P.stamp_first_output(policy, P.first_outputs())


def _num(x, nd=4):
    return "" if x is None or x == "" else (round(x, nd) if isinstance(x, float) else x)


def write_csv(matrix, path):
    with open(path + ".tmp", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for e in matrix:
            row = dict(e, quality=e["quality"], caveats=" | ".join(e.get("caveats", [])),
                       accepted="yes" if e["accepted"] else "no")
            w.writerow({c: _num(row.get(c)) for c in COLUMNS})
    os.replace(path + ".tmp", path)


def _secs(s):
    return "%.1f" % s if s < 10 else "%.0f" % s


def _cfg(e, col):
    think = "think %s" % e["think"] if e["think"] not in ("", "n/a", "default") else ""
    temp = "t%s" % e["temperature"] if e["temperature"] not in ("", None) else ""
    j = "—" if e["j_per_solve"] is None else "%s%.0f J" % ("~" if e["j_source"] == "estimated" else "", e["j_per_solve"])
    rk = ("#%s of %s" % (e["rank_" + col], e["of_" + col])) if e["rank_" + col] != "" else ""
    return "%s · %.0f%% (≥%.0f%%) · %s s · %s%s%s" % (
        " ".join(x for x in (think, temp, e["tablet_class"] and "class " + e["tablet_class"]) if x) or "default",
        100 * e["quality"], 100 * e["quality_low"], _secs(e["s_per_solve"]), j, " · " if rk else "", rk)


def write_md(matrix, path, src_sha, now):
    acc = [e for e in matrix if e["accepted"]]
    unm = sorted({e["as_measured"] for e in matrix if not e["mapped"]})
    est = sum(1 for e in acc if e["j_source"] == "estimated")
    nocost = sum(1 for e in acc if e["j_per_solve"] is None)
    md = ["# Project 34 model matrix — every model, on every box it lives on, by job (generated by `hard-bench\\menu_matrix.py`)",
          "",
          "Built %s from `standings.csv` (sha %s) with the placement table's own rules: %d configs measured, %d on the menu, "
          "%d refused (stopped, void, never solved, or thinking-off loses). The full point cloud is `model-matrix.csv` "
          "(one row per config, every number, every rank)." % (now, src_sha, len(matrix), len(acc), len(matrix) - len(acc)),
          "",
          "**How to read a cell.** For one model on one box and one job, the three winning configs, one per lens: "
          "*best quality* = highest 95% lower bound of the solve rate, *fastest* = fewest seconds per solved answer, "
          "*least energy* = fewest joules per solved answer. A line reads `thinking · temperature · success (lower "
          "bound) · seconds per solve · joules per solve · rank among the configs ranked on that lens for that box and job`. "
          "`~` = joules estimated from seconds at the box's metered watts; `—` = no energy figure. The three lenses are "
          "never merged into one score.",
          "",
          "**Read with care.** Compare quality only within one job. Many settings sit at 100%%, so the lower bound and the "
          "other two lenses do the separating. A model's name is matched by name, not by weights: %s. "
          "%d menu configs have no joules figure (%d are estimates)." % (
              ("every name is mapped" if not unm else "UNMAPPED, shown under their raw name: " + ", ".join(unm)),
              nocost, est),
          ""]
    groups = collections.defaultdict(list)
    for e in matrix:
        groups[e["model"]].append(e)
    md += ["## Index", "", "| model | builds | boxes | jobs it is on the menu for |", "|---|---|---|---|"]
    for model in sorted(groups, key=str.lower):
        es = groups[model]
        a = [e for e in es if e["accepted"]]
        md.append("| %s | %s | %s | %s |" % (
            model, ", ".join(sorted({e["build"] for e in es})),
            ", ".join(b for b in BOX_ORDER if any(e["box"] == b for e in es)),
            ", ".join(sorted({e["family"] for e in a})) or "none"))
    md.append("")
    for model in sorted(groups, key=str.lower):
        es = groups[model]
        md += ["## %s" % model, ""]
        md += ["| box · job | best quality | fastest | least energy |", "|---|---|---|---|"]
        places = sorted({(e["box"], e["family"]) for e in es}, key=lambda p: (BOX_ORDER.index(p[0]) if p[0] in BOX_ORDER else 9, p[1]))
        for box, fam in places:
            here = [e for e in es if e["box"] == box and e["family"] == fam]
            ok = [e for e in here if e["accepted"]]
            if not ok:
                why = collections.Counter(e["why_not"].split(":")[0][:60] for e in here).most_common(1)[0][0]
                md.append("| %s · %s | not on the menu: %s (%d configs) | | |" % (box, fam, why, len(here)))
                continue
            cells = []
            for col in ("quality", "speed", "cost"):
                w = [e for e in ok if e["model_best_" + col] == "yes"]
                if not w:
                    cells.append({"speed": "none inside the interactive line", "cost": "no energy figure"}.get(col, "—"))
                    continue
                e = w[0]
                tag = "" if len({x["build"] for x in ok}) == 1 else "%s: " % e["build"]
                cells.append(tag + _cfg(e, col))
            refused = len(here) - len(ok)
            md.append("| %s · %s%s | %s | %s | %s |" % (box, fam, " (+%d refused)" % refused if refused else "", *cells))
        md.append("")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    os.replace(path + ".tmp", path)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out_dir = argv[argv.index("--out-dir") + 1] if "--out-dir" in argv else MENU
    rows, spreads, sha = load_rows()
    stops = P.load_stops()
    assert stops, "zero stop verdicts read - the hold files are unreadable, not empty"
    matrix = build_matrix(rows, stops, stamp=real_stamp, spreads=spreads)
    acc = [e for e in matrix if e["accepted"]]
    assert acc, "no config accepted - the verdicts are broken, not the menu"
    assert len(matrix) == len(rows), "a standings row is missing from the matrix"
    joined = sum(1 for e in matrix if e["seeds"] != "")
    assert joined, "the spread join matched ZERO rows - the key is broken"
    write_csv(matrix, os.path.join(out_dir, "model-matrix.csv"))
    write_md(matrix, os.path.join(out_dir, "model-matrix.md"), sha, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    unm = sorted({e["as_measured"] for e in matrix if not e["mapped"]})
    print("model matrix: %d configs (%d on the menu, %d refused), %d models, %d with a seed spread" % (
        len(matrix), len(acc), len(matrix) - len(acc), len({e["model"] for e in matrix}), joined))
    print("stamps: load_s %d, read_tok_s %d, first_out_s %d, order_overhead_s %d rows" % tuple(
        sum(1 for e in matrix if e.get(k) is not None) for k in ("load_s", "read_tok_s", "first_out_s", "order_overhead_s")))
    print("energy: %d measured, %d estimated, %d none (menu configs)" % tuple(
        sum(1 for e in acc if e["j_source"] == k) for k in ("measured", "estimated", "")))
    print("UNMAPPED model names: %s" % (", ".join(unm) if unm else "none"))
    print("written", os.path.join(out_dir, "model-matrix.csv"), "+ model-matrix.md")


if __name__ == "__main__":
    main()
