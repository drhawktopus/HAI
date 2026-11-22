"""Fleet scheduler: place a job on the host that makes the most sense for it, right now.

Replaces per-consumer LADDERS (pick_brain had its own, dataset jobs hardcoded vlm_host,
renders assumed the lead) with ONE placement decision. Every rule below already existed
somewhere in this codebase; gathering them here prevents contradictions and ensures
every kind of work applies to every rule.

WHAT A HOST HAS is not one number. A card being busy does NOT make its box unavailable:
    GPU - exclusive. One model per card (e.g., 8 GB VRAM cannot hold a 9B brain AND a vision model).
    RAM - shareable but EXHAUSTIBLE; exhaustion is fatal here (e.g., 27B generating mid-render
           froze the lead outright by spilling 17 GB into 32 GB total RAM).
    CPU - shareable and contended. Mostly independent of GPU, but heavy CPU work on a training
           box penalizes step-rate (partly due to post-reboot contention).
    disk/net - effectively unconstrained on array-attached boxes.

MEASURED CAPABILITY:
    The lead: 16 cpu, 31.9 GB RAM, RX 7900 XTX (24 GB VRAM)
    the failover box: 16 cpu, 47.3 GB RAM, RTX 3070 Ti (8 GB VRAM) -- most RAM, usually idle
    brain box: 6 cpu, 15.9 GB RAM, RTX 3070 (8 GB VRAM) -- weakest box
    the head: 6 cpu, 15.0 GB RAM, (no GPU) -- orchestrator VM

POLICY:
  1. The brain box is the chat default; its WoL wake takes SECONDS, so wait it out before failing over.
  2. The lead is FAILOVER for chat; the failover box is last resort for chat.
     Reason: The failover box is LOAD-BEARING (hosts Plex, the head VM, orchestrator, scheduler).
     Keep the big chat model off the box everything else depends on.
  3. Never put a brain on a lead card mid-render/training, but keep it as final
     fallback ('availability never regresses').
  4. The failover box's GPU is Plex-gated (<20% mem, <50% util, <=1 transcode).
  5. Short-lived transient work (embeds) floats AWAY from LOAD-BEARING hosts. Not from the chat card: BH's chat model
     reloading occasionally is a cheaper cost than degrading services users depend on.
  6. Training run owns its card for HOURS: refuse immediately, never wait (collision gate).
  7. One model per card.
  8. GPU work is energy-gated and quota-costed elsewhere; this module does not second-guess that.
  9. `nvidia-smi` is NOT usable for BUSYNESS (absent on AMD box; 'No devices found' while ollama
     holds VRAM says nothing about queued work). Busy state comes from /api/ps, worker heartbeat,
     and claims.
     NOTE: `nvidia-smi` IS trustworthy for HEALTH. When GPU is lost mid-run, nvidia-smi reports
     'GPU is lost' accurately (Windows says OK, ollama says resident, gpu_health says HEALTHY).
     Health now uses box's own pushed nvidia-smi verdict (report_gpu_state).
 10. A USER-SELECTED EXACT model (force_27b, an exact effort level) is a CONSTRAINT, not a hint. Never
     silently overridden -- if we cannot honour it we say so. The GENERAL modes (Quick, Hard) are WEIGHTS
     inside the weighing."""
import datetime
import json
import os
import re
import threading
import time

import config
import model_fit

# --- the roster: the devices this head places work on ---------------------------------------------
# config.py reads the roster file (HAI_ROSTER_FILE; its rules are there). CARDS and every per-device table in this module
# are DERIVED from its records, and the devices the code means by role are ROLE lookups: BRAIN is the device holding
# `brain`, FAILOVER `failover`, HEAD `head`, ADVISOR_HOME `advisor`, TOLKIEN_BOX `tolkien` (the box
# Tolkien runs on, a GPU factory whose own lane is reached through Tolkien's desk, never a card here). A record's `why` keeps the reason for each
# hand-set value.
ROSTER_ROLES = config.ROSTER_ROLES
ROSTER_FILE = config.ROSTER_FILE
one_device_roster = config.one_device_roster
roster_role = config.roster_role
_CARD_FIELDS = ("vram_gb", "ram_gb", "cpu", "label", "gpu")


def _is_card(d):
    return "tolkien" not in (d.get("roles") or [])


def validate_roster(data):
    """config.validate_roster, plus: every `url_setting` names a real setting of this head."""
    config.validate_roster(data)
    for d in data["devices"]:
        if "url_setting" in d and not isinstance(getattr(config, str(d["url_setting"]), None), str):
            raise config.RosterError("device %s names url_setting %r, which is not a setting"
                                     % (d["name"], d["url_setting"]))
    return data


def load_roster(path=None):
    """(roster, path it came from or None for the one-device roster), by config's rules, checked by validate_roster."""
    named = path is not None or bool(os.getenv("HAI_ROSTER_FILE", "").strip())
    roster, source = config.load_roster(path or ROSTER_FILE, named=named)
    return validate_roster(roster), source


ROSTER, ROSTER_SOURCE = validate_roster(config.ROSTER), config.ROSTER_SOURCE


def roster_cards(roster=None):
    """CARDS from the roster: each card device's url (its named setting's value, or its own `url`), card fields, flags."""
    out = {}
    for d in [d for d in (roster or ROSTER)["devices"] if _is_card(d)]:
        url = getattr(config, d["url_setting"]) if "url_setting" in d else d.get("url")
        out[d["name"]] = dict({"url": url}, **{k: d[k] for k in _CARD_FIELDS}, **(d.get("flags") or {}))
    return out


def roster_table(field, roster=None, order_by_value=False):
    """{device: value} for every device that sets `field`, in roster order (or by value, for an ordering table)."""
    rows = [(d["name"], d[field]) for d in (roster or ROSTER)["devices"] if field in d]
    if order_by_value:
        rows.sort(key=lambda kv: kv[1])
    return dict(rows)


def roster_tablets(roster=None):
    """TABLETS from the roster's `tablets` records (every field but the name and `why`)."""
    return {t["name"]: {k: v for k, v in t.items() if k not in ("name", "why")}
            for t in (roster or ROSTER).get("tablets") or []}


# --- capability -----------------------------------------------------------------------------------
# vram_gb/ram_gb are PHYSICAL. Usable headroom is computed live; these are the ceilings.
LEAD_BOX = config.LEAD_BOX     # the machine the lead seat sits on (from the menu's crown; config.lead_box)
BRAIN = roster_role("brain")
FAILOVER = roster_role("failover")
HEAD = roster_role("head")
ADVISOR_HOME = roster_role("advisor")
TOLKIEN_BOX = roster_role("tolkien")      # None: this head has no Tolkien box


def model_servers_by_role():
    """[(device, url)] for the card devices with a model server: the brain, the failover, the lead, then any other, in
    that order (the order status listings show them in)."""
    first = [BRAIN, FAILOVER, LEAD_BOX]
    names = list(dict.fromkeys(first + list(CARDS)))
    return [(h, CARDS[h]["url"]) for h in names if h in CARDS and CARDS[h].get("url")]


def roster_labels(roster=None):
    """{device: label} for every device with a model server, the Tolkien box included (pins and tiles name it)."""
    return {d["name"]: d["label"] for d in (roster or ROSTER)["devices"] if d.get("url_setting") or d.get("url")
            or not _is_card(d)}

CARDS = roster_cards()

# Reserve headroom against MEASURED FREE RAM, not total installed.
# Hyper-V commits host memory to VMs; using total causes false-positive capacity bans.
RAM_RESERVE_GB = roster_table("ram_reserve_gb")
CPU_BUSY_PCT = roster_table("cpu_busy_pct")

CLAIM_DIR = os.path.join(config.STATE_DIR, "scheduler-claims")
CLAIM_TTL_DEFAULT = 900.0        # rolling: the holder refreshes; a crashed job auto-releases
_lock = threading.Lock()


# --- claims: an INTENTIONAL dedication, never inferred ----------------------------------------------
def claim(host, resource, holder, ttl=CLAIM_TTL_DEFAULT, note=""):
    """Declare that `holder` is using `resource` on `host` until it stops refreshing.

        Dedication is intentional, not inferred from residency: embed models (transient blips) and
        vision jobs (long-term owners) look identical in memory but require opposite routing.

        TTL is rolling; a stalled/dead holder auto-releases the resource to prevent stranding cards.
    """
    os.makedirs(CLAIM_DIR, exist_ok=True)
    rec = {"host": host, "resource": resource, "holder": holder, "note": note,
           "expires": (datetime.datetime.now(datetime.timezone.utc)
                       + datetime.timedelta(seconds=ttl)).isoformat()}
    with _lock:
        with open(os.path.join(CLAIM_DIR, "%s.%s.json" % (host, resource)), "w",
                  encoding="utf-8") as f:
            json.dump(rec, f)
    return rec


# An OPERATOR HOLD keeps a card out of placement while a person fixes it (bad RAM, a driver, a reseat): a `gpu` claim
# whose holder starts with this prefix. Every placement and every Kirk-side caller already skips a gpu claim; the
# factory's own idle report leaves it alone (app.py run-state). Lapses by its ttl, or `release(host, "gpu")`.
OPERATOR_HOLD = "operator:"


def release(host, resource):
    try:
        os.remove(os.path.join(CLAIM_DIR, "%s.%s.json" % (host, resource)))
        return True
    except OSError:
        return False


def claim_for_url(url, resource="gpu"):
    """The live claim on the card behind this ollama URL, or None (a URL not in CARDS has none). For Kirk-side calls
    that reach a box by URL rather than through place() - embeds, chat titles: a claimed card is reserved for the
    order holding it."""
    key = (url or "").rstrip("/")
    for name, card in CARDS.items():
        if (card.get("url") or "").rstrip("/") == key:
            return active_claim(name, resource)
    return None


# A LIVE HAI JOB holds a claim of its own kind on the box its model calls reach (ollama_client.track refreshes it at
# every job call's start and end, rolling CLAIM_TTL_DEFAULT): VRAM is a cache and a job is not a factory order, so
# nothing told placement the box was in use - the claim sweep placed reviews on the lead "free; warm
# (qwen3.8:27b resident)" mid-job and evicted the 3.8 three times (#34 B12). It is NOT a `gpu` claim: the user's own
# chats keep sharing the lead through Kirk's slot. Two consumers: kinds that `yield_to_live` (background housekeeping)
# skip the box in place(), and occupied_for() refuses a call that would load a DIFFERENT model there.
LIVE = "live"


def own_window(host):
    """The model Kirk's OWN chat window holds on `host` (chat_window.py, #34 chat-as-order), or None. Its gpu claim and
    the factory's `compute` run-state are this process's turns, not another job: a turn for that model joins the
    window, any other model is still refused (one model per card). A window this process no longer holds - left by a
    restarted Kirk until its heartbeat lapses - is NOT its own and reads busy like any order."""
    try:
        import chat_window
        return chat_window.held_on(host)
    except Exception:
        return None


def idle_own_window(host, model):
    """The NAME of Kirk's own chat window on `host` when nobody uses it and it holds another model than `model` - a
    CACHE the call for `model` will close and replace (chat_window.idle_cache / close_idle) - or None. A desk claim held
    by that window (its run-state name starts with it) is then not a reason to refuse the card. Only when `model` is
    named; fail-soft to None (the claim stands)."""
    if not model or not host:
        return None
    try:
        import chat_window
        w = chat_window.idle_cache(host, model)
        return w.name if w is not None else None
    except Exception:                                                # noqa: BLE001
        return None


def lendable_window(host):
    """The model of Kirk's own chat window on `host` when it can LEND its card now - idle, not ended, not Strata
    (chat_window.lendable) - or None. Only place(lend=True) asks: McCoy's on-demand bump."""
    try:
        import chat_window
        w = chat_window.lendable(host)
        return w.model if w else None
    except Exception:
        return None


def _host_for_url(url):
    key = (url or "").rstrip("/")
    for name, card in CARDS.items():
        if (card.get("url") or "").rstrip("/") == key:
            return name
    return None


def occupied_for(url, model=None, takes_window=False):
    """The claim that makes the card behind `url` unavailable to a call that would load `model` there, or None: a `gpu`
    claim (a factory order) always - except Kirk's own chat window for this very model, which the call joins; a LIVE
    job's claim unless the call uses the job's own model (it then shares the slot and evicts nothing). Kirk's
    second-model fallbacks ask this - one derivation for every caller."""
    c = claim_for_url(url)
    if takes_window:
        # a chat turn's window is an ORDER: one the desk is about to hand that factory comes first, whether or not a
        # claim exists yet (between two orders there is none), and Kirk's idle window gives way to it (turn_line)
        box = _host_for_url(url)
        line = turn_line(box) if box else None
        if line:
            return {"host": box, "resource": "gpu", "holder": line[0],
                    "note": "%d order(s) ahead of a new turn at the desk" % len(line)}
    # a CHAT turn (takes_window) closes Kirk's idle window of another model and takes the card, so that window's own
    # claim is a cache to it; any other caller (an embed, a title) would load BESIDE the idle model and evict it
    idle = idle_own_window(_host_for_url(url), model) if takes_window else None
    if c and not (model and own_window(_host_for_url(url)) == model) \
            and not (idle and str(c.get("holder") or "").startswith(idle)):
        return c
    for host, card in CARDS.items():
        if (card.get("url") or "").rstrip("/") == (url or "").rstrip("/"):
            busy, why = render_busy(host)
            if busy:
                return {"host": host, "resource": "gpu", "holder": why}
    live = claim_for_url(url, LIVE)
    if live and (model is None or (live.get("note") or "") != model):
        # A FINISHED chat's still-loaded model is a cache to the chat turn behind it, exactly as its idle window is
        # above. design § Seamless:
        # the card "is never reserved for one model", it stays resident WHILE IN USE. A chat's live claim outlives its
        # turn by the model's keep_alive, and a pinned turn for another model waited that out on a box whose only
        # occupant was the leftover (brain box 5 min, the failover box 3 min, the 11-05 park proofs). Still held: a JOB's
        # claim (its run keeps the window between its own calls), a chat turn in flight (its window is in use, so it is
        # not idle), and every caller that is not a chat turn (an embed or a title would load BESIDE the model).
        if not (idle and str(live.get("holder") or "").startswith("hai-chat:")):
            return live
    return None


def active_claim(host, resource):
    """The live claim on (host, resource), or None. Expired claims are ignored AND cleaned up."""
    p = os.path.join(CLAIM_DIR, "%s.%s.json" % (host, resource))
    try:
        with open(p, encoding="utf-8") as f:
            rec = json.load(f)
        exp = datetime.datetime.fromisoformat(rec["expires"])
        if exp <= datetime.datetime.now(datetime.timezone.utc):
            try:
                os.remove(p)
            except OSError:
                pass
            return None
        return rec
    except Exception:
        return None


# Path to the foundry's pushed run state file (see app.py: FLEET_RUN_STATE).
# Mounted at /docs inside the orchestrator container.
STATED_RUN = os.environ.get("HAI_STATED_RUN", os.path.join(config.STATE_DIR, "fleet-run-state.json"))


def stated_run(path=None):
    """Return the training run that declares itself as active, or None.

    Liveness is determined solely by explicit state declarations ('training', 'warming',
    'rendering'), never by inferring activity from timestamps or file modification times.
    Staleness of a record does not imply the run has ended; a long-running job remains BUSY
    until it explicitly transitions to 'paused' or completes."""
    recs = _run_records(path)
    for rec in recs.values():            # a run that owns a card outranks one that merely exists
        if (rec.get("state") or "").strip().lower() in ("training", "warming", "rendering"):
            return rec
    return next(iter(recs.values()), None)


def _run_records(path=None):
    """Return {host: rec} for every declared run. Tolerates BOTH on-disk shapes.

        Primary input is the per-host MAP in `fleet-run-state.json` (written by
        POST /fleet/run-state). Also accepts single-record shape to satisfy
        `test_stated_run_guard`.
    """
    try:
        with open(path or STATED_RUN, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}                        # absent/unreadable = no declared run; other guards still apply
    if not isinstance(d, dict):
        return {}
    if d.get("name"):                    # single record (test/legacy shape)
        return {d.get("host") or "": d}
    return {k: v for k, v in d.items() if isinstance(v, dict) and v.get("name")}


def training_busy(host, path=None):
    """(busy, why) for `host` according to the stated run. Fails CLOSED on an unrecognized state."""
    rec = _run_records(path).get(host)
    if not rec:
        return render_busy(host)
    state = (rec.get("state") or "").strip().lower()
    if state == "paused":
        return False, ""                 # current run, but nothing is on the card
    if state == "training":
        return True, "training run %r owns this card" % (rec.get("name"),)
    # Default to BUSY: non-training jobs or legacy nodes without explicit state.
    # Claiming "free" is expensive if wrong, so we err on the side of caution.
    return True, "%r is on this card (state %r)" % (rec.get("name"), state or "unstated")


PAGE_ORDER_PREFIX = "hai-page-"


def page_on_lane(host, path=None):
    """The stated run on this lane is a job's page render (gpu_render.py). They take seconds and queue behind each other, and
    a finished one's record outlives it by a beat, so to the next page this lane is not busy. a job's
    second render, seconds after its first, was sent to software by the first one's own record."""
    rec = _run_records(path).get(host)
    return bool(rec) and str(rec.get("name") or "").startswith(PAGE_ORDER_PREFIX)


def render_busy(host):
    """A fresh, host-stamped worker heartbeat must name a real unfinished render order.

    The legacy render worker uses the shared desk but has no trainer run-state. Its
    named order explains GPU load and reserves the card through the same busy check.
    A generic 'rendering' or external-preview heartbeat cannot claim arbitrary load.
    """
    try:
        import p20_queue
        st = p20_queue.gpu_status(host=host)
        if not st or st.get("host") != host or st.get("state") != "rendering":
            return False, ""
        match = re.fullmatch(r"rendering job ([0-9a-f]{12})", st.get("detail") or "")
        if not match:
            return False, ""
        job = p20_queue.get(match.group(1)) or {}
        if (job.get("kind") or "") not in ("", "render", "quick_preview"):
            return False, ""
        if job.get("status") not in ("pending", "claimed", "running"):
            return False, ""
        if job.get("claimed_by") and job["claimed_by"] != host:
            return False, ""
        return True, "render order %s" % match.group(1)
    except Exception:
        return False, ""


# --- pushed GPU verdicts: the box tells us what its own driver says ----------------------------------
# This module runs in a container on the head, so it cannot run nvidia-smi on the lead or the brain box.
# The box reports instead (POST /fleet/gpu-state), exactly like the foundry reports its run state.
GPU_STATE_DIR = os.path.join(config.STATE_DIR, "gpu-reports")
GPU_REPORT_FRESH_S = 300.0     # a report older than this is stale; see gpu_health for the asymmetry

# --- pushed GPU LOAD + accounting: "is this load claimed by anything we can see?" -------------------------
# The
# health verdict above answers "is the card alive?"; the police answers "is this process allowed?"; neither
# asks whether the load on the card is ACCOUNTED FOR. Each GPU box pushes utilisation/power/loaded models
# every minute (gpu_load_reporter.py); every push is evaluated here against the three things that can
# legitimately own the card -- a foundry run (run-state), a scheduler claim, an in-flight Kirk call (or one
# that ended within the grace window) -- and busy-with-none-of-them, twice running, is an UNACCOUNTED
# episode: REPORT tier (deck field + one DM per episode), never an arrest.
GPU_LOAD_DIR = os.path.join(config.STATE_DIR, "gpu-load")
GPU_LOAD_FRESH_S = 180.0
GPU_BUSY_UTIL_PCT = 20.0       # util at/above this = busy (idle NVIDIA/AMD cards here read 0-3 %)
GPU_LOAD_STREAK = 2            # consecutive unaccounted pushes before an episode opens (one push can be a tail)
GPU_CALL_GRACE_S = 90.0        # a Kirk call that ended this recently still explains load (unload/KV teardown)


def _gpu_load_path(host):
    return os.path.join(GPU_LOAD_DIR, "%s.json" % host)


def gpu_load(host):
    """Last pushed load record for `host` (with its accounting verdict), or None. Never raises."""
    try:
        with open(_gpu_load_path(host), encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) and "util_pct" in rec else None
    except Exception:
        return None


def _accounted_by(host, now=None):
    """What legitimately explains load on `host` right now, or '' if nothing does. One derivation, shared by
    the evaluation and the deck: foundry run-state, scheduler claim, in-flight Kirk call, recent Kirk call, and on
    the failover box a live Plex transcode (Plex is that card's known user; the placement verdict already says so)."""
    busy, why = training_busy(host)
    if busy:
        return "foundry order: %s" % (why or "run-state")
    if host == TOLKIEN_BOX:
        # The Tolkien box's work is stated under the Fellowship's run-state: Tolkien's free-spot report says whether the Tolkien box's lane
        # (the 1060: an llm order, a chat window, an embed) holds work. Fresh within its own ttl, or it explains nothing
        cap, _rec = _fellowship_capacity()
        if cap is not None and cap.get("rigby_busy"):
            return "Tolkien: %s's lane is busy (%d in line)" % (TOLKIEN_BOX, int(cap.get("rigby_line") or 0))
    if host == FAILOVER:
        _, n, _ = plex_transcoding()
        if n:
            return "Plex: %d transcode(s)" % n
    c = active_claim(host, "gpu")
    if c:
        return "claim: %s" % (c.get("holder") or "?")
    try:
        import ollama_client
        url = (CARDS.get(host, {}).get("url") or (config.TOLKIEN_WINDOW_URL if host == TOLKIEN_BOX else "") or "").rstrip("/")
        if not url:
            return ""                    # no address to match: an empty one would match every call that names none
        rows = [r for r in ollama_client.inflight_snapshot()["rows"] if (r.get("host") or "").rstrip("/") == url]
        if rows:
            return "Kirk call: %s" % (rows[0].get("purpose") or rows[0].get("kind"))
        ended = ollama_client.last_call_end(url)
        if ended and (now or time.time()) - ended <= GPU_CALL_GRACE_S:
            return "Kirk call ended %ds ago" % int((now or time.time()) - ended)
    except Exception:
        pass
    return ""


def _clean_ram(ram):
    """A box's pushed memory reading -> {"free_mb", "total_mb"} or None: numbers only, free within total."""
    try:
        free, total = float(ram["free_mb"]), float(ram["total_mb"])
    except (TypeError, KeyError, ValueError):
        return None
    return {"free_mb": free, "total_mb": total} if 0 <= free <= total and total > 0 else None


def report_gpu_load(host, util_pct, power_w=None, vram_used_mb=None, ollama_loaded=None, source="", stale=False,
                    gpu_memory=None, ollama_version=None, ram=None):
    """Record a box's pushed load sample and evaluate accounting on it. Returns the stored record, whose
    `accounting` is {"state": idle|accounted|unaccounted|no-sample, "by": str, "streak": n, "episode": bool,
    "opened": bool} -- `opened` is True only on the push that opens an episode (the caller DMs on it)."""
    os.makedirs(GPU_LOAD_DIR, exist_ok=True)
    prev = gpu_load(host) or {}
    prev_acc = prev.get("accounting") or {}
    now = time.time()
    rec = {"host": host, "util_pct": None if util_pct is None else float(util_pct),
           "power_w": None if power_w is None else float(power_w),
           "vram_used_mb": None if vram_used_mb is None else float(vram_used_mb),
           "ollama_loaded": list(ollama_loaded or []), "source": source, "stale": bool(stale),
           "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "gpu_memory": gpu_memory, "ollama_version": ollama_version, "ram": _clean_ram(ram)}
    if stale or rec["util_pct"] is None:
        acc = {"state": "no-sample", "by": "", "streak": 0, "episode": False, "opened": False}
    elif rec["util_pct"] < GPU_BUSY_UTIL_PCT:
        acc = {"state": "idle", "by": "", "streak": 0, "episode": False, "opened": False}
    else:
        by = _accounted_by(host, now)
        if by:
            acc = {"state": "accounted", "by": by, "streak": 0, "episode": False, "opened": False}
        else:
            streak = int(prev_acc.get("streak") or 0) + 1
            episode = streak >= GPU_LOAD_STREAK
            # One unaccounted push is SUSPECT (a tail can look like this); the deck says so plainly rather
            # than calling it accounted or crying wolf a push early.
            acc = {"state": "unaccounted" if episode else "suspect", "by": "",
                   "streak": streak, "episode": episode, "opened": episode and not prev_acc.get("episode")}
    if prev_acc.get("episode") and not acc["episode"]:
        acc["cleared"] = True
    rec["accounting"] = acc
    with _lock:
        with open(_gpu_load_path(host), "w", encoding="utf-8") as f:
            json.dump(rec, f)
    return rec


def gpu_accounting_line(host):
    """Deck string for a factory tile. Never invents: 'no load report' when nothing fresh was pushed."""
    rec = gpu_load(host)
    if not rec:
        return "no load report"
    age = _report_age_s(rec)
    if age is None or age > GPU_LOAD_FRESH_S:
        return "load report stale (%s)" % ("?" if age is None else "%.0f min" % (age / 60.0))
    acc = rec.get("accounting") or {}
    util = rec.get("util_pct")
    u = "util %.0f%%" % util if util is not None else "util ?"
    st = acc.get("state")
    if st == "idle":
        return "idle (%s)" % u
    if st == "accounted":
        return "accounted · %s (%s)" % (acc.get("by") or "?", u)
    if st == "suspect":
        return "suspect · busy with no owner, 1 of %d pushes (%s)" % (GPU_LOAD_STREAK, u)
    if st == "unaccounted":
        return "UNACCOUNTED · %s · ollama: %s" % (u, ", ".join(rec.get("ollama_loaded") or []) or "nothing loaded")
    return "no sample (%s)" % (rec.get("source") or "?")


def report_gpu_state(host, healthy, detail="", source="nvidia-smi"):
    """Record a box's own verdict on its GPU. Called by the /fleet/gpu-state endpoint."""
    os.makedirs(GPU_STATE_DIR, exist_ok=True)
    rec = {"host": host, "healthy": bool(healthy), "detail": str(detail)[:400], "source": source,
           "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    with _lock:
        with open(os.path.join(GPU_STATE_DIR, "%s.json" % host), "w", encoding="utf-8") as f:
            json.dump(rec, f)
    return rec


def gpu_report(host):
    """The last pushed verdict for `host`, or None. Never raises -- a missing report is not a verdict."""
    try:
        with open(os.path.join(GPU_STATE_DIR, "%s.json" % host), encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) and "healthy" in rec else None
    except Exception:
        return None


def _report_age_s(rec):
    try:
        ts = datetime.datetime.fromisoformat(rec["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()
    except Exception:
        return None


# --- live state: multi-source, because no single source is trustworthy -------------------------------
def _glances(host, plugin, timeout=4):
    """Glances REST if the box runs it. This is a preference, never the only source:
    The lead's CPU plugin returns all zeros under SYSTEM."""
    # The brain box runs no Glances (journalno task, no install, no listener - observed absence; the "by
    # design" a session wrote there was its own label, never Its firewall drops the SYN, so probing it cost a 4 s timeout on every placement
    #. Absent = not asked; its RAM comes from its own minute report
    #.
    url = config.device_setting(host, "GLANCES") or None
    if not url:
        return None
    try:
        import httpx
        import ollama_client as _oc
        if _oc.probe_quiet(url):                  # timed out within PROBE_QUIET_S: 'could not ask', without the wait
            return None
        try:
            d = httpx.get(url + plugin, timeout=timeout).json()
        except Exception as exc:
            _oc.probe_result(url, exc)
            raise
        _oc.probe_result(url)
        return d or None
    except Exception:
        return None


def _resident_model_for(kind):
    """The model a kind runs, by its base name (no tag), when the lab names one - the spill gate's 'already resident' test.
    None for kinds whose model the caller chooses."""
    m = {"chat": config.BRAIN_MODEL, "chat27b": config.HEAVY_MODEL, "advisor": getattr(config, "MCCOY_MODEL", None),
         "embed": getattr(config, "EMBED_MODEL", None), "medical_review": getattr(config, "MEDICAL_REVIEW_MODEL", None)}.get(kind)
    return str(m).split(":")[0] if m else None


def ram_free_gb(host):
    """Free RAM in GB, or None when unknown. None means 'unknown', and callers treat it as such. Glances where the box
    runs it; else the box's own minute report (gpu_load_reporter `ram`, fresh only) - the brain box runs no
    Glances, so its RAM was never known and nothing checked a split model's RAM side."""
    d = _glances(host, "mem")
    if isinstance(d, dict) and d.get("free"):
        return float(d["free"]) / 1e9
    rec = gpu_load(host)
    age = _report_age_s(rec) if rec else None
    ram = (rec or {}).get("ram")
    if ram and age is not None and age <= GPU_LOAD_FRESH_S:
        return float(ram["free_mb"]) * 1048576 / 1e9
    return None


def cpu_load_pct(host):
    d = _glances(host, "cpu")
    if isinstance(d, dict):
        total = d.get("total")
        # The lead's plugin reports a flat 0.0 for everything; that is 'unknown', not 'idle'.
        if total and float(total) > 0:
            return float(total)
    return None


def gpu_state(host):
    """(busy, why). GPU truth comes from worker heartbeat, ollama residency, and claims.

    Avoid nvidia-smi: it is absent on AMD hosts and unreliable on some NVIDIA setups (e.g.,
    the brain box returns "No devices were found" while ollama holds memory). This prevents
    scheduling conflicts like loading a VLM during an active training run."""
    c = active_claim(host, "gpu")
    if c:
        return True, "claimed by %s (%s)" % (c.get("holder"), c.get("note") or "no note")
    if host == LEAD_BOX:
        try:
            import comfy_client
            if comfy_client.render_busy():
                return True, "training or rendering"
        except Exception:
            pass
        return False, "free"
    url = CARDS[host]["url"]
    if not url:
        return False, "no gpu"
    # VRAM is a cache, not an allocation: residency is ignored. Only CLAIM reserves a card.
    return False, "free"


def host_state(host):
    """One dict describing what this host can take right now. Unknown values stay None."""
    card = CARDS[host]
    busy, why = gpu_state(host)
    ram = ram_free_gb(host)
    cpu = cpu_load_pct(host)
    return {"host": host, "label": card["label"], "gpu_busy": busy, "gpu_why": why,
            "ram_free_gb": ram, "cpu_pct": cpu, "vram_gb": card["vram_gb"],
            "ram_gb": card["ram_gb"], "cpu_count": card["cpu"]}


# --- GPU HEALTH: catch a lost card IMMEDIATELY, not hours later ------------------------------------
def gpu_health(host):
    """Returns (healthy, reason). A card can be present but unusable.

    Uses two signals because neither alone covers the fleet:
      1. nvidia-smi verdict, pushed from the host via `report_gpu_state`.
         Authoritative for NVIDIA; absent for AMD. Pushed because this code
         runs in a container and cannot poll remote hosts directly.
      2. resident-but-0%-VRAM. Vendor-neutral fallback for when a model loads
         onto an already-broken card.

    Note: `size_vram` is recorded at load time and never revised, so it cannot
    detect a GPU dying *after* a successful load. Signal (1) catches this case.

    Staleness handling is asymmetric:
      - A stale "lost" status remains "lost" because recovery requires a reboot,
        which restarts the reporter and clears the state.
      - A stale "ok" status is discarded, falling through to signal (2),
        since past health does not guarantee current health."""
    rec = gpu_report(host)
    if rec and not rec.get("healthy"):
        age = _report_age_s(rec)
        return False, ("GPU LOST per %s: %s%s"
                       % (CARDS.get(host, {}).get("label", host),
                          str(rec.get("detail") or "device unavailable")[:120],
                          "" if age is None or age < GPU_REPORT_FRESH_S
                          else " (report %.0f min old; a lost GPU stays lost until reboot)" % (age / 60.0)))
    try:
        import ollama_client as _oc
        url = CARDS.get(host, {}).get("url")
        if url:
            # the shared quick probe: REACH_TIMEOUT and the quiet window; unanswered stays 'ok' below - could not ask is never dead
            r = _oc._quick_get(url.rstrip("/") + "/api/ps")
            if True:
                for mm in r.json().get("models", []):
                    size, vram = mm.get("size", 0), mm.get("size_vram", 0)
                    if size and vram / float(size) < 0.05:
                        return False, ("GPU NOT IN USE: %s is resident but %.0f%% on the card "
                                       "(model running on CPU)" % (mm["name"], 100.0 * vram / size))
    except Exception:
        pass
    return True, "ok"


def gpu_lost_report():
    """Fleet-wide GPU health and reachability.

    Returns separate reachability and health status because probe failures (network
    down) must not be interpreted as hardware failures for placement logic,
    while UI/alerting requires explicit `unreachable` state rather than defaulting
    to healthy or lost.
    """
    out = {}
    for h in (LEAD_BOX, BRAIN, FAILOVER):
        ok, why = gpu_health(h)
        url = CARDS[h].get("url")
        alive = True
        if url:
            try:
                import ollama_client
                alive = ollama_client.reachable(url)
            except Exception:
                alive = True          # cannot tell -> do not cry wolf on the tile either
        out[h] = {"healthy": ok, "reason": why if alive else "unreachable",
                  "reachable": alive, "label": CARDS[h]["label"]}
    return out


# --- placement --------------------------------------------------------------------------------------
# Job kinds and what they need. `vram_gb` 0 means "no GPU at all" -- a CPU/IO job, which is a large
# share of our work (dataset acquisition, dedupe, collect, caption transforms, RAG chunking).
KINDS = {
    "chat":    {"vram_gb": 6.0,  "interactive": True,  "cpu_heavy": False},
    "chat27b": {"vram_gb": 20.0, "interactive": True,  "cpu_heavy": False},
    # The 0.3 GB embed model beside Plex is how HAI's embeds have always run on the failover box (the fixed list's failover);
    # the embed family on the menu places them (embed_placement) and prices the call per box.
    "embed":   {"vram_gb": 1.0,  "interactive": True,  "cpu_heavy": False, "coexists_with_plex": True},
    "vision":  {"vram_gb": 6.0,  "interactive": False, "cpu_heavy": False},
    # The FLUX.2-32B's footprint. Unchanged on purpose -- see render_small below.
    "render":  {"vram_gb": 20.0, "interactive": False, "cpu_heavy": False},
    # `render_small` added to support 4B-class models (klein) on 8GB cards.
    # The heavy `render` entry (20 GB) is retained to prevent OOM errors when
    # large models (e.g., 32B Flux.2) are scheduled; capacity enforcement is
    # handled by the `requires` capability layer.
    "render_small": {"vram_gb": 6.0, "interactive": False, "cpu_heavy": False},
    "train":   {"vram_gb": 22.0, "interactive": False, "cpu_heavy": False},
    "cpu":     {"vram_gb": 0.0,  "interactive": False, "cpu_heavy": True},
    # A job's test run on another box: no card, all CPU, and it never lands beside a live HAI job: nothing has measured what that costs the job.
    "test":    {"vram_gb": 0.0,  "interactive": False, "cpu_heavy": True, "yields_to_live": True},
    # A job's page shown in a browser on a real GPU. A browser's draw, not a
    # model, so it fits beside one; it never displaces a live HAI job's card, and Plex keeps the failover box's first.
    "page":    {"vram_gb": 0.5,  "interactive": True,  "cpu_heavy": False, "yields_to_live": True},
    # vram_gb is the MIN viable footprint. Factories use the smallest quant that fits
    # their card (e.g., Q4_K_M ~6 GB), so this value must not be set to the largest model.
    "caption": {"vram_gb": 6.0,  "interactive": False, "cpu_heavy": False},
    # Review work: the doc claim sweep's desk ORDER. The menu row the desk weighs picks the box
    # and its model (the desk's model_fit checks the fit), so the envelope is any GPU compute order's (kind_for_order's
    # render_small band) - the old 16 GB sized it for one 27B and kept every review on the lead It still never displaces a live HAI job (B12).
    "review":  {"vram_gb": 6.0,  "interactive": False, "cpu_heavy": False, "yields_to_live": True},
    # The lead's medical helper (medical_review.py). II-Medical 8B's measured 6,702 MiB at its 25,600 window. Someone is waiting, but it never takes a box from live work: no helper beats a stall.
    "medical_review": {"vram_gb": 6.6, "interactive": True, "cpu_heavy": False, "yields_to_live": True, "joins_own_window": True},
    # McCoy, the advisor (tev1 4B decision model; 5.9 GB peak measured beside a live transcode, every Plex band ok -
    # register, advisor decision models 10-16). - so HOME the failover box, any card that can take
    # it otherwise. It coexists with Plex (runs in the transcode's buffer, no Plex-first gate) and never displaces a live
    # HAI job's model (that box costs the job a reload; no card free = McCoy is unsure = the lead, mccoy.py).
    "advisor": {"vram_gb": 6.0,  "interactive": True,  "cpu_heavy": False, "yields_to_live": True,
                "home": ADVISOR_HOME, "coexists_with_plex": True},
}

# Weights for load-splitting to balance heterogeneous hardware speeds;
# an even split leaves faster boxes idle waiting for slower ones.
CAPTION_S_PER_IMG = roster_table("caption_s_per_img")

# Tablet CPU fleet, from the roster's `tablets`. GFLOPS (single-threaded numpy).
# Independent jobs only; MPI fails due to high-latency shared AP radio.
# Not in CARDS: no GPU/LLM semantics. Used by place() for eligible CPU work.
# Runtime architecture is per-node. Direct CPU placement remains fail-closed when the
# deployed payload ABI is unsupported; Fellowship/Tolkien may still route compatible work.
TABLETS = roster_tablets()
# The head places by speed and size only; each tablet's address and serial live with Tolkien, which reaches it.
# Tablet factories must declare capability despite being absent from CARDS (which implies GPU/LLM).
# Without this, /fleet/run-state validation fails for tablet publishes, returning HTTP 200 {"ok": false}
# which callers misinterpret as success. The fleet declares as a single host, but place() ranks tablets
# individually for placement.
NON_CARD_FACTORIES = {"fellowship"}
# Plex/media host: takes fleet ORDERS only when no other x86 factory can -- the box is
# primarily the Plex host. Chat placement already encodes this as a dominating tier;
# place_order applies this one at ORDER selection.
ORDER_HOST_DEPRIORITY = roster_table("order_deprioritize")
# Baselines are observed medians, not estimates; weights reflect throughput capacity.
TRIAGE_CLASSIFY_S_PER_ROW = roster_table("triage_classify_s_per_row")

# Measured throughput (s/section). Do not bump without new calibration.
TRIAGE_REWRITE_S_PER_SECTION = roster_table("triage_rewrite_s_per_section")

# Scope restricts uncalibrated models to fail-closed (empty scope).
# "None" means full scope; specific sets limit allowed section tiers. The calibrated models are the lab's own
# measurement: TRIAGE_REWRITE_MODEL_SCOPE, JSON {"<model>": null | ["<tier>", ...]}; none set = every model uncalibrated.
TRIAGE_REWRITE_MODEL_SCOPE = {m: (None if v is None else set(v)) for m, v in
                              json.loads(os.getenv("TRIAGE_REWRITE_MODEL_SCOPE", "") or "{}").items()}

TABLET_SSH_PORT = 8022            # Termux sshd, key-only
# Split weights for chunk(): proportional GFLOP/s, same idiom as CAPTION_S_PER_IMG. An even split
# leaves gandalf idle waiting for merry.
TABLET_GFLOPS = {h: t["gflops"] for h, t in TABLETS.items()}

_tablet_alive_cache = {}          # host -> (ts, bool); WiFi drop-offs are routine, probe cheaply


def tablet_alive(host, ttl=60.0, timeout=1.5):
    """Probe Termux sshd via TCP. Fail-closed: unreachable hosts are not offered work.

    Resolves <host>.lan via Pi-hole. DHCP leases may drift; failing closed ensures safety."""
    import socket
    now = time.time()
    hit = _tablet_alive_cache.get(host)
    if hit and now - hit[0] < ttl:
        return hit[1]
    ok = False
    try:
        with socket.create_connection((host + config.TABLET_DNS_SUFFIX, TABLET_SSH_PORT), timeout=timeout):
            ok = True
    except OSError:
        ok = False
    _tablet_alive_cache[host] = (now, ok)
    return ok


def plex_transcoding():
    """(True | False | None, n, why): is Plex transcoding on the failover box right now? Tautulli's live transcode count is
    the signal, because it sees CPU-mode transcodes and our own order's load cannot fake it. None = could not ask."""
    try:
        import app as _app
        a = _app.plex.activity()
    except Exception:                                                # noqa: BLE001
        a = None
    if a is None:
        return None, None, "tautulli unreachable - could not tell"
    n = int(a.get("transcodes") or 0)
    return n >= config.FAILOVER_MAX_TRANSCODES, n, "%d Plex transcode(s)" % n


def _plex_gate(model=None):
    """Policy 4: The failover box's GPU is Plex's first. Fails CLOSED -- "could not ask the
    gate" is not "Plex is idle"; the box is order-last anyway, so exclusion costs
    near zero. `model`: the model the order would serve, so the gate's claim lookup is about that model (an order's own
    caller holding a live claim for it is not a reason to hold the order)."""
    try:
        import app as _app
        return _app.failover_available(model) if model else _app.failover_available()
    except Exception:                                                # noqa: BLE001
        return False, "plex gate unreachable - fail closed"


POLICY_LENSES = ("fastest", "least_energy", "best_quality")
_POLICY_CACHE = {"key": None, "data": None}


def policy_data():
    """One fingerprinted read for placement rows AND memory profiles, as this head reads the table (config.load_policy:
    a device that names a reference box places from that box's rows until its own are measured); unreadable is an
    empty document."""
    try:
        st = os.stat(config.POLICY_TABLE)
        key = (st.st_mtime, st.st_size)
        if _POLICY_CACHE["key"] != key:
            _POLICY_CACHE.update(key=key, data=config.load_policy(config.POLICY_TABLE))
        return _POLICY_CACHE["data"]
    except Exception:                                                # noqa: BLE001
        return {}


def read_rate(box, model):
    """The menu's MEASURED read rate (tokens/s) of `model` on `box` - the policy table carries one per pair; None when
    unmeasured. A chat window prices re-reading an older chat with it (helper_window.fetch_from_array)."""
    data = policy_data()
    for fam in ((data.get("families") or {}) if isinstance(data, dict) else {}).values():
        for tier in ((fam.get("tiers") or {}) if isinstance(fam, dict) else {}).values():
            for rows in (tier.values() if isinstance(tier, dict) else ()):
                for r in (rows if isinstance(rows, list) else ()):
                    if r.get("box") == box and r.get("model") == model and r.get("read_tok_s"):
                        return float(r["read_tok_s"])
    return None


def policy_order(family, tier, lens):
    """The policy table's ordered (box, entry) rows; [] when this family/tier/lens is not priced."""
    try:
        rows = policy_data()["families"][family]["tiers"][tier][lens]
        return [(r["box"], r) for r in rows]
    except (KeyError, TypeError):
        return []


def policy_lens(kind, lens=None, interactive=None):
    """The lens a placement uses: the caller's, else fastest for an interactive kind and least_energy for the rest.
    One derivation for place() and for compose_orders' policy tokens."""
    if lens in POLICY_LENSES:
        return lens
    spec = dict(KINDS.get(kind) or KINDS["cpu"])
    if interactive is not None:
        spec["interactive"] = bool(interactive)
    return "fastest" if spec.get("interactive") else "least_energy"


# A template that lets the table choose the setting carries these; compose_orders resolves them per host from the
# SAME policy row that ranked the host (the placed delegate, #34 Phase 7). Whole-element tokens become values;
# *_ARGS tokens expand to zero or more argv elements.
POLICY_VALUE_TOKENS = ("{POLICY_MODEL}", "{POLICY_NUM_CTX}")
# {POLICY_OFFLOAD_ARGS}: --offload-ok when the row's model is a declared split on that host (offload_ok - the menu's own
# list), as the menu cells and the chat window declare it; without it the factory's residency check refuses the order
POLICY_ARG_TOKENS = ("{POLICY_THINK_ARGS}", "{POLICY_TEMPERATURE_ARGS}", "{POLICY_OFFLOAD_ARGS}")


def _think_args(think):
    t = str(think).strip().lower()
    if t in ("", "off", "false", "none", "n/a", "default"):
        return []
    if t in ("on", "true"):
        return ["--think"]
    return ["--think-level", t]


def _temperature_args(temperature):
    try:
        return ["--temperature", "%g" % float(temperature)]
    except (TypeError, ValueError):
        return []                                      # "vendor": the model's own default


def weighed_entries(family, tier, urgency):
    """For an order with family + tier + urgency: ((band, rejected), {host: the band row that ranks it}) - the per-request
    weighing computed ONCE, so the host place() picks and the model/settings filled into its order come from the SAME row
    (the row that chose the box sets its model, as for chat). ((None, None), {}) without urgency or on any failure."""
    if not (family and tier and urgency in ("interactive", "background")):
        return None, {}
    try:
        band, rejected = weigh_rows(family, tier, urgency == "interactive")
    except Exception:                                                # noqa: BLE001 - the lens path is the fallback
        return None, {}
    rows = {}
    for c in band:
        rows.setdefault(c["host"], c["entry"])
    return (band, rejected), rows


def resolve_policy_tokens(spec, host, family, tier, lens, entry=None):
    """-> (spec, None) with the policy tokens filled from the table row that placed `host`, or (None, reason). A spec
    without tokens comes back unchanged. Refuses (never a default model) when the order names no family/tier, the
    table has no row for the host, the model has no tested context there, or the host does not declare the model.
    `entry` = the weighed row that ranked `host` (weighed_entries); without it, the `lens` row for the host."""
    text = json.dumps(spec)
    if "{POLICY_" not in text:
        return spec, None
    if not family or not tier:
        return None, "the template carries policy tokens but the order names no family/tier"
    if entry is not None:
        lens = "weighed"
    else:
        # the host's FIRST row on that lens: a box can carry several models' winners, best first (dict() kept the last)
        entry = next((e for b, e in policy_order(family, tier, lens) if b == host), None)
    if not entry:
        return None, "%s has no %s/%s/%s row in the placement table" % (host, family, tier, lens)
    model = entry.get("model")
    if not declares_model(host, model):
        return None, "%s does not declare model.%s" % (host, model)
    ctx = config.ctx_for((CARDS.get(host) or {}).get("url"), model, None)
    if "{POLICY_NUM_CTX}" in text and not ctx:
        return None, "no tested context for %s on %s (config.MODEL_NUM_CTX)" % (model, host)
    values = {"{POLICY_MODEL}": model, "{POLICY_NUM_CTX}": str(ctx)}
    expand = {"{POLICY_THINK_ARGS}": _think_args(entry.get("think")),
              "{POLICY_TEMPERATURE_ARGS}": _temperature_args(entry.get("temperature")),
              "{POLICY_OFFLOAD_ARGS}": ["--offload-ok"] if offload_ok(host, model) else []}
    out = json.loads(text)
    args = []
    for a in out.get("args") or []:
        if a in expand:
            args.extend(expand[a])
        else:
            for tok, val in values.items():
                a = a.replace(tok, val) if isinstance(a, str) else a
            args.append(a)
    if "args" in out:
        out["args"] = args
    left = [t for t in POLICY_VALUE_TOKENS + POLICY_ARG_TOKENS if t in json.dumps(out)]
    if left:
        return None, "policy tokens outside `args` are not resolved: %s" % ", ".join(left)
    if isinstance(out.get("requires"), list) and "model.%s" % model not in out["requires"]:
        out["requires"].append("model.%s" % model)
    out["policy_row"] = {"family": family, "tier": tier, "lens": lens, "model": model, "think": entry.get("think"),
                         "temperature": entry.get("temperature"), "num_ctx": ctx}
    return out, None


def wilson_high(p, n, z=1.96):
    """The Wilson interval's UPPER bound for a pass rate `p` over `n` tries - the other side of the menu's `quality_low`
    (the same interval, tolkien_llm.wilson_low). No `n` = the point rate itself (no range claimed)."""
    try:
        p, n = float(p or 0), int(n or 0)
    except (TypeError, ValueError):
        return float(p or 0)
    if n <= 0:
        return p
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (centre + spread) / den)


def residency_cost(host, model, load_s, read_tok_s=None, context_tokens=0, loaded=None):
    """(seconds, note): what a request to `host` would pay before its first token because the model is cold. 0 when the model is resident; when
    it is not: the table's MEASURED load_s, plus - for a request that brings a conversation of `context_tokens` - its
    re-read at the table's MEASURED read_tok_s (a cold model has lost that conversation's cache). A part nobody measured
    is never charged; the note says which. A host that could not be ASKED is priced COLD; it is still placed when it is the best option, it just cannot beat a box known to be alive."""
    url = (CARDS.get(host) or {}).get("url")
    if not url or not model:
        return 0.0, None
    if loaded is None:                      # a caller weighing several rows on one box asks the box once and passes it
        try:
            import ollama_client as _oc
            loaded = _oc.models_resident(url)
        except Exception:                                            # noqa: BLE001
            loaded = None
    unknown = loaded is None or loaded == "unknown"
    if unknown:
        loaded = []
    # /api/ps names a model by its full tag ("nomic-embed-text:latest"); a table row may carry the untagged name. The
    # embed proof charged the brain box "cold +1 s load" with the model resident under its tag.
    if any(m == model or m == model + ":latest" or model == m + ":latest" for m in loaded):
        return 0.0, "resident"
    cost, notes_ = 0.0, (["residency unknown (could not ask) - priced cold"] if unknown else [])
    if load_s is None:
        notes_.append("cold, load unmeasured")
    else:
        cost += float(load_s)
        notes_.append("cold +%.0f s load" % float(load_s))
    if context_tokens:
        if read_tok_s:
            reread = float(context_tokens) / float(read_tok_s)
            cost += reread
            notes_.append("+%.0f s re-read of %d tokens" % (reread, int(context_tokens)))
        else:
            notes_.append("re-read unmeasured")
    return cost, ", ".join(notes_)


def _apply_policy(ranked, family, tier, lens, context_tokens=0):
    """Re-order hosts the hard rules already PASSED by the table's order (#34 Phase 7 step 3). The table never adds a host
    and never removes one: fit, health, Plex, claims and the caller's pins decided eligibility before this. Hosts the
    table does not list keep their own order after the listed ones. On the `fastest` lens a cold model's MEASURED load
    is added to its seconds per solve (step 4), within home and within overflow: a request someone waits on pays the
    load once, before its first token. Other lenses keep the table's order (a load's energy is not measured).
    -> (ranked, applied?)"""
    order = policy_order(family, tier, lens)
    if not order:
        return ranked, False
    pos, entry = {}, {}
    for i, (box, e) in enumerate(order):
        host = desk_host(box)                 # the Fellowship's workers rank as ONE destination, at its best row
        if host not in pos:
            pos[host], entry[host] = i, e
    out = []
    for i, (host, score, why) in enumerate(ranked):
        e = entry.get(host)
        key = (2, pos.get(host, len(pos) + i), 0.0)
        if e is not None:
            cost, note = (residency_cost(host, e.get("model"), e.get("load_s"), e.get("read_tok_s"), context_tokens)
                          if lens == "fastest" else (0.0, None))
            key = (0 if e.get("role", "home") == "home" else 1,
                   float(e.get("s_per_solve") or 0) + cost if lens == "fastest" else pos[host], pos[host])
            why = "; ".join(w for w in (why, "policy %s/%s/%s #%d: %s%s %s, %.0f%% at %.1f s/solve%s" % (
                family, tier, lens, pos[host] + 1, "" if e.get("box") in (None, host) else e["box"] + " ",
                e.get("model"), e.get("think"), 100 * float(e.get("quality") or 0),
                float(e.get("s_per_solve") or 0), ("; " + note) if note else "")) if w)
        out.append((key, (host, score, why)))
    return [r for _k, r in sorted(out, key=lambda kv: kv[0])], True


# --- Area C step 1: the PER-REQUEST WEIGHING ------------------
# The charter's rule (§52: "the least expensive source of compute and intelligence that reliably satisfies a task's
# quality + time requirements") read literally: the REQUEST brings needs (a quality floor, a time budget), the lab's
# state brings each row's real costs now, and the pick is the cheapest row that meets the needs. Every row's three
# values are read AT ONCE - quality (its 95 % lower bound), speed (wait + wake + cold load + re-read + solve) and
# cost (J/solve; the price multiplies every row alike within one request). Not a fourth lens: the three lenses stay the
# three sorted views; this is area C inside place(), per request (checklist A1/A2). Order, measured into shape by the
# step-0 replay (weighing_replay.py): (1) the QUALITY NEED - the tier's best HOME row's lower bound - binds first; a
# row whose point quality is below it is a dominated fallback, home or overflow (think_off_refusal's test,
# generalised); (2) within the band, rows inside the family's interactive line before rows past it (checklist A4); a
# cold lead past the line still wins when nothing of its quality is inside it - the wait is the price, never a lower
# tier; (3) home before overflow; (4) cheapest energy; (5) fastest. Background work is not bound by the line.
AUTO_TIER_FAMILY = {"fellowship": "chat-turn", "8gb": "chat-c1", "lead": "chat-c1", "tool": "tool"}
AUTO_TABLE_TIER = {"tool": "8gb"}        # McCoy's `tool` = plain code / a lab tool; the model that drives it is an 8 GB row
# The ONE family answer of McCoy's measured to name the task's SHAPE: at the 0.90 line he called 42 rows "coding" - 40 menu coding tasks or W1 programs, 2 the "write
# add(a, b)" control, every one a request for code - and caught all 36 coding tasks. His other families name TOPICS
# (63 % for the full mapping, 10-22) and stay off. Only on the 8 GB tier: the coding rows are function-sized tasks,
# and on the lead tier their home row is qwen3-coder:30b thinking off - a request McCoy sends to the lead is one he
# judged HARD, which is the 3.8's The Fellowship serves
# chat turns only, so its tier keeps chat-turn.
AUTO_CODING_FAMILY = "coding-f2"            # the coding family with the most measured tasks (24 x 3-9 per row)
AUTO_CODING_TIERS = ("8gb",)


def auto_needs(tier, decided=True, kind=None, kind_confidence=None, line=None, family=None, family_confidence=None,
               depth=None, depth_confidence=None, depth_line=None):
    """McCoy's triage row -> what Spock weighs an Auto turn by: (menu family, table tier, interactive). The TIER is the
    tier's line is McCoy's (config.MCCOY_TIER_THRESHOLD, applied in mccoy.triage's `decided`); undecided = the lead ("unsure => the 3.8"). The menu family
    comes from the tier, except McCoy's "coding" sure at the same line on a turn he is sure is BELOW the lead, which
    weighs the 8 GB coding rows (AUTO_CODING_FAMILY: a plain chat, a chat asking for work and a job are different things
    on the back end - checklist C3). `job` makes the turn background only when the kind is sure at the same line; unsure = interactive,
    the direction that never makes a user wait. `deep` (area C step 3, design § 6 step 8a): McCoy's `depth` answer, trusted
    only at MCCOY_DEPTH_THRESHOLD - then the interactive LINE stops rejecting rows (a deep answer is worth its wait; every
    other gate stays); unsure = quick, the direction that never makes a user wait. One derivation for the chat caller and
    for the placement record. -> (family, table tier, interactive, deep)."""
    line = config.MCCOY_THRESHOLD if line is None else line
    t = tier if (decided and tier in AUTO_TIER_FAMILY) else "lead"
    interactive = not (kind == "job" and (kind_confidence or 0.0) >= line)
    fam = AUTO_TIER_FAMILY[t]
    if family == "coding" and (family_confidence or 0.0) >= line and t in AUTO_CODING_TIERS:
        fam = AUTO_CODING_FAMILY
    dline = getattr(config, "MCCOY_DEPTH_THRESHOLD", line) if depth_line is None else depth_line
    deep = depth == "deep" and (depth_confidence or 0.0) >= dline
    return fam, AUTO_TABLE_TIER.get(t, t), interactive, deep


# A user's GENERAL mode is a WEIGHT inside the same weighing, never a route of its own and never a blind tier step: a
# good model can often answer about as fast as a weaker one, and the lead's better record should still pull the choice
# towards it. An EXACT model pick stays a pin (policy 10).
# Every tier's table prices every box on the family's tasks, so the rows are comparable; the tier only names its HOME
# rows, which set the quality need. The modes move that need and the order inside the band (weigh_rows `mode`):
# quick - the need drops to the WEAKEST CARD home row of McCoy's tier (a quality the table already accepts there; a
# tablet row never sets it, so Quick never means the weakest answers); every row within QUICK_TIME_SLACK_S of the fastest counts as
# fast, and among those the better quality wins; then the rest by time. The lead is weighed like any row - a
# warm 3.8 a breath behind a 9B is the quick answer.
# hard - the need rises to the BEST row's 95 % lower bound, whatever its role (statistically as good as the best);
# inside that band the best point quality first (weighted hard towards quality), home, energy and time
# only break ties, and the line stops rejecting rows (deep). The lead's row takes the think-hard path (app.py).
MODES = ("quick", "hard")


def mode_verdict(verdict, quick=False, hard=False):
    """McCoy's verdict with the user's general mode attached -> a new verdict (the original untouched), or the same one
    when no mode is set or McCoy did not decide (None). Adds `mode` for the weighing and the placement record. Quick
    sends a lead verdict to the weighing (lane "below") instead of straight to the lead, so the lead competes on time
    with the rest; Hard keeps McCoy's lane (a lead verdict already goes to the best row). Quick wins when both are set
    (the page never sends both: Hard disables under the 8B)."""
    if not verdict or not (quick or hard):
        return verdict
    v = dict(verdict)
    v["mode"] = "quick" if quick else "hard"
    if quick:
        v["lane"] = "below"
    return v


def policy_rows(family, tier):
    """Every table row of family x tier across the three lenses, one per (box, model, think, temperature) - the union,
    because the `fastest` lens already drops rows past the line and the weighing applies the line itself. Temperature is
    in the key: since the table keeps each model's own lens winners, one model's winners can differ by it
    alone, and dropping one moved the quality need (the 27B's t=0.0 row, lower bound 0.940, stood in for its t=0.6 row's
    0.969, and every non-owner's lead turn left the lead in the replay)."""
    seen, out = set(), []
    for lens in POLICY_LENSES:
        for box, e in policy_order(family, tier, lens):
            k = (box, e.get("model"), str(e.get("think")), str(e.get("temperature")))
            if k not in seen:
                seen.add(k)
                out.append(e)
    return out


def _interactive_line(family):
    try:
        v = policy_data()["families"][family].get("interactive_line_s")
        return float(v) if v else None
    except (KeyError, TypeError, ValueError):
        return None


def _price_of_the_hour():
    """energy.price_now + its band, fail-soft (#34 area C step 2): a module or sensor that cannot be read is the
    placeholder, said in the record - a placement never raises over a price."""
    try:
        import energy as _en
        p = _en.price_now()
        p["band"] = _en.price_band(p["usd_per_kwh"])
        return p
    except Exception as exc:                                             # noqa: BLE001
        return {"usd_per_kwh": float(getattr(config, "ENERGY_PRICE_PLACEHOLDER", 0.11)), "source": "placeholder",
                "hour": None, "curve": None, "band": "normal", "error": repr(exc)[:120]}


def _hold_notice(price):
    """What an Auto request is told when the hour's price is at or above the EXTREME line: the price and its source, the line, the cheapest coming hour when a curve can
    say, and the way to run it anyway - a pin (his override, D3)."""
    nxt = ""
    try:
        import energy as _en
        w = _en.cheapest_window(price.get("curve"), 1) if price.get("curve") else None
        if w:
            nxt = " The cheapest coming hour is %s %02d:00 at $%.3f/kWh." % (w["day"], w["start_hour"] - 1, w["avg_price"])
    except Exception:                                                    # noqa: BLE001
        pass
    return ("Electricity is $%.2f/kWh this hour (%s), at or above the lab's extreme line of $%.2f/kWh - Auto is holding "
            "this until the price drops.%s To run it anyway, pick a Model."
            % (price["usd_per_kwh"], price["source"], config.ENERGY_EXTREME_PRICE or 0.0, nxt))


def _window_open_cost(host, model):
    """(seconds, note) a chat turn on `host` pays to open a NEW window for `model`: 0 when one is live; else the median a
    new window there took to its first output, load left out (chat_window.first_output_s). Fail-soft: no window module
    (Tolkien's copy of this file) or nothing measured on that box = 0 with the reason in the note."""
    try:
        import chat_window as _cw
    except Exception:                                                # noqa: BLE001
        return 0.0, None
    try:
        if _cw.has_window(host, model):
            return 0.0, "window open"
        s, n = _cw.first_output_s(host)
    except Exception as exc:                                         # noqa: BLE001
        return 0.0, "window open cost unreadable (%s)" % type(exc).__name__
    if s is None:
        return 0.0, "new window, its open time not measured on %s yet" % host
    return float(s), "+%.0f s new window to first output (median of %d)" % (s, n)


# Chat's box order (the brain box < the lead < the failover box): place() scores it as a dominating tier, and the weighing keeps the
# last-resort box behind every other row. ONE derivation: place() and the weighing both read this table, so the
# weighing's own sort can never put the failover box (the Plex host) ahead of a free brain box.
CHAT_TIER = roster_table("chat_tier", order_by_value=True)


def chat_last_resort_last(rows):
    """`rows` with the chat tier's last-resort box after every other row, the order otherwise kept, each moved row noted."""
    last = max(CHAT_TIER.values())
    keep = [c for c in rows if CHAT_TIER.get(c.get("host")) != last]
    moved = [c for c in rows if CHAT_TIER.get(c.get("host")) == last]
    for c in moved:
        c.setdefault("notes", []).append("chat's last resort (hosts the lab's services): after every other row")
    return keep + moved


def _row_prices(box, model):
    """The table's measured load_s / read_tok_s for `model` on `box`, from any family (they are prices of the box and model,
    not of the job) -> {"load_s", "read_tok_s"}, either None when nobody measured it."""
    out = {"load_s": None, "read_tok_s": None}
    for fam in ((policy_data() or {}).get("families") or {}).values():
        for lenses in (fam.get("tiers") or {}).values():
            for rows in (lenses or {}).values():
                for e in rows or []:
                    if e.get("box") == box and e.get("model") == model:
                        for k in out:
                            if out[k] is None and e.get(k) is not None:
                                out[k] = e[k]
                        if all(v is not None for v in out.values()):
                            return out
    return out


ON_THE_CARD = ("claimed", "running")     # a factory has taken the order


def desk_lines():
    """{host: [order names]}: the desk orders a NEW window order for that box would stand behind, since a factory works
    one order at a time. In the line: an order a factory has taken, and a pending one the desk would hand that factory
    at its next ask (p20_queue.dispatchable under the hour's energy rule, the factories' own poll). Not in the line:
    this Kirk's own chat windows (a turn joins one or pays its open), a job's page renders (seconds), an order the desk
    is holding or one waiting on a predecessor, and anything in the desk's folder that is not an order (the boxes'
    status files carry a host and no id). None when the desk could not be read."""
    try:
        import chat_window as _cw
        import p20_queue
        try:
            import energy as _en
            energy_ok = bool(_en.is_runnable_now()[0])
        except Exception:                                            # noqa: BLE001
            energy_ok = False                        # the factories' poll fails closed the same way (app.p20_jobs)
        ready = {j.get("id") for j in p20_queue.dispatchable(energy_ok)}
        lines = {}
        for j in p20_queue.all_jobs():
            jid, host = j.get("id"), str(j.get("host") or j.get("claimed_by") or "").lower()
            if not jid or not host or (j.get("status") not in ON_THE_CARD and jid not in ready):
                continue
            name = str(j.get("order_name") or jid)
            if name.startswith(PAGE_ORDER_PREFIX) or _cw.is_own_window(name):
                continue
            lines.setdefault(host, []).append(name)
        return lines
    except Exception:                                                # noqa: BLE001 - could not ask
        return None


def line_ahead(host, lines=False):
    """desk_lines() for one box -> its order names ([] = no line); None when the desk could not be read. `lines`: a
    desk_lines() result already in hand (one read per weighing)."""
    if lines is False:
        lines = desk_lines()
    return None if lines is None else list(lines.get(host, []))


def turn_line(host, lines=False):
    """The orders a NEW chat turn placed on `host` would wait behind ([] = none; None = the desk could not be read).
    Kirk's served window there that a run is using keeps its card whatever waits behind it. An idle one gives way to
    the order behind it at the factory's next ask so it holds the card for no new turn."""
    try:
        import chat_window as _cw
        own = _cw.held_on(host)
        if own and _cw.window_in_use(host, own):
            return []
    except Exception:                                                # noqa: BLE001
        pass
    return line_ahead(host, lines)


# set by app: host -> (the order the deck shows running there, its remaining seconds or None). The deck's own reading
# (app._factory_view), so the weighing prices the wait the tile shows; unset where no deck runs (Tolkien) = unknown.
FACTORY_REMAINING = [None]


def _line_wait(host, model, lines=False):
    """(note, wait_s): the factory's line for a turn placed on `host`, priced as WAIT when the deck knows it - the
    order ahead is the one running there and the deck states its remaining time (Phase-7 design § 1, SPEED: "an order
    ahead: the holder's remaining time, from ... the deck's eta"; the treatment Tolkien box's line has had since 10-23).
    wait_s None with a note = the wait is unknown (more than one order ahead, or the deck states none), said in the
    row and not judged inside the line. ("", None) = no line."""
    note = _line_note(host, model, lines)
    if not note:
        return "", None
    ahead = line_ahead(host, lines)
    if not ahead or len(ahead) != 1 or not FACTORY_REMAINING[0]:
        return note, None
    try:
        running, remaining = FACTORY_REMAINING[0](host)
    except Exception:                                                # noqa: BLE001 - could not ask = unknown
        return note, None
    if remaining is None or not running or not str(running).startswith(str(ahead[0])[:40]):
        return note, None
    return ("%s ahead on %s (+%.0f s wait, the deck's remaining time)" % (ahead[0][:40], host, remaining),
            float(remaining))


def _line_note(host, model, lines=False):
    """Why a turn placed on `host` would WAIT behind the factory's line, or "" when it would not. An order on a box is
    a claim, and claims decide eligibility before any row is priced: place() leaves the box out for a chat turn by the
    same reading (turn_line), and the band's next row serves (the overflow rule). The row's note says why. A served
    window of this model that a run is using keeps its card whatever waits behind it."""
    try:
        import chat_window as _cw
        import ollama_client as _oc
        # ANOTHER run holding Kirk's window there is a wait for this one (design § 1, SPEED: "another run's window
        # turn"; B4b: a request that would wait behind another user's work goes to the next box). The asking run's
        # own window never counts. Concurrency comes from the fleet: jobs do not trade turns on one card.
        if _cw.serving_others(host, getattr(_oc._CALL_CONTEXT, "window_run", None)):
            return "another run holds Kirk's window on %s (its turns come first) - the wait is unknown, not judged " \
                   "inside the line" % host
        if _cw.window_in_use(host, model):
            return ""
    except Exception:                                                # noqa: BLE001
        pass
    ahead = line_ahead(host, lines)
    if ahead is None:
        return "the desk's line for %s could not be read - not judged inside the line" % host
    if not ahead:
        return ""
    return "%d order(s) ahead on %s (%s) - the wait is unknown, not judged inside the line" % (
        len(ahead), host, ", ".join(a[:40] for a in ahead[:2]))


def _displacement_cost(host, model, solve):
    """(seconds, notes, unknown): what placing `model` on `host` costs the work already on that card.
      - a window holding ANOTHER model loses its cache: its model's measured reload plus the re-read of the conversation it
        held, "a SPEED cost to its next turn... charged like any residency cost" (§ 4b);
      - that window in use by another run cannot give up the card now: the wait is the holder's remaining time, which Kirk
        does not know, so the row is not judged inside the line;
      - a window holding THIS model in use by another run is joined, which parks that run: it waits this turn's own time.
    A part nobody measured is said, never charged as 0 or guessed."""
    try:
        import chat_window as _cw
        import ollama_client as _oc
        held = _cw.card_windows(host, exclude_run=getattr(_oc._CALL_CONTEXT, "window_run", None))
    except Exception:                                                # noqa: BLE001 - Tolkien's copy has no windows
        return 0.0, [], False
    cost, notes_, unknown = 0.0, [], False
    for w in held:
        if w["model"] == model:
            if w["in_use"]:
                cost += float(solve or 0)
                notes_.append("joins the window another run is using: that run waits this turn (+%.0f s); its pause and "
                              "resume are unmeasured" % float(solve or 0))
            continue
        if w["in_use"]:
            unknown = True
            notes_.append("the card's %s window serves another run: its remaining time is unknown" % w["model"])
        p = _row_prices(host, w["model"])
        if p["load_s"] is None:
            notes_.append("displaces %s: its reload unmeasured" % w["model"])
        else:
            cost += float(p["load_s"])
            notes_.append("displaces %s: +%.0f s to reload it" % (w["model"], float(p["load_s"])))
        if w.get("context_tokens"):
            if p["read_tok_s"]:
                reread = float(w["context_tokens"]) / float(p["read_tok_s"])
                cost += reread
                notes_.append("+%.0f s to re-read its %d tokens" % (reread, int(w["context_tokens"])))
            else:
                notes_.append("its re-read unmeasured")
    return cost, notes_, unknown


def weigh_rows(family, tier, interactive=True, context_tokens=0, window_cost=False, exclude=(), deep=False, mode=None,
               conv=None, cost_first=False):
    """The weighing over the table's rows for one request -> (band, rejected): each candidate carries its three numbers
    and the notes that explain its place - `host` (the desk host: a Fellowship worker ranks as the Fellowship), `box`,
    `entry` (the table row: model, think, temperature, caveats), `q_low`, `quality`, `time_s` (solve + what a cold model
    pays now: residency_cost asks the box), `first_s` (before the first token: the cold part), `j`, `over` (past the
    line), `role`. `band` is ordered best first; `rejected` are the rows below the quality need, with why.
    `window_cost` (a chat turn: it runs in a factory WINDOW): a GPU row whose box has no window for its model also pays
    a new window's MEASURED time to first output, its model's load left out (chat_window.first_output_s - the desk round
    trip, the window's start, the prompt read; the 10-23 overflow turn took 37.9 s on a 12.0 s price). Unmeasured on that
    box is noted and charged nothing until the first new window there measures it.
    `exclude`: hosts the caller already rules out (a pin's complement, a box it just found unreachable) - their rows are
    not weighed and their residency is not asked (a sleeping box costs a connect timeout per ask).
    `deep` (area C step 3): the request is worth its wait - rows past the interactive line stay in contention (the line is
    noted, not applied); quality need, home, energy and time order the band as before.
    `mode` (the user's general mode, mode_verdict / MODES): "quick" - the need is the tier's weakest home row, and the
    rows within QUICK_TIME_SLACK_S of the fastest (on the time the line judges: time to visible on a chat turn, else the
    total) come first, best quality first, then the rest by time; "hard" - the need is the best row's lower bound and
    the turn is deep. A peak or extreme price still weighs energy first (slow and low binds every request).
    `cost_first` (every user but the lab's owner -: inside the quality band, cheaper energy before home (home breaks ties); the line, the floor and a peak
    price's energy-first are unchanged. The owner (config.PRIORITY_USERS) keeps home-first.
    `conv` (a chat turn's "user/chat_id"): the box whose window last served this chat holds its saved state
    (chat_window.conv_home), so a COLD row there pays its load but not the conversation's re-read - the state is
    restored (quick resume step 6). Warm rows keep today's price: their measured first output already includes a
    typical prompt read."""
    rows = policy_rows(family, tier)
    home = None
    if conv and window_cost:
        try:
            import chat_window as _cw
            home = _cw.conv_home(conv)
        except Exception:                                            # noqa: BLE001
            home = None
    if not rows:
        return [], []
    line = _interactive_line(family) if interactive else None
    price = _price_of_the_hour()                    # one price per request; it multiplies every row alike
    cands = []
    loaded = {}                                     # one residency read per box, however many rows it has
    desk = {}
    fel = None                                      # the Fellowship's load, read once per request when a row needs it
    for e in rows:
        box = e.get("box")
        host = desk_host(box)
        if host in exclude or not box_here(box):
            continue
        q_low = float(e.get("quality_low") if e.get("quality_low") is not None else (e.get("quality") or 0))
        quality = float(e.get("quality") or 0)
        q_high = wilson_high(quality, e.get("n"))
        solve = float(e.get("s_per_solve") or 0)
        notes_ = []
        cold, first_unknown = 0.0, False
        if box not in FELLOWSHIP_BOXES:
            if host not in loaded:
                url = (CARDS.get(host) or {}).get("url")
                try:
                    import ollama_client as _oc
                    loaded[host] = _oc.models_resident(url) if url else None
                except Exception:                                    # noqa: BLE001
                    loaded[host] = None
            at_home = bool(home and home[0] == host and home[1] == e.get("model"))
            cold, note = residency_cost(host, e.get("model"), e.get("load_s"), e.get("read_tok_s"),
                                        0 if at_home else context_tokens,
                                        loaded=loaded[host] if loaded[host] is not None else "unknown")
            if note:
                notes_.append(note)
            if note and "load unmeasured" in note:
                # a cold model whose load nobody measured on this box is not a fast one. replay: once every model's rows reached the table, Quick sent
                # 118 of 119 cold turns to a lead model with no measured load, priced as if it were already loaded.
                first_unknown = True
                notes_.append("cold load unmeasured - not judged inside the line")
            if at_home and context_tokens and note and note != "resident":
                notes_.append("this chat's state is saved here: restored, not re-read")
            if window_cost:
                wcost, wnote = _window_open_cost(host, e.get("model"))
                cold += wcost
                if wnote:
                    notes_.append(wnote)
                dcost, dnotes, dunknown = _displacement_cost(host, e.get("model"), solve)
                cold += dcost
                notes_.extend(dnotes)
                first_unknown = first_unknown or dunknown
                if "lines" not in desk:
                    desk["lines"] = desk_lines()        # one read of the desk per weighing
                lnote, lwait = _line_wait(host, e.get("model"), desk["lines"])
                if lwait is not None:
                    cold += lwait                       # a known wait is time like any other (the band orders it)
                    notes_.append(lnote)
                elif lnote:
                    first_unknown = True
                    notes_.append(lnote)
        else:
            # A Fellowship row is a desk ORDER, not a card: before its first token it pays the desk round trip, Tolkien's
            # pickup, the per-order server launch and the prompt read - the table's MEASURED `order_overhead_s`
            # (menu_policy.fellowship_overheads, from delivered hai-chat orders). Unmeasured is noted, never charged as 0.
            oh = e.get("order_overhead_s")
            if box == TOLKIEN_BOX and window_cost:
                try:
                    import chat_window as _cw
                    if _cw.has_window(_cw.TOLKIEN_BOX, e.get("model")):
                        oh = 0.0                # The Tolkien box's chat window is open: the turn joins it, no desk order (10-25)
                        notes_.append("%s window open" % TOLKIEN_BOX)
                except Exception:                                    # noqa: BLE001
                    pass
            if oh is None:                      # could not tell is never "inside the line": charged nothing,
                first_unknown = True            # but not judged interactive either
                notes_.append("desk-order overhead unmeasured - not judged inside the line")
            else:
                cold = float(oh)
                notes_.append("+%.0f s desk order (round trip, launch, prompt read)" % cold)
            # A BUSY worker is a wait, priced here so the band's own ordering picks the next row - Tolkien reports his free spots, Spock decides.
            if fel is None:
                fel = fellowship_load()
            if box == TOLKIEN_BOX and fel["ahead"]:
                wait = fel["ahead"] * (solve + cold)
                cold += wait
                notes_.append("%s busy: %d ahead of this turn (+%.0f s wait; %s)" % (TOLKIEN_BOX, fel["ahead"], wait, fel["why"]))
            elif box != TOLKIEN_BOX and fel["free_nodes"] == 0:
                first_unknown = True
                notes_.append("no free tablet (%s) - the wait is unknown, not judged inside the line" % fel["why"])
        time_s = solve + cold
        # the other half of speed (checklist B1/B5): the row's MEASURED time to first visible output on a warm window
        # (menu_policy.stamp_first_output from the windows' own turn records) + what this turn pays before it starts;
        # shown and recorded; the line below judges a CHAT turn on it (checklist B2, per-user, is open)
        fo = e.get("first_out_s")
        first_out = (float(fo) + cold) if isinstance(fo, (int, float)) else None
        if first_out is not None:
            notes_.append("first output ~%.1f s" % first_out)
        # The line judges what the user waits on (checklist B1: speed includes the time until the model starts showing
        # output): a CHAT turn (it streams through a window) on its time to first visible output, the total breaking
        # ties below; anything else, and a row whose first output is unmeasured (said), on its total. Time to visible
        # output stays in view for every turn; B2 (per user) is open.
        visible = first_out if window_cost and first_out is not None else None
        judged = visible if visible is not None else time_s
        over = bool(line and (judged > line or first_unknown))
        if line and window_cost and visible is None:
            notes_.append("time to visible unmeasured - the line judges the total")
        if over:
            notes_.append("past the interactive line (%.1f s, on %s)" % (line, "time to visible" if visible is not None
                                                                        else "the total"))
        if e.get("role", "home") == "overflow":
            notes_.append("overflow")
        j = e.get("j_per_solve")
        if j is None:
            notes_.append("J unmetered")
        j_order = e.get("j_order") if box in FELLOWSHIP_BOXES else None
        if j is not None and isinstance(j_order, (int, float)):
            # the per-ORDER energy of a Fellowship row (menu_policy.stamp_fellowship: the desk overhead at the worker's
            # watts) - the row's J is per request, launch included
            j = float(j) + float(j_order)
            notes_.append("+%.0f J per-order launch" % float(j_order))
        # what this turn burns now: a cold card's seconds at the row's own measured draw, so a hot model is cheaper than the
        # same row cold checklist
        # B3: load TIME is never cost, its electricity is). A Fellowship row's launch is already in `j` (j_order).
        j_now = None if j is None else float(j)
        if j is not None and box not in FELLOWSHIP_BOXES and solve > 0 and cold > 0:
            j_now = float(j) * (solve + cold) / solve
            notes_.append("+%.0f J for the cold seconds" % (j_now - float(j)))
        cands.append({"host": host, "box": box, "entry": e, "q_low": q_low, "q_high": q_high, "quality": quality, "time_s": time_s,
                      "first_s": cold, "first_out_s": first_out, "judged_s": judged, "j": None if j is None else float(j), "j_now": j_now,
                      "first_unknown": first_unknown,
                      "j_order": j_order if isinstance(j_order, (int, float)) else None, "over": over,
                      "role": e.get("role", "home"), "notes": notes_,
                      # area C step 2: the row's energy at the hour's price (cents), and which price that was
                      "cents": None if j is None else float(j) / 3.6e6 * price["usd_per_kwh"] * 100.0,
                      "price": price["usd_per_kwh"], "price_source": price["source"], "price_band": price["band"],
                      "held": False})
    # a WINDOW row (a chat turn) whose model has no measured memory profile on its box is one the desk will refuse to
    # open (model_fit.check, the same lookup) - set it aside BEFORE the quality need, which it must not set, and say why
    #. Only
    # when the table carries model_fits.
    unfit = []
    # a CARD row whose box states its capabilities without the row's model (declares_model, the order's own rule): the
    # box does not have it, so the order would be refused - set aside before the quality need, which it must not set.
    # A box that has stated nothing is left to place()'s own rule (cannot-tell is never free). A row copied from a
    # reference box (config.apply_reference) is the usual case: its model is measured there, not installed here.
    caps_of = {}
    for c in cands:
        if c["box"] in FELLOWSHIP_BOXES:
            continue
        if c["host"] not in caps_of:
            caps_of[c["host"]] = factory_capabilities(c["host"])
        m = (c["entry"] or {}).get("model")
        if caps_of[c["host"]] and not declares_model(c["host"], m, caps_of[c["host"]]):
            c["notes"].append("%s does not declare model.%s - the order would be refused" % (c["host"], m))
            unfit.append(c)
    cands = [c for c in cands if c not in unfit]
    fits = (policy_data() or {}).get("model_fits") if window_cost else None
    if fits and fits.get("hosts"):
        import model_fit as _mf
        for c in cands:
            m = (c["entry"] or {}).get("model")
            # a declared split is servable, and so is a model served through Strata: the desk's own rule
            # (model_fit.requirement: "Explicit offload and Strata have different memory semantics") never fit-checks it,
            # so Flash-Next's chat rows were set aside here while every pinned Flash-Next job ran
            if c["box"] not in FELLOWSHIP_BOXES and not _mf.has_profile(fits, c["box"], m) \
                    and not offload_ok(c["box"], m) \
                    and not config.swap_base((CARDS.get(c["box"]) or {}).get("url"), m):
                c["notes"].append("no measured memory profile on %s - the desk would refuse its window" % c["box"])
                unfit.append(c)
        cands = [c for c in cands if c not in unfit]
    if not cands:
        return [], unfit
    home = [c for c in cands if c["role"] == "home"]
    card_home = [c for c in home if c["box"] not in FELLOWSHIP_BOXES]
    if mode == "quick" and card_home:
        # a quality this tier's table already accepts as a home ON A CARD
        need = min(c["q_low"] for c in card_home)
    elif mode == "hard":        # not measurably worse than the best row, whatever its role (band filter below)
        need, deep = max(c["q_low"] for c in cands), True
    else:
        need = max(c["q_low"] for c in home) if home else max(c["q_low"] for c in cands)
    # Hard: a row is in when its 95 % range OVERLAPS the best row's - its upper bound reaches the best's lower bound. Every other mode: point quality at or above the need, as before.
    band = [c for c in cands if (c["q_high"] if mode == "hard" else c["quality"]) >= need]
    rejected = [c for c in cands if c not in band]
    for c in rejected:
        c["notes"].append("below the quality band (need %.2f)" % need)
    rejected += unfit
    if deep:
        for c in band:
            if c["over"]:
                c["notes"].append("deep: past the line stays in contention")
    if price["band"] in ("peak", "extreme"):
        # SLOW AND LOW: inside the quality band, the
        # cheapest energy first - before the line, before home; time last.
        for c in band:
            c["notes"].append("%s price $%.3f/kWh (%s): energy weighed first"
                              % (price["band"], price["usd_per_kwh"], price["source"]))
        band.sort(key=lambda c: (c["j"] is None, c["j"] if c["j"] is not None else 0.0, False if deep else c["over"],
                                 c["role"] != "home", c["time_s"]))
    elif mode == "quick" and band:
        # among the rows about as fast as the fastest, the ones statistically as good as the best of them (point quality
        # at or above its 95 % lower bound) are equals and the charter decides them - home, then the cheapest energy (the
        # Fellowship keeps the work it was priced for); a clearly better fast row still beats a weaker one
        # a row whose time to first output nobody measured is never "fast" (it would set the pace on a guess)
        known = [c["judged_s"] for c in band if not c.get("first_unknown")]
        fastest = min(known) if known else min(c["judged_s"] for c in band)
        slack = float(config.QUICK_TIME_SLACK_S)
        for c in band:
            c["fast"] = c["judged_s"] <= fastest + slack and (not c.get("first_unknown") or not known)
        best_low = max(c["q_low"] for c in band if c["fast"])
        for c in band:
            c["top"] = c["fast"] and c["quality"] >= best_low
            c["notes"].append("quick: %s (%.1f s vs fastest %.1f s, slack %.0f s)"
                              % ("fast, as good as the best fast row - home and energy decide" if c["top"]
                                 else "fast - quality decides" if c["fast"] else "slower - time decides",
                                 c["judged_s"], fastest, slack))

        def _quick_key(c):
            energy = (c["j_now"] is None, c["j_now"] if c["j_now"] is not None else 0.0)
            tail = ((energy + (c["role"] != "home",)) if cost_first else ((c["role"] != "home",) + energy)) \
                + (c["judged_s"],)
            if c["top"]:
                return (0, 0, 0.0) + tail
            if c["fast"]:
                return (0, 1, -c["quality"]) + tail
            return (1, 0, c["judged_s"]) + tail
        band.sort(key=_quick_key)
    elif mode == "hard":
        # weighted hard toward quality: the best point
        # quality first; home, energy and time only break ties
        for c in band:
            c["notes"].append("hard: quality weighed first")
        band.sort(key=lambda c: (-c["quality"], c["role"] != "home", c["j_now"] is None,
                                 c["j_now"] if c["j_now"] is not None else 0.0, c["time_s"]))
    elif cost_first:
        # every user but the owner: inside the band and the line, the cheapest energy first, home as the tie-break
        band.sort(key=lambda c: (False if deep else c["over"], c["j_now"] is None, c["j_now"] if c["j_now"] is not None else 0.0,
                                 c["role"] != "home", c["time_s"]))
    else:
        band.sort(key=lambda c: (False if deep else c["over"], c["role"] != "home", c["j_now"] is None,
                                 c["j_now"] if c["j_now"] is not None else 0.0, c["time_s"]))
    if price["band"] == "extreme":
        # an EXTREME price holds Auto work with a notice; the caller tells the user and serves nothing (a pin never
        # reaches the weighing, so his override and every user's pin still answer - D3)
        hold = _hold_notice(price)
        for c in band:
            c["held"], c["hold"] = True, hold
    if window_cost:
        band = chat_last_resort_last(band)
    for c in band:
        c["need"] = need
    return band, rejected


def _weigh_why(family, tier, interactive, c, n):
    return "weighing %s/%s/%s #%d: %s%s %s, %.0f%% (low %.2f) at %.1f s, %s%s%s" % (
        family, tier, "interactive" if interactive else "background", n,
        "" if c["box"] in (None, c["host"]) else c["box"] + " ", c["entry"].get("model"), c["entry"].get("think"),
        100 * c["quality"], c["q_low"], c["time_s"], ("%.0f J" % c["j"]) if c["j"] is not None else "J unmetered",
        (" = %.4f cents at $%.2f/kWh (%s)" % (c["cents"], c["price"], c["price_source"]))
        if c.get("cents") is not None else "",
        ("; " + "; ".join(c["notes"])) if c["notes"] else "")


def _apply_weighing(ranked, family, tier, interactive, context_tokens=0, weighed=None, deep=False):
    """Re-order the hosts the hard rules already PASSED by the weighing (never adds, never removes a host: eligibility
    was decided before this). Each host takes its best band row; a host whose rows all fell below the quality band
    keeps its place after the band hosts and says so. -> (ranked, applied?, record) where record lists every table
    row with its numbers and whether its host was eligible - the review surface (design § 1 f). `weighed` = a
    (band, rejected) the caller already computed (place_weighed), so the boxes are asked once."""
    band, rejected = weighed if weighed is not None else weigh_rows(family, tier, interactive, context_tokens, deep=deep)
    if not band and not rejected:
        return ranked, False, None
    eligible = {h for h, _s, _w in ranked}
    best = {}
    for i, c in enumerate(band):
        best.setdefault(c["host"], (i, c))
    out = []
    for i, (host, score, why) in enumerate(ranked):
        if host in best:
            n, c = best[host]
            key = (0, n)
            why = "; ".join(w for w in (why, _weigh_why(family, tier, interactive, c, n + 1)) if w)
        else:
            rej = [c for c in rejected if c["host"] == host]
            key = (1, len(band) + i)
            if rej:
                why = "; ".join(w for w in (why, "weighing %s/%s: %s" % (family, tier, "; ".join(rej[0]["notes"]))) if w)
        out.append((key, (host, score, why)))
    record = [{"host": c["host"], "box": c["box"], "model": c["entry"].get("model"), "think": c["entry"].get("think"),
               "role": c["role"], "quality": c["quality"], "q_low": c["q_low"], "time_s": round(c["time_s"], 2),
               "first_s": round(c["first_s"], 2), "first_out_s": c.get("first_out_s"), "j": c["j"],
               "j_order": c.get("j_order"), "over_line": c["over"], "band": c in band,
               "eligible": c["host"] in eligible, "notes": c["notes"],
               "cents": c.get("cents"), "usd_per_kwh": c.get("price"), "price_source": c.get("price_source"),
               "price_band": c.get("price_band"), "held": bool(c.get("held"))} for c in band + rejected]
    return [r for _k, r in sorted(out, key=lambda kv: kv[0])], True, record


def place_weighed(kind, family, tier, interactive=True, context_tokens=0, **kw):
    """place() by the weighing, computed ONCE -> (ranked, band): `ranked` as place() returns it; `band` the weighing's
    candidates best first, each with `host`, `entry` (the table row: model, think, temperature) and its numbers, plus
    `eligible` (its host passed the hard rules now). A caller that needs the SETTING walks the band for the first
    eligible host it can serve - the row that chose the box chooses its setting (design § 3). `deep` / `prio` (area C
    step 3): the request's own needs - deep keeps rows past the line in contention; both go into the placement record."""
    deep, prio, mode = bool(kw.pop("deep", False)), kw.pop("prio", None), kw.pop("mode", None)
    conv, cost_first = kw.pop("conv", None), bool(kw.pop("cost_first", False))
    band, rejected = weigh_rows(family, tier, interactive, context_tokens, window_cost=(kind == "chat"),
                                exclude=tuple(kw.get("exclude") or ()), deep=deep, mode=mode, conv=conv,
                                cost_first=cost_first)
    needs = {"deep": deep or mode == "hard", "prio": prio}
    if mode:
        needs["mode"] = mode                    # the user's general mode, weighed (mode_verdict) - the review surface
    ranked = place(kind, family=family, tier=tier, urgency="interactive" if interactive else "background",
                   context_tokens=context_tokens, _weighed=(band, rejected), needs=needs, **kw)
    eligible = {h for h, _s, _w in ranked}
    # THE MATRIX CONSIDERS EVERY ROW: the quality need RANKS, it never removes. Rows below it follow the
    # band in the walk, best quality first (then time, then energy) - only a hard constraint (no measured fit, a box that
    # cannot take it, a pin) takes a row out. Marked `below_need` for the record.
    below = [c for c in rejected if "no measured memory profile" not in " ".join(c.get("notes") or [])]
    below.sort(key=lambda c: (-c["quality"], c.get("judged_s", c["time_s"]), c["j"] is None,
                              c["j"] if c["j"] is not None else 0.0))
    hold = next((c.get("hold") for c in band if c.get("held")), None)
    for c in below:
        c["below_need"] = True
        if c.get("price_band") == "extreme":           # an extreme price holds every row alike, the walk's too
            c["held"], c["hold"] = True, hold or _hold_notice({"usd_per_kwh": c.get("price") or 0.0,
                                                                  "source": c.get("price_source") or "?"})
    walk = band + below
    for c in walk:
        c["eligible"] = c["host"] in eligible
    return ranked, walk


# The embed family (menu_policy.EMBED_FAMILY / EMBED_TIER: one priced row per GPU box, the same model everywhere). HAI's
# per-turn embeds - library search, tool selection - went to a FIXED host list (config.EMBED_HOSTS) while every other
# model call was placed from the table; that list, not placement, dropped them on the failover box and evicted McCoy all day
EMBED_FAMILY, EMBED_TIER = "embed", "gpu"


def embed_placement(exclude=()):
    """Where HAI's next embed call goes, by the menu: -> [(url, model, candidate)] best first over the boxes that passed
    place()'s rules, in the embed family's weighing (one quality band - one model everywhere - so inside the line the
    cheapest energy first, then time; a cold box pays its measured load). None when the table has no embed family yet
    (the caller keeps its fixed list and says so once); [] when the family exists but no box passed the rules right now.
    A card LIVE for another model is still the caller's check (occupied_for), as for every Kirk-side call by URL.
    `exclude`: hosts the caller found unreachable - not weighed, not asked."""
    if not policy_rows(EMBED_FAMILY, EMBED_TIER):
        return None
    ranked, band = place_weighed("embed", EMBED_FAMILY, EMBED_TIER, interactive=True, exclude=tuple(exclude or ()))
    by_host = {}
    for c in band:
        by_host.setdefault(c["host"], c)
    out = []
    for host, _s, _w in ranked:
        c, url = by_host.get(host), (CARDS.get(host) or {}).get("url")
        if c and url:
            out.append((url, c["entry"].get("model") or config.EMBED_MODEL, c))
    return out


# The Fellowship is ONE desk destination (Tolkien's factory, desk host `fellowship`). The placement table prices its
# workers as two boxes - the Tolkien box and `fellowship` (the tablet pool) - so the table can rank the destination, while the
# choice BETWEEN them is Tolkien's, read from the same rows.
FELLOWSHIP_HOST = "fellowship"
FELLOWSHIP_BOXES = tuple(b for b in (TOLKIEN_BOX, "fellowship") if b)


def _fellowship_capacity(path=None):
    """Tolkien's free-spot report from his run-state push ({"nodes", "free_nodes", "rigby_busy", "rigby_line"}) while it
    is fresh within its own ttl, else None. One reading for the weighing (fellowship_load) and load accounting."""
    try:                                 # the raw record: _run_records drops nameless (idle) ones, and idle says "free"
        with open(path or STATED_RUN, encoding="utf-8") as f:
            rec = (json.load(f) or {}).get(FELLOWSHIP_HOST) or {}
    except Exception:                                                # noqa: BLE001
        return None, None
    rec = rec if isinstance(rec, dict) else {}
    cap = rec.get("capacity") if isinstance(rec.get("capacity"), dict) else None
    age = _report_age_s(rec) if rec else None
    fresh = cap is not None and age is not None and age <= max(60, int(rec.get("ttl") or 180))
    return (cap if fresh else None), rec


FELLOWSHIP_PENDING = [None]      # set by fellowship_turn: -> how many HAI turns Kirk has on the Fellowship, unanswered


def fellowship_load(path=None):
    """How loaded the Fellowship is for the weighing: {"ahead", "free_nodes", "tolkien_box_free", "why"}. `ahead` =
    turns ahead of a new one on Tolkien's box: the larger of what TOLKIEN reports (its box running + its line - his
    free-spot report in the run-state push, fresh within its own ttl) and what KIRK knows (his Fellowship turns sent and
    not answered yet - the burst Tolkien has not collected). `free_nodes` = untaken tablets, None when not reported
    (never a made-up "all free"). `tolkien_box_free` = Tolkien's fresh report says his box holds no work (False when
    there is no fresh report: "could not tell" is never "free")."""
    tol_ahead, free, why, box_free = 0, None, [], False
    cap, rec = _fellowship_capacity(path)
    cap_stale = cap is None and isinstance(rec, dict) and isinstance(rec.get("capacity"), dict)
    if cap is not None:
        tol_ahead = int(bool(cap.get("rigby_busy"))) + int(cap.get("rigby_line") or 0)
        free = cap.get("free_nodes") if isinstance(cap.get("free_nodes"), int) else None
        box_free = not cap.get("rigby_busy")
        why.append("Tolkien: %s %s, %d in line, %s free tablets" % (
            TOLKIEN_BOX or "his box", "busy" if cap.get("rigby_busy") else "free", int(cap.get("rigby_line") or 0),
            free if free is not None else "?"))
    else:
        why.append("Tolkien's free-spot report %s" % ("stale" if cap_stale else "not reported"))
    kirk = 0
    try:
        kirk = int(FELLOWSHIP_PENDING[0]()) if FELLOWSHIP_PENDING[0] else 0
    except Exception:                                                # noqa: BLE001
        kirk = 0
    if kirk:
        why.append("Kirk: %d Fellowship turn%s unanswered" % (kirk, "" if kirk == 1 else "s"))
    return {"ahead": max(tol_ahead, kirk), "free_nodes": free, "tolkien_box_free": box_free, "why": "; ".join(why)}


def desk_host(box):
    """The desk host a placement-table box is reached through: a Fellowship worker -> the Fellowship; else itself."""
    return FELLOWSHIP_HOST if box in FELLOWSHIP_BOXES else box


def fellowship_role(family, tier, lens):
    """'home' / 'overflow' for the table's best Fellowship row for this key, None when the table lists none."""
    for box, e in policy_order(family, tier, lens):
        if box in FELLOWSHIP_BOXES:
            return e.get("role", "home")
    return None


def place(kind, vram_gb=None, cpu_heavy=None, interactive=None, exclude=(), only=(),
          avoid_load_bearing=False, needs_container=False, family=None, tier=None, lens=None, fellowship=False,
          context_tokens=0, lend=False, urgency=None, _weighed=None, needs=None):
    """Rank hosts for this job. Returns [(host, score, why)], best first; [] when nothing fits. `context_tokens`: the
    conversation a request brings - on the fastest lens a cold box also pays its re-read (residency_cost).

    `urgency` ("interactive" | "background", with `family` + `tier`): the per-request WEIGHING orders the eligible hosts
    instead of one lens - quality need, then the interactive line, home, energy, time - and
    the placement record carries every row's three numbers. Without it the lens path below is unchanged.

    `lend=True` (McCoy's on-demand bump): a card held only by Kirk's OWN idle chat window may be borrowed - the window
    saves its conversation and restores it before its next turn (chat_window.lend). Its live claim is that saved
    conversation, so it does not refuse the borrow; every free card still outranks a borrowed one.

    `only` / `exclude` carry a USER-SELECTED exact pick and are applied as hard constraints -- force_27b
    restricts to hosts that fit the 27B (Quick is a weight, not an exclusion: policy 10). Never silently relaxed: a
    caller that gets [] must TELL the user, not quietly downgrade (policy 10).

    `family` + `tier` (+ `lens`, default fastest when interactive, else least_energy): the #34 placement table orders
    the hosts that passed every rule above - a pin still wins because it filtered first. No family, or no table entry,
    = the ranking below unchanged.

    `fellowship=True` (an order that also brings a Fellowship form, place_order): the Fellowship joins the candidates
    as ONE destination and the table ranks it by its best worker row like any box (design § D). It needs a policy - with
    no family/tier there is nothing to rank it by, and it stays the non-card candidate place_order adds last."""
    policy_tier = tier                       # the loop below reuses `tier` for chat's host order
    spec = dict(KINDS.get(kind) or KINDS["cpu"])
    if vram_gb is not None:
        spec["vram_gb"] = float(vram_gb)
    if cpu_heavy is not None:
        spec["cpu_heavy"] = bool(cpu_heavy)
    if interactive is not None:
        spec["interactive"] = bool(interactive)

    ranked = []
    desk_seen = {}                                  # one read of the desk's lines per placement, and only for a chat kind
    row_models = {}                                 # host -> the models of this weighing's rows there
    for c in [c for part in (_weighed or ()) for c in (part or ())]:
        m = (c.get("entry") or {}).get("model")
        if m:
            row_models.setdefault(c.get("host"), set()).add(m)
    for host, card in CARDS.items():
        if only and host not in only:
            continue
        if host in exclude:
            continue
        why = []

        needs_gpu = spec["vram_gb"] > 0
        if needs_gpu:
            if card["vram_gb"] < spec["vram_gb"]:
                continue                                     # physically cannot hold it
            healthy, hwhy = gpu_health(host)
            if not healthy:
                continue          # a lost card SUCCEEDS slowly on CPU; never place real work on it
            # the reader's next read joins the window its last one opened; place_reader's occupied_for refuses another model's in-use window
            joins_own = kind in ("chat", "chat27b") or spec.get("joins_own_window")
            own = own_window(host) if joins_own else None
            if joins_own:
                if "lines" not in desk_seen:
                    desk_seen["lines"] = desk_lines()
                if turn_line(host, desk_seen["lines"]):
                    continue      # an order runs there or is about to: the turn would wait behind it (checklist D4c)
            lent = lendable_window(host) if (lend and not own) else None
            if own:
                # Kirk's own chat window holds this card: its claim and run-state are chat's own turns, which join it
                gwhy = "Kirk's own chat window (%s)" % own
            elif lent:
                gwhy = "borrows Kirk's idle chat window (%s): its conversation is saved and restored before its next turn" % lent
            else:
                busy, gwhy = gpu_state(host)
                if busy:
                    continue                                     # Training runs have exclusive ownership. Checked separately from gpu_state/
# claims because legacy foundry jobs never called claim(), risking
# silent corruption of long-running training if treated as free.
                t_busy, twhy = training_busy(host)
                if t_busy and not (kind == "page" and page_on_lane(host)):
                    continue
            if spec.get("yields_to_live") and active_claim(host, LIVE) and not lent and not (own and spec.get("joins_own_window")):
                continue                                     # housekeeping never displaces a live HAI job (B12)
            if host == FAILOVER and spec.get("coexists_with_plex"):
                why.append("coexists with Plex (measured beside a live transcode)")
            elif host == FAILOVER:
                # policy 4: The failover box's GPU is Plex's first.
                ok, pwhy = _plex_gate()
                if not ok:
                    continue
                why.append("plex idle (%s)" % pwhy)
            why.append(gwhy)
        elif card.get("gpu") is None:
            why.append("cpu-only box, no gpu to waste")

        if spec["cpu_heavy"] and not card.get("cpu_heavy_ok", True):
            continue                                          # The brain box: 6 cores, keep it clear

        ram = ram_free_gb(host)
        need_ram = max(2.0, spec["vram_gb"] * 0.5)            # models spill; leave room (policy: RAM kills)
# A chat kind whose model is ALREADY resident on this host has nothing left to spill: the
# weights are memory-mapped, which is exactly what makes free RAM look low. Residency
# is still only a cache for every OTHER decision; here it is the reason the spill gate is moot.
# The same holds for EVERY kind whose model the lab names.
        resident_same = False
        want = _resident_model_for(kind)
        if needs_gpu and want and card.get("url"):
            try:
                import ollama_client as _oc
                resident_same = any(str(m).split(":")[0] == want for m in (_oc.models_loaded(card["url"]) or []))
            except Exception:
                resident_same = False
        if needs_gpu and not resident_same and row_models.get(host) and card.get("url"):
            # the row that chose the box chooses its model: a weighed row whose model is already loaded here has nothing
            # left to spill either (the brain box's 35B, a declared split, leaves ~4 GB free while it is resident)
            try:
                import ollama_client as _oc
                resident_same = any(m in row_models[host] for m in (_oc.models_loaded(card["url"]) or []))
            except Exception:
                resident_same = False
        if resident_same:
            why.append("model already resident - spill gate skipped")
        elif ram is not None and ram - RAM_RESERVE_GB.get(host, 3.0) < need_ram:
            continue
        cpu = cpu_load_pct(host)
        if cpu is not None and spec["cpu_heavy"] and cpu > CPU_BUSY_PCT.get(host, 60.0):
            continue

        # --- scoring: prefer the SMALLEST card that comfortably fits ---------------------------------
        # The whole point of "The lead is the biggest hitter" is that it should be doing work only
        # it can do. An 8 GB job squatting on the 24 GB card is the waste this scheduler exists to
        # stop, so oversized cards are penalised for small jobs -- not excluded, just ranked lower.
        score = 100.0
        if needs_gpu:
            headroom = card["vram_gb"] - spec["vram_gb"]
            score -= headroom * 2.0
        if spec.get("home") == host:
            score += 500.0                                    # the kind's home box wins whenever it can take the job
            why.append("home")
        if needs_gpu and lent:
            score -= 1000.0                                   # a borrowed card costs its window a reload: free ones first
        # Chat routing uses hard tiers (the brain box < the lead < the failover box) to dominate
        # generic headroom scoring. This prevents the Plex/VM host (the failover box) from being
        # preferred over failover targets, which happened when penalties competed directly.
        # Do not adjust weights without revisiting this priority.
        is_chat = kind in ("chat", "chat27b")
        if is_chat:
            tier = CHAT_TIER.get(host, 3)
            score += (3 - tier) * 1000.0
            why.append(["chat default", "the failover", "last resort (hosts the lab's services)",
                        "not a chat host"][tier])
        if card.get("hosts_services") and spec["cpu_heavy"]:
            score -= 10.0                                     # Prefer BRAIN for non-interactive GPU work <=8GB.
# These jobs claim the card, relocating the chat brain.
# Do not use for short-lived embeds (see avoid_load_bearing).
        if (needs_gpu and not is_chat and not spec["interactive"]
                and spec["vram_gb"] <= 8.0 and host == BRAIN):
            score += 40.0
            why.append("small-model box (chat relocates if this claims the card)")
        if spec["cpu_heavy"]:
            score += card["cpu"] * 1.5                        # cores matter for cpu work
            if ram is not None:
                score += min(ram, 32.0) * 0.5                 # do not stack a second model on the card already committed to chat
        if avoid_load_bearing and card.get("hosts_services"):
            score -= 40.0                                     # policy 5: transients float off load-bearing hosts
            why.append("load-bearing host (services); prefer transients elsewhere")
        # policy: heavy CPU work on a training box measurably slows the run -- penalty, not a ban.
        if spec["cpu_heavy"] and host == LEAD_BOX:
            mbusy, _ = gpu_state(LEAD_BOX)
            if mbusy:
                score -= 35.0
                why.append("gpu training here; cpu contention slows it")
        # A guest VM competes with its own host for the same cores and RAM. Only prefer it when the
        # job actually needs what the guest uniquely provides; otherwise place on the host and skip
        # the hypervisor tax.
        parent = card.get("parent")
        if parent:
            if needs_container and card.get("container_env"):
                score += 60.0
                why.append("job needs the container environment")
            else:
                score -= 60.0
                why.append("guest VM on %s - prefer the host" % CARDS[parent]["label"])
        elif needs_container and not card.get("container_env"):
            continue                      # job requires the container env; this host cannot give it

        # Warm-start tiebreak ONLY: worth a nudge, never enough to overturn a policy tier or a
        # capability judgement. Cold start is cheap; correctness of placement is not.
        if needs_gpu and card.get("url"):
            try:
                import ollama_client as _oc
                _m, _ = _oc.model_state(card["url"])
                if _m and _m not in ("—", "-", ""):
                    score += 3.0
                    why.append("warm (%s resident)" % _m)
            except Exception:
                pass

        ranked.append((host, round(score, 1), "; ".join(w for w in why if w)))

    # Tablet fleet: independent CPU work only. No GPU, no interactive latency,
    # <1.5 GB RAM per process, no large models. Scored lower than x86 (used only
    # when real servers are busy). Relies on standard exclude/only and chunk() weighting.
    if (spec["cpu_heavy"] and spec["vram_gb"] <= 0 and not spec["interactive"]
            and not needs_container):    # no docker, no arrnet, no orchestrator libs on a tablet
        for host, tab in TABLETS.items():
            if only and host not in only:
                continue
            if host in exclude:
                continue
            if not tab.get("direct_cpu_eligible", True):
                continue                         # capability mismatch, not separate membership
            if not tablet_alive(host):
                continue                                  # fail-closed: unreachable = not offered
            score = 40.0 + tab["gflops"]                  # gandalf 54.9 ... pippin 49; x86 base is 100
            why = "tablet crunch node (%d cores, %.1f GFLOP/s%s)" % (
                tab["cpu"], tab["gflops"],
                ", PROVISIONAL" if tab.get("provisional") else "")
            ranked.append((host, round(score, 1), why))

    ranked.sort(key=lambda r: -r[1])
    applied = False
    if (fellowship and family and policy_tier and FELLOWSHIP_HOST not in exclude
            and (not only or FELLOWSHIP_HOST in only)):
        ranked.append((FELLOWSHIP_HOST, 0.1, "the Fellowship - Tolkien picks his own box or a tablet from the table's rows"))
    weighing = None
    if family and policy_tier and urgency:
        # area C step 1: the per-request weighing (quality need -> line -> home -> energy -> time); the lens path below
        # stays for callers that name a lens (the placed delegate's pieces) until they move over, one caller at a time
        lens = None
        ranked, applied, weighing = _apply_weighing(ranked, family, policy_tier, urgency == "interactive",
                                                    context_tokens, weighed=_weighed,
                                                    deep=bool((needs or {}).get("deep")))
    elif family and policy_tier:
        lens = policy_lens(kind, lens, interactive)
        ranked, applied = _apply_policy(ranked, family, policy_tier, lens, context_tokens)
    policy = None
    if family:
        policy = {"family": family, "tier": policy_tier, "lens": lens, "applied": applied}
        if urgency:
            policy["urgency"] = urgency
            policy["weighing"] = weighing
            if needs:
                policy["needs"] = dict(needs)         # area C step 3: the request's own needs (deep, prio) - the review surface
    record_placement(kind, spec, only=only, exclude=exclude, ranked=ranked, policy=policy)
    return ranked


# Every placement decision with the state it was made on. Append-only JSONL,
# one file per month. Fail-soft and silent after one warning: a log write must never change or break a placement
# (a read-only mount, e.g. Tolkien's /docs, simply records nothing).
PLACEMENT_LOG_DIR = os.path.join(config.STATE_DIR, "placements")
_PLACEMENT_LOG_WARNED = [False]
_PLACEMENT_LOG_LOCK = threading.Lock()


def record_placement(kind, spec, only=(), exclude=(), ranked=(), policy=None):
    try:
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind,
               "vram_gb": spec.get("vram_gb"), "cpu_heavy": spec.get("cpu_heavy"),
               "interactive": spec.get("interactive"), "only": sorted(only or ()), "exclude": sorted(exclude or ()),
               "ranked": [[h, round(float(s), 2), str(w)[:400]] for h, s, w in ranked],
               "chosen": ranked[0][0] if ranked else None}
        if policy:
            rec["policy"] = policy                                  # the table's input and whether it applied
        path = os.path.join(PLACEMENT_LOG_DIR, time.strftime("%Y-%m") + ".jsonl")
        with _PLACEMENT_LOG_LOCK:
            os.makedirs(PLACEMENT_LOG_DIR, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
    except Exception as e:                                         # noqa: BLE001 - never breaks placement
        if not _PLACEMENT_LOG_WARNED[0]:
            _PLACEMENT_LOG_WARNED[0] = True
            print("placement record unavailable (%s) - placements continue unrecorded" % type(e).__name__, flush=True)


def explain(kind, **kw):
    """Human-readable placement decision, for logs and for telling a user WHY."""
    out = place(kind, **kw)
    if not out:
        return "no host can take a %r job right now" % kind
    lines = ["%-11s %6.1f  %s" % (CARDS[h]["label"], s, w) for h, s, w in out]
    return "placement for %r:\n  " % kind + "\n  ".join(lines)


# `place()` ranks by HARDWARE/BUSYNESS but lacks INSTALLED context (e.g., missing ai-toolkit prevents training
# regardless of VRAM). Factories publish DETECTED capabilities via run-state push; this layers "can build?"
# checks atop existing policy rather than replacing it.

def _fleet_records(path=None):
    """Every declared record, INCLUDING nameless ones. Not the same question as `_run_records`.

    `_run_records` filters to records with a `name`, which is right for its job -- `stated_run()`
    must never return a run that is not a run. But a factory announcing itself while IDLE posts
    `name: ""` precisely BECAUSE no run exists, and that filter silently discarded every capability
    record on the way out: the push was correct, the store was correct, and the reader dropped it.
    Two different questions were sharing one reader -- "what run is on this card?" and "what can
    this box build?" -- so they get two readers.
    """
    try:
        with open(path or STATED_RUN, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    if d.get("name"):
        return {d.get("host") or "": d}
    return {k: v for k, v in d.items() if isinstance(v, dict)}


def model_tag(name):
    """A model name as ollama resolves it: a name with no tag after its last `/` is `<name>:latest`
    (`nomic-embed-text` and `nomic-embed-text:latest` are one model; the menu writes the first, a factory declares the
    second)."""
    name = str(name or "")
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def declares_model(host, model, caps=None):
    """Whether `host` states it can serve `model` (`model.<name>` among its capabilities, names compared as ollama
    resolves them: model_tag). The one rule for both places a model meets a box: an order naming the model is refused
    without it (resolve_policy_tokens), and the weighing sets a row without it aside before the quality need
    (weigh_rows)."""
    want = model_tag(model)
    return any(c.startswith("model.") and model_tag(c[len("model."):]) == want
               for c in (factory_capabilities(host) if caps is None else caps))


def box_here(box):
    """Whether a placement-table box exists on this head: a card in its roster, or a Fellowship worker when the head
    has a Tolkien box. A table measured elsewhere (the lab's reference menu) names boxes a head does not have; their
    rows are not weighed, so they set no quality need and win no place."""
    if box in FELLOWSHIP_BOXES:
        return bool(TOLKIEN_BOX)
    return box in CARDS


def factory_capabilities(host, path=None):
    """What `host` last said it can build. Unknown host or no statement -> empty set.

    Empty means NOT STATED, and a caller must treat that as "do not place here" -- cannot-tell has
    never meant free. The orchestrator keeps the last stated set sticky precisely so this is empty
    only when a factory has genuinely never reported."""
    rec = (_fleet_records(path) or {}).get(host) or {}
    return set(rec.get("capabilities") or [])


CTX_CAP = "triage.ctx."


def _ctx_of(cap):
    try:
        return int(str(cap)[len(CTX_CAP):]) if str(cap).startswith(CTX_CAP) else None
    except ValueError:
        return None


def unmet_requires(requires, caps):
    """The `requires` entries `caps` does not satisfy. A `triage.ctx.N` entry is a MINIMUM window: a
    factory declaring `triage.ctx.K` with K >= N meets it. Every other entry must match exactly.
    The factory side (p20_foundry.missing_capabilities) applies the same rule."""
    windows = [w for w in (_ctx_of(c) for c in caps) if w is not None]
    out = []
    for r in requires:
        if r in caps:
            continue
        need = _ctx_of(r)
        if need is not None and any(w >= need for w in windows):
            continue
        out.append(r)
    return out


_USER_CACHE = {}               # host -> (t, state, why): the lead free-VRAM probe is an ssh round trip
USER_CACHE_S = 60.0            # the load reporter pushes once a minute; asking more often learns nothing new


def user_on_card(host):
    """Is a PERSON (or anything not ours) using this card right now? Returns ("user" | "clear" | None, why).

    Reuses the lab's existing detectors - no new thresholds
    to the user, a feature that "should live on all factories"):
      * every box: load accounting - busy with no foundry order, claim or Kirk call for GPU_LOAD_STREAK pushes is
        `unaccounted` (report_gpu_load), i.e. someone else is on the card;
      * lead: the gaming guard (the lead.gaming_busy: low free VRAM with no ollama model resident), which
        also catches a game idling in a menu - asked only when nothing of ours explains the card, because a render
        or trainer of ours holds the same VRAM.
    None = could not tell, which never counts as a user."""
    now = time.time()
    hit = _USER_CACHE.get(host)
    if hit and now - hit[0] < USER_CACHE_S:
        return hit[1], hit[2]
    state, why = None, "no fresh load report"
    rec = gpu_load(host)
    age = _report_age_s(rec) if rec else None
    if rec and age is not None and age <= GPU_LOAD_FRESH_S:
        acc = rec.get("accounting") or {}
        if acc.get("state") == "unaccounted":
            state, why = "user", ("load with no owner: util %s%%, ollama: %s"
                                  % (rec.get("util_pct"), ", ".join(rec.get("ollama_loaded") or []) or "nothing"))
        elif acc.get("state") in ("idle", "accounted", "suspect"):
            state, why = "clear", acc.get("state")
    if state == "user" and host == FAILOVER:
        # Plex is the failover box's known user and has its own rule (plex_transcoding / _plex_gate); counting its load
        # here as well would overrule that rule's threshold.
        _, n, _ = plex_transcoding()
        if n:
            state, why = "clear", "the load is Plex's (%d transcode(s)); the Plex rule decides" % n
    if state != "user" and host == LEAD_BOX and not _accounted_by(host, now):
        try:
            import lead_ssh
            if lead_ssh.gaming_busy():
                state, why = "user", "a game holds the card (low free VRAM, no ollama model resident)"
        except Exception:
            pass
    _USER_CACHE[host] = (now, state, why)
    return state, why


def model_fit_verdict(host, spec, start_sample=None):
    """Same measured arithmetic for desk placement and the factory's last check before loading."""
    need = model_fit.requirement(spec, factory_capabilities(host)) if spec else None
    if need and need.get("serving_host"):
        # Capacity of THIS factory proves nothing about a different --host. Do not load another box's model
        # through this order; that server's own factory must own it.
        from urllib.parse import urlsplit
        try:
            target = urlsplit(need["serving_host"])
            own = urlsplit(CARDS[host]["url"])
            local_names = {own.hostname, host, "localhost", "127.0.0.1", "::1"}
            same = target.hostname in local_names and target.port == own.port and target.scheme == own.scheme
        except (ValueError, KeyError):
            same = False
        if not same:
            return {"ok": False, "why": "model fit: serving endpoint is not this factory's model server"}
    rec = gpu_load(host) if need is not None else None
    age = _report_age_s(rec) if rec else None
    if need is not None and start_sample is not None:
        # The factory just measured these counters at START. Never reuse a minute-old allocation or an older
        # ambient reading when its current local query failed/found no allocation counters.
        mem = start_sample.get("gpu_memory")
        ambient = None
        if isinstance(mem, dict) and isinstance(mem.get("ambient_age_s"), (int, float)) \
                and 0 <= mem["ambient_age_s"] <= GPU_LOAD_FRESH_S:
            ambient = mem.get("ambient_mb")
        rec = dict(rec or {}, gpu_memory=mem, vram_used_mb=ambient)
    return model_fit.check(policy_data().get("model_fits") or {}, host, need, rec,
                           fresh=age is not None and age <= GPU_LOAD_FRESH_S)


def offload_ok(host, model):
    """True when `model` runs partially resident ON PURPOSE on `host` - the menu's own list (policy.json `offload_ok`,
    compiled from menu_fill.OFFLOAD_OK; for the brain box's split A3B). ONE lookup for
    the weighing (such a row is servable without a fully-resident profile) and the window door (its window declares
    --offload-ok, which the desk's fit check and the factory's residency check both honour, as for the menu cells)."""
    return [host, model] in ((policy_data() or {}).get("offload_ok") or [])


def _split_ram_ctx(host, model, ctx_max):
    """A DECLARED split (offload_ok): ollama places its card part itself, so the card is not the limit - the RAM side
    is. -> (ctx_max, why) when the measured RAM side (policy.json `offload_ram`, from the deliveries that ran it split)
    fits in the box's free RAM above its reserve, or the model is already loaded (its RAM side is already in use);
    (None, why) when it does not fit OR cannot be checked (free RAM unknown, footprint unmeasured) - a load is never
    decided on a guess."""
    foot = (((policy_data() or {}).get("offload_ram") or {}).get(host) or {}).get(model) or {}
    need = foot.get("host_mib")
    if not isinstance(need, (int, float)):
        return None, "declared split %s on %s: its RAM side is unmeasured" % (model, host)
    rec = gpu_load(host) or {}
    if model in (rec.get("ollama_loaded") or []):
        return ctx_max, "declared split, already loaded (its RAM side is in use)"
    free = ram_free_gb(host)
    if free is None:
        return None, "declared split %s on %s: free RAM unknown - its %d MiB RAM side cannot be checked" % (
            model, host, need)
    free_mib = free * 1e9 / 1048576
    reserve_mib = RAM_RESERVE_GB.get(host, 3.0) * 1024
    if free_mib - reserve_mib < need:
        return None, "declared split %s needs %d MiB of RAM on %s: %.0f free - %.0f reserve" % (
            model, need, host, free_mib, reserve_mib)
    return ctx_max, "declared split fits: %d MiB RAM side, %.0f free - %.0f reserve" % (need, free_mib, reserve_mib)


def window_ctx(host, model, ctx_max):
    """The chat window to open for `model` on `host`: its full measured window when that fits beside what the card holds
    now, else the largest smaller MEASURED point that does (model_fit.fit_ctx - the same arithmetic and the same report
    as model_fit_verdict, so the desk's check at open agrees) -> (num_ctx or None, why). None = no measured point fits
    now (the caller serves the next row). Could-not-measure keeps ctx_max (the desk decides at open, as before)."""
    if offload_ok(host, model):
        return _split_ram_ctx(host, model, ctx_max)
    fits = (policy_data() or {}).get("model_fits") or {}
    if not ((fits.get("hosts") or {}).get(host) or {}).get(model):
        return ctx_max, "no memory profile"
    rec = gpu_load(host)
    age = _report_age_s(rec) if rec else None
    n, v = model_fit.fit_ctx(fits, host, model, ctx_max, rec, fresh=age is not None and age <= GPU_LOAD_FRESH_S)
    return n, v.get("why", "")


def order_host_unavailable(host, kind, order_spec=None, fit_verdict=None):
    """Why `host` cannot take an order of `kind` RIGHT NOW, or "" when it can.

    UNAVAILABLE is not BUSY. A busy factory's own queue holds an order until its lane frees; a lost card
    never frees (only a reboot does), and the failover box is Plex's while Plex transcodes (policy 4). An
    order queued on either runs on CPU or takes resources from Plex, so it is held at the desk instead.
    The Plex rule covers EVERY kind on the failover box, CPU included the card rules cover GPU kinds only.
    Same derivations place uses: gpu_health for the card, plex_transcoding + _plex_gate for Plex."""
    plex_ok = bool((order_spec or {}).get("coexists_with_plex"))
    if host == FAILOVER and not plex_ok:
        active, _, why = plex_transcoding()
        if active:
            return "Plex first: %s" % why
    if (KINDS.get(kind) or KINDS["cpu"]).get("vram_gb", 0) <= 0 or host not in CARDS:
        return ""
    ok, why = gpu_health(host)
    if not ok:
        return why
    rendering, render_why = render_busy(host)
    if rendering:
        return render_why
    if host == FAILOVER and not plex_ok:
        try:
            need = model_fit.requirement(order_spec, factory_capabilities(host)) if order_spec else None
        except Exception:                                            # noqa: BLE001 - unknown model: the gate asks as before
            need = None
        ok, why = _plex_gate((need or {}).get("model"))
        if not ok:
            return "Plex first: %s" % why
    state, why = user_on_card(host)
    if state == "user":
        return "user on the card: %s" % why
    fit = fit_verdict if fit_verdict is not None else model_fit_verdict(host, order_spec)
    if not fit["ok"]:
        return fit["why"]
    return ""


def place_order(requires, kind="caption", parallel=False, items=None,
                chunk_weights=None, chunk_budgets=None, chunk_restrict=None, fellowship_requires=None,
                order_spec=None, **kw):
    """Place ONE order across the fleet. Returns a dict, never raises.

        Args:
            requires: Capabilities required by the order. A factory missing ANY is ineligible.
                Capability check precedes busyness checks to prevent accepting work that
                cannot be executed (avoiding hold states).
            parallel: If False (default), assign to single best host (order-sensitive).
                If True, spread across all eligible hosts.
            items: Work list. When parallel=True, split is returned per host.

        Returns:
            dict with keys: ok, placements ({host: [items] or None}),
            rejected ({host: why}), why ([...]).

        Note:
            An empty `placements` with ok=False indicates a CAPABILITY GAP requiring
            manual build request (not auto-filed).

        fellowship_requires: set when the order also brings a FELLOWSHIP FORM (compose_orders `fellowship=`): the
            Fellowship is then ranked by the placement table like a box (place(fellowship=True)) and judged by the
            form's own requires, since it would build that form, not the template.
    """
    requires = list(requires or [])
    fel_form = fellowship_requires is not None and not parallel
    # An order with family + tier + urgency is placed by the per-request WEIGHING, computed ONCE - by compose_orders when it calls, else here - so the host place()
    # ranks and the model / settings filled into its order come from the same row.
    weighed, weighed_rows = kw.pop("_weighed", None), kw.pop("_weighed_rows", None) or {}
    if weighed is None:
        weighed, weighed_rows = weighed_entries(kw.get("family"), kw.get("tier"), kw.get("urgency"))

    def _need(host):
        return list(fellowship_requires) if fel_form and host == FELLOWSHIP_HOST else requires
    # Hard routing pins bind EVERY eligibility derivation below, not just place()'s ranking --
    # the busy-host fallback and the parallel re-add each enumerate CARDS independently, and an
    # excluded host re-entering through either is a pin that silently did not apply.
    _only = {str(h).strip().lower() for h in (kw.get("only") or ()) if str(h).strip()}
    _excl = {str(h).strip().lower() for h in (kw.get("exclude") or ()) if str(h).strip()}

    def _pinned_out(host):
        return host in _excl or (_only and host not in _only)

    def _unavailable(host):
        if host not in CARDS or not order_spec:
            return order_host_unavailable(host, kind)
        candidate, why_not = resolve_policy_tokens(order_spec, host, kw.get("family"), kw.get("tier"),
                                                    policy_lens(kind, kw.get("lens"), kw.get("interactive")),
                                                    entry=weighed_rows.get(host))
        if why_not:
            return "model fit: " + why_not
        # Unrelated allocation protocols retain the existing two-argument availability contract.
        if model_fit.requirement(candidate, factory_capabilities(host)) is None and not candidate.get("coexists_with_plex"):
            return order_host_unavailable(host, kind)
        return order_host_unavailable(host, kind, candidate)

    ranked = list(place(kind, fellowship=fel_form, _weighed=weighed, **kw))
    # A Fellowship that the table ranked keeps its place; the table's HOME row for it leads (tiers fellowship / the Tolkien box /
    # tablets), as a GPU home box would. Otherwise non-card factories (e.g., tablet queues) are low-priority candidates:
    # below all place()-ranked hosts, subject to standard capability checks.
    fel_home = (fel_form and kw.get("family") and kw.get("tier")
                and fellowship_role(kw["family"], kw["tier"],
                                    policy_lens(kind, kw.get("lens"), kw.get("interactive"))) == "home")
    ranked_hosts = {h for h, _s, _w in ranked}
    for fac in sorted(NON_CARD_FACTORIES):
        if fac not in ranked_hosts and not _pinned_out(fac):
            ranked.append((fac, 0.1, "non-card factory - declared capabilities decide"))
    rejected, eligible, unavailable = {}, [], {}
    for host, score, why in ranked:
        caps = factory_capabilities(host)
        if not caps:
            rejected[host] = "no capabilities stated - cannot-tell is not free"
            continue
        missing = unmet_requires(_need(host), caps)
        if missing:
            rejected[host] = "cannot build: " + ", ".join(missing)
            continue
        gone = _unavailable(host)
        if gone:
            unavailable[host] = gone
            rejected[host] = "unavailable: " + gone
            continue
        eligible.append((host, score, why))
    for host in CARDS:
        if host not in [h for h, _s, _w in ranked] and host not in rejected:
            rejected[host] = "not offered by place(%r) - hardware, busyness or policy" % kind

    if not eligible:
        # Second pass (fallback): if no free capable factory exists, place on a busy
        # one that has all required capabilities. Its internal queue holds the order
        # until the lane frees. This avoids conflating a temporary "busy" state with a
        # permanent capability gap. An UNAVAILABLE host (lost card, Plex using the failover box)
        # is not busy and never gets the order queued on it.
        queued = []
        for host in CARDS:
            if _pinned_out(host):
                continue
            # A WEIGHED order waits on a box whose row is in its quality band, the row that fills its model; a box
            # outside the band would fill the model from its first menu row (a small model on judgment work).
            if weighed is not None and host not in weighed_rows:
                continue
            caps = factory_capabilities(host)
            if caps and requires and not unmet_requires(requires, caps):
                gone = _unavailable(host)
                if gone:
                    unavailable[host] = gone
                    continue
                queued.append((host, 0.0, "capable but busy - queued at the factory (WAIT, "
                                          "not a capability gap)"))
        if queued:
            rejected = {h: w for h, w in rejected.items() if h not in [q[0] for q in queued]}
            eligible = queued

    if not eligible and unavailable:
        rejected.update({h: "unavailable: %s" % w for h, w in unavailable.items()})
        return {"ok": False, "held": True, "placements": {}, "rejected": rejected,
                "why": ["every capable factory is unavailable right now: %s"
                        % "; ".join("%s (%s)" % (h, w) for h, w in sorted(unavailable.items())),
                        "HELD at the desk: placed automatically when a capable factory is available"]}

    if not eligible:
        return {"ok": False, "placements": {}, "rejected": rejected,
                "why": ["no factory can build this order",
                        "this is a CAPABILITY GAP, not a failure: it should become a build request"]}

    if parallel and items is not None:
        # A PARALLEL ITEMS DEAL INCLUDES EVERY CAPABLE HOST, BUSY OR NOT: busy is WAIT at
        # this desk (each factory's serial queue holds its share), and a deal composed
        # against only the momentarily-free hosts both starves the busy box's queue and
        # produces spurious "undealable" refusals when the free host's window or tier
        # scope cannot take every item.
        have = {h for h, _s, _w in eligible}
        for host in CARDS:
            if host in have or _pinned_out(host):
                continue
            caps = factory_capabilities(host)
            if caps and requires and not unmet_requires(requires, caps):
                gone = _unavailable(host)
                if gone:
                    rejected[host] = "unavailable: %s" % gone
                    continue
                eligible.append((host, 0.0, "capable but busy - queued at the factory"))
                rejected.pop(host, None)

    # PLEX HOST LAST -- A DOMINATING TIER, NOT A SCORE: a tradeable score inverts under
    # load (a busy AI box's eaten headroom can rank it below the media host), so the rule
    # must dominate. Stable sort: place()'s ranking still decides WITHIN a tier; non-card
    # factories stay behind every x86 box.
    eligible.sort(key=lambda t: -1 if (fel_home and t[0] == FELLOWSHIP_HOST)
                  else 2 if t[0] in NON_CARD_FACTORIES
                  else ORDER_HOST_DEPRIORITY.get(t[0], 0))
    hosts = [h for h, _s, _w in eligible] if parallel else [eligible[0][0]]
    out = {h: None for h in hosts}
    if items is not None:
        try:
            out = chunk(items, hosts, weights=chunk_weights, budgets=chunk_budgets,
                        restrict=chunk_restrict)
        except ValueError as e:
            return {"ok": False, "placements": {}, "rejected": rejected,
                    "why": ["undealable items: %s" % e]}
    return {"ok": True, "placements": out, "rejected": rejected,
            "why": ["%s (score %.1f) %s" % (h, s, w) for h, s, w in eligible]}


def chunk(items, hosts, weights=None, budgets=None, restrict=None):
    """Deterministic content-hash split, weighted by measured throughput.

    HASH, not round-robin or slicing: the same item always lands on the same host, so a re-run of a
    split places identically and a resumed half is still that half. Slicing a sorted list would
    also hand one box an entire collection, which turns a fleet split into a per-collection bias.

    WEIGHTED because the boxes are not equal: The lead captions at ~2.75 s/img and the brain box at
    ~3.55, so an even split leaves the lead idle for the last ~20% waiting on the slower box.
    """
    import hashlib
    hosts = list(hosts)
    if not hosts:
        return {}
    if len(hosts) == 1 and not budgets:
        # the shortcut must yield to the budget path: a single-host deal that skipped the
        # size check would hand oversized items straight over
        return {hosts[0]: list(items)}
    w = weights or {h: 1.0 / CAPTION_S_PER_IMG.get(h, 3.0) for h in hosts}
    total = float(sum(w.get(h, 0.0) for h in hosts)) or 1.0

    # Distribute items by sorted hash rank, not raw hash value.
    # Raw hash bands cause starvation on re-balancing (e.g., fastest host gets 0 items
    # if its band is empty in the remainder). Sorting by hash and slicing preserves
    # determinism and proportional distribution across hosts.
    #
    # Note: this trades strict "sticky" assignment (item always goes to same host)
    # for better load balancing. Re-splitting a remainder may move items to different
    # hosts than originally staged. This is acceptable because staging is cheap
    # (re-copy from share); if future jobs have expensive per-host local state,
    # implement a `sticky=True` mode instead.
    ranked = sorted(items, key=lambda it: hashlib.sha256(str(it).encode("utf-8")).hexdigest())
    ordered = sorted(hosts)
    if restrict and not budgets:
        # the tier gate rides the budget path's fitting loop; a restrict with no budgets
        # would be silently ignored, which is an unwired capability, not a smaller deal
        budgets = {h: None for h in ordered}
    if budgets:
        # SIZE-AWARE DEAL: an item longer than a host's budget is never dealt
        # there -- "accepted, then errored" is a routing defect wearing a guard costume. Same
        # ranked order (determinism), each item to the most-underfilled FITTING host by weight;
        # an item NO host fits raises a NAMED ValueError the caller turns into a refusal --
        # a silent drop would ship a corpus with a hole in it.
        fill = {h: 0.0 for h in ordered}
        out = {h: [] for h in ordered}
        unfit = 0
        biggest = 0
        for it in ranked:
            size = len(str(it))
            tier = it.get("tier") if isinstance(it, dict) else None
            fits = [h for h in ordered
                    if (budgets.get(h) is None or size <= budgets[h])
                    # tier scope: a host restricted to certain tiers takes only items that
                    # STATE one of them; a tierless item on a restricted host fails closed
                    and ((restrict or {}).get(h) is None or tier in restrict[h])]
            if not fits:
                unfit += 1
                biggest = max(biggest, size)
                continue
            h = min(fits, key=lambda x: (fill[x] / (w.get(x, 0.0) or 1e-9), x))
            out[h].append(it)
            fill[h] += 1.0
        if unfit:
            caps = sorted(b for b in budgets.values() if b)
            raise ValueError("%d item(s) fit NO host's declared window or tier scope "
                             "(largest item %d chars; declared budgets %s; tier scopes %s) - "
                             "route them to a bigger box or split the section"
                             % (unfit, biggest, caps,
                                {h: sorted(s) for h, s in (restrict or {}).items()
                                 if s is not None}))
        return out
    out, i, n = {h: [] for h in hosts}, 0, len(ranked)
    for k, h in enumerate(ordered):
        take = n - i if k == len(ordered) - 1 else int(round(n * w.get(h, 0.0) / total))
        out[h] = ranked[i:i + take]
        i += take
    return out


# Model tags are hardware properties (VRAM fit), not order config.
# The scheduler owns these; if an order specifies a tag, it overrides.
# This is distinct from detected capabilities announced by the factory.
HOST_CAPTION_MODEL = roster_table("caption_model")      # per device: the captioner its VRAM fits (`why` in the roster)

# Concurrency limit per host, constrained by hardware capacity (a lower one keeps a services host's footprint light).
HOST_CAPTION_WORKERS = roster_table("caption_workers")

# Map host to its torch-capable interpreter for GPU-heavy payloads.
# Keyed by HOST (hardware property), not order: submitters don't need
# to know which venv lives where. Explicit python_exe in the template
# still wins (same override rule as caption_model). A device with no
# entry falls back to the factory's default interpreter, which is correct
# for stdlib payloads and fails loudly at import for torch ones.
HOST_COMPUTE_PY = roster_table("compute_py")

# Size-aware deal registry: payload basename -> prompt-reserve tokens (rubric template +
# completion overhead). A NEW parallel items order kind must register here, or the desk
# deals it size-blind (hash+throughput only) and only the payload's own oversize belt
# keeps a too-small box from receiving work it cannot fit. Module-level so the submitting
# tool (queue_compute) and the desk share ONE registry object.
SIZE_RESERVES = {"triage_rewrite": 2600, "triage_classify": 1600,
                 "native_history_review": 2000,
                 # infer_ollama: per-item prompt + the payload's system/suffix framing; PROVISIONAL
                 # until per-row rates are recorded from the first fleet rungs (hard bench S6/S7).
                 "infer_ollama": 1200}


def compose_steps(order, **kw):
    """Compose a multi-step chained order from a single template.

    Unlike `compose_orders` (fan-out), this handles chains where stages require
    different capabilities on different hosts. Each step is still composed by
    `compose_orders`, preserving per-host properties and safety guards.

    Input format:
        {
            "name": "...",
            "steps": [
                {"name": "teacher", "requires": ["probe"], ...},
                {"name": "student", "requires": ["train"],  "after": "teacher"},
                ...
            ]
        }

    - Step fields override order-level fields; others are inherited.
    - `after` defines the chain dependency. Forward/cyclic references are refused.

    Returns dict with keys:
        - ok (bool)
        - steps (list): placed steps if successful.
        - rejected (dict) & why (list): if ANY step fails, the WHOLE order is
          rejected to prevent partial execution on valuable hardware.
    """
    steps = (order or {}).get("steps") or []
    if not steps:
        return {"ok": False, "steps": [], "rejected": {},
                "why": ["order declares no `steps` - use compose_orders for a single-stage order"]}

    base = {k: v for k, v in (order or {}).items() if k != "steps"}
    seen, placed, why = set(), [], []
    for i, st in enumerate(steps):
        sname = str(st.get("name") or "step%d" % i).strip()
        after = str(st.get("after") or "").strip()
        if after and after not in seen:
            return {"ok": False, "steps": [], "rejected": {},
                    "why": ["step %r waits on %r, which is not an EARLIER step in this order "
                            "(forward or unknown reference)" % (sname, after)]}
        spec = json.loads(json.dumps(base))          # never mutate the caller's order
        spec.update({k: v for k, v in st.items() if k not in ("after", "requires", "items")})
        spec["name"] = "%s_%s" % (base.get("name") or "order", sname)
        req = list(st.get("requires") or base.get("requires") or [])
        res = compose_orders(spec, req, parallel=bool(st.get("parallel")),
                             items=st.get("items"), **kw)
        if not res.get("ok"):
            return {"ok": False, "steps": [], "rejected": res.get("rejected") or {},
                    "why": (res.get("why") or []) + [
                        "step %r cannot be placed, so the whole chain is refused: a half-placed "
                        "chain spends the card on a product that can never be finished" % sname]}
        for host, hspec in (res.get("orders") or {}).items():
            placed.append({"step": sname, "host": host, "spec": hspec, "after": after,
                           "requires": req})
        why += res.get("why") or []
        seen.add(sname)
    return {"ok": True, "steps": placed, "rejected": {}, "why": why}


def kind_for_order(template, requires=()):
    'Determine the VRAM band (`kind`) required for an order based on its template and requirements.\n\nThe scheduler owns placement policy; orders specify product intent, not hardware bands.\nAn explicit `kind` argument acts as an override. Default returns "caption" to maintain\nbackward compatibility with `compose_orders`.'
    spec = template or {}
    need = [str(r).lower() for r in (requires or spec.get("requires") or [])]
    mode = str(spec.get("mode") or "").strip().lower()
    if mode == "train" or any(r.startswith("train") for r in need):
        return "train"
    if mode in ("compute", "cpu") or "order.compute" in need:
        # compute orders declare what they burn via `load` (gpu|mixed|cpu). A cpu-load order
        # needs no VRAM envelope -> the small band. GPU/mixed picks the band by which weights the
        # step names, same flux2-vs-klein split as the render branch below.
        if str(spec.get("load") or "mixed").strip().lower() == "cpu":
            return "caption"
        return "render" if any("flux2" in r for r in need) else "render_small"
    if mode == "caption" or "caption" in need:
        return "caption"
    if any(r.startswith("triage") for r in need):
        # library-triage steps (30b): extract is CPU text work and classify drives a factory's
        # resident local LLM -- both fit the small band. Explicit rather than riding the fallback,
        # so a future change to the default cannot silently reband them.
        return "caption"
    if any(r.startswith(("render", "probe", "bake")) for r in need):
        # klein-class work fits the small band; anything naming the 32B flux2 path does not. WHICH
        # base a box actually holds is enforced above this by `requires` -- this only picks the
        # envelope. A mixed order takes the BIG band: the small one would under-provision its
        # flux2 half.
        if any("flux2" in r for r in need):
            return "render"
        if any(r.endswith((".klein", "_klein")) or "klein" in r for r in need):
            return "render_small"
        return "render"
    if mode in ("probe", "render"):
        return "render"
    return "caption"


def compose_orders(template, requires, parallel=True, items=None, **kw):
    """Compose parallel orders from a template.

    Splits work across factories based on HOST properties; the caller handles delivery.

    Returns:
        dict: Envelope with 'ok', 'orders' (host->spec map), 'rejected', and 'why'.
              Specs are fully materialized. Delivery is the caller's responsibility;
              factories pull their own orders via HTTP to ensure compatibility across
              diverse runner environments.

    Per-host variations derived from HOST properties:
      - name: suffixed with host ID to prevent archive collisions
      - caption_model: uses HOST_CAPTION_MODEL unless template specifies one
      - item slice: determined by chunk(), weighted by measured throughput
    """
    # An explicit kind WINS (an override); otherwise the scheduler derives it from the order. The
    # old literal default of "caption" is preserved inside kind_for_order's fallback, so no existing
    # caller changes behaviour.
    chunk_kw = {}
    _payload_base = os.path.basename(str(template.get("payload") or "")).lower()
    _size_key = next((k for k in SIZE_RESERVES if k in _payload_base), None)
    if items and str(template.get("mode") or "") in ("compute", "cpu") and _size_key:
        # SIZE-AWARE DEAL, desk half: per-host item budgets from the declared triage.ctx.N cap.
        # The chars-per-token estimate here is deliberately COARSE and generous -- the payload's
        # own context guard (est_tokens vs 0.9*num_ctx) measures the REAL prompt and stays the
        # backstop; this filter only stops the desk dealing what a box provably cannot take.
        # b64 items: raw chars ~= 0.75 * b64 chars; budget = 90% of the declared window minus
        # the prompt reserve (rubric ~1250 tok for classify; charter + template ~2600 for
        # rewrite, which also reserves room for the section-sized completion; corrective-review
        # rubric ~1200 + completion ~700 for native_history_review).
        _rewrite = _size_key == "triage_rewrite"
        _reserve = SIZE_RESERVES[_size_key]
        budgets = {}
        for h in set(CARDS) | set(NON_CARD_FACTORIES):
            ctx = None
            for c in factory_capabilities(h):
                if c.startswith("triage.ctx."):
                    try:
                        ctx = int(c.rsplit(".", 1)[1])
                    except ValueError:
                        pass
            budgets[h] = None if not ctx else int((0.9 * ctx - _reserve) * 3.5 / 0.75)
        chunk_kw = {"chunk_budgets": budgets,
                    "chunk_weights": {h: 1.0 / TRIAGE_CLASSIFY_S_PER_ROW.get(h, 1.5)
                                      for h in set(CARDS)}}
        if _rewrite:
            # Scope is determined by the model's triage capability:
            # full-scope models accept any tier; keep-only models are restricted to
            # pre-tiered keep-register sections; uncalibrated models are excluded (fail closed).
            restrict = {}
            for h in set(CARDS) | set(NON_CARD_FACTORIES):
                tm = None
                for c in factory_capabilities(h):
                    if c.startswith("triage.model."):
                        tm = c[len("triage.model."):]
                restrict[h] = TRIAGE_REWRITE_MODEL_SCOPE.get(tm, set())
            chunk_kw["chunk_restrict"] = restrict
            chunk_kw["chunk_weights"] = {h: 1.0 / TRIAGE_REWRITE_S_PER_SECTION.get(h, 17.0)
                                         for h in set(CARDS)}
    kind = kw.pop("kind", None) or kind_for_order(template, requires)
    # THE FELLOWSHIP FORM (#34 Phase 7 design § D): an order placed by the table may also bring the Tolkien form of the
    # same work - {"spec": <a Tolkien order with no worker named>, "requires": [...]}. Spock ranks the Fellowship as one
    # destination; if it wins, the desk sends THAT form with the placement key and Tolkien picks the Tolkien box or a tablet from
    # the table's rows. A single placed order only: a parallel deal has no Fellowship slice to give it.
    fel = kw.pop("fellowship", None) or None
    if fel is not None:
        why_not = None
        if parallel or items:
            why_not = "a Fellowship form is for a single placed order, not a parallel deal"
        elif not (kw.get("family") and kw.get("tier")):
            why_not = "a Fellowship form needs the order's family and tier - the table ranks the Fellowship by them"
        elif not isinstance(fel.get("spec"), dict) or not fel["spec"].get("kind"):
            why_not = "the Fellowship form has no spec (a Tolkien order with a kind)"
        if why_not:
            return {"ok": False, "orders": {}, "rejected": {FELLOWSHIP_HOST: why_not}, "why": [why_not]}
    weighed, weighed_rows = weighed_entries(kw.get("family"), kw.get("tier"), kw.get("urgency"))
    res = place_order(requires, kind=kind, parallel=parallel, items=items, order_spec=template, **chunk_kw,
                      fellowship_requires=list(fel.get("requires") or []) if fel is not None else None,
                      _weighed=weighed, _weighed_rows=weighed_rows, **kw)
    if not res.get("ok"):
        return {"ok": False, "held": bool(res.get("held")), "orders": {},
                "rejected": res.get("rejected") or {}, "why": res.get("why") or []}

    base = (template.get("name") or "order").strip()
    orders = {}
    for host, slice_ in (res.get("placements") or {}).items():
        if items is not None and not slice_:
            # a host the deal gave zero items (tier scope or window excluded it, or the
            # weights left it none) gets no order at all - an empty-slice order would spend
            # a lane doing nothing
            continue
        if fel is not None and host == FELLOWSHIP_HOST:
            spec = json.loads(json.dumps(fel["spec"]))
            spec["name"] = "%s_%s" % (base, host)
            spec["requires"] = list(fel.get("requires") or [])
            spec.setdefault("placed_by", "scheduler.compose_orders")
            spec["placed_on"] = host
            spec["placement"] = {"family": kw["family"], "tier": kw["tier"],
                                 "lens": policy_lens(kind, kw.get("lens"), kw.get("interactive"))}
            if kw.get("urgency"):
                spec["placement"]["urgency"] = kw["urgency"]
            if kw.get("only"):
                spec["pinned_to"] = sorted(str(h).strip().lower() for h in kw["only"])
            orders[host] = spec
            continue
        spec = json.loads(json.dumps(template))          # never mutate the caller's template
        spec["name"] = "%s_%s" % (base, host)
        # An explicit model in the template WINS -- an arm deliberately pinning a quant is a valid
        # experiment, and silently overriding it would make the arm a lie.
        if not spec.get("caption_model") and host in HOST_CAPTION_MODEL:
            spec["caption_model"] = HOST_CAPTION_MODEL[host]
        if not spec.get("caption_workers") and host in HOST_CAPTION_WORKERS:
            spec["caption_workers"] = HOST_CAPTION_WORKERS[host]
        # compute orders: the torch venv is a HOST property (same override rule as caption_model)
        if spec.get("mode") in ("compute", "cpu") and not spec.get("python_exe") \
                and host in HOST_COMPUTE_PY:
            spec["python_exe"] = HOST_COMPUTE_PY[host]
        # Fail closed: if no caption_model is specified, refuse rather than silently
        # inheriting the factory default, which would mix VLMs within a single corpus.
        if (spec.get("mode") == "caption" or "caption" in requires) and not spec.get("caption_model"):
            return {"ok": False, "orders": {}, "rejected": {host: "no caption model known"},
                    "why": ["%s has no HOST_CAPTION_MODEL entry and the template names none, so "
                            "its arm would silently use the factory's default captioner and put a "
                            "second model into one corpus. Add the host to HOST_CAPTION_MODEL."
                            % host]}
        spec["requires"] = list(requires)
        spec.setdefault("placed_by", "scheduler.compose_orders")
        spec["placed_on"] = host
        spec, why_not = resolve_policy_tokens(spec, host, kw.get("family"), kw.get("tier"),
                                              policy_lens(kind, kw.get("lens"), kw.get("interactive")),
                                              entry=weighed_rows.get(host))
        if why_not:
            return {"ok": False, "orders": {}, "rejected": {host: why_not},
                    "why": ["policy tokens unresolved on %s: %s" % (host, why_not)]}
        if kw.get("only"):
            # PINNED: the customer chose these boxes. The factory never hands a pinned order back for re-routing -
            # it waits for its own card
            spec["pinned_to"] = sorted(str(h).strip().lower() for h in kw["only"])
        if slice_ is not None:
            if spec.get("mode") in ("compute", "cpu"):
                spec["items"] = list(slice_)
                spec["items_count"] = len(slice_)
                # PROGRESS BY CONVENTION, not per-order-type wiring: a payload that writes
                # one marker per item under <out-stem>*.progress gets tile progress on
                # every parallel deal with nothing declared - the desk knows the out arg
                # and the slice size at compose time, which the submitter does not. The
                # wildcard covers both a plain <out>.json.progress dir and the
                # items-file share rename (.share-<name>.json.progress). A payload that
                # writes no markers just leaves the dir absent = phase-only, as before.
                # Template wins if it declares its own.
                _args = spec.get("args") or []
                if (not spec.get("progress_glob")
                        and len(_args) >= 2 and str(_args[1]).endswith(".json")):
                    spec["progress_glob"] = str(_args[1])[:-5] + "*.progress\\*.done"
                    spec["progress_total"] = len(slice_)
            else:
                spec["caption_stage_include"] = list(slice_)
                spec["caption_stage_count"] = len(slice_)
        orders[host] = spec
    return {"ok": True, "orders": orders, "rejected": res.get("rejected") or {},
            "why": res.get("why") or []}


def template_from_placed(spec, host):
    """(template, items) for an order compose_orders built for `host` -- undoes exactly what it adds, so a
    desk record written before records kept their template can still be re-placed. `items` is the dealt
    slice (compute items or caption stage list), None when the order was not dealt."""
    t = json.loads(json.dumps(spec or {}))
    suffix = "_%s" % host
    if host and str(t.get("name", "")).endswith(suffix):
        t["name"] = t["name"][:-len(suffix)]
    for field, table in (("caption_model", HOST_CAPTION_MODEL), ("caption_workers", HOST_CAPTION_WORKERS),
                         ("python_exe", HOST_COMPUTE_PY)):
        if host in table and t.get(field) == table[host]:
            t.pop(field)
    for field in ("placed_on", "placed_by", "requires", "pinned_to"):
        t.pop(field, None)
    items = None
    if "items" in t and t.get("mode") in ("compute", "cpu"):
        items = t.pop("items")
        t.pop("items_count", None)
    elif "caption_stage_include" in t:
        items = t.pop("caption_stage_include")
        t.pop("caption_stage_count", None)
    return t, items
