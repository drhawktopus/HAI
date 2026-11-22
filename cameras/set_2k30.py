"""Set dining (ch1 / index 0) MAIN stream to 2688x1520 @30 H.265 via RPC2, and VERIFY THE STREAM.

WHY THIS CAN WORK WHERE FAILED:
  * That pass wrote via **CGI**, which on this unit is a documented LYING SUCCESS -- it returns "OK",
    writes the value into the table, and the encoder never emits it. `nvr_rpc2.py` exists precisely
    because the web UI does not use CGI for writes.
  * It also tried **2560x1440**, which is NOT in this device's vocabulary. The valid list, read from
    `encode.getConfigCaps` (shape taken from the NVR's own UI):
        3840x2160, 3072x2048, 3072x1728, 2592x1944, 2688x1520, 3M, 2304x1296, 1080P, 1_3M, 720P
    with **FPSMax 30**, H.264/H.265, bitrate 768-6144.
  * 2688x1520 is chosen over 2592x1944 because it is **16:9** -- it preserves the current field of
    view, so every zone and mask coordinate keeps meaning what it says. 2592x1944 is 4:3 and would
    reframe the camera.

 ONE CHANNEL ONLY. Dining is index 0; basement (index 1) is left untouched as a control, so a
change in the stream cannot be confused with something device-wide.
 MainFormat[0] only -- that is the slot RTSP subtype=0 serves. [1] is the MOTION-RECORDING slot and
is a separate decision; changing both at once would move two variables.
 VERIFICATION IS THE SPS IN THE STREAM, never the config read-back. Reading back a written value is
what had the basement believed to be 720p for two days.

Revert data is written to encode_snapshot_<ts>.json BEFORE anything is sent.
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

# Table index, 0-based: 0 == camera channel 1 == dining, 1 == channel 2 == basement.
# The Encode TABLE is 0-based while the RTSP/CGI `channel=` parameter is 1-BASED. Mixing them
# writes the wrong camera and the read-back looks fine because both rows exist.
CH = int(sys.argv[1]) if len(sys.argv) > 1 else 0
WANT = {"Width": 2688, "Height": 1520, "FPS": 30,
        "Compression": "H.265", "BitRate": 6144, "BitRateControl": "CBR"}


def describe(ch=1):
    """Ask the STREAM what it is actually emitting. ffprobe on the tablet (the head box has no ffmpeg)."""
    key = TABLET_KEY
    url = (rtsp() + "/"
           "cam/realmonitor?channel=%d&subtype=0" % ch)
    cmd = ["ssh", "-p", "8022", "-i", key,
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR", ffmpeg_tablet(),
           "ffprobe -v error -select_streams v -show_entries "
           "stream=width,height,codec_name,avg_frame_rate -of default=nw=1 "
           "-rtsp_transport tcp '%s'" % url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return (r.stdout or "").strip() or (r.stderr or "").strip()[:200]
    except Exception as e:
        return "PROBE FAILED %s: %s" % (type(e).__name__, e)


def main():
    print("=== BEFORE, from the stream ===")
    before = describe(CH + 1)
    print(before)

    r = RPC2()
    r.login()
    cfg = r.call("configManager.getConfig", {"name": "Encode"})
    table = (cfg.get("params") or {}).get("table")
    if not table:
        print("could not read Encode table"); return

    snap = os.path.join(HERE, "encode_snapshot_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    with open(snap, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1)
    print("\nrevert snapshot -> %s" % snap)

    v = table[CH]["MainFormat"][0]["Video"]
    print("current MainFormat[0].Video: %sx%s @%s %s %s kbps"
          % (v.get("Width"), v.get("Height"), v.get("FPS"), v.get("Compression"), v.get("BitRate")))

    for k, val in WANT.items():
        v[k] = val
    # CustomResolutionName is NOT the control field (dining read 3840x2160 there while emitting
    # 1080p) but the UI keeps it consistent, so set it to match rather than leaving a stale label.
    if "CustomResolutionName" in v:
        v["CustomResolutionName"] = "2688x1520"

    res = r.set_config("Encode", table)
    print("\nsetConfig -> %s" % json.dumps({k: res.get(k) for k in ("result", "error")}))

    print("\nwaiting for the encoder to apply…")
    time.sleep(12)

    print("\n=== AFTER, from the stream (THE only evidence) ===")
    after = describe(CH + 1)
    print(after)

    # The OTHER channel, untouched -- proves the change was not device-wide.
    print("\n=== control: the other channel, untouched ===")
    print(describe(2 if CH == 0 else 1))

    print("\nVERDICT: changed" if after != before else
          "\nVERDICT: STREAM UNCHANGED -- the write did not land (another lying success)")
    try:
        r.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
