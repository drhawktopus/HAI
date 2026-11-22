"""Put BOTH substreams on H.265 at 1536 kbps and measure whether the bitrate survives the codec.



The main streams are already H.265 on both channels; this is only about ExtraFormat[0].

THE ONE REAL DOUBT: `getConfigCaps` declares the sub ceiling as **768 for H.265** and **1280 for
H.264**. Both subs currently run 1536 -- above even the H.264 figure -- and emit it, so the declared
caps are demonstrably soft. But "soft for H.264" is not evidence about H.265, so the question is
whether 1536 survives the codec switch or gets clamped to 768.

H.265 at the same bitrate is better quality per bit, so this is worth having IF the bitrate holds.
If it clamps to ~768, H.264 at 1536 is likely the better deal and this reverts.

Verification is the MEASURED delivered rate. A read-back cannot tell a clamp from a success, and
this device is documented to accept substream bitrates it never emits.

Usage: python set_sub_h265.py apply and measure
        python set_sub_h265.py --revert put both subs back to H.264 @1536
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

REVERT = "--revert" in sys.argv
WANT = {"BitRate": 1536, "Compression": "H.264" if REVERT else "H.265"}

KEY = TABLET_KEY
NODE = ffmpeg_tablet()
CAPTURE_S = 20
SETTLE_S = 45
NAMES = {0: "dining (ch1)", 1: "basement (ch2)"}


def measure_kbps(ch, seconds=CAPTURE_S):
    """Delivered kbit/s of the SUB stream, or None. None never means zero."""
    # %% -- password is percent-encoded and this literal sits inside a %-format expression.
    url = (rtsp() + "/"
           "cam/realmonitor?channel=%d&subtype=1" % ch)
    # -y is load-bearing: mktemp creates the file and ffmpeg refuses to overwrite it, exiting 0.
    remote = ("f=$(mktemp); "
              "ffmpeg -y -nostdin -v error -rtsp_transport tcp -i '%s' -t %d -c copy -f mpegts \"$f\" "
              ">/dev/null 2>&1; stat -c %%s \"$f\"; rm -f \"$f\"" % (url, seconds))
    cmd = ["ssh", "-p", "8022", "-i", KEY,
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR", NODE, remote]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 90)
        size = int((r.stdout or "").strip().splitlines()[-1])
    except Exception:
        return None
    return round(size * 8 / seconds / 1000) if size > 0 else None


def codec_of(ch):
    url = (rtsp() + "/"
           "cam/realmonitor?channel=%d&subtype=1" % ch)
    cmd = ["ssh", "-p", "8022", "-i", KEY,
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR", NODE,
           "ffprobe -v error -select_streams v -show_entries "
           "stream=codec_name,width,height -of default=nw=1 -rtsp_transport tcp '%s'" % url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    out = (r.stdout or "").strip()
    return out.replace("\n", " ") if out else None


def snapshot(when):
    print("=== substreams, %s ===" % when)
    got = {}
    for idx, ch in ((0, 1), (1, 2)):
        kbps, desc = measure_kbps(ch), codec_of(ch)
        got[idx] = kbps
        print("  %-14s %-40s %s" % (NAMES[idx], desc or "(no probe)",
                                    "%s kbit/s" % kbps if kbps is not None
                                    else "COULD NOT MEASURE (unknown, not zero)"))
    print()
    return got


def main():
    before = snapshot("BEFORE")

    r = RPC2()
    ok, msg = r.login()
    if not ok:
        print("ABORT: login failed (%s). Nothing written." % msg)
        return 2
    cfg = r.call("configManager.getConfig", {"name": "Encode"})
    table = (cfg.get("params") or {}).get("table")
    if not table:
        print("ABORT: could not read Encode table. Nothing written.")
        return 2

    snap = os.path.join(HERE, "encode_snapshot_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    with open(snap, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1)
    print("revert snapshot -> %s" % snap)

    for idx in (0, 1):
        v = table[idx]["ExtraFormat"][0]["Video"]
        print("  %-14s %s %s -> %s %s" % (NAMES[idx], v.get("Compression"), v.get("BitRate"),
                                          WANT["Compression"], WANT["BitRate"]))
        for k, val in WANT.items():
            v[k] = val

    res = r.set_config("Encode", table)
    print("\nsetConfig -> %s" % json.dumps({k: res.get(k) for k in ("result", "error")}))
    print("settling %d s…\n" % SETTLE_S)
    time.sleep(SETTLE_S)

    after = snapshot("AFTER")

    print("=== verdict ===")
    for idx in (0, 1):
        b, a = before.get(idx), after.get(idx)
        if a is None:
            print("  %-14s INCONCLUSIVE -- capture failed, behaviour unknown." % NAMES[idx])
        elif a >= WANT["BitRate"] * 0.85:
            print("  %-14s %s -> %s kbit/s  ✅ bitrate SURVIVED the codec switch" % (NAMES[idx], b, a))
        elif a <= 900:
            print("  %-14s %s -> %s kbit/s  ⚠ CLAMPED (declared H.265 sub cap is 768). "
                  "H.264 @1536 is probably the better deal -- rerun with --revert." % (NAMES[idx], b, a))
        else:
            print("  %-14s %s -> %s kbit/s  ⚠ unexpected" % (NAMES[idx], b, a))

    print("\n⚠ Delivered rate is scene-dependent (this device's CBR behaves like capped VBR), so "
          "read direction and neighbourhood, not equality.")
    print("revert snapshot: %s" % snap)
    try:
        r.logout()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
