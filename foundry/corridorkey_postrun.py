r"""Post-run offload for CorridorKey.

Generates deliverables and sends notifications after a render finishes:
  1. Stitch Output\Comp PNGs to <clip>_comp.mp4 (if missing)
  2. Create 1080p proxy <clip>_comp_1080.mp4 (if missing)
  3. Generate filmstrip JPG (12 frames, 4x3 grid) for preview
  4. Parse telemetry jsonl for stats (frames/secs/s/f)
  5. Send summary + filmstrip via ck_notify
  6. Send video: 1080p proxy if < 8MB, else generate 720p DM cut (~7.5MB).
     Full-res remains on disk.

Usage:  python corridorkey_postrun.py <clip> [--fps 30] [--no-dm] [--no-video]
"""
import argparse, glob, json, os, subprocess, sys

CK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CK_DIR)
import ck_notify

import foundry_paths as P  # noqa: E402
FFMPEG = os.path.join(P.FFMPEG_DIR, "ffmpeg.exe")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"  # PATH fallback (BigHonker)


def sh(args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("%s -> %s" % (args[0], (r.stderr or "")[-300:]))


def stats_line(clip):
    """Summarize per-stage stage_end events from the newest jsonl for this clip."""
    logs = sorted(glob.glob(os.path.join(CK_DIR, "telemetry", clip + "-*.jsonl")))
    if not logs:
        return "no telemetry log found"
    parts, status = [], "run_done missing (check log!)"
    with open(logs[-1]) as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == "stage_end":
                parts.append("%s %sf %.2fs/f" % (
                    e.get("stage"), e.get("frames"), e.get("spf", 0)))
            elif e.get("kind") == "run_done":
                status = "run_done OK"
            elif e.get("kind") in ("error", "abort"):
                status = "%s: %s" % (e["kind"], e.get("error") or e.get("reason"))
    return "%s · %s" % (" | ".join(parts) or "no stage_end events", status)


DM_CAP = 8 * 1024 * 1024          # relay hard limit
DM_TARGET_KBIT = 7.2 * 8192       # aim ~7.2MB so container overhead stays under the cap


def dm_video(out, clip, mp4, proxy, fps):
    """Pick/make a video that fits the relay cap. Returns path or None."""
    if os.path.exists(proxy) and os.path.getsize(proxy) <= DM_CAP:
        return proxy
    src = proxy if os.path.exists(proxy) else mp4
    if not os.path.exists(src):
        return None
    cut = os.path.join(out, clip + "_dmcut.mp4")
    n = len(glob.glob(os.path.join(os.path.dirname(mp4), "Comp", "*.png")))
    dur = max(n / fps, 1.0) if n else 30.0
    kbit = max(int(DM_TARGET_KBIT / dur), 300)
    sh([FFMPEG, "-v", "error", "-y", "-i", src, "-vf", "scale=-2:720",
        "-b:v", "%dk" % kbit, "-maxrate", "%dk" % kbit, "-bufsize", "%dk" % (2 * kbit),
        "-an", cut])
    return cut if os.path.getsize(cut) <= DM_CAP else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--no-dm", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    a = ap.parse_args()

    out = os.path.join(CK_DIR, "ClipsForInference", a.clip, "Output")
    comp_dir = os.path.join(out, "Comp")
    mp4 = os.path.join(out, a.clip + "_comp.mp4")
    proxy = os.path.join(out, a.clip + "_comp_1080.mp4")
    strip = os.path.join(out, a.clip + "_filmstrip.jpg")

    made = []
    if not os.path.exists(mp4) and glob.glob(os.path.join(comp_dir, "*.png")):
        sh([FFMPEG, "-v", "error", "-y", "-framerate", str(a.fps),
            "-i", os.path.join(comp_dir, "%05d.png"), "-c:v", "libx264", "-crf", "16", mp4])
        made.append("comp.mp4")
    if os.path.exists(mp4) and not os.path.exists(proxy):
        sh([FFMPEG, "-v", "error", "-y", "-i", mp4, "-vf", "scale=-2:1080", "-crf", "23", proxy])
        made.append("1080p proxy")
    if os.path.exists(mp4):
        n = len(glob.glob(os.path.join(comp_dir, "*.png"))) or 120
        step = max(n // 12, 1)
        sh([FFMPEG, "-v", "error", "-y", "-i", mp4,
            "-vf", "select='not(mod(n\\,%d))',scale=480:-2,tile=4x3" % step,
            "-frames:v", "1", "-q:v", "4", strip])
        made.append("filmstrip")

    summary = "postrun %s: %s · made [%s]" % (a.clip, stats_line(a.clip), ", ".join(made) or "nothing new")
    print(summary)
    if not a.no_dm:
        ck_notify.post(summary, strip if os.path.exists(strip) else None)
        if not a.no_video:
            vid = dm_video(out, a.clip, mp4, proxy, a.fps)
            if vid:
                ck_notify.post("video: %s (%.1fMB)" % (
                    os.path.basename(vid), os.path.getsize(vid) / 1048576), vid)
            else:
                ck_notify.post("video too big for DM even at 720p cut — on disk: " + proxy)


if __name__ == "__main__":
    main()
