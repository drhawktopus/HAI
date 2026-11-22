r"""How skewed is the A3B's expert routing? Reads an imatrix GGUF (llama-imatrix: `<tensor>.counts` = tokens routed to
each expert of that tensor) and prints, per layer and overall, the share of routed tokens the hottest k experts carry,
and how many experts carry 50 / 80 / 90 %. The pinning question: with ~46 experts per layer of spare 1060 memory
(~2.5 GB at ~1.35 MB per expert-layer at Q3), what share of the CPU-held expert work could move to the card?

    python3 routing_skew.py <imatrix.gguf> [k ...]      (needs llama.cpp's gguf-py on sys.path; rigby has it)
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/llama.cpp-rpcasync/gguf-py"))   # llama.cpp's gguf-py on the tablet head
import gguf  # noqa: E402

path = sys.argv[1]
KS = [int(x) for x in sys.argv[2:]] or [16, 32, 46, 64, 128]
r = gguf.GGUFReader(path)
counts = {}
for t in r.tensors:
    if t.name.endswith(".counts") and "ffn_up_exps" in t.name:        # gate/up/down of a layer share one routing
        layer = int(t.name.split(".")[1])
        counts[layer] = [float(x) for x in t.data.flatten()]
assert counts, "no ffn_up_exps .counts tensors in %s - wrong file or format, not a flat routing" % path
n_exp = len(next(iter(counts.values())))
print("layers %d, experts per layer %d, routed tokens per layer (median) %.0f" % (
    len(counts), n_exp, sorted(sum(c) for c in counts.values())[len(counts) // 2]))


def cover(c, k):
    s = sorted(c, reverse=True)
    tot = sum(s)
    return sum(s[:k]) / tot if tot else 0.0


def need(c, share):
    s = sorted(c, reverse=True)
    tot, acc = sum(s), 0.0
    for i, x in enumerate(s, 1):
        acc += x
        if acc >= share * tot:
            return i
    return len(s)


uniform = {k: k / n_exp for k in KS}
print("share of routed tokens carried by the hottest k experts (uniform routing would give k/%d):" % n_exp)
print("layer " + " ".join("top%-4d" % k for k in KS) + "  n50 n80 n90  never")
for layer in sorted(counts):
    c = counts[layer]
    print("%5d " % layer + " ".join("%6.1f%%" % (100 * cover(c, k)) for k in KS)
          + "  %3d %3d %3d  %5d" % (need(c, .5), need(c, .8), need(c, .9), sum(1 for x in c if x == 0)))
for k in KS:
    vals = sorted(cover(c, k) for c in counts.values())
    print("top%-3d across layers: median %.1f%% (min %.1f, max %.1f) vs uniform %.1f%%" % (
        k, 100 * vals[len(vals) // 2], 100 * vals[0], 100 * vals[-1], 100 * uniform[k]))
