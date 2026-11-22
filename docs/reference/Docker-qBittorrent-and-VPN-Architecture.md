# SundaeStackarr - qBittorrent and VPN Architecture



## qBittorrent + VPN Architecture (container-based)

The VPN now runs **entirely inside Docker**. The host-level ExpressVPN service has been retired.

### Traffic routing and network topology

The media stack uses Docker Compose service linking to enforce VPN tunneling for all torrent traffic. The `expressvpn` container owns the virtual network interface; dependent services share its namespace rather than binding directly to host interfaces.

#### Component roles and network constraints

- **`expressvpn`**: Runs ExpressVPN (Lightway UDP) using image `misioslav/expressvpn:14.1.0.13058`. This container creates and owns the `tun0` tunnel interface on `arrnet`. Configuration requirements:
  - Network Lock kill-switch is **ON**.
  - `ALLOW_LAN=true` to permit upstream service connectivity.
- **`qbittorrent`**: Uses `network_mode: service:expressvpn` with `depends_on: expressvpn`. This configuration shares the `expressvpn` container’s network namespace, removing an independent network stack. As a design fact:
  - All qBittorrent traffic traverses the `tun0` tunnel.
  - If the tunnel drops, the kill-switch blocks torrent traffic.
  - The service does **not** bind `tun0` directly and is **not** in host-mode.

#### Port exposure

The `expressvpn` container publishes the following ports for external access:
- `8080` (WebUI)
- `13338` tcp+udp (torrent listen)

### Retired / removed\n\nThe following components and configurations have been disabled in this environment:\n\n- Host-level ExpressVPN (`expressvpn-service`): **STOPPED AND DISABLED**.

<no change>

## qBittorrent App Settings

Live config: `/opt/arrstack/qbittorrent/qBittorrent/qBittorrent.conf`.
WebUI port `8080`. Credentials stored in the credentials reference. Localhost auth disabled.</no change>

### Paths (container → host)

| Purpose | Container path | Host path |
|---------|----------------|-----------|
| Incomplete downloads | `/downloads` | `/mnt/ssdshared/qBittorrent Downloads in Progress` |
| Default save (completed) | `/media` | `/mnt/media` |
| Finished `.torrent` export | `/archive` | `/mnt/ssdshared/ChilliCore finished Archive` |

### Categories

All with Auto TMM enabled and empty `save_path`: `radarr`, `tv-sonarr`, `lidarr`, `prowlarr`,
`Movies`, `Tv Shows`.

### Ratio / rate limits & behaviour

| Setting | Value |
|---------|-------||
Max ratio ‹ 6.85—stop seeding at this ratio.‚ ||
Max inactive seeding | 1440 min (24 h) |Upload limit | 50,000 KB/s (50 MB/s) |
Download limit ‹ 100,000 KB/s⁻(100 MB/s)‚ ||
Max active downloads / uploads| 5 / 5||
Max active total / checking |8/10|
Slow-torrent threshold ‹ 30 KB/s (up and down) |
anonymous mode‹ enabled |
auto TMM ‹( default)‚ enabled|Preallocation |enabled|
`.!qB` extension on incomplete|enabled|
Merge trackers |enables||
torrent listen port‹ 13338 ( tcp + udp, published by the expressvpn container) |",

### Auth whitelist

The Docker Media Stack allows unauthenticated access only from the following subnets:

- `<lan-subnet>/24` (LAN)
- `172.18.0.0/16` (Docker bridge, enabling internal container-to-container communication without password prompts)
