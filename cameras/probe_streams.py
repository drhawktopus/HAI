"""Read what BOTH camera channels are ACTUALLY emitting, from the stream's SPS.

Verification is the stream, never the NVR's config read-back -- reading back a written value is
what had the basement believed to be 720p for two days.
ffprobe runs on a tablet (CAMERAS_FFMPEG_NODE); the head box has no ffmpeg.
"""
import subprocess
import sys
from cam_settings import TABLET_KEY, ffmpeg_tablet, tablet
from nvr_settings import rtsp

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KEY = TABLET_KEY
NODE = ffmpeg_tablet()


def describe(ch, subtype=0):
    # %% -- the password is percent-encoded ("!"=%21, "$"=%24) and this literal is inside a
    # %-format expression, so every literal percent must be doubled.
    url = (rtsp() + "/"
           "cam/realmonitor?channel=%d&subtype=%d" % (ch, subtype))
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


def main():
    for ch in (1, 2):
        name = {1: "dining", 2: "basement"}[ch]
        print("--- channel %d (%s) main ---" % (ch, name))
        print(describe(ch, 0))
        print()


if __name__ == "__main__":
    main()
