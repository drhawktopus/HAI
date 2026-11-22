"""#36 lab patch 4 for llama.cpp ggml-rpc (async-RPC 23b0202, AFTER the cpy_tensor_async, trace and input-set
patches): with GGML_RPC_TRACE=1, every place the HOST thread can block on an RPC server logs how long it waited,
when that is 1 ms or more:

    RPCTRACE <us> <endpoint> hostwait <send|sync|event> <waited us>

The three are every host-side wait the client has (a synchronous send, synchronize(), event_synchronize()); the
scheduler's and llama's drains all reach the server through one of them. Off unless GGML_RPC_TRACE is set.

Usage (on rigby, in the worktree):  python3 rpc_hostwait_trace_patch.py ggml/src/ggml-rpc/ggml-rpc.cpp
Every anchor must match its stated count or NOTHING is written.
"""
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()
if "hostwait" in src:
    sys.exit("already patched")
if "GGML_RPC_TRACE" not in src or "#36 patch 3" not in src:
    sys.exit("ABORT: apply the trace patch and patch 3 first - nothing written")


def sub(text, anchor, new, label, count=1):
    n = text.count(anchor)
    if n != count:
        sys.exit("ABORT: anchor '%s' found %d times (want %d) - nothing written" % (label, n, count))
    return text.replace(anchor, new)


src = sub(src,
          "    std::string      trace_ep;\n",
          "    std::string      trace_ep;\n"
          "    void trace_wait(const char * what, std::chrono::steady_clock::time_point t0) {\n"
          "        if (!trace) {\n"
          "            return;\n"
          "        }\n"
          "        auto now = std::chrono::steady_clock::now();\n"
          "        long long us = (long long)std::chrono::duration_cast<std::chrono::microseconds>(now - t0).count();\n"
          "        if (us >= 1000) {\n"
          "            fprintf(stderr, \"RPCTRACE %lld %s hostwait %s %lld\\n\", (long long)std::chrono::duration_cast<"
          "std::chrono::microseconds>(now.time_since_epoch()).count(), trace_ep.c_str(), what, us);\n"
          "        }\n"
          "    }\n",
          "trace_ep member")

src = sub(src,
          "    GGML_ASSERT(queue.push(msg));\n    auto future = msg->completion.get_future();\n    future.wait();\n}",
          "    GGML_ASSERT(queue.push(msg));\n    auto future = msg->completion.get_future();\n"
          "    auto t0 = std::chrono::steady_clock::now();\n    future.wait();\n    trace_wait(\"send\", t0);\n}",
          "sync send x2", count=2)

src = sub(src,
          "    GGML_ASSERT(queue.push(msg));\n    msg->completion.get_future().wait();\n}",
          "    GGML_ASSERT(queue.push(msg));\n    auto t0 = std::chrono::steady_clock::now();\n"
          "    msg->completion.get_future().wait();\n    trace_wait(\"sync\", t0);\n}",
          "synchronize")

src = sub(src,
          "    rpc_event * ev = (rpc_event *)event->context;\n    ev->sf.wait();\n}",
          "    rpc_event * ev = (rpc_event *)event->context;\n    auto t0 = std::chrono::steady_clock::now();\n"
          "    ev->sf.wait();\n    trace_wait(\"event\", t0);\n}",
          "event_synchronize")

open(path, "w", encoding="utf-8", newline="\n").write(src)
print("patched: host waits >= 1 ms traced (send x2, sync, event)")
