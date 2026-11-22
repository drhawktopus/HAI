"""Duty-cycle probe: same total compute, different burst/rest shapes.

Tests whether a fanless tablet sustains more THROUGHPUT when work is chunked
with cooldown gaps than when run flat out. Thermal headroom (not clock speed)
is the scarce resource on passively cooled hardware --
where pinning the 'performance' governor made things 2.5x WORSE.

Reports GFLOP/s measured over BURST TIME ONLY, plus wall-clock including rest,
so both "how fast while working" and "how much total work per minute" are visible.
"""
import json
import multiprocessing as mp
import os
import sys
import time

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np  # noqa: E402

N = 1024
BURST = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
REST = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
CYCLES = int(sys.argv[3]) if len(sys.argv) > 3 else 4


def worker(args):
    seed, burst = args
    rng = np.random.default_rng(seed)
    a = rng.random((N, N))
    b = rng.random((N, N))
    np.dot(a, b)
    reps = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < burst:
        np.dot(a, b)
        reps += 1
    return reps, time.perf_counter() - t0


if __name__ == "__main__":
    try:
        mp.set_start_method("fork", force=True)
    except RuntimeError:
        pass
    ncpu = os.cpu_count() or 1
    total_reps = 0
    burst_time = 0.0
    t_wall0 = time.perf_counter()
    per_cycle = []

    with mp.Pool(ncpu) as p:
        for c in range(CYCLES):
            res = p.map(worker, [(c * 100 + i, BURST) for i in range(ncpu)])
            reps = sum(r for r, _ in res)
            dt = max(d for _, d in res)
            total_reps += reps
            burst_time += dt
            gf = 2.0 * N ** 3 * reps / dt / 1e9
            per_cycle.append(round(gf, 2))
            if REST > 0 and c < CYCLES - 1:
                time.sleep(REST)

    wall = time.perf_counter() - t_wall0
    flops = 2.0 * N ** 3 * total_reps
    print(json.dumps({
        "cores": ncpu,
        "burst_s": BURST, "rest_s": REST, "cycles": CYCLES,
        "gflops_while_working": round(flops / burst_time / 1e9, 2),
        "gflops_per_wallclock": round(flops / wall / 1e9, 2),
        "per_cycle": per_cycle,
        "wall_s": round(wall, 1),
    }))
