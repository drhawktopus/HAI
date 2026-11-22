# Mothership — RX 7900 XTX GPU host: the 27B brain and all P20 renders/training

Thin orientation hub for the **Mothership** machine: identity, what runs here, and where the depth
lives. Facts are single-homed in the linked pages; the canonical inventory row is in
[[Network-Homelab-Machine-Inventory-Virtualization]].

## Identity

- **Role:** the owner's gaming/main PC and the lab's heavyweight GPU host — the 27B model plus every P20
  image render and LoRA/QLoRA training run.
- **LAN IP:** <mothership-address>
- **OS / GPU:** Windows 11 · AMD Radeon RX 7900 XTX 24 GB (ROCm)
- **Access:** `ssh mothership`, RDP 3389, or WinRM.

## What runs here

- **qwen3.6:27b heavyweight brain** — needs the card to itself (~19 GB); the deep-reasoning tier of
  the fleet → [[HAI-Core-Architecture-API-Contracts]], [[HAI-Control-Surface]]
- **All P20 renders + LoRA/QLoRA training** — the primary factory → [[P20-MULTI-FACTORY-FLEET]], [[Flux2-32B-QLoRA-AMD-ROCm-Windows]]
- **ComfyUI image generation** (both instances) → [[RUNBOOK-ComfyUI-Image-Generation]]
- **Foundry health, stall-rescue & telemetry daemons** → [[RUNBOOK-Foundry-and-Rescue-Daemons]]
- **GPU police** (arrest/stand-down) → [[RUNBOOK-GPU-Police-Operation]]
- **Compute/training order intake** → [[RUNBOOK-Compute-and-Training-Order-Submission]]

## Notes

- VRAM residency is load-bearing here — one big model at a time unless measured to coexist; the fleet
  residency map is in [[RUNBOOK]].
- The AMD ROCm/Windows driver work that made this card usable → [[Flux2-32B-QLoRA-AMD-ROCm-Windows]], [[Driver-RootCause-Investigation-Plan]].
- **No WSL, on purpose (the owner's decision).** the owner, 2026-07-15: *"I think I used to have wsl on mothership but it caused some gaming
  bugginess so I uninstalled it"*, and *"I'm OK with doing WSL if that's really going to be our best option"*. So WSL goes back only
  when a need is demonstrated and nothing native will do, and it is his call. Hyper-V stays on and causes no trouble (same day).
  Checked 2026-11-07: `wsl --status` says not installed. Other boxes: BigHonker has the WSL feature with no distro, and installing
  one there is also his call ([[HOST-Basement-Desktops-Capacity-Determination-Pending-Dec]] § C1b); Softserve runs WSL 2 with Ubuntu.

## See also

- [[Network-Homelab-Machine-Inventory-Virtualization]] · [[P20-MULTI-FACTORY-FLEET]] · [[Machine-BigHonker]]
