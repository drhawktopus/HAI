"""Summarize a GGML_RPC_TRACE log: per endpoint, the compute windows (GRAPH_COMPUTE/RECOMPUTE sent -> the next GET
answer from that endpoint, which arrives only after the queued compute ran), and how much the endpoints' windows
overlap in the LAST timed pass (after llama-bench's warmup). Prints a few lines, never the raw trace."""
import re
import sys

ev = []
for ln in open(sys.argv[1], encoding="utf-8", errors="replace"):
    m = re.match(r"RPCTRACE (\d+) (\S+) (begin|end) (\d+)", ln)
    if m:
        ev.append((int(m.group(1)) / 1e6, m.group(2), m.group(3), int(m.group(4))))
assert ev, "zero RPCTRACE lines - the trace was not on, or the log is wrong"
eps = sorted({e[1] for e in ev})
windows = {ep: [] for ep in eps}
pending = {ep: None for ep in eps}
for t, ep, kind, cmd in ev:
    if kind == "begin" and cmd in (10, 16) and pending[ep] is None:
        pending[ep] = t
    elif kind == "end" and cmd == 8 and pending[ep] is not None:
        windows[ep].append((pending[ep], t))
        pending[ep] = None
for ep in eps:
    w = windows[ep]
    print("%s: %d compute windows, busy %.1f s" % (ep, len(w), sum(b - a for a, b in w)))
if len(eps) == 2:
    a, b = windows[eps[0]], windows[eps[1]]
    half = len(a) // 2           # the second half of the windows = the timed pass, not the warmup
    a, b = a[half:], b[len(b) // 2:]
    t0 = min(x for x, _ in a + b)
    t1 = max(y for _, y in a + b)
    both = 0.0
    for x0, x1 in a:
        for y0, y1 in b:
            both += max(0.0, min(x1, y1) - max(x0, y0))
    busy_a = sum(y - x for x, y in a)
    busy_b = sum(y - x for x, y in b)
    print("timed pass %.1f s: A busy %.1f s, B busy %.1f s, BOTH at once %.1f s (%.0f%% of the pass)"
          % (t1 - t0, busy_a, busy_b, both, 100.0 * both / max(1e-9, t1 - t0)))
    print("first windows (s from pass start): A %s | B %s" % (
        [(round(x - t0, 2), round(y - t0, 2)) for x, y in a[:4]], [(round(x - t0, 2), round(y - t0, 2)) for x, y in b[:4]]))
