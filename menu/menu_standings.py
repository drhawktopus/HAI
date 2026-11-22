"""Project 34 menu - standings per box and job family, on the Shape Catalogue's scorecard, from the big table.

One setting = shape · box · model · thinking · temperature (an accuracy never blends two settings). Per setting:
n usable rows, success, spread (min..max of the per-seed success, never a bare mean), median seconds per answer,
seconds per SOLVED task, and GPU joules per solved task where the box meters energy. Per box and family the page
names three settings, because placement trades them by urgency: the best quality, the fastest per solve, the least energy per solve.
A setting is PRELIMINARY until it holds on >= 2 task families on that box and reproduces (>= 2 seeds) - the
catalogue's status rule. Controls, void rows (cap or method) and VOID windows are excluded; priced-out rows count as
not solved (a measured cost). Writes Datasets\\Project34-Menu\\standings.md + standings.csv. Submits nothing."""
import collections
import csv
import os
import statistics

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_paths as MPATH  # noqa: E402
MENU = MPATH.MENU
FAMILIES = ["coding-f1", "coding-f2", "work-w1", "chat-c1", "tool", "vlm", "vlm-2x"]
BOX_ORDER = MPATH.box_order()


import math
import re

# Specialists are priced ONLY on the job they would do: their generic
# coding / tool / vision cells are held, so they never stand in these generic families.
SPECIALIST = re.compile(r"joycaption|medgemma|abliterated|huihui|glm-ocr", re.I)
# build_menu.format_failure writes "format: ..."; a grade that ran the answer reports SyntaxError / NameError
FORMAT_FAIL = re.compile(r"^format:|SyntaxError|NameError|IndentationError")
from menu_fill import FIT_VOID_SHARE as CANNOT_FIT_SHARE  # noqa: E402 - one cannot-fit line for the filler and the table


def wilson_low(k, n, z=1.96):
    """Lower bound of the 95% Wilson interval: ranks a success rate with its sample size (n 10 at 100% < n 120 at 92%)."""
    if not n:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load():
    rows = list(csv.DictReader(open(os.path.join(MENU, "menu-cells.csv"), encoding="utf-8")))
    assert rows, "empty table"
    scope = [r for r in rows if r["shape"] not in ("control",) and r["family"] in FAMILIES and r["shape"] != "S2"
             and r["box"] in BOX_ORDER and not SPECIALIST.search(r["model"] or "")]
    keep = [r for r in scope if r["usable"] not in ("False", "false")]
    assert keep, "no usable single-attempt rows in the standings families"
    # rows that ended at the context: left out of success, but COUNTED () - a setting whose answers mostly
    # hit the cap scores only its survivors
    at_cap = collections.Counter(setting_key(r) for r in scope if r["void"] == "cap")
    return keep, at_cap


def answer_protocol(r):
    """A task answered as `ANSWER = ...` (W1 reasoning/ops, C1) - where a parse failure is format, not a wrong program."""
    return r["family"] == "chat-c1" or (r["family"] == "work-w1" and not str(r["task"]).startswith("code_"))


def setting_key(r):
    return (r["box"], r["family"], r["shape"], r["model"], r["think"], r["temperature"])


def settings(rows, at_cap=None):
    at_cap = at_cap or {}
    by = collections.defaultdict(list)
    for r in rows:
        by[setting_key(r)].append(r)
    out = []
    for (box, fam, shape, model, think, temp), rs in by.items():
        ok = [str(r["ok"]).lower() in ("true", "1") for r in rs]
        solved = sum(ok)
        walls = [num(r["wall_s"]) for r in rs if num(r["wall_s"]) is not None]
        js = [num(r["energy_j"]) for r in rs if num(r["energy_j"]) is not None]
        seeds = collections.defaultdict(list)
        for r, k in zip(rs, ok):
            seeds[r["seed"] or r["replicate"] or "?"].append(k)
        per_seed = [sum(v) / len(v) for v in seeds.values() if v]
        out.append({
            "box": box, "family": fam, "shape": shape, "model": model, "think": think, "temperature": temp,
            "n": len(rs), "success": solved / len(rs), "seeds": len(per_seed), "wilson": wilson_low(solved, len(rs)),
            "spread": (min(per_seed), max(per_seed)) if per_seed else (None, None),
            "priced_out": sum(1 for r in rs if str(r["priced_out"]).lower() == "true"),
            # not solved because the answer did not parse or run as an answer - reported apart from a wrong answer
            # only on ANSWER-protocol tasks: in a program (coding families, W1 code_*) a SyntaxError/NameError is a bug
            "format_fail": sum(1 for r, k in zip(rs, ok) if not k and answer_protocol(r)
                               and FORMAT_FAIL.search(str(r.get("error") or ""))),
            "med_s": statistics.median(walls) if walls else None,
            "s_per_solve": (sum(walls) / solved) if solved and len(walls) == len(rs) else None,
            "j_per_solve": (sum(js) / solved) if solved and len(js) == len(rs) else None,
            "at_cap": at_cap.get((box, fam, shape, model, think, temp), 0),
        })
    families_on_box = collections.defaultdict(set)
    for s in out:
        if s["n"]:
            families_on_box[(s["box"], s["model"], s["think"], s["temperature"])].add(s["family"].replace("-2x", ""))
    for s in out:
        fams = families_on_box[(s["box"], s["model"], s["think"], s["temperature"])]
        coding = {"coding-f1", "coding-f2"} & fams
        nfam = len(fams - coding) + (2 if len(coding) == 2 else len(coding))
        s["status"] = "holds" if nfam >= 2 and s["seeds"] >= 2 else "PRELIMINARY"
        cap_share = s["at_cap"] / (s["n"] + s["at_cap"]) if s["n"] + s["at_cap"] else 0.0
        if cap_share >= CANNOT_FIT_SHARE:
            s["status"] = "VOID: cannot fit (%d of %d at the cap)" % (s["at_cap"], s["n"] + s["at_cap"])
    return out


def fmt(x, f="%.1f"):
    return "—" if x is None else f % x


def label(s):
    return "%s (think %s, t%s)" % (s["model"], s["think"], s["temperature"])


def main():
    st = settings(*load())
    with open(os.path.join(MENU, "standings.csv"), "w", newline="", encoding="utf-8") as f:
        cols = ["box", "family", "shape", "model", "think", "temperature", "n", "seeds", "success", "spread_min",
                "spread_max", "priced_out", "format_fail", "at_cap", "med_s", "s_per_solve", "j_per_solve", "status"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for s in sorted(st, key=lambda s: (BOX_ORDER.index(s["box"]), s["family"], -s["success"])):
            w.writerow({**{k: s[k] for k in cols if k in s}, "spread_min": s["spread"][0], "spread_max": s["spread"][1]})
    md = ["# Project 34 menu — standings per box and job (generated by `hard-bench\\menu_standings.py`)", "",
          "One setting = shape · box · model · thinking · temperature. **success** over usable single-attempt rows "
          "(controls, void-by-cap, void-by-method and VOID windows excluded; priced-out rows count as not solved); "
          "**spread** = min..max of per-seed success; **s/solve** and **J/solve** = total seconds / GPU joules over "
          "solved tasks (J only where the box meters energy). Per box and job: the best quality (ranked by the 95% "
          "Wilson lower bound, so a small sample cannot outrank a large one on luck), the fastest per solve, and the "
          "least energy per solve — placement trades them by urgency. Per-solve figures charge failed answers to the "
          "solves (low quality is a price, 34-CHARTER \"How results shape action\"). A setting whose answers hit the "
          "context at the filler's cannot-fit share is **VOID** and never picked: its success counts only survivors "
          "(`at_cap` column). Specialists (joycaption, medgemma, "
          "huihui) are excluded: they are priced only on their own jobs. **PRELIMINARY** until a setting "
          "holds on ≥ 2 task families on that box and reproduces (≥ 2 seeds). Source: `menu-cells.csv`.", ""]
    for box in BOX_ORDER:
        bs = [s for s in st if s["box"] == box]
        if not bs:
            continue
        md += ["## %s" % box, "",
               "| job | best quality | fastest per solve | least energy per solve | settings measured |",
               "|---|---|---|---|---|"]
        for fam in FAMILIES:
            fs = [s for s in bs if s["family"] == fam and s["n"] >= 10 and not s["status"].startswith("VOID")]
            if not fs:
                continue
            best = max(fs, key=lambda s: (s["wilson"], -(s["s_per_solve"] or 1e9)))
            fast = min((s for s in fs if s["s_per_solve"]), key=lambda s: s["s_per_solve"], default=None)
            green = min((s for s in fs if s["j_per_solve"]), key=lambda s: s["j_per_solve"], default=None)

            def cell(s, what):
                if s is None:
                    return "—"
                return "%s<br>%.0f%% (%s–%s, n %d) · %s s/solve%s · %s" % (
                    label(s), 100 * s["success"], fmt(100 * s["spread"][0], "%.0f"), fmt(100 * s["spread"][1], "%.0f"),
                    s["n"], fmt(s["s_per_solve"]), (" · %s J/solve" % fmt(s["j_per_solve"], "%.0f"))
                    if s["j_per_solve"] else "", s["status"])
            md.append("| %s | %s | %s | %s | %d |" % (fam, cell(best, "q"), cell(fast, "t"), cell(green, "e"), len(fs)))
        md.append("")
    md += ["Every setting with its numbers: `standings.csv` (%d settings)." % len(st)]
    with open(os.path.join(MENU, "standings.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("settings %d -> %s" % (len(st), os.path.join(MENU, "standings.md")))


if __name__ == "__main__":
    main()
