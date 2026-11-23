# Project 34 chat factory integration: one job per GPU, lifecycle audit and cutover blockers

Current state, 2026-10-20: core integration is staged only. No production cutover.
The original daily Strata launcher guard remains in force and is unchanged in
the staged candidate. The earlier owner-metadata exception is withdrawn.

Routing review after the owner's scope challenge: an additional staged fast-model
shortcut in app.pick_brain bypassed the existing chooser. It was an actual
integration mistake and has been removed. The original brain_order,
_brain_option, pick_brain, _LeadSlot and p20_order now match canonical
syntax trees. Do not reintroduce a factory-mode shortcut to those decisions.
The full integration is still a candidate, not certified for production.

## Shape review 2026-10-20 evening (Claude): one order per TURN is the wrong shape — use the lab's WINDOW

Adversarial review of the staged candidate against the existing code. The fencing, replay, release check and the
restored chooser are sound and worth keeping. The SHAPE is wrong: `inference_orders.submit` makes every model turn
its own desk order, which breaks the 10-19 decision's own condition (*"Latency is solved INSIDE the loop"*, and
requirement 4, *"factories must not get in the conversation's way"*):

- **Per-order overhead on every turn.** A desk order costs ~12 s pick → spawn → residency → ship (measured
  2026-10-06; it is why `helper_window.py` exists), plus the factory's 5 s `QUICK_POLL_S` intake. A 100-turn HAI job
  pays it 100 times.
- **Flash-Next cold-starts per turn.** `infer_live.model_engine` launches and stops Strata for every turn. And
  `inference.live.strata` can never be advertised (it looks for a `FACTORY_LIVE_PROTOCOL` marker in a wrapper that no
  longer has it), so after cutover the agentic crown cannot be placed at all.
- **Every token crosses the CIFS desk store.** Batches POST every ≤50 ms into `QUEUE_DIR\model-turns` (the store
  `p20_queue.all_jobs` documents as ~2,100 records over CIFS); Kirk re-reads it every 0.1 s; `submit` scans
  `all_jobs()` on every turn; each turn leaves a permanent record with no retention owner.
- **Busy factories now collect desk orders every 5 s while running** (`run_compute_order` beat). Unplaced orders are
  open to any host (`p20_queue.placed_for`), so a busy box can claim work an idle box should take. How often orders
  arrive unplaced was not checked.

**Chosen shape (the lab's own answer, and the industry's — an interactive allocation with steps inside it, like
Slurm's `salloc` + `srun`, or a long-lived serving replica a scheduler admits):** one **window order per HAI run**,
placed by Spock with the normal availability judgement, holding the engine warm (Strata included) for the run —
`helper_window.py`'s existing item protocol already carries tool-calling turns (`messages` + `tools`). Kirk feeds each
turn as an item. What the window lacks: (1) **live token streaming** (today it writes the whole answer; `tb_relay`
sends it as one chunk) — salvage the candidate's fenced append/replay as that transport; (2) **yield between turns**
through the existing `LaneYield` when higher-priority work waits (B9: the between-turn handover is the decided point);
(3) **close on status** — the run ends, or #37 parks an idle run (checkpoint), never a clock. Re-opening after a park
costs the reload — which is exactly the owed menu cell "resume / reload price", so that cell feeds this design.

### Window build — factory half SHIPPED 2026-10-21 ~00:00 CT (all three GPU factories, fleet_deploy)

The contract Kirk's side builds against (`helper_window.py` docstring is the source of truth):
- `infer_ollama` `on_chunk` hook: both wires hand every parsed chunk to a callback; reply unchanged (foundry 532/532).
- **Live turn** = item with `"stream": true` → `<id>.stream.jsonl` beside it: `{"attempt", "id"}` header, then each
  chunk exactly as the engine sent it, flushed one by one; `.out.json` is still the answer and the end marker. A
  re-answered item starts the file over under a new attempt id.
- The turn names its own `think` (Kirk's API value) and may name its `model`; an effort label the window cannot send
  as itself, or a model it does not hold, is a row error — never coerced to medium, never a second model on the card.
- **Owner heartbeat** `OWNER.json` {run, period, t}: silent past 3 × the declared period (the deck's DOWN rule) ends
  the window. Unreadable = no verdict.
- **`--live` windows yield between user turns**: while `IDLE` exists (Kirk writes it only when the run waits on its
  user — never during a tool call, B9) and `GET /p20/jobs?host=<FLEET_HOST>` (read-only) shows GPU work waiting, the
  window ends; the next turn opens a window wherever Spock places it. A desk that cannot be asked never yields.
- **Stop**: `<id>.cancel` (Kirk writes it whenever a read does not finish normally) ends that generation within
  `CANCEL_CHECK_S` (1 s); the row says `TurnCancelled`. Turns also carry Kirk's `keep_alive` (Softserve's short one is
  Plex-first) and a `num_ctx` that differs from the window's placed one is refused.
Tests: hard-bench `a1\tests\test_helper_window.py` 18/18; `test_a1_delegate` 32/32; foundry 533/533.

### Window build — Kirk's half SHIPPED 2026-10-21 ~00:20 CT, SWITCHED OFF (`HAI_CHAT_WINDOWS` unset)

`chat_window.py` + one hook in `ollama_client._open_chat_stream` (orchestrator `4f696b1`, gate 2972/2; Stop follow-up
gate 2973). How it works: the brain chooser picks host/model/context exactly as before; the window is ONE desk order per
(host, model, num_ctx), `only=[host]`, priority 100 (the lab's front-load rule for a service order a live user waits
on), shared by every Kirk run. A held/refused window raises `httpx.ConnectError` → Kirk's existing failover takes the
next box (a held order is cancelled first). A run holds the window across its tool calls (`run_ledger` scope, B9); IDLE
only when no run uses it; a run moving to another model on the same box lets go of its old window there first (else a
self-deadlock in the lane). Kirk beats `OWNER.json` every 15 s (= the factory's COMPUTE_BEAT_S).
**The switch:** `HAI_CHAT_WINDOWS` = comma list of users (`*` = everyone), read at import → set in the orchestrator's
compose environment and recreate. Order: `ClaudeSupervisor` (live proof, the owner can watch) → `the owner` → `*`.
**Proven offline:** the REAL `helper_window.serve` and the REAL `chat_window` reader on one dir (scratchpad
`seam_window.py`): two turns byte-for-byte (text, tool call), effort/keep_alive/tools reach the engine as sent, Stop
ends the generation after 5 of 200 chunks, IDLE and CLOSE behave.
**Still owed:** the LIVE proof (a ClaudeSupervisor run with a tool call, a Stop, a second user turn on the warm window,
the deck showing the window order, the police quiet); Strata windows (`--strata` via infer_ollama's order-owned
lifecycle — until then a Strata turn keeps its direct path, so the switch must not reach `*` while Flash-Next is a
routed lead); a turn in flight when Kirk restarts runs to its end on the old window (bounded, not duplicated as a tool
run — run_ledger re-asks it on the new window); window artifact retention (`/tabjobs/chat-windows/`).

### LIVE PROOF #1 FAILED 2026-10-21 00:35 CT — tailing a factory-written file over Sundae's CIFS mount reads NUL tails and stale sizes

First ClaudeSupervisor turn ("capital of France", thread `2093e941826c`): the windows on BigHonker (`desk 92dc2a357082`)
and Mothership opened through the desk and generated the whole answer ("Paris"), but Kirk's reader got 0 lines on
every attempt — `stream ended without a done chunk (content_len=0)` — failed over BigHonker → Mothership → Softserve
(correctly HELD: Plex transcoding) and the run ended unanswered. Nothing else was touched. Unit tests, the seam decoy and
a local run of Kirk's real `_chat_once` all passed: every one of them read a LOCAL file.
**Root cause, reproduced cross-machine with no GPU** (`tabfoundry-jobs\chat-windows\_repro\`: the real helper_window
writing on Softserve's disk, the reader in the orchestrator container over `/tabjobs`, mount `cache=strict,actimeo=1`):
0/5 turns whole — NUL-filled tails (`JSONDecodeError` at char 0), sizes that went BACKWARDS, and the row visible before
the stream's last lines. **Fix:** each poll opens the file, `posix_fadvise(DONTNEED)` drops the client's pages, reads to
the server's EOF; a NUL tail is cut to the last whole line; a cached size is never trusted; a turn ends at the ENGINE'S
done chunk, or by the row's `stream_chunks` count when the window ended it without one. Same repro: 20/20 raw turns,
and 10/10 through Kirk's REAL `_chat_once` in the container with 0 spurious `.cancel` markers. (Also fixed on the way:
`os.open` is TEXT mode on Windows — `O_BINARY`.)
⚠ **The class:** a check that only ever reads a LOCAL file cannot see a network-mount seam (12b, and doctrine row
"identify the object actually in force"). Any reader of a file another box is appending, over this mount, needs the
same rule — or an in-band end marker plus a cache drop.

### LIVE PROOF #2 PASSED 2026-10-21 00:49 CT — the first HAI turn served end to end by a factory order

ClaudeSupervisor, thread `2093e941826c`: "Paris." from BigHonker's qwen3.5:9b through window order `f7c47dab9a88`
(`hai-win-bighonker-qwen3.5-9b-20261021-004900`). Thinking streamed live as deltas; the order showed as a card in the
conversation (#37's order cards); the deck showed the window as BigHonker's running compute order. 27.5 s total,
which includes opening the window (desk placement, claim, spawn, model load, a ~14k-token prompt read). The two proof
#1 windows closed themselves when Kirk's restart stopped their OWNER beat (the owner rule, first live firing).
**Found by proof #2b:** the next turn left the warm window for Mothership's qwen3:14b — the log said `EMBED skips
…bighonker: its card is held by hai-win-bighonker-…`. Every busy check (`place()` via `gpu_state` + `training_busy`,
`occupied_for`, `brain_host_busy`) read Kirk's OWN window order as a foreign job. Fixed with one answer,
`scheduler.own_window(host)` (the model this process's open window holds there): chat kinds keep the host; a call for
that model joins; any other model, or a window this process no longer holds, still reads busy. Tests 22/22.

### LIVE PROOFS #3–#5 PASSED 2026-10-21 01:08–01:15 CT (orchestrator `d18586d`, gate 2980/2; Tolkien rebuilt 901/0)

All as ClaudeSupervisor, thread `2093e941826c`, BigHonker qwen3.5:9b window `hai-win-bighonker-qwen3.5-9b-20261021-010838`:
- **Warm reuse:** after a restart the first turn correctly avoided the PREVIOUS process's window (not its own) and
  went to Mothership; once that window's beat lapsed, ONE window order served every later turn — "Madrid.",
  "Berlin.", "Berlin." — 8.6 s / 8.8 s end to end, first delta 2.9 s, 5–7 s model time, 66–72 chunks each, 0
  `.cancel` markers. (Cold first turn on a fresh window, proof #2: 27.5 s.) ⚠ Not yet compared against the direct
  path under the same conditions — no faster/slower claim until that one-variable measurement exists.
- **Tool call:** `fleet_status` called through the window, result read, answered; 11.5 s.
- **Stop:** pressed after 15 streamed deltas → the run returned "Stopped." 0.1 s later; the window ended that
  generation (`TurnCancelled`, row written at 282 chunks) and the card was free.
**Still owed before the switch widens past ClaudeSupervisor:** a same-conditions latency comparison against the
direct path; Strata windows (Flash-Next keeps its direct path until then); window-folder retention; the remaining
direct consumers (McCoy `/v1/systemone`, embeddings, `ollama_proxy`).

### INCIDENT 2026-10-21 ~00:37 → ~08:10 CT — three ownerless chat windows held Softserve's card overnight

Proof #1's failover tried Softserve three times; the desk HELD each for Plex. `chat_window._cancel` called
`/p20/order/cancel`, which had no branch for `held` (no host yet → `ok: false`, "collected order has no host"), and
`_cancel` never read the reply. The three stayed held, were placed when Plex finished, and ran `helper_window --live`
in folders with NO `OWNER.json` (Kirk wrote it only after a successful placement) — and a window treated "no owner
file" as an A1 lead-order window, so none ever closed. HAI's log said `chat fallback skips …softserve: its card is held
by hai-win-softserve-…` every minute all night. Found the next morning when the A/B measurement's first arm stalled.
**Second finding, same morning:** window orders carried no `rush`, so at peak price the desk held a 07:56 CT window as
deferrable work and the turn waited in line (last night's proofs ran in cheap hours, so nothing showed).
**Cleared through the front door:** stop slip for the running one, desk cancel controls for the two queued, desk cancel
for the pending one, and the stuck run Stopped — all four read back `cancelled`. **Fixed:** the desk cancels `held`
like `pending` (`app.p20_order_cancel`); `_cancel` logs a refusal; Kirk writes `OWNER.json` BEFORE submitting, so a
window placed after Kirk gave up finds a beat nobody refreshes; a `--live` window with no owner file closes; window
orders are `rush` (the quick-preview precedent — work a live user waits on bypasses the cheap-power window; chat's
direct path was never energy-gated). Tests: window 19/19, Kirk window 24/24, desk cancel 13/13.
⚠ **The class:** an order you abandon is still an order — every give-up path must either cancel it with a CHECKED
reply or leave it a way to end itself; "I raised, so it is gone" is the absence-of-claim error (class 3).

### MEASURED 2026-10-21 ~08:15–08:40 CT — window vs direct, ABA, same user / questions / box (the owner chose "measure first")

ClaudeSupervisor, ephemeral turns (no history growth), the same eight one-line questions in the same order, one
warm-up discarded per arm; ONLY `HAI_CHAT_WINDOWS` differed (env, recreate). Every turn's PATH verified (a window item
written or not); every turn BigHonker qwen3.5:9b; prompt tokens identical turn-for-turn across arms (8012, 8607, 9431…).
Rows: `Claude Home` scratchpad `ab_W1/D/W2.jsonl`.

| arm | median total | range | median first token |
|---|---|---|---|
| W1 window | 9.2 s | 8.4–11.2 | 7.8 s |
| D direct  | 16.3 s | 15.6–17.1 | 14.5 s |
| W2 window | 9.4 s | 8.3–14.1 | 7.8 s |

The ranges do not overlap and W1≈W2 (no drift). **The cause is NOT the window transport** (an inversion: the window
adds a hop): `/api/ps` on BigHonker, polled read-only through one direct turn, showed qwen3.5:9b → none (+1.7 s) →
`nomic-embed-text` (+4.3 s) → none → qwen3.5:9b reloaded (+13.2 s). **HAI's per-query embedding evicts the chat model
on the 8 GB card and the 9B reloads on EVERY direct turn (~6 s)** — a pre-existing bug in everyday chat. The window's
claim already sends embeds elsewhere (`EMBED skips …held by hai-win-…`), so the 9B stays resident. Cold window open
(first turn on a new window) costs 25–32 s vs a cold direct turn ~15 s; windows stay open across turns, so that is paid
rarely. **Owed:** the direct path's embed eviction is its own fix for any user still on it (embeds must not evict a
resident chat model — one-model-per-card, `occupied_for` semantics), independent of widening.

### FIXED 2026-10-21 ~09:00 CT — embeds now ask `occupied_for`; plain chats mark their box live; the warm-27B shortcut respects claims

- **Embed eviction** (found by the ABA): the doctrine said embeds use `scheduler.occupied_for` since 10-17; the code
  still asked `claim_for_url` (factory orders only). Now `embeddings._claim_on` = `occupied_for(host, EMBED_MODEL)`.
  And a plain chat call now holds the `live` claim for the box's own keep_alive (`_mark_live`, `_residency_s`): the
  box is "in use" exactly while its chat model would be resident. B12 (10-17) had scoped `live` to JOB calls for its
  incident, with no recorded reason to exclude chats. The full `place("embed")` was measured at 0.65–4.2 s per call —
  too heavy per query; `occupied_for` is the lab's light lookup for exactly these calls. Orchestrator `93275db`.
- **Warm-27B shortcut** (found while live-checking the above): Auto's "Mothership already warm → 27B" checked warm +
  `render_busy` only, so the `test` user's chat joined a running factory order's qwen3.8
  (`digest-test-mo-qwen38-27b-ctx125952`) and waited 303 s behind it — one job per card broken. Now
  `app._warm_lead_free()` also asks `_card_claimed` (occupied_for). The shortcut itself contradicts menu routing
  (warmth is a priced cost on the `fastest` lens, not an override) and goes when chat asks the menu.
- **Verified live 09:00–09:01 CT** (orchestrator `5fea002`, gate 2988/2): three direct `test` turns on BigHonker while
  `/api/ps` was polled — turn 1 loaded the 9B (box not yet live, embed on BigHonker); turns 2–3 kept the 9B resident
  throughout (`EMBED skips …bighonker: its card is held by hai-chat:test`), 12.8 s then **6.8 s** (direct was ~16 s).

### SHIPPED 2026-10-21 ~09:15 CT — Strata (Flash-Next) windows + window-folder retirement (orchestrator `c1c60a4`, gate 2992/2; factories 534)

- **Why Strata windows were urgent:** the agentic crown is Flash-Next on Mothership via llama-swap (`roles.crown`,
  10-18), so job-shaped turns bypassed windows; and with a 3.8 window holding Mothership, llama-swap's
  `strata_serve.py run` REFUSES ("while a factory order holds the card") — a crown job would have failed over.
- **How:** a turn to a box's llama-swap base (`config.SWAP_MODELS`) opens a `--strata auto` window at the model's measured
  context (`config.ctx_for`); `model_fit` leaves `--strata` orders to Strata's own placement, as for the menu's
  `infer_ollama --strata` orders. The window resolves the plan from the box's `strata_served.json` row (refuses rc 2 if
  the box does not serve that model at that context), frees llama-swap the way `_free_swap` does (GET /running, unload
  only when something runs), starts the engine through `infer_ollama._strata_up(frontier=False)` — strata_arm's one
  lifecycle, DAILY use, no frontier mode (RUNBOOK § FRONTIER MODE) — and stops it by any route. Turns ride the OpenAI
  wire (`_chat_openai`), the plan's sampler under the turn's options, effort read back from Kirk's
  `chat_template_kwargs`; Kirk replays the chunks as `data:` lines ending `[DONE]`.
- **Seam proven:** Kirk's REAL `_chat_once_swap` through a REAL `helper_window` (scratchpad `repro_swap.py`): content
  + thinking + tools, effort round-trips (`xhigh`, off), 0 spurious `.cancel`.
- **LIVE PROOF #6 PASSED 09:19–09:24 CT:** a think-hard turn as ClaudeSupervisor opened
  `hai-win-mothership-qwen3.8-flash-next-strata-q2-0-20261021-091941` (desk `6fe1c1167ac1`); HAI's log: `LEAD MODEL …
  qwen3.8:27b -> qwen3.8-flash-next-strata-q2_0 (policy crown, job-shaped call)`, effort `xhigh`; both passes ran on
  Flash-Next through the window ("17 × 23 = 391"), 266.9 s incl. the cold engine start; rows 21.3 s / 15.9 s, no errors;
  the deck showed the window order with the engine's own metrics (write 59 / read 634 tok/s, ctx 3k / 262k);
  llama-swap ran nothing (the window owned the engine). The retirement sweep fired live: 9 old folders retired.
- **Gap found by the proof, fixed same pass:** Strata does not unload itself like ollama, so an idle Strata window
  would hold Mothership's card AND RAM indefinitely. A `--strata --live` window now ends after llama-swap's own daily
  idle rule (`ttl: 300` = the box's 5m keep_alive), measured from Kirk's `IDLE` marker (`STRATA_IDLE_TTL_S`).
- **Retirement:** an ENDED window's folder (whole conversations on the shared jobs share — transport copies of HAI's
  history) is removed by its own name by Kirk, the caller that sees the end (doctrine 10); a sweep also takes a
  previous process's ended windows (CLOSE, or an owner beat past 3 periods). Could-not-tell = left alone.

## Understand why the rule exists before changing it

the owner's standing instruction: "if you want to change a rule, you must first
understand why it was made that way." Recorded in the workspace AGENTS.md.

The one-lane rule prevents the documented two-workers-on-one-card collision,
not just duplicate queue records. P20-FACTORY-LAYOUT-GREEN identifies the
2026-07-27 incident and requires mutual exclusion. P20-FOUNDRY-RUNNER-PLAN says
orders are consumed serially. The GPU police runbook explains the secondary
failure: an unregistered workload starves the legitimate worker and corrupts
the daemon's interpretation of performance and card health. The HAI surface
design explicitly requires one model and one slot per box; concurrency comes
from different boxes in the fleet.

A matching ownership record cannot establish that no other workload is running.
A dead request worker cannot establish that a separately managed engine stopped.
Those are the original failure modes this integration must preserve protection
against, including completion, Stop, error, restart and transfer to another host.

## Contract review

| Contract / existing owner | Staged implementation and evidence | Current disposition |
|---|---|---|
| One intake; Kirk/Spock/factory split | inference_orders submits through app.p20_order; worker fetches private input using the existing P20 worker token. Factory performs model I/O; Kirk retains tools and its ledger. | Mocked order/API/adapter tests pass. No direct-model fallback in these modules. |
| One local order runner / lane | Existing run_queue, singleton, claim-before-stage, start checks and serial run_compute_order remain the executor. | Preserved; not a second runner or queue. |
| Strata order-owned lifecycle | infer_live.model_engine now calls the existing strata_arm prepare/install cache, enter(frontier=False), launch and stop_pid; engine is the order worker's child. | Corrected in staging. No daily-serving guard exception. |
| Strata launch while already held | Existing port_free refuses a held configured port before enter/launch. | Mocked occupied-port case starts nothing and unloads nothing. |
| Strata shutdown before lane release | Worker stops and waits for its child. Factory independently checks the real engine image and configured port before clear_current_run. | Remaining process, held port and inconclusive/error reads raise the existing TrainerUnstoppable fate. Actual compute-runner test asserts no clear, retirement or receipt on that route. |
| OS evidence, not observer bookkeeping | Reuse ps_process_query; CIM errors are terminating errors rather than successful empty output. Installed Mothership launcher and engine tree were read without executing Strata. | strata.exe identity confirmed. CPU-only Windows image-name decoy verifies detection while alive and absence after exit. No GPU decoy or image generation. |
| Native Ollama cancellation / release | Interrupted work now reuses the existing unload channel, requires its acknowledgment plus a valid empty resident-model response, and checks the existing runner-allocation sample against the existing release handshake. Unknown/retained allocation holds the lane. Completed turns retain their warm cache. | Corrected in staging; 13 native cases include the real compute-runner release boundary. Positive gpu_ok=False retains the existing UNAVAILABLE/hand-back semantics after acknowledged model shutdown, instead of waiting for unreadable counters on a lost device. No unknown shutdown is excused. Real factory/runtime proof remains required. |
| Factory-contract checklist: dealing | A live turn is one order, not a parallel items deal. It declares actual model, engine and window through model_memory and uses normal Spock admission. | No per-item size reserve is inferred. Preserve the single-order interface; parallel dealing would need its own checklist treatment. |
| Factory-contract checklist: Stop ships what exists | Each claim has an original-line spool under ORDER_OUT; committed desk response bytes survive a reader restart. Existing Stop controls and factory shipping remain the control/retirement path. | Mocked raw-line, replay, incomplete-response, cancellation and claim fencing tests pass. On-box Stop/transfer proof remains required. |
| Factory-contract checklist: progress / supervision | Existing factory EngineRates can read engine_live.json and engine logs. Kirk retains the load-aware silence watcher; queue wait does not spend the model clock. | PARTIAL. Explicit on-box supervision of long silent native preparation/read must be demonstrated; do not substitute timer-thread liveness for work progress. |
| Factory-contract checklist: artifacts | Desk artifacts are inside the existing queue's non-scanned model-turns subdirectory; payload output is in existing ORDER_OUT. Each retry has a unique physical order name. | Crash/append atomicity and replay covered. Final retention/retirement of all model-turn artifacts still needs an owner; no silent janitor added. |
| Factory-contract checklist: capabilities | inference.live.ollama is conditional on the payload. The restored Strata wrapper has no FACTORY_LIVE_PROTOCOL marker. | Strata live capability intentionally remains unadvertised. Remove the obsolete wrapper-marker dependency only after the order-owned path has all required proof. |
| Factory-contract checklist: priority consumer yielding | Existing queue rank, queued_orders, LaneYield and run_queue handler are unchanged. The staged canonical inference payload now returns a typed yield only after a saved item; its parent routes that into the existing LaneYield path without reporting completion or retiring it. Existing beat thread collects desk work on the already-defined quick-intake cadence while status keeps its own cadence. | Staged bridge only. All three installed compute paths were verified to return before the existing training yield logic; original batch code has no between-item yield. An unmodified-payload negative control completes both mocked rows instead of lending after the first, demonstrating this narrow gap. Resumption keeps the first answer. No new router or priority policy was built. Real factory hand-off proof is still required. |
| Energy permission | _intake no longer sets rush=True for everyone; it uses the shared queue's documented admin-rush permission. | Two new intake tests distinguish admin from ordinary users. No peak-price gate disabled. |
| User pins / measurement invariants | Authorized explicit host pin is carried to intake; the existing ghost eval_model comparison now carries its original brain-box pin. Actual served host/model remains response evidence. | Four additional front-door context tests preserve same-box comparison and force permission. |
| Measured context on a rerouted box | The broker preserves the request's explicit window, which was selected using the nominal box before order placement. Fit admission checks that window on the actual box. | OPEN FUNCTIONAL CONTRACT: a rerouted box's larger measured maximum is not automatically selected. Resolve through existing Spock/context selection, before compaction, rather than inventing a cap or silently changing the request after it was checkpointed. |
| Checkpoint / tools / restart | Stable checkpoint + request fingerprint reattaches; claim revisions isolate reroutes; original tool ledger and parser remain authoritative. | Mocked reuse/claim-change/cancellation tests pass. Real front-door restart/Stop proof still pending. |
| Consumer enumeration | Native chat/generate and review helpers use the new adapter. McCoy /v1/systemone, embeddings._embed and ollama_proxy.forward still have direct model paths. comfy_client performs existing unload controls. | OPEN CUTOVER BLOCKER. A claim-based precheck in embeddings is not atomic admission. Do not claim all model traffic uses orders. Distinct measured protocols must be carried through the same door. |
| Installation latency / cache | Verified installed-shard cache is reused from strata_serve; no setup/hash pass is invented for every turn. | Cold start/stop per Strata order still costs time. **Measured 2026-11-16 on Mothership:** every Strata window START full-hashes the model shards (`strata_arm.prepare`, ~66 GB, ~90 s of a 166 s time to first text; setup 3 s, engine up 43 s, turn 25 s). Fix in progress (Doc, foundry workspace): a size+mtime record of a verified shard lets the daily path skip the re-hash; measurement starts keep the full hash. Any reuse must stay inside factory ownership and preserve quick-priority boundaries. |
| Deployment | Canonical/live source hashes remain at the original integration baseline. No factory deploy or orchestrator activation performed. | Full fleet_deploy / orchestrator runbook, runtime hashes and front-door proof are still required after blockers close. |

## Validation evidence and limits

- Windows foundry gate: original 530 cases plus 49 relay/lifecycle/release/yield
  cases, integrated into the existing monolithic test_foundry.py with
  EXPECT_TESTS=579. The standard entry point passes 579 tests and its collection pin.
- Orchestrator: the actual run-suite.sh container invocation was used with
  read-only staged source and complete test overlays, preserving its state
  redirections and notification-secret isolation. 3,003 tests passed, two skips,
  exactly the staged 3,003/2 pin (baseline 2,958 plus 45 new cases).
- Read-only installed-source verification: p20_foundry, infer_ollama and
  helper_window match canonical hashes on Mothership, BigHonker and Softserve;
  stated runner PIDs started after their source files. Running orchestrator
  app/client/scheduler match canonical; the proposed inference_orders module is
  absent there. No integration deployment occurred.
- Exact component reuse was checked by syntax-tree comparison: brain chooser,
  existing Mothership slot, fleet intake, queue/rank, LaneYield, queue runner and
  factory order/control collectors are unchanged. Their new call connections
  still need end-to-end runtime proof; component equality alone is not that proof.
- A focused mocked routing control passes with the restored chooser and fails
  with the exact withdrawn shortcut: that shortcut does not call brain_order.
  This is a real defect the earlier green suite did not cover, not a claim that
  every staged change is correct.
- Negative controls: disabling the new factory release check in memory makes both
  real compute-runner boundary tests fail by assertion, with no unrelated errors.
  The mutation never touches a source file and is restored before the test process exits.
- Earlier approximate runs are retained as failed diagnostic artifacts. Missing
  fixtures, missing gate environment and sandbox-denied Windows CIM/loopback
  reads were not treated as production failures or worked around by changing
  application policy. The exact gate and appropriately permitted Windows unit
  environment resolved those test-environment defects.
- Green suites do not close the open rows above or establish live exclusivity.
  No image was generated for this audit. No patrol exemption or stand-down flag
  was added. No production guard was relaxed.

Sources: OPERATING-BRIEF; all four Doctrine-* reference pages;
P20-FOUNDRY-RUNNER-PLAN; P20-FACTORY-LAYOUT-GREEN; P20-MULTI-FACTORY-FLEET;
12-CAMPAIGN-CONCLUSION; HAI-Agent-Surface-Design; 37-Closeout;
RUNBOOK-Compute-and-Training-Order-Submission (factory-contract checklist,
Strata order lifecycle, helper windows, Stop and host-priority yielding);
RUNBOOK-Ollama-Host-Operations; RUNBOOK-GPU-Police-Operation.
