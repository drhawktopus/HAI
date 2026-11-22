"""Fault-ahead for routed experts: batched PrefetchVirtualMemory per expert matmul (Windows).

    python moe_fault_ahead.py <llama.cpp source dir>

Groups rows in mul_mat_id to issue a single PrefetchVirtualMemory call for all used experts, avoiding serial page faults.
Applies to both standard (ggml-cpu.c) and repack (repack.cpp) paths.
Disabled by default; enable via GGML_MOE_PREFETCH=1.
Uses exact-match edits and fails if source drifts."""
import sys, os

SRC = sys.argv[1]


def patch(rel, marker, edits):
    f = os.path.join(SRC, rel)
    text = open(f, encoding="utf-8", newline="").read()
    if marker in text:
        print("already patched", rel)
        return
    nl = "\r\n" if "\r\n" in text else "\n"
    for old, new, min_hits in edits:
        o, n = old.replace("\n", nl), new.replace("\n", nl)
        c = text.count(o)
        assert c >= min_hits and c >= 1, "%s: anchor matched %d times:\n%s" % (rel, c, old[:160])
        text = text.replace(o, n)
    open(f, "w", encoding="utf-8", newline="").write(text)
    print("patched", rel)


HELPER = r'''// lab patch: moe fault-ahead. One batched prefetch of every routed expert's slice of src0 (GGML_MOE_PREFETCH=1).
void ggml_moe_prefetch_experts(const char * base, size_t stride, const int64_t * counts, int n_as) {
#if defined(_WIN32)
    // local copy of WIN32_MEMORY_RANGE_ENTRY's layout: the SDK only declares it when _WIN32_WINNT >= 0x0602
    typedef struct { PVOID VirtualAddress; SIZE_T NumberOfBytes; } lab_range_t;
    static int enabled = -1;
    typedef BOOL (WINAPI *pfn_t)(HANDLE, ULONG_PTR, lab_range_t *, ULONG);
    static pfn_t pfn = NULL;
    if (enabled == -1) {
        const char * e = getenv("GGML_MOE_PREFETCH");
        enabled = (e && e[0] == '1') ? 1 : 0;
        if (enabled) {
            HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
            pfn = k32 ? (pfn_t) (void *) GetProcAddress(k32, "PrefetchVirtualMemory") : NULL;
            if (!pfn) {
                enabled = 0;
            }
            fprintf(stderr, "ggml: moe fault-ahead %s\n", enabled ? "ON (PrefetchVirtualMemory)" : "requested but unavailable");
        }
    }
    if (enabled != 1 || !base) {
        return;
    }
    lab_range_t entries[64];
    ULONG n = 0;
    for (int a = 0; a < n_as; ++a) {
        if (counts[a] > 0) {
            entries[n].VirtualAddress = (PVOID) (base + (size_t) a*stride);
            entries[n].NumberOfBytes  = stride;
            if (++n == 64) {
                pfn(GetCurrentProcess(), n, entries, 0);
                n = 0;
            }
        }
    }
    if (n) {
        pfn(GetCurrentProcess(), n, entries, 0);
    }
#else
    (void) base; (void) stride; (void) counts; (void) n_as;
#endif
}

static void ggml_compute_forward_mul_mat_id('''

patch(os.path.join("ggml", "src", "ggml-cpu", "ggml-cpu.c"), "lab patch: moe fault-ahead", [
    ("static void ggml_compute_forward_mul_mat_id(", HELPER, 1),
    ("""        // MoE routing observation for the llama expert cache
        {
            void * moe_obs_ud = NULL;
            ggml_moe_obs_cb_t moe_obs_cb = ggml_get_moe_obs_callback(&moe_obs_ud);
            if (moe_obs_cb && strstr(src0->name, "ffn_gate_exps")) {
                moe_obs_cb(src0->name, ids, moe_obs_ud);
            }
        }""", """        // MoE routing observation for the llama expert cache
        {
            void * moe_obs_ud = NULL;
            ggml_moe_obs_cb_t moe_obs_cb = ggml_get_moe_obs_callback(&moe_obs_ud);
            if (moe_obs_cb && strstr(src0->name, "ffn_gate_exps")) {
                moe_obs_cb(src0->name, ids, moe_obs_ud);
            }
        }

        ggml_moe_prefetch_experts((const char *) src0->data, nb02, matrix_row_counts, n_as);""", 1),
])

patch(os.path.join("ggml", "src", "ggml-cpu", "repack.cpp"), "ggml_moe_prefetch_experts", [
    ("#include \"repack.h\"", "#include \"repack.h\"\n\nextern \"C\" void ggml_moe_prefetch_experts(const char * base, size_t stride, const int64_t * counts, int n_as);", 1),
    ("""                if (moe_obs_cb && strstr(src0->name, "ffn_gate_exps")) {
                    moe_obs_cb(src0->name, ids, moe_obs_ud);
                }
            }
        }""", """                if (moe_obs_cb && strstr(src0->name, "ffn_gate_exps")) {
                    moe_obs_cb(src0->name, ids, moe_obs_ud);
                }
            }

            ggml_moe_prefetch_experts((const char *) src0->data, nb02, matrix_row_counts, n_as);
        }""", 1),
])
