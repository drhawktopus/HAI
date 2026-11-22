# Basement Desktops — Host Inventory & Execution Status



## Host Inventory

| Attribute | `rigby` (formerly `Worktop`) | `mordecai` |
|---|---|---|
| Identity | HP / Intel i5-3470, 12 GB RAM | Asus / Intel i7-3770, 16 GB RAM |
| Network | Wired. Motherboard NIC: `<mac-address>`. PCIe NIC: `<mac-address>`. | Wired. NIC: `<mac-address>`. |
| Addressing | DHCP reserved `.82` (motherboard) / `.79` (PCIe). **Access via hostname only** (`ssh rigby`). Do not pin IP addresses; see [[RUNBOOK]] §"Can't reach rigby?". | DHCP reserved `<lan-address>`. |
| OS | Ubuntu 24.04.4 LTS | Ubuntu 24.04.4 LTS |

**Design doctrine:** Both hosts run Linux. Windows-era configurations (Hyper-V, workgroups, `powercfg`, `schtasks`) are superseded and do not apply. If a port scan returns no response on ICMP, the host is still up if other services respond; do not treat failed ping as downtime.

## Execution Status and State

This document tracks the application of onboarding procedures to `mordecai` and `rigby`. Current state is defined by the following executed or closed actions.

### Executed Sections

- **§A–§C:** ssh keys installed; benchmarks completed; worker-count curve established; co-location probe performed (§C5 verdict applied).
- **§C1b:** Closed as WON'T-DO. No additional installation on `BigHonker` for comparison scale.
- **§E (Naming):** Executed 2026-08-16. `StreamBox` renamed to **`mordecai`**; `pinky` renamed to **`rigby`**.

### Rigby: Hardware and Network Configuration

#### Memory
- **Capacity:** 16 GB total (four slots populated with 4 GB each). Usable memory: ~15 GiB. All modules rated at 1333 MT/s, matching the board’s ceiling. The swap increased capacity without increasing speed.
- **Modules:** Mixed parts (`KP4T2F-PSB`, `HMT451U6AFR8C-PB`, `HMT351U6CFR8C-H9`). One module is dual-rank.
- **Health:** No EDAC or mcelog errors since boot. HP firmware reports generic bank names only; configured speed field is blank in firmware.

#### Network Architecture
- **Topology:** Point-to-point connection to `mordecai` via PCIe card (`enp1s0`). This interface holds **no IP address** and is enslaved to bridge `br-lan`.
- **Primary Interface:** Onboard Ethernet (`eno1`) carries LAN traffic. Enslaved to bridge `br-lan`.
- **Addressing:** Only the bridge `br-lan` holds an IP address. Do not pin a specific static IP for `rigby`. Use host-based resolution: `ssh rigby` or `Lab\bin\sshto.py`.
- **Interface Naming Policy:** Network configuration uses pattern matching (`eno*`, `enp*`, `ens*`) in `01-wired-any-slot.yaml` to remain stable across PCIe slot changes. USB-attached tablets (`enx*`) are explicitly excluded from DHCP.
- **Cloud-init Network:** Disabled via `99-disable-network-config.cfg` to prevent regeneration of network configs on reboot, which previously caused connectivity loss when interface names changed.
- **Legacy Configs:** Preserved at `/etc/netplan-backup-20260817/` for reference only.

#### Connectivity Troubleshooting
- See [[RUNBOOK]] §"Can't reach rigby?" and [[Network-Infrastructure]] for current access procedures.
- **ARP Flux Caveat:** On multi-homed Linux boxes, ARP responses may not distinguish between interfaces clearly. MAC-keyed lookups may be unreliable.

### Pending Actions

- **DNS + DHCP Reservations:** Outside the two-box scope. Requires approval from the owner. Note: `rigby`’s address has changed seven times due to DHCP identity changes and cabling shifts; stable resolution is critical before proceeding.
- **Host `benson`:** Not yet built.

### Undecided Items

- **CPU Swap:** If CPUs are swapped between `mordecai` and `rigby`, all benchmark data must be re-run. Benchmark tables key hostname to CPU; the hostname remains tied to the chassis.
