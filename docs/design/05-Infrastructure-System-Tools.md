# Homelab Ops Agent - Infrastructure & System Tools



## HAI Read-Only Toolset

The following tools provide read-only access to homelab state and configuration. They are unchanged by this project:

*   `check_plex`: Interfaces with Tautulli.
*   `check_downloads`: Retrieves the active download list from qBitTorrent.
*   `check_services`: Reports up/down status for arr services.
*   `check_host`: Host health check.
*   `read_config`: allow-listed config files, read-only.
*   `fetch_url`: read-only HTTP GET. LAN endpoints only from the `LAN_FETCH_ALLOW` host:port list (so **not** ports 8989, 9696 or 8080); any other host only when every address it resolves to is a public internet address, each redirect re-checked, HTML returned as text in `offset` pages (2026-10-31, `lanfetch.py`).
*   `write_config`: Admin-level config file writes. Restricted to allow-listed files. Uses a **preview→confirm** pattern.
*   `wake_host`: Wake-on-LAN capability.

This project adds write/action capabilities layered on top of this foundation, including access to arr/qBit/media APIs that the read tools do not touch.

## Homepage Configuration
- **Structured UI**: Tools to add, edit, or remove service tiles, bookmarks, widgets, and groups.
- **Direct Config Edit**: `write_config` tool for targeted edits to `services.yaml`. This tool provides auto-backup and preview→confirm workflows.
- *Note: Implementation uses either the structured UI or `write_config` exclusively; `write_config` is sufficient for current requirements.*

## Container & Web Server Control
- **Docker/Compose**: Start, stop, restart containers; tail logs; pull and update images.
  - **Safety**: Image pulls and updates require explicit admin confirmation due to risk profile.
- **Caddy**: Trigger configuration reloads.
- **Watchtower**: Trigger manual image updates.

## Pi-hole Management
- **DNS Records**: Edit `lan.list` records.
- **Service Control**: Restart DNS service (`restartdns`).
- **Statistics**: Query block stats and manage blocklists.
- **Constraints**:
  - Host cannot reach Pi-hole directly at `<pihole-address>` due to macvlan isolation.
  - Never mount Pi-hole configuration on SMB shares.

## Filesystem & Disk Utilities
- **Scope**: Read-only status checks for `/mnt/media` and SSD staging area.
- **Operations**:
  - Report free space (`df` equivalent).
  - Report folder sizes (`du` equivalent/stat-style).

## Version Control
- **Git Integration**: Commit and push homelab-config changes to the configuration repository following edits.

## Infrastructure Operations Tools

### Homepage and Caddy Configuration

Homepage and Caddy configuration edits use the existing `write_config` tool. This tool supports admin access, preview-then-confirm workflows, and timestamped backups. It is allow-listed for `/hostcfg/homepage` and `/hostcfg/caddy`, both mounted read-write. No new tool is required for these config edits.

Caddy requires a reload after configuration changes via `docker exec`. See host-control decision in [[05-HOMELAB-OPS-AGENT-PLAN]] §Host-Control.

### Disk Usage Monitoring

The `disk_usage` tool provides read-only, ungated access to disk free space. It is implemented in `infra.py` and verified via test suite `tests/test_disk.py` (349 passing tests).

#### Capabilities
- Instant free-space reporting using `statvfs` across all orchestrator-visible mounts.
- Path-scoped queries for `/media`, `/downloads`, and `/docs`. Queries to other paths (e.g., `/etc`) are refused.
- Optional detailed subfolder sizes via `du --max-depth=1` when `sizes=true` is passed.

#### Configuration and Constraints
- **Mounts:**
  - `/media`: 45T array.
  - `/downloads`: SSD staging (`:ro` mount of `/mnt/ssdshared`).
  - `/docs`.
- **Timeout:** `du` operations are bounded by `DISK_DU_TIMEOUT` (default 25s) to prevent hangs on large arrays. On timeout, the tool returns a message directing users to query specific subfolders.
- **Config Keys:** `DISK_ROOTS`, `DISK_DU_TIMEOUT`.

#### Performance Notes
Free-space reporting is the primary fast operation. Folder-size calculations over CIFS/SSD mounts are slow and treated as best-effort opt-in features.

### Host-ops broker architecture and capabilities

The **`hostops` sidecar** (`hostops_sidecar.py`) is the single privileged container for host operations. It mounts `/var/run/docker.sock` and communicates with the Docker Engine API via `http.client` (stdlib, no CLI dependencies). The orchestrator remains socket-free and interacts with the sidecar through the thin client `hostops.py`.

#### Security and access control
*   **Network:** The API is exposed on `arrnet` only; it is not host-published.
*   **Authentication:** All endpoints require a bearer token via the `x-hostops-token` header. The secret is stored in `/opt/arrstack/secrets/hostops_token` and referenced by the `HOSTOPS_TOKEN_FILE` environment variable in both the sidecar and the orchestrator.
*   **Protected containers:** The broker refuses actions on the protected set: `hostops`, `orchestrator`, `expressvpn`, `pihole`. This prevents accidental severing of VPN netns, DNS, or the orchestrator/broker itself.
*   **Input validation:** Container names are validated for alphanumeric characters, dashes, and dots only (no path traversal). Log tails are capped.

#### Endpoint registry
The broker exposes a narrow API. All write actions require admin confirmation. Destructive actions carry a MED risk banner unless noted otherwise.

**Read endpoints (ungated):**
*   `GET /health`
*   `GET /docker/ps` (tool: `docker_status`)
*   `GET /docker/logs` (tool: `docker_logs`)

**Action endpoints (admin + confirm):**
*   `POST /docker/restart`: Restarts a container. Refuses protected containers.
*   `POST /caddy/reload`: Executes `caddy reload --config /etc/caddy/Caddyfile`. Completes the Caddyfile edit loop via `write_config`.
*   `POST /pihole/restartdns`: Executes `pihole reloaddns`. Updates lists, flushes cache, and applies `lan.list` edits without bouncing FTL. Note: Pi-hole v6 deprecated `restartdns`; `reloaddns` is the current valid command for non-destructive reloads.
*   `POST /recyclarr/sync` (**the media add-on's route**, since 2026-11-15: declared in `hostops_sidecar_ext_media.py`, loaded by `hostops_sidecar._load_oneoff_routes` from any `hostops_sidecar_ext_*.py` beside the sidecar; the extension's code fixes image and command, the request only picks dry-run or not; a broker without the file has no such route): Runs a one-off `ghcr.io/recyclarr/recyclarr:latest sync` container on the one-off network (`HOSTOPS_ONEOFF_NETWORK`, default `arrstack_arrnet`) with `/opt/arrstack/recyclarr:/config`. Default behavior is dry-run (`apply=false`). `apply=true` executes the real sync and carries a destructive warning regarding profile score resets. Pairs with `quality_profile_score`/`custom_format_add` tools for targeted tweaks.
*   `POST /docker/stop`: Stops a container. Refuses protected containers. Destructive.
*   `POST /docker/start`: Starts a container. Allowed on protected containers to restore service.
*   `POST /watchtower/run`: Runs a one-off `containrrr/watchtower --run-once` to pull and recreate updated containers. Honors disable labels. Destructive.
*   `POST /git/status`: Returns git status for `/opt/arrstack`. Read-only.
*   `POST /git/commit`: Commits local changes in `/opt/arrstack` as uid 1000 (`<user>`). Commit message passed via env var `MSG` to prevent shell injection. Local only (no push). Destructive.

#### Design constraints and behaviors
*   **Fixed commands:** Exec endpoints use hardcoded command strings per endpoint, never request-supplied strings. This prevents arbitrary-exec/shell capabilities.
*   **One-off container execution:** The `_run_oneoff` helper (create→start→wait→logs→remove) pulls missing images automatically if not present locally, as the Engine API `create` does not auto-pull.
*   **Timeouts:** Broker socket timeout is 180s. `recyclarr_sync` uses a dedicated `RECYCLARR_SYNC_TIMEOUT` of 200s (`arr_config.py`, the media add-on's settings) to accommodate external calls to Sonarr/Radarr.
*   **Extension routes are mounted files too:** `docker-compose.override.yml` mounts `orchestrator/hostops_sidecar_ext_media.py` beside `/hostops_sidecar.py` (`:ro`). A new extension file needs its own mount line and a recreate. Verified 2026-11-15: a dry-run through the route answered 200, `dry_run=True`, `exit_code=0`, "No changes"; an unknown route 404.
*   **Recyclarr safety:** the owner’s hand-crafted quality profiles in Radarr (`4K WEB`, `1080p WEB`) have `reset_unmatched_scores: enabled: true`. A real sync zeroes custom-format scores not defined in `recyclarr.yml` for these profiles. `delete_old_custom_formats` is disabled. Dry-run default prevents accidental score zeroing.

See [[Docker-Media-Stack]] §QUALITY PROFILES — PRESERVE THE HAND-CRAFTED ONES for profile preservation details.
