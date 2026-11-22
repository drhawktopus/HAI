"""Acceptance tests for `a1_ollama.make_chat_fn` (spec #34 A1, step 2).

A fake ollama is served by `http.server` on 127.0.0.1 in a thread — no real
ollama, no network beyond loopback.  The 6 criteria map to the 6 test
methods.  Standard library only.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

# make the workspace root importable regardless of discovery top-level dir
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import a1_harness  # noqa: E402
import a1_ollama   # noqa: E402


# ---------------------------------------------------------------------------
# fake ollama
# ---------------------------------------------------------------------------

class FakeOllama:
    """Stateful fake: a scripted list of response payloads (or Exception to
    raise).  Records every request body it receives."""

    def __init__(self, responses: List[Any]):
        self.responses = list(responses)
        self.requests: List[Dict[str, Any]] = []

    def next(self) -> Any:
        if not self.responses:
            raise AssertionError("fake ollama scripted out of responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _Handler(BaseHTTPRequestHandler):
    fake: FakeOllama  # set by the server factory

    def log_message(self, *args, **kwargs):  # silence
        pass

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {"__unparsed__": raw}
        self.fake.requests.append(body)

        item = self.fake.next()
        payload = json.dumps(item).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class FakeOllamaServer:
    def __init__(self, fake: FakeOllama):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        _Handler.fake = fake
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.port = server.server_address[1]
        self.base_url = "http://127.0.0.1:%d" % self.port

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def ollama_response(content: str = "",
                    tool_calls: Optional[List[Dict[str, Any]]] = None,
                    thinking: Optional[str] = None,
                    **stats: Any) -> Dict[str, Any]:
    """Build a fake ollama /api/chat response body."""
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if thinking is not None:
        message["thinking"] = thinking
    resp: Dict[str, Any] = {"model": "fake", "message": message}
    resp.update(stats)
    return resp


# ---------------------------------------------------------------------------
# synthetic exercise (adder) — self-contained copy so this file never depends
# on test_a1_harness internals
# ---------------------------------------------------------------------------

ADD_INSTRUCTIONS = (
    "# Adder\n\n"
    "Implement `add(a, b)` so that it returns the sum of its two integer arguments.\n"
    "It must also work when either argument is a float.\n"
)
ADD_STUB = "def add(a, b):\n    pass\n"
ADD_TEST = (
    "import unittest\n\n"
    "from adder import add\n\n"
    "class AdderTests(unittest.TestCase):\n"
    "    def test_add_ints(self):\n"
    "        self.assertEqual(add(2, 3), 5)\n\n"
    "    def test_add_floats(self):\n"
    "        self.assertAlmostEqual(add(1.5, 2.25), 3.75)\n\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)
ADD_EXAMPLE = "def add(a, b):\n    return a + b\n"


def make_adder_exercise(base: str) -> str:
    ex = os.path.join(base, "adder")
    os.makedirs(os.path.join(ex, ".docs"), exist_ok=True)
    os.makedirs(os.path.join(ex, ".meta"), exist_ok=True)
    with open(os.path.join(ex, ".docs", "instructions.md"), "w", encoding="utf-8") as f:
        f.write(ADD_INSTRUCTIONS)
    with open(os.path.join(ex, "adder.py"), "w", encoding="utf-8") as f:
        f.write(ADD_STUB)
    with open(os.path.join(ex, "adder_test.py"), "w", encoding="utf-8") as f:
        f.write(ADD_TEST)
    with open(os.path.join(ex, ".meta", "example.py"), "w", encoding="utf-8") as f:
        f.write(ADD_EXAMPLE)
    return ex


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class A1OllamaTests(unittest.TestCase):
    def _exercise(self) -> str:
        self._base = tempfile.mkdtemp(prefix="a1_ollama_")
        self.addCleanup(shutil.rmtree, self._base, ignore_errors=True)
        return make_adder_exercise(self._base)

    def _server(self, fake: FakeOllama) -> FakeOllamaServer:
        srv = FakeOllamaServer(fake)
        srv.start()
        self.addCleanup(srv.stop)
        return srv

    def _check_harness_tools(self, body: Dict[str, Any]) -> None:
        """The request body must carry the harness's tool names, converted to
        ollama's function schema (name, description, JSON-schema parameters)."""
        tools = body.get("tools")
        self.assertIsInstance(tools, list)
        names = {t.get("function", {}).get("name") for t in tools}
        self.assertEqual(names, {"list_files", "read_file", "write_file",
                                 "run_tests", "finish"})
        for t in tools:
            self.assertEqual(t.get("type"), "function")
            fn = t.get("function")
            self.assertIn("description", fn)
            params = fn.get("parameters")
            self.assertIsInstance(params, dict)
            self.assertEqual(params.get("type"), "object")
            self.assertIn("properties", params)
        # spot-check write_file's parameters
        wf = next(t for t in tools if t["function"]["name"] == "write_file")
        props = wf["function"]["parameters"]["properties"]
        self.assertIn("path", props)
        self.assertIn("content", props)
        for p in props.values():
            self.assertIn("type", p)

    # -- criterion 1 ---------------------------------------------------------
    def test_c1_success_passes_and_request_body_shape(self):
        ex = self._exercise()
        with open(os.path.join(ex, ".meta", "example.py"), "r", encoding="utf-8") as f:
            solution = f.read()

        fake = FakeOllama([
            ollama_response(content="", tool_calls=[
                {"function": {"name": "write_file",
                              "arguments": {"path": "adder.py", "content": solution}}}
            ], **{"prompt_eval_count": 11, "eval_count": 5, "eval_duration": 0.05,
                  "prompt_eval_duration": 0.01, "done_reason": "stop"}),
            ollama_response(content="", tool_calls=[
                {"function": {"name": "finish", "arguments": {"note": "done"}}}
            ]),
        ])
        srv = self._server(fake)

        logs: List[Dict[str, Any]] = []
        chat_fn = a1_ollama.make_chat_fn(srv.base_url, "qwen2.5-coder:7b",
                                         think=True, log=lambda s: logs.append(s))
        result = a1_harness.run_task(ex, chat_fn, mode="agentic")

        self.assertTrue(result["passed"], msg=result)
        self.assertEqual(result["ended"], "finish")
        self.assertEqual(len(fake.requests), 2)

        for body in fake.requests:
            self.assertEqual(body.get("model"), "qwen2.5-coder:7b")
            self.assertFalse(body.get("stream"))
            self.assertTrue(body.get("think"))
            self.assertIsInstance(body.get("messages"), list)
            self._check_harness_tools(body)

        # log fired once per request, with the pinned fields
        self.assertEqual(len(logs), 2)
        for entry in logs:
            for key in ("prompt_eval_count", "eval_count", "eval_duration",
                        "prompt_eval_duration", "done_reason", "thinking_chars",
                        "tool_calls"):
                self.assertIn(key, entry)
        self.assertEqual(logs[0]["done_reason"], "stop")
        self.assertEqual(logs[0]["tool_calls"], 1)

    # -- criterion 2 ---------------------------------------------------------
    def test_c2_json_string_arguments_parsed_to_dict(self):
        fake = FakeOllama([
            ollama_response(content="", tool_calls=[
                {"function": {"name": "write_file",
                              "arguments": json.dumps({"path": "adder.py",
                                                       "content": ADD_EXAMPLE})}}
            ]),
            ollama_response(content="", tool_calls=[
                {"function": {"name": "finish", "arguments": json.dumps({"note": "n"})}}
            ]),
        ])
        srv = self._server(fake)
        chat_fn = a1_ollama.make_chat_fn(srv.base_url, "fake-model")

        out = chat_fn([{"role": "user", "content": "hi"}], a1_harness._TOOLS)
        self.assertEqual(out["content"], "")
        self.assertEqual(out["tool_calls"][0]["name"], "write_file")
        self.assertIsInstance(out["tool_calls"][0]["arguments"], dict)
        self.assertEqual(out["tool_calls"][0]["arguments"]["path"], "adder.py")
        self.assertEqual(out["tool_calls"][0]["arguments"]["content"], ADD_EXAMPLE)

        out2 = chat_fn([{"role": "user", "content": "hi"}], [])
        self.assertEqual(out2["tool_calls"][0]["arguments"], {"note": "n"})

    # -- criterion 3 ---------------------------------------------------------
    def test_c3_thinking_not_used_as_content(self):
        fake = FakeOllama([
            ollama_response(content="", thinking="Let me think this through carefully. " * 20),
        ])
        srv = self._server(fake)
        logs: List[Dict[str, Any]] = []
        chat_fn = a1_ollama.make_chat_fn(srv.base_url, "fake-model",
                                         think=True, log=lambda s: logs.append(s))

        out = chat_fn([{"role": "user", "content": "hi"}], [])
        self.assertEqual(out["content"], "")
        self.assertEqual(out["tool_calls"], [])
        self.assertEqual(len(logs), 1)
        self.assertGreater(logs[0]["thinking_chars"], 0)

    # -- criterion 4 ---------------------------------------------------------
    def test_c4_num_ctx_and_options_reach_body(self):
        # (a) options + num_ctx merge into body["options"]
        fake = FakeOllama([ollama_response(content="ok")])
        srv = self._server(fake)
        chat_fn = a1_ollama.make_chat_fn(
            srv.base_url, "fake-model",
            options={"temperature": 0.2, "num_predict": 64}, num_ctx=8192)
        chat_fn([{"role": "user", "content": "hi"}], [])
        body = fake.requests[0]
        self.assertEqual(body.get("options"),
                         {"temperature": 0.2, "num_predict": 64, "num_ctx": 8192})

        # (b) options only
        fake = FakeOllama([ollama_response(content="ok")])
        srv = self._server(fake)
        chat_fn = a1_ollama.make_chat_fn(srv.base_url, "fake-model",
                                         options={"temperature": 0.1})
        chat_fn([{"role": "user", "content": "hi"}], [])
        self.assertEqual(fake.requests[0].get("options"), {"temperature": 0.1})

        # (c) num_ctx only
        fake = FakeOllama([ollama_response(content="ok")])
        srv = self._server(fake)
        chat_fn = a1_ollama.make_chat_fn(srv.base_url, "fake-model", num_ctx=4096)
        chat_fn([{"role": "user", "content": "hi"}], [])
        self.assertEqual(fake.requests[0].get("options"), {"num_ctx": 4096})

        # (d) neither given -> no options key required (absent is the contract)
        fake = FakeOllama([ollama_response(content="ok")])
        srv = self._server(fake)
        chat_fn = a1_ollama.make_chat_fn(srv.base_url, "fake-model")
        chat_fn([{"role": "user", "content": "hi"}], [])
        self.assertNotIn("options", fake.requests[0])

    # -- criterion 5 ---------------------------------------------------------
    def test_c5_done_reason_length_visible_in_log(self):
        fake = FakeOllama([
            ollama_response(content="truncated answer",
                            **{"prompt_eval_count": 3, "eval_count": 2,
                               "eval_duration": 0.02, "prompt_eval_duration": 0.001,
                               "done_reason": "length"}),
        ])
        srv = self._server(fake)
        logs: List[Dict[str, Any]] = []
        chat_fn = a1_ollama.make_chat_fn(srv.base_url, "fake-model",
                                         log=lambda s: logs.append(s))
        out = chat_fn([{"role": "user", "content": "hi"}], [])
        self.assertEqual(out["content"], "truncated answer")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["done_reason"], "length")

    # -- criterion 6 ---------------------------------------------------------
    def test_c6_http_500_raises_and_run_task_ends_error(self):
        class _H500(BaseHTTPRequestHandler):
            def log_message(self, *args, **kwargs):
                pass

            def do_POST(self):
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"internal error")

        server = ThreadingHTTPServer(("127.0.0.1", 0), _H500)
        self.addCleanup(server.server_close)     # cleanups run LIFO: shutdown() stops serve_forever before the close
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        base_url = "http://127.0.0.1:%d" % server.server_address[1]

        chat_fn = a1_ollama.make_chat_fn(base_url, "fake-model")

        # (a) the call raises
        with self.assertRaises(Exception):
            chat_fn([{"role": "user", "content": "hi"}], [])

        # (b) run_task reports ended: "error"
        ex = self._exercise()
        result = a1_harness.run_task(ex, chat_fn, mode="agentic")
        self.assertEqual(result["ended"], "error")
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
