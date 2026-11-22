# Self-Engineering Agent - Design Doctrine & Autonomy Governance



## Design Doctrine & Scope
The self-engineering agent extends the orchestrator to autonomously execute verifiable tasks within a sandboxed environment. The system supports two classes of work:
1. **Orchestrator Code Chores:** Tasks that produce code changes verified by deterministic unit tests. These are applied automatically.
2. **Homelab/Config Items:** Tasks that produce configuration changes or diffs requiring human review before application. These generate a proposed diff for morning approval.

The agent operates under strict safety constraints:
*   **Sandbox Isolation:** All self-engineering tasks run in a sandboxed environment (`sandbox-only` flag). Deploy tools are stripped from the execution context.
*   **Deterministic Verification:** Task completion is decided by deterministic test verifiers, not by model self-assessment. The `coding_test` verifier confirms success before marking items as DONE.
*   **Budget & Safety Gates:** Runs are bounded by time ceilings (`max_seconds`), item counts, and progress checks. A "busy signal" keeps the host awake during active work; a kill switch allows immediate termination.

## Deployment Governance and Scope

### Autonomy Model
All deployments require explicit approval before execution. Trust-tiers are not currently implemented.

### Rollback Policy
The system implements automatic health-gated rollbacks for all deployment stages. Manual promotion to live requires an explicit "promote" action. Canary releases are part of the standard workflow.

### Edit Scope
The orchestrator modifies only its own Python codebase and associated tests. Docker Compose configurations and secrets management files remain protected from automated changes.

### Promotion Workflow
Local canonicalization uses file copy operations (`cp`). GitHub synchronization remains a manual or nightly batch process, not part of the real-time deployment pipeline.

### Model Selection
The default model for build and review tasks is the 27B variant. Escalation fallbacks use Claude or Codex models. Early escalation is expected under normal operation.

### Cancellation Protocol
Tasks support cooperative cancellation via the `deploy_cancel` signal, checked at every execution boundary. Pre-cutover cancellations perform clean unwinding. Post-cutover cancellations trigger full rollback procedures.

<!-- REWRITE START -->

## Design doctrine: Autonomous engineering loop

HAI conducts multi-phase software projects on its own codebase and the homelab infrastructure. It executes a standard lifecycle: plan → build → test → deploy → verify → document.

**Scope of authority:**
- **Operations (#11):** HAI performs operational tasks previously handled via SSH, including media management and general homelab administration.
- **Engineering (#12):** HAI builds the systems that enable #11. It possesses the agency to initiate and complete engineering projects within defined guardrails.

**Verification:** Conformance is verified by successful completion of the lifecycle loop under guardrail constraints.

## Operational Boundaries
The workspace defined in `orchestrator-pilot/AGENTS.md` cannot deploy, restart services, commit, push, or modify canonical production source. Promotion to canonical source and Sundae is a separate human-controlled workflow.

<!-- Triaged: doctrine/gap-analysis. Moved to Reference or preserved in Plan as status tracker depending on final policy. See [[LIBRARY-CHARTER]] §1 (Documents state what IS) and §3 (Live status never lives in a document). -->

## Capability Gaps vs. Building #11 Baseline

The following table defines the capability gap between Claude’s execution of Building #11 and HAI’s current capabilities, establishing requirements for Building #12.

| Capability (Building #11) | HAI Current State | Gap / Requirement for #12 |
|---|---|---|
| Read/grep codebase, learn patterns | ✅ read/list/search | None |
| Hash-safe edits | ✅ `patch` | None |
| Write + run tests | 🟡 unit/compile profiles; HAI writes tests | Broaden scope: in-container suite (`EMBED_ENABLED=0`), live-test profiles |
| Multi-phase planning + decomposition | 🟡 task+acceptance + 27B think-hard | **Project driver**: sequence multiple tasks + iterate |
| Convention-matching code quality | 🟡 27B can, with scaffolding | Reliability: model-capability validation |
| **Deploy** (promote → build → restart) | ❌ Human-controlled | **Self-deploy executor** (keystone) |
| **Live verification** (drive endpoints/broker, observe) | ❌ Not wired | Post-deploy verify loop |
| Debug via observe→fix iteration | ❌ | Build→deploy→observe→fix loop |
| Architecture/security decisions + user consultation | 🟡 Chat interface | Structured **decision gates** |
| **Self-recovery** (if deploy bricks HAI) | ❌ | Health-gated deploy + **auto-rollback** (external) |
| Docs + memory updates | ✅ file/memory tools | None |

### Co-location Advantage
HAI executes on Sundae. Deployment is local (`cp` from workspace to canonical directory, followed by `docker compose build/up`). This eliminates remote-agent friction (SSH/PowerShell/BOM/base64-pipe), lowering the complexity threshold for HAI compared to remote agents.

## Self-modification safety: external executor requirement
HAI editing, rebuilding, and restarting its own container creates a failure mode where a bad deploy takes down the orchestrator with no internal recovery path. Deploy and rollback logic must reside in an external executor, not inside the orchestrator being replaced.

The #11 host-ops broker serves as the external executor. It provides a `deploy` endpoint that:
1. Promotes the diff.
2. Builds the new image.
3. Restarts the orchestrator.
4. Performs health checks.
5. Auto-rolls-back on failure, using existing `backups/`, checkpoints, and `docker restart` mechanisms.

## Autonomy vs. control: phase-gated execution
HAI proposes a plan. the owner approves the phase. HAI executes in the sandbox. Tests and independent review must pass. Promotion and deployment are confirm-gated. Deployment is health-gated with auto-rollback. the owner reviews the result.

Human oversight remains at phase boundaries and the deploy gate until trust is earned. The pending-action confirmation UI from #11 Phase 5 handles deploy approvals.

## Model-capability ceiling: staged trust
With the existing harness, review pass, tight task scoping, and human checkpoints, the 27B model can likely handle incremental, well-scoped self-changes. A fully autonomous #11-scale build in one shot exceeds current capabilities.

Trust is staged:
1. Start with trivial self-changes.
2. Widen scope as reliability proves out.
3. Keep Claude/Codex escalation as the fallback for beyond-capability planning.

## Verification: external health gating
HAI verifying its own deploy creates circular verification. Mitigation relies on an external health check rather than trusting HAI’s self-assessment alone. The broker pings `/health` and runs a smoke set to gate the deploy, supplemented by the owner's review.

## Explicit non-goals (carry the #11 discipline)
- **No unbounded self-rewrite.** Staged trust; scoped, reviewed, reversible changes only. The system does not support "improve yourself however you want" directives.
- **No unsupervised external push/PR.** Promotion and GitHub interactions are human-gated initially, consistent with the git tool constraints in #11.
- **Not a Claude/Codex replacement.** HAI handles incremental, well-scoped self-work. Large or novel builds escalate to assisted sessions. The system maintains and extends itself between these sessions.
- **No general unsandboxed shell.** The executor runs allow-listed deploy steps only; arbitrary commands are not permitted, consistent with #11 constraints.

## Project sequence and trust model
The Self-Engineering Agent (#12) extends HAI from operational execution (#11: ops automation) to system construction (#12: build automation). It relies on three existing enablers:
1. A coding-agent harness containing a copy of HAI's source.
2. A privileged host-ops broker serving as the safe-deploy executor.
3. The confirm-on-write and human-decision discipline established in #11 to ensure trustworthiness.

The autonomous loop implements safe self-deploy with auto-rollback, governed by the same decision gates used in operational automation.
