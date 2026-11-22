# Fellowship Cluster - Operations, Connectivity, and Reliability



## Head Node Identity and Key Management

Softserve (`E:\SSDShared\Lab\fellowship\`) is the tablet cluster head node. It generates A and B, splits A into row-blocks, ships a block and all of B to each worker, gathers C-blocks, and verifies results.

### Identity Keys and Access
The cluster uses a single Ed25519 key pair located at `cluster\tabcluster_ed25519`. This key is wired into Softserve's `~/.ssh/config` for all seven tablet aliases (`Port 8022`, `StrictHostKeyChecking no`). Operators must use existing SSH aliases and the established `tabcluster` key to reach tablets. Do not mint new credentials or establish parallel head nodes.

### Distributed Harness and Tools
| Path | Function |
|---|---|
| `cluster\master.py` / `worker.py` | Distributed work harness (head/worker split) |
| `platform-tools\adb.exe` (37.0.1) | Primary ADB binary. `master.py` references this exact path. |
| `platform-tools-34\adb.exe` (34.0.4) | Secondary ADB build. Use if the primary crashes; see [[HANDOFF-tablet-lineage-flash]]. |

### Benchmark and Driver Suites
| Path | Content |
|---|---|
| `cluster\bench.py bench_mp.py burn.py duty.py` | Benchmark suite providing GFLOP/s figures |
| `cluster\RESULTS-*.md` | HPL, duty, and stream-iperf results |
| `cluster\fleet_duty.sh overnight.sh warm.sh` | Fleet-wide drivers |

**Design Fact:** All operational tools required by the cluster harness must reside in permanent library paths. Tools existing only in session scratchpads are considered defects and will not be found by subsequent sessions.

## 3. HOW TO REACH THEM

### SSH Access and Configuration

Nodes are addressed by hostname alias (`gandalf`, `frodo`, `sam`, `merry`, `pippin`) defined in the Softserve `~/.ssh/config`.

**Connection Protocol:**
*   **Protocol:** SSH
*   **Port:** 8022 (Termux sshd)
*   **Authentication:** Key-only
*   **Key Location:** `E:\SSDShared\Lab\fellowship\cluster\tabcluster_ed25519`

```bash
ssh <hostname>  # e.g., ssh gandalf
```

### Network Addressing Doctrine

DNS names resolve to `<name>.lan` via Pi-hole.

**IP Assignment Rule:**
Node IPs are assigned via DHCP and are **not reserved by default**. An unreserved IP address causes DNS resolution drift. Before relying on hostname stability, add MAC-based reservations in the DHCP server as documented in `see [[RUNBOOK]] §DHCP Reservations` (or relevant heading).

### Software Environments

Each node runs two distinct environments:

1.  **Native Termux Userland:**
    *   Python 3.14
    *   NumPy 2.4.4
    *   Open MPI 4.1.5
    *   Clang compiler

2.  **Debian 13 (Proot):**
    *   Access via: `proot-distro login debian`
    *   Includes: OpenBLAS library

## Liveness Signal Architecture

The liveness signal for Fellowship nodes uses a dual-mechanism design: local heartbeat files combined with remote age polling. This addresses the failure modes of push-only (cannot report own death) and probe-only (confirms SSH reachability, not worker health).

### Heartbeat Generation
Each node generates its own observations via `wifi_watchdog.sh`, writing to `/data/local/tmp/wifiwd.log`.
The heartbeat record contains:
*   **Cadence:** Self-declared by the reporter (e.g., `cadence=1800`). The reader derives thresholds from this value; the Python poller does not duplicate cadence constants.
*   **State fields:** `state`, `gateway`, `fails`, `mode`.

### Liveness Calculation
Liveness is determined by the **age of the heartbeat file** (`mtime`), NOT process existence. A live PID can mask a dead worker.

*   **Calculation Location:** Age is computed **on the device** (`now - mtime`) in one round trip. Remote calculation fails due to timezone drift (tablets use `America/Chicago`; containers use `UTC`).
*   **Polling Function:** `tablet_foundry.wifi_watchdog_status()` reads the file age.

### State Definitions
The liveness system reports five distinct states. Binary up/down is insufficient:
1.  **Fresh:** Recent heartbeat within threshold.
2.  **Silent:** No heartbeat, but node reachable.
3.  **Down:** Heartbeat stale; worker assumed dead.
4.  **Absent:** Watchdog not installed on node.
5.  **Denied:** File exists but current UID cannot read it (e.g., Termux uid restrictions).

**Unreachable** nodes return `UNKNOWN` and are never actionable in scheduling decisions.

## Node Connectivity Requirements

### DHCP Stability
Scheduler liveness probes use hostnames (`<name>.lan:8022`). Name resolution is load-bearing.
*   **Risk:** DHCP lease changes break name resolution until Pi-hole updates.
*   **Mitigation:** Fail-closed scheduling prevents offering unreachable nodes, but does not fix the root cause.
*   **Requirement:** Static DHCP reservations are required for all tablet MACs. (See `RUNBOOK` for MAC addresses).

### ADB Configuration
All Fellowship nodes must have persistent ADB over TCP enabled at boot.
*   **Configuration:** `persist.adb.tcp.port=5555` must be set on every node.
*   **Boot Persistence:** Standard Android wireless debugging (`adb tcpip`) resets on boot/WiFi change and uses random ports. Persistent configuration via system property is required.
*   **Privilege Requirement:** Setting the property requires root access. Run `adb root` before executing the shell command, especially on nodes where adbd may not default to root (uid 2000).
*   **Verification:** A node without this property is considered defective and must be fixed.

## 9. Freeze mitigation mechanisms

Every Fellowship node must defeat two distinct power-management mechanisms to maintain full computational throughput. Failure of either mechanism results in severe performance degradation while maintaining a healthy status appearance.

### Mechanism 1: Doze / App-Standby Freezing

**Symptom:** Process suspended for minutes; CPU idle; short bursts appear normal.
**Fix:** `cmd deviceidle whitelist +com.termux`
**Verification:** `cmd deviceidle whitelist | grep termux`
*   **Transport:** SSH is sufficient for reading/verification.
*   **Constraint:** Writing to the whitelist requires ADB privileges (Termux receives `SecurityException: DEVICE_POWER` over SSH).

### Mechanism 2: CPU Wake-Lock Holding

**Symptom:** Sustained CPU throttling (8-12x slowdown); process runs but crawls.
**Fix:** `termux-wake-lock`
**Verification:** `dumpsys power | grep -c termux` ≥ 1
*   **Transport:** ADB is required. SSH returns zero bytes for `dumpsys power` regardless of node health, rendering SSH verification unreliable and causing false-negative health checks.
*   **Constraint:** The command must run in the Termux APP context.

### Operational Doctrine: Node Revival and Boot

*   **Wake-Lock Dependency:** A node revived outside the Termux app context holds no wake-lock and is degraded. Prefer a reboot over an `adb run-as` revival.
*   **Boot Configuration:** Termux:Boot must be installed fleet-wide to restart sshd + wake-lock inside the app context on boot.
    *   **Package Source:** Use the GitHub build (v0.8.1+). The F-Droid APK is refused due to `INSTALL_FAILED_SHARED_USER_INCOMPATIBLE` (signing key mismatch with the GitHub build of Termux).

> Note: If ADB is unavailable, wake-lock status is UNKNOWN. Do not render UNKNOWN as "no lock".

## DRAM State Degradation Detection and Remediation

### Diagnostic Fact
A node may operate at full CPU frequency while exhibiting degraded memory performance (up to 3.4x slower). This condition is invisible to standard CPU benchmarks and sequential I/O checks.

### Conformance Instrument
The definitive test for DRAM health on Fellowship nodes is random-access latency over a large working set.
*   **Probe:** `numpy` gather operation over a buffer ≥32 MB.
*   **Healthy Baseline:** ~2.0–2.9 M gathers/s on Lenovo TB-X606F hardware (mt8768, 1.84 GB RAM).
*   **Degraded Indicator:** 0.5–0.8 M gathers/s.
*   **False Negatives:** Sequential reads (e.g., `dd`, disk I/O) and small-buffer operations (<32 MB) remain near parity even when degraded, as prefetching and caching mask the fault. Core affinity shifts do not resolve this issue.

**Design Rule:** A node is considered healthy only if it passes the ≥32 MB random-access gather test. Sequential metrics are insufficient evidence of health.

### Remediation Procedure
1.  Reboot the affected node.
2.  Restore services via Termux:Boot (restores `sshd` + wake-lock in ~55 s).

**Note on Differential Diagnosis:**
Orphaned processes from incomplete remote kills can produce similar symptoms and are also cured by reboot. If load average is high, check for orphaned processes first (see `Archive\handoff-tablet-cluster-20260821\HANDOFF-tablet-cluster.md` §5). If no orphans are visible but DRAM gather scores remain degraded, treat as memory state failure requiring reboot.

### Performance Validation Protocol
Do not classify a node as having "slow hardware" based on isolated timing comparisons. To confirm hardware faults:
1.  Perform a clean reboot.
2.  Run simultaneous twin comparisons against the same stratified sample under identical conditions.
3.  Only if discrepancies persist after these steps, consider hardware defects.

## The Fellowship roster file (FELLOWSHIP_ROSTER): one table for every tablet tool

`E:\Documentation\State\fellowship-roster.json`, named by the lab setting `FELLOWSHIP_ROSTER` (`Lab\etc\lab.env`). One
record per tablet: `address`, `serial` (the adb serial), and where a tool uses them `usb_octet` (its address on the USB
segment), `class` (A, B or C) and `battery_switch` (the charge switch file). `Lab\fellowship\roster.py` loads it and refuses
when it is unset; the battery governor, `fellowship_ctl.py` (its class groups are derived from `class`), `usbnet_up.py`,
the re-armer's fallback and the watchdog provisioner all read it, and `lab_settings.tablet_hosts()` (the probe and the
capability tool) is the roster minus the tablets whose only address is on the USB segment (`FELLOWSHIP_USB_SEGMENT`,
`10.55.0`). It replaced four copied tables in the tools and the older `TABLET_HOSTS` setting (2026-11-19; every derived
table proven identical to the one it replaced). **Tolkien's `TABLET_NET` (its env file on the tablet head) is the same
table for Tolkien: add a tablet to both;** `provision_node.sh` ends by printing both lines.

## The tablet head's USB reach probe (USB_REACH): the watchdog's check over the wire

Before Tolkien's watchdog calls the fleet unreachable over WiFi, it asks the tablet head which tablets answer on the USB
segment. The head runs `usb_reach_probe.py` as the forced command of the watchdog's key and prints one line,
`USB_REACH <octet> ...`; an empty list means none answer, and when it cannot tell what the segment is it prints
`USB_PROBE_ERROR`, which the watchdog reads as inconclusive. Since 2026-11-19 it needs no roster: the segment comes from the
head's own `br0` address and every address on it is tried at once on the tablets' sshd port. The earlier copy tried a
fixed range of seven and could not see the four tablets added after it. Source: `Lab\fellowship\usb_reach_probe.py`
(tests beside it); installed on the head with
`python Lab\bin\run_remote.py rigby Lab\fellowship\install_usb_reach_probe.sh 120 --args <src>:<dest>` (from Git Bash,
set `MSYS_NO_PATHCONV=1` or the paths are rewritten), at the path the forced command names
(`/home/owner/bin/usb_reach_probe.py`). Proven through the forced command from Tolkien's container: six tablets on the wire,
matching `usbnet_up.py`.
