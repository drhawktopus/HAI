#!/usr/bin/env python3
"""Root timer on rigby's HOST that reboots the host when GPU cards are missing.

This script must run on the host (Tolkien container cannot reboot the host).
It installs itself as `/usr/local/bin/rigby_card_keeper.py` and writes its own systemd units to keep the declared and running cadence synchronized.

Reboots ONLY when:
1. Expected cards (from fresh `card_wanted.json` or driver list) are missing.
2. `nvidia-smi -L` confirms the cards are physically missing (not just driver glitch).
3. Both Tolkien lanes are idle (no active orders) to avoid killing running work.
4. It has not already rebooted for this specific drop (prevents infinite reboot loop).

Stamps `checked` and `period_s` into `card_keeper.json` on every run so external systems can detect liveness based on the last update timestamp."""
import json
import os
import subprocess
import sys
import time

# Settings, from the environment (the service unit loads /etc/default/tolkien-host): Tolkien's state folder on this
# host and the cards this host HAS (CARD_KEEPER_CARDS, comma-separated: a NAMES key or the driver's own name).
ENVFILE = "/etc/default/tolkien-host"
STATE = os.environ.get("CARD_KEEPER_STATE", "")
WANTED = "card_wanted.json"
KEEPER = "card_keeper.json"
LANE_CLAIMS = ("CURRENT_ORDER.json", "CURRENT_ORDER.rigby.json")
NAMES = {"gtx1060": "GTX 1060", "gt730": "GT 730"}        # tolkien_llm.GPU_NAMES: a card by NAME
# the cards this host HAS: one missing from `nvidia-smi -L` is a drop, order waiting or not; none = only a fresh marker
EXPECTED = tuple(c.strip() for c in os.environ.get("CARD_KEEPER_CARDS", "").split(",") if c.strip())
STALE_X = 3                                               # the lab's reporter rule: silent past 3x its own cadence
PERIOD_S = 120          # the keeper's cadence: the timer below runs at it and every heartbeat declares it
UNITS = {
    "rigby-card-keeper.service": (
        "[Unit]\n"
        "Description=rigby card keeper - reboot rigby when one of its GPUs fell off the bus "
        "(once per drop)\n"
        "Documentation=file:///usr/local/bin/rigby_card_keeper.py\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "EnvironmentFile=-/etc/default/tolkien-host\n"
        "ExecStart=/usr/bin/python3 /usr/local/bin/rigby_card_keeper.py\n"),
    "rigby-card-keeper.timer": (
        "[Unit]\n"
        "Description=rigby card keeper tick (nvidia-smi + Tolkien's marker; reboots only under its rules)\n\n"
        "[Timer]\n"
        "OnBootSec=%ds\n"
        "OnUnitActiveSec=%ds\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n" % (PERIOD_S, PERIOD_S)),
}


def _seen(cards, smi_list):
    return bool(cards) and all(NAMES.get(c, c) in (smi_list or "") for c in cards)


def _fresh(marker, now):
    period = float((marker or {}).get("period_s") or 0)
    return bool(marker) and bool(period) and now - float(marker.get("updated") or 0) <= STALE_X * period


def decide(marker, keeper, smi_list, lanes_busy, boot_id, now):
    """Return (action, record, reason) where action is "none", "reboot", or "give_up".

    "Drop" means an expected card is missing from the driver list. A drop triggers a reboot even if no active order depends on the card (to prevent silent downtime).

    Rules:
    - Never reboot under running work.
    - Reboot once per drop event.
    - Give up if the reboot did not restore the card."""
    keeper = dict(keeper or {})
    if _fresh(marker, now):
        wanted, waiting = list(marker.get("cards") or []), "order %s waits on" % marker.get("order")
    else:
        wanted, waiting = [c for c in EXPECTED if not _seen([c], smi_list)], "no order waiting;"
    if keeper.get("rebooted_from_boot") and _seen(wanted or keeper.get("cards") or list(EXPECTED), smi_list):
        # the card came back (after our reboot or by itself): this drop is over - the next one is a fresh drop
        return "none", {"last_ok": now, "last_reboot": keeper.get("rebooted_at")}, "cards visible: the drop is over"
    if not wanted:
        return "none", None, "every card rigby has is visible (%s)" % ", ".join(EXPECTED)
    if _seen(wanted, smi_list):
        return "none", None, "cards %s visible to the driver - Tolkien will run the order itself" % wanted
    if keeper.get("gave_up"):
        return "none", None, "gave up on this drop at %s - waiting for a human" % keeper["gave_up"]
    if keeper.get("rebooted_from_boot"):
        # a timer never starts a run once shutdown has begun, so a run in the SAME boot means the reboot never happened
        keeper["gave_up"] = now
        keeper["gave_up_why"] = (
            "still missing in the boot after the keeper's reboot" if keeper["rebooted_from_boot"] != boot_id else
            "the keeper asked for a reboot and rigby never rebooted (journalctl -u rigby-card-keeper)")
        return "give_up", keeper, "card %s: %s" % (wanted, keeper["gave_up_why"])
    if lanes_busy:                     # only the REBOOT waits for idle lanes; giving up is a report and never waits
        return "none", None, "a lane is busy (%s) - never reboot under running work" % ", ".join(lanes_busy)
    rec = {"rebooted_at": now, "rebooted_from_boot": boot_id, "order": (marker or {}).get("order") if
           _fresh(marker, now) else None, "cards": wanted}
    return "reboot", rec, "%s %s missing, both lanes idle - rebooting rigby (once per drop)" % (waiting, wanted)


def record(keeper, rec, now, action, why):
    """What to write: a decision REPLACES the decision fields (rec), otherwise they are kept; the heartbeat fields are
    stamped on every run either way."""
    out = dict(rec if rec is not None else (keeper or {}))
    out.update({"checked": now, "period_s": PERIOD_S, "last": "%s: %s" % (action, why)})
    return out


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                                # noqa: BLE001
        return {}


def write_units(dest):
    for name, text in UNITS.items():
        path = os.path.join(dest, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote " + path)


def main(argv):
    if "--write-units" in argv:
        rest = argv[argv.index("--write-units") + 1:]
        write_units(rest[0] if rest else "/etc/systemd/system")
        return 0
    dry = "--dry" in argv
    if not STATE:
        print("card keeper: CARD_KEEPER_STATE unset (%s) - cannot see the lanes, no action" % ENVFILE)
        return 2
    marker = _read(os.path.join(STATE, WANTED))
    keeper = _read(os.path.join(STATE, KEEPER))
    try:
        smi = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=60).stdout
    except Exception as e:                                           # noqa: BLE001 - cannot tell = never reboot
        print("card keeper: nvidia-smi failed (%s) - no action" % type(e).__name__)
        return 0
    busy = [c for c in LANE_CLAIMS if os.path.exists(os.path.join(STATE, c))]
    with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as f:
        boot = f.read().strip()
    now = time.time()
    action, rec, why = decide(marker, keeper, smi, busy, boot, now)
    print("card keeper: %s - %s%s" % (action, why, " (dry)" if dry else ""))
    if dry:
        return 0
    tmp = os.path.join(STATE, KEEPER + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record(keeper, rec, now, action, why), f)
    os.replace(tmp, os.path.join(STATE, KEEPER))
    if action == "reboot":
        subprocess.run(["systemctl", "reboot"], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
