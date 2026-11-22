r"""Whole-box WALL energy per menu answer - the menu's own spec: price the TOTAL draw of the box at the wall while it answers (some day the boxes sleep
between jobs, so the idle share is a real cost), and show the extra-over-idle and the card meter beside it.

Each answer carries its own window (`t0`/`t1` on the delivered row). Per box:
  MEASURED the box's own wall log covers >= COVER of the window: the log's mean watts x wall_s.
            mothership CyberPower GX1500U, `<FOUNDRY_ROOT>\var\state\telemetry\ups_power.csv` - everything on its battery outlets
            bighonker APC ES 750G, `ups_load_pct` x 4.5 W in its own `gpu_telemetry-<day>.csv`
            softserve APC NS 1500M2, `ups_load_pct` x 9 W in Softserve's own telemetry, MINUS rigby (same APC): rigby's
                        idle for its era, plus its working extra while a Tolkien order ran (`RIGBY` below)
  MODELLED otherwise: the box's fitted line wall_W = a + k x card_W (+ c x cpu_pct on Mothership, the one box whose
            telemetry records CPU), fitted on 60 s bins wherever the box logged both (`calibrate`, written to
            wall_calibration.json with its R2, n and span). The card watts come from the box's own telemetry over the
            window when it has rows there, else from the answer's own meter (`gpu_j` / `wall_s`).
`wall_extra_j` = wall_j - the box's idle watts x wall_s (idle = the fitted line at the box's idle card watts and CPU).

The logs reach Softserve through `sync`: one ssh call per box running `wall_tail.py`, which reads each log only from the
byte this cache already holds (a growing log costs its new bytes; never a whole-log read on a working box). A box that
cannot be reached keeps its cache: its newest answers are MODELLED until the next sync, never priced as zero.

    python menu_wall.py sync pull the new bytes of every box's wall log
    python menu_wall.py calibrate fit each box's line, validate it on the measured answers, write the calibration"""
import array
import bisect
import csv
import datetime as dt
import glob
import json
import os
import pickle
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_paths as MPATH  # noqa: E402
LS = MPATH.LS                 # the boxes the wall reads, the shares and this box's telemetry are settings

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "wall_cache")
CALIBRATION = os.path.join(HERE, "wall_calibration.json")
TAIL = os.path.join(HERE, "wall_tail.py")
OUTBOX = os.path.join(LS.setting("SSD_SHARED_UNC"), "tabfoundry-jobs", "outbox")
SS_TELEM = LS.setting("WALL_LOCAL_TELEMETRY")   # this box's own telemetry folder
COVER = 0.9          # a log must cover this share of an answer's window to MEASURE it
GAP_S = 30.0         # a log row speaks for at most this long (a longer silence is a gap, not a reading)
# The boxes the wall reads and how to reach each one: WALL_BOXES (a setting, JSON) = {box: {"python": the interpreter
# on that box that runs wall_tail.py, then EITHER "files": [telemetry csv paths] OR "glob": a pattern of dated
# telemetry files with "since": the first day worth reading (YYYYMMDD); "parse": "ups" (a UPS power log plus a card+CPU
# telemetry log) or "telemetry" (dated card logs carrying the APC's load percent, priced by "ups_w_per_pct" = NOMPOWER /
# 100); "local": true = this box, read from WALL_LOCAL_TELEMETRY and never synced; "subtract_tablet_head": true = the
# tablet head's plug is on this box's UPS and its watts come off}}. Empty = the wall reads no box.
BOXES = LS.setting_json("WALL_BOXES", {}) or {}
# A Fellowship tablet, priced from its port's SPEC: every tablet charges
# from a standard USB data port, `current_max` 500000 uA at 5 V (Tablet-Architecture-Topology-Fleet-Status § "Power
# delivery to the tablets"), and the input limit cannot be raised in software - so over time a tablet draws at most 2.5 W
# whole-device (a burst above it comes from the battery, which the same port refills). A CEILING; the one measurement is a
# FLOOR (~2 W extra per working tablet, 34-Hard-Bench-Order-Spec, partly paid from batteries).
TABLET_PORT_W = 2.5
UPS_W_PER_PCT = {b: s["ups_w_per_pct"] for b, s in BOXES.items() if "ups_w_per_pct" in s}   # NOMPOWER / 100 per box
# rigby on Softserve's APC, by era (34-Hard-Bench-Order-Spec § rigby energy): its own plug idles 41-46 W and 46.9 W (the
# plug's export); the GTX 1060's 600 W supply joined the APC later (+32.1 W step, R2 0.92); working adds ~55 W before
# the 1060 (97-101 W at the plug) and 78 W on the x16 riser.
RIGBY = ({"since": "2026-01-01", "idle_w": 44.0, "work_w": 55.0},
         {"since": "2026-10-13", "idle_w": 76.0, "work_w": 78.0},
         {"since": "2026-10-26T14:45", "idle_w": 76.0, "work_w": 78.0})


def _epoch_local(s):
    """A naive `YYYY-MM-DD HH:MM:SS` telemetry stamp is Softserve-local (Central) time, as every box's logger writes."""
    return dt.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").timestamp()


# --- sync ----------------------------------------------------------------------------------------------------------
def _ssh(box, args, timeout):
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", box, '"%s" - %s' % (
        BOXES[box]["python"], " ".join('"%s"' % a for a in args))]
    with open(TAIL, "rb") as f:
        p = subprocess.run(cmd, stdin=f, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError("ssh %s rc=%s: %s" % (box, p.returncode, p.stderr.decode(errors="replace")[-300:]))
    return p.stdout


def _frames(blob):
    """(path, offset, data, size) for each frame wall_tail.py printed; asserts the stream parsed to the end."""
    i, out = 0, []
    while i < len(blob):
        nl = blob.index(b"\n", i)
        path, off, n, size = blob[i:nl].decode().split("\t")
        n = int(n)
        out.append((path, off, blob[nl + 1:nl + 1 + n], int(size)))
        i = nl + 1 + n
    assert i == len(blob), "wall_tail stream ended mid-frame"
    return out


def _append(local, off, data, size):
    have = os.path.getsize(local) if os.path.exists(local) else 0
    if size < 0:
        return 0
    if int(off) != have:                       # the remote file shrank or was replaced: start the copy again
        assert int(off) == 0, "frame offset %s for a %d-byte cache" % (off, have)
        open(local, "wb").close()
    with open(local, "ab") as f:
        f.write(data)
    return len(data)


def sync(boxes=None, timeout=900, log=print):
    """Pull every box's new log bytes. Returns {box: bytes pulled or the error text}; a failure keeps the cache."""
    if not BOXES:
        raise SystemExit("no WALL_BOXES setting: the boxes the wall reads (python, files or glob+since) go in %s"
                         % LS.settings_file())
    res = {}
    for box in boxes or BOXES:
        spec = BOXES[box]
        if spec.get("local"):
            continue                               # this box: read in place, nothing to pull
        d = os.path.join(CACHE, box)
        os.makedirs(d, exist_ok=True)
        try:
            if "glob" in spec:
                (_, _, listing, _), = _frames(_ssh(box, [spec["glob"] + "|LIST"], 60))
                remote = dict(l.split("\t") for l in listing.decode().splitlines())
                rdir = os.path.dirname(spec["glob"])
                want = [n for n, sz in sorted(remote.items())
                        if n.split("-")[-1][:8] >= spec["since"] and
                        int(sz) != (os.path.getsize(os.path.join(d, n)) if os.path.exists(os.path.join(d, n)) else 0)]
                paths = [rdir + "\\" + n for n in want]
            else:
                paths = list(spec["files"])
            args = []
            for p in paths:
                local = os.path.join(d, p.rsplit("\\", 1)[-1])
                args.append("%s|%d" % (p, os.path.getsize(local) if os.path.exists(local) else 0))
            n = 0
            for i in range(0, len(args), 8):
                for path, off, data, size in _frames(_ssh(box, args[i:i + 8], timeout)):
                    n += _append(os.path.join(d, path.rsplit("\\", 1)[-1]), off, data, size)
            res[box] = n
            log("wall sync %s: %d files checked, %d new bytes" % (box, len(args), n))
        except Exception as e:                                          # noqa: BLE001  the cache stays; rows model
            res[box] = "%s: %s" % (type(e).__name__, e)
            log("wall sync %s FAILED (cache kept, its newest answers are modelled): %s" % (box, res[box]))
    return res


# --- series --------------------------------------------------------------------------------------------------------
class Series:
    """A sorted (epoch, value) step series; each reading speaks until the next one or for GAP_S, whichever is first."""

    def __init__(self, pts=None, t=None, v=None):
        if pts is not None:
            pts.sort()
            t, v = [p[0] for p in pts], [p[1] for p in pts]
        self.t, self.v = t if t is not None else [], v if v is not None else []

    def __len__(self):
        return len(self.t)

    def window(self, a, b):
        """(time-weighted mean over [a, b], covered seconds) - covered < b - a where the log was silent."""
        if not self.t or b <= a:
            return None, 0.0
        i = max(0, bisect.bisect_right(self.t, a) - 1)
        acc = cov = 0.0
        while i < len(self.t) and self.t[i] < b:
            nxt = self.t[i + 1] if i + 1 < len(self.t) else self.t[i] + GAP_S
            lo, hi = max(a, self.t[i]), min(b, nxt, self.t[i] + GAP_S)
            if hi > lo:
                acc += self.v[i] * (hi - lo)
                cov += hi - lo
            i += 1
        return (acc / cov if cov else None), cov


def _x_ups(r, ix, st):                     # Mothership's ups_power.csv
    st["wall"].append((float(r[ix["epoch"]]), float(r[ix["active_w"]])))


def _x_cpu_telemetry(r, ix, st):           # Mothership's gpu_telemetry.csv (card + CPU, no UPS)
    t = _epoch_local(r[ix["ts"]])
    card, cpu = float(r[ix["power_w"]]), float(r[ix["cpu_pct"]])
    st["card"].append((t, card))
    st["cpu"].append((t, cpu))


def _x_ups_telemetry(per):                 # BigHonker's / Softserve's gpu_telemetry-<day>.csv (card + the APC's load %)
    def x(r, ix, st):
        t = _epoch_local(r[ix["ts"]])
        st["card"].append((t, float(r[ix["power_w"]])))
        if "ups_load_pct" in ix and r[ix["ups_status"]].startswith("ONLINE") and r[ix["ups_load_pct"]]:
            st["wall"].append((t, float(r[ix["ups_load_pct"]]) * per))
    return x


def _parsed(box, path, extract):
    """{'wall','card','cpu': [(t, v)]} parsed from one log, cached beside the sync cache and advanced over the new bytes
    only (a log that shrank is parsed again). A header row mid-file (a logger schema bump) re-keys the columns."""
    key = os.path.join(CACHE, "parsed", "%s__%s.pkl" % (box, os.path.basename(path)))
    size = os.path.getsize(path)
    st = None
    if os.path.exists(key):
        try:
            with open(key, "rb") as f:
                st = pickle.load(f)
        except Exception:                                               # noqa: BLE001  a torn cache is re-parsed
            st = None
    if st is None or st["offset"] > size:
        st = {"offset": 0, "ix": None, "wall": [], "card": [], "cpu": []}
    if st["offset"] < size:
        with open(path, "rb") as f:
            f.seek(st["offset"])
            data = f.read(size - st["offset"])
        end = data.rfind(b"\n") + 1                                     # a line still being written waits for next time
        ix = st["ix"]
        for r in csv.reader(data[:end].decode("utf-8", "replace").splitlines()):
            if r and r[0] == "ts":
                ix = {k: i for i, k in enumerate(r)}
            elif r and ix is not None:
                try:
                    extract(r, ix, st)
                except (ValueError, IndexError, KeyError):
                    pass
        st["offset"] += end
        st["ix"] = ix
        os.makedirs(os.path.dirname(key), exist_ok=True)
        with open(key + ".tmp", "wb") as f:
            pickle.dump(st, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(key + ".tmp", key)
    return st


def _merge(parts):
    """One sorted Series from per-file pieces (files need not sort by name: a day's `_prev-schema_` part is its EARLIER
    hours)."""
    parts = sorted((p for p in parts if p), key=lambda p: p[0][0])
    t, v = array.array("d"), array.array("d")
    for p in parts:
        if t and p[0][0] < t[-1]:                                       # overlapping pieces: sort the whole lot once
            pts = sorted(x for q in parts for x in q)
            return Series(t=array.array("d", (x[0] for x in pts)), v=array.array("d", (x[1] for x in pts)))
        t.extend(x[0] for x in p)
        v.extend(x[1] for x in p)
    return Series(t=t, v=v)


_LOADED = {}


def load(box):
    """{'wall': Series, 'card': Series, 'cpu': Series or None} for one box, from the synced cache (or Softserve's own
    telemetry). Cached per process."""
    if box in _LOADED:
        return _LOADED[box]
    spec = BOXES.get(box) or {}
    if spec.get("parse") == "ups":
        srcs = [(os.path.join(CACHE, box, "ups_power.csv"), _x_ups),
                (os.path.join(CACHE, box, "gpu_telemetry.csv"), _x_cpu_telemetry)]
    else:
        files = (sorted(glob.glob(os.path.join(SS_TELEM, "gpu_telemetry-2026*.csv"))) if spec.get("local") else
                 sorted(glob.glob(os.path.join(CACHE, box, "gpu_telemetry-*.csv"))))
        srcs = [(p, _x_ups_telemetry(UPS_W_PER_PCT[box])) for p in files]
    got = [_parsed(box, p, x) for p, x in srcs if os.path.exists(p)]
    wall = [g["wall"] for g in got]
    if spec.get("subtract_tablet_head"):
        rig = rigby_watts()
        wall = [[(t, w - rig(t)) for t, w in part] for part in wall]
    cpu = [g["cpu"] for g in got if g["cpu"]]
    _LOADED[box] = {"wall": _merge(wall), "card": _merge([g["card"] for g in got]), "cpu": _merge(cpu) if cpu else None}
    return _LOADED[box]


_RIGBY_BUSY = None


def rigby_busy():
    """Sorted (start, end) of every Tolkien order window (its delivered result: `finished` - `wall_s`), merged."""
    global _RIGBY_BUSY
    if _RIGBY_BUSY is None:
        spans = []
        for f in glob.glob(os.path.join(OUTBOX, "*.results.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    d = json.load(fh)
                fin = d["finished"]
                end = dt.datetime.fromisoformat(fin).timestamp() if isinstance(fin, str) else float(fin)
                spans.append((end - float(d["wall_s"]), end))
            except Exception:                                           # noqa: BLE001  not a timed delivery
                continue
        spans.sort()
        merged = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        _RIGBY_BUSY = [tuple(s) for s in merged]
    return _RIGBY_BUSY


def rigby_era(t):
    era = RIGBY[0]
    for e in RIGBY:
        if t >= dt.datetime.fromisoformat(e["since"]).timestamp():
            era = e
    return era


def rigby_watts():
    busy = rigby_busy()
    starts = [a for a, _ in busy]

    def at(t):
        era = rigby_era(t)
        i = bisect.bisect_right(starts, t) - 1
        working = i >= 0 and busy[i][0] <= t < busy[i][1]
        return era["idle_w"] + (era["work_w"] if working else 0.0)
    return at


# --- calibration ---------------------------------------------------------------------------------------------------
def _ols(X, y):
    k = len(X[0])
    m = [[0.0] * (k + 1) for _ in range(k)]
    for xi, yi in zip(X, y):
        for i in range(k):
            m[i][k] += xi[i] * yi
            for j in range(k):
                m[i][j] += xi[i] * xi[j]
    for i in range(k):
        p = max(range(i, k), key=lambda r: abs(m[r][i]))
        m[i], m[p] = m[p], m[i]
        for r in range(k):
            if r != i:
                f = m[r][i] / m[i][i]
                m[r] = [a - f * b for a, b in zip(m[r], m[i])]
    beta = [m[i][k] / m[i][i] for i in range(k)]
    mean = sum(y) / len(y)
    sst = sum((v - mean) ** 2 for v in y)
    ssr = sum((v - sum(b * x for b, x in zip(beta, xi))) ** 2 for xi, v in zip(X, y))
    return beta, 1 - ssr / sst


def bins(box, width=60.0, exclude_rigby_busy=False):
    """60 s bins where the box logged wall AND card (and CPU where it records it) for >= COVER of the bin."""
    s = load(box)
    if not len(s["wall"]):
        return []
    busy = rigby_busy() if exclude_rigby_busy else []
    starts = [a for a, _ in busy]
    out = []
    t = s["wall"].t[0] - s["wall"].t[0] % width
    end = s["wall"].t[-1]
    while t < end:
        b = t + width
        w, cw = s["wall"].window(t, b)
        if w is not None and cw >= COVER * width:
            c, cc = s["card"].window(t, b)
            u, cu = s["cpu"].window(t, b) if s["cpu"] is not None else (None, width)
            i = bisect.bisect_right(starts, b) - 1
            hit = i >= 0 and busy[i][1] > t
            if c is not None and cc >= COVER * width and cu >= COVER * width and not hit:
                out.append((t, w, c, u))
        t = b
    return out


def calibrate(log=print):
    cal = {"built": dt.datetime.now().isoformat(timespec="seconds"), "boxes": {}}
    for box in BOXES:
        bs = bins(box, exclude_rigby_busy=bool(BOXES[box].get("subtract_tablet_head")))
        if len(bs) < 200:
            log("%s: only %d calibration bins - not fitted" % (box, len(bs)))
            continue
        use_cpu = bs[0][3] is not None
        X = [(1.0, c, u) if use_cpu else (1.0, c) for _, _, c, u in bs]
        beta, r2 = _ols(X, [w for _, w, _, _ in bs])
        cards = sorted(c for _, _, c, _ in bs)
        idle_card = cards[len(cards) // 20]                              # the box's quiet card (5th percentile)
        idle_cpu = sorted(u for *_, u in bs)[len(bs) // 20] if use_cpu else None
        idle_w = beta[0] + beta[1] * idle_card + (beta[2] * idle_cpu if use_cpu else 0.0)
        cal["boxes"][box] = {"a": round(beta[0], 2), "k_card": round(beta[1], 4),
                             "c_cpu": round(beta[2], 4) if use_cpu else None, "r2": round(r2, 4), "bins": len(bs),
                             "span": [dt.datetime.fromtimestamp(bs[0][0]).isoformat(timespec="minutes"),
                                      dt.datetime.fromtimestamp(bs[-1][0]).isoformat(timespec="minutes")],
                             "card_range_w": [round(cards[0], 1), round(cards[-1], 1)],
                             "idle_card_w": round(idle_card, 1), "idle_cpu_pct": idle_cpu, "idle_w": round(idle_w, 1),
                             # the CPU term is identified only by minutes of heavy CPU: a fit without
                             # 10-18's (~99 % CPU) work put it anywhere from 0.87 to 1.39 W/% and read those answers
                             # 8 % high; with them in, a holdout of the answers' own minutes reads 0.996
                             "cpu_over_30_bins": sum(1 for *_, u in bs if u > 30) if use_cpu else None}
        log("%s: wall = %.1f + %.3f x card%s  R2 %.3f  bins %d  idle %.1f W (card %.1f W%s)" % (
            box, beta[0], beta[1], (" + %.3f x cpu%%" % beta[2]) if use_cpu else "", r2, len(bs), idle_w, idle_card,
            (", cpu %.0f%%" % idle_cpu) if use_cpu else ""))
    return cal


# --- pricing -------------------------------------------------------------------------------------------------------
_CAL = None


def calibration():
    global _CAL
    if _CAL is None:
        try:
            with open(CALIBRATION, encoding="utf-8") as f:
                _CAL = json.load(f)["boxes"]
        except FileNotFoundError:
            print("menu_wall: NO %s - unmeasured answers stay unpriced until `menu_wall.py calibrate` runs" % CALIBRATION)
            _CAL = {}
    return _CAL


def price(box, t0, t1, card_j=None, wall_s=None):
    """{'wall_j', 'wall_extra_j', 'card_j', 'wall_src'} for one answer on `box` over [t0, t1]; wall_j None when the box
    has neither a covering log nor a fitted line (never 0)."""
    out = {"wall_j": None, "wall_extra_j": None, "card_j": card_j, "wall_src": ""}
    if box not in ("mothership", "bighonker", "softserve"):
        return out
    cal = calibration().get(box)
    try:
        t0, t1 = float(t0), float(t1)
    except (TypeError, ValueError):
        t0 = t1 = None
    try:
        dur = float(wall_s) if wall_s not in (None, "") else (t1 - t0)
    except TypeError:
        return out
    if t0 is None or t1 <= t0:                       # no window on the row: its own meter on the box's line, CPU at idle
        if cal and card_j not in (None, "") and dur > 0:
            watts = cal["a"] + cal["k_card"] * float(card_j) / dur + (cal["c_cpu"] or 0.0) * (cal["idle_cpu_pct"] or 0.0)
            out.update(wall_j=watts * dur, wall_extra_j=(watts - cal["idle_w"]) * dur, wall_src="modelled (no window)")
        return out
    if dur <= 0:
        return out
    s = load(box)
    w, cov = s["wall"].window(t0, t1)
    if w is not None and cov >= COVER * (t1 - t0):
        out["wall_j"], out["wall_src"] = w * dur, "measured"
    elif cal:
        c, cc = s["card"].window(t0, t1)
        if c is None or cc < COVER * (t1 - t0):
            if card_j in (None, ""):
                return out
            c = float(card_j) / dur
        watts = cal["a"] + cal["k_card"] * c
        if cal.get("c_cpu") is not None:
            u, cu = s["cpu"].window(t0, t1) if s["cpu"] is not None else (None, 0.0)
            watts += cal["c_cpu"] * (u if u is not None and cu >= COVER * (t1 - t0) else cal["idle_cpu_pct"])
        out["wall_j"], out["wall_src"] = watts * dur, "modelled"
    if out["wall_j"] is not None and cal:
        out["wall_extra_j"] = out["wall_j"] - cal["idle_w"] * dur
    return out


def tablet_j(wall_s, tablets=1):
    """A Fellowship answer's joules at the spec ceiling (TABLET_PORT_W per tablet x its wall), or None without a wall."""
    try:
        return round(float(wall_s) * TABLET_PORT_W * int(tablets), 1)
    except (TypeError, ValueError):
        return None


def fleet_answers(camp=None):
    """(host, row) for every delivered fleet answer with a window - the same submission.json walk build_menu makes."""
    camp = camp or MPATH.CAMPAIGNS
    sys.path.insert(0, HERE)
    import menu_price as MP
    for folder in glob.glob(os.path.join(camp, "hard-bench-*", "fleet*")) + glob.glob(os.path.join(camp, "w1ops-*", "fleet*")):
        sub = os.path.join(folder, "submission.json")
        if not os.path.exists(sub):
            continue
        with open(sub, encoding="utf-8") as f:
            orders = json.load(f).get("orders", {})
        for rec in orders.values():
            out = MP.delivered_out(rec.get("out"))
            if not out:
                continue
            with open(out, encoding="utf-8") as f:
                d = json.load(f)
            for r in d.get("rows") or []:
                if r.get("t0") and r.get("t1"):
                    yield d.get("host", ""), r


def validate(log=print):
    """Positive control: on every answer a wall log MEASURES, what the fitted line would have said instead, and how the
    answer's own card meter compares with the box telemetry's card watts. Returns {box: summary}."""
    import statistics as st
    cal = calibration()
    out = {}
    per = {}
    for host, r in fleet_answers():
        if host not in cal:
            continue
        s = load(host)
        t0, t1 = float(r["t0"]), float(r["t1"])
        if t1 - t0 < 5:
            continue
        w, cov = s["wall"].window(t0, t1)
        if w is None or cov < COVER * (t1 - t0):
            continue
        c, cc = s["card"].window(t0, t1)
        if c is None or cc < COVER * (t1 - t0):
            continue
        model = cal[host]["a"] + cal[host]["k_card"] * c
        if cal[host].get("c_cpu") is not None:
            u, _ = s["cpu"].window(t0, t1)
            model += cal[host]["c_cpu"] * (u if u is not None else cal[host]["idle_cpu_pct"])
        meter = float(r["gpu_j"]) / (t1 - t0) if r.get("gpu_j") not in (None, "") else None
        per.setdefault(host, []).append((model / w, (meter / c) if meter and c else None, w))
    for host, v in per.items():
        ratios = sorted(x[0] for x in v)
        meters = sorted(x[1] for x in v if x[1] is not None)
        q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))]
        out[host] = {"answers": len(v), "model_over_measured_median": round(st.median(ratios), 3),
                     "model_over_measured_p10_p90": [round(q(ratios, .1), 3), round(q(ratios, .9), 3)],
                     "meter_over_telemetry_median": round(st.median(meters), 3) if meters else None,
                     "meter_over_telemetry_p10_p90": [round(q(meters, .1), 3), round(q(meters, .9), 3)] if meters else None,
                     "measured_w_median": round(st.median(x[2] for x in v), 1)}
        log("%s: %d measured answers - model/measured median %.3f (p10-p90 %s), answer meter/telemetry %s, wall %s W" % (
            host, len(v), out[host]["model_over_measured_median"], out[host]["model_over_measured_p10_p90"],
            out[host]["meter_over_telemetry_median"], out[host]["measured_w_median"]))
    assert per, "validate compared ZERO answers - the join is broken, not the model"
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:2] == ["sync"]:
        sync()
    elif sys.argv[1:2] == ["validate"]:
        validate()
    elif sys.argv[1:2] == ["calibrate"]:
        c = calibrate()
        _CAL = c["boxes"]
        for box, v in validate().items():
            c["boxes"][box]["validation"] = v
        tmp = CALIBRATION + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=1)
        os.replace(tmp, CALIBRATION)
        print("wrote", CALIBRATION)
    else:
        print(__doc__)
