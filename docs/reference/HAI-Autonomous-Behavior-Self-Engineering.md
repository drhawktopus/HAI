# HAI Orchestrator - Autonomous Behavior & Self-Engineering



## Self-engineering request path
- Gate: `SELF_ENGINEERING_INTENT` regex (a self-eng noun like self-deploy/orchestrator/yourself + an action verb) **AND** `q.user in config.ADMIN_USERS`. Sets the `self_engineering` flag in `_run_query_core`.
- The `SELF_ENGINEERING_BRIEF` (in `app.py`) is appended to Mothership's system prompt by `_heavy_suffix(text)` (only when the intent matches). It instructs: use `orchestrator-real` directly, clean-git check, plan→edit→BOTH unit+compile→review-BEFORE-checkpoint→checkpoint→`self_deploy` (gate 1)→`deploy_promote` (gate 2); never approve your own pending action; **one decision gate per turn** (added 2026-06-29, defect #2 fix).
- `_dispatch_lead()` runs `ollama_client.chat()` then a **continuation driver**: re-prompts "execute, don't announce" up to `config.SELF_ENGINEERING_MAX_PASSES` while an admin task/project is active and no human gate is pending; stops after `config.SELF_ENGINEERING_STALL_LIMIT` no-progress passes (progress = real task/project/gate state change, not timestamps).
- **Defect #2 guard (2026-06-29):** before returning, self-eng answers pass through `_reconcile_decision_claims(answer, _recorded_decisions())` — appends a factual "State check: the durable decision store actually contains: …" note if the answer claims more decision gates than are actually persisted. Trust the store, never the prose.

## Durable state APIs (coding-runner; via `coding_client`)
- `decision(action, …)` → `/decisions`: **create** (pauses task, presents 2–4 options + risk/recommendation/reversibility), **list** (`include_resolved`), **get**, **resolve** (records the owner's choice, resumes task). Persisted. This is the Phase-4 decision-gate store.
- `task(...)` start/get/update/finish/resume (objective + acceptance_criteria + plan; `isolate` worktree option). `project_driver(start|advance|get|list|resume)` = the Phase-2 topological multi-task graph (cycle detection, dependency-gated advance). `checkpoint` create/get/**restore**. `coding_review` = independent read-only diff review on the 27B.
- **Process-locality gotcha:** `pending_actions` (the confirm cards) live **in the orchestrator web process** (in-memory). Creating one via a one-shot `docker exec python …` puts it in a throwaway process the UI can't see — create them through the live `/query` path or a wired tool. `/pending` defaults `user=Guest`; query **`/pending?user=the owner`** to see admin cards. Confirm/deny via `POST /pending/act {action,token,user}`.

## Remedy-with-consent (Tier B) + the librarian writers — as built

Watch verdicts PROPOSE remedies; a human consents from any surface; execution re-verifies.
Design facts (proposal record: `Archive` copy of `10d-REMEDY-CONSENT-AND-LIBRARIAN-PROPOSAL.md`;
live receipts: Journal 2026-08-15):

**Remedy proposals**
- Producer: `_watch_tick` — a firing watch whose remedy is whitelisted files a proposal
  alongside the DM, naming its exact artifact verbatim.
- **Whitelist: stop slip (`*.stop.json`) and requeue slip (`*.ctl.json`) only.** No kills,
  deletes, deploys, or config writes. Growing the whitelist is a new signed line, not a code
  edit. WoL wake is deliberately NOT consent-gated — `wake_host` is an existing ungated
  Tier-A tool; a watch that finds a sleeping host offers the wake instead.
- **Three consent surfaces, one store, first-answer-wins:** Discord buttons (HawkAdminBot
  queue-descriptor table), the web review panel, and in-chat `confirm_action`. The others
  render a decided proposal as resolved.
- **TTL = the watch's deadline horizon**, and approval NEVER fires on the stored verdict:
  the executor re-reads the deck at yes-time — condition still present → execute;
  self-resolved → decline politely, stating when it cleared. A stale yes cannot fire on a
  ghost; an expired proposal refuses late approval.
- **Delivery rides the existing pull channel**: slips are factory-local and factories have
  no inbound ports, so the desk holds a claimed-once control record and
  `collect_fleet_controls` (sibling of `collect_fleet_orders`, same claim-before-write and
  atomic-rename rules) materialises the local slip the receptionist already reads.
- Auth: the bot↔Kirk bridge is LAN-trust with `user=the owner`; remedy approval is admin-gated
  server-side like contributions.

**Librarian writers (HAI's only library write paths)**
- `journal_append(entry)` → `Journal\YYYY-MM-DD-hai.md`: server-side writer, timestamped
  sections, own-file by the side-session convention; `Journal\` is fetch-only (not indexed).
- `[HAI SESSION]` block TOP-prepended to CURRENT-PROJECTS: code-generated header and H2,
  append-only, re-reads the file top immediately before writing; the model supplies content
  lines only — never the retrieval-facing title.
- Guardrails: sensitive content never (person-records rule in the tool description + the
  librarian clause in [[HAI-INVARIANTS]]); no writes to Reference/Runbook/LLM-Project
  bodies; Credentials untouched by construction (fixed-path writers, no path parameters);
  `claim_review` watches `Journal\*-hai.md`.
- Cadence: the overnight loop's finish path and the drill protocol journal as part of
  COMPLETION; a failed write is a named failure in the run record, never silence.

**Explicitly out of scope:** auto-remedies without consent (Tier C would be its own signed
proposal) · new alert paths · HAI editing existing docs · indexing journals.
