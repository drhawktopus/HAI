"""Make the DIAGNOSTIC source tree llama.cpp-620d222-r4-labsw from the lab engine's r4 tree.

A normal-speed build whose Vulkan backend reads four lab-only switches, one at each place the coopmat path differs
from the path that repeats itself (GGML_VK_DISABLE_COOPMAT). They only exist to find WHERE the history comes in; the
fix is a source change, never one of these. This tree is never a serving build and never gets a lab revision number.

    LAB_VK_NO_CM1_INT   the RDNA3/RDNA4 integer coopmat multiply (mul_mmq_cm1.comp) is not used
    LAB_VK_NO_QUANT_Y   the activations are not quantised to Q8_1 before a multiply
    LAB_VK_NO_F16_B     the activations are not staged as f16 for a quantised multiply
    LAB_VK_NO_Y_CACHE   the staged activations (prealloc_y) are restaged for every multiply, never reused

    python make_labsw_tree.py"""
import io
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "llama.cpp-620d222-r4")
DST = os.path.join(HERE, "llama.cpp-620d222-r4-labsw")


def skip(d, names):
    return [n for n in names if n == ".git" or (n.startswith("build") and os.path.isdir(os.path.join(d, n)))]


if os.path.isdir(DST):
    shutil.rmtree(DST)
shutil.copytree(SRC, DST, ignore=skip)
p = os.path.join(DST, "ggml", "src", "ggml-vulkan", "ggml-vulkan.cpp")
s = io.open(p, encoding="utf-8", newline="").read()
counts = {}


def sub(name, old, new, want=None):
    global s
    n = s.count(old)
    assert n >= 1 and (want is None or n == want), "%s: found %d times (wanted %s)" % (name, n, want)
    s = s.replace(old, new)
    counts[name] = n


sub("LAB_VK_NO_CM1_INT", "device->coopmat_int_support = true;",
    "device->coopmat_int_support = getenv(\"LAB_VK_NO_CM1_INT\") == nullptr;", 1)
sub("LAB_VK_NO_QUANT_Y", "const bool can_quantize_y = (ctx->device->integer_dot_product || ctx->device->coopmat_int_support) &&",
    "const bool can_quantize_y = getenv(\"LAB_VK_NO_QUANT_Y\") == nullptr && (ctx->device->integer_dot_product || ctx->device->coopmat_int_support) &&")
sub("LAB_VK_NO_F16_B", "const bool prefer_f16_b = is_coopmat && ggml_is_quantized(src0->type) && src1->type == GGML_TYPE_F32;",
    "const bool prefer_f16_b = getenv(\"LAB_VK_NO_F16_B\") == nullptr && is_coopmat && ggml_is_quantized(src0->type) && src1->type == GGML_TYPE_F32;", 1)
sub("LAB_VK_NO_Y_CACHE", "ctx->prealloc_y_last_tensor_used != src1 ||",
    "ctx->prealloc_y_last_tensor_used != src1 || getenv(\"LAB_VK_NO_Y_CACHE\") != nullptr ||")
# LAB_VK_CPU_OPS="MUL_MAT;FLASH_ATTN_EXT;MUL_MAT:f32": those ops (by name, or name:src0 type) are refused by the Vulkan
# backend, so the scheduler runs them on the CPU. The arm that REPEATS names the op that reads the history.
nl = "\r\n" if "\r\n" in s else "\n"
head = ("static bool ggml_backend_vk_device_supports_op(ggml_backend_dev_t dev, const ggml_tensor * op) {" + nl +
        "    ggml_backend_vk_device_context * ctx = (ggml_backend_vk_device_context *)dev->context;" + nl +
        "    const vk_device& device = ggml_vk_get_device(ctx->device);" + nl)
sub("LAB_VK_CPU_OPS", head, head + nl.join([
    "    if (const char * lab_cpu = getenv(\"LAB_VK_CPU_OPS\")) {",
    "        const std::string lab_list = std::string(\";\") + lab_cpu + \";\";",
    "        const std::string lab_name = ggml_op_name(op->op);",
    "        const std::string lab_typed = lab_name + \":\" + (op->src[0] ? ggml_type_name(op->src[0]->type) : \"-\");",
    "        if (lab_list.find(\";\" + lab_name + \";\") != std::string::npos || lab_list.find(\";\" + lab_typed + \";\") != std::string::npos) {",
    "            return false;",
    "        }",
    "    }",
    ""]), 1)
# The CPU-ops order (16:21) named FLASH_ATTN_EXT: attention on the CPU repeats, everything else on the card does not
# matter. These cut inside it. LAB_VK_FA_SCALAR: batches use the scalar attention shader (what a batch of one already
# uses) instead of flash_attn_cm1; LAB_VK_FA_F32ACC: 32-bit accumulators; LAB_VK_FA_NO_MASK_OPT: no mask shortcut
# bits; LAB_VK_FA_NO_SPLIT_K: the KV range is never split across workgroups.
sub("LAB_VK_FA_SCALAR", "    // scalar is faster than coopmat when N==1" + nl,
    "    if (getenv(\"LAB_VK_FA_SCALAR\") != nullptr) { path = FA_SCALAR; }" + nl + "    // scalar is faster than coopmat when N==1" + nl, 1)
sub("LAB_VK_FA_F32ACC", "const bool f32acc = !ctx->device->fp16 || dst->op_params[3] == GGML_PREC_F32 || k->type == GGML_TYPE_BF16;",
    "const bool f32acc = getenv(\"LAB_VK_FA_F32ACC\") != nullptr || !ctx->device->fp16 || dst->op_params[3] == GGML_PREC_F32 || k->type == GGML_TYPE_BF16;", 1)
sub("LAB_VK_FA_NO_MASK_OPT", "bool use_mask_opt = mask && !use_sparse &&",
    "bool use_mask_opt = getenv(\"LAB_VK_FA_NO_MASK_OPT\") == nullptr && mask && !use_sparse &&", 1)
sub("LAB_VK_FA_NO_SPLIT_K", "    if (!use_sparse && split_k > 1) {" + nl,
    "    if (getenv(\"LAB_VK_FA_NO_SPLIT_K\") != nullptr) { split_k = 1; }" + nl + "    if (!use_sparse && split_k > 1) {" + nl, 1)
# The attention order (16:29) named the batch shader flash_attn_cm1.comp (LAB_VK_FA_SCALAR repeats). LAB_VK_FA_BITS=<n>
# rides in spare bits of a push constant and switches probes INSIDE that shader at run time:
#   1  attention weights of masked cells are forced to exactly 0      2  query rows past the batch are zeroed
#   4  a fully masked block is never skipped                          8  masked scores are forced to -inf
sub("LAB_VK_FA_BITS", "uint32_t mask_n_head_log2 = ((sinks != nullptr) << 24) | n_head_log2;",
    "uint32_t mask_n_head_log2 = ((sinks != nullptr) << 24) | n_head_log2 | (getenv(\"LAB_VK_FA_BITS\") ? ((uint32_t)atoi(getenv(\"LAB_VK_FA_BITS\")) << 25) : 0u);", 1)
# The four shader probes (16:36) changed nothing. Which USE of the shader: a batch of one runs it too, in "grouped
# query" mode (N <= 8: the tile's rows become the query heads of one token). LAB_VK_FA_NO_GQA: that mode is off, so a
# batch of one uses the scalar shader. LAB_VK_FA_SCALAR_ROWS_GT=<n>: scalar only when the tile has more than n rows
# (real batches), the grouped mode keeps the coopmat shader. LAB_VK_FA_UNALIGNED: the bounds-checked shader variant.
sub("LAB_VK_FA_NO_GQA", "    if (N <= 8 && qk_ratio > 1 && qk_ratio <= max_gqa &&",
    "    if (getenv(\"LAB_VK_FA_NO_GQA\") == nullptr && N <= 8 && qk_ratio > 1 && qk_ratio <= max_gqa &&", 1)
sub("LAB_VK_FA_SCALAR_ROWS_GT", "    if (getenv(\"LAB_VK_FA_SCALAR\") != nullptr) { path = FA_SCALAR; }",
    "    if (getenv(\"LAB_VK_FA_SCALAR\") != nullptr) { path = FA_SCALAR; }" + nl +
    "    if (getenv(\"LAB_VK_FA_SCALAR_ROWS_GT\") != nullptr && n_rows != 512 && n_rows > (uint32_t)atoi(getenv(\"LAB_VK_FA_SCALAR_ROWS_GT\"))) { path = FA_SCALAR; }", 1)
sub("LAB_VK_FA_UNALIGNED", "    bool aligned = (KV % alignment) == 0 &&",
    "    bool aligned = getenv(\"LAB_VK_FA_UNALIGNED\") == nullptr && (KV % alignment) == 0 &&", 1)


def L(*lines):
    return nl.join(lines)


# LAB_VK_FA_HASH: both uses of the coopmat attention shader carry history (16:43) and no probe inside it moved the
# result, so LOOK: every attention node is submitted alone, and its inputs (q, k, v, mask, sinks) and its output are
# fingerprinted straight off the card. The first attention call whose output differs between a fresh ask and a repeat
# says which call, with which shapes, and whether q and the mask still matched.
sub("LAB_VK_FA_HASH submit", "        bool enqueued = ggml_vk_build_graph(ctx, cgraph, i, cgraph->nodes[submit_node_idx],",
    L("        if (getenv(\"LAB_VK_FA_HASH\") != nullptr) {",
      "            const int lab_next = i + ctx->num_additional_fused_ops + 1;",
      "            if (cgraph->nodes[i]->op == GGML_OP_FLASH_ATTN_EXT ||",
      "                (lab_next < cgraph->n_nodes && cgraph->nodes[lab_next]->op == GGML_OP_FLASH_ATTN_EXT)) {",
      "                submit = true;",
      "            }",
      "        }",
      "        if (getenv(\"LAB_VK_ALL_HASH\") != nullptr) { submit = true; }",
      "        bool enqueued = ggml_vk_build_graph(ctx, cgraph, i, cgraph->nodes[submit_node_idx],"), 1)
sub("LAB_VK_FA_HASH before", L("        // Do staging buffer copies", "        for (auto& cpy : subctx->in_memcpys) {"),
    L("        lab_fa_before(ctx, cgraph, tensor_idx);", "        // Do staging buffer copies", "        for (auto& cpy : subctx->in_memcpys) {"), 1)
sub("LAB_VK_FA_HASH after", L("        ctx->submit_pending = true;", "", "#ifdef GGML_VULKAN_CHECK_RESULTS", "        ggml_vk_synchronize(ctx);"),
    L("        ctx->submit_pending = true;", "        lab_fa_after(ctx, cgraph, tensor_idx);", "", "#ifdef GGML_VULKAN_CHECK_RESULTS", "        ggml_vk_synchronize(ctx);"), 1)
head = "void ggml_vk_compute_forward(ggml_backend_vk_context * ctx, ggml_cgraph * cgraph, ggml_tensor * tensor, int tensor_idx, bool almost_ready) {"
assert s.count(head) >= 1, "compute_forward's head was not found"
first = s.index(head)
s = s[:first] + L("void ggml_vk_synchronize(ggml_backend_vk_context * ctx);",
                  "static void lab_fa_before(ggml_backend_vk_context * ctx, ggml_cgraph * cgraph, int tensor_idx);",
                  "static void lab_fa_after(ggml_backend_vk_context * ctx, ggml_cgraph * cgraph, int tensor_idx);", "") + s[first:]
s += L("",
       "// ===== LAB DIAGNOSTIC (2026-11-11, never a serving build): LAB_VK_FA_HASH =====",
       "static uint64_t lab_hash_tensor(const ggml_tensor * t, size_t lab_max = 1024ull * 1024ull * 1024ull) {",
       "    if (t == nullptr || t->buffer == nullptr) { return 0; }",
       "    size_t n = ggml_nbytes(t);",
       "    if (n == 0) { return 0; }",
       "    if (n > lab_max) { return 1; }",
       "    std::vector<uint8_t> tmp(n, 0);",
       "    if (ggml_backend_buffer_is_vk(t->buffer)) {",
       "        ggml_backend_vk_buffer_context * buf_ctx = (ggml_backend_vk_buffer_context *)t->buffer->context;",
       "        vk_buffer& buffer_gpu = buf_ctx->dev_buffer;",
       "        uint64_t offset = vk_tensor_offset(t) + t->view_offs;",
       "        if (offset >= buffer_gpu->size) { return 2; }",
       "        if (offset + n > buffer_gpu->size) { n = buffer_gpu->size - offset; }",
       "        ggml_vk_buffer_read(buffer_gpu, offset, tmp.data(), n);",
       "    } else if (t->data != nullptr) {",
       "        memcpy(tmp.data(), t->data, n);",
       "    } else {",
       "        return 3;",
       "    }",
       "    uint64_t h = 1469598103934665603ull;",
       "    for (size_t i = 0; i < n; i++) { h = (h ^ tmp[i]) * 1099511628211ull; }",
       "    return h;",
       "}",
       "static uint64_t lab_fa_in[5] = {0, 0, 0, 0, 0};",
       "static uint64_t lab_fa_count = 0;",
       "static void lab_fa_before(ggml_backend_vk_context * ctx, ggml_cgraph * cgraph, int tensor_idx) {",
       "    GGML_UNUSED(ctx);",
       "    if (getenv(\"LAB_VK_ALL_HASH\") != nullptr) {      // every op: inputs up to 4 MB (weights are constants)",
       "        const ggml_tensor * ta = cgraph->nodes[tensor_idx];",
       "        for (int i = 0; i < 4; i++) { lab_fa_in[i] = lab_hash_tensor(ta->src[i], 4ull * 1024ull * 1024ull); }",
       "        return;",
       "    }",
       "    if (getenv(\"LAB_VK_FA_HASH\") == nullptr) { return; }",
       "    const ggml_tensor * t = cgraph->nodes[tensor_idx];",
       "    if (t->op != GGML_OP_FLASH_ATTN_EXT) { return; }",
       "    for (int i = 0; i < 5; i++) { lab_fa_in[i] = lab_hash_tensor(t->src[i]); }",
       "}",
       "static void lab_fa_after(ggml_backend_vk_context * ctx, ggml_cgraph * cgraph, int tensor_idx) {",
       "    if (getenv(\"LAB_VK_ALL_HASH\") != nullptr) {",
       "        ggml_vk_synchronize(ctx);",
       "        const ggml_tensor * ta = cgraph->nodes[tensor_idx];",
       "        std::cerr << \"LABOP \" << tensor_idx << \" \" << ggml_op_name(ta->op) << \" name=\" << ta->name",
       "                  << \" ne=\" << ta->ne[0] << \"x\" << ta->ne[1] << \"x\" << ta->ne[2] << \"x\" << ta->ne[3]",
       "                  << \" in=\" << lab_fa_in[0] << \",\" << lab_fa_in[1] << \",\" << lab_fa_in[2] << \",\" << lab_fa_in[3]",
       "                  << \" out=\" << lab_hash_tensor(ta, 64ull * 1024ull * 1024ull) << std::endl;",
       "        return;",
       "    }",
       "    if (getenv(\"LAB_VK_FA_HASH\") == nullptr) { return; }",
       "    ggml_vk_synchronize(ctx);",
       "    const ggml_tensor * t = cgraph->nodes[tensor_idx];",
       "    if (t->op != GGML_OP_FLASH_ATTN_EXT) { return; }",
       "    const ggml_tensor * q = t->src[0];",
       "    const ggml_tensor * k = t->src[1];",
       "    const ggml_tensor * m = t->src[3];",
       "    std::cerr << \"LABFA \" << ++lab_fa_count << \" name=\" << t->name",
       "              << \" q=\" << q->ne[0] << \"x\" << q->ne[1] << \"x\" << q->ne[2]",
       "              << \" k=\" << k->ne[0] << \"x\" << k->ne[1] << \"x\" << k->ne[2] << \":\" << ggml_type_name(k->type)",
       "              << \" m=\" << (m ? m->ne[0] : 0) << \"x\" << (m ? m->ne[1] : 0)",
       "              << \" in=\" << lab_fa_in[0] << \",\" << lab_fa_in[1] << \",\" << lab_fa_in[2] << \",\" << lab_fa_in[3] << \",\" << lab_fa_in[4]",
       "              << \" out=\" << lab_hash_tensor(t) << std::endl;",
       "}", "")
io.open(p, "w", encoding="utf-8", newline="").write(s)

p2 =os.path.join(DST, "ggml", "src", "ggml-vulkan", "vulkan-shaders", "flash_attn_cm1.comp")
s = io.open(p2, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in s else "\n"


def L(*lines):
    return nl.join(lines)


sub("shader bit 1 (masked weights 0)",
    "                    const FLOAT_TYPEV4 Pf = FLOAT_TYPEV4(exp(vec4(sfsh[row / 4 + col * sfshstride]) - mfvec));",
    L("                    const vec4 lab_s = vec4(sfsh[row / 4 + col * sfshstride]);",
      "                    vec4 lab_p = exp(lab_s - mfvec);",
      "                    if ((p.mask_n_head_log2 & (1u << 25)) != 0) {",
      "                        lab_p = mix(lab_p, vec4(0.0), lessThanEqual(lab_s, vec4(-30000.0)));",
      "                    }",
      "                    // bit 64: the weights of DEAD cells (masked for the batch's last row) forced to 0 by the mask itself",
      "                    if (MASK_ENABLE && (p.mask_n_head_log2 & (1u << 31)) != 0 &&",
      "                        float(data_m[m_offset + (N - 1) * m_stride + (j * Bc + col)]) <= -30000.0) {",
      "                        lab_p = vec4(0.0);",
      "                    }",
      "                    // bit 4 (reused 17:25): the weights of the tile's UNUSED rows (past the batch) forced to 0",
      "                    if ((p.mask_n_head_log2 & (1u << 27)) != 0) {",
      "                        const uint lab_r0 = (p.gqa_ratio > 1) ? row : (i * Br + row);",
      "                        lab_p = mix(vec4(0.0), lab_p, lessThan(uvec4(lab_r0, lab_r0 + 1, lab_r0 + 2, lab_r0 + 3), uvec4(N)));",
      "                    }",
      "                    const FLOAT_TYPEV4 Pf = FLOAT_TYPEV4(lab_p);"), 1)
sub("shader bit 2 (query rows zeroed)",
    L("            Qf[r * qstride + d] = FLOAT_TYPEV4(data_qv4[q_offset / 4 + (i * Br + r) * q_stride / 4 + d] * p.scale);",
      "        }"),
    L("            Qf[r * qstride + d] = FLOAT_TYPEV4(data_qv4[q_offset / 4 + (i * Br + r) * q_stride / 4 + d] * p.scale);",
      "        } else if ((p.mask_n_head_log2 & (1u << 26)) != 0 && r < Br && d < HSK / 4) {",
      "            Qf[r * qstride + d] = FLOAT_TYPEV4(0);",
      "        }"), 1)
sub("shader bit 4 (never skip)",
    "                if (max_mask <= NEG_FLT_MAX_OVER_2) {",
    "                if (max_mask <= NEG_FLT_MAX_OVER_2 && (p.mask_n_head_log2 & (1u << 27)) == 0) {", 1)
sub("shader bit 8 (masked scores -inf)",
    "                        ACC_TYPEV4 masks = ACC_TYPEV4(mask_cache[idx / WorkGroupSize]);",
    L("                        ACC_TYPEV4 masks = ACC_TYPEV4(mask_cache[idx / WorkGroupSize]);",
      "                        if ((p.mask_n_head_log2 & (1u << 28)) != 0) {",
      "                            vec4 lab_m = vec4(masks);",
      "                            lab_m = mix(lab_m, vec4(uintBitsToFloat(0xFF800000u)), lessThanEqual(lab_m, vec4(-30000.0)));",
      "                            masks = ACC_TYPEV4(lab_m);",
      "                        }"), 1)
# The every-op fingerprints (17:04): on the repeat ask the first attention call has the same q and the same mask, K/V
# differ only in dead cells (masked for every row: leftovers of the earlier answer), and its output differs. Bits 16
# and 32 zero the K, resp. the V, of dead cells as they are staged, to see which one carries it.
sub("shader bit 16 (dead K zeroed)", "                        kvsh[row * kvsh_stride + col_vec] = K_Tf;",
    L("                        if (MASK_ENABLE && (p.mask_n_head_log2 & (1u << 29)) != 0 &&",
      "                            float(data_m[m_offset + (N - 1) * m_stride + (j * Bc + row)]) <= -30000.0) {",
      "                            K_Tf = FLOAT_TYPEV4(0);",
      "                        }",
      "                        kvsh[row * kvsh_stride + col_vec] = K_Tf;"), 1)
sub("shader bit 32 (dead V zeroed)",
    "                    if (USE_SPARSE ? (kv_active && v_col < HSV) : (!KV_bounds_check || (v_row < KV && v_col < HSV))) {",
    L("                    const bool lab_dead_v = MASK_ENABLE && (p.mask_n_head_log2 & (1u << 30)) != 0 &&",
      "                                            float(data_m[m_offset + (N - 1) * m_stride + v_row]) <= -30000.0;",
      "                    if (!lab_dead_v && (USE_SPARSE ? (kv_active && v_col < HSV) : (!KV_bounds_check || (v_row < KV && v_col < HSV)))) {"), 1)
io.open(p2, "w", encoding="utf-8", newline="").write(s)
print("made", DST, "| sites patched:", counts)
