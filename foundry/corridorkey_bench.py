"""A/B bench orchestrator for stock vs. --pipelined legs.

Runs both legs on a clip, parses s/frame numbers, and prints + DMs one summary line.

Box-specific execution constraints:
  - ROCm (Mothership): Must use `uv run --no-sync`. A bare `uv run` re-syncs the lockfile,
    silently reverting the vendor-transplant torch to CPU. Gate stays ON.
  - CUDA (BigHonker): Use `uv run --extra cuda` + --no-gate (no MES bug, no worker defer).

Usage: python corridorkey_bench.py <clip> [--frames 40] [--legs stock,pipelined] [--no-dm]
"""
import argparse, os, re, subprocess, sys

CK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CK_DIR)
import ck_notify

IS_ROCM = os.path.isdir(os.path.join(CK_DIR, ".venv", "Lib", "site-packages", "_rocm_sdk"))
UV = ["uv", "run", "--no-sync"] if IS_ROCM else ["uv", "run", "--extra", "cuda"]
BOX_ARGS = [] if IS_ROCM else ["--no-gate"]
DONE_RE = re.compile(r"keyer DONE (\d+)s \((\d+) frames, ([\d.]+)s/frame avg\)")


def leg(clip, frames, pipelined):
    args = UV + ["python", "corridorkey_run.py", clip, "--max-frames", str(frames)] + BOX_ARGS
    if pipelined:
        args.append("--pipelined")
    r = subprocess.run(args, cwd=CK_DIR, capture_output=True, text=True, timeout=7200)
    m = DONE_RE.search(r.stdout + r.stderr)
    if not m:
        return None, "no 'keyer DONE' in output (rc=%d): %s" % (r.returncode, (r.stderr or r.stdout)[-200:])
    return float(m.group(3)), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--legs", default="stock,pipelined")
    ap.add_argument("--no-dm", action="store_true")
    a = ap.parse_args()

    results, errors = {}, []
    for name in a.legs.split(","):
        spf, err = leg(a.clip, a.frames, pipelined=(name.strip() == "pipelined"))
        if err:
            errors.append("%s: %s" % (name, err))
        else:
            results[name.strip()] = spf
            print("%s: %.2f s/f" % (name, spf))

    if "stock" in results and "pipelined" in results and results["pipelined"]:
        summary = "bench %s (%df, %s): stock %.2f vs pipelined %.2f s/f = %.1fx" % (
            a.clip, a.frames, "ROCm" if IS_ROCM else "CUDA",
            results["stock"], results["pipelined"], results["stock"] / results["pipelined"])
    else:
        summary = "bench %s (%df): %s%s" % (
            a.clip, a.frames,
            " ".join("%s %.2fs/f" % kv for kv in results.items()) or "no results",
            (" · ERRORS: " + "; ".join(errors)) if errors else "")
    print(summary)
    if not a.no_dm:
        ck_notify.post(summary)


if __name__ == "__main__":
    main()
