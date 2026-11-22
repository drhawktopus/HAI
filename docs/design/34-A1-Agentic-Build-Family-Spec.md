# #34 A1 — the AGENTIC BUILD family (Aider Polyglot + Terminal-Bench, all 6 languages): can a model BUILD, alone or with sub-agents

**⭐ CURRENT PROTOCOL: v2.1 (2026-10-05 09:00, a1_harness `a1fdbdcd`, suite 50 OK, deployed on all three boxes).**
the owner asked us to check for sandbagging before calling a ceiling (*"make sure we didn't sandbag or something before
calling it"*). The audit (`scratchpad\a1_sandbag_audit.py`) found three protocol defects in v2. None was a limit hit:
0 capped turns, and the 3.8 ended 34/34 by its own `finish`.

1. **The brief was half the spec.** We sent `.docs/instructions.md` only. Aider's benchmark sends `introduction.md` +
   `instructions.md` + `instructions.append.md` (read in Aider-AI/aider `benchmark/benchmark.py`). 16 of the 34
   exercises have an append file, and 14 of those carry the exact exception messages the tests assert. The 3.8 failed
   7 of those 16 versus 4 of the other 18.
2. **The agentic prompt said `run_tests` runs "the hidden tests".** It runs the visible half. A model told it just ran
   the grading tests finishes on green, which is what the 3-turn failures look like (scale-generator, transpose,
   robot-name). The "claims success without verification" reading below is CONFOUNDED by this prompt until v2.1
   re-measures it.
3. **The loop cutter ended on its first trip, with no word to the model.** Ornith lost 10 of 34 this way, 6 without
   ever writing a file, while it hunted for the withheld test file with repeated `run_tests` calls. The lab's own
   coding loop nudges before it ends.

v2.1 fixes all three:
- **The brief is Aider's.** Aider's addendum ("Don't change the names of existing functions or classes…") and its
  retry text ("The tests are correct, don't try and change them…") are used verbatim.
- **The prompt is honest.** It says the test file is not in the workspace, `run_tests` runs the visible subset, and
  the grade runs all.
- **The cutter nudges once.** On its first trip the model is told why it is stuck; only a second trip ends the
  exercise.

**What Inspect's agent loop does that ours does not — the gap list (2026-10-15, CLAUDE.md row "adopt part of an
engine").** A1 keeps its own loop (`a1_harness`, the thin lab layer of 10-05: our tools, the no-progress rule, the
finish gate, review, delegation, checkpoints) and sends model calls through Inspect. Against Inspect 0.3.276's
`react()` agent:

| `react()` feature | ours |
|---|---|
| `submit` tool | `finish` — same role |
| `on_continue` (reply with no tool call) | Inspect's own continue text since v2.2 |
| `compaction` near the context limit | **Inspect's summary compaction on all four factories since 10-15** (§ Context compaction below): ONE call path, `a1\a1_chat.py` (`CompactingChat`), used by rigby's `a1_task` and the GPU boxes' `a1_order`, which now call ollama through the `lab` provider's foundry backend under `{FOUNDRY_ROOT}\venv-inspect`. Proven not to change what ollama receives: `test_a1_order.OneCallPath` (same options incl. sampling keys Inspect cannot name, num_ctx, num_predict, the effort level verbatim, byte-equal tool schemas, same conversation; tool results additionally carry ollama's `tool_name` — measured to render
the SAME prompt on Softserve qwen3:8b and Mothership qwen3.8:27b: same count, read at the cached speed, against a
control that re-read visibly) |
| `truncation` (overflow fallback) | `context_full` ends the exercise, graded (only after a forced compaction failed) |
| `attempts` (re-submit when wrong) | deliberately none: one finish, graded on hidden tests (Aider mode keeps Aider's 2 tries) |
| `retry_refusals` | not needed: the lab provider never reports a content-filter refusal |
| sample limits (messages / tokens / time) | deliberately none (doctrine 13a): the no-progress rule and the measured `turn_cap` end work instead |
| tool errors returned to the model | yes — unknown tools and unparseable calls are answered, not raised |
| `approval` / `review` policies | not applicable: tools act only inside the exercise workspace |
| full transcripts of every model call | rigby: Inspect's log; GPU boxes: `a1_order`'s own transcripts (the calls go through Inspect's model outside an eval, so no Inspect log there) |

**Context compaction (2026-10-15, the owner: "when you hit context limit you compact and keep going").** Inspect's
`CompactionSummary` (its default prompt), wired as `react()` wires it: `compact_input` before each call,
`record_output` after, a forced compaction and one retry when the server refuses an over-context request. The prefix
every compacted view keeps is the system prompt + the brief. Threshold = the served context minus the order's
`turn_cap` (room for one whole reply; rigby 65,536 − 20,480 = 45,056), else Inspect's default 90 %. Rows carry
`compactions` [{at_message, trigger}]. Token counts: BOTH paths calibrate on the server's own count after the first
reply — rigby on llama-server's prompt_n + cache_n, the GPU boxes on ollama's prompt_eval_count, which is the FULL
prompt whether or not the cache was reused (measured 2026-10-15, Softserve qwen3:8b: 1,355 cold and cached alike, read
time 340 → 16 ms; `scratchpad` probe `tool_name_probe.py`). Before the first reply, and for messages added since,
Inspect estimates (tiktoken o200k_base + 10 %). **Measured 2026-10-15** (`lab-engine\research\split-inference\a1_token_estimate.py`, rigby's Qwen3.6, 28
calls with a true read+cached count): estimate / true = min 0.83, median 0.99, max 1.02 — the low end on SMALL inputs
(~200 tokens of chat-template tool preamble the estimate cannot see), within 1–2 % on long conversations; the room
compaction keeps (10 % or the turn cap) covers it. Two instrument faults before that number, both the same class
(an instrument that saw less than the model): a hand-rolled text join missed tool-call arguments, and `read_eval_log`
without `resolve_attachments=True` counted attachment placeholders instead of the content.
**Live proof on rigby** (`a1v22-rigby-compact-decoy.20261015`, threshold pulled to 4,096): wordy compacted 10 times
in 21 turns and carried its work to 24/25 hidden, graded, not void. A `turn_cap` must leave room in the context (refused at Tolkien's door and by both runners).
Before it, a long session either hit HTTP 400 on rigby (void, re-run) or was silently
cut by ollama on the GPU boxes (oldest messages — the brief — dropped, no summary).

**Runaway guard — order field `turn_cap` (2026-10-15, the owner: "Yes we want the runaway guard").** A run-away turn is a
single reply that deliberates for tens of thousands of tokens without calling a tool until the context is full (rigby
react 31,948 / paasio 25,447 tokens, 10-14). The order's `turn_cap` is sent as the per-call output cap (rigby:
llama-server `max_tokens`; GPU path: ollama `num_predict`). A reply cut there without a tool call is discarded, and
the model is told to call a tool (`RUNAWAY_TEXT`). The first cut nudges; a second ends the exercise as
`ended: "runaway"`, graded on the workspace. A guard cut is the model's result, not a void cap hit: rows carry
`runaway_cuts` and `turn_cap`, and rigby's `cap_hits` excludes them. The value comes from a measurement (doctrine 13a):
on rigby (A3B, thinking off) every acting turn was ≤ 14,888 tokens and every runaway ≥ 25,447, so rigby orders set
**20,480**. **GPU plans set NONE — measured 2026-10-15** (`lab-engine\research\split-inference\a1_turn_lengths_gpu.py`, 10,393
GPU A1 calls): the thinking models have NO gap between acting and running away — longest call that went on to act:
qwen3.8:27b thinking-on 46,111, at xhigh 71,288, Ornith 119,714 (its no-tool length cuts start at 109,610: they
overlap), qwen3:8b thinking-on 18,308. Any cap would cut real thinking (a sandbag); compaction keeps them inside the
context and a fill AT the tested ceiling is already scored as the model's own result (`a1_validity.at_ceiling`). Measurement, design and validation: [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] row 10-13 14:3x.

**v2.1 results:**
- **qwen3.8:27b agentic: 25/34 (74 %)**, versus v2's 23/34 on the same exercises. It gained bowling,
  scale-generator, sgf-parsing and transpose, and lost poker and rest-api.
- Two of the gains (scale-generator, transpose) were v2's 3-turn "green on the visible half, finish" failures. They
  now pass after more turns (10 and 6), which is the honest-prompt effect.
- The two losses with one run per protocol mean the net +2 is within run-to-run noise: price it as "mid-70s %",
  not a jump.
- 0 nudges were used and 0 caps hit.
- 7 of the 9 failures miss by one or two hidden tests (real edge cases), with no harness cause left that the audit
  can find.
- The model called two tools that do not exist (`edit_file`, `grep_files`); they were answered "unknown tool".

**A1-hai — the model with HAI's PRODUCTION toolset and its best evidenced settings (2026-10-05 12:12).** the owner: the
question is the MODEL's ability being sandbagged, by anything, not just the prompt.

The 3.8's v2.1 failures were all hidden edge cases it never probed: it could only run the visible half, and it
reached for tools that did not exist (`edit_file`, `grep_files`). Two levers came out of that:
- **Tools.** The new mode `agentic-hai` (a1_harness `1d1a5961`, A1 suite 56 OK) gives the model HAI's coding-runner
  tools: same names, same descriptions verbatim, same contracts (`coding_read` returns a sha256; `coding_patch` is
  an exact, sha-guarded, unique-match edit; `coding_search`; `coding_create`; `coding_file`). There is no arbitrary
  shell, as in production. `coding_test` runs the visible half plus every `test_*.py` the model writes, in a capped
  child. An own-test file that names the answer key or leaves the workspace is refused and counted.
- **Settings — "best" = top quality AND speed for the TASK TYPE** (the owner 2026-10-05; [[34-CHARTER]] § "How results
  shape action": cost per solved answer, failures charged to the solves). Vendor value or lab measurement, whichever
  is better, never a guess. The menu has no agentic-build family. So where the families nearest A1 (tool, work-w1,
  coding) **split on thinking, A1 measures both arms** and `submit_a1.py --settings=best` refuses to submit without
  `--think=on|off`. Every plan carries its evidence; menu numbers are pass rate (n in the plan).

| lead | thinking | temperature | evidence |
|---|---|---|---|
| qwen3.8:27b | **both arms** | 0.6 | OFF wins coding-f1 1.000 vs 0.803, f2 0.980 vs 0.887 and tool 1.000 vs 0.833, 3-4× faster; ON wins work-w1 0.985 vs 0.907. 0.6 = Qwen precise-coding preset; the menu at 0.6 matches or beats 0.0 in both modes |
| qwen3:8b (BigHonker) | **both arms**, ON first | ON 0.6 / OFF 0.7 | ON wins the multi-step families (tool 0.976 vs 0.931, w1 0.70 vs 0.57); OFF wins single-answer coding (f2 0.921 vs 0.696) and is far cheaper (w1 ~3 s vs ~89 s per solve). 0.6 / 0.7 = the card's two presets; OFF temperature is flat in the menu |
| Ornith 1.5 | on | 0.6 | vendor card only (no menu rows) |
| Nemotron 3 Nano 30B | on | 0.6 | vendor card only (the NVIDIA tool-call value; no menu rows) |

  Results are read as **solved / 34 AND wall time per solved exercise** per arm; the better arm per lead is its A1
  "best", recorded per task type so triage can choose thinking per turn (the lead stays resident, and thinking is a
  per-request setting, not a model swap — [[HAI-Agent-Surface-Design]]). The first table, which picked qwen3:8b's
  thinking from single-answer coding, is archived in `Archive\docs-corrected-20261005.md`.

**Proof the old modes are untouched:** the v2.1 harness `a1fdbdcd` and `1d1a5961` were run side by side with one
scripted model. Every message shown matched (agentic 8/8 incl. the nudge and a failed write, aider 2/2), and so did
the rows. Queued v2.1 orders therefore behave as labelled.

**Submitted:** `a1v21-py-agentic-hai-{q38,ornith,nemotron30}-best.20261005-1212` (Mothership) and
`…-bi-q3-8b-best…` (BigHonker, behind its v2.1 think-on run).

**Every queued A1 run is pinned to BEST settings (the owner 2026-10-05 12:25: *"make sure that any submitted runs … have
the models pinned to their best"*).**
- Pulled at the desk (they were on tag settings): Nemotron-30 agentic v2.1 and the three v2.1 Aider runs. Mothership
  had already claimed them, so each is a factory cancel control that its runner collects between orders, before the
  next pick (sent 2026-10-05 ~13:15, landing when Ornith v2.1 finishes).
- Stopped by slip: BigHonker's qwen3:8b thinking-ON run.
- Re-queued with `--settings=best`:
  - `a1v21-py-agentic-{q38,nemotron30}-best` (minimal tools — the clean control for the tools lever, one variable
    vs `agentic-hai-*-best`);
  - `a1v21-py-aider-{q38,ornith,nemotron30}-best`;
  - `a1v21-py-agentic-bi-q3-8b-best`.
- Ornith's v2.1 agentic run (in flight) was left to finish: its tag values ARE its best (0.6, thinking on), so the
  rerun would be identical work.
- The 3.8's finished v2.1 run (25/34) was at the unmeasured tag 1.0. It stays as a "tag settings" price; its
  best-settings twin is queued.

**Adopt before building, from here on (the owner 2026-10-05: *"I am sure we aren't the first people to try to benchmark
models in a local home-based lab"*; CLAUDE.md "Don't reinvent the wheel").** The hand-built harness needed five
rebuilds in two days, each found after hours of live runs. Two existing tools cover most of it: Aider's own
`benchmark.py` (the published Polyglot protocol, Docker, `--model ollama/<tag>`) and Inspect AI (UK AISI; agent
loops, sandboxes, deterministic scorers, full logs, local ollama). **Decided:** the published Aider comparison
comes from Aider's own harness, run as a factory order (rigby has Docker); our Aider-mode arms are skipped
(cancelled at the desk 18:20). **Next:** evaluate both against CURRENT releases before any new A1 arm is built.

**Inspect AI evaluated hands-on, 2026-10-05** (0.3.276, released 2026-10-02; venv `E:\tools\inspect\venv` on
Softserve; probes `E:\tools\inspect\probe_inspect*.py`, logs beside them; qwen3:8b on Softserve's idle card):

| need | measured |
|---|---|
| native tool calls through ollama | ✅ a custom tool was called without `emulate_tools`, and its result returned to the model |
| full transcripts | ✅ every message (system, user, assistant, tool call, tool result) plus timing and token usage, read back through `read_eval_log` |
| context per box | ❌ through its ollama provider (the OpenAI-style endpoint) a request's `num_ctx` is ignored: the model loaded at Softserve's default 8,192 with and without `extra_body`. A Modelfile tag with `PARAMETER num_ctx` works (loaded at 17,408, fully on card) |
| thinking off | ❌ `extra_body think=false` and `reasoning_effort=none` were both ignored (reasoning present in all four runs) |

**Decision (the thin lab layer):** Inspect is the engine for the A1 arms still to be built (behaviour, review,
team). The model calls go through an Inspect custom model provider that wraps the foundry's own
`infer_ollama._chat`, which passes `think`, `num_ctx` and the options natively and carries the residency guards
and visibility tracking. That makes one call path, and the two ❌ rows are answered by the lab's own call rather than
by workarounds. Aider's own `benchmark.py` (rigby, Docker) provides the published comparison.

**The `lab` Inspect provider: BUILT and accepted 2026-10-05.**
- Code: `hard-bench\inspect_lab\lab_provider.py`. Use `--model lab/<tag> -M num_ctx=<tested> -M think=<on|off>`;
  both are required and it refuses without them.
- Live acceptance (`probe_lab.py`, Softserve qwen3:8b), all PASS:
  - loaded context = the plan's 17,408, fully on card, no baked tag needed;
  - think=off gives 0 reasoning characters; think=on gives 1,329;
  - native tool call with its result returned;
  - refuses a run with `num_ctx` missing.
- Offline pins: `test_lab_provider.py`, 5 OK.
- It also prints the foundry's `[beat]`, so a run is visible to the stall guard as an order.

**Build plan for rigby and the Fellowship (started 2026-10-05 late, the owner: "get the Rigby and fellowship stuff build
started").** What they wait on is code, not card time. rigby's llama-server (`70adb1b`) already does tool calling
(`--jinja` on by default) and `--reasoning on|off`. Its context (`-c`) and thinking are fixed at server LAUNCH,
unlike ollama's per-request settings.
- **R1:** `tolkien_llm.chat(messages, tools)`, a streamed, silence-ended call that assembles tool calls. One call
  path for Tolkien, as `infer_ollama._chat` is for the GPU boxes.
- **R2:** a `tolkien` backend in the `lab` Inspect provider. It refuses unless the running server's context and
  reasoning match the plan (read from llama-server `/props`).
- **R3:** a Tolkien order that runs an Inspect A1 eval against rigby's own model on rigby (same box; nothing calls
  up).
- **R4:** `inspect-ai` in Tolkien's image, through `run-tolkien-suite.sh` and `fleet_deploy_tolkien.py`.
- **Then `delegate`** (A1-team, S10): a lead hands a piece DOWN, placed like an order.

**Progress, 2026-10-06 ~01:00:**
- **R1 built:** `tolkien_llm.chat()` returns the same shape as `infer_ollama._chat`. Deployed with R3/R4 on
  2026-10-06 01:39 (below).
- **R2 built:** the `lab` provider has a `tolkien` backend: OpenAI-format messages for llama-server, and it refuses
  when the server's `/props` context differs from the plan. `infer_ollama` is imported only by the foundry backend.
  Offline tests 8 OK.
- **A1 on Inspect, without a second loop:** `inspect_lab\a1_task.py`. Each sample runs `a1_harness.run_task`
  (same brief, tools, no-progress rule, hidden grade); only the model call goes through Inspect's model (the `lab`
  provider). The score carries the harness row. Mock-model controls: the reference scores CORRECT, a model that
  never writes scores INCORRECT, both graded (`test_a1_task.py`, 2 OK). Every arm still to come builds on this
  task.
- **R3 built (2026-10-06): A1 is an AGENTIC PAYLOAD of Tolkien's existing `llm` kind, not a new order kind.**
  - Payload: `/ssdshared/tabfoundry-jobs/payloads/a1/a1_payload.py`. Beside it in that folder:
    `a1_harness.py` `5274e5d2` (the GPU boxes' copy), `a1_task.py`, `lab_provider.py`, `control/control-add`, and the
    archived task set (byte-identical, 34 exercises).
  - `tolkien_llm.is_agentic(mod)` recognises it. `load_scorer` runs `self_test()` before any model time: on the
    control and all 34 exercises, the stub must fail the hidden tests and the reference must pass them.
  - Each item is a one-sample Inspect eval against the order's llama-server. Everything else is the code every
    `llm` order already runs: control first, per-item checkpoint and resume, stop slip, hung-engine hold, at-cap
    brake, tile.
  - Each item's Inspect log ships to `outbox\inspect\<order>\`.
  - The order must state `reasoning` on|off; otherwise it is refused before the server loads. `a1_mode` defaults to
    `agentic-hai`.
  - Context-filling turns are counted as `cap_hits`, and such an item is `finish_reason: length`.
  - `tolkien_llm.chat` gained `live`, so a long turn keeps the order's beat (the watchdog's limit is 300 s).
- **Found while building R3:** Inspect memoizes a model instance across evals, and the rigby lane outlives orders. So
  the `lab` provider's Tolkien backend now checks the served context on every call (one `/props` read), not at
  construction.
- **Tests:**

  | Suite | Where | Result |
  |---|---|---|
  | `tests/test_llm.py` | Tolkien's image | 86 OK (+12) |
  | Inspect side (`test_a1_payload` 9, `test_a1_task` 2, `test_lab_provider` 8) | Windows | 19 OK |
  | Inspect side, same 19 | Linux probe image | 19 OK, including a run from a worker thread, as the rigby lane runs it |
  | A1 harness suite | Linux | 55 OK; first run on Linux, memory cap via `RLIMIT_AS` |

  Gate pin: 518 → 530 (`run-tolkien-suite.sh`, backup `.bak-20261006-a1r3`).
- **R4:** `inspect-ai==0.3.276` is in Tolkien's `requirements.txt`. It resolves with httpx 0.28.1, pillow 12.3.0 and
  requests 2.34.2 unchanged. Cost: the image grows from 266 MB to 672 MB; rigby has 762 GB free.
- **Deployed 2026-10-06 01:39** via `fleet_deploy.py --only tolkien tolkien_llm.py tests/test_llm.py requirements.txt`:
  - gate: 530 tests OK, matching the pin;
  - both containers recreated, bytes verified;
  - checked in the live container: `inspect_ai` imports, `/app/tolkien_llm.py` has `chat(live=)` and `is_agentic`.
- **Harness v2.2 (`8de0a6aa`, 2026-10-06): a reply with no tool call gets Inspect's react continue message.**
  - Text: *"Please proceed to the next step using your best judgement. If you believe you have completed the task,
    please call the `finish()` tool."* (Inspect's wording; `finish` for its `submit`).
  - v2.1 re-sent the conversation ending on the model's own message instead. ollama silently turns that into a
    prefill continuation; llama-server refuses two trailing assistant messages with HTTP 400. That is how rigby's
    first order failed: Qwen2.5-Coder-3B wrote its tool call as fenced JSON, which is no native call on any backend.
  - **The v2.1 branch was live in measured GPU rows** (counted on-box from the saved conversations):

    | Run | Exercises hit | Times |
    |---|---|---|
    | 3.8 HAI-best | 6 of 34 | 9 |
    | qwen3:8b HAI, think on | 9 of 34 | 35 |
    | Ornith v2.1 | 1 of 24 | 1 |

  - **v2.1 is retired on every lane** (the owner 2026-10-07: "why are we bothering finishing out 2.1 instead of moving to
    2.2?"). The queued v2.1 cells were stopped/cancelled by slip and resubmitted on v2.2 as spot tests:
    `a1v22-py-agentic-hai-q38-best-thoff-spot`, `a1v22-py-agentic-q38-best-thon-spot` (minimal tools),
    `a1v22-py-agentic-hai-nemotron30-best-spot` (all `.20261007-0752`). The stopped v2.1 Nemotron full-set run's 7
    exercises shipped as partial output (v2.1 rows; not pooled with v2.2).
  - Every row carries `harness_sha256`, so the two protocols cannot be pooled by accident.
- **Two traps in a long-lived order taker**, both fixed in `a1_payload.py` (`3df3e2aa`):
  - **Cached modules.** Each load drops `a1_harness` / `a1_task` / `lab_provider` from `sys.modules`; otherwise
    order 2 would run order 1's harness while its `harness_sha256` named the new file.
  - **Memoized providers.** The model is built with `get_model(..., memoize=False)`. With Inspect's default
    memoization, order 2 (same model and args) got order 1's provider instance: 0 calls to its own server and a
    stop slip that never connected. Proven by a positive control against a memoizing copy.
  - Pinned by the two-orders-in-one-process test (`test_a1_payload`, 21 OK on Windows and Linux).
- **First rigby order, `a1-rigby-coder3b-hai-off.20261006-0141`: stopped as a pipeline fault** (the v2.1 HTTP 400
  above; 3 rows shipped flagged `stopped`). **Re-submitted on v2.2 as `a1v22-rigby-coder3b-hai-off.20261006-0155`**
  (spec template in `hard-bench\inspect_lab\orders\`). **Result: VOID** - every tool reached the model with no parameters (a1_task bridge bug, fixed with a seam test against the GPU orders' schema; the void reading is archived in `Archive\docs-corrected-20261006.md`). The 7B and MoE orders that followed were void for the same reason and were stopped.
  The Coder-3B order's settings were:
  - model Qwen2.5-Coder-3B, the S10 fast-lead candidate;
  - `agentic-hai` mode, all 34 exercises;
  - ctx **65,536** (measured 2026-10-09 — § "Rigby's A1 context"; the base order `inspect_lab\orders\
    a1v22-rigby-moe-hai-off.tjob.json` carries it; 32,768 until then, which agentic turns overran), reasoning off;
  - temperature 0.0, from `best_settings.resolve(box=rigby)`: measured, best-or-tied, quickest per solve.

**Aider benchmark: the RUNNING setup (2026-10-06).**
- **Where:** an order on the box whose model is measured, payload `{FOUNDRY_ROOT}\workspace\aider\aider_order.py`
  (`hard-bench\aider\`, queued by `submit_aider.py`).
- **How:** the model answers from that box's ollama. The model-written code runs in Aider's own benchmark container
  on **Sundae** (`/opt/aider-bench`, image `aider-benchmark`, memory capped at 4 GB). Sundae is a VM on Softserve:
  same physical box, Docker already present, and admitted by Softserve's ollama firewall rule. Aider refuses to run
  unvetted code outside a container.
- **Measured before queuing:**
  - `num_ctx` reaches ollama (Nemotron 4B loaded at 261,120, fully on card);
  - `think` reaches ollama through litellm 1.82.3 (`think=false` gave 0 reasoning characters, `true` gave 170);
  - Aider's transcript does not show thinking (litellm moves it to `reasoning_content`), so judge thinking from
    the model's side, never from the transcript;
  - the commit is recorded as `5dc9490-dirty` (only file modes and our settings file differ; content identical).
- **Request timeout (class 13a):** Aider's `models.py` defaults every request to `timeout=600`, applied only when
  `extra_params` lacks one. Aider's benchmark does not stream, so 600 s caps the WHOLE answer: the first Nemotron
  thinking-on run hit `litellm.Timeout ... 600.0 seconds` five times in 40 min, and each retry restarted the answer.
  The payload now measures the model's decode rate on the box before the run, then writes
  `timeout = 2 x num_ctx / tok_s + 60` into `extra_params`. That is twice the time needed to fill the whole context,
  so the model's own context limit ends an answer first. Every `litellm.Timeout` line is counted into
  `energy.json` as `timeout_hits`, along with `tok_s` and `request_timeout_s`. **A run with `timeout_hits > 0` is
  void.** First measured value: Nemotron 4B on Softserve, 117.6 tok/s, giving 4,499 s.
- **A stop slip must end the Sundae container too.** A stop kills the payload's local `ssh`. With plain `ssh`, the
  remote `docker run` survives that kill: the stopped Nemotron arm's container kept answering Softserve's ollama for
  an hour, and held the card so `card_quiet` blocked the next order. A live decoy (busybox `sleep` through ssh,
  local ssh killed) tested five arms:

  | Arm | Container after the local ssh was killed |
  |---|---|
  | plain ssh | orphaned |
  | `-tt` | orphaned |
  | `-tt` + remote `trap` | orphaned |
  | plain ssh + remote `trap` | orphaned |
  | **`ssh -tt` + `docker run --init`** | **died** |

  `-tt` makes sshd hang up the session. `--init` puts tini at PID 1, and tini acts on the signal; a bare PID 1
  ignores it. The payload uses both since `fe62e82a`.
- **Queued 2026-10-06 00:11 on Softserve:** Nemotron 3 Nano 4B and MiniCPM5-1B (no menu rows, so both thinking
  arms) and qwen3:8b (thinking off, the menu's coding families). Temperature is Aider's protocol default.

**Aider benchmark on rigby (2026-10-05):**
- Location: `/home/owner/aider-bench` (Aider `5dc9490`, 2026-05-22; `/opt/arrstack` is not writable for owner).
- Image `aider-benchmark` (7.31 GB).
- Task set: our archived `polyglot-benchmark@7e0611e7` (zip sha256 `ed96df1d…` = manifest, 34 Python exercises)
  at `tmp.benchmarks/polyglot-benchmark`.
- Model settings: `lab-model-settings.yml` (`extra_params` num_ctx / think).
- **Placement, corrected the same evening:** the benchmark runs ON THE BOX WHOSE MODEL IT MEASURES, as a factory
  order (Python needs no Docker; a venv beside A1's payload), calling that box's own ollama. rigby's copy measures
  rigby's own models only. A first attempt ran it on rigby against Softserve's ollama, which is an upward call
  (the owner: *"Rigby should be calling down to the tablets, not up"*). Softserve's firewall rule "Ollama LAN
  (Sundae+HA)" refused it, rightly, and it stays as it is.

**Adoption criteria** (what the project needs, so the evaluation is judged against them):
1. **Visible as factory orders** (doctrine 12e), with residency guards and stop slips.
2. **The tests decide**: deterministic scoring, never a model judge (charter deterministic-first; #35 layer C).
3. **Full transcripts, both sides, exportable**: #35's training and evaluation data. Its recon's patterns are
   missed objective 29 %, re-derived plan 17 %, unverified claims 13 %; first-pass behaviour is the deficit.
4. **Any ollama tag**, including a future LoRA adapter, so base-vs-adapter is a plain comparison (#35's NI-0
   evaluation).
5. **HAI's own tool contracts** in the measured loop (the one-loop rule, [[HAI-Agent-Surface-Design]]).
6. **Feeds Phase 7 (the scheduling policy, #34's finish line).** Every row carries box, model tag, settings
   (thinking, temperature, context), task type, wall, **energy (board joules, through the SAME `PowerMeter` the
   menu uses — A1 recorded none until 2026-10-05)**, tokens, load and prompt-read time, and outcome. Readable per
   box AND across the fleet (charter § "How results shape action"); acceptable quality judged per factory
   (one-shot on GPU boxes; the Fellowship multi-attempt only with a production selector). In A1-team: each
   delegated piece priced per worker, plus the lead's own overhead and its wait, apart. Results flow into the
   priced menu and `best_settings.py`.
7. **Feeds #35 as LAB EXPERIENCE** (NI proposition §8: state, action, outcome, recovery — not bare prompt/answer
   pairs). STATE = the brief, the plan, settings, the harness and task-set versions, timestamps. ACTION = the full
   transcript. OUTCOME = deterministic test results with the failing tests named. RECOVERY = what it did after a
   failure or a correction (the behaviour arms; and repeated non-improving rewrites, like the 2026-10-05 qwen3:8b
   loop, are recovery failures worth keeping).
8. **Serves #35's evaluation needs** (proposition §17-19):
   - general capability retention: A1 is the coding check when a trained adapter is compared with its base;
   - objective preservation and plan respect: behaviour arms, plus Terminal-Bench for "missed the actual
     objective";
   - layered ground truth: deterministic tests first; any model judgment keeps its provenance and is never
     truth by itself (no circular self-confirmation);
   - chronology: every row stamped with date, model tag, harness version and task-set commit, so splits by time
     and lineage are possible. Public sets may be in a model's training data, so treat them as a strong signal,
     not gospel.

   Where #34 and #35 converge is measured capability routing.

**Progress rule + energy + stamps, 2026-10-05 ~20:00 (a1_harness `5274e5d2`, a1_order `fc4216c7`, all three boxes
and HAI's `sample`).**
- **Progress now means the workspace reaches a state it has NEVER been in, or the model makes an observation (read,
  search, test run) it has not made on this state.** Rewriting back to tried code and re-testing it is not
  progress; the existing `NO_PROGRESS_STALL` (8) cuts such a loop.
- Why: qwen3:8b spent 4.6 h on one exercise rewriting and re-testing, because every edit counted as progress.
- Validated on 207 replayed exercises (v2 + v2.1): it cuts **0 of 118 solves**, whose longest idle run is 4, and
  ends 23 of 89 failures early, saving 131 turns.
- Two simpler rules failed that check, and the evidence is kept. "No better score for 8 turns" would have cut real
  solves that needed up to 13 flat turns. "New state only" would have cut 5 Ornith solves that explore by reading.
- Otherwise the side-by-side with `5dacc275` is identical in all three modes.
- a1_order adds per-exercise energy (`gpu_j`, through the menu's `PowerMeter`, with an idle baseline) and per-row
  `at`/`t0`/`t1`, plus `harness_sha256` and `task_set` on the run (criteria 6-8 above). Suite 65 OK.
- Runs already in flight keep the harness they loaded at start.

**Recording-only harness change 2026-10-05 ~16:30 (a1_harness `5dacc275`, a1_order `ec175a2b`, all three boxes):**
each row names its failing tests and why (`failing`); each transcript keeps the whole conversation (tool results
included) and the full final grade output. Proven identical to `1d1a5961` from the model's side (every message, all
three modes, one scripted model) and on the scored fields. Orders queued before it carry `1d1a5961` in their
protocol label and run `5dacc275`: same measurement, richer record. Settings now resolve through the lab's one
resolver, `hard-bench\best_settings.py` (LAB-TOOL-GAPS #1); it reproduces every arm below exactly.

**Thinking arms added 2026-10-05 15:14** (the table above): `a1v21-py-agentic{,-hai}-q38-best-thoff` (Mothership; the
queued `…-q38-best` pair are the ON arms), and on BigHonker `a1v21-py-agentic-hai-bi-q3-8b-best-thon`,
`a1v21-py-agentic-bi-q3-8b-best-thon`, then `…-thoff` LAST (the earlier `agentic-bi-q3-8b-best` was pulled at the desk
before it started and re-queued as that last arm; the running `agentic-hai-bi-q3-8b-best` is the HAI-tools OFF arm).

**Reading the arms:** A1-hai-best vs v2.1 changes tools AND settings. It is the promising arm (doctrine: promising
first). If it moves a lot, a separating control (hai at tag settings) prices the two levers apart.

**v2 results below are priced as "v2, under-specified".** They are not the A1 number. Ornith's v2 Aider run was
stopped at 25/34, and both Nemotron-30 v2 orders were pulled before they started. Running now (Mothership,
submitted 09:01): `a1v21-py-agentic-{q38,ornith,nemotron30}`, then `a1v21-py-aider-{q38,ornith,nemotron30}`. Each
plan's `protocol` field carries the harness hash, read at submit time.

**Status 2026-10-04 evening: harness BUILT by the 3.8 via HAI and verified** — `hard-bench\a1\a1_harness.py` + 13 tests
(2 HAI runs: `abr-1791149465-ab33c3` build, `abr-1791150449-662c4e` fix for hyphenated exercise names found by the
real-exercise control); `a1\controls.py` on all 34 Python exercises: reference solutions pass 34/34, stubs graded and
fail 34/34.

**Status 2026-10-04 night: first live runs, and a harness flaw they exposed.** A1 runs as a factory order
(`a1_order.py`, `submit_a1.py --box=`), deployed on Mothership, BigHonker and Softserve. The qwen3.8:27b solved **33/34**
(0 cap hits, median 6.5 turns, 55 tok/s, 118 min; the one fail, `forth`, is a real infinite loop in its code, replayed
locally) — but **under a FULL-FEEDBACK condition, not the A1 condition**: the model's `run_tests` ran the GRADING tests
and returned their full output, so nothing was hidden and the score saturates. Label it "full feedback" wherever it is
cited; it is not comparable to published Aider numbers. BigHonker's nemotron-3-nano:4b under the same condition
solved **0/34** (control passed, 0 cap hits, 89 tok/s, 79 min). How it failed, measured from its 34 transcripts:
14 ended looping without progress, 12 called finish on wrong code, 6 left code that does not import (5 plain
SyntaxErrors — unterminated strings, unclosed brackets — and 1 file of literal `\n` text), 2 ended on a loop crash
whose cause the harness did not record. 5 exercises it never wrote a file, 7 it never ran the tests, 68 of its 423
turns were empty; 7 exercises passed some tests (best: transpose 9/12). The harness labelled the 8 import/crash rows
`error`; since 2026-10-05 non-importing or hanging code is a fail (`graded: "crashed"`) and every `error` row records
its cause. Its queued qwen3:8b / qwen3.5:9b orders were cancelled at the desk. **Fixed 2026-10-05 (A1 protocol v2):** HAI
`abr-1791169694-b1c2ad` (`a1\packet_a1_harness_fix2b.md`; the first attempt loaded test code IN the harness process
and was set aside) — every test run happens in a child interpreter (`CHILD`); sorted test ids, visible = even
positions, hidden = odd (a one-test exercise keeps its test hidden); `run_tests` runs only the visible ones; the grade
runs all and records `visible_*`/`hidden_*`. Supervisor fix on top: a child that crashes before counting (the model's
file does not import) returns its traceback, never "No visible tests". Verified: A1 suite 33 OK; controls 34/34
reference pass + 34/34 stub fail; on all 34 real exercises 300 visible / 284 hidden tests and zero hidden ids in the
model's output (positive control: the same pattern finds all 300 visible). Deployed to all three boxes (15673ecf…).
**Hardened 2026-10-05 (deployed 7a601a74 / a1_order f8002c50):** error rows carry their cause; code that does not import
or hangs is a FAIL (`graded: "crashed"`), not an instrument `error`; aider mode gives a non-importing first attempt its
second try; the test child runs under a 2 GiB memory cap (measured: the 34 references peak at 27.5 MB); a test module
that cannot import the model's code is SHOWN to the model (it was split into the hidden half and reported as "No
visible tests" — found on the 3.8's `go-counting`), and the control counts a stub that fails to load as failing.
**Running 2026-10-05:** the v2 leads (Mothership: Ornith → Nemotron-30 → the 3.8; BigHonker: qwen3.5:9b → qwen3:8b);
the first 3.8 v2 attempt was stopped at 13/34 because it loaded the pre-fix harness. **qwen3.8:27b (v2, complete): 23/34** — 0 caps, 0 crashes, median 57 tok/s, 6 turns, 89 min; **ALL 11 failures passed
every visible test and failed hidden ones** (bowling, dot-dsl, forth, pov, react, robot-name, scale-generator,
sgf-parsing, …): it verifies against what it can see, gets green, and finishes — the behaviour arm "claims success
without real verification", exposed by the split (v1's full-feedback 33/34 hid it). **Ornith-1.5 35B-A3B (v2,
complete): 21/34** — 0 length caps, 1 crash, 4 failures passed every visible test and failed
hidden ones (bowling, forth, pov, tree-building), median 133 tok/s, 7 turns, 48 min (preliminary until the leads
finish). **Nemotron-3-Nano 30B: first v2 attempt stopped at 6/34 as an instrument fault** — 4 of its 5 failures were ONE
tool call ollama could not parse (`XML syntax error … element <function> closed by …`), which the harness turned into
the end of the exercise. A real agent loop tells the model and lets it retry: since 2026-10-05 02:30 the adapter
returns `invalid_tool_call`, the harness hands it back ("could not be parsed … call the tool again") as a no-progress
turn, so a model that keeps doing it is still cut (harness d0a006d0, a1_order af6d5c4b, suite 45 OK); resubmitted
fresh. **qwen3.5:9b on BigHonker (v2, stopped at 12/34 as a known-bad arm): 1/12, and 9 of its 11 failures hit the
7,168-token context (111 length-capped turns) — truncated code, then no progress.** That prices the 8 GB box's context
ceiling, not the 9B's skill: "qwen3.5:9b at its tested 7k on 8 GB cannot do agentic coding." The other 2 rows: ollama
on BigHonker (0.32.3; Mothership runs 0.34) rejected the model's tool calls (`XML syntax error`) — model output, but
parser-version dependent, so recorded as `error` with the cause. **Queued behind them: Aider mode**
for the three leads (`a1v2-py-aider-*`, the published-comparison protocol; one variable vs agentic: the mode). Before
its first live run the harness was fixed to extract the file from a fenced whole-file reply (as Aider's own "whole"
format does — raw, every fenced answer would have been a SyntaxError) and to keep its marker lines out of the
attempt-2 test output (A1 suite 41 OK, deployed 84f0c2a8).

## Why (the owner's words, 2026-10-04)

Prompted by Kai's Strata video comments (a local Qwen3.8-Flash-Next *"built a working racing game in opencode on the
first try … even tested its own code"*): *"Have we been asking them to do hard enough tasks? … I did not think local
models did that."* And, on this proposal: *"that sounds like it's properly putting into words what we have kind of
been circling when I say a model can use sub agents."*

**What the menu cannot see today.** Every family is ONE-SHOT: one prompt, one answer, graded by hidden checks
([[34-Hard-Bench-Order-Spec]]). For the frontier models those families are at or near the ceiling — the spec already
says *"the current families are too easy to separate the 3.8's quality"*; on 2026-10-04 the top three read f1/f2
92-100%, C1 96-100% (thinking on), and W1 (the work family built to fix that) 81-94%. One-shot measures whether a
model KNOWS; A1 measures whether it can BUILD — many turns, tools, an environment that pushes back (errors, failing
tests), self-testing. That is also where the contenders should differ most (Ornith 1.5 is an agentic-coding model,
Nemotron 3 Nano is tuned for tool calls).

## What A1 is

- **A task** = a small project with a written brief: a playable terminal game, a CLI tool, a parser plus its tests, a
  bug hunt in a seeded multi-file codebase, a refactor that must keep a suite green. Multi-file, several hundred lines,
  more than one turn of work by design.
- **The loop**: the model gets a fixed tool set — list/read/write files in its own per-item workspace, run the item's
  VISIBLE tests (and the program), finish — and works until it calls finish or stops making progress.
- **The grade**: HIDDEN acceptance tests run after finish (pass/fail per test; the task passes when all pass), plus
  turns, wall time, tokens, and joules per solve — "per solved", failures charged to the solves, as the menu does
  ([[34-CHARTER]] "How results shape action").
- **The visible/hidden split** is the point: visible tests let a model check itself (what the racing-game run did);
  hidden ones keep it honest.

## Sub-agents — the same family, one level up (A1-team)

The lead gets one more tool: **`delegate(brief) -> result`**, a sub-task placed by Spock on any worker below it (an
8 GB box, rigby, the tablets) exactly like an order — [[34-CHARTER]] "ONE shape at EVERY layer". Same tasks, same
hidden tests, so **A1-solo vs A1-team on the same task IS the sub-agent measurement**: does splitting help, at what
cost, with which helpers. It generalises S10c ("lead plans, tablets draft pieces", [[34-S10-Rigby-Lead-Fellowship-Design]])
from rigby + tablets to every lead, and is the Phase 7 placement rule applied to sub-tasks.

**rigby as the 3.8's sub-agent — BUILT 2026-10-07 (decision 3a).** `a1_delegate.RigbyDelegator`: a piece is a
Tolkien `llm` order placed through Kirk's desk BY CAPABILITY (`only: ["fellowship"]`, `requires: ["rigby.llm",
"rigby.gt730"]` — Tolkien now DETECTS both, `tolkien_llm.rigby_capabilities`), one task in a generated payload on the
share (the f2 grader + control), the rigby MoE with thinking off at 32,768 and the GT 730 placement, `priority: 100`,
`deliver_to` a stated path the lead reads; the lead waits on the desk's status like the BigHonker path (shared code:
`Delegator._order_body` / `_read_answer` hooks). `submit_a1.py --team=rigby:Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf`
(name `-team-ri-…`). Live decoy 2026-10-07: one piece desk → Tolkien → rigby → delivery, a correct `count_vowels`,
control passed, 18 s helper wall. One layer at a time: rigby's own arms are stopped/held while this arm runs.

**A1-team BUILT 2026-10-06 (GPU layer first: the 3.8 lead on Mothership, BigHonker helper).**
- **Harness:** `delegate(brief) -> text` is offered only when the order passes a `delegate_fn`, so solo is unchanged.
  Pinned by `A1DelegateTool` (solo offers no delegate; a team run does; an empty brief is refused).
- **Delegation as an order:** `a1_delegate.Delegator` turns each brief into ONE desk order: an `infer_ollama` item,
  `only: [helper]`, `requires model.<helper>`. So the helper's foundry claims its card and the deck shows it
  (doctrine 12e: the lead never calls the helper's ollama directly).
  - The lead waits on `GET /p20/order/{id}` (status, not a clock), keeps its own model warm every 4 min, and gets
    the delivered answer back.
  - A failed or held piece is told to the lead; nothing hangs.
  - Each row carries `delegations` (timings, tokens) and `delegate_wait_s`, so waiting is priced apart.
  - Tests: `test_a1_delegate` (4) and `TeamPlan` end to end. A1 suites: 75 OK.
- **Protocols by name:** the boxes keep v2.1 as `a1_harness.py` (`5274e5d2`) while v2.1 orders are queued; v2.2
  plus `delegate` deploys as `a1_harness_v22.py` (`20d85c2b`). A plan names its harness (`"harness"`), and every
  row records the hash of the file actually loaded.
- **Submit:** `submit_a1.py --harness=a1_harness_v22 --team=bighonker:qwen3:8b`. The helper's thinking and
  temperature come from the resolver's coding family (qwen3:8b: thinking off, 0.7, the 8 GB coding pick); its
  context is the tested max (17,408).
- **First pair, on the 10 exercises that split the models** (wordy, sgf-parsing, react, bowling, paasio,
  rest-api, poker, robot-name, transpose, zipper): team first, the solo v2.2 control last (one layer at a time).
  **Team arm DONE 2026-10-06 10:03** (`a1v22-py-agentic-hai-q38-best-thon-spot-team-bi.20261006-0823`, helper
  qwen3:8b on BigHonker): **5/10** (bowling, poker, robot_name, transpose, zipper; the five fails each one hidden
  test short, all ended by `finish`) and **0 delegations in 207 turns** — the tool was in the brief's tool line of
  every transcript and in the request's tool list (seam-pinned); the 3.8 never chose it. BigHonker idle throughout.
  **Solo v2.2 control DONE 11:58: 6/10** (sgf-parsing and rest-api passed, transpose failed; seven verdicts identical
  to the team arm) — the 5 vs 6 is run-to-run variance on one pair, not a tool-presence price. **Directed arm**
  (`a1_harness.DIRECTED_BRIEF`: the lead is told to hand self-contained pieces to the helper, still deciding the
  split; plan `team.mode: "directed"`, `submit_a1.py --team-mode=directed`) started 11:58 on the same spot set,
  `…-team-bi-dir.20261006-1102`. Decided with the owner 2026-10-06 after checking the charter: the organic arm answered
  "does the lead reach for a description-only tool" (no); the pricing arm needs pieces, so delegation is directed.
- **Fellowship layer (S10c) BUILT the same day:** the rigby lead's `delegate` deals each brief as a Tolkien map
  order on `map_brief.py` (one tablet drafts it) through Tolkien's own inbox — `a1_fellowship.FellowshipDelegator`,
  same call protocol as `a1_delegate`. Details: [[34-S10-Rigby-Lead-Fellowship-Design]] § "S10c BUILT".

**Priced entries (the deliverable; PRELIMINARY — n=1 per arm, reproduces-once not met). Reducer: `hard-bench\a1\reduce_a1.py`
(GPU energy = board joules `gpu_j`; rigby = wall × 54 W metered plug marginal; tablet pieces = effort, no joule claimed);
tables under `mothership-build\workspace\a1\summary\`.** Spot set, 10 exercises, 2026-10-06:

| arm | lead · helper | solved | wall total | wall median (min..max) | energy | J per solved | pieces | lead wait | lead turns |
|---|---|---|---|---|---|---|---|---|---|
| team, organic | 3.8 · qwen3:8b BigHonker, tool merely available | **5/10** | 3,708 s | 382 (45..699) | 1,424 kJ | 285 kJ | 0 | 0 s | 207 |
| solo v2.2 control | 3.8 alone | **6/10** | 6,842 s | 253 (44..2,987) | 2,616 kJ | 436 kJ | — | — | 248 |
| team, directed | 3.8 · qwen3:8b BigHonker, told to use the helper for self-contained pieces | **7/10** | 10,158 s | 270 (79..5,418) | 3,592 kJ | 513 kJ | **0** | 0 s | 252 |
| team, MANDATORY (done 18:06) | 3.8 · qwen3:8b BigHonker, the brief requires ≥ 1 delegated function | **6/10** | 11,155 s | 433 (62..5,341) | 4,018 kJ lead + 8.7 kJ pieces | 671 kJ incl. pieces | **15** (15 done) | 706 s | 259 |
| S10c team, organic (cut after 2) | MoE on rigby · Coder-1.5B tablets, tool merely available | **0/2** | 13,482 s | 6,741 (3,062..10,420) | 728 kJ est. | — | 0 | 0 s | 57 |

Reading: four runs of the 3.8 on the same 10 exercises scored 5, 6, 7 and 6 — that spread is the lead's run-to-run
variance; the three with zero pieces carry no delegation effect at all. The 3.8 does not delegate with a
description-only tool and does not delegate when told to use the helper; made to (mandatory), it delegated 15 pieces
(1 per exercise, 5 on rest-api, 2 on bowling), the 8B answered each in **2–15 s** (median 4.2 s, 400–2,800 chars)
for a lead-side wait of 20–83 s per piece (48–83 s before the 16:24 foundry fix, 20–33 s after), and the arm scored
the same 6/10 as solo at **1.6× solo's wall and 1.5× its energy per solved** — helper pieces are nearly free, the
lead's turns around them are not (259 lead turns vs 248 solo). The pieces were USED, not just requested: 14 of 15
helper functions are defined by the lead in later writes, 9 with a helper line pasted verbatim (`pieces_used.py`
on the transcripts), so the extra wall is briefing, waiting and integrating. Per-worker piece prices are in the summary table
(`summary\20261006-1809-spot-arms.md`). sgf-parsing is the 3.8's coin-flip exercise (10 min fail · 50 min pass ·
90 min pass · 43 min pass) and dominates every arm's wall and energy; rest-api is the other long one (89 min, 55
turns, mandatory). Loop-cut turns (infer_ollama's no-progress rule) touched 1 / 2 / 5 / 5 of 10 rows across the four
arms; rows with loop cuts still pass, and the share is stated per arm in the table. rigby's lead price is ~1–3 h per
exercise at CPU prompt-eval speed. The directed S10c (MoE + tablets) is running and HAS delegated (aragorn, 305 s,
the whole SGF parser as one brief); its rows land when it delivers. **The misses are the same hidden tests in every
arm** (the reducer names them per cell): wordy `test_non_math_question` ("syntax error" answered where the brief's
error table says "unknown operation"; 4 of 4 runs), react `…callbacks_should_not_be_called_if_dependencies_change_
but_output_value_doesn_t_change` (4/4), paasio `test_meteredfile_iteration` (4/4), transpose's ragged-line group
(2/4), sgf-parsing `test_node_without_tree` (1/4) — the gate and review arms are judged on whether THESE close, by
name. **Delivered 2026-10-07 (n=1):** gate 7/10 (520 kJ/solved), gate + review 8/10 (495 kJ/solved; react closed
for the first time; wordy and paasio still missed). ⚠ **The review measured nothing:** the reviewer (BigHonker
qwen3:8b) got the full package (1.3k–8.7k prompt tokens, inside its 17,408 ctx) and answered `NO DEFECTS` in exactly
5 tokens with zero thinking on all 10 — including wordy and paasio, which failed hidden tests. The settings resolver
chose the reviewer's settings from one-shot CODING (thinking off), the wrong task type for a review. So 8/10 is the
gate plus a rubber stamp; the gate-vs-review difference is noise until the arm re-runs with the reviewer thinking ON
(and other peers priced, per [[HAI-Agent-Surface-Design]] "which peer helps most"). Replicates of solo and gate are
queued (2026-10-07) because every ladder cell is n=1.

**DECIDED with the owner, 2026-10-06 ("that all sounds good"), on this evidence — how the gaps get closed, cheapest first:**
1. **A deterministic finish gate** (Phase 8: the model proposes, code decides): `finish` is not accepted until the lead
   has written its own tests for every requirement in the brief and they pass — targets the one-test-short misses
   (react 13/14 in seven runs, wordy 24/25, paasio 24/25). **BUILT 2026-10-06 17:35** (`a1_harness.FINISH_GATE_BRIEF`,
   agentic-hai only: `finish` is refused with a reason — no `test_*.py` yet, or they have not passed via `coding_test`
   on the CURRENT workspace hash — and refusals are counted in `tool_counts.finish_refused`; nothing capped, a lead that
   keeps calling finish trips the existing no-progress rules and is still graded). Plan `finish_gate: true`,
   `submit_a1.py --finish-gate`; rigby's payload takes the same flag (`a1_task.DELEGATE["finish_gate"]`). Arm queued on
   Mothership at priority 8: `a1v22-py-agentic-hai-q38-best-thon-spot-gate.20261006-1735` (same 10 exercises).
2. **A1-review** (§ A1-review): a different model reads the diff + test output against the brief, one repair turn.
   **BUILT 2026-10-06 17:35**: when the lead's finish is accepted, `a1_harness._review_package` hands the reviewer the
   ARTIFACT — the brief, every file the lead can see (its solution and its own tests), the last `coding_test` output it
   saw; never the hidden tests, never the lead's narration — through the same desk mechanism as a helper piece
   (`a1_delegate.Delegator(prefix="a1review-")`, one `infer_ollama` order pinned to the reviewer's box). Findings other
   than `NO DEFECTS` reach the lead as ONE user message (`REVIEW_REPAIR_TEXT`) and its next accepted finish is final;
   a reviewer ERROR (held, refused, undelivered) lets the finish stand. Rows carry `review` (clean / error / repaired /
   findings / wall) plus `review_pieces` and `review_wait_s`, priced apart from the lead's turns. Plan `review`
   block, `submit_a1.py --review=<box>:<model>` (refuses the lead's own model). Arm queued at priority 7, gate +
   review so each rung is one variable against the one below: `…-spot-gate-rev-bi.20261006-1735` (reviewer qwen3:8b
   on BigHonker at its tested 17,408 context, thinking off per the resolver's coding family).
3. **S10b**: the lead's own tests select helper pieces without hidden checks. **BUILT 2026-10-06 18:04** on the
   family-2 pool (the lead writes the checks; the tablets' existing best-of-5 candidates are chosen by them; graded by
   the hidden checks with false-pass / false-fail honesty rates): [[34-S10-Rigby-Lead-Fellowship-Design]] § "S10b
   BUILT". Lead order `s10b-rigby-moe-f2tests-s1.20261006-1804` queued behind the directed S10c on rigby.
4. **Delegation is SYSTEM-directed, never model instinct and never a user ask**: the lead does not split work on its own
   (organic 0 pieces on both layers) but splits well when told (mandatory arm: pieces back from the 8B in ~5 s).
   In the end-state surface the user types into one window; Kirk (advisor proposing) decides whether and how to
   split, Spock places the pieces, and the lead's brief arrives already directed — today's harness brief is that
   injection in test clothing. Open design: the advisor's decomposition proposal (its transcripts come from these arms).
   **Measured inputs for that design (2026-10-06, the mandatory arm; corpus `summary\delegation-corpus-*.jsonl`):**
   made to delegate, the 3.8 writes ONE function per piece with an exact contract (signature, input/output examples,
   edge cases; briefs median 1,899 chars) and integrates what comes back (14 of 15 used); the 8B answers a piece in
   2–15 s (median 4.2 s, ~0.4–0.6 kJ); the lead's own cost around a piece — writing the brief, waiting, reading and
   integrating — is what made the arm 1.6× solo's wall for the same score. So on tasks the lead can do alone,
   splitting costs; where it can pay, by the numbers so far: (a) tasks the lead CANNOT do alone (S10a's residue
   shape: cheap workers first, the lead on what they miss — 0.83–0.92 vs 0.75 on family 2), (b) pieces that are
   CHECKABLE without the lead reading them (S10b: the lead's checks select, not its eyes), (c) waiting that is
   nearly free (the helper window: a piece ≈ the helper's answer + 3 s) with several pieces in flight at once —
   which needs a helper box with more than one slot (an 8 GB card serialises; the measurement for that is a
   Mothership-hosted helper). The advisor's "whether to split" therefore reads as: never for speed on a task the lead
   solves alone; yes when the lead's own solve rate on the task class is low or the piece has a check.
5. **#35**: the A1 transcripts with their hidden-test verdicts are the "lab experience" corpus for training the
   judgment into the champion.
Measured price to carry into Phase 7: the desk round trip cost the lead ~40–110 s per piece for ~5 s of helper work
until 16:24 on 2026-10-06; after the foundry's status-based quiet gate and 5 s desk cadence (all three GPU factories
by 18:08) a piece is **~21 s desk-created → done** (live decoy rest-api-d8; 28–33 s from the lead's own clock), of
which ~12 s is the per-piece process spawn and residency check.
**Re-measured 2026-10-10 (the owner: "we should try to fix the round trip then"), and three costs removed:** a placed piece
on BigHonker took 34–42 s lead-to-done (10-09 #4 pieces; the decoy 67 s). Split: the desk POST **8–11 s** — every
`place()` waited out a 3 s connect to Mothership's stopped ComfyUI (port 8189 DROPS the SYN) and a 4 s connect to
BigHonker's Glances, which is absent (not installed; the 08-22 "by design" was a session's label, never the owner's - 2026-10-25) — fixed (250 ms connect, measured LAN connect
0.5–0.8 ms; BigHonker not asked): **POST 11.15 → 0.38 s, for every placement incl. HAI chat**. Preflight ~9 s — a
PowerShell started to stop a ComfyUI that was not running, plus a PowerShell port check — fixed (netstat, skip when
free): **2 s**. Left: desk → claim ~3 s (the 5 s pull cadence) and the **cold model load, 13 s** (qwen3:8b /
nemotron 4B; 56 s on nemotron's first, 0.40-migrating load), because pieces arrive minutes apart and only the LEAD's
model is kept warm. After: **17.7 s submit → delivered** (decoy 16:41, 0.4 s of helper work). An overflow piece placed
while BigHonker was still releasing went to Mothership qwen3-coder:30b cold: **67 s for a 0.2 s answer** — placement
does not yet price residency (Phase 7 design step 4). The cold load is what the helper WINDOW removes (below), so the
placed delegate's next form is a PLACED window: Spock places the window (family / tier / lens → host + model) once
per run, pieces cost the answer + ~2 s. **BUILT + LIVE 2026-10-10 17:2x** (`a1_delegate.PlacedWindowDelegator`,
`team = {placed, window, family, tier, urgency}`; `helper_window.py` now takes `--think-level` — before, a policy row's
level would have been dropped silently by `parse_known_args`). **Live decoy 17:22:** the window was placed through the
desk in 0.42 s on BigHonker from the coding-f2/8gb/least_energy row (nemotron-3-nano:4b, ctx 261120, temperature 0);
item 1 **14.3 s** (the window's one-time spawn + load), item 2 **1.1 s** (warm: 0.4 s of helper work); CLOSE → desk `done`.
**A placed piece now costs ~1 s after the first, against ~40 s per piece this morning.**

**Helper WINDOW (BUILT 2026-10-06 19:00, the owner: "we gotta fix that wait time")** pays that fixed cost once per run:
`team.window: true` (`submit_a1.py --team-window`, name suffix `-win`) makes the lead's delegator place ONE compute
order on the helper box — `bin\helper_window.py`, which checks residency once, holds the model, and answers item
files the lead drops into `workspace\a1\windows\<window>\` (`d<k>.item.json` → `d<k>.out.json`, an infer_ollama-
shaped row with its own `gpu_j`); the lead writes `CLOSE` when its run ends by any route, and the worker also closes
itself when the lead's desk order is no longer alive (`--lead-order <id>`, read from the lead's own queued
`.job.json`; a status, never a clock). The window is still a desk order: the helper's foundry claims the card, the
deck shows `a1win-…` with items answered as progress, the police see it (12e). A held or ended window is an ERROR
the lead can act on; a refused window fails every piece fast. **Live decoy 2026-10-06 19:11 through the real desk
(Softserve as helper, qwen3:8b): the window placed as one order and showed on the deck; item 1 cost 24.0 s lead-side
(the window's own pick, spawn and model load, paid once per run; helper wall 0.4 s); item 2 cost 2.0 s lead-side for
a 0.2 s answer — the poll grain; CLOSE → desk `done` in 2 s; rows delivered with live `gpu_j` (12 J, 15 J).** So a
piece after the first costs the helper's answer + ~2 s, against 20–33 s per-piece orders. The real price on real
pieces comes from its own arm (`…-must-win`, the 3 quick exercises first) before it replaces the per-piece path. Corpus of what the lead delegates (brief, answer, used or not, outcome, price):
`hard-bench\a1\delegation_corpus.py` → `summary\delegation-corpus-<stamp>.jsonl` (rung 4 / #35 input).

**A1-review — peers who grade the work (the owner's Absurd goal, 2026-10-05).** The lead gets one PEER review of the
ARTIFACT — its diff and the full test output, never its own write-up — then one repair turn in the same loop. Solo vs
solo + review on the same tasks IS the measurement of whether peers make a local model smarter, and the reviewer is a
DIFFERENT model from the lead (decorrelated). the owner: *"I was trying to get "agentic" behavior out of a local model where
it can get reviewed on its work and be smarter through having peers to help grade"*. Absurd's critic never saw the
work ([[HAI-Agent-Surface-Design]] § "How the agent does WORK"), so this has never been measured.

**Shared hardware — a helper box runs ONE thing at a time (the owner 2026-10-04: *"if 3.8 27b delegates a task to a sub
agent on BH then BH can't very well be running its own try at delegation"*).** Every layer draws on the same boxes, so
an A1-team run and another A1 run (solo or team) that touch the same box would void each other: the lead's run would
price a BUSY helper as a SLOW one, the helper box's own run would be disrupted mid-item, and one model slot per card
means a delegation also evicts the helper box's own model. **DECIDED: one layer at a time** (the owner 2026-10-04, asked *"should we just wait and run them one layer at a time?"*, then *"Sounds good"*). Solo runs on DIFFERENT boxes never touch each other, so they run in parallel; each A1-team run goes alone, with its helper boxes running nothing else (desk queue order plus the existing lane holds). No yield, priority ladder or busy-routing is built for the measurement: A1-team exists to find out whether delegation pays at all, and that machinery belongs to PRODUCTION delegation (HAI splitting live work while people chat), designed in Phase 7/8 once A1-team numbers exist. Kept as a design note for then: yield only at a unit boundary (a fresh conversation, so nothing to re-read; the factory's `lane.yield` does this for training checkpoints only today); delegation only goes DOWN the layers (no cycle, no deadlock); priority lives on the order, inherited by a piece and raised when a lead is blocked on it, decided by Spock never by a model; `delegate` reports status and expected wait so the lead can choose. Two things the measurement DOES need: (1) every A1 turn records ollama's `load_s` and `prompt_eval_s` (DONE 2026-10-04, `a1_order.py` 8784f2e4…), so a reload or a re-read can never hide inside a score; (2) when `delegate` is built, the LEAD keeps its model loaded for its whole run (A1 calls go through `infer_ollama._chat`, which sends `keep_alive: 10m`, so a lead waiting longer would be unloaded and pay a cold reload plus a full re-read). Waiting is priced separately from the lead's own turns.

## Harness — build from what exists (decision for the owner, see the end)

**Recommended: A1 as a factory ORDER** (a measurement is an order): payload `agent_build.py` on every factory, driving
that box's own model through ollama's native tool calling. It inherits, rather than rebuilds: the residency + spill
guard, the hung-engine probe, the loop cutter and progress markers (`infer_ollama`), the bounded child-process grader
(`grade_jobs` / the payload `score()` that already runs model-written code for W1), run-state + the deck (visibility,
doctrine 12e), stop/requeue slips. Per item: a fresh temp workspace, deleted at the end; tests run in a bounded child
with no network. Tolkien gets the same shape for rigby (`tolkien_llm`, kind `llm` + tools).
**Alternative: drive HAI's own coding loop** (the coding-runner's 13 constrained tools, isolated worktrees,
checkpoints) with a per-run model. More realistic for HAI's production use, but it runs through Kirk on Sundae (one
lane for the whole fleet, and the loop is Mothership-centred), so the boxes' own measurement machinery would be
bypassed.

## Rules carried in from doctrine

- **No arbitrary limits (13a):** no turn cap and no clock on the measurement; the loop cutter (no progress) ends a
  stuck run, and a run that ends on a cap is VOID and counted, never scored as a fail. Context = the model's tested
  maximum on its box.
- **Tasks stay OUT of the indexed library** (the E2 lesson: a pinned test whose answer enters the RAG corpus stops
  testing behaviour). Briefs, visible tests, hidden tests and references live in `hard-bench\a1\` with a reference
  solution per task that must pass its own hidden tests (positive control) and a seeded wrong one that must fail
  (negative control) — W1's selftest pattern.
- **One variable:** A1-solo vs A1-team differ ONLY in the `delegate` tool.
- **Specialists are judged as helpers here** (their real job), not as solo builders.

## Decisions (the owner, 2026-10-04: "I'm down for all that, but I don't want to burn your usage building an answer key")

1. **Harness: a factory order per box** (recommended by Claude from the library, the owner to confirm): doctrine 12e (work is
   a foundry order, visible by construction); it inherits `infer_ollama`'s residency + spill guard, the hung-engine
   probe (RUNBOOK-Ollama-Host-Operations "Ollama HUNG"), the loop cutter, stop/requeue slips. HAI's coding loop is a
   PRODUCTION loop — lead = Kirk's `HEAVY_MODEL` (qwen3.8:27b on Mothership, live config 2026-10-04), allow-listed
   tests in registered workspaces ([[04-CODING-RUNNER-STATUS]]), no turn or search count since 2026-10-05 (the old 50-turn cut
   ended HAI's streaming FIX 1 at 17 min of a 4 h budget, doctrine 13a); it ends on demonstrated idleness or the
   job's deadline ([[HAI-Core-Architecture-API-Contracts]]),
   sandbox on Sundae (a guest VM on Softserve, the Plex host).
2. **Tasks: all kinds, from PUBLIC sets that ship their own tests — no answer key written by Claude.** Start with
   **Aider Polyglot** (225 Exercism exercises in C++, Go, Java, JavaScript, Python, Rust, unit tests included, a second
   attempt with the failing test output; the de facto local-model coding benchmark, so our numbers compare with
   published ones), then **Terminal-Bench 2.0** (89 containerised agentic tasks, 16 categories, hidden tests) for the
   "make it work end to end" class. Positive controls come from the sets' own example solutions. Caveat: public sets
   may be in the models' training data — a strong signal, not gospel. Self-authored tasks only later, and authored by
   the lab's own models (HAI / fleet orders), validated by the controls.
3. **Leads: the 3.8, Ornith and Nemotron solo first**, then A1-team with helpers. **The crown is named at the very
   END of #34, not before** (the owner 2026-10-04: *"I don't want to crown a challenger yet until we've finished our stuff.
   That's what we have the A1 agent stuff to help with"*) — A1 is the closest the project gets to the charter's "beat
   it in ACTUAL lab work".
3a. **rigby and the tablets are tested in their END-STATE ROLES, not by more one-shot cells** (the owner 2026-10-04: *"think
   about what exactly we're aiming for as the end state with Rigby… we're not just blindly testing models"*). rigby's
   one-shot grid did its job — the shortlist (Qwen3.6-35B-A3B thinking-off, Qwen2.5-14B, Coder-3B) and the price of its
   ~4 tok/s prompt reading — and its lane is held in `menu_fill_hold.json`. rigby's roles and their A1 arms:
   - **Lead** ([[34-S10-Rigby-Lead-Fellowship-Design]]): the rigby shortlist runs A1 SOLO on the same Polyglot tasks;
     then **A1-team with rigby delegating to the tablets** (S10c), its reading cost priced inside the run.
   - **Sub-agent to the 3.8:** A1-team with the 3.8 lead delegating pieces to rigby (and to the 8 GB boxes).
   - **Advisor (Phase 8):** needs its own controller-decision family (ambiguity, decomposition, result comparison,
     escalation) — not A1; designed with Phase 8.
   The **tablets'** end state is sub-agents, so their next test is A1-team / S10 as helpers, not challenger grids;
   MiniCPM5-1B / MiniCPM-V quality carries over from the boxes that ran them (same weights), speed per class from the
   same-size models already measured per class.
4. **Languages: all six.** First step before any build: inventory where the toolchains (Go, Rust, Java, C++, Node)
   exist or can run — the GPU boxes are Windows; rigby is Linux with Docker — measured, not assumed.

## Work behaviour, scored by code (2026-10-04) — what the crown actually rests on

The crown is named at the end of #34 on A1 (decision 3), and the charter judges on WORK, not benchmarks
([[34-CHARTER]] § "Evaluation methodology", §45-47). The Native Intelligence recon names the failure patterns that
matter in real lab work — missed actual objective 29 %, re-derived plan 17 %, unverified claims 13 % — so A1 scores
them too, **deterministically** (no judge model), as SEPARATE arms so the plain A1 solve rate stays comparable to the
published Aider numbers (one variable):
1. **Unverified success** (every arm, free): `finish` called with a note claiming success while the hidden tests fail;
   and whether `run_tests` was called after the last `write_file` before `finish` (verify-before-claim).
2. **Failure recovery** (§47) — arm `a1-recover`: one injected transient tool failure (e.g. `run_tests` returns an error
   once at a fixed turn); scored on the next actions: retry/investigate vs finish anyway / claim success / blind repeat
   (the loop cutter's count).
3. **Taking a correction** (§46) — arm `a1-correct`: after the first `run_tests`, one injected message changes a
   requirement, backed by an extra hidden test; pass = the final code passes the extra test AND the original ones
   (incorporates without breaking; reverting or overcorrecting fails one side).
4. **Respecting the plan** (§45) — arm `a1-plan`: the brief states a constraint the hidden tests do not check (no new
   files; keep the stub's names and signatures); checked against the final workspace.
Missed objective (29 %) has no clean code check on Polyglot; it is left to Terminal-Bench's end-to-end tasks and named
here so it is not silently dropped.

**BUILT 2026-10-07** (harness `225002c7` = `a1_harness_v22.py` on all three GPU boxes; `submit_a1.py --behaviour=
recover|correct|plan`, name suffix `-b<arm>`; tests `A1Behaviour` 9 + a mutation check, reducer `ReduceBehaviour`; A1 suite
120 OK). Two departures from the text above, both forced by the owner's "no answer key built by Claude" (10-04): the
correction is a requirement checkable on ANY exercise from its own stub — `CORRECT_TEXT`: the module must also define
`__all__` naming every public top-level function/class; pass = applied AND the hidden tests still pass — instead of a
per-exercise extra hidden test; and the plan constraint is `PLAN_BRIEF` (no new files; the stub's names and parameters
unchanged), checked against the final workspace with `ast`. Recover = the first `run_tests`/`coding_test` returns
`RECOVER_ERROR` once; the next call is scored retry / investigate / finish, and `recovered` = a real test ran after.
**Unverified success is recorded on EVERY run** (`behaviour.verified_before_finish`, `unverified_success` = finished
failing code with no test after its last edit). Queued 2026-10-07: `a1v22-py-agentic-hai-q38-best-thon-spot-
b{recover,correct,plan}.20261007-0836` (Mothership, priority 7). Every transcript is kept — they are #35's evaluation (and later training) data.

## Branching — an arm starts from a saved run instead of regenerating what it shares (2026-10-07)

the owner: *"couldn't we save the regeneration of the initial part from the model and use what we got earlier as the
input?"* — the P20 resume / probe-from-checkpoint idea and the menu's re-grade-from-saved-answers, applied to A1.
**Rule: an arm may reuse a parent run only up to where its OWN difference begins.** `point: finish` (gate+review from
a gate run — the reviewer acts after the accepted finish) · `point: first_test` (b-recover / b-correct from a solo run).
NOT branched: b-plan (constraints are in the brief), team arms (delegation starts early), replicates (their point is a
fresh sample), other models.

- **How (`a1\a1_branch.py`, `a1_order.py` plan field `branch`, `submit_a1.py --branch-from=<parent> --branch-point=`):**
  the harness file is UNCHANGED (branched runs share the parent's `harness_sha256`); `ReplayChat` is the chat function
  the harness already takes — it returns the parent's recorded assistant turns up to and including the branch turn,
  then calls the live model. The harness re-executes every replayed tool call, so workspace, gate state and the last
  test output rebuild exactly. **Self-checking:** every replayed file edit's result (its new sha256) must equal the
  parent's, or the row is `branch.valid: false` and never counts. A parent that never reached the point is replayed
  WHOLE (the arm's event never fires on that history — the paired answer is the parent's own ending, at no model cost).
- **Guards:** the parent must match model, context, effort, mode AND harness hash (else VOID); a parent not yet
  delivered = rc 2 (the foundry's hold-and-retry) — never a half-finished parent.
- **Pricing:** each row records `branch.prefix_wall_s` / `prefix_eval_tokens` (the parent's cost up to the branch), so a
  branched arm's full price = prefix + its own live part.
- **Why it is the better measurement, not only the cheaper one:** the comparison becomes PAIRED — same exercise, same
  history to the branch, one variable after it (doctrine 13). Unbranched, a lucky or unlucky first half reads as the
  arm's effect.
- **First use 2026-10-07 23:30:** `…-gate-rev-bi-rthon-exhigh-br` (from the gate arm `…-gate-exhigh.20261007-2052`,
  at finish) and `…-b{recover,correct}-exhigh-br` (from the solo baseline `…-exhigh.20261007-1542`, at first_test)
  replaced their unbranched copies (cancelled at the desk); ~4–5 h less Mothership time. Tests `test_a1_branch` (5,
  through the real harness); A1 suite 135.

## Resume from checkpoint — a stopped A1 order loses only the turn in flight (2026-10-08)
the owner: *"Why are the stops costing time? Can we not just branch from the checkpoint? I keep saying we need to do that?"*
Before this, a stop slip kept finished exercises but threw away the one in progress (10-08: rest-api lost ~35 min to two
stops), because a transcript was saved only when an exercise ENDED. Now `a1_order` writes
`transcripts\<exercise>.partial.json` (the conversation so far + per-turn costs) before EVERY model call; on requeue, an
exercise with a checkpoint is driven by `ReplayChat(checkpoint, "end", live)` — the branching mechanism above with
point `end` = every saved turn. Each replayed tool call re-executes, so the workspace rebuilds exactly, and each replayed
edit must reproduce its recorded sha (else `row.resumed.valid` is False). The row records `resumed` (turns replayed,
pre-stop model time) and `wall_s` = pre-stop + live (`wall_s_live` keeps the live part). The checkpoint is removed once
the full transcript is saved. ⚠ An order that STARTED before the deploy (2026-10-08 ~09:20, `a1_order` 0ebc75c5,
`a1_branch` 93f5a3f3) runs the old code and writes no checkpoints — check the process start time before relying on it.
Same pass, a seam bug in `ReplayChat`: its edit check re-ran on every LIVE turn against the last replayed turn — harmless
when that turn was a finish, a false "invalid" when it was an edit; now each replayed turn is checked once. Test
`test_a_stopped_exercise_resumes_from_its_checkpoint_without_regenerating`; A1 suite 143.

## Validity — a row that measured the INSTRUMENT is VOID, rerun once, never scored (2026-10-08)
the owner: *"how have we still not instituted guards and watchers and demons that monitor for things like this??"* Rigby's
path had scored engine crashes as fails for two days (a `ToolCall` validation crash on a malformed tool call; HTTP 400
from the 32k context), while the GPU path only voided such rows on a resume. **`a1_validity.py` is the one
derivation** (byte-identical copy in `tabfoundry-jobs\payloads\a1`, pinned by `test_a1_validity.OneDerivation`):
- `verdict(row)` → VOID when the row ended on an engine/harness error, a turn was cut by `length`/`priced_out`, or the
  model never took a turn (a loop NUDGE that the model recovered from is not a cap). Otherwise None (a real pass/fail).
  **Except a cut at the model's OWN ceiling (repair 2026-10-09 10:50):** `at_ceiling(turns, num_ctx)` counts `length`
  cuts whose call filled the whole tested `num_ctx` — such a call ran away on its own, 13a's remedy has no room to
  give, so the attempt is the model's result, scored as graded (`row.length_at_ceiling`). A cut below the ceiling is
  a limit we imposed and stays void. One derivation: `a1_order` applies it live; `reduce_a1.ceiling_attempts` reads it
  back onto attempts the pre-repair code voided (`transcripts/<ex>.voidN.json` carries the per-call counts), scores
  the ceiling attempt and discards the re-run it triggered — named per exercise in the reduce (10-09 13:15: Ornith's
  dot-dsl and pov first attempts each filled exactly 262,144; pov's had passed 15/15).
- `run_flags(rows, plan)` → the harness hash changed mid-run; a `directed`/`mandatory` team arm delegated nothing on
  its last 3 exercises.
- **GPU path (`a1_order`)**: a void exercise reruns ONCE — after `wait_for_engine` sees `/api/version` answer (status,
  never a clock: 10-08 15:15 a rerun 22 s after ollama died voided again) — the void attempt kept as
  `<ex>.void1.json`; a second void is recorded with `row.void` and excluded from `solved`. Progress line carries
  `void N`; alarms print `ALERT: A1 <order>: …`, which `p20_foundry` DMs once per distinct text and shows on the tile.
- **Rigby path (Tolkien)**: `a1_payload` stamps `void`; `tolkien_llm.run_arm` keeps a void row `ran=False` (a resume
  reruns it), reruns once in-run, and alerts through `notify_desk` in the same `ALERT:` words; llama-server's error
  body is kept (`llama-server HTTP 400: …`), and a malformed tool call goes back to the model as a parse error.
- Proven on 211 real delivered rows before shipping: it voids exactly the known crashes and nothing clean.
- Deploy: `fleet_deploy --a1` (all three GPU boxes, no restart; a bare `--check` includes it). Rigby's payload ships
  only through `fleet_deploy --rigby-a1` (suites gated in rigby's image, no stop of a running order; since 10-09).

## ⚠ THE SUB-AGENT TESTS TO DATE MEASURED THE WRONG SHAPE — not a verdict on sub-agents (audit 2026-10-10)
the owner, 2026-10-10: *"I'm wondering if you have been setting up a bunch of tests wrong and we haven't really been
giving things a fair shake."* Checked against the published guidance ([[Subagent-Delegation-Published-Guidance]]):
every team / sub-agent arm so far tested a shape that guidance says does not pay, so their numbers price THAT shape
only.

| test | what it actually was | what the guidance says | fair shake? |
|---|---|---|---|
| A1-team organic / directed / mandatory (10-06…09, spot set) | lead STOPS and waits; helper has NO tools and is told it "cannot see your workspace"; the lead must paste everything into a "write this function" brief; single-stub Polyglot tasks | delegate bounded jobs that run in PARALLEL or need reading across files; helper reads, lead keeps the conclusion; most coding tasks fit poorly | **No.** The lead not delegating, and 1.6× wall when forced, price a blind synchronous piece on a task with nothing to hand out |
| S10c (rigby lead → tablet pieces) | same blind one-shot drafting via `map_brief`; one brief was "the whole SGF parser" | bounded jobs with an objective, boundaries and a return format | **No** — same shape, and not bounded |
| Cascade arm (10-10, W1 code) | every task walked up every tier; the lead wrote checks first | charter: escalation is the WHOLE task passed UP when a lead needs it, not a ladder every task climbs | **Partly** — a valid ROUTING fact (hard code: start at the lead), not a delegation test |
| S10b (lead writes checks, tablets' candidates chosen) | generate-then-verify | a recognised pattern | **Yes** as a pattern; on hard W1 the checks cost as much as solving and 13/24 were valid |
| A1-review (peer reads the diff) | reviewer got the artifact, but its resolved settings (thinking off) made it a 5-token rubber stamp | a reviewer judges what it is handed; settings must suit the job | **Fair input, mis-set** — re-run with thinking on was already planned |

What still stands: the menu's one-shot prices, the lead's solo results (minus the write-ups leak below), helper
SPEED (qwen3:8b answered pieces in 2–15 s), the window overhead (1.1 s warm), and the routing fact. What does not:
any reading that "sub-agents don't help" or "the lead won't delegate". A fair test needs (1) work with something to
hand out — multi-file, read-heavy, explore-and-run (Terminal-Bench 2.0, approved 10-04, never started); (2) helpers
WITH tools in the lead's workspace, the answer-key area locked; (3) asynchronous errands with bounded briefs and
short answers; (4) the lead's own rule "one search = do it yourself", measured (`one_call`). Built 2026-10-10:
(2)–(4) ([[34-Phase-7-Placement-Policy-Design]] § "Two shapes of delegation").

## ⚠ SOLUTION WRITE-UPS LEAK — `.approaches/` / `.articles/` were readable (found + fixed 2026-10-10)
Exercism ships solution write-ups (approach essays with code snippets) in `.approaches/` and `.articles/` beside three
Python exercises: **pig-latin, robot-name, wordy**. `_setup_workspace` left out only `.meta` and the hidden test, so
the lead could list and read them; Aider's own protocol never shows them (its model sees the brief and the solution
file only). Found by the errand decoy's listing. **Audit, 2026-10-10 (scratchpad `approaches_audit.py`, every count
asserted): 897 of 897 delivered transcripts, 14,341 tool calls → 70 reads inside the folders, in 14 arms, 21 arm ×
exercise pairs (robot-name 12, wordy 9).** Both are in the 9-exercise spot set, so spot-set results — including the
crowned 3.8's solo baseline `…-spot-exhigh.20261007-1542` (8/9; it read both) — carry two exercises the lead may have
solved with the write-up open. **Fix (lock the area, not the tool — the owner 10-10):** `a1_harness._locked_dir` — every
dot-folder but `.docs` is never copied, listed, read or searched; one rule at every site; pinned by
`test_a1_harness.A1SolutionWriteupsAreLocked` (listing, reads both slash styles, search, with a positive control).
A1 suite 183; deployed `fleet_deploy --a1` 19:10 (three GPU factories); rigby's share follows once Track B's bench
order releases rigby. **Rows from before the fix on robot-name / wordy are not clean results** — whether to re-run or
mark them in the reduce is the owner's call (register row 10-10 19:1x).

## Rigby's A1 context = 65,536 (measured 2026-10-09)
32,768 came from the one-shot menu (largest use 5,278 tokens) and was never measured for agentic turns, which overran
it (`exceed_context_size_error`: 32,854 / 32,779). Probes (`inspect_lab\submit_speed_probe.py --only=gt730-attn-nooff
--ctx=N`): 98,304 fails to create its context on the GT 730, 65,536 loads. Arithmetic: 10 full-attention layers × 2
KV heads × 256 × 2 × 2 B = 20 KiB/token + 191 MiB weights + buffers → ~72–76k ceiling (≈140k with an 8-bit KV).
Rigby A1 orders take `--ctx=65536` (`submit_s10c.py`); rows that finished under 32k never touched the limit and stay
valid.

## Early stop — end an arm once it is clearly worse than its baseline (2026-10-08)

the owner, "Yes option 1": stop spending the card on an arm whose answer is decided. **Rule (`a1\a1_stop.py`, plan field
`stop_rule`, `submit_a1.py --stop-vs=<baseline order>`):** before each exercise, if `solved so far + exercises left <
baseline solved − margin`, even a perfect finish leaves the arm clearly worse, so it stops and ships what it answered
(`results.stopped_early` names the not-run exercises; the reducer prints ⏹ beside the arm so a partial count never reads
as a full run). **margin = 2, MEASURED:** four runs of the 3.8 on this spot set scored 5, 6, 7, 6 (the lead's
run-to-run spread). Both sides exclude the non-discriminating exercises (`reduce_a1.NON_DISCRIMINATING`: paasio).
**Never** stops for being clearly better (the crown and Phase 7 want the full magnitude and cost of a win); **off**
until the baseline has delivered; **not** used for behaviour arms (not judged on solves) or replicates (their point is
a full fresh run). Applied 2026-10-08 to the queued gate+review (vs its parent gate arm), team-ri-dir, the 4
BigHonker-team arms, Ornith and Nemotron (vs the fixed-tools solo baseline) by amending their plan files before they
ran (`amend_stop_rule.py`, backups `.bak-20261008-stop`). Tests `test_a1_stop` (6); A1 suite 141.

## Build plan (2026-10-04) — the 3.8 builds the harness, the machine checks it

- **Toolchains measured 2026-10-04:** Mothership + BigHonker: Python only; Softserve: Python + Node; rigby: g++,
  cmake, Docker (no Go/Rust/Java on any box). **Task set fetched 2026-10-04** (the owner's OK): commit `7e0611e7`, zip
  sha256 `ed96df1d…`, archive `Z:\Archives\Benchmarks\polyglot-benchmark\` (+ `manifest.json`), working copy
  `hard-bench\a1\polyglot-benchmark` — 225 exercises (python 34, javascript 49, java 47, go 39, rust 30, cpp 26), every
  Python one with its `.meta/example.py` and `_test.py`. Kept out of the RAG-indexed library. ⇒ **Phase 1 = the 34 Python exercises** of
  `Aider-AI/polyglot-benchmark` (1.1 MB; each exercise: `.docs/instructions.md`, stub, `<name>_test.py`,
  `.meta/example.py` = a free answer key), on-box. **Phase 2 = the other five languages**: the GPU box's order
  writes the code and sends it DOWN to rigby's Docker toolchains to run the tests. Calls only ever go down the
  layers (the owner 2026-10-05: *"Rigby should be calling down to the tablets, not up"*), so nothing on rigby calls a
  GPU box's model.
- **Builder: the 3.8, via HAI** (the owner: *"Might be a good time to try putting the 3.8 to work and have it build the
  harness"*) — packet `hard-bench\a1\packet_a1_harness.md`, submitted with `POST /absurd/start` as ClaudeSupervisor
  ([[RUNBOOK-HAI-Coding-Escalations]]). **It needs Mothership's card to itself:** one model per card, and HAI's heavy
  calls wait only for renders, not factory orders — so it runs in a window after the Strata orders, with a timed
  `mothership` hold in `menu_fill_hold.json` so the menu filler does not refill the card under it.
- **Supervision is light by design** (the owner: *"Make sure this isn't a 'Claude double checks every single and basically
  does it too' type supervision"*; the review-margin rule): the packet pins six acceptance tests HAI must pass with a
  fake model, plus HAI's own independent review; Claude checks only the test output, that the diff touches the two
  named files, then ONE real smoke order (examples pass, empty stubs fail). A failure goes back to HAI, not into
  Claude's hands.
