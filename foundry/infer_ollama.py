"""Run prompts against this box's Ollama and write one row per item.

Usage:
    python infer_ollama.py <items.json> <out.json> --model <name> [--host URL] [--num-ctx N]
                           [--temperature T] [--seed-base S] [--rounds R] [--items-file PATH]
                           [--price-ceiling-s SECONDS] [--offload-ok]

items.json: {"items": [{"id": "...", "prompt": "...", "system": "...?", "options": {...}?}, ...]}
out.json:   {"host", "model", "served", "rows": [{"id","round","seed","answer","thinking","served_model",
             "done_reason","prompt_eval_count","eval_count","eval_duration","tok_s","wall_s","error"}], "summary": {...}}

Constraints:
- `--price-ceiling-s`: Seconds after which a row is marked `done_reason: "priced_out"` and excluded from results.
  Rows with `id` starting with "control" are exempt to preserve validation integrity.
- GPU Residency: Exit code 2 if model is not fully on GPU (checked via `size_vram` AND `gpu_spill`).
  Refuses CPU-offload to ensure measurement accuracy. `--offload-ok` overrides this check.
- Hung Engine: If no tokens for IDLE_S, probe with 4 tokens. If still silent, exit code 2 (held, not failed).
- Checkpointing: Appends to `<out>.partial.jsonl` as rows complete to support stop/resume."""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib

# An embedded-python interpreter (._pth beside the exe) builds sys.path without the script
# dir, so sibling imports need this insert; PYTHONPATH is ignored in that mode.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_spill  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


RETRIES_5XX = 1        # Ollama returns intermittent 500s during load spikes; retry succeeds after a delay.
RETRY_PAUSE_S = 8


def _post(host, path, body, timeout):
    """One POST, retried ONCE on a 5xx after a pause (the pause changes the load state the 5xx came from --
    a retry must change something that matters). The final failure carries ollama's own error body."""
    req = urllib.request.Request(host.rstrip("/") + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    for attempt in range(RETRIES_5XX + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < RETRIES_5XX:
                try:
                    detail = e.read().decode("utf-8", "replace")[:200]
                except Exception:                                      # noqa: BLE001
                    detail = ""
                print("[infer] HTTP %s on %s (%s) - retrying once after %ds" % (e.code, path, detail or e.reason, RETRY_PAUSE_S), flush=True)
                time.sleep(RETRY_PAUSE_S)
                continue
            raise


IDLE_S = 600     # silence between streamed chunks that means the server has stopped working on this request
WORK_BEAT_S = 30  # while tokens stream, print `[beat] <chunks>` this often: the worker's own liveness, which the
                  # runner publishes as work_beat so the stall demon never reads one long answer as a wedge
_BEAT = {"chunks": 0, "printed": 0.0}
# A loop is no progress: tokens flow but nothing new is said (work ends on no progress, never on a clock).
# Rule, measured on 571 genuine finishes and 253 answers that ran to the context: BOTH of the last two
# 4000-char windows, digits collapsed to "0", compress below 0.10 -> 0 correct answers flagged (lowest genuine finish
# 0.166). Collapsing digits catches a COUNTING loop ("1.0.2402", "1.0.2403", ...: 0.125 raw, 0.013 collapsed) that the
# raw ratio missed. loop_fixtures.json holds the real texts; tolkien_llm and the tablet payloads use the same rule.
# --- LOOP RULE: one source. This block is byte-identical in infer_ollama.py and tolkien_llm.py (test_fleet_deploy pins it).
LOOP_WINDOW, LOOP_RATIO, LOOP_EVERY = 4000, 0.10, 2000
_DIGITS = re.compile(r"\d+")
# Collapsing digits makes a table of numbers read as one passage over and over, so a turn that works through a numeric
# table (in its thinking, then in its code) would be cut. So when the digits are what makes it low, the same windows are
# read with the digits kept. Real loops
# read at most 0.126 that way (counting_loop), the tables at least 0.198 (number_table_in_code), and no other turn of 433 saved
# turns of 8,000+ chars reaches this branch. Numbers that differ are data until they have run far longer than any table seen
# (the longest stretch measured is 9,000 chars): LOOP_DATA_WINDOWS windows of them is a runaway, whatever the digits say.
LOOP_RAW_RATIO, LOOP_DATA_WINDOWS = 0.15, 6
# A loop LONGER than those windows compresses like prose, so the ratio cannot see it (measured: one 23,401-char block
# written back to back, byte for byte, 14 copies, 333,843 chars). `repeating` is the
# second half of the rule: the output ENDS in one block written back to back, exactly, at least three copies and at
# least the same 2 * LOOP_WINDOW chars of it. Blocks under LOOP_MIN_PERIOD (half of LOOP_WINDOW * LOOP_RATIO, where the
# ratio takes over) are the ratio's to judge. Digits are NOT collapsed here: rows that differ only in their numbers are
# data. Measured on the saved long runs: it ends that turn at 168,000 chars (half), and fires on none of the other turns
# of 8,000+ chars (the longest 206,716).
LOOP_PROBE = int(LOOP_WINDOW * LOOP_RATIO)
LOOP_MIN_PERIOD = LOOP_PROBE // 2


def repeating(text):
    """The length of the block `text` ends in back to back (>= 3 exact copies, >= 2 * LOOP_WINDOW chars of them), else 0."""
    n = len(text)
    if n < 2 * LOOP_WINDOW:
        return 0
    q = text.rfind(text[-LOOP_PROBE:], 0, n - 1)
    if q < 0:
        return 0
    p = n - LOOP_PROBE - q
    if p < LOOP_MIN_PERIOD:
        return 0
    copies = max(3, -(-2 * LOOP_WINDOW // p))
    if n < copies * p:
        return 0
    last = text[n - p:]
    return p if all(text[n - (i + 1) * p:n - i * p] == last for i in range(1, copies)) else 0


def looping(text):
    """True when the output stopped making progress. `text` is everything the model has written this turn: its last
    two LOOP_WINDOW-char windows, digits collapsed, both compress below LOOP_RATIO and with the digits kept both below
    LOOP_RAW_RATIO; or LOOP_DATA_WINDOWS such windows of numbers that differ; or it ends in one long block written over
    and over (`repeating`)."""
    if len(text) < 2 * LOOP_WINDOW:
        return False

    def r(s, collapse=True):
        b = (_DIGITS.sub("0", s) if collapse else s).encode("utf-8", "replace")
        return len(zlib.compress(b, 9)) / len(b)
    last, prev = text[-LOOP_WINDOW:], text[-2 * LOOP_WINDOW:-LOOP_WINDOW]
    if r(last) < LOOP_RATIO and r(prev) < LOOP_RATIO:
        if r(last, False) < LOOP_RAW_RATIO and r(prev, False) < LOOP_RAW_RATIO:
            return True
        if len(text) >= LOOP_DATA_WINDOWS * LOOP_WINDOW and all(
                r(text[-(i + 1) * LOOP_WINDOW:-i * LOOP_WINDOW]) < LOOP_RATIO for i in range(2, LOOP_DATA_WINDOWS)):
            return True
    return bool(repeating(text))
# --- END LOOP RULE


def _chat_stream(host, body, idle_s, ceiling_s=None, on_chunk=None):
    """Streams /api/chat and reassembles it into the standard reply shape.
    `idle_s` is a socket-read timeout, so it only fires if Ollama is silent; an in-progress answer is never cut off by it.
    `ceiling_s` is a cost-based limit: if the generation cost exceeds this, the stream is closed, the partial text is kept, and `priced_out` is set.
    Loop detection (`looping`) is the only other early termination, setting `looped` without a time or cost check."""
    req = urllib.request.Request(host.rstrip("/") + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    content, thinking, calls, last = [], [], [], {}
    t0, priced_out, looped = time.time(), False, False
    tail, unchecked = "", 0
    with urllib.request.urlopen(req, timeout=idle_s) as r:
        for line in r:
            if not line.strip():
                continue
            chunk = json.loads(line.decode("utf-8", "replace"))
            if chunk.get("error"):
                raise RuntimeError("ollama: %s" % chunk["error"])
            if on_chunk is not None:
                on_chunk(chunk)
            msg = chunk.get("message") or {}
            piece = (msg.get("thinking") or "") + (msg.get("content") or "")
            content.append(msg.get("content") or "")
            thinking.append(msg.get("thinking") or "")
            calls.extend(msg.get("tool_calls") or [])
            last = chunk
            tail += piece                              # the whole turn: a long loop is only visible across all of it
            unchecked += len(piece)
            if unchecked >= LOOP_EVERY:
                unchecked = 0
                if looping(tail):
                    looped = True
                    break
            _BEAT["chunks"] += 1
            if time.time() - _BEAT["printed"] >= WORK_BEAT_S:
                _BEAT["printed"] = time.time()
                print("[beat] %d" % _BEAT["chunks"], flush=True)
            if ceiling_s and time.time() - t0 > ceiling_s:
                priced_out = True
                break
    out = dict(last)
    out["message"] = {"role": "assistant", "content": "".join(content), "thinking": "".join(thinking)}
    if calls:
        out["message"]["tool_calls"] = calls
    if priced_out:
        out["priced_out"] = True
        out["done_reason"] = "priced_out"
    elif looped:
        out["done_reason"] = "loop"          # void like "length": the model's answer to this task, not a measurement fault
    return out


THINK_LEVELS = ("xhigh", "medium", "low")
# ollama 0.40.5 bug: "high" maps to MEDIUM; use "xhigh" for max reasoning.
OLLAMA_THINK = {"xhigh": "xhigh", "medium": "medium", "low": "low"}


def think_value(think):
    """ollama's `think`: a level label is sent as ollama's level string, anything else as a bool. For qwen3.8 on
    ollama 0.40.5, `true`, "medium" and "high" all render MEDIUM; only "xhigh" renders the vendor's xhigh."""
    return OLLAMA_THINK[think] if think in THINK_LEVELS else bool(think)


# Strata rejects over-context requests with custom error strings, not llama-server's standard codes. Match both sets in OVER_CONTEXT so is_context_full correctly triggers compaction.
OVER_CONTEXT = ("exceeds the context", "leaves no room to answer in the context", "exceed_context_size",
                "context_length_exceeded")


def template_kwargs(think):
    """`think` -> chat_template_kwargs: off = enable_thinking false; an effort level = enable_thinking + that
    reasoning_effort; on = {} (the model template's own default: Qwen3.8-Flash-Next's is xhigh)."""
    if think is False:
        return {"enable_thinking": False}
    if isinstance(think, str):
        return {"enable_thinking": True, "reasoning_effort": think}
    return {}


def response_format(fmt):
    """ollama's `format` (a JSON schema, or "json") -> the OpenAI response_format. Strata VALIDATES the answer against
    it after writing (its /props: method prompt_and_validate, constrained_decoding false) where ollama constrains the
    writing itself - the engine's own behaviour, measured as served."""
    if fmt is None:
        return None
    if fmt == "json":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": {"name": "answer", "schema": fmt}}


def parse_openai_stream(lines, looping=None, loop_every=2000, beat=None, ceiling_s=None, t0=None, on_chunk=None):
    """An OpenAI chat/completions SSE stream (bytes lines) -> the ollama-shaped result: {"message": {content, thinking,
    tool_calls: [{function: {name, arguments}}]}, prompt_eval_count (what was READ: timings.prompt_n, else
    usage.prompt_tokens), eval_count, prompt_eval_duration / eval_duration (ns), cache_n, done_reason}. Tool calls
    stream as indexed fragments and are merged; unparseable arguments pass through as text (the harness hands the
    model its own malformed call). A stream `looping` flags ends as done_reason "loop" with no tool calls (a looped
    call is cut mid-arguments). `ceiling_s` (from `t0`) is the order's price line, as in _chat_stream: past it the
    stream is closed and the result says priced_out. `on_chunk(obj)` sees every parsed chunk as it arrives, as in
    _chat_stream. Pure: `lines` is any iterable, so tests feed canned chunks."""
    parts, think, calls, tm, usage, finish = [], [], {}, {}, {}, None
    tail, unchecked, priced_out = "", 0, False
    t0 = time.time() if t0 is None else t0
    for raw in lines:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).strip()
        if line.startswith(":"):
            # the server's keep-alive: before a read's first progress line (up to a 32,768-token chunk) it is all the engine
            # says. A wedged engine is the server's to end (ENGINE_SILENCE_S, whose allowance scales with the chunk), so
            # while it keeps talking the request is alive; a hung server sends none and the guards still fire.
            if beat is not None:
                beat()
            if on_chunk is not None:
                on_chunk({"keepalive": True})
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        obj = json.loads(data)
        if obj.get("error"):
            raise RuntimeError("openai server: %s" % ((obj["error"] or {}).get("message") or obj["error"]))
        if on_chunk is not None:
            on_chunk(obj)
        for ch in obj.get("choices") or []:
            delta = ch.get("delta") or {}
            parts.append(delta.get("content") or "")
            think.append(delta.get("reasoning_content") or "")
            piece = (delta.get("reasoning_content") or "") + (delta.get("content") or "")
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", len(calls)), {"name": "", "arguments": ""})
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
                piece += (fn.get("name") or "") + (fn.get("arguments") or "")
            finish = ch.get("finish_reason") or finish
            if beat is not None:
                beat()
            tail += piece
            unchecked += len(piece)
        if looping is not None and unchecked >= loop_every:
            unchecked = 0
            if looping(tail):
                finish = "loop"
                break
        tm = obj.get("timings") or tm
        usage = obj.get("usage") or usage
        if ceiling_s and time.time() - t0 > ceiling_s:
            priced_out, finish = True, "priced_out"
            break
    tool_calls = []
    for i in (sorted(calls) if finish not in ("loop", "priced_out") else []):
        a = calls[i]["arguments"]
        try:
            a = json.loads(a) if a.strip() else {}
        except ValueError:
            pass
        tool_calls.append({"function": {"name": calls[i]["name"], "arguments": a}})

    def ns(ms):
        return None if ms is None else int(float(ms) * 1e6)
    out = {"message": {"role": "assistant", "content": "".join(parts), "thinking": "".join(think),
                       "tool_calls": tool_calls},
           "prompt_eval_count": tm.get("prompt_n", usage.get("prompt_tokens")),
           "eval_count": tm.get("predicted_n", usage.get("completion_tokens")),
           "prompt_eval_duration": ns(tm.get("prompt_ms")), "eval_duration": ns(tm.get("predicted_ms")),
           "cache_n": tm.get("cache_n"), "done_reason": finish or "stop"}
    if priced_out:
        out["priced_out"] = True
    return out


def _beat():
    _BEAT["chunks"] += 1
    if time.time() - _BEAT["printed"] >= WORK_BEAT_S:
        _BEAT["printed"] = time.time()
        print("[beat] %d" % _BEAT["chunks"], flush=True)


def _chat_openai(base, messages, options, fmt=None, think=False, ceiling_s=None, tools=None, timeout=None, model=None,
                 on_chunk=None):
    """One streamed chat turn on an OpenAI-compatible server at `base`.

    `options` must not carry `num_predict` unless a per-call cap is intended (maps to `max_tokens`).
    Termination is driven solely by idle timeout (silence) or `timeout` (bounded probe), not a global request clock.
    `model` identifies the served model for recording; Strata provides this via `/props`."""
    # return_progress: emits chunks during prompt reading to prevent long reads from appearing as idle stalls.
    body = {"messages": messages, "stream": True, "stream_options": {"include_usage": True}, "return_progress": True}
    for k in ("temperature", "seed", "top_p", "top_k", "presence_penalty", "frequency_penalty", "stop"):
        if (options or {}).get(k) is not None:
            body[k] = options[k]
    if (options or {}).get("num_predict"):
        body["max_tokens"] = int(options["num_predict"])
    if tools:
        body["tools"] = tools
    rf = response_format(fmt)
    if rf:
        body["response_format"] = rf
    kw = template_kwargs(think)
    if kw:
        body["chat_template_kwargs"] = kw
    req = urllib.request.Request(base.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout or IDLE_S)
    except urllib.error.HTTPError as e:        # the server's own reason - over-context becomes the standard signal
        text = ""
        try:
            text = e.read().decode("utf-8", "replace")[:600]
        except Exception:                                                    # noqa: BLE001
            pass
        tag = " exceed_context_size" if any(s in text for s in OVER_CONTEXT) else ""
        raise RuntimeError("openai server HTTP %s%s: %s" % (e.code, tag, text or e.reason)) from None
    with resp as r:
        out = parse_openai_stream(r, looping=looping, loop_every=LOOP_EVERY, beat=_beat,
                                  ceiling_s=ceiling_s, t0=t0, on_chunk=on_chunk)
    out["model"] = model
    return out



def on_strata(a):
    """True when the order runs on Strata: `--strata` names its plan file (a path, nothing else counts)."""
    return isinstance(getattr(a, "strata", None), str) and bool(a.strata)


def _ask(a, messages, options, fmt=None, ceiling_s=None):
    """The order's one answer call: ollama's /api/chat, or the openai wire when the order runs on Strata (`--strata`).
    On Strata num_ctx / num_predict are not sent: the served context IS the tested one (checked against /props at
    start) and Strata never truncates, so an answer still ends at the context, as num_predict = num_ctx does on ollama."""
    if on_strata(a):
        # the plan's vendor sampler lies UNDER the order's own knobs, as an ollama tag's defaults do (a tag carries
        # top_p / top_k; Strata applies only what the request sends)
        opts = dict(getattr(a, "strata_sampler", None) or {})
        opts.update({k: v for k, v in options.items() if k not in ("num_ctx", "num_predict")})
        return _chat_openai(a.host, messages, opts, fmt=fmt, think=a.think, ceiling_s=ceiling_s, model=a.model)
    return _chat(a.host, a.model, messages, options, fmt=fmt, think=a.think,
                 **({"ceiling_s": ceiling_s} if ceiling_s else {}))


def _chat(host, model, messages, options, timeout=None, fmt=None, think=False, ceiling_s=None, tools=None,
          on_chunk=None, keep_alive=None):
    """`timeout` given = one bounded non-streamed call (the warm-up). Otherwise streamed, ended only by silence
    (or by `ceiling_s`, the order's price line). `tools` = ollama native tool calling; the calls come back in
    `message.tool_calls`. `on_chunk` = see _chat_stream (passed only when given, so existing callers are unchanged).
    `keep_alive` = the caller's own residency rule (HAI's per-host one: Softserve unloads soon for Plex); default 10m."""
    body = {"model": model, "stream": timeout is None, "keep_alive": keep_alive or "10m", "think": think_value(think),
            "options": options, "messages": messages}
    if fmt is not None:
        body["format"] = fmt          # ollama structured output: a JSON schema the reply must satisfy
    if tools:
        body["tools"] = tools
    if timeout is not None:
        return _post(host, "/api/chat", body, timeout)
    for attempt in range(RETRIES_5XX + 1):
        try:
            return _chat_stream(host, body, IDLE_S, ceiling_s=ceiling_s,
                                **({"on_chunk": on_chunk} if on_chunk is not None else {}))
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < RETRIES_5XX:
                print("[infer] HTTP %s on /api/chat - retrying once after %ds" % (e.code, RETRY_PAUSE_S), flush=True)
                time.sleep(RETRY_PAUSE_S)
                continue
            raise


PROBE_S = 180    # 180s is ~4x the slowest cold-reload measurement, ensuring a loading engine never times out on a 4-token probe.


def _silence(e):
    """True when an answer ended because ollama sent nothing for IDLE_S (read or connect), not because it said no."""
    if isinstance(e, urllib.error.HTTPError):
        return False
    return isinstance(e, (TimeoutError, socket.timeout)) or (
        isinstance(e, urllib.error.URLError) and isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout)))


def _engine_alive(host, model, num_ctx, strata=False):
    """After an answer went silent: can the engine produce ANY token? A silent answer alone cannot tell a hung runner
    from a slow one, and an error row per silent answer reads as progress to the factory; one 4-token probe can tell."""
    try:
        if strata:
            _chat_openai(host, [{"role": "user", "content": "ok"}], {"num_predict": 4}, timeout=PROBE_S)
        else:
            _chat(host, model, [{"role": "user", "content": "ok"}], {"num_ctx": int(num_ctx), "num_predict": 4},
                  timeout=PROBE_S)
        return True
    except Exception:                                                      # noqa: BLE001
        return False


def _residency(host, model):
    with urllib.request.urlopen(host.rstrip("/") + "/api/ps", timeout=30) as r:
        ps = json.load(r)
    for m in ps.get("models", []):
        if model in (m.get("name"), m.get("model")):
            return m.get("size_vram", 0), m.get("size", 1)
    return None, None


def _spill_reading():
    return gpu_spill.read()


def _placed(host, model):
    """-> (on the card and not spilled, size_vram, size, spill reading or None). ollama counts shared system memory as
    resident, so a full size_vram is checked against the runner's own counters too (gpu_spill)."""
    vram, size = _residency(host, model)
    if vram is None or vram < size:
        return False, vram, size, None
    sp = _spill_reading()
    return not gpu_spill.spilled(sp), vram, size, sp


# A declared offload that is already placed is not stale. The last clean placement for each (host, model, context) is remembered on the box;
# a resident placement at least as good as it is kept, a worse one is still evicted and reloaded.
PLACEMENTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "state",
                               "clean-placements.json")


def _clean_key(host, model, num_ctx):
    return "%s|%s|%d" % ((host or "").rstrip("/"), model, int(num_ctx))


def _last_clean(host, model, num_ctx):
    try:
        with open(PLACEMENTS_FILE, encoding="utf-8") as f:
            return (json.load(f) or {}).get(_clean_key(host, model, num_ctx))
    except (OSError, ValueError):
        return None


def _remember_clean(host, model, num_ctx, vram, size):
    """Never raises: a placement that cannot be remembered only means the next window reloads, as before."""
    try:
        if not os.path.isdir(os.path.dirname(PLACEMENTS_FILE)):
            return
        try:
            with open(PLACEMENTS_FILE, encoding="utf-8") as f:
                book = json.load(f) or {}
        except (OSError, ValueError):
            book = {}
        book[_clean_key(host, model, num_ctx)] = {"size_vram": vram, "size": size, "t": time.time()}
        tmp = PLACEMENTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(book, f, indent=1)
        os.replace(tmp, PLACEMENTS_FILE)
    except Exception as e:                                               # noqa: BLE001
        print("clean placement not remembered (%s): the next window reloads" % type(e).__name__, flush=True)


def _resident(vram, size, sp):
    return {"size_vram": vram, "size": size, "offload": False, "spill": sp if sp is not None else "could not tell"}


def _assert_resident(host, model, num_ctx, offload_ok=False, cold=False):
    """size_vram < size is the silent CPU-offload crawl, and a full size_vram with the runner in shared system memory
    is the same crawl that ollama cannot see: one clean reload, then refuse (rc 2).
    `offload_ok`: the order declares a model larger than the card (a big MoE whose experts live in RAM by design), so
    partial residency after the clean load IS the model being measured. `cold`: evict a resident model first, so the
    order measures a cold load (--cold-load). -> the measured placement {vram, size, cold, load_s}: `load_s` is
    ollama's own load_duration for the clean load, the residency price the placement table reads."""
    ok, vram, size, sp = _placed(host, model)
    if ok and not cold:
        return dict(_resident(vram, size, sp), cold=False, load_s=None)
    if offload_ok and not cold and vram and size and vram < size:
        last = _last_clean(host, model, num_ctx) or {}
        if last.get("size") == size and vram >= (last.get("size_vram") or float("inf")):
            print("placement: %s at %.1f/%.1f GB, as its last clean load placed it (%.1f GB) - offload declared by the "
                  "order, kept without a reload" % (model, vram / 1e9, size / 1e9, last["size_vram"] / 1e9), flush=True)
            return dict({"size_vram": vram, "size": size, "offload": True}, cold=False, load_s=None)
    if ok:
        print("cold-load measurement: evicting the resident %s first" % model, flush=True)
        _post(host, "/api/chat", {"model": model, "keep_alive": 0, "messages": []}, 120)
        time.sleep(5)
        if not gpu_spill.wait_released():
            print("the runner still holds the card 90 s after the unload - loading anyway", flush=True)
    elif vram is None:
        print("%s not loaded - performing a clean load" % model, flush=True)
    else:
        if vram >= size:
            print("spilled: %s reports %.1f/%.1f GB on the card but its runner holds %d MB in shared system memory "
                  "on a full card (%d of %d MB) - evicting and reloading clean" % (
                      model, vram / 1e9, size / 1e9, sp["shared_mb"], sp["adapter_mb"], sp["card_mb"]), flush=True)
        else:
            print("stale placement: %s at %.1f/%.1f GB - evicting and reloading clean" % (model, vram / 1e9, size / 1e9), flush=True)
        _post(host, "/api/chat", {"model": model, "keep_alive": 0, "messages": []}, 120)
        time.sleep(5)
        if not gpu_spill.wait_released():
            print("the runner still holds the card 90 s after the unload - reloading anyway, the check below decides",
                  flush=True)
    r = _chat(host, model, [{"role": "user", "content": "ok"}], {"num_ctx": int(num_ctx), "num_predict": 4},
              timeout=900) or {}
    ld = r.get("load_duration")
    load = {"cold": True, "load_s": round(ld / 1e9, 2) if isinstance(ld, (int, float)) and ld > 0 else None}
    ok, vram, size, sp = _placed(host, model)
    if ok:
        return dict(_resident(vram, size, sp), **load)
    if vram is not None and size and vram >= size:
        print("REFUSED (rc 2): %s at num_ctx %d holds %d MB in shared system memory on a full card (%d of %d MB) "
              "after a clean reload - ollama reports it resident, but it does not fit this card at this context"
              % (model, int(num_ctx), sp["shared_mb"], sp["adapter_mb"], sp["card_mb"]), flush=True)
        raise SystemExit(2)
    if offload_ok and vram:
        print("placement: %s at %.1f/%.1f GB on the card after a clean load - offload declared by the order, measuring "
              "it as placed" % (model, vram / 1e9, size / 1e9), flush=True)
        _remember_clean(host, model, num_ctx, vram, size)
        return dict({"size_vram": vram, "size": size, "offload": True}, **load)
    # rc 2 = refused on this run's precondition, not the order: the foundry holds the order and retries it.
    print("REFUSED (rc 2): %s partially resident (%s/%.1f GB) at num_ctx %d after a clean reload - the card "
          "cannot hold it right now; inference at CPU speed is not the model being measured"
          % (model, "%.1f" % (vram / 1e9) if vram is not None else "?", (size or 0) / 1e9, int(num_ctx)), flush=True)
    raise SystemExit(2)


class _BoxPython:
    """subprocess stand-in for a tablet payload's scorer: the payload runs checks with `python3`, which on the fleet
    boxes is the Windows Store alias (it prints an install prompt and runs nothing, so every answer would score 0).
    Swap that one argv element for this box's own interpreter; everything else is the real subprocess module."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, argv, **kw):
        return subprocess.run([sys.executable if a == "python3" else a for a in argv], **kw)


def _grade_jobs():
    """grade_jobs.py from THIS file's directory (deployed beside it in bin), whatever the caller's sys.path."""
    import importlib.util
    if "grade_jobs" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "grade_jobs", os.path.join(os.path.dirname(os.path.abspath(__file__)), "grade_jobs.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["grade_jobs"] = mod
    return sys.modules["grade_jobs"]


class Grader:
    """Grade a row with the SAME scorer the tablets use: a tablet payload module (code / tool / vlm), loaded from the
    share. The order then ships verdicts, so no session re-grades fleet work by hand. `kind` is detected from what the
    payload defines. A code grader proves itself on a known-good program at start: a grader that cannot run checks
    must fail the order loudly, never score every answer 0."""

    def __init__(self, path):
        import hashlib
        import importlib.util
        import tempfile
        spec = importlib.util.spec_from_file_location("grader_payload", path)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.path = path
        self.sha = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
        m = self.mod
        if hasattr(m, "score") and hasattr(m, "extract_code"):
            self.kind = "code"
            m.subprocess = _BoxPython()
            self.work = tempfile.mkdtemp(prefix="infer_grade_")
            passed, total, err = m.score("def _probe():\n    return 41 + 1\n", ["lambda: _probe() == 42"], self.work)
            if (passed, total) != (1, 1):
                raise SystemExit("GRADER BROKEN: the code scorer failed a known-good program (%r) - no verdict from "
                                 "this box can be trusted" % (err,))
        elif hasattr(m, "score_tool"):
            self.kind = "tool"
        elif hasattr(m, "_vlm_score"):
            self.kind = "vlm"
        else:
            raise SystemExit("GRADER BROKEN: %s defines no known scorer" % os.path.basename(path))

    def grade(self, row):
        """-> dict of verdict fields; never raises (an ungradable row says why)."""
        t0 = time.time()
        ans = row.get("answer") or ""
        field = "content"
        if not ans.strip() and (row.get("thinking") or "").strip():
            ans, field = row["thinking"], "thinking"
        out = {"graded_by": "%s@%s" % (os.path.basename(self.path), self.sha), "answer_field": field}
        try:
            m, tid = self.mod, row.get("id")
            if self.kind == "code":
                task = (m.TASKS.get(tid) or getattr(m, "CONTROL_TASKS", {}).get(tid))
                # ONE grader: the menu re-grades every row with this same call
                passed, total, err = _grade_jobs().score_answer(m, task, ans, self.work)
            elif self.kind == "tool":
                task = (m.TASKS.get(tid) or getattr(m, "CONTROL_TASKS", {}).get(tid))
                passed, total, err = m.score_tool(ans, task["gold_tool"], task["gold_args"])
            else:
                _img, _q, gold, match, _cat = m._VLM_ALL[tid]
                passed = int(bool(ans.strip()) and m._vlm_score(ans, gold, match))
                total, err = 1, ""
            out.update({"ok": total > 0 and passed == total, "checks_passed": passed, "checks_total": total,
                        "grade_error": err or ""})
        except Exception as e:                                             # noqa: BLE001
            out.update({"ok": None, "grade_error": "%s: %s" % (type(e).__name__, str(e)[:160])})
        out["grade_s"] = round(time.time() - t0, 2)
        return out


class PowerMeter:
    """GPU board power over this order, so the order ships its own energy (Hard Bench: energy per solved task).

    NVIDIA: one streaming `nvidia-smi -lms 250` process, every GPU summed per tick. AMD (no nvidia-smi): the
    box's own telemetry log (foundry_paths.GPU_TELEMETRY, ~11 s cadence), read at the end. `joules(t0, t1)`
    holds each sample until the next (step integral). No usable source => None, never 0 (a meter that could
    not measure must not read as a free request)."""

    def __init__(self):
        self.source, self.samples, self._proc = None, [], None
        try:
            self._proc = subprocess.Popen(["nvidia-smi", "--query-gpu=index,power.draw", "--format=csv,noheader,nounits",
                                           "-lms", "250"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            self.source = "nvidia-smi@4Hz"
            threading.Thread(target=self._read, daemon=True).start()
        except (FileNotFoundError, OSError):
            self._proc = None
            try:
                import foundry_paths
                if os.path.exists(foundry_paths.GPU_TELEMETRY):
                    self.source, self._csv = "telemetry-csv", foundry_paths.GPU_TELEMETRY
            except ImportError:
                pass

    def _read(self):
        tick, t = {}, None
        for line in self._proc.stdout:
            try:
                idx, w = [x.strip() for x in line.split(",")[:2]]
                w = float(w)
            except ValueError:
                continue
            if idx in tick:                      # a GPU repeats => the previous tick is complete
                self.samples.append((t, sum(tick.values())))
                tick = {}
            if not tick:
                t = time.time()
            tick[idx] = w

    def stop(self):
        if self._proc:
            self._proc.terminate()
        elif self.source == "telemetry-csv":
            with open(self._csv, encoding="utf-8", errors="replace") as f:
                head = f.readline().strip().split(",")
                its, iw = head.index("ts"), head.index("power_w")
                for line in f:
                    p = line.rstrip("\n").split(",")
                    try:
                        self.samples.append((time.mktime(time.strptime(p[its], "%Y-%m-%d %H:%M:%S")), float(p[iw])))
                    except (ValueError, IndexError):
                        continue
        self.samples.sort()

    def joules(self, t0, t1):
        s = [x for x in self.samples if x[0] < t1]
        if not s or t1 <= t0:
            return None
        before = [x for x in s if x[0] <= t0]
        pts = ([(t0, before[-1][1])] if before else []) + [x for x in s if x[0] > t0]
        if not pts or (not before and pts[0][0] - t0 > 1.0):   # the window's start is not covered: refuse,
            return None                                        # a partial integral would read as a real one
        e = sum(w * ((pts[i + 1][0] if i + 1 < len(pts) else t1) - t) for i, (t, w) in enumerate(pts))
        return round(e, 1)


def wilson_low(k, n, z=1.96):
    """Lower 95% bound of a k-of-n share (tolkien_llm.wilson_low is the same bound on Tolkien)."""
    if not n:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5) / (1 + z * z / n)


def brake_verdict(rows, share):
    """None, or why the order stops: answers that ran to the context or looped (done_reason "length" / "loop" - num_predict is the
    context) are at least the family's cannot-fit share at 95% confidence. Controls, errors and priced rows don't count."""
    if share is None:
        return None
    graded = [r for r in rows if not r.get("error") and not r.get("priced_out")
              and not str(r.get("id") or "").startswith("control")]
    capped = sum(1 for r in graded if r.get("done_reason") in ("length", "loop"))
    low = wilson_low(capped, len(graded))
    if low < share:
        return None
    return {"at_cap": capped, "answers": len(graded), "share_low": round(low, 3), "rule_share": share,
            "why": "%d of %d answers ran to the context (share at least %.2f >= the stop rule's %.2f)"
                   % (capped, len(graded), low, share)}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("items")
    ap.add_argument("out")
    ap.add_argument("--model", required=True)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--temperature", type=float, default=None, help="omit = ollama/model default")
    ap.add_argument("--seed-base", type=int, default=0, help="seed = seed_base + round; 0 = no seed pin")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--think", action="store_true", help="let a thinking model think (default off)")
    ap.add_argument("--think-level", choices=THINK_LEVELS, default=None,
                    help="thinking ON at this reasoning effort (sent as the level string); overrides --think")
    ap.add_argument("--items-file", default=None, help="desk share: run only the ids listed in this JSON list")
    ap.add_argument("--grade", default=None, help="a tablet payload (code/tool/vlm) whose scorer grades every row here")
    ap.add_argument("--offload-ok", action="store_true",
                    help="the model is larger than the card: run it partially resident and record the placement")
    ap.add_argument("--price-ceiling-s", type=float, default=None,
                    help="the order's measured price line in seconds: an answer that costs more is cut and recorded "
                         "priced_out (control items are exempt). Omit = no price line.")
    ap.add_argument("--at-cap-brake", type=float, default=None,
                    help="the family's cannot-fit share (menu_fill FIT_VOID_SHARE): stop the order once the answers "
                         "that ran to the context are at least that share at 95%% confidence. Omit = no brake.")
    ap.add_argument("--cold-load", action="store_true",
                    help="evict the model first if it is resident, so placement.load_s measures a COLD load "
                         "(the residency price the placement table reads)")
    ap.add_argument("--strata", default=None, metavar="PLAN.json",
                    help="run on Strata instead of ollama: the plan names the server (strata, gguf_dir, data_dir, "
                         "model, shards, port, setup_args - workspace\\flashnext's strata_arm lifecycle); the order "
                         "starts it, checks its served context == --num-ctx, and stops it by any route")
    a, _extra = ap.parse_known_args(argv)          # the runner appends the order name; ignore it
    if a.think_level:
        a.think = a.think_level
    if a.price_ceiling_s is not None and not a.price_ceiling_s > 0:
        raise SystemExit("--price-ceiling-s must be a positive number of seconds (a measured price), got %r"
                         % a.price_ceiling_s)
    if a.at_cap_brake is not None and not 0 < a.at_cap_brake <= 1:
        raise SystemExit("--at-cap-brake must be a share in (0, 1], got %r" % a.at_cap_brake)
    a.grader = Grader(a.grade) if a.grade else None   # before any GPU time: a broken grader fails the order now

    with open(a.items, encoding="utf-8") as f:
        items = json.load(f).get("items") or []
    if a.items_file:
        with open(a.items_file, encoding="utf-8") as f:
            keep = set(json.load(f))
        items = [it for it in items if it.get("id") in keep]
        if not items:
            raise SystemExit("BROKEN DEAL: items-file matched 0 items - the deal is broken, not the work")
    total = len(items) * max(1, a.rounds)
    box = socket.gethostname().lower()
    print("[infer] %s model=%s items=%d rounds=%d num_ctx=%d temp=%s price_ceiling_s=%s" % (
        box, a.model, len(items), a.rounds, a.num_ctx, a.temperature, a.price_ceiling_s), flush=True)

    server = None                               # (strata_arm, proc, port) while this order's Strata server runs
    try:
        if on_strata(a):
            server, a.placement = _strata_up(a)
        else:
            a.placement = _assert_resident(a.host, a.model, a.num_ctx, offload_ok=a.offload_ok, cold=a.cold_load)
        meter = PowerMeter()
        idle = (time.time(), None)
        if meter.source:
            time.sleep(3)                            # idle baseline: resident model, no request in flight
            idle = (idle[0], time.time())
        try:
            return _run(a, items, total, box, meter, idle)
        finally:
            try:
                meter.stop()
            except Exception:                                              # noqa: BLE001
                pass
    finally:
        if server is not None and server[0] is not None:   # the server ends with the order, by ANY route
            server[0].stop(server[1], server[2])           # (stop never raises)


def strata_module():
    """<root>\\workspace\\flashnext\\strata_arm - Strata's ONE lifecycle (prepare / enter / launch / stop), shared with
    the Flash-Next arms and a1_order's `engine: "strata"`. <root> = the foundry this file is deployed in."""
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workspace", "flashnext")
    if d not in sys.path:
        sys.path.append(d)
    import strata_arm
    return strata_arm


ENGINE_LIVE_FILE = "engine_live.json"     # the same name p20_foundry.ENGINE_LIVE_FILE reads


def write_engine_live(out_dir, host, ctx):
    """Declare this order's live engine to its factory: {"engine": "strata", "metrics": <url>, "ctx": N} in the order's
    own folder. Stated, never probed for - the factory polls only what an order declares."""
    with open(os.path.join(out_dir, ENGINE_LIVE_FILE), "w", encoding="utf-8") as f:
        json.dump({"engine": "strata", "metrics": host.rstrip("/") + "/metrics", "ctx": ctx}, f)


def _strata_up(a, frontier=True):
    """Start the plan's Strata server for this order -> ((strata_arm, proc, port), placement). Sets a.host. Refuses (rc 2) when the served context does not match the order's --num-ctx: a mismatch distorts measurements. Use frontier=False for daily use."""
    with open(a.strata, encoding="utf-8") as f:
        plan = json.load(f)
    S = strata_module()
    port = int(plan.get("port", 8080))
    a.host = "http://127.0.0.1:%d" % port
    a.strata_sampler = {k: v for k, v in (plan.get("sampler") or {}).items() if k not in ("temperature", "seed")}
    placement = {"engine": "strata", "model": plan.get("model"), "host": a.host, "offload": True}
    out_dir = os.path.dirname(os.path.abspath(a.out))
    bat, src = S.prepare(plan, out_dir, placement, trust_verified=not frontier)
    placement["serving"] = S.apply_serving(plan, src)   # the plan's env + engine flags (its measured best config)
    if not S.port_free(port):
        print("REFUSED (rc 2): port %d is already in use - not starting a second server beside it" % port, flush=True)
        raise SystemExit(2)
    S.enter(frontier=frontier)
    server = (S, None, port)
    try:
        proc, ok, load_s = S.launch(bat, src, port, out_dir)
        server = (S, proc, port)
        if not ok:
            raise RuntimeError("Strata died before answering (rc %s) - see server.log" % proc.returncode)
        placement["load_s"] = load_s
        with urllib.request.urlopen(a.host + "/props", timeout=30) as r:
            props = json.load(r)
        served = (props.get("default_generation_settings") or {}).get("n_ctx") or props.get("n_ctx") or \
            ((props.get("context") or {}).get("native"))
        placement["served_ctx"] = served
        if served != int(a.num_ctx):
            print("REFUSED (rc 2): Strata serves a %s-token context, the order asks for %d" % (served, a.num_ctx),
                  flush=True)
            raise SystemExit(2)
        print("strata up in %.0f s on %s, context %s" % (load_s, a.host, served), flush=True)
        # the deck's LIVE read / write / context rows: the factory polls Strata's own /metrics each beat
        # (p20_foundry.EngineRates; Strata's Monitor fields, docs\DETAILS.md "What the model is doing right now")
        write_engine_live(out_dir, a.host, served)
        return server, placement
    except BaseException:
        S.stop(server[1], server[2])
        raise


def marker_name(item_id, rnd):
    """Constructs the progress-marker filename, sanitizing item_id characters that are invalid in Windows filenames to prevent crashes."""
    return "%s.r%d.done" % (re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(item_id)), rnd)


def _run(a, items, total, box, meter, idle):
    prog_dir = a.out[:-5] + ".progress" if a.out.endswith(".json") else a.out + ".progress"
    os.makedirs(prog_dir, exist_ok=True)
    partial = a.out + ".partial.jsonl"
    rows, n = [], 0
    served = None
    braked = None
    # RESUME: a pinned order that waited for its card (Plex, a lost card) restarts in the same {ORDER_OUT}; every
    # (item, round) already answered without an error is kept, not redone. Errored rows run again. The meter cannot
    # have sampled a previous run, so a resumed row's gpu_j stays None rather than a made-up number.
    done = {}
    if os.path.exists(partial):
        for line in open(partial, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue                                  # a line cut by the stop is not a result
            if isinstance(r, dict) and not r.get("error"):
                done[(r.get("id"), r.get("round"))] = r
        if done:
            print("[infer] resuming: %d answered rows kept from %s" % (len(done), os.path.basename(partial)), flush=True)
        # Ensure the partial file ends with a newline to prevent merging with the next appended row.
        with open(partial, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell():
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    with open(partial, "a", encoding="utf-8") as pf0:
                        pf0.write("\n")
    with open(partial, "a", encoding="utf-8") as pf:
        for rnd in range(1, max(1, a.rounds) + 1):
            for it in items:
                prior = done.get((it.get("id"), rnd))
                if prior is not None:
                    rows.append(prior)
                    served = served or prior.get("served_model")
                    open(os.path.join(prog_dir, marker_name(it.get("id"), rnd)), "w").close()
                    n += 1
                    continue
                # Cap num_predict at the tested context window to prevent loop failures in shifting-window models (e.g., qwen3-vl).
                # "length" voids are treated as "cannot fit" by the stop rule.
                options = {"num_ctx": int(a.num_ctx), "num_predict": int(a.num_ctx)}
                if a.temperature is not None:
                    options["temperature"] = float(a.temperature)
                if a.seed_base:
                    options["seed"] = int(a.seed_base) + rnd
                options.update(it.get("options") or {})
                user_msg = {"role": "user", "content": it.get("prompt") or ""}
                if it.get("images"):
                    user_msg["images"] = list(it["images"])      # base64, as ollama's chat API takes them
                messages = ([{"role": "system", "content": it["system"]}] if it.get("system") else []) + [user_msg]
                row = {"id": it.get("id"), "round": rnd, "seed": options.get("seed"), "answer": "", "thinking": "",
                       "served_model": None, "done_reason": None, "prompt_eval_count": None, "eval_count": None,
                       "wall_s": None, "error": ""}
                t0 = time.time()
                # the price line binds every answer but a control: a cut control would read as a failed control
                # and void the order it exists to validate
                ceiling = getattr(a, "price_ceiling_s", None)
                ceiling = None if str(it.get("id") or "").startswith("control") else ceiling
                try:
                    resp = _ask(a, messages, options, fmt=it.get("format"), ceiling_s=ceiling)
                    msg = resp.get("message") or {}
                    row.update({"answer": msg.get("content") or "", "thinking": msg.get("thinking") or "",
                                "served_model": resp.get("model"), "done_reason": resp.get("done_reason"),
                                "prompt_eval_count": resp.get("prompt_eval_count"), "eval_count": resp.get("eval_count"),
                                "eval_duration": resp.get("eval_duration"),
                                "prompt_eval_duration": resp.get("prompt_eval_duration")})
                    if row["eval_count"] and row["eval_duration"]:
                        row["tok_s"] = round(row["eval_count"] / (row["eval_duration"] / 1e9), 1)
                    # reading is compute-bound where writing is bandwidth-bound: a clock or engine change shows here first
                    if row["prompt_eval_count"] and row["prompt_eval_duration"]:
                        row["prompt_tok_s"] = round(row["prompt_eval_count"] / (row["prompt_eval_duration"] / 1e9), 1)
                    if resp.get("priced_out"):
                        row.update({"priced_out": True, "ceiling_s": ceiling})
                    # Strata treats 'length' as a loop guard, not a cap: if it ends well short of the context limit, reclassify as 'loop'.
                    if on_strata(a) and row["done_reason"] == "length" and \
                            (row["prompt_eval_count"] or 0) + (row["eval_count"] or 0) < int(a.num_ctx) - 1024:
                        row.update({"done_reason": "loop", "engine_repeat_stop": True})
                    served = served or resp.get("model")
                except urllib.error.HTTPError as e:
                    # Capture the response body; the status code alone is insufficient to distinguish VRAM spills from timeouts.
                    try:
                        body = e.read().decode("utf-8", "replace")[:300]
                    except Exception:                                      # noqa: BLE001
                        body = ""
                    row["error"] = "HTTP %s: %s" % (e.code, body or e.reason)
                except Exception as e:                                     # noqa: BLE001
                    if _silence(e) and not _engine_alive(a.host, a.model, a.num_ctx, strata=on_strata(a)):
                        # rc 2 = held, not failed: this answer is NOT recorded, so the retry re-asks it; answered
                        # rows stay in the partial file and are skipped on the retry
                        print("REFUSED (rc 2): engine hung - %s r%d got no token for %ds and a 4-token probe of %s got "
                              "nothing in %ds; the order is held for a human (restart ollama), answered rows kept"
                              % (it.get("id"), rnd, IDLE_S, a.model, PROBE_S), flush=True)
                        raise SystemExit(2)
                    row["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
                row["t0"], row["t1"] = round(t0, 2), round(time.time(), 2)
                row["wall_s"] = round(row["t1"] - t0, 1)
                if row.get("priced_out"):
                    # a cut answer is a stated price, not a graded one: grading its partial text could pass by luck
                    if a.grader is not None:
                        row.update({"ok": False, "checks_passed": 0, "checks_total": 0, "grade_error": "priced_out"})
                elif a.grader is not None and not row["error"]:
                    row.update(a.grader.grade(row))        # after t1: grading time is grade_s, never wall_s
                rows.append(row)
                pf.write(json.dumps(row, ensure_ascii=False) + "\n"); pf.flush()
                open(os.path.join(prog_dir, marker_name(it.get("id"), rnd)), "w").close()
                n += 1
                print("[infer] %d/%d %s r%d %s %.1fs%s%s" % (
                    n, total, it.get("id"), rnd, row["served_model"] or "?", row["wall_s"],
                    (" %.1f tok/s" % row["tok_s"]) if row.get("tok_s") else "",
                    (" ERROR " + row["error"]) if row["error"] else ""), flush=True)
                braked = brake_verdict(rows, getattr(a, "at_cap_brake", None))
                if braked:
                    print("[infer] AT-CAP BRAKE - %s; the rest of this order is not run" % braked["why"], flush=True)
                    break
            if braked:
                break
    errors = sum(1 for r in rows if r["error"])
    try:
        meter.stop()
    except Exception:                                                      # noqa: BLE001
        pass
    for r in rows:
        r["gpu_j"] = meter.joules(r["t0"], r["t1"]) if meter.source else None
    idle_j = meter.joules(*idle) if meter.source and idle[1] else None
    energy = {"source": meter.source, "samples": len(meter.samples),
              "idle_w": round(idle_j / (idle[1] - idle[0]), 1) if idle_j is not None else None,
              "gpu_j_total": round(sum(r["gpu_j"] for r in rows if r["gpu_j"] is not None), 1)
                             if any(r["gpu_j"] is not None for r in rows) else None,
              "rows_metered": sum(1 for r in rows if r["gpu_j"] is not None)}
    graded = [r for r in rows if r.get("ok") is not None] if a.grader is not None else []
    grading = ({"graded_by": "%s@%s" % (os.path.basename(a.grader.path), a.grader.sha), "kind": a.grader.kind,
                "rows_graded": len(graded), "rows_ok": sum(1 for r in graded if r["ok"]),
                "grade_s_total": round(sum(r.get("grade_s") or 0 for r in graded), 2)} if a.grader is not None else None)
    outd = {"host": box, "model": a.model, "served": served, "num_ctx": a.num_ctx, "temperature": a.temperature,
            "think": think_value(a.think),
            "grading": grading, "placement": getattr(a, "placement", None),
            "price_ceiling_s": getattr(a, "price_ceiling_s", None), "braked": braked,
            "rows": rows, "summary": {"items": len(items), "rounds": a.rounds, "rows": len(rows), "errors": errors,
                                      "priced_out": sum(1 for r in rows if r.get("priced_out")),
                                      "wall_mean_s": round(sum(r["wall_s"] or 0 for r in rows) / max(1, len(rows)), 1),
                                      "energy": energy}}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(outd, f, indent=1, ensure_ascii=False)
    print("[infer] done rows=%d errors=%d -> %s" % (len(rows), errors, a.out), flush=True)
    return 1 if errors == len(rows) and rows else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
