"""#34 Phase 7 step 1: compile the priced menu into Spock's POLICY TABLE ([[34-Phase-7-Placement-Policy-Design]]).

    python menu_policy.py [--standings <csv>] [--out <json>]

Reads `Documentation\\Datasets\\Project34-Menu\\standings.csv` (menu_standings.py) and writes `policy.json` beside it.
Keyed the way [[HAI-Agent-Surface-Design]] § "The design" places a request: TRIAGE picks the tier (the advisor's own
labels, advisor_tasks.TIERS), SPOCK places within it. For each job family x tier x lens: the tier's HOME boxes first,
then the other tiers' boxes as OVERFLOW with a caveat, each with its cheapest setting for that lens. Every refused
setting is listed with its reason. Generated, never hand-edited. The `tool` tier is plain code, so it is not placed.

Every rule below is the charter's or the Order Spec's, not this module's:
- ACCEPTABLE ([[34-CHARTER]] § "How results shape action"; [[34-Hard-Bench-Order-Spec]] § "When an arm stops being
  filled"): an arm is out only when it cannot fit, is priced out (per answer or per CORRECT answer, 10x the box's
  cheapest cold solve), or the owner has ruled it out - exactly the verdicts the menu filler already writes
  (menu_fill_auto_holds.json) and the owner's rulings file (menu_fill_hold.json), read through menu_fill's own matcher.
  "Dominated is NOT a reason to stop": an acceptable but pricier arm is a fallback with its caveat.
- A Fellowship best-of-N row picked by the hidden checks is a CEILING, never a quality (no production selector).
- Lenses = the two per-solved prices the menu publishes (failures charged to the solves): `fastest` = seconds per
  solve, `least_energy` = joules per solve (unmetered sorts last). Which lens a request uses (urgency, deadlines,
  the energy trough) is area C's input, not decided here."""
import csv
import datetime
import hashlib
import glob
import json
import os
import re
import sys

import menu_fill
import menu_wall

# The placement arithmetic itself is one stdlib-only module in the orchestrator's canonical source tree.
import menu_paths as MPATH  # noqa: E402  every folder, mirror and box table the menu reads
sys.path.append(MPATH.ORCH_MIRROR)
import model_fit

STANDINGS = os.path.join(MPATH.MENU, "standings.csv")
OUT = os.path.join(MPATH.MENU, "policy.json")
# the design's tiers -> their home boxes ("TOLKIEN -> tablets / rigby . simple chat" / "8 GB boxes ... mid-weight;
# overflow when the lead is busy (with its caveat)" / "THE LEAD (the 3.8 today)")
# `fellowship` is Tolkien's whole destination (rigby + the tablet pool; Tolkien picks the worker) and keeps its rows.
# `rigby` and `tablets` are the two rungs inside it, separate tiers so a request can name the class of worker it needs
# (`tablets` homes the `fellowship` BOX = the tablets; the GT 730 joins it once it has its own priced box). A rung only
# ADDS a tier: `fellowship`/`8gb`/`lead` homes and overflow stay as they were, so their rows do not change.
TIER_HOMES = MPATH.tier_homes()           # boxes.json "tiers": {"homes": ..., "overflow": ...}
TIER_OVERFLOW = MPATH.tier_overflow()
# the lead model (the crown is named at the END of #34; the 3.8 until then). Mothership is NOT reserved for it: the card
# unloads after 5 min idle, so any
# model there is priced like any other and carries a residency caveat - a cost, never a refusal (design step 4)
def _lead():
    """The lead: the lead tier's home box and that box's champion (boxes.json: tiers.homes.lead, boxes.<box>.
    champion), or None when the box table names none."""
    homes = ((MPATH.BOXES.get("tiers") or {}).get("homes") or {}).get("lead") or []
    box = homes[0] if homes else None
    model = (((MPATH.BOXES.get("boxes") or {}).get(box) or {}).get("champion")) if box else None
    return (box, model) if box and model else None


LEAD = _lead()
RIGBY_MARGINAL_W = 54.0     # rigby's metered plug marginal (Order Spec); the menu leaves rigby's J empty
ORACLE_SHAPES = ("S2:best_of_n",)
LENSES = ("fastest", "least_energy", "best_quality")    # time · cost · quality
# The Order Spec's price line (10x the cheapest solve), applied across the FLEET for a request someone is waiting on:
# a setting slower than this x the family's fleet-fastest solve is priced out of the `fastest` lens; background keeps it.
PRICE_X = 10
UNRECORDED_THINK = ("default", "n/a")   # the filler keys a model without a thinking switch as "off"


def rigby_watts(model, at=None):
    """(watts, caveat) for rigby's WHOLE box at the wall while it answers - the menu prices the total draw, so rigby is its idle for the placement's era (menu_wall.RIGBY: own PSU + the 1060's 600 W
    supply) plus the placement's measured working extra. A model with no placement takes the idle of the era it ran in
    (`at`, epoch; the plug-metered pre-1060 era when unknown) plus the plug's measured marginal."""
    placement = menu_fill.RIGBY_PLACEMENT.get(model)
    if placement:
        era = menu_wall.rigby_era(datetime.datetime.fromisoformat(placement["since"]).timestamp())
        return era["idle_w"] + placement["watts"], ("energy = wall x %g W (rigby whole box: idle %g + working %g, %s, %s)"
                                                   % (era["idle_w"] + placement["watts"], era["idle_w"], placement["watts"],
                                                      placement["name"], placement.get("measured", "unrecorded")))
    era = menu_wall.rigby_era(at) if at else menu_wall.RIGBY[0]
    return era["idle_w"] + RIGBY_MARGINAL_W, ("energy = wall x %g W (rigby whole box: idle %g + plug marginal %g)"
                                              % (era["idle_w"] + RIGBY_MARGINAL_W, era["idle_w"], RIGBY_MARGINAL_W))


def wilson_low(p, n, z=1.96):
    if n <= 0:
        return 0.0
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5
    return max(0.0, (centre - margin) / den)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def entries(rows):
    out = []
    for r in rows:
        n, p = int(float(r["n"] or 0)), _f(r["success"]) or 0.0
        e = {"box": r["box"], "family": r["family"], "shape": r["shape"], "model": r["model"], "think": r["think"],
             "temperature": r["temperature"], "n": n, "quality": round(p, 4), "quality_low": round(wilson_low(p, n), 4),
             "s_per_solve": _f(r["s_per_solve"]), "j_per_solve": _f(r["j_per_solve"]), "status": r["status"],
             "caveats": []}
        if e["j_per_solve"] is None and e["box"] == "rigby" and e["s_per_solve"] is not None:
            watts, why = rigby_watts(e["model"])                         # the file's placement prices its watts
            e["j_per_solve"] = round(e["s_per_solve"] * watts, 1)
            e["caveats"].append(why)
        if e["status"] == "PRELIMINARY":
            e["caveats"].append("preliminary (reproduces-once not met)")
        if e["box"] == "fellowship":
            e["caveats"].append("tablets: judged per class at the menu rebuild, one-shot until a production selector")
        # A Fellowship row carries the Tolkien `llm` fields it was priced with: Spock sends "the Fellowship" one order and
        # TOLKIEN picks the worker from these rows ([[34-Phase-7-Placement-Policy-Design]] § D, the two routers), so the
        # row that places a piece also sets it - the GPU rows' policy-token rule, on the Fellowship's side.
        if e["box"] == "rigby" and e["temperature"] not in ("", None):
            e["order"] = menu_fill.rigby_order_fields(e["model"], e["think"], e["temperature"])
        elif e["box"] == "fellowship":
            e["order"] = menu_fill.tablet_order_fields(e["model"], e["think"], e["temperature"])
        if LEAD and e["box"] == LEAD[0] and e["model"] != LEAD[1]:
            e["caveats"].append("residency: Mothership unloads after 5 min idle (OLLAMA_KEEP_ALIVE=5m), so this loads "
                                "cold like any model; if the %s is warm it is a swap" % LEAD[1])
        out.append(e)
    return out


def stop_verdict(e, stops):
    """The filler's / the owner's stop value for this arm, or None - menu_fill's own matcher, so one derivation. A stop
    is a verdict on the ARM, so a cell its `except` keeps open for measurement does not reopen it for placement."""
    fam = menu_fill.FAM_ALIAS.get(e["family"], e["family"])
    think = "off" if e["think"] in UNRECORDED_THINK else e["think"]
    try:
        t = "%.1f" % float(e["temperature"])
    except (TypeError, ValueError):
        t = str(e["temperature"])
    return menu_fill.cell_hold(stops, (e["box"], e["model"], fam, think, t, "*"))


def refusal(e, stops):
    if e["status"].startswith("VOID"):
        return e["status"]
    if e["shape"] in ORACLE_SHAPES:
        return "oracle: best-of-N picked by the hidden checks is a ceiling, not a quality (no production selector)"
    if e["s_per_solve"] is None or e["quality"] <= 0:
        return "never solved"
    v = stop_verdict(e, stops)
    if v is not None and not (isinstance(v, dict) and v.get("orders_only")):     # a scope cut stops ORDERS only
        return "stopped: " + menu_fill.hold_why(v)
    return None


def think_off_refusal(e, siblings):
    """the owner thinking OFF is never a default - "you probably should just never use the thinking off option...
    we only win by having a few seconds saved" - except where the menu shows a quality TIE. So an `off` setting is not
    acceptable when the SAME model on the SAME box in the same family with thinking on is clearly better: its Wilson
    lower bound above off's score. A model with no thinking switch (UNRECORDED_THINK) is not touched. Without this the
    `fastest` lens chose thinking-off on every chat cell (off is cheaper per solve), contradicting the rule
    ollama_client.lead_think already enforces at call time."""
    if e["think"] != "off":
        return None
    better = [s for s in siblings if s is not e and s["box"] == e["box"] and s["model"] == e["model"]
              and s["think"] not in ("off",) + UNRECORDED_THINK and s["quality_low"] > e["quality"]]
    if not better:
        return None
    b = max(better, key=lambda s: s["quality_low"])
    return ("thinking off loses quality here (off %.3f vs %s %.3f, lower bound %.3f) - off only on a tie "
            "(the owner, 2026-10-19)" % (e["quality"], b["think"], b["quality"], b["quality_low"]))


def _energy_key(e):
    return (e["j_per_solve"] is None, e["j_per_solve"] if e["j_per_solve"] is not None else 0.0, e["s_per_solve"])


def _per_box(pool, key):
    """Each MODEL's winner on each box, best first - not only the box's overall winner. A model that loses every lens
    on its box can still be the right row when it is already loaded there, which only the weighing knows."""
    best = {}
    for e in sorted(pool, key=key):
        best.setdefault((e["box"], e["model"]), e)
    return sorted(best.values(), key=key)


# The three lenses' sort keys, named ONCE: _lenses picks each box's winner with them and menu_matrix.py ranks every
# config with them, so "who wins a lens" has one derivation, not two that can drift.
LENS_KEYS = {"fastest": lambda e: (e["s_per_solve"], -e["quality_low"]),
             "least_energy": _energy_key,
             "best_quality": lambda e: (-e["quality_low"], e["s_per_solve"])}


def interactive_line(fleet_fastest):
    """The fastest lens's price line (PRICE_X x the family's fleet-fastest solve), or None when nothing is priced."""
    return PRICE_X * fleet_fastest if fleet_fastest else None


def _lenses(pool, fleet_fastest):
    line = interactive_line(fleet_fastest)
    return {"fastest": _per_box([e for e in pool if line is None or e["s_per_solve"] <= line], LENS_KEYS["fastest"]),
            "least_energy": _per_box(pool, LENS_KEYS["least_energy"]),
            "best_quality": _per_box(pool, LENS_KEYS["best_quality"])}


def load_stops():
    """the owner's rulings + the filler's automatic stops, both as menu_fill reads them."""
    stops = dict(menu_fill.read_auto().get("stops", {}))
    stops.update(menu_fill.load_hold())
    return stops


CAMPAIGNS = MPATH.CAMPAIGNS


def load_prices(root=CAMPAIGNS):
    """{(box, model): {"load_s": median, "load_n": n}}: the RESIDENCY price (design step 4), from every delivered order
    whose placement record measured a cold load. Orders that found the model warm, or predate the field, say nothing and are skipped - never a zero."""
    obs = {}
    for dirpath, _dirs, files in os.walk(root):
        if "out.json" not in files:
            continue
        try:
            with open(os.path.join(dirpath, "out.json"), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        p = d.get("placement") if isinstance(d, dict) else None
        if isinstance(p, dict) and p.get("cold") and isinstance(p.get("load_s"), (int, float)) and p["load_s"] > 0:
            obs.setdefault((str(d.get("host") or "").lower(), d.get("model")), []).append(float(p["load_s"]))
    return {k: {"load_s": round(_median(v), 2), "load_n": len(v)} for k, v in obs.items()}


def stamp_load(policy, prices):
    """Every table row gets its box x model residency price: load_s (None = unmeasured, never zero) and load_n."""
    for p in policy.values():
        for t in p["tiers"].values():
            for rows in t.values():
                for e in rows:
                    lp = prices.get((e["box"], e["model"])) or {}
                    e["load_s"], e["load_n"] = lp.get("load_s"), lp.get("load_n", 0)
    return policy


HAI_CHAT = os.path.join(CAMPAIGNS, "hai-chat")     # fellowship_turn.py's deliveries: one desk order per placed chat turn
# Deliveries submitted before this instant priced a DIFFERENT system and do not count. Tolkien's lanes went from 30 s polls to the 1 s idle wait (`tablet_runner`
# QUICK_POLL_S) and the form had started sending the bare turn (`f567763`, 09:31) - the one earlier delivery waited
# 46 s on polls and 31 s reading a grounded prompt. Move this when a change alters what a Fellowship turn waits on.
OVERHEAD_SINCE = datetime.datetime(2026, 10, 23, 9, 33)
FELLOWSHIP_BOXES = ("rigby", "fellowship")


def fellowship_overheads(root=HAI_CHAT):
    """{"overhead_s": median, "overhead_n": n}: what a chat turn placed on the Fellowship pays BEFORE its first token on
    top of the row's own solve time - the desk round trip, Tolkien's pickup, the per-order server launch and the prompt
    read - measured from every delivered hai-chat order: (finished - the submit stamp in the order's name) minus the
    turn row's own generation time. The GPU rows pay residency_cost for the same thing; a Fellowship row is not a card,
    so until this field existed the weighing charged it NOTHING while proof paid 85 s on a 1.9 s
    row. An undelivered, void or unreadable order, or one without a
    generated turn row, says nothing. Kirk's own poll cadence (0.3 s) is under it, not in it.

    PER WORKER: -> {box: {"overhead_s", "overhead_n"}}, keyed by the delivery's own `placement_row.box`
    (rigby, or the tablet pool `fellowship`). One median for both hid the gap - rigby ~6 s, a tablet ~25 s (it starts a
    server and loads the model per order) - and priced every tablet row at rigby's overhead. A delivery that does not
    say which worker served it says nothing."""
    obs = {}
    if not os.path.isdir(root):
        return {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".results.json"):
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                    d = json.load(f)
                base = str(d.get("name") or fn).split("_")[0].split("-")
                submitted = datetime.datetime.strptime(base[-3] + base[-2], "%Y%m%d%H%M%S")
                finished = datetime.datetime.strptime(d["finished"], "%Y-%m-%d %H:%M:%S")
                turn = [r for r in d.get("rows") or [] if r.get("task") == "turn" and r.get("ran") and r.get("gen_ms")]
                box = (d.get("placement_row") or {}).get("box")
            except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
                continue
            if not turn or not d.get("usable") or box not in FELLOWSHIP_BOXES:
                continue
            oh = (finished - submitted).total_seconds() - float(turn[0]["gen_ms"]) / 1000.0
            if oh > 0 and submitted >= OVERHEAD_SINCE:
                obs.setdefault(box, []).append(oh)
    return {b: {"overhead_s": round(_median(v), 1), "overhead_n": len(v)} for b, v in obs.items()}


def stamp_fellowship(policy, by_box):
    """Every Fellowship row (rigby, the tablet pool) carries the desk-order overhead a chat turn pays before its first
    token ON ITS OWN WORKER (fellowship_overheads, per box): order_overhead_s (None = unmeasured, never zero) and
    overhead_n, plus a caveat saying so. GPU rows are untouched - they pay residency instead."""
    for p in policy.values():
        for t in p["tiers"].values():
            for rows in t.values():
                for e in rows:
                    if e["box"] not in FELLOWSHIP_BOXES:
                        continue
                    oh = by_box.get(e["box"]) or {"overhead_s": None, "overhead_n": 0}
                    e["order_overhead_s"], e["overhead_n"] = oh.get("overhead_s"), oh.get("overhead_n", 0)
                    # The per-ORDER energy: the overhead seconds at the
                    # worker's marginal watts - rigby's plug marginal, or the model's placement watts when it has one
                    # (entries()' own rule); the tablet pool is unmetered (None, never 0). Cost = literal electricity
                    # (checklist B3): a rigby chat turn's launch is ~550 J beside its ~100 J answer, and the weighing
                    # adds this to the row's J per request. Measured from deliveries, so when the desk-time project
                    # shrinks the overhead the energy price falls with it on the next refresh.
                    e["j_order"] = None
                    if oh.get("overhead_s") is not None and e["box"] == "rigby":
                        watts = rigby_watts(e.get("model"))[0]
                        e["j_order"] = round(float(oh["overhead_s"]) * watts, 1)
                    if oh.get("overhead_s") is not None:
                        e["caveats"] = e["caveats"] + [
                            "a chat turn here is a desk order: +%g s before the first token (desk round trip, per-order "
                            "server launch, prompt read; n=%d hai-chat deliveries)" % (oh["overhead_s"], oh["overhead_n"])]
                        if e["j_order"] is not None:
                            e["caveats"] = e["caveats"] + ["+%g J per order (that overhead at %g W), added to the row's J by the "
                                                           "weighing" % (e["j_order"], watts)]
    return policy


# ------------------------------------------------------------------------------------- the embed family
# HAI's per-turn embeds (library search, tool selection) went to a FIXED host list (config.EMBED_HOSTS) while every other
# model call was placed from this table - that list, not placement, dropped them on Softserve and evicted McCoy all day
#. The family is one row per GPU box
# from the embed-price orders (foundry workspace\embed\embed_check.py, submit_embedcheck.py; deliveries under
# campaigns\embed-<day>): ONE model everywhere, so quality is the model's, not the box's - every accepted row carries
# 1.0 and the rows differ in time and energy only; a box whose vectors disagree with the fleet's for the same probe is
# an instrument fault and is refused, never a cheaper row. No Fellowship rows: a desk order per embed pays the table's
# measured overhead before its first token against 0.1-0.3 s on a card, and a warm embed server would be a second
# kept-loaded model (checklist E1: McCoy's is the one exception). The cold load joins through load_prices like any
# order's (the payload writes infer_ollama's `placement` shape).
EMBED_FAMILY, EMBED_TIER = "embed", "gpu"      # scheduler.EMBED_FAMILY / EMBED_TIER are the same two names (test-pinned)
EMBED_AGREE_MIN = 0.99     # cosine with another box's vector for the same probe: float noise is ~1e-4; below this it is
#                            a different embedding (nomic's useful band is 0.36-0.76 wide), i.e. a broken instrument
EMBED_GLOB = os.path.join(CAMPAIGNS, "embed-*", "*", "out.json")


def _cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(x * x for x in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def _order_time(path):
    """Epoch of the order a delivery answers: the `.<YYYYmmdd-HHMMSS>` stamp in its folder name, else the file's mtime."""
    m = re.search(r"[.-](\d{8}-\d{6})", os.path.basename(os.path.dirname(path)))
    if m:
        return datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp()
    return os.path.getmtime(path)


def embed_deliveries(pattern=EMBED_GLOB):
    """Every delivered embed-price order, oldest first: [{box, model, dims, walls, loop_j, calls, vectors, meter, path}].
    Unreadable, call-less or row-less deliveries say nothing."""
    out = []
    for p in sorted(glob.glob(pattern)):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            s = d.get("summary") or {}
            walls = [float(r["wall_s"]) for r in d.get("rows") or [] if isinstance(r.get("wall_s"), (int, float))]
            if not walls or not s.get("calls"):
                continue
            out.append({"box": str(d.get("host") or "").lower(), "model": d.get("model"), "dims": d.get("dims"),
                        "walls": walls, "loop_j": s.get("loop_j"), "loop_s": s.get("loop_s"), "calls": int(s["calls"]),
                        "vectors": d.get("vectors") or {}, "meter": s.get("meter"), "path": p,
                        "delivered": _order_time(p)})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return out


def embed_agreement(deliveries):
    """{box: (agreement, n probes)}: agreement = over the probes the box shares with the OTHER boxes' newest vectors, the
    worst probe's cosine with the NEAREST other box - a box agrees when at least one other box returns the same
    embedding for every probe, so one broken box does not refuse the two that agree (two boxes that disagree are
    both unchecked-and-refused: nothing says which is right). No other box -> (None, 0): unchecked, said."""
    newest = {}
    for d in deliveries:                       # oldest first, so the last delivery per box wins
        newest[d["box"]] = d["vectors"]
    out = {}
    for box, vecs in newest.items():
        cos = []
        for pid, v in vecs.items():
            others = [o[pid] for b, o in newest.items() if b != box and pid in o]
            if others:
                cos.append(max(_cos(v, w) for w in others))
        out[box] = (round(min(cos), 4) if cos else None, len(cos))
    return out


def embed_family(deliveries, fellowship_oh=None):
    """The policy entry for EMBED_FAMILY, or None without deliveries: one tier (EMBED_TIER), every GPU box a home. Per
    box the calls of every delivery are POOLED - s_per_solve = the median wall per call, j_per_solve = the loops' joules /
    their calls (None when unmetered, never 0), n = calls. quality = quality_low = 1.0 by construction (one model) once
    the box's vectors agree with the fleet's (EMBED_AGREE_MIN); a box that disagrees is refused with why."""
    if not deliveries:
        return None
    agree = embed_agreement(deliveries)
    by_box = {}
    for d in deliveries:
        b = by_box.setdefault(d["box"], {"walls": [], "j": 0.0, "j_calls": 0, "calls": 0, "model": d["model"],
                                         "meters": set(), "deliveries": 0})
        b["walls"] += d["walls"]
        b["calls"] += d["calls"]
        b["deliveries"] += 1
        b["model"] = d["model"]                # the newest delivery names the model
        b["delivered"] = d.get("delivered")
        if isinstance(d["loop_j"], (int, float)):
            # the menu prices the WHOLE box: the loop's card joules on the box's fitted wall line (no window on an
            # embed delivery, so menu_wall's own-meter path); a box with no line keeps nothing rather than the card
            wj = menu_wall.price(d["box"], None, None, d["loop_j"], d.get("loop_s") or sum(d["walls"]))["wall_j"]
            if wj is not None:
                b["j"] += wj
                b["j_calls"] += d["calls"]
        b["meters"].add(d["meter"] or "unmetered")
    acc, refused = [], []
    for box, b in sorted(by_box.items()):
        a_min, a_n = agree.get(box, (None, 0))
        s = round(_median(b["walls"]), 4)
        j = round(b["j"] / b["j_calls"], 2) if b["j_calls"] else None
        j_note = ("J per call = the whole box at the wall over the warm loop / calls (the loop's %s board energy on "
                  "the box's fitted wall line, menu_wall); %d calls in %d deliveries"
                  % (", ".join(sorted(b["meters"])), b["calls"], b["deliveries"]))
        if j is None and box == "rigby":
            # rigby is not metered per order: the menu's rule for every rigby row (entries()) - wall x its whole box
            watts, why = rigby_watts(b["model"], at=b.get("delivered"))
            j = round(s * watts, 2)
            j_note = "%s; %d calls in %d deliveries" % (why, b["calls"], b["deliveries"])
        e = {"box": box, "family": EMBED_FAMILY, "shape": "E1", "model": b["model"], "think": "n/a", "temperature": "",
             "n": b["calls"], "quality": 1.0, "quality_low": 1.0, "s_per_solve": s, "j_per_solve": j, "status": "holds",
             "caveats": ["one model everywhere (%s): quality is the model's, not the box's - rows differ in time and "
                         "energy only" % b["model"],
                         ("vectors agree with the fleet's: min cos %.4f over %d probes" % (a_min, a_n)) if a_min is not None
                         else "agreement unchecked: no other box has delivered this probe set yet",
                         j_note]}
        if a_min is not None and a_min < EMBED_AGREE_MIN:
            refused.append(dict(e, why="instrument fault: this box's vectors disagree with the fleet's (min cos %.4f over %d "
                                       "probes, line %.2f) - not a cheaper row" % (a_min, a_n, EMBED_AGREE_MIN)))
        else:
            acc.append(e)
    fleet_fastest = min((e["s_per_solve"] for e in acc), default=None)
    # An embed is small work: its HOMES are the 8 GB boxes (design: "8 GB boxes ... mid-weight; overflow when the lead
    # is busy") and the lead's card is OVERFLOW with the caveat the 8gb tier's own overflow rows carry - Mothership runs
    # one model per card, so an embed loaded there evicts whatever the lead holds, a swap the weighing does not price.
    # Home sorts before overflow inside the band, so the lead's card serves an embed only when neither 8 GB box is
    # inside the line (asleep, held, cold).
    # The Fellowship's rows (rigby, the tablet pool) are homes too: not the lead's card; the weighing charges them the
    # desk-order overhead stamp_fellowship adds (interactive turns reject them on the line by MEASUREMENT; a background
    # batch - the library reload - may find them cheapest).
    homes = tuple(TIER_HOMES["8gb"]) + FELLOWSHIP_BOXES
    lens = _lenses(acc, fleet_fastest)
    oh = ", ".join("%s %s s" % (b, (fellowship_oh or {})[b]["overhead_s"]) for b in sorted(fellowship_oh or {}))

    def _role(e):
        if e["box"] in FELLOWSHIP_BOXES:
            # a different instrument from the GPU rows (ollama's API): Tolkien's per-order llama-server, asked directly
            return dict(e, role="home", caveats=e["caveats"] + [
                "served by Tolkien's per-order llama-server (asked directly), not ollama's API - the path HAI would pay"])
        if e["box"] in homes:
            return dict(e, role="home")
        return dict(e, role="overflow", caveats=e["caveats"] + [
            "overflow from the 8gb tier: this card runs one model, so an embed here evicts whatever the lead holds"])
    fel = sorted(e["box"] for e in acc if e["box"] in FELLOWSHIP_BOXES)
    return {"tiers": {EMBED_TIER: {l: [_role(e) for e in lens[l]] for l in LENSES}},
            "fleet_fastest_s": fleet_fastest, "interactive_line_s": PRICE_X * fleet_fastest if fleet_fastest else None,
            "refused": refused,
            "note": ("Fellowship rows priced: %s - each a desk order, charged its measured overhead before the first token "
                     "(%s) by the weighing; no warm embed server (checklist E1: McCoy's is the one exception)"
                     % (", ".join(fel), oh or "unmeasured")) if fel else
                    ("GPU boxes only so far: a Fellowship embed is a desk order (measured overhead before the first token: "
                     "%s) against the card rows' %s s; no warm embed server (checklist E1: McCoy's is the one exception)"
                     % (oh or "unmeasured", fleet_fastest))}


# ---------------------------------------------------------------------- first visible output per GPU row
# Speed is the time to the FIRST VISIBLE OUTPUT as well as to the answer (checklist B1; B5 named the gap: priced only
# for the Fellowship rows and new windows). Kirk's chat windows record every turn's time to first output
# (chat_window.record_turn -> State\chat-window-turns.json, keyed by host, with model and whether it was a new window's
# first turn). The median of the non-first turns per box x model is stamped on every GPU row as `first_out_s` (None =
# unmeasured, never zero); the weighing shows it beside the total and the record carries it.
TURNS_STORE = os.path.join(MPATH.DOCS_STATE_LOCAL, "chat-window-turns.json")


CHAT_WINDOWS = MPATH.CHAT_WINDOWS


def strata_first_outputs(root=CHAT_WINDOWS):
    """{(box, model): [seconds]} for STRATA windows (Flash-Next) from their own per-turn records. Kirk's turn store holds only
    ollama windows (chat_window measures `not sse`), so Flash-Next had no time to first output at all and Quick judged it on its
    whole answer. Per later turn: the engine's own state restore
    plus prompt read (`out.json` state_s + prompt_eval_s), the part before its first token; the window's first turn pays the
    open and is left out, as first_outputs does. A turn with an error or no read time is skipped."""
    obs = {}
    for w in glob.glob(os.path.join(root, "hai-win-*")):
        try:
            who = json.load(open(os.path.join(w, "WINDOW.json"), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not who.get("strata"):
            continue
        key = (str(who.get("host") or "").lower(), who.get("model"))
        for out in glob.glob(os.path.join(w, "*.out.json")):
            if os.path.basename(out).startswith("t00001-"):
                continue
            try:
                o = json.load(open(out, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if o.get("error") or not isinstance(o.get("prompt_eval_s"), (int, float)):
                continue
            obs.setdefault(key, []).append(float(o.get("state_s") or 0.0) + float(o["prompt_eval_s"]))
    return obs


def first_outputs(path=TURNS_STORE, windows=CHAT_WINDOWS):
    """{(box, model): {"first_out_s": median seconds, "first_out_n": n}} over turns on an EXISTING window (a new window's
    first turn pays the open and is priced by the window cost, not here): Kirk's store for ollama windows, the Strata windows'
    own records for Flash-Next (strata_first_outputs)."""
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    obs = {}
    for host, rows in (data or {}).items():
        for r in rows or []:
            if r.get("first_turn") or not isinstance(r.get("first_s"), (int, float)):
                continue
            obs.setdefault((str(host).lower(), r.get("model")), []).append(float(r["first_s"]))
    for k, v in (strata_first_outputs(windows) if windows else {}).items():
        obs.setdefault(k, []).extend(v)
    return {k: {"first_out_s": round(_median(v), 2), "first_out_n": len(v)} for k, v in obs.items()}


def stamp_first_output(policy, firsts):
    """Every GPU row gets its box x model first-visible-output price: first_out_s (None = unmeasured) and first_out_n."""
    for p in policy.values():
        for t in p["tiers"].values():
            for rows in t.values():
                for e in rows:
                    if e["box"] in FELLOWSHIP_BOXES:
                        continue
                    f = firsts.get((e["box"], e["model"])) or {}
                    e["first_out_s"], e["first_out_n"] = f.get("first_out_s"), f.get("first_out_n", 0)
    return policy


LONG_READ_TOKENS = 2000   # a row must have READ at least this many prompt tokens for its rate to be a reading rate


def read_prices(root=CAMPAIGNS):
    """{(box, model): {"read_tok_s": median, "read_n": n}}: the RE-READ half of the reload price. A turn on a
    box that lost the conversation's cache pays load_s (cold) + the conversation's tokens / read_tok_s before its first
    token. Rows count only when the engine READ >= LONG_READ_TOKENS (prompt_eval_count counts evaluated tokens only, so a
    cache-served row cannot pass) with its own prompt_eval_duration, and no error. Unmeasured = absent, never zero."""
    obs = {}
    for dirpath, _dirs, files in os.walk(root):
        if "out.json" not in files:
            continue
        try:
            with open(os.path.join(dirpath, "out.json"), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        key = (str(d.get("host") or "").lower(), d.get("model"))
        for r in d.get("rows") or []:
            n, dur = r.get("prompt_eval_count"), r.get("prompt_eval_duration")
            if (isinstance(n, (int, float)) and n >= LONG_READ_TOKENS and isinstance(dur, (int, float)) and dur > 0
                    and not r.get("error")):
                obs.setdefault(key, []).append(n / (dur / 1e9))
    return {k: {"read_tok_s": round(_median(v), 1), "read_n": len(v)} for k, v in obs.items()}


def rigby_read_prices(outbox=None):
    """read_prices for rigby, whose deliveries are Tolkien `*.results.json` in the tablet outbox (llama.cpp timings:
    `prompt_tokens` read, `prompt_ms`, `cache_n` served from cache) - read_prices only knew ollama's out.json, so rigby's
    re-read price was structurally unmeasurable. Same rules: >= LONG_READ_TOKENS read with the engine's own duration, no error, and nothing served from cache;
    a delivery from before its file's placement epoch (menu_fill.placement_stale) prices hardware that is gone."""
    outbox = outbox or os.path.join(menu_fill.TABJOBS, "outbox")
    obs = {}
    for path in glob.glob(os.path.join(outbox, "*.results.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        for r in (d.get("rows") if isinstance(d, dict) else None) or []:
            n, ms, model = r.get("prompt_tokens"), r.get("prompt_ms"), r.get("model")
            if r.get("arm") != "rigby" or r.get("error") or r.get("cache_n") != 0 or not model:
                continue
            if not (isinstance(n, (int, float)) and n >= LONG_READ_TOKENS and isinstance(ms, (int, float)) and ms > 0):
                continue
            if menu_fill.placement_stale(model, path):
                continue
            obs.setdefault(("rigby", model), []).append(n / (ms / 1000.0))
    return {k: {"read_tok_s": round(_median(v), 1), "read_n": len(v)} for k, v in obs.items()}


def stamp_read(policy, prices):
    """Every table row gets its box x model re-read price: read_tok_s (None = unmeasured, never zero) and read_n."""
    for p in policy.values():
        for t in p["tiers"].values():
            for rows in t.values():
                for e in rows:
                    rp = prices.get((e["box"], e["model"])) or {}
                    e["read_tok_s"], e["read_n"] = rp.get("read_tok_s"), rp.get("read_n", 0)
    return policy


def classify(rows, stops):
    """{family: (accepted, refused, fleet_fastest_s)} - the ONE place a config is accepted or refused (and why).
    compile_policy builds the placement table from it; menu_matrix.py lists every config with the same verdict."""
    fams = {}
    for e in entries(rows):
        fams.setdefault(e["family"], []).append(e)
    out = {}
    for fam, es in sorted(fams.items()):
        refused, acc = [], []
        for e in es:
            why = refusal(e, stops) or think_off_refusal(e, [s for s in es if not refusal(s, stops)])
            if why:
                refused.append(dict(e, why=why))
            else:
                acc.append(e)
        out[fam] = (acc, refused, min((e["s_per_solve"] for e in acc), default=None))
    return out


def compile_policy(rows, stops):
    policy = {}
    for fam, (acc, refused, fleet_fastest) in classify(rows, stops).items():
        tiers = {}
        for tier, homes in TIER_HOMES.items():
            over_boxes = [b for t in TIER_OVERFLOW[tier] for b in TIER_HOMES[t]]
            home = _lenses([e for e in acc if e["box"] in homes], fleet_fastest)
            over = _lenses([dict(e, caveats=e["caveats"] + ["overflow from the %s tier" % tier])
                            for e in acc if e["box"] in over_boxes], fleet_fastest)
            tiers[tier] = {lens: [dict(e, role="home") for e in home[lens]]
                           + [dict(e, role="overflow") for e in over[lens]] for lens in LENSES}
        policy[fam] = {"tiers": tiers, "fleet_fastest_s": fleet_fastest,
                       "interactive_line_s": interactive_line(fleet_fastest),
                       "refused": sorted(refused, key=lambda e: (e["box"], e["model"]))}
    return policy


ROLE_SUMMARIES = os.path.join(MPATH.FOUNDRY_MIRROR, "workspace", "a1", "summary", "*-phase7-roles-*.json")   # reduce_a1 --label phase7-roles-<day>
# Lead defaults are the owner's DECISIONS (register), not measurements; each names its evidence.
LEAD_DEFAULTS = {
    "finish_gate": {"value": False, "decided": "2026-10-08", "why": "the gate arm solved the same at 3x the cost"},
    "reviewer": {"value": False, "decided": "2026-10-08", "why": "a second reader (qwen3:8b, thinking on) gave an identical score (#2)"},
    "escalation": {"value": "hard code starts at the lead", "decided": "2026-10-10",
                   "why": "cascade arm on W1 code, reproduced: cheap tiers first then up cost 1.5-2.6x the lead's time and "
                          "energy for the same solves (4.4-7.4x with the Fellowship tier) - the lead's check-writing costs "
                          "as much as solving and only 13-15 of 24 suites were valid (Phase 7 doc, register 10-10). "
                          "RE-READ 2026-10-17 at TRUE xhigh (the 10-10 checks ran at medium - register EFFORT SANDBAG "
                          "#2; checklist A2, two orders x 3 replicates): the same solves at 2.6-4.3x the lead's time "
                          "and 2.6-4.3x its energy from the 8gb tier, 5.3-7.9x its time with the Fellowship; 3-5 of 8 suites "
                          "valid - the rule holds, stronger"},
    "ops_selector": {"value": "W1 ops pieces start on an 8 GB box (qwen3.5:9b, thinking off); tev1 4B (/v1/systemone, "
                              "Softserve) vouches at p_correct >= 0.95; anything it does not vouch for goes WHOLE to the "
                              "lead (Kirk/Spock move it - the judge selects, it never routes)",
                     "decided": "2026-10-17", "preliminary": True,
                     "why": "B3 (submit_w1_judge.py, Project34-Menu/w1_judge.md): bar = the lead's own error rate on "
                            "W1 ops, 0 of 72; tev1 4B @ 0.95 accepted 165 of 287 8 GB answers with 0 wrong (CI 0-2.3 %), "
                            "separating right from wrong within a task; per piece ~6.3 s / ~1.25 kJ against the lead "
                            "alone (thinking off, its cheapest perfect setting) 8.7 s / 3.3 kJ, and ~78 % of ops pieces "
                            "off Mothership. Charter: a checker is a production selector; 'no model picks routing' "
                            "binds placement, not selection. Preliminary: 8 questions, two 8 GB workers, no Fellowship "
                            "ops rows. The owner, 2026-10-17: decide by the charter and the data"},
    "reason_selector": {"value": "none - W1 reason pieces stay the lead's", "decided": "2026-10-17",
                        "why": "B3: the lead's own error rate on W1 reason is 3.1 % (3 of 96, thinking on); the best "
                               "judge (tev1 4B @ 0.99) let through 5.6 % (CI 3.1-10.0), bound 4.7 % with its 33 "
                               "context-refused answers counted correct"},
    "delegation": {"value": "the lead works alone", "decided": "2026-10-11",
                   "why": "Terminal-Bench 2.0 spot set: the 3.8 lead offered ERRANDS (bounded look-ups by a Qwen3.5 9B "
                          "helper, published-guidance brief) sent 0 in 10 tasks and scored 7/10, the same as solo; "
                          "the 8 GB leads score 0-5/10 alone (34-Terminal-Bench-Lead-Level-Grid-Plan). That run was "
                          "really MEDIUM effort (register EFFORT SANDBAG); RE-READ at TRUE xhigh 2026-10-17 (A4): solo "
                          "8/10 and the errand arm 8/10 on the same tasks with 0 errands; 2026-10-19 (B8) the errand "
                          "arm with fetch + simple commands 7/10, 0 errands - 3 runs, 0 errands, the rule holds"},
}
# The AGENTIC lead (family agentic-a1): what HAI's job-shaped calls to Mothership run on (ollama_client.lead_model /
# lead_think). The crown is SHARED by job family - the menu's quick families keep the 3.8 through the
# family rows above; this is the agentic / long-context half. Flash-Next is served on demand through llama-swap.
def _crown():
    """The agentic lead's crown, a decision recorded on its box in boxes.json (boxes.<box>.crown), or None."""
    for box, c in sorted((MPATH.BOXES.get("boxes") or {}).items()):
        if c.get("crown"):
            rec = c["crown"]
            order = [k for k in ("model", "think", "decided", "why") if k in rec] + sorted(set(rec) - {"model", "think", "decided", "why"})
            return dict([("box", box)] + [(k, rec[k]) for k in order])
    return None


CROWN = _crown()


def _box_of(arm):
    return "rigby" if arm.get("layer") == "rigby" else arm["name"].rsplit("_", 1)[-1]


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


# Helper round trips measured before fixes carry two bugs: every desk order POST waited out dead connects
# (11.15 s -> 0.38 s) and each compute-order preflight started PowerShell for a ComfyUI that was not running
# (~9 s -> ~2 s). The post-fix values below are those two stages as measured after the fix; the per-piece stage
# evidence is helper_piece_stages.csv (piece_stages.py). Cold model load is not a bug and is never removed.
STAGES = os.path.join(MPATH.MENU, "helper_piece_stages.csv")
POST_FIX_SUBMIT_S = 0.38
POST_FIX_PREFLIGHT_S = 2.0


def load_stages(path=STAGES):
    """The per-piece stage CSV as {(arm, exercise, piece): {column: float or None}}, or None when it is not there.
    A blank cell is a stage that could not be reconstructed - it stays None, never a guess."""
    if not path or not os.path.exists(path):
        return None
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[(r["arm"], r["exercise"], str(r["piece"]))] = {
                k: (float(v) if v not in (None, "") else None) for k, v in r.items()
                if k not in ("arm", "exercise", "piece", "host")}
    return out


def fixed_round_trip(raw, st):
    """One piece's round trip with the two bugs taken out: raw - max(0, submit->created - post-fix POST)
    - max(0, claimed->spawn - post-fix preflight). A stage the evidence lacks removes nothing; the second return value
    names the stages that were missing so the caller can say how partial the correction is."""
    cut, missing = 0.0, []
    for col, floor in (("submit_to_created_s", POST_FIX_SUBMIT_S), ("claimed_to_spawn_s", POST_FIX_PREFLIGHT_S)):
        v = st.get(col)
        if v is None:
            missing.append(col)
        else:
            cut += max(0.0, v - floor)
    return raw - cut, missing


def compile_roles(summary, stages=STAGES):
    """Lead and helper prices from a reduce_a1 summary (one derivation: the reducer's own totals and piece records).
    Leads = solo arms, pooled per (box, model, effort) across task sets, priced per SOLVE like the menu. Helpers = the
    pieces of team arms, per (helper box, model, path): the helper's own wall, the lead's round trip (submit -> done),
    joules per piece where metered. A pinned pair prices the HELPER; it is never a placement verdict.
    `round_trip_s` stays the raw measurement; `round_trip_s_fixed` is its median with the two desk/preflight
    bugs removed per piece from `stages` (a CSV path or an already-loaded dict), None where there is no evidence."""
    st_map = load_stages(stages) if isinstance(stages, (str, os.PathLike)) or stages is None else stages
    leads, helpers = {}, {}
    for a in summary["arms"]:
        t, lead = a["totals"], a.get("lead") or {}
        if not a.get("team"):
            key = (_box_of(a), lead.get("model") or a["name"], str(lead.get("think")))
            e = leads.setdefault(key, {"box": key[0], "model": key[1], "think": lead.get("think"),
                                       "num_ctx": lead.get("num_ctx"), "solved": 0, "scored": 0, "wall_s": 0.0,
                                       "energy_j": 0.0, "energy_rows": 0, "rows": 0, "capped_rows": 0, "arms": []})
            e["solved"] += t.get("scored_solved") or 0
            e["scored"] += t.get("scored_n") or 0
            e["wall_s"] += t.get("wall_s") or 0.0
            e["energy_j"] += t.get("energy_j") or 0.0
            e["energy_rows"] += t.get("energy_rows") or 0
            e["rows"] += t.get("n") or 0
            e["capped_rows"] += t.get("capped_rows") or 0
            e["arms"].append(a["name"])
            continue
        team = a["team"]
        key = (team.get("helper"), team.get("helper_model"), "window" if team.get("window") else "order")
        h = helpers.setdefault(key, {"helper": key[0], "model": key[1], "path": key[2], "pieces": 0,
                                     "helper_wall_s": [], "round_trip_s": [], "energy_j": 0.0, "metered": 0,
                                     "arms": [], "fixed": [], "no_evidence": 0, "partial": 0, "timed": 0})
        for ex, row in a["rows"].items():
            for p in row.get("pieces") or []:
                if p.get("status") != "done":
                    continue
                h["pieces"] += 1
                h["helper_wall_s"].append(p.get("helper_wall_s"))
                if p.get("done") is not None and p.get("submitted") is not None:
                    raw = p["done"] - p["submitted"]
                    h["round_trip_s"].append(raw)
                    h["timed"] += 1
                    ev = (st_map or {}).get((a["name"], ex, str(p.get("k"))))
                    if ev is None:
                        h["no_evidence"] += 1
                    else:
                        val, missing = fixed_round_trip(raw, ev)
                        h["fixed"].append(val)
                        h["partial"] += bool(missing)
        if t.get("pieces_energy_j"):
            h["energy_j"] += t["pieces_energy_j"]
            h["metered"] += t.get("pieces_done") or 0
        h["arms"].append(a["name"])
    lead_rows = []
    for e in leads.values():
        q = e["solved"] / e["scored"] if e["scored"] else 0.0
        whole = e["energy_rows"] == e["rows"] and e["rows"] > 0
        lead_rows.append({"box": e["box"], "model": e["model"], "think": e["think"], "num_ctx": e["num_ctx"],
                          "solved": e["solved"], "scored": e["scored"], "quality": round(q, 4),
                          "quality_low": round(wilson_low(q, e["scored"]), 4),
                          "s_per_solve": round(e["wall_s"] / e["solved"], 1) if e["solved"] else None,
                          "j_per_solve": round(e["energy_j"] / e["solved"]) if e["solved"] and whole else None,
                          "capped_rows": e["capped_rows"], "arms": e["arms"],
                          "crowned": bool(CROWN) and (e["box"], e["model"], str(e["think"])) == (CROWN["box"], CROWN["model"], CROWN["think"]),
                          "caveats": ["preliminary (n=1 per arm)"]
                          + ([] if whole else ["energy unmetered on some rows: J per solve not stated"])})
    lead_rows.sort(key=lambda r: (-r["quality_low"], r["s_per_solve"] or 1e12))
    helper_rows = []
    for h in helpers.values():
        fixed = _median(h["fixed"])
        caveats = ["a pinned pair prices the helper; never a placement verdict (register 10-10)"]
        if h["pieces"] < 2:
            caveats.append("one piece only")
        if st_map is None:
            caveats.append("round_trip_s_fixed unavailable: stage evidence CSV not found, no guess made")
        elif fixed is None:
            caveats.append("round_trip_s_fixed unavailable: no per-piece stage evidence for this path")
        else:
            if h["no_evidence"]:
                caveats.append("round_trip_s_fixed: %d of %d timed pieces have no stage evidence and are left out"
                               % (h["no_evidence"], h["timed"]))
            if h["partial"]:
                caveats.append("round_trip_s_fixed: %d piece(s) lack a stage (removed nothing for it): a lower "
                               "bound on the correction" % h["partial"])
        helper_rows.append({"helper": h["helper"], "model": h["model"], "path": h["path"], "pieces": h["pieces"],
                            "helper_wall_s": _median(h["helper_wall_s"]), "round_trip_s": _median(h["round_trip_s"]),
                            "round_trip_s_fixed": round(fixed, 1) if fixed is not None else None,
                            "round_trip_fixed_pieces": len(h["fixed"]),
                            "round_trip_basis": ("helper_piece_stages.csv: raw - max(0, submit_to_created - %s) - "
                                                 "max(0, claimed_to_spawn - %s); cold load kept"
                                                 % (POST_FIX_SUBMIT_S, POST_FIX_PREFLIGHT_S)) if fixed is not None
                            else None,
                            "j_per_piece": round(h["energy_j"] / h["metered"]) if h["metered"] else None,
                            "arms": h["arms"], "caveats": caveats})
    helper_rows.sort(key=lambda r: (r["round_trip_s"] is None, r["round_trip_s"] or 0))
    return {"family": "agentic-a1", "crown": CROWN, "lead_defaults": LEAD_DEFAULTS, "leads": lead_rows,
            "helpers": helper_rows}


def load_roles(pattern=ROLE_SUMMARIES):
    """The newest phase7-roles summary (stamped names; the newest is the one in force), or (None, None)."""
    import glob
    paths = sorted(glob.glob(pattern))
    if not paths:
        return None, None
    raw = open(paths[-1], "rb").read()
    return json.loads(raw.decode("utf-8")), {"path": paths[-1], "sha256": hashlib.sha256(raw).hexdigest()[:16]}


def contexts_doc():
    """Each model's measured window on each GPU box - the largest context the lab measured fully resident (the menu
    fill runs every cell at it, so the scores were taken there). HAI reads its windows from HERE, not from a copy."""
    boxes = tuple(b for b in MPATH.BOXES["box_order"] if b in menu_fill.TESTED_CTX)   # the boxes with a tested window table
    out = {b: {m: int(c) for m, c in sorted(menu_fill.TESTED_CTX.get(b, {}).items())} for b in boxes}
    assert all(out[b] for b in boxes), "an empty context row - menu_fill.TESTED_CTX is broken, not empty"
    return dict(out, source="menu_fill.TESTED_CTX (spill-aware fit; campaigns/fit-20261015; BigHonker 9B: fit-20261019-desktop)")


def offload_ok_doc():
    """The (box, model) pairs that run partially resident ON PURPOSE - menu_fill.OFFLOAD_OK, named by the owner, the list the menu cells were measured under. HAI's chat windows read it
    HERE: a window for such a pair declares --offload-ok like the cells did, so the row the menu priced is
    servable - without it the desk refused BigHonker's 8 GB home row (no fully-resident profile) and the factory's
    window would have refused the planned split."""
    return sorted([box, model] for box, model in menu_fill.OFFLOAD_OK)


def offload_ram_doc(root=menu_fill.CAMP):
    """For each OFFLOAD_OK pair, the RAM side of its split as MEASURED by the deliveries that ran it that way: the
    largest size - size_vram over every delivery whose placement says offload and whose rows all finished without an
    error -> {box: {model: {"host_mib", "num_ctx", "n", "sources"}}}. HAI's window door refuses a split window whose
    RAM side does not fit the box's free RAM. A pair never measured split is
    simply absent - the door then refuses it as unmeasured, never guesses a footprint."""
    out = {}
    for path in sorted(glob.glob(os.path.join(root, "*", "*", "out.json"))):
        try:
            with open(path, "rb") as f:
                raw = f.read()
            doc = json.loads(raw.decode("utf-8-sig"))
        except (OSError, ValueError):
            continue
        p = doc.get("placement") or {}
        pair = (doc.get("host"), doc.get("model"))
        rows = doc.get("rows") or []
        size, vram = p.get("size"), p.get("size_vram")
        if not p.get("offload") or pair not in menu_fill.OFFLOAD_OK or not rows or any(r.get("error") for r in rows) \
                or not isinstance(size, (int, float)) or not isinstance(vram, (int, float)) or not 0 < vram < size:
            continue
        e = out.setdefault(pair[0], {}).setdefault(pair[1], {"host_mib": 0, "num_ctx": None, "n": 0, "sources": []})
        e["host_mib"] = max(e["host_mib"], int(-(-(size - vram) // 1048576)))
        e["num_ctx"] = max(e["num_ctx"] or 0, int(doc.get("num_ctx") or 0)) or None
        e["n"] += 1
        e["sources"].append({"path": path, "sha256": hashlib.sha256(raw).hexdigest()})
    return out


def memory_profiles(root=menu_fill.CAMP):
    """The spill-aware 0.40.5 fits already measured the model's private allocation and shared-memory behaviour.

    Use post-upgrade records only. gpu_spill remains the ONE owner of the spill thresholds; policy carries them
    as data so Spock and the Windows guard cannot silently acquire different definitions of "full".
    """
    import importlib.util
    path = os.path.join(menu_fill.MIRROR, "gpu_spill.py")
    spec = importlib.util.spec_from_file_location("policy_gpu_spill", path)
    spill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spill)
    docs = []
    for path in sorted(glob.glob(os.path.join(root, "fit-*", "*", "fit.json"))):
        with open(path, "rb") as f:
            raw = f.read()
        doc = json.loads(raw.decode("utf-8-sig"))
        if str(doc.get("measured_at") or "") >= "2026-10-15":
            docs.append(({"path": path, "sha256": hashlib.sha256(raw).hexdigest()}, doc))
    rules = {"full_margin_mib": spill.FULL_MARGIN_MB, "shared_min_mib": spill.SPILL_MIN_MB}
    result = model_fit.compile_profiles(docs, rules, engine_version="0.40.5")
    assert all(result["hosts"].get(h) for h in sorted(menu_fill.TESTED_CTX)), \
        "memory fit records did not cover every GPU factory with a tested window table"
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    src = argv[argv.index("--standings") + 1] if "--standings" in argv else STANDINGS
    out = argv[argv.index("--out") + 1] if "--out" in argv else OUT
    raw = open(src, "rb").read()
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    assert rows, "zero standings rows - the source is broken, not empty"
    # the one-line chat turn on every tier (reduce_chat1.py -> family "chat-turn"): the simple-chat price
    chat = os.path.join(os.path.dirname(src), "chat1_standings.csv")
    if os.path.exists(chat):
        crows = list(csv.DictReader(open(chat, encoding="utf-8")))
        print("chat-turn rows from %s: %d" % (os.path.basename(chat), len(crows)))
        rows += crows
    stops = load_stops()
    assert stops, "zero stop verdicts read - the hold files are unreadable, not empty"
    pol = compile_policy(rows, stops)
    oh = fellowship_overheads()
    emb = embed_family(embed_deliveries(), fellowship_oh=oh)
    if emb is None:
        print("no embed-price deliveries (campaigns\\embed-*): no %s family - HAI's embeds keep the fixed host list"
              % EMBED_FAMILY)
    else:
        pol[EMBED_FAMILY] = emb
        print("%s family: %s; %d refused" % (EMBED_FAMILY, ", ".join(
            "%s %.0f ms %s J (n=%d)" % (e["box"], 1000 * e["s_per_solve"], e["j_per_solve"], e["n"])
            for e in emb["tiers"][EMBED_TIER]["fastest"]), len(emb["refused"])))
    prices = load_prices()
    stamp_load(pol, prices)
    print("residency prices (measured cold loads): %d box x model pairs, %d loads" % (
        len(prices), sum(v["load_n"] for v in prices.values())))
    reads = read_prices()
    reads.update(rigby_read_prices())
    stamp_read(pol, reads)
    print("re-read prices (measured long reads): %d box x model pairs, %d rows" % (
        len(reads), sum(v["read_n"] for v in reads.values())))
    stamp_fellowship(pol, oh)
    firsts = first_outputs()
    stamp_first_output(pol, firsts)
    print("first visible output (warm window turns): %d box x model pairs, %d turns" % (
        len(firsts), sum(v["first_out_n"] for v in firsts.values())))
    print("Fellowship desk-order overhead before the first token, per worker: %s" % (
        ", ".join("%s %s s (n=%d)" % (b, oh[b]["overhead_s"], oh[b]["overhead_n"]) for b in sorted(oh))
        or "unmeasured"))
    n_stopped = sum(1 for p in pol.values() for e in p["refused"] if e["why"].startswith("stopped"))
    assert n_stopped, "the stop verdicts matched ZERO standings rows - the key join is broken"
    doc = {"generated": datetime.datetime.now().isoformat(timespec="seconds"), "source": src,
           "source_sha256": hashlib.sha256(raw).hexdigest()[:16], "stop_verdicts": len(stops),
           "rows_stopped": n_stopped, "tier_homes": TIER_HOMES, "families": pol,
           "contexts": contexts_doc(), "model_fits": memory_profiles(), "offload_ok": offload_ok_doc(),
           "offload_ram": offload_ram_doc()}
    summary, src_info = load_roles()
    if summary is not None:
        roles = compile_roles(summary)
        assert roles["leads"] and roles["helpers"], "the roles summary gave no lead or no helper rows - wrong summary"
        doc["roles"] = dict(roles, source=src_info)
        print("roles from %s: %d lead rows, %d helper rows" % (os.path.basename(src_info["path"]),
                                                              len(roles["leads"]), len(roles["helpers"])))
    else:
        print("no phase7-roles summary found: the table carries no role rows")
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(out + ".tmp", out)
    print("%d stop verdicts read, %d standings rows stopped by them" % (len(stops), n_stopped))
    for fam, p in pol.items():
        for tier, t in p["tiers"].items():
            print("%-10s %-10s " % (fam, tier) + " | ".join(
                "%s %s" % (lens.replace("_", " "), ["%s%s" % (e["box"], "" if e["role"] == "home" else "*")
                                                    for e in t[lens]]) for lens in LENSES))
    print("(* = overflow, with caveat)")
    print("written", out)


if __name__ == "__main__":
    main()
