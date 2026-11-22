"""The box's standing GPU clock policy, and the nvidia-smi arguments for any clock form: ONE derivation for the health
daemon (duty 1: re-apply the policy when the card has lost it) and the foundry (an order's own `gpu_clock`, and the
restore of the box's policy when the order ends).

Three settings, in etc\\lab.env beside the foundry root, read through foundry_paths (unset = no clock duty: the card
runs at its own boost, and nothing here touches it):
    FOUNDRY_TARGET_CLOCK    a LOCK at N MHz (nvidia-smi -lgc N); wins when set
    FOUNDRY_CLOCK_FLOOR     at least N MHz
    FOUNDRY_CLOCK_CEILING   at most N MHz        floor and ceiling together are a band (nvidia-smi -lgc lo,hi)

A policy is None (off), an int (a lock) or {"floor": N or None, "ceiling": M or None} (a band; one side may be open,
and the card's own range fills it). `BH_TARGET_CLOCK` in the environment is still honoured as the lock, for the task
that carried it before the setting had its name."""
import os

import foundry_paths as P


def _mhz(value):
    try:
        n = int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return n if n and n > 0 else None


def box_policy(settings=None):
    """The box's policy from its settings (a mapping for tests; the lab.env through foundry_paths otherwise)."""
    def get(name):
        return (settings.get(name, "") if settings is not None else P.setting(name, "")) or ""
    lock = _mhz(get("FOUNDRY_TARGET_CLOCK") or (os.environ.get("BH_TARGET_CLOCK", "") if settings is None else ""))
    if lock:
        return lock
    floor, ceiling = _mhz(get("FOUNDRY_CLOCK_FLOOR")), _mhz(get("FOUNDRY_CLOCK_CEILING"))
    if floor is None and ceiling is None:
        return None
    return {"floor": floor, "ceiling": ceiling}


def ceiling(policy):
    """The clock a policy forbids the card to exceed (a lock, or a band's ceiling), or None when it sets none."""
    if isinstance(policy, bool) or policy is None:
        return None
    if isinstance(policy, int):
        return policy
    return policy.get("ceiling") if isinstance(policy, dict) else None


def label(policy):
    """Words for a policy: 'lock 1500 MHz', 'band 1200-1800 MHz', 'floor 1200 MHz', 'ceiling 1800 MHz', 'unlocked'."""
    if policy is None or policy == 0:
        return "unlocked"
    if isinstance(policy, int):
        return "lock %d MHz" % policy
    if isinstance(policy, dict):
        lo, hi = policy.get("floor"), policy.get("ceiling")
        if lo and hi:
            return "band %d-%d MHz" % (lo, hi)
        if lo:
            return "floor %d MHz" % lo
        if hi:
            return "ceiling %d MHz" % hi
        return "unlocked"
    return str(policy)


def card_clock_range(run):
    """(lowest supported graphics clock, the card's max SM clock) in MHz, or None when the card cannot say.
    `run(args)` runs nvidia-smi with those arguments and returns (ok, text)."""
    ok_max, text_max = run(["--query-gpu=clocks.max.sm", "--format=csv,noheader,nounits"])
    ok_sup, text_sup = run(["--query-supported-clocks=graphics", "--format=csv,noheader,nounits"])
    try:
        hi = int(float(text_max.split()[0])) if ok_max else None
        sup = [int(float(x)) for x in text_sup.replace(",", " ").split()
               if x.replace(".", "", 1).isdigit()] if ok_sup else []
        lo = min(sup) if sup else None
    except (ValueError, IndexError):
        hi, lo = None, None
    return (lo, hi) if lo and hi else None


def clock_args(want, log, card_range):
    """nvidia-smi arguments for a clock form (an order's `gpu_clock`, or the box's policy), or None when it cannot be
    expressed (logged). `card_range()` is asked only for a one-sided band. Forms:
    "unlocked"                    the card's own boost (-rgc)
    N (int)                       a LOCK at N MHz (-lgc N)
    {"floor": N}                  at least N (card's max above it)
    {"ceiling": N}                at most N (card's lowest clock below it)
    {"floor": N, "ceiling": M}    a band"""
    if isinstance(want, dict):
        floor, ceiling_ = want.get("floor"), want.get("ceiling")
        if floor in (None, "") and ceiling_ in (None, ""):
            return None
        if floor in (None, "") or ceiling_ in (None, ""):
            rng = card_range()
            if rng is None:
                log("gpu clock: %r needs the card's clock range and nvidia-smi could not give it; not applied" % (want,))
                return None
            floor = rng[0] if floor in (None, "") else floor
            ceiling_ = rng[1] if ceiling_ in (None, "") else ceiling_
        lo, hi = int(floor), int(ceiling_)
        if lo > hi:
            log("gpu clock: %r has its floor above its ceiling; not applied" % (want,))
            return None
        return ["-lgc", "%d,%d" % (lo, hi)]
    if str(want).lower() == "unlocked":
        return ["-rgc"]
    return ["-lgc", str(int(want))]
