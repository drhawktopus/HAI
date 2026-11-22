"""5-node distributed matrix multiply across the tablet cluster, all over WiFi.

C = A x B, split into row-blocks WEIGHTED BY CORE COUNT (the cluster is
heterogeneous: 8/8/8/4/4), so every node finishes at roughly the same time
instead of the octa-cores idling on the quad-cores.

Transport is ssh on 8022 to every node -- no adb, no USB.
Times send / compute / recv separately, because on a shared WiFi radio the
transfer is expected to dominate and that is the point of the measurement.
"""
import json
import os
import subprocess
import sys
import threading
import time

import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # the fellowship folder
import roster  # noqa: E402

SC = os.path.dirname(os.path.abspath(__file__))
KEY = roster.S.setting("TABLET_SSH_KEY", "")

_T = roster.hosts()
_ALL = [
    {"name": "gandalf", "ip": _T["gandalf"], "cores": 8},
    {"name": "frodo", "ip": _T["frodo"], "cores": 8},
    {"name": "sam", "ip": _T["sam"], "cores": 8},
    {"name": "pippin", "ip": _T["pippin"], "cores": 4},
    {"name": "merry", "ip": _T["merry"], "cores": 4},
]

# CLUSTER_NODES="ip,ip,ip" selects a subset (used by the overnight battery)
_sel = os.environ.get("CLUSTER_NODES", "").strip()
if _sel:
    _want = [s.strip() for s in _sel.split(",") if s.strip()]
    NODES = [n for n in _ALL if n["ip"] in _want]
else:
    NODES = _ALL

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3072

_COMMON = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "ConnectTimeout=20", "-o", "LogLevel=ERROR"]
SSH = ["-i", KEY, "-p", "8022"] + _COMMON   # ssh uses lowercase -p
SCP = ["-i", KEY, "-P", "8022"] + _COMMON   # scp uses uppercase -P


def run(cmd):
    return subprocess.run(cmd, capture_output=True)


def do_node(node, a_block, b, out, idx):
    t = {"name": node["name"], "cores": node["cores"], "rows": a_block.shape[0]}
    inp = os.path.join(SC, "in5_%s.npz" % node["name"])
    outp = os.path.join(SC, "out5_%s.npy" % node["name"])
    np.savez(inp, a=a_block, b=b)
    t["mb_out"] = os.path.getsize(inp) / 1e6

    t0 = time.perf_counter()
    run(["scp"] + SCP + [inp, "%s:in5.npz" % node["ip"]])
    t["send_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    r = run(["ssh"] + SSH + [node["ip"], "python worker.py in5.npz out5.npy"])
    t["exec_s"] = time.perf_counter() - t0
    try:
        t["w"] = json.loads(r.stderr.decode(errors="replace").strip().splitlines()[-1])
    except Exception:
        t["w"] = {"err": r.stderr.decode(errors="replace")[-200:]}

    t0 = time.perf_counter()
    run(["scp"] + SCP + ["%s:out5.npy" % node["ip"], outp])
    t["recv_s"] = time.perf_counter() - t0

    out[idx] = (t, outp)


if __name__ == "__main__":
    total_cores = sum(n["cores"] for n in NODES)
    print("=== 5-NODE DISTRIBUTED C = A x B   N=%d ===" % N)
    print("nodes=%d  cores=%d  (weighted split)\n" % (len(NODES), total_cores))

    for n in NODES:
        run(["scp"] + SCP + [os.path.join(SC, "worker.py"), "%s:worker.py" % n["ip"]])

    rng = np.random.default_rng(0)
    A = rng.random((N, N))
    B = rng.random((N, N))

    # weighted row split
    bounds, acc = [], 0
    for i, n in enumerate(NODES):
        rows = N - acc if i == len(NODES) - 1 else int(round(N * n["cores"] / total_cores))
        bounds.append((acc, acc + rows))
        acc += rows

    out = [None] * len(NODES)
    ths = []
    t_wall = time.perf_counter()
    for i, n in enumerate(NODES):
        lo, hi = bounds[i]
        th = threading.Thread(target=do_node, args=(n, A[lo:hi], B, out, i))
        th.start(); ths.append(th)
    for th in ths:
        th.join()
    wall = time.perf_counter() - t_wall

    print("%-10s %5s %6s %8s %8s %8s %9s" %
          ("node", "cores", "rows", "send_s", "exec_s", "recv_s", "node_GF"))
    tot_gf = 0.0
    parts = []
    for i, n in enumerate(NODES):
        t, outp = out[i]
        gf = t["w"].get("gflops", 0) if isinstance(t["w"], dict) else 0
        tot_gf += gf if isinstance(gf, (int, float)) else 0
        print("%-10s %5d %6d %8.2f %8.2f %8.2f %9s" %
              (t["name"], t["cores"], t["rows"], t["send_s"], t["exec_s"], t["recv_s"], gf))
        parts.append(np.load(outp))

    C = np.vstack(parts)
    flops = 2.0 * N ** 3
    eff = flops / wall / 1e9
    print("\nsum of node compute rates : %.2f GFLOP/s" % tot_gf)
    print("effective end-to-end      : %.2f GFLOP/s   (wall %.2f s)" % (eff, wall))
    if tot_gf:
        print("interconnect tax          : %.1f%%" % (100 * (1 - eff / tot_gf)))
    ok = np.allclose(C[:64], np.dot(A[:64], B), rtol=1e-9, atol=1e-9)
    print("\nRESULT VERIFIED vs local numpy: %s" % ok)
