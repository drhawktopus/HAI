# Fellowship Cluster - Purpose, Architecture, and Network Constraints



## Purpose
The Fellowship tablet cluster serves as a low-priority compute resource for small jobs that do not require significant compute power. It is integrated into the scheduler to handle tasks when primary lab nodes are occupied.

## Architecture and Network Configuration

### ADB Connectivity
- Every node in the fleet exposes the Android Debug Bridge (ADB) on port `5555`.
- Wireless debugging is disabled as it uses random ports that do not persist across reboots, requiring manual re-enabling.

### Persist Properties and Reboot Behavior
- The property `persist.adb.tcp.port=5555` survives reboots.
- To set this property, use `adb root`. Do not use `su`, as it is absent from the standard paths on LOS 18.1 devices.
- The assertion that "persist-prop writes never reach disk" applies **only** to `persist.adb.tls_server.enable`.

## Independent vs. Coupled Workload Performance

The Fellowship fleet is designated exclusively for independent, embarrassingly-parallel workloads. Tightly-coupled jobs (e.g., MPI) are prohibited due to structural performance penalties.

| Mode | Measured Performance |
|---|---|
| 5 nodes running independent jobs | 49.3 GFLOP/s (≈57 with a healthy node) |
| 5 nodes on one MPI job (HPL, N=24000, 32 ranks) | 7.91 GFLOP/s |

**Note:** The measurements above cover five nodes. The current roster is **eleven** — see [[Tablet-Cluster-INDEX]] for the full list. The seven original aarch64 nodes plus Sméagol are wire-connected; Gimli/Boromir/Faramir joined 2026-09-04 as WiFi-only. Adding nodes scales throughput on independent jobs approximately proportionally (for like nodes); the ~6× performance penalty for coupled work remains structural and grows with node count on WiFi. **⚠ Sméagol** (armv7l/32-bit Samsung SM-T560NU) has ~30% the FLOPS of the aarch64 8-core tablets and does not run the aarch64 native binaries used by coupled workloads — it should be excluded from HPL-class comparisons, not folded into them.

## Network Architecture Constraints

The primary constraint on coupled workloads is the wireless interconnect:

* **Shared Medium:** All nodes associate with a single AP radio (5500 MHz, BSSID `<mac-address>`). The aggregate ceiling is ≈333 Mbit/s shared. With multiple nodes transmitting, per-node throughput drops significantly.
* **Latency Floor:** Node-to-node traffic crosses the AP twice, resulting in a latency floor of ~10 ms (single hop: 5.8 ms). This contrasts with ~0.1 ms on wired connections. Coupled workloads requiring frequent synchronization suffer repeated latency penalties.
* **Reliability:** MPI runs are all-or-nothing; transient WiFi disconnections cause total job failure (`ORTE has lost communication with a remote daemon`). Task-farm models tolerate individual node failures via retry.

## USB Wired Networking Alternative

A wired alternative exists using USB RNDIS/NCM tethering through the `rigby` host. This is a finished, implemented state, not a proposal.

### Performance Characteristics

* **Latency:** Node-to-node round-trip is 0.40–0.52 ms over wired connection vs. 2.9–5.7 ms over WiFi (7–11× improvement). This latency reduction applies primarily to coordination-heavy, small-message workloads.
* **Throughput:** Wired throughput is ~2.5× per node and ~2.6× in aggregate compared to WiFi. Four tablets sharing WiFi achieve less total throughput than one tablet alone over USB.
* **Coupled Workload Impact:** In a coupled matmul benchmark (4 nodes, N=2048), wired connection improved performance by 1.83× (20.1–21.1 s over WiFi vs. 11.2 s wired). The interconnect tax decreased from 95% to 93%. Bulk data shuffling sees ~2× improvement; small-message coordination sees ~10× improvement.
* **Projection:** Previous projections of 35–48 GFLOP/s for distributed coupled workloads are unsupported by current measurements and remain untested.

### Implementation Details

* **Routing:** Node-to-node traffic is routed through `rigby` (USB add-in card, bridged onto `br0`, IP `10.55.0.1/24`). This offloads routing CPU from the Plex/Sundae host (`Softserve`).
* **Bus Contention:** Current implementation uses a single USB controller (Renesas add-in card, bus 5). Contention begins at the second device on the same controller. Splitting tablets across multiple USB controllers (e.g., Intel xHCI on bus 3) is an untested optimization to reduce shared-bus artifacts.
* **Coverage:** Every tablet is on the wire (`10.55.0.11`–`.21`; Sméagol at `.18` on Samsung SM-T560NU USB-RNDIS). The wire is the designed main path and WiFi the backup; Tolkien's wire-first routing is ON (`TABLET_ROUTE_PROBE=1`, [[Fellowship-Order-Taker-Design]] §12), and since 2026-10-10 a node whose wire answers counts as alive even with its WiFi down.
* **Persistence:** Configuration does not survive reboots. Restore procedure is documented in [[Tablet-USB-Networking-RNDIS]] §6b.

## Design Directive

Do not propose USB Ethernet for primary use if portability (untethered fleet) is a strict requirement per the owner’s decision. However, the wired implementation via `rigby` is active and provides superior latency and throughput characteristics for specific workload classes. Use independent workloads as the default strategy; utilize wired connectivity only when explicitly required for performance-sensitive coordinated tasks.
