"""Try dining (ch1 / table index 0) MAIN stream at 3072x1728 @30 H.265 via RPC2, VERIFY THE STREAM,
and REVERT AUTOMATICALLY if the camera does not actually emit it.

WHY THIS IS WORTH TRYING, AND WHY IT MIGHT NOT WORK -- both halves matter:
  * The NVR's own `encode.getConfigCaps` lists 3072x1728 as a valid MainFormat resolution with
    FPSMax 30 (captured in `resolution_types.json`). That is the RECORDER's claim.
  * The CAMERA's spec sheet (`e841ca_specs.txt`, from the E841CA datasheet) says
    "4K(8MP)@15fps, 5MP@30fps, 1080p@30fps, 720p@30fps". 3072x1728 is 5.3 MP -- just ABOVE the
    sensor's stated 30 fps mode. So the recorder declaring it proves nothing about the camera.
    DECLARED != PRESENT: the same NVR accepts 30 fps at 3840x2160, which the camera certainly
    cannot do.
  * Therefore the only outcomes that mean anything come from the STREAM's SPS, and there are
    THREE of them, not two:
        3072x1728 @30 -> the win
        3072x1728 @15 -> resolution applied, frame rate silently halved (a gated parameter
                          dropped rather than refused -- the documented failure shape here)
        unchanged -> refused; the table now holds a PHANTOM value and must be resynced

 A REFUSED RESOLUTION STILL WRITES TO THE TABLE. After 1440p was refused the table
read 2560x1440 while the stream emitted 1080p, which is exactly the phantom that had the basement
believed to be 720p for two days. So this script REVERTS the table itself on any non-win.

 A STREAM NEEDS TO SETTLE BEFORE IT IS EVIDENCE. The first probe of basement read
15 fps when the resolution had already changed, because it was taken 12 s after the write; a
re-probe a minute later read 30. This script probes TWICE -- once early, once after ~80 s -- and
only the late probe is the verdict. The early one is kept because "resolution changed but fps is
still settling" and "fps was silently halved" look identical at 12 s.

 ONE CHANNEL ONLY. Dining is table index 0; basement (index 1) is left untouched as a CONTROL, so
a change in the stream cannot be confused with something device-wide.
 MainFormat[0] only -- that is the slot RTSP subtype=0 serves. MainFormat[1] is the
MOTION-RECORDING slot and is a separate decision; changing both at once would move two variables.

 ASPECT: 2688x1520 is 1.768:1; 3072x1728 is exactly 1.778:1 (16:9). That is a ~0.6% difference,
so normalised zone/mask coordinates survive but are not pixel-exact. 2592x1944 (the sensor's real
5MP@30 mode) is 4:3 and WOULD reframe the camera -- that is why it is not what we try first.

 BITRATE stays at 6144, which is this device's hard cap (8192 is accepted into the table and
silently ignored). 3072x1728 is 30% more pixels than 2688x1520 at the SAME bitrate, so even a
successful apply costs per-pixel quality. That is a judgement for the owner's eye, not for this script.

Revert data is written to encode_snapshot_<ts>.json BEFORE anything is sent.

Usage: python set_3072x1728.py [table_index] (default 0 = dining/ch1)
        python set_3072x1728.py 0 --keep (do not auto-revert on a partial result)
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

# The Encode TABLE is 0-based while the RTSP/CGI `channel=` parameter is 1-BASED. Mixing them
# writes the wrong camera and the read-back looks fine because both rows exist.
CH = 0
KEEP = "--keep" in sys.argv
for a in sys.argv[1:]:
    if not a.startswith("-"):
        CH = int(a)
        break

WANT = {"Width": 3072, "Height": 1728, "FPS": 30,
        "Compression": "H.265", "BitRate": 6144, "BitRateControl": "CBR"}

KEY = TABLET_KEY
NODE = ffmpeg_tablet()           # the head box has no ffmpeg
SETTLE_EARLY = 20
SETTLE_LATE = 60               # total ~80 s, well past the 12 s that misread basement on 08-11


def describe(ch):
    """Ask the STREAM what it is actually emitting. Never the config read-back."""
    # %% -- the password is percent-encoded ("!"=%21, "$"=%24) and this literal sits inside a
    # %-format expression, so every literal percent must be doubled.
    url = (rtsp() + "/"
           "cam/realmonitor?channel=%d&subtype=0" % ch)
    cmd = ["ssh", "-p", "8022", "-i", KEY,
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR", NODE,
           "ffprobe -v error -select_streams v -show_entries "
           "stream=width,height,codec_name,avg_frame_rate -of default=nw=1 "
           "-rtsp_transport tcp '%s'" % url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return "PROBE FAILED %s: %s" % (type(e).__name__, e)
    out = (r.stdout or "").strip()
    return out if out else "ERR rc=%s: %s" % (r.returncode, (r.stderr or "").strip()[:300])


def parse(desc):
    """-> (width, height, fps) or None if the probe did not return a usable measurement.

    Returning None rather than a default matters: an instrument that failed and an instrument that
    measured 'unchanged' must not render as the same verdict.
    """
    d = {}
    for line in desc.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
    try:
        num, _, den = d["avg_frame_rate"].partition("/")
        fps = float(num) / float(den or 1)
        return int(d["width"]), int(d["height"]), round(fps, 1)
    except Exception:
        return None


def write_video(r, want, note):
    """Read the Encode table fresh, apply `want` to MainFormat[0].Video of row CH, send it back."""
    cfg = r.call("configManager.getConfig", {"name": "Encode"})
    table = (cfg.get("params") or {}).get("table")
    if not table:
        print("  could not read Encode table"); return None, None
    v = table[CH]["MainFormat"][0]["Video"]
    for k, val in want.items():
        v[k] = val
    # CustomResolutionName is NOT the control field (dining read 3840x2160 there while emitting
    # 1080p) but leaving a stale label is how phantoms start, so keep it consistent.
    if "CustomResolutionName" in v:
        v["CustomResolutionName"] = "%dx%d" % (want["Width"], want["Height"])
    res = r.set_config("Encode", table)
    print("  setConfig(%s) -> %s" % (note, json.dumps({k: res.get(k) for k in ("result", "error")})))
    return table, res


def main():
    other = 2 if CH == 0 else 1
    name = {0: "dining", 1: "basement"}.get(CH, "index %d" % CH)
    print("=== TARGET: %s (table index %d / RTSP channel %d) ===" % (name, CH, CH + 1))

    print("\n=== BEFORE, from the stream ===")
    before = describe(CH + 1)
    print(before)
    b = parse(before)
    if b is None:
        print("\nABORT: could not measure the CURRENT stream, so there is no baseline to compare "
              "against and no way to tell a refusal from a probe failure. Nothing was written.")
        return 2

    r = RPC2()
    ok, msg = r.login()
    print("\nRPC2 login: %s (%s)" % (ok, msg))
    if not ok:
        print("ABORT: not logged in. Nothing was written.")
        return 2

    # Revert data BEFORE anything is sent.
    cfg = r.call("configManager.getConfig", {"name": "Encode"})
    table0 = (cfg.get("params") or {}).get("table")
    if not table0:
        print("ABORT: could not read the Encode table. Nothing was written.")
        return 2
    v0 = table0[CH]["MainFormat"][0]["Video"]
    restore = {k: v0.get(k) for k in WANT}
    snap = os.path.join(HERE, "encode_snapshot_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    with open(snap, "w", encoding="utf-8") as f:
        json.dump(table0, f, indent=1)
    print("revert snapshot -> %s" % snap)
    print("current MainFormat[0].Video: %sx%s @%s %s %s kbps"
          % (v0.get("Width"), v0.get("Height"), v0.get("FPS"),
             v0.get("Compression"), v0.get("BitRate")))

    print("\n=== WRITING %dx%d @%d ===" % (WANT["Width"], WANT["Height"], WANT["FPS"]))
    write_video(r, WANT, "target")

    print("\nsettling %d s…" % SETTLE_EARLY)
    time.sleep(SETTLE_EARLY)
    early = describe(CH + 1)
    print("early probe (NOT the verdict -- fps may still be settling):\n%s" % early)

    print("\nsettling a further %d s…" % SETTLE_LATE)
    time.sleep(SETTLE_LATE)
    late = describe(CH + 1)
    print("\n=== AFTER, from the stream (THE evidence) ===")
    print(late)

    print("\n=== control: %s, untouched ===" % ({1: "dining", 2: "basement"}[other]))
    ctrl = describe(other)
    print(ctrl)

    a = parse(late)
    if a is None:
        print("\nVERDICT: INCONCLUSIVE -- the late probe did not return a measurement, so the "
              "camera's behaviour is UNKNOWN, not 'unchanged'.")
        verdict, win = "inconclusive", False
    elif a == (WANT["Width"], WANT["Height"], float(WANT["FPS"])):
        print("\nVERDICT: ✅ WIN -- %dx%d @%s in the stream." % a)
        verdict, win = "win", True
    elif (a[0], a[1]) == (WANT["Width"], WANT["Height"]):
        print("\nVERDICT: ⚠ PARTIAL -- resolution applied (%dx%d) but fps is %s, not %d. The frame "
              "rate was silently dropped rather than refused." % (a[0], a[1], a[2], WANT["FPS"]))
        verdict, win = "partial", False
    else:
        print("\nVERDICT: ❌ REFUSED -- stream is %dx%d @%s, unchanged from baseline %dx%d @%s."
              % (a + b))
        verdict, win = "refused", False

    if not win and not KEEP:
        print("\n=== REVERTING the table to %sx%s @%s (a refused resolution still WRITES, and a "
              "phantom value in the table is how the basement was believed to be 720p for two "
              "days) ===" % (restore.get("Width"), restore.get("Height"), restore.get("FPS")))
        write_video(r, restore, "revert")
        time.sleep(SETTLE_EARLY)
        back = describe(CH + 1)
        print("after revert, from the stream:\n%s" % back)
        if parse(back) != b:
            print("⚠ REVERT DID NOT RESTORE THE BASELINE -- check this channel by hand. "
                  "Snapshot: %s" % snap)
    elif not win and KEEP:
        print("\n--keep given: table NOT reverted. It may now hold a phantom value. Snapshot: %s"
              % snap)

    try:
        r.logout()
    except Exception:
        pass
    return 0 if win else 1


if __name__ == "__main__":
    sys.exit(main())
