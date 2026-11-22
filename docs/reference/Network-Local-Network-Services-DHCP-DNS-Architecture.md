# Local Network Services: DHCP & DNS Architecture



## DHCP Reservations: RS300 Router Configuration

**Location:** `RS300` > `ADVANCED` > `Setup` > `LAN Setup`

The following table lists all **25** IP-MAC reservations configured on the router. These entries define the static address assignments for the lab network.

> ⚠ **2026-09-16 — the entire Address Reservation table was found WIPED (0 rows) and rebuilt.** Discovered when Mothership drifted off `.76` (to a dynamic `.128`) after being powered off — its reservation, and every other, was gone. Root cause not confirmed (suspected firmware/config event on RS300 firmware **V1.2.7.2**; the whole table vanished at once). Rebuilt to parity from this doc + the Codex intake handoff (`Codex Home\tablet-intake-canonical\DRIVER-HANDOFF-2026-09-02.md` held the three new-tablet MACs). **The rebuild took the count 20 → 25:** it backfilled the three 2026-09-04 tablets (faramir/boromir/gimli, rows 21–23) and added **BigHonker `.167`** and **Lorex NVR `.116`** (rows 24–25), which had been unreserved despite being hardcoded lab-wide.
>
> 🔒 **A binary config backup now exists:** `E:\Documentation\State\network-backups\RS300-config-backup-20260916.cfg`. Restore via `ADVANCED → Administration → Backup Settings → Restore` (upload the file). **Re-run the Back Up after ANY future reservation change** so the recovery file stays current — a wipe is then a one-click restore, not a rebuild.
>
> ⚠ **RS300 lease behaviour:** the router honours an existing lease until the lease table is flushed, so adding a reservation does NOT move a device that already holds a different lease, and a client-side `ipconfig /release`+`/renew` just re-gets the same address. Only a **router reboot** (flush) or natural lease expiry (24 h) migrates devices onto their reserved IPs.

| # | IP Suffix | MAC Address | Router Name / Note |
|---|---|---|---|
| 1 | `.31` | `<mac-address>` | MinuteMan (Physical Raspberry Pi) |
| 2 | `.204` | `<mac-address>` | SoftServe |
| 3 | `.13` | `<mac-address>` | MORPHEUS |
| 4 | `.17` | `<mac-address>` | MORPHEUS |
| 5 | `.76` | `<mac-address>` | MOTHERSHIP |
| 6 | `.2` | `<mac-address>` | VPN-PC |
| 7 | `.205` | `<mac-address>` | RemoteVM1 (SundaeStackarr, USB 1Gb NIC) |
| 8 | `.209` | `<mac-address>` | ShawnsVM |
| 9 | `.212` | `<mac-address>` | Herpstat4 |
| 10 | `.21` | `<mac-address>` | Sundae-eth1-pihole (Motherboard 1Gb NIC) |
| 11 | `.71` | `<mac-address>` | gandalf |
| 12 | `.72` | `<mac-address>` | frodo |
| 13 | `.73` | `<mac-address>` | sam |
| 14 | `.74` | `<mac-address>` | merry |
| 15 | `.75` | `<mac-address>` | pippin |
| 16 | `.78` | `<mac-address>` | legolas |
| 17 | `.80` | `<mac-address>` | aragorn |
| 18 | `.82` | `<mac-address>` | rigby-motherboard-nic (`eno1`) |
| 19 | `.110` | `<mac-address>` | mordecai |
| 20 | `.79` | `<mac-address>` | rigby-pcie-nic (`enp1s0`) |
| 21 | `.22` | `<mac-address>` | faramir (TB-X606F class-B, joined 2026-09-04) |
| 22 | `.93` | `<mac-address>` | boromir (TB-X606F class-B, joined 2026-09-04) |
| 23 | `.96` | `<mac-address>` | gimli (TB-X606F class-B, joined 2026-09-04) |
| 24 | `.167` | `<mac-address>` | BigHonker (added 2026-09-16) |
| 25 | `.116` | `<mac-address>` | Lorex NVR N844A8 (added 2026-09-16) |

### Design Rules and Notes

*   **Rigby Dual-Port Reservation:** The host `rigby` holds two separate reservations, one for each physical network port. `.82` is assigned to the onboard NIC (`eno1`, named `motherboard`). `.79` is assigned to the PCIe card (`enp1s0`, named `pcie`). Both addresses are held simultaneously when both ports are active.
*   **SundaeStackarr Layering:** Row 10 (`.21`) and Row 7 (`.205`) belong to the same VM host, SundaeStackarr. The physical Raspberry Pi running the hypervisor is `MinuteMan` at `.31` (Row 1). Row 10 (`eth1`) serves as the parent interface for the Pi-hole container’s macvlan.
*   **Reservation Application Behavior:** Adding a reservation for an IP address already held by a device takes effect immediately. The rule "a reservation does not move a device that already holds a lease" applies only when reserving a *different* address than the one currently leased.
*   **Common Misconceptions:** The Vizio TV (`.70`) is **not** DHCP reserved but holds `.70` by stable retention across reboots. ⚠ The Lorex NVR (`.116`) was historically unreserved but **is now reserved as of 2026-09-16** (row 25) — older notes calling it unreserved are stale.

### Unreserved Addresses

Only the Vizio TV (`.70`) is unreserved (dynamically assigned; stable by retention, not by reservation). It does not appear in the reservation table. (The Lorex NVR `.116`, previously listed here, was added to the reservation table on 2026-09-16 — row 25.)

## Pi-hole DNS Resolution Rules

Pi-hole loads **every** file in the container directory `/etc/pihole/hosts/`. Any file present in this directory is served as live DNS data. Stale or backup files left in this path cause silent misrouting, where work may succeed against the wrong host without error.

### File Placement Doctrine
*   Only `lan.list` and `custom.list` belong in `/etc/pihole/hosts/`.
*   All backups must be stored in `/opt/arrstack/pihole/etc-pihole/hosts-archive/`.
*   **Never** place `.bak` files, temporary edits, or archived lists in the `hosts/` load path.

### Persistence and Mount Structure
The host directory `/opt/arrstack/pihole/etc-pihole` is bind-mounted to the container’s `/etc/pihole`. Editing files on the host or inside the container modifies the same persistent state. Changes require an explicit reload:
```bash
docker exec pihole pihole reloaddns
```

### Verification Protocol
Every DNS name must resolve to **exactly one** address. Ambiguity (multiple addresses for one name) is a defect that hides errors by returning the first match via `getent`.

Run this verification after every change:
```bash
for n in gandalf frodo sam merry pippin legolas; do
  echo "$n.lan -> $(docker exec tablet-foundry getent hosts $n.lan | awk '{print $1}' | tr '\n' ' ')"
done
```
If a name maps to multiple IPs, the DNS configuration is incorrect. Count the outputs; do not eyeball them.

### Dependency: DNS as Roster Consumer
The scheduler function `scheduler.tablet_alive()` connects via `<host>.lan` hostname, not the IP in `TABLET_NET`. A tablet is invisible to the system if its DNS record is missing, even if it is correctly listed in `TABLET_NET`, `TABLETS`, code files, and tools. Adding a tablet requires adding a corresponding DNS record.

## DHCP reservation activation: reboot the router, not the client

Adding or modifying a DHCP reservation does not affect devices holding an existing lease. The router honours existing bindings until expiration (24 h), even across device reboots.

To apply a reservation change immediately:
1. Apply the configuration change.
2. Reboot the router to flush the lease table.

Rebooting the client device does not release the lease or trigger a new offer.

### Impact of router reboot
A router reboot causes an AP bounce. The USB segment remains active as it is independent of WiFi and the router. WiFi nodes self-heal after the bounce.

Nodes may flap for several minutes post-bounce, with different nodes unreachable in each probe sample. Do not assess node health from a single probe; a node requiring physical intervention will remain down across multiple probe rounds on both USB and WiFi paths.

### The USB segment — redundancy and dual-path routing

The USB segment provides independent connectivity between tablets, Rigby, and Mordecai, serving as a redundant path to every node. It also serves as Mordecai's only route to the LAN.

#### Topology and addressing

| direction | over | provides |
|---|---|---|
| tablets ↔ rigby | USB (`br0`, `10.55.0.x`) | Wake-on-LAN capability for either side; independent of WiFi and the router |
| rigby → mordecai | point-to-point wired (`enp1s0`) | Mordecai's sole LAN connectivity |
| any tablet | WiFi `.71`–`.80` **or** USB `10.55.0.x` | Two independent routes to every node |

**Design doctrine:** Every tablet is cabled to Rigby and keeps an active USB link; the wire is the designed main path and WiFi the backup (Tolkien's wire-first routing is currently switched off — [[Fellowship-Order-Taker-Design]] §12). Each presents one `enx*` interface; a missing USB link is a **fault** (Tolkien's USB keeper restores one a reboot dropped; see [[Tablet-Architecture-Topology-Fleet-Status]] § Restore Procedure and [[Tablet-USB-Networking-RNDIS]] traps). Sméagol (armv7l Samsung SM-T560NU) is on the wire at `10.55.0.18`.

#### Address mapping

| node | WiFi | USB | notes |
|---|---|---|---|
| frodo | `.72` | `10.55.0.11` | aarch64 |
| sam | `.73` | `10.55.0.12` | aarch64 |
| legolas | `.78` | `10.55.0.13` | aarch64 |
| merry | `.74` | `10.55.0.14` | aarch64 |
| pippin | `.75` | `10.55.0.15` | aarch64 |
| gandalf | `.71` | `10.55.0.16` | aarch64 |
| aragorn | `.80` | `10.55.0.17` | aarch64 |
| sméagol | — (wlan0 reservation set for future) | `10.55.0.18` | armv7l/32-bit Samsung SM-T560NU |
| gimli | `.96` | `10.55.0.19` | aarch64 |
| boromir | `.93` | `10.55.0.20` | aarch64 |
| faramir | `.22` | `10.55.0.21` | aarch64 |

#### Operational procedures

* **Restoring the segment:** The USB segment does not survive a reboot of a tablet or Rigby ([[Tablet-USB-Networking-RNDIS]] §4f). Restore connectivity with:
  ```bash
  usbnet_up.py --apply
  ```
  This script configures the tablet half over WiFi adb, then executes the root half on Rigby via `run_remote.py`.

* **Diagnosis and fault isolation:**
  * A node unreachable via one path is not down. Verify the alternative path before concluding a hardware failure.
  * Reachable via USB but not WiFi localizes the fault to the Access Point. Down on both paths indicates a node fault.
  * `Lab\bin\probe.py` monitors only WiFi addresses and reports nodes with healthy USB links as unreachable. Always verify USB connectivity from Rigby before concluding unreachability:
    ```bash
    ssh rigby "for n in 11 12 13 14 15 16 17; do timeout 2 bash -c \"echo > /dev/tcp/10.55.0.\$n/8022\" 2>/dev/null && echo 10.55.0.\$n UP || echo 10.55.0.\$n no; done"
    ```
  * Confirm a node's own USB address with `getprop ro.serialno` and `ip -4 -o addr show rndis0`.

* **Rigby failure mode:** Rigby is the central hub for all three network legs (WiFi, USB, Wired) and is the single point of failure. Its suspend/hibernate targets are masked to preserve network stability. Do not un-mask these targets without defining the failover strategy for the tablet segment and Mordecai's connectivity.

* **MAC randomization:** Disable per-network MAC randomization on an Android device **before** reserving its address in the router. Android randomizes per SSID; reserving a randomized MAC and later disabling randomization orphans the reservation, requiring full configuration rework.

* **Duplicate IP detection:** If two devices share an IP, ARP maps the address to whichever device answers first, causing probes, SSH, and sweeps to silently reach the wrong host. Symptoms include a node appearing offline in scans despite being powered on. If a device is "missing" but appears in the router's attached-devices list, check for duplicate IPs before investigating hardware.

* **Address reservation authority:** The router's "attached-devices" list reflects only currently powered-on devices and is **not** the authority on address availability. Verify reserved addresses in **ADVANCED → Setup → LAN Setup → Address Reservation**. Note that a device may be offline (e.g., BigHonker sleeping) while still holding its reserved address.

#### Credentials and remote access

* **RS300 Admin Credential:** Stored in `Credentials\network-credentials.md`. the owner handles login; the assistant drives the authenticated session.
* **Mothership GPU Service Ports (`<mothership-address>`):**
  * `8188`: ComfyUI #19 (Flux.1/SDXL family-photo pipeline, "ComfyUI Server" scheduled task)
  * `8189`: Flux.2 render bundle (`F:\comfyui-rocm`, ROCm/GGUF, manual launch; see [[RUNBOOK]] §"Second ComfyUI — Flux.2 render bundle")
  * `61208`: Glances system telemetry (SYSTEM task)
  * `11434`: Ollama
  * Design doctrine: ComfyUI instances run on permanent, fixed ports. Do not swap ports.
* **Windows Workgroup:** Softserve, Mothership, and BigHonker reside in the **`MOSSMATRIX` workgroup**. No devices are domain-joined. (See [[RUNBOOK]] §"Softserve → Workgroup / domain" for history of removed AD domain).

#### Tailscale routing

* SundaeStackarr's native Tailscale node is offline.
* Remote hosts and DO reach SundaeStackarr via Softserve's Tailscale subnet router (`SUNDAE_HOST=<sundae-address>` through `<tailscale-address>`).

## DNS / Pi-hole: 3-resolver architecture
The lab DNS infrastructure uses a three-resolver design.

### Topology

The homelab DNS infrastructure consists of three Pi-hole instances and an RS300 router acting as the central DHCP server.

| Resolver | IP Address | Role |
|----------|------------|------|
| **Sundae Pi-hole** | <pihole-address> (Docker macvlan on eth1) | **Master**: Source of truth for `.lan` records (`lan.list`) and curated blocklist. Returns `NULL` (`0.0.0.0` / `::`) for blocked domains. |
| **MinuteMan Pi-hole** | <minuteman-address> (Pi 3B+, native install) | **LAN peer**: Provides redundancy for LAN filtering and `.lan` resolution during maintenance of Sundae/Softserve. Returns `NXDOMAIN` for blocked domains. |
| **DO Pi-hole** | <droplet-address> (public) / <droplet-tailscale-address> (Tailscale) | **Remote resolver**: Serves external remote clients. Returns `NULL` for blocked domains. |

**DNS Query Distribution**: The RS300 router is the sole DHCP server. It hands out its own IP (<router-address>) as the DNS address to all LAN clients.

The RS300 does not implement strict failover. It races and distributes queries across all configured upstream slots:
*   Slot 1: Sundae (.21)
*   Slot 2: MinuteMan (.31)
*   Slot 3: DO (Avery-monitoring only)

**Conformance Requirement**: Every slot must independently filter ads and resolve `.lan` names. If any slot returns a non-filtered response, ad leaks will occur.

**Per-device Statistics Limitation**: The RS300 does not expose DHCP DNS fields to upstream resolvers; it relays DNS queries itself. All LAN queries appear in Pi-hole analytics as originating from <router-address>. Per-device statistics are unavailable unless Pi-hole assumes the DHCP role or clients are manually configured to use <pihole-address>.

### "Is Pi-hole blocking my smart device?" — search by VENDOR DOMAIN, not by client

The masking above hides WHICH device asked, but every query still records the DOMAIN and its STATUS.
So the question is answerable per vendor even though it is not answerable per device. Query FTL's
database read-only on **both LAN resolvers** (the router races Sundae and MinuteMan, so either can be
the one that blocks):

```
# Sundae (docker)                      # MinuteMan (native)
docker exec pihole pihole-FTL sqlite3 -readonly /etc/pihole/pihole-FTL.db "<SQL>"
ssh raspi "sudo pihole-FTL sqlite3 -readonly /etc/pihole/pihole-FTL.db \"<SQL>\""
```

Blocked statuses (FTL v6): `1,4,5,6,7,8,9,10,11,15,16`. Group vendor-matching domains
(`domain LIKE '%irobot%'` etc.) by domain with a blocked-count column, and also check `reply_type`
(`2` NXDOMAIN, `7` SERVFAIL, `8` REFUSED) — "not blocked" is not "answered". Always print the total
query count in the window first, so a broken query cannot read as "nothing blocked".

**Measured 2026-09-26** (7 days, Sundae 3.6 M queries, MinuteMan 1.5 M, DO 0.3 M): **zero** blocked
queries for iRobot/Roomba (`*.irobotapi.com`, `*.irobot.com`, its AWS IoT `*-ats.iot.us-east-1.amazonaws.com`
endpoints), GE Cync (`*.gelighting.com`, `*.xlink.cloud`) or Govee (`*.govee.com`) on any of the three
resolvers; replies were real IPs/CNAMEs (2 stray REFUSED on `app.govee.com` out of ~45 k). No
allow/deny/regex entries touch those vendors. So when these devices misbehave, **DNS is not the first
suspect — whitelisting will not help**; look at the app, the device's Wi-Fi band, or the vendor cloud.
Clean one-variable test if it is still in doubt: pause filtering on BOTH LAN resolvers for a few
minutes (`docker exec pihole pihole disable 10m` on Sundae, `ssh raspi "sudo pihole disable 10m"`),
retry the device, and let the timer re-enable. Pausing only one leaves the other still filtering.

### Sundae Pi-hole (primary)

Docker container using **macvlan** on eth1 — own MAC/IP directly on the physical NIC,
bypasses host iptables entirely.

| Setting | Value |
|---------|-------|
| DNS address | <pihole-address> |
| Web UI | http://<pihole-address>/admin |
| Upstream DNS | AdGuard (94.140.14.14 / 94.140.15.15) + Quad9 (9.9.9.9 / 149.112.112.112) |
| Config path | `/opt/arrstack/pihole/etc-pihole/` (local disk, not SMB) |
| Gravity domains | Curated HaGeZi set; count is a live measurement |

### Curated Blocklist configuration

The 39 legacy lists remain disabled in configuration but are not deleted.

| List | Purpose |
|------|---------|
| `pro.plus.txt` (HaGeZi Pro++) | Aggressive general ad/tracker block; requires no whitelist |
| `doh.txt` | Blocks DNS-over-HTTPS bypass endpoints to force client traffic through Pi-hole |
| `native.roku.txt` | Roku ads/telemetry |
| `native.winoffice.txt` | Windows/Office telemetry |
| `native.amazon.txt` | Amazon/Alexa device telemetry |

**Gravity update procedure:**
*   **Sundae:** `docker exec pihole pihole -g`
*   **MinuteMan:** `ssh raspi "sudo pihole -g"`

### `.lan` Hostname Sync (Sundae → MinuteMan)

Sundae is the authoritative source for all `*.lan` DNS records. The master file is `/opt/arrstack/pihole/etc-pihole/hosts/lan.list` on Sundae.

Most names resolve to `<sundae-address>`, where Caddy on port 80 routes requests to backend containers (e.g., `qbit.lan` routes to `expressvpn:8080`).

**Sync Mechanism**
- **Script:** `/usr/local/bin/push-lan-to-minuteman.sh` on Sundae (mode 755).
- **Trigger:** Cron job runs every 15 minutes (`*/15 * * * *`). Logs to `/home/<user>/lan-sync.log`.
- **Logic:** The script computes a SHA256 hash of `lan.list` and compares it to `/home/<user>/.lan_last_pushed_hash`. If the hash differs, the file is SCP'd to MinuteMan at `/tmp/lan.list.new`, then copied via `sudo cp` to `/etc/pihole/hosts/custom.list`, ownership is set to `pihole:pihole`, and `pihole reloaddns` is executed. If the hash matches, the script exits silently.
- **Auth:** Authentication uses the dedicated key `/home/<user>/.ssh/lan_sync_ed25519` on Sundae, authorized in MinuteMan's `~/.ssh/authorized_keys`. The connection is unidirectional: Sundae → MinuteMan only.

**Operator Procedures**
1. Edit `/opt/arrstack/pihole/etc-pihole/hosts/lan.list` on Sundae.
2. Apply changes locally on Sundae: `docker exec pihole pihole reloaddns`.
3. Wait up to 15 minutes for the cron job to push updates to MinuteMan, or run `/usr/local/bin/push-lan-to-minuteman.sh` manually.

**Constraints**
- Do not hand-edit `custom.list` on MinuteMan; it is overwritten by the sync process.

### IPv6 (AAAA) Blocking and Internet Path Policy

The LAN has no global IPv6 (GUA) internet path. The RS300 does not advertise RA or DHCPv6 DNS. Devices rely on IPv4 for all internet traffic.

Home Pi-holes filter AAAA queries according to their respective block modes. Ad domains resolve to `::` or NXDOMAIN; real domains resolve normally. Because there is no IPv6 internet path, no IPv6 DNS-bypass vector exists.

### DO Pi-hole — Upstream Configuration

The DigitalOcean droplet hosting Pi-hole has no IPv6 routing. `/etc/pihole/pihole.toml` configures only IPv4 upstream DNS resolvers. The configured upstreams are:

*   AdGuard: `94.140.14.14`, `94.140.15.15`
*   Quad9 ECS: `9.9.9.11`, `149.112.112.11`
*   Quad9: `9.9.9.9`, `149.112.112.112`

No IPv6 upstreams are present in the configuration.

### DO ↔ Sundae Sync Hardening

`/usr/local/bin/pihole-sync.sh` on DO pulls Sundae’s gravity and lan config as a downstream replica.

Execution schedule: nightly at 3:00.

Reliability: The script wraps the docker-stop SSH call and both rsyncs in a `retry 3 10` helper (3 attempts, 10s apart) to survive transient network errors.

Connectivity: Connects to `SUNDAE_HOST=<sundae-address>` via Softserve’s Tailscale subnet router. See [[Network-Infrastructure]] §Tailsale Configuration.

### Avery Auto-ACL: Dynamic UFW Rule for External DNS Access

Avery’s external device uses DO’s Pi-hole (<droplet-address>) as DNS. DO’s UFW restricts port 53 access to known IPs only. The system automatically tracks changes to Avery’s public IPv4 address and updates the firewall accordingly.

- **2026-10-12:** also tracks the owner's iPhone (`iphone181`, `<tailscale-address>`) with a separate rule/state; details and token notes in `Runbook\RUNBOOK-DO-Pihole-and-MinuteMan-Services.md` § "Avery Auto-ACL".
- **Script:** `/usr/local/bin/update-avery-acl.sh` (DO, root, mode 700), cron `*/10 * * * *
  → `/var/log/pihole-avery-acl.log`
- **Beacon:** Tailscale REST API `/devices` (DO’s local `tailscale status` cannot see
  DERP-relayed endpoints) — identifies Avery’s iPad (Tailscale IP <tailscale-address>), extracts the first public IPv4 from `clientConnectivity.endpoints`
- **State:** `/var/lib/pihole-dns-acl/{last_avery_ip,avery_status,last_api_alert}` (12h alert throttle)
- **On IP change:** removes old ufw rule, allows new IP on 53/udp+53/tcp with comment
  "Avery STL summer - remove ~Aug 2026", verifies the rule applied
- **Fail-safe:** API outage or no data → preserves existing rule
- **Discord:** alerts only on apply-failure or Tailscale-API outage, not routine rotations
- **Daily rollup:** DO cron `25 9 * * *` scp’s `avery_status` to
  `<user>@<sundae-address>:/home/<user>/.avery_status` (key `pihole_sync_ed25519`); Sundae's
  `stack-health-check.sh` (09:30 UTC) includes a "Avery DNS" field from this file.

**Cleanup Procedure:** When Avery returns home, remove the Avery ufw rule, both DO crons, and the Avery field in `stack-health-check.sh`.

## Host DNS Settings (SundaeStackarr)

The host resolver and the Docker daemon use the LAN DNS (RS300 at <router-address>, which fans out to the three Pi-holes).

VPN-tun0 DNS (<tailscale-address>) is not configured.

### systemd-resolved

Stub listener disabled (`DNSStubListener=no` in `/etc/systemd/resolved.conf`). Host upstream DNS is `<router-address>` (the RS300), which races the .21/.31/DO resolvers.

### Docker daemon DNS

The Docker daemon configures DNS via `/etc/docker/daemon.json`:
```json
{
  "dns": ["<router-address>"]
}
```

Bridge containers resolve names through the host’s eth0 interface (upstream: switch → RS300). The `expressvpn` and `qbittorrent` containers ignore this setting and use their own internal Lightway DNS resolvers.
