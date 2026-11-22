# External Connectivity & Remote Access



## Container-Based VPN Architecture

ExpressVPN runs inside a Docker container. Only qBittorrent traffic is tunneled; all other services use plain `arrnet` bridge networking.

| Item | Value |
|------|-------|
| VPN container | `expressvpn`
| Image | `misioslav/expressvpn:14.1.0.13058` (pinned tag; Watchtower does not update it)
| Network | `arrnet` bridge
| Protocol | Lightway UDP, Smart-location exit
| Kill switch | Network Lock ON inside the container. The entrypoint disables Network Lock briefly (~15s) at start to allow the first DNS lookup, then re-arms once `tun0` is up.
| LAN reach | `ALLOW_LAN=true` permits the container (and qBittorrent) to reach `<lan-subnet>/24 despite Network Lock.
| qBittorrent networking | `network_mode: service:expressvpn`. Shares the expressvpn container's netns/tun0; `depends_on: expressvpn`.
| Published ports (via expressvpn) | 8080 (qBit WebUI), 13338 tcp+udp (qBit torrent)
| Configuration persistence | `/expressvpn/etc/settings.json` persisted via `/vpn_shared` volume → `/opt/arrstack/expressvpn/shared`
| Subscription | Prepaid through 2028. Do not suggest alternatives.

The host-level ExpressVPN client (`expressvpn-service`) is stopped and disabled. The binary remains installed at `/opt/expressvpn/` for rollback only; it does not run. Workarounds specific to host-level VPN operation are no longer required or present:

- Tailscale CGNAT nftables drop-in `vpn-dns-fix.conf` is removed (host Network Lock no longer blocks Tailscale).
- Docker image pull / apt bypass via Network Lock disablement is not required; container Network Lock does not block the host's Docker daemon or apt.
- Pi-hole VPN fix scripts (`pihole-vpn-fix.sh`, `pihole-vpn-fix.service`) are removed.

qBittorrent does not use host-mode networking and does not bind `tun0` at the host level. It has no independent IP address. Access its WebUI via the `expressvpn` container:

| URL | Context |
|-----|---------|
| `http://expressvpn:8080` | Docker DNS, from any arrnet container. **Preferred for new configuration.**

The Docker bridge gateway (`172.18.0.1`) resolves to qBittorrent and other arrnet services because Docker's published-port DNAT applies to the gateway IP. Existing configurations pointing to `172.18.0.1:{port}` remain functional but are considered convenience/fallback only. Prefer Docker DNS service names (e.g., `expressvpn:8080`, `flaresolverr:8191`, `prowlarr:9696`) for new configuration.

## SSH Access Configuration

SSH client configuration resides on Softserve at `C:\Users\<user>\.ssh\config`. The following aliases are defined for standard access:

| Alias | Target Host | User / IP | Auth Key | Notes |
|-------|-------------|-----------|----------|-------|
| `ssh sundae` | SundaeStackarr | `<user>@<sundae-address>` | `sundae_ed25519` | LAN access |
| `ssh sundae-ts` | SundaeStackarr | `<user>@<tailscale-address>` | `sundae_ed25519` | Tailscale access; node may be offline — see **Tailscale Connectivity** in [[Network-Infrastructure]] |
| `ssh softserve-ts` | Softserve | `Administrator@<tailscale-address>` | Default key | Tailscale access |
| `ssh do-pihole` | DO Pi-hole | `root@<droplet-tailscale-address>` | `do_pihole_ed25519` | **Preferred** Tailscale access |
| `ssh do-pihole-public` | DO Pi-hole | `root@<droplet-address>` | `do_pihole_ed25519` | Fallback only; public IP |
| `ssh raspi` | MinuteMan | `<user>@<minuteman-address>` | `id_ed25519` | LAN access |
| `ssh raspi-ts` | MinuteMan | `<user>@<tailscale-address>` | `id_ed25519` | Tailscale access |

### Raw Fallback Commands

When SSH config aliases are unavailable, use the following direct commands:

**SundaeStackarr**
```bash
ssh -i C:\Users\<user>\.ssh\sundae_ed25519 <user>@<sundae-address>
<secret>   # Password fallback; see `Reference/Credentials.md`
```

**HomeAssistantOS Debug Shell**
Access requires explicit port and key specification, as this host is not in the standard SSH config aliases:
```bash
ssh -i C:\Users\<user>\.ssh\haos_ed25519 -p 22222 root@<home-assistant-address>
```

### Key Storage

All private SSH keys are stored in `C:\Users\<user>\.ssh\` on Softserve:
- `sundae_ed25519`: SundaeStackarr access
- `do_pihole_ed25519`: DO Pi-hole access
- `id_ed25519`: MinuteMan (raspi) access
- `haos_ed25519`: HomeAssistantOS debug shell access

## example.org DNS & DDNS

- **Registrar:** No-IP. Registration only; nameservers point at DigitalOcean (`ns1/ns2/ns3.digitalocean.com`).
- **DNS hosting:** DigitalOcean.
- **DO API token / DO DNS record IDs** — see `Reference\Credentials\network-credentials.md`.

### DO DNS Records (all → home external IP, TTL 60)

All domain objects (@, wildcard *, router, softserve) resolve to the home external IP address.

| Name | Points to |
|------|-----------|
| `@` | home external IP |
| `*` | home external IP |
| `router` | home external IP |
| `softserve` | home external IP |

**Design rule:** TTL for these records is set to 60 seconds.

### DDNS Update Chain

The home network maintains dynamic DNS records and firewall access lists through a coordinated update chain between the Pi and DigitalOcean (DO).

1. **Local IP detection and DNS update**
   - **Schedule:** Every 15 minutes (`*/15 * * * *`) via Pi cron.
   - **Mechanism:** `/usr/local/bin/ddns_update.py` queries the token-gated IP-echo endpoint on DO (port 9876, secret path) to retrieve the home external IPv4 address.
   - **Action:** If the retrieved IP differs from current records, the script PATCHes all four DO A records and sends a Discord alert.

2. **Firewall ACL synchronization**
   - **Schedule:** Every 5 minutes (`*/5 * * * *`) via DO cron.
   - **Mechanism:** `update-pihole-acl.sh` resolves the public IP of `router.example.org`.
   - **Action:** Updates `ufw` rules to allow traffic on ports 53, 61208, and 9876 from the resolved IP.

3. **IP-echo service**
   - **Location:** DigitalOcean.
   - **Runtime:** Systemd unit `ip-echo.service` executing `/usr/local/bin/ip_echo.py` on port 9876.
   - **Behavior:** Returns the caller’s IPv4 address at a specific secret path; returns 404 for all other paths.
   - **Credentials:** The secret path is defined in `Reference\Credentials\network-credentials.md`.

### No-IP / example.org Domain Details

`router.example.org` is a DDNS A record. Record management for `example.org` operates via the DigitalOcean API; DigitalOcean is the authoritative nameserver.

Credential references:
- No-IP account credentials: see `Reference\Credentials\network-credentials.md` §<No-IP>
- Tailscale API key (used by the Avery ACL script): see `Reference\Credentials\network-credentials.md` §<Tailscale>
