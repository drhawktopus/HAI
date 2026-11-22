"""[P20] THE RESCUE-TRICK TEST: can a foreign GPU alloc/free knock a stalled trainer into the saturated state?

Observed once: a stalled run jumped 67 -> 6.9 s/it (~10x) at the moment an unrelated process allocated and freed a
large GPU block. Telemetry showed slow = 3100 MHz @ ~230 W (clocked up, executing almost
nothing = STALLED) and fast = 2570 MHz @ ~390 W (saturated). Hypothesis: the foreign allocation forces the driver
to reorganise the memory map and relocate the trainer's working set out of a bad placement.

Run this ONLY against a disposable run while the stall signature is confirmed (high clock + LOW power).
Allocates in small steps and frees immediately, so it grabs as little as possible.
"""
import sys, time
import torch

MB = int(sys.argv[1]) if len(sys.argv) > 1 else 256
print("free/total before: %.2f / %.2f GB" % tuple(x / 1024**3 for x in torch.cuda.mem_get_info()))
blocks = []
got = 0
try:
    for _ in range(MB // 32):
        blocks.append(torch.empty(32 * 1024 * 1024, dtype=torch.uint8, device="cuda"))
        got += 32
except RuntimeError as e:
    print("stopped allocating at %d MB (%s)" % (got, type(e).__name__))
print("allocated %d MB" % got)
torch.cuda.synchronize()
time.sleep(2)
del blocks
torch.cuda.empty_cache()
torch.cuda.synchronize()
print("freed. free/total after: %.2f / %.2f GB" % tuple(x / 1024**3 for x in torch.cuda.mem_get_info()))
