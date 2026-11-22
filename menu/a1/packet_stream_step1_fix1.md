SUPERVISED LOCAL CODING JOB — HAI agent surface step 1, FIX 1: the deploy gate went RED; redo it with no signature changes

Workspace: `orchestrator-real`, on top of your commit `df55042`. Live is UNCHANGED (rolled back; the gate on live code:
2549 ran, OK, 2 pinned skips). Design: Z:\Documentation\LLM-Project\HAI-Agent-Surface-Design.md.

What the REAL deploy gate (`/opt/arrstack/run-suite.sh`, the orchestrator image, 2026-10-04 23:50Z) said about
`df55042` — collected 2550 (pin 2557), FAILED failures=7 errors=6:
1. `tests/test_delta_streaming.py` did not import at all: line 17 `from unittest import mock, patch` —
   `ImportError: cannot import name 'patch' from 'unittest'` (`patch` lives in `unittest.mock`). Your sandbox reported
   these 8 as passing; the gate never collected them.
2. Every test that stubs `_run_query_core` broke: `_run_query` now passes `delta=` and the stubs' `fake_core()` reject
   it (`got an unexpected keyword argument 'delta'`) — test_hai_thinking (2), and the three `test_query_lifecycle`
   failures ("worker core never ran") are the SAME TypeError raised inside the worker thread. They are NOT
   pre-existing: on the untouched live code they pass.
3. `test_ctx_downgrade_logging` (2) and `test_cancellation...test_shield_keeps_thread_alive_after_generator_ends`
   broke on the changed `ollama_client` call path.
4. Source-text wiring tests assert the literal `def` lines you changed: test_diag
   (`def _run_query_core(q: Q, status=None, diag=None):`), test_gating, test_medical_critique (`nursing_mode=False):`,
   `def _run_think_hard(q, task_text, messages, relevant, context_compacted, status, nursing_mode=False):`).

The fix — pinned design: CHANGE NO EXISTING SIGNATURE AND NO EXISTING CALL
- `ollama_client` already keeps per-thread call context (`_CALL_CONTEXT = threading.local()`, `call_context()`).
  Add a delta SINK to it: e.g. `_CALL_CONTEXT.delta_sink` (a callable or None) and a context manager
  `delta_sink(fn)` that sets/restores it, mirroring `call_context()`. `_chat_once_raw` emits each chunk to the sink
  when one is set (wrapped so an exception in the sink is swallowed and never reaches the model call). Revert the
  `delta_callback` parameters you added to `chat()`, `_chat_once()` and `_chat_once_raw()`.
- `query_stream`'s worker sets the sink for ITS thread only (inside the function the worker thread runs), so
  `/query`, Absurd, overnight and every other caller are untouched and see no deltas. Revert the `delta=` parameters
  you threaded through `_run_query`, `_run_query_core`, `_dispatch_*` and `_run_think_hard`.
- The `source` label (`answer` / `critic` / `pass-N` …): where a caller already labels its calls with
  `call_context(purpose=...)`, use that label; otherwise send no label. Do not add parameters to get one.
- Keep your `events()` queue + 100 ms poll + `{"type":"delta"}` lines and your CHAT_HTML live-bubble handling.

Acceptance criteria (tests in `tests/test_delta_streaming.py`, imports from `unittest.mock`)
1. With a sink set, a faked stream emits one delta per chunk in order; with no sink, nothing is emitted and the
   return value is byte-identical.
2. A sink that raises changes nothing.
3. The sink is per-thread: a second thread's calls are not seen by the first thread's sink.
4. `/query/stream` yields delta lines before the `result` line; `meta`/`status`/`result` unchanged.
5. **Every test listed above passes again UNCHANGED** — do not edit test_hai_thinking, test_query_lifecycle,
   test_ctx_downgrade_logging, test_cancellation, test_diag, test_gating or test_medical_critique. If one still fails,
   your change is wrong, not the test.
6. The CHAT_HTML script check from your previous tests stays.

Before your final report run the FULL `unit` suite and list EVERY failure with its message. A failure is yours unless
you show it failing identically on the untouched baseline `b09a26d` in the same run — and name it in the report
either way. The deploy gate pins 2557 (2549 + your tests); state your exact added count.

Constraints: touch only `app.py`, `ollama_client.py`, `tests/test_delta_streaming.py`. Final report: the unit command
and its full summary, the files changed, the added test count.
