# Tablet USB Networking - Architecture, Topology & Fleet Status



## Search terms

- `rndis0 does not exist`
- `sys.usb.config rndis has no effect`
- `svc usb setFunctions rndis does nothing`
- `tablet USB tethering Android GSI`
- `USB link is up but nothing comes back`
- `adb died after setting sys.usb.config`
- `are the tablets faster over USB`
- `why is USB 2.0 only giving 140 Mbit/s instead of 400`
- `USB tethering slower than expected`
- `RNDIS vs NCM throughput latency`
- `ncm.0 mkdir No such file or directory`
- `tablet USB 95% packet loss but transfers work`
- `usb udc state not attached`
- `is the USB link CPU bound`

## 2. Topology

```
  rigby  br0 = 10.55.0.1/24        (bridge; all enx* enslaved)
    |
    +-- USB -- frodo    10.55.0.11      (Android 11, aarch64)
    +-- USB -- sam      10.55.0.12      (Android 11, aarch64)
    +-- USB -- legolas  10.55.0.13      (Android 11, aarch64)
    +-- USB -- merry    10.55.0.14      (Android 12, aarch64)
    +-- USB -- pippin   10.55.0.15      (Android 12, aarch64)
    +-- USB -- gandalf  10.55.0.16      (Android 12, aarch64)
    +-- USB -- aragorn  10.55.0.17      (Android 12, aarch64)
    +-- USB -- sméagol  10.55.0.18      (Android 11, armv7l 32-bit, Samsung SM-T560NU; joined 2026-09-03)

    +-- USB -- gimli    10.55.0.19      (Android 11, aarch64)
    +-- USB -- boromir  10.55.0.20      (Android 11, aarch64)
    +-- USB -- faramir  10.55.0.21      (Android 11, aarch64)
```

**Design doctrine: Bridge over per-interface addressing.**
Android RNDIS gadgets assign a random MAC address on every enable event. The resulting host interface name (`enx<mac>`) changes on each reconnect. Mapping specific `enx` interfaces to specific tablets fails silently upon replug. The bridge topology enslaves all existing `enx*` interfaces regardless of identity, allowing tablets to self-address via DHCP. This provides layer-2 forwarding for tablet-to-tablet traffic, avoiding kernel routing decisions and NAT overhead.

**Configuration:**
Interfaces matching `enx*` are excluded from rigby’s netplan (`01-wired-any-slot.yaml`) to prevent conflicts with DHCP services.

**Infrastructure location:**
The USB network bridge resides on **rigby** using a USB add-in card. It serves as the router/bridge for node-to-node traffic, offloading CPU load from Softserve (which hosts Plex/Sundae).

## Segment Composition and Restore Procedure

Every Fellowship tablet is cabled to `rigby` and on the USB segment (`br0`, `10.55.0.0/24`): the roster below is the segment. USB is the main path and WiFi the backup: Tolkien reaches each node over the wire first and falls back to WiFi per node ([[Fellowship-Order-Taker-Design]] §12). Sméagol at `10.55.0.18` is a Samsung SM-T560NU on Termux-driven RNDIS with a rigby-side reassert on boot. Every node must have the RNDIS persist configuration; a node without it is a defect to fix. See §4f.

### Node Roster

| Tablet | IP Address | Arch |
|---|---|---|
| frodo | `10.55.0.11` | aarch64 |
| sam | `10.55.0.12` | aarch64 |
| legolas | `10.55.0.13` | aarch64 |
| merry | `10.55.0.14` | aarch64 |
| pippin | `10.55.0.15` | aarch64 |
| gandalf | `10.55.0.16` | aarch64 |
| aragorn | `10.55.0.17` | aarch64 |
| sméagol | `10.55.0.18` | **armv7l** (32-bit; Samsung SM-T560NU) |
| gimli | `10.55.0.19` | aarch64 |
| boromir | `10.55.0.20` | aarch64 |
| faramir | `10.55.0.21` | aarch64 |

### Restore Procedure

**Automatic — Tolkien's USB link keeper.** A tablet's USB network does not survive a reboot that bypasses Tolkien (any raw `adb reboot`, power loss, a crash). Tolkien checks every node every 5 minutes over WiFi adb and restores one whose link is down: two confirming passes, only while the order taker is idle, at most once per 30 minutes per node, and every action is posted to the desk (`USB keeper: <node> restored`). A link is the INTERFACE — `rndis0` up with the node's own `10.55.0.<octet>` — not `sys.usb.config`: on the Android 11 configfs nodes that property stays `adb` while the link works. Code: `tablet_control.keep_usb_links`. A rigby reboot or a cable unplug leaves class A (gandalf, aragorn) with `ffs.adb` in both gadget slots, which the keeper repairs with a configfs relink ([[Tablet-RNDIS-Setup-Configuration-Procedures]] § "Class A … restore-failed").

**Manual, one node or a group:** `POST /fellowship/usb-restore {"target": "<node>|class-a|all"}` on Tolkien's control port (`:8740`, token header — [[Tablet-RNDIS-Setup-Configuration-Procedures]]).

**Whole segment including rigby's bridge**, after a rigby reboot:

```
python E:\SSDShared\Lab\fellowship\usbnet_up.py --apply
```

The script performs the tablet-side configuration over WiFi via adb, then executes the root-side configuration on `rigby` via `Lab\bin\run_remote.py`. It passes the roster IP octets to `fellowship_bridge.sh` to ensure both sides verify against the same node set. Run the script without `--apply` first to inspect state without making changes.

### Verification

A successful restore returns:
* `N of N tablets verified ON THE WIRE`, N being every node in the roster above
* `bridge OK` (indicating `enx` interfaces are present and are members of `br0`)

⚠ **Check `usbnet_up.py` and `fellowship_bridge.sh` walk the full current roster** (octets `.11`–`.21`) before trusting their "N of N" line: both were written for the original seven, and a script walking an old list reports a short roster as complete.

Verify that traffic routes correctly through the bridge. An IP address alone does not guarantee correct routing; traffic must leave via the USB network interface, not `wlan0`.

## RNDIS Fleet Status and Conformance

All tablets on the homelab USB network operate using the RNDIS protocol. RNDIS is the mandated resting state for this fleet to ensure low-latency connectivity; sam and legolas support only RNDIS (§4e).

**Conformance Instrument:** `restore_rndis.py` verifies protocol adherence. Every node must pass this verification; a node failing it is considered defective.

### Current Node Table

| Tablet | IP Address | Protocol | Held-TCP RTT Median | Solo Throughput (Mbit/s) | Notes |
|---|---|---|---:|---:|---|
| sam | 10.55.0.12 | RNDIS | 0.265 ms | 141.0 | Android 11; required §4b gadget repair |
| legolas | 10.55.0.13 | RNDIS | 0.269 ms | 140.6 | Android 11; required §4b gadget repair |
| merry | 10.55.0.14 | RNDIS | 0.310 ms | 137.6 | Android 12; supports NCM (210 Mbit/s @ 2.1 ms) |
| pippin | 10.55.0.15 | RNDIS | 0.340 ms | 135.5 | Android 12; supports NCM (213 Mbit/s) |

### Throughput and Latency Design Facts

*   **Individual Performance Band:** Tablets operate in a 135.5–141.0 Mbit/s solo throughput band on RNDIS, across both silicon families.
*   **Controller Irrelevance:** The USB controller type does not impact performance; the protocol determines latency and throughput (§1a).
*   **Aggregate Capacity:** Whole segment capacity with all four nodes sending simultaneously is 224 Mbit/s (§1a). Per-node share at four-way contention is approximately 56 Mbit/s. Scheduling decisions must use segment-level figures, not single-node metrics.

### Physical Topology and Enumeration Rules

**Enumeration Channel:** USB device configuration and UDC status checks must be performed over adb. Termux access to `/sys/class/udc/` is denied (`Permission denied`), causing glob expansion failures that misreport UDC absence.

**Physical Layout (Host: rigby):**

| Tablet | USB Path | Attached To |
|---|---|---|
| pippin | 5-2.1 | Add-in card → 4-port hub |
| merry | 5-2.2 | Add-in card → 4-port hub |
| sam | 5-2.3 | Add-in card → 4-port hub |
| legolas | 5-2.4 | Add-in card → 4-port hub |
| frodo | 5-3 | Add-in card, direct root port |
| gandalf | 1-1.3 | Motherboard EHCI (oldest controller) |

**Hub Constraints:**
*   The hub used for sam, legolas, merry, and pippin is a Hitachi `045b:0209` with a Single TT (`bDeviceProtocol 1`).
*   Four tablets share one transaction translator. This architecture limits peer traffic to half the host-facing figure (§1b) and explains hybrid arm performance gains when moving pairs off the bus (§1c).

**Identification:**
Sam and legolas are physically indistinguishable (same model, same GSI, `ro.product.model` reads `Phh-Treble vanilla`). Identification requires serial number verification:
*   sam: HA15WNRT
*   legolas: HA197X4L
*   merry: HA10FXCV
*   pippin: HA10F5DZ
*   frodo: HA1998VW
*   gandalf: HA1WKD37
*   aragorn: HA1TXV62
*   sméagol: da2dec72221ccc9a (Samsung SM-T560NU — `ro.product.model` reads `SM-T560NU`, not `Phh-Treble vanilla`; distinguishable by model string alone)
*   gimli: MJ0ETXPE
*   boromir: HA198QW5
*   faramir: MJ0EW27F

**Power delivery to the tablets (read 2026-10-12; read-only):**
*   **Host:** rigby is an HP Compaq Elite 8300 SFF (240 W supply, Intel 7-series PCH ports). The add-in card is a Renesas uPD720201 xHCI with an unbranded subsystem ID (1912:0014), a 4-port hub on it (USB ids 045b:0209 / 045b:0210), fed by SATA power. The uPD720201 datasheet gives per-port `PPONx` (port power on) and `OCIxB` (over-current in) pins "connected to external power switch", so the per-port current limit is set by that switch on the card, not by software, and its feature list says "Supports USB Battery Charging Specification" (p. 1). Whether this card presents a charging port is not documented; the tablets do not see one.
*   **Tablets:** every tablet reports its port as a standard data port: `usb` supply type USB, `current_max` 500000 µA, gadget `MaxPower` 500. **Input-current limits LOOK writable (file mode bits; a `[ -w ]` test says yes) but the kernel REFUSES the write: on merry, `echo 700000 > /sys/class/power_supply/usb/current_max` and `.../sdp_current_max` both return "Permission denied" (SELinux Enforcing; adbd is uid 0 in the `su` domain, but the node is labelled `sysfs_usb_supply`), and sam's `current_max` (MediaTek, labelled `sysfs_battery_mmi`, the same label as `charging_enable`) is refused the same way; a stepped 700 / 900 mA test on merry on 2026-10-12 therefore changed nothing: input stayed at 495 mA, `current_max` read back 500000 throughout, merry ended at the original setting. A permission-bit test is not a write test.** The nodes that exist: MediaTek tablets expose `/sys/class/power_supply/usb/current_max` and `/proc/mtk_battery_cmd/current_cmd`; the Qualcomm tablets expose `sdp_current_max`, `current_max`, `hw_current_max` and a 2 A `constant_charge_current_max`. The tablets' USB device controllers are high-speed (USB 2.0) only (`musb-hdrc` on MediaTek, `msm_hsusb` on Qualcomm; `maximum_speed` = high-speed), so every tablet runs on the 480 Mbit/s pair even in a 3.0 port and never gets the 900 mA a SuperSpeed device is allowed; the 5000M buses carry no tablets. The USB 2.0 limit for a configured device is 500 mA; more needs a BC 1.2 charging port (CDP / DCP, up to 1.5 A, signalled on D+/D-). Raising a tablet's limit past 500 mA is therefore out of spec for a standard port and untested here; the hub's real per-port capacity is unknown. A step-up test with a voltage-sag stop is the way to find it.
*   **Researched 2026-10-12 — there is no software lever to raise a tablet's input current.** MediaTek `/proc/mtk_battery_cmd/current_cmd` is NOT an input-current limit: community bypass-charging projects document `echo "0 1" > current_cmd` as bypass charging (battery current drops to ~10 mA, the system runs from USB) and `"0 0"` as normal (sam reads `0 0`); `en_power_path` is the same family of on/off switches the governor already uses. The input limit itself comes from charger-type detection (standard port → 500 mA), the nodes that hold it are SELinux-labelled out of reach on merry and sam, and changing that means flipping SELinux to permissive or editing the kernel, which are security settings left to the owner. The designed route to more current is a BC 1.2 charging port (CDP/DCP), i.e. hardware. Sources: community write-ups of `current_cmd` (blog.rem01gaming.dev "Bypass Charging on MediaTek", github.com/mmetem55/vendor_mtk-bypass-charge, VR-25/acc).
*   **Sam's "won't charge" (2026-10-12) was cured by a REBOOT — cause unknown, and it was not the port:** after 56 days up it sat at 16 % "Charging" with battery current ≈ −9 mA; after `adb reboot` + `usbnet_up.py --apply` (9 of 9 on the wire) it charged at **+339–349 mA** on the same 500 mA port. **Not the cause (measured, fleet survey the same night):** the three `kworker`s in `boost_work` and cpu0 reading 2.3 GHz are NORMAL on every X606F — all five class-B X606F nodes show exactly 3 `boost_work` workers and 2.3 GHz, sam included straight after its reboot while charging fine. The load average of ~25 is also normal on the X606F: ~22 MediaTek kernel threads (`wdtk-*`, `charger_thread`, `battery_thread`, …) sleep uninterruptibly by design, and it returns within minutes of a clean boot. The symptom to act on is the battery trend: "Charging", below the governor band, not gaining while idle — Tolkien's charge keeper now does exactly that ([[Fellowship-Order-Taker-Design]] § "The charge keeper").
*   **A tablet whose battery DIED shows up on rigby's USB as HID-only** (faramir 2026-10-12, after dying at 7 % on 10-12 02:14): `0e8d:20ff`, one interface of class 03 bound to `hid-generic`, no adb (`ff`) and no RNDIS (`e0`) — and nothing on WiFi either. Read it with `for d in /sys/bus/usb/devices/*; do … serial, interface classes …` on rigby. The HID function is an empty placeholder (report descriptor `05 01 09 00 a1 01 c0` = Generic Desktop, undefined usage, empty collection; configuration string "HID", `MaxPower 500mA`, 4-byte EP1 IN/OUT) — a gadget that exists only to be configured and draw charging current. Most likely it is in power-off charging (MediaTek charger mode, inferred from that, not proven): a rigby reboot does not bring it back and Tolkien's USB keeper cannot reach it (it works over WiFi adb). It needs its power button held at the tablet.
*   **Hub descriptors, read as root 2026-10-12** (root on rigby is ordinary lab work: `python E:\SSDShared\Lab\bin\run_remote.py rigby <script.sh>` feeds the locker's sudo password on stdin, never printed — see `Fellowship-Gauntlet-Tournament-Order.md`): the Renesas card's hub (045b:0209 USB 2.1 half, 045b:0210 USB 3.0 half) and both Intel PCH hubs report **self-powered, per-port power switching and per-port over-current protection**; `bHubContrCurrent` 100 / 200 mA (the hub controller's own draw, not a port limit). **Zero over-current events in `dmesg`.** The descriptors do not state a per-port current capacity; that is the external switch's rating. Per-port power switching means rigby can power-cycle one tablet's port (e.g. `uhubctl`) — a remote recovery for a hung tablet, not a way to raise current. ⚠ **Only the HUB ports have it: all six ROOT hubs (buses 1–6) report "No power switching"** — confirmed at the hardware, not just the kernel's synthesized root-hub descriptor: both xHCI controllers' own `HCCPARAMS1` read **PPC = 0** (Intel 00:14.0 `0x20007181`, Renesas 03:00.0 `0x014051c7`; read from PCI BAR0, 2026-10-12), i.e. their ports are always powered. The external hubs' per-port switching is the hub chips' own descriptor claim, not yet exercised — so a tablet plugged straight into a root port (faramir, gimli, boromir, aragorn on bus 3; sam, legolas on bus 5) cannot be power-cycled from rigby. Free switchable ports 2026-10-12: Intel rate-matching hub on bus 1 ports 1–4, Renesas-card hub (bus 5) port 4.

**Untested Optimization:**
Splitting the four hub-connected tablets across two controllers (utilizing rigby's empty motherboard xHCI bus 3, `00:14.0`) is a known potential improvement but remains unmeasured.
