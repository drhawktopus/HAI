"""Terminal-Bench model relay: OpenAI chat endpoint served on Sundae next to Harbor.

Turns requests into items for the lead's MODEL WINDOW. The window applies lab settings (effort, temperature, context) from the placement table, ignoring client-specified parameters.

Usage:
    python3 tb_relay.py --items-dir <items_dir> --port 8765 --log <requests.jsonl>

Waits for the answer file to appear (no timeout); returns an error if CLOSE appears first.
Logs all requests (wall time, tokens, done reason) for run analysis.
Standard library only."""
import argparse
import http.server
import json
import os
import socketserver
import threading
import time

ITEM, OUT, CLOSE = ".item.json", ".out.json", "CLOSE"
POLL_S = 0.5
# The lab's no-progress rule for a trial (the A1 loop cutter's NO_PROGRESS_REPEAT = 3, a1_harness, validated on 207
# replayed exercises): this many LOOP-cut answers in a row end the trial. It ends the way the agent ends itself -
# Terminus-2's own task_complete, answered again for its confirmation turn - so Harbor still GRADES the trial (closing
# the window instead made it an exception, decoy 10-10). Never a clock.
LOOP_STREAK_END = 3
# ...and the A1 cutter's STALL rule (NO_PROGRESS_STALL = 8, a1_harness): this many requests in a row whose observation
# (the last user message: the terminal output the agent is shown) was ALREADY SEEN in this trial end it too - the
# 10-10 arm's first trial answered "persist in waiting" with no commands for dozens of turns, never a loop. No nudge:
# a nudge would edit the agent's prompts, and the plan keeps what Terminus sends unchanged.
STALL_END = 8
# ...and the A1 cutter's REPEAT rule (NO_PROGRESS_REPEAT = 3, a1_harness: same tool + same args this many turns in a
# row): the agent sends the same non-empty keystrokes this many turns in a row. The stall rule cannot see an agent
# typing into an open editor - every keystroke changes the screen (10-10 arm: 96 of 112 turns inside nano). Turns
# that only wait (all keystrokes empty) are left to the stall rule.
REPEAT_END = 3
# A turn Terminus REJECTED ran nothing (A1: "no tool call at all - count as a stall turn"). Terminus-2 opens its next
# prompt with this exact text only on a parse ERROR, never on warnings (installed terminus_2.py, read 10-11);
# nemotron-3-nano 10-11: 94 of 109 turns rejected, each error screen slightly different, so the seen-rule missed it.
PARSE_ERROR = "Previous response had parsing errors:"
# The first line of Terminus-2's task prompt (templates/terminus-json-plain.txt, installed 10-11). Its summarization
# handoff opens instead with "You are picking up work from a previous AI agent" (terminus_2.py) - same trial.
TASK_PROMPT_HEAD = "You are an AI assistant tasked with solving command-line tasks"
# Terminus-2's summarization runs FOUR calls per handoff (terminus_2.py, installed 10-11); none is the agent acting on
# the terminal, so the no-progress rules neither count them as stall nor reset on them (10-11: 490 of the 9B's 917
# calls were these, and each new text reset the stall count).
SUMMARY_PROMPTS = ("You are about to hand off your work to another AI agent",
                   "You are picking up work from a previous AI agent",
                   "The next agent has a few questions for you",
                   "Here are the answers the other agent provided")
TERMINUS_DONE = json.dumps({"analysis": "Ended by the lab's no-progress rule (%d loop-cut answers in a row, %d turns "
                                        "in a row with no new terminal output, or the same keystrokes %d turns in a "
                                        "row)." % (LOOP_STREAK_END, STALL_END, REPEAT_END),
                            "plan": "", "commands": [], "task_complete": True})


def _text(content):
    """OpenAI content may be a string or a list of parts; the window takes text."""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ("" if content is None else str(content))


def keystrokes_of(answer):
    """The keystrokes a Terminus-2 answer sends, as a tuple; None when it sends none (or only waits) or is not JSON."""
    start, end = (answer or "").find("{"), (answer or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        cmds = json.loads(answer[start:end + 1]).get("commands")
    except (ValueError, AttributeError):
        return None
    keys = tuple(((c.get("keystrokes") or "") if isinstance(c, dict) else str(c)) for c in cmds or [])
    return keys if any(k.strip() for k in keys) else None


def to_window_messages(messages):
    """OpenAI chat messages -> the window's (ollama) shape: text content, tool-call arguments as objects."""
    out = []
    for m in messages or []:
        msg = {"role": m.get("role", "user"), "content": _text(m.get("content"))}
        calls = []
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except ValueError:
                    args = {"_raw": args}
            calls.append({"function": {"name": fn.get("name", ""), "arguments": args or {}}})
        if calls:
            msg["tool_calls"] = calls
        out.append(msg)
    return out


def to_openai_response(row, model, rid):
    """The window's row -> an OpenAI chat.completion. A context cap or a loop cut reports finish_reason "length"
    (the client sees a cut answer, and the relay's log counts it)."""
    calls = []
    for i, tc in enumerate(row.get("tool_calls") or []):
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        calls.append({"id": "call_%s_%d" % (rid, i), "type": "function",
                      "function": {"name": fn.get("name", ""),
                                   "arguments": args if isinstance(args, str) else json.dumps(args or {})}})
    # the lab's extraction rule (content, else thinking): a model that never closes its think block files the WHOLE
    # answer under thinking (nemotron-3-nano think on, 10-11: 9 of 12 valid JSON answers dropped, trial stalled out)
    message = {"role": "assistant", "content": row.get("answer") or row.get("thinking") or ""}
    if calls:
        message["tool_calls"] = calls
    done = row.get("done_reason")
    finish = "tool_calls" if calls else ("length" if done in ("length", "loop", "priced_out") else "stop")
    pe, ec = row.get("prompt_eval_count") or 0, row.get("eval_count") or 0
    return {"id": "chatcmpl-%s" % rid, "object": "chat.completion", "created": int(time.time()),
            "model": row.get("served_model") or model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": pe, "completion_tokens": ec, "total_tokens": pe + ec}}


class Relay:
    def __init__(self, items_dir, log_path=None, sleep=time.sleep, clock=time.time, rules=True):
        self.items_dir, self.log_path, self.sleep, self.clock = items_dir, log_path, sleep, clock
        self.rules = rules             # False for an ERRAND helper's relay: its turns are not Terminus trials
        self.lock = threading.Lock()
        self.n = 0
        self.loop_streak = 0
        self.stall = 0
        self.repeat, self.last_keys = 0, None
        self.seen = set()              # observations (terminal outputs) already shown in this trial
        self.ended = False             # this trial was ended by the no-progress rule
        self.ended_why = None

    def _new_trial(self, messages):
        """A fresh conversation that opens with Terminus's TASK prompt is a new trial: the no-progress state starts
        over. A fresh conversation that opens otherwise is Terminus's summarization handoff inside the SAME trial and
        keeps it (10-11: 124 handoffs each reset the rules, and one reset the relay's own end - a 4 h trial)."""
        convo = [m for m in messages or [] if m.get("role") in ("user", "assistant")]
        if len(convo) <= 1 and _text(convo[0].get("content") if convo else "").lstrip().startswith(TASK_PROMPT_HEAD):
            self.loop_streak, self.stall, self.seen, self.ended, self.ended_why = 0, 0, set(), False, None
            self.repeat, self.last_keys = 0, None

    def _observe(self, messages):
        """Count a STALL turn when the observation the agent is shown now was already shown in this trial, and a
        REPEAT when the agent's last answer (the newest assistant message) sent the same keystrokes as the one before."""
        users = [m for m in messages or [] if m.get("role") == "user"]
        obs = _text(users[-1].get("content")) if users else ""
        if obs.lstrip().startswith(SUMMARY_PROMPTS):
            return                                            # Terminus summarizing: neither progress nor stall
        self.stall = self.stall + 1 if (obs in self.seen or obs.startswith(PARSE_ERROR)) else 0
        self.seen.add(obs)
        said = [m for m in messages or [] if m.get("role") == "assistant"]
        keys = keystrokes_of(_text(said[-1].get("content"))) if said else None
        if keys is None:
            self.repeat = 0
        else:
            self.repeat = self.repeat + 1 if keys == self.last_keys else 1
        self.last_keys = keys

    def _next_id(self):
        with self.lock:
            self.n += 1
            return "r%05d" % self.n

    def ask(self, body):
        """One chat request -> (status, response dict). Writes the item, waits on its answer file (no clock)."""
        rid = self._next_id()
        if self.rules:
            self._new_trial(body.get("messages"))
        if self.rules and not self.ended:
            self._observe(body.get("messages"))
            if self.stall >= STALL_END:
                self.ended, self.ended_why = True, "stall"
            elif self.repeat >= REPEAT_END:
                self.ended, self.ended_why = True, "repeat"
        if self.ended:                                            # a stall, or the confirmation turn after an end
            self._log(rid, self.clock(), None, "ended_no_progress", why=self.ended_why)
            return 200, to_openai_response({"answer": TERMINUS_DONE, "done_reason": "stop"}, body.get("model") or "",
                                           rid)
        item = {"id": rid, "messages": to_window_messages(body.get("messages"))}
        if body.get("tools"):
            item["tools"] = body["tools"]                      # already in the OpenAI/ollama function schema
        path = os.path.join(self.items_dir, rid + ITEM)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
        out = os.path.join(self.items_dir, rid + OUT)
        t0 = self.clock()
        while True:
            if os.path.exists(out):
                try:
                    with open(out, encoding="utf-8") as f:
                        row = json.load(f)
                    break
                except (OSError, ValueError):
                    pass                                          # being written: whole on the next poll
            if os.path.exists(os.path.join(self.items_dir, CLOSE)):
                self._log(rid, t0, None, "window_closed")
                return 503, {"error": {"message": "the model window has closed", "type": "window_closed"}}
            self.sleep(POLL_S)
        if row.get("done_reason") == "length":
            # A length cutoff implies the context window was fully exhausted (num_predict == num_ctx). We must map this to a context_length_exceeded error to trigger upstream summarization/trimming logic.
            self._log(rid, t0, row, "context_full")
            return 400, {"error": {"message": "This model's maximum context length was reached "
                                              "(context_length_exceeded): prompt %s + answer %s tokens" % (
                                                  row.get("prompt_eval_count"), row.get("eval_count")),
                                   "type": "invalid_request_error", "code": "context_length_exceeded"}}
        self._log(rid, t0, row, None)
        if row.get("error"):
            return 502, {"error": {"message": "model window: %s" % row["error"][:300], "type": "window_error"}}
        self.loop_streak = self.loop_streak + 1 if row.get("done_reason") == "loop" else 0
        if self.rules and self.loop_streak >= LOOP_STREAK_END:
            self.ended, self.ended_why = True, "loop"
            self._log(rid, t0, None, "ended_no_progress", why="loop")
            return 200, to_openai_response(dict(row, answer=TERMINUS_DONE, done_reason="stop"),
                                           body.get("model") or "", rid)
        return 200, to_openai_response(row, body.get("model") or "", rid)

    def _log(self, rid, t0, row, err, why=None):
        if not self.log_path:
            return
        rec = {"id": rid, "relay_wall_s": round(self.clock() - t0, 2), "error": err}
        if why:
            rec["why"] = why
        if row is not None:
            rec.update({k: row.get(k) for k in ("done_reason", "eval_count", "prompt_eval_count", "gpu_j",
                                                "served_model")})
            if not (row.get("answer") or "").strip() and (row.get("thinking") or "").strip():
                rec["answer_from_thinking"] = True
            rec["window_wall_s"] = row.get("wall_s")             # the model's own time, apart from transport
        with self.lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def make_handler(relay):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body):
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):
                return self._send(200, {"object": "list", "data": [{"id": "lab-window", "object": "model"}]})
            self._send(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self._send(404, {"error": {"message": "not found"}})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, {"error": {"message": "bad json"}})
            status, resp = relay.ask(body)
            if status == 200 and body.get("stream"):
                return self._stream(resp)
            self._send(status, resp)

        def _stream(self, resp):
            """A client that asked for a stream gets the whole answer as one chunk, then [DONE]."""
            ch = resp["choices"][0]
            chunk = {"id": resp["id"], "object": "chat.completion.chunk", "created": resp["created"],
                     "model": resp["model"], "choices": [{"index": 0, "delta": ch["message"],
                                                          "finish_reason": ch["finish_reason"]}],
                     "usage": resp["usage"]}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\ndata: [DONE]\n\n")
    return Handler


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--items-dir", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")                 # local to Harbor only
    ap.add_argument("--log", default=None)
    ap.add_argument("--plain", action="store_true", help="an errand helper's relay: no Terminus trial rules")
    a = ap.parse_args(argv)
    os.makedirs(a.items_dir, exist_ok=True)
    srv = ThreadedServer((a.host, a.port), make_handler(Relay(a.items_dir, a.log, rules=not a.plain)))
    print("[relay] %s:%d -> %s" % (a.host, a.port, a.items_dir), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
