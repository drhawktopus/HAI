"""Instrumented CorridorKey pipeline runner.

Wires per-frame callbacks from clip_manager into telemetry:
  F:/CorridorKey/telemetry/<clip>-<ts>.jsonl   append-only history (stages, rolling s/frame, ETA)
  F:/CorridorKey/telemetry/STATUS.json         overwritten live snapshot for cheap polling

Usage: python corridorkey_run.py <clip_name> [--device cuda] [--no-gate] [--log-every N]
  <clip_name> = folder under ClipsForInference (must contain Input.*)
  --no-gate   = skip the collision gate: use on BigHonker (no MES bug, no worker to defer);
                Mothership runs GATED through corridorkey_gated.
Run through the venv (Mothership: uv run --no-sync; BigHonker: uv run --extra cuda)."""
import argparse
import datetime
import json
import os
import sys
import threading
import time

import foundry_paths as P  # noqa: E402
CK_DIR = P.CORRIDORKEY_DIR
TELE_DIR = os.path.join(CK_DIR, "telemetry")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class Tele:
    def __init__(self, clip):
        os.makedirs(TELE_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = os.path.join(TELE_DIR, "%s-%s.jsonl" % (clip, ts))
        self.status_path = os.path.join(TELE_DIR, "STATUS.json")
        self.stage = None
        self.t_stage = None
        self.t_frame = None
        self.frame_secs = []

    def event(self, **kw):
        kw["ts"] = now()
        with open(self.path, "a", encoding="ascii") as f:
            f.write(json.dumps(kw) + "\n")

    def snapshot(self, **kw):
        kw["ts"] = now()
        tmp = self.status_path + ".tmp"
        with open(tmp, "w", encoding="ascii") as f:
            f.write(json.dumps(kw))
        os.replace(tmp, self.status_path)

    def stage_start(self, stage, clip, total):
        self.stage, self.t_stage, self.frame_secs = stage, time.time(), []
        self.t_frame = time.time()
        self.event(kind="stage_start", stage=stage, clip=clip, total_frames=total)
        self.snapshot(stage=stage, clip=clip, frame=0, total=total)
        print("[%s] %s start (%d frames)" % (now(), stage, total), flush=True)

    def frame(self, idx, total, log_every):
        t = time.time()
        self.frame_secs.append(t - self.t_frame)
        self.t_frame = t
        done = idx + 1
        if done % log_every == 0 or done == total:
            recent = self.frame_secs[-log_every:]
            spf = sum(recent) / len(recent)
            eta = int(spf * (total - done))
            self.event(kind="progress", stage=self.stage, frame=done, total=total,
                       spf=round(spf, 2), eta_secs=eta)
            self.snapshot(stage=self.stage, frame=done, total=total,
                          spf=round(spf, 2), eta_secs=eta)
            print("[%s] %s %d/%d  %.2fs/frame  ETA %dm%02ds"
                  % (now(), self.stage, done, total, spf, eta // 60, eta % 60), flush=True)

    def stage_end(self):
        dt = time.time() - self.t_stage
        n = len(self.frame_secs)
        gpu = gpu_window_stats(self.t_stage, time.time())
        self.event(kind="stage_end", stage=self.stage, secs=round(dt, 1), frames=n,
                   spf=round(dt / n, 2) if n else None, **gpu)
        print("[%s] %s DONE %.0fs (%s frames, %.2fs/frame avg) %s"
              % (now(), self.stage, dt, n, dt / n if n else 0,
                 ("| med %sW load %s%% vram %sMB" % (gpu.get("power_w_med"),
                  gpu.get("gpu_load_med"), gpu.get("vram_mb_med"))) if gpu else ""),
              flush=True)


GPU_CSV = P.GPU_TELEMETRY


def gpu_window_stats(t_start, t_end):
    """Calculate median power/load/vram from `gpu_telemetry.csv` within [t_start, t_end].
    Distinguishes compute-bound (high W, high load) from launch-overhead-bound (low W, mid load).

    Returns empty dict if the telemetry file is missing or unreadable."""
    try:
        rows = []
        with open(GPU_CSV, encoding="utf-8", errors="replace") as f:
            header = f.readline().strip().split(",")
            idx = {k: header.index(k) for k in ("ts", "power_w", "gpu_load", "vram_used_mb")}
            for line in f:
                parts = line.rstrip("\n").split(",")
                try:
                    ts = time.mktime(time.strptime(parts[idx["ts"]], "%Y-%m-%d %H:%M:%S"))
                except (ValueError, IndexError):
                    continue
                if t_start <= ts <= t_end:
                    rows.append(parts)
        if not rows:
            return {}
        def med(key):
            vals = sorted(float(r[idx[key]]) for r in rows if r[idx[key]])
            return round(vals[len(vals) // 2], 1) if vals else None
        return {"power_w_med": med("power_w"), "gpu_load_med": med("gpu_load"),
                "vram_mb_med": med("vram_used_mb"), "gpu_samples": len(rows)}
    except Exception:
        return {}


def run_keyer_pipelined(clip, device, max_frames, on_frame, log, comp_only=False):
    """Pipeline run_inference by overlapping decode, GPU inference, and I/O.

        Stock run_inference is strictly serial per frame. This loop overlaps all three phases:
          prefetch thread (decode N+1) | main thread (GPU only) | writer pool (3 workers, encodes)

        Safety invariants:
        - Ordering is filename-stamped.
        - Queues are bounded to limit RAM usage.
        - CUDA context stays single-threaded to avoid collisions.

        Constraints:
        - Supports video Input + PNG AlphaHint sequence + default settings only;
          asserts loudly on unsupported shapes. Fall back to stock (--pipelined off)
          for exotic inputs.
        - MP4 stitching is handled by the caller via ffmpeg.
    """
    import queue
    from concurrent.futures import ThreadPoolExecutor

    import cv2
    import numpy as np
    from backend.frame_io import EXR_WRITE_FLAGS
    from CorridorKeyModule.backend import create_engine

    assert clip.input_asset.type == "video", "pipelined path expects a video Input"
    assert clip.alpha_asset.type == "sequence", "pipelined path expects an AlphaHint sequence"

    out_root = os.path.join(clip.root_path, "Output")
    dirs = {k: os.path.join(out_root, k) for k in ("FG", "Matte", "Comp", "Processed")}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    alpha_files = sorted(f for f in os.listdir(clip.alpha_asset.path)
                         if f.lower().endswith(".png"))
    total = min(clip.input_asset.frame_count, len(alpha_files))
    if max_frames:
        total = min(total, max_frames)

    engine = create_engine(device=device)
    in_q = queue.Queue(maxsize=2)

    def reader():
        cap = cv2.VideoCapture(clip.input_asset.path)
        try:
            for i in range(total):
                ok, frame = cap.read()
                if not ok:
                    break
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
                m = cv2.imread(os.path.join(clip.alpha_asset.path, alpha_files[i]),
                               cv2.IMREAD_ANYDEPTH | cv2.IMREAD_UNCHANGED)
                if m is None:
                    break
                if m.ndim == 3:
                    m = m[:, :, 0]
                m = m.astype(np.float32) / (255.0 if m.dtype == np.uint8 else
                                            65535.0 if m.dtype == np.uint16 else 1.0)
                if m.shape[:2] != img.shape[:2]:
                    m = cv2.resize(m, (img.shape[1], img.shape[0]), cv2.INTER_LINEAR)
                in_q.put((i, img, m))
        finally:
            cap.release()
            in_q.put(None)

    def _imwrite(path, arr, *flags):
        # cv2.imwrite returns False on failure; raise to halt pipeline
        if not cv2.imwrite(path, arr, *flags):
            raise RuntimeError("imwrite FAILED (disk full?): " + path)

    def write_comp_only(i, comp_u8):
        # Payload is a single ~25MB uint8 array, not the ~1GB float res dict, to keep in-flight RAM bounded on small-RAM boxes.
        _imwrite(os.path.join(dirs["Comp"], "%05d.png" % i),
                 cv2.cvtColor(comp_u8, cv2.COLOR_RGB2BGR))

    def write_frame(i, res):
        stem = "%05d" % i
        _imwrite(os.path.join(dirs["FG"], stem + ".exr"),
                 cv2.cvtColor(res["fg"], cv2.COLOR_RGB2BGR), EXR_WRITE_FLAGS)
        alpha = res["alpha"][:, :, 0] if res["alpha"].ndim == 3 else res["alpha"]
        _imwrite(os.path.join(dirs["Matte"], stem + ".exr"), alpha, EXR_WRITE_FLAGS)
        if res.get("comp") is not None:
            comp = (np.clip(res["comp"], 0.0, 1.0) * 255.0).astype(np.uint8)
            _imwrite(os.path.join(dirs["Comp"], stem + ".png"),
                     cv2.cvtColor(comp, cv2.COLOR_RGB2BGR))
        if "processed" in res:
            _imwrite(os.path.join(dirs["Processed"], stem + ".exr"),
                     cv2.cvtColor(res["processed"], cv2.COLOR_RGBA2BGRA), EXR_WRITE_FLAGS)

    threading.Thread(target=reader, daemon=True).start()
    futures = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        done = 0
        while True:
            item = in_q.get()
            if item is None:
                break
            i, img, m = item
            res = engine.process_frame(img, m, input_is_linear=False, fg_is_straight=True,
                                       despill_strength=0.5, auto_despeckle=True,
                                       despeckle_size=400, refiner_scale=1.0,
                                       generate_comp=True, screen_channel=1)
            while len([f for f in futures if not f.done()]) >= 3:   # bound in-flight RAM
                time.sleep(0.05)
            if comp_only:
                comp_u8 = (np.clip(res["comp"], 0.0, 1.0) * 255.0).astype(np.uint8)
                del res  # release the ~1GB float dict before the next forward
                futures.append(pool.submit(write_comp_only, i, comp_u8))
            else:
                futures.append(pool.submit(write_frame, i, res))
            done += 1
            on_frame(done - 1, total)
        for f in futures:
            f.result()      # surface writer errors loudly
    log("pipelined keyer: %d frames, %d writes verified" % (done, len(futures)))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--no-gate", action="store_true")
    ap.add_argument("--log-every", type=int, default=10)
    ap.add_argument("--max-frames", type=int, default=None, help="bench: limit keyer frames")
    ap.add_argument("--pipelined", action="store_true",
                    help="v1.2 surgery: prefetch + writer-pool keyer loop (video+PNG-hints only)")
    ap.add_argument("--comp-only", action="store_true",
                    help="v1.3: skip FG/Matte/Processed EXRs, write Comp PNGs only — ~40x less "
                         "in-flight RAM + ~90%% less disk (BigHonker OOM fix; pipelined only)")
    args = ap.parse_args()

    sys.path.insert(0, CK_DIR)
    os.chdir(CK_DIR)
    tele = Tele(args.clip)
    gate = None
    stop = threading.Event()
    if not args.no_gate:
        import corridorkey_gated as gate
        gate.write_hold()
        quiet = 0
        t0 = time.time()
        while quiet < 2:
            if time.time() - t0 > 1800:
                tele.event(kind="abort", reason="card never quiet in 30min")
                os.remove(gate.HOLD)
                sys.exit(1)
            if gate.ollama_resident():
                gate.unload_ollama()
            quiet = quiet + 1 if gate.card_quiet() else 0
            if quiet < 2:
                gate.write_hold()
                time.sleep(30)
        def refresher():
            while not stop.wait(300):
                try:
                    gate.write_hold()
                except Exception:
                    pass
        threading.Thread(target=refresher, daemon=True).start()
        tele.event(kind="gate", state="acquired")

    try:
        import clip_manager as cm
        clips = [c for c in cm.scan_clips() if c.name == args.clip]
        if not clips:
            tele.event(kind="abort", reason="clip %r not found" % args.clip)
            sys.exit(2)
        clip = clips[0]
        total = clip.input_asset.frame_count
        tele.event(kind="run_start", clip=args.clip, frames=total, device=args.device)

        if clip.alpha_asset is None:
            tele.stage_start("birefnet", args.clip, total)
            cm.run_birefnet(clips, device=args.device,
                            on_frame_complete=lambda i, n: tele.frame(i, n, args.log_every))
            tele.stage_end()
            clips = [c for c in cm.scan_clips() if c.name == args.clip]
            if clips[0].alpha_asset is None:
                tele.event(kind="abort", reason="AlphaHint missing after BiRefNet")
                sys.exit(3)
        else:
            tele.event(kind="skip", stage="birefnet", reason="AlphaHint already present")

        tele.stage_start("keyer", args.clip, args.max_frames or total)
        if args.pipelined:
            run_keyer_pipelined(clips[0], args.device, args.max_frames,
                                on_frame=lambda i, n: tele.frame(i, n, args.log_every),
                                log=lambda m: print("[%s] %s" % (now(), m), flush=True),
                                comp_only=args.comp_only)
        else:
            cm.run_inference(clips, device=args.device, max_frames=args.max_frames,
                             on_frame_complete=lambda i, n: tele.frame(i, n, args.log_every))
        tele.stage_end()
        tele.event(kind="run_done", clip=args.clip)
        tele.snapshot(stage="done", clip=args.clip)
        print("PIPELINE-DONE %s" % args.clip, flush=True)
    except Exception as e:
        tele.event(kind="error", error=repr(e)[:500])
        raise
    finally:
        stop.set()
        if gate is not None:
            try:
                os.remove(gate.HOLD)
                tele.event(kind="gate", state="released")
            except OSError:
                pass


if __name__ == "__main__":
    main()
