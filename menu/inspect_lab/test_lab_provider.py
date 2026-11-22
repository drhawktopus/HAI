"""Offline pins for the "lab" Inspect provider (no ollama): message/tool conversion, options, refusal, output mapping.
The live acceptance check is probe_lab.py (context, thinking off/on, native tools, measured on a box)."""
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FOUNDRY_BIN", r"E:\SSDShared\mothership-build")
import lab_provider as L  # noqa: E402
from inspect_ai.model import (ChatMessageAssistant, ChatMessageSystem, ChatMessageTool, ChatMessageUser,  # noqa: E402
                              GenerateConfig)
from inspect_ai.tool import ToolCall, ToolInfo, ToolParams  # noqa: E402


class OpenAIBackend(unittest.TestCase):
    """Strata (Flash-Next) behind the A1 harness. the wire itself (request, SSE parser,
    loop rule, Strata's over-context translation) lives ONCE in the foundry's infer_ollama._chat_openai - its parser
    tests are in the foundry suite (InferOpenAIWire). These pin lab_provider's side of the seam, and run anywhere
    (Tolkien's gate has no foundry): the call reaches that one wire, its ollama-shaped result reads as Inspect output,
    and the over-context error text it raises is the compaction signal."""

    def test_openai_chat_rides_the_foundrys_one_wire(self):
        import lab_provider as P
        seen = []
        fake = unittest.mock.Mock()
        fake._chat_openai = lambda base, messages, options, think=None, tools=None: (
            seen.append((base, messages, options, think, tools)) or {"message": {"content": "ok"}})
        msgs, tools, opts = [{"role": "user", "content": "x"}], [{"type": "function"}], {"temperature": 0.6}
        with unittest.mock.patch.object(P, "_foundry", return_value=fake):
            r = P.openai_chat("http://127.0.0.1:8080", msgs, tools, opts, "xhigh")
        self.assertEqual(r["message"]["content"], "ok")
        self.assertEqual(seen, [("http://127.0.0.1:8080", msgs, opts, "xhigh", tools)])

    def test_an_openai_result_reads_as_inspect_output(self):
        import lab_provider as P
        r = {"message": {"role": "assistant", "content": "ok", "thinking": "plan ",
                         "tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "a.py"}}}]},
             "prompt_eval_count": 100, "cache_n": 800, "eval_count": 40, "prompt_eval_duration": 50_000_000,
             "eval_duration": 600_000_000, "done_reason": "tool_calls"}
        out = P.to_model_output("m", r)                   # the compaction reads input + cache_read = the whole prompt
        self.assertEqual((out.usage.input_tokens, out.usage.input_tokens_cache_read), (100, 800))
        self.assertEqual(out.choices[0].stop_reason, "tool_calls")
        self.assertEqual([c.function for c in out.choices[0].message.tool_calls], ["write_file"])

    def test_effort_levels_reach_the_openai_backend_verbatim(self):
        import lab_provider as P
        self.assertEqual(P.think_arg("xhigh", "openai"), "xhigh")

    def test_the_wires_over_context_text_is_the_compaction_signal(self):
        """The text infer_ollama._chat_openai raises for Strata's own refusal (the foundry suite pins that it adds the
        tag); this side pins that a1_chat reads that text as context-full, so the two halves agree."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "a1"))
        import a1_chat
        e = RuntimeError('openai server HTTP 400 exceed_context_size: {"error": {"message": "prompt (130000 tokens) '
                         '+ max tokens (20480) exceeds the context (131072); requests are never truncated"}}')
        self.assertTrue(a1_chat.is_context_full(e), str(e))

    def test_the_openai_backend_needs_a_host(self):
        import lab_provider as P
        with self.assertRaises(ValueError):
            P.LabOllama("m", num_ctx=131072, think="on", backend="openai")


class Conversion(unittest.TestCase):
    def test_messages_keep_tool_calls_and_tool_results(self):
        msgs = [ChatMessageSystem(content="sys"), ChatMessageUser(content="go"),
                ChatMessageAssistant(content="", tool_calls=[ToolCall(id="c1", function="add",
                                                                      arguments={"a": 2, "b": 3})]),
                ChatMessageTool(content="1005", tool_call_id="c1", function="add")]
        out = L.to_ollama_messages(msgs)
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool"])
        self.assertEqual(out[2]["tool_calls"], [{"function": {"name": "add", "arguments": {"a": 2, "b": 3}}}])
        self.assertEqual((out[3]["content"], out[3]["tool_name"]), ("1005", "add"))

    def test_tools_become_ollama_function_schemas(self):
        t = ToolInfo(name="add", description="Add.", parameters=ToolParams(
            properties={"a": {"type": "integer"}}, required=["a"]))
        self.assertEqual(L.to_ollama_tools([t])[0]["function"]["parameters"]["required"], ["a"])

    def test_effort_levels_reach_the_foundry_verbatim_and_tolkien_refuses_them(self):
        """the GPU A1 calls moved onto this provider: a bool would run every effort level as the renderer's
        default; llama-server fixes reasoning at launch, so tolkien takes on/off only."""
        self.assertEqual([L.think_arg(v, "foundry") for v in ("xhigh", "HIGH", "low", "on", False)],
                         ["xhigh", "high", "low", True, False])
        with self.assertRaises(ValueError):
            L.think_arg("xhigh", "tolkien")
        self.assertIs(L.think_arg("off", "tolkien"), False)

    def test_the_plans_own_options_are_sent_and_a_call_config_lays_over_them(self):
        import asyncio
        sent = {}

        def chat(host, model, messages, options, think=False, tools=None):
            sent.update(options=dict(options), think=think)
            return {"message": {"content": "ok"}, "done_reason": "stop"}
        api = L.LabOllama("m", num_ctx=8192, think="xhigh", options={"temperature": 0.7, "min_p": 0.0,
                                                                         "repeat_penalty": 1.0})
        with unittest.mock.patch.object(L, "_foundry", return_value=type("IO", (), {"_chat": staticmethod(chat)})):
            asyncio.run(api.generate([ChatMessageUser(content="hi")], [], "auto", GenerateConfig(max_tokens=2048)))
        self.assertEqual(sent["options"], {"temperature": 0.7, "min_p": 0.0, "repeat_penalty": 1.0,
                                           "num_ctx": 8192, "num_predict": 2048})
        self.assertEqual(sent["think"], "xhigh")
        with self.assertRaises(ValueError):
            L.LabOllama("m", num_ctx=8192, think="on", options={"num_ctx": 4096})
        with self.assertRaises(ValueError):
            L.LabOllama("m", num_ctx=8192, think="on", backend="tolkien", options={"temperature": 0.7})

    def test_tool_schema_is_the_harness_converters_byte_for_byte(self):
        """ToolParams' round trip adds additionalProperties:false and an empty required; neither was ever sent."""
        t = ToolInfo(name="ls", description="List.", parameters=ToolParams(properties={}, required=[]))
        self.assertEqual(L.to_ollama_tools([t])[0]["function"]["parameters"], {"type": "object", "properties": {}})

    def test_only_set_options_are_sent_and_num_ctx_always(self):
        self.assertEqual(L.options_from(GenerateConfig(), 17408), {"num_ctx": 17408})
        self.assertEqual(L.options_from(GenerateConfig(temperature=0.6, max_tokens=50), 4096),
                         {"num_ctx": 4096, "temperature": 0.6, "num_predict": 50})

    def test_output_maps_thinking_tool_calls_usage_and_length_stop(self):
        r = {"message": {"content": "x", "thinking": "hmm", "tool_calls": [
            {"function": {"name": "add", "arguments": {"a": 1}}}]}, "prompt_eval_count": 10, "eval_count": 5,
             "done_reason": "stop"}
        o = L.to_model_output("m", r)
        msg = o.choices[0].message
        self.assertEqual([p.type for p in msg.content], ["reasoning", "text"])
        self.assertEqual(msg.tool_calls[0].function, "add")
        self.assertEqual(o.choices[0].stop_reason, "tool_calls")
        self.assertEqual((o.usage.input_tokens, o.usage.output_tokens), (10, 5))
        capped = L.to_model_output("m", {"message": {"content": "x"}, "done_reason": "length"})
        self.assertEqual(capped.choices[0].stop_reason, "max_tokens", "a capped answer must read as capped (13a)")

    def test_openai_messages_carry_call_ids_and_json_arguments_for_llama_server(self):
        msgs = [ChatMessageAssistant(content="", tool_calls=[ToolCall(id="c1", function="add", arguments={"a": 2})]),
                ChatMessageTool(content="1002", tool_call_id="c1", function="add")]
        out = L.to_openai_messages(msgs)
        self.assertEqual(out[0]["tool_calls"][0], {"id": "c1", "type": "function",
                                                   "function": {"name": "add", "arguments": "{\"a\": 2}"}})
        self.assertEqual(out[1], {"role": "tool", "tool_call_id": "c1", "content": "1002"})

    def test_tolkien_backend_refuses_a_server_with_another_context_on_every_call(self):
        """Checked per call: Inspect memoizes the instance and the rigby lane outlives orders, so a server relaunched
        with another context must be refused by the SAME instance that accepted the old one."""
        import asyncio
        from unittest import mock
        fake = mock.Mock(SERVER_PORT=8091)
        fake.chat.return_value = {"message": {"content": "ok"}, "eval_count": 1, "prompt_eval_count": 2}
        served = [8192]
        api = L.LabOllama("rigby-model", num_ctx=8192, think="off", backend="tolkien")

        def ask():
            return asyncio.run(api.generate([ChatMessageUser(content="hi")], [], "auto", GenerateConfig()))
        with mock.patch.object(L, "_tolkien", lambda: fake), mock.patch.object(L, "_served_ctx", lambda p: served[0]):
            self.assertEqual(ask().choices[0].message.text, "ok")
            served[0] = 16384
            with self.assertRaisesRegex(ValueError, "n_ctx 16384"):
                ask()
        self.assertEqual(fake.chat.call_count, 1, "no model call against the mismatched server")

    def test_tolkien_backend_routes_through_tolkien_chat_without_num_ctx(self):
        import asyncio
        from unittest import mock
        fake = mock.Mock(SERVER_PORT=8091)
        fake.chat.return_value = {"message": {"content": "ok"}, "eval_count": 1, "prompt_eval_count": 2}
        with mock.patch.object(L, "_tolkien", lambda: fake), mock.patch.object(L, "_served_ctx", lambda p: 8192):
            api = L.LabOllama("rigby-model", num_ctx=8192, think="off", backend="tolkien")
            out = asyncio.run(api.generate([ChatMessageUser(content="hi")], [], "auto",
                                           GenerateConfig(temperature=0.6)))
        args = fake.chat.call_args[0]
        self.assertEqual(args[2], {"temperature": 0.6}, "context is the server's launch setting, never per request")
        self.assertEqual(out.choices[0].message.text, "ok")

    def test_a_looped_or_context_filling_turn_is_a_counted_cap_hit(self):
        for reason in ("length", "loop"):
            out = L.to_model_output("m", {"message": {"content": "x"}, "done_reason": reason})
            self.assertEqual(out.choices[0].stop_reason, "max_tokens", reason)
        self.assertEqual(L.to_model_output("m", {"message": {"content": "x"}, "done_reason": "stop"})
                         .choices[0].stop_reason, "stop")

    def test_no_silent_defaults(self):
        with self.assertRaises(ValueError):
            L.LabOllama("m", think="off")
        with self.assertRaises(ValueError):
            L.LabOllama("m", num_ctx=4096)
        with self.assertRaises(ValueError):
            L._flag("maybe")


if __name__ == "__main__":
    unittest.main()
