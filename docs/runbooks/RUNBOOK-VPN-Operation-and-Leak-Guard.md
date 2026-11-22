# RUNBOOK — VPN Operation & qBittorrent Leak Guard



## ExpressVPN & Networking

### VPN Overview (container-based)

- ExpressVPN runs **inside the `expressvpn` Docker container** (`misioslav/expressvpn:14.1.0.13058`, pinned tag so Watchtower won't bump it), not on the host
- Protocol: Lightway UDP, connected to a Smart-location exit IP
- Network Lock (kill switch): on inside the container — entrypoint override disables it for ~15s after start so the supervisor's first DNS lookup succeeds, then it re-arms once the tunnel is up
- `ALLOW_LAN=true` lets the container (and qBittorrent, which shares its netns) reach `<lan-subnet>/24` despite the kill-switch
- **Only qBittorrent traffic uses the VPN** — `network_mode: service:expressvpn` shares the `expressvpn` container's network namespace (including its tun0)
- All other stack services use plain `arrnet` bridge networking, unaffected by the VPN
- Prepaid through 2028

The **host-level** ExpressVPN client (`expressvpn-service`) has been **stopped and disabled** — `/opt/expressvpn/` is left installed for now (cheap rollback) but not running. Old host-level workarounds removed as part of this migration:
- The `vpn-dns-fix.conf` Tailscale CGNAT nftables drop-in (no longer needed — no host Network Lock to block Tailscale)
- `sudo apt-vpn install` / "disable Network Lock to pull images" — no longer applicable on the host (container Network Lock doesn't block the host's Docker daemon)

```bash
# Container VPN status/logs
docker exec expressvpn expressvpnctl status
docker logs expressvpn --tail 50

Settings file (inside container): `/expressvpn/etc/settings.json` (persisted via the `/vpn_shared` volume → `/opt/arrstack/expressvpn/shared`)

### ⚠ Real-IP leak — qBit MUST be bound to tun0 (the DMCA-incident class)

**The leak shape:** an unbound qBittorrent advertises the **real WAN IP** to swarms *alongside*
the VPN IP — visible in qBit's own log (`Detected external IP` lists BOTH the ExpressVPN exit and
the real IP). Note qBit's TZ is `America/Chicago` while DMCA timestamps are usually UTC — mind
the offset when correlating.

**Mechanism:** qBittorrent not bound to an interface (`[BitTorrent] Session\Interface` /
`Session\InterfaceName` empty) listens on `0.0.0.0` — including `eth0` (the arrnet bridge), not
just `tun0`. The shared netns still carries `default via 172.18.0.1 dev eth0`, and
`ALLOW_LAN=true` + Network Lock's allow-LAN chains permit bridge traffic, which the host then
NATs to the internet with the real WAN IP. This path is only dead while something blocks
bridge→internet — **any host↔bridge↔internet change can reopen it; re-run the leak test below
after any such change.**

**The `curl api.ipify.org` leak test above does NOT catch this** — it only exercises the default route (tun0), which passes, while the parallel bridge *listener* leaks. Use the better test below.

**Fix:** with qBit **stopped** (it rewrites the conf on exit), bind it to `tun0` by NAME in `/opt/arrstack/qbittorrent/qBittorrent/qBittorrent.conf`:
```
[BitTorrent]
Session\Interface=tun0
Session\InterfaceName=tun0
```
Bind by NAME so it follows tun0's IP across reconnects. qBit now **fails closed**: VPN down → tun0 gone → qBit can't bind → it stops instead of falling back to the WAN IP.

The binding lives in gitignored `qBittorrent.conf` (not in git), but **two backstops make that safe** (both drill-verified):

**Backstop 1 — netns kill-switch (chain `QBITKILL`).** qBit runs as **uid 1000**; the expressvpn daemon runs as **root** — so uid-1000 iptables rules touch ONLY qBit. In the expressvpn netns:
```
# egress: qBit (uid 1000) may leave via eth0 only to WebUI clients / bridge / LAN — else DROP:
iptables -N QBITKILL
iptables -A QBITKILL -o eth0 -m owner --uid-owner 1000 -p tcp --sport 8080 -j RETURN
iptables -A QBITKILL -o eth0 -m owner --uid-owner 1000 -d 172.18.0.0/16 -j RETURN
iptables -A QBITKILL -o eth0 -m owner --uid-owner 1000 -d <lan-subnet>/24 -j RETURN
iptables -A QBITKILL -o eth0 -m owner --uid-owner 1000 -j DROP
iptables -I OUTPUT 1 -j QBITKILL
# ingress: kill torrent-port traffic arriving on the real-IP path (published :13338 on eth0):
iptables -I INPUT 1 -i eth0 -p tcp --dport 13338 -j DROP
iptables -I INPUT 1 -i eth0 -p udp --dport 13338 -j DROP
```
This does NOT affect local services or the WebUI (port 8080 is allowed; torrent traffic runs over tun0). Drill-verified: a fully-unbound qBit still could not emit the real IP.

**Backstop 2 — the VPN watchdog self-heals it** (see next section). Every 3 min it re-asserts `QBITKILL` (Network Lock rebuilds its chains on reconnect and can flush it) and, if qBit is listening on `0.0.0.0`/`172.18.x:13338`, re-binds it to tun0 + restarts (or stops it fail-closed on failure, with a Discord alert). So a config wipe recovers within ~3 min instead of silently leaking.

**⚠ Editing `qBittorrent.conf` over ssh — the INI keys have a literal backslash** (`Session\Interface`). ssh→bash→sed HALVES `\\`→`\`, so a sed that matches/emits the backslash silently no-ops. Use **line-addressed** editing instead: `grep -nE '^Session.Interface='` (`.` matches the backslash) to get the line number `N`, then `sed -i "Ns/=.*/=tun0/"` to set only the value.

**Better leak test (checks the actual bound interface, not just the default route):**
```bash
# (qBit ships /bin/netstat, no ss):
docker exec qbittorrent netstat -tln | awk '$4 ~ /:13338$/ {print $4}'
# qBit's detected external IP(s) must be VPN-only:
docker exec qbittorrent grep 'Detected external IP' /config/qBittorrent/logs/qbittorrent.log | tail
# kill-switch present?
docker exec expressvpn iptables -S QBITKILL
```

### VPN Watchdog — auto-recovery

**Problem it solves:** the `expressvpn` tunnel can drop to Disconnected and get stuck (stale session / the
daemon needs `background enable` before `connect` works), and **Docker's healthcheck still reports the container
"healthy" while the tunnel is dead**. Since qBittorrent shares the netns and Network Lock is always-on, a dead
tunnel = qBittorrent silently blocked (downloads stall, Cleanuparr reaps them). The stuck-daemon tell:
hot-looping connectivity probes, ~11k DNS errors/30s in the console.

**The fix — a self-healing watchdog (no second VPN; there is only the one container VPN, qbit-only):**
- `/usr/local/bin/expressvpn-watchdog.sh` + `expressvpn-watchdog.timer` (systemd, every 3 min, `OnBootSec=2min`,
  `flock` so recoveries never overlap). Log: `/var/log/expressvpn-watchdog.log`; Discord-notifies on recover/fail.
- Health check = the ACTUAL tunnel: `docker exec expressvpn ip route get 1.1.1.1` must go `… dev tun0` (NOT
  `dev eth0`). On failure it runs: stop qbit → `background enable` → `set networklock false` → re-login with
  `$CODE` only if "not logged in" → `connect smart` → wait for tun0 → `set networklock true` → start qbit.
- Verified: forced `expressvpnctl disconnect` → watchdog auto-recovered the tunnel + qbit in ~20s.
- **Also enforces the qBit LEAK GUARD every tick:** `ensure_killswitch` re-asserts the
  `QBITKILL` netns chain + the eth0:13338 INPUT drops (Network Lock can flush them on reconnect), and
  `enforce_qbit_bind` re-binds qBit to tun0 if it ever listens on `0.0.0.0`/`172.18.x:13338` (else stops it
  fail-closed + alerts). Both are no-ops in steady state. Drill-verified: unbound qBit → kill-switch held (no
  real-IP leak) → auto re-bound in ~13s. See "Real-IP leak — qBit MUST be bound to tun0" above.

**Manual recovery** (if the watchdog ever can't): see the `reference-expressvpn-recovery` memory — same sequence.
The KEY gotcha both rely on: `connect` is a silent no-op unless `background enable` is set first, and it's async
(fires the RPC, tun0 comes up ~20-25s later). **DEFERRED (accepted risk):** the deeper root-cause fix (patch the
image's `start.sh` `activate_account`) is intentionally NOT done — the watchdog is the accepted mitigation;
revisit start.sh only if the watchdog proves insufficient.

### Reaching qBittorrent

qBittorrent has no IP of its own — it's reachable only via the `expressvpn` container, which publishes the ports:

- `http://expressvpn:8080` — Docker DNS, from any `arrnet` container (used by Caddy, Homepage)
- `http://<sundae-address>:8080` — from the LAN
- `http://172.18.0.1:8080` — still works from bridge containers too (Docker's published-port DNAT applies to the bridge gateway IP as well as the host's LAN IP), so Sonarr/Radarr/Lidarr's existing `172.18.0.1:8080` download-client config did **not** need to change

### Pi-hole & VPN Isolation

Pi-hole runs on a Docker **macvlan** network using eth1 as its parent interface. This gives the container its own MAC address and IP (<pihole-address>) directly on the physical Motherboard 1Gb NIC, completely bypassing the host kernel's iptables. The (now removed) host ExpressVPN never had visibility into Pi-hole's traffic anyway.

**No VPN fix scripts needed.** The old `pihole-vpn-fix.sh` and `pihole-vpn-fix.service` have been removed.

**Hyper-V requirement:** MAC Address Spoofing must be **On** for the "PiHole" vNIC on SundaeStackarr. If re-creating the VM or the NIC, set this via:
```powershell
Set-VMNetworkAdapter -VMName "SundaeStackarr" -Name "PiHole" -MacAddressSpoofing On
```

### systemd-resolved

Stub listener is disabled (`DNSStubListener=no` in `/etc/systemd/resolved.conf`). Upstream DNS is `<router-address>` (the RS300 — see the "Pi-hole / DNS Architecture" section above for how it fans out to the .21/.31/DO resolvers). This replaced the old VPN-tun0 DNS (`<tailscale-address>`), which died once the host VPN was removed.

### Docker DNS

`/etc/docker/daemon.json`:
```json
{
  "dns": ["<router-address>"]
}
```

Same RS300 entry point as the host — bridge containers reach it via eth0 → switch → RS300 → races .21/.31/DO. The `expressvpn`/`qbittorrent` containers use their own internal Lightway DNS regardless of this setting.

---
