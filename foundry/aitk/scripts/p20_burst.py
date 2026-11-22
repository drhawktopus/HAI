"""[P20] Parametric GEMM burst — find the MINIMUM foreign workload that flips a stalled trainer to saturated.

Established so far:
  * A 512x512 continuous keep-warm does NOT flip it (occupancy alone is not the trigger).
  * The kernel benchmark (6144x6144 and 6144x18432 GEMMs, ~20 s) DOES flip it, n=4.
  => The trigger scales with MAGNITUDE of the foreign work, not its mere presence.

This runs a fixed-size GEMM loop for a fixed duration so we can sweep size and find the threshold.
A threshold that tracks bandwidth vs FLOPs vs footprint tells us different things about the mechanism.

Usage: p20_burst.py <dim> <seconds>
"""
import sys, time
import torch

N = int(sys.argv[1]) if len(sys.argv) > 1 else 4096
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

mb = N * N * 2 / 1024**2
print("[burst] dim=%d  (%.0f MB/matrix, ~%.0f MB working set)  for %.0f s" % (N, mb, mb * 3, SECS))
sys.stdout.flush()

a = torch.randn(N, N, device="cuda", dtype=torch.bfloat16)
b = torch.randn(N, N, device="cuda", dtype=torch.bfloat16)
torch.cuda.synchronize()

t0 = time.time()
n = 0
while time.time() - t0 < SECS:
    c = a @ b
    n += 1
torch.cuda.synchronize()
el = time.time() - t0
tflops = (2.0 * N ** 3 * n) / el / 1e12
print("[burst] %d matmuls in %.1f s  =>  %.2f TFLOP/s achieved" % (n, el, tflops))
del a, b, c
torch.cuda.empty_cache()
print("[burst] released")
