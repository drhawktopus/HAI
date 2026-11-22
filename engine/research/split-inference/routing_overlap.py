r"""Do two routing counts agree on which experts are hot? For each MoE layer: the overlap of the two imatrix GGUFs'
top-k experts, and the share of B's routed tokens that A's top-k would catch (what a static hot set built from A hits
on B's text) next to B's own top-k share (the best any k-set could do on B) and chance (k / n_experts).

    python3 routing_overlap.py <A.imatrix.gguf> <B.imatrix.gguf> [k ...]      (needs llama.cpp's gguf-py; rigby has it)
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/llama.cpp-rpcasync/gguf-py"))   # llama.cpp's gguf-py on the tablet head
import gguf  # noqa: E402


def counts(path):
    out = {}
    for t in gguf.GGUFReader(path).tensors:
        if t.name.endswith(".counts") and "ffn_up_exps" in t.name:
            out[int(t.name.split(".")[1])] = [float(x) for x in t.data.flatten()]
    assert out, "no ffn_up_exps .counts in %s" % path
    return out


A, B = counts(sys.argv[1]), counts(sys.argv[2])
KS = [int(x) for x in sys.argv[3:]] or [27, 48]
layers = sorted(set(A) & set(B))
assert len(layers) == len(A) == len(B), "layer sets differ: %d / %d / %d" % (len(A), len(B), len(layers))
n = len(A[layers[0]])


def top(c, k):
    return set(sorted(range(len(c)), key=lambda e: (-c[e], e))[:k])


print("layers %d, experts %d; B routed per layer (median) %.0f" % (len(layers), n, sorted(sum(B[l]) for l in layers)[len(layers) // 2]))
for k in KS:
    ov, cat, best = [], [], []
    for l in layers:
        ta, tb, tot = top(A[l], k), top(B[l], k), sum(B[l])
        ov.append(len(ta & tb) / k)
        cat.append(sum(B[l][e] for e in ta) / tot)
        best.append(sum(B[l][e] for e in tb) / tot)
    med = lambda v: sorted(v)[len(v) // 2]
    print("k=%d: top-k overlap median %.0f%% | A's set catches %.1f%% of B (median; min %.1f max %.1f) | B's own best %.1f%% | chance %.1f%%"
          % (k, 100 * med(ov), 100 * med(cat), 100 * min(cat), 100 * max(cat), 100 * med(best), 100 * k / n))
