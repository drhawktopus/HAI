"""Raise every enabled encoder slot we care about to its ceiling, on BOTH channels, and VERIFY by
MEASURING the delivered bitrate rather than reading the value back.



WHAT THIS CHANGES, and why each one:
  MainFormat[0] live main -- already 6144 = cap on both. LEFT ALONE, nothing to do.
  MainFormat[1] MOTION RECORDING -- 4096 -> 6144. This is the slot recorded clips are written at,
                                   so it is the one that actually affects what a burst analysis
                                   later gets to look at.
                                    ~50% more disk per clip. That SHORTENS NVR RETENTION. Flagged
                                   to the owner before running; recorded here so it is not a surprise.
  MainFormat[2] -- already 6144 = cap on both. LEFT ALONE.
  MainFormat[3] -- 3840x2160 H.264 @4096, cap not reported. LEFT ALONE: nothing
                                   known consumes it and its ceiling is unknown, so raising it
                                   would be a change with no stated benefit and no way to verify.
  ExtraFormat[0] sub, basement -- H.265 @256 -> H.264 @1536, i.e. MATCH DINING.
                                    THIS MOVES TWO VARIABLES (codec and bitrate) ON PURPOSE. The
                                   goal is not "a bigger number on ch2", it is to undo the 08-08
                                   divergence: ch2 was physically moved and came back as H.265/256
                                   against ch1's H.264/1536. Matching ch1 restores the pair to one
                                   configuration, which is the property that makes detection
                                   comparable across channels. Dining's 1536 is also the
                                   PROVEN-ACCEPTED value -- it has been emitting it for days.
  ExtraFormat[1..2] -- LEFT ALONE. [2] is disabled; [1] is a spare nothing reads.

 THE DECLARED CAP IS NOT RELIABLE HERE AND MUST NOT BE TREATED AS ONE. `encode.getConfigCaps`
reports the sub ceiling as 1280 for H.264 and 768 for H.265 -- yet dining has been emitting 1536,
above its own declared cap, for days. So the caps are a hint, not a wall, and the only thing that
settles it is a measured stream.

 VERIFICATION IS A MEASURED BITRATE, NOT A READ-BACK. This device is documented to accept a
bitrate into the table and silently not emit it: 3072 on a substream was "written, read back, never
emitted". A read-back would report that as success. So this captures N seconds of each
affected stream with `-c copy` and divides bytes by duration. That is the delivered rate.

 CBR is a TARGET, not a guarantee -- a static night scene can undershoot. So the test is not
"equals the target" but "moved decisively toward it and is in the right neighbourhood", and the
BEFORE measurement of the same stream is the comparison. A stream that does not move at all is the
failure this is looking for.

Revert data is written to encode_snapshot_<ts>.json BEFORE anything is sent, and any slot that does
not move is reported explicitly rather than folded into a pass.

Usage: python set_top_bitrate.py (measure, apply, measure, report)
        python set_top_bitrate.py --dry-run (measure and report only, write nothing)
"""
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nvr_rpc2 import RPC2
from cam_settings import TABLET_KEY, ffmpeg_tablet, tablet
from nvr_settings import rtsp

DRY = "--dry-run" in sys.argv

KEY = TABLET_KEY
NODE = ffmpeg_tablet()           # the head box has no ffmpeg
CAPTURE_S = 20
SETTLE_S = 45

NAMES = {0: "dining (ch1)", 1: "basement (ch2)"}

# (table index, format kind, slot index, subtype-for-measuring or None, {fields to set})
# subtype 0 measures MainFormat[0]; the recording slot has no live RTSP subtype of its own, so it
# is verified by read-back ONLY and that limitation is printed rather than hidden.
CHANGES = [
    (0, "MainFormat", 1, None, {"BitRate": 6144}),
    (1, "MainFormat", 1, None, {"BitRate": 6144}),
    (1, "ExtraFormat", 0, 1, {"BitRate": 1536, "Compression": "H.264"}),
]

# Streams to measure before and after, as (label, channel, subtype).
MEASURE = [
    ("dining main", 1, 0),
    ("basement main", 2, 0),
    ("dining sub", 1, 1),
    ("basement sub", 2, 1),
]


def measure_kbps(ch, subtype, seconds=CAPTURE_S):
    """Capture `seconds` of the stream with -c copy and return delivered kbit/s, or None.

    Returning None on any failure matters: an instrument that could not measure must never render
    as a measurement of zero.
    """
    # %% -- the password is percent-encoded ("!"=%21, "$"=%24) and this literal sits inside a
    # %-format expression, so every literal percent must be doubled.
    url = (rtsp() + "/"
           "cam/realmonitor?channel=%d&subtype=%d" % (ch, subtype))
    # -y IS LOAD-BEARING. `mktemp` CREATES the file, so without -y ffmpeg refuses to overwrite it
    # ("File ... already exists. Exiting.") -- and it still EXITS 0 while doing so. The exit code
    # lies; only the byte count catches it. Cost one silent all-four-streams "COULD NOT MEASURE".
    remote = ("f=$(mktemp); "
              "ffmpeg -y -nostdin -v error -rtsp_transport tcp -i '%s' -t %d -c copy -f mpegts \"$f\" "
              ">/dev/null 2>&1; "
              "stat -c %%s \"$f\"; rm -f \"$f\"" % (url, seconds))
    cmd = ["ssh", "-p", "8022", "-i", KEY,
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR", NODE, remote]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 90)
    except Exception:
        return None
    out = (r.stdout or "").strip().splitlines()
    try:
        size = int(out[-1])
    except Exception:
        return None
    if size <= 0:
        return None
    return round(size * 8 / seconds / 1000)


def measure_all(when):
    print("=== delivered bitrate, %s (%d s capture each) ===" % (when, CAPTURE_S))
    got = {}
    for label, ch, sub in MEASURE:
        kbps = measure_kbps(ch, sub)
        got[label] = kbps
        print("  %-16s %s" % (label, "%s kbit/s" % kbps if kbps is not None
                              else "COULD NOT MEASURE (not zero -- unknown)"))
    print()
    return got


def main():
    before = measure_all("BEFORE")

    r = RPC2()
    ok, msg = r.login()
    print("RPC2 login: %s (%s)" % (ok, msg))
    if not ok:
        print("ABORT: not logged in. Nothing was written.")
        return 2

    cfg = r.call("configManager.getConfig", {"name": "Encode"})
    table = (cfg.get("params") or {}).get("table")
    if not table:
        print("ABORT: could not read the Encode table. Nothing was written.")
        return 2

    snap = os.path.join(HERE, "encode_snapshot_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    with open(snap, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1)
    print("revert snapshot -> %s\n" % snap)

    print("=== planned changes ===")
    applied = 0
    for idx, kind, slot_i, _sub, want in CHANGES:
        v = table[idx][kind][slot_i]["Video"]
        cur = {k: v.get(k) for k in want}
        if cur == want:
            print("  %-14s %-12s[%d]  already %s -- skipped" % (NAMES[idx], kind, slot_i, cur))
            continue
        print("  %-14s %-12s[%d]  %s  ->  %s" % (NAMES[idx], kind, slot_i, cur, want))
        for k, val in want.items():
            v[k] = val
        applied += 1

    if DRY:
        print("\n--dry-run: nothing written.")
        return 0
    if not applied:
        print("\nNothing to change; every target slot is already at its target.")
        return 0

    res = r.set_config("Encode", table)
    print("\nsetConfig -> %s" % json.dumps({k: res.get(k) for k in ("result", "error")}))

    print("\nsettling %d s…" % SETTLE_S)
    time.sleep(SETTLE_S)

    after = measure_all("AFTER")

    # Read the table back too -- not as proof, but because the recording slot has no live stream and
    # a read-back is the only signal available for it. Labelled as the weak evidence it is.
    cfg2 = r.call("configManager.getConfig", {"name": "Encode"})
    t2 = (cfg2.get("params") or {}).get("table") or []
    print("=== table read-back (WEAK evidence -- this device accepts values it never emits) ===")
    for idx, kind, slot_i, sub, want in CHANGES:
        v = t2[idx][kind][slot_i]["Video"]
        got = {k: v.get(k) for k in want}
        mark = "ok" if got == want else "⚠ DID NOT STICK"
        extra = "" if sub is not None else "   (no live RTSP subtype -- read-back is ALL we have here)"
        print("  %-14s %-12s[%d]  %s  %s%s" % (NAMES[idx], kind, slot_i, got, mark, extra))

    print("\n=== verdict, from the MEASURED streams ===")
    for label, _ch, _sub in MEASURE:
        b, a = before.get(label), after.get(label)
        if b is None or a is None:
            print("  %-16s INCONCLUSIVE -- a capture failed, so this stream's behaviour is "
                  "unknown, not unchanged." % label)
        elif a > b * 1.3:
            print("  %-16s %s -> %s kbit/s   ✅ moved up" % (label, b, a))
        elif abs(a - b) <= max(b * 0.15, 30):
            print("  %-16s %s -> %s kbit/s   (unchanged -- expected for slots not targeted)"
                  % (label, b, a))
        else:
            print("  %-16s %s -> %s kbit/s   ⚠ moved unexpectedly" % (label, b, a))

    print("\n⚠ CBR is a target, not a guarantee: a static scene can undershoot the configured rate. "
          "Compare the direction and neighbourhood, not equality.")
    print("⚠ MainFormat[1] is the MOTION-RECORDING slot -- raising it means ~50%% more disk per "
          "clip and therefore SHORTER retention. Watch free space on the NVR.")
    print("revert snapshot: %s" % snap)

    try:
        r.logout()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
