# Fellowship WiFi watchdog — the 12-hourly re-association blip, auto-recovered in ~40 s (scan tier, re-armer, phantom-process check)

The system re-associated twice daily on lease clocks. The watchdog's scan tier automatically recovers each event in approximately 40 seconds.

### DHCP Renewal Cycle and Node Recovery Behavior

The RS300 router assigns a `leaseDuration` of 86400 seconds (24 hours). Android devices initiate DHCP renewal at the 50% mark, resulting in re-provisioning exactly 12 hours after association. This event generates a `NETWORK_DISCONNECTION_EVENT` with `reason=1`, requiring the node to re-associate with the network.

This cycle affects all nodes on the Fellowship network. Intervals are consistent across devices (12h 00m 00s ±5s). Recovery time varies by node, ranging from <1 second to ~35 minutes without intervention. Implementing the scan-tier optimization reduces recovery time to approximately 40 seconds for all nodes.

RSSI strength does not correlate with recovery time. The fault is node-specific rather than infrastructure-specific; no single node is inherently "flaky." A slow-recovering node exhibits typical behavior for this network-wide condition.

### Configuration Uniformity

All Fellowship nodes share identical configurations regarding:
* AP, band, and channel settings
* RSSI class
* `wifi_sleep_policy=2`
* `doze_always_on`
* `screen_off_timeout`
* `stay_on_while_plugged_in`
* Doze whitelist status
* CPU wake-lock status
* WifiLock status (all hold `type=3` `WIFI_MODE_FULL_HIGH_PERF` via termux)

Note: When inspecting lock status via `dumpsys`, the output `Locks acquired: 1 / released: 0` indicates one lock is **currently held**.

### Observed Correlation: Wakefulness State

A correlation exists between runtime `mWakefulness` state and recovery duration:
* Nodes in `Asleep` state (e.g., gandalf): recovery time <1 second.
* Nodes in `Dozing` state (remaining nodes): unassisted recovery time 20–35 minutes.

This correlation is based on snapshots of the original five nodes. The mechanism remains unproven. The prevailing hypothesis is that Android’s disconnected-scan backoff is deferred while the device is dozing.

### Diagnostic Procedures

To verify DHCP renewal events and timing:
1. Run `adb shell dumpsys wifi | grep NETWORK_DISCONNECTION_EVENT` on any tablet.
2. Observe intervals of 12h 00m 00s ±5s.
3. Note that provisioning success and disconnect events occur in the same second, sharing a clock signal.

To verify lock status:
* Inspect `Locks acquired: 1 / released: 0` to confirm current hold status.

### Design Constraints

Raising the router’s DHCP lease duration is not an acceptable mitigation. It masks the device-side recovery defect by reducing event frequency rather than resolving the underlying latency. The trigger (DHCP renewal) is a routine event expected to cost milliseconds; the fault lies in the node’s recovery implementation.

### 🛑 Watchdog Action Tier is Disabled

The watchdog's automatic radio reset action ("ACT" tier) is disabled. Re-enabling the radio does not resolve connectivity outages because Android association latency persists after radio restart; the node requires approximately 20 minutes to re-associate regardless of radio state.

**Design rationale:** The connectivity fault originates from delayed scanning by Android while the device is dozing and unassociated, not from the radio being powered off. Consequently, the recovery action relies on triggering a scan rather than cycling the radio power.

**Detection status:** Connectivity detection logic is validated. `DISCONNECTED` checks fire correctly, `WOULD ACT` logs appear on schedule, and `RECOVERED` status registers upon reassociation with no false positives.

**Process persistence:** Detached processes survive network link drops. Methods including `setsid`, background execution (`&`), and `nohup` remain active when the ADB session closes on a healthy node. Do not attribute lost processes to network connectivity loss.

## ⚠ PHANTOM PROCESS MONITORING IS A **DETECTOR** DEPENDENCY

The resident detector is an app-child chain. This dependency exists under Termux app uid and includes `com.termux`, `start-fellow.sh`, `tablet_foundry.py --agent`, and `ncnn_detect`. The watchdog itself operates outside this risk group.

On Android 12 nodes, the phantom process killer targets this detector configuration. On Android 11 nodes, where this feature does not exist, the setting reads as `null` instead of `false` or a boolean value.

### Disconnection recovery procedure: forced scan (no toggle)
`cmd wifi start-scan` triggers a disconnected-state scan operation. **It does not disable any service**, therefore it cannot strand a node and requires no dead-man switch — worst case is the outage that was going to happen anyway.
**Criterion for unattended testing:** A test may be run without supervision only if it satisfies: `Can this test strand a node?` No.
The following command **is prohibited**: `cmd wifi connect-network <ssid> wpa2 <passphrase>`
or any form connecting using the passphrase directly on the device. This puts the house PSK in process lists (`ps`) and logs, creating credential leaks even when trusted locally (specifically on nodes where camera detectors run).
The wireless network configuration is already saved on every node.

## Recovery Performance Characteristics
The watchdog log records a collapse in scan-results age for natural disconnect events. For recovery following the sequence `DISCONNECTED → SCAN → RECOVERED`, measured results show an evidence-age reduction from >1000 to ~3 seconds.

This performance is validated on real, natural 12-hour events (not decoys). The observed metric represents a ~30-50x improvement against the baseline of unassisted recovery, which takes between 20 and 35 minutes.

## Operational Mode Constraints
The watchdog operates exclusively in `report` mode. The scan tier is not equivalent to an ACT tier; it does not disable any components and cannot strand a node, allowing it to run regardless of current mode settings.

Attempts to toggle the radio via the ACT tier are refused by code (`wifi_watchdog.sh`). This refusal persists because faults related to radios being off were determined never to require this intervention. Consequently, re-enable attempts convert recoverable outages into indefinite ones requiring physical access rather than fixing them quickly; therefore, an untested candidate must be proven robust before any ACT armoring occurs.

## Service Persistence and Fault Behavior
The command `svc wifi disable` is PERSISTENT across system states including both adb and ssh sessions. It does not recover automatically over a 12-hour deauth period.

Until re-enable candidates are proven to survive radio dropping, the watchdog remains an OBSERVER only. Validated candidates include:
* An AlarmManager-backed `am broadcast`.
* A foreground service.
* A Termux:Boot hook.

## Single-Node Outage Handling
A single-node outage does not cascade; it stays a single-node event. Tolkien drops the offending node from rotation automatically while remaining nodes stay fresh, allowing detection to continue without losing coverage in cameras assigned elsewhere.

### ⚠ "Scans run but it never re-joins" / unreachable for days = Android PERMANENTLY DISABLED the saved network (`NO_INTERNET_PERMANENT`) — the scan tier cannot fix it; recover over the USB wire

**Found 2026-10-10 on legolas:** off WiFi from 2026-10-08 21:06 (the log's last line before the outage:
`REPORT associated but gateway <router-address> unreachable`) for two days, while the watchdog issued **1,053 scans**, each
proven real (`results-age` drop) and the AP visible in every scan (MossMatrix, −63 dBm). Cause: Android's internet
validation failed once during a gateway hiccup and struck the saved network off auto-join for good —
`dumpsys wifi`: `NetworkSelectionStatus NETWORK_SELECTION_PERMANENTLY_DISABLED`,
`mNetworkSelectionDisableReason NETWORK_SELECTION_DISABLED_NO_INTERNET_PERMANENT`. A scan cannot lift that; only an
explicit user connect does.

**The tell in `wifiwd.log`:** `DISCONNECTED` + `SCAN #N` with a real age drop, over and over, with no `RECOVERED`,
for far longer than the 12-hour blip's ~40 s.

**Recovery (no passphrase on any command line, nothing disabled):**
1. The node is still reachable on its USB wire even with WiFi down: ssh `10.55.0.<octet>:8022`, and **adb at
   `10.55.0.<octet>:5555`** (adbd listens on every interface; rigby's host adb key is authorized). From rigby:
   `adb connect 10.55.0.13:5555`.
2. Confirm: `cmd wifi status` (enabled, not connected) and `dumpsys wifi | grep -E "^ NetworkSelectionStatus|^ mNetworkSelectionDisableReason"`.
   ⚠ A looser grep matches months-old history lines (`setNetworkSelectionStatus: … ENABLED`) and reads as healthy.
3. `cmd wifi clear-user-disabled-networks` does **NOT** lift it (it clears only user-disconnect blocks) — tried, no change.
4. What works: an explicit connect to the SAVED network through Settings. `input keyevent KEYCODE_WAKEUP`, swipe the
   lock (`cmd lock_settings verify` succeeds = no credential), `am start -a android.settings.WIFI_SETTINGS`,
   `uiautomator dump` to find the `MossMatrix` / `Saved` row, `input tap` its centre. legolas joined within 20 s
   (`NETWORK_SELECTION_ENABLED`, `tablet_alive` True, Tolkien health `ok`), then `KEYCODE_HOME`.

**Why it benched the node:** Tolkien's liveness gate asked WiFi only (`scheduler.tablet_alive` → `<host>.lan:8022`)
while its work routes wire-first (`node_ip`), so a node fine on its wire read "unreachable". Fixed 2026-10-10 in
`tablet_foundry.node_health` (alive = the wire answers, else the WiFi check) and `_adb_targets`
(`10.55.0.<octet>:5555` first, `<host>.lan:5555` the backup — [[Fellowship-Order-Taker-Design]] §12).

**Configuration drift found on the same node:** legolas's lock screen is ENABLED (swipe, no credential;
`cmd lock_settings get-disabled` → false) while gandalf's is DISABLED (→ true). Not changed — a settings change is
the owner's call. Not yet audited across the other nodes.

### ⚠ 2026-09-26 — THE RE-ARMER WAS DEAD FOR A MONTH (fixed same day; how to see it, how it failed)

**Found:** `wifi_watchdog_status()` reported the on-device watchdog **`down` on 7 of 10 aarch64 nodes** (gandalf +
aragorn since 2026-08-29; merry, pippin, boromir, faramir, gimli since 2026-09-02) — heartbeat ages 24–28 days —
while the Softserve **"Tablet WiFi Watchdog Re-armer"** task ran every 15 min with exit 0. `rearm.log` had said
`SKIP could not read Tolkien status (no JSON in Tolkien's reply)` on **every run since 2026-08-28**.

**Two migration leftovers (doctrine class 19, consumers of a moved path):**
1. The script asked Tolkien via `ssh sundae "sudo docker exec tablet-foundry …"`. Tolkien's `tablet-foundry`
   container moved to **rigby** in late August; the re-armer was never repointed, so it could not read status
   and — correctly, by its own design — never acted. A guard that fails closed and reports only to a log nobody
   reads expires silently.
2. Its node roster was a hardcoded 7 (`$Nodes`), so gimli/boromir/faramir (joined 2026-09-04) were never in
   scope at all.

**Fix (live):** `rearm_wifi_watchdogs.ps1` now runs `ssh rigby "docker exec tablet-foundry python3 <helper>"`
(rigby login is in the docker group, no sudo), and the helper `tabfoundry-jobs/tools/wifiwd_status.py` ships a
`_nodes` map (host → LAN ip) straight from Tolkien's `TABLET_NET`; the script builds its roster from that and
keeps the old list only as a logged fallback. **Proven with a live decoy:** two manual runs → 6 nodes
`RE-ARMED`, and on the tablets `wifi_watchdog.sh` was running with heartbeat age ≈35 s on each.

**What the drops looked like (three WiFi-only class-B nodes in 17 min, all with the watchdog dead):**
`wpa_supplicant: CTRL-EVENT-DISCONNECTED bssid=<RS300> reason=1 locally_generated=1` immediately after
`SETSUSPENDMODE 1`, signal −45 dBm, and **`wlan0` DOWN** (interface down, not merely disassociated). Nothing
reassociated on its own for 5+ min. Sleep settings were identical on all ten nodes (sleep policy never, wake-lock
held, doze-whitelisted), so this is not a per-node config gap. **Recovery that worked, 3/3, ~10 s:** over USB adb
`input keyevent KEYCODE_WAKEUP` then `svc wifi enable`. A plain `svc wifi disable/enable` toggle did NOT bring
the interface back; the WAKEUP was the load-bearing step. ⚠ **Unmeasured:** whether the watchdog's scan tier
(`cmd wifi start-scan`) recovers this interface-DOWN variant at all — the doc's ~40 s recovery figure was
measured on the 12-hourly re-association blip. Watch the next natural drop with the watchdog alive before
claiming it does; if it does not, the scan tier needs the WAKEUP step, which is a change to `wifi_watchdog.sh`.

### WHO RE-ARMS THE WATCHDOG?

**Scheduled Task Location:** `Softserve`.
The scheduled task **"Tablet WiFi Watchdog Re-armer"** invokes `/data/local/tmp/wifi_watchdog.sh` on the target tablet to restart stopped watchdogs.

**Observation Authority:** `Tolkien`
elective status via `tablet_foundry.wifi_watchdog_status()` monitoring heartbeat file age over existing SSH paths. Termux reads logs directly; no new dependencies required for this tier.

| Component | Location / Execution Context | Function |
|---|---|---|
| **recover** | ON THE TABLET — `/data/local/tmp/wifi_watchdog.sh` (source: `Lab\\fellowship\\cluster\\`) | Issues `cmd wifi start-scan`. Refuses the radio-cycle ACT tier.

*   When unassociated, it initiates a scan. The radio-cycle ACT tier is refused by design because nothing external can rescue a node mid-outage if adb drops with the tablet.
| **observe** | TOLKIEN — `tablet_foundry.wifi_watchdog_status()` | Monitors heartbeat file age via existing SSH path; Termux reads logs directly, requiring no new dependencies for this tier.

The re-arm function protects subsequent events only. Network reachability is a prerequisite; it fires once the node is already back online.

**Operational Constraints & Configuration:**
The task utilizes S4U as `SOFTSERVE\\Administrator`. This account's profile holds both the authorized adbkey and the ssh key used to restart watchdogs that have stopped. Standard user shells fail with "Access is denied"; use `[System.Security.Principal.WindowsIdentity]::GetCurrent().Name` to resolve principal names, avoiding domain mapping errors where `$env:USERDOMAIN` returns `MOSSMATRIX`. The task runs at startup with indefinite duration (`IgnoreNew`, RestartCount 3).

Stand-down requires creating `/data/local/tmp/wifiwd.OFF` on the target node. This file is checked every pass; nothing ever deletes it to ensure stability.
Logs reside at node path `/data/local/tmp/wifiwd.log` and `Softserve\Lab\\fellowship\\cluster\\rearm.log`. The operational mode is `report`, which remains constant (the ACT tier is refused).

**Provisioning Requirements:**
The script must be provisioned; an absent check fails if the file does not exist before starting. Starting a nonexistent file via adb returns exit 0 and would incorrectly log "RE-ARMED" indefinitely.
Install procedure:
```bash
cd "E:/SSDShared/Lab/fellowship/cluster" && tr -d '\r' < wifi_watchdog.sh > /tmp/wd_lf.sh && ../platform-tools/adb.exe -s <ip>:5555 shell "cat > /data/local/tmp/wifi_watchdog.sh" < /tmp/wd_lf.sh
```
*   `adb push` fails with `remote fchown failed`; use pipe via `cat`.
*   Strip CR from input; Android's `sh` breaks on CRLF, and the Write tool may produce them.
*   Under Git Bash, prefix commands with `MSYS_NO_PATHCONV=1`. The path `/data/local/tmp/...` is rewritten to `C:/Program Files/Git/data/...`

**Superseded Alternatives:**
(a) **Static IP per node**: Rejected because it would remove the DHCP half and discriminate DHCP-trigger from AP-trigger. Additionally, `cmd wifi` cannot set static IPs without root (denied), requiring on-screen Settings UI.
(b) **Raising router's DHCP lease**: Rejected as fixing a 35-minute recovery via routine events rarer house-wide rather than addressing the immediate defect.

## Current setup (2026-11-19): the status helper under git, the gateway read on every check

- **The status helper is `Lab\fellowship\cluster\wifiwd_status.py`** (under git). The re-armer pipes it into Tolkien's
  container (`ssh <tablet head> "docker exec -i tablet-foundry python3 -" < wifiwd_status.py`); the old copy on the jobs
  share is retired to `tabfoundry-jobs\tools\retired-20261119\`. Its `_nodes` is every tablet in Tolkien's `TABLET_NET`;
  the re-armer leaves out the ones on the USB segment (`FELLOWSHIP_USB_SEGMENT`), which have no network adb door, and its
  fallback roster is the Fellowship roster file (`FELLOWSHIP_ROSTER`), filtered the same way.
- **The watchdog finds the gateway itself.** `GW` is no longer written into `wifi_watchdog.sh`: unset, each check reads
  the route to a documentation address (`ip route get 192.0.2.1`, no packet sent), so a watchdog started before WiFi is
  up still finds it, and a gateway it cannot find reports `gateway=unknown`, never unreachable. Android keeps its default
  route in a per-network table, so `ip route` alone shows none. Redeployed to all ten network tablets the same day with
  `cluster\provision_watchdog.sh <names>` (which now requires the names): every hash matched, every first heartbeat said
  `gateway=yes`.
- **The keepalive's gateway comes from `~/.keepalive.env`** (`GW=<address>`), which `flash\provision_node.sh` writes when
  it installs `start-keepalive.sh`: Termux cannot read the routing table (`ip` is not on its path and `/proc/net/route` is
  denied), and adb can. With no gateway known the keepalive logs why and exits. Live keepalive copies predate this and keep
  their own gateway until the node is reprovisioned.
