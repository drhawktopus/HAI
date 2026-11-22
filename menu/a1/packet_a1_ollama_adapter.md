SUPERVISED LOCAL CODING JOB — A1, step 2: a live ollama model behind `chat_fn`

Workspace: `sample` (as before; `a1_harness.py` and its 13 tests at commit 3962314 stay untouched and green). Its
`unit` command is `python -m unittest discover -s tests -t tests -v`. The supervising session ports the reviewed files
into the lab's hard-bench folder and wires the factory side; nothing here is deployed.

Objective
Write `a1_ollama.py` (standard library only) and `tests/test_a1_ollama.py`. It turns an ollama model into the
`chat_fn(messages, tools) -> {"content": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}` that
`a1_harness.run_task` already takes. Spec: Z:\Documentation\LLM-Project\34-A1-Agentic-Build-Family-Spec.md.

Interface (pin exactly)
- `make_chat_fn(base_url, model, think=False, options=None, num_ctx=None, log=None) -> chat_fn`
  - each call POSTs `{base_url}/api/chat` with `{"model", "messages", "tools", "stream": false, "think": think,
    "options": options plus num_ctx when given}`; `tools` converted from the harness's tool list to ollama's function
    schema (name, description, JSON-schema parameters);
  - maps `message.tool_calls[*].function` -> `{"name", "arguments"}` (arguments may arrive as a dict or a JSON string;
    accept both);
  - **thinking trap (lab doctrine):** a thinking model can leave `message.content` EMPTY and file its text under
    `message.thinking`. Return `content` from `message.content` only; never treat the thinking text as the answer.
    Record the thinking length separately;
  - NO request timeout and NO retry loop (lab doctrine: no arbitrary limits; the harness's no-progress rule ends a
    stuck run). An HTTP or connection error raises — the harness reports `ended: "error"`;
  - `log`, when given, is called once per request with `{"prompt_eval_count", "eval_count", "eval_duration",
    "prompt_eval_duration", "done_reason", "thinking_chars", "tool_calls"}` read from the response (missing fields
    -> None). `done_reason == "length"` must be visible there (a capped answer is void, not a fail).
- The harness's existing tool names and arguments are unchanged.

Acceptance criteria (tests/test_a1_ollama.py, with a fake ollama served by `http.server` on 127.0.0.1 in a thread —
no real ollama, no network beyond loopback)
1. A scripted fake that answers with a `write_file` tool call, then `finish` -> run through `a1_harness.run_task` on
   a synthetic exercise -> `passed` True; the request bodies carried `tools`, `stream: false` and the given `think`.
2. Tool-call `arguments` given as a JSON STRING are parsed into a dict.
3. A response with empty `content` and non-empty `thinking` -> `content` is "" and the log shows `thinking_chars` > 0.
4. `num_ctx` and `options` reach the request body; with neither given, no `options` key is required.
5. `log` receives `done_reason: "length"` when the fake returns it.
6. An HTTP 500 from the fake -> the call raises, and `run_task` ends with `ended == "error"`.

Constraints: standard library only. Touch only `a1_ollama.py` and `tests/test_a1_ollama.py`. Existing tests stay
green. Do not invent extra features; anything missing goes in the final report.

Stop and report (do not improvise) if a criterion contradicts the existing harness interface.

Final report: the `unit` test command and its full output, the files changed, any open question.
