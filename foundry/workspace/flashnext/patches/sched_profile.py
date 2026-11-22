"""Scheduler profile: times CPU work, waiting, and copies for both devices in a single run.

Usage: python sched_profile.py <llama.cpp source dir>

Instrumentation strategy: `ggml_backend_sched_compute_splits` executes segments sequentially. For each segment, it:
1. Waits for the previous device.
2. Copies inputs (which may wait on the other device).
3. Launches the segment.

CPU backends are synchronous (launch == compute). GPU launch is asynchronous (submit only), so GPU execution time appears as CPU waiting at the next hand-off. Measuring steps 1-3 per backend, plus wall time between graphs, decomposes the total time into CPU compute, waiting, copies, and scheduler overhead in one pass.

Enabled via env GGML_SCHED_PROFILE=N (prints summary to stderr every N graphs)."""
import sys, os

F = os.path.join(sys.argv[1], "ggml", "src", "ggml-backend.cpp")
text = open(F, encoding="utf-8", newline="").read()
if "lab patch: scheduler profile" in text:
    print("already patched")
    sys.exit(0)
nl = "\r\n" if "\r\n" in text else "\n"


def sub(old, new, count=1):
    global text
    o, n = old.replace("\n", nl), new.replace("\n", nl)
    c = text.count(o)
    assert c == count, "anchor matched %d times (want %d):\n%s" % (c, count, old[:160])
    text = text.replace(o, n)


HELPER = r'''// lab patch: scheduler profile (GGML_SCHED_PROFILE=N prints a per-graph average every N graphs)
#include <map>
#include <string>
#include <cstdlib>
struct lab_sp_acc { int64_t wait_us = 0, copy_us = 0, compute_us = 0, splits = 0; };
static int lab_sp_every = -1;
static int64_t lab_sp_graphs = 0, lab_sp_last = 0, lab_sp_wall = 0;
static std::map<std::string, lab_sp_acc> lab_sp;
static void lab_sp_note(ggml_backend_t be, int64_t t0, int64_t t1, int64_t t2, int64_t t3) {
    if (lab_sp_every <= 0) {
        return;
    }
    lab_sp_acc & a = lab_sp[ggml_backend_name(be)];
    a.wait_us += t1 - t0;
    a.copy_us += t2 - t1;
    a.compute_us += t3 - t2;
    a.splits++;
}
static void lab_sp_graph_begin(void) {
    if (lab_sp_every < 0) {
        const char * e = getenv("GGML_SCHED_PROFILE");
        lab_sp_every = e ? atoi(e) : 0;
    }
    if (lab_sp_every <= 0) {
        return;
    }
    const int64_t now = ggml_time_us();
    if (lab_sp_last) {
        lab_sp_wall += now - lab_sp_last;
    }
    lab_sp_last = now;
    if (++lab_sp_graphs % lab_sp_every == 0) {
        const double g = (double) lab_sp_every;
        double acc = 0;
        fprintf(stderr, "SCHED PROFILE over %d graphs, us per graph: wall %.1f\n", lab_sp_every, lab_sp_wall / g);
        for (auto & kv : lab_sp) {
            const lab_sp_acc & a = kv.second;
            fprintf(stderr, "  %-14s splits %6.1f | wait-before %9.1f | inputs+copy %9.1f | compute-call %9.1f\n",
                    kv.first.c_str(), a.splits / g, a.wait_us / g, a.copy_us / g, a.compute_us / g);
            acc += (a.wait_us + a.copy_us + a.compute_us) / g;
        }
        fprintf(stderr, "  outside-scheduler %.1f (sampling, MTP logic, the caller's final sync)\n", lab_sp_wall / g - acc);
        lab_sp.clear();
        lab_sp_wall = 0;
    }
}

static enum ggml_status ggml_backend_sched_compute_splits(ggml_backend_sched_t sched) {'''

sub("static enum ggml_status ggml_backend_sched_compute_splits(ggml_backend_sched_t sched) {", HELPER)

sub("""    int prev_backend_id = -1;

    for (int split_id = 0; split_id < sched->n_splits; split_id++) {""",
    """    int prev_backend_id = -1;

    lab_sp_graph_begin();

    for (int split_id = 0; split_id < sched->n_splits; split_id++) {""")

sub("""        // ensure the previous split's async work has completed before we start
        // this split, the allocator may have reused buffer regions across splits
        if (split->n_inputs == 0 && prev_backend_id >= 0 && prev_backend_id != split_backend_id) {""",
    """        const int64_t lab_t0 = ggml_time_us();
        // ensure the previous split's async work has completed before we start
        // this split, the allocator may have reused buffer regions across splits
        if (split->n_inputs == 0 && prev_backend_id >= 0 && prev_backend_id != split_backend_id) {""")

sub("""        // copy the input tensors to the split backend
        for (int input_id = 0; input_id < split->n_inputs; input_id++) {""",
    """        const int64_t lab_t1 = ggml_time_us();
        // copy the input tensors to the split backend
        for (int input_id = 0; input_id < split->n_inputs; input_id++) {""")

sub("""        if (!sched->callback_eval) {
            enum ggml_status ec = ggml_backend_graph_compute_async(split_backend, &split->graph);
            if (ec != GGML_STATUS_SUCCESS) {
                return ec;
            }""",
    """        const int64_t lab_t2 = ggml_time_us();
        if (!sched->callback_eval) {
            enum ggml_status ec = ggml_backend_graph_compute_async(split_backend, &split->graph);
            lab_sp_note(split_backend, lab_t0, lab_t1, lab_t2, ggml_time_us());
            if (ec != GGML_STATUS_SUCCESS) {
                return ec;
            }""")

open(F, "w", encoding="utf-8", newline="").write(text)
print("patched", F, "(scheduler profile, 5 edits)")
