r"""Per-layer expert ranking for the hot-expert store's static fill: reads an imatrix GGUF (llama-imatrix `.counts` =
tokens routed to each expert; gate/up/down of a layer share one routing, so ffn_up_exps is read) and writes one line per
MoE layer, hottest expert first:  `<layer> <e0> <e1> ... <e255>`.  The engine patch (LAB_EXPERT_HOT_RANKING,
hotstore_block_patch.py) fills each layer's S slots from the first S ids.

    python3 expert_ranking.py <imatrix.gguf> <out.ranking.txt>      (needs llama.cpp's gguf-py; rigby has it)
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/llama.cpp-rpcasync/gguf-py"))   # llama.cpp's gguf-py on the tablet head
import gguf  # noqa: E402

src, out = sys.argv[1], sys.argv[2]
counts = {}
for t in gguf.GGUFReader(src).tensors:
    if t.name.endswith(".counts") and "ffn_up_exps" in t.name:
        counts[int(t.name.split(".")[1])] = [float(x) for x in t.data.flatten()]
assert counts, "no ffn_up_exps .counts tensors in %s" % src
n_exp = {len(c) for c in counts.values()}
assert len(n_exp) == 1, "layers disagree on the expert count: %s" % n_exp
with open(out, "w", encoding="ascii", newline="\n") as f:
    for layer in sorted(counts):
        c = counts[layer]
        order = sorted(range(len(c)), key=lambda e: (-c[e], e))
        f.write("%d %s\n" % (layer, " ".join(map(str, order))))
print("wrote %d layers x %d experts -> %s" % (len(counts), n_exp.pop(), out))
