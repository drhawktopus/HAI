"""Charts for the Phase 0 exhibits — two-phenomena evidence."""
import datetime as dt
import os
import re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SP = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(SP, "driverlogs")

BOOTS = [dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S") for s in [
    "2026-07-20 21:20:59", "2026-07-21 01:13:12", "2026-07-22 03:01:46",
    "2026-07-22 14:18:22", "2026-07-22 15:06:06", "2026-07-22 15:19:17",
    "2026-07-22 15:40:57", "2026-07-22 15:57:58", "2026-07-22 16:11:14",
    "2026-07-22 16:23:11"]]

def boot_of(ts):
    prev = None
    for b in BOOTS:
        if ts >= b:
            prev = b
        else:
            break
    return prev

TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(.*)")
ev = []
with open(os.path.join(BASE, "telemetry", "stall_rescue.log"), encoding="utf-8", errors="replace") as f:
    for ln in f:
        m = TS.match(ln.rstrip())
        if not m:
            continue
        ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        msg = m.group(2)
        if "trigger=render-start" in msg:
            ev.append((ts, "render"))
        elif "WEDGED verdict" in msg:
            ev.append((ts, "wedge"))
        elif "DUD:" in msg:
            mm = re.search(r"DUD: ([\d.]+) TFLOP", msg)
            if mm:
                ev.append((ts, "dud", float(mm.group(1))))

fig, axes = plt.subplots(2, 1, figsize=(11, 8), gridspec_kw={"height_ratios": [1.2, 1]})

# --- Panel 1: render-starts + wedges within each boot (churn timeline) ---
ax = axes[0]
renders = [(e[0], boot_of(e[0])) for e in ev if e[1] == "render" and boot_of(e[0])]
wedges = [(e[0], boot_of(e[0])) for e in ev if e[1] == "wedge" and boot_of(e[0])]
boots_used = sorted({b for _, b in renders})
ylab = []
for yi, b in enumerate(boots_used):
    xs = [(t - b).total_seconds() / 3600.0 for t, bb in renders if bb == b]
    ax.plot(xs, [yi] * len(xs), "o", color="#4a90d9", ms=4, alpha=0.7,
            label="render-start" if yi == 0 else None)
    wx = [(t - b).total_seconds() / 3600.0 for t, bb in wedges if bb == b]
    ax.plot(wx, [yi] * len(wx), "X", color="#d94a4a", ms=12,
            label="WEDGE verdict" if yi == 0 else None)
    ylab.append(b.strftime("%m-%d %H:%M"))
ax.set_yticks(range(len(boots_used)))
ax.set_yticklabels(ylab, fontsize=8)
ax.set_xlabel("hours since boot")
ax.set_title("RENDER regime: wedges (X) arrive only after sustained churn — every fresh-boot first render clean (7/7)")
ax.legend(loc="center right", fontsize=8)
ax.grid(axis="x", alpha=0.3)

# --- Panel 2: dud spawns vs boot age (training lottery) ---
ax = axes[1]
duds = [((e[0] - boot_of(e[0])).total_seconds() / 3600.0, e[2]) for e in ev
        if e[1] == "dud" and boot_of(e[0])]
ax.plot([d[0] for d in duds], [d[1] for d in duds], "o", color="#c48a2a", ms=4, alpha=0.6)
ax.axhline(14, color="#888", ls="--", lw=1)
ax.text(0.2, 14.3, "flip boundary ~14 TFLOP/s", fontsize=8, color="#666")
ax.set_xlabel("hours since boot at spawn time")
ax.set_ylabel("spawn speed (TFLOP/s)")
ax.set_title("TRAINING regime: born-degraded spawns occur at EVERY boot age — a lottery, not aging (reboot never was the cure)")
ax.grid(alpha=0.3)

plt.tight_layout()
out = os.path.join(SP, "exhibit_two_phenomena.png")
plt.savefig(out, dpi=110)
print("saved", out)
