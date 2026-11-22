# Homelab Ops Agent - Media Stack Tools & Integration



## Arr stack component inventory

The following components constitute the operational homelab stack. The list defines what IS present; absence of a component listed here indicates it is not part of the current stack definition.

### Running containers

The following services run as persistent Docker containers:

- **Media management:** sonarr, radarr, lidarr (music), prowlarr
- **Indexing and proxy:** jackett (secondary indexer proxy/torznab), flaresolverr (Cloudflare solver for indexers)
- **Download clients:** qbittorrent (runs within the `expressvpn` network namespace)
- **Maintenance and automation:** cleanuparr (stalled/failed/orphaned download cleanup), watchtower, autoheal
- **Request interfaces:** seerr (Overseerr replacement for requests), requestrr (Discord request bot)
- **Analytics and monitoring:** tautulli (Plex activity read-side), homepage
- **Networking and infrastructure:** caddy (reverse proxy), pihole (DNS filtering), expressvpn, orchestrator
- **Development and AI agents:** coding-runner, wol-sidecar, claudediscordbot
- **YouTube pipeline:** pinchflat + yt-torznab

### On-demand tools

The following components are defined in the compose file under the `tools` profile. They do not run as persistent containers but are available for on-demand execution:

- recyclarr

### Excluded components

The following components are explicitly NOT part of this stack. Adding them requires architectural changes before tool integration:

- Bazarr (subtitles)
- Tdarr or FileFlows (transcoding)

### Plex architecture note

Plex does not run as an arrstack container. It runs on Softserve (the Windows host). Consequently:

- Tools interacting with Plex must target the Softserve-hosted Plex API and token.
- Tautulli serves as the local read-side interface for Plex activity.

See [[RUNBOOK#Plex]] and [[Machine-Softserve]] for the current Plex operational architecture.

## Ops-Agent Tooling: Stacks and Interfaces

The Homelab Ops Agent interfaces with the media stack via defined tools. The stack comprises Sonarr, Prowlarr, qBittorrent, and supporting services (Jackett, FlareSolverr, CleanupArr). The agent image includes `ffmpeg`/`ffprobe`.

### Tool Inventory and Capabilities

**Sonarr**
*   **Read:** Series, seasons, episodes, queue, history, quality profiles.
*   **Write (Confirm-Gated):** Interactive release search + grab, monitor toggles, trigger season/series search, manual import, queue remove, blocklist-and-redownload.

**Prowlarr**
*   **Role:** Primary indexer proxy.
*   **Capabilities:** Indexer release search (read), indexer health status.
*   **Architecture Note:** The stack also runs Jackett (secondary/legacy torznab proxy) and FlareSolverr (Cloudflare-challenge solver). When a search returns no results, diagnose FlareSolverr connectivity as a common failure mode before assuming indexer error.

**CleanupArr Awareness**
*   **Role:** Auto-removes stalled, failed, or orphaned grabs.
*   **Diagnosis:** If a grab appears to "disappear" from the queue, verify if CleanupArr processed it. This is expected behavior, not a failure. A trigger-cleanup tool is optional.

**qBittorrent**
*   **Endpoint:** `172.18.0.1:8080` (via `expressvpn` netns published-port DNAT).
*   **Write:** Add magnet/torrent (scoped to category), remove/pause/resume/recheck, set category/limits.
*   **Read:** Preferences (including `save_path`), per-torrent details.

**Media Probe (ffprobe)**
*   **Environment:** Orchestrator image must include `ffmpeg`. Mount `/media` as read-only.
*   **Scope:** Path-scoped to `/media` only. No arbitrary filesystem access.
*   **Capability:** Report audio/subtitle tracks for a file/episode (e.g., dual-audio detection).

### Operational Target: Bleach Use Case

The system is considered operational when an admin can instruct HAI to:
1.  Find a dual-audio *Bleach* release.
2.  Confirm and grab the release via Sonarr.
3.  Monitor import into Sonarr.
4.  Verify audio tracks using `ffprobe`.

This reproduces the manual Bleach session within the HAI agent context.

## Tool availability and integration points

**Radarr** is present in the stack. It provides film lifecycle tools mirroring Sonarr: search, grab, monitor, import, and quality management.

**Seerr (Overseerr)** serves as the entry point for content requests (list, approve, deny, add). Admins perform approval/denial actions. **Requestrr** acts as a Discord bot that feeds requests into Seerr; it is an external input channel, not a primary operator tool.

**Plex** runs on Softserve, separate from the `arrstack`. Operator tools trigger library scans and metadata refreshes via the Plex API on Softserve using `X-Plex-Token`. Tautulli remains the local read-side interface via `check_plex`.

**Bazarr** is not in the stack. Subtitle management via Bazarr requires explicit installation first. If added, it provides subtitle search and download capabilities for series, movies, and episodes. Treat as install-gated.

## PVR Stack: Component Status and Constraints

### Sonarr and Radarr Profile Management
The media stack uses Sonarr and Radarr for series and movie management. Custom quality profiles are maintained to handle dual-audio releases. Configuration involves cloning existing profiles, adding custom formats via release-title regex, and assigning scores. All changes are structured and confirm-gated.

### Recyclarr Synchronization
Recyclarr is configured within the Docker Compose stack under the `tools` profile but does not run as a persistent container. To synchronize library states, execute:
```
docker compose --profile tools run --rm recyclarr sync
```

### Lidarr Integration
Lidarr is present in the media stack for music management. The live homepage widget is intentionally omitted due to the large scope of the Lidarr API. Interactions with the Lidarr API must keep responses bounded.

### YouTube Download Integration
The stack includes Pinchflat and the custom `yt-torznab` indexer. Pinchflat queues YouTube downloads; `yt-torznab` wires YouTube content into the existing Arr/ qBitTorrent flow. Both components are present and active.

### Transcoding Services
Tdarr and FileFlows are **not** part of the current stack. There is no active transcode service. Do not assume transcoding capabilities exist.

<!-- FILE: Runbook/Homelab-Ops-Agent.md -->

## Homelab Ops Agent Capabilities

The Homelab Ops Agent provides the following operational controls. All actions require admin confirmation where marked.

## Secrets Management for Arr Stack

Sonarr and Prowlarr API keys are stored in root-only secret files at `/opt/arrstack/secrets/{sonarr,prowlarr}_apikey`. These files are mounted into the orchestrator at `/run/secrets/*` and accessed via `SONARR_APIKEY_FILE` and `PROWLARR_APIKEY_FILE` environment variables. Keys must never appear inline in documentation or code.

## Media Probe Tool

The `media_probe` tool provides read-only metadata summaries for media files. It is path-scoped to `MEDIA_DIR` and refuses access to any paths outside this directory. The tool uses `ffprobe` (included in the image) to generate audio, subtitle, and video summaries, including a dual-audio flag. Mounts are read-only (`/media` RO mount).

## Sonarr Lookup Logic

The `sonarr_lookup` function filters results client-side because Sonarr's `/series` API ignores the `?term=` parameter and returns the entire library. Client-side filtering ensures that queries return only matching series (e.g., `sonarr_lookup "Bleach"` returns id 31). This enables the grab path requiring a specific series ID.

## Sonarr Manual Import Agent Suite

The Sonarr agent suite provides read and admin capabilities for managing media imports.

### `sonarr_import_candidates`
- **Access:** Read-only.
- **Function:** Lists files Sonarr can import from a specified folder or download. Returns proposed episode mapping, quality, languages, and rejection status for each file.

### `sonarr_manual_import`
- **Access:** Admin (confirm-gated).
- **Function:** Builds the ManualImport payload server-side using a fresh candidate list. The model provides only the folder or download ID.
  - Imports files mapped to episodes, skipping rejected items unless `include_rejected` is set.
  - Supports `import_mode` options: `move` or `copy`.
  - Design goal: Reliable operation with low model burden.

### Safety Controls and Known Limitations
- **Confirm Gate:** All write operations require explicit human confirmation before execution.
- **File Filtering Behavior:** The parameter `filterExistingFiles=true` excludes files already in the library only when the scanned folder maps to a standard download path. It does not exclude existing files if the scanned folder maps directly to library paths (e.g., a test folder may return already-present files as "importable").
- **Operator Responsibility:** The confirm-gate serves as the primary safety net. Operators must verify the file count and action type before approving imports.
- **Proposed Hardening:** Warn when candidate paths overlap with existing series library paths.

### Phase 1 Completion Status
Phase 1 (Bleach parity) is complete. The full pipeline is operational in HAI with admin-gate and confirm-on-write controls:
1. Search → Releases → Grab
2. Manual Import
3. Media Probe (dual-audio verification)
4. qBit add/action/remove

**Next Phase:** Phase 2 (Radarr mirror, Seerr requests, Plex library refresh on Softserve).

## Radarr, Seerr, and Plex Integration Architecture

The integration layer follows Phase-1 patterns: `arrclient`/`pending_actions`, admin-gating, risk classification, audit logging, and Tier-2 checks.

### Tool Inventory and Permissions

All write tools require admin authentication and explicit confirmation. No secrets are inlined in code or documentation.

**Radarr (Movies)**
*   `radarr_lookup`: Client-side filtering of `/movie` results.
*   `radarr_releases`: Interactive release search with language support; identifies dual-audio and guid/indexerId.
*   `radarr_import_candidates`: Retrieves candidate imports.
*   `radarr_search`: Initiates movie search (`MoviesSearch`). Write tool.
*   `radarr_grab`: Grabs a specific release by guid+indexerId via `POST /release`. Write tool.
*   `radarr_manual_import`: Builds ManualImport payload server-side from fresh candidate list; maps one movie mapping per file (distinct from Sonarr’s per-episode mapping). Write tool.

**Seerr / Overseerr**
Client: `seerrclient.py` interacting with `/api/v1` using `X-Api-Key`.
*   `seerr_requests`: Filters requests by status (all/pending/approved/etc.). Resolves title via best-effort `/movie|/tv/{tmdbId}` lookup to display readable confirmations (e.g., "approve #41: Dune") instead of bare IDs. Read tool.
*   `seerr_approve`: Approves requests. Write tool.
*   `seerr_decline`: Declines requests. Flagged as **MED risk**; requires Tier-2 check. Write tool.
*   `seerr_add`: Adds requests by TMDB ID. Write tool.

**Plex**
Client: `plexserver.py` interacting with Softserve’s Plex instance (`<softserve-address>:32400`) using `X-Plex-Token`. Tautulli remains the read side for analytics.
*   `plex_sections`: Lists libraries. Read tool.
*   `plex_refresh`: Scans a section by key or all libraries (`force=true` triggers metadata refresh). Write tool. Plex rescans files but never modifies them.

### Secret Management and Configuration

Secrets are stored in root-only files under `/opt/arrstack/secrets/` and mounted read-only (`:ro`) at `/run/secrets/*`. Environment variables point to these files (e.g., `RADARR_APIKEY_FILE`, `SEERR_APIKEY_FILE`, `PLEX_TOKEN_FILE`) within `docker-compose.override.yml`.

*   **Radarr Key**: Sourced from `config.xml`.
*   **Seerr Key**: Sourced from container’s `settings.json` (`main.apiKey`).
*   **Plex Token**: Sourced from Softserve’s registry key `HKCU\Software\Plex, Inc\.\…\PlexOnlineToken`.

Code retrieves secrets via `config._secret_value`, which reads the mounted files. Never inline keys in code or documentation.

**Configuration Parameters**
*   `SEERR_URL`: Default `overseerr:5055`.
*   `SEERR_APIKEY`: Secret reference.
*   `SEERR_TIMEOUT`.
*   `PLEX_URL`: Default `<softserve-address>:32400`.
*   `PLEX_TOKEN`: Secret reference.
*   `PLEX_TIMEOUT`.

### Operational Constraints and Known Issues

**Seerr Status Enum Mapping**
The Overseerr API uses the `MediaRequestStatus` enum for request status, distinct from media-availability enums. Correct mapping is enforced by tests:
1.  PENDING
2.  APPROVED
3.  DECLINED
4.  FAILED
5.  COMPLETED

Incorrectly mapping `4→available` or treating raw value `5` as unfulfilled is a defect. Fulfilled requests must render as `request=completed`.

**Manual Import Safety Net**
For Sonarr and Radarr manual imports, `filterExistingFiles=true` does not exclude already-imported files when the scanned folder maps to library paths. The confirm-gate and file count serve as the primary safety mechanisms against duplicate imports.

## Sonarr and Radarr: Custom Format Scoring Tools

### Tool Interface Definition
Sonarr and Radarr share the v3 API for quality profile and custom format management. Lidarr is excluded from this interface set, as it uses API v1.

The agent interface provides four tools:

**Read Operations:**
- `quality_profiles(service)`: Returns quality profiles and any existing custom-format scores.
- `custom_formats(service)`: Returns custom format ID, name, and specification summary (including Recyclarr regex-based formats).

**Write Operations (Admin + Confirmation Required):**
- `custom_format_add(service, name, regex)`: Creates a `ReleaseTitleSpecification` custom format that matches release titles by regex.
- `quality_profile_score(service, profile_id, format_id, score)`: Sets a custom format's score within a quality profile. The tool re-fetches the target profile and PUTs it back to ensure the write targets current state.

### Workflow Sequence
1. Query `custom_formats` and `quality_profiles` to locate relevant IDs.
2. Define a new regex-based custom format (e.g., "Dual Audio") via `custom_format_add`.
3. Assign a positive score to that custom format within the desired quality profile via `quality_profile_score`.

### Design Doctrine: Profile Scoring Conformance
Every quality profile in the stack must carry explicit custom-format scores for all active custom formats it intends to weight. A profile showing "no custom-format scores set" is non-conformant with respect to that format's influence on grab decisions.

Verification instrument: `quality_profiles(service)` output shows per-profile score lists. Any format without an explicit score entry does not influence grabbing behavior, regardless of its presence in the global custom-format list.

### Test Coverage and Status Tracking
- Test suite: 330 green (includes 7 additions in `test_phase2_tools.py`).
- Deployment state: deployed version matches source.
- Backup reference: `pre-phase3-20260626-175850`.
- Admin gating verification: write operations are gated by admin confirmation. Preview mode is available for all tools; live writes require explicit confirmation. See Status CURRENT-PROJECTS §Sonarr/Radarr Agent State for current deployment health.

## Lidarr Integration and API Constraints

Lidarr uses API v1, distinct from the v3 standard used by other `*arr` clients. The `arrclient.ArrClient` class handles this via an `api_version` parameter. The default is `v3`; Lidarr explicitly sets `"v1"`. Existing callers remain unaffected.

### Tooling and Response Bounds

Three tools interface with Lidarr. Responses are bounded to prevent overload, given the library size (e.g., >4MB raw API returns). The homepage widget is excluded.

*   `lidarr_lookup`: Reads artists. Filters client-side and caps results.
*   `lidarr_albums`: Reads albums for a single artist.
*   `lidarr_search`: Writes via ArtistSearch or AlbumSearch. Requires admin confirmation. Previews are shown before execution; non-admin users are denied access.

### Secret Management

The Lidarr API key is stored in `/opt/arrstack/secrets/lidarr_apikey`. It is mounted read-only (`:ro`) and referenced via the `LIDARR_APIKEY_FILE` environment variable. This follows the standard pattern for other stack secrets.

### Deferred Workstreams

*   **Recyclarr sync**: Folded into Phase 4 (docker/compose tooling) because it requires host-docker control, not accessible via the arrclient path.
*   **Pinchflat + yt-torznab**: Parked for a future dedicated pass on the YouTube pipeline. This involves bespoke API wiring and TVDB integration.
