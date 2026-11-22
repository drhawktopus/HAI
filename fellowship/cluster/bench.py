"""DGEMM throughput probe. GFLOPS = 2*N^3 / seconds.
HPL is essentially DGEMM, so this is a fair proxy for Rmax."""
import time

import numpy as np


def bench(n, reps=3):
    a = np.random.rand(n, n)
    b = np.random.rand(n, n)
    np.dot(a, b)  # warm up / let BLAS settle
    best = None
    for _ in range(reps):
        t0 = time.perf_counter()
        np.dot(a, b)
        dt = time.perf_counter() - t0
        if best is None or dt < best:
            best = dt
    return 2.0 * n ** 3 / best / 1e9, best


print("numpy %s" % np.__version__)
try:
    cfg = np.__config__.show(mode="dicts")
    blas = cfg.get("Build Dependencies", {}).get("blas", {})
    print("blas: %s %s" % (blas.get("name"), blas.get("version")))
except Exception:
    pass

for n in (512, 1024, 1536):
    g, dt = bench(n)
    print("N=%-5d  %7.3f s  %6.2f GFLOP/s (fp64)" % (n, dt, g))
