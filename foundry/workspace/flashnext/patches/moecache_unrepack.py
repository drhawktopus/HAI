"""Patch PR #27861 to support CPU weight repack.

    python moecache_unrepack.py <llama.cpp source dir>

Repacked experts (IQ4_NL as x8 interleave, Q2_K as x8 interleave) are byte-shuffled in-place by the CPU backend. Device kernels expect the native layout, so naive uploads yield garbage; this patch de-interleaves during upload. The layer gate now validates up, gate, and down experts to prevent uploading unsupported layouts. Edits use exact-match replacement and fail if they do not hit exactly once."""
import sys, os

SRC = sys.argv[1]
F = os.path.join(SRC, "src", "llama-moecache.cpp")
text = open(F, encoding="utf-8", newline="").read()
if "moecache_unrepack_iq4_nl_x8" in text:
    print("already patched")
    sys.exit(0)
nl = "\r\n" if "\r\n" in text else "\n"


def sub(old, new):
    global text
    old, new = old.replace("\n", nl), new.replace("\n", nl)
    n = text.count(old)
    assert n == 1, "expected exactly one match, found %d for:\n%s" % (n, old[:200])
    text = text.replace(old, new)


HELPERS = r'''// CPU_REPACK stores IQ4_NL experts as block_iq4_nlx8 (ggml-cpu repack.cpp, make_block_iq4_nlx8): per group of 8
// rows and per 32-element block, d[8] then qs[128] where 8-byte chunk j holds row (j % 8), bytes (j / 8) * 8.
// Device cache slots hold plain block_iq4_nl {fp16 d; uint8 qs[16]}, so a repacked slice is de-interleaved here.
bool moecache_is_repacked(const ggml_tensor * t) {
    return t->buffer && strcmp(ggml_backend_buft_name(ggml_backend_buffer_get_type(t->buffer)), "CPU_REPACK") == 0;
}

bool moecache_src_ok(const ggml_tensor * t) {
    if (!t->buffer) {
        return false;
    }
    if (ggml_backend_buffer_is_host(t->buffer)) {
        return !moecache_is_repacked(t);
    }
    if (!moecache_is_repacked(t) || t->ne[1] % 8 != 0) {
        return false;
    }
    return (t->type == GGML_TYPE_IQ4_NL && t->ne[0] % 32 == 0) || (t->type == GGML_TYPE_Q2_K && t->ne[0] % 256 == 0);
}

// CPU_REPACK Q2_K (q2_K_8x8_q8_K): block_q2_Kx8 = {fp16 d[8]; fp16 dmin[8]; scales[128]; qs[512]} (672 B) per 8 rows;
// qs chunk j (8 B) = row (j % 8), bytes (j / 8) * 8; scales[i] = row ((i % 16) / 2), scale ((i / 16) * 2 + i % 2).
// Plain block_q2_K = {scales[16]; qs[64]; fp16 d; fp16 dmin} (84 B).
void moecache_unrepack_q2_K_x8(const uint8_t * src, uint8_t * dst, int64_t nrows, int64_t nblocks) {
    for (int64_t g = 0; g < nrows / 8; ++g) {
        for (int64_t x = 0; x < nblocks; ++x) {
            const uint8_t * b = src + (g*nblocks + x)*672;
            for (int r = 0; r < 8; ++r) {
                uint8_t * o = dst + ((g*8 + r)*nblocks + x)*84;
                for (int s = 0; s < 16; ++s) {
                    o[s] = b[32 + (s/2)*16 + r*2 + (s%2)];
                }
                for (int c = 0; c < 8; ++c) {
                    memcpy(o + 16 + c*8, b + 160 + (c*8 + r)*8, 8);
                }
                memcpy(o + 80, b + 2*r,      2);
                memcpy(o + 82, b + 16 + 2*r, 2);
            }
        }
    }
}

void moecache_unrepack_iq4_nl_x8(const uint8_t * src, uint8_t * dst, int64_t nrows, int64_t nblocks) {
    for (int64_t g = 0; g < nrows / 8; ++g) {
        for (int64_t x = 0; x < nblocks; ++x) {
            const uint8_t * b = src + (g*nblocks + x)*144;
            for (int i = 0; i < 8; ++i) {
                uint8_t * o = dst + ((g*8 + i)*nblocks + x)*18;
                memcpy(o,      b + 2*i,              2);
                memcpy(o + 2,  b + 16 + i*8,         8);
                memcpy(o + 10, b + 16 + (8 + i)*8,   8);
            }
        }
    }
}

void upload_slice('''

sub("void upload_slice(", HELPERS)

sub('''    ggml_backend_tensor_set(dst_c, (const char *) src->data + (size_t) expert*sz, (size_t) slot*dst_c->nb[2], sz);
}''', '''    if (moecache_is_repacked(src)) {
        thread_local std::vector<uint8_t> plain;
        plain.resize(sz);
        if (src->type == GGML_TYPE_Q2_K) {
            moecache_unrepack_q2_K_x8((const uint8_t *) src->data + (size_t) expert*sz, plain.data(), src->ne[1], src->ne[0]/256);
        } else {
            moecache_unrepack_iq4_nl_x8((const uint8_t *) src->data + (size_t) expert*sz, plain.data(), src->ne[1], src->ne[0]/32);
        }
        ggml_backend_tensor_set(dst_c, plain.data(), (size_t) slot*dst_c->nb[2], sz);
        return;
    }
    ggml_backend_tensor_set(dst_c, (const char *) src->data + (size_t) expert*sz, (size_t) slot*dst_c->nb[2], sz);
}''')

sub('''            if (!l.ffn_up_exps->buffer || !ggml_backend_buffer_is_host(l.ffn_up_exps->buffer)) {
                continue; // experts already on a device: nothing to cache
            }''', '''            if (!l.ffn_up_exps->buffer || (!ggml_backend_buffer_is_host(l.ffn_up_exps->buffer) && !moecache_is_repacked(l.ffn_up_exps))) {
                continue; // experts already on a device: nothing to cache
            }
            if (!moecache_src_ok(l.ffn_up_exps) || !moecache_src_ok(l.ffn_gate_exps) || !moecache_src_ok(l.ffn_down_exps)) {
                LLAMA_LOG_WARN("moe-cache: layer %d: an expert tensor is in a layout the cache cannot upload - layer not cached\\n", (int) il);
                continue;
            }''')

open(F, "w", encoding="utf-8", newline="").write(text)
print("patched", F, "(3 edits)")
