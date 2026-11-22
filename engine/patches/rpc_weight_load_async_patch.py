"""Lab patch 5 of the async-RPC set for llama.cpp ggml-rpc (upstream 23b0202, AFTER patches 1, trace, 3 and 4): load
weights onto every RPC server AT ONCE instead of one tensor round trip at a time.

Why: llama.cpp's loader walks the GGUF one tensor at a time, and every weight upload to an RPC server was a
synchronous send - with `rpc-server -c` preceded by a synchronous hash query - so only one server's link was ever
busy (a ~7.2 GB MoE went to nine tablets one after another at ~20 MB/s, 6-7 minutes). Now a weight upload copies the
bytes at call time and POSTS a job to that server's own dispatcher thread, which does the hash query (when the size
qualifies for the cache), and on a miss sends the tensor with the cache flag set. The loader moves straight on, so
every server's queue fills and drains concurrently.

Ordering is unchanged: every command to a server still goes through that server's one queue, so a weight lands
before any later compute or read. Both upload paths are covered (the buffer's set_tensor and the backend's
set_tensor_async, which the loader uses when the device reports async + events). Cost: queued uploads hold a copy
of their bytes until sent, so the host's RAM briefly holds up to the servers' combined share.

Usage (in the worktree):  python3 rpc_weight_load_async_patch.py ggml/src/ggml-rpc/ggml-rpc.cpp
Every anchor must match exactly once or NOTHING is written."""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()
if "#36 patch 5" in src:
    sys.exit("already patched")
if "#36 patch 3" not in src or "ggml_backend_rpc_cpy_tensor_async" not in src:
    sys.exit("ABORT: apply patches 1 and 3 first - nothing written")


def sub(text, anchor, new, label):
    n = text.count(anchor)
    if n != 1:
        sys.exit("ABORT: anchor '%s' found %d times (want 1) - nothing written" % (label, n))
    return text.replace(anchor, new)


HELPER = (
    "// #36 patch 5: a weight upload is copied now and sent by the server's own dispatcher thread, hash check\n"
    "// included, so model loading fills every server's link at once instead of one round trip at a time\n"
    "static void rpc_post_weight_set(std::shared_ptr<rpc_dispatcher> d, const rpc_tensor & rt, const void * data,\n"
    "                                size_t offset, size_t size, bool use_hash) {\n"
    "    size_t input_size;\n"
    "    auto input = serialize_set_tensor(rt, 0, offset, data, size, input_size);\n"
    "    d->post([d, input, input_size, rt, offset, size, use_hash]() {\n"
    "        if (use_hash) {\n"
    "            rpc_msg_set_tensor_hash_req req;\n"
    "            req.tensor = rt;\n"
    "            req.offset = offset;\n"
    "            req.hash   = fnv_hash(input.get() + (input_size - size), size);\n"
    "            rpc_msg_set_tensor_hash_rsp rsp;\n"
    "            RPC_STATUS_ASSERT(d->send_inline(RPC_CMD_SET_TENSOR_HASH, &req, sizeof(req), &rsp, sizeof(rsp)));\n"
    "            if (rsp.result) {\n"
    "                return;\n"
    "            }\n"
    "            input.get()[sizeof(rpc_tensor)] = 1;   // cache_flag: the server keeps a copy\n"
    "        }\n"
    "        RPC_STATUS_ASSERT(d->send_inline(RPC_CMD_SET_TENSOR, input.get(), input_size, nullptr, 0));\n"
    "    });\n"
    "}\n\n")

BUF = ("static void ggml_backend_rpc_buffer_set_tensor(ggml_backend_buffer_t buffer, ggml_tensor * tensor, "
       "const void * data, size_t offset, size_t size) {\n"
       "    ggml_backend_rpc_buffer_context * ctx = (ggml_backend_rpc_buffer_context *)buffer->context;\n")
src = sub(src, BUF,
          HELPER + BUF +
          "    if (tensor->buffer->usage == GGML_BACKEND_BUFFER_USAGE_WEIGHTS) {\n"
          "        rpc_post_weight_set(ctx->dispatcher, serialize_tensor(tensor), data, offset, size,\n"
          "                            rpc_use_hash_cache(tensor, size));\n"
          "        return;\n"
          "    }\n",
          "buffer set_tensor")

ASY = ("static void ggml_backend_rpc_set_tensor_async(ggml_backend_t backend, ggml_tensor * tensor, "
       "const void * data, size_t offset, size_t size) {\n"
       "    ggml_backend_rpc_context * ctx = (ggml_backend_rpc_context *)backend->context;\n")
src = sub(src, ASY,
          ASY +
          "    if (tensor->buffer && tensor->buffer->usage == GGML_BACKEND_BUFFER_USAGE_WEIGHTS) {\n"
          "        rpc_post_weight_set(ctx->dispatcher, serialize_tensor(tensor), data, offset, size,\n"
          "                            rpc_use_hash_cache(tensor, size));\n"
          "        return;\n"
          "    }\n",
          "backend set_tensor_async")

open(path, "w", encoding="utf-8", newline="\n").write(src)
print("patched: weight uploads posted to each server's dispatcher (hash check included)")
