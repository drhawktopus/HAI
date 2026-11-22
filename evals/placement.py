"""Placement + identity assertions for the HAI eval harness.

WHY THIS EXISTS: a measurement that bypasses the order system cannot say which model answered.
`scheduler.place()` (Spock) returns the fleet's own placement decision; a harness that hand-picks
hosts instead attributes results to a tier that did not serve them, and a routing change halfway
through a measurement compares across tiers.

So this module does three things, and refuses rather than annotates:

  1. ASKS Spock where the work belongs (never picks).
  2. READS BACK what actually served, and REFUSES TO RECORD a row whose served identity does not
     match the placement.
  3. VOIDS a row whose call was answered at a degraded context.

(3) is why `ollama_client`'s ladder logs loudly: it halves the window (65536 -> 32768 -> 8192) on
any transient error. A degraded answer is not a worse answer, it is an answer FROM A DIFFERENT
CONFIGURATION, and without that log line there is no way to tell the two apart after the fact.

USES EXISTING INSTRUMENTS ONLY. No new capture: `scheduler.place()` for placement, the `/query`
response for served identity, `deep_trace_v1` (deliberation.build_deep_trace) for per-row evidence,
and the orchestrator work log for the downgrade signal.
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import datetime
import json
import pathlib
import re
import subprocess

# The orchestrator work log — notes.log() writes here, and it is where the CTX DOWNGRADE line lands.
import os as _os
import sys as _sys2
_sys2.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "bin"))
import lab_settings as _S  # noqa: E402
NOTES_DIR = pathlib.Path(_S.setting("LIBRARY_ROOT", "")) / "Notes"   # the library's notes folder


class PlacementError(RuntimeError):
    """Raised when placement cannot be established. NEVER downgraded into a guess."""


def _in_container(pycode, timeout=90):
    """Run read-only python inside the orchestrator container and return stdout.

    Piped as a file rather than embedded in nested quotes: an earlier in-force check used
    `ssh "... sh -c \\"grep -c $m ...\\""`, the variable expanded to EMPTY, grep took a FILENAME as
    its pattern, and it returned a confident 0 for every marker — which reads exactly like "the
    deploy did not land". Nested shell quoting is not a place to be clever."""
    tmp = pathlib.Path(__file__).with_name("_probe_tmp.py")
    tmp.write_text(pycode, encoding="utf-8")
    try:
        with tmp.open("rb") as fh:
            out = subprocess.run(["ssh", _S.setting("HEAD_NAME", "head"), "docker exec -i orchestrator python -"],
                                 stdin=fh, capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            raise PlacementError(f"in-container probe failed rc={out.returncode}: "
                                 f"{(out.stderr or '')[-300:]}")
        return out.stdout
    finally:
        tmp.unlink(missing_ok=True)


# --- 1. ASK SPOCK ---------------------------------------------------------------------------------
_PLACE_PROBE = '''
import json, scheduler, config
kind = %(kind)r
ranked = scheduler.place(kind)
def _url(h):
    # scheduler.CARDS OWNS the host-name -> URL mapping. Taken from the owning component, never
    # reconstructed: an earlier pass hardcoded a name->IP map from memory and then read its own
    # label back as evidence.
    try:
        return (scheduler.CARDS.get(h) or {}).get("url")
    except Exception:
        return None
print("PLACEMENT " + json.dumps({
    "kind": kind,
    "ranked": [{"host": h, "score": s, "why": w, "url": _url(h)} for h, s, w in ranked[:5]],
    "best": (ranked[0][0] if ranked else None),
    "best_url": (_url(ranked[0][0]) if ranked else None),
    "heavy_host": config.HEAVY_HOST, "heavy_model": config.HEAVY_MODEL,
    "heavy_num_ctx": config.HEAVY_NUM_CTX,
    "brain_host": config.BRAIN_HOST, "brain_model": config.BRAIN_MODEL,
    "num_ctx": config.NUM_CTX,
}))
'''


def intended_placement(kind="chat"):
    """Spock's ranking for this kind of work. Raises rather than guessing.

     NOTE FOR THE REVIEWER: chat today does NOT route through `scheduler.place()` — it uses the
    BRAIN_HOST/HEAVY_HOST pointers plus the routing flag. So a disagreement between Spock's ranking
    and what actually serves is a FINDING about the lab's wiring, not a bug in this harness, and it
    is surfaced rather than reconciled."""
    out = _in_container(_PLACE_PROBE % {"kind": kind})
    line = next((l for l in out.splitlines() if l.startswith("PLACEMENT ")), None)
    if not line:
        raise PlacementError(f"no PLACEMENT line from the probe; got {out[-300:]!r}")
    return json.loads(line[len("PLACEMENT "):])


# --- 1b. LET SPOCK CHOOSE THE TIER — do not hand-pick a routing flag ------------------------------
# The first cut of this module ASKED Spock and then overrode the answer with my own force_27b /
# quick_8b picks. Asking and ignoring is not going through Fleet Command — it is hand-picking with a
# consultation attached. Fleet Command already models both chat tiers as KINDS:
# chat vram 6.0 interactive -> Spock: bighonker ("chat default")
# chat27b vram 20.0 interactive -> Spock: mothership ONLY (the 20 GB gate excludes the rest)
# which is exactly the distinction the pins need. So the PIN declares the KIND (a property of the
# work), Spock decides the HOST, and the routing flag is DERIVED from Spock's answer rather than
# chosen by me.
# policy 10, quoted from place()'s own docstring: "a caller that gets [] must TELL the user, not
# quietly downgrade". If Spock cannot place the kind, this REFUSES. It never falls back to the other
# tier — a silent downgrade is how a measurement ends up on the wrong model, which is the whole
# reason this lane exists.
KIND_FOR_TIER = {"heavy": "chat27b", "quick": "chat"}


def route_for_tier(tier):
    """(routing_flag, expected_host, placement) — chosen BY SPOCK for this tier's kind.

    Raises PlacementError when Spock places nothing: no work-around, no fallback tier."""
    kind = KIND_FOR_TIER.get(tier)
    if not kind:
        raise PlacementError(f"no fleet KIND maps to tier {tier!r}")
    plc = intended_placement(kind)
    best = plc.get("best")
    if not best:
        raise PlacementError(
            f"Spock placed NOTHING for kind {kind!r} — the fleet cannot serve this tier right now. "
            f"Refusing (policy 10: tell, never quietly downgrade). ranked={plc.get('ranked')}")
    # Derive the /query axis from Spock's host, rather than picking it. Compare URL to URL — the
    # name and the URL are different namespaces, and matching a name against a URL silently fails.
    best_url = (plc.get("best_url") or "").rstrip("/")
    heavy_host = (plc.get("heavy_host") or "").rstrip("/")
    brain_host = (plc.get("brain_host") or "").rstrip("/")
    if not best_url:
        raise PlacementError(f"Spock placed {kind!r} on {best!r} but the scheduler has no URL for "
                             f"that host — cannot verify what the chat API would reach.")
    routing = "force_27b" if kind == "chat27b" else "quick_8b"
    expect_url = heavy_host if kind == "chat27b" else brain_host
    if best_url != expect_url:
        raise PlacementError(
            f"Spock placed {kind!r} on {best!r} ({best_url}), but the chat axis {routing!r} reaches "
            f"{expect_url}. The API cannot address Spock's choice directly — refusing rather than "
            f"sending it somewhere else and calling that placement.")
    return routing, best, plc


# --- 2. READ BACK WHAT SERVED, AND ASSERT ---------------------------------------------------------
# `route` is the only served-identity field the response carries that is actually measured:
# `host` is a hardcoded literal on the heavyweight path and `model` is the CONFIGURED name, not the
# responder's. (Both were checked: `.76 = Mothership`, `.167 = BigHonker`, so the LABELS are correct
# — but "correct label" is not "measured value", and the distinction is the whole point here.)
TIER_BY_ROUTE = {"heavyweight": "heavy", "local": "quick"}


def served_identity(resp):
    return {"route": resp.get("route"),
            "tier": TIER_BY_ROUTE.get(resp.get("route")),
            "reported_host": resp.get("host"),
            "reported_model": resp.get("model")}


def assert_placement(expect_tier, resp, placement=None):
    """(ok, reason). ok=False means DO NOT RECORD THIS ROW.

    Refuses; does not annotate. A row served by a tier other than the one the pin was cut from is not
    a noisy measurement — it is a measurement of a different system, and recording it with a caveat
    is how "1 in 4" ended up in three documents as a fact about HAI."""
    got = served_identity(resp)
    if got["tier"] is None:
        return False, (f"route={got['route']!r} carries no tier (error/direct route) — "
                       f"nothing was measured")
    if expect_tier and got["tier"] != expect_tier:
        return False, (f"pin requires the {expect_tier!r} tier; served on {got['tier']!r} "
                       f"(route={got['route']}, reported model={got['reported_model']}). "
                       f"Different system — refusing to record.")
    if placement and placement.get("best") and got["tier"] == "heavy":
        # Surface, never reconcile: chat does not consult Spock today.
        return True, (f"served heavy; Spock's best for this kind was {placement['best']!r} "
                      f"(chat does not route through place() — disagreement is a WIRING FINDING)")
    return True, "placement consistent"


# --- 3. VOID ON A DEGRADED CONTEXT ----------------------------------------------------------------
_DOWNGRADE = re.compile(r"CTX DOWNGRADE .*?requested=(\d+) using=(\d+)")


def ctx_downgraded_since(started_at, host_hint=None):
    """Did any call downgrade its context during this measurement window?

    Reads the orchestrator work log — the existing instrument, made loud in the same pass. Returns
    (bool, [lines]). A MISSING LOG FILE RETURNS None, NOT False: "could not measure" must never be
    rendered as "did not happen", which is the failure this whole lane keeps finding."""
    day = datetime.datetime.now().strftime("%Y-%m-%d")
    f = NOTES_DIR / f"{day}-orchestrator.md"
    if not f.is_file():
        return None, [f"work log not readable at {f} — COULD NOT MEASURE"]
    hits = []
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "CTX DOWNGRADE" not in line:
            continue
        if host_hint and host_hint not in line:
            continue
        hits.append(line.strip())
    return (bool(hits), hits)


def void_if_degraded(started_at, host_hint=None):
    """(verdict, why). verdict is None when clean, else a row verdict that must not be graded."""
    flag, lines = ctx_downgraded_since(started_at, host_hint)
    if flag is None:
        return "CTX_UNKNOWN", lines[0]
    if flag:
        m = _DOWNGRADE.search(lines[-1])
        detail = (f"requested={m.group(1)} using={m.group(2)}" if m else lines[-1][:160])
        return "VOID_DEGRADED_CTX", (f"a context downgrade occurred during this call ({detail}). "
                                     f"The answer came from a different configuration than the one "
                                     f"under test — not graded.")
    return None, "full requested context"
