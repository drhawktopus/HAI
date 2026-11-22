SUPERVISED LOCAL CODING JOB — HAI agent surface, step 1: live token streaming to the chat page

Workspace: `orchestrator-real` (verified identical to live for app.py / ollama_client.py / config.py at submission).
Design: Z:\Documentation\LLM-Project\HAI-Agent-Surface-Design.md (read § "the owner's requirements" 1 and § "Build order"
step 1 in full first). Nothing is deployed by you; the supervising session ports the reviewed diff and deploys it
through the orchestrator procedure.

Objective (the owner, 2026-10-04): *"see the live output as they're going not just the whole thing dropped at once
afterwards."* Today `/query/stream` (app.py `query_stream`) sends `meta`, `status` heartbeats, then ONE `result`.
Meanwhile `ollama_client._chat_once_raw` already reads ollama as a stream (thinking / content / tool_calls chunks) and
only accumulates them. Pass those chunks through to the page and render them live.

Pin these decisions
1. `ollama_client.chat(...)` gains an optional `delta_callback=None` (same pattern and safety as `status_callback`:
   wrapped so an exception in the callback is swallowed and NEVER reaches the model call). `_chat_once_raw` calls it per
   chunk with `{"kind": "thinking" | "content" | "tool_call", "text": <str> | "call": <dict>, "model": <model>}`.
   Accumulation, `split_leaked_thinking`, the return shape, cancellation and the no-`done`-chunk retry stay unchanged.
2. `_run_query` / `_run_query_core` thread the callback to the chat calls the same way `status` is threaded. Where one
   turn makes several model calls (tool loop, think-hard passes, critics), each delta carries a `source` label the
   caller already knows (e.g. `answer`, `critic`, `pass-N`) — no new semantics invented.
3. `query_stream`'s `events()` drains a thread-safe queue and yields `{"type": "delta", ...}` lines between the
   existing `status` lines; poll the queue at sub-second granularity (the current loop sleeps 1 s — deltas must not be
   batched into 1-second lumps). `meta`, `status` and the final `result` line keep their EXACT current shape; the
   `result` stays the source of truth (the page replaces the live bubble with the rendered result when it lands).
4. `/query` (non-streamed) is unchanged. Detach / `/query/reattach` behaviour is unchanged (a re-attached client gets
   the result as today; live deltas from before the re-attach are not replayed — say so in the final report).
5. The page (CHAT_HTML): a live assistant bubble fills as `content` deltas arrive; `thinking` deltas go into the existing
   🧠 thoughts area and `tool_call` deltas into the existing 🧭 steps area, honouring the View axis (normal = collapsed,
   thinking = thoughts open, verbose = all open, summary = answer only). ⚠ CHAT_HTML is a NON-RAW Python string: any
   `\n` you need inside the JavaScript must be written `\\n` in the Python source, or Python eats it and the whole page
   (login included) dies. Keep the JS change small.

Acceptance criteria (tests in `tests/`, run by the workspace's `unit` command; fake the ollama stream — no network)
1. `ollama_client.chat` with a fake streamed response calls `delta_callback` once per chunk, in order, with the right
   `kind`, and still returns exactly the same dict as without the callback.
2. A callback that raises does not change the return value or raise out of `chat`.
3. `/query/stream` with a stubbed `_run_query` that emits deltas yields `delta` lines, in order, BEFORE the `result`
   line, and the `meta` / `status` / `result` lines are byte-identical in shape to today's.
4. Cancellation still works mid-stream (existing cancel tests stay green).
5. The served page's script is unchanged except the delta handling: add a test that extracts the `<script>` from the
   rendered CHAT_HTML and asserts the new handler's key strings are present AND that the string contains no bare
   control characters (`chr(10)` inside a JS string literal) — the session will additionally run `node --check` on the
   served script before deploying.
6. The whole existing suite stays green.

Constraints: touch only `app.py`, `ollama_client.py` and new/changed files under `tests/`; standard library + what is
already imported; no new endpoint; no change to any other route's behaviour.

Stop and report (do not improvise) if: threading the callback requires changing a function's existing return shape;
or a criterion contradicts another.

Final report: the `unit` command and its full output (test counts — the deploy gate pins its count, so say exactly how
many tests you added), the files changed, and what a re-attached client sees.
