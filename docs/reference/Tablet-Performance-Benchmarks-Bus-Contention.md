# Tablet USB Networking - Performance Benchmarks & Bus Contention



## USB networking performance benchmarks: latency and throughput characteristics

### Small-message round-trip delay

Node-to-node round-trip latency for small messages is significantly lower over USB RNDIS than over Wi-Fi. Measurements use a held TCP connection, 300 round-trips per reading, 3 readings per arm, with arms interleaved and the same server process serving both arms.

| client → sam | over WiFi | over USB | faster by |
|---|--:|--:|--:|
| legolas | 2.92 ms (2.79–3.10) | **0.400 ms** (0.399–0.402) | 7.3x |
| merry | 5.72 ms (5.58–5.87) | **0.524 ms** (0.505–0.541) | 10.9x |
| pippin | 5.56 ms (5.49–5.61) | **0.518 ms** (0.517–0.520) | 10.7x |
| merry → pippin | 8.38–8.52 ms | **0.59–0.98 ms** | 11.0x |

USB latency ranges do not overlap with Wi-Fi ranges; the worst USB reading beats the best Wi-Fi reading by approximately 7x. USB interconnects exhibit higher stability, with minimal variance between identical runs compared to Wi-Fi.

This confirms the design prediction in [[Fellowship-Crunch-Integration]] §1 that two USB hops result in hundreds of microseconds versus ~10 ms for Wi-Fi.

### Bulk-work throughput (distributed matmul)

For bulk workloads involving megabyte-scale matrix transfers, performance gains are limited by bandwidth rather than latency. Measurements use a distributed matrix multiplication workload across 4 nodes (N=2048), with the same master (rigby) for both arms, interleaved arms, and 3 repeats. Only worker addresses differ between arms.

| arm | wall | effective | interconnect tax |
|---|--:|--:|--:|
| WiFi | 20.11 / 20.18 / 21.10 s | 0.81–0.85 GF/s | 95.0–95.4% |
| USB | **11.16 / 11.17 / 11.25 s** | 1.53–1.54 GF/s | 92.8–93.2% |

USB provides approximately 1.83x faster completion for bulk workloads. Ranges do not overlap.

### Interpretation of latency vs. bandwidth gains

The ~10x latency improvement for small messages does not translate to equivalent gains in bulk workloads. Bulk transfers are limited by bandwidth, where the USB/Wi-Fi gap is smaller than the latency gap. Interconnect tax remains high (93–95%) because nodes spend most of the job moving data rather than computing. Do not quote the ~10x latency improvement when characterizing bulk-work performance.

## 1a. Throughput: comparative performance and limitations

Sustained single-TCP transfer from tablets to rigby (the only common host; arms differ only in worker address). Instrument: `bwtest.py`, incompressible payload, 300 MB per stream.

| Transport | Single node throughput | Aggregate throughput |
|---|---|---|
| WiFi | 68–136 Mbit/s | 79.8–92.3 Mbit/s |
| USB RNDIS | 132.8–141.0 Mbit/s | 224 Mbit/s (four nodes) |
| USB NCM | 209.1–213.0 Mbit/s | 254 Mbit/s (pair only; max two tablets support NCM) |

**Limitations and observations:**
* **Tablet-to-tablet transfers:** The figure for tablet-to-rigby does not apply to tablet-to-tablet traffic. Usable throughput is capped at 108 Mbit/s; see §1b.
* **WiFi saturation:** Four tablets sharing WiFi achieve lower aggregate throughput (~85 Mbit/s) than a single tablet on USB (140–213 Mbit/s). WiFi per-node throughput is highly variable (23–141 Mbit/s within one concurrent run). USB RNDIS solo transfers repeat to within 0.5% variance.

## Throughput limits: Protocol overhead and bus contention

USB 2.0 RNDIS networking on this fleet achieves ~138 Mbit/s per node under single-node load, saturating well below the theoretical 426 Mbit/s bus ceiling. Two distinct factors limit throughput: protocol framing inefficiency (Gap 1) and shared bus resource contention (Gap 2). Both factors consume bus *transactions* rather than raw payload bytes.

### Gap 1: RNDIS protocol overhead

RNDIS transmits one Ethernet frame per USB transfer. A standard 1448-byte payload with headers totals 1558 bytes, packing into four 512-byte USB packets and leaving 466 bytes of the fourth packet unused. This framing inefficiency wastes bus transaction time.

NCM (Network Control Model) mitigates this by bundling ~10 frames into one 16 KB block, reducing transaction overhead.

**Trade-off:** NCM increases throughput by ~50% but increases latency by ~6–7x. For coordination-heavy workloads, RNDIS is the preferred default due to lower latency.

*Note: Tablet families (MediaTek vs. Qualcomm controllers) show identical performance when holding protocol constant. The performance difference between families in mixed tests was attributable to protocol selection, not silicon.*

**Protocol-only comparison (single node, merry tablet):**
| Protocol | held-TCP RTT median | throughput (Mbit/s) |
|---|---|---|
| RNDIS | 0.31 / 0.37 ms | 132.8–140.0 |
| NCM | 2.10 / 2.30 ms | 209.1–210.4 |

*See §4f for device capability details: sam and legolas do not support NCM.*

### Gap 2: Shared bus contention

All four tablets connect to a single Renesas uPD720201 hub on `usb5`. Adding nodes increases contention on this shared bus.

**Contested throughput (all RNDIS):**
| Senders | Sum of node rates (Mbit/s) | Per-node rate |
|---|---|---|
| 1 | 138 | 138 |
| 2 | 186 | 93 each |
| 3 | 210 | 70 each |
| 4 | **224** | 56 each |

*Measurement note: Sum of node rates is the authoritative figure for comparison. Wall-clock totals are lower due to ssh/Python start-up overhead charged to transfer time.*

Contention begins at the second device, reducing per-node throughput by ~34% (138 → 93). Cross-family and same-family pairs show identical contention penalties, confirming bus topology—not silicon differences—drives the limit.

### Bus capacity vs. Protocol saturation

The four-node RNDIS ceiling of 224 Mbit/s is set by protocol transaction limits, not raw bandwidth availability. The Renesas uPD720201 hub on `usb5` supports higher aggregate throughput when NCM reduces framing overhead.

**Two-node capacity test (merry + pippin only):**
| Protocol | Sum of node rates (Mbit/s) | Per-node rate |
|---|---|---|
| RNDIS | 185 | 92 each |
| **NCM** | **254** | 127 each |
| RNDIS (repeat control) | 185 | 92 each |

Two tablets on NCM (254 Mbit/s) exceed four tablets on RNDIS (224 Mbit/s), proving the bus can carry more payload if transaction overhead is reduced. Extrapolation beyond these measurements is not supported: sam and legolas lack NCM support (§4e), preventing four-node NCM tests.

### Bus topology

| Bus | Controller | Tablets | Historical enumerations |
|---|---|---|---|
| 5 | Renesas uPD720201 (add-in card) | **All four** | 324 |
| 1 | Intel EHCI 00:1a.0 | Empty | 3 (root hub only; untested reachability) |
| 2 | Intel EHCI 00:1d.0 | Empty | 3 (root hub only; untested reachability) |

**Improvement path:** Splitting tablets across `usb5` and `usb3` doubles available bus time by isolating controllers. This requires physical plug relocation. Buses 1 and 2 are not claimed as usable pending physical connectivity verification.

### Performance Characteristics and Bottleneck Analysis

USB networking provides ~10x throughput over WiFi for coordination-heavy work (small messages) using RNDIS. For bulk data shuffling, USB provides ~2.5x throughput per node; NCM adds another 50% if the transfer phase is long enough to amortise a ~60 s protocol switch. USB networking is more predictable than WiFi, which matters for barrier-dependent work.

The following factors are **not** the cause of throughput limitations:

- **CPU utilization.** Single-core saturation is not the bottleneck. Testing separate sender processes on one tablet showed flat throughput (198 → 201 → 201 Mbit/s for 1, 2, and 3 senders respectively). The busiest core on rigby during transfers peaked at 8.7%.
- **USB hub version.** USB 3.0 hubs contain two independent controllers by spec; a USB 2.0 device connects only to the 2.0 controller. On the host machine, the Hitachi hub appears on bus 005 at 480 Mbit/s carrying all four tablets, and on bus 006 at 5000 Mbit/s carrying nothing.
- **MTU.** The MTU is fixed at 1500 while the gadget interface is bound (`RTNETLINK answers: Device or resource busy`).
- **NCM aggregation settings.** Settings are maximal in both directions: `tx_max = rx_max = 16384`.
- **Directional asymmetry.** Throughput is not one-sided. NCM shows 219 out / 181 in; RNDIS shows 144 out / 133 in.

## 1b. PEER traffic throughput and bus contention

**Design constraint:** Peer-to-peer traffic between tablet nodes traverses the shared USB bus twice (in on one device, out on another) plus a bridge hop on rigby. This double traversal halves effective peer bandwidth relative to host-facing figures.

**Throughput specification:**
*   **Host-facing ceiling:** 224 Mbit/s aggregate (four nodes to rigby). This figure serves as the independent bus capacity measurement.
*   **Peer-to-peer delivered capacity:** ~108 Mbit/s aggregate. Individual pair tests yield ~91–92 Mbit/s; concurrent dual-pair tests yield 108 Mbit/s total (54 Mbit/s per node).

**Conformance verification:**
Arithmetic closure confirms the bus contention mechanism: 108 Mbit/s delivered peer traffic implies ~216 Mbit/s of bus traffic, which aligns with the independently measured 224 Mbit/s bus ceiling.

**Scheduling doctrine:**
1.  **Hub-preference:** Prefer topologies where rigby is the hub (gather/scatter, one hop each way) over all-to-all tablet exchanges to avoid double-wire penalty.
2.  **Capacity planning:** Size tile-exchange steps against the 108 Mbit/s peer limit, not the 224 Mbit/s host-facing figure.
3.  **Controller isolation:** Maintain the two-controller split (see [[Tablet-USB-Networking-RNDIS]] §1a). Peers on different controllers do not contend for the same bus.

### Peer Latency and Qualcomm-to-Qualcomm Instability

A peer hop costs approximately **1.5–2× the host leg**. This is consistent with two USB hops. The floor for stable links is ~0.4–0.5 ms.

**Design Fact:** Every hop containing at least one MediaTek endpoint exhibits stable latency of 0.50 ms. The instability is a property of the **Qualcomm ↔ Qualcomm pairing** (specifically `msm_hsusb` drivers on both ends), not of individual devices or directionality. Both endpoints must be `msm_hsusb` for the excursions to manifest.

**Constraint:** Do not quote a median for Qualcomm↔Qualcomm hops. The distribution is bimodal, not noisy; a median names a value the link never produces. Quote the floor and the spread instead.

**Scheduling Rule:** Do not pair `merry` with `pippin` for latency-sensitive peer work. Pair each with a MediaTek node to ensure stable 0.50 ms hops.

**Troubleshooting Protocol:** When one pair misbehaves, test each member against a KNOWN-GOOD partner before naming a culprit. A two-cell result cannot distinguish a device defect from a pairing defect.

---
title: "Reference/Tablet-USB-Networking-RNDIS"
---

### Design Doctrine: Mutual-Redundancy Mesh and Traffic Splitting

The USB segment exists to provide mutual redundancy, not primarily for latency reduction. Every Fellowship node maintains two independent network paths:

1.  **WiFi path**: Addresses `.71`–`.78` on the standard subnet.
2.  **USB path**: Addresses `10.55.0.11`–`.17` on the RNDIS subnet.

The USB path bypasses the Access Point (AP) and router entirely. This architecture ensures that if one path is unreachable, the node remains accessible via the other. This provides two operational benefits:

*   **Fault Isolation**: A node unreachable on one path is not necessarily down. Check the alternate path before investigating hardware. "Fine on USB, dead on WiFi" indicates an AP fault; "dead on both" indicates a node fault.
*   **Resilience**: The mesh survives partial infrastructure failure (e.g., router reboot) as long as at least one path remains active.

See [[Network-Infrastructure]] §"belt and suspenders" for the broader network resilience strategy.

### Traffic Routing Principle: Decongestion via Media Splitting

The USB bus is a contended resource among tablets; WiFi airtime is generally idle during tablet-to-tablet transfers. Consequently, design fact:

> **Spend the wire on what benefits most from it — small, frequent, latency-sensitive coordination traffic — and push BULK onto WiFi.**

This split exploits USB's low latency for control planes and WiFi's higher aggregate throughput (when uncontented) for bulk data. The gain from mixing media is due to decongesting the USB bus, not because WiFi is faster per-pair than USB.

#### Performance Baseline: Hybrid vs. Single-Media

The following measurements demonstrate the effect of bus contention on USB-only transfers versus hybrid splitting. These values are benchmarks for specific load conditions and hardware states; they do not define the design requirement.

| Arm | Configuration | Per-Round Latency (Sender 1 + Sender 2) | Best Total |
|---|---|---|---|
| **A** | Both pairs over USB (`10.55.0.x`) | 55+55=111 · 54+54=108 | **111** |
| **B** | Both pairs over WiFi (`192.168.0.x`) | 35+24=59 · 30+22=52 | **59** |
| **C** | HYBRID — sam→legolas USB, merry→pippin WiFi | 89+56=145 · 91+51=142 | **145** |

*   **Observation**: The hybrid approach (Arm C) reduces total transfer time by approximately 31% compared to USB-only (Arm A). This gain arises because moving one pair off the USB bus decongests it, allowing the remaining USB pair to perform closer to its single-pair baseline (~91–92 ms).
*   **Corroboration**: Sam's latency in Arm C (89–91 ms) matches the single-pair USB baseline measured in §1b (91–92 ms). Arm A's total (108–111 ms) matches the two-pair contention baseline from §1b.

#### Operational Limits and Assumptions

1.  **WiFi Variability**: WiFi performance (Arm B) is highly dependent on ambient airtime usage. The measured 59 ms represents a best-case scenario with no other household traffic. Do not schedule time-sensitive operations against the hybrid total (145 ms) assuming stable WiFi performance.
2.  **Hardware Dependency**: The magnitude of the hybrid gain depends on USB bus contention. If hardware changes raise the USB-only ceiling (e.g., reduced contention via hardware fixes), the relative benefit of the hybrid split diminishes.
3.  **Pair Assignment**: Only one assignment of traffic pairs to media has been tested (sam/legolas on USB, merry/pippin on WiFi). Other assignments are untested but expected to behave similarly given the symmetric nature of the contention model.

### Verification: Route Isolation Check

To confirm that hybrid tests actually split traffic across distinct physical media, verify that routing tables direct each destination to the correct interface. A misrouted packet (e.g., WiFi-labeled traffic sent over USB) invalidates the comparison.

Run on each sender before measuring:

```bash
/system/bin/ip route get 10.55.0.13
## Expected: dev rndis0 table 55   src 10.55.0.12

/system/bin/ip route get <lan-address>
## Expected: dev wlan0 table 1010 src <lan-address>
```

Confirm `dev rndis0` for USB destinations and `dev wlan0` for WiFi destinations. Both interfaces must be active simultaneously.
