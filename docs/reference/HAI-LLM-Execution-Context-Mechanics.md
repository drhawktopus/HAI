# HAI Orchestrator - LLM Execution & Context Mechanics



## Model-call path — `ollama_client.chat()` (THE critical mechanics)
- Signature: `chat(host, model, messages, tools_list, num_ctx=None, status_callback, user, keep_alive)`. Runs an **agentic tool loop**: with any `coding_*` tool present it has NO step count and ends on the answer, `config.CODING_IDLE_TURNS` repeat-only turns, or the `call_context` `loop_deadline` (since 2026-10-05); otherwise up to `config.TOOL_LOOP_MAX` steps.
- Each step = `_chat_once()` → Ollama `/api/chat` with `options={"num_ctx": num_ctx}`. **`num_predict` is NOT set** (Ollama default output budget).
- On HTTP/ValueError it **downshifts** the window and retries: `context_attempts = [requested, max(8192, requested//2), 8192]` (`config.CHAT_RETRIES`).
- If the model returns `tool_calls` → executes each via `tools.run_tool()`, appends `{"role":"tool",...}`, loops. If no tool_calls → returns the answer.
- **Truncation/overflow detection:** if the answer is < 40 chars it logs `SHORTANS host=… done_reason=… prompt_tokens=… eval_tokens=… content=…`; if < 2 chars it raises `ChatError("model returned a truncated answer: '')`. **A "truncated answer: ''" almost always = the prompt filled the window, leaving no room to generate** (not a model-quality failure).

## Context-window arithmetic — WHY self-authoring overflows (key 2026-06-29 finding)
- `config.NUM_CTX = 8192` (generic fallback), `config.BRAIN_NUM_CTX = 39936` (BigHonker 9B), **`config.HEAVY_NUM_CTX = 125952`**
  (Mothership qwen3.8:27b). **Since 2026-10-19 HAI READS every model's window from the policy table** -
  `policy.json` → `contexts` {box: {model: ctx}}, written by `menu_policy.py` from `menu_fill.TESTED_CTX` (the largest
  context measured fully resident - the spill-aware 0.40.5 re-fit, `campaigns\fit-20261015` - which is also the window
  every menu cell was scored at). `config._measured_contexts()` reads it once at import into `MEASURED_CONTEXTS`;
  `BRAIN_NUM_CTX` / `HEAVY_NUM_CTX` / `MODEL_NUM_CTX` derive from it; an explicitly set env var still wins; an unreadable
  table falls back to the in-code values, which hard-bench `test_ctx_stores_agree.py` keeps equal to the fit (and
  `triage_local.json` too - the factories run as SYSTEM and keep their own file). A new fit reaches HAI on its next
  restart, with nothing to hand-copy (the owner: *"Why are we not just selecting the largest context that we have tested
  work well for each model?"*). Live value: `GET /health` → `heavy_num_ctx`.
  Every other call (critics, think-hard, review, overnight, VLM, failover) sizes its window by MODEL via
  `config.ctx_for(host, model, fallback)` from `config.MODEL_NUM_CTX` (RUNBOOK residency map); the old per-path
  constants (`CODING_REVIEW_NUM_CTX` 16384, `ADVERSARY_NUM_CTX`, `OVERNIGHT_PLAN_NUM_CTX`, `CLAIM_REVIEW_NUM_CTX`) are
  only fallbacks for a model the table has not measured.
  > ⚠ **CORRECTED 2026-08-14: this said 32768. Live value verified in-container is 65536.**
  > **The difference is this section's OWN recommendation, implemented and never written back** —
  > see the 2026-06-29 probe below, which ends *"Recommendation: raise HEAVY_NUM_CTX 32768 → 65536"*.
  > Classified STALE DOC, not a rogue deployment.
  > ⚠ **This matters for how the probe below is read:** the "0 tool calls, pure narration" result was
  > measured at **32768**, i.e. the PRE-FIX state. A narrate-instead-of-execute failure seen TODAY
  > (lead pass at 65536) is **not** explained by that finding, and reaching for it is a live trap —
  > it cost a session on 2026-08-14, which dismissed a real finding by crediting this stale line.
  > **Current related values, verified in-container 2026-08-14:** `CODING_REVIEW_NUM_CTX = 16384`
  > (the critic/review path) and `THINK_HARD_CRITIC_POOL = ['qwen3.6:27b', 'qwen3.6:35b-a3b']`, so a
  > think-hard turn runs its LEAD at 65536 and every CRITIC at 16384, across two models.
- **`HEAVY_TOOLS` JSON schema ≈ 13,000 tokens** (just defining the tools).
- **`app.py` ≈ 30,300 tokens** (2,189 lines, 121 KB). `tools.py` ≈ 82 KB, `hostops.py` ≈ 14 KB.
- ⇒ **The instant HAI reads its own `app.py` to edit itself: ~13k (tools) + ~30k (app.py) ≈ 43k > the 32k window** — before history, brief, thinking, or output room. This is the root of the "27B can't self-author" failures seen in Project #12 (empty/`truncated answer: ''`, and narrate-without-editing). It is **largely arithmetic, not raw capability.**
- **Levers to fix (in priority order):** (1) **bigger `HEAVY_NUM_CTX`** (64k–128k; needs Mothership VRAM headroom — use q8_0 KV cache as done on BigHonker; bigger MoE model like `qwen3.6:35b-a3b` for long overnight runs per the Phase-5 decision); (2) **trim the self-eng tool set** (don't load all ~13k of `HEAVY_TOOLS` when only `coding_*` are needed); (3) **targeted reads / split `app.py`** so HAI reads a function, not 2,189 lines; (4) keep deploy-driving requests `ephemeral` to drop history.
- **EMPIRICALLY CONFIRMED 2026-06-29 (probe, Claude):** same ~43k self-edit prompt (full `app.py` + `HEAVY_TOOLS`), one model turn: `qwen3.6:27b @ num_ctx=32768` → Ollama truncated the prompt to **16,386 tok** (`app.py` cut off) → **0 tool calls, pure narration** (= the exact failure we hit). `qwen3.6:27b @ 65536` → full **44,857 tok** fit → **1 real tool call (executed)**. `qwen3.6:35b-a3b @ 65536` → same, executed. ⇒ **the narrate-vs-execute gap is primarily window truncation, not model capability**; the plain 27B loads + runs at 64k on the 24 GB card. **Recommendation: raise `HEAVY_NUM_CTX` 32768 → 65536 for the self-engineering path** (per-path, not global — 64k on ordinary chat wastes VRAM/speed). Caveat: this proves it *starts* executing; multi-step coherence over a long task is a separate test.

## GPU collision serialization (2026-07-23, driver root-cause aftermath)
Root cause (see [[Driver-RootCause-Phase0-Exhibits]]): concurrent generation on a second
GPU context during a big-latent render poisons a context. Mothership-card consumers enumerated from
this doc + config: force_27b chat, auto-escalation, VLM_HOST (vision), ADVERSARY_HOST (critics),
CODING_REVIEW_HOST, self-eng continuation passes, generate_image, P20 renders. Serialization layers:
1. generate_image -> SAME ComfyUI queue as P20 renders (:8189; :8188 retired 2026-07-23; E:
   checkpoints via extra_model_paths.yaml "generic_estore").
2. Interactive chat paths: pick_brain render_busy() downgrade (routing-time, force_27b + escalation).
3. **Everything else: dispatch-time collision gate INSIDE ollama_client.chat()** — host==HEAVY_HOST
   + render_busy() => bounded wait (300s cap, 10s poll, one status note, notes.log on release);
   NEVER fails the call. Covers VLM/adversary/review/self-eng passes + any future caller.
4. Render side: worker v1.11 unload_ollama() VERIFIES /api/ps empty (120s bound) before rendering —
   a mid-flight chat delays the render, never collides with it.
5. Safety net: daemon v4.9 wedge verdict -> context kill (~30s cure) + DISCRIMINATOR log line.
6. CorridorKey (installed F:\CorridorKey, 2026-07-23): GPU use ONLY via
   `F:\CorridorKey\corridorkey_gated.py <clips-folder>` (master: mothership-build) — writes
   `C:\ProgramData\HAI\corridorkey.hold` (ISO-8601 expiry, 15-min TTL, 5-min refresh, crash-safe)
   then waits for 2x30s quiet card (trainer + foundry phase + :8189 queue). Worker v1.13 defers
   new renders while the hold is unexpired (garbled = fail-open) and heartbeats state "busy".
   GPU-ENABLED 2026-07-23 (same day): production 7.15 torch family vendor-transplanted into the
   CorridorKey venv (rebuilt on py3.12 to match cp312 binaries; ~9.4GB family robocopy; verified
   54.9 TFLOP/s bf16 solo). Launch ONLY via the gated wrapper with `uv run --no-sync` — a bare
   `uv run` re-syncs the lockfile and silently reverts to CPU torch. Wrapper v1.1 also checks +
   unloads resident Ollama models pre-launch (worker v1.11 pattern).

## Tool selection — which tools reach the model, and how to drive it

Before every model call, `ollama_client.chat()` runs `tool_selection.select(tools_list, user, text,
num_ctx, ...)`. The **orchestrator, not the model, decides which of the ~86 tools go in the box.**
This is why the 8B is usable at all: the full `HEAVY_TOOLS` schema set is ~16k tokens — 100% of the
8B's 16,384 window — and selection cuts it to a per-request subset (measured 6-31%).

**Three signals, combined:** (1) semantic retrieval over each tool's description (`nomic-embed-text`,
disk-cached — the description IS the retrieval document, so every token in it is charged and matters);
(2) the asker's own corpus namespaces; (3) in-flight state (an active review boosts its tools). Scores
are read as a LEAD over each query's own median, never as a raw cosine. When the chosen tools disagree
with each other, or the signals pull apart, `select()` returns a note telling the model to **ASK
rather than guess.**

**The line this holds:** the orchestrator NARROWS (retrieval + corpus + state); the model PICKS from
the narrowed set. Not a hardcoded keyword router (a regex knows match/no-match, never UNSURE), and not
all-86-tools-on-the-model (context saturation).

**Which lists are narrowed, and how many tools survive (since 2026-10-19, orchestrator `848de54`):**
- **The GENERAL pools (`HEAVY_TOOLS`, `BRAIN_TOOLS`) are narrowed on EVERY tier, the 3.8 included.** `tools.py`
  registers them with `tool_selection.register_pool()`. Before this, a pool that merely FIT the window passed whole,
  so every 3.8 request (122,880 window) carried every schema - ~14k prompt tokens re-read per call, "pong" included
  (the owner 08-26: *"it's not supposed to see all the tools at once, it's supposed to see sets"*).
- **Purpose-built SETS pass whole:** `CODING_JOB_TOOLS` and `CODING_SURVEY_TOOLS`, registered with
  `register_set()`. Sets and pools are told apart BY NAME, never by size. Any other caller's own list passes untouched.
- **No tool-count cap** (the owner 2026-10-19: *"No arbitrary limits on tools, I want to get all of the relevant and
  necessary tools for the job or task"*): every tool that clears the relevance floors (`TOOL_SELECT_MIN_LEAD`,
  `TOOL_SELECT_TOP_FRAC` x the top lead) is handed over; CORE is always in; the only ceiling is the context budget
  (`TOOL_SELECT_BUDGET_FRAC` x the window), which binds on small windows, never on the 3.8. `TOOL_SELECT_MAX_TOOLS`
  (24) is gone.
- **Index outage:** if embeddings are unreachable and the whole pool fits the window (the 3.8), the model gets EVERY
  tool; only a window the pool would saturate falls back to CORE.
- Live check 2026-10-19: a 3.8 "pong" got 11 of 63 tools (~2.3k tokens) - 11 cleared relevance.
- **The instrument: `retrieval_probe.py`** (orchestrator mirror root, baked into the image as `/app/retrieval_probe.py`;
  run `docker exec -w /app orchestrator python retrieval_probe.py <user> <BRAIN_TOOLS|HEAVY_TOOLS> <num_ctx>`). It
  replays every saved chat of the user turn by turn as Kirk serves it (PRECISION: how many follow-up turns grow the
  tool block, and which tools do it) and runs named RECALL controls both fresh and as a mid-chat follow-up, plus a
  negative control. It was cited here before it existed (rebuilt 2026-10-21). Put a candidate `tool_selection.py`
  beside it in a temp dir and run from there to measure a change before deploying it.
- **A conversation's tool block grows only on the selector's own verdict (2026-10-21).** Chat keeps the tools a
  conversation was sent (append-stable, `ollama_client.sticky_tools`), and the block renders BEFORE the conversation,
  so growth of the block itself re-reads the whole conversation (Mothership qwen3-14b: 29 -> 34 tools, 17,811 tok re-read
  in 10.3 s) - which since 2026-11-14 only happens on an engine that cannot season (next bullet).
  Starfleet still picks a set every turn (the owner 10-21: *"a dynamic toolset that is chosen by starfleet"*); a SURE turn
  adds every pick, an UNSURE turn (the case the note answers with ASK) adds only the strongest tool on its own words
  and only when that reading is sure (it changed the subject), else nothing. Measured on the owner's 125 follow-up turns:
  growth **56 % -> 14 %** (BRAIN_TOOLS @19,968; HEAVY @122,880: 57 % -> 16 %), recall controls 0 of 10 missed,
  "thanks, that's all" no longer pulls `code_*_cleanup`. Rejected on the same replay: the held set as in-flight state
  (an image request got no render tools) and "unsure keeps the block" (a mid-chat topic switch lost its tool).
- **Thaw, then season: on a proven engine a held block never grows (2026-11-14).** the owner: *"why can't it just resume, and
  once it's resumed it gets a little update ... thaw it out and then start cooking"* and *"the running models only got
  tools at the beginning of the conversation ... not really dynamic in session"*. Three parts, all in
  `ollama_client.sticky_tools`: (1) the block a conversation was sent is kept on disk with its exact text
  (`State\tool-blocks`) and resent verbatim, so a Kirk restart or a deploy that rewords a tool changes nothing above the
  conversation; (2) a held tool whose wording changed is announced once at the END of the turn (`TOOL_UPDATE_NOTE`);
  (3) a tool the selector adds to a held conversation is DESCRIBED at the end of the turn (`TOOL_ADDED_NOTE`, log line
  `TOOLS SEASONED`) instead of joining the block, remembered as that conversation's seasoned tools, and its call is typed
  by Kirk from the tool's schema (`_typed_args`: the engine had none). **Only where the engine parses a call to a tool it
  was not listed** (`can_season`): ollama models whose Modelfile says `PARSER qwen3.5` or `qwen3-coder` (the 3.8 27B, the
  3.5 9B and 35B) and Flash-Next. A model with no parser line (BigHonker's `qwen3.6:35b-a3b-ud-q3kxl-mtp`, `qwen3:8b`)
  and rigby keep the growth above, one re-read; a conversation that moves to such an engine takes its seasoned tools into
  that engine's block. A job's workspace open still REPLACES its block with the coding set (11-12), which is a different
  rule. Design, evidence and proofs: [[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] § "Thaw, then season".
- **The master start: a new chat's block is the always-offered core, and its picks ride in the first message
  (2026-11-14, the owner: "Let's build the master chat").** Same rule as growth, applied to turn one, so every new chat of a
  user on a box begins with the same tokens and the window's saved start covers the tools and the system text (5,120
  tokens already in the engine on the 27B; log `MASTER START`). Not a job, not an index outage, not an engine that
  cannot be called by description; a picked `render_person` stays in the block. Only the prompt is shared: no request
  carries a seed. **A conversation is its chat id and its opening** (`ollama_client.conversation_key`): two chats that
  open with the same words are two conversations, and a first turn Kirk knows only by its opening starts its own - once
  tools ride in the conversation, taking a second chat for the first leaves it without its definitions.

**Failure mode — similar-verb tool families bunch.** Tools whose descriptions lead with the same
action all retrieve together, so ALL are handed to the model and the discrimination among them falls
to the model, which is sensitive to phrasing. The `library_*` restructure family
(`propose_split` / `apply_split` / `carve_h1` / `check_refs` / `draft_index`) is the worked example:
a generic ask ("apply the split for X") can let the model pick `propose` over `apply` because both
lead with "split a domain". The fix is sharp, stage-distinct descriptions (propose = draft/preview,
changes nothing · apply = commit a reviewed draft) so retrieval and the model separate them — never a
hardcoded override.

**Operator discipline when DRIVING tools (deterministic flows / machine users):**
- **Name the exact tool + args** ("Call `library_apply_split` with domain=X"). Do not rely on the
  model to disambiguate a similar-verb family from a generic ask — the ASK-when-bunched safety cannot
  help a machine user (no human to ask mid-flow).
- **A write `/query` may return `route:error` while the tool STILL registered its pending action** —
  the model can fail to assemble its final reply AFTER the tool ran. Check `/pending`; do not blindly
  retry, because a retry can fire a DIFFERENT tool of the family (e.g. re-running `propose`
  regenerates a draft you had already reviewed).
- **pending→confirm is a verification gate: READ the preview against your expected plan/counts BEFORE
  confirming.** Never issue preview and confirm as one step. A preview that does not match your
  reviewed plan means the underlying draft/state changed under you — stop and re-review, do not
  confirm.
