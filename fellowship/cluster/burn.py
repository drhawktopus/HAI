"""Embarrassingly-parallel compute probe: generate locally, compute flat out,
return one number. Ships bytes, not megabytes -- the workload class this
cluster is actually good at.

One single-threaded process per core (Termux OpenBLAS has threading compiled
out, so a single process would only ever use one A53 core).
"""
import json
import multiprocessing as mp
import os
import sys
import time

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1024
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0


def worker(seed):
    rng = np.random.default_rng(seed)
    a = rng.random((N, N))
    b = rng.random((N, N))
    np.dot(a, b)                      # warm up
    flops = 0.0
    t_end = time.perf_counter() + SECS
    reps = 0
    t0 = time.perf_counter()
    while time.perf_counter() < t_end:
        np.dot(a, b)
        reps += 1
    dt = time.perf_counter() - t0
    flops = 2.0 * N ** 3 * reps
    return flops / dt / 1e9


if __name__ == "__main__":
    try:
        mp.set_start_method("fork", force=True)
    except RuntimeError:
        pass
    ncpu = os.cpu_count() or 1
    t0 = time.perf_counter()
    with mp.Pool(ncpu) as p:
        rates = p.map(worker, range(ncpu))
    wall = time.perf_counter() - t0
    print(json.dumps({
        "cores": ncpu,
        "gflops": round(sum(rates), 2),
        "per_core": round(sum(rates) / ncpu, 2),
        "wall_s": round(wall, 1),
        "N": N,
    }))
