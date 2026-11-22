"""Project 34 menu filler - the FULL GPU grid, kept topped up through the normal desk. Scope rule:
every model on every box at every knob; nothing is left out except a model that cannot generate text at all.

Grid per box: every model its ollama holds whose capabilities include "completion"
  x family: coding-f1, coding-f2, tool, and for "vision" models vlm (1x images) and vlm-2x
  x thinking: off, and on for "thinking" models
  x temperature 0.0 0.3 0.6 0.9 x seed set (seed-base) 1 4 7
Context: the tested max for that model on that box (config.MODEL_NUM_CTX / RUNBOOK residency map); an untested model
runs at the box's bench standard below, and every row that hits the context is counted by the reducers, so a binding
limit is visible and the cell re-runs once the model's fit is measured ().

Top-up: each run counts every box's outstanding orders (submitted, not yet delivered) and submits the next missing cells
until that box has TARGET waiting - breadth first (every model and knob once before more seeds). "Already ordered" is
read from every order record ever written, so re-runs never duplicate. An order the factory cancelled (tombstone) or
retired without delivering (menu_boxstate.py) no longer counts as outstanding and its cell is re-orderable.

Holds (menu_fill_hold.json) - a hold is a decision, and carries its evidence:
  "<box>" the whole box gets nothing
  "<box>/<model>" one model's cells
  "<box>/<model>/<family>/<thinking>" ONE ARM (an arm = model x family x thinking on one box, the unit the
                                            stop rule judges); family is f1 f2 tool vlm vlm2x or *, thinking is off on or *
  a value is "<why>" or {"why":..., "until": "YYYY-MM-DDTHH:MM", "except": ["0.6/4"]}: `until` lapses by itself;
  `except` (arm and model holds) names "<temperature>/<seed set>" cells the hold leaves open - the one repeat cell an
  arm needs before it can be called priced out on its own box. `"orders_only": true` = stop ORDERING the cells, but
  the arm's priced rows still PLACE. Without it a hold is a verdict on the
  arm and placement refuses it too (menu_policy.refusal).

Price line: every GPU order carries `--price-ceiling-s` = PRICE_X x the worst per-task cheapest COLD solve of its family
on ITS box (menu_price.py), computed here from that box's own delivered rows; infer_ollama cuts an answer that reaches it
and records it priced_out - a cost measurement, distinct from a context-cap row (void). The control is never cut.

    python menu_fill.py plan # missing cells and outstanding orders per box, submits nothing
    python menu_fill.py topup # submit up to TARGET outstanding per box (the scheduled task runs this)"""
import collections, datetime, glob, json, os, re, statistics, subprocess, sys, urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import menu_boxstate as BS  # noqa: E402
import menu_price as MP  # noqa: E402
import menu_void as MV  # noqa: E402
import task_versions as TVER  # noqa: E402  a versioned task is one question here: coverage and price follow its current id

import menu_paths as MPATH  # noqa: E402  every folder, mirror, box name and box table the menu reads
CAMP = MPATH.CAMPAIGNS
TV = os.path.join(CAMP, "hard-bench-20260927", "fleet-tv")
HOLD = os.path.join(HERE, "menu_fill_hold.json")
PRICE_CACHE = os.path.join(HERE, "price_history_cache.json")
CACHE_V = 3                                                     # bump when the condensed row shape changes
MIRROR = MPATH.FOUNDRY_MIRROR                                   # infer_ollama.Grader: the on-box grader, reused here
PAYLOADS = MPATH.PAYLOADS
TARGET = 25
LS = MPATH.LS                 # box addresses, the head and the share are settings (FLEET_HOSTS, HAI_URL, ...)
_FILL_ORDER = MPATH.fill_order()                                 # the order cells are filled box by box; others follow by name
BOX = {n: "http://%s:%s" % (a, LS.setting("OLLAMA_PORT", "11434"))
       for n, a in sorted(LS.fleet_hosts().items(),
                          key=lambda kv: (_FILL_ORDER.index(kv[0]) if kv[0] in _FILL_ORDER else len(_FILL_ORDER), kv[0]))}
# Each box's tested contexts (the spill-aware fit probes), its bench standard and the champion are boxes.json's
# (menu_paths): a box's table is its own measured facts, never inferred from size.
TESTED_CTX = MPATH.tested_ctx()
BENCH_CTX = MPATH.bench_ctx()                                    # the families' long-standing bench standard per box
# Models run partially resident on purpose (infer_ollama --offload-ok records the placement instead of refusing).
# Named per box by the owner, never inferred from size: BigHonker's qwen3.5:35b is "in the store only" (RUNBOOK residency
# map) on a 16 GB-RAM shared desktop, and a size rule would have started running it there.
# BigHonker's A3B: experts in RAM BY DESIGN
# the owner "Offload allowed."
OFFLOAD_OK = {(b, m) for b, c in MPATH.BOXES["boxes"].items() for m in c.get("offload_ok", [])}   # boxes.json
# Models a box serves on STRATA, not ollama (infer_ollama --strata starts and stops the server per order). ollama does
# not list them, so they are named here and their orders are pinned to the box. Flash-Next: #34 Phase 2 PRELIMINARY
# WINNER
# priced per family to decide keep / share / move. A ROLE test, not a grid:
# ONE cell per family at its top config - xhigh at its vendor temperature, seed set 1. Context = its TRAINED
# 262,144 (GGUF context_length), measured clean (order flashnext-strata-plateau-ctx262k: same write/read as
# 131K, deep read 191,857 tokens with the needle found) - the owner: "would it be worth pursuing bringing flash next to
# maximum context?"
# The folder of Strata's serving plans, as the box that runs them names it (STRATA_PLANS_DIR). Unset = no Strata arm:
# the entry below is the lab's own measured arm on its AMD box, and a lab without that folder orders nothing from it.
STRATA_PLANS = LS.setting("STRATA_PLANS_DIR", "")
STRATA = {("mothership", "qwen3.8-flash-next-strata-q2_0"): {
    "plan": STRATA_PLANS + r"\strata-serve-q2_0-v045.20261018.json", "ctx": 262144,   # Strata v0.1.45 best (10-18)
    "caps": ["completion", "thinking", "tools"], "thinks": ("xhigh",), "temperature": "0.6"}} if STRATA_PLANS else {}
TEMPS = ("0.0", "0.3", "0.6", "0.9")
SEEDS = ("1", "4", "7")
# Depth per arm: every ARM stays covered - the 09-28/29
# "huge thing / every knob" rule corrected a session collapsing arms - but each arm is ordered at greedy once (one is
# enough: deterministic), 0.6 once, and 0.6 again in a second seed set (the charter's "reproduces once"). Cells
# already delivered at other knobs stay on the menu.
CORE_KNOBS = {("0.0", "1"), ("0.6", "1"), ("0.6", "4")}
# The vendor's own temperature, one more cell per arm at seed set 1 beside the uniform pair. The value is the one the model's tag
# carries - official tags ship the vendor's defaults, configure_new_models.py sets the rest. A tag carries ONE value, the
# thinking-mode one, so a card's different non-thinking or tool-calling value is named here (Qwen3/3.5/3.6/3.8 and
# Qwen3-VL cards: instruct 0.7; MiniCPM5: 0.7 without thinking; Nemotron 3 Nano: 0.6 for tool calling). No stated
# temperature = no vendor cell; a vendor value that is already a core knob adds nothing.
VENDOR_THINK_OFF = {"qwen3:": "0.7", "qwen3.5:": "0.7", "qwen3.6:": "0.7", "qwen3.8:": "0.7",
                    "qwen3.8-flash-next:": "0.7", "qwen3.8-flash-next-strata-": "0.7", "qwen3-vl:": "0.7", "huihui_ai/qwen3-vl-abliterated:": "0.7",
                    "minicpm5:": "0.7", "Qwen3.6-": "0.7", "MiniCPM5-": "0.7"}
VENDOR_TOOL = {"nemotron-3-nano:": "0.6", "Nemotron-3-Nano-": "0.6"}
# rigby's llama.cpp files carry no sampling: their vendor temperature, by file name (Qwen2.5 generation_config 0.7)
RIGBY_VENDOR = {"Qwen2.5-": "0.7", "Qwen3.6-": "1.0", "Nemotron-3-Nano-": "1.0", "MiniCPM5-": "0.9"}


def _named(table, model):
    return next((v for p, v in table.items() if model.startswith(p)), None)


def vendor_temp(model, think, fam, carried):
    """The vendor's temperature for one arm as '%.1f' (`carried`: the tag's own temperature), or None."""
    t = (fam == "tool" and _named(VENDOR_TOOL, model)) or (think == "off" and _named(VENDOR_THINK_OFF, model)) or carried
    return "%.1f" % float(t) if t is not None else None


def rigby_carried(models):
    return {m: _named(RIGBY_VENDOR, m) for m in models}


def is_vendor_cell(c, carried):
    """A seed-set-1 cell at its arm's vendor temperature. `carried` is {model: the tag's temperature} for c's box."""
    return c[5] == "1" and c[4] == vendor_temp(c[1], c[3], c[2], carried.get(c[1]))


def core(cells, carried=None):
    """The cells the filler ORDERS: the grid at CORE_KNOBS, plus each arm's vendor cell when `carried` is given. grid()
    stays the full knob space for counts and holds."""
    return [c for c in cells if (c[4], c[5]) in CORE_KNOBS or (carried is not None and is_vendor_cell(c, carried))]


def open_vendor_cells(stops, carried_of):
    """An AUTOMATIC stop leaves its arm's vendor cell open (`except`): the rule judged the arm at the uniform knobs only,
    which is the gap the vendor cell measures. A human hold is a ruling and is not touched. carried_of(box) ->
    {model: tag temperature}."""
    out = {}
    for key, v in stops.items():
        arm = parse_arm(key)
        vt = vendor_temp(arm[1], arm[3], arm[2], carried_of(arm[0]).get(arm[1])) if arm else None
        if vt and (vt, "1") not in CORE_KNOBS:
            v = dict(v, **{"except": sorted(set(v.get("except") or []) | {"%s/1" % vt})})
        out[key] = v
    return out
LEGACY_TV = {"tool-9b": "qwen3.5:9b", "tool-27b": "qwen3.6:27b"}
FAMS = ("f1", "f2", "w1", "c1", "tool", "vlm", "vlm2x")       # a grid cell's family; the review pages say coding-f1 etc.
FAM_ALIAS = {"coding-f1": "f1", "coding-f2": "f2", "work-w1": "w1", "chat-c1": "c1", "vlm-2x": "vlm2x"}
THINKS = ("off", "on", "xhigh")
# Models whose `think: true` renders BELOW the vendor's default effort get the level as its own thinking arm, sent as
# ollama's level string (infer_ollama --think-level). qwen3.8: true = MEDIUM, the vendor default is xhigh.
EFFORT_LEVELS = {"qwen3.8:": ("xhigh",)}


def think_label(args):
    """An order's thinking setting from its payload args - ONE derivation for records() and build_menu."""
    return arg(args, "--think-level") or ("on" if "--think" in args else "off")
GRADE_PAYLOAD = {"f1": "map_swarm_code_hard.py", "f2": "map_swarm_code_hard_f2.py", "w1": "map_swarm_code_hard_w1.py",
                 "c1": "map_swarm_code_hard_c1.py", "tool": "map_tool_swarm_hard.py",
                 "vlm": "map_vlm_swarm_hard.py", "vlm2x": "map_vlm_swarm_hard.py"}
# A cell whose order was RETIRED without delivering (stopped or failed, no human cancel) is re-ordered once; a second
# such death is machinery misbehaving, not the cell, so it is parked for a human instead of looping ().
# A human cancel (tombstone) is a decision and never counts toward this.
RETIRED_PARK = 2

# rigby (Tolkien kind "llm", arm rigby) - coding f1/f2 and tool through the tablet inbox; a tool cell is the GPU tool
# cell's test (same 24 tasks, PREAMBLE and golds), constrained by the payload's own GBNF and graded by its score_tool.
TABJOBS = MPATH.TABJOBS
RIGBY_MANIFEST = os.path.join(TABJOBS, "rigby-models.json")      # published by Tolkien (tolkien_llm.publish_models)
RIGBY_SUBS = os.path.join(CAMP, "hard-bench-20260927", "rigby", "submissions.json")
RIGBY_PAYLOAD = {k: MPATH.TABJOBS_SEEN + "/payloads/" + v for k, v in                      # as Tolkien sees the share
                 {"f1": "map_swarm_code_hard.py", "f2": "map_swarm_code_hard_f2.py", "w1": "map_swarm_code_hard_w1.py",
                  "c1": "map_swarm_code_hard_c1.py", "tool": "map_tool_swarm_hard.py"}.items()}
RIGBY_FAMS = ("f1", "f2", "w1", "c1", "tool")


def is_c1(name):
    """C1 by its exact tokens: "c1" alone also matches the W1 tablet lane c15b."""
    return any(t in name for t in ("_c1.", "_c1_", "-c1-"))
RIGBY_CTX = 32768             # Qwen2.5's trained context and the MoE's tested value on rigby; the payload default 2048
                              # capped the MoE (rigby-12: 29 f2 rows ended on "length")
RIGBY_THINKING = {"Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf",              # named, never inferred from the file name
                  "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"}
# rigby's placements beyond the CPU path, per model FILE (the file is the menu's key, so a 1060 row never merges with a
# CPU-era row): the order fields and the measured marginal watts. The standard 1060 order ([[Fellowship-Order-Taker-
# Design]] § the standard 1060 llm order, r66-r74a): ~16.8 tok/s write on code. Watts: A3B writing on the 1060 over idle,
# r51 +78, r54 +111, r61a +90, r62a +81 -> median ~85 W, n=4 (energy_history.py); the CPU path keeps
# menu_policy.RIGBY_MARGINAL_W (54 W, plug-metered). menu_policy reads this same object.
# `since` = the placement's hardware epoch: a delivery of the file finished before it was priced on hardware that is
# gone, so the filler re-orders its cells and build_menu supersedes its rows once the setting has newer ones (until then
# they still price - a stale price beats a hole). The 1060's x1 riser was swapped for a full-width one; the identical c1
# order then read ~2K-token prompts 3.5x faster with 61/61 answers token-identical (the Rigby whole-box synthesis, § 8).
# "watts" re-measured on the x16 riser: 78 W working-over-idle at the wall = card ~15 + CPU ~63. One regression over
# every 10-60 s bin of four days (26,040 bins, R2 0.92-0.97): APC W (Softserve's per-second telemetry, ups_load_pct x 9)
# on the 1060's own board power + rigby CPU % (Tolkien telemetry) + Softserve's GPU W + CPU % (its own activity, so the
# two boxes on one APC are told apart). Controls: Softserve's GPU 1.20 W per board W; rigby's CPU 1.40 W/% x 40 pts =
# 56 W, the plug's independent 54-56 W. The 1060's slope (1.02-1.23) says its 600 W supply IS on the APC. Script:
# apc_split.py (in the Journal). The 85 W before was 4 APC pairings in the x1 era.
# `fields` = rigby's PRODUCTION launch for the file, read from Tolkien's own rigby_profiles.LAUNCH (the Tolkien mirror):
# one copy of what the menu prices and what rigby runs on every launch (production uses the best settings; per-order
# settings are for tests). The menu adds only the pricing facts (watts, epoch).
sys.path.insert(0, MPATH.TOLKIEN_MIRROR)
import rigby_profiles  # noqa: E402
RIGBY_PLACEMENT = {
    "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf": {
        "name": "gtx1060-standard", "watts": 78.0, "since": "2026-10-26T14:45",
        "measured": "x16 riser, APC regression on rigby + Softserve activity, 26,040 bins, 2026-10-30",
        "fields": rigby_profiles.LAUNCH["Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"]},
}
# The tablets' tested context: the f2 overnight lanes' measured setting, which W1, C1 and the CHAT1 warm orders all ran
# at (hard_bench_calibrate.py; [[34-Hard-Bench-Order-Spec]] § W1 "ctx 16,384, generation ended on idleness").
TABLET_CTX = 16384


def placement_since(model):
    """The file's placement epoch (RIGBY_PLACEMENT `since`), or None."""
    s = (RIGBY_PLACEMENT.get(model) or {}).get("since")
    return datetime.datetime.fromisoformat(s) if s else None


def delivered_at(path):
    """A Tolkien result file's delivery stamp (`<order>.<YYYYMMDD-HHMMSS>.results.json`, Softserve-local), or None."""
    m = re.search(r"\.(\d{8}-\d{6})\.results\.json$", os.path.basename(str(path or "")))
    return datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S") if m else None


def placement_stale(model, path):
    """True when this delivery of `model` predates its placement's `since`. A stamp that cannot be read counts as stale
    (re-measure rather than let an unknown-era row price the new hardware)."""
    since = placement_since(model)
    if since is None:
        return False
    at = delivered_at(path)
    return at is None or at < since


def supersede_pre_placement(rows):
    """build_menu's pass: a rigby row delivered before its placement's `since` is not usable once its SETTING (the
    standings key: box, family, shape, model, thinking, temperature) has a row delivered after it. Returns how many."""
    def key(r):
        return (r["box"], r["family"], r["shape"], r["model"], r["think"], str(r["temperature"]))
    mine = [r for r in rows if r.get("box") == "rigby" and placement_since(r.get("model")) is not None]
    fresh = {key(r) for r in mine if not placement_stale(r["model"], r["source_file"])}
    n = 0
    for r in mine:
        if key(r) in fresh and placement_stale(r["model"], r["source_file"]) and r["usable"] == "True":
            p = RIGBY_PLACEMENT[r["model"]]
            r["usable"], r["void"] = "False", "priced before %s's %s epoch; superseded by its re-measure" % (
                p["name"], p["since"])
            n += 1
    return n


def rigby_order_fields(model, think, temperature):
    """The Tolkien `llm` fields of a rigby cell: the rigby arm, the grid's context, the cell's sampling and the FILE's own
    placement (the 1060 standard order for the MTP file). One derivation: rigby_submit orders a cell with these and
    menu_policy stamps them on the cell's table row, so the row that places a Fellowship piece also sets it
    ([[34-Phase-7-Placement-Policy-Design]] § D)."""
    out = {"arms": ["rigby"], "model": model, "ctx": RIGBY_CTX, "temperature": float(temperature)}
    if model in RIGBY_THINKING:
        out["reasoning"] = think
    placement = RIGBY_PLACEMENT.get(model)
    if placement:                      # the file's own placement (the 1060 standard order), never a CPU run of it
        out.update(json.loads(json.dumps(placement["fields"])))
    return out


def tablet_order_fields(model, think, temperature):
    """The Tolkien `llm` fields of a tablet row: a WARM answer on ONE node (Tolkien picks it: the fastest measured
    healthy node its class rules allow), the tablets' tested context, the row's sampling. A row priced on one class
    names it after the model ("... @class A (warm)"); the model is the name before it."""
    out = {"arms": ["warm"], "model": str(model).split(" @class ")[0].strip(), "warm_nodes": 1, "ctx": TABLET_CTX}
    try:
        out["temperature"] = float(temperature)
    except (TypeError, ValueError):
        pass                           # "": the payload's own default, as the row was measured
    if think in ("on", "off"):
        out["reasoning"] = think
    return out


PRICE_X = 10                  # priced out at 10x the cheapest measured solve on the same box (34-Hard-Bench-Order-Spec)
# Families measured at full capability: no per-answer price ceiling, only the box's tested context bounds an answer.
# The stop rules still judge each arm from its delivered rows (cannot fit / priced out). the owner on W1: "for
# quality we want to see what it comes up with" (a W1 reference from a fast model would cut a thinking 27B mid-answer).
UNCEILED = {"w1"}
#... on the frontier box only. On the helper boxes a W1 cell is a final-answer starting suggestion, not the helper's
# price (34-CHARTER: helper value is the S10 sub-agent shapes; an arm is not culled for failing alone), so the box's
# price line applies there.
UNCEILED_BOXES = {b for b, c in MPATH.BOXES["boxes"].items() if c.get("unceiled")}   # boxes.json


def unceiled(box, fam):
    return fam in UNCEILED and box in UNCEILED_BOXES


TOPUP_S = 900                # the "HAI P34 menu fill" task's repetition
RIGBY_QUEUE_S = 2 * TOPUP_S   # queued work must outlast one interval plus Tolkien's inbox intake


def arg(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default


def slug(s):
    return re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------------------------------------------------- holds
def parse_arm(key):
    """'<box>/<model>/<family>/<thinking>' -> (box, model, family, thinking), or None when the key is not an arm hold.
    Read from the right: a model name may itself contain '/' (aha2025/llama-joycaption...), a family and a thinking
    setting never do. The family and thinking words are a closed vocabulary, so a `<box>/<model>` hold is never
    mistaken for an arm."""
    parts = key.rsplit("/", 2)
    if len(parts) != 3 or "/" not in parts[0]:
        return None
    prefix, fam, think = parts
    fam = FAM_ALIAS.get(fam, fam)
    if fam not in FAMS + ("*",) or think not in THINKS + ("*",):
        return None
    box, model = prefix.split("/", 1)
    return box, model, fam, think


def hold_why(value):
    return value.get("why", "") if isinstance(value, dict) else str(value)


def keep_cells(value):
    """{(temperature, seed set)} an arm/model hold leaves open: {"except": ["0.6/4"]} -> {("0.6", "4")}."""
    out = set()
    for e in (value.get("except") or []) if isinstance(value, dict) else []:
        t, sep, sb = str(e).partition("/")
        if not sep:
            raise ValueError("hold 'except' entries are '<temperature>/<seed set>', got %r" % (e,))
        out.add(("%.1f" % float(t), str(int(sb))))
    return out


def cell_hold(hold, cell):
    """The hold value that stops this grid cell (box, model, family, thinking, temperature, seed set), or None.
    A whole-BOX hold is not decided here: it stops submissions, while `missing` still counts what it would order."""
    box, model, fam, think, t, sb = cell
    for key, value in hold.items():
        if key == "%s/%s" % (box, model):
            arm = (box, model, "*", "*")
        else:
            arm = parse_arm(key)
        if not arm or arm[0] != box or arm[1] != model or arm[2] not in ("*", fam) or arm[3] not in ("*", think):
            continue
        if (t, sb) in keep_cells(value):
            continue
        return value
    return None


def load_hold(path=HOLD, now=None):
    """menu_fill_hold.json minus the holds whose `until` has passed (local time)."""
    hold = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            hold = json.load(f)
    now = now or datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    return {k: v for k, v in hold.items() if not (isinstance(v, dict) and v.get("until") and now >= v["until"])}


def hold_counts(hold, cells):
    """{hold key: grid cells it stops} for every model or arm hold whose box owns one of these cells. A key that stops
    NOTHING on a grid it addresses prints as 0: a filter that selects nothing must say so ()."""
    counts = {}
    boxes = {c[0] for c in cells}
    for key in hold:
        arm = parse_arm(key)
        box = arm[0] if arm else key.split("/", 1)[0]
        if "/" not in key or box not in boxes:
            continue
        counts[key] = sum(1 for c in cells if c[0] == box and cell_hold({key: hold[key]}, c) is not None)
    return counts


# ---------------------------------------------------------------------------------------------------------- the grid
def tag_temperature(show):
    """The temperature an /api/show reply's parameters carry, or None."""
    for line in (show.get("parameters") or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "temperature":
            return parts[1]
    return None


def box_models(box, carried=None):
    """{model: capabilities} from the box's own ollama, or None when it cannot be asked (then it gets no orders).
    `carried`, when given, is filled with {model: the temperature its tag carries}."""
    try:
        tags = json.load(urllib.request.urlopen(BOX[box] + "/api/tags", timeout=20)).get("models", [])
        out = {}
        for m in tags:
            req = urllib.request.Request(BOX[box] + "/api/show", data=json.dumps({"model": m["name"]}).encode(),
                                         headers={"Content-Type": "application/json"})
            show = json.load(urllib.request.urlopen(req, timeout=30))
            out[m["name"]] = show.get("capabilities") or []
            if carried is not None:
                carried[m["name"]] = tag_temperature(show)
        for (b, model), s in STRATA.items():               # served by Strata on this box: named, not listed
            if b == box:
                out[model] = list(s["caps"])
                if carried is not None:
                    carried[model] = s["temperature"]
        return out
    except Exception as e:                                           # noqa: BLE001
        print("  %s: cannot list models (%s) - no orders this run" % (box, type(e).__name__))
        return None


def _temps(model, think, fam, carried):
    if carried is None:
        return TEMPS
    vt = vendor_temp(model, think, fam, carried.get(model))
    return TEMPS + ((vt,) if vt and vt not in TEMPS else ())


def grid(box, models, carried=None):
    """Every cell of the box's knob space; `carried` ({model: tag temperature}) adds each arm's vendor temperature."""
    cells = []
    for sb in SEEDS:                                                 # breadth first
        for model, caps in sorted(models.items()):
            if "completion" not in caps:
                continue
            fams = ["f1", "f2", "w1", "c1", "tool"] + (["vlm", "vlm2x"] if "vision" in caps else [])
            thinks = (["off", "on"] + list(_named(EFFORT_LEVELS, model) or ())) if "thinking" in caps else ["off"]
            if (box, model) in STRATA:                # a role test: its top config only, once per family
                s = STRATA[(box, model)]
                if sb == SEEDS[0]:
                    cells += [(box, model, fam, th, s["temperature"], sb)
                              for fam in ["f1", "f2", "w1", "c1", "tool"] for th in s["thinks"]]
                continue
            for fam in fams:
                for think in thinks:
                    for t in _temps(model, think, fam, carried):
                        cells.append((box, model, fam, think, t, sb))
    return cells


# ------------------------------------------------------------------------------------------------------- the ledger
def _host_of(out_path, only):
    if only and len(only) == 1:
        return only[0]
    for h in BOX:
        if ("_%s" % h) in (out_path or ""):
            return h
    return None


def _record(cell, out_path, host):
    folder = os.path.basename(os.path.dirname(out_path)) if out_path else ""
    return {"cell": cell, "out": out_path or "", "host": host,
            "names": BS.candidate_names(folder, host) if folder else []}


def records():
    """Every GPU order ever submitted, one dict per order record: {cell, out, host, names}. The ledger and the price
    history read the SAME records (one derivation), so a cell can never be 'delivered' to one and absent to the other."""
    out = []
    for sub in glob.glob(os.path.join(CAMP, "hard-bench-*", "fleet*", "submission.json")):
        folder = os.path.dirname(sub)
        if os.path.basename(folder) == "fleet-tv":
            continue
        for rung, rec in (json.load(open(sub, encoding="utf-8")).get("orders") or {}).items():
            of = os.path.join(folder, "order_%s.json" % rung)
            try:
                body = json.load(open(of, encoding="utf-8"))
            except Exception:
                continue
            args = (body.get("spec") or {}).get("args") or []
            host = _host_of(rec.get("out"), body.get("only"))
            if not host:
                continue
            grade = arg(args, "--grade") or ""
            fam = ("c1" if is_c1(grade) or is_c1(folder + "-") else
                   "w1" if ("_w1" in grade or "-w1" in folder) else
                   "f2" if ("_f2" in grade or "-f2" in folder) else "f1")
            temp = arg(args, "--temperature")
            key = (host, arg(args, "--model"), fam, think_label(args),
                   "%.1f" % float(temp) if temp is not None else None, arg(args, "--seed-base", "1"))
            out.append(_record(key, rec.get("out"), host))
    saved_argv = sys.argv
    sys.argv = [sys.argv[0]]
    try:
        import hard_bench_fleet_tv as T
    finally:
        sys.argv = saved_argv
    tvsub = json.load(open(os.path.join(TV, "submission.json"), encoding="utf-8")).get("orders", {})
    for rung, rec in tvsub.items():
        if rung not in T.RUNGS:
            continue
        division, model, _ctx = T.RUNGS[rung]
        host = rec.get("host") or _host_of(rec.get("out"), T.ONLY.get(rung))
        if not host:
            continue
        fam = "tool" if division == "tool" else ("vlm2x" if rung.startswith("vlm2x") else "vlm")
        kn = T.KNOBS.get(rung, {})
        key = (host, rec.get("model") or model, fam,
               T.THINK_LEVEL.get(rung) or ("on" if rung in T.THINK_RUNGS else "off"),
               "%.1f" % float(kn.get("temperature", T.TEMP)), str(kn.get("seed_base", 1)))
        out.append(_record(key, rec.get("out"), host))
    return out


_VOIDS = []


def _voids():
    if not _VOIDS:
        _VOIDS.append(MV.load())
    return _VOIDS[0]


def voided(rec):
    """True when the record's delivery is on the instrument-fault void list (menu_void)."""
    return MV.is_voided(rec["out"], _voids())


def delivered_out(rec):
    """The delivered out.json of an order record, or None: the record's own folder, else the newest RESUMED folder
    (`<folder>.<stamp>` - an order the pause machinery stopped and resumed ships into a second folder, and its record
    still names the first; three delivered cells read as outstanding forever because of it). A voided delivery is
    not delivered (menu_void)."""
    if voided(rec):
        return None
    if rec["out"] and os.path.exists(rec["out"]):
        return rec["out"]
    base = os.path.dirname(rec["out"]) if rec["out"] else ""
    hits = glob.glob(os.path.join(glob.escape(base) + ".*", "out.json")) if base else []
    return max(hits, key=os.path.getmtime) if hits else None


def ledger(desk=None, recs=None):
    """cell -> {"delivered", "outstanding", "cancelled", "retired": order counts}. `desk` is the desk's own record of
    every factory order (menu_boxstate.read_desk, read-only; None = could not be read, every order stays outstanding),
    one derivation for every GPU box."""
    seen = {}
    for rec in (recs if recs is not None else records()):
        verdict = ("dead:cancelled" if voided(rec) else          # an instrument fault, voided by a human: re-order
                   BS.classify(rec["names"], delivered_out(rec) is not None, desk))
        e = seen.setdefault(rec["cell"], {"delivered": 0, "outstanding": 0, "cancelled": 0, "retired": 0})
        e[verdict.split(":")[-1]] += 1
    return seen


def cell_state(e):
    """'delivered' > 'outstanding' > 'dead' (every order for the cell was cancelled or retired without delivering)."""
    return "delivered" if e["delivered"] else "outstanding" if e["outstanding"] else "dead"


def to_order(e):
    """-> (order it?, attempt number, why not). A free cell is order #1. A dead cell is re-ordered; a cell whose orders
    were retired without delivering RETIRED_PARK times is parked for a human."""
    if e is None:
        return True, 1, ""
    if cell_state(e) != "dead":
        return False, 0, ""
    if e["retired"] >= RETIRED_PARK:
        return False, 0, "retired without delivering %d times - machinery, not the cell: needs a human" % e["retired"]
    return True, sum(e.values()) + 1, ""


def plan_cells(cells, hold, mine):
    """-> (missing [(cell, attempt)] in grid order, number held by a model/arm hold, parked [(cell, why)]).
    `mine` is the ledger for this box. A held cell is never ordered, whatever the ledger says about it."""
    missing, held, parked = [], 0, []
    for c in cells:
        if cell_hold(hold, c) is not None:
            held += 1
            continue
        go, attempt, why = to_order(mine.get(c))
        if go:
            missing.append((c, attempt))
        elif why:
            parked.append((c, why))
    return missing, held, parked


# ------------------------------------------------------------------------------------------------------ price line
def family_tasks(fam):
    sys.path.insert(0, HERE)
    if fam in ("vlm", "vlm2x"):
        import hard_vlm_tasks
        return list(hard_vlm_tasks.TASKS)
    return rigby_tasks(fam)


def _grader(fam, graders, task=None):
    """infer_ollama.Grader for the family - the one on-box grader, so a row graded here matches a row graded there.
    With `task`, the payload generation that defines it (task_versions; the family's named payload when none does)."""
    names = [n for i, n in enumerate(TVER.generations(GRADE_PAYLOAD[fam]))
             if i == 0 or os.path.exists(os.path.join(PAYLOADS, n))]
    for name in names:
        if name not in graders:
            try:
                sys.path.insert(0, MIRROR)
                import infer_ollama
                graders[name] = infer_ollama.Grader(os.path.join(PAYLOADS, name))
            except (Exception, SystemExit) as e:                     # noqa: BLE001
                print("  price history: no grader for %s (%s) - its ungraded rows are left out" % (name, str(e)[:100]))
                graders[name] = None
        g = graders[name]
        if g is not None and (task is None or task in getattr(g.mod, "TASKS", {})):
            return g
    return graders[names[0]]


def read_out(path, fam, graders):
    """One delivered out.json -> condensed rows {task, wall_s, ok, prompt_tokens, priced_out}. A row the order graded
    on its box keeps that verdict; an older ungraded row is graded with the same Grader; a cut row is never graded."""
    d = json.load(open(path, encoding="utf-8"))
    rows = []
    for r in d.get("rows") or []:
        if str(r.get("id", "")).startswith("control"):
            continue
        cut = MP.row_status(r) == "priced_out"
        ok = False if cut else (bool(r["ok"]) if r.get("ok") is not None else None)
        if ok is None and not r.get("error"):
            g = _grader(fam, graders, r.get("id"))
            ok = (g.grade(r) if g else {}).get("ok")
        rows.append({"task": TVER.current(r["id"]), "model": d.get("model") or "", "wall_s": r.get("wall_s"), "ok": ok,
                     "prompt_tokens": MP.prompt_read(r), "priced_out": cut, "void": MP.row_status(r) == "void",
                     "loop": r.get("done_reason") == "loop"})
    return rows


def handrun_sources(known):
    """(path, box, family) for delivered GPU result files no order record names: the hand-run rungs (`out_<rung>.json`)
    and orders whose folders were never recorded. They are real measured rows on a real box, so the price line reads
    them too; the box is the file's own `host`, the family comes from its folder or rung name."""
    out = []
    pats = ("fleet*/out_*.json", "fleet*/*/out.json", "fleet*/*/*/out.json")
    for pat in pats:
        for p in glob.glob(os.path.join(CAMP, "hard-bench-*", pat)):
            if p in known or "vision-factor" in p or "progress" in p:
                continue
            try:
                host = (json.load(open(p, encoding="utf-8")).get("host") or "").lower()
            except Exception:                                        # noqa: BLE001
                continue
            if host not in BOX:
                continue
            rung = os.path.basename(p)[len("out_"):-len(".json")] if os.path.basename(p).startswith("out_") else ""
            if "fleet-tv" in p:
                fam = "tool" if rung.startswith("tool") else "vlm2x" if rung.startswith("vlm2x") else "vlm"
            else:
                fam = "c1" if is_c1(p) else "w1" if "-w1" in p else "f2" if "-f2" in p else "f1"
            out.append((p, host, fam))
    return out


def gpu_history(recs=None):
    """Normalised rows of every delivered GPU order, {box, fam, task, wall_s, ok, prompt_tokens, priced_out}. The
    grading of older rows is cached per file under a fingerprint (size, mtime, grader payload): unverifiable = stale."""
    try:
        cache = json.load(open(PRICE_CACHE, encoding="utf-8"))
        cache = cache["files"] if cache.get("v") == CACHE_V else {}
    except Exception:                                                # noqa: BLE001
        cache = {}
    graders, rows, dirty, files = {}, [], False, 0
    recs = recs if recs is not None else records()
    sources = [(delivered_out(r), r["cell"]) for r in recs]
    sources = [s for s in sources if s[0]]
    sources += [(p, (b, None, f, None, None, None))
                for p, b, f in handrun_sources({s[0] for s in sources} | {r["out"] for r in recs})]
    for path, cell in sources:
        box, fam = cell[0], cell[2]
        st, pay = os.stat(path), os.stat(os.path.join(PAYLOADS, GRADE_PAYLOAD[fam]))
        fp = [st.st_size, st.st_mtime_ns, pay.st_size, pay.st_mtime_ns]
        ent = cache.get(path)
        if not ent or ent.get("fp") != fp:
            ent = {"fp": fp, "rows": read_out(path, fam, graders)}
            cache[path] = ent
            dirty = True
        files += 1
        rows.extend(dict(r, box=box, fam=fam, model=r.get("model") or cell[1] or "", think=cell[3], temp=cell[4],
                         seed=cell[5]) for r in ent["rows"])
    if dirty:
        with open(PRICE_CACHE + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"v": CACHE_V, "files": cache}, fh)
        os.replace(PRICE_CACHE + ".tmp", PRICE_CACHE)
    print("price history: %d delivered GPU result files, %d rows" % (files, len(rows)))
    return rows


def price_lines(rows, boxes, fams_of):
    """-> ({(box, fam): ceiling s or None}, report lines). report: the reference before (every solve counts, warm rows
    included) and after (cold solves only) for each box x family, with the counts that explain the move."""
    after, stats = MP.reference(rows)
    before, _ = MP.reference(rows, exclude_warm=False)
    ceilings, lines = {}, []
    for box in boxes:
        for fam in fams_of(box):
            tasks = family_tasks(fam)
            a = None if unceiled(box, fam) else MP.order_ceiling(after, box, fam, tasks, PRICE_X)
            b = None if unceiled(box, fam) else MP.order_ceiling(before, box, fam, tasks, PRICE_X)
            s = stats.get((box, fam), {})
            ceilings[(box, fam)] = a
            lines.append("  %-10s %-6s ceiling %s -> %s   (solved rows %d, warm left out %d, prompt count missing %d, "
                         "tasks priced %d of %d)" % (
                             box, fam, "%.1f s" % b if b else "none", "%.1f s" % a if a else "none", s.get("solved", 0),
                             s.get("warm", 0), s.get("unknown_prompt", 0),
                             sum(1 for t in tasks if (box, fam, t) in after), len(tasks)))
    return ceilings, lines


# ------------------------------------------------------------------------------------------------------- submitting
# --------------------------------------------------------------------------------------- the stop rule, applied itself
# the owner - the rule is
# 34-Hard-Bench-Order-Spec "When an arm stops being filled". Every top-up re-derives it from each box's OWN rows.
AUTO_HOLD = os.path.join(HERE, "menu_fill_auto_holds.json")      # this filler's own file - never menu_fill_hold.json
DESK = LS.head_url()                                             # Kirk's desk (= hard_bench_fleet_orders.KIRK)
CHAMPION = MPATH.champion()                 # priced at every knob, never auto-stopped: its price list is the point
# (34-Hard-Bench-Order-Spec "Mothership is the lab's top end", 34-CHARTER §10)
# A PRELIMINARY winner's role cells are top-limit tests and run on the champion's terms - no price line, no at-cap brake,
# never auto-stopped: a price line on its coding cells would measure the line, not the model.
PRELIM_CHAMPION = {(b, m) for b, c in MPATH.BOXES["boxes"].items() for m in c.get("prelim_champion", [])}   # boxes.json


def is_champion(box, model):
    """ONE derivation of "priced on the champion's terms" (no price line, no brake, never auto-stopped)."""
    return CHAMPION.get(box) == model or (box, model) in PRELIM_CHAMPION
FIT_VOID_SHARE = 0.25   # the gap in reviews: every arm the owner stopped for "cannot fit" had >=28% of its
# rows at the cap, every arm he kept had <=12% (owner call, the owner's to change)
PRICED_SHARE = 0.5      # a cell is priced out when at least half its answers cost PRICE_X x their task's cheapest solve
# "answer": each answer's own cost against its task's cheapest solve (a wrong answer is never charged). "solved": ALSO
# stop on the cost of a CORRECT answer - per task, the median answer's wall x answers per solve (failures charged to the
# solves, "per solved"; 34-CHARTER "How results shape action": low quality is a price); a cell is priced out when at
# least PRICED_SHARE of its priced tasks cost PRICE_X x their cheapest solve, and a task it never solved costs without
# limit. The median, not the mean: the reference is one task's fastest cold solve, so a mean trips on a single slow
# answer. It only ADDS stops: a cap row is void here (it measured the cap) but the per-answer rule still sees it, so
# neither may reopen an arm the other stopped.
PRICE_BASIS = "solved"


def cell_price(rs, box, fam, cheapest, basis=None):
    """-> (priced, out_of) for one cell's non-control rows under the price basis (PRICE_BASIS when None)."""
    if (basis or PRICE_BASIS) == "answer":
        return sum(1 for r in rs if r.get("priced_out") or (
            r.get("wall_s") and (box, fam, r["task"]) in cheapest
            and float(r["wall_s"]) >= PRICE_X * cheapest[(box, fam, r["task"])])), len(rs)
    walls, solves = collections.defaultdict(list), collections.Counter()
    for r in rs:
        if r.get("void") or (box, fam, r["task"]) not in cheapest:     # a cap row measured the cap; an unpriced task
            continue                                                    # has no price to be measured against
        walls[r["task"]].append(float(r.get("wall_s") or 0.0))
        solves[r["task"]] += bool(r.get("ok")) and not r.get("priced_out")
    out = sum(1 for t, w in walls.items() if not solves[t] or
              statistics.median(w) * len(w) / solves[t] >= PRICE_X * cheapest[(box, fam, t)])
    return out, len(walls)


def arm_stops(rows, judge_price=True, basis=None):
    """The stop rule under PRICE_BASIS (or `basis`). "solved" = the per-answer stops PLUS the arms priced out per
    correct answer - it only ever adds (see PRICE_BASIS)."""
    if (basis or PRICE_BASIS) == "answer":
        return _arm_stops(rows, judge_price, "answer")
    out = _arm_stops(rows, judge_price, "answer")
    for k, v in _arm_stops(rows, judge_price, "solved").items():
        out.setdefault(k, dict(v, why=v["why"].replace(
            "most answers cost", "the cost per correct answer (median answer x tries) is")))
    return out


def rigby_stops(rows):
    """rigby: the cannot-fit rule, plus (under PRICE_BASIS "solved") the cost per CORRECT answer - rigby answers one-shot
    like a GPU box, and the 10-02 rigby review priced it by "time per solve". Not the per-answer test: rigby's own
    per-order price brake already cuts slow answers (it never sees wrong ones)."""
    out = _arm_stops(rows, False, "answer")
    if PRICE_BASIS == "solved":
        for k, v in _arm_stops(rows, True, "solved").items():
            out.setdefault(k, dict(v, why=v["why"].replace(
                "most answers cost", "the cost per correct answer (median answer x tries) is")))
    return out


def _arm_stops(rows, judge_price, basis):
    """-> {"box/model/fam/think": {"why", "evidence"}} for every arm-family the stop rule now stops, each box judged on its
    own rows. (1) cannot fit: >= FIT_VOID_SHARE of the family's rows ended at the context cap AND the cap is the model's
    TESTED maximum on that box (at the bench standard the cell re-runs once the fit is measured instead). (2) priced
    out: >= PRICED_SHARE of a cell's answers cost >= PRICE_X x their task's cheapest cold solve on that box, in >= 2
    families, and reproduced (a second priced-out cell of one family - another temperature or seed set is a fresh
    sampled run; breadth-first ordering reaches a second temperature long before a second seed set) - then every
    priced-out family of that arm stops."""
    cheapest, _ = MP.reference(rows)
    cells = collections.defaultdict(list)
    for r in rows:
        if r.get("think") in THINKS and r.get("model") and not str(r.get("task", "")).startswith("control"):
            cells[(r["box"], r["model"], r["think"], r["fam"], r.get("temp"), r.get("seed"))].append(r)
    arms = collections.defaultdict(lambda: collections.defaultdict(dict))
    for (box, model, think, fam, t, sb), rs in cells.items():
        # a loop is scored as the model's answer, but it is cannot-fit evidence like a cap hit (the brake's same rule)
        void = sum(1 for r in rs if r.get("void") or r.get("loop"))
        priced, out_of = cell_price(rs, box, fam, cheapest, basis)
        arms[(box, model, think)][fam][(t, sb)] = (len(rs), void, priced, out_of)
    out = {}
    for (box, model, think), fams in arms.items():
        if is_champion(box, model):
            continue
        tested = box == "rigby" or model in TESTED_CTX.get(box, {})     # rigby rows count only at RIGBY_CTX
        priced_fams = {}
        for fam, cl in fams.items():
            n = sum(c[0] for c in cl.values())
            void = sum(c[1] for c in cl.values())
            if tested and n and void / n >= FIT_VOID_SHARE:
                out["%s/%s/%s/%s" % (box, model, fam, think)] = {
                    "why": "AUTO stop rule (cannot fit): %d of %d answers ended at the tested max context on %s" % (
                        void, n, box), "evidence": {"rows": n, "at_cap": void, "cells": len(cl)}}
            priced_cells = {(t, sb) for (t, sb), (_cn, _cv, cp, co) in cl.items() if co and cp / co >= PRICED_SHARE}
            if priced_cells and judge_price:
                priced_fams[fam] = priced_cells
        if len(priced_fams) >= 2 and any(len(s) >= 2 for s in priced_fams.values()):
            for fam, cells_priced in priced_fams.items():
                key = "%s/%s/%s/%s" % (box, model, fam, think)
                out.setdefault(key, {
                    "why": "AUTO stop rule (priced out): most answers cost >= %dx their task's cheapest solve on %s, in "
                           "%d families (%s), reproduced (a second priced-out cell)" % (
                               PRICE_X, box, len(priced_fams), ", ".join(sorted(priced_fams))),
                    "evidence": {"priced_cells": {f: ["/".join(map(str, c)) for c in sorted(s, key=str)]
                                                  for f, s in priced_fams.items()}}})
    return out


def read_auto(path=AUTO_HOLD):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:                                                # noqa: BLE001
        return {}


def write_auto(stops, cancel_sent, path=AUTO_HOLD):
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"generated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "stops": stops,
                   "cancel_sent": cancel_sent}, fh, indent=1)
    os.replace(path + ".tmp", path)


def cancel_at_desk(desk_id, why):
    """POST /p20/order/cancel - the desk cancels an uncollected order itself, or queues a factory cancel control."""
    req = urllib.request.Request(DESK + "/p20/order/cancel", method="POST",
                                 data=json.dumps({"id": desk_id, "why": why[:300], "source": "menu_fill"}).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def cancel_stopped(recs, desk, auto, cancel_sent):
    """Cancel every still-outstanding order whose cell an AUTOMATIC stop now covers (human holds are the owner's rulings and
    their orders were handled when he made them). One request per order, remembered in cancel_sent."""
    sent = 0
    for rec in recs:
        key = next((k for k in auto if cell_hold({k: auto[k]}, rec["cell"]) is not None), None)
        if not key or delivered_out(rec) is not None or not desk:
            continue
        drec = next((desk[n] for n in rec["names"] if n in desk), None)
        if not drec or str(drec.get("status") or "").lower() not in ("pending", "claimed"):
            continue
        if drec["order_name"] in cancel_sent:
            continue
        try:
            res = cancel_at_desk(drec["id"], "%s [%s]" % (auto[key]["why"], key))
        except Exception as e:                                       # noqa: BLE001
            print("    CANCEL FAILED %s (%r) - retried next top-up" % (drec["order_name"], e))
            continue
        cancel_sent[drec["order_name"]] = {"at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M"), "key": key,
                                           "how": res.get("how") or res.get("error")}
        sent += 1
        print("    CANCEL %s -> %s (%s)" % (drec["order_name"], res.get("how") or res.get("error"), key))
    return sent


def submit_cell(box, model, fam, think, t, sb, ceiling=None, attempt=1):
    strata = STRATA.get((box, model))
    ctx = strata["ctx"] if strata else TESTED_CTX.get(box, {}).get(model, BENCH_CTX[box])
    tag = "g-%s-%s-%s-t%s-%s-s%s" % (box[:2], slug(model)[:36], fam, t.replace(".", ""), think, sb)
    if attempt > 1:
        tag += "-r%d" % attempt          # a re-order of a dead cell is a new order with its own product folder
    env = dict(os.environ)
    offload = ["--offload-ok"] if (box, model) in OFFLOAD_OK else []
    offload += ["--strata", strata["plan"]] if strata else []         # rides beside the placement flags
    price = ["--price-ceiling-s", "%.1f" % ceiling] if ceiling else []
    think_flags = ["--think"] if think == "on" else ["--think-level", think] if think != "off" else []
    if fam in ("f1", "f2", "w1", "c1"):
        cmd = [sys.executable, os.path.join(HERE, "hard_bench_fleet_orders.py"), "submit", "--tag", tag,
               "--model-name", model, "--temperature", t, "--seed-base", sb, "--only-host", box, "--num-ctx", str(ctx)]
        cmd += ["--pool", fam] if fam in ("f2", "w1", "c1") else []
        cmd += think_flags
        cmd += offload + price
        cmd += ["--at-cap-brake", str(FIT_VOID_SHARE)] if fam in UNCEILED and not is_champion(box, model) else []
    else:
        rung = ("vlm2x-" + tag) if fam == "vlm2x" else tag
        env["HB_VLM_SCALE"] = "2" if fam == "vlm2x" else "1"
        cmd = [sys.executable, os.path.join(HERE, "hard_bench_fleet_tv.py"), "cell", "--rung", rung, "--division",
               "tool" if fam == "tool" else "vlm", "--model", model, "--ctx", str(ctx), "--host", box,
               "--temperature", t, "--seed-base", sb] + think_flags + offload + price
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE, env=env)
    ok = r.returncode == 0 and '"ok": true' in r.stdout
    return ok, tag, "" if ok else ((r.stdout + r.stderr).strip().splitlines() or ["?"])[-1][:200]


# ------------------------------------------------------------------------------------------------------------ rigby
def rigby_fam(path):
    b = os.path.basename(path)
    return "tool" if "-tool-" in b else "c1" if is_c1(b) else "w1" if "-w1" in b else "f2" if "-f2" in b else "f1"


def rigby_tasks(fam):
    sys.path.insert(0, HERE)
    if fam == "tool":
        import hard_tool_tasks
        return list(hard_tool_tasks.TASKS)
    if fam == "w1":
        import hard_tasks_w1
        return list(hard_tasks_w1.TASKS)
    if fam == "c1":
        import hard_tasks_c1
        return list(hard_tasks_c1.TASKS)
    if fam == "f2":
        import hard_tasks_f2
        return list(hard_tasks_f2.TASKS)
    import hard_bench_matrix as mx
    return list(mx.TASKS)


def rigby_cell(row, fam):
    """A delivered row's grid cell, or None for a row outside the grid (a draft / speculation knob). A file's own
    placement decoding (RIGBY_PLACEMENT: the MTP file's draft-mtp) IS its grid, not a knob beside it."""
    m = row.get("model") or "?"
    own = (RIGBY_PLACEMENT.get(m) or {}).get("fields") or {}
    spec = row.get("spec_type")
    if row.get("draft_model") or (spec and spec != own.get("spec_type")) \
            or str(row.get("task", "")).startswith("control"):
        return None
    think = "on" if m in RIGBY_THINKING and row.get("reasoning") in (None, "on", "auto") else "off"
    sb = str(((int(row.get("replicate") or 1) - 1) // 3) * 3 + 1)
    return ("rigby", m, fam, think, "%.1f" % float(row.get("temperature", 0.6)), sb)


def rigby_measured(row, ctx):
    """Stopped or errored rows re-run; a row capped by a context SMALLER than the grid's measured the cap, not the
    model () - a runaway at the grid's own context is the model's answer, graded or not (a grader that
    could not run a context-sized answer must not send the pair round again)."""
    if row.get("priced_out"):
        return True
    if row.get("finish_reason") == "length":
        return rigby_at_cap(row, ctx)
    return bool(row.get("ran"))


def rigby_at_cap(row, ctx):
    """The answer ran to the end of the grid's context. `ctx` is the ORDER's context (its result's cohort); token sums
    cannot tell - a cached prompt reports prompt_tokens 1 and the server stops just short of ctx. Rigby asks for
    max_tokens -1, so "length" is the context filling (tolkien_llm.at_cap is the same test inside the order)."""
    return row.get("finish_reason") == "length" and (ctx or 0) >= RIGBY_CTX


def order_ctx(result, arm):
    """The context the order ran this arm at, from its result's cohort (tolkien_llm.run_arm's meta), or None."""
    return next((m.get("ctx") for m in (result.get("cohort") or {}).get("arms") or [] if m.get("arm") == arm), None)


def rigby_history():
    """(cells, per-answer seconds, cheapest solve, solve rows) from every rigby result in the outbox.
    cells: cell -> {"pairs": measured {(task, rep)}, "control": any contributing order passed its control}.
    cheapest: (family, task) -> the cheapest COLD solve (menu_price.reference: a cache-warm row prices a hit, not a task)."""
    cells = defaultdict(lambda: {"pairs": set(), "control": False})
    walls, solves, judged = defaultdict(list), [], []
    files = glob.glob(os.path.join(TABJOBS, "outbox", "hardbench-rigby-*.results.json"))
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:                                            # noqa: BLE001
            continue
        fam = rigby_fam(f)
        for r in d.get("rows") or []:
            if not str(r.get("task", "")).startswith("control"):
                solves.append({"box": "rigby", "fam": fam, "task": TVER.current(r["task"]), "model": r.get("model") or "",
                               "wall_s": r.get("wall_s"), "ok": r.get("ok"), "prompt_tokens": MP.prompt_read(r),
                               "priced_out": r.get("priced_out")})
            c = rigby_cell(r, fam)
            ctx = order_ctx(d, r.get("arm"))
            if c is None or not rigby_measured(r, ctx) or placement_stale(c[1], f):
                continue                                             # stale = priced on gone hardware: re-ordered
            cells[c]["pairs"].add((TVER.current(r["task"]), int(r["replicate"])))
            cells[c]["control"] |= bool(d.get("control_passed"))
            walls[(c[1], fam)].append(float(r.get("wall_s") or 0))
            judged.append({"box": "rigby", "model": c[1], "fam": fam, "think": c[3], "temp": c[4], "seed": c[5],
                           "task": TVER.current(r["task"]), "wall_s": r.get("wall_s"), "ok": r.get("ok"),
                           "prompt_tokens": MP.prompt_read(r), "priced_out": r.get("priced_out"),
                           "void": rigby_at_cap(r, ctx), "loop": r.get("finish_reason") == "loop"})
    cold, _ = MP.reference(solves)
    print("rigby      history: %d result files, %d cells touched" % (len(files), len(cells)))
    return (cells, {k: sum(v) / len(v) for k, v in walls.items() if v},
            {(k[1], k[2]): v for k, v in cold.items()}, solves, judged)


def rigby_models():
    try:
        return json.load(open(RIGBY_MANIFEST, encoding="utf-8")).get("models") or []
    except Exception as e:                                           # noqa: BLE001
        print("  rigby: no model manifest (%s) - no orders this run" % type(e).__name__)
        return None


def rigby_grid(models):
    cells, carried = [], rigby_carried(models)
    for sb in SEEDS:
        for m in sorted(models):
            for fam in RIGBY_FAMS:
                for think in (("off", "on") if m in RIGBY_THINKING else ("off",)):
                    for t in _temps(m, think, fam, carried):
                        cells.append(("rigby", m, fam, think, t, sb))
    return cells


def rigby_pairs(cell):
    sb = int(cell[5])
    return {(t, r) for t in rigby_tasks(cell[2]) for r in range(sb, sb + 3)}


def rigby_ceiling(fam, tasks, cheapest):
    """One value per order: PRICE_X x the worst of the order's per-task cheapest measured COLD solves on rigby.
    None when no task in the order has a measured solve - there is no price to state. rigby is a helper box, so W1
    carries its line here too (unceiled)."""
    if unceiled("rigby", fam):
        return None
    priced = [cheapest[(fam, t)] for t in tasks if (fam, t) in cheapest]
    return round(PRICE_X * max(priced), 1) if priced else None


def _deploy_stop_matcher():
    sys.path.insert(0, MIRROR)
    import fleet_deploy_tolkien
    return fleet_deploy_tolkien.is_deploy_stop


def rigby_outstanding(outbox=None, is_deploy_stop=None, stopped_by_filler=None):
    """(every submission, the ones not yet delivered). An order whose NEWEST results file is a deploy's stop is still
    outstanding: the deploy requeues it from its checkpoint, so re-ordering its pairs would run them twice - unless the
    filler has since stopped it itself (its own stop slips, `cancel_sent` in AUTO_HOLD): that order has ended.
    four such orders read as outstanding forever and filled rigby's queue budget - rigby sat idle 3 h."""
    if stopped_by_filler is None:
        stopped_by_filler = set(read_auto().get("cancel_sent") or {})
    subs = json.load(open(RIGBY_SUBS, encoding="utf-8")) if os.path.exists(RIGBY_SUBS) else {}
    files = glob.glob(os.path.join(outbox or os.path.join(TABJOBS, "outbox"), "hardbench-rigby-m-*.results.json"))
    stems = sorted(os.path.basename(f)[:-len(".results.json")] for f in files)
    is_deploy_stop = is_deploy_stop or _deploy_stop_matcher()
    out = {}
    for n, s in subs.items():
        mine = [d for d in stems if d.startswith(n + ".")]
        if mine and n in stopped_by_filler:
            continue
        if mine:
            try:
                with open(os.path.join(os.path.dirname(files[0]), mine[-1] + ".results.json"), encoding="utf-8") as f:
                    newest = json.load(f)
            except Exception as e:                                   # noqa: BLE001
                print("  rigby: %s unreadable (%s) - left outstanding" % (mine[-1], type(e).__name__))
                newest = {"stopped": True, "stop_cause": None}
                is_deploy_stop_or_unread = True
            else:
                is_deploy_stop_or_unread = bool(newest.get("stopped")) and is_deploy_stop(newest.get("stop_cause"))
            if not is_deploy_stop_or_unread:
                continue
        out[n] = s
    return subs, out


def rigby_submit(cell, missing, cheapest, subs):
    _, m, fam, think, t, sb = cell
    tasks = sorted({task for task, _ in missing})
    name = "hardbench-rigby-m-%s-%s-t%s-%s-s%s.%s" % (slug(m.replace(".gguf", ""))[:34], fam, t.replace(".", ""),
                                                        think, sb, datetime.datetime.now().strftime("%Y%m%d-%H%M"))
    ceiling = rigby_ceiling(fam, tasks, cheapest)
    spec = {"kind": "llm", "name": name, "payload": TVER.newest_payload(RIGBY_PAYLOAD[fam]), "tasks": tasks,
            "replicates": int(sb) + 2, "items": [[task, r] for task, r in sorted(missing)], "initiator": "menu_fill",
            "why": "P34 menu cell %s (%d unmeasured pairs); price ceiling %s" % (
                "/".join(cell[1:]), len(missing), "%ss = %dx the worst per-task cheapest cold rigby solve" % (
                    ceiling, PRICE_X) if ceiling else "none: no task in this order has a measured rigby solve")}
    spec.update(rigby_order_fields(m, think, t))
    placement = RIGBY_PLACEMENT.get(m)
    if placement:
        spec["why"] += "; placement %s" % placement["name"]
    if ceiling:
        spec["price_ceiling_s"] = ceiling
    if fam in UNCEILED:
        spec["at_cap_brake"] = FIT_VOID_SHARE      # no price brake, so the cannot-fit rule also runs inside the order
    tmp = os.path.join(TABJOBS, "inbox", name + ".tjob.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=1)
    os.replace(tmp, tmp[:-4])
    subs[name] = {"cell": list(cell), "items": spec["items"], "at": datetime.datetime.now().isoformat(timespec="seconds")}
    os.makedirs(os.path.dirname(RIGBY_SUBS), exist_ok=True)
    with open(RIGBY_SUBS + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(subs, fh, indent=1)
    os.replace(RIGBY_SUBS + ".tmp", RIGBY_SUBS)
    return name, ceiling


def stop_rigby_stopped(auto, cancel_sent, inbox=None):
    """A tablet stop slip for every outstanding rigby order whose cell an AUTOMATIC stop now covers; once per order."""
    inbox = inbox or os.path.join(TABJOBS, "inbox")
    _subs, outstanding = rigby_outstanding()
    sent = 0
    for name, s in sorted(outstanding.items()):
        key = next((k for k in auto if cell_hold({k: auto[k]}, tuple(s["cell"])) is not None), None)
        if not key or name in cancel_sent:
            continue
        slip = os.path.join(inbox, name + ".stop.json")
        with open(slip + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"why": "%s [%s]" % (auto[key]["why"], key)}, fh)
        os.replace(slip + ".tmp", slip)
        cancel_sent[name] = {"at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M"), "key": key, "how": "stop slip"}
        sent += 1
        print("    STOP %s -> stop slip (%s)" % (name, key))
    return sent


def rigby_fill(mode, hold, hist):
    models = rigby_models()
    if models is None:
        return
    cells, per_answer, cheapest, _solves, _judged = hist
    subs, outstanding = rigby_outstanding()
    ordered = {tuple(s["cell"]) for s in outstanding.values()}
    missing, held = [], 0
    for c in core(rigby_grid(models), rigby_carried(models)):
        if c in ordered:
            continue
        if cell_hold(hold, c) is not None:
            held += 1
            continue
        got = cells.get(c)
        todo = rigby_pairs(c) - (got["pairs"] if got else set())
        if got and got["pairs"] and not todo and not got["control"]:
            todo = rigby_pairs(c)                                    # complete but its control never passed: VOID
        if todo:
            missing.append((c, todo))

    def est(c, n):
        return per_answer.get((c[1], c[2]), TOPUP_S / max(1, n)) * n
    queued_s = sum(est(tuple(s["cell"]), len(s["items"])) for s in outstanding.values())
    print("%-10s models %2d | grid %4d | missing %4d | outstanding %3d (~%dm) | held by model/arm holds %3d | hold: %s" % (
        "rigby", len(models), len(rigby_grid(models)), len(missing), len(outstanding), queued_s // 60, held,
        hold.get("rigby", "-")))
    vend = [c for c in core(rigby_grid(models), rigby_carried(models)) if (c[4], c[5]) not in CORE_KNOBS]
    print("    vendor-temperature cells %d (missing %d); no stated temperature: %s" % (
        len(vend), sum(1 for c, _t in missing if c in vend),
        ", ".join(m for m, t in sorted(rigby_carried(models).items()) if not t) or "-"))
    for key, n in sorted(hold_counts(hold, rigby_grid(models)).items()):
        print("    hold %-62s stops %3d rigby cells%s" % (key, n, "" if n else "   WARNING: matches no cell"))
    if mode != "topup" or "rigby" in hold:
        return
    for c, todo in missing:
        if queued_s >= RIGBY_QUEUE_S:
            break
        name, ceiling = rigby_submit(c, todo, cheapest, subs)
        queued_s += est(c, len(todo))
        print("  OK   %s (%d pairs, ceiling %s)" % (name, len(todo), ceiling), flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    hold = load_hold()
    # the desk's record of every factory order (read-only, local to Softserve); an unreadable desk leaves orders outstanding
    desk = BS.read_desk()
    if desk is None:
        print("  desk records unreadable - undelivered orders stay outstanding this run")
    recs = records()
    seen = ledger(desk, recs)
    stopped = [r["names"][0] for r in recs if BS.stopped(r["names"], delivered_out(r) is not None, desk)]
    if stopped:
        print("STOPPED by an operator slip, not cancelled and not delivered (a requeue or a cancel is a human's call): %d"
              % len(stopped))
        for n in stopped:
            print("    " + n)
    hist = gpu_history(recs)
    ceilings, lines = price_lines(hist, list(BOX), lambda box: list(FAMS))
    rhist = rigby_history()
    carried = {box: {} for box in BOX}
    listing = {box: box_models(box, carried[box]) for box in BOX}
    carried["rigby"] = rigby_carried(rigby_models() or [])
    auto = arm_stops(hist)
    auto.update(rigby_stops(rhist[4]))
    auto = open_vendor_cells(auto, lambda box: carried.get(box, {}))
    prev = read_auto()
    cancel_sent = dict(prev.get("cancel_sent") or {})
    new = sorted(set(auto) - set(prev.get("stops") or {}))
    print("stop rule, applied by the filler: %d arm-families stopped automatically (%d new this run)" % (len(auto), len(new)))
    for k in sorted(auto):
        print("    %s %-62s %s" % ("NEW " if k in new else "    ", k, auto[k]["why"]))
    if mode == "topup":
        n = cancel_stopped(recs, desk, auto, cancel_sent)
        n += stop_rigby_stopped(auto, cancel_sent)
        print("    cancelled %d outstanding order(s) of automatically stopped arms" % n)
        write_auto(auto, cancel_sent)
    hold = dict(hold)
    for k, v in auto.items():
        hold.setdefault(k, v)                                        # a human hold on the same key wins (its `except`)
    _rc, rlines = price_lines(rhist[3], ["rigby"], lambda box: list(RIGBY_FAMS))
    print("price line, per box x family: %dx the worst per-task cheapest cold solve (before -> after the warm-row fix)" % PRICE_X)
    for ln in lines + rlines:
        print(ln)
    for box in BOX:
        mine = {k: e for k, e in seen.items() if k[0] == box}
        outstanding = sum(1 for e in mine.values() if cell_state(e) == "outstanding")
        dead = sum(1 for e in mine.values() if cell_state(e) == "dead")
        models = listing[box]
        if models is None:
            continue
        cells = grid(box, models, carried[box])
        missing, held, parked = plan_cells(core(cells, carried[box]), hold, mine)
        room = max(0, TARGET - outstanding)
        print("%-10s models %2d | grid %4d | missing %4d | outstanding %3d | dead (re-orderable) %3d | held by "
              "model/arm holds %3d | hold: %s" % (
                  box, sum("completion" in c for c in models.values()), len(cells), len(missing), outstanding, dead,
                  held, hold.get(box, "-")))
        vend = [c for c in core(cells, carried[box]) if (c[4], c[5]) not in CORE_KNOBS]
        print("    vendor-temperature cells %d (missing %d); no stated temperature: %s" % (
            len(vend), sum(1 for c, _a in missing if c in vend),
            ", ".join(sorted(m for m, caps in models.items() if "completion" in caps and not carried[box].get(m)))
            or "-"))
        for key, n in sorted(hold_counts(hold, cells).items()):
            print("    hold %-62s stops %3d %s cells%s" % (key, n, box, "" if n else "   WARNING: matches no cell"))
        for c, why in parked:
            print("    PARKED %s: %s" % ("/".join(c), why))
        for c, attempt in missing:
            print("    MISSING %s%s" % ("/".join(c), "" if attempt == 1 else " (re-order %d)" % attempt))
        if mode != "topup" or box in hold or not room:
            continue
        for c, attempt in missing[:room]:
            # the champion is priced at every knob - no ceiling may cut its answers
            ok, tag, err = submit_cell(*c, ceiling=None if is_champion(box, c[1]) else ceilings.get((box, c[2])),
                                       attempt=attempt)
            print("  %s %s %s" % ("OK  " if ok else "FAIL", tag, err), flush=True)
    rigby_fill(mode, hold, rhist)


if __name__ == "__main__":
    main()
