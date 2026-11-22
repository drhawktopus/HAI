# Homelab Ops Agent - Security, Guardrails & Auditing



## Guardrails

1. **Admin-only execution.** Every write and pipeline tool is gated to `ADMIN_USERS`. The system enforces hard refusal at dispatch in `tools.run_tool`. Non-admin models receive a filtered tool list excluding admin capabilities (pattern: `_ADMIN_TOOLS` / `_allowed`, consistent with `ha_control_device`/`write_config`).
2. **Two-phase write confirmation.** Every state-changing action uses a shared confirmation framework (Phase 0). Phase 1 (`preview`) returns the exact plan or diff with no side effects. The user explicitly approves before Phase 2 (`execute`). This generalizes the `write_config` preview-confirm pattern to all write tools.
3. **Destructive operation friction and scoping.** Deleting torrents/data, removing series/movie files, and blocklisting require explicit "yes, delete" confirmation. Operations are scoped (e.g., limited to `tv-sonarr` category or monitored items only) to prevent library-wide failure from single-command errors.
4. **Structured write audit.** All write actions generate a structured audit trail recording who, what, when, and result, layered on top of `notes.log`.

## Security Test Conformance

The `test_api_keys` security test verifies that configuration sources use `_secret_value` references rather than hardcoded literals. It does not assert that keys are empty at runtime, as keys are properly wired via secrets.

## Deterministic risk assessment and audit trail

Risk assessment uses deterministic rules rather than a secondary model to prevent prompt injection vulnerabilities in attacker-influenced fields such as release names or file metadata. Rule-based counting of effects provides reliable guarding for dangerous actions.

### Risk evaluation logic

The `assess_risk(kind, destructive, metadata)` function evaluates every write tool via `pending_actions.register()`. It returns a risk level (LOW/MED/HIGH) and reasons, included in the payload fields `risk` and `risk_reasons`.

Risk levels are determined by these rules:
* **HIGH**: `delete_files` actions, on-disk data loss, bulk operations >= 25 items (`RISK_BULK_HIGH`), or combinations of remove+delete+bulk.
* **MED**: Actions with the `destructive` flag, `qBit` remove actions, manual-import moves, or bulk operations between 5 and 24 items (`RISK_BULK_MED`).
* **LOW**: Additive actions or those not meeting MED/HIGH criteria. No banner is shown for LOW risk to reduce routine operational noise.

A warning banner (`🛑 RISK HIGH` or `⚠️ RISK MED`) is prepended to the preview payload for MED and HIGH risks.

### Audit trail implementation

An audit log writes fail-soft JSON lines to `/docs/State/audit/ops-audit.jsonl`. Every action result (PREVIEW, APPLIED, DENIED, FAILED, EXPIRED) records a timestamp, kind, user, risk level, metadata, and result.

### Test coverage and backups

The suite contains 288 passing tests (local and in-container), verifying HIGH banners on destructive/bulk actions, absence of banners on additive actions, and audit file writes. Pre-risk layer backups are stored as `pre-risklayer-*`.

### Semantic check architecture: Three-tier guard for write actions

The system enforces a three-layer guard before executing write actions:
1. **Tier 1**: Deterministic risk flags (default on).
2. **Tier 2**: Model-based semantic intent check (default on; disabled by `ACTION_REVIEW_ENABLED=0`).
3. **Tier 3**: Human confirmation.

Tier 1 and Tier 3 remain authoritative; mismatches flagged by Tier 2 append a warning to the preview but do not block execution.

#### Tier 2 implementation: `action_review.py`

`action_review.py` performs a secondary semantic check, asking "does this write action match what the user asked?" before confirmation. It uses the same critique-JSON pattern as nursing deliberation.

**Design constraints:**
* **Model selection**: Uses a fast 8B model by default (`ACTION_REVIEW_HOST`/`MODEL` = BigHonker/qwen3:8b) because waking larger models (e.g., 27B) per-confirm is impractical. The 8B model provides sufficient capability for intent-vs-action comparison. Larger models can be pointed to via environment variables if desired.
* **Isolation**: Direct httpx usage; no `ollama_client` import to avoid tools↔ollama_client cycles.
* **Failure mode**: Returns `{verdict: match|mismatch|unsure, concern}` on success. Returns `None` (fail-soft) on any error or model unavailability.
* **Integration**: Wired into `tools._finish_pending()` so every write tool inherits it.
* **Scope**: Gated to MED/HIGH pending actions.

**Output behavior:**
On a mismatch or unsure verdict, the system appends `⚠️ SEMANTIC CHECK (…)` to the preview and sets a `semantic_review` field. It does not gate execution.

See `Runbook\Tooling-LLM-Inference.md` for model hosting details.

## Homelab Write Operations and Confirmation UI

The homelab operator interface requires human confirmation for all write actions. The system implements a three-layer guardrail:
1. Deterministic risk classification.
2. Tier-2 semantic check.
3. Human confirm-on-write via the web UI.

### API Endpoints
All endpoints are defined in `app.py`. Access is owner-scoped; users may only view and act on their own pending actions, enforced by the same-user gate used by the `pending_actions` component.

- `GET /pending?user=`: Returns a list of pending write actions for the specified user.
- `POST /pending/act`: Confirms or denies a pending action. A denied action is aborted; no restart or modification occurs.
- `GET /audit?limit=`: Returns recent logged write actions from the audit jsonl, ordered newest-first.

### Web Interface
The UI includes two primary panels:

1. **Pending Actions Panel**: Polls `/pending` every 6 seconds and refreshes after each submission. Displays pending actions with risk-colored left borders (HIGH, MED, LOW) and full action previews. Includes ✓ Approve and ✗ Deny buttons.
2. **Audit Log Panel**: Admin-gated toggle that displays recent operations sourced from the audit endpoint.

### Routing and Tooling
Write tools reside in `BRAIN_TOOLS` and `HEAVY_TOOLS`. They are admin-gated. Homelab-ops requests route to these tools on the 8B model. Complex multi-step flows escalate via the existing think-hard/27B path. No separate intent classifier is required; the deterministic risk check, Tier-2 semantic check, and confirmation UI govern the write flow.

### Design Boundaries
- **Proactive Status Notifications**: Not implemented. The system relies on existing Watchtower Discord notices and the 4:30 AM stack-health embed. Proactive pings (e.g., disk-low) remain an optional follow-up, not part of the core confirmation UX.
- **Sandboxing**: HAI does not have access to a general unsandboxed shell tool. Write capabilities are limited to the mediated host-ops broker covering Sonarr, Radarr, Lidarr, Prowlarr, qBit, Seerr, Plex, Recyclarr, Docker, Caddy, Pi-hole, disk operations, and git.

See `Reference\` for detailed component manuals on individual services.

### Orchestrator host control via narrow sidecar

The orchestrator operates within a container and lacks direct access to the host Docker daemon. To execute host-level actions (Docker restart/stop/start/logs/pull, Caddy reload, Watchtower trigger, Pi-hole restartdns, git commit+push), the system uses a **narrow privileged sidecar**.

The sidecar holds the Docker socket or equivalent host access and exposes a restricted HTTP API. The orchestrator calls this API to perform whitelisted actions only. This design enforces the "no general shell" guardrail by preventing the orchestrator from holding the socket directly, limiting blast radius to explicitly permitted operations.

Alternative approaches (mounting the Docker socket directly into the orchestrator, or SSH with command-restricted keys) are rejected due to larger security blast radius or operational complexity.
