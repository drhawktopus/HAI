#!/usr/bin/env python
"""Random-access DRAM probe. Prints one number: millions of gathers/second.

Shipped as a FILE and scp'd to the node, never embedded in an ssh command string -- inline python
through bash -> ssh -> sh loses its quoting and returns nothing, which reads identically to "the
node is unmeasurable". That mistake has now been made four times in one session.

48 MB working set and RANDOM access on purpose: cache-resident or sequential probes read HEALTHY on
a node whose memory state has degraded, because the prefetcher hides the fault. See
Tablet-Crunch-Fleet-Integration.md §11 -- sam passed five such probes while running 3.4x slow.
"""
import sys
import time

import numpy as np

N = 48 * 1024 * 1024 // 4
a = np.ones(N, dtype=np.float32)
idx = np.random.randint(0, N, size=N // 16).astype(np.int64)
t0 = time.time()
g = a[idx]
el = time.time() - t0
sys.stdout.write("%.2f\n" % ((N / 16) / max(el, 1e-9) / 1e6))
