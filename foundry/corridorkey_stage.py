"""Stages a source video into ClipsForInference/<clip_name>/Input.mov.

All inputs are re-timed to <=30fps, even when --full is used. Downstream
stitch/postrun assume 30fps; higher fps causes half-speed playback.
--full sets CRF 10 (max quality), not stream copy. Sources already <=30fps
with --full are stream-copied untouched.
"""
import argparse
import os
import subprocess
import sys

import foundry_paths as P  # noqa: E402
CK_DIR = P.CORRIDORKEY_DIR
FFMPEG = os.path.join(P.FFMPEG_DIR, "ffmpeg.exe")
FFPROBE = os.path.join(P.FFMPEG_DIR, "ffprobe.exe")
if not os.path.exists(FFMPEG):
    FFMPEG, FFPROBE = "ffmpeg", "ffprobe"  # PATH fallback (BigHonker)


def src_fps(path):
    out = subprocess.run([FFPROBE, "-v", "quiet", "-select_streams", "v:0",
                          "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    try:
        num, den = out.split("/")
        return float(num) / float(den)
    except Exception:
        return 30.0  # unprobeable -> assume fine, re-encode path will conform anyway


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("clip_name")
    ap.add_argument("--secs", type=float, default=None, help="trim to first N seconds")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--full", action="store_true",
                    help="max quality (crf 10); stream-copy only if source is already <=30fps")
    args = ap.parse_args()

    dst_dir = os.path.join(CK_DIR, "ClipsForInference", args.clip_name)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "Input.mov")

    fps_in = src_fps(args.src)
    needs_retime = fps_in > args.fps + 0.5

    cmd = [FFMPEG, "-v", "error", "-y", "-i", args.src]
    if args.secs:
        cmd += ["-t", str(args.secs)]
    if args.full and not needs_retime:
        cmd += ["-c", "copy"]
    else:
        crf = "10" if args.full else "16"
        cmd += ["-r", str(args.fps), "-c:v", "libx264", "-crf", crf, "-preset", "fast", "-an"]
        if needs_retime:
            print("source %.6g fps -> conforming to %d fps" % (fps_in, args.fps))
    cmd += [dst]
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        sys.exit(rc)
    probe = subprocess.run([FFPROBE, "-v", "quiet", "-show_entries",
                            "format=duration:stream=r_frame_rate,nb_frames",
                            "-of", "default=noprint_wrappers=1", dst],
                           capture_output=True, text=True).stdout
    print("STAGED", dst)
    print(probe.strip())


if __name__ == "__main__":
    main()
