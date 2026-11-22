"""Lab patch: allow CUDA graphs on pre-Volta cards (a GTX 1060 is Pascal, cc 6.1) when LAB_CUDA_GRAPHS_PRE_VOLTA
is set. OFF unless set - the default build behaves exactly as upstream.

    python cuda_graphs_pre_volta_patch.py <llama.cpp source dir>

Why: on a GTX 1060, issuing GPU work costs ~8 of 70 ms per written token, and forcing graphs OFF changes nothing - they
never engage, because ggml_cuda_graph_set_enabled() disables them for any card below GGML_CUDA_CC_VOLTA. The only other
gate on a MoE model's decode, MUL_MAT_ID, does not fire there (one token, quantized experts -> the MMVQ path,
ggml_cuda_mul_mat_id_needs_sync() returns false). Upstream's reason for the arch gate is not stated in the code; this
is an experiment, measured against the same build with the variable unset.
Exact-match edit; refuses unless the anchor matches once."""
import os, sys

F = os.path.join(sys.argv[1], "ggml", "src", "ggml-cuda", "ggml-cuda.cu")
text = open(F, encoding="utf-8", newline="").read()
if "LAB_CUDA_GRAPHS_PRE_VOLTA" in text:
    print("already patched")
    sys.exit(0)
nl = "\r\n" if "\r\n" in text else "\n"
old = "        if (ggml_cuda_info().devices[cuda_ctx->device].cc < GGML_CUDA_CC_VOLTA) {".replace("\n", nl)
new = ("        // lab patch: LAB_CUDA_GRAPHS_PRE_VOLTA lets a pre-Volta card (rigby's GTX 1060) use CUDA graphs\n"
       "        if (ggml_cuda_info().devices[cuda_ctx->device].cc < GGML_CUDA_CC_VOLTA && !getenv(\"LAB_CUDA_GRAPHS_PRE_VOLTA\")) {"
       ).replace("\n", nl)
c = text.count(old)
assert c == 1, "anchor matched %d times (want 1)" % c
text = text.replace(old, new)
open(F, "w", encoding="utf-8", newline="").write(text)
print("patched", F, "(CUDA graphs on pre-Volta when LAB_CUDA_GRAPHS_PRE_VOLTA is set)")
