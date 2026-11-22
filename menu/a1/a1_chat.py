"""The A1 lead's model call, ONE derivation for all four factories. Every call goes through Inspect's model (the `lab` provider: rigby's llama-server via the
tolkien backend, the GPU boxes' ollama via the foundry backend) with Inspect's own conversation compaction, the
runaway guard's turn cap and the context-full backstop. `a1_task` (rigby's Inspect solver) and `a1_order` (the GPU
boxes' compute order) both build a `CompactingChat`; the harness loop (`a1_harness`) is unchanged and shared.

Needs Inspect AI: rigby's Tolkien image carries it; the GPU boxes run A1 orders under `{FOUNDRY_ROOT}\\venv-inspect`
(`setup_inspect_venv.py`, the same pins)."""
import asyncio
import os
import sys
import threading

from inspect_ai.model import (ChatMessageAssistant, ChatMessageSystem, ChatMessageTool, ChatMessageUser,
                              CompactionSummary, GenerateConfig, compaction)
from inspect_ai.tool import ToolCall, ToolInfo, ToolParams

# Inspect's compaction estimates tokens with tiktoken's o200k_base before the first reply calibrates it (and for the
# messages added since). The GPU venv caches that encoding beside itself (setup_inspect_venv.py).
_TK = os.path.join(sys.prefix, "tiktoken-cache")
if os.path.isdir(_TK):
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", _TK)

# CONTEXT COMPACTION: Inspect's own agent
# compaction (the summary strategy, its default prompt), wired as its react agent wires it - compact_input before each
# call, record_output after, a forced compaction on overflow. Instructions: Inspect's documented example.
COMPACT_INSTRUCTIONS = "Focus on preserving code snippets, variable names, and technical decisions."
COMPACT_DEFAULT_FRACTION = 0.9           # Inspect's own default threshold


def compact_threshold(model, turn_cap):
    """Tokens at which the conversation is compacted: room must stay for one whole reply - the order's turn cap when
    it has one (rigby: 65,536 - 20,480), else Inspect's default 90 % of the served context."""
    ctx = int(getattr(getattr(model, "api", None), "num_ctx", 0) or 0)
    if not ctx:                          # not the lab provider: Inspect resolves the fraction against the model's window
        return COMPACT_DEFAULT_FRACTION
    if turn_cap:
        if turn_cap >= ctx:
            raise ValueError("turn_cap %d leaves no room in a %d context" % (turn_cap, ctx))
        return ctx - int(turn_cap)
    return int(COMPACT_DEFAULT_FRACTION * ctx)


def is_context_full(err):
    """The server refused the request because the conversation no longer fits the served context (llama-server HTTP
    400 exceed_context_size_error): graded as it stands - not an engine fault."""
    return "exceed_context_size" in str(err)


def is_runaway(output, turn_cap):
    """A reply cut at the order's per-call turn cap without a tool call (a1_harness RUNAWAY GUARD). A cut below the cap
    is the context's, not the guard's."""
    used = output.usage.output_tokens if output.usage else 0
    return bool(turn_cap) and output.stop_reason == "max_tokens" and not output.message.tool_calls \
        and used >= turn_cap


def to_inspect(messages):
    """The harness's message list (ollama-style dicts) -> Inspect chat messages."""
    out, last_ids = [], []
    for m in messages:
        role, content = m.get("role"), m.get("content") or ""
        if role == "system":
            out.append(ChatMessageSystem(content=content))
        elif role == "user":
            out.append(ChatMessageUser(content=content))
        elif role == "assistant":
            calls = [ToolCall(id=tc.get("id") or "call_%d_%d" % (len(out), i),
                              function=(tc.get("function") or {}).get("name", ""),
                              arguments=(tc.get("function") or {}).get("arguments") or {})
                     for i, tc in enumerate(m.get("tool_calls") or [])]
            last_ids = [c.id for c in calls]
            out.append(ChatMessageAssistant(content=content, tool_calls=calls or None))
        elif role == "tool":
            cid = m.get("tool_call_id") or (last_ids.pop(0) if last_ids else "")
            out.append(ChatMessageTool(content=content, tool_call_id=cid))
    return out


def to_tool_infos(tools):
    """The harness's tool definitions -> Inspect ToolInfo, through the SAME converter the GPU path always used
    (`a1_ollama.to_ollama_tools`): the harness declares parameters as a flat {name: description} map, not JSON
    Schema, and reading only `properties` sent every tool with NO parameters."""
    import a1_ollama
    infos = []
    for t in a1_ollama.to_ollama_tools(tools):
        fn = t["function"]
        p = fn["parameters"]
        infos.append(ToolInfo(name=fn["name"], description=fn["description"],
                              parameters=ToolParams(properties=p.get("properties") or {},
                                                    required=p.get("required") or [])))
    return infos


def from_output(output, turn_cap=0):
    """Inspect ModelOutput -> the harness's chat_fn reply {"content", "tool_calls": [{"name", "arguments"}]}."""
    msg = output.message
    if is_runaway(output, turn_cap):
        return {"content": "", "tool_calls": [], "runaway": turn_cap}
    bad = (output.metadata or {}).get("invalid_tool_call")
    if bad and not msg.tool_calls:             # the harness tells the model, as the GPU path does
        return {"content": msg.text or "", "tool_calls": [], "invalid_tool_call": bad}
    return {"content": msg.text or "",
            "tool_calls": [{"name": tc.function, "arguments": tc.arguments or {}} for tc in (msg.tool_calls or [])]}


class CompactingChat:
    """One model call for the harness: compact (Inspect), generate (the lab provider, the order's turn cap), and on an
    over-context refusal a forced compaction + one retry, as Inspect's react agent recovers. `run(coro)` executes a
    coroutine on the loop that owns the model. `calibrate`: feed each reply's usage back to the compaction - right
    wherever the reported input count covers the WHOLE prompt."""

    def __init__(self, model, turn_cap, run, calibrate):
        self.model, self.turn_cap, self.run, self.calibrate = model, int(turn_cap or 0), run, calibrate
        self.gen_config = GenerateConfig(max_tokens=self.turn_cap) if self.turn_cap else GenerateConfig()
        self.hist, self.compact, self.compactions = [], None, []

    def call(self, messages, tools):
        """(ModelOutput, done_reason); (None, "context_full") when even a compacted conversation does not fit."""
        # compaction tracks messages by id, so each harness message keeps ONE Inspect message (the harness only
        # appends); the system prompt and the brief are the prefix every compacted view keeps
        self.hist.extend(to_inspect(messages)[len(self.hist):])
        infos = to_tool_infos(tools)
        if self.compact is None:
            self.compact = compaction(CompactionSummary(threshold=compact_threshold(self.model, self.turn_cap),
                                                        instructions=COMPACT_INSTRUCTIONS),
                                      prefix=self.hist[:2], tools=infos, model=self.model)
        inp, summary = self.run(self.compact.compact_input(self.hist))
        if summary is not None:
            self.compactions.append({"at_message": len(self.hist), "trigger": "threshold"})
        try:
            out = self.run(self.model.generate(inp, tools=infos, config=self.gen_config))
        except RuntimeError as e:
            if not is_context_full(e):
                raise
            try:
                inp, _ = self.run(self.compact.compact_input(self.hist, force=True))
                self.compactions.append({"at_message": len(self.hist), "trigger": "forced"})
                out = self.run(self.model.generate(inp, tools=infos, config=self.gen_config))
            except RuntimeError as e2:
                if not is_context_full(e2):
                    raise
                return None, "context_full"
        if self.calibrate:
            self.run(self.compact.record_output(inp, out))
        return out, ("runaway" if is_runaway(out, self.turn_cap) else getattr(out, "stop_reason", None))


class LoopThread:
    """An event loop on its own thread, for a synchronous caller (the GPU boxes' a1_order) to run Inspect's async model
    and compaction calls on: `run(coro)` blocks until the coroutine finishes. Daemon: it never holds a process open."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="a1-chat-loop").start()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()
