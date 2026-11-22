"""Lab patch 3 of the async-RPC set for llama.cpp ggml-rpc (upstream 23b0202, AFTER rpc_cpy_tensor_async_patch.py):
post the scheduler's per-chunk INPUT writes instead of waiting for them.

Why: ggml_backend_sched_copy_input copies user inputs (positions, KQ mask) with a SYNCHRONOUS
ggml_backend_tensor_copy -> ggml_backend_rpc_buffer_set_tensor -> dispatcher->send(), which waits for the stage's
dispatcher queue. That queue already holds the posted activation copy from the previous stage, so the host thread
blocks until the previous stage finishes - for every stage in turn. A trace shows it: the host starts chunk k+1 at the
instant chunk k reaches the last stage, every chunk, so the stages run one at a time.

Safe: serialize_set_tensor already copies the caller's bytes into the message (the "copied immediately" the scheduler
needs), and every command to a server goes through that server's one ordered queue, so the write still lands before
the compute that reads it and before any later GET. Weight buffers keep the synchronous path (hash cache, loader).

Usage (in the worktree):  python3 rpc_input_set_async_patch.py ggml/src/ggml-rpc/ggml-rpc.cpp
Every anchor must match exactly once or NOTHING is written."""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()
if "#36 patch 3" in src:
    sys.exit("already patched")
if "ggml_backend_rpc_cpy_tensor_async" not in src:
    sys.exit("ABORT: apply rpc_cpy_tensor_async_patch.py first - nothing written")

anchor = ("    auto input = serialize_set_tensor(rpc_tensor, cache_flag, offset, data, size, input_size);\n"
          "    ctx->dispatcher->send(RPC_CMD_SET_TENSOR, input, input_size);\n}")
n = src.count(anchor)
if n != 1:
    sys.exit("ABORT: set_tensor anchor found %d times (want 1) - nothing written" % n)
src = src.replace(anchor,
                  "    auto input = serialize_set_tensor(rpc_tensor, cache_flag, offset, data, size, input_size);\n"
                  "    if (tensor->buffer->usage != GGML_BACKEND_BUFFER_USAGE_WEIGHTS) {\n"
                  "        // #36 patch 3: the bytes are already copied into `input` and this server's queue is ordered,\n"
                  "        // so do not block the scheduler's host thread behind earlier queued work\n"
                  "        ctx->dispatcher->send_async(RPC_CMD_SET_TENSOR, input, input_size);\n"
                  "        return;\n"
                  "    }\n"
                  "    ctx->dispatcher->send(RPC_CMD_SET_TENSOR, input, input_size);\n}")
open(path, "w", encoding="utf-8", newline="\n").write(src)
print("patched: non-weight set_tensor posted (send_async), weights stay synchronous")
