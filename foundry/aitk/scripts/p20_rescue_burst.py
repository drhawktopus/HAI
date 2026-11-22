"""[P20] Rescue burst v2 — heavy GEMMs from a FRESH process, to flip a stalled trainer back to saturated.

This is the payload `p20_stall_rescue.py` spawns. It must be run as a NEW PROCESS every time: a long-lived
context that has been sitting idle is degraded right along with the trainer and cannot lift anything
(in-process bursts managed ~7 TFLOP/s on a card that does >100, and rescued 0 of 4 stalls). Freshness is the active
ingredient.

IN-FLIGHT DUD DETECTION. A fresh process can ALSO spawn degraded (~50% of
observed rescues: 6.0/7.0/7.1 TFLOP/s duds vs 15-24 healthy-while-sharing). A dud is tellable ~3 s in, so we
self-abort with exit code 3 instead of burning the full burst + the daemon's settle/cooldown on a known-dud.
Thresholds from measured data: dud 6-7.1, healthy-sharing >=15.1, idle baseline 77.5 => DUD_TFLOPS = 11.0.

Shapes are deliberately the ones with the track record: 6144x6144 @ 6144x18432, the same GEMMs
p20_kernel_bench.py fires. A 512x512 keep-warm does NOT flip it, so magnitude matters, not mere occupancy.
Nothing between 512 and 6144 has been tested — if you shrink DIM, treat it as an experiment, not a tune.

PRINTS ACHIEVED TFLOP/s — do not remove. If a
rescue fails, the first question is whether the rescuer itself ran degraded or ran fine.
MEASURED BASELINE (idle card, dim=6144): **77.5 TFLOP/s** = healthy. Do NOT expect >100 — the
card's ~122 TFLOP/s bf16 figure is paper peak; 60-80% of peak is what real GEMMs deliver.

Usage: p20_rescue_burst.py [dim] [seconds]
Exit:  0 = burst completed, 2 = could not allocate even at the floor, 3 = DUD (spawned degraded, self-aborted)
"""
import sys, time
import torch

DIM = int(sys.argv[1]) if len(sys.argv) > 1 else 6144
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0
FLOOR = 2048          # below this the burst is not known to flip anything; fail rather than pretend
DUD_TFLOPS = 14.0     # the flip boundary is ~14-15 TFLOP/s (15.1/18.7/24.1/44.3 flipped a stall; 12.1/13.3/13.7 did not): abort marginals at
# 3 s instead of burning full cycles
DUD_CHECK_SECS = 3.0  # judge after this much timed work


def alloc(dim):
    """A (dim x dim) @ B (dim x 3*dim) — mirrors the proven kernel-bench shapes."""
    a = torch.randn(dim, dim, device="cuda", dtype=torch.bfloat16)
    b = torch.randn(dim, dim * 3, device="cuda", dtype=torch.bfloat16)
    torch.cuda.synchronize()
    return a, b


# The trainer may be holding nearly the whole card (observed free VRAM as low as ~150 MB mid-run). Windows
# will spill us to shared host memory, which is slower but still produces heavy foreign work — that is fine,
# the point is magnitude, not our own throughput. Halve on a genuine OOM rather than dying outright.
dim = DIM
A = B = None
while dim >= FLOOR:
    try:
        A, B = alloc(dim)
        break
    except RuntimeError as e:
        if "out of memory" not in str(e).lower():
            raise
        torch.cuda.empty_cache()
        print("[burst] OOM at dim=%d, halving" % dim); sys.stdout.flush()
        dim //= 2

if A is None:
    print("[burst] FAILED: could not allocate even at dim=%d" % FLOOR)
    sys.exit(2)

if dim != DIM:
    print("[burst] WARNING: running at dim=%d (requested %d) — below-6144 sizes are UNPROVEN" % (dim, DIM))

# MUST synchronise inside the loop. An unbounded async enqueue (matmul with no sync) runs at CPU speed,
# queues tens of thousands of ops plus a fresh output tensor each iteration, and the trailing synchronize()
# then blocks for MINUTES draining the backlog. Sync every iteration: the GPU stays saturated (the whole
# point) while queue depth stays at 1.
C = A @ B                      # warmup outside the timed region
torch.cuda.synchronize()

FLOP_PER_MM = 2.0 * dim * dim * (dim * 3)

t0 = time.time()
n = 0
dud_checked = False
while time.time() - t0 < SECS:
    C = A @ B
    torch.cuda.synchronize()
    n += 1
    if not dud_checked:
        el = time.time() - t0
        if el >= DUD_CHECK_SECS:
            dud_checked = True
            tf = FLOP_PER_MM * n / el / 1e12
            if tf < DUD_TFLOPS:
                print("[burst] DUD: %.1f TFLOP/s at %.1fs (dim=%d, %d matmuls) — spawned degraded, aborting "
                      "for immediate respawn" % (tf, el, dim, n))
                sys.exit(3)
el = time.time() - t0

tflops = FLOP_PER_MM * n / el / 1e12
print("[burst] dim=%d  %d matmuls in %.1fs  =>  %.1f TFLOP/s" % (dim, n, el, tflops))
print("[burst] (~75-80 = rescuer ran at full speed [idle baseline 77.5]; 15-25 typical while sharing the "
      "card with the trainer; <11 would have self-aborted as a dud)")
sys.exit(0)
