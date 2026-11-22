"""Submit and track TABLET-FLEET map orders from HAI's tool loop.

The tablet factory (tablet_runner.py, its own container) consumes `*.tjob.json` orders from the
inbox on the SSDShared share and ships `*.results.json` to the outbox. This module is the
CUSTOMER-side counter: it composes a valid `kind: "map"` spec and drops it in the letter slot.
It never touches the factory's queue, state or line — orders in, results out, nothing else
(the customer does not walk the factory floor).

Payloads are WHITELISTED by presence in the share's payloads/ directory — HAI picks a proven
payload by name and supplies only the items. Arbitrary code submission is deliberately not a
capability of this counter; adding a payload is a human act (drop a reviewed .py in payloads/).

Path dialects: this module runs in the head's container, which sees the share at TABFOUNDRY_SHARE
(/tabjobs); the tablet-foundry container sees the SAME share at TABFOUNDRY_SHARE_FOUNDRY. Spec paths
must be written in the CONSUMER'S dialect — the foundry is what resolves them (the loader decides
which copy is real).
"""
import json
import os
import re
import time

SHARE = os.environ.get("TABFOUNDRY_SHARE", "/tabjobs")    # the same default as every other head module that reads it
FOUNDRY_SHARE = os.environ.get("TABFOUNDRY_SHARE_FOUNDRY", "/ssdshared/tabfoundry-jobs")

MAX_ITEMS = 500          # a map order is a batch errand, not a corpus migration
_NAME_RE = re.compile(r"[^a-z0-9_-]+")


ENQUEUE_HOOKS = []   # called with each new order on the placing thread (app registers the chat announcer)


def _payloads_dir():
    return os.path.join(SHARE, "payloads")


def payload_names():
    """Available (human-reviewed) payloads, by basename without .py."""
    try:
        return sorted(f[:-3] for f in os.listdir(_payloads_dir())
                      if f.endswith(".py") and not f.startswith("_"))
    except Exception:
        return []


def submit_map(name, payload, items, control_item, control_expect, batch=None):
    """Compose and drop one map order. Returns a human-readable receipt or a refusal."""
    names = payload_names()
    if payload not in names:
        return ("payload %r is not in the whitelist. Available payloads: %s. "
                "New payloads are added by a human placing a reviewed .py in %s."
                % (payload, ", ".join(names) or "(none)", _payloads_dir()))
    if not isinstance(items, list) or not items:
        return "items must be a non-empty list of strings."
    clean = [str(x).strip() for x in items if str(x).strip()]
    bad = [x for x in clean if "|" in x or "\n" in x]
    if bad:
        return "%d item(s) contain '|' or newline, which the batcher reserves." % len(bad)
    if len(clean) > MAX_ITEMS:
        return "too many items (%d > %d) — split the job." % (len(clean), MAX_ITEMS)
    if not control_item or not str(control_expect).strip():
        return ("every order needs a POSITIVE CONTROL: control_item (an input whose correct "
                "output is known) and control_expect (a substring that output must contain). "
                "Without one, a broken payload and a genuinely empty result look identical.")
    if str(control_item).strip() in clean:
        # The factory keys coverage by item ID, so a control matching an item collapses the row count and marks the order VOID.
        return ("control_item %r is also one of the items. The factory counts coverage by item, so a control that "
                "repeats an item makes the order VOID. Use a control input that is NOT in the items list."
                % str(control_item).strip())
    base = _NAME_RE.sub("-", (name or "map").strip().lower()).strip("-") or "map"
    order = "%s.%s" % (base, time.strftime("%Y%m%d-%H%M%S"))
    spec = {
        "kind": "map",
        "name": order,
        "payload": "%s/payloads/%s.py" % (FOUNDRY_SHARE, payload),
        "items": clean,
        "control": {"item": str(control_item).strip(), "expect": str(control_expect).strip()},
    }
    if batch:
        spec["batch"] = int(batch)
    inbox = os.path.join(SHARE, "inbox")
    try:
        os.makedirs(inbox, exist_ok=True)
        # Atomic drop: the foundry tolerates half-written files with a grace period, but there is
        # no reason to make it exercise that tolerance.
        tmp = os.path.join(inbox, ".%s.tjob.json.tmp" % order)
        dst = os.path.join(inbox, "%s.tjob.json" % order)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=1)
        os.replace(tmp, dst)
    except OSError as e:
        return ("COULD NOT SUBMIT: the order share is not writable from this container "
                "(%s: %s). The order was NOT placed — this is an infrastructure fault to report, "
                "not a reason to retry." % (type(e).__name__, e))
    for hook in list(ENQUEUE_HOOKS):   # #37: the conversation that placed it shows it (same hook as p20_queue)
        try:
            hook({"id": "tablet:" + order, "kind": "tablet_map", "order_name": order})
        except Exception:
            pass
    return ("SUBMITTED tablet map order %r: %d items + control via payload %r. The factory DMs "
            "the outcome; check progress with tablet_map action=status name=%s. Results ship to "
            "the outbox as %s.results.json." % (order, len(clean), payload, order, order))


def order_status(name=None):
    """What the counter can SEE: the letter slot and the shipping dock. The factory's own queue
    and line state live inside its container; live progress reaches the homepage tile and DMs via
    the factory's own publishing — this deliberately does not build a second window onto it."""
    out = []
    inbox = os.path.join(SHARE, "inbox")
    outbox = os.path.join(SHARE, "outbox")
    try:
        pend = sorted(f for f in os.listdir(inbox) if f.endswith(".tjob.json"))
    except Exception:
        pend = []
    if name:
        pend = [f for f in pend if f.startswith(name)]
    if pend:
        out.append("in the letter slot (not yet accepted): %s" % ", ".join(pend))
    try:
        res = sorted((f for f in os.listdir(outbox) if f.endswith(".results.json")), reverse=True)
    except Exception:
        res = []
    if name:
        res = [f for f in res if f.startswith(name)]
    for f in res[:3]:
        try:
            with open(os.path.join(outbox, f), encoding="utf-8") as fh:
                r = json.load(fh)
            out.append("%s: usable=%s VOID=%s control=%s coverage=%s rows=%d wall=%.0fs"
                       % (f, r.get("usable"), r.get("VOID"), r.get("control_passed"),
                          r.get("coverage"), len(r.get("rows") or []), r.get("wall_s") or 0))
        except Exception as e:
            out.append("%s: unreadable (%s)" % (f, type(e).__name__))
    if not out:
        out.append("nothing in the letter slot and no shipped results%s. If an order was accepted "
                   "it is either running (see the homepage tablet tile / DM) or was HELD for a "
                   "spec defect (the DM says which)." % (" for %r" % name if name else ""))
    return "\n".join(out)


def order_rows(name):
    """Full rows from the newest shipped results for `name` (for when the answer IS the rows)."""
    outbox = os.path.join(SHARE, "outbox")
    try:
        res = sorted((f for f in os.listdir(outbox)
                      if f.endswith(".results.json") and f.startswith(name)), reverse=True)
    except Exception:
        res = []
    if not res:
        return "no shipped results for %r." % name
    with open(os.path.join(outbox, res[0]), encoding="utf-8") as fh:
        r = json.load(fh)
    rows = r.get("rows") or []
    body = json.dumps(rows[:200])
    if len(rows) > 200:
        body += " ...(%d more rows in %s)" % (len(rows) - 200, res[0])
    return body
