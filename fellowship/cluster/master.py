"""Distributed matrix multiply across the tablet cluster.

Softserve is the head node: generates A and B, splits A into row-blocks,
ships a block + all of B to each worker, gathers the C-blocks, verifies.

Two transports on purpose:
  gandalf -> ssh over WiFi   (has an IP)
  sam -> adb over USB    (WiFi blocked by the GSI SAE bug)

Times transfer and compute separately, because on a WiFi-class interconnect
the transfer is expected to dominate -- that is the Beowulf lesson.
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
ADB = roster.S.setting("ADB_EXE", "adb")
PFX = "/data/data/com.termux/files/usr"
HOME = "/data/data/com.termux/files/home"
ENVV = (
    "export PREFIX=%s HOME=%s LD_LIBRARY_PATH=%s/lib "
    "PATH=%s/bin:$PATH TMPDIR=%s/tmp;" % (PFX, HOME, PFX, PFX, PFX)
)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2048

_T = roster.hosts()
NODES = [
    {"name": "gandalf", "transport": "ssh", "host": _T["gandalf"], "port": "8022"},
    {"name": "sam", "transport": "adb", "serial": roster.tablets()["sam"]["serial"]},
]

SSH_OPTS = [
    "-i", KEY, "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=15",
    "-o", "LogLevel=ERROR",
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


def push(node, local, remote_name):
    if node["transport"] == "ssh":
        return run(["scp"] + SSH_OPTS + ["-P", node["port"], local,
                                         "%s:%s" % (node["host"], remote_name)])
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    r = run([ADB, "-s", node["serial"], "push", local, "/sdcard/" + remote_name], env=env)
    run([ADB, "-s", node["serial"], "shell",
         "su -c 'cp /sdcard/%s %s/%s && chown u0_a133:u0_a133 %s/%s'"
         % (remote_name, HOME, remote_name, HOME, remote_name)], env=env)
    return r


def pull(node, remote_name, local):
    if node["transport"] == "ssh":
        return run(["scp"] + SSH_OPTS + ["-P", node["port"],
                                         "%s:%s" % (node["host"], remote_name), local])
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    run([ADB, "-s", node["serial"], "shell",
         "su -c 'cp %s/%s /sdcard/%s && chmod 666 /sdcard/%s'"
         % (HOME, remote_name, remote_name, remote_name)], env=env)
    return run([ADB, "-s", node["serial"], "pull", "/sdcard/" + remote_name, local], env=env)


def execute(node, cmd):
    if node["transport"] == "ssh":
        return run(["ssh"] + SSH_OPTS + ["-p", node["port"], node["host"], cmd])
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    return run([ADB, "-s", node["serial"], "shell",
                "run-as com.termux sh -c '%s cd %s && %s'" % (ENVV, HOME, cmd)], env=env)


def stage_worker(node):
    execute(node, "true")
    push(node, os.path.join(SC, "worker.py"), "worker.py")


def do_node(node, a_block, b, results, idx):
    t = {}
    inp = os.path.join(SC, "in_%s.npz" % node["name"])
    outp = os.path.join(SC, "out_%s.npy" % node["name"])
    np.savez(inp, a=a_block, b=b)
    t["bytes_out"] = os.path.getsize(inp)

    t0 = time.perf_counter()
    push(node, inp, "in.npz")
    t["send_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    r = execute(node, "python worker.py in.npz out.npy")
    t["exec_s"] = time.perf_counter() - t0
    try:
        t["worker"] = json.loads(r.stderr.decode(errors="replace").strip().splitlines()[-1])
    except Exception:
        t["worker"] = {"raw": r.stderr.decode(errors="replace")[-300:]}

    t0 = time.perf_counter()
    pull(node, "out.npy", outp)
    t["recv_s"] = time.perf_counter() - t0
    t["bytes_in"] = os.path.getsize(outp) if os.path.exists(outp) else 0

    results[idx] = (t, outp)


if __name__ == "__main__":
    print("=== distributed C = A x B   N=%d   nodes=%d ===" % (N, len(NODES)))
    rng = np.random.default_rng(0)
    A = rng.random((N, N))
    B = rng.random((N, N))
    print("A,B generated: %.1f MB each\n" % (A.nbytes / 1e6))

    for n in NODES:
        stage_worker(n)

    step = (N + len(NODES) - 1) // len(NODES)
    blocks = [(i, min(i + step, N)) for i in range(0, N, step)]

    results = [None] * len(NODES)
    threads = []
    t_wall0 = time.perf_counter()
    for i, node in enumerate(NODES):
        lo, hi = blocks[i]
        th = threading.Thread(target=do_node, args=(node, A[lo:hi], B, results, i))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    wall = time.perf_counter() - t_wall0

    print("%-10s %9s %9s %9s %10s %9s" %
          ("node", "send_s", "exec_s", "recv_s", "node_GF", "MB_out"))
    total_compute_gf = 0.0
    parts = []
    for i, node in enumerate(NODES):
        t, outp = results[i]
        w = t.get("worker", {})
        gf = w.get("gflops", 0)
        total_compute_gf += gf if isinstance(gf, (int, float)) else 0
        print("%-10s %9.2f %9.2f %9.2f %10s %9.1f" %
              (node["name"], t["send_s"], t["exec_s"], t["recv_s"],
               gf, t["bytes_out"] / 1e6))
        parts.append(np.load(outp))

    C = np.vstack(parts)
    flops = 2.0 * N ** 3
    print("\nsum of node compute rates : %.2f GFLOP/s" % total_compute_gf)
    print("effective end-to-end rate : %.2f GFLOP/s  (wall %.2f s)" % (flops / wall / 1e9, wall))
    print("interconnect tax          : %.1f%% of peak lost to data movement"
          % (100.0 * (1 - (flops / wall / 1e9) / total_compute_gf)) if total_compute_gf else "")

    ref = np.dot(A[:64], B)
    ok = np.allclose(C[:64], ref, rtol=1e-9, atol=1e-9)
    print("\nRESULT VERIFIED against local numpy: %s" % ok)
