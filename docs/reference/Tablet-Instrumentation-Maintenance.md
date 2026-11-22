# Tablet USB Networking - Instrumentation & Maintenance



## 5. Instruments

The durable pattern for lab instrumentation is `E:\SSDShared\Lab\bin\`. New instruments reside here, not in session scratchpads or assistant workspaces.

### Active Tools

| Tool | Location | Function |
|---|---|---|
| `usbnet_up.py` | `E:\SSDShared\Lab\fellowship\` | Discovers tablets over WiFi ADB, identifies by SERIAL, applies §4c/§4b in correct execution order, and verifies the route leaves via `rndis0`. Defaults to measure-only; use `--apply` to configure. Reports `N of M` status. Exits non-zero on partial segment failure. |
| `fellowship_bridge.sh` | `E:\SSDShared\Lab\fellowship/` (rigby); `~/fellowship_bridge.sh` (local) | The rigby-side bridge operator (requires root). Enslaves tablets by glob, asserts count, flushes ARP, and probes every roster octet with `ping -c3`. Takes the octet list as the first argument (`sudo bash ~/fellowship_bridge.sh 11,12,13,14,15,16,17`). `usbnet_up.py` passes its own roster to ensure consistency between tools. |
| `rebridge.sh` | `~` on rigby — **superseded** | **Superseded by `fellowship_bridge.sh`; do not route operators here.** The 2026-08-17 bridge tool, still present on rigby, but defaults to `WANT=4` and so false-positives a short bridge on the 7-node fleet. Use `fellowship_bridge.sh` (or `usbnet_up.py`, which drives it with the full roster). |
| `probe_link.py` | `~` on rigby | Latency measurement. |
| `bwtest.py` | `~` on rigby | Throughput measurement. Uses threaded sink (one thread per connection). |
| `rtt.py` | `~` on rigby | Round-trip delay over a HELD connection. Reports median and p95 after warm-up. |
| `battery_governor.py` | `E:\SSDShared\Lab\fellowship\` | Holds each tablet's charge in a longevity band by toggling its per-family charge knob over WiFi adb. Deployed as the recurring Softserve task **"Fellowship Battery Governor"** (10-min poll). `--dry-run` inspects, `--skip <names>` holds nodes out, `--band LOW,HIGH` overrides. See "Battery charge governance" below. |

### Battery charge governance

The tablets are always USB-powered, so they sit at 100% and calendar-age. `battery_governor.py` holds each node in a longevity band (default **50-70%**: near-ideal storage charge, shallow cycles, off the 100% top) by toggling its charge knob over WiFi adb, polled every 10 min by the Softserve task. **Fail-safe by construction:** the knob resets to charging-ENABLED on reboot and the hardware default is always "charge", so any failure (unreachable node, crash, missed poll) leaves a tablet charging normally — none can be stranded empty.

**Charge knob per hardware family** (proven 2026-08-28 by `status` flip Full<->Discharging; value uniform: `0` = stop charging, `1` = charge). adbd runs as root on these `-userdebug` GSIs, so writes need no `su`:

| family | tablets | SoC / Android | knob (`echo 0/1 >`) |
|---|---|---|---|
| A | frodo, sam, legolas | mt6765 / 11 | `/sys/class/power_supply/battery/charging_enable` |
| B | gandalf, aragorn | mt6765 / 12 | `/proc/mtk_battery_cmd/en_power_path` |
| C | merry, pippin | msm8937 / 12 | `/sys/class/power_supply/battery/charging_enabled` |

* Family B's dedicated `StopCharging_Test`/`StartCharging_Test` nodes are **SELinux-blocked even as root**; `en_power_path` is the working path, and `battery/disable` does **not** stop charging.
* Telemetry: `dumpsys battery` or `/sys/class/power_supply/battery/capacity`.
* **Unverified caveat:** family B's `en_power_path` disables the whole charger power-path — confirm USB *data* survives it (families A/C use a charge-specific knob, so their USB belt stays up while discharging).
* The governor runs on **Softserve** because adb is authorized there; moving it under Tolkien on rigby needs rigby's adb key authorized on the tablets first.

### Instrument Design Doctrine

* **Positive Controls**: Latency tools (`probe_link.py`, `rndis_latency_all.py`) must echo a positive control before timing any path. Timing a dead path reports it as a slow link, invalidating the metric.
* **Verification Before Assertion**: Network reconfiguration tools must verify that routes actually exit via `rndis0` before declaring success. A partial segment failure exits non-zero.
* **Peer vs. Host Metrics**: Throughput numbers reported in this document are tablet-to-rigby (host). Distributed work is peer-to-peer traffic, which crosses the shared bus twice and includes a bridge hop. Host figures cannot be inferred for peer work; quoting host figures for distributed tasks overstates fleet capacity. Peer throughput must be measured directly.
* **Layered Failure Diagnosis**: When a layered protocol fails, dump every layer on both ends before reasoning about specific components. Theoretical iteration without full-dump data produces no verdict.

### Instrument conformance and positive controls

An instrument that returns a null or default value for both valid data and error conditions is untrustworthy without verification. Before accepting any negative result from an instrument, run a **positive control**: force the instrument to report a case known to be true.

#### Instrument failures on this segment

1.  **`/proc/stat` reports fiction.** System-wide CPU usage via `/proc/stat` shows incorrect values (e.g., 0.4% system busy when pinned at 99%). Use per-process `/proc/<pid>/stat` instead.
2.  **`top` truncation masquerades as empty results.** `top -H -m 14` prints identical output for a truncated list and an empty list. Do not infer absence of processes from this flag combination; use `ps -A`.
3.  **`ping` measures link wake-up, not latency.** Spaced pings read high latency (2.45 ms merry, 0.19 sam) while held connections show lower latency (0.265–0.340 ms median). Do not use `ping` for latency claims on this segment. Use `rtt.py`.
4.  **Verify controls against the correct target.** Ensure control targets address the specific device under test, not a similar host that answers on all interfaces. Latency thresholds (e.g., <1.5 ms) must not be used as pass/fail gates for connectivity; connectivity is proven by successful routing (`br0` on rigby). Latency is a result, not a gate.
5.  **Keep comparison variables isolated.** Do not mix different hardware or protocol types within the same comparative curve. Ensure the x-axis represents only one variable (e.g., device count) and that all rows use the same protocol (NCM vs. RNDIS).
6.  **Validate script exit codes and stderr.** Scripts designed for diagnosis must fail loudly. `rebridge.sh` exiting 0 despite errors hides diagnostics. Ensure scripts check return codes and do not redirect `stderr` to `/dev/null` during diagnostic phases.
7.  **Verify service state via OS, not logs.** A new instance failing with "Address already in use" in logs may indicate a healthy prior listener, not a broken start. Use `ss -lnt` to verify exactly one listener exists.
8.  **Verify interface names dynamically.** NCM presents as `usb0`, not `ncm0` (configfs function is `ncm.0`). Discover the interface name, then verify the address landed on it (§4e). Do not hardcode interface names in scripts.
9.  **Check stderr and exit codes for command failures.** An empty stdout from a command like `ip -4 -o addr show` may indicate "command not found" (rc=127) if stderr is discarded. Always inspect stderr and the exit code. If an instrument reports a device is absent, verify connectivity via SSH to confirm presence. Use adb for enumeration and ssh for termination, per lab doctrine.

#### Cluster job execution requirements

The cluster master for jobs over the USB segment must run on **rigby**. Rigby is the only host on that segment; running the master elsewhere forces transfer legs through WiFi, confounding the comparison.

*   `~/cluster/` on rigby contains `worker.py`, `usb_matmul.py`, and a copy of the cluster key `tabcluster_ed25519` (mode 600).
*   This is a deliberate second copy of the Fellowship key. The canonical copy resides at `E:\SSDShared\Lab\fellowship\cluster\`.

#### Measurement constraints

*   **Do not measure over `adb forward`.** ADB's userspace relay adds ~3.25 ms latency regardless of hardware, understating real benefits by ~6x. ADB also compresses sync transfers; `/dev/zero` payloads measure ~2695 MB/s on a 480 Mbit/s bus due to compression. Benchmark payloads must be incompressible.
*   **Do not use `ping` for the WiFi arm.** Pinging an idle tablet measures radio wake-up from power-save (average 212 ms, peak 558 ms) rather than active latency (8.4 ms). This is the same idle artifact documented in [[Fellowship-Crunch-Integration]] §1.

---

## 6a. Bridge restoration after host reboot

**The host bridge self-heals (2026-08-29).** `fellowship-bridge.service` on rigby watches `ip monitor link` and reconciles every `enx*` into `br0` (bring-up, TX-offload-off, ARP-flush) within seconds of any link event, plus a reconcile at boot — so a normal reboot needs **no manual host step** ([[Tablet-RNDIS-Setup-Configuration-Procedures]] §4f). The manual procedure below is the fallback for a stopped service; the **ZERO-`enx` kernel-fallback case (below) is service-independent** — a missing host driver is not something the bridge daemon can fix.

Rebooting the RIGBY host does not disrupt tablet power states or USB links, but it removes the Linux bridge (`br0`) until the service rebuilds it. The resulting state (before the service reconciles) is:

* **Tablets that remained powered:** `rndis0` interfaces stay UP with intact addresses and UDC configured. Routes via `rndis0` remain valid.
* **Tablets that lost link or rebooted:** `rndis0` goes DOWN; routing falls back to `wlan0`.
* **Host (rigby):** All tablet `enx*` interfaces (one per connected node) are present but enslaved to no bridge. The `br0` interface is absent until `fellowship-bridge.service` (or a manual run) restores it.

### Diagnosis and restoration

When `enx*` interfaces appear DOWN on the host, verify the tablet state before assuming cable failure. The tablets often remain connected; the failure point is the missing bridge on the host.

1. Confirm which tablets have active `rndis0` links.
2. Restore the bridge by following the procedure in §6b.
3. Verify connectivity using multi-ping sequences (see Instrument #10 below).

### If ZERO `enx` interfaces are present on rigby (kernel fallback)

⚠ **An `enx*` count of 0 (not "present but unenslaved") means the RNDIS host driver is missing, not the bridge.** This happens when rigby boots a kernel whose `linux-modules-extra` is not installed — `usbnet` / `cdc_ether` / `rndis_host` live in that package. Without them the tablets still enumerate on the USB bus (`lsusb -t` shows them) but bind `Driver=[none]`, so no `enx` is created and `fellowship_bridge.sh` reports "0 usb interfaces present -- NOTHING TO DO". Traced 2026-08-28: ~10 reboots during a hardware swap left rigby on the older **6.8.0-31** kernel (which lacks `-extra`) instead of the newest **6.8.0-138** (which has it).

```bash
uname -r                                              # which kernel actually booted
lsusb -t | grep 'Class=CDC Data'                      # tablets present but Driver=[none]?
modinfo -n rndis_host || apt-get install -y linux-modules-extra-$(uname -r)
modprobe rndis_host                                   # enx* now appear
```
Then run `fellowship_bridge.sh` to enslave them. The GT730 nvidia-470 driver builds against both 6.8.0-31 and 6.8.0-138 ([[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]]), so it does **not** pin the kernel — a fallback is a boot-order accident, not intentional.

### USB Configuration Note

Some tablets may report `sys.usb.config=rndis,none,adb`. This configuration is functional and distinct from the catastrophic `none`-only state described in §4a. No intervention is required for this variant.

The tablet side (gadget + address, §4g) is runtime-only and re-applied per reboot; the **host `br0` bridge is now persistent across reboots** via `fellowship-bridge.service` ([[Tablet-RNDIS-Setup-Configuration-Procedures]] §4f, and the self-heal note at the top of this section).
