r"""Expert Deferral, Stage A (graph only) for qwen4exp: KTransformers' technique (SOSP'25) in llama.cpp's graph.

    python moe_expert_defer.py <llama.cpp source dir>

Env (read once per process), all off by default so the build serves as its own control:
    LAB_MOE_DEFER=k            defer the k LOWEST-weighted of the routed experts (0 = off = the upstream graph)
    LAB_MOE_DEFER_LAYERS=n     only layers il < n (0 = every layer that may defer); set to --n-cpu-moe so only
                               CPU-side layers defer
    LAB_MOE_DEFER_MAXTOK=t     only batches of <= t tokens (decode + MTP verify; never prompt processing), default 4

A deferred expert of layer L is computed with layer L's input and weights, and joins the residual at the END of
layer L+1 instead of the end of L (it skips layer L+1's input). Routing weights are normalised over ALL selected
experts first, so immediate + deferred = the full mixture, just one layer late. Never deferred: the last two layers
(nothing pending at the final gather), warmup (n_expert_used == n_expert), the MTP head (it calls build_layer_ffn).

Stage A alone does NOT overlap anything: ggml_backend_sched_compute_splits synchronises the GPU before every CPU
split that follows a GPU split. This stage measures the LOSSY part (output quality, MTP acceptance). The deferred
experts are built after layer L+1's attention is in the graph, which is the node order Stage B (a scheduler that
skips that synchronisation when safe) needs.

Two edits to llm_graph_context::build_moe_ffn (llama-graph.h/.cpp): a `weights_in` parameter (precomputed routing
weights; skips the router), and the final aggregation bounded by the n_expert_used ARGUMENT when weights_in is
given (it otherwise sums hparams.n_expert_used(il) views = 10, reading past a 4-expert tensor).
Every edit is an exact match that must hit the stated count, or the patch fails."""
import sys, os

SRC = sys.argv[1]
MARK = "lab patch: expert deferral"


def edit(rel, pairs):
    path = os.path.join(SRC, *rel.split("/"))
    text = open(path, encoding="utf-8", newline="").read()
    if MARK in text:
        print("already patched", path)
        return
    nl = "\r\n" if "\r\n" in text else "\n"
    for old, new, want in pairs:
        o, n_ = old.replace("\n", nl), new.replace("\n", nl)
        got = text.count(o)
        assert got == want, "%s: expected %d match(es), found %d for:\n%s" % (rel, want, got, old[:200])
        text = text.replace(o, n_)
    assert MARK in text, "%s: no edit carries the marker" % rel
    open(path, "w", encoding="utf-8", newline="").write(text)
    print("patched", path, "(%d edits)" % len(pairs))


# --- llama-graph.h: both overload declarations gain weights_in -------------------------------------------------
edit("src/llama-graph.h", [
    ("""             ggml_tensor * selected_experts_in = nullptr) const;""",
     """             ggml_tensor * selected_experts_in = nullptr,
             ggml_tensor * weights_in = nullptr) const; // lab patch: expert deferral""", 2),
])

# --- llama-graph.cpp ---------------------------------------------------------------------------------------------
edit("src/llama-graph.cpp", [
    # first overload: signature + forwarding
    ("""         ggml_tensor * selected_experts_in) const {
    return build_moe_ffn(""",
     """         ggml_tensor * selected_experts_in,
         ggml_tensor * weights_in) const {
    return build_moe_ffn(""", 1),
    ("""        down_exps_s,
        selected_experts_in
    );
}""",
     """        down_exps_s,
        selected_experts_in,
        weights_in
    );
}""", 1),
    # second overload: signature
    ("""         ggml_tensor * selected_experts_in) const {
    const int64_t n_embd   = cur->ne[0];""",
     """         ggml_tensor * selected_experts_in,
         ggml_tensor * weights_in) const {
    const int64_t n_embd   = cur->ne[0];""", 1),
    # router: skipped entirely when the caller supplies selected experts AND their weights
    ("""    ggml_tensor * logits = nullptr;

    if (probs_in == nullptr) {""",
     """    // lab patch: expert deferral - with weights_in the caller has already routed: selected_experts_in
    // holds the experts and weights_in their final (normalised, scaled) weights, so the router is skipped
    ggml_tensor * selected_experts = selected_experts_in;
    ggml_tensor * weights          = weights_in;
    if (weights_in == nullptr) {
    ggml_tensor * logits = nullptr;

    if (probs_in == nullptr) {""", 1),
    ("""    ggml_tensor * selected_experts = selected_experts_in;
    if (selected_experts == nullptr) {""",
     """    if (selected_experts == nullptr) {""", 1),
    ("""    ggml_tensor * weights = ggml_get_rows(ctx0, probs, selected_experts); // [1, n_expert_used, n_tokens]""",
     """    weights = ggml_get_rows(ctx0, probs, selected_experts); // [1, n_expert_used, n_tokens]""", 1),
    ("""        cb(weights, "ffn_moe_weights_scaled", il);
    }

    //call early so that topk-moe can be used""",
     """        cb(weights, "ffn_moe_weights_scaled", il);
    }
    } // end of the router (skipped with weights_in)

    //call early so that topk-moe can be used""", 1),
    # aggregation: a caller-routed subset has n_expert_used views, not the layer's full count
    ("""    const uint32_t n_expert_used_il = hparams.n_expert_used(il);""",
     """    const uint32_t n_expert_used_il = weights_in ? (uint32_t) n_expert_used : hparams.n_expert_used(il);""", 1),
])

# --- qwen4exp.cpp: the deferral itself ---------------------------------------------------------------------------
edit("src/models/qwen4exp.cpp", [
    ("""#include <algorithm>
#include <cinttypes>
""",
     """#include <algorithm>
#include <cinttypes>
#include <cmath>
#include <cstdlib>

// lab patch: expert deferral - env knobs, read once per process (see patches\\moe_expert_defer.py)
static int lab_defer_env(const char * name, int dflt) {
    const char * v = getenv(name);
    return (v && *v) ? atoi(v) : dflt;
}
""", 1),
    # state + the deferring FFN, declared before the layer loop
    ("""    for (int il = 0; il < n_layer; ++il) {
        res->t_layer_inp[il] = res_hc;
""",
     """    static const int lab_defer_k      = lab_defer_env("LAB_MOE_DEFER", 0);
    static const int lab_defer_layers = lab_defer_env("LAB_MOE_DEFER_LAYERS", 0);
    static const int lab_defer_maxtok = lab_defer_env("LAB_MOE_DEFER_MAXTOK", 4);

    // the previous layer's deferred experts, waiting to be built after this layer's attention
    struct lab_defer_pend {
        ggml_tensor * x      = nullptr;   // layer L's FFN input
        ggml_tensor * sel    = nullptr;   // [k, n_tokens] deferred expert ids
        ggml_tensor * w      = nullptr;   // [1, k, n_tokens] their weights, normalised over all selected
        ggml_tensor * inject = nullptr;   // layer L's FFN hyper-connection injection
        int           il     = -1;
        int64_t       k      = 0;
    } lab_pend;

    auto lab_build_ffn_defer = [&](ggml_tensor * x, int il, ggml_tensor * inj) -> ggml_tensor * {
        const auto & L   = model.layers[il];
        const int64_t nt = x->ne[1];
        const int64_t k_imm = n_expert_used - lab_defer_k;

        // the router of build_moe_ffn for this arch (softmax, top-k, weights normalised over the k), done here so
        // the selection can be split while every weight keeps its share of the FULL mixture
        ggml_tensor * logits = build_lora_mm(L.ffn_gate_inp, x);
        cb(logits, "ffn_moe_logits", il);
        ggml_tensor * probs = ggml_soft_max(ctx0, logits);
        cb(probs, "ffn_moe_probs", il);
        ggml_tensor * sel = ggml_argsort_top_k(ctx0, probs, n_expert_used);   // descending: strongest first
        cb(sel, "ffn_moe_topk", il);
        ggml_tensor * w = ggml_get_rows(ctx0, ggml_reshape_3d(ctx0, probs, 1, n_expert, nt), sel);
        w = ggml_reshape_2d(ctx0, w, n_expert_used, nt);
        w = ggml_div(ctx0, w, ggml_clamp(ctx0, ggml_sum_rows(ctx0, w), 6.103515625e-5, INFINITY));
        if (hparams.expert_weights_scale != 0.0f && hparams.expert_weights_scale != 1.0f) {
            w = ggml_scale(ctx0, w, hparams.expert_weights_scale);
        }
        cb(w, "lab_defer_weights", il);
        ggml_build_forward_expand(gf, w);

        auto take = [&](int64_t off, int64_t k, ggml_tensor ** s, ggml_tensor ** ww) {
            *s  = ggml_cont(ctx0, ggml_view_2d(ctx0, sel, k, nt, sel->nb[1], off*sel->nb[0]));
            *ww = ggml_reshape_3d(ctx0,
                    ggml_cont(ctx0, ggml_view_2d(ctx0, w, k, nt, w->nb[1], off*w->nb[0])), 1, k, nt);
        };
        ggml_tensor * s_imm = nullptr;
        ggml_tensor * w_imm = nullptr;
        take(0, k_imm, &s_imm, &w_imm);

        lab_pend.x = x; lab_pend.il = il; lab_pend.k = lab_defer_k; lab_pend.inject = inj;
        take(k_imm, lab_defer_k, &lab_pend.sel, &lab_pend.w);

        ggml_tensor * out = build_moe_ffn(x, L.ffn_gate_inp, L.ffn_up_exps, L.ffn_gate_exps, L.ffn_down_exps,
                nullptr, n_expert, k_imm, LLM_FFN_SILU, true, hparams.expert_weights_scale,
                LLAMA_EXPERT_GATING_FUNC_TYPE_SOFTMAX, il,
                nullptr, L.ffn_gate_up_exps, L.ffn_up_exps_s, L.ffn_gate_exps_s, L.ffn_down_exps_s,
                s_imm, w_imm);
        cb(out, "ffn_moe_out", il);

        // shared expert, exactly as build_layer_ffn
        if (L.ffn_up_shexp != nullptr) {
            ggml_tensor * shexp = build_ffn(x,
                    L.ffn_up_shexp, NULL, L.ffn_up_shexp_s,
                    L.ffn_gate_shexp, NULL, L.ffn_gate_shexp_s,
                    L.ffn_down_shexp, NULL, L.ffn_down_shexp_s,
                    NULL, LLM_FFN_SILU, LLM_FFN_PAR, il);
            cb(shexp, "ffn_shexp", il);
            ggml_tensor * sg = ggml_sigmoid(ctx0, build_lora_mm(L.ffn_gate_inp_shexp, x));
            cb(sg, "shared_expert_gate_sigmoid", il);
            out = ggml_add(ctx0, out, ggml_mul(ctx0, shexp, sg));
        }
        return out;
    };

    for (int il = 0; il < n_layer; ++il) {
        res->t_layer_inp[il] = res_hc;
""", 1),
    # the FFN step of every layer
    ("""        cur = build_layer_ffn(cur, il);
        cb(cur, "ffn_out", il);

        res_hc = build_hc_combine(res_hc, cur, inject, il);

        // "l_last" is the layer output name that build_cvec and imatrix look for""",
     """        // lab patch: expert deferral. Layer L-1's deferred experts are built here, after this layer's
        // attention and FFN mixer are in the graph (the node order a non-serialising scheduler can overlap).
        ggml_tensor * lab_def_out    = nullptr;
        ggml_tensor * lab_def_inject = nullptr;
        if (lab_pend.x) {
            ggml_build_forward_expand(gf, cur);
            const auto & PL = model.layers[lab_pend.il];
            lab_def_out = build_moe_ffn(lab_pend.x, PL.ffn_gate_inp, PL.ffn_up_exps, PL.ffn_gate_exps,
                    PL.ffn_down_exps, nullptr, n_expert, lab_pend.k, LLM_FFN_SILU, true,
                    hparams.expert_weights_scale, LLAMA_EXPERT_GATING_FUNC_TYPE_SOFTMAX, lab_pend.il,
                    nullptr, PL.ffn_gate_up_exps, PL.ffn_up_exps_s, PL.ffn_gate_exps_s, PL.ffn_down_exps_s,
                    lab_pend.sel, lab_pend.w);
            cb(lab_def_out, "lab_defer_out", lab_pend.il);
            lab_def_inject = lab_pend.inject;
            lab_pend = lab_defer_pend{};
        }

        const bool lab_defer = lab_defer_k > 0 && lab_defer_k < n_expert_used && n_expert_used < n_expert &&
                il < n_layer - 2 && (lab_defer_layers <= 0 || il < lab_defer_layers) &&
                n_tokens <= lab_defer_maxtok && model.layers[il].ffn_gate_inp != nullptr;

        cur = lab_defer ? lab_build_ffn_defer(cur, il, inject) : build_layer_ffn(cur, il);
        cb(cur, "ffn_out", il);

        res_hc = build_hc_combine(res_hc, cur, inject, il);

        if (lab_def_out) {
            // one layer late, scattered with the deferring layer's own injection weights (unfused path: il = -1)
            res_hc = build_hc_combine(res_hc, lab_def_out, lab_def_inject, -1);
        }

        // "l_last" is the layer output name that build_cvec and imatrix look for""", 1),
    ("""    // export res_hc itself, never a reshape view: a pure view gets no backend assignment to read back.""",
     """    GGML_ASSERT(lab_pend.x == nullptr && "lab patch: expert deferral left a layer's experts unconsumed");

    // export res_hc itself, never a reshape view: a pure view gets no backend assignment to read back.""", 1),
])
