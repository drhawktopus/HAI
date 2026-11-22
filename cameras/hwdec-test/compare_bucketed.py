"""Compare decode arms WITHIN matched detection load.

WHY THIS EXISTS: the naive before/after produced two different wrong answers in one
night. First it moved two variables (who decodes AND which stream). Then, with the stream held
fixed, it compared a BUSY arm against an IDLE one -- `det 4.4-5.0` every round against `det 0.0`
every round -- and attributed the whole gap to decode. Detection costs CPU too.

Detection load is real-world motion and cannot be controlled, so it is matched AFTER THE FACT:
sample each arm long enough to span quiet and busy, bucket the rounds by `detection_fps`, and only
compare arms inside the same bucket.

 A bucket with rounds from only ONE arm proves nothing and is reported as UNPAIRED rather than
quietly dropped -- an empty comparison and a null result must not look the same.

Usage: python compare_bucketed.py cost_armC_long.json cost_armB_long.json
"""
import json
import statistics
import sys

# Buckets chosen from the observed range: dining sits at either ~0 (nothing moving) or ~5 (an
# object actively tracked, Frigate detecting every frame). The middle bucket catches transitions.
BUCKETS = [("idle   det<0.5", 0.0, 0.5),
           ("light  0.5-2.5", 0.5, 2.5),
           ("busy   det>2.5", 2.5, 99.0)]


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    rows = []
    for r in d["rows"]:
        if "error" in r:
            continue
        v = r["cams"]["dining_room"]
        rows.append({"cpu": r["frigate_cpu_pct_of_one_core"],
                     "det": v["detection_fps"], "skip": v["skipped_fps"],
                     "proc": v["process_fps"]})
    return d.get("label", path), rows


def summ(vals):
    if not vals:
        return None
    return (statistics.median(vals), min(vals), max(vals), len(vals))


def fmt(s):
    return "  --  " if not s else "%5.1f [%4.1f-%4.1f] n=%d" % s


def main():
    arms = [load(p) for p in sys.argv[1:]]
    if len(arms) < 2:
        print("need two arm files")
        return

    print("%-16s | %-28s | %-28s" % ("bucket", arms[0][0], arms[1][0]))
    print("-" * 80)
    for name, lo, hi in BUCKETS:
        cells = []
        for _, rows in arms:
            sel = [r for r in rows if lo <= r["det"] < hi]
            cells.append((summ([r["cpu"] for r in sel]),
                          summ([r["skip"] for r in sel]),
                          summ([r["proc"] for r in sel])))
        a, b = cells
        print("%-16s | cpu %s | cpu %s" % (name, fmt(a[0]), fmt(b[0])))
        print("%-16s | skp %s | skp %s" % ("", fmt(a[1]), fmt(b[1])))
        print("%-16s | prc %s | prc %s" % ("", fmt(a[2]), fmt(b[2])))
        if not a[0] or not b[0]:
            print("%-16s | >> UNPAIRED: only one arm has rounds here, proves nothing" % "")
        else:
            am, _, amax, _ = a[0]
            bm, bmin, _, _ = b[0]
            sep = (amax < bmin) or (b[0][2] < a[0][1])
            print("%-16s | >> delta %+.1f %% of one core   ranges %s"
                  % ("", bm - am, "SEPARATED" if sep else "OVERLAP - not clean"))
        print("-" * 80)


if __name__ == "__main__":
    main()
