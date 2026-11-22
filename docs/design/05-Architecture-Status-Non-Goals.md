# Homelab Ops Agent - Architecture, Status & Non-Goals



## Current State and Status

Project #11 is complete. The full homelab/media-ops surface is live in HAI.

Current implementation constraints:
* Admin-gated access.
* Confirm-on-write enforcement.
* Click-to-approve UI present.

See CURRENT-PROJECTS §#11 for the neutral one-line status summary.

## Out of Scope and Parked Features

The following capabilities are not implemented in the current release:

* **Proactive push-notifications:** Deferred. Overlaps with Watchtower's Discord integration (Phase 5 was optional).
* **YouTube integration (Pinchflat/yt-torznab):** Deferred by the owner.
* **Bazarr/subtitles:** Not installed; requires installation before implementation.

## Admin operations via HAI chat

HAI runs as a container on Sundae. It performs homelab and media-pipeline operations for the admin through chat interaction, accessing local services directly (`localhost:8989/9696/8080/...`). This co-location eliminates SSH and PowerShell quoting overhead, providing direct tooling access distinct from read-only observability.

## Cross-cutting Foundations

### Generic arr v3 API Client
`arrclient.py` provides the generic client for *arr v3 APIs. It handles base URL and API key configuration, standard HTTP methods (GET/POST/PUT/DELETE), and the `/command` submit-then-poll pattern. Sonarr, Radarr, Lidarr, Prowlarr, and Bazarr share this interface; subsequent components are thin wrappers or tool schemas built on top of this client.

### Secrets and Configuration Management
API keys for each service are stored in configuration files (environment variables or `secrets/` files), following the pattern established by `TAUTULLI_APIKEY`. A deploy step reads each container's `config.xml` to extract API keys once. HAI runs on Sundae.

### Write-capable Client Layer
A write-capable client layer exists separately from the GET-only `fetch_url` allow-list. This layer provides access to *arr and qBitTorrent ports for write operations.

### Confirmation Framework
The confirmation framework enforces a generic "pending write" mechanism for safety. When a write tool returns `{preview, action_token}`, execution does not occur until a follow-up call to `confirm_action(token)` (or setting `confirm=true`) is made. The web UI surfaces these pending confirmations for approve/deny decisions. This framework is the primary safety control and must be implemented before any write tools are added, ensuring uniform inheritance across all write capabilities.

### Gating and Audit Logging
The `_ADMIN_TOOLS` mechanism is extended to include gating logic. An audit log records relevant actions.

### Routing Logic
Intent routing distinguishes between "homelab ops" actions:
- Simple one-shot actions execute on the 8B model with tools.
- Complex multi-step flows (e.g., search → grab → import → verify) escalate to the 27B "think-hard" planner, which is already built.

## Non-goals and risk constraints

- **No general unsandboxed shell tool for HAI.** An always-on, LAN-reachable chat model does not receive a root shell. Structured tools (Sonarr/Radarr/Prowlarr/qBit/ffprobe) provide the required outcomes without that risk. General "inspect anything" flexibility is out of scope.
- **Engineering build, not time-cost minimization.** The architecture prioritizes capability through contained client modules and the confirmation-framework keystone. Phases are additive; execution pauses are permitted between phases. Phase 1 covers the Bleach use case.
- **Content liability.** This is the owner's self-hosted media automation pipeline. Responsibility for content and legal compliance rests with the operator.
- **Test pattern.** Each module uses static wiring and behavioral tests with mocked arr/qBit/Plex clients, consistent with `pubmed.py` / `lanfetch.py`.

## Build order and verification

The deployment sequence is:
1. Phase 0: Foundations + confirmation framework
2. Phase 1: Sonarr + qBit-write + Prowlarr + ffprobe (Bleach parity goal)
3. Phase 2: Radarr + Seerr + Bazarr + Plex
4. Phases 3/4/5: As appetite allows

Each phase deploys and verifies independently with a backup, consistent with all prior HAI builds.

## Codex implementation scope

Project #11 defines the operational boundary for the Homelab Ops Agent. The following components and tools are deployed and active:

- **Infrastructure clients**: `pending_actions.py`, admin-only `confirm_action`, `arrclient.py`, `qbitclient.py`.
- **Read-only tools**: `sonarr_lookup`, `prowlarr_search`, `qbit_torrents`.
- **Confirm-gated write previews**: `sonarr_search`, `qbit_action` (pause/resume/recheck), `qbit_add` (magnet/http(s) with required category; defaults to `paused=true`).

The following operations are explicitly excluded from current exposure:
- Destructive qBittorrent actions: remove, delete.
- Sonarr actions: release grab, manual import.
- Media probing: `ffprobe`.

Live torrent actions are disabled. Manual Bleach imports by Claude trigger a block on live Bleach tests.

### Sonarr Interactive Grab Implementation

The Sonarr agent suite includes an **interactive grab** path for retrieving specific media releases. This feature is additive and non-destructive to active downloads.

#### Tool Specification
1. **`sonarr_releases`**: Lists grabbable releases for a given series or season.
   - Returns release metadata including available **languages** (to identify dual-audio options) and the unique identifier tuple (`guid` + `indexerId`).
2. **`sonarr_grab`**: Executes a grab for a single specific release.
   - **Input**: Requires `guid` + `indexerId`.
   - **Action**: Issues `POST /release` to the Sonarr API.
   - **Security**: Admin-only, confirm-gated. Non-admin requests are denied.

#### Architecture & Conformance
- Implements the `pending_actions` + `arrclient` pattern established by Codex.
- **Deployment Status**: The deployed test suite on Sundae is synchronized with the source code (266 tests). Divergence between local and deployed tests is treated as a defect; tests are deployed alongside code to maintain parity.

#### Secret Configuration Requirement
Arr tools (`sonarr_grab`, etc.) return `api_key is required` if secrets are not configured. The tools are code-complete and security-gated but cannot reach live services until the corresponding API keys (e.g., `SONARR_APIKEY`) are wired in the deployed orchestrator.
- **Action Required**: the owner must approve wiring of these secrets to enable write-access to the real pipeline. Do not wire unilaterally.

<!-- Tier: Reference (Supporting docs) -->
<!-- Purpose: Design doctrine and current operational state of the qBit agent integration -->

### qBit Agent Integration Status and Design Doctrine

The `qbitclient` module provides the interface between the Homelab Operations Agent and the qBittorrent WebAPI. The library maintains conformance with the active service version via a fallback strategy.

#### API Conformance and Version Handling

The target system runs **qBittorrent v5.2.2** (WebAPI 2.15.1). This version deprecated v4 endpoints in favor of renamed counterparts:
- `torrents/pause` → `torrents/stop`
- `torrents/resume` → `torrents/start`
- Add parameter `paused` → `stopped`

**Design Doctrine:** The client implements a v5-preferred, v4-fallback logic. It issues requests using v5 names first. If the server returns a 404 error, it retries with the corresponding v4 name. This ensures compatibility across minor version drifts without breaking existing deployments.

*   `add_url` sends both `paused` and `stopped` parameters to ensure correct initial state regardless of API version interpretation.
*   Unit tests mock the client behavior; they do not validate endpoint names against live server responses. Live verification is required when WebAPI versions change.

#### Write Path Operations and Safety Gates

The write path (add, pause, resume, remove) is fully implemented and verified against a test environment (`hai-test` category with paused placeholder torrents). Real production torrents were not used in this verification phase.

**1. `qbit_remove` (Admin-Only)**
*   **Behavior:** Removes torrent(s) by hash. Supports optional `delete_files` flag.
*   **Safety Gate:** The command is confirm-gated. Before execution, it performs a lookup of each target to display name, state, and progress.
*   **Warning Logic:** If a target is an incomplete or active download, the preview displays a WARNING. This prevents blind deletion of live grabs.
*   **Destructive Nature:** This action is irreversible if files are deleted; operator confirmation is mandatory.

**2. State Transitions Verified**
The following state transitions have been validated:
*   `add` → Torrent appears with 0% progress (stopped/paused).
*   `resume` → Torrent begins downloading.
*   `pause` → Torrent stops downloading, remains in list.
*   `remove` → Torrent removed from list; associated files cleaned if requested.

#### Conformance Verification

*   **Test Suite:** 270 tests green (local and in-container). Deployed code matches source.
*   **Backup Points:** `pre-qbitremove-*` and `qbitclient-pre-v5fix-*` artifacts exist for rollback reference. See `Reference\Backup-Registry.md` for location.

#### Outstanding Phase-1 DoD Items

The following items are required to complete Phase 1 but are not yet implemented:
1.  **Secrets Wiring:** Pending decision on implementation method (the owner).
2.  **ffprobe Dual-Audio Probe:** Requires `ffmpeg` installation and read-only `/media` mount.
3.  **Sonarr Manual Import:** Integration path not yet established.

See `Runbook\qBit-Operator-Manual.md` for current operational commands.

## Phase-1 Workflow Status

Phase-1 is functionally complete for the find→grab→verify path. The workflow is admin-gated and requires confirmation on write:
1. `sonarr_lookup`
2. `sonarr_releases`
3. `sonarr_grab` (confirm)
4. `media_probe`

Sonarr manual import is deferred until in-flight manual imports are complete.
