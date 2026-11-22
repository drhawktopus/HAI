"""Expert Deferral, one copy: the layer's FFN input, expert ids and weights cross to the CPU ONCE.

    python moe_defer_onecopy.py <llama.cpp source dir> (after moe_expert_defer.py and moe_defer_overlap.py)

Both immediate and deferred expert groups take views of three pinned CPU copies (lab_defer_cpu_x / _sel / _w),
ensuring a deferring layer moves exactly what the plain graph moves (input, ids, weights). Views of a CPU tensor
stay on the CPU, preventing additional copies. The shared expert retains the GPU-side x. This patch performs an
exact-match edit that must hit once, or it fails."""
import sys, os

SRC = sys.argv[1]
MARK = "lab patch: defer one copy"
path = os.path.join(SRC, "src", "models", "qwen4exp.cpp")
text = open(path, encoding="utf-8", newline="").read()
if MARK in text:
    print("already patched", path)
    sys.exit(0)
assert "lab patch: defer overlap" in text, "apply moe_expert_defer.py and moe_defer_overlap.py first"
nl = "\r\n" if "\r\n" in text else "\n"

old = """        ggml_tensor * s_imm = nullptr;
        ggml_tensor * w_imm = nullptr;
        take(0, k_imm, &s_imm, &w_imm);

        lab_pend.x = x; lab_pend.il = il; lab_pend.k = lab_defer_k; lab_pend.inject = inj;
        take(k_imm, lab_defer_k, &lab_pend.sel, &lab_pend.w);

        // lab patch: defer overlap - the deferred experts' inputs become CPU tensors HERE, inside this layer's CPU
        // split, so the deferred split at layer L+1 has nothing to copy from the GPU
        lab_pend.x = ggml_cont(ctx0, x);
        cb(lab_pend.x, "lab_defer_cpu_x", il);
        cb(lab_pend.sel, "lab_defer_cpu_sel", il);
        cb(lab_pend.w->view_src, "lab_defer_cpu_w", il);
        ggml_build_forward_expand(gf, lab_pend.x);
        ggml_build_forward_expand(gf, lab_pend.sel);
        ggml_build_forward_expand(gf, lab_pend.w);

        ggml_tensor * out = build_moe_ffn(x, L.ffn_gate_inp,"""

new = """        // lab patch: defer one copy - x, the ids and the weights cross to the CPU once (pinned conts); the immediate
        // and the deferred experts both read views of those copies
        (void) take;
        ggml_tensor * xc   = ggml_cont(ctx0, x);
        ggml_tensor * selc = ggml_cont(ctx0, sel);
        ggml_tensor * wc   = ggml_cont(ctx0, w);
        cb(xc,   "lab_defer_cpu_x",   il);
        cb(selc, "lab_defer_cpu_sel", il);
        cb(wc,   "lab_defer_cpu_w",   il);
        auto cpu_take = [&](int64_t off, int64_t k, ggml_tensor ** s, ggml_tensor ** ww, const char * ns, const char * nw) {
            *s = ggml_cont(ctx0, ggml_view_2d(ctx0, selc, k, nt, selc->nb[1], off*selc->nb[0]));
            ggml_tensor * wv = ggml_cont(ctx0, ggml_view_2d(ctx0, wc, k, nt, wc->nb[1], off*wc->nb[0]));
            cb(*s, ns, il);
            cb(wv, nw, il);
            *ww = ggml_reshape_3d(ctx0, wv, 1, k, nt);
        };
        ggml_tensor * s_imm = nullptr;
        ggml_tensor * w_imm = nullptr;
        cpu_take(0, k_imm, &s_imm, &w_imm, "lab_defer_cpu_simm", "lab_defer_cpu_wimm");

        lab_pend.x = xc; lab_pend.il = il; lab_pend.k = lab_defer_k; lab_pend.inject = inj;
        cpu_take(k_imm, lab_defer_k, &lab_pend.sel, &lab_pend.w, "lab_defer_cpu_sdef", "lab_defer_cpu_wdef");
        ggml_build_forward_expand(gf, xc);
        ggml_build_forward_expand(gf, lab_pend.sel);
        ggml_build_forward_expand(gf, lab_pend.w);

        ggml_tensor * out = build_moe_ffn(xc, L.ffn_gate_inp,"""

o, n_ = old.replace("\n", nl), new.replace("\n", nl)
assert text.count(o) == 1, "deferral block not found once - source drifted (found %d)" % text.count(o)
text = text.replace(o, n_)
open(path, "w", encoding="utf-8", newline="").write(text)
print("patched", path, "(1 edit)")
