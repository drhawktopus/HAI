"""CHAT1 reduce: the one-line chat turn priced on every tier (HAI-Agent-Surface-Design measurement 3).

    python reduce_chat1.py

Reads every delivered CHAT1 result - GPU (`campaigns\\chat1-*\\fleet-chat1-*\\*\\out.json`), rigby
(`outbox\\chat1-rigby-*.results.json`) and the tablets (`outbox\\hardbench-chat1-*.results.json`) - and writes to
`Documentation\\Datasets\\Project34-Menu\\`:
  chat1.md / chat1.csv    one row per tier x box (tablets: per CLASS) x model x thinking: turns, solved, median /
                          p90 / max seconds per turn, joules per turn where metered, first-token estimate where the
                          engine reports its prompt rate
  chat1_standings.csv     the same, in standings.csv's columns with family "chat-turn" - menu_policy.py reads it, so the
                          placement table prices the simple-chat turn on every tier from one derivation
A result counts only when its control passed (the instrument proved itself); control rows are never priced.
Tablet wall time includes llama-cli loading the model for each turn (no resident server on the tablets): it is the
cold-turn price, stated as such.
"""
import csv
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_void as MV  # noqa: E402
import menu_wall as MW  # noqa: E402  whole-box wall joules per turn window (the menu's rule)
import menu_policy as MPOL  # noqa: E402  rigby_watts: rigby's whole box

import menu_paths as MPATH  # noqa: E402  every folder and box table the menu reads
CAMP = MPATH.CAMPAIGNS
OUTBOX = MPATH.OUTBOX
OUT = MPATH.MENU
CLASS = MPATH.tablet_classes()           # the tablets' hardware classes (hardbench_small_shapes), boxes.json
TABLET_RUN = "-r4"          # the tablet run whose payload graded AND timed correctly (see tablet_rows)
TIER = MPATH.tier_of_box()


def _q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else None


def gpu_rows():
    out = []
    # menu_void is "one list, read by every consumer of delivered rows" - this reducer did not read it, so a delivery
    # voided for an instrument fault was pooled into the 3.8's xhigh chat-turn price that menu_policy reads.
    voids = MV.load()
    for f in glob.glob(os.path.join(CAMP, "chat1-*", "fleet-chat1-*", "*", "out.json")):
        if MV.is_voided(f, voids):
            continue
        d = json.load(open(f, encoding="utf-8"))
        rows = d.get("rows") or []
        ctl = [r for r in rows if r.get("id") == "control_add"]
        if not ctl or not all(r.get("ok") for r in ctl):
            continue
        think = d.get("think")
        think = "off" if think in (False, None) else "on" if think is True else str(think)
        for r in rows:
            if r.get("id") == "control_add" or r.get("error"):
                continue
            # the menu's rule: the WHOLE box at the wall over the turn's window, the card meter on its line
            wj = MW.price(d.get("host"), r.get("t0"), r.get("t1"), r.get("gpu_j"), r.get("wall_s"))["wall_j"]
            out.append({"box": d.get("host"), "model": d.get("model"), "think": think, "task": r["id"],
                        "ok": bool(r.get("ok")), "wall_s": r.get("wall_s"), "j": wj, "ttft_s": None,
                        "source": os.path.basename(os.path.dirname(f))})
    return out


def rigby_rows():
    out = []
    # newest result per ORDER: a stopped-then-requeued order ships twice and its resumed run carries the kept rows
    newest = {}
    for f in sorted(glob.glob(os.path.join(OUTBOX, "chat1-rigby-*.results.json")), key=os.path.getmtime):
        newest[os.path.basename(f).rsplit(".", 3)[0]] = f
    for f in newest.values():
        d = json.load(open(f, encoding="utf-8"))
        if not d.get("control_passed"):
            continue
        for r in d.get("rows") or []:
            if str(r.get("task", "")).startswith("control") or not r.get("ran"):
                continue
            ttft = (r["prompt_tokens"] / r["prompt_tps"]) if r.get("prompt_tokens") and r.get("prompt_tps") else None
            out.append({"box": "rigby", "model": r.get("model"), "think": r.get("reasoning") or "default",
                        "temperature": r.get("temperature"),       # the row's sampling: menu_policy stamps rigby's
                        "task": r["task"], "ok": bool(r.get("ok")), "wall_s": r.get("wall_s"),   # `order` fields from it
                        "j": (r["wall_s"] * MPOL.rigby_watts(r.get("model"))[0]) if r.get("wall_s") else None,
                        "ttft_s": round(ttft, 2) if ttft else None, "source": os.path.basename(f)})
    return out


def warm_rows():
    """The Fellowship serving chat from RESIDENT servers: one row per
    turn, its node and time to first token; the newest result per order (a resumed order ships its kept rows again)."""
    out, newest = [], {}
    for f in sorted(glob.glob(os.path.join(OUTBOX, "chat1-warm-*.results.json")), key=os.path.getmtime):
        newest[os.path.basename(f).rsplit(".", 3)[0]] = f
    for f in newest.values():
        d = json.load(open(f, encoding="utf-8"))
        if not d.get("control_passed"):
            continue
        for r in d.get("rows") or []:
            if str(r.get("task", "")).startswith("control") or not r.get("ran"):
                continue
            node = r.get("node") or "?"
            out.append({"box": "fellowship", "model": "%s @class %s (warm)" % (r.get("model"), CLASS.get(node, "?")),
                        "think": r.get("reasoning") or "n/a", "task": r.get("task"), "ok": bool(r.get("ok")),
                        "wall_s": r.get("wall_s"), "j": MW.tablet_j(r.get("wall_s")), "ttft_s": r.get("ttft_s"),
                        "source": os.path.basename(f)})
    return out


def tablet_rows():
    out = []
    # TABLET_RUN only. r1/r2: the chat extract sat AFTER the __main__ guard and the tablets RUN the payload as a script,
    # so every reply was graded as Python. r3: graded right, but the idle loop slept 5 s between polls, so every turn's
    # wall rounded UP to the next 5 s, and merry (cannot hold the 2B) drained and voided the 2B order. r4: both fixed.
    for f in glob.glob(os.path.join(OUTBOX, "hardbench-chat1-*%s.*.results.json" % TABLET_RUN)):
        d = json.load(open(f, encoding="utf-8"))
        if not d.get("control_passed"):
            continue
        for r in d.get("rows") or []:
            if r.get("arm") == "control" or str(r.get("task", "")).startswith("control") or not r.get("ran"):
                continue
            if str(r.get("error") or "").startswith("answer_boundary"):
                continue                                   # instrument failure, never a model result
            host = r.get("_host") or "?"
            out.append({"box": "fellowship", "model": "%s @class %s" % (r.get("model"), CLASS.get(host, "?")),
                        "think": "off" if "Qwen3.5" in str(r.get("model")) else "n/a", "task": r.get("task"),
                        "ok": bool(r.get("ok")), "wall_s": r.get("wall_s"), "j": MW.tablet_j(r.get("wall_s")), "ttft_s": None,
                        "source": os.path.basename(f)})
    return out


def reduce(rows):
    cells = {}
    for r in rows:
        cells.setdefault((r["box"], r["model"], r["think"]), []).append(r)
    table = []
    for (box, model, think), rs in sorted(cells.items(), key=lambda kv: (TIER.get(kv[0][0], "?"), kv[0])):
        walls = [r["wall_s"] for r in rs if r["wall_s"] is not None]
        solved = sum(r["ok"] for r in rs)
        js = [r["j"] for r in rs if r["j"] is not None]
        tt = [r["ttft_s"] for r in rs if r["ttft_s"] is not None]
        temps = {r.get("temperature") for r in rs if r.get("temperature") is not None}
        table.append({"tier": TIER.get(box, "?"), "box": box, "model": model, "think": think, "n": len(rs),
                      # one sampling per cell = the table row can carry Tolkien's order fields (rigby_order_fields needs
                      # the temperature; a blank would leave rigby's chat-turn row without them, so a Fellowship chat pick
                      # would have nothing to fill its spec from); mixed or absent = blank, as before
                      "temperature": temps.pop() if len(temps) == 1 else "",
                      "solved": solved, "quality": round(solved / len(rs), 4) if rs else 0.0,
                      "median_s": round(statistics.median(walls), 2) if walls else None,
                      "p90_s": round(_q(walls, 0.9), 2) if walls else None, "max_s": round(max(walls), 2) if walls else None,
                      "s_per_solve": round(sum(walls) / solved, 2) if solved and walls else None,
                      "j_per_solve": round(sum(js) / solved, 1) if solved and js and len(js) == len(rs) else None,
                      "ttft_med_s": round(statistics.median(tt), 2) if tt else None,
                      "sources": len({r["source"] for r in rs})})
    return table


def main():
    rows = gpu_rows() + rigby_rows() + tablet_rows() + warm_rows()
    assert rows, "zero CHAT1 rows read - the globs are broken or nothing delivered yet"
    table = reduce(rows)
    with open(os.path.join(OUT, "chat1.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(table)
    with open(os.path.join(OUT, "chat1_standings.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["box", "family", "shape", "model", "think", "temperature", "n", "success",
                                          "s_per_solve", "j_per_solve", "status"])
        w.writeheader()
        for t in table:
            w.writerow({"box": t["box"], "family": "chat-turn", "shape": "S1", "model": t["model"], "think": t["think"],
                        "temperature": t.get("temperature", ""), "n": t["n"], "success": t["quality"],
                        "s_per_solve": t["s_per_solve"] if t["s_per_solve"] is not None else "",
                        "j_per_solve": t["j_per_solve"] if t["j_per_solve"] is not None else "",
                        # the menu's reproduces-once rule (menu_standings: >= 2 seeds), with chat-turn's one family:
                        # a cell holds once two independent deliveries (sources) agree in this pool. Hard-coded
                        # PRELIMINARY - a second seed could never lift it.
                        "status": "holds" if t["sources"] >= 2 else "PRELIMINARY"})
    md = ["# CHAT1 - the one-line chat turn, priced on every tier", "",
          "HAI's own 7 tablet-tier chat requests, verbatim (hard_tasks_chat1.py). Results count only when their control "
          "passed. Tablet seconds include loading the model each turn (cold turn). PRELIMINARY: one run per cell.", "",
          "| tier | box | model | think | n | solved | median s | p90 s | max s | s / solve | J / solve | first token s |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in table:
        md.append("| %s | %s | %s | %s | %d | %d | %s | %s | %s | %s | %s | %s |" % (
            t["tier"], t["box"], t["model"], t["think"], t["n"], t["solved"], t["median_s"], t["p90_s"], t["max_s"],
            t["s_per_solve"], t["j_per_solve"] if t["j_per_solve"] is not None else "-",
            t["ttft_med_s"] if t["ttft_med_s"] is not None else "-"))
    open(os.path.join(OUT, "chat1.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n".join(md))
    print("rows %d, cells %d -> %s" % (len(rows), len(table), OUT))


if __name__ == "__main__":
    main()
