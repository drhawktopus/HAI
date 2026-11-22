# Homelab Machine Inventory & Virtualization

**Machines front door.** Every machine and grouping in the lab, with the canonical inventory tables
(MACs, Tailscale IPs, VMs, drives). For a quick orientation on any one box — what it is and what runs
on it — jump to its **hub page** in *Per-machine hub pages* below.

## Machine Inventory

All machines operate on the `<tailnet>.ts.net` tailnet. The table below lists each machine’s role, network identity, and operating system. Absence from Tailscale is indicated by `—`; presence is mandatory for lab participation.

| Name | Role | IP (LAN) | Tailscale IP | OS |
|------|------|----------|--------------|-----|
| MossMatriarch | Netgear RS300 router — DHCP server, DNS relay | <router-address> | — | Router firmware |
| Softserve | Hyper-V host, Plex server, Tailscale subnet router (→ <lan-subnet>/24) | <softserve-address> | <tailscale-address> | Windows 11 Pro |
| SundaeStackarr | Hyper-V VM — arr stack, Pi-hole (master) | <sundae-address> (eth0) | <tailscale-address> | Ubuntu 26.04 |
| SundaeStackarr Pi-hole | Docker macvlan container on eth1 | <pihole-address> | — | (Part of SundaeStackarr) |
| Mothership | Gaming/main PC, RX 7900 XTX GPU host (LLM + image gen). Access via OpenSSH `ssh mothership`, RDP 3389, or WinRM. | <mothership-address> | — | Windows 11 |
| HomeAssistantOS | Hyper-V VM — energy monitoring, automation | <home-assistant-address> | — | HAOS 18.3 / Core 2026.9.4 (2026-10-03) |
| Physical Raspi (MinuteMan) | Pi 3B+ — LAN peer Pi-hole resolver, BLE gateway, DDNS updater | <minuteman-address> | <tailscale-address> | Raspberry Pi OS |
| DO-PiHole (AMDVM) | DigitalOcean droplet — remote Pi-hole, Avery DNS, DDNS/Avery-ACL automation hub | <droplet-address> (public) | <droplet-tailscale-address> | Ubuntu 24.04.4 LTS |

**Inventory Constraints:**
* The only Hyper-V VMs on Softserve are `HomeAssistantOS` and `SundaeStackarr`. No other VMs exist on this host.
* The IP address `.21` is reserved for the Docker Pi-hole macvlan interface on SundaeStackarr's `eth1`. It does not correspond to a discrete virtual machine.

### Per-machine hub pages

Each general-purpose box has a thin **hub page** — identity, what runs on it, and links out to the
functional wikis for depth. This inventory page holds the canonical tables; the hub is the quick
"what is this box" orientation.

| Machine | Hub | One-line role |
|---|---|---|
| Softserve | [[Machine-Softserve]] | Hyper-V host · Plex · Tailscale subnet router · CPU/caption factory |
| Mothership | [[Machine-Mothership]] | RX 7900 XTX GPU host — 27B brain + all P20 renders/training |
| BigHonker | [[Machine-BigHonker]] | Primary 8B brain · video encode · 3070 factory node |
| SundaeStackarr | [[Machine-SundaeStackarr]] | Media Docker stack · Pi-hole master · HAI orchestrator |
| HomeAssistant | Machine-HomeAssistant | HAOS VM — energy monitoring + automation |
| MinuteMan | [[Machine-MinuteMan]] | Raspberry Pi — peer Pi-hole · BLE gateway · DDNS |

The router (MossMatriarch RS300) and the DigitalOcean droplet (DO-PiHole) have no separate hub — see
the inventory table above and [[RUNBOOK-DO-Pihole-and-MinuteMan-Services]].

### RAM / DIMM inventory (read off each box 2026-10-01 — the first time it was written down)

| Machine | Board / CPU | DIMMs (slot: part) | Total | Slots free | Running at |
|---|---|---|---|---|---|
| **Mothership** | ASUS ROG Strix B550-F Gaming / Ryzen 7 5800X (B2) | A2 + B2: 2 × 32 GB G.Skill Ripjaws V **F4-3600C18-32GVK** (the sticks of kit F4-3600C18D-64GVK, DDR4-3600 CL18, dual-rank); A1 + B1: 2 × 8 GB Corsair **CMK16GX4M2B3200C16** (BigHonker's old pair, rated 3200 CL16 — running ABOVE its rating) | **80 GB** (Corsair pair added 2026-10-23 ~15:30 CT by the owner; 79.9 GB visible) | 0 | 3600 MT/s on all four (read back 2026-10-23 15:40, `Win32_PhysicalMemory`); **0 × WHEA 19 at 6.4 min** after the 15:33:55 boot, past the burst window (query validated against the 1,087 from the 10-01 ladder); a memory stress test at 3600 is OWED before trusting it (the Corsair sticks are rated 3200); FCLK not yet read — ZenTimings |
| **Softserve** | ASUS PRIME B450M-A II / Ryzen 7 5700G | A2 + B2: 2 × 16 GB Corsair **CMK32GX4M2E3200C16**; A1 + B1: 2 × 8 GB Corsair **CMK16GX4M2B3200C16** (the pair that came out of BigHonker) | **48 GB** | 0 | 3200 |
| **BigHonker** | ASUS PRIME Z390-P / i5-9600K | A2 + B2: 2 × 16 GB G.Skill Trident Z Neo **F4-4000C18-16GTZN** (DDR4-4000 CL18, single-rank; Mothership's pair until 2026-10-17) | **32 GB** | 2 | 4000 MT/s (read back 2026-10-17 14:49; 0 WHEA after two A3B loads) |

All DDR4 (SMBIOS type 26). Softserve's use on 2026-10-01: 37.7 of 47.3 GB — SundaeStackarr VM 16 GB (5.8 real + 8.9
page cache; dynamic, max 16), HomeAssistantOS 7.1 (demand 6), the Claude desktop app 4.75, Windows + Defender ~5,
Plex 0.2 idle. Mothership at 32 GB is the binding constraint for Flash-Next on varied prompts
([[Qwen3.8-Flash-Next-on-Mothership]] §Review).

**The layout-C RAM swap (2026-10-17 ~14:30–15:10 CT, the owner):** BigHonker took Mothership's 2 × 16 Trident Z Neo
pair (4000 MT/s at XMP); Mothership took the 2 × 32 Ripjaws V kit bought 2026-10-09 (used, Jawa/Combox, $375 + $10).
**Mothership now (2026-10-25 17:34 boot, read live): 64 GB, A2/B2 G.Skill F4-3600C18-32GVK at 3600** — the owner pulled the
Corsairs to get back on track for the HAI 1.9 release ("I'll get further into it another time"; 3400 / FCLK 1700 with
all four "didn't work"). **Verified clean on the same instrument: 0 of 20 reads bad (was 18 of 20), 0 WHEA at 9 min
after boot.** One variable changed (the two Corsair sticks out), so the 80 GB mixed layout was the cause. The 80 GB
attempt, for the record of a later tuning session: A2/B2 G.Skill + A1/B1 Corsair **CMK16GX4M2B3200C16** (8 GB each,
BigHonker's old pair, added after the 10-23 ask) — **all four configured at 3600 MT/s, the 3200-rated Corsairs 400 MT/s
past their rating**; no memtest was recorded. ⚠ **What the 80 GB layout did (2026-10-25): unstable reads
on Mothership** — one unchanged 37.6 GB GGUF (`F:\Strata-models\Q2_0\…-00001-of-00002.gguf`, mtime 10-04) hashed to
three DIFFERENT values on three reads, one matching its archive hash; the same day one of six NVMe model copies came out
bad; crash dumps 10-22 09:15 and 10-25 08:01; no WHEA events (non-ECC errors do not log). **Baseline, measured the
same afternoon (read-only, idle box):** that 37.6 GB shard on F: read BAD **18 of 20** times (19 distinct hashes);
the two largest ollama blobs on the C: NVMe (18.6 / 16.8 GB, self-named by sha256) read BAD **3 of 5** and **2 of 5** —
a second drive, the same roughly constant error per GB read, so not one drive. The 10-25 0xA dump's faulting address is
inside `ntoskrnl.exe` (+0x321ac5, a wild pointer at IRQL 2: memory corruption's usual look), not an Acronis driver;
Acronis's `file_protector` minifilter is attached to the volumes, so it is the one other component every read crosses.
Leading suspect the memory layout (sticks / speed / fabric), not concluded; the one-variable re-tests (RAM change, or
Acronis paused) and the stick/BIOS decision are the owner's. Script: Claude scratchpad `shard_hash20.py` (20 reads, counts
BAD). Journal 2026-10-25-claude. Mothership's fabric ceiling is measured: FCLK 1900 clean, 1933+ throws WHEA 19 at any SoC tried
([[Qwen3.8-Flash-Next-on-Mothership]] §"RAM at 1:1"; read WHEA only ≥ 5 min after boot — the burst comes ~2½ min in).
Previous rows and the purchase note: `Archive\machine-inventory-superseded-2026-10.md`.

### Machine groupings

- **The Fellowship** — **11 tablets** (as of 2026-09-04: seven aarch64 originals + Sméagol (armv7l/32-bit Samsung SM-T560NU) + Gimli/Boromir/Faramir (aarch64 class-B TB-X606F)) → [[Tablet-Cluster-INDEX]]
- **Basement desktops** — rigby + mordecai (i5/i7 Ivy Bridge, HPL compute) → [[HOST-ONBOARDING-Basement-Desktops]]
- **The GPU factory / fleet** — Mothership + BigHonker + Softserve as factory nodes → [[P20-MULTI-FACTORY-FLEET]]

### The Fellowship (tablet cluster) — `.71`–`.75`, `.78`, `.80`, plus 2026-09 additions

The Fellowship consists of **eleven** tablets, all managed via DHCP reservations on the RS300 (`ADVANCED → Setup → LAN Setup`). Each maintains a `persist` property; absence constitutes a defect.

The four 2026-09 additions (Sméagol + Gimli/Boromir/Faramir) were also covered in Pi-hole `hosts/lan.list` and the tolkien container's `TABLET_NET`; their RS300 reservations were added 2026-09-04 by the owner, **lazy-locked to whatever IP each tablet had already landed on** rather than moved, so no DHCP-renew or rebuild was needed. (Sméagol's reservation is for its wlan0 MAC, applicable when it moves off USB-only.)

Every aarch64 node must present an active USB link (`rndis0`) to the wire bridge on rigby; a lack of `rndis0` on an aarch64 node is a fault to investigate. **Sméagol is on the wire at `10.55.0.18`** as USB-RNDIS from Termux with a rigby-side reassert on boot. Gimli, Boromir and Faramir are on the wire at `10.55.0.19`–`.21`.

Scheduler membership is determined by hostname resolution. A node without a corresponding `.lan` DNS record is excluded from the Fellowship schedule and considered fail-closed.

| node | IP | serial | model | MAC | band | notes |
|---|---|---|---|---|---|---|
| gandalf | `.71` | HA1WKD37 | TB-X6C6F | `<mac-address>` | 5 GHz | class-A |
| frodo | `.72` | HA1998VW | TB-X606F | `<mac-address>` | 2.4 GHz | class-B |
| sam | `.73` | HA15WNRT | TB-X606F | `<mac-address>` | 5 GHz | class-B |
| merry | `.74` | HA10FXCV | TB-X505F | `<mac-address>` | 5 GHz | class-C |
| pippin | `.75` | HA10F5DZ | TB-X505F | `<mac-address>` | 5 GHz | class-C |
| legolas | `.78` | HA197X4L | TB-X606F | `<mac-address>` | 5 GHz | class-B |
| aragorn | `.80` | HA1TXV62 | TB-X6C6F | `<mac-address>` | 5 GHz | class-A |
| sméagol | USB `10.55.0.18` (wlan0 reservation set for future WiFi use) | da2dec72221ccc9a | Samsung SM-T560NU (`gtelwifiue`) | `<mac-address>` | — | **armv7l/32-bit; ping + arch-neutral payload only; no detect/burst/hwdec/ncnn** |
| gimli | `.96` (reserved) | MJ0ETXPE | TB-X606F | `<mac-address>` | WiFi | class-B |
| boromir | `.93` (reserved) | HA198QW5 | TB-X606F | `<mac-address>` | WiFi | class-B |
| faramir | `.22` (reserved) | MJ0EW27F | TB-X606F | `<mac-address>` | WiFi | class-B, canary |

Full per-component manuals and design doctrine are in [[Tablet-Cluster-INDEX]].

### Basement Desktops: `rigby` and `mordecai`

**Current State:**
*   **`rigby`**: Acts as an L2 bridge. Connects the LAN switch (upstairs) to `mordecai` via a point-to-point PCIe link.
*   **`mordecai`**: Sits behind `rigby`. Reaches the LAN exclusively through `rigby`. IP: `<lan-address>`.

**Network Topology & Configuration:**
*   `rigby` bridges two wired ports (`eno1` + `enp1s0`) into `br-lan`.
*   **STP is enabled** on `br-lan` to prevent LAN-wide broadcast storms if both ports connect to the same switch.
*   `rigby` operates as a pure L2 pass-through: `ip_forward=0`, no DHCP server, no NAT.
*   `mordecai` obtains IP, gateway, and DNS from the RS300 router. It appears in the router’s device list as a wired host at `.110`.
*   `rigby` also manages a separate bridge (`br0`, `10.55.0.1/24`) for the tablets' USB NICs (`enx*`), one per cabled tablet.

**Access & Identification:**
*   **Do not pin `rigby`'s IP address.** It has two ports (LAN: `.82`, PCIe-side: `.79`), but neither assignment is fixed to a specific physical cable location. Use `ssh rigby` (via `Lab\bin\sshto.py`) to connect; it resolves to whichever interface answers.
*   `enp1s0` holds no IP address. It is a bridge port only.
*   **WoL for `mordecai`:** Works from suspend only, not from powered-off. A powered-off NIC has no carrier, preventing Ethernet frame transmission. Check `/sys/class/net/enp1s0/carrier` on `rigby`; `0` means physical power-on is required. Use `etherwake` on `rigby`; `wakeonlan` fails because it requires an IP/route.

**DHCP Lease Behavior:**
*   Changing the DHCP client identifier (e.g., switching from default DUID+IAID to MAC-based) causes a new lease to be issued. Perform this change once, then reserve the address. Do not iterate on DHCP identity changes without re-reserving.

**Hardware Specifications:**

| Feature | `rigby` (HP chassis) | `mordecai` (Asus chassis) |
|---|---|---|
| **OS** | Ubuntu 24.04.4 LTS | Ubuntu 24.04.4 LTS |
| **CPU/RAM** | i7-3770, 4c/8t · 24 GB | i5-3470, 4c/4t · 8 GB |
| **GPU** | **GTX 1060 6 GB** (GP106, Pascal) in the x16 CPU slot on the owner's new riser (2026-10-26) → link **x16**, Gen 3 capable (`nvidia-smi` gen 3 / width 16; sysfs reads 2.5 GT/s while idle, the normal downclock), no AER errors; `nvidia0` / Vulkan0. **GT 730 2 GB** (GK208B, Kepler) in the chipset slot → **x4** (card max Gen 2 x8; the slot exposes no AER counters), `nvidia1` / Vulkan1. Both on driver 470.256.02. Probed live 2026-10-26 after the riser install ([[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]] § "Two cards on rigby"). ⚠ Tolkien's container lists both `/dev/nvidia0` and `/dev/nvidia1`: with either card out, `tablet-foundry` refuses to start ("error gathering device information … /dev/nvidia1") | not probed (host asleep) |
| **⚠ 2026-09 surgery** | rigby and mordecai SWAPPED CPUs and re-RAM'd: rigby took the i7-3770 + 24 GB; mordecai took the i5-3470 and dropped to 8 GB. Verified 2026-09-13 (rigby probed live; mordecai per the owner, host asleep). | ← was i5-3470/12 GB and i7-3770/16 GB pre-surgery |
| **MAC (LAN)** | `<mac-address>` (`eno1`) | `<mac-address>` |
| **MAC (PCIe)** | **removed 2026-10-13** — the Intel 82574L add-in NIC (`<mac-address>`) came out to make room for the 1060; rigby runs on `eno1` only (netplan `01-wired-any-slot.yaml` matches any wired port) | was "receives via `rigby`" — check mordecai's path |

**Performance Benchmarks:**
*   Measured using netlib HPL 2.3 / OpenMPI 4.1.6 / serial OpenBLAS / NB=192.
*   **Individual Throughput:**
    *   `mordecai`: 100.97 – 101.60 GFLOP/s (HPL); 16.1 GB/s (STREAM Triad)
    *   `rigby`: 89.91 – 91.13 GFLOP/s (HPL); 12.0 GB/s (STREAM Triad)
*   **Distributed MPI Performance:**
    *   Combined: ~192 GFLOP/s when independent.
    *   Single MPI job across both: ~160 GFLOP/s (83% retention of summed throughput).
    *   Link bandwidth: 941 Mbit/s symmetric wired link.
    *   Note: The "never one MPI job" rule for Fellowship tablets does not apply here due to RAM capacity and problem size (`N=41088`).

**Operational Notes:**
*   **Time Sync:** `rigby` runs UTC. Log timestamps will be ~5 hours ahead of Softserve’s local time. Convert timestamps before correlating logs across boxes.
*   **Identity:** Names are authoritative from the router. `Worktop`/.57 and `pinky` are retired aliases for `rigby`.
*   **RDP Behavior:** `mordecai` listens on port 3389 (xrdp) but does not complete X.224 negotiation. This is expected Linux behavior; do not re-investigate as an RDP fault.

### Static IP assignments and fixed-role devices

The following addresses are occupied by persistent hardware or reserved for specific system roles. These addresses are not available for general DHCP assignment.

| IP Suffix | Device / Role | MAC Address / Details |
|---|---|---|
| `.70` | Vizio TV (Chromecast) | `<mac-address>`. Ports 7000 (AirPlay), 8009 (Cast), 8443. Generates high DNS query volume (~16k/3 days). Treated as static; holds `.70` persistently across reboots and moves. |
| `.116` | Lorex NVR (N844A8) | `<mac-address>`. Wired. Hosts the camera system. |
| `.212` | Herpstat 4 | Reptile thermostat controller. |
| `.31` | MinuteMan | Reserved. |
| `.13` / `.17` | MORPHEUS | Reserved. |
| `.2` | VPN-PC | Reserved. |
| `.209` | ShawnsVM | Reserved. |

**Design Doctrine:** Addresses listed above are treated as fixed allocations. Do not assign these IPs to other clients. The Vizio TV (`.70`) does not have a DHCP reservation but exhibits stable address retention; treat it as static for network planning purposes.

## SundaeStackarr Network Adapters

### Interface Configuration

| Interface | vSwitch | MAC | IP | Purpose |
|-----------|---------|-----|----|---------|
| eth0 | USB 1Gb (RemoteVM main) | <mac-address> | <sundae-address> (DHCP) | Arr stack, general traffic, container-VPN egress |
| eth1 | Motherboard 1Gb | <mac-address> | none (dumb pipe) | Pi-hole macvlan parent — MAC spoofing must be **ON** |

### Pi-hole Macvlan Passthrough Design

eth1 functions as a bare pipe for the Pi-hole Docker macvlan network. The host OS assigns no IP address to eth1.

The Pi-hole container binds <pihole-address> directly to this physical NIC, bypassing the host's iptables/netfilter stack entirely.

### Hyper-V Configuration Requirements

When creating or re-creating the "SundaeStackarr" VM or the "PiHole" vNIC, MAC Address Spoofing must be enabled on the adapter.

```powershell
Set-VMNetworkAdapter -VMName "SundaeStackarr" -Name "PiHole" -MacAddressSpoofing On
```

### Docker Networks (on SundaeStackarr)

| Network | Driver | Subnet / parent | Members |
|---------|--------|-----------------|---------|
| **arrnet** | bridge | `172.18.0.0/16` (gateway `172.18.0.1`) | All services except Pi-hole |
| **piholenet** | macvlan on eth1 | <lan-subnet>/24 (container IP <pihole-address>) | Pi-hole only |

**Pi-hole network isolation design:** Pi-hole operates via macvlan on the physical NIC, assigning it a distinct MAC/IP address and bypassing the host kernel's iptables. Due to macvlan routing rules, direct communication between the host (<sundae-address>) and the container IP (<pihole-address>) is blocked; traffic must route through eth0, the physical switch, and back to Pi-hole.

**Pi-hole storage constraint:** Do not mount Pi-hole configuration (`/opt/arrstack/pihole/etc-pihole/`) over SMB/CIFS. SQLite WAL locking fails over CIFS mounts. Configuration files reside on local disk.

## Virtual Machine Inventory (Softserve)

The Hyper-V host "Softserve" hosts the following virtual machines. Configuration paths and disk states are authoritative.

| VM Name | Guest OS | Startup Action | VHDX Path | Size | Status & Role |
|---------|----------|----------------|-----------|------|---------------|
| SundaeStackarr (Ubuntu) — boot disk | Ubuntu | AutoStart | `E:\Storage For VM Configs and HDDs\SundaeStackarr\Virtual Hard Disks\SundaeStackarr-boot.vhdx` (SCSI 0:0) | 80 GB virtual, ~1 GB file | Running. EFI + `/boot`. Main arr + Pi-hole VM. |
| SundaeStackarr (Ubuntu) — data disk, same VM | Ubuntu | attached to SundaeStackarr | `E:\Storage For VM Configs and HDDs\SundaeStackarr\Virtual Hard Disks\SundaeStackarr-data.vhdx` (SCSI 0:3) | 200 GB virtual, ~155 GB file | The whole root LVM (`ubuntu-vg/ubuntu-lv`, 157 GB, 43 GB free in the VG to grow into). Layout since 2026-10-10. |
| HomeAssistantOS | Home Assistant OS | StartIfRunning | `E:\Storage For VM Configs and HDDs\HomeAssistantOS\Virtual Hard Disks\haos_ova-17.2.vhdx` | 6.2 GB | Off. Home Assistant instance. |
| Raspi VM | Linux (Pi) | retired, VM unregistered | `Z:\Archives\vm-disks\Raspi VM.vhdx` (archived 2026-10-10, sha256-verified) | 30.3 GB | Off. Retired. Pi-hole functionality migrated to Docker macvlan. |

> **Hyper-V storage root (2026-10-10):** every VM's configuration, checkpoints, smart paging and disks live under `E:\Storage For VM Configs and HDDs\<VM>\`; `Get-VMHost` defaults (`VirtualHardDiskPath`, `VirtualMachinePath`) point there, so a new VM lands on E:, not C:. Nothing of Hyper-V remains on C:.
