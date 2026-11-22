r"""Let ggml-cpu repack Q2_K tensors on AVX2 CPUs (the 5800X has no AVX-512).

    python q2k_repack_avx2.py <llama.cpp source dir>

ggml_repack_get_optimal_repack_type() only returns q2_K_8x8_q8_K when ggml_cpu_has_avx512(), but both kernels it
dispatches to have AVX2 bodies in arch/x86/repack.cpp: ggml_gemv_q2_K_8x8_q8_K under `#if defined(__AVX2__)` and
ggml_gemm_q2_K_8x8_q8_K under `#if defined(__AVX2__) || defined(__AVX512F__)` with an AVX-512BW/DQ fast path and an
AVX2 path after it. On Flash-Next q2k the Q2_K gate/up experts (~17-20 GB CPU-side) are the half of the expert
weights that today run the plain kernel. Exact-match edit; fails loudly if the source drifted."""
import sys, os

F = os.path.join(sys.argv[1], "ggml", "src", "ggml-cpu", "repack.cpp")
text = open(F, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in text else "\n"
old = """    } else if (cur->type == GGML_TYPE_Q2_K) {
        if (ggml_cpu_has_avx512()) {""".replace("\n", nl)
new = """    } else if (cur->type == GGML_TYPE_Q2_K) {
        if (ggml_cpu_has_avx512() || ggml_cpu_has_avx2()) {""".replace("\n", nl)
if new in text:
    print("already patched")
    sys.exit(0)
n = text.count(old)
assert n == 1, "expected exactly one Q2_K selector, found %d" % n
open(F, "w", encoding="utf-8", newline="").write(text.replace(old, new))
print("patched", F, "(Q2_K repack enabled on AVX2)")
