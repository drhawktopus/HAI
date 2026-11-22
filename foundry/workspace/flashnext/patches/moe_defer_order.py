"""Ordering: queue the current layer's GPU work before the CPU executes the previous layer's deferred experts.

    python moe_defer_order.py <llama.cpp source dir>   (after moe_expert_defer, moe_defer_overlap, moe_defer_onecopy)

Per deferring layer L+1, the graph order is:
  1. router + shared expert matmuls/gate (GPU) FIRST, so one GPU split carries attention + mixer + router + shared expert;
  2. layer L's deferred experts (CPU, the lab_defer_cpu_head split);
  3. shared expert gate multiply (GPU, tiny): must remain a separate GPU split to prevent the next CPU split's input copies from synchronizing the GPU before the deferred work runs;
  4. one-copy conts + immediate experts (CPU), then out = immediate + gated shared expert.

CPU tensors the GPU reads (immediate out, deferred out) are marked as graph outputs so ggml-alloc does not reuse their memory while a GPU input copy is in flight.
LAB_OVERLAP_EVENT=0 skips the scheduler event record (default 1 is safe).

Exact-match edits must hit once or the patch fails."""
import sys, os, re

SRC = sys.argv[1]
MARK = "lab patch: defer order"


def load(rel):
    p = os.path.join(SRC, *rel.split("/"))
    t = open(p, encoding="utf-8", newline="").read()
    return p, t, ("\r\n" if "\r\n" in t else "\n")


# --- qwen4exp.cpp -------------------------------------------------------------------------------------------------
path, text, nl = load("src/models/qwen4exp.cpp")
if MARK in text:
    print("already patched", path)
else:
    assert "lab patch: defer one copy" in text, "apply moe_expert_defer, moe_defer_overlap, moe_defer_onecopy first"
    t = text.replace(nl, "\n")

    # (a) the deferring FFN, rewritten whole: bounded by its header and the first 'return out; };' after it
    head = "    auto lab_build_ffn_defer = [&](ggml_tensor * x, int il, ggml_tensor * inj) -> ggml_tensor * {\n"
    i = t.index(head)
    j = t.index("        return out;\n    };\n", i) + len("        return out;\n    };\n")
    assert t.count(head) == 1
    new_fn = '''    // lab patch: defer order - build the pending deferred experts (layer L) as their own CPU split
    auto lab_flush_pend = [&](ggml_tensor ** def_out, ggml_tensor ** def_inj) {
        const auto & PL = model.layers[lab_pend.il];
        ggml_tensor * lab_head = ggml_scale(ctx0, lab_pend.x, 1.0f);
        cb(lab_head, "lab_defer_cpu_head", lab_pend.il);
        ggml_build_forward_expand(gf, lab_head);
        ggml_tensor * d = build_moe_ffn(lab_head, PL.ffn_gate_inp, PL.ffn_up_exps, PL.ffn_gate_exps, PL.ffn_down_exps,
                nullptr, n_expert, lab_pend.k, LLM_FFN_SILU, true, hparams.expert_weights_scale,
                LLAMA_EXPERT_GATING_FUNC_TYPE_SOFTMAX, lab_pend.il,
                nullptr, PL.ffn_gate_up_exps, PL.ffn_up_exps_s, PL.ffn_gate_exps_s, PL.ffn_down_exps_s,
                lab_pend.sel, lab_pend.w);
        cb(d, "lab_defer_out", lab_pend.il);
        ggml_set_output(d);                      // read by a GPU copy: ggml-alloc must not reuse it mid-graph
        ggml_build_forward_expand(gf, d);
        *def_out = d;
        *def_inj = lab_pend.inject;
        lab_pend = lab_defer_pend{};
    };

    auto lab_build_ffn_defer = [&](ggml_tensor * x, int il, ggml_tensor * inj,
                                   ggml_tensor ** def_out, ggml_tensor ** def_inj) -> ggml_tensor * {
        const auto & L   = model.layers[il];
        const int64_t nt = x->ne[1];
        const int64_t k_imm = n_expert_used - lab_defer_k;

        // 1. GPU work first: router (as build_moe_ffn: softmax, top-k, weights normalised over the k) ...
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

        // ... and the shared expert's matmuls + gate logit (exactly as build_layer_ffn, gate multiply held back)
        ggml_tensor * shexp = nullptr;
        ggml_tensor * sg    = nullptr;
        if (L.ffn_up_shexp != nullptr) {
            shexp = build_ffn(x,
                    L.ffn_up_shexp, NULL, L.ffn_up_shexp_s,
                    L.ffn_gate_shexp, NULL, L.ffn_gate_shexp_s,
                    L.ffn_down_shexp, NULL, L.ffn_down_shexp_s,
                    NULL, LLM_FFN_SILU, LLM_FFN_PAR, il);
            cb(shexp, "ffn_shexp", il);
            sg = ggml_sigmoid(ctx0, build_lora_mm(L.ffn_gate_inp_shexp, x));
            cb(sg, "shared_expert_gate_sigmoid", il);
            ggml_build_forward_expand(gf, shexp);
            ggml_build_forward_expand(gf, sg);
        }

        // 2. the previous layer's deferred experts: CPU, runs while the GPU executes step 1
        if (lab_pend.x) {
            lab_flush_pend(def_out, def_inj);
        }

        // 3. a tiny GPU split between the deferred work and the next CPU split
        ggml_tensor * shexp_g = nullptr;
        if (shexp) {
            shexp_g = ggml_mul(ctx0, shexp, sg);
            cb(shexp_g, "ffn_shexp_gated", il);
            ggml_build_forward_expand(gf, shexp_g);
        }

        // 4. one copy of x / ids / weights to the CPU, views for both groups, then the immediate experts
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

        ggml_tensor * out = build_moe_ffn(xc, L.ffn_gate_inp, L.ffn_up_exps, L.ffn_gate_exps, L.ffn_down_exps,
                nullptr, n_expert, k_imm, LLM_FFN_SILU, true, hparams.expert_weights_scale,
                LLAMA_EXPERT_GATING_FUNC_TYPE_SOFTMAX, il,
                nullptr, L.ffn_gate_up_exps, L.ffn_up_exps_s, L.ffn_gate_exps_s, L.ffn_down_exps_s,
                s_imm, w_imm);
        cb(out, "ffn_moe_out", il);
        ggml_set_output(out);                    // read by a GPU copy: ggml-alloc must not reuse it mid-graph
        if (shexp_g) {
            out = ggml_add(ctx0, out, shexp_g);
            ggml_set_output(out);
        }
        return out;
    };
'''
    t = t[:i] + new_fn + t[j:]

    # (b) the layer loop: deferring layers flush inside lab_build_ffn_defer; others flush here, as before
    a = t.index("        // lab patch: expert deferral. Layer L-1's deferred experts are built here")
    b_mark = "        cur = lab_defer ? lab_build_ffn_defer(cur, il, inject) : build_layer_ffn(cur, il);\n"
    b = t.index(b_mark) + len(b_mark)
    assert t.count(b_mark) == 1
    new_loop = '''        // lab patch: defer order - a deferring layer builds the previous layer's deferred experts itself, after
        // queueing its own GPU work; a non-deferring layer builds them here, before its FFN
        const bool lab_defer = lab_defer_k > 0 && lab_defer_k < n_expert_used && n_expert_used < n_expert &&
                il < n_layer - 2 && (lab_defer_layers <= 0 || il < lab_defer_layers) &&
                n_tokens <= lab_defer_maxtok && model.layers[il].ffn_gate_inp != nullptr;
        ggml_tensor * lab_def_out    = nullptr;
        ggml_tensor * lab_def_inject = nullptr;
        if (lab_defer) {
            cur = lab_build_ffn_defer(cur, il, inject, &lab_def_out, &lab_def_inject);
        } else {
            if (lab_pend.x) {
                ggml_build_forward_expand(gf, cur);
                lab_flush_pend(&lab_def_out, &lab_def_inject);
            }
            cur = build_layer_ffn(cur, il);
        }
'''
    t = t[:a] + new_loop + t[b:]
    assert MARK in t
    open(path, "w", encoding="utf-8", newline="").write(t.replace("\n", nl))
    print("patched", path, "(2 regions)")

# --- ggml-backend.cpp: the event becomes optional (LAB_OVERLAP_EVENT=0) ------------------------------------------
path, text, nl = load("ggml/src/ggml-backend.cpp")
if MARK in text:
    print("already patched", path)
else:
    assert "lab patch: defer overlap" in text, "apply moe_defer_overlap first"
    pairs = [
        ("static bool                 lab_ov_armed   = false;\n",
         "static bool                 lab_ov_armed   = false;\n"
         "static bool                 lab_ov_recorded = false;   // lab patch: defer order - event optional\n"),
        ("                ggml_backend_event_synchronize(lab_ov_event);\n",
         "                if (lab_ov_recorded) {\n"
         "                    ggml_backend_event_synchronize(lab_ov_event);\n"
         "                }\n"),
        ("            if (lab_ov_event != NULL) {\n"
         "                ggml_backend_event_record(lab_ov_event, split_backend);\n"
         "                lab_ov_armed = true;\n"
         "            }\n",
         "            // LAB_OVERLAP_EVENT=0: the GPU-read CPU tensors are graph outputs (never reused), so skip the event\n"
         "            static const bool lab_ov_use_event = [] { const char * v = getenv(\"LAB_OVERLAP_EVENT\"); return v == NULL || atoi(v) != 0; }();\n"
         "            if (!lab_ov_use_event) {\n"
         "                lab_ov_armed    = true;\n"
         "                lab_ov_recorded = false;\n"
         "            } else if (lab_ov_event != NULL) {\n"
         "                ggml_backend_event_record(lab_ov_event, split_backend);\n"
         "                lab_ov_armed    = true;\n"
         "                lab_ov_recorded = true;\n"
         "            }\n"),
    ]
    t = text.replace(nl, "\n")
    for old, new in pairs:
        assert t.count(old) == 1, "ggml-backend.cpp: expected 1 match for:\n" + old[:160]
        t = t.replace(old, new)
    open(path, "w", encoding="utf-8", newline="").write(t.replace("\n", nl))
    print("patched", path, "(3 edits)")
