r"""Header-only comparison of two GGUFs: per tensor, the quant type and size in each file (no data hashing - instant).
Lists every shared tensor whose TYPE differs, totals bytes per file over the shared tensors, and the tensors only in one.

    python3 gguf_type_diff.py <a.gguf> <b.gguf>      (needs llama.cpp's gguf-py; rigby has it)
"""
import collections
import os
import sys

sys.path.insert(0, os.path.expanduser("~/llama.cpp-rpcasync/gguf-py"))   # llama.cpp's gguf-py on the tablet head
import gguf  # noqa: E402


def info(path):
    return {t.name: (t.tensor_type.name, int(t.n_bytes)) for t in gguf.GGUFReader(path).tensors}


A, B = info(sys.argv[1]), info(sys.argv[2])
both = sorted(set(A) & set(B))
assert both, "no shared tensor names - the comparison is empty"
changed = [n for n in both if A[n][0] != B[n][0]]
print("shared %d tensors; type changed on %d; shared bytes A %.0f MB, B %.0f MB" % (
    len(both), len(changed), sum(A[n][1] for n in both) / 1e6, sum(B[n][1] for n in both) / 1e6))
pairs = collections.Counter((A[n][0], B[n][0]) for n in changed)
for (ta, tb), k in pairs.most_common():
    print("  %3d tensors  %s -> %s" % (k, ta, tb))
for n in changed:
    print("    %-34s %-8s %7.1f MB -> %-8s %7.1f MB" % (n, A[n][0], A[n][1] / 1e6, B[n][0], B[n][1] / 1e6))
print("only in B: %d tensors, %.0f MB" % (len(set(B) - set(A)), sum(B[n][1] for n in set(B) - set(A)) / 1e6))
