# HANDOFF → Mothership session: what changed for the multi-factory fleet (2026-08-02)

The Mothership fleet supports multiple factories on BigHonker. All factories process identical orders and operate under the same workflow rules. The system uses per-factory homepage tiles and host-prefixed Discord alerts for status notification.

## Deployed Components

The following components are fully deployed and verified:
- Second factory instance on BigHonker
- Per-factory homepage tile integration
- Host-prefixed Discord alert routing

## Shared Files Modified

Three shared files were modified to support the multi-factory configuration. `p20_foundry.py` is one of these files; review §1 before editing it.

## Pending Mothership Changes

Four items require action on Mothership's live files. See §5 "WHAT MOTHERSHIP NEEDS FROM YOU" in `Reference/HANDOFF-mothership-factory-fleet-changes.md` for specific tasks.

## 1. ⚠ READ FIRST — `p20_foundry.py` is a TWO-HOST file

BigHonker executes **the same `p20_foundry.py`**. The port functions by honoring an existing lever: `foundry_paths.FOUNDRY_ROOT`. This environment variable allows the code to run on either factory depending on its setting.

**Operational Constraint:** Because this mechanism was written for blue/green deployment, it is now active across a second box. Hardcoding paths (e.g., `F:\foundry\...`) or assuming `FLEET_HOST == "mothership"` causes silent failure of the secondary factory. Such hardcoded logic would cause the second factory to write its **lock**, **heartbeat** and **STOP flags into Mothership's tree**. This violates the rule prohibiting a two-order-takers-one-lane collision.

## Path resolution policy
Foundry-owned paths are resolved via properties prefixed with `P.` (`P.STATE_DIR`, `P.BIN`, `P.CACHE_DIR`, `P.ETC`, `P QUEUE`, `P.JOB_ARCHIVE`, `P.TELEMETRY_DIR`) rather than hard-coded literals (e.g., `F:\foundry\...`). Literal paths defeat the `FOUNDRY_ROOT` abstraction.

## Fleet host assignment policy
The fleet host variable is sourced from the environment:
```python
FLEET_HOST = os.environ.get("FOUNDRY_FLEET_HOST", "mothership")
```
A static value claiming another box's card within the fleet state causes the scheduler to assign work to a GPU believed free on that incorrect host.

## External tool override policy
The following environment variables allow overriding external tool paths without requiring `ai-toolkit` or ComfyUI locally. This is required for hosts capable of **importing** modules to run caption orders but lacking the full toolkit:
- `FOUNDRY_PY`
- `FOUNDRY_LORAS`
- `FOUNDRY_COMFY`
- `FOUNDRY_COMFY_LAUNCH`

## Capability declaration policy
`FACTORY_CAPABILITIES` is declared per-host via `_HOST_CAPS_OMIT`. The host BigHonker declares 19 capabilities and explicitly omits claims for `train`/`probe`. An order requiring these capabilities on this host is HELD, with the specific capability named in the hold message.

## Alert routing policy
Alert methods (`notify()`, `notify_desk()`) include a host prefix derived from `_host_tag()` to ensure alerts name the factory FIRST (see §4).

**Default configuration:** Mothership behavior remains byte-for-byte identical unless environment variables are modified.

## Orchestrator (Sundae) — Single-writer store isolation

Orchestrator stores must remain single-writer to prevent data corruption in multi-factory environments. When multiple factories exist, any store written by exactly one process for its whole life becomes a concurrency hazard if isolation is not enforced.

### `_gpu_status.json` (`p20_queue.py`)

BigHonker workers post `state: free` every 30 s. Without isolation, this overwrites Mothership's card status via last-writer-wins semantics.

### `rescue-state.json` (`app.py`)

BigHonker's health daemon writes daemon verdicts. Without isolation, this overwrites Mothership's daemon verdict.

**Failure mode:** If Mothership is mid-run, the homepage displays an idle card during a live job. This error is silent and does not generate exceptions.

### What changed (deployed, hash-verified in-container)

*   `p20_queue.set_gpu_status()` — writes `_gpu_status_<host>.json` when the payload carries `host`. A host-stamped worker writes **only** its own file; a pre-host worker still writes the legacy `_gpu_status.json`.
*   `p20_queue.gpu_status(host=...)` — new optional argument. Omitted = historical behaviour, so every existing caller is untouched.
*   `p20_queue.gpu_status_hosts()` — returns `{host: (status, age)}` for the fleet view.
*   `app.py` `_rescue_state_path()` / `_rescue_all()` — same treatment as daemon state.
*   `app.py` `/p20/training` endpoint now requests **`host="mothership"`** with a legacy fallback. Without that, the response shows whichever factory posted most recently.

### New endpoints

| Method | Endpoint |
|---|
| `GET` `/p20/factories` | Fleet view response containing: `{factories: {host: {card, run, health, summary}}}`.
| `GET` `/p20/factory/{host}` | Single-object factory representation (flat) intended for homepage tile display.

## Homepage
The lab interface exposes two service tiles in `services.yaml`: **Foundry - Mothership** (backup: `services.yaml.bak-factorytiles-*`) pointing at `/p20/factory/<host>` and **Foundry - BigHonker**, also pointing at its own `/p20/factory/<host>`. Resolutions are verified from inside the homepage container. The tiles for **P20 Training** and **Stall Rescue** remain in configuration.

## 3. BigHonker's factory, for reference

Same three long-lived processes as Mothership, same contract, host-appropriate duties.

### 3a. BigHonker's daemon duties
BigHonker's daemon performs clock-lock verification (the highest-value duty), GPU-lost detection, frozen-telemetry detection (identical readings indicate a dead card), and caption-progress stall monitoring.
Mothership hunts an AMD wedge (220-250 W at >2900 MHz, 0-10% memload) with rescue bursts. BigHonker never reboots the node or kills the order taker; a lost GPU requires an operator decision and reboot.

### Render capability detection: automatic via ComfyUI launcher presence

The system determines whether a host can perform rendering by checking for the existence of the ComfyUI launcher binary. It does not hardcode specific hosts into the worker configuration.

*   **Mechanism**: The `WORKER_CAN_RENDER` variable defaults to the boolean result of "does the ComfyUI launcher exist on this box?". This behavior can be overridden via environment variables.
*   **Design doctrine**: New nodes automatically claim render jobs upon installation of ComfyUI. No code changes or manual configuration updates are required when a new node is added to the fleet.

## 4. Discord alerts now name the factory FIRST
Format for all operator notifications (excluding image delivery) is defined in `_host_tag()` to ensure consistency across components:
```
[mothership] [foundry:<order>] <message>     <- notify()
[mothership] [foundry:desk] <message>        <- notify_desk()
[bighonker]  [health] ALERT <message>       <- health daemon
```
The image delivery function `notify_file` deliberately places the host identifier on the **second line** rather than leading it. This design choice ensures machine identifiers do not obscure the headline in mobile view.
Health-daemon alerts are **edge-triggered**: they fire on state transitions and report recovery exactly once upon exit from an alerting condition.

## Mothership Runtime Dependencies and Open Defects

Mothership requires the following configurations to function correctly. Deviations result in reduced telemetry precision or health reporting gaps.

### Required Software Updates

*   **`p20_render_worker.py`**: The worker script must include `host` in every `post_gpu_status` payload. Without this update, Mothership relies on the legacy fallback for card-to-tile rendering. This fallback functions but provides reduced precision; specifically, `/p20/factory/mothership` displays `age_s: null`.
*   **`p20_foundry.py`**: To enable host-prefixed DMs on Mothership, update `p20_foundry.py`. Defaults remain unchanged. When applying updates, re-baseline from the local live file to preserve recent edits; do not overwrite the live file with external copies without merging.

### Current Defects and Alignment Gaps

*   **Daemon Health Reporting**: `/p20/factory/*` reports `health: unknown` for both hosts. The Mothership stall daemon posts to `/fleet/rescue-state` using a payload shape that does not populate `health.state` in the current view. Both daemons must align on a single payload schema to resolve this. The Mothership daemon has not yet been updated to match the expected schema.
*   **Image Staging Protocol**: Operators must use `caption_stage_from` for image staging. Manually staging images into `var\work` violates patrol rules and triggers TRESPASS flags from BigHonker's patrol.

---

## GPU Fleet: Hardware Limits, Monitoring Conformance, and Throughput Specs

### BigHonker (RTX 3070) Stability and Configuration

BigHonker’s RTX 3070 is defective at factory-overclocked settings. It boosts to 1965–1995 MHz (reference ~1725 MHz) but cannot sustain compute load, failing in 11 of 11 runs within 2–5 minutes. The card remains stable when locked at 1500 MHz, passing a 3638 s soak test and a 487-caption production run with 0 failures.

**Design Doctrine: Persistence is not guaranteed.**
- GPU configuration changes via `-lgc` do not survive reboot.
- Driver installation resets GPU configuration.
- A boot task and a daemon duty must enforce the locked clock state. Every deployment of BigHonker must verify these guards are active; absence constitutes a defect.

**Throughput Spec (Q4_K_M):**
BigHonker runs JoyCaption Q4_K_M model (5.8 GB, fits 8 GB VRAM, 100% resident) at 2.85 s/img over a 493-image run with 0 failures.

*Reference: [[BigHonker-3070-DPC-Watchdog-Investigation]]*

### Mothership Throughput Spec (Q8_0)

Mothership runs JoyCaption Q8_0 model (8.82 GB, exceeds 8 GB VRAM capacity) at ~3.5 s/img.

*Reference: [[BigHonker-3070-DPC-Watchdog-Investigation]]*

### NVML Health Check Conformance Rules

Standard health checks must not rely on plausible static values, as a wedged GPU’s zombie NVML handle serves its last reading indefinitely (e.g., 248.94 W / 99% util / 68 °C).

**Design Doctrine: Change detection and timeout enforcement.**
1. **Detect Stasis:** Health checks must verify CHANGE across samples, not just plausible values. Identical power readings to two decimal places across samples indicate a dead/wedged card.
2. **Enforce Timeouts:** `nvidia-smi -q` hangs rather than errors on a wedged card. Any script shelling out to `nvidia-smi` must include a timeout; failure to implement this causes the checker itself to wedge.

*Reference: [[BigHonker-3070-DPC-Watchdog-Investigation]]*
