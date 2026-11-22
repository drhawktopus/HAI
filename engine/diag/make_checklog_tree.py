"""Make the DIAGNOSTIC source tree llama.cpp-620d222-r4-checklog from the lab engine's r4 tree.

One change: ggml-vulkan-debug.cpp's result checker (built only with -DGGML_VULKAN_CHECK_RESULTS=ON) logs every op's
error against the CPU when GGML_VK_LOG_CHECKS is set, so two identical asks can be compared op by op. Upstream prints
only large errors. This tree is never a serving build and never gets a lab revision number.

    python make_checklog_tree.py"""
import io
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "llama.cpp-620d222-r4")
DST = os.path.join(HERE, "llama.cpp-620d222-r4-checklog")


def skip(d, names):
    return [n for n in names if n == ".git" or (n.startswith("build") and os.path.isdir(os.path.join(d, n)))]


if os.path.isdir(DST):
    shutil.rmtree(DST)
shutil.copytree(SRC, DST, ignore=skip)
p = os.path.join(DST, "ggml", "src", "ggml-vulkan", "ggml-vulkan-debug.cpp")
s = io.open(p, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in s else "\n"
anchor = "    avg_err /= counter;" + nl + nl + "    if (vk_output_tensor > 0 && vk_output_tensor == check_counter) {"
assert s.count(anchor) == 1, "the checker's anchor was found %d times" % s.count(anchor)
add = nl.join([
    "    avg_err /= counter;",
    "",
    "    // LAB DIAGNOSTIC (2026-11-11, never a serving build): every op's error against the CPU, for an op-by-op compare",
    "    if (getenv(\"GGML_VK_LOG_CHECKS\") != nullptr) {",
    "        std::cerr << \"LABCHECK \" << check_counter << \" \" << ggml_op_name(tensor->op) << \" name=\" << tensor->name",
    "                  << \" ne=\" << tensor->ne[0] << \"x\" << tensor->ne[1] << \"x\" << tensor->ne[2] << \"x\" << tensor->ne[3]",
    "                  << \" src0=\" << (src0 ? ggml_type_name(src0->type) : \"-\") << \":\" << (src0 ? src0->ne[0] : 0) << \"x\" << (src0 ? src0->ne[1] : 0)",
    "                  << \" avg_err=\" << avg_err << std::endl;",
    "    }",
    "",
    "    if (vk_output_tensor > 0 && vk_output_tensor == check_counter) {"])
s = s.replace(anchor, add)
# Upstream's checker aborts the runner at 1 % average error; a quantised multiply sits just above that (first run:
# MUL_MAT q4_K 0.0118) and the runner died on its third op. With the log on, the checker only reports.
stop = "    if (avg_err > 0.01 || std::isnan(avg_err)) {" + nl + "        std::cerr << \"ERROR: avg_err=\" << avg_err << \" in \" << ggml_op_name(tensor->op) << \" (check \" << check_counter"
assert s.count(stop) == 1, "the checker's abort test was found %d times" % s.count(stop)
s = s.replace(stop, stop.replace("    if (avg_err > 0.01 || std::isnan(avg_err)) {",
                                 "    if ((avg_err > 0.01 || std::isnan(avg_err)) && getenv(\"GGML_VK_LOG_CHECKS\") == nullptr) {"))
# HASH MODE (GGML_VK_LOG_HASH): no CPU recompute at all (the third order died at op 971 inside it). Each op's inputs are
# fingerprinted before it runs and its output after, straight off the GPU; the first op whose output differs between
# two identical asks while its inputs match is the one reading something left behind.
helper = nl.join([
    "static uint64_t lab_hash_tensor(ggml_tensor * t, size_t max_bytes) {",
    "    if (t == nullptr || t->buffer == nullptr) { return 0; }",
    "    size_t n = ggml_nbytes(t);",
    "    if (n == 0) { return 0; }",
    "    if (n > max_bytes) { return 1; }",
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
    "static uint64_t lab_src_hash[3] = {0, 0, 0};",
    "",
    "size_t check_counter = 0;",
    "void ggml_vk_check_results_0(ggml_backend_vk_context * ctx, ggml_cgraph * cgraph, int tensor_idx) {"])
head0 = "size_t check_counter = 0;" + nl + "void ggml_vk_check_results_0(ggml_backend_vk_context * ctx, ggml_cgraph * cgraph, int tensor_idx) {"
assert s.count(head0) == 1, "check_results_0's head was found %d times" % s.count(head0)
s = s.replace(head0, helper)
cnt = "    check_counter++;" + nl + "    if (!(vk_output_tensor > 0 && vk_output_tensor == check_counter) && check_counter <= vk_skip_checks) {"
assert s.count(cnt) == 1, "check_results_0's counter was found %d times" % s.count(cnt)
s = s.replace(cnt, nl.join([
    "    check_counter++;",
    "    if (getenv(\"GGML_VK_LOG_HASH\") != nullptr) {",
    "        std::cerr << \"LABSTEP \" << check_counter << \" before \" << ggml_op_name(tensor->op) << \" name=\" << tensor->name << std::endl;",
    "        for (int i = 0; i < 3; i++) {",
    "            lab_src_hash[i] = lab_hash_tensor(tensor->src[i], 64ull * 1024ull * 1024ull);",
    "            std::cerr << \"LABSTEP \" << check_counter << \" src\" << i << \" read\" << std::endl;",
    "        }",
    "        return;",
    "    }",
    "    if (!(vk_output_tensor > 0 && vk_output_tensor == check_counter) && check_counter <= vk_skip_checks) {"]))
dbg1 = "    VK_LOG_DEBUG(\"ggml_vk_check_results_1(\" << tensor->name << \")\");"
assert s.count(dbg1) == 1, "check_results_1's first log was found %d times" % s.count(dbg1)
s = s.replace(dbg1, nl.join([
    "    if (getenv(\"GGML_VK_LOG_HASH\") != nullptr) {",
    "        std::cerr << \"LABSTEP \" << check_counter << \" ran\" << std::endl;",
    "        ggml_tensor * hs0 = tensor->src[0];",
    "        ggml_tensor * hs1 = tensor->src[1];",
    "        std::cerr << \"LABHASH \" << check_counter << \" \" << ggml_op_name(tensor->op) << \" name=\" << tensor->name",
    "                  << \" ne=\" << tensor->ne[0] << \"x\" << tensor->ne[1] << \"x\" << tensor->ne[2] << \"x\" << tensor->ne[3]",
    "                  << \" src0=\" << (hs0 ? ggml_type_name(hs0->type) : \"-\") << \":\" << (hs0 ? hs0->ne[0] : 0) << \"x\" << (hs0 ? hs0->ne[1] : 0)",
    "                  << \" src1=\" << (hs1 ? ggml_type_name(hs1->type) : \"-\") << \":\" << (hs1 ? hs1->ne[0] : 0) << \"x\" << (hs1 ? hs1->ne[1] : 0)",
    "                  << \" in=\" << lab_src_hash[0] << \",\" << lab_src_hash[1] << \",\" << lab_src_hash[2]",
    "                  << \" out=\" << lab_hash_tensor(tensor, 2048ull * 1024ull * 1024ull) << std::endl;",
    "        return;",
    "    }",
    dbg1]))
for inc in ("#include <vector>", "#include <cstdint>", "#include <cstring>"):
    if inc not in s:
        first = s.index("#include")
        s = s[:first] + inc + nl + s[first:]
if "#include <cstdlib>" not in s:
    first = s.index("#include")
    s = s[:first] + "#include <cstdlib>" + nl + s[first:]
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("made", DST, "| LABCHECK lines:", s.count("LABCHECK"), "| LABHASH lines:", s.count("LABHASH"), "| newline", repr(nl))
