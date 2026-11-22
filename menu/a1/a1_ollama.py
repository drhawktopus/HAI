"""A1 agentic-build: drive a real ollama model as the harness's `chat_fn`.

Standard library only. This module turns an ollama chat endpoint into the
injected function the harness already takes:

    chat_fn(messages, tools) -> {"content": str,
                                 "tool_calls": [{"name": str,
                                                 "arguments": dict},...]}

Pinned interface:

    make_chat_fn(base_url, model, think=False, options=None,
                 num_ctx=None, log=None) -> chat_fn

Contract (per spec #34 A1, step 2):
  * each call POSTs to ``{base_url}/api/chat`` with
    ``{"model", "messages", "tools", "stream": false, "think": think,
    "options": options + num_ctx when given}``;
  * harness tools (``name``/``description``/``parameters``) are converted to
    ollama's function schema (``type: "function"`` with JSON-schema
    ``parameters``) — the harness's own tool names and arguments are left
    unchanged;
  * response ``message.tool_calls[*].function`` maps to
    ``{"name", "arguments"}``; ``arguments`` may arrive as a dict OR a JSON
    string (ollama has shipped both) and is normalised to a dict;
  * thinking trap: a thinking model can leave
    ``message.content`` EMPTY and file its text under ``message.thinking``.
    We return ``content`` from ``message.content`` ONLY — never from
    ``thinking``. The thinking length is recorded in the log instead;
  * NO request timeout and NO retry loop (— no arbitrary
    limits; the harness's no-progress rule ends a stuck run). An HTTP or
    connection error raises; the harness reports ``ended: "error"``."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# tool schema conversion
# ---------------------------------------------------------------------------

def _type_of(value: Any) -> str:
    """Best-effort JSON-Schema primitive type for a parameter value.

    The harness declares parameters as ``{name: description-string}``, i.e.
    the value is the human description and the actual type is unknown.  We
    infer from the value where possible and default to ``"string"``.
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def to_ollama_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Convert the harness tool list to ollama's chat request tool schema.

    Harness shape:
        {"name": str, "description": str, "parameters": {param: description, ...}}

    Ollama shape:
        {"type": "function",
         "function": {"name": str,
                      "description": str,
                      "parameters": {"type": "object",
                                     "properties": {...},
                                     "required": [...]}}}
    """
    out: List[Dict[str, Any]] = []
    for tool in tools or []:
        name = tool.get("name", "")
        description = tool.get("description", "")
        raw_params = tool.get("parameters") or {}
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for pname, pvalue in raw_params.items():
            properties[pname] = {"type": _type_of(pvalue)}
            required.append(pname)
        parameters: Dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            parameters["required"] = required
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })
    return out


# ---------------------------------------------------------------------------
# response mapping
# ---------------------------------------------------------------------------

def _normalize_arguments(args: Any) -> Dict[str, Any]:
    """Ollama may return ``arguments`` as a dict or a JSON string; accept both."""
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}
    if isinstance(args, list):
        return {}
    return {}


def map_response(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Map an ollama ``/api/chat`` response to the harness ``chat_fn`` shape.

    Returns ``{"content": str, "tool_calls": [{"name", "arguments"}, ...]}``.
    Thinking text is NEVER used as content (lab doctrine); its length is
    reported through the log, not here.
    """
    message = resp.get("message") or {}
    content = message.get("content")
    if content is None:
        content = ""
    elif not isinstance(content, str):
        content = str(content)

    tool_calls: List[Dict[str, Any]] = []
    for tc in (message.get("tool_calls") or []):
        fn = tc.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        arguments = _normalize_arguments(fn.get("arguments"))
        tool_calls.append({"name": name, "arguments": arguments})

    return {"content": content, "tool_calls": tool_calls}


# ---------------------------------------------------------------------------
# logging / stats
# ---------------------------------------------------------------------------

def _stats(resp: Dict[str, Any], content: str, tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    thinking = resp.get("message", {}).get("thinking") if isinstance(resp.get("message"), dict) else None
    if isinstance(thinking, str):
        thinking_chars = len(thinking)
    elif thinking is None:
        thinking_chars = 0
    else:
        thinking_chars = len(str(thinking))

    return {
        "prompt_eval_count": resp.get("prompt_eval_count"),
        "eval_count": resp.get("eval_count"),
        "eval_duration": resp.get("eval_duration"),
        "prompt_eval_duration": resp.get("prompt_eval_duration"),
        "done_reason": resp.get("done_reason"),
        "thinking_chars": thinking_chars,
        "tool_calls": len(tool_calls),
    }


# ---------------------------------------------------------------------------
# request body
# ---------------------------------------------------------------------------

def build_request_body(model: str,
                       messages: List[Dict[str, Any]],
                       tools: Optional[List[Dict[str, Any]]],
                       think: bool,
                       options: Optional[Dict[str, Any]],
                       num_ctx: Optional[int]) -> Dict[str, Any]:
    """Assemble the ``/api/chat`` request body.

    ``options`` plus ``num_ctx`` (when given) are merged into a single
    ``options`` dict.  When neither is given the body carries no ``options``
    key at all (criterion 4).
    """
    body: Dict[str, Any] = {
        "model": model,
        "messages": list(messages or []),
        "tools": to_ollama_tools(tools),
        "stream": False,
        # ollama 0.34.0 accepts "high"/"medium"/"low"/"max"/bool; "high" renders qwen3.8's xhigh
        "think": {"xhigh": "high", "medium": "medium", "low": "low"}.get(think, bool(think)),
    }

    merged: Dict[str, Any] = dict(options or {})
    if num_ctx is not None:
        merged["num_ctx"] = num_ctx
    if merged:
        body["options"] = merged
    return body


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def make_chat_fn(base_url: str,
                 model: str,
                 think: bool = False,
                 options: Optional[Dict[str, Any]] = None,
                 num_ctx: Optional[int] = None,
                 log: Optional[Callable[[Dict[str, Any]], None]] = None) -> Callable:
    """Build the ``chat_fn`` for ``a1_harness.run_task``.

    Parameters
    ----------
    base_url: ollama server root (e.g. ``http://127.0.0.1:11434``).
               Each call POSTs to ``{base_url}/api/chat``.
    model: ollama model tag.
    think: sent as ``"think"`` on every request.
    options: ollama request ``options`` dict, merged with ``num_ctx``.
    num_ctx: context window override; folded into ``options`` when given.
    log: optional callable; invoked once per request with a stats dict
          (missing response fields -> ``None``; ``done_reason == "length"``
          is visible here — a capped answer is void, not a fail).

    Raises
    ------
    urllib.error.HTTPError / URLError / ValueError: on any transport or
        JSON failure. No timeout, no retry ()."""

    def chat_fn(messages: List[Dict[str, Any]],
                tools: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        body = build_request_body(model, messages, tools, think, options, num_ctx)
        payload = json.dumps(body).encode("utf-8")
        url = base_url.rstrip("/") + "/api/chat"

        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        # NOTE: no timeout argument on purpose (lab ). A hung
        # connection stays hung; the harness's no-progress rule ends a stuck
        # run. A transport error RAISES (see except path below).
        with urllib.request.urlopen(request) as response:  # type: ignore[assignment]
            raw = response.read()

        try:
            resp = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("ollama returned non-JSON response: %r" % exc) from exc

        mapped = map_response(resp)
        if log is not None:
            log(_stats(resp, mapped["content"], mapped["tool_calls"]))
        return mapped

    return chat_fn
