# SundaeStackarr — the media Docker stack, Pi-hole master, and HAI orchestrator

Thin orientation hub for the **SundaeStackarr** machine: identity, what runs here, and where the
depth lives. Facts are single-homed in the linked pages; the canonical inventory row and network
adapters are in [[Network-Homelab-Machine-Inventory-Virtualization]].

## Identity

- **Role:** Ubuntu VM on Softserve (Hyper-V) hosting the arr/media Docker stack, the master Pi-hole,
  and the Home AI (HAI) orchestrator.
- **LAN IP:** <sundae-address> (eth0) · **Pi-hole macvlan:** <pihole-address> (eth1) · **Tailscale:** <tailscale-address>
- **OS:** Ubuntu 26.04
- **Access:** `ssh sundae`. Docker stack lives in `/opt/arrstack` (`docker compose up -d`).

## CPU containment — why Sundae's load cannot starve Plex (read live 2026-10-10)

Hyper-V on Softserve gives **SundaeStackarr 6 vCPUs, relative weight 80** (the default and HomeAssistantOS are 100)
and a 20 % reservation, limit 100 % of those 6 (`Get-VMProcessor -VMName SundaeStackarr`, via `ssh softserve`). Plex
runs natively on Softserve, not in a container or VM, so heavy work inside Sundae (Docker builds, benchmark task
containers) can use at most 6 of Softserve's cores and loses CPU scheduling to the host under contention. the owner
2026-10-10: *"Sunday already has limits on how much CPU can use so that's why I'm telling you I'm not worried about
ITS contention with Plex."* Softserve is the lab's hub: lab-critical and multi-box things live here, usually in this
VM ([[Machine-Softserve]]).

## Notes

- Deploying the orchestrator is not a plain `restart` (the code is baked into the image) — follow
  [[RUNBOOK-Orchestrator-Deploy-Procedure]].

## See also

- [[Network-Homelab-Machine-Inventory-Virtualization]] · [[Media-INDEX]] · [[HAI-INDEX]] · [[Machine-Softserve]]
