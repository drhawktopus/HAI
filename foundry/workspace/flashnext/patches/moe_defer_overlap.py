r"""Expert Deferral, Stage B: let the deferred experts' CPU work run WHILE the GPU computes the next layer.

    python moe_defer_overlap.py <llama.cpp source dir>      (after moe_expert_defer.py)

Env: LAB_SCHED_OVERLAP=1 turns the overlap on (needs LAB_MOE_DEFER > 0). Unset = Stage A behaviour exactly.

Why Stage A cannot overlap (ggml-backend.cpp, ggml_backend_sched_compute_splits): before a split with no inputs on a
different backend than the previous split, the scheduler synchronises the previous backend in full - "the allocator
may have reused buffer regions across splits"; and a split WITH inputs copied from the GPU synchronises the GPU in
the copy (the CPU backend has no async copy). So every CPU split waits for all queued GPU work.

What this patch does:
1. qwen4exp.cpp - the deferred experts' inputs (layer L's FFN input, their ids, their weights) are made CPU tensors
   inside layer L's own CPU split (names lab_defer_cpu_x/_sel/_w, pinned to the CPU by name), and the deferred
   work at layer L+1 starts with a marker node lab_defer_cpu_head. A tensor already copied to a backend is reused
   by later splits without another copy (split_graph pass 5), so the deferred split has no inputs to copy.
2. llama-context.cpp - the graph callback pins every node named lab_defer_cpu* to the CPU backend (the same
   mechanism it already uses to pin "norm" / "l_last").
3. ggml-backend.cpp - the GPU split right before a deferred split records an event after its input copies are
   queued; the deferred split waits on that event only (the copies read pinned host memory that the allocator may
   hand to this split), not for the GPU's compute. Vulkan's event_record submits the queued copies with a timeline
   semaphore; event_synchronize waits for that value. Logs once when the overlap first happens, and once if a
   deferred split still has inputs (then it waits for the GPU as before: correct, no overlap).
Every edit is an exact match that must hit the stated count, or the patch fails."""
import sys, os

SRC = sys.argv[1]
MARK = "lab patch: defer overlap"


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


q = open(os.path.join(SRC, "src", "models", "qwen4exp.cpp"), encoding="utf-8", newline="").read()
assert "lab patch: expert deferral" in q, "apply moe_expert_defer.py first"

# --- qwen4exp.cpp -------------------------------------------------------------------------------------------------
edit("src/models/qwen4exp.cpp", [
    ("""        lab_pend.x = x; lab_pend.il = il; lab_pend.k = lab_defer_k; lab_pend.inject = inj;
        take(k_imm, lab_defer_k, &lab_pend.sel, &lab_pend.w);
""",
     """        lab_pend.x = x; lab_pend.il = il; lab_pend.k = lab_defer_k; lab_pend.inject = inj;
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
""", 1),
    ("""            lab_def_out = build_moe_ffn(lab_pend.x, PL.ffn_gate_inp,""",
     """            // first node of the deferred split: the scheduler recognises the split by this name
            ggml_tensor * lab_head = ggml_scale(ctx0, lab_pend.x, 1.0f);
            cb(lab_head, "lab_defer_cpu_head", lab_pend.il);
            ggml_build_forward_expand(gf, lab_head);
            lab_def_out = build_moe_ffn(lab_head, PL.ffn_gate_inp,""", 1),
])

# --- llama-context.cpp: pin lab_defer_cpu* to the CPU ------------------------------------------------------------
edit("src/llama-context.cpp", [
    ("""        if (il >= 0) {
            ggml_format_name(cur, "%s-%d", name, il);
        } else {
            ggml_set_name(cur, name);
        }
""",
     """        if (il >= 0) {
            ggml_format_name(cur, "%s-%d", name, il);
        } else {
            ggml_set_name(cur, name);
        }

        // lab patch: defer overlap - expert-deferral inputs and the deferred head stay on the CPU
        if (strncmp(name, "lab_defer_cpu", 13) == 0 && backend_cpu != nullptr) {
            ggml_backend_sched_set_tensor_backend(sched.get(), cur, backend_cpu);
        }
""", 1),
])

# --- ggml-backend.cpp: event-scoped wait instead of a full GPU sync ----------------------------------------------
edit("ggml/src/ggml-backend.cpp", [
    ("""static enum ggml_status ggml_backend_sched_compute_splits(ggml_backend_sched_t sched) {""",
     """// lab patch: defer overlap (patches\\moe_defer_overlap.py, env LAB_SCHED_OVERLAP=1)
static ggml_backend_event_t lab_ov_event   = NULL;
static bool                 lab_ov_failed  = false;
static bool                 lab_ov_armed   = false;
static int64_t              lab_ov_count   = 0;
static int64_t              lab_ov_blocked = 0;

// a CPU split holding the deferred experts' work (marker node lab_defer_cpu_head) while the overlap is on
static bool lab_ov_is_deferred_split(ggml_backend_sched_t sched, const struct ggml_backend_sched_split * split) {
    static const bool on = [] { const char * v = getenv("LAB_SCHED_OVERLAP"); return v != NULL && atoi(v) != 0; }();
    if (!on || ggml_backend_dev_type(ggml_backend_get_device(sched->backends[split->backend_id])) != GGML_BACKEND_DEVICE_TYPE_CPU) {
        return false;
    }
    for (int i = 0; i < split->graph.n_nodes; i++) {
        if (strncmp(split->graph.nodes[i]->name, "lab_defer_cpu_head", 18) == 0) {
            return true;
        }
    }
    return false;
}

static enum ggml_status ggml_backend_sched_compute_splits(ggml_backend_sched_t sched) {""", 1),
    ("""        if (split->n_inputs == 0 && prev_backend_id >= 0 && prev_backend_id != split_backend_id) {
            if (sched->events[prev_backend_id][sched->cur_copy] != NULL) {
                ggml_backend_event_synchronize(sched->events[prev_backend_id][sched->cur_copy]);
            } else {
                ggml_backend_synchronize(sched->backends[prev_backend_id]);
            }
        }
""",
     """        const bool lab_ov_deferred = lab_ov_is_deferred_split(sched, split);
        if (lab_ov_deferred && split->n_inputs > 0 && lab_ov_blocked++ == 0) {
            GGML_LOG_WARN("lab overlap: a deferred-expert split has %d input(s) - it waits for the GPU (no overlap)\\n", split->n_inputs);
        }
        if (split->n_inputs == 0 && prev_backend_id >= 0 && prev_backend_id != split_backend_id) {
            if (lab_ov_deferred && lab_ov_armed) {
                // wait only for the previous GPU split's input copies; its compute keeps running alongside this split
                ggml_backend_event_synchronize(lab_ov_event);
                if (lab_ov_count++ == 0) {
                    GGML_LOG_INFO("lab overlap: deferred-expert CPU split running alongside the GPU\\n");
                }
            } else if (sched->events[prev_backend_id][sched->cur_copy] != NULL) {
                ggml_backend_event_synchronize(sched->events[prev_backend_id][sched->cur_copy]);
            } else {
                ggml_backend_synchronize(sched->backends[prev_backend_id]);
            }
        }
        lab_ov_armed = false;
""", 1),
    ("""        if (!sched->callback_eval) {
            enum ggml_status ec = ggml_backend_graph_compute_async(split_backend, &split->graph);""",
     """        // arm the event after this GPU split's input copies when a deferred-expert CPU split comes next
        if (split_id + 1 < sched->n_splits &&
            ggml_backend_dev_type(ggml_backend_get_device(split_backend)) != GGML_BACKEND_DEVICE_TYPE_CPU &&
            lab_ov_is_deferred_split(sched, &splits[split_id + 1])) {
            if (lab_ov_event == NULL && !lab_ov_failed) {
                lab_ov_event  = ggml_backend_event_new(ggml_backend_get_device(split_backend));
                lab_ov_failed = lab_ov_event == NULL;
                if (lab_ov_failed) {
                    GGML_LOG_WARN("lab overlap: backend %s has no events - no overlap\\n", ggml_backend_name(split_backend));
                }
            }
            if (lab_ov_event != NULL) {
                ggml_backend_event_record(lab_ov_event, split_backend);
                lab_ov_armed = true;
            }
        }

        if (!sched->callback_eval) {
            enum ggml_status ec = ggml_backend_graph_compute_async(split_backend, &split->graph);""", 1),
])
