# MossMatriarch Home Server Runbook

**RUNBOOK is the operator's manual** — quick operational steps + operator-unique content with no wiki home. Deep design and reference for each domain lives in the linked wiki pages. See [[OPERATING-BRIEF]] for how to work in this lab, and the operating-doctrine cluster beside it.

## Machines & Network Orientation

### Machines

| Name | Role | IP | OS |
|------|------|----|----|
| **MossMatriarch** (router) | Netgear RS300 | <router-address> | — |
| **Softserve** | Hyper-V host, Plex server | <softserve-address> | Windows 11 Pro |
| **SundaeStackarr** | Hyper-V VM — arr stack + Pi-hole | <sundae-address> (eth0), <pihole-address> (Pi-hole macvlan on eth1) | Ubuntu 26.04 |
| **Mothership** | the owner's gaming PC — 27B heavyweight, all P20 renders + LoRA training | <mothership-address> | Windows |
| **BigHonker** | **PRIMARY 8B brain host** (`qwen3:8b`) + video encode (CorridorKey clips) | <bighonker-address> | Windows |
| **HomeAssistant** | HAOS VM — energy monitoring, automation | <home-assistant-address> | Home Assistant OS |
| **Physical Raspi** | MinuteMan — **peer Pi-hole resolver**, BLE gateway, DDNS updater | <minuteman-address> | Raspberry Pi OS |
| **Raspi VM** | Former Pi-hole host — **retired, OFF** | — | Raspberry Pi OS |

**Per-machine hubs** — what each box is and what runs on it: [[Machine-Softserve]] · [[Machine-Mothership]] · [[Machine-BigHonker]] · [[Machine-SundaeStackarr]] · Machine-HomeAssistant · [[Machine-MinuteMan]]. Full inventory, groupings & front-door: [[Network-Homelab-Machine-Inventory-Virtualization]].

**Tablet compute cluster — "The Fellowship"** (7× decommissioned Lenovo tablets — see
§ *Tablet compute cluster* below). Each node: `ssh <name>` from Softserve (aliases in
`~/.ssh/config`) and `<name>.lan` in DNS (both Pi-hole slots).

**Ten tablets** (plus Sméagol, below). All hold DHCP reservations on MossMatriarch (`<router-address>` → ADVANCED →
Setup → LAN Setup; credential in `Reference\Credentials\network-credentials.md`).

| Name | Serial | Model | SoC | cores | Android | IP | MAC | USB (`10.55.0.x`) |
|------|------|-------|-----|------:|---------|----|----|----|
| **gandalf** | HA1WKD37 | TB-X6C6F "Tab K10" | mt6765 | 8 | 12 (LOS 19.1) | `.71` | `<mac-address>` | `.16` |
| **frodo** | HA1998VW | TB-X606F | mt6765 | 8 | 11 (LOS 18.1) | `.72` | `<mac-address>` | `.11` |
| **sam** | HA15WNRT | TB-X606F | mt6765 | 8 | 11 (LOS 18.1) | `.73` | `<mac-address>` | `.12` |
| **merry** | HA10FXCV | TB-X505F | msm8937 | 4 | 12 (LOS 19.1) | `.74` | `<mac-address>` | `.14` |
| **pippin** | HA10F5DZ | TB-X505F | msm8937 | 4 | 12 (LOS 19.1) | `.75` | `<mac-address>` | `.15` |
| **legolas** | HA197X4L | TB-X606F | mt6765 | 8 | 11 (LOS 18.1) | `.78` | `<mac-address>` | `.13` |
| **aragorn** | HA1TXV62 | TB-X6C6F | mt6765 | 8 | 12 (LOS 19.1) | `.80` | `<mac-address>` | `.17` |
| **faramir** | MJ0EW27F | TB-X606F | mt6765 | 8 | 11 (LOS 18.1) | `.22` | `<mac-address>` | `.21` |
| **boromir** | HA198QW5 | TB-X606F | mt6765 | 8 | 11 (LOS 18.1) | `.93` | `<mac-address>` | `.20` |
| **gimli** | MJ0ETXPE | TB-X606F | mt6765 | 8 | 11 (LOS 18.1) | `.96` | `<mac-address>` | `.19` |

Sméagol (armv7l Samsung SM-T560NU, USB `10.55.0.18`) is the 11th node; it is arch-neutral only and
lives in the Fellowship tables, not this GPU-adjacent roster.

**Every tablet is on two independent paths: the USB wire (main) and WiFi (backup).** All are cabled to
rigby and present one `enx*` interface each. Tolkien routes work over the wire first and falls back to WiFi per
node ([[Fellowship-Order-Taker-Design]] §12, `TABLET_ROUTE_PROBE=1`). A node with no USB link is a fault: Tolkien's USB keeper
restores a link a reboot dropped, and anything it cannot restore (cable, hub port) is reported on the
desk — [[Tablet-Architecture-Topology-Fleet-Status]] § Restore Procedure.

Verify a USB address by asking the node: `getprop ro.serialno` and `ip -4 -o addr show rndis0`.
Map an `enx*` back to a tablet **by serial, never by name** — the RNDIS gadget takes a new random
MAC on every enable, so the host-side interface name changes:

```bash
ssh rigby "for d in /sys/bus/usb/devices/*/; do [ -f \$d/serial ] || continue; printf '%s %s %s\n' \$(basename \$d) \$(cat \$d/serial) \$(ls \$d/*/net/ 2>/dev/null); done"
```

⚠ Read that listing whole. It returns one line per USB device including root hubs, so a `head`/`tail`
window silently drops tablets.

⚠ The MACs above are **device MACs**. Android's per-SSID randomized MACs are stable for one SSID
but change on any other network and whenever "Wi-Fi privacy" is toggled — turn OFF per-network MAC
randomization on a device BEFORE reserving its address.

All machines on Tailscale (`<tailnet>.ts.net`).

**SSH access from Softserve** (all keys in `~/.ssh/config` — use the alias, never improvise):

| Alias | Target | Key |
|-------|--------|-----|
| `ssh sundae` | SundaeStackarr | — |
| `ssh mothership` | Mothership | mothership key |
| `ssh bighonker` | `<user>@<bighonker-address>` | `~/.ssh/bighonker_ed25519` (**NOT** the mothership key) |
| `ssh <fellowship node>` | any of the seven tablets (port 8022, Termux) | `Lab\fellowship\cluster\tabcluster_ed25519` |

BigHonker WoL MAC: `<mac-address>`. It hosts the primary brain and is reliable — if a probe
fails, report UNREACHABLE and check the `sshd` service; never conclude "asleep/off" from a failed
probe.

### SundaeStackarr Network Adapters

| Interface | vSwitch | MAC | IP | Purpose |
|-----------|---------|-----|----|---------|
| eth0 | USB 1Gb (RemoteVM main) | <mac-address> | <sundae-address> (DHCP) | Arr stack, general traffic, VPN |
| eth1 | Motherboard 1Gb | <mac-address> | none (dumb pipe) | Pi-hole macvlan parent — MAC spoofing ON |

eth1 has no IP on the host — it's a bare pipe for the Pi-hole macvlan Docker network. The Pi-hole container owns <pihole-address> directly on this physical NIC, bypassing the host's iptables entirely.

---

### Tablet compute cluster

Seven decommissioned Lenovo tablets → 48 ARM cores of LineageOS compute. Vanilla GSI (**no
GApps**), PHH superuser, Termux for the userspace. Current roster and per-node facts:
[[Tablet-Cluster-INDEX]].

**Reach a node** (Termux sshd listens on **8022**, key-only — aliases carry the key + port):
```bash
ssh gandalf     # any Fellowship node by name; also <name>.lan in DNS
```
Raw form: `ssh -i "E:\SSDShared\Lab\fellowship\cluster\tabcluster_ed25519" -p 8022 <ip>`
**Root authority comes from adb (`uid=2000 shell`), not `su`** — `su` fails on the 19.1 nodes and
does not exist on 18.1. (`adb root` is separately gated behind LineageOS "Rooted debugging" and is
not needed for diagnostics.)

**Reboot or power-off a node** (admin/backend layer, from any LAN/tailnet device with the token) —
Tolkien's control endpoint on rigby, `http://<rigby-address>:8740`, token in
`Reference\Credentials\network-credentials.md` § *Tolkien tablet control*:
```bash
# header: X-Tolkien-Token: <token>
POST /fellowship/reboot  {"target":"gandalf","restore":true}   # reboot + auto-restore USB segment
POST /fellowship/off     {"target":"gandalf"}                  # power off — NO remote power-ON (physical button)
GET  /fellowship/status?target=all                             # adb/rndis0 per node
```
`target` = a node name, `all`, or `class-a|class-b|class-c`. A reboot with `restore:true` drives the
tablet side (adb-root → gadget → address) and the host `br0` self-heals (`fellowship-bridge.service`,
[[Tablet-RNDIS-Setup-Configuration-Procedures]] §4f); full recipe + wrinkles: same doc §4g.

### Tablet WiFi 12-hour blip ("tablets drop off WiFi") — a MANAGED NON-EVENT · manual: `Reference\Fellowship-WiFi-Watchdog.md`

Every node re-associates at exactly 12 h (lease-clocked); the watchdog's **scan tier** recovers
each one in ~40 s (`cmd wifi start-scan` — it disables nothing, so it cannot strand a node).
Not an open problem while the watchdog runs. Operator facts:

* **Status**: ask Tolkien (`tablet_foundry.wifi_watchdog_status()`); node log
  `/data/local/tmp/wifiwd.log`, re-armer log `Lab\fellowship\cluster\rearm.log`.
* **Stand-down**: create `/data/local/tmp/wifiwd.OFF` on the node (nothing ever deletes it).
* ⚠ **"Not an open problem while the watchdog runs" — so CHECK THAT IT RUNS.** 2026-09-26: it was dead on
  7/10 nodes for a month because the re-armer still pointed at Sundae after Tolkien moved to rigby (fixed; roster
  now comes from Tolkien). Verify with the helper on rigby:
  `ssh rigby "docker exec -i tablet-foundry python3 -" < E:/SSDShared/Lab/fellowship/cluster/wifiwd_status.py` (the helper lives in git beside the re-armer, which pipes it in the same way) — every
  node should be `fresh`; `down` ⇒ read `rearm.log` for `SKIP` lines. A node stuck with `wlan0 DOWN` (locally
  generated drop after suspend) comes back in ~10 s with `adb -s <serial> shell input keyevent KEYCODE_WAKEUP`
  then `svc wifi enable` from rigby (USB adb); a bare wifi toggle does not. Detail: `Reference\Fellowship-WiFi-Watchdog.md` § 2026-09-26.
* **Re-arm** is the Softserve scheduled task **"Tablet WiFi Watchdog Re-armer"** — it protects the
  NEXT event, never the current one (it needs the network to reach a node).
* 🛑 The radio-toggle **ACT tier stays refused in code** (a failed re-enable converts a 35-minute
  outage into an indefinite one), and 🛑 **never** `cmd wifi connect-network … wpa2 <psk>` — it
  puts the house PSK in `ps` and logs.
* ⚠ The Android-12 **phantom-process killer** must stay disabled on gandalf/merry/pippin/aragorn
  or the resident detector dies with no error anywhere; check it **over adb only** (ssh renders
  the permission denial as a healthy-looking value). The re-armer logs the check every run.

Mechanism, evidence, hypotheses, and everything that was tried and rejected: the manual above.

### Workgroup / domain (settled — don't re-diagnose this)

Softserve is in the **`MOSSMATRIX` workgroup — NOT domain-joined**. Mothership and BigHonker are also `MOSSMATRIX`.

**History:** Softserve carried a leftover join to a **dead Active Directory domain `ad.<tailnet>.ts.net`** (from an
abandoned Windows-Server experiment — it was the *intended* DC but the edition was changed before it ever became one, so no
DC ever existed). With no DC, the machine↔domain secure channel was broken (`Test-ComputerSecureChannel`=False), which broke
Windows security-descriptor creation and silently **blocked Plex from writing new metadata/artwork bundles** — posters
wouldn't download, and the Plex log showed `boost::filesystem::create_directories ... ALREADY_EXISTS` on
`…\Metadata\Movies\<hex>\*.bundle`. It looked like disk corruption but was NOT. Fixed by leaving the domain:
`Add-Computer -WorkgroupName` fails without a live DC, so the **offline WMI** method was used —
`(Get-WmiObject Win32_ComputerSystem).UnjoinDomainOrWorkgroup($null,$null,0)` then `.JoinDomainOrWorkgroup("MOSSMATRIX")`,
then reboot. Auto-login (local `SOFTSERVE\Administrator`, LSA-secret method) survived the change. A harmless unresolvable
domain SID may linger in some ACLs — ignore it.

### netstat shows no 0.0.0.0 listeners on BigHonker, port reads as free when it is not (Windows build 26300)

**Measured 2026-11-05.** BigHonker runs Windows build **26300** (updates KB5127936 and KB5132402 landed 10-29); Softserve runs
26200. On 26300, `netstat -ano` lists **no wildcard listener at all**: 0 rows with a `0.0.0.0` local address, where Softserve
shows 22, even with sshd and ollama plainly listening. `Get-NetTCPConnection` still lists them, with a **blank State**, so
`-State Listen` matches nothing. A loopback-only listener (`127.0.0.1:port`) does show.

**What that broke, silently:** `p20_foundry._port_free` read netstat, so on BigHonker a server on `0.0.0.0` read as free, and
`stop_comfy`'s lookup found no process to stop while logging "ComfyUI stopped". It was caught by the foundry suite on the first
deploy after the update, because its test opens a real listener.

**The fix:** `_port_free` also asks the OS with a plain bind (refused for a listener on that address; no exclusive flag, so a
closed server's TIME_WAIT does not read as held). Every stop-a-listener lookup in the factory code now matches a row whose
state is `Listen` OR whose remote address is all zeros (`Where-Object { $_.State -eq 'Listen' -or $_.RemoteAddress -in
'0.0.0.0','::' }`). Tests: `test_foundry.PortFreeIsCheapAndTrue`, one of which runs the stop's own lookup live on each box's
Windows. **Writing a new port check on any Windows box: do not read `netstat` alone and do not use `-State Listen`.**

### Drives

| Letter | Label | Size | Purpose |
|--------|-------|------|---------|
| C: | — | 465 GB | Windows OS |
| E: | 1 TB SSD Storage | 931 GB | Downloads, VM configs, app data |
| Z: | Big Boy Pool | 46 TB | HDD array — all media |

### SMB Shares

| Share | Path | Content |
|-------|------|---------|
| `\\<softserve-address>\Media` | Z:\Media | Movies, TV, Music |
| `\\<softserve-address>\SSDShared` | E:\SSDShared | Downloads, staging |
| `\\<softserve-address>\Big Shared` | Z:\ | Full HDD array |
| `\\<softserve-address>\Documentation` | Z:\Documentation | The doc library |
| `\\<softserve-address>\Workspace` | Z:\Workspace | Workspace |
| `\\<softserve-address>\Datasets` | Z:\Datasets | ⏳ **NOT CREATED YET** — long-term training-corpus archive; see PROJECT-BACKLOG "Dataset archive share" |

**SMB reachability from Mothership — do not re-diagnose (2026-07-25):** `\\SOFTSERVE\...` fails with
**system error 53 because the NAME does not resolve from that box**, NOT because SMB is closed. Ports
139/445 are open and permitted by the **"File and Printer Sharing (Restrictive) (SMB-In)"** rule — which
a `Get-NetFirewallRule -DisplayGroup 'File and Printer Sharing'` query misses because of the group name,
making it look closed when it isn't. **Address Softserve shares from Mothership by IP.** What is actually
missing is a stored credential: `net use \\<softserve-address>\...` prompts for a username.

### Hyper-V VMs

| VM | State | VHDX | Notes |
|----|-------|------|-------|
| SundaeStackarr | Running, AutoStart | two disks under `E:\Storage For VM Configs and HDDs\SundaeStackarr\Virtual Hard Disks\`: `SundaeStackarr-boot.vhdx` (SCSI 0:0, 80 GB virtual, EFI + `/boot`) and `SundaeStackarr-data.vhdx` (SCSI 0:3, 200 GB virtual, the whole root LVM `ubuntu-vg`) | Main arr+pihole VM. Layout since 2026-10-10; fstab mounts by UUID / LVM id, never by `sdX` letter |
| HomeAssistantOS | Running, StartIfRunning | `E:\Storage For VM Configs and HDDs\HomeAssistantOS\Virtual Hard Disks\haos_ova-17.2.vhdx` (+ `haos-ssh-config.vhdx` CONFIG disk beside it; automatic checkpoints OFF since 2026-10-10) | Home Assistant |
| Raspi VM | **retired; VM unregistered, disk archived 2026-10-10** | `Z:\Archives\vm-disks\Raspi VM.vhdx` (sha256-verified copy; no longer on C:) | Retired — Pi-hole migrated out |

### Plex

- Runs via Task Scheduler as `SOFTSERVE\Administrator` at boot
- Config: `C:\Users\<user>\AppData\Local\Plex Media Server` (junction → `E:\ProgramData\Plex
  LocalAppData\Plex Media Server`). **Windows Plex keeps its settings in the REGISTRY, not a Preferences.xml** —
  `HKCU\Software\Plex, Inc.\Plex Media Server` (the `PlexOnlineToken` value is the token below).
- Port: 32400
- Token: in the locker — `Reference\Credentials\arr-stack-credentials.md` (this doc is RAG-indexed;
  credentials live in the locker only)
- **CPU priority over Claude Code:** Claude Code runs on Softserve, the Plex host, and heavy agent
  activity can starve `Plex Transcoder.exe` (dropped subtitles, frozen transcoding clients).
  Persistent IFEO priority on Softserve — registry
  `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\<exe>\PerfOptions\CpuPriorityClass`.
  **All five Plex exes = 3 (High)**: `Plex Media Server.exe`, `Plex Transcoder.exe`,
  `Plex Tuner Service.exe`, `Plex Update Service.exe`, `PlexScriptHost.exe`. RealTime (4) is
  deliberately avoided — it preempts system/disk/input threads and worsens stalls. Applies on each
  process launch. Reversible: delete those `PerfOptions` keys.
- **PlexWatchdog scheduled task:** every 1 min as `SOFTSERVE\Administrator` (Interactive, Highest),
  runs `E:\SSDShared\SSD\Open Access\Claude Home\plex_watchdog\plex_watchdog.ps1`. Relaunches Plex if the local API
  (`127.0.0.1:32400/identity`) is down (Plex runs as a console-session tray app and does NOT auto-restart after a
  crash), and lowers `claude` agent procs to BelowNormal. Priority for Plex is NOT set here — that's the IFEO keys
  above. Log: `plex_watchdog\plex_watchdog.log`. Companion to the notify-only `PlexMonitor` task (`plex_monitor\`),
  which alerts to Discord but does not restart anything.
  - **No-flash launcher:** the task action is `wscript.exe "…\plex_watchdog\run_hidden.vbs"`,
    NOT `powershell.exe -WindowStyle Hidden -File …` directly. An **Interactive**-logon scheduled task flashes a
    conhost window on each launch even with `-WindowStyle Hidden` (conhost is created before PowerShell parses the
    flag) — at 1-min cadence that's a PowerShell window popping up every minute on the RDP desktop. `run_hidden.vbs`
    calls `WScript.Shell.Run(… , 0, False)` (window-style 0 = hidden), so conhost is created hidden from the start —
    no flash. Kept Interactive on purpose so a relaunched Plex lands in the desktop session with its tray icon (S4U /
    "run whether logged on or not" would start Plex headless in session 0). If you ever revert the action back to
    `powershell.exe` directly, the every-minute flash returns.
  - **Two Plex servers (found 2026-11-18):** a scheduled task `Plex Media Server` (boot trigger, "run whether logged on
    or not", task file dated 2026-05-12) starts a SECOND, headless Plex in session 0 at every boot; with the console
    tray copy both bind 32400 and share one database and log. The headless one wedged for 33 min on 11-18. **Disabled
    2026-11-18 22:50 on the owner's word** (`schtasks /change /tn "Plex Media Server" /disable`; the session-0 copy ended):
    one Plex, the console copy, the watchdog owns restarts, autologon supplies the console session after a reboot.
    `tasklist /v` must show ONE `Plex Media Server.exe` (Console); a Services-session one means the task came back.
  - **The script's encoding is load-bearing:** `plex_watchdog.ps1` is saved as UTF-8 WITH a byte-order mark and its
    code strings are ASCII. Windows PowerShell 5.1 reads a mark-less file as code page 1252, where an em dash decodes
    to a smart quote that ends a string; from July to 2026-11-18 that swallowed the wedge branch (kill + relaunch +
    state reset), so a wedged Plex was never restarted (33 counted cycles, no log line). After ANY edit run
    `powershell -File plex_watchdog\parse_check.ps1` (exit 1 = the branch is text again).

### SSH to SundaeStackarr

```powershell
# From Softserve (key auth)
ssh -i C:\Users\<user>\.ssh\sundae_ed25519 <user>@<sundae-address>

# Password fallback — value in Reference\Credentials\network-credentials.md (the locker;
# REDACTED here 2026-08-13: this is a RAG-indexed doc, credentials live in the locker only)
<secret>
```

---

## Local DNS (.lan Records)

`*.lan` names resolve via Pi-hole's `hosts/lan.list` (master copy on Sundae, auto-synced to MinuteMan — see Pi-hole section). Most point to `<sundae-address>`, where Caddy on port 80 routes to the correct backend.

| Hostname | Backend |
|----------|---------|
| homepage.lan | homepage:3000 |
| radarr.lan | radarr:7878 |
| sonarr.lan | sonarr:8989 |
| lidarr.lan | lidarr:8686 |
| prowlarr.lan | prowlarr:9696 |
| jackett.lan | jackett:9117 |
| qbit.lan | expressvpn:8080 |
| overseerr.lan / seer.lan | overseerr:5055 |
| doplarr.lan | doplarr-admin:8390 (Caddy **basic-auth**, user `admin`) — Doplarr config UI |
| tautulli.lan | tautulli:8181 |
| pinchflat.lan | pinchflat:8945 |
| cleanuparr.lan | cleanuparr:11011 |
| flaresolverr.lan | flaresolverr:8191 |
| ha.lan / homeassistant.lan | <home-assistant-address>:8123 (HAOS VM) |
| hai.lan | orchestrator:8099 (Home AI) |
| plex.lan | <softserve-address>:32400 (Softserve) |
| pihole.lan | <pihole-address>:80 |
| softserve.lan | <softserve-address> |

Reload DNS after editing lan.list: `docker exec pihole pihole reloaddns`
Reload Caddy after editing Caddyfile: `docker exec caddy caddy reload --config /etc/caddy/Caddyfile`

---

## SMB Mounts on SundaeStackarr

Defined in `/etc/fstab`, mount with `nosharesock,_netdev,nofail`.

| Mount | Share | Purpose |
|-------|-------|---------|
| `/mnt/media` | `\\<softserve-address>\Media` | Z:\Media — all media |
| `/mnt/ssdshared` | `\\<softserve-address>\SSDShared` | E:\SSDShared — downloads |

Credentials at `/etc/smb-credentials` (mode 600, root only).

## Fleet VRAM residency map — CHECK THIS before placing/loading any model

**The rule (the owner):** never load a model anywhere without the arithmetic: weights + KV cache +
vision/compute buffers + desktop overhead vs the card. Verify with `/api/ps` — **`size_vram <
size` = SILENT CPU OFFLOAD** (qwen3-vl on BigHonker ran 1.6GB-on-CPU forever = the 55-65s reads;
nothing errors, it's just 3x slow). One big model at a time per card unless MEASURED to coexist.

**Tested maximum context** = the largest `num_ctx` at which the model sits FULLY on the card
(`size_vram == size` AND no shared-memory spill: since 2026-10-04 `gpu_spill` reads the runner's own Windows GPU counters,
because on Vulkan ollama counts SHARED system memory as resident — spill = ≥512 MB shared with the card within 1 GB of
full; the same test runs in every order's residency guard), measured ON the box by a `fit_probe.py` order (starts at the box's own `triage_local.json`
context, doubles to the first spill, bisects back; 1,024-token resolution; card cleared first). A declared model is
a guarantee it fits up to this context (the owner, 2026-09-28) — an order asking for more does not fit that box.
Raw rows: `SSDShared\tabfoundry-jobs\campaigns\fit-<date>\<order>\fit.json`.
**What a row does and does NOT say.** It is a MEMORY ceiling under the box's settings at measurement time
(Mothership 2026-09-29: ollama 0.34.0, flash attention + q8_0 KV, one parallel slot, card otherwise EMPTY) —
confirmed by the runner's own allocation in serve.log (all layers on GPU at the row, one layer off at the spill;
KV and compute buffers are allocated at load, so a full-length prompt needs no more). It is NOT a tested
working length: nothing has yet run past 65,536 tokens on Mothership (one runaway thinking answer filled exactly
that on 2026-09-29), so quality and speed past 65k are unmeasured; a co-resident model or render eats the headroom; a second parallel slot halves it. The
standing settings are set from this table (the owner, 2026-09-29): Mothership qwen3.8:27b at **122,880** and BigHonker
qwen3.5:9b at **7,168**, each in BOTH `triage_local.json` and the orchestrator (`HEAVY_NUM_CTX` / `BRAIN_NUM_CTX`) —
the two must be equal or ollama reloads the model whenever chat and factory work alternate. The old 65,536 was the
2026-06-29 step up from 32,768 (f16 KV, ollama 0.32.0), never measured as a ceiling. An order's
`requires: triage.ctx.N` is a MINIMUM: any factory declaring a window ≥ N satisfies it (desk and factory, one rule).
**HAI sizes every ollama call by model from this table** — `config.MODEL_NUM_CTX` / `config.ctx_for(host, model,
fallback)` (2026-09-29): critics, think-hard, overnight planning, code/claim/action review, chat titles, VLM reads,
failover. A new model or a re-measured row goes into BOTH this table and `MODEL_NUM_CTX`. Measured before adopting:
qwen3.8 at 16,384 vs 122,880 on identical short work ran 39.1 vs 39.1 tokens/s with 75/75 identical answers — a big
window costs nothing; the cost is the ~50 s reload when a call asks for a different size than the one loaded.

| Box / card | Model | Tested max context | Measured | Notes |
|---|---|---|---|---|
| BigHonker 3070 8GB | qwen3:8b | **17,408** (spills 18,432) | 2026-09-28 | chat / HAI brain model |
| BigHonker | qwen3.5:9b | **7,168** (spills 8,192) | 2026-09-28 | the standing setting since 2026-09-29 (triage row + `BRAIN_NUM_CTX`). ⚠ The cap is NOT the context cache — only 8 of 32 layers keep one, 119 MiB at 7,168 (~17 MiB per 1k tokens). It is the built-in VISION encoder: the runner reserves ~1.29 GB worst case for image input + 0.25 GB of its compute, on top of 4.86 GB of weights (runner log, 2026-09-28). qwen3:8b has no vision part, so its far costlier cache (1.3 GB at 17,408, all 36 layers) still fits more |
| BigHonker | qwen3-vl:8b | **5,120** (spills 6,144) | 2026-09-28 | a single-image check needs ~1,700 tokens; 4,096 ran 24/24 on 09-28 |
| BigHonker | llama-joycaption Q4_K_M | 10,240 | 2026-09-28 | |
| BigHonker | medgemma:4b | 131,072 (its native max) | 2026-09-28 | |
| BigHonker | qwen3.5:35b | **does not fit** (spills at 1,024) | 2026-09-28 | in the store only |
| Softserve 3070 Ti 8GB | qwen3:8b | **17,408** (spills 18,432) | 2026-09-28 21:42, Plex idle | identical to BigHonker; Softserve's `triage_local` row runs it at 17,408 since 2026-09-28 |
| Softserve | qwen3.5:9b | **7,168** (spills 8,192) | 2026-09-28 21:42 | identical to BigHonker |
| Softserve | qwen3-vl:8b | **5,120** (spills 6,144) | 2026-09-28 21:42 | identical to BigHonker |
| Softserve | llama-joycaption Q4_K_M | **10,240** | 2026-09-28 21:42 | identical to BigHonker |
| Softserve | medgemma:4b | **131,072** (native max) | 2026-09-28 21:42 | identical to BigHonker |
| BigHonker + Softserve | nemotron-3-nano:4b | **261,120** (spills 262,144, its native max) | 2026-10-03 21:16 / 21:23 | identical on both boxes; hybrid Mamba (few layers keep a cache): 6.29 GB at 261,120. Official tag (temp 1, top_p 1) |
| BigHonker + Softserve | minicpm-v:4.6-q4km (= `hf.co/openbmb/MiniCPM-V-4.6-gguf:Q4_K_M`) | **262,144** (its native max, no spill) | 2026-10-03 | the configured tag is the same weights with the vendor sampling (`configure_new_models.py`); the probe measured the raw name |
| BigHonker + Softserve | minicpm5:1b-q4km (= `hf.co/openbmb/MiniCPM5-1B-gguf:Q4_K_M`) | **131,072** (its native max, no spill) | 2026-10-03 | same: configured tag, raw name probed |
| Mothership XTX 24GB | qwen3.6:27b | **188,416** (spills 189,440) | 2026-09-29 02:57 | hybrid attention keeps the cache small (KV 6.26 GB at 188,416; model 14.8 + compute 1.0 GB, ~22 of 23.4 GB); the 65,536 thinking run sat well inside |
| Mothership | qwen3.8:27b | **123,904** (spills 124,928) | 2026-09-29 02:57 | lower than 3.6 because MTP (on by default) adds its own draft cache (0.48 GB at 123,904) and a larger model buffer (15.3 GB); the `-mtp-q4_K_M` tag is the same weights: identical |
| Mothership | qwen3.6:35b-a3b | **84,992** (spills 86,016) | 2026-09-29 02:57 | |
| Mothership | medgemma:27b | **60,416** (spills 61,440) | 2026-09-29 02:57 | |
| Mothership | qwen3-coder:30b | **55,296** (spills 56,320) | 2026-09-29 02:57 | |
| Mothership | qwen3:14b | **40,960** (its native max) | 2026-09-29 02:57 | coexists with SDXL ✓ (measured earlier) |
| Mothership | qwen3.8-flash-next:125b-a6b-q4_K_M | **does not fit — partially resident by design** (23.1 of 58.3 GB on the card at 8,192) | 2026-09-29 21:55 | a 120 GB MoE: the overflow experts run memory-mapped from F:, so 1.9 tok/s gen / 0.9 tok/s prompt. Orders must carry `infer_ollama --offload-ok` or the residency guard refuses them forever. Only 12 of 48 layers keep a cache (~280 MiB at 8,192), so context is cheap. No tested max. [[Qwen3.8-Flash-Next-on-Mothership]] |
| Mothership | qwen3-vl:8b (and the abliterated 8b) | **222,208** (spills 223,232) | 2026-09-29 02:57 | |
| Mothership | qwen3-vl-abliterated:32b-instruct | **26,624** (spills 27,648) | 2026-09-29 02:57 | |
| Mothership | llama-joycaption Q8_0 | **104,448** (spills 105,472) | 2026-09-29 02:57 | |
| Mothership | gemma3:27b | **131,072** (its native max, no spill; runner shared memory 152 MB) | 2026-10-04 00:13 (Vulkan, 1 slot, spill-aware re-fit) | the 10-03 "78,848" was a floating-parallelism + leftover-runner artefact: at 78,848 one load put 1.5 GB in shared memory and ran 15 tok/s vs 43 (two cells voided in `hard-bench\menu_void.json`) |
| Mothership | nemotron-3-nano:30b-iq4xs (= `hf.co/bartowski/nvidia_Nemotron-3-Nano-30B-A3B-GGUF:IQ4_XS`) | **1,048,576** (its native max, no spill) | 2026-10-03 21:29; re-fit 2026-10-04 00:13 (1 slot, spill-aware): same, runner shared memory 1,038 MB with the card NOT full (≥1 GB free) | hybrid Mamba MoE: 22.56 GB fully on the card at 1M (19.12 GB at 245,760). Configured tag = same weights + vendor sampling; raw name probed. Speed (Vulkan, `speedcheck` 2026-10-03, f1 items, think off, 11 answers each): **158 tok/s at 1,048,576 vs 173 at 8,192** — unlike qwen3.8, the big window costs ~9% here (ranges 157.5–160.6 vs 172.7–178.0, no overlap) |
| Mothership | ornith-1.5:35b-a3b-q4km (= `hf.co/AtomicChat/Ornith-1.5-35B-A3B-GGUF:Q4_K_M`) | **262,144** (its native max, no spill) | re-fit 2026-10-04 00:13 (1 slot, spill-aware; the 10-03 row was a 2-slot load) | configured tag; runner shared memory 268 MB |

**Softserve and BigHonker hold every model identically once their ollama matches** — the 3070 Ti's usable room
equals the 3070's. Found 2026-09-28 after the owner doubted "the 9B/8B do not fit Softserve at ANY context": Softserve
ran ollama **0.30.7** with **no flash attention and a full f16 KV cache**. Fixed one variable at a time, re-probing
after each: flash attention + `q8_0` (21:25) took qwen3:8b 9,216 → 17,408 and joycaption 5,120 → 10,240; ollama
**0.32.3** (21:42, BigHonker's own install copied over) took qwen3.5:9b and qwen3-vl:8b from spilling at 1,024 to
7,168 and 5,120 — 0.30.7 over-sized those vision-capable models (qwen3.5:9b 6.16 GB at 1,024 vs 5.58 GB at 4,096 on
0.32.3) and kept them partly off the card. Config: RUNBOOK-Ollama-Host-Operations §"Softserve — ollama config".
| Mothership | SDXL/RealVis render | ~7GB | — | coexists with 14B or VLM ✓ |
| Mothership | Flux/Kontext render | full card | — | evicts LLMs first (unload!); TWO Flux graphs at once = commit OOM 1455 |

Embedding models (`nomic-embed-text`) cannot be fit-probed through `/api/generate` (HTTP 400) — not a fit failure.
Softserve's numbers hold with Plex idle, which is the only state a GPU order may START in there (the desk's Plex
gate); a Plex transcode mid-run makes the factory hand the order back.

Interplay traps, measured: harness `unload_lead_llms()` evicts the failover brain
mid-conversation (option-B routing compensates); loading the VLM on BigHonker evicts the chat
8B (or offloads itself); pagefile peak hit 27/32GB during double-Flux.

## 🔌 Reaching rigby and mordecai — basement desktop ssh, "rigby is down", rigby IP address

Run **`ssh rigby`**. To see which address it resolved to, run
`python E:\SSDShared\Lab\bin\sshto.py rigby --which`.

**Do not pin rigby's IP address anywhere.** rigby has two ethernet ports and either may be the one
on the LAN. `Host rigby` in `~/.ssh/config` uses `ProxyCommand python …\Lab\bin\sshto.py rigby`,
which tries every known address and connects to whichever answers. Host keys are filed under the
alias, not the IP. `Lab\bin\run_remote.py` resolves through the same module.

Reserved addresses: `.82` (`rigby-motherboard-nic`) and `.79` (`rigby-pcie-nic`), one per physical
port. mordecai is reserved at `.110`.

* To register a new address, add it to `CANDIDATES` in `sshto.py`; find it with `Lab\bin\lansweep.py`.
* `sshto.py` reporting NOT FOUND means unreachable by this method, not that the box is down.

### Root on rigby / mordecai

`python Lab\bin\run_remote.py <host> <local-script.sh> [wait_s]` reads the credential from the
locker, passes it on sudo's stdin, runs the script detached, and waits for the script's own `DONE`
marker. **The script must print `DONE` as its last line.** A timeout is reported as INCONCLUSIVE.

### Topology

mordecai is not on the switch. The 1 Gb switch is upstairs with the Xbox and the NVR; one line runs
to rigby, and rigby's PCIe port runs point-to-point to mordecai. rigby bridges its two wired ports
(`br-lan` = `eno1` + `enp1s0`, STP on) as a pure L2 pass-through — `ip_forward=0`, no DNS, no DHCP,
no NAT. mordecai gets its address, gateway and DNS from the RS300 and appears in the router's device
list as its own wired host at `.110`.

* `enp1s0` holds no IP. It is a bridge port; do not assign one.
* `br0` (`10.55.0.1/24`, seven `enx*` interfaces) is the **tablet USB segment** — a different bridge.
  A stranded rigby takes the Fellowship's USB network with it, so netplan changes on rigby run
  detached with a self-heal that restores the previous config if no address appears
  (`Lab\bin\rigby_bridge.sh`).
* STP is enabled. Without it, both bridge ports landing on the same switch is a broadcast storm
  across the whole LAN.
* **WoL reaches mordecai from suspend, not from powered-off.** A suspended NIC holds link; a
  powered-off one presents no carrier, and Ethernet cannot transmit a frame without carrier. Check
  `cat /sys/class/net/enp1s0/carrier` first — `0` means no wake is possible and the box needs a
  physical power-on. `etherwake` is installed on rigby and works on the unaddressed bridge port;
  `wakeonlan` does not, as it needs an IP and a route.

### rigby reboot: shares fail to mount, Tolkien starts with an unreadable inbox — `rigby-shares.service`

**Symptom:** after a reboot, orders sit in `E:\SSDShared\tabfoundry-jobs\inbox` and nothing takes them;
`systemctl --failed` on rigby lists `mnt-ssdshared.mount` / `mnt-documentation.mount`; the kernel log
says `CIFS: VFS: cifs_mount failed w/return code = -101` (network unreachable). **Cause:**
`systemd-networkd-wait-online` is skipped on rigby (unmet condition), so `network-online.target` is
reached when a link comes up, before the LAN route exists; the fstab CIFS mounts fire ~40 ms later
and fail, and mount units never retry (measured 2026-09-29 19:33:04).

**Standing fix (installed 2026-09-29):** `rigby-shares.service` runs
`/usr/local/sbin/rigby-mount-shares.sh /mnt/ssdshared /mnt/documentation` (its source and installer:
`Lab\fellowship\rigby`, `install_rigby_host_units.sh`), which mounts both fstab shares and retries every 5 s until
both are up; `docker.service` is ordered after it (drop-in
`/etc/systemd/system/docker.service.d/wait-for-shares.conf`). Docker, and therefore Tolkien, never
starts on empty mount points; if Softserve is down at boot, rigby waits and heals itself when the
shares come back. The fstab mount units may still show `failed` from the first try; that is expected.
Check: `journalctl -b -u rigby-shares` (`both shares mounted (retries: N)`). Backups of anything the
installer replaced: `/root/rigby-shares-bak-<stamp>/`.

**Tolkien reports it:** an inbox it cannot list is logged `INBOX UNREACHABLE (...)` (once, and
`inbox reachable again after Ns` on recovery), carried on its heartbeat, shown on the Fellowship tile,
and DM'd by the watchdog after two polls. Before 2026-09-29 intake read a missing inbox as an empty
one, silently.

**Manual recovery if ever needed:** as root, `mount /mnt/ssdshared; mount /mnt/documentation`, then
`cd /opt/arrstack && docker compose restart tablet-foundry tablet-watchdog` (a container started
before the mount keeps seeing the empty directory).

---

### Containers

| Container | Image | Network | Port(s) | Notes |
|-----------|-------|---------|---------|-------|
| pihole | pihole/pihole:latest | **macvlan** (piholenet on eth1) | 53 (DNS), 80 (web UI) | <pihole-address> — bypasses host iptables/VPN |
| radarr | linuxserver/radarr | arrnet | 7878 | |
| sonarr | linuxserver/sonarr | arrnet | 8989 | |
| lidarr | linuxserver/lidarr | arrnet | 8686 | |
| prowlarr | linuxserver/prowlarr | arrnet | 9696 | |
| jackett | linuxserver/jackett | arrnet | 9117 | |
| expressvpn | misioslav/expressvpn:14.1.0.13058 | arrnet | 8080 (qBit WebUI) — 13338 torrent port publish REMOVED 2026-07-14 (leak fix) | LightwayUDP container VPN; Network Lock kill-switch; `ALLOW_LAN=true`; + `QBITKILL` netns chain |
| qbittorrent | linuxserver/qbittorrent | **service:expressvpn** (shares netns) | — (published via expressvpn) | All torrent traffic tunneled via the `expressvpn` container's tun0 |
| cleanuparr | cleanuparr/cleanuparr | arrnet | 11011 | |
| claudediscordbot | local build | arrnet | — | Discord music bot; Watchtower excluded |
| caddy | caddy:latest | arrnet | 80 | Reverse proxy for all *.lan names |
| seerr | seerr/seerr:latest | arrnet | 5055 | Media requests (service key is `overseerr`, historical) |
| doplarr | ghcr.io/activexray/doplarr_rs | arrnet | — | Discord → Seerr request bot (Rust; slash `/request`, **no web UI**). **Replaced Requestrr 2026-07-05** (Requestrr v2.1.10 abandoned, couldn't hold a Discord session). Reuses the old Requestrr bot token; config `doplarr/config.toml` (secrets via `.env`: `DISCORD_TOKEN`, `SEERR_API_KEY`); unlinked users → Seerr `fallback_user_id=13` |
| pinchflat | ghcr.io/kieraneglin/pinchflat | arrnet | 8945 | YouTube series downloader — see yt-torznab notes |
| yt-torznab | python:3.12-slim | arrnet | 9797 | Torznab adapter + fake qBit for YouTube pipeline |
| tautulli | lscr.io/linuxserver/tautulli | arrnet | 8181 | Plex activity monitor |
| watchtower | containrrr/watchtower | arrnet | — | Auto-updates Docker images nightly at 4am |
| recyclarr | ghcr.io/recyclarr/recyclarr | tools profile | — | TRaSH guide sync — run manually, not persistent |
| coding-runner | local build | arrnet | 8100 | Local coding agent for Home AI (token-authed); `docker-compose.override.yml` |
| searxng | searxng/searxng | arrnet | 8080 (internal only) | Web-search backend for Home AI `web_search`; `docker-compose.override.yml`; reachable as `searxng:8080` on arrnet, no LAN port |

Docker bridge network `arrnet`: `172.18.0.0/16`

### Common Commands

```bash
# Start/stop everything
cd /opt/arrstack && docker compose up -d
cd /opt/arrstack && docker compose stop

# Restart one container
cd /opt/arrstack && docker compose restart radarr

# Status
docker ps --format 'table {{.Names}}\t{{.Status}}'

# Resource usage
docker stats --no-stream

# Logs
docker logs pihole --tail 50
docker logs sonarr --tail 50 -f

# Install packages (host VPN is gone — plain apt resolves DNS normally now)
sudo apt install <package>

# Run Recyclarr TRaSH sync manually
docker compose --profile tools run --rm recyclarr sync

# Recyclarr dry-run (preview only)
docker compose --profile tools run --rm recyclarr sync --preview
```

## Bring-up & where the depth lives (domain wikis)

- **Media stack:** `cd /opt/arrstack && docker compose up -d` — full inventory, qBit/VPN, JukeBot, YouTube: [[Docker-Infrastructure-Inventory-and-Operations]], [[Docker-JukeBot-Discord-Music-Integration]], [[Docker-YouTube-Content-Ingestion-Pipeline]].
- **DNS / Pi-hole (3 resolvers):** [[Network-Local-Network-Services-DHCP-DNS-Architecture]].
- **Remote access / DDNS:** [[Network-External-Connectivity-Remote-Access]].
- **HAI orchestrator (endpoints, gating, dashboard, thinking tile):** [[HAI-Core-Architecture-API-Contracts]], [[HAI-Control-Surface]].
- **Fleet remedies / HAI's own library writes:** [[HAI-Autonomous-Behavior-Self-Engineering]].
- **Friend onboarding:** [[Friend-Architecture-Flow]], [[Friend-Operations-History]].
- **Home Assistant / the utility energy:** HomeAssistant-Energy, HomeAssistant-Weather-Windows.

## Pages in this document

- **RUNBOOK — Elevation & Headless Tasks (ssh-for-privilege)** — see [[RUNBOOK-Elevation-and-Headless-Tasks]]
- **RUNBOOK — Prowlarr, Indexers & Cleanuparr** — see RUNBOOK-Prowlarr-Indexers-and-Cleanuparr
- **RUNBOOK — Quality Profiles & Custom Formats** — see [[RUNBOOK-Quality-Profiles-and-Custom-Formats]]
- **RUNBOOK — Lorex NVR Operation & Tooling** — see [[RUNBOOK-Lorex-NVR-Operation]]
- **RUNBOOK — Tablet Detector Operation (ncnn)** — see [[RUNBOOK-Tablet-Detector-Operation]]
- **RUNBOOK — HawkAdminBot (Discord admin)** — see [[RUNBOOK-HawkAdminBot]]
- **RUNBOOK — Kirk Change Watchdog** — see [[RUNBOOK-Kirk-Change-Watchdog]]
- **RUNBOOK — HAI Coding-Job Escalations & Delegating Supervised Coding Jobs (ClaudeSupervisor machine packets)** — see [[RUNBOOK-HAI-Coding-Escalations]]
- **RUNBOOK — VPN Operation & qBittorrent Leak Guard** — see [[RUNBOOK-VPN-Operation-and-Leak-Guard]]
- **RUNBOOK — Ollama & Local LLM Cluster Host Operations** — see [[RUNBOOK-Ollama-Host-Operations]]
- **RUNBOOK — GPU Host Power Standard & Glances** — see [[RUNBOOK-GPU-Host-Power-and-Glances]]
- **RUNBOOK — Automated Maintenance Schedule** — see [[RUNBOOK-Automated-Maintenance-Schedule]]
- **RUNBOOK — Backup & Recovery** — see [[RUNBOOK-Backup-and-Recovery]]
- **RUNBOOK — Plex Butler Maintenance Window** — see [[RUNBOOK-Plex-Butler-Maintenance]]
- **RUNBOOK — HAI Fixture Test Cycle** — see [[RUNBOOK-HAI-Fixture-Test-Cycle]]
- **RUNBOOK — ComfyUI Image Generation Operation** — see [[RUNBOOK-ComfyUI-Image-Generation]]
- **RUNBOOK — DO Pi-hole & MinuteMan Host Services** — see [[RUNBOOK-DO-Pihole-and-MinuteMan-Services]]
- **RUNBOOK — Known Quirks & Gotchas** — see [[RUNBOOK-Known-Quirks-and-Gotchas]]
- **RUNBOOK — Windows Host Reachability Traps** — see [[RUNBOOK-Windows-Reachability-Traps]]
- **RUNBOOK — GPU Police Operation & Stand-Down** — see [[RUNBOOK-GPU-Police-Operation]]
- **RUNBOOK — GPU Reporters on the fleet boxes: Health Reporter (alive?) + Load Reporter / UNACCOUNTED GPU LOAD accounting (no blind spot)** — see [[RUNBOOK-GPU-Reporters-and-Load-Accounting]]
- **RUNBOOK — GPU box fan control (FanControl): the lab standard (case fans on max(CPU, GPU)), headless BigHonker as a SERVICE, proving the fans are really controlled** — see [[RUNBOOK-GPU-Box-Fan-Control]]
- **RUNBOOK — Foundry Health, Stall-Rescue & Telemetry Daemons** — see [[RUNBOOK-Foundry-and-Rescue-Daemons]]
- **RUNBOOK — Submitting Compute & Training Orders to the Fleet** — see [[RUNBOOK-Compute-and-Training-Order-Submission]]
- **RUNBOOK — Orchestrator Deploy Procedure & Self-Eng Lanes** — see [[RUNBOOK-Orchestrator-Deploy-Procedure]]
- **RUNBOOK — Training Dataset Acquisition & Corpus Storage** — see RUNBOOK-Training-Dataset-Acquisition
- **RUNBOOK — HAI Nursing Tutor (Riley / Morgan)** — see [[RUNBOOK-Nursing-Tutor]]
- **RUNBOOK — Claude Code Transcript Backup (daily 03:30, Softserve → `Z:\Archives\claude-transcripts`, restore a deleted session)** — see [[RUNBOOK-Claude-Transcript-Backup]]
