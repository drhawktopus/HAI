# Self-Engineering Agent - Phased Implementation Plan



## Implementation Status by Phase

### Phases 0–5: Core Infrastructure & Robustness (Live)
*   **Phase 0:** Safe self-deployment keystone (Claude).
*   **Phase 1:** Autonomous single-change loop (Codex).
*   **Phase 2:** Multi-task project driver (Codex build / Claude finish). 
    *   *Constraint:* Orchestrator images must be built as root or via the self-deploy broker. Non-root `docker compose build` drops non-world-readable files (e.g., `0600 coding_client.py`). The promotion/restore logic (`hostops_deploy._promote`/`_restore`) normalizes source permissions to `0644` to prevent empty module errors.
*   **Phase 3:** Bounded recovery and progress-based continuation (Codex).
*   **Phase 4:** Decision gates and escalation logic. 
    *   *Constraint:* Context window management is critical; overflow causes failures in larger models. Fixes include explicit context-window handling for 27B-class models.
*   **Phase 5:** Unattended Robustness (Self-healing). Includes auto-close of stale tasks, busy-predicate signaling, and Mothership power-down integration with watchdogs (Keep-Awake, Ollama, Sundae VPN).

### Phase 6: Proactive Overnight Autonomy (Code-Complete, Validated)
Phase 6 implements the overnight run loop using primitives from Phases 4–5. It is wired behind a `dry_run` default (inert until approved) and an approval gate.

#### Components
*   **`overnight.py` (Control Logic):**
    *   Defines `RunBudget` constraints: time ceiling, no-progress detection, and max-item bounds.
    *   Implements the main loop (`run_overnight`) with stop conditions: halt, time exhaustion, no progress, item exhaustion, or source errors.
    *   Persists run state to `/docs/State/overnight/` as JSON.
    *   Exposes `POST /self-eng/overnight/start` (UI configurable `max_seconds`, default `dry_run=True`) and `GET /self-eng/overnight/status` (admin-only).
    *   Handles sandbox-only flip/restore on run start/end.
*   **`overnight_worker.py` (Execution Logic):**
    *   Processes items via `work_item` with injectable `WorkerDeps`.
    *   Creates coding tasks and drives the 27B model via the orchestrator’s self-engineering path (`_run_query_core`, ephemeral, forced 27B, sandbox-only ON).
    *   Verifies completion via deterministic `coding_test`. 
    *   Implements bounded hybrid retry (`OVERNIGHT_MAX_ATTEMPTS=2`). Uses Phase-3 recovery for fingerprint/baseline issues.
    *   Bails and reverts on verification failure. Homelab-domain items bail with a "needs morning-review" note if auto-application is not supported.
*   **`adversary.py` (Critique & Review):**
    *   Runs after deterministic verification passes. Uses a decorrelated model (different from the 27B author) to avoid shared blind spots.
    *   Implements tier-escalating critique: Floor (27B) → Qwen3.6:35b-a3b → Qwen3-Coder:30b → DeepSeek-R1:70b, selecting the richest tier within remaining time budget.
    *   Critiques are test-anchored: the author adds a failing test for the critique finding, fixes it, and deterministic re-verification must stay green.
    *   Bounded by `ADVERSARY_MAX_ROUNDS=2`. Bails to human at cap.
    *   Verdicts: Pass/Fixed → DONE-clean; Bail/Human (tests green, critic unhappy) → DONE-flagged-for-review; Reverify-Red (fix broke tests) → Revert → BAILED; Skipped (no time) → DONE with note.
    *   Controlled by `force_adversarial` toggle and tier selection logic based on remaining time (@200s→floor, @500s→decorrelated, @99999s→max, @100s→None).

#### Operational Constraints & Policies
*   **Energy Gating:** No large GPU runs before ~9:00 PM CDT to avoid high-price energy hours. See CURRENT-PROJECTS for policy details.
*   **Drive Pass Cap:** A per-item drive budget bound (`overnight.drive_pass_cap`, `Q.self_eng_max_passes`) caps a slow item’s passes by remaining budget to ensure the 6C ladder gets headroom. Live caps: 1 pass at 900s budget, 8 passes at 4000s budget.
*   **Task State Management:** Overnight tasks are non-isolated. `finish_task` marks them as terminal (not 'active') to prevent blocking subsequent items and pinning the busy signal.

## Verification & Testing
*   Unit tests: >519 green across components.
*   Supervised Validation Run: Passed end-to-end with the owner+Claude observation.
    *   HAI autonomously created a coding task, implemented `durations.py` and tests, committed checkpoints, and verified success.
    *   The deterministic verifier confirmed return code 0.
    *   The run stopped on the time ceiling; state was persisted; sandbox-only mode maintained (no live deployment).
*   Known Limitation: The 6C adversarial ladder was skipped in validation due to budget consumption by the single item (~22 min/item including continuation and review). Future runs with larger budgets or `force_adversarial` will validate this tier. See CURRENT-PROJECTS §Nightly Validation for run logs.

Harness internals (read-as-truth): see [[HAI-Orchestrator-Internals]] - model-call path, context-window arithmetic, self-deploy state machine, operational gotchas.

## Self-engineering agent implementation phases

The self-engineering agent capability is delivered in six sequential phases. Each phase expands the scope of autonomous operation while maintaining safety gates. The design fact is that HAI executes only within the boundaries defined by the current active phase; capabilities from future phases are not present until explicitly enabled and verified.

**Phase 0 — Safe self-deploy foundation.**
The deploy executor runs on the host-ops broker, external to the orchestrator, to ensure self-healing on bad deploys. The sequence is:
1. Promote `orchestrator-real` diff to canonical state (auto-backup triggered first).
2. Execute `compose build` and `up -d`.
3. Perform health-check on `/health`.
4. Auto-rollback to the backup on failure.
The process is confirm-gated and audited, reusing #11's pending-action, audit, and confirmation UI.

**Phase 1 — Autonomous single-change loop.**
HAI processes a single small-scoped request end-to-end:
1. Plans the change.
2. Edits `orchestrator-real`.
3. Writes and runs unit/compile checks.
4. Conducts independent review.
5. Executes confirm-gated deploy via the Phase 0 executor.
6. Performs live verification (`/health`, smoke query).
7. Updates docs/memory.
Human approval is required for the plan and the deploy. Proof-of-concept targets include adding a read tool or fixing a known bug.

**Phase 2 — Multi-task project driver.**
HAI decomposes a goal into a task graph, sequences it, and iterates on failing tests or review findings. Checkpoints are created between tasks to allow resumption after interruption. Human approval occurs at phase boundaries, mirroring the execution model of #11.

**Phase 3 — Live verification + self-debugging loop.**
After deploy, HAI drives its own endpoints and the broker, reads diag logs and container logs, and iterates on failures (e.g., "pihole v6 reloaddns" class issues). This closes the build→deploy→observe→fix loop.

**Phase 4 — Judgment, decision gates & escalation.**
Structured gates surface architecture and security decisions to the owner (analogous to git-auth and sidecar-vs-socket choices in #11). The system maintains risk awareness and escalates to Claude/Codex for beyond-capability work. Trust tiers widen the scope of HAI self-execution without per-deploy approval as reliability is demonstrated.

**Phase 5 — Unattended robustness (self-healing).**
The self-engineering loop survives and self-cleans with no human watching. Capabilities include:
- Auto-close/abandon tasks.
- Dirty-workspace auto-revert.
- Generalize verify-your-own-results.
- Idempotent cards.
- Single-active-tasks enforcement.
- Mechanical sandbox-only toolset.
- Kill switch and busy-predicate.
These features also clean up interactive chat-driving. Details correspond to the Overnight-Run Readiness Gate's P0-safety and P1-state-integrity items.

**Phase 6 — Proactive overnight autonomy.**
Built on Phase 5, HAI works an approved, test-verifiable backlog overnight. Mechanisms include:
- Backlog file with human-only approval flag and item tagging.
- Deterministic explore-retry logic.
- Run-level circuit breaker.
- Checkpoint-and-resume capability.
- Durable-state morning report.
This is the engineering analogue of #11 Phase 5's proactive status.

## Conformance

Verify the current phase state via the health endpoint:
- Endpoint: `/health`
- Field: `self_engineering_phase`
- Expected value: `4`

See CURRENT-PROJECTS §"#12 STATUS RECONCILE + RECOVERED Phase-5 decisions" for the durable decision store status.

## Phase structure: Self-engineering lifecycle

The self-engineering lifecycle is defined by two sequential phases. The original single "Phase 5" definition was superseded and split into the current structure.

### Phase 5 — Unattended Robustness

**Objective:** Ensure the self-engineering loop survives failures and cleans its own state without human intervention. This phase also standardizes interactive chat-driving workflows.

**Capabilities:**
- State-integrity hardening: auto-close or abandon tasks, dirty-workspace auto-revert.
- Result verification: generalized verify-your-results checks, idempotent task cards, single-active-tasks enforcement.
- Safety controls: mechanical sandbox-only toolset, kill switch, busy-predicate.

**Milestone:** A supervised overnight-style run (30–60 minutes) completes cleanly with human observation only.

**Status of enablers:** The current 64k context window and fixes for defect #1 and defect #2 provide banked Phase-5 capabilities. Specifically, authoring capability and the false-success guard are already in place.

### Phase 6 — Proactive Overnight Autonomy

**Objective:** Enable unattended operation using a structured backlog and deterministic failure handling.

**Capabilities:**
- Backlog management: backlog file with human-only approval flags, test-verifiable tagging.
- Execution control: deterministic explore-retry logic, run-level circuit breaker, checkpoint-and-resume support.
- Reporting: durable-state morning report.

**Milestone:** A real unattended all-night run on approved test-verifiable backlog items produces a clean morning report.
