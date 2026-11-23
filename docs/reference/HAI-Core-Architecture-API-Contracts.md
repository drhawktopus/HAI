# HAI Orchestrator - Core Architecture & API Contracts



## Components & ports

> **NAMES (the owner, 2026-08-07): the orchestrator is KIRK, the scheduler is SPOCK, and the tablet
> Fellowship's head (the `tablet-foundry` order-taker role) is TOLKIEN.** The responsibility split
> the names carry: **Kirk + Spock own ORDER DECOMPOSITION and placement** — `compose_steps()`
> chunks one complete order into steps and places each on the factory whose declared capabilities
> match that step's `requires`. The **Fellowship is ONE placement target** to them; **Tolkien alone
> distributes work inside it** (see [[Tablet-Cluster-INDEX]] §head).
>
> ⚠ **CORRECTED 2026-08-14. This section said TOM and JERRY.** the owner renamed the pair to Kirk/Spock
> on 2026-08-07 *the same night*, and 2026-08-07-claude §NAMES filed the doc fix as
> **STILL PENDING**, warning: *"correct them next pass so the library isn't self-contradictory — a
> superseded name un-cross-referenced re-teaches the wrong one."* It was never done, and on
> 2026-08-14 it did exactly that: a session read this doc and reported "Tom + Jerry" to the owner as
> current. ⚠ This file is in `Reference\` and therefore **RAG-indexed**, so a stale name here is
> served to HAI as well as to sessions.

- **orchestrator (TOM)** — FastAPI (`app.py`), the brain/chat/RAG/tool-loop. Host port **8099** (`curl http://localhost:8099/health`). Container also listens 8099.
- **coding-runner** — sidecar (container port **8100**, arrnet only). Owns the coding workspaces (`/app/workspaces/`: `sample`, `orchestrator-pilot` = sanitized self-copy, **`orchestrator-real`** = the deploy source), and the durable **task / project / decision / checkpoint** stores. Reached via `coding_client.py`.
- **hostops** broker — privileged external container (port **9120**, arrnet only, auth header `x-hostops-token`, token at `/opt/arrstack/secrets/hostops_token`). Runs host ops + the **self-deploy state machine** (`hostops_deploy.py`). Reachable from inside orchestrator via `hostops._request(...)`.
- Routing doctrine: **BigHonker (8B) → Mothership (27B failover/heavy) → Softserve (last resort)**. `/health` reports `self_engineering_phase`.

## Internal API signatures (the exact contracts — REFERENCE these, don't guess the args/returns)

### `POST /fleet/run-state` — how a factory DECLARES itself and its capabilities
*(contract written down here 2026-08-06; it existed only in `app.py` and cost a spelunk to find.
RUNBOOK explains WHY it is a push — a scheduled task can write the share, but a rolling-TTL claim
frees its own card when a run dies, which a file never can.)*

`app.py: class RunStateReq(BaseModel)` — **`host` and `name` are the only required fields**:

| field | type | default | notes |
|---|---|---|---|
| `host` | str | — | **required**; the per-host key in the stored map |
| `name` | str | — | **required**; run name. A factory announcing itself IDLE posts `name: ""` |
| `state` | str | `"training"` | |
| `total_steps` / `step` | int | `0` | |
| `eta` / `phase` / `started` | str | `""` | |
| `rescue` | dict | `{}` | |
| `ttl` | int | `180` | rolling claim; a dead run stops refreshing and frees its card |
| `capabilities` | list | `[]` | **what this factory can BUILD**, derived by DETECTION, not configured |

Stored per-host into `HAI_FLEET_RUN_STATE` (default `config.STATE_DIR/fleet-run-state.json`, i.e.
`/docs/State/fleet-run-state.json` in production; since 2026-09-26 every orchestrator write store roots at
`STATE_DIR` = env `HAI_STATE_DIR`, which the deploy gate points at `/tmp` so the test suite can never write
this file) by `_fleet_run_write`; read back by `scheduler.factory_capabilities(host)`.

⚠ **ONE FILE, MANY WRITERS — written under a lock with an atomic replace (since 2026-10-17, `60356cc`).** Every
factory pushes every ~20 s and each push rewrites the whole map from FastAPI's threadpool. It used to be rewritten
IN PLACE (`open("w")` then dump), so a read that landed mid-write saw half a file: the deck (`_fleet_run_all`) read
`{}` and Mothership's tile showed **`free` for one poll with A2 at 16/27**; `scheduler.training_busy` (Spock's
busy-box guard, which reads the same file) read the busy box as NOT busy; and a push that read the half file wrote
back only ITS host, dropping every other factory's record until each pushed again. Negative control on Linux against
the old writer: 24 concurrent hosts → 1 survivor, hundreds of guard misses. Now: `_FLEET_RUN_LOCK` around the
read-modify-write, `os.replace` of a temp file (readers see the old or the new file, never half), and an unreadable
store shows the LAST GOOD copy on the deck, never "nothing running" (a never-written store is still `{}`).
`training_busy`'s "unreadable = no verdict" stays as designed (`test_stated_run_guard`: claims and health still
guard) — the atomic write is what removed the race. Tests: `tests/test_fleet_run_store.py`. ⚠ On WINDOWS the
concurrency test fails (a file open for reading cannot be renamed over there); the orchestrator runs on Linux.

⚠ **OMIT fields you do not have; never send an explicit `null`.** Every field is a concrete type
with a default, so a missing key uses the default — but an explicit `null` fails validation. That
exact trap (`RescueStateReq.last_tflops: float = None`) rejected every rescue-daemon push for
**eleven hours** while a hand-rolled probe from the same box returned 200, because the probe omitted
the key. Proving YOUR payload works proves nothing about THEIRS — send the real one, read the status.

⚠ **An IDLE factory must still publish.** Capabilities used to ride only during-run pushes, so a box
idle since before the feature existed reported nothing, the scheduler saw `0 caps`, and could place
no work at all — unroutable exactly when available.

### `GET /hai/inflight` — every Ollama request Kirk has open, by box (added 2026-09-27, the "no blind spot" registry)

Registered INSIDE the request (`ollama_client.track`, wrapping `_chat_once`, the one function every
`/api/chat` crosses; the side doors — embeddings, VLM reads in `genetics`/`identity`, the pre-render unload —
wrap their own calls), so a caller cannot put load on a box without a row here. Unauthenticated like
`/hai/thinking`; app-authored strings only.

| field | meaning |
|---|---|
| `active` | number of open requests |
| `rows[]` | `host` (bighonker/mothership/softserve or the URL host), `model`, `kind` (chat/embed/unload), `purpose` (e.g. `eval_step coding-01 r1`, `absurd deep-think`, `chat`), `user`, `elapsed_s` |
| `by_host` | `{host: {calls, models[]}}` |
| `tile` | `{host: "N · model"}` strings for the dashboard, `"0"` when nothing is open |

`GET /hai/thinking` reads **Idle only when BOTH registries are empty** (the cancel-handle registry of
user queries AND this one); otherwise `state: busy` with `who` = user or purpose and `stage` =
`purpose · model on host`. `GET /llm/<host>` carries `inflight` ("N · purpose") and `inflight_n`. Purpose
labels come from `ollama_client.call_context()`; `_run_query` sets one per query kind
(`_query_purpose`). Pin: `tests/test_inflight_registry.py`. (Eval campaigns no longer appear here at all
since 2026-09-27: they are foundry compute orders, so Kirk makes no call for them — see the next section.)

### `eval_campaign` via `POST /p20/order` — an evaluation is COMPOSED COMPUTE ORDERS, never a Kirk call (changed 2026-09-27)

the owner, 2026-09-27: *"Kirk is literally supposed to be submitting orders to the foundry. There is no other
option."* Kirk validates the campaign, writes each batch's items to the share, and composes **one
`infer_ollama` compute order per (kind × pin-batch of ≤ 25)** through `scheduler.compose_orders`; the box's
own foundry collects and runs it (claimed, on the factory tile, `accounting: accounted · foundry order: …`,
stoppable with the standard slip). The old executor (`_eval_step_worker` → `_run_query(ephemeral=True)`,
which called the box's ollama around its foundry) is **retired**; a behavioural pin rigs `_run_query`,
`ollama_client.chat` and `_chat_once_raw` to explode across the whole eval lifecycle.

| spec field | contract |
|---|---|
| `kind` | `"eval_campaign"` (routes to `_eval_campaign_intake`, never to ordinary composition) |
| `name`, `user` | name required; `user` must be an admin identity |
| `pins[]` | 1..50 of `{id, kind: chat\|chat27b, effort: normal, prompt, prompt_sha256}` — sha of the verbatim prompt, checked |
| `rounds` | 1..10 → the payload's `--rounds` (one order, not one step per round) |
| `control`, `evidence` | positive-control pin + `expect` + `rationale`; `require_served_identity`, `require_deep_trace`, `void_on_ctx_downgrade` all `true` (unchanged) |
| `requires` (body or spec) | **`["model.<name>"]` is HONOURED** — overrides the kind's model for every pin; two model pins = refused `ambiguous_model_pin` |
| `num_ctx` / `temperature` / `seed_base` | optional; default = the kind's production context and the model's own sampling |
| `only` / `exclude` (body) | hard routing, same as any order |
| `eval_user` | ignored (no Kirk pipeline runs); accepted so older specs still validate |

**Kind → model:** `chat` → `config.BRAIN_MODEL` @ `BRAIN_NUM_CTX`; `chat27b` → `config.HEAVY_MODEL` @
`HEAVY_NUM_CTX`; each order `requires ["order.compute", "model.<model>"]`, so the desk places it only on a
factory whose ollama declares that model.

**Typed refusals** (`{"ok": false, "refused": <type>, "error": …}`, nothing queued, no files written):
`invalid_spec` · `effort_unsupported` (**`think_hard` is refused** — the order runs the model on the prompt
as given, with no Kirk deliberation, and a think_hard pin silently measured as normal would be a lie) ·
`ambiguous_model_pin` · `capability_gap` (no factory declares the model — named in the error, e.g.
`no factory declares order.compute + model.does-not-exist:1b`) · `staging_failed`.

**Scope change, stated plainly:** an eval now measures **the MODEL** on the pin prompt — no Kirk grounding,
tools, persona or deliberation. Pins that grade HAI's PIPELINE behaviour (a tool trail, a deep-trace packet —
e.g. the HAI-independence E-pins) are not expressible as an eval_campaign until a pipeline-as-an-order
payload exists; `think_hard` refusal is what makes such a campaign fail loudly instead of silently.

**Files:** `E:\SSDShared\tabfoundry-jobs\campaigns\eval\<campaign_id>\items-<kind>-b<n>.json` and
`out-<kind>-b<n>.json` (+ `.progress\` markers, `.partial.jsonl`), written by Kirk through its `/tabjobs`
mount (`EVAL_CAMPAIGN_ROOT`), read/written by the factory as `\\<softserve-address>\SSDShared\tabfoundry-jobs\…`.

**Completion:** each order's `report_fleet_result` receipt (`POST /p20/jobs/<id>/result`) re-assembles the
campaign from that order's `out.json`; the watch loop's `_eval_orders_tick` is the backstop for a missed
receipt. A failed/cancelled order fails the campaign (reason names the order and the partial-rows file) and
cancels sibling orders no factory has collected yet; a done order whose `out.json` has not appeared after
300 s fails it too. A terminal campaign is never reopened.

**Readback `GET /p20/eval/<id>?user=<admin>`** (observation only; a running campaign is assembled fresh,
read-only): `status`, `progress`/`total` (rows landed incl. `.done` markers), `hold_reason`, `reason`,
`executor`, `orders[]` (`id, order, kind, host, model, num_ctx, status`), `rows[]`. Each row: `id, round,
kind, effort, prompt_sha, order_id, answer, thinking, measurement_state, measurement_reason,
execution_evidence[0] = {host, box, order_id, executor: "infer_ollama", requested_model, served_model,
requested_num_ctx, effective_num_ctx, downgraded, done_reason, prompt_eval_count, eval_count, wall_s, seed}`.
`measurement_state`: `clean` (served model == required and context == required) · `placement_mismatch`
(ollama served a different model) · `identity_unknown` (no served model reported) · `void_degraded_ctx` ·
`error` (the row's own error, e.g. ollama's HTTP body). Pins: `tests/test_eval_order.py` (22).

### `POST /fleet/gpu-load` — a box reports what its card is DOING; Kirk judges whether anything owns it (added 2026-09-27)

Pushed every minute by `gpu_load_reporter.py` (task "HAI GPU Load Reporter", SYSTEM, on BigHonker,
Softserve, Mothership; source `E:\SSDShared\Lab\bin\`). Companion to `/fleet/gpu-state` (alive?) — this one
answers *busy, and accounted for?*

| field | type | notes |
|---|---|---|
| `host` | str | required; must be a `scheduler.CARDS` host |
| `util_pct` / `power_w` / `vram_used_mb` | float or omitted | NVIDIA: `nvidia-smi`; Mothership: the foundry's own telemetry CSV (last row) |
| `ollama_loaded` | list[str] | model names from the box's own `/api/ps` |
| `source` | str | what measured it, or WHY it could not |
| `stale` | bool | true = "could not measure" → rendered `no sample`, never idle |

Stored per host in `STATE_DIR/gpu-load/<host>.json` with an `accounting` verdict computed on every push
(`scheduler.report_gpu_load`): util ≥ 20 % is busy; busy is **accounted** by a foundry run-state, a
scheduler `gpu` claim, an in-flight Kirk call on that box (`/hai/inflight`) or one that ended within 90 s, or
(Softserve only) a live Plex transcode per Tautulli — "could not ask Tautulli" is not Plex;
busy with none of those on ONE push is **suspect** (rendered `suspect · busy with no owner, 1 of 2 pushes`);
on **two consecutive pushes** it opens an **UNACCOUNTED** episode — REPORT tier: the
factory tile's `accounting` field reads `UNACCOUNTED · util N% · ollama: …` and ONE DM fires per episode
(`🚨 UNACCOUNTED GPU LOAD on <host> …`); the episode clears when load drops or a claim appears. The deck
line never invents: `no load report` / `load report stale (N min)` / `no sample (reason)`. Never an arrest —
this is the accounting question the GPU police cannot ask (ollama is orchestrator-owned and exempt).
Pins: `tests/test_gpu_load_accounting.py`.

### `GET /fleet/availability` + `POST /p20/jobs/{id}/handback` — a factory that cannot run an order returns it; the desk re-places or HOLDS it (added 2026-09-28)

the owner, 2026-09-28: *"If the computer is not available, then Kirk is supposed to re-route to a similar device …
if we can't work, then the mid tier orders should be held by Kirk."* Operator detail:
[[RUNBOOK-Compute-and-Training-Order-Submission]] §"How the desk picks factories".

`GET /fleet/availability?host=<h>` (factory token, `X-P20-Token`) →
`{ok, host, gpu_ok, gpu_why, start_ok, start_why, plex_active, plex_why, user_state, user_why}`. `gpu_ok` =
`scheduler.gpu_health` (the box's pushed nvidia-smi verdict); `start_ok` = `scheduler.order_host_unavailable(h,
"render_small")` — the same check placement uses (lost card, Plex transcoding or the utilisation gate on Softserve,
or a user on the card); `user_state` = `scheduler.user_on_card(h)`: `"user"` (load accounting `unaccounted`, or on
Mothership the gaming guard while nothing of ours holds the card; load Plex's transcodes explain on Softserve is left
to the Plex rule), `"clear"`, or **`null` = could not tell** (no fresh load report), never counted as a user;
`plex_active` (Softserve only) = `scheduler.plex_transcoding()`: transcodes ≥ `FAILOVER_MAX_TRANSCODES` (`SOFTSERVE_MAX_TRANSCODES` until 2026-11-17) per
`plex.activity()` — Tautulli first, Plex's own `/status/sessions` (`PLEX_TOKEN`) when Tautulli cannot answer, the
answer tagged `source` — and **`null` only when neither answers** (never a definite state). Factory use (`p20_foundry.py`,
`host_unavailable_reason`): before launching ANY order, `plex_active` (plus `start_ok` for GPU orders); every
`CARD_CHECK_EVERY_BEATS` compute beats (~60 s) while one runs, `plex_active` (every order) and `gpu_ok` (GPU orders).
`scheduler.order_host_unavailable` applies the Plex-transcoding rule to every kind on Softserve, CPU included.

`POST /p20/jobs/{id}/handback?host=<h>&why=<text>` (factory token) — only the owner (`claimed_by` / placed
`host`) of a non-terminal order may hand it back. The desk re-composes the order from its record's `route`
(`template`, `kind`, `only`, `exclude`, dealt `items`; records written before 2026-09-28 are recovered with
`scheduler.template_from_placed`), excluding any host that handed it back within `HANDBACK_COOLDOWN_S` (900 s).
Placed → the SAME record flips to `pending` on the new host (`claimed_by` cleared, `reroutes` history appended);
nothing available → status **`held`** (`held_why`), which `dispatchable()` never offers. Returns
`{ok, id, outcome: "placed"|"held", host, why}`. `_held_orders_tick()` (scheduler loop, 300 s) re-places held
orders; since 2026-09-29 a hold at submission and a release from this tick send NO DM (only a reroute DMs;
holds are visible on the deck and in the desk's order records). `POST /p20/order` itself answers `{"ok": true, "held": true, "id": …}` when
capable factories exist but none is available — a capability gap is still `{"ok": false}`.
Pins: `tests/test_scheduler.py` (`UnavailableIsNotBusy`, `OrderHostUnavailable`, `UserOnCard`, `TemplateFromPlaced`),
`tests/test_fleet_desk.py` (`HandBackAndHold`, `FactoryAvailability`), foundry `test_foundry.py`
(`TestCardUnavailableHandsBack`).


Verified against source 2026-07-01. Return-type surprises are called out — several (esp. `next_runnable`'s **tuple**)
are non-obvious and have caused probe/call mistakes.

**`config.py` — grounding / persona resolution**
- `namespace_for_user(user) -> str | None` — the user's OWN corpus namespace (identity). `None` = ungrounded.
- `default_persona_for(user) -> str` — "general" | "nursing" | "law".
- `shared_namespace_for_persona(persona) -> str | None` — the persona-shared corpus ns, or None.
- `grounding_namespaces(identity_ns, persona) -> list[str]` — **the two corpus layers** (identity ∪ persona-shared,
  de-duped, order-preserving). Single source of truth for BOTH seed-grounding and the search_docs/get_document tools.
- `is_nursing_namespace(ns) / is_writable_namespace(ns) / is_valid_persona(p) -> bool`.

**`app.py` — request-path helpers**
- `_resolve_persona(q, identity_ns) -> str` — resolved persona (explicit q.persona wins; else back-compat; else default).
- `_grounding_namespaces(identity_ns, persona) -> list[str]` — thin delegate to `config.grounding_namespaces`.
- `_plans_dir_for(user) -> str` — admin → `SELFENG_PLANS_DIR`; everyone else → `USER_PLANS_DIR/<user>` (layer split).

**`tools.py` / `ollama_client.py` — tool + model call**
- `tools.run_tool(name, args, user_text, user=None, persona=None) -> str` — persona threads the two corpus layers into
  search_docs/get_document (persona=None → identity only, unchanged for non-persona callers).
- `tools._corpus_namespaces(user, persona=None) -> list[str]`.
- `ollama_client.chat(host, model, messages, tools_list, num_ctx=None, status_callback=None, user=None,
  keep_alive=None, timeout=None, persona=None, telemetry=None) -> (answer, escalation)`. **Returns a 2-tuple.**
  `telemetry` (2026-07-08): caller-provided dict; chat fills it with author-loop stats
  `{"turns", "infer_s", "tool_s", "tools": {name: count}}`. **OVERWRITE semantics per call** (`.update(_tele)`,
  not accumulate) — multi-pass callers must pass a FRESH dict per call and aggregate themselves (see
  `app._merge_drive_tele`, used by `_dispatch_lead` for both mothership passes). Coding loops also log
  the same stats as a `DRIVE_TELE` line to notes.
- `Q.drive_telemetry: dict | None` (2026-07-08) — set (to `{}`) ONLY by the overnight drive (`_ov_drive`);
  `_dispatch_lead` aggregates into it. **Read it back off the model object after `_run_query_core(q)`**
  (pydantic may copy the literal on validation — the local dict you passed is NOT the collector).
  `_ov_drive` returns `{"telemetry": tele}` or `None`; `overnight_worker.work_item` stores it as
  `timings["drive_tele"]` on the item record.
- **How a coding loop ends (2026-10-05):** The coding tool loop has **no turn or search count** (since 2026-10-05, doctrine 13a; the old 50-turn cut ended HAI jobs minutes into a 4-hour budget). It ends when the model answers; after `config.CODING_IDLE_TURNS` (3) consecutive turns whose calls all repeat a call already made on an unchanged workspace (a successful `coding_patch`/`create`/`file`, checkpoint create/restore or task start/finish/resume/abandon is a change), with one "NO NEW WORK" note at the first such turn; or at the job's own deadline, carried by `ollama_client.call_context(loop_deadline=...)` (Absurd: its chosen budget; decomposed leaves: the run's budget; overnight drive: remaining run time minus `OVERNIGHT_LADDER_RESERVE_SECONDS`; think-hard tightens it to its EffortState budget via `tighten_loop_deadline`, refused outside a call_context so no pooled thread inherits one), with one "FINAL TURNS" note when the deadline is under three of the run's own average turns away. Telemetry: `nudges_idle`, `nudges_deadline`, `ended` (`idle`/`deadline`); records before 2026-10-05 carry `nudges_soft`/`nudges_hard`. Non-coding tool loops keep `config.TOOL_LOOP_MAX`.
- `review_context.strip_diff_comments(text) -> str` (2026-07-09) — pure; ``#``-comments removed line-locally,
  ``#`` inside '/" string literals kept, string literals KEPT (data-key wiring like ``timings["drive_tele"]``
  is real wiring). Used by BOTH B1 `overnight_worker._missed_scope` and B2 `adversary._omission_exempt` so a
  comment neither satisfies the scope gate nor defeats an omission claim. Line-local ⇒ triple-quoted
  docstrings stay matchable (deliberate fail-permissive; B1.2 backstops).
- `review_context.strip_negated_clauses` (2026-07-09 extension) — now also drops EXAMPLE clauses ("for
  example"/"for instance"/"such as"/"example:"), with "e.g."/"i.e." word-normalized first ("for example"/
  "that is") so their periods don't shred the clause at the splitter and "e.g" can't substring-match
  ``state.get``.
- **Nudge telemetry (2026-07-09):** the turn-nudge blocks set `_tele["nudges_soft"]`/`["nudges_hard"]` (1 when
  fired; keys ABSENT when not) — ride the `telemetry=` caller dict → persisted `timings["drive_tele"]`;
  `render_run_summary` item lines append `· nudged:soft`/`soft+hard`.
- **DM paths (2026-07-09):** `app._overnight_dm_send(message)` = the shared fail-soft Discord sender (no-op
  without token+valid user id; swallows all errors). Finish path: `_overnight_notify` →
  `render_run_dm(state)`. Start path: `_overnight_start` → `overnight.render_start_dm(run_id,
  eligible_count, max_seconds)` for REAL (non-dry) runs only.
- **Heartbeat (bl-drive-heartbeat, hand-built 2026-07-09):** `overnight.render_heartbeat(turn, tools) -> str` —
  pure; `"turn 20 · coding_read 8 · coding_search 5"`, tool counts busiest-first; empty/None tools → just the
  turn part. `ollama_client.chat(..., heartbeat=None)` gained a `heartbeat` callable param: pulsed every
  `config.CODING_HEARTBEAT_TURNS` turns (env, default 10; 0 disables). Wiring: `Q.drive_heartbeat: bool`
  (app.py:470) — set True by BOTH the overnight drive (app.py:2146) and fix rounds (app.py:2320); the chat
  call site (app.py:~2858) passes the pulse into the activity feed only when the flag is set.
- `app._ov_verify(task_id, item) -> {"passed", "summary", "tests"?}` (2026-07-08) — `"tests"` = suite size via
  `overnight.parse_test_count`, parsed from the **FULL runner stdout/stderr** — the `summary` is only the LAST
  output line (`"rc=0 | OK"`); the `"Ran N tests"` phrase is earlier, so parsing the summary alone yields None.
- `overnight.parse_test_count(summary) -> int | None` — pure; regex `Ran\s+(\d+)\s+test`.
- `cancellation._set_now(fn | None)` / `cancellation._current_now()` (2026-07-08) — injectable clock seam for
  TTL/retention (`purge_expired` / `purge_retired` / `QueryRun.created_at` all read it). `_set_now(None)`
  restores real time. NOTE: `reset_for_tests()` does NOT restore the clock — test setUp/tearDown must call
  `_set_now(None)` themselves (the lifecycle tests do both).

**`overnight.py` — run loop**
- `next_runnable(eligible, completed_ids, attempted_ids) -> (item | None, reason)` — **RETURNS A TUPLE**; reason is
  "ok" | "blocked" | "empty". Honors `depends_on`.
- `commission_rollup(items) -> list[str]` — per-commission done/total lines for items with a `parent_id`.

**`adversary.py` — critic ladder**
- `tiers_from_config(raw) -> list[AdversaryTier]` — **takes the raw config list** (`config.ADVERSARY_TIERS`), NOT no-arg.
- `critic_timeout(tier, remaining_seconds, base_timeout) -> float` — **positional** args; `max(base, tier.est_seconds)`
  capped at `remaining_seconds`.
- `apply_evidence_anchor(critique, corpus, severities=("critical","high"), min_chars=12) -> dict` — pure; downgrades
  critical/high findings whose `evidence` quote isn't verbatim (whitespace-normalized) in `corpus` to
  `severity:"medium"` + `unanchored:true` + `claimed_severity`. **Returns the SAME object** when nothing changed.
- `run_ladder(...)` new kwargs (2026-07-06): `dispute_severities=("low",)` (which severities the dispute hook is
  offered), `evidence_anchor=False`, `evidence_severities`, `evidence_min_chars`. The STRICT high/critical dispute
  quorum is enforced by the CALLER (`app._ov_review._dispute`), not by run_ladder.

**`overnight.py` — bail adjudication**
- `build_adjudication_request(state) -> dict | None` — pure; from a FINISHED run state (None if incomplete/clean),
  items with `adversarial.verdict == "bail_human"` or `test_tampering`. Keys: `run_id/ended_at/stop_reason/
  workspace_root/items[]/ask`. Item recs in run state now carry `workspace` (added 2026-07-06).
- `run_overnight(..., on_item_done=lambda item_id, run_id: None)` — fires on DONE only (not bailed/dry-run);
  hook exceptions swallowed. App wires it to `backlog.record_completed`.
- `run_overnight(..., pre_item_guard=lambda item: None)` — (D0, 2026-07-06) called per item BEFORE work; return
  a non-empty str to REFUSE it (recorded as an ERROR item + counts as non-progress). App passes
  `_make_stale_guard()` for REAL runs only (dry runs pass a no-op). Fires the D0 staleness guard.

**`overnight.py` — D0 staleness guard (hand-finished 2026-07-06, live)**
- `_source_manifest(root) -> dict[name, sha256hex]` — top-level `*.py` only (skips `*.bak`); the orchestrator
  runs it on its own `/app` (== live). Missing dir → {}.
- `workspace_stale(live_hashes, ws_hashes, ws_ahead) -> (is_behind, differing_files)` — PURE. identical→(False,[]);
  differ+ahead→(False, differing) [undeployed work allowed]; differ+not-ahead→(True, differing) [STALE].
- App wiring `app._make_stale_guard()` — hashes `/app` (`config.SELFENG_LIVE_ROOT`, default `/app`), fetches the
  workspace manifest+ahead from the runner (`coding_client.workspace_source_hashes(ws)` → `/files/source_hashes`),
  scopes to top-level *.py (drops `rel` containing `/`), calls `workspace_stale`. FAIL-OPEN (never halts a run);
  gated `config.OVERNIGHT_WORKSPACE_STALE_CHECK`. NOTE: the live orchestrator container has NO `/app/tests` (bind-
  mounted for the test run only), so the comparison is top-level *.py — the deploy-relevant set.

**coding-runner — `/files/source_hashes` (POST {workspace}) → `runner.source_hashes(ws)` (2026-07-06)**
- Returns `{files: [{rel, sha256}...], ahead: bool, baseline: sha}`. `files` = workspace top-level `*.py` (skip
  `*.bak`) + `tests/*.py`. `ahead` = `runner._ahead_of_sync_baseline`: HEAD beyond the last commit whose subject
  starts `sync-to-live baseline` (git `--grep '^sync-to-live baseline'`); NO baseline found or any git error →
  `(True, "")` (optimistic — a never-synced/ungit-able workspace is not blocked).

**`overnight_worker.py` — acceptance-delta guard (2026-07-06, live)**
- `WorkerDeps.get_diff: Callable[[task_id], str] | None` — item's cumulative unified diff; None disables the guard
  (default None → existing unit tests unaffected). App wires `_ov_task_diff` only when
  `config.OVERNIGHT_ACCEPTANCE_DELTA_CHECK`.
- `_acceptance_demands_tests(text) -> bool` (conservative substring set — fires on "unit test"/"new test"/"tests
  assert…", NOT "suite stays green"); `_diff_adds_tests(diff) -> bool` (added `def test_*` line or new tests/ file).
- In `work_item`'s DONE path, AFTER review (so fix-round tests count): acceptance-names-tests + diff-adds-none →
  `bail_task` + BAILED with a synthesized `adversarial.verdict="bail_human", reason="hollow_no_tests"` (flows to the
  adjudication request). FAILS OPEN (diff-fetch error or no test-demand → item completes normally).

**`review_context.py` — critic context packets (2026-07-06)**
- `changed_files(diff) -> list[str]` / `changed_symbols(diff, cap=8) -> list[str]` — pure diff parsing.
- `build_packet(diff, *, read_file, find_refs, max_files=4, max_file_chars=8000, max_ref_lines=40,
  max_total=16000) -> str` — injected fetchers (app wires coding_client.read/search); "" when nothing usable.
- `adversary.run_ladder(anchor_corpus=…)` — optional callable widening the evidence-anchor corpus to
  diff+packet (a packet quote must not read as fabricated).

**`app.py` — resilient runs (ep-resilient-runs, live 2026-07-06)**
- `_run_query` registers runs in `cancellation.QueryRunRegistry` (`register_with_user(cid, q.user or "Guest")`);
  completes/fails them; `query_stream` SHIELDS the worker and on client disconnect DETACHES via
  `_detach_stream_worker(inner, cid)` (strong-ref set `_DETACHED_STREAMS`; done-callback calls
  `cancellation.end`). `GET /query/reattach?user=|cancel_id=` — non-admin gated users are PINNED to
  X-Hai-User for the user lookup. Registry self-maintains: `register()` → purge_expired (GRACE_TTL_SEC=1800,
  running-only) + purge_retired (RUN_RETENTION_SEC=86400, deletes finished). CHAT_HTML: `resumeRun()` reattaches
  on load/user-switch via localStorage `hai.activeRun.<user>`.
- `_load_invariants()` — SELFENG_INVARIANTS_PATH (docs mount, the owner-owned) → injected into drive + critic prompts.

**`tests/test_test_hygiene.py` — suite self-defense (2026-07-06)**
- AST-scans all test modules; FAILS on any class with test_* methods not inheriting unittest.TestCase (in-file
  transitive; utf-8-sig). Born from 40+ lifecycle tests that unittest discover silently never ran.
- GOTCHA: tests that stub `sys.modules["httpx"]` must RESTORE the real module after the module-under-test binds
  the fake (test_lanfetch/test_websearch/test_pubmed do this now) — a leaked stub breaks starlette.testclient.

**`backlog.py` — persistent done-ledger (2026-07-06)**
- `load_completed() -> dict` — `{item_id: {"run_id","at"}}` from `config.SELFENG_COMPLETED`
  (`/selfeng-proposals/completed.json`); fail-soft `{}` on any error.
- `record_completed(item_id, run_id="") -> bool` — atomic append; best-effort.
- `eligible_items(done_ids=())` — NOW ALSO excludes ledgered ids (union with per-run done_ids). To deliberately
  re-run an item, delete its ledger entry (host: `/mnt/workspace/hai-self-engineering/proposals/completed.json`).

**`decompose.py` / `deliberation.py` — planning + JSON parsing**
- `decompose.parse_plan(content: str) -> list[Leaf]` (each Leaf has `.id/.title/.objective/.acceptance/.depends_on/.integration`).
- `decompose.write_plan(plans_dir, commission, trail) -> dict` (`{dir, md, yaml}`); `read_feedback(plans_dir, commission_id) -> list[str]`.
  A commission (the old task-scale "epic", renamed 2026-08-27) = one objective decomposed into leaves; `emit_plan`/
  `revise_once` take `commission=`, plan.yaml's top key is `commission:` (legacy `epic:` still read).
- `deliberation.converge(*, initial_findings, can_continue, improve, recritique, max_rounds=None,
  stall_stops=True, on_round=...) -> dict` — **the SHARED convergence loop both think-hard AND the overnight
  adversarial ladder run on** (ep-unify-deliberation, 2026-07-01). Pure/injected: `improve(prev_n)->{"accepted":bool,
  ...}` (think-hard = text refine + regression guard; ladder = test-anchored fix + deterministic reverify),
  `recritique()->critique|None`, `can_continue()->bool`. Returns `{rounds, stop_reason, final_findings, converged,
  rounds_run}`; stop_reason ∈ `converged|stalled|rejected|recritique_unavailable|budget|max_rounds` (module constants
  `CONVERGED/STALLED/REJECTED/RECRITIQUE_UNAVAILABLE/BUDGET/MAX_ROUNDS`). `adversary.run_ladder` maps these onto its
  verdicts (`FIXED/BAIL_HUMAN/REVERIFY_RED`); `app._run_think_hard` uses the last accepted refinement.
- `deliberation.extract_json(content) -> dict | list` — strips `<think>`/prose/fences (raises DeliberationError if none).
- `deliberation.parse_critique(content) -> dict` (`{verdict, summary, findings}`).
- `deliberation.merge_decorrelated(critiques, *, max_findings=24) -> dict`; `merge_critiques(primary, secondary,
  second_label="medical") -> dict`; `refinement_regressed(refined, draft) -> bool`.

**`promote.py` (host-only) / `absurd.py`**
- `promote.namespaced_id(commission_id, leaf_id) -> str` (e.g. `ep-x` + `l1` → `ep-x-l1`).
- `promote.build_promotion(plan, existing_items, *, approve, force=False) -> dict`; `append_to_backlog(path, new_items) -> int`.
- `absurd.seconds_for_choice(choices, key) -> int | None`; `new_run_id() -> str`.
- `absurd.AbsurdRegistry(state_dir)`: `.create(run_id, user, text, choice, max_seconds, status=QUEUED, **extra) -> dict`,
  `.update(run_id, **fields) -> dict|None`, `.get(run_id) -> dict|None`, `.list_for(user, limit=20) -> list`,
  `.has_active(user=None) -> bool`.

### P20 render INTENT parsing — when a chat message becomes a render order

`p20_intent` owns the create-intent vocabulary and the decision itself. `app.py` re-exports the names
unchanged, so existing references keep working.

- **The module is stdlib-only deliberately.** The test runner can import it, which puts the gate under
  behavioural tests instead of source-wiring checks. Keep orchestrator-only imports out of it.
- **`create_intent` applies three guards, each load-bearing:**
  - **PROXIMITY** — the action verb and the image noun must appear near each other. Independent
    searches across the whole message match a verb in one paragraph and a noun in another, which is
    not a request.
  - **NEGATION** — an explicit negative ("do not generate any image") suppresses the intent.
  - **LENGTH cap** — a message over the cap is pasted material, not a request.
- **Why all three are needed, not one:** without them a large pasted document queues a render, and the
  follow-up message asking to cancel that render queues a second one — the cancellation itself parses
  as an intent. Removing any one guard reopens that path.
