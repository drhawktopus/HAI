"""Expert Deferral, prefix-first: compute the deferred experts at the start of the next CPU split, before its input copies. This avoids extra CPU/GPU hand-offs by overlapping computation with data transfer.

Usage: python moe_defer_prefix.py <llama.cpp source dir> (requires prior application of moe_expert_defer, moe_defer_overlap, moe_defer_onecopy, and moe_defer_order).

Mechanism: When LAB_SCHED_OVERLAP=1, a CPU split starting with lab_defer_cpu_head executes nodes up to lab_defer_out before performing input copies. This is safe because the deferred prefix reads only CPU tensors from earlier splits, input copies are allocated at split start (preventing aliasing), and in-flight GPU splits read graph outputs.

Optional: LAB_DEFER_CONCAT=1 enables batching the layer's immediate result and previous deferred result into a single GPU copy via CPU-side concatenation. This reduces CPU-to-GPU transfer latency. The graph callback pins lab_defer_gpu* to the first non-CPU backend.

This patch performs exact-match edits that must succeed exactly once."""
import sys, os

SRC = sys.argv[1]
MARK = "lab patch: defer prefix"


def edit(rel, pairs, needs):
    path = os.path.join(SRC, *rel.split("/"))
    text = open(path, encoding="utf-8", newline="").read()
    if MARK in text:
        print("already patched", path)
        return
    assert needs in text, "%s: apply the earlier deferral patches first (%r missing)" % (rel, needs)
    nl = "\r\n" if "\r\n" in text else "\n"
    t = text.replace(nl, "\n")
    for old, new in pairs:
        assert t.count(old) == 1, "%s: expected 1 match, found %d for:\n%s" % (rel, t.count(old), old[:200])
        t = t.replace(old, new)
    assert MARK in t
    open(path, "w", encoding="utf-8", newline="").write(t.replace("\n", nl))
    print("patched", path, "(%d edits)" % len(pairs))


# --- ggml-backend.cpp: prefix-first ------------------------------------------------------------------------------
edit("ggml/src/ggml-backend.cpp", [
    ("""        // copy the input tensors to the split backend
        for (int input_id = 0; input_id < split->n_inputs; input_id++) {""",
     """        // lab patch: defer prefix - a CPU split that STARTS with the deferred experts (lab_defer_cpu_head) runs them
        // up to lab_defer_out before its input copies, which wait for the GPU: the deferred work overlaps the GPU
        int lab_pf_end = -1;
        {
            static const bool lab_pf_on = [] { const char * v = getenv("LAB_SCHED_OVERLAP"); return v != NULL && atoi(v) != 0; }();
            if (lab_pf_on && split->n_inputs > 0 && split->graph.n_nodes > 0 &&
                ggml_backend_dev_type(ggml_backend_get_device(split_backend)) == GGML_BACKEND_DEVICE_TYPE_CPU &&
                strncmp(split->graph.nodes[0]->name, "lab_defer_cpu_head", 18) == 0) {
                for (int i = 0; i < split->graph.n_nodes; i++) {
                    if (strncmp(split->graph.nodes[i]->name, "lab_defer_out", 13) == 0) {
                        lab_pf_end = i;
                        break;
                    }
                }
                if (lab_pf_end >= 0) {
                    struct ggml_cgraph lab_pf = ggml_graph_view(&split->graph, 0, lab_pf_end + 1);
                    enum ggml_status lab_ec = ggml_backend_graph_compute_async(split_backend, &lab_pf);
                    if (lab_ec != GGML_STATUS_SUCCESS) {
                        return lab_ec;
                    }
                    static int64_t lab_pf_count = 0;
                    if (lab_pf_count++ == 0) {
                        GGML_LOG_INFO("lab prefix: deferred experts computed before the split's GPU wait (%d nodes)\\n", lab_pf_end + 1);
                    }
                }
            }
        }

        // copy the input tensors to the split backend
        for (int input_id = 0; input_id < split->n_inputs; input_id++) {"""),
    ("""        if (!sched->callback_eval) {
            enum ggml_status ec = ggml_backend_graph_compute_async(split_backend, &split->graph);""",
     """        if (!sched->callback_eval) {
            struct ggml_cgraph lab_rest = lab_pf_end >= 0
                ? ggml_graph_view(&split->graph, lab_pf_end + 1, split->graph.n_nodes)
                : split->graph;
            enum ggml_status ec = ggml_backend_graph_compute_async(split_backend, &lab_rest);"""),
], "lab patch: defer order")

# --- llama-context.cpp: pin lab_defer_gpu* to the GPU ------------------------------------------------------------
edit("src/llama-context.cpp", [
    ("""        if (strncmp(name, "lab_defer_cpu", 13) == 0 && backend_cpu != nullptr) {
            ggml_backend_sched_set_tensor_backend(sched.get(), cur, backend_cpu);
        }
""",
     """        if (strncmp(name, "lab_defer_cpu", 13) == 0 && backend_cpu != nullptr) {
            ggml_backend_sched_set_tensor_backend(sched.get(), cur, backend_cpu);
        }
        // lab patch: defer prefix - the concatenated results land on the first non-CPU backend
        if (strncmp(name, "lab_defer_gpu", 13) == 0) {
            for (const auto & backend : backends) {
                if (ggml_backend_dev_type(ggml_backend_get_device(backend.get())) != GGML_BACKEND_DEVICE_TYPE_CPU) {
                    ggml_backend_sched_set_tensor_backend(sched.get(), cur, backend.get());
                    break;
                }
            }
        }
"""),
], "lab patch: defer overlap")

# --- qwen4exp.cpp: optional single copy of both results ----------------------------------------------------------
edit("src/models/qwen4exp.cpp", [
    ("""        ggml_set_output(out);                    // read by a GPU copy: ggml-alloc must not reuse it mid-graph
        if (shexp_g) {
            out = ggml_add(ctx0, out, shexp_g);
            ggml_set_output(out);
        }
        return out;
    };
""",
     """        ggml_set_output(out);                    // read by a GPU copy: ggml-alloc must not reuse it mid-graph
        if (shexp_g) {
            out = ggml_add(ctx0, out, shexp_g);
            ggml_set_output(out);
        }
        // lab patch: defer prefix - LAB_DEFER_CONCAT=1: this layer's result and the previous layer's deferred result
        // cross to the GPU as one copy (concat on the CPU, GPU-pinned cont, two views)
        static const bool lab_concat = lab_defer_env("LAB_DEFER_CONCAT", 0) != 0;
        if (lab_concat && *def_out != nullptr) {
            ggml_tensor * both = ggml_concat(ctx0, out, *def_out, 1);
            cb(both, "lab_defer_cpu_both", il);
            ggml_set_output(both);
            ggml_tensor * g = ggml_cont(ctx0, both);
            cb(g, "lab_defer_gpu_both", il);
            out      = ggml_view_2d(ctx0, g, g->ne[0], nt, g->nb[1], 0);
            *def_out = ggml_view_2d(ctx0, g, g->ne[0], nt, g->nb[1], nt*g->nb[1]);
        }
        return out;
    };
"""),
], "lab patch: defer order")
