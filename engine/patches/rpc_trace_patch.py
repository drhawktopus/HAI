"""#36 instrumentation for llama.cpp ggml-rpc: GGML_RPC_TRACE=1 makes every dispatcher log, to stderr, when it starts
sending a command and when that command finishes (response read, or write done) - one line each, millisecond clock,
endpoint + command. Off by default: one getenv at dispatcher start, a branch per message.

Usage (on rigby, AFTER rpc_cpy_tensor_async_patch.py):  python3 rpc_trace_patch.py ggml/src/ggml-rpc/ggml-rpc.cpp
Every anchor must match exactly once or NOTHING is written.
"""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()
if "GGML_RPC_TRACE" in src:
    sys.exit("already patched")


def sub(text, anchor, new, label):
    n = text.count(anchor)
    if n != 1:
        sys.exit("ABORT: anchor '%s' found %d times (want 1) - nothing written" % (label, n))
    return text.replace(anchor, new)


src = sub(src,
          "    std::atomic_uint busy_spin_users = 0;\n",
          "    std::atomic_uint busy_spin_users = 0;\n"
          "    bool             trace = false;      // #36: GGML_RPC_TRACE=1\n"
          "    std::string      trace_ep;\n",
          "busy_spin_users member")

src = sub(src,
          "    LOG_DBG(\"[%s] connected to %s\\n\", __func__, endpoint.c_str());\n    running = true;",
          "    LOG_DBG(\"[%s] connected to %s\\n\", __func__, endpoint.c_str());\n"
          "    trace = getenv(\"GGML_RPC_TRACE\") != nullptr;\n"
          "    trace_ep = endpoint;\n"
          "    running = true;",
          "start running")

src = sub(src,
          "        if (msg_ptr->cmd != RPC_CMD_NONE) {\n            if (msg_ptr->output) {",
          "        if (trace) {\n"
          "            fprintf(stderr, \"RPCTRACE %lld %s begin %d %zu\\n\", (long long)std::chrono::duration_cast<std::chrono::microseconds>("
          "std::chrono::steady_clock::now().time_since_epoch()).count(), trace_ep.c_str(), (int)msg_ptr->cmd, msg_ptr->input_size);\n"
          "        }\n"
          "        if (msg_ptr->cmd != RPC_CMD_NONE) {\n            if (msg_ptr->output) {",
          "work send")

src = sub(src,
          "        if (msg_ptr->fn) {\n            msg_ptr->fn();\n        }\n        msg_ptr->completion.set_value();",
          "        if (msg_ptr->fn) {\n            msg_ptr->fn();\n        }\n"
          "        if (trace) {\n"
          "            fprintf(stderr, \"RPCTRACE %lld %s end %d %s\\n\", (long long)std::chrono::duration_cast<std::chrono::microseconds>("
          "std::chrono::steady_clock::now().time_since_epoch()).count(), trace_ep.c_str(), (int)msg_ptr->cmd, msg_ptr->fn ? \"fn\" : \"-\");\n"
          "        }\n"
          "        msg_ptr->completion.set_value();",
          "work completion")

open(path, "w", encoding="utf-8", newline="\n").write(src)
print("patched: GGML_RPC_TRACE begin/end per dispatcher message")
