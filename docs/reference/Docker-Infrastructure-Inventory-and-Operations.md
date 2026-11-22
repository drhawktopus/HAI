# SundaeStackarr - Infrastructure, Inventory, and Operations



## Infrastructure and Location

The SundaeStackarr arr/media Docker stack runs on an Ubuntu VM (`<sundae-address>`) hosted on the Softserve Hyper-V machine (`<softserve-address>`).

- **Stack root:** `/opt/arrstack/` containing the compose file and per-app bind-mount config directories.
- **Owner:** `<user>` (UID 1000).
- **Access:** All commands execute on the VM via `ssh sundae`.

## Credential Policy

API keys, WebUI/admin passwords, Discord tokens, and YouTube API keys are excluded from this document. See `Reference\Credentials\arr-stack-credentials.md` for credential management.

## Container Inventory

Two Docker networks:
- **arrnet** — bridge, `172.18.0.0/16` — all services except Pi-hole.
- **piholenet** — macvlan on eth1 — Pi-hole only (its own MAC/IP `<pihole-address>` on the physical NIC).

| Container | Image | Network mode | Port(s) | Purpose |
|-----------|-------|--------------|---------|---------|
| pihole | pihole/pihole:latest | macvlan (piholenet on eth1) | 53 (DNS), 80 (web UI) | Network-wide DNS/ad-blocking — <pihole-address>, bypasses host iptables |
| radarr | lscr.io/linuxserver/radarr:latest | arrnet | 7878 | Movie management |
| sonarr | lscr.io/linuxserver/sonarr:latest | arrnet | 8989 | TV management |
| lidarr | lscr.io/linuxserver/lidarr:latest | arrnet | 8686 | Music management |
| prowlarr | lscr.io/linuxserver/prowlarr:latest | arrnet | 9696 | Indexer manager (syncs to arr apps) |
| jackett | lscr.io/linuxserver/jackett:latest | arrnet | 9117 | Indexers not in Prowlarr's catalog |
| **expressvpn** | misioslav/expressvpn:14.1.0.13058 | arrnet | 8080 (qBit WebUI), 13338 tcp+udp (qBit torrent) | Container-based ExpressVPN (Lightway UDP); owns tun0 + the qBittorrent published ports |
| **qbittorrent** | lscr.io/linuxserver/qbittorrent:latest | service:expressvpn (shared netns) | — (published by expressvpn) | Torrent client; all traffic routed through the expressvpn container's tunnel |
| cleanuparr | ghcr.io/cleanuparr/cleanuparr:latest | arrnet | 11011 | Stalled-download cleanup + proactive seeker search |
| caddy | caddy:latest | arrnet | 80 | Reverse proxy for all `*.lan` names |
| seerr | seerr/seerr:latest | arrnet | 5055 | Media requests (compose service key is `overseerr`, historical) |
| doplarr | ghcr.io/activexray/doplarr_rs:latest | arrnet | — | Discord → Seerr request bot (Rust; slash `/request`, no web UI). Replaced Requestrr 2026-07-05 |
| pinchflat | ghcr.io/kieraneglin/pinchflat | arrnet | 8945 | YouTube series downloader |
| yt-torznab | python:3.12-slim | arrnet | 9797 | Torznab adapter + fake-qBit for the YouTube pipeline (currently manual-only) |
| tautulli | lscr.io/linuxserver/tautulli | arrnet | 8181 | Plex activity monitor |
| claudediscordbot | local build (`/opt/arrstack/claudediscordbot`) | arrnet | — | JukeBot Discord music bot; excluded from Watchtower |
| watchtower | containrrr/watchtower:latest | arrnet | — | Auto-updates images nightly at 4am; Discord notifications |
| recyclarr | ghcr.io/recyclarr/recyclarr:latest | tools profile | — | TRaSH-guide sync; run manually, not persistent |

Watchtower auto-updates everything **except** `claudediscordbot` (label
`com.centurylinklabs.watchtower.enable=false`) and `expressvpn` (pinned to tag `14.1.0.13058` so the
image is never bumped).

---

## Common Operational Commands

```bash

# Start / stop / restart
cd /opt/arrstack && docker compose up -d
cd /opt/arrstack && docker compose stop
cd /opt/arrstack && docker compose restart <service>

# Status & resources
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker stats --no-stream

# Logs
docker logs <container> --tail 50
docker logs <container> --since=5m -f

# Reload Caddy config without restart
docker exec caddy caddy reload --config /etc/caddy/Caddyfile

# Reload Pi-hole DNS after editing lan.list
docker exec pihole pihole reloaddns

# Recyclarr (tools profile, not started by default)

# ⚠️ ALWAYS dry-run first — recyclarr.yml manages the 4K WEB / 1080p WEB Radarr profiles with

# reset_unmatched_scores=ON, which ZEROES hand-tuned custom-format scores in those profiles. See the RUNBOOK

# "QUALITY PROFILES — PRESERVE THE HAND-CRAFTED ONES" note. The HAI recyclarr_sync tool defaults to dry-run.
docker compose --profile tools run --rm recyclarr sync --preview  # dry run (review the diff FIRST)
docker compose --profile tools run --rm recyclarr sync            # apply (only after reviewing the dry-run)

# Install packages (host VPN is gone — plain apt resolves DNS normally now)
sudo apt install <package>

# JukeBot rebuild
docker compose build --no-cache claudediscordbot && docker compose up -d claudediscordbot
```

> **Note:** Docker image pulls no longer require the old "disable Network Lock first" workaround now
> that the VPN is container-scoped rather than host-wide.
