# GPU Inventory and Placement Policy

## Hardware Inventory
- **Mothership**: RX 7900 XTX 24GB (AMD, Windows, ROCm). Serves as the home for the 27B model. Wake-on-LAN on demand.
- **BigHonker**: RTX 3070 8GB (CUDA). Serves as the home for the 8B brain. VRAM is fully occupied by the brain when active.
- **Softserve**: RTX 3070 Ti 8GB. Plex host.

## Placement Doctrine
Mothership is the designated placement for image generation. AMD ROCm 7.1.1 provides official ComfyUI support on Windows, including one-click installation and explicit support for the RX 7900 XTX. Custom nodes dependent on xformers are experimental.

Softserve supports ordinary workloads: captioning, CPU tools, and hosting a local factory at `E:\foundry` (6 caption orders, `SS Foundry Runner`).

## Softserve Restrictions
Crash-class testing is prohibited on Softserve. This includes clock soaks and any operation that risks hanging the system. Softserve functions as the Plex server, the Hyper-V host, and the machine hosting the active Claude session.

See [[BigHonker-3070-DPC-Watchdog-Investigation]] §Softserve for specific constraint details.

## VRAM Coexistence Policy
The 27B model (17GB) and image generation models (SDXL ~8–10GB; Flux.1-dev fp8 ~16GB) do not fit in shared VRAM on Mothership. Apply the swap-on-demand pattern: load the image generation model only when the 27B is unloaded.

A gaming-busy guard must be checked before any image load, identical to the LLM guard. Generation bursts are short; energy monitoring covers awareness.

---
### P0 — Install validation and baseline performance

This tier is complete. See **Runbook** §*ComfyUI Image Generation on Mothership* for operational procedures.

**Hardware detection:** `torch 2.9.1+rocm7.2.1` detects AMD Radeon RX 7900 XTX.

**Baseline generation times (SDXL):**
- Cold start: 35s
- Warm start: 11s
- With Qwen3:14B resident (coexistence validated): 16s

**Flux dev fp8:**
- Cold start: 205s
- Output quality: legible in-image text

**System state:**
- Service: *ComfyUI Server* task active
- Network: LAN firewall rules live

### P1 — Core orchestrator generation

The core orchestrator implements the following design constraints:

* **Client interface:** `comfy_client.py` provides templated workflow submission, polling, and fetching with fail-soft behavior.
* **VRAM choreography:**
    * Gaming gate enforces `IMG_MIN_FREE_MB=7000`.
    * LLMs are unloaded for Flux execution.
    * Resources are freed after completion.
* **Tool interface:** The `generate_image` tool is gated, quota-enforced, and audited under the IMG_GEN line.
* **Endpoint security:**
    * `/images/` endpoint requires authentication.
    * Render whitelist restricts inline `<img>` tags to same-origin `images/*.png` files only.
* **Quotas:** Per-user daily quotas are enforced; test users are exempt.
