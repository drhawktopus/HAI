"""
Degradation analysis for Phase 0 miners.

Classifies Mothership telemetry events (TRAINING vs RENDER), buckets by boot,
and correlates wedge/stall probability with churn-within-boot.
Source: driverlogs/ directory.
"""
import csv
import datetime as dt
import os
import re
import sys

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "driverlogs")

BOOTS = [dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S") for s in [
    "2026-07-20 21:20:59",
    "2026-07-21 01:13:12",
    "2026-07-22 03:01:46",
    "2026-07-22 14:18:22",
    "2026-07-22 15:06:06",
    "2026-07-22 15:19:17",
    "2026-07-22 15:40:57",
    "2026-07-22 15:57:58",
    "2026-07-22 16:11:14",
    "2026-07-22 16:23:11",
]]

def boot_of(ts):
    prev = None
    for b in BOOTS:
        if ts >= b:
            prev = b
        else:
            break
    return prev

TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(.*)")

def parse_rescue():
    events = []
    with open(os.path.join(BASE, "telemetry", "stall_rescue.log"), encoding="utf-8", errors="replace") as f:
        for ln in f:
            m = TS.match(ln.rstrip())
            if not m:
                continue
            ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            msg = m.group(2)
            kind = None
            val = None
            if "STALL CONFIRMED" in msg:
                kind = "stall"
            elif "WEDGED verdict" in msg:
                kind = "wedge_verdict"
            elif "WEDGED CONTEXT" in msg:
                kind = "context_kill"
            elif "trigger=render-start" in msg:
                kind = "render_start"
            elif "DUD:" in msg:
                kind = "dud"
                mm = re.search(r"DUD: ([\d.]+) TFLOP", msg)
                val = float(mm.group(1)) if mm else None
            elif "daemon" in msg and "===" in msg:
                kind = "daemon_start"
            elif "prophylactic scheduled" in msg:
                kind = "prophylactic"
            if kind:
                events.append((ts, kind, val))
    return events

def parse_worker():
    jobs = []
    p = os.path.join(BASE, "p20_render_worker.log")
    with open(p, encoding="utf-8", errors="replace") as f:
        for ln in f:
            m = TS.match(ln.rstrip())
            if not m:
                continue
            ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            msg = m.group(2)
            jm = re.match(r"job (\w+): (\w+) for", msg)
            dm = re.match(r"job (\w+) DONE", msg)
            if jm:
                jobs.append([jm.group(1), ts, None])
            elif dm:
                for j in jobs:
                    if j[0] == dm.group(1):
                        j[2] = ts
    return jobs

def parse_telemetry():
    rows = []
    with open(os.path.join(BASE, "telemetry", "gpu_telemetry.csv"), encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            try:
                ts = dt.datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
                rate = float(r["rate_s_it"]) if r["rate_s_it"] else None
                pw = float(r["power_w"]) if r["power_w"] else None
                ml = float(r["mem_load_pct"]) if r["mem_load_pct"] else 0.0
                load = float(r["gpu_load"]) if r["gpu_load"] else 0.0
            except (ValueError, KeyError):
                continue
            rows.append((ts, r["run"], rate, pw, ml, load))
    return rows

def fmt_h(td):
    return "%.1fh" % (td.total_seconds() / 3600.0)

def main():
    ev = parse_rescue()
    jobs = parse_worker()
    tel = parse_telemetry()
    out = []
    say = out.append

    say("# Degradation-curve exhibits (Phase 0) — generated 2026-07-22")
    say("")

    # ---- Exhibit A: per-boot event table ----
    say("## Exhibit A — per-boot event census (rescue-daemon era, 07-20 21:20 onward)")
    say("")
    say("| boot | daemon events: stalls | duds (born-degraded) | render-starts | wedge verdicts | context-kills |")
    say("|---|---|---|---|---|---|")
    per = {}
    for ts, kind, val in ev:
        b = boot_of(ts)
        if b is None:
            continue
        d = per.setdefault(b, {})
        d[kind] = d.get(kind, 0) + 1
    for b in BOOTS:
        d = per.get(b, {})
        if not d:
            continue
        say("| %s | %d | %d | %d | %d | %d |" % (
            b.strftime("%m-%d %H:%M"), d.get("stall", 0), d.get("dud", 0),
            d.get("render_start", 0), d.get("wedge_verdict", 0), d.get("context_kill", 0)))
    say("")

    # ---- Exhibit B: render regime — wedge verdicts vs render index within boot ----
    say("## Exhibit B — RENDER regime: where in a boot do wedges land?")
    say("")
    say("For each boot: ordered render-start events; mark which were followed by a wedge verdict")
    say("within 15 min (before the next render-start).")
    say("")
    rs = [(ts, b) for ts, k, _ in ev if k == "render_start" for b in [boot_of(ts)] if b]
    wv = [ts for ts, k, _ in ev if k == "wedge_verdict"]
    say("| boot | render# in boot | start | wedged after? |")
    say("|---|---|---|---|")
    bycount = {}
    wedge_by_index = {}
    for i, (ts, b) in enumerate(rs):
        idx = bycount.get(b, 0) + 1
        bycount[b] = idx
        nxt = rs[i + 1][0] if i + 1 < len(rs) else ts + dt.timedelta(minutes=15)
        wedged = any(ts < w <= min(nxt, ts + dt.timedelta(minutes=15)) for w in wv)
        wedge_by_index.setdefault(idx, []).append(wedged)
        say("| %s | %d | %s | %s |" % (b.strftime("%m-%d %H:%M"), idx,
                                       ts.strftime("%H:%M:%S"), "YES" if wedged else "no"))
    say("")
    say("**Wedge rate by render index within a boot** (the H1 curve):")
    say("")
    say("| render # within boot | n | wedged | rate |")
    say("|---|---|---|---|")
    for idx in sorted(wedge_by_index):
        lst = wedge_by_index[idx]
        say("| %d | %d | %d | %.0f%% |" % (idx, len(lst), sum(lst), 100.0 * sum(lst) / len(lst)))
    say("")

    # ---- Exhibit C: training regime — dud (born-degraded) rate vs hours-since-boot ----
    say("## Exhibit C — TRAINING regime: born-degraded spawn rate vs boot age")
    say("")
    say("Dud = a fresh burst process measuring <14 TFLOP/s at spawn. If boot freshness mattered,")
    say("dud rate should rise with boot age; if spawn-lottery, it should be flat.")
    say("")
    duds = [(ts, val) for ts, k, val in ev if k == "dud"]
    resp = [ts for ts, k, _ in ev if k in ("dud",)]
    # bucket all burst spawns is not directly logged; use duds vs prophylactic+stall counts as denominator proxy
    buckets = {}
    for ts, val in duds:
        b = boot_of(ts)
        if not b:
            continue
        age_h = int((ts - b).total_seconds() // 3600)
        buckets.setdefault(age_h, []).append(val)
    say("| hours since boot | dud spawns | median dud TFLOP/s |")
    say("|---|---|---|")
    for h in sorted(buckets):
        vals = sorted(v for v in buckets[h] if v)
        med = vals[len(vals) // 2] if vals else 0
        say("| %d | %d | %.1f |" % (h, len(buckets[h]), med))
    say("")

    # ---- Exhibit D: telemetry stall-state fraction vs hours-since-boot (training rows) ----
    say("## Exhibit D — telemetry: fraction of samples in stall-state vs boot age")
    say("")
    say("Stall-state row = gpu_load>=95 AND power<290W AND (mem_load<15 OR rate>20s/it).")
    say("Denominator = rows with gpu_load>=95 (i.e., GPU 'busy'). Per hour-since-boot, pooled over era.")
    say("")
    tb = {}
    for ts, run, rate, pw, ml, load in tel:
        b = boot_of(ts)
        if not b or load < 95 or pw is None:
            continue
        age_h = int((ts - b).total_seconds() // 3600)
        stalled = pw < 290 and (ml < 15 or (rate is not None and rate > 20))
        a, s = tb.get(age_h, (0, 0))
        tb[age_h] = (a + 1, s + (1 if stalled else 0))
    say("| hours since boot | busy samples | stalled | stalled % |")
    say("|---|---|---|---|")
    for h in sorted(tb):
        a, s = tb[h]
        say("| %d | %d | %d | %.0f%% |" % (h, a, s, 100.0 * s / a))
    say("")

    # ---- Exhibit E: today's worker renders end-to-end ----
    say("## Exhibit E — worker-era renders (07-22): boot freshness vs outcome")
    say("")
    say("| job | boot | mins after boot | duration s | outcome |")
    say("|---|---|---|---|---|")
    for jid, t0, t1 in jobs:
        b = boot_of(t0)
        mins = (t0 - b).total_seconds() / 60.0 if b else -1
        say("| %s | %s | %.0f | %s | %s |" % (
            jid[:8], b.strftime("%H:%M") if b else "?", mins,
            ("%.0f" % (t1 - t0).total_seconds()) if t1 else "-", "done" if t1 else "no-DONE-line"))
    say("")

    text = "\n".join(out)
    dest = os.path.join(os.path.dirname(BASE), "exhibits_phase0.md")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print("\nwritten:", dest, file=sys.stderr)

main()
