"""Inspect AI model provider "lab": every model call goes through the foundry's own `infer_ollama._chat` (ollama's
NATIVE API, streamed, hang-proof), not Inspect's OpenAI-style ollama provider.

Why: through the OpenAI-style endpoint a
request's `num_ctx` is ignored (the model loads at the server default) and thinking cannot be switched off. The native
call passes both, plus every sampling option, and it is the call path the factory already guards.

Use: import this module (registering "lab"), then `--model lab/<ollama tag> -M num_ctx=<tested ctx> -M think=<on|off>`
(or `model_args={...}` in `eval()`). `num_ctx` and `think` are REQUIRED: a silent default would cap the context
() or switch thinking off unasked (`_chat` sends bool(think)). `host` defaults to this box's ollama."""
import asyncio
import itertools
import os
import sys

from inspect_ai.model import (ChatCompletionChoice, ChatMessageAssistant, ContentReasoning, ContentText,
                              GenerateConfig, ModelAPI, ModelOutput, ModelUsage, modelapi)
from inspect_ai.tool import ToolCall

# the factory's modules: FOUNDRY_BIN, else <FOUNDRY_ROOT>\bin (the factory hands FOUNDRY_ROOT to every order it runs)
FOUNDRY_BIN = os.environ.get("FOUNDRY_BIN") or (os.path.join(os.environ["FOUNDRY_ROOT"], "bin") if os.environ.get("FOUNDRY_ROOT") else "")


def _foundry():
    """infer_ollama from this GPU box's foundry bin (imported only by the foundry backend: Tolkien has none)."""
    if not FOUNDRY_BIN:
        raise RuntimeError("lab_provider: neither FOUNDRY_BIN nor FOUNDRY_ROOT is set, so this GPU box's foundry bin "
                           "is unknown (the factory hands FOUNDRY_ROOT to every order it runs)")
    if FOUNDRY_BIN not in sys.path:
        sys.path.append(FOUNDRY_BIN)
    import infer_ollama
    return infer_ollama


EFFORTS = ("low", "medium", "high", "xhigh")    # ollama think levels (infer_ollama.think_value maps them per model)


def think_arg(v, backend):
    """The `think` model arg: on/off for every backend; an effort level is passed through verbatim to the foundry's
    `_chat` (the GPU A1 plans send them - a bool would run every level as the renderer's default, the 10-07 effort
    sandbag) and to the openai backend's chat template. llama-server fixes reasoning at launch, so the tolkien
    backend takes on/off only."""
    if isinstance(v, str) and v.strip().lower() in EFFORTS:
        if backend not in ("foundry", "openai"):
            raise ValueError("an effort level (%s) is an ollama setting: the tolkien backend takes think on|off" % v)
        return v.strip().lower()
    return _flag(v)


def _flag(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("on", "true", "1", "yes"):
        return True
    if s in ("off", "false", "0", "no"):
        return False
    raise ValueError("think must be on/off (got %r)" % (v,))


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(getattr(p, "text", "") or "" for p in (content or []) if getattr(p, "type", "") == "text")


def to_ollama_messages(messages):
    """Inspect chat messages -> ollama /api/chat messages (assistant tool calls and tool results kept)."""
    out = []
    for m in messages:
        if m.role == "assistant":
            d = {"role": "assistant", "content": _text(m.content)}
            if m.tool_calls:
                d["tool_calls"] = [{"function": {"name": tc.function, "arguments": tc.arguments or {}}}
                                   for tc in m.tool_calls]
            out.append(d)
        elif m.role == "tool":
            text = _text(m.content)
            if m.error is not None:
                text = (text + "\n" if text else "") + "Error: %s" % getattr(m.error, "message", m.error)
            out.append({"role": "tool", "content": text, "tool_name": m.function or ""})
        else:
            out.append({"role": m.role, "content": _text(m.content)})
    return out


def to_openai_messages(messages):
    """Inspect chat messages -> OpenAI-style messages for llama-server (Tolkien backend): assistant tool calls carry
    ids and JSON-text arguments, tool results carry the id of the call they answer."""
    import json
    out = []
    for m in messages:
        if m.role == "assistant":
            d = {"role": "assistant", "content": _text(m.content)}
            if m.tool_calls:
                d["tool_calls"] = [{"id": tc.id, "type": "function",
                                    "function": {"name": tc.function, "arguments": json.dumps(tc.arguments or {})}}
                                   for tc in m.tool_calls]
            out.append(d)
        elif m.role == "tool":
            text = _text(m.content)
            if m.error is not None:
                text = (text + "\n" if text else "") + "Error: %s" % getattr(m.error, "message", m.error)
            out.append({"role": "tool", "tool_call_id": m.tool_call_id or "", "content": text})
        else:
            out.append({"role": m.role, "content": _text(m.content)})
    return out


def to_ollama_tools(tools):
    """Inspect ToolInfo -> the tool schema sent to the server, byte-equal to the harness's own converter
    (`a1_ollama.to_ollama_tools`, what the GPU path always sent): ToolParams' round trip adds
    `additionalProperties: false` and an empty `required`, which the direct path never sent."""
    out = []
    for t in tools or []:
        params = t.parameters.model_dump(exclude_none=True)
        if params.get("additionalProperties") is False:
            params.pop("additionalProperties")
        if params.get("required") == []:
            params.pop("required")
        out.append({"type": "function", "function": {"name": t.name, "description": t.description,
                                                      "parameters": params}})
    return out


def options_from(config, num_ctx):
    """GenerateConfig -> ollama options. Only what was SET is sent; num_ctx always (the tested context)."""
    opts = {"num_ctx": num_ctx}
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("top_k", "top_k"), ("seed", "seed"),
                     ("max_tokens", "num_predict"), ("presence_penalty", "presence_penalty"),
                     ("frequency_penalty", "frequency_penalty")):
        v = getattr(config, src, None)
        if v is not None:
            opts[dst] = v
    if config.stop_seqs:
        opts["stop"] = list(config.stop_seqs)
    return opts


_CALL_IDS = itertools.count(1)       # ollama tool calls carry no id; Inspect pairs results to calls by id

# Set by the Tolkien agentic payload around one item: the order's stop slip (`stop_event.is_set()`) and its beat
# (`live(answer, thinking, n_chunks, elapsed_s)`). Model args cannot carry them: Inspect writes those into the log.
TOLKIEN_HOOKS = {"stop_event": None, "live": None}


class Stopped(RuntimeError):
    """The order's stop slip fired: no further model call is made for this item."""


def to_model_output(model, r):
    msg = r.get("message") or {}
    content = []
    if msg.get("thinking"):
        content.append(ContentReasoning(reasoning=msg["thinking"]))
    content.append(ContentText(text=msg.get("content") or ""))
    turn = next(_CALL_IDS)
    calls, invalid = [], []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        args = fn.get("arguments") or {}
        if not isinstance(args, dict):           # tolkien_llm passes unparseable arguments through as text; ToolCall
            invalid.append("%s: %s" % (fn.get("name", ""), str(args)[:200]))   # would raise, so it goes back to
            continue                                                             # the model as a parse error
        calls.append(ToolCall(id="call_%d_%d" % (turn, i), function=fn.get("name", ""), arguments=args))
    # "loop" (the lab's looping() rule) is void like "length": both are counted as a context-cap hit (13a)
    stop = "tool_calls" if calls else ("max_tokens" if r.get("done_reason") in ("length", "loop") else "stop")
    ins, outs = r.get("prompt_eval_count") or 0, r.get("eval_count") or 0
    # llama-server's prompt_n counts only the tokens it read this call; the reused prefix is cache_n. Compaction sizes
    # the conversation from input + cache_read, so both are carried.
    cached = r.get("cache_n") or None
    return ModelOutput(model=model, choices=[ChatCompletionChoice(
        message=ChatMessageAssistant(content=content, tool_calls=calls or None, model=model, source="generate"),
        stop_reason=stop)], usage=ModelUsage(input_tokens=ins, output_tokens=outs, total_tokens=ins + outs,
                                             input_tokens_cache_read=cached),
        metadata={"done_reason": r.get("done_reason"), "invalid_tool_call": "; ".join(invalid) or None,
                  # the server's own counters, for the order's per-turn record (a1_order's tok_s / load / read time)
                  "server": {k: r.get(k) for k in ("prompt_eval_count", "eval_count", "eval_duration",
                                                   "load_duration", "prompt_eval_duration", "cache_n")},
                  "thinking_chars": len(msg.get("thinking") or ""),
                  "load_s": round(r["load_duration"] / 1e9, 2) if r.get("load_duration") else None,
                  "prompt_eval_s": round(r["prompt_eval_duration"] / 1e9, 2) if r.get("prompt_eval_duration") else None})


@modelapi(name="lab")
class LabOllama(ModelAPI):
    def __init__(self, model_name, base_url=None, api_key=None, config=GenerateConfig(), **model_args):
        super().__init__(model_name=model_name, base_url=base_url, api_key=api_key, config=config)
        missing = [k for k in ("num_ctx", "think") if k not in model_args]
        if missing:
            raise ValueError("lab provider needs %s (no silent defaults: a default caps the context or switches "
                             "thinking off)" % " and ".join("-M %s=..." % k for k in missing))
        self.num_ctx = int(model_args["num_ctx"])
        self.host = base_url or model_args.get("host") or "http://127.0.0.1:11434"
        self.backend = model_args.get("backend", "foundry")
        if self.backend not in ("foundry", "tolkien", "openai"):
            raise ValueError("backend must be foundry, tolkien or openai (got %r)" % (self.backend,))
        if self.backend == "openai" and not (base_url or model_args.get("host")):
            raise ValueError("the openai backend needs host= (the server's base URL): no default server")
        self.think = think_arg(model_args["think"], self.backend)
        # the plan's own ollama options (best settings: sampling the GenerateConfig cannot name), sent as given; what a
        # GenerateConfig sets on a call (e.g. the turn cap's max_tokens) is laid over them, num_ctx is the required arg
        self.options = dict(model_args.get("options") or {})
        if "num_ctx" in self.options:
            raise ValueError("num_ctx is the provider's required arg, not an option")
        if self.options and self.backend == "tolkien":
            raise ValueError("options are ollama's (foundry backend); the tolkien backend takes sampling from the "
                             "GenerateConfig")

    async def generate(self, input, tools, tool_choice, config):
        if self.backend == "tolkien":
            stop = TOLKIEN_HOOKS["stop_event"]
            if stop is not None and stop.is_set():
                raise Stopped("stop slip")
            # Inspect memoizes a model instance across evals and the rigby lane outlives every order, so the server
            # is checked per call, not at construction: the order launched it with -c from the SAME plan.
            tv = _tolkien()
            served = await asyncio.to_thread(_served_ctx, tv.SERVER_PORT)
            if served != self.num_ctx:
                raise ValueError("llama-server serves n_ctx %s, the plan says %s - refusing to measure a different "
                                 "context" % (served, self.num_ctx))
            opts = {k: v for k, v in options_from(config, self.num_ctx).items() if k != "num_ctx"}
            r = await asyncio.to_thread(tv.chat, to_openai_messages(input), to_ollama_tools(tools) or None, opts,
                                        stop_event=stop, live=TOLKIEN_HOOKS["live"])
        elif self.backend == "openai":
            # an OpenAI-compatible server on a GPU box: its context is checked per call against
            # the plan, as the tolkien backend checks llama-server's; the plan's sampling options lay under the call's
            served = await asyncio.to_thread(_served_ctx_at, self.host)
            if served != self.num_ctx:
                raise ValueError("the server serves n_ctx %s, the plan says %s - refusing to measure a different "
                                 "context" % (served, self.num_ctx))
            opts = dict(self.options)
            opts.update({k: v for k, v in options_from(config, self.num_ctx).items() if k != "num_ctx"})
            r = await asyncio.to_thread(openai_chat, self.host, to_openai_messages(input),
                                        to_ollama_tools(tools) or None, opts, self.think)
        else:
            opts = dict(self.options)
            opts.update(options_from(config, self.num_ctx))
            r = await asyncio.to_thread(_foundry()._chat, self.host, self.model_name, to_ollama_messages(input),
                                        opts, think=self.think, tools=to_ollama_tools(tools) or None)
        return to_model_output(self.model_name, r)


def _tolkien():
    """tolkien_llm from Tolkien's app dir (the container's /app, or TOLKIEN_APP)."""
    app = os.environ.get("TOLKIEN_APP", "/app")
    if app not in sys.path:
        sys.path.append(app)
    import tolkien_llm
    return tolkien_llm


def _served_ctx(port):
    """The running llama-server's context, from its /props (default_generation_settings.n_ctx, or n_ctx)."""
    return _served_ctx_at("http://127.0.0.1:%d" % port)


def _served_ctx_at(base):
    """A server's context from its /props at a base URL (llama-server and Strata both answer it)."""
    import json
    import urllib.request
    p = json.load(urllib.request.urlopen(base.rstrip("/") + "/props", timeout=30))
    return (p.get("default_generation_settings") or {}).get("n_ctx") or p.get("n_ctx")


# ── the openai backend: an OpenAI-compatible server on a GPU box ─────────────────────────────────
# The same contract as tolkien_llm.chat (the rigby path) and infer_ollama._chat (the ollama path): ONE ollama-shaped
# result, so to_model_output, the compaction's calibration (prompt_n + cache_n via cache_read) and the order's turn
# records read it unchanged. The wire itself - the request, the SSE parser, the loop rule, the silence timeout and
# Strata's over-context refusal translated to exceed_context_size (so a1_chat.is_context_full forces a compaction) -
# lives ONCE in the foundry's infer_ollama (_chat_openai), where the menu's Strata orders use it too. The openai backend runs only on a GPU box, which always has that foundry.


def openai_chat(base, messages, tools, options, think):
    """One streamed chat turn on an OpenAI-compatible server at `base`, through the foundry's one openai wire (no
    max_tokens unless the order's turn cap sets num_predict; no request clock - only IDLE_S of silence ends it)."""
    return _foundry()._chat_openai(base, messages, options, think=think, tools=tools)
