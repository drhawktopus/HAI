# RUNBOOK — DO Pi-hole & MinuteMan Host Services



## MinuteMan Pi (<minuteman-address>)

SSH: `ssh raspi` (Tailscale: `<tailscale-address>`)

### Pi-hole (peer resolver)

Native Pi-hole install (not Docker). Runs the same curated 5-list HaGeZi blocklist as Sundae (238,507 gravity domains). Block mode NXDOMAIN. Receives `.lan` records from Sundae's push sync into `/etc/pihole/hosts/custom.list` — do not edit that file by hand; edit Sundae's lan.list instead.

Hardware reality check (benchmarked): Pi 3B+, quad-core, 906MB RAM, ~20MB/s SD card. RAM is the constraint — keep gravity well under ~1M domains. Disk speed doesn't matter for query serving (FTL holds gravity in RAM).

### Crontab

```
*/15 * * * *  python3 /usr/local/bin/ddns_update.py >> /var/log/ddns_update.log 2>&1
```

### bt-proxy — the Pi's radio as Home Assistant's Bluetooth proxy (since 2026-10-03)

`denvera/bt-proxy` (ESPHome-compatible Bluetooth proxy for Linux; plaintext ESPHome API, mDNS
`_esphomelib._tcp`) at `/opt/bt-proxy` (git checkout + `.venv`, deps bleak + zeroconf, no uv).
Systemd: `bt-proxy.service` (`ExecStart=/opt/bt-proxy/.venv/bin/python -m bt_proxy --log-level INFO
--max-connections 6`, `Restart=on-failure`). HA sees it as ESPHome entry **"Bluetooth Proxy"** and as a
connectable remote scanner `bt-proxy (<mac-address>)`. Through it HA runs `govee_ble` (H5106 ×2,
H5075) and `govee_ble_air_purifier` (the H7124 filters — one persistent connection each, hence the
6-connection cap). Range measured 2026-10-03 from the Pi: every Govee device in the house, −37 to −88.
This is the Pi's only Bluetooth client; `theengs-gateway.service` is installed but **disabled** (its
start/stop scanning leaked BlueZ memory — see the hang section). Healthy check: `sudo hcitool con` lists
four `LE … CENTRAL` links (the purifiers). After an HA restart the proxy reconciles stale links for a
minute or two and the purifier entities read `unavailable` meanwhile — that is normal; wait before acting.
Update: `sudo git -C /opt/bt-proxy pull --ff-only && sudo /opt/bt-proxy/.venv/bin/pip install -q /opt/bt-proxy && sudo systemctl restart bt-proxy`.
Logs: `journalctl -u bt-proxy`. Install script of record: Claude scratchpad `install_bt_proxy.sh`
(2026-10-03 journal).

### bt-mem-watch — bluetoothd memory watchdog (timer, every 10 min)

`/usr/local/bin/bt_mem_watch.sh` via `bt-mem-watch.timer`. Logs bluetoothd RSS + MemAvailable to
`/var/lib/bt-mem-watch/history.log` every run. REPORTS (journal, tag `bt-mem-watch`) when MemAvailable
< 100 MB; ACTS only when **bluetoothd RSS > 64 MB**: restarts `bluetooth`, then `bt-proxy` (and
`theengs-gateway` via `try-restart`, a no-op while it is disabled), and appends to `restarts.log`. Limit derivation: healthy RSS 3–6 MB; the OOM had
187 MB after 43 days (~4.3 MB/day) → 64 MB ≈ 2 weeks of growth with ~120 MB headroom. Live decoy on
install (limit lowered to 1 kB → ACT fired, services came back, limit restored). To check it is alive:
`systemctl list-timers bt-mem-watch.timer` and `tail /var/lib/bt-mem-watch/history.log`.

### Also on this Pi

NUT server for the upstairs Eaton (3493, see HomeAssistant-Energy); Glances on 61208 (Homepage
widget, API v3); Tailscale; the lan.list sync target (Sundae pushes over SSH). Since 2026-10-03 the box
boots to the CLI (`multi-user.target`), Raspberry Pi Connect is off (`rpi-connect on` to reverse) and
Docker/containerd are disabled (`systemctl enable --now docker` to reverse; Portainer and the 2025
Requestrr/Radarr images are still on disk, unused — Requestrr lives on Sundae). A tty1 autologin shell
still starts user `pipewire` (~20 MB, harmless). Config backup before those changes:
`Z:\Archives\minuteman\minuteman-config-20261003.tar.gz` (Pi-hole config without the 1 GB FTL DB, NUT,
Theengs unit, DDNS script, crontabs, swap, BlueZ conf, NM connections).

### MinuteMan hung — SSH dead, DNS still answers → it is an OOM from bluetoothd, not a Pi quirk

Seen 2026-09-30 → 2026-10-03 (Eaton/NUT data stopped 2026-09-30 23:52 CDT; the owner power-cycled 10-03
morning). The previous boot's kernel OOM dump (`journalctl -b -1 -k`) showed **bluetoothd 187 MB and
dbus-daemon 157 MB** (normally ~3 MB each) plus pihole-FTL 236 MB on a 906 MB box with 512 MB SD swap,
thrashing for ~40 min. Cause: Theengs' `-sd 10 -tb 50` starts and stops a BLE scan every minute, and
repeated bleak scan start/stop leaks BlueZ D-Bus state (bleak discussion #1273, ble2mqtt #18); 43 days
of uptime filled RAM. FTL serves gravity from RAM so DNS keeps answering while everything else is dead.
Check: `ps -o rss= -C bluetoothd,dbus-daemon` — anything far above ~3 MB is the leak building.
Mitigation (2026-10-03, same day): the `bt-mem-watch` timer above restarts BlueZ and its clients before
the leak can OOM the box; the desktop, Pi Connect and Docker were switched off (~600 MB available at
boot, swap 0, vs ~525 MB with 285 MB swapped before). The leak's source, Theengs' start/stop scanning,
was disabled the same day once Home Assistant's consumers were moved to the local Bluetooth entities.
If it ever hangs again: a power cycle recovers it, then read `/var/lib/bt-mem-watch/history.log`.

---

## DO Pi-hole Droplet (<droplet-address> / Tailscale <droplet-tailscale-address>)

SSH: `ssh do-pihole` (root, via Tailscale, key `do_pihole_ed25519`)

### Role

Remote Pi-hole replica / secondary DNS. Port 53 is UFW-restricted to known IPs only — the ufw
allow-list on the droplet is the authority on who is allowed. Admin UI reachable ONLY via
Tailscale: **http://<droplet-tailscale-address>:8080/admin** (:80/:443 are reserved for the friend-onboarding
Caddy — see [[Friend-Onboarding-Design]]; webserver ports set via `pihole-FTL --config webserver.port '8080o,8443os,[::]:8080o,[::]:8443os'`, `dns.port=53` untouched). Upstreams are
IPv4-only (the droplet has no IPv6 routing).

⚠ **The DDNS updater runs from ROOT's crontab on the droplet** — under a non-root user its
`/var/log` redirect fails and cron never runs the script, a silent stall. The `ip-echo` service
on :9876 can hang (listening but not accepting). When DO records drift from the real home IP:
restart `ip-echo`, run the updater once, confirm the records. A stalled DDNS also makes the DO
`plex_monitor.py` (which resolves `router.example.org`) post a **FALSE "Plex DOWN"** — the
monitor's self-healing port logic covers UPnP drift, not a wrong home IP (see § Plex Monitor).

### Crontab

```
*/5  * * * *  /usr/local/bin/update-pihole-acl.sh >> /var/log/pihole-dns-acl.log 2>&1
*/10 * * * *  /usr/local/bin/update-avery-acl.sh >> /var/log/pihole-avery-acl.log 2>&1
0    3 * * *  /usr/local/bin/pihole-sync.sh >> /var/log/pihole-sync.log 2>&1
25   9 * * *  scp avery_status → <user>@<sundae-address>:/home/<user>/.avery_status (key: pihole_sync_ed25519)
*/15 * * * *  /usr/local/bin/plex_monitor.py >> /var/log/plex_monitor.log 2>&1
```

### Avery Auto-ACL (`update-avery-acl.sh`)

> **2026-10-12:** the script now tracks TWO roaming devices from one Tailscale API call, each with its own ufw rule, comment and state file: **AVERY** (iPad `<tailscale-address>`, comment "Avery STL summer…", `last_avery_ip`/`avery_status`) and **owner** (the owner's iPhone `iphone181`, `<tailscale-address>`, comment "the owner iPhone roaming", `last_owner_ip`/`owner_status`). To add another device, add one line to the `DEVICES` array. Backup of the pre-change script: `/usr/local/bin/update-avery-acl.sh.bak-20261012`.
> **Token:** needs a Tailscale **API access token** (`tskey-api-…`, NOT an auth key), max 90-day expiry; locker: `Reference\Credentials\network-credentials.md` § Tailscale. The previous token had been dead for weeks (script logging `ERROR Tailscale API unreachable/auth-failed` every 10 min) before it was replaced on 2026-10-12. Next expiry ≈ 2027-01-10. When it dies, edit `TS_API_KEY` in the script.

Tracks the external IP of Avery network and keeps DO's UFW port-53 rule current:

- **Beacon**: Tailscale REST API `/devices` → finds Avery iPad (<tailscale-address>) → first public IPv4 from `clientConnectivity.endpoints`. (Local `tailscale status` can't see it — the iPad is DERP-relayed.)
- **On change**: deletes old ufw rule, allows new IP on 53/udp+tcp (comment "Avery STL summer - remove ~Aug 2026"), verifies the rule applied
- **Fail-safe**: API outage or no data → keeps the existing rule
- **Discord**: alerts ONLY on apply-failure or API outage (12h throttle); routine rotations are silent
- **Daily rollup**: status line lands in the 9:30 UTC stack-health Discord embed ("Avery DNS" field) via the scp + `stack-health-check.sh` on Sundae
- State: `/var/lib/pihole-dns-acl/{last_avery_ip,avery_status,last_api_alert}`
- **Standing status (the owner's call, 2026-08-20): LEFT IN PLACE** — his own separate thing; do NOT
  raise removal as due. When he does call for it: delete the two crons, the ufw rule, and the
  Avery field in stack-health-check.sh.

### Pi-hole Sync

`pihole-sync.sh` (nightly 3am pull from Sundae) retries the docker-stop SSH call and both rsyncs 3× with 10s spacing (`retry` helper). Uses `SUNDAE_HOST=<sundae-address>` via Softserve's Tailscale subnet router — Sundae's own Tailscale node is offline (ExpressVPN CGNAT block).

### Plex Monitor

Script: `/usr/local/bin/plex_monitor.py` (root cron, every 15 min; perms **700**). Resolves
`router.example.org` (home IP via DDNS) and directly checks `:<port>/identity`. Discord alert on down/up/IP change.
State: `/var/lib/plex_monitor/state.json` — now also remembers the last-good `port`.

**Self-healing port:** the direct check is PRIMARY and fully
plex.tv-independent. **Only if the direct check fails** does it consult plex.tv (`plextv_port()`, machine
`8403b1de…`) to distinguish "UPnP moved the external port" (server up → self-heal to the new port + post a "port
changed, no outage" note) from "actually down" (→ the normal DOWN alert). If plex.tv is unreachable it treats the
server as down — so a real outage is never masked. Seed/default port `28364`; the token is in the script (root-only).
A **static router port-forward** would let us drop the plex.tv fallback entirely (a router change — the owner's call).

### IP Echo Service

Systemd: `ip-echo.service` (`/usr/local/bin/ip_echo.py`) on port 9876.
Returns caller's IPv4 at the secret path (locker: `network-credentials.md`), 404 for any other path.
Used by Pi DDNS updater to discover home's current external IP.

---
