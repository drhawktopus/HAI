"""Compare two Comp output dirs frame-by-frame (baseline vs experiment).

Reports per-frame mean/max absolute difference and PSNR. Verdict thresholds are
deliberately tight to filter out pixel differences caused by fp16 nondeterminism
(PSNR ~50+). A PSNR under 40 indicates a substantive visual change requiring
manual review.

Usage: python corridorkey_bench_diff.py <comp_dir_A> <comp_dir_B> [--frames N]
"""
import argparse
import glob
import os
import sys

import cv2
import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir_a")
    ap.add_argument("dir_b")
    ap.add_argument("--frames", type=int, default=0, help="limit to first N common frames")
    args = ap.parse_args()

    a_files = {os.path.basename(p): p for p in glob.glob(os.path.join(args.dir_a, "*.png"))}
    b_files = {os.path.basename(p): p for p in glob.glob(os.path.join(args.dir_b, "*.png"))}
    common = sorted(set(a_files) & set(b_files))
    if args.frames:
        common = common[:args.frames]
    if not common:
        print("NO COMMON FRAMES — nothing to compare")
        sys.exit(2)

    worst_psnr, worst_name, mean_diffs = 1e9, None, []
    for name in common:
        ia = cv2.imread(a_files[name]).astype(np.float32)
        ib = cv2.imread(b_files[name]).astype(np.float32)
        if ia.shape != ib.shape:
            print("SHAPE MISMATCH on %s: %s vs %s" % (name, ia.shape, ib.shape))
            sys.exit(2)
        diff = np.abs(ia - ib)
        mean_diffs.append(float(diff.mean()))
        mse = float((diff ** 2).mean())
        psnr = 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)
        if psnr < worst_psnr:
            worst_psnr, worst_name = psnr, name

    print("frames compared: %d" % len(common))
    print("mean abs diff (avg): %.4f / 255" % (sum(mean_diffs) / len(mean_diffs)))
    print("worst-frame PSNR: %.1f dB (%s)" % (worst_psnr, worst_name))
    if worst_psnr >= 50:
        print("VERDICT: IDENTICAL-CLASS (fp16 noise floor) — adopt freely")
    elif worst_psnr >= 40:
        print("VERDICT: NEAR-IDENTICAL — spot-check the worst frame by eye")
    else:
        print("VERDICT: OUTPUT CHANGED — full eye pass required before adopting")
        sys.exit(1)


if __name__ == "__main__":
    main()
