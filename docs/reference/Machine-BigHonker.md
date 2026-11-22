# BigHonker — primary 8B brain host, video encode, 3070 factory node

Thin orientation hub for the **BigHonker** machine: identity, what runs here, and where the depth
lives. Facts are single-homed in the linked pages; the canonical inventory row is in
[[Network-Homelab-Machine-Inventory-Virtualization]].

## Identity

- **Role:** Primary 8B brain host (the everyday quick-chat tier), video encode, and a 3070 factory
  node. **Not** the deep-reasoning brain — that is Mothership's 27B.
- **LAN IP:** <bighonker-address> · **WoL MAC:** `<mac-address>`
- **OS / GPU:** Windows · NVIDIA RTX 3070 8 GB
- **Access:** `ssh bighonker` (`<user>@<bighonker-address>`, key `~/.ssh/bighonker_ed25519` — **not** the
  mothership key). Sleeps at ~30 min idle; probe the service, never conclude "asleep/off" from a
  failed ping.

## What runs here

- **The 8 GB chat tier's home box** — WHICH model serves a turn is the menu's per-request weighing, not a standing brain
  (the rows live in `Datasets\Project34-Menu\policy.json`; the operator default and windows are in
  [[RUNBOOK-Ollama-Host-Operations]]) → [[34-Phase-7-Placement-Policy-Design]], [[HAI-Control-Surface]]
- **A shared desktop**: sometimes the owner is logged in, sometimes Avery, sometimes nobody (no auto-logon). A logged-in
  session — even locked — keeps the Windows desktop compositor holding ~1 GB of the card (measured 2026-10-25:
  `dwm` ~1 GB, Edge 13 MB, Armoury Crate 10 MB; ~330 MB with nobody logged in, 2026-10-22), so a chat window opens at
  the largest MEASURED size that fits beside it (`model_fit.fit_ctx`) and the full window when it fits; GPU use no lab
  order explains (gaming, Lightroom editing) makes the box unavailable until it clears (`scheduler.user_on_card`).
- **The split 35B-A3B** (`qwen3.6:35b-a3b-ud-q3kxl-mtp`) runs here ON PURPOSE, partly in RAM (the owner 2026-10-17 *"Offload
  allowed."*, `offload_ok` in the menu's `boxes.json` → `menu_fill.OFFLOAD_OK` → `policy.json offload_ok`): ~6.8 GB on the 3070, ~11.5 GB in system RAM, window
  65,536; its chat windows declare `--offload-ok` (since 2026-10-25). It is the 8 GB tier's best home row, so the
  weighing sends that tier's turns to it. A window opens only when its measured RAM side (~10.7 GB) fits the box's
  free RAM above a 3 GB reserve — read from the box's own minute report (BigHonker runs no Glances; that was never a
  decision, just not installed).
- **Video encode** (CorridorKey clips) → [[CorridorKey-Ops]]
- **P20 factory node** — captioning and compute orders → [[P20-MULTI-FACTORY-FLEET]], [[RUNBOOK-Compute-and-Training-Order-Submission]]
- **Foundry health daemon + GPU police** → [[RUNBOOK-Foundry-and-Rescue-Daemons]], [[RUNBOOK-GPU-Police-Operation]]

## Notes

- The 3070 is healthy — its DPC-watchdog crashes were the **factory OC**, not the card. Lock it to
  1500 MHz. Full account: [[BigHonker-3070-DPC-Watchdog-Investigation]].
- 8 GB card = one worker per model (a real VRAM law from the small cards); a second parallel slot's
  KV cache does not fit and spills to CPU.

## See also

- [[Network-Homelab-Machine-Inventory-Virtualization]] · [[BigHonker-3070-DPC-Watchdog-Investigation]] · [[Machine-Mothership]]
