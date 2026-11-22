"""Energy-price gating for overnight self-engineering runs.

Reads the utility's hourly price curve (ENERGY_PSP_ENTITY) to determine if current/forthcoming
rates are low enough to run GPU-heavy jobs. The gate fails closed: if prices
cannot be confirmed as cheap, execution is deferred to save cost. This
behavior can be overridden by passing `force` or `ignore_energy_gate`.

The HA fetch logic is injectable (via `state=` or `http_get=`) for testing."""
from __future__ import annotations

import config


def _fetch_state(http_get=None):
    """Raw HA state dict for the PSP sensor, or None (fail-soft)."""
    if not config.HA_URL or not config.HA_TOKEN:
        return None
    import httpx
    url = config.HA_URL.rstrip("/") + "/api/states/" + config.ENERGY_PSP_ENTITY
    try:
        r = (http_get or httpx.get)(
            url, headers={"Authorization": f"Bearer {config.HA_TOKEN}"},
            timeout=config.ENERGY_HA_TIMEOUT)
        return r.json()
    except Exception:
        return None


def _hours(d):
    """A 24-element list of floats (or None) from a {"1":p,...,"24":p} dict."""
    out = []
    if isinstance(d, dict):
        for h in range(1, 25):
            try:
                out.append(float(d.get(str(h))))
            except (TypeError, ValueError):
                out.append(None)
    return out


def price_curve(state=None):
    """Normalized price info, or None: {current: float|None, current_hour: int|None,
    today: [24], tomorrow: [24]}. Hours are 1-24 (Hour N = (N-1):00..N:00 local)."""
    s = state if state is not None else _fetch_state()
    if not isinstance(s, dict):
        return None
    a = s.get("attributes") or {}
    try:
        cur = float(s.get("state"))
    except (TypeError, ValueError):
        cur = None
    ch = a.get("current_hour")
    try:
        ch = int(ch)
    except (TypeError, ValueError):
        ch = None
    return {"current": cur, "current_hour": ch,
            "today": _hours(a.get("today")), "tomorrow": _hours(a.get("tomorrow"))}


def curve_coverage(state=None):
    """Return coverage metadata for the fetched price curve.\n\nThe curve's date anchor is derived from HA's `last_updated`, not the wall clock,\nbecause the utility publishes once daily (early evening, local time). A stale sensor state without\nthis check would silently serve outdated data as current.\n\nReturns:\n    dict: {"fetched_at": epoch|int|None, "slots": {date_str: list[bool]}}\n        - `fetched_at`: Timestamp of the fetch, or None if no state.\n        - `slots`: Dict mapping date strings to a list of 24 booleans indicating\n          validity for each local hour (0-23). Empty if no timestamp exists.\n    None: If the input state is invalid or missing."""
    import datetime
    from zoneinfo import ZoneInfo
    s = state if state is not None else _fetch_state()
    if not isinstance(s, dict):
        return None
    local = config.LAB_ZONE
    fetched_at, slots = None, {}
    try:
        dt = datetime.datetime.fromisoformat(str(s.get("last_updated")).replace("Z", "+00:00"))
        fetched_at = dt.timestamp()
        anchor = dt.astimezone(local).date()
        a = s.get("attributes") or {}
        for day_offset, key in ((0, "today"), (1, "tomorrow")):
            hours = _hours(a.get(key))
            date = anchor + datetime.timedelta(days=day_offset)
            slots[date.isoformat()] = [h is not None for h in hours] if hours else [False] * 24
    except (TypeError, ValueError):
        pass
    return {"fetched_at": fetched_at, "slots": slots}


def window_covered(start_epoch, end_epoch, state=None):
    """Check if fetched data covers every hour in the window. FAILS CLOSED.

    Rejects mere recency; requires coverage of the specific time range.
    Returns explicit STALE reasons on failure to distinguish from idle states."""
    import datetime
    from zoneinfo import ZoneInfo
    cov = curve_coverage(state)
    if cov is None:
        return False, {"reason": "no_price_state"}
    if not cov.get("fetched_at"):
        return False, {"reason": "no_data_timestamp"}
    local = config.LAB_ZONE
    slots = cov["slots"]
    # Step by whole hours (Central offsets are whole hours). Avoid naive wall-clock math: `fold` makes DST-fallback hours ambiguous, breaking window bounds.
    s, e = float(start_epoch), float(end_epoch)
    if e <= s:
        return False, {"reason": "empty_window"}
    missing = []
    x = s
    while x < e:
        t = datetime.datetime.fromtimestamp(x, tz=local)
        day = slots.get(t.date().isoformat())
        if not day or not day[t.hour]:
            missing.append("%sT%02d:00" % (t.date().isoformat(), t.hour))
        x = (int(x // 3600) + 1) * 3600.0
    if missing:
        return False, {"reason": "window_not_covered", "missing_hours": missing[:6],
                       "missing_count": len(missing),
                       "note": "the price feed has no prices for these hours - stale or "
                               "pre-publication data (publishes ~6pm Central; the upstream fetch "
                               "is a parked repair)"}
    return True, {"reason": "covered", "fetched_at": cov["fetched_at"]}


def is_runnable_now(curve=None, ceiling=None):
    """(ok: bool, info: dict). ok=True iff the current price is at/below the ceiling.
    FAIL-CLOSED: price unknown -> ok=False (defer), so a data glitch never runs at peak."""
    if ceiling is None:
        ceiling = config.ENERGY_PRICE_CEILING
    if curve is None:
        curve = price_curve()
    price = (curve or {}).get("current")
    if price is None:
        return False, {"reason": "price_unknown", "price": None, "ceiling": ceiling}
    ok = price <= ceiling
    return ok, {"reason": "cheap" if ok else "expensive", "price": round(price, 5), "ceiling": ceiling}


def price_now(curve=None, placeholder=None):
    """Return the current-hour price for placement.

    Returns a dict with "usd_per_kwh", "source", "hour", and "curve". The price is the
    live sensor value if available (source "psp"), otherwise the configured placeholder
    (source "placeholder").

    `curve=None` reads the sensor. Pass `curve={}` to indicate a read failure."""
    c = curve if curve is not None else price_curve()
    cur = (c or {}).get("current")
    if cur is not None:
        return {"usd_per_kwh": float(cur), "source": "psp", "hour": (c or {}).get("current_hour"), "curve": c}
    ph = config.ENERGY_PRICE_PLACEHOLDER if placeholder is None else placeholder
    return {"usd_per_kwh": float(ph), "source": "placeholder", "hour": None, "curve": c or None}


def price_band(usd_per_kwh):
    """'normal' | 'peak' | 'extreme' based on config.ENERGY_PEAK_PRICE / ENERGY_EXTREME_PRICE.
    Unset config lines are ignored; thresholds must be derived from real price history, not arbitrary round numbers."""
    if config.ENERGY_EXTREME_PRICE is not None and usd_per_kwh >= config.ENERGY_EXTREME_PRICE:
        return "extreme"
    if config.ENERGY_PEAK_PRICE is not None and usd_per_kwh >= config.ENERGY_PEAK_PRICE:
        return "peak"
    return "normal"


def cents_per_solve(joules, usd_per_kwh):
    """A row's J per solve at a price -> cents (3,600,000 J per kWh); None when the row's energy is unmetered."""
    if joules is None:
        return None
    return float(joules) / 3.6e6 * float(usd_per_kwh) * 100.0


def cheapest_window(curve, hours_needed, *, from_hour=None):
    """Cheapest contiguous `hours_needed`-hour window over the rest of today (from the current hour)
    plus all of tomorrow. Returns {day, start_hour, end_hour, avg_price} or None. For the scheduler
    to pick 'run when cheapest'."""
    if not curve:
        return None
    hours_needed = max(1, int(hours_needed))
    start = from_hour if from_hour is not None else (curve.get("current_hour") or 1)
    timeline = []  # (day, hour, price) forward from now
    for h in range(start, 25):
        timeline.append(("today", h, curve["today"][h - 1] if h - 1 < len(curve["today"]) else None))
    for h in range(1, 25):
        timeline.append(("tomorrow", h, curve["tomorrow"][h - 1] if h - 1 < len(curve["tomorrow"]) else None))
    best = None
    for i in range(0, len(timeline) - hours_needed + 1):
        window = timeline[i:i + hours_needed]
        prices = [w[2] for w in window]
        if any(p is None for p in prices):
            continue
        avg = sum(prices) / len(prices)
        if best is None or avg < best["avg_price"]:
            best = {"day": window[0][0], "start_hour": window[0][1],
                    "end_hour": window[-1][1], "avg_price": round(avg, 5)}
    return best


def cheapest_window_label(curve, hours_needed):
    """Human-readable label for the cheapest window, e.g. 'cheapest 4h: today 02:00-05:00 @ 0.033/kWh'.

    Pure wrapper around cheapest_window: returns 'cheapest window unavailable' when no valid window
    exists."""
    w = cheapest_window(curve, hours_needed)
    if w is None:
        return "cheapest window unavailable"
    h = max(1, int(hours_needed))
    sh = f"{w['start_hour']:02d}:00"
    eh = f"{w['end_hour']:02d}:00"
    day = w['day']
    if w['start_hour'] > w['end_hour']:
        # Overnight window: start > end means it wraps into the next day
        day = w['day'] + "/tomorrow"
    return f"cheapest {h}h: {day} {sh}-{eh} @ {w['avg_price']}/kWh"
