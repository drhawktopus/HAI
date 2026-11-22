"""Aggregate DGEMM throughput with one single-threaded process per core.
This is the shape HPL actually runs in: 1 MPI rank per core, serial BLAS.
"""
import multiprocessing as mp
import os
import time

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np  # noqa: E402  (must follow env vars)

N = 1024
REPS = 3


def worker(_):
    a = np.random.rand(N, N)
    b = np.random.rand(N, N)
    np.dot(a, b)
    best = None
    for _ in range(REPS):
        t0 = time.perf_counter()
        np.dot(a, b)
        dt = time.perf_counter() - t0
        if best is None or dt < best:
            best = dt
    return 2.0 * N ** 3 / best / 1e9


if __name__ == "__main__":
    ncpu = os.cpu_count()
    print("cores: %d   N=%d" % (ncpu, N))
    for procs in (1, 2, 4, ncpu):
        t0 = time.perf_counter()
        with mp.Pool(procs) as p:
            res = p.map(worker, range(procs))
        wall = time.perf_counter() - t0
        print(
            "procs=%-3d  aggregate %6.2f GFLOP/s   (per-proc %.2f)  wall %.1fs"
            % (procs, sum(res), sum(res) / procs, wall)
        )
