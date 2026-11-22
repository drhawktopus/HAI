# Mothership GPU Wedge Root Cause

## Root Cause Statement
The root cause of Mothership GPU wedges is **cross-context generation collision** during big-latent renders. This collision poisons a compute context (MES class). The resulting degradation is not cumulative, nor caused by kernel age, churn, or VRAM traffic.

## Conformance and Evidence
- **Reproducibility:** The fault is reproducible on demand. Two collision cycles produce two wedges in ~90s. 125 collision-free harness cycles complete cleanly.
- **Defect Signature:** A poisoned context exhibits "born-degraded" behavior (duds). Fresh spawns after a kill show TFLOP/s drops (76.6/76.3), confirming the fault is context-held, not hardware-permanent.
- **Verification Instrument:** Full evidence, repro recipe, and mitigation plan reside in [[Driver-RootCause-Phase0-Exhibits]] (§⭐⭐ + ✅ sections).

## Current Mitigation State
- **Primary Fix:** Consolidation remains on backlog.
- **Interim Guard:** The orchestrator guards `generate_image` calls to defer during renders.
- **Runtime Pin:** A/B runtime pinning is optional.
- **Upstream Reporting:** Phase 3 upstream report pending (repro in hand).

## Related Documentation
- [[Flux2-32B-QLoRA-AMD-ROCm-Windows]] §wedge science
- `PROJECT-20-ROADMAP`
- Research chip task_43ea7ad7

## GPU Stall Root Cause: Design Facts and Evidence Inventory

The GPU stall condition ("wedge") is a driver/firmware-resident state defect characterized by stalled compute queues that do not trigger OS-level hang detection.

## Wedge Signature

- **Power/Compute:** Draw stabilizes at ~220–250W with clocks pinned >2900MHz and memory load 0–10%. This contrasts with healthy saturated render states (~370–390W, ~33% memory load). The flip boundary occurs at ≈14 TFLOP/s. A burst-immune variant of the signature exists.
- **Degradation Metric:** Wedge probability correlates with CHURN (renders/context-cycles since boot), not uptime (hours since boot).
- **Recovery Constraint:** Only a system reboot fully resets the degradation floor. Terminating the ComfyUI process (context-kill) may recover a single render but does not reset the underlying degradation state.

## OS Layer Visibility

The OS remains silent during wedge events:
- Zero TDR events (codes 4101/141/117).
- Zero WHEA-Logger events.
- The condition represents a stalled compute queue, not a detected hang.

## Contributing Factors

- **Model Size:** Models exceeding VRAM capacity (e.g., 8188-resident models with 5.2GB spill) aggravate the condition but are not the root cause. Wedges persist on clean VRAM states.

## Telemetry Instruments

The following instruments record the degradation curve and wedge events:
- `stall_rescue.log`: Records every wedge event, outcome, and recovery ladder.
- GPU telemetry history logger.
- `render_progress.json`: Step traces.
- Foundry logs.

## MES queue state and ROCm stability constraints

- **MES unrecoverable state:** On-GPU firmware scheduler (MES) enters an unrecoverable state when concurrent multi-application GPU use triggers failures to remove hardware queues. Symptom: "failed to remove hardware queue from MES"; recovery requires cold power-off. Every ComfyUI context creation/destruction churns MES queues.
- **Nightly regression risk:** ROCm Windows nightlies exhibit regressions (e.g., driver timeouts on gfx1201 in `rocm7.10.0a20251106`). A/B testing across nightly builds is the standard method for isolating such regressions.
- **Process termination behavior:** ROCm processes on Windows do not self-terminate without an explicit sync at exit. This causes orphan contexts and file handles (e.g., LoRA files) to persist after process kill signals.
- **Version pinning strategy:** Performance regresses across ROCm versions; pinning specific versions is the established mitigation.

### Driver diagnostic instrumentation

The telemetry logger collects the following per-boot counters:
- Contexts created
- Renders
- GB loaded/unloaded

#### Wedge discriminator probe
When a performance wedge is active, the daemon spawns a fresh Python process and executes a known >14 TFLOP matrix multiplication.

This measurement prunes the root-cause hypothesis tree:
- **Fresh-process-also-slow**: Indicates global system bottleneck (MES/KMD, H1/H5).
- **Fresh-process-fine**: Indicates process-local bottleneck (runtime/fragmentation, H2/H3).

The probe runs automatically during real wedges to conserve GPU time.

#### Active-state logging
At each wedge, the system logs the active state of:
- Parsec
- Ollama
- Port 8188

This provides free correlation data for hypothesis H4.

## Test infrastructure
The churn harness is a scripted loop that measures system stability under load. The loop executes: load ComfyUI context → render → kill. Existing barrage and daemon components are sequenced to form this reproducible benchmark.

## Metrics and scoring
1. **renders-to-first-wedge**: Count of renders before the first system wedge.
2. **wedge density**: Frequency of wedges per unit time or render count.
3. **Smoking gun threshold**: A variable that improves renders-to-first-wedge by a factor of ≥3 indicates the primary root cause.

## Test sequence (Controlled A/B)
Each test isolates ONE VARIABLE per run. Each run consumes approximately 1–2 hours of GPU time. Execution follows this priority order:

1. **Runtime pin**: Compare venv clone with newest *stable* (or several-weeks-older nightly) TheRock wheel against current alpha.
   - Hypothesis: H2 (cheapest high-value test; execute first).
2. **Parsec/Ollama off**: Disable Parsec and Ollama during churn.
   - Hypothesis: H4 (free config change).
3. **HAGS toggle**: Toggle `HwSchMode` registry key. Requires one reboot per state change.
   - Hypothesis: H4/H5 adjacent.
4. **KMD A/B**: Compare previous Adrenalin release against 32.0.31021.5001.
   - Hypothesis: H5 (most invasive; execute last).

<!-- REWRITTEN -->
## Scope and constraints
This track runs parallel to Phases 0–2. It consumes no GPU time. It proceeds independently of live lab operations.

## Search vectors
Research follows a structured sweep ordered by expected payoff:

1. **GitHub issue archives**: High-yield targets for exact error signatures ("MES", "doorbell", "queue stuck", gfx1100 + Windows, high-clock-low-power stalls, born-degraded processes).
   - Sources: ROCm/ROCm, ROCm/TheRock, pytorch.
   - Action: Catalog issues, versions affected, workarounds, and fix status.
   - Validation: Cross-reference TheRock nightly changelogs against the install date to bracket changes in HIP queue/scheduler code.
2. **Community forums**: AMD community boards, r/ROCm, r/StableDiffusion AMD threads, ComfyUI discussions.
   - Focus: 7900 XTX long-session degradation reports and pinned configurations.
3. **Driver release notes**: Adrenalin changelogs (specifically around version 32.0.31021.5001).
   - Focus: Fixed/known issues touching compute, OpenCL/HIP hangs; AMD ROCm-on-Windows support matrix status for gfx1100.
4. **Primary documentation and source code**: MES behavior in the open amdgpu Linux driver (queue management, doorbells, unrecoverable-state paths).
   - Relevance: MES firmware is common to Windows deployments; source readability explains degradation mechanisms and defines logging targets.
5. **Academic papers**: Included only if warranted by gaps in above sources.
   - Constraint: GPU scheduler/firmware reliability literature is thin and Linux/datacenter-centric. Treat as background. Blog-grade sources (e.g., TheRock build docs) take precedence over academia.

## Deliverable
The curated research findings are folded into this document (see §"Upstream MES-bug family"):
- Per-source findings.
- A "configs other people pinned to" shortlist to feed Phase 2 A/B choices.

### Upstream escalation with reproducible evidence

If investigation isolates a version-specific issue or produces a clean churn reproduction, file the report against ROCm/TheRock. They actively triage gfx1100 on Windows.

**Required exhibits:**
* Degradation curves
* Wedge telemetry signature
* Harness script

**Expected outcomes:**
* A fixed nightly build
* A named workaround
* Confirmation and a tracking issue

## 6. Scope constraints and mitigation strategy

The investigation operates under the constraint that AMD MES firmware, KMD, and HIP runtime are not directly modifiable by the lab. The ceiling for root causes within the AMD stack is identification, configuration pinning, and upstream reporting with reproduction evidence.

Diagnostic visibility on Windows-side driver state is limited; no equivalents to Linux debugfs exist, and Linux tooling provides richer diagnostic capabilities.

The investigation targets four deliverables:
1. A pinned configuration (runtime + KMD + settings) that reduces wedge frequency by a factor of three or more.
2. A reproducible benchmark to reduce future driver/runtime upgrade validation from days to approximately two hours.
3. An upstream issue report with clean reproduction evidence, leveraging TheRock’s active triage of gfx1100 Windows issues.
4. Automation improvements: if discriminator probes confirm wedges are process-local, the mitigation shifts from system reboots to surgical process respawning.

Phase 0 and R require minimal CPU time. Phase 2 is resource-intensive, with each A/B leg requiring 1–2 GPU-hours plus setup. Version environment (venv) and driver swaps carry rollback risk; exact wheels are pinned and the current venv remains untouched, modified only via clone-and-modify workflows. Driver KMD swaps pose the highest risk and are scheduled last. If nightly-pinning yields success, subsequent phases may be skipped.

If the root cause is unfixable upstream on Windows, the deliverable is the formalization of the pinned configuration and mitigations into documented policy, converting informal practices into verified config.

## Upstream MES-bug family

The RDNA3/RDNA3.5 Message Execution Scheduler (MES) queue arbitration exhibits known instability under multi-context concurrency. This manifests as context poisoning or hard deadlocks across Linux and Windows platforms.

## Manifestations

| Report | HW / OS | Signature | Trigger | Recovery | Status |
|---|---|---|---|---|---|
| **Lab repro** | 7900 XTX (gfx1100), Win11, TheRock alpha 7.15 | context poisoned to ~2% throughput; 210-250W @ >3100MHz, memload<10%; ZERO TDR/WHEA | big-latent render + concurrent gen on 2nd context (~90s, 2-for-2) | **kill poisoned context → instant full recovery** | reproducible; upstream report drafted |

## Synthesis

1. **MES/queue arbitration degradation is real, known, open, and cross-platform.** It degrades or deadlocks under multi-context concurrency across RDNA3/RDNA3.5 on Linux and Windows. Severity ranges from context poisoning (recoverable) to machine hang (fatal).
2. **Concurrency limiting is the convergent mitigation.** ROCm#6012 workaround requires ≤3 processes; this lab serializes to one generation at a time. The mechanism is queue-count arbitration.
3. **No shipped fix exists in available nightlies/changelogs.** Pinning forward is not obviously fruitful. If AMD identifies a fixed build, the churn harness re-validates it in ~15 minutes.
4. **Lab contributions include:** deterministic minimal repro (2 contexts); context-held proof + 30s kill-cure; quantified signature including "fresh spawns measure slow = contention"; Windows coverage.
