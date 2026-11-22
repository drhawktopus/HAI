"""Project 34 menu filler - what the DESK says happened to an order (read-only).

The ledger used to ask one question of an order - "does out.json exist" - so an order that was cancelled and never
delivered counted as outstanding forever (the spec's old "Known ledger gap"). The desk already knows: every factory
reports each order's fate to the orchestrator, which keeps one record per order in `State\\p20-render-queue\\<id>.json`
(`p20_queue.py`: kind `factory_order`, `order_name` = the box-side order name, `status`, `error`). When a factory reads a
`cancel` control slip it closes the desk record `failed` with "cancelled on <box> by operator slip: <why>"
(`_desk_report_cancel` in p20_foundry.py). That store is on Softserve, so every GPU box - Softserve, BigHonker,
Mothership - is read the same way, locally, with no ssh and no credentials. This module only READS it and never writes.

classify() is the ledger's whole decision for one order, and it is pure:
  delivered      out.json exists (checked by the caller; a factory ships, then reports, so it is looked at last)
  outstanding    the desk record is pending / held / claimed / running, or `failed: stopped by operator slip` (a stop is a
                 pause: a requeue slip may resume it, so it is not dead - plan prints it for a human), or there is no
                 desk record yet, or the desk could not be read
  dead:cancelled the desk record says it was cancelled (`failed: cancelled on ...`, or status `cancelled`)
  dead:retired   the desk record says `failed` for any other reason (the payload exited non-zero, a refusal), or `done`
                 with no out.json to show for it (a factory ships before it reports, so that is a failed delivery): it
                 ended without delivering
"""
import glob
import json
import os

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_paths as MPATH  # noqa: E402
# the desk's records in the library's State folder, by the path every identity on this box sees and by the mapped one
DESK_DIRS = tuple(os.path.join(d, "p20-render-queue") for d in dict.fromkeys((MPATH.DOCS_STATE_LOCAL, MPATH.DOCS_STATE_DIR)) if d)
LIVE = ("pending", "held", "claimed", "running", "queued")


def read_desk(path=None):
    """{order_name: desk record} for every factory_order record, or None when the store cannot be read (every order
    then stays outstanding, as before). When two records share a name the newest `updated` wins."""
    folder = path or next((d for d in DESK_DIRS if os.path.isdir(d)), None)
    if not folder or not os.path.isdir(folder):
        return None
    out, bad = {}, 0
    for p in glob.glob(os.path.join(folder, "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                r = json.load(f)
        except Exception:                                            # noqa: BLE001
            bad += 1
            continue
        if not isinstance(r, dict) or r.get("kind") != "factory_order" or not r.get("order_name"):
            continue
        old = out.get(r["order_name"])
        if old is None or str(r.get("updated") or "") >= str(old.get("updated") or ""):
            out[r["order_name"]] = r
    return out if (out or not bad) else None


def candidate_names(folder, host):
    """A delivered folder is `<order>_<host>`; the desk's order_name is that on BigHonker/Mothership and, on Softserve,
    without the suffix."""
    sfx = "_%s" % host
    return [folder] + ([folder[:-len(sfx)]] if host and folder.endswith(sfx) else [])


def fate(rec):
    """The desk record's verdict on an order that has no out.json: 'outstanding' | 'dead:cancelled' | 'dead:retired'."""
    status = str(rec.get("status") or "").lower()
    why = str(rec.get("error") or rec.get("reason") or "").lower()
    if status == "cancelled" or (status == "failed" and why.startswith("cancelled")):
        return "dead:cancelled"
    if status == "failed":
        return "outstanding" if why.startswith("stopped by operator slip") else "dead:retired"
    if status == "done":
        # a factory ships before it reports done, so done with nothing delivered is a delivery that failed - ended, not on its way
        return "dead:retired"
    return "outstanding"


def classify(names, delivered, desk):
    """-> 'delivered' | 'outstanding' | 'dead:cancelled' | 'dead:retired' (see the module docstring)."""
    if delivered:
        return "delivered"
    if desk is None:
        return "outstanding"
    for n in names:
        if n in desk:
            return fate(desk[n])
    return "outstanding"


def stopped(names, delivered, desk):
    """True for an undelivered order the desk shows as stopped by an operator slip (outstanding, awaiting a human)."""
    if delivered or not desk:
        return False
    rec = next((desk[n] for n in names if n in desk), None)
    return bool(rec) and str(rec.get("status")).lower() == "failed" and fate(rec) == "outstanding"
