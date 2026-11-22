"""Cluster worker: compute one row-block of C = A x B.

Reads in.npz  (a_block, b)  ->  writes out.npy (c_block)
Uses one single-threaded process per core, because Termux's OpenBLAS has
threading compiled out (a single np.dot only ever uses one A53 core).
"""
import json
import multiprocessing as mp
import os
import sys
import time

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np  # noqa: E402

IN = sys.argv[1]
OUT = sys.argv[2]

_G = {}


def _part(args):
    lo, hi = args
    return np.dot(_G["a"][lo:hi], _G["b"])


def _init(a, b):
    _G["a"] = a
    _G["b"] = b


if __name__ == "__main__":
    # Python 3.14 defaults to 'forkserver', which PICKLES initargs to every
    # child -- shipping all of B to all 8 workers and dwarfing the actual
    # math (measured 0.69 GFLOP/s vs 20.9 expected). fork() inherits the
    # arrays copy-on-write instead.
    try:
        mp.set_start_method("fork", force=True)
    except RuntimeError:
        pass

    t0 = time.perf_counter()
    z = np.load(IN)
    a = z["a"]
    b = z["b"]
    t_load = time.perf_counter() - t0

    ncpu = os.cpu_count() or 1
    rows = a.shape[0]
    step = (rows + ncpu - 1) // ncpu
    chunks = [(i, min(i + step, rows)) for i in range(0, rows, step)]

    # set globals BEFORE forking so children inherit them without pickling
    _G["a"] = a
    _G["b"] = b

    t1 = time.perf_counter()
    with mp.Pool(ncpu) as p:
        parts = p.map(_part, chunks)
    c = np.vstack(parts)
    t_compute = time.perf_counter() - t1

    t2 = time.perf_counter()
    np.save(OUT, c)
    t_save = time.perf_counter() - t2

    flops = 2.0 * a.shape[0] * a.shape[1] * b.shape[1]
    sys.stderr.write(
        json.dumps(
            {
                "rows": int(rows),
                "cores": ncpu,
                "load_s": round(t_load, 3),
                "compute_s": round(t_compute, 3),
                "save_s": round(t_save, 3),
                "gflops": round(flops / t_compute / 1e9, 2),
            }
        )
        + "\n"
    )
