"""Are two GGUFs the same weights? Hashes every tensor's raw bytes in both files and reports: tensors in both with
identical bytes / with different bytes (and their types), and tensors only in one file. For example: whether a
vendor's MTP build of a quant is the same weights plus the MTP layer (so no quality check is needed).

    python3 gguf_tensor_diff.py <a.gguf> <b.gguf>      (needs llama.cpp's gguf-py)"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.expanduser("~/llama.cpp-rpcasync/gguf-py"))   # llama.cpp's gguf-py on the tablet head
import gguf  # noqa: E402


def tensors(path):
    out = {}
    for t in gguf.GGUFReader(path).tensors:
        out[t.name] = (t.tensor_type.name, hashlib.sha256(t.data.tobytes()).hexdigest(), int(t.n_bytes))
    return out


A, B = tensors(sys.argv[1]), tensors(sys.argv[2])
both = sorted(set(A) & set(B))
assert both, "no tensor names in common - wrong files, the comparison is empty"
same = [n for n in both if A[n][1] == B[n][1]]
diff = [n for n in both if A[n][1] != B[n][1]]
print("A %d tensors, B %d, in both %d: identical %d, DIFFERENT %d" % (len(A), len(B), len(both), len(same), len(diff)))
for n in diff[:20]:
    print("  differs: %-40s A %s %d B  %s %d" % (n, A[n][0], A[n][2], B[n][0], B[n][2]))
only_b = sorted(set(B) - set(A))
only_a = sorted(set(A) - set(B))
print("only in A: %d %s" % (len(only_a), only_a[:10]))
print("only in B: %d (%.0f MB) e.g. %s" % (len(only_b), sum(B[n][2] for n in only_b) / 1e6, only_b[:8]))
