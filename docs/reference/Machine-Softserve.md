# Softserve — Hyper-V host, Plex server, Tailscale subnet router, GPU-factory node

Thin orientation hub for the **Softserve** machine: identity, what runs here, and where the depth
lives. Facts are single-homed in the linked pages; the canonical inventory row (MACs, Tailscale IP,
VMs, drives) is in [[Network-Homelab-Machine-Inventory-Virtualization]].

## Identity

- **Role:** Hyper-V host (runs the SundaeStackarr and HomeAssistantOS VMs), Plex server, Tailscale
  subnet router (→ <lan-subnet>/24), CPU/caption factory node, and the host these operator sessions
  run on.
- **LAN IP:** <softserve-address> · **Tailscale:** <tailscale-address>
- **OS:** Windows 11 Pro · **Workgroup:** MOSSMATRIX (not domain-joined)
- **Access:** RDP / console (auto-login local `SOFTSERVE\Administrator`); the operator sessions live
  here directly.

## What runs here

- **Hyper-V host** — the only two VMs are `SundaeStackarr` and `HomeAssistantOS` → [[Machine-SundaeStackarr]], Machine-HomeAssistant
- **Plex Media Server** (32400) — console-session tray app, kept alive by PlexWatchdog; holds CPU
  priority over agent processes. Operator detail in [[RUNBOOK]]; measured usage (when it transcodes, how
  many at once, hardware vs software) in [[Plex-Usage-Profile-Softserve]].
- **Local model archive** on the HDD array — big GGUFs already downloaded (Nemotron-3-Super, Qwen3.8-Flash-Next,
  GLM-5.3-Flash, Qwen3.5-122B …), not installed anywhere → [[Local-Model-Archive-Z]]
- **Tailscale subnet router** — advertises the LAN to the tailnet → [[Network-External-Connectivity-Remote-Access]]
- **P20 factory node** — captioning and compute orders, foundry root `E:\foundry` → [[P20-MULTI-FACTORY-FLEET]], [[RUNBOOK-Compute-and-Training-Order-Submission]]
- **GPU police + foundry/rescue daemons** → [[RUNBOOK-GPU-Police-Operation]], [[RUNBOOK-Foundry-and-Rescue-Daemons]]
- **Camera-detector build/deploy + Lorex NVR tooling** → [[RUNBOOK-Tablet-Detector-Operation]], [[RUNBOOK-Lorex-NVR-Operation]]
- **Gateway to the basement desktops** rigby/mordecai and the Fellowship tablets → [[HOST-ONBOARDING-Basement-Desktops]], [[Tablet-Cluster-INDEX]]

## See also

- [[Network-Homelab-Machine-Inventory-Virtualization]] — canonical inventory tables
- [[Network-INDEX]] · [[RUNBOOK]] · [[HAI-INDEX]]
