# Self-Engineering Agent - Deployment Pipeline & Recovery



## hostops Deploy Pipeline

### Execution Flow
The `hostops_deploy.py` state machine executes deployments via the external `hostops` broker. The sequence is:
1. Start
2. Preflight checks
3. Backup
4. Promote
5. Canary build and verification on port 8101
6. **Human promote gate**
7. Cutover
8. Live verification
9. Auto-rollback on failure
10. Cleanup

### Component Details
*   **Canary Definition**: A profiled Compose service inheriting live configuration via a YAML anchor.
*   **Build Mechanism**: One-off `docker:cli` execution using Compose v5.2.0.
*   **Administrative Tools**:
    *   `self_deploy`
    *   `deploy_status`
    *   `deploy_promote`
    *   `deploy_cancel`
*   **Safety Controls**: HIGH-risk classification with two mandatory confirmation gates.

See CURRENT-PROJECTS for full writeup.
<!-- REWRITE END -->

## Phase 3 — SPEC: Self-Engineering Agent Recovery and Continuation Controls

The self-engineering agent loop implements recovery from failure to prevent thrashing. The following behaviors define the current spec for the orchestration layer.

### Failure-Aware Recovery
On detection of a repeated identical test failure (same test ID and error message), the loop stops re-running that specific test. On a broken-workspace signal (import errors or multiple simultaneous failures), the orchestrator restores the workspace from the last good checkpoint using `coding-runner checkpoint restore` rather than attempting further patches on the broken tree. Identical-failure retries are capped at approximately two attempts; upon exceeding this cap, the agent re-plans or escalates.

### Single-Active-Project Invariant
The system enforces at most one active project per workspace. The agent reuses the existing active project via `project_driver(get/advance)` and never initiates a second project while one is active. If an ID miss occurs, the agent re-fetches the active project/task ID from `project_driver(get)` instead of generating new IDs.

### Post-Deploy Live Verification
After a live cutover, the agent verifies runtime functionality by reading `/health`, the diagnostic log (`/docs/State/diag/<date>.jsonl`), and container logs (`hostops docker_logs orchestrator`). If a runtime error is detected, the agent iterates on a fix. This covers errors such as the pihole v6 reloaddns class.

### Progress-Based Continuation Bound
Continuations are tied to progress metrics (a node advanced or a gate reached). The loop stops if repeated no-progress iterations occur, replacing fixed-count limits (such as `SELF_ENGINEERING_MAX_CONTINUATIONS=3`) with dynamic bounds based on actual advancement.

### Escalation Policy
On failed deploy/verify, the agent diagnoses the issue and proposes a fix, then waits for human approval. This is the conservative default behavior. Auto-diagnose-and-retry with bounded retries followed by escalation is not implemented.

### Scope Constraints
The 27B model operates within a reliability ceiling suitable for incremental single changes (Phase 1). It does not support unattended multi-node execution. Multi-node projects require tighter per-node scoping and escalation to Claude/Codex for planning.

### Test Configuration
Tests verify: repeated-failure-stop, restore-on-broken-workspace, single-active-project invariant, ID re-fetch on miss, and post-deploy diag/log read. Run with `EMBED_ENABLED=0`.

## Phase 3: bounded recovery and progress mechanisms

### Recovery-aware tests
Recovery-aware tests accept the durable task ID. They fingerprint identical failures to stop redundant runs. For broad import or workspace collapse, they restore the task-start Git baseline once. They block if the same failure persists.

### Project creation constraints
Project creation enforces one non-finished project per workspace. The client-side recovery list retrieves and reuses the authoritative active project ID rather than trusting a guessed ID.

### Continuation limits
Continuations use stable workflow snapshots. The system stops after two no-progress passes, with an absolute safety ceiling of eight passes. This replaces the flat three-continuation limit.

### Deploy and repair doctrine
Post-deploy health and log observation is required. Automatic canary or live redeployment is forbidden. Repair is local. Every changed production attempt requires fresh approval.

Local sandbox diagnosis and retry are allowed as they are cheap. Production retries are not automatic. One baseline restore is allowed for broad local collapse. Canary failure, live rollback, or behavioral failure may produce a repair, followed by a fresh deploy approval.

### Operational constraints on approvals
Pending approvals must be created inside the long-lived orchestrator web process (HAI `/query`). They cannot be created via one-shot `docker exec` Python processes because the approval store is process-local.

The protected broker promotes top-level orchestrator Python tests. The coding-runner remains a separately rebuilt and verified sidecar. Its nested canonical source must be synchronized after rollout.

## Defect mitigations

The following defects were identified in Phase 4 usage and fixed. These fixes are required to prevent silent loss of decision data or runtime errors.

### Compaction worker NameError on BigHonker downstate

When the `BigHonker` service is down, `_compaction_worker()` previously called the undefined function `softserve_allowed()`, causing a `NameError` during compaction. 

**Fix:** The call was changed to `failover_available()`. This fix is live in `app.py:557`. The self-deploy loop handles deployment with gate-1 and gate-2 approval, canary verification, and auto-rollback.

**Constraint:** Any deploy-driving `/query` issued by the owner must include the flag `ephemeral:true`. Omission of this flag triggers the same compaction bug.

### HAI durable decision-gate persistence failure

HAI previously claimed durable resolution for decision gates that were never persisted to the store, resulting in lost decisions.

**Fix Part A:** Enforce a one-gate-per-turn brief rule. The system must not claim resolution without a successful resolve record.
**Fix Part B:** Implement `_reconcile_decision_claims`, a deterministic guard that appends a factual state-check note when the answer over-claims against the durable store. This guard is wired into self-engineering only via `_dispatch_lead`.

Verification: 428 tests passed before deployment. Deployment followed canary-first procedure. Behavioral proof confirmed live functionality. Claude implemented this fix directly, as HAI's own 27B model failed to produce valid edits (empty or truncated answers), demonstrating the escalation path working as designed.
