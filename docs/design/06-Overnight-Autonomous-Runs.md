# Self-Engineering Agent - Overnight Autonomous Runs



## Self-Engineering Autonomy Specifications

HAI performs unattended self-engineering in bounded, sandbox-only overnight runs using free local compute. The system maintains an approved backlog and processes items according to the following locked design decisions.

### Sandbox Enforcement and Promotion

Overnight work does not auto-promote changes. the owner reviews all results. Mechanical enforcement removes `self_deploy` and `deploy_promote` from the toolset during overnight mode. Do not rely on prompt instructions for this isolation.

### Retry Logic and Defect Handling

Retry logic uses a deterministic, hybrid approach:
1. **Anti-repeat guard:** Identical or cosmetic diffs block immediate retry. The variance check must be deterministic (normalized-diff/AST), never relying on HAI's self-judgment.
2. **Diagnosis-driven repair:** One bounded alternate plan (≤2 files, ≤50 lines).
3. **Hard stop:** Execution stops with evidence if the alternate plan fails.

Cap: 2 variance attempts + 1 baseline restore.

### Backlog Management

*   **Location:** `Z:\Workspace\hai-self-engineering-backlog.yaml`.
*   **Nature:** Project-agnostic file in the owner's general co-working workspace. It is NOT in coding-state and is NOT named "phase5-*" because the backlog outlives specific phases.
*   **Coding-State Scope:** Holds only run-state and morning reports. `Z:\Documentation\` serves as read-as-truth reference.
*   **Access:** A narrow Workspace mount provides access to just the backlog file/subfolder.
*   **Permissions:** Split write permissions. HAI proposes and appends items; the "approved-to-run-overnight" flag is human-only-writable.

### Model Routing

*   **Architecture Double-Check:** `qwen3.6:35b-a3b` (MoE, ≈GPU-sized). Chosen over DeepSeek-R1 70B (42.5 GB) to avoid offloading/thrashing on the 24 GB GPU / 32 GB RAM system.
*   **Code Review:** `qwen3-coder:30b`.
*   **High-Value Queries:** DeepSeek-R1 70B reserved for rare, stuck, high-value all-night questions.
*   **Reviewer Role:** Tool-free + self-contained evidence packet. Claude/Codex assembles and verifies.

## >> OVERNIGHT-RUN READINESS GATE

### P0 — SAFETY (Mechanical Enforcement)
*   **Sandbox Enforcement:** `tools.sandbox_safe()` mechanically strips `self_deploy`/`deploy_promote`. It is wired behind a runtime toggle: `POST /self-eng/mode {sandbox_only:true}` causes `_dispatch_lead` to use `sandbox_safe(HEAVY_TOOLS)` for self-eng requests. An unattended deploy is mechanically impossible when this toggle is active.
*   **Destructive/Secrets Guardrails:** Existing guardrails (#11) and protected paths block destructive actions, secrets exposure, and external pushes in the overnight toolset.
*   **Kill Switch:** `POST /self-eng/mode {halt:true}` stops the self-eng continuation loop (`_SELF_ENG_MODE["halt"]` checked at the top of `_self_engineering_needs_continuation`). This is resettable. The Phase-6 circuit breaker can also flip it. Per-query stop remains available via `cancellation.py`.

### P0 — INFRASTRUCTURE SURVIVAL
*   **Mothership Stability:** Crash-resilient checkpoint-and-resume mitigates stability risks. A fault triggers a resume from the last checkpoint; full reinstalls (DDU/Adrenalin) are not gated but remain recommended maintenance.
*   **Power Management:** Unattended jobs remain awake via `standby-timeout-ac=0`. The busy signal (`GET /self-eng/busy`) prevents sleep/shutdown during active runs. Future workload-aware idle-shutdown policies will use this signal as a hook.
*   **Ollama Autostart Resilience:** The existing self-heal mechanism must catch mid-run Ollama deaths, not just cold starts. (AtLogon task reliability is addressed by the self-heal requirement).
*   **VRAM Optimization:** Q8_0 KV cache is enabled on Mothership (`start-ollama.ps1` sets `$env:OLLAMA_FLASH_ATTENTION='1'` + `OLLAMA_KV_CACHE_TYPE='q8_0'`). Flash attention works on 7900 XTX/ROCm. This reduces VRAM usage for 27B @ 64k context (~4-6 GB headroom) with near-zero quality cost.

### P1 — STATE INTEGRITY & SELF-HEALING
*   **Auto-Close Stale Tasks:** `_auto_cancel_stale_tasks()` sweeps `task_list` and auto-cancels standalone active tasks idle > `TASK_AUTO_CANCEL_SECONDS` (default 24h). Project-linked tasks are protected. Abandoned tasks are cancelled, not guessed as complete.
*   **Abandon/Reset Mechanism — work is KEPT, never deleted:** `runner.task_abandon(task_id)` cancels the task and restores the workspace to its `baseline_commit` (or discards an isolated worktree), and BEFORE either it saves any work as a checkpoint (`preserved_checkpoint`; the diff lives in the runner's records, outside the branch, and `coding_checkpoint restore` brings it back). Isolated path since 2026-08-29 (`_preserve_before_discard`); since 2026-10-17 EVERY hard reset of a shared workspace (`_preserve_before_reset`: abandon, the automatic baseline restore after a repeated broad test failure, and a restore back to a checkpoint), skipping work a checkpoint at the same commit already holds and a forward restore that loses nothing. the owner's rule (#12 lesson 7, reaffirmed 2026-10-16): *"keep in its own section and marked. Not deleted. We may want to come back to it."* The overnight lane's `bail_task` and HAI's interactive "Not verified" exit (#37: a checkpoint marked UNVERIFIED, then abandon) both go through it. (Superseded text: 06-overnight-autonomous-runs-superseded-2026-10.)
*   **Dirty-Workspace Blocking:** `task_start` hard-blocks on uncommitted diffs in non-isolated, non-`_internal` tasks, requiring a commit/checkpoint or abandon first. `_internal` flag exempts project-driver child tasks.
*   **Claim Verification:** Tool failures are surfaced/handled instead of narrating false success.
    *   Decisions: Guarded by `_reconcile_decision_claims`.
    *   Tasks: Guarded by `_reconcile_task_claims` (flags claimed-closed tasks still active).
    *   Deploys: Guarded by `_reconcile_deploy_claims` (flags claims if broker phase != done).
    *   Remaining: Extend verification to test-pass and backlog-item claims.
*   **Idempotent Pending Cards:** `pending_actions.register` reuses identical non-expired cards (same user+kind+metadata+preview) instead of duplicating. This prevents duplicate-deploy-card races.
*   **Single-Active Discipline:** `task_start` refuses a second active non-isolated task per workspace (`_active_task_for_workspace`). Mirrors the project rule. `_internal` flag exempts the project driver.

### P1 — CONVERGENCE & CONTROL
*   **Deterministic Retry/Explore:** The worker bounds attempts via `OVERNIGHT_MAX_ATTEMPTS`. Coding-runner Phase-3 recovery performs anti-repeat (identical-failure fingerprint + baseline restore). 6C adversarial ladder adds an explore axis. Retries must vary (explore) rather than just stop.
*   **Run-Level Circuit Breaker:** `RunBudget` + `run_overnight()` stop on:
    1.  Backlog exhausted.
    2.  No net progress across N items (`no_progress_limit`; only DONE resets it).
    3.  Hard time ceiling.
    4.  Max items.
    5.  Halt kill switch.
    Time is a ceiling, not a quota. The deterministic verifier (`coding_test`) decides when an item is DONE. Once acceptance+tests are green, the item is marked done and the run moves on. No gold-plating of passing solutions.
*   **Backlog Substrate:**
    *   `backlog.yaml` (mounted :ro): Human-curated, mechanically restricted to human-write-only (HAI container gets "Read-only file system" on write). Contains items with `approved_overnight` flag.
    *   `proposals.yaml` (mounted :rw): HAI appends proposed items. Never eligible for auto-execution.
    *   Eligibility: `eligible_items()` returns items where `approved_overnight AND kind=="test-verifiable" AND id not in done`.
    *   Kinds: `test-verifiable` (orchestrator/sandbox) or `judgment`/homelab (config/infra that cannot auto-apply; overnight produces proposed diff + dry-run evidence for morning approval).
    *   Run status lives in machine-owned run-state, not the backlog.

### P1 — MORNING REVIEW & RECOVERABILITY
*   **Morning Report:** `GET /self-eng/report` renders markdown from durable state only (active tasks/decisions/projects/last-deploy + Overnight runs section via `overnight.render_run_summary`). Reads persisted run-state JSONs for per-item outcomes and adversarial verdicts. Facts-only; no model-generated narrative.
*   **Audit Logging:** Every action is audited in `ops-audit.jsonl` (tool path). Deploy-broker ops are audited (`_audit_deploy` in `hostops._request`). The overnight-loop’s own action audit remains pending.
*   **Checkpoint-and-Resume:** Run-state is persisted after every item (`on_state` → `/docs/State/overnight/<run_id>.json`). Crashed runs are detected via `overnight.is_incomplete`. `POST /self-eng/overnight/start {resume:true}` continues the SAME `run_id`, seeding `done_ids` from persisted items to skip already-attempted work and maintain accurate totals.

#### Adversarial Review Ladder — guaranteed floor + time-scaled ceiling

Adversarial review operates as a tiered ladder with a mandatory minimum and a time-dependent maximum. Every item reaching the owner must satisfy the floor; additional available time increases the review tier.

- **Floor (Tier 1):** Mandatory independent adversarial critique. This step is strictly above basic self-check mechanisms (`coding_review`). The critique must be adversarial in framing (e.g., "break this"), use fresh context, and avoid rubber-stamping. Ideally, the critic model differs from the generation model to decorrelate errors. If time budget is tight, a same-model adversarial check is the minimum acceptable floor.
- **Ceiling (Tiers 2–3):** Conditional escalation based on available time. The adversary model escalates to progressively larger local models (e.g., `qwen3.6:35b-a3b`, `qwen3-coder:30b`, `deepseek-r1:70b`). Each tier involves a tougher critic and a test-anchored fix loop.
- **Model Selection Logic:** Maps remaining time to adversary tier conservatively. Estimates begin static, log actuals, and refine over runs. Operations require checkpoint-before-start to ensure safe aborts; worst-case outcomes are skipped tiers or clean reverts, never corrupted partial states.
- **Quality vs. Breadth:** The guaranteed floor prioritizes vetted quality over throughput. Nightly UI max-time dials allow trade-offs: longer nights enable more items and deeper review. Output includes the hardened solution and the adversarial trail (tiers run, findings, fixes) for the owner’s morning review.
- **Implementation via `deliberation.py`:** Reuses the existing bounded multi-pass engine (`EffortState(budget_seconds, max_passes≤5)`). Extensions for overnight runs include:
  - Escalating adversary models per pass (climbing the ladder/decorrelating critics).
  - Larger overnight time budgets than interactive caps (1800s/30min).
  - Test-anchor findings (red→green) for autonomous cases.
  - Bail-to-human at cap if `changes_requested` remains.
- **Safety Critical Path:** Mechanical sandbox-only enforcement is P0 safety; it prevents unattended self-deploy from bricking HAI. State integrity items are P1 "self-healing" passes. Build safety, state integrity, busy-predicate, and checkpoint-resume before the first real overnight run. Convergence tuning occurs during early short/supervised runs.

## Overnight scheduling and energy-price gating

### Energy price gating

The system gates unattended runs against real-time pricing to defer execution during peak rates. The `energy.py` module consumes the Home Assistant sensor `sensor.utility_psp_data`, which provides the hourly RTP forward curve for the current day and the next 24 hours ($/kWh).

**Behavior and logic:**
*   **Fail-closed safety:** If the price sensor returns unknown data, execution defers.
*   **Window selection:** The module calculates the cheapest contiguous N-hour window across the remaining current day and the upcoming tomorrow (`cheapest_window`).
*   **Threshold enforcement:** A real (non-dry) run defers if the current price exceeds `ENERGY_PRICE_CEILING` (default: $0.10/kWh). This threshold separates overnight off-peak rates (~$0.03/kWh) from evening peak rates (~$0.18–0.20/kWh).
*   **Override:** The `ignore_energy_gate` flag bypasses this check.

**Endpoints:**
*   `GET /self-eng/energy`: Returns the current verdict and the calculated cheapest window.

### Overnight scheduler

The scheduler manages unattended jobs using a persistent queue and a daemon loop. It is request-driven; recurring automatic triggers are not currently implemented.

**Components:**
*   **Firing logic (`overnight.should_fire`):** A pure function that returns true when price is cheap, the system is not busy, work exists in the queue, and the job has not expired.
*   **Persistence:** Jobs are stored in `scheduled.json`, which survives process restarts.
*   **Daemon loop:** Polls every 5 minutes to evaluate pending jobs.

**Endpoints:**
*   `POST /self-eng/overnight/schedule`: Submit a job.
*   `GET /self-eng/overnight/schedule`: List queued jobs.
*   `CANCEL /self-eng/overnight/schedule`: Remove a specific job.

Execution invokes the standard run machinery with `ignore_energy_gate` set for one-shot execution.

### Observability

Unattended runs provide a live activity trail to replace inference from GPU counters. Events are emitted via `overnight.emit/set_event_sink` and threaded through the loop (`item_start`, `item_end`, `stop`), the worker (`drive`, `verify`, `review_start`, `item_done`), and the ladder (`run_ladder(on_event=...)`: tier selection, critique verdict, fix rounds, re-verification, completion).

**Status endpoints:**
*   `GET /self-eng/overnight/status`: Returns current state and an activity ring buffer.
*   **Logs:** Per-run activity logs are available at `/docs/State/overnight/<run_id>.activity.log`.

### Auto-ladder tier constraints

The auto-ladder selects adversary tiers based on budget and performance constraints. To prevent routine runs from crawling due to excessive offloading, the default tier list excludes models that cannot reside fully in GPU memory.

**Constraints:**
*   **Default cap:** The `_ADVERSARY_TIERS_DEFAULT` list caps at `qwen3-coder:30b` and `qwen3.6:35b-a3b`. These models are GPU-resident and fast.
*   **Restricted access:** `deepseek-r1:70b` is removed from the default list because it violates the locked decision that 70b-class models are for rare explicit use only, and its ~23GB offload requirement on a 24GB GPU causes ~5 minute/call latency. It remains accessible via `ADVERSARY_TIERS_JSON`.
*   **Interruptibility:** The `run_ladder` does not check the kill switch mid-round; a halt waits for the current round to complete.
