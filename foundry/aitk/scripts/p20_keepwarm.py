"""[P20] KEEP-WARM: tiny continuous GPU load — simultaneously a HYPOTHESIS TEST and a candidate FIX.

HYPOTHESIS: the trainer is not compute- or bandwidth-bound when "stalled". Sensors show high core clock
(~3100 MHz) and high vcore (1.10 V) but LOW power (~230 W) and almost no memory traffic (mem_load 2-6%) — a card
that is clocked up and READY but starved of work. Suspected cause: per-kernel dispatch / idle-transition overhead
under Windows WDDM (the GPU briefly idles between kernels, and each transition costs latency).

WHY THIS TESTS IT: if merely keeping the GPU continuously occupied holds the trainer in the saturated state, the
mechanism is occupancy/idle-transitions — NOT power budget (a trivial load cannot move a power governor) and NOT
allocation (already falsified: alloc+free of 256 MB did nothing).

WHY IT MIGHT ALSO BE THE FIX: unlike the 250-500 MB rescue poke, this uses a few MB, so it is safe to run beside a
real training job at ~150 MB headroom.

Usage:  p20_keepwarm.py [size] [sleep_ms]
        size     - square matmul dim (default 512 => ~1.5 MB of weights, trivial)
        sleep_ms - pause between iterations (default 0 = continuous)
Ctrl-C or kill to stop.
"""
import sys, time
import torch

N = int(sys.argv[1]) if len(sys.argv) > 1 else 512
SLEEP = (float(sys.argv[2]) / 1000.0) if len(sys.argv) > 2 else 0.0

free0, tot = torch.cuda.mem_get_info()
a = torch.randn(N, N, device="cuda", dtype=torch.bfloat16)
b = torch.randn(N, N, device="cuda", dtype=torch.bfloat16)
used_mb = (a.numel() + b.numel()) * 2 / 1024**2
print("[keepwarm] dim=%d  weights=%.1f MB  sleep=%.1f ms  (deliberately tiny)" % (N, used_mb, SLEEP * 1000))
print("[keepwarm] running continuously — kill this process to stop")
sys.stdout.flush()

i = 0
t0 = time.time()
while True:
    a = (a @ b) * 0.0 + a          # keep values bounded; the point is occupancy, not the result
    i += 1
    if SLEEP:
        torch.cuda.synchronize()
        time.sleep(SLEEP)
    if i % 20000 == 0:
        torch.cuda.synchronize()
        print("[keepwarm] %d iters, %.0f s elapsed" % (i, time.time() - t0))
        sys.stdout.flush()
