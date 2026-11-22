SUPERVISED LOCAL CODING JOB — HAI agent surface step 1, FIX 2: live token streaming through a per-thread delta sink
Mode: decomposed edit-test-review

Workspace: `orchestrator-real`, HEAD `2d3f233` (sync-to-live baseline 2026-10-05 — this is the LIVE code, including
tonight's coding-loop change in `ollama_client.chat`). Design: Z:\Documentation\LLM-Project\HAI-Agent-Surface-Design.md.
Your tool loop no longer has a 50-turn cap: it ends when you answer, when you repeat calls without new work, or at
this job's time budget. Every leaf runs the FULL `unit` suite before it lands and quotes the summary line.

History, so you do not repeat it: your step-1 commit `df55042` changed the signatures of `chat`, `_chat_once`,
`_chat_once_raw`, `_run_query`, `_run_query_core`, `_dispatch_mothership`, `_run_think_hard` to thread a `delta`
callback through. That broke 13 tests (8 test fakes rejecting the new keyword, 4 source-text tests on `def` lines,
1 import typo) and was not deployed. Those edits are NOT in HEAD now; only your test file
`tests/test_delta_streaming.py` remains (fix its line 17: `patch` lives in `unittest.mock`). Measured at HEAD in this
sandbox: 2556 ran, the ONLY error is that file's import; every other test passes. So any other failure is yours.

THE DESIGN — pinned: CHANGE NO EXISTING SIGNATURE AND NO EXISTING CALL
1. `ollama_client.py`: a per-thread delta sink on the context it already keeps (`_CALL_CONTEXT = threading.local()`).
   Add a context manager `delta_sink(fn)` that sets `_CALL_CONTEXT.delta_sink = fn` and restores the previous value on
   exit (mirror `call_context()`), and this helper:
   ```
   def _delta(payload):
       sink = getattr(_CALL_CONTEXT, "delta_sink", None)
       if sink:
           try:
               sink(payload)
           except Exception:
               pass
   ```
   In `_chat_once_raw`'s streaming loop, after each existing append, emit (these three lines are from your step 1):
   `_delta({"kind": "content", "text": msg["content"], "model": model})` after `content.append(...)`,
   `_delta({"kind": "thinking", "text": msg["thinking"], "model": model})` after `thinking.append(...)`,
   and for each call in `msg["tool_calls"]`: `_delta({"kind": "tool_call", "call": dict(_call), "model": model})`.
   If a caller already set `call_context(purpose=...)`, add `"source": <that purpose>` to the payload; otherwise none.
2. `app.py` `query_stream` — KEEP these pieces of your step 1 exactly: the thread-safe queue
   `dq = _queue.Queue()` (import `queue as _queue` if missing), `def push_delta(payload): dq.put(payload)`, the
   0.1 s poll that drains `dq` into `{"type": "delta", **d}` lines, and the final drain after the worker finishes,
   before the `result` line. CHANGE only how the worker starts, so the sink is set INSIDE the worker thread:
   ```
   def _work():
       with ollama_client.delta_sink(push_delta):
           return _run_query(q, set_status)
   inner = asyncio.create_task(asyncio.to_thread(_work))
   ```
   `_run_query` keeps its two arguments; `/query`, Absurd, overnight and every other caller see no deltas.
3. `CHAT_HTML` (the page script) — KEEP your step-1 handling: `liveEl`/`liveText`/`liveThink`/`liveSteps` state; on
   `event.type === 'delta'` with `kind === 'content'` create or extend the live bubble (`add('bot live', '')`),
   accumulate thinking and tool names; on the result, remove the live bubble (the result is the source of truth).
   ⚠ `CHAT_HTML` is a NON-raw Python string: a JavaScript `'\n'` must be written `'\\n'` in the Python source, as
   your step 1 did. The suite's CHAT_HTML check runs the extracted script through `node --check`.

Acceptance (tests in `tests/test_delta_streaming.py`, mocks from `unittest.mock`):
1. With a sink set, a faked stream emits one delta per chunk in order; with no sink nothing is emitted and the return
   value is byte-identical.
2. A sink that raises changes nothing.
3. The sink is per-thread: another thread's calls are not seen by this thread's sink, and it is restored on exit.
4. `/query/stream` yields delta lines before the `result` line; `meta`/`status`/`result` lines unchanged.
5. NO existing test is edited, and every existing test passes. If one fails, the change is wrong, not the test.
6. The CHAT_HTML `node --check` test passes.

Constraints: touch only `app.py`, `ollama_client.py`, `tests/test_delta_streaming.py`. Final report: the unit command
and its full summary (0 failures, 0 errors), the files changed, and the exact added test count.
