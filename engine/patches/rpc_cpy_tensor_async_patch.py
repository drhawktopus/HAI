"""Lab patch for llama.cpp ggml-rpc: async RPC -> RPC tensor copies, so pipeline stages on RPC devices overlap.

Usage (in the async-RPC worktree):  python3 rpc_cpy_tensor_async_patch.py ggml/src/ggml-rpc/ggml-rpc.cpp
Every anchor must match exactly once or NOTHING is written. Written for upstream 23b0202.
Client-side only: no RPC protocol change, so the rpc-servers on the other machines are untouched."""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()
if "ggml_backend_rpc_cpy_tensor_async" in src:
    sys.exit("already patched")


def sub(text, anchor, new, label):
    n = text.count(anchor)
    if n != 1:
        sys.exit("ABORT: anchor '%s' found %d times (want 1) - nothing written" % (label, n))
    return text.replace(anchor, new)


if "#include <functional>" not in src:
    src = sub(src, "#include <future>", "#include <future>\n#include <functional>", "include future")

src = sub(src,
          "        std::promise<void>            completion;\n    };\n    using rpc_msg_ptr",
          "        std::promise<void>            completion;\n"
          "        std::function<void()>         fn;          // #36: run on the dispatcher thread, in queue order\n"
          "    };\n    using rpc_msg_ptr",
          "rpc_msg.completion")

src = sub(src,
          "    void send_async(enum rpc_cmd cmd, std::shared_ptr<const void> input, size_t input_size, void * output, size_t output_size);\n",
          "    void send_async(enum rpc_cmd cmd, std::shared_ptr<const void> input, size_t input_size, void * output, size_t output_size);\n"
          "    // #36: a GET queued now (ordered after the work already queued) whose completion can be waited on later\n"
          "    std::shared_future<void> get_async(std::shared_ptr<const void> input, size_t input_size, void * output, size_t output_size);\n"
          "    // #36: run fn ON this dispatcher's thread in queue order; only fn may call send_inline\n"
          "    void post(std::function<void()> fn);\n"
          "    bool send_inline(enum rpc_cmd cmd, const void * input, size_t input_size, void * output, size_t output_size);\n",
          "send_async decl")

src = sub(src,
          "ggml_backend_event_t rpc_dispatcher::event_new(ggml_backend_dev_t dev) {",
          "std::shared_future<void> rpc_dispatcher::get_async(std::shared_ptr<const void> input, size_t input_size, void * output, size_t output_size) {\n"
          "    auto msg = std::make_shared<rpc_msg>();\n"
          "    msg->cmd = RPC_CMD_GET_TENSOR;\n"
          "    msg->input = input;\n"
          "    msg->input_size = input_size;\n"
          "    msg->output = output;\n"
          "    msg->output_size = output_size;\n"
          "    std::shared_future<void> done = msg->completion.get_future().share();\n"
          "    GGML_ASSERT(queue.push(msg));\n"
          "    return done;\n"
          "}\n\n"
          "void rpc_dispatcher::post(std::function<void()> fn) {\n"
          "    auto msg = std::make_shared<rpc_msg>();\n"
          "    msg->cmd = RPC_CMD_NONE;\n"
          "    msg->fn = std::move(fn);\n"
          "    GGML_ASSERT(queue.push(msg));\n"
          "}\n\n"
          "bool rpc_dispatcher::send_inline(enum rpc_cmd cmd, const void * input, size_t input_size, void * output, size_t output_size) {\n"
          "    return output ? send_rpc_cmd(sock, cmd, input, input_size, output, output_size)\n"
          "                  : send_rpc_cmd(sock, cmd, input, input_size);\n"
          "}\n\n"
          "ggml_backend_event_t rpc_dispatcher::event_new(ggml_backend_dev_t dev) {",
          "event_new def")

src = sub(src,
          "        msg_ptr->completion.set_value();\n        last_cmd",
          "        if (msg_ptr->fn) {\n"
          "            msg_ptr->fn();\n"
          "        }\n"
          "        msg_ptr->completion.set_value();\n        last_cmd",
          "work completion")

src = sub(src,
          "static ggml_backend_i ggml_backend_rpc_interface = {",
          "// #36 (lab patch): queue RPC -> RPC copies instead of blocking. Without it ggml_backend_sched falls back to\n"
          "// ggml_backend_synchronize(input_backend) + a host copy for every stage hand-off, so the one host thread waits\n"
          "// for each stage to finish a ubatch before it can feed the next and pipeline stages compute one at a time\n"
          "// (measured on rigby 2026-10-10). The GET is queued on the SOURCE now, right after the compute that produced the\n"
          "// tensor, so it snapshots this ubatch before a later one can reuse the buffer; the SET runs on the DESTINATION's\n"
          "// thread once that GET has landed, ahead of the destination's compute. Copies only flow forward along a pipeline,\n"
          "// so no dispatcher ever waits on a later one. Anything that is not RPC -> RPC keeps the scheduler's sync path.\n"
          "static bool ggml_backend_rpc_cpy_tensor_async(ggml_backend_t backend_src, ggml_backend_t backend_dst, const ggml_tensor * src, ggml_tensor * dst) {\n"
          "    GGML_UNUSED(backend_src);\n"
          "    if (backend_dst->iface.graph_compute != ggml_backend_rpc_graph_compute ||\n"
          "        src->buffer == nullptr || dst->buffer == nullptr ||\n"
          "        !ggml_backend_buffer_is_rpc(src->buffer) || !ggml_backend_buffer_is_rpc(dst->buffer) ||\n"
          "        !ggml_is_contiguous(src) || !ggml_is_contiguous(dst) || ggml_nbytes(src) != ggml_nbytes(dst)) {\n"
          "        return false;\n"
          "    }\n"
          "    auto * sctx = (ggml_backend_rpc_buffer_context *)src->buffer->context;\n"
          "    auto * dctx = (ggml_backend_rpc_buffer_context *)dst->buffer->context;\n"
          "    std::shared_ptr<rpc_dispatcher> sd = sctx->dispatcher;\n"
          "    std::shared_ptr<rpc_dispatcher> dd = dctx->dispatcher;\n"
          "    if (sd == dd) {\n"
          "        auto request = std::make_shared<rpc_msg_copy_tensor_req>();\n"
          "        request->src = serialize_tensor(src);\n"
          "        request->dst = serialize_tensor(dst);\n"
          "        dd->post([dd, request]() {\n"
          "            rpc_msg_copy_tensor_rsp response;\n"
          "            RPC_STATUS_ASSERT(dd->send_inline(RPC_CMD_COPY_TENSOR, request.get(), sizeof(*request), &response, sizeof(response)));\n"
          "            GGML_ASSERT(response.result);\n"
          "        });\n"
          "        return true;\n"
          "    }\n"
          "    const size_t nbytes = ggml_nbytes(src);\n"
          "    auto request = std::make_shared<rpc_msg_get_tensor_req>();\n"
          "    request->tensor = serialize_tensor(src);\n"
          "    request->offset = 0;\n"
          "    request->size   = nbytes;\n"
          "    auto data = std::make_shared<std::vector<uint8_t>>(nbytes);\n"
          "    std::shared_future<void> got = sd->get_async(request, sizeof(*request), data->data(), nbytes);\n"
          "    const rpc_tensor rd = serialize_tensor(dst);\n"
          "    dd->post([dd, sd, request, data, got, rd, nbytes]() {\n"
          "        got.wait();\n"
          "        size_t input_size;\n"
          "        auto input = serialize_set_tensor(rd, 0, 0, data->data(), nbytes, input_size);\n"
          "        RPC_STATUS_ASSERT(dd->send_inline(RPC_CMD_SET_TENSOR, input.get(), input_size, nullptr, 0));\n"
          "    });\n"
          "    return true;\n"
          "}\n\n"
          "static ggml_backend_i ggml_backend_rpc_interface = {",
          "interface def")

src = sub(src,
          "    /* .cpy_tensor_async        = */ NULL,\n    /* .synchronize             = */ ggml_backend_rpc_synchronize,",
          "    /* .cpy_tensor_async        = */ ggml_backend_rpc_cpy_tensor_async,\n    /* .synchronize             = */ ggml_backend_rpc_synchronize,",
          "interface slot")

open(path, "w", encoding="utf-8", newline="\n").write(src)
print("patched: get_async, post, send_inline, work() runs fn, ggml_backend_rpc_cpy_tensor_async, interface slot")
