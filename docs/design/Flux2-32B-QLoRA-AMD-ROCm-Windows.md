# Training Flux.2 [dev] (32B) QLoRA on 24 GB AMD GPU — native ROCm, Windows

This procedure trains the Flux.2 32B base model resident in VRAM on a 24 GB AMD RX 7900 XTX using native ROCm under Windows. The configuration exceeds the typical 24 GB QLoRA ceiling (~13B) by utilizing specific memory optimizations to fit the 32B base.

## Prerequisites and Base Image

- **GPU:** AMD RX 7900 XTX (24 GB VRAM).
- **Base Model Source:** `cupertinomiranda/ai-toolkit-amd-rocm-support` (AMD ROCm fork of ai-toolkit).

## Configuration Patches

Apply the following patches to the base toolkit source:
1. `extensions_built_in/diffusion_models/flux2/flux2_model.py`
2. `.../sd_trainer/SDTrainer.py`
3. Training configuration file.

## Performance Baseline

- **Resolution:** 512 px.
- **Step Time:** ~40 seconds per step.
- **Memory State:** Fully resident in VRAM; system RAM decreases by ~19 GB as the base loads to GPU.
- **Verification Method:** Monitor `torch.cuda.mem_get_info` for ~20 GB usage and confirm high compute engine utilization for the Python PID.

## Environment

### Hardware and Precision Constraints
- GPU: AMD RX 7900 XTX 24 GB (gfx1100). The hardware lacks FP8/FP4 support; use **uint4** weight-only quantization.

### Software Stack
- Quantizer: `optimum-quanto` (`QTensor`, `quantize()`/`freeze()`/`get_qtype()`). Do not use `torchao`; the fork's `quantize_model` wraps `quanto`.
- PyTorch: 2.12.0+rocm7.15 (sourced from the `comfyui-rocm` bundle's `python_env`, reused as the `ai-toolkit` venv). No flash-attn ROCm kernels are available; the system falls back to the **Triton** attention path.

### Model Configuration
- Vision/Transformer: Official `black-forest-labs/FLUX.2-dev`. Use the repo-root single file `flux2-dev.safetensors` (64.4 GB bf16). BFL-native keys match `ai-toolkit`'s `src.model.Flux2` (missing 0 / unexpected 0). Do not use the `transformer/` subdir; it contains sharded diffusers-format weights with incompatible key names.
- Text Encoder: `mistralai/Mistral-Small-3.1-24B-Instruct-2503` (local copy, defined in the fork's `MISTRAL_PATH`).

### Host Resources
- RAM: 32 GB + large pagefile (≈115 GB total virtual). This capacity is required for one-time bf16 loads.

## Flux2 uint4 quantization cache: preparation and loading
`safetensors` `mmap` `load_file` crashes natively (no traceback) on the 64 GB bf16 file. A standalone script performs the initial load, quanto-uint4 quantization, and freeze operations exactly once, saving the result as `flux2-dev-uint4.pt`. This file is a `state_dict` of quanto `QTensor`s, approximately 19 GB in size.

Training loads this cache via `torch.load`, bypassing the native crash and avoiding repeated quantization. Every Flux2 QLoRA run must use this cached artifact for model initialization.

Caching for this component follows the house shape defined in [[P20-FOUNDRY-RUNNER-PLAN]] §"The caching shape": artifacts are content-addressed, provenance is recorded beside the artifact, and validity is gated on that record.

## Memory management and hardware constraints

### Manual safetensors loading (64 GB file crash)
The `safetensors` mmap `load_file` crashes on files approximately 64 GB in size due to whole-file mmap limits. Files under 33 GB load successfully.

**Implementation:** Implement a manual non-mmap loader for cache building:
1. Parse the 8-byte header length and JSON header.
2. For each tensor: `seek(base+offset)`, `read(nbytes)`, then `torch.frombuffer(bytearray, dtype).reshape(shape)`.

### CPU-side quantization (Transformer OOM)
Moving the full 64 GB bf16 transformer to the GPU before quantizing causes an out-of-memory error (~38 GB allocation on a 24 GB card).

**Implementation:** Quantize on CPU. The model remains on CPU after manual loading. Only the resulting ~19 GB uint4 tensor is moved to the GPU.

### State dict memory release (Mistral `0xC0000005` crash)
The `transformer_state_dict` pins 64 GB of bf16 memory. Loading Mistral (48 GB bf16) on top exceeds the ~115 GB virtual address space limit, causing an access violation (`0xC0000005`).

**Implementation:** Execute `del transformer_state_dict; gc.collect()` immediately after `load_state_dict(assign=True)`.

### CPU-side quantization (Mistral TE OOM)
The `load_te` function moves the full 48 GB bf16 Mistral model to the GPU before quantizing, causing a c10 abort/OOM.
**Note:** The fork's `load_te` uses `model_config.qtype`, not `qtype_te`.

**Implementation:** Quantize on CPU first using `quantize(te, weights=get_qtype(qtype))`. Move the resulting ~12 GB uint4 model to the GPU with `.to(device)`.

### Disable block-swap (HIP driver deadlock)
Block-swap (`layer_offloading`) deadlocks the HIP driver at the pre-train sample and first step, requiring process termination via `Stop-Process`.

**Implementation:** Set `layer_offloading: false`. Do not attempt to offload to resolve VRAM pressure near the 1 GB threshold.

### Disable sampling (Driver deadlock and preview instability)
The offload machinery causes sampling deadlocks. uint4 image previews are unstable, producing black frames or rainbow-noise bands on certain seeds.

**Implementation:** Set `disable_sampling: true`. Evaluate finished LoRA outputs in ComfyUI.

### Expandable segments (uint4 GPU move fragmentation)
Moving uint4 tensors to the GPU causes memory fragmentation and OOM errors without expandable segments.

**Implementation:** Launch with environment variable `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True` (and `PYTORCH_CUDA_ALLOC_CONF=...` if applicable).

### Use uint4 cache for training
The cache branch (`model_kwargs.use_uint4_cache: true`) loads `flux2-dev-uint4.pt` via `torch.load` and skips `quantize_model`.

**Behavior:** The cache path trains resident on the GPU identically to the re-quantization path but loads in seconds instead of ~8 minutes. Use this method for all training sessions.

## Trustworthy GPU Utilization Signals
Use these methods to verify GPU residency and utilization on ROCm/Windows. Do not use cross-process `torch.cuda.mem_get_info()` probes, as they are unreliable on this platform.

1. **In-process VRAM check**: Print `torch.cuda.mem_get_info()` from *inside* the training process. This accurately reports resident VRAM (e.g., ~20 GB for a 32B base on `cuda:0`).
2. **Windows GPU Performance Counters**:
   - `\GPU Process Memory(<pid>)\Dedicated Usage`: Reports VRAM usage.
   - `\GPU Process Memory(<pid>)\Shared Usage`: Reports host memory spill.
   - `\GPU Engine(*engtype_compute)\Utilization`: Reports compute utilization.
3. **System RAM Delta**: Monitor host system RAM. A drop of ~19 GB in resident RAM confirms the model has moved from CPU to GPU memory.

## Device Wiring and State Retention
The following architectural facts govern device placement for `low_vram: true` + cache + `[P20]` resident-move configurations:

- **Object Identity**: `unet`, `transformer`, and `model` refer to the same `BaseModel` object (`self.model`).
- **State Dict Patching**: `patch_dequantization_on_save` modifies only the save method’s `state_dict`; it does not alter parameters or `.to()` device placement.
- **Initialization Order**:
  1. `accelerator.prepare(unet)` executes via `super().hook_before_train_loop()` *before* resident memory moves occur.
  2. `set_device_state(train_device_state_preset)` executes immediately after the hook. When `train_unet=True`, this retains `unet` on the designated device.
- **Result**: The `.to(cuda)` move executes correctly and persists. There is no re-parking of the base model during training initialization.

## Common Misinterpretations
- **Single Busy CPU Core**: One busy CPU core during GPU training is normal behavior; it drives the GPU workstream. It does not indicate CPU-only training.
- **Cross-Process VRAM Probes**: External calls to `torch.cuda.mem_get_info()` may report `used=0.2 GB` while the process genuinely holds ~20 GB. Do not rely on these external probes for residency verification.

## Flux source patches: VRAM collision avoidance

The following source modifications prevent VRAM collision during TE caching by managing device placement of the base model and Mistral adapter.

### SDTrainer.hook_before_train_loop
Move the base model back to the GPU after the token embedder (TE) is cached and unloaded. The base model is parked on CPU during TE caching to prevent collision between the 19 GB base model and the 12 GB Mistral adapter. After TE caching, the `[P20]` resident-move block transfers the packed bytes correctly using:
```
if low_vram and not layer_offloading: self.sd.model.to(self.sd.device_torch)
```

### flux2_model.load_model (line 280 gate)
Gate the `pipe.transformer.to(device_torch)` call on `if not low_vram:`. Under `low_vram`, the base model must remain on CPU through TE caching. Moving the base to GPU while the 12 GB uint4 Mistral adapter is resident causes VRAM collision. The `[P20]` block performs the actual move after Mistrel unloads.

## VRAM Spill & Step Rate Performance

The Flux-32B workload on the AMD GPU (20 GB VRAM base) exhibits performance constraints driven by memory spill and compute overhead.

### Memory Management
Host-memory spill over PCIe occurs when the working set exceeds available VRAM. The allocator behavior is governed by `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True` (and `PYTORCH_CUDA_ALLOC_CONF=` for compatibility). This configuration packs the working set into VRAM, reducing host spill and step time.

**Observed performance with correct allocator tuning:**

| Resolution | Host Spill | Step Time |
|------------|------------|-----------|
| 1024       | 2.27 GB    | ~204 s    |
| 768        | 0.82 GB    | ~79 s     |
| 512        | 0.83 GB    | ~40 s     |

**Performance Notes:**
*   At resolutions 768 and 512, spill floors at approximately 0.83 GB. This fixed overflow results from the base model + context marginally exceeding 24 GB (allocator reserves ~0.6 GB as dedicated unusable space).
*   The speedup from 768 to 512 is driven by reduced compute requirements, not reduced spill.
*   Without allocator tuning (e.g., during fragmentation events), spill can balloon (e.g., 3.45 GB), causing memory-bound step times (~175 s). Applying `expandable_segments:True` reduces spill to ~0.97 GB and step time to ~69 s.
*   `layer_offloading` is not used; it causes deadlocks (Wall #5) and cannot reduce the remaining ~1 GB of fixed overflow.

### Compute Constraints
The practical step-rate ceiling on this hardware is determined by:
1.  quanto uint4 dequantization every forward pass (including gradient-checkpoint recomputation, approx. ×2 overhead).
2.  Triton attention fallback (no ROCm flash-attn support).

**Hardware Capability:**
The unstalled step rate for a batch size of 1 at 448 resolution is approximately 6.9 s/it.

### Telemetry & Monitoring
Performance diagnosis requires persistent GPU telemetry, not on-demand readings.

**Instrumentation:**
*   **Tool:** LibreHardwareMonitor (`F:\tools\LHM`, read via DLL from PowerShell).
*   **Logger:** `F:\tools\gpu_telemetry_logger.ps1` executed by the **"GPU Telemetry Logger"** scheduled task (SYSTEM context, AtStartup, no time limit, 3 restarts).
*   **Output:** Appends every 10 s to `F:\foundry\var\state\telemetry\gpu_telemetry.csv`.
*   **Schema:** `ts,run,step,rate_s_it,core_mhz,mem_mhz,power_w,temp_c,hotspot_c,gpu_load,vram_used_mb,vram_free_mb,shared_spill_mb,…`
*   **Run Identification:** The `run` column is populated from `F:\foundry\var\state\CURRENT_RUN.json`. If the file is missing or invalid, the system is considered idle (blank run column).

**Thermal Status:**
Baseline environment temperature is 74 °F; GPU temperature remains at ~50 °C under load. Thermal throttling is not a constraint.

## Flux2 QLoRA launch configuration and environment

The following YAML defines the canonical training configuration for Flux2-32B on AMD ROCm with uint4 quantization. This configuration enforces design constraints required to avoid ROCm deadlocks and VRAM exhaustion.

```yaml
model:
  arch: "flux2"
  quantize: true
  qtype: "uint4"          # Drives quanto and TE quantization in this fork
  quantize_te: true       # Enables uint4 Mistral for embedding cache (~12 GB)
  qtype_te: "uint4"
  low_vram: true          # Parks base model during TE caching; resident after Mistral unloads
  layer_offloading: false # BLOCK-SWAP DEADLOCKS on ROCm — do not enable
  model_kwargs:
    use_uint4_cache: true # Loads flux2-dev-uint4.pt directly; avoids ~8-min re-quantization
datasets:
  - resolution: [ 512 ]   # 512px yields ~0.8 GB fixed spill; higher resolutions degrade performance
    cache_latents_to_disk: true
    cache_text_embeddings: true
train:
  gradient_checkpointing: true
  disable_sampling: true  # Offload+sampling causes deadlocks; uint4 previews unreliable (judge in ComfyUI)
```

### Launch environment and execution

Set the following environment variables before launching to manage ROCm memory allocation:

```bash
PYTORCH_HIP_ALLOC_CONF=expandable_segments:True
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

Execute training via direct Python invocation with unbuffered output:

```bash
python -u run.py config\...yaml
```

*   **Direct invocation requirement:** Launch `python` directly. Using detached processes (e.g., `Start-Process -RedirectStandardOutput`) risks losing early import-time stdout/stderr if the child exits prematurely.
*   **vcvars64 is not required:** PyTorch and ROCm imports function correctly without vcvars64 environment setup.

## Flux.2 QLoRA Training Performance and Configuration

### Mistral Embedding Cache Optimization
The training pipeline caches the quantized Mistral model (`mistral-uint4.pt`) adjacent to the Mistral repository to reduce launch latency. The loader checks for this ~12 GB uint4 file; if present, it loads in seconds instead of reloading 48 GB bf16 and re-quantizing on CPU (a 2–3 minute cost). The load path is fail-safe: it uses quanto+meta with a fallback to the bf16 build path. If the cache load fails, the system quantizes on-demand and saves the cache for future runs. This behavior is gated by `model_kwargs.use_mistral_cache` (default: true).

### Deferred Optimization: Conditional TE Loading
Loading the Text Encoder (TE) is deferred when the dataset text-embedding cache is complete. Because cached embeddings do not require Mistral inference, skipping TE loading would eliminate that phase. However, this optimization is not implemented because it lacks a trivial fail-safe mechanism; an incorrect "cache complete" check can crash runs, and blank/trigger embeddings must also be cached. The marginal performance gain is small given the fast cached load time described above.

### Flash Attention Environment Variables
Do not set `FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE` or `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1` during Flux.2 training. These variables, used by ComfyUI for rendering, cause training to run slower (~60 s/step vs ~36 s/step). `flash_attn_2_cuda` is not built for this environment, forcing a Triton fallback. Attention is not the bottleneck in Flux.2 (FFN/linear layers are), so these variables add overhead without payoff.

### Performance Measurement Standards
Performance measurements must filter for saturated steps (power > 350 W). Runs exhibiting stall states (~230 W, ~1/8 expected speed) are invalid for benchmarking. All pre-2026-07-20 performance conclusions in this document are invalid because they were measured without accounting for GPU stalls.

### GPU Power Management and VRAM
* **ULPS Setting:** `EnableUlps` has no effect on single-GPU training performance or idle power. The registry key `…Class\{4d36e968-…}\0000\EnableUlps` should remain at 1.
* **Background VRAM Usage:** Idle background processes consume critical VRAM headroom near the 24 GB limit. A scheduled ComfyUI Server task consumes ~0.19 GB; `dwm` consumes ~0.24 GB. To maximize trainer performance, terminate idle ComfyUI instances before starting training. ComfyUI will restart on the next boot.
* **Reboot Behavior:** A system reboot does not reset GPU performance stalls. Post-reboot runs may still exhibit stalled performance (~43.5–47.9 s/it at 223–265 W).

## Pre-run environment preparation
Flux2-32B QLoRA on AMD ROCm requires strict VRAM and CPU hygiene to operate within the 24 GB VRAM limit. The design goal is to maximize headroom (approximately 147 MB at batch_size 2) for resolution scaling.

### Resource reclamation procedure
Execute these steps before every training session:

1. **Terminate non-essential GPU holders.**
   Services must be both stopped and disabled to prevent auto-relaunch.
   *   **ComfyUI Server:** Disable the scheduled task. Note: ComfyUI is required for step #19 (inference/evaluation); re-enable after training if that step is pending.
   *   **AMD Services:** Disable specific background processes. Current driver version (32.0.31021.5001, 2026-06-27) does not include `AMDRSServ` or `AMDRSSrcExt`. Target the following if present:
        *   "AMD Crash Defender Service"
        *   "AMD External Events Utility"
        *   `AUEPLauncher` (telemetry uploader; primary target for disabling)
       *   Verify active AMD services with: `Get-Service | ? DisplayName -like '*AMD*'`
   *   **Stray Processes:** Kill `msedgewebview2` instances.
   *   **Unavoidable:** `dwm` (desktop compositor) consumes ~0.24 GB VRAM and cannot be disabled.

2. **Verify Defender exclusions.**
   Ensure Windows Defender does not scan model caches during runtime. The following directories must be excluded:
   *   `F:\ai-toolkit`
   *   `F:\flux2-train` (model caches)
   *   `F:\foundry`
   *   `F:\ComfyModels`

3. **Validate VRAM headroom.**
   Measure `\GPU Process Memory(*)\Shared Usage` to confirm spill reduction after cleanup.
   *   If spill drops significantly, increase training resolution for quality gains.
   *   Note: VRAM spill alone does not dictate speed; kernel-autotune warm-up and CPU contention are primary latency factors. However, freed headroom on a clean start enables higher resolutions.

4. **Post-run restoration.**
   After the session concludes:
   *   Re-enable disabled AMD services.
   *   Re-enable the ComfyUI scheduled task.
   *   Re-enable the Ollama Watchdog.

<!-- File: LLM-Project/Flux2-32B-QLoRA-AMD-ROCm-Windows.md -->
<!-- Section: Render / judge the LoRA in ComfyUI -->

## Render / judge the LoRA in ComfyUI

Render and evaluate Flux.2 LoRA adapters using the **comfyui-rocm bundle** (`F:\comfyui-rocm`) because `ai-toolkit` sampling is disabled due to ROCm deadlock risks. The render instance runs on port **8189**, distinct from the primary ComfyUI on port 8188. Infrastructure and port assignments are defined in [[RUNBOOK]] §"Second ComfyUI — Flux.2 render bundle".

### Model architecture

*   **Base model path:** `F:\ComfyModels`, mapped via `extra_model_paths.yaml` (`flux2_nvme`).
*   **UNet:** `unet\flux2-dev-Q3_K_M.gguf`
*   **Text Encoder:** `text_encoders\Mistral-Small-3.2-24B-Instruct-2506-Q5_K_M.gguf` (uses Tekken tokenizer rebuilt from GGUF)
*   **VAE:** `vae\flux2-vae.safetensors`

### Execution steps

1.  **Load Adapter:** Copy the `.safetensors` adapter file to `F:\comfyui-rocm\models\loras\`. Do not place adapters in `F:\ComfyModels\loras`; that path is unmapped in this bundle and files placed there are ignored.
2.  **Verify Service Status:** Confirm the bundle is running by polling `http://<mothership-address>:8189/system_stats`. If inactive, launch via `F:\comfy_launch.bat` as a background ssh-foreground task.
3.  **Submit Job:** POST the API graph to `http://127.0.0.1:8189/prompt` from the Mothership localhost. Body format: `{"prompt": {...}}`. Verify the response contains empty `node_errors`.
4.  **Monitor Completion:** Poll `http://127.0.0.1:8189/history/{prompt_id}` until `.status.completed`. The output PNG filename is found at `.outputs.14.images[0].filename`; the file resides in `F:\comfyui-rocm\output`.
    *   **Performance:** Typical render time is 5–6 minutes (3 min model load, 20 steps inference).
    *   **Logging:** Monitor progress via launcher stdout. Do not monitor `user\comfyui.log` for live status.

### API Graph Definition

The following node graph defines the rendering pipeline. Node IDs are fixed; edges reference `[src_id, out_idx]`. Do not guess node class names or input signatures; consult the running server’s `/object_info/{class}` endpoint for current truth (e.g., verify `CLIPLoaderGGUF` type enum contains `flux2`; retrieve sampler names from `KSamplerSelect`).

A durable JSON template for this workflow is stored at `LLM-Project\flux2-render-workflow.json`. To render, swap values for `lora_name`, `text`, `noise_seed`, and prefix. For higher resolutions or different prompt structures, adjust `EmptyFlux2LatentImage` and `Flux2Scheduler` width/height parameters accordingly.

### Tier 1 — highest value, quality-neutral

#### Warm-down phenomenon: host-side investigation
The training run exhibits a speedup from ~58 s/it to ~32–45 s/it after step 1000–1250. GPU telemetry (core clock, power, temperature, VRAM spill) does not correlate with this rate change; these variables remain essentially flat across the variation.

**Current hypothesis:** The cause is host-side contention (CPU dataloader/dequant orchestration, RAM/pagefile pressure, disk I/O for checkpoints, or scheduled background processes). GPU variables are ruled out as primary drivers.

**Instrumentation requirement:** Log host-side metrics (CPU %, RAM used, pagefile usage, disk I/O, top process) alongside GPU metrics. Glances runs on port 61208 and exposes these metrics; the logger must be configured to pull this data.

#### Fused int4 path: blocked on gfx1100
Fused low-bit kernels (int4) are unsupported for training on RDNA3 (gfx1100) hardware. AMD ROCm restricts `_convert_weight_to_int4pack_cuda` to CDNA2+ architectures. The following implementations are excluded:
*   **PyTorch/ROCm:** Hard gate excludes gfx1100.
*   **sglang:** HIP compilation permits only gfx942/gfx950.
*   **GemLite/Marlin:** NVIDIA-only or inference-only.

Community defaults for int4 on RDNA3 (AWQ-INT4 via vLLM, llama.cpp) are inference-only and provide no autograd/backward support. Fused low-bit training kernels are unavailable for this hardware; do not attempt fork surgery to enable them.

**Reference bf16 GEMM timing:** M=1024, K=6144, N=6144: **7.40 ms** on gfx1100.

### Gradient Checkpointing Optimization
Selective gradient checkpointing reduces VRAM usage by recomputing only selected layers. This technique preserves output identity (pure schedule change) and yields performance gains proportional to the number of skipped layers. Implementation requires `ai-toolkit` support for selective or per-layer checkpointing.

### Training Step Requirements
The training run requires 2000 steps. Reducing the count to 1500 degrades facial geometry accuracy, resulting in elongated features (narrower jaw, thinner cheeks). Facial proportion convergence occurs after identity markers (hair, coloring, general features) appear correct; therefore, checkpoints at 1500 steps are insufficient for identity LoRA quality. Evaluate checkpoint quality based on proportional accuracy, not recognizability alone.

### Rescue-cycle cross-cuts

The rescue cycle mechanism operates via coordinated versions of the daemon, payload, and logger components. The trainer component is not involved in swap operations.

**Component Versions and Behavior**
*   **Daemon (v4):** Initiates prophylactic bursts on launch/resume and checkpoint-save triggers. Triggers are constrained by `only-if-stepping` and a minimum 120 s gap between bursts.
*   **Payload (v2):** Self-aborts spawns exceeding the performance threshold (<11 TFLOP/s or exit code 3) after a 3-second evaluation window. Aborted duds do not enter cooldown states and do not count toward disable thresholds. The daemon immediately respawns aborted processes.
*   **Logger (v5):** Operates on a 10-second cadence with a `SETTLE` duration of 25 seconds.

**Operational Constraints**
*   **Burst-Echo Guard:** Freezes the state machine for rows within the window `[burst − 2 s, end + 35 s]` to prevent interference during recovery actions.
*   **Performance Expectation:** The mechanism is designed to reduce worst-case stall cycles from ~8 minutes to ~1–2 minutes. Most stalls are prevented by immediate intervention.

**Known Behavioral Quirk**
If Daemon v4 starts while a trainer process is already running, it interprets the state as a launch transition and fires one prophylactic burst (~60 seconds after boot). This event is harmless. To suppress this behavior on future daemon restarts, baseline `prev_training` from the first data row.

<!-- No change required. The section contains a design fact/deferred item statement compliant with manual register. -->

### Tier 3 — free but small (startup, not step rate)
5. **Skip the TE entirely when text-embeddings are cached** (PROJECT-BACKLOG, deferred) — ~4 min per launch.

### Device-Wide VRAM Monitoring: Use LibreHardwareMonitor, Not PyTorch

On this ROCm/Windows build, `torch.cuda.mem_get_info()` reports per-process availability, not device-wide free memory. It returns incorrect values when other processes are active, leading to false-positive free-memory readings.

**Design Rule:** Never use `torch.cuda.mem_get_info()` for GPU-side side-experiments or resource guards on this system.

**Correct Instrument:** Use LibreHardwareMonitor (`F:\tools\LHM`) or the telemetry CSV to determine device-wide free VRAM. Guard all non-trainer processes against the LHM report.

### Hardware constraints and kernel limitations on RDNA3

The 7900 XTX (gfx1100) has 24 GB of VRAM. A 32B parameter model requires:
- BF16: 64 GB
- Int8: 32 GB
- UInt4 (with scales/overhead): ~19 GB

Int8 quantization exceeds the available VRAM and cannot fit on a 24 GB card. UInt4 is used because it fits within memory limits, not for speed optimization.

RDNA3 WMMA natively supports FP16, BF16, INT8, and INT4 arithmetic. It does not support FP8 or FP4 floating-point formats.

Current software stacks (e.g., `quanto`) dequantize UInt4 to BF16 before performing matrix multiplication, even though the silicon supports native INT4 operations. This dequantization overhead is a software/kernel limitation, not a hardware ceiling. Fused low-bit kernels that utilize native INT4 math would eliminate this unnecessary compute tax.

Performance benchmarking (Unsloth 0.7) demonstrates that fast QLoRA training on this hardware is achievable, showing only a ~10–20% penalty versus an RTX 4090 for 7B/13B models. This indicates the performance gap is due to stack and kernel maturity rather than silicon capability.

`bitsandbytes` now supports gfx1100 via community forks (`st1vms/bitsandbytes-rocm-gfx1100`) and upstream integration, described as "functional but not optimal kernels." NF4 quantization is therefore a viable alternative to evaluate. See `Reference\AMD-ROCm-Kernel-Support.md` for kernel maturity details.

The system runs ROCm 7.2.x (current as of June 2026) with official support for the 7900 XTX.

### Constraint: Hardware-agnostic tuning levers

The project scope is strictly defined by the existing 24 GB RDNA3 GPU. Hardware upgrades are excluded from solution space; all optimizations must achieve gains within this fixed memory constraint.

#### Levers that do not improve step time

**Block-swap (`layer_offloading`)**
Do not use block-swap or `layer_offloading` to optimize step time. These mechanisms trade PCIe throughput for VRAM headroom. On this configuration, spilling incurs a penalty of ~204 seconds per step for 2.27 GB spill. This latency dominates execution time. 

**Batch size > 1**
Do not increase `batch_size` beyond 1 as a performance optimization without retuning the learning rate. Increasing batch size amortizes per-step dequantization cost but alters the effective batch and learning rate dynamics. This is not quality-neutral; it requires a corresponding adjustment to the training recipe.

#### Diagnostic status
The interaction between ROCm, pagefile behavior (64–96 GB configured), and block-swap latency is documented as observed but not fully diagnosed. Further profiling with verbose ROCm logging may identify specific bottleneck locations, though such diagnostics are expected to confirm that spilling remains the primary latency driver rather than resolving it.

### GPU Stall Detection and Resolution: `mem_load` and Power Metrics

**Diagnosis**
GPU stalls present as high clock speed with disproportionately low power draw. The definitive diagnostic metric is `mem_load_pct` (CSV column 17). A stalled card exhibits ~4% memory load while reporting 100% GPU utilization. Healthy operation exhibits 16–33% memory load.

| Regime | Power Draw | `mem_load` | vCore | Throughput |
|---|---|---|---|---|
| Stalled | ~230 W | ~4.0% | 1.10 V | <15 s/it (degraded) |
| Healthy (Low-Res) | 278 W | ~17.0% | 1.00 V | ~15.5 s/it |
| Healthy (High-Res) | 270–380 W | 16–33% | 0.90–1.00 V | ~19.5–22.3 s/it |

*Note: Power draw alone is not a reliable discriminator for stalls, as healthy runs may operate in the same power band (~270 W) under specific resolutions. `mem_load` provides non-overlapping separation.*

**Resolution Protocol**
To resolve a confirmed stall (high clock, low power, ~4% `mem_load`), inject sustained compute work from a secondary process.
1. Run `F:\ai-toolkit\p20_kernel_bench.py` in a separate process.
2. The benchmark must perform sustained GEMM operations; simple allocation/free cycles have no effect.
3. The intervention is one-shot: saturation persists after the rescuing process exits.
4. Monitor VRAM headroom before execution. The benchmark allocates ~250–500 MB. Use `torch.cuda.mem_get_info()` cautiously; prefer Logical Hardware Manager (LHM) VRAM reporting for accuracy.

**Verification**
The mechanism has been reproduced across multiple independent runs (including Riley and disposable benchmarks). The effect flips the GPU from a stalled state to saturated execution (~8–10× throughput improvement). The underlying hardware capability exceeds previous recorded floors when unstalled.

### Checkpoint-swap pause-and-resume pattern
Checkpoint-swap previews (pause → render → resume) are a standard, low-overhead pattern for inspecting model state without incurring restart latency. The resume mechanism itself is reliable; historical performance penalties were caused by startup stalls, which the rescue daemon now clears automatically upon process launch.

#### State-flag based pausing
Pausing training requires specific flag files to ensure zero-step loss and clean state preservation. Abrupt process termination (kill) is prohibited for checkpoint swaps as it risks data integrity and step loss.

**Control Flags:**
- Location: `F:\foundry\var\state` (active since 2026-07-27 green cutover)
- The trainer checks these files at every step.
- `SAVE_NOW`: Creates a checkpoint at the current step and continues training.
- `STOP_NOW`: Creates a checkpoint at the current step, then exits the training loop cleanly.

#### Swap Procedure
1. Trigger clean stop:
   ```cmd
   type nul > F:\foundry\var\state\STOP_NOW
   ```
2. Wait for the trainer to save the checkpoint and exit cleanly (verify via log).
3. Perform inference/rendering (e.g., in ComfyUI) using the saved checkpoint.
4. Relaunch the run launcher. The system auto-resumes from the exact step; the rescue daemon handles any residual startup stalls.

<!-- ARCHIVE MOVE: Superseded validation narrative and dated measurements from 2026-07-21 -->

## Design Doctrine: Fresh Process Requirement

The rescue mechanism must launch a **fresh process** to generate sufficient compute throughput. Sustained compute in a long-lived context is insufficient because idle contexts degrade performance alongside the stalled trainer.

- A fresh process on an idle card achieves ~77.5 TFLOP/s (dim 6144, bf16).
- A long-lived idle context degrades to ~7 TFLOP/s, rendering the rescuer itself stalled and unable to lift the trainer state.
- Performance expectations must reflect hardware limits: paper peak for bf16 is ~122 TFLOP/s, but real GEMMs deliver 60–80% of peak. A threshold of >100 TFLOP/s is invalid; 77.5 TFLOP/s on an idle GPU represents healthy performance.

Any GEMM burst operation MUST call `torch.cuda.synchronize()` inside the loop. Unbounded async enqueue without synchronization causes hangs, not memory starvation.

## Tooling and Execution

Both tools reside on Mothership at `F:\ai-toolkit\`.

### Payload: `p20_rescue_burst.py`

This script runs in a fresh process context. It supports 6144×6144 and 6144×18432 shapes, synchronizes every iteration, halves dimensions on OOM (floor 2048), and prints achieved TFLOP/s.

**Manual Execution:**
```bash
F:\ai-toolkit\venv\python.exe p20_rescue_burst.py 6144 25
```

### Watcher: `p20_stall_rescue.py` v3

This watcher imports no torch and holds no GPU memory. It reads telemetry CSV to detect stalls.

**Stall Definition:**
`power < 300 W AND clk > 2900 AND load > 90 AND step present`

The clock term excludes checkpoint saves, which exhibit ~1448 MHz / 90 W / 18% load.

**Operational Parameters:**
- CONFIRM: 2 rows
- COOLDOWN: 240 s
- Detection floor is telemetry cadence (~31 s/row). Reaction time is ~35–65 s. Faster reaction requires speeding up the LOGGER, not polling the daemon more aggressively.

**Execution Constraints:**
Do NOT launch via `Start-Process` over SSH. Windows sshd kills `Start-Process` children on session exit, causing the daemon to vanish silently without traceback.

**Correct Launch Methods:**
1. Local Console:
   ```powershell
   Start-Process F:\ai-toolkit\venv\python.exe -ArgumentList '-u','F:\ai-toolkit\p2_stall_rescue.py','15' -WindowStyle Hidden
   ```
2. SSH Foreground Command (keep session open):
   ```bash
   ssh mothership "F:\ai-toolkit\venv\python.exe -u F:\ai-toolkit\p20_stall_rescue.py 15"
   ```
3. Scheduled Task.

**Control:**
- Stop cleanly by creating: `New-Item F:\foundry\var\state\telemetry\STOP_RESCUE`
- Log location: `F:\foundry\var\state\telemetry\stall_rescue.log`

## Safety Rails

The daemon implements the following safeguards against false positives and resource exhaustion:
1. **Telemetry Staleness Check:** Prevents infinite rescues against an idle card if the logger dies (frozen stalled row).
2. **Non-blocking Execution:** Uses `Popen` with a 180 s hard kill limit.
3. **Effectiveness Grading:** Evaluates post-rescue power readings. The daemon self-disables after 3 consecutive useless rescues to prevent unattended GPU burn.
4. **Stop Flag:** Respects the STOP_RESCUE state file.

### batch_size performance characteristics and design constraints

Benchmark data from 100-step runs on saturated steps (power >350 W) confirms that QLoRA with AMD ROCm on Windows benefits from `batch_size` = 2 when the system remains in a saturated, memory-bound state. The per-step weight dequantization is amortized effectively; doubling the work increases step time by approximately 38%, not 100%. Memory load (`mem_load`) doubles (23% to 44%), corroborating that data movement scales with batch size.

**Design constraint:** `batch_size` > 1 is only viable if the system maintains saturated state. When stalls occur, `batch_size` = 2 degrades performance by approximately 1.47× per sample compared to `batch_size` = 1 (70.36 s vs. 47.98 s per sample). The stall penalty scales with work per step; larger batches suffer greater absolute penalties during stalls.

**Operational requirements:**
- Fix stall conditions before increasing `batch_size`.
- Retune learning rate (LR) for any change in `batch_size`, as it alters effective batch size and quality characteristics.
- Monitor VRAM headroom (~147 MB available); `batch_size` = 2 competes with selective checkpointing for VRAM resources.

<!-- ARCHIVED: Superseded by current performance baseline and priority matrix in Reference/LLM-Perf-Baseline.md -->

## Training Runbook and Performance Characteristics

### VRAM Management
VRAM probe prints (`[P20-DSP]`/`[P20-FWD]`) must be removed from the clean shareable patch set.
The allocator is configured with `expandable_segments:True`. This reclaims fragmentation (observed reduction from 3.45 GB to 0.97 GB on resume). A residual ~0.9 GB spill remains as a hard floor, determined by base memory exceeding headroom.

### Performance Characteristics
The launcher (`F:\ai-toolkit\run_riley.bat`) uses the expandable-segments-only configuration. Do not enable TunableOp; it causes HIP driver deadlocks on this ROCm build.

Training speed varies by run phase:
- **Early run:** ~50–58 s/it.
- **Late run (after ~1000–1250 steps):** Speed increases to ~12 s/it. This acceleration is attributed to thermal/clock ramp or deep framework warmup.

Speed does not degrade across reboots. Fresh post-reboot runs start at ~53 s/it, identical to sustained late-run rates before warmup effects diminish. TunableOp tuning (`PYTORCH_TUNABLEOP_ENABLED=1`) provides zero steady-state speedup on default hipBLASLt kernels and introduces instability.

Expected runtime for a 2000-step run:
- ~10–13 hours if the run achieves late-stage warmup acceleration.
- Up to ~29 hours if warmup acceleration does not occur.

### Pending Validation
Validate the Mistral uint4 cache load path end-to-end on the next launch. This path is designed as a fail-safe but requires exercise for confirmation.

## GPU Context Poisoning and Remediation

Stalls during high-latency renders are caused by cross-generation collisions that poison the GPU context (MES class). Rebooting does not resolve this state; terminating the poisoned context does. Daemon v4.9 automates termination and logs a discriminator verdict for each event.

The architecture uses a single ComfyUI queue (`:8188`). Worker v1.11 waits for verified-idle Ollama responses to prevent contention with active contexts.

**Performance Baseline:**
* True card ceiling: ~76 TFLOP/s.
* Flip boundary 14 corresponds to 18% of the true ceiling.

## Boot-time Optimization: Checkpoint Resume Acceleration

Checkpoint resume latency is optimized to approximately 49 seconds, down from ~17 minutes for training launch and checkpoint resume operations. This optimization is implemented via `FIX-01` through `FIX-08` across four ai-toolkit source files. Application of these fixes is gated on the global `P20_RESUME_MODE` environment variable.

> **Design Fact**: The `P20_RESUDGE_MODE` gate controls access to the optimized resume path. The `FIX-01` through `FIX-08` mnemonics identify the specific code changes in ai-toolkit source comments.

### Timing instrumentation and phase reporting

The training process logs wall-clock timestamps (`HH:MM:SS.mmm`) via `[P20-TIMING]` prints at every phase of `BaseSDTrainProcess.run()` leading into `hook_before_train_loop()`.

Phase identifiers follow the waypoint convention:
- Top-level phases: `WP1` through `WP12`
- Sub-steps within a phase: lettered suffixes on the parent number (e.g., `WP2a`, `WP2b`). Sub-steps inside called methods continue the letter sequence from the enclosing phase's slot (e.g., Mistral uint4-cache-load flow occupies `WP2h` through `WP2n`; `load_te` return is `WP2o`).
- Fast-path exits: appended with `-FAST` suffix (e.g., `WP7f-FAST`, `WP10-FAST`).

The `BOOT_MARKERS` table in `p20_foundry.py` reads the tail of the trainer log to determine the latest phase. The `foundry` interface reports this state as "BOOTING - <phase>", replacing silent boot states with active status feedback.

### Fix stack design (8 fixes)

The training pipeline applies eight fixes to reduce launch time and avoid redundant device transfers. All fixes are shipped and active.

**FIX-01 SKIP-QDEV-MOVE**
In `flux2_model.py` (`load_model`, uint4-cache branch), the call to `transformer.to(self.quantize_device)` is skipped when `low_vram` is set (default). The cache-branch previously moved the transformer to the quantize device before immediately parking it back to CPU. Skipping this step avoids unnecessary transfer overhead.

**FIX-02 UNET-CPU-GUARD**
In `SDTrainer.hook_before_train_loop`, the call to `quanto uint4 .to('cpu')` is guarded by a device check on the first parameter. If the model is already on CPU, the operation is skipped. Calling `.to('cpu')` on an already-CPU model is not a no-op; it triggers tensor operations that cause significant latency (~1m 30s on the 19 GB base).

**FIX-03 LATENTS-FAST**
In `dataloader_mixins.cache_latents_all_latents`, a fast-path exists for pre-cached latents. At method entry, the code checks `os.path.exists()` for every latent path. If all paths exist and `to_memory` is not set, the function sets per-file metadata and returns immediately. This bypasses the device shuttle sequence (`save_device_state` → move Mistral CPU→GPU → loop → restore_device_state GPU→CPU), which otherwise adds ~6m 30s overhead.

**FIX-04 RESUME-FLAG**
At entry to `BaseSDTrainProcess.run`, the environment variable `P20_RESUME_MODE='1'` is set if:
(a) a `<name>_*.safetensors` checkpoint exists in `save_root`, AND
(b) the SDTrainer embeds cache exists at the global path.
The function also exports `P20_EMBEDS_CACHE_PATH` and `P20_MISTRAL_CACHE_HIT`. Detection is trigger-word-keyed; `P20_RESUME_MODE` is the single signal downstream fixes use to determine resume state.

**FIX-05 EMBEDS-CACHE + FIX-05 EXT**
In `SDTrainer.hook_before_train_loop`, three prompts are encoded on boot: `unconditional_embeds`, `cached_blank_embeds`, and `cached_trigger_embeds`. These are deterministic given `(Mistral weights, unconditional_prompt, trigger_word)`.
*   **First run:** Embeds are computed and saved to disk via `torch.save` with a `_p20_key` field. The encode block (including `text_encoder_to(cuda)` and `unload_text_encoder`) executes fully.
*   **Resume:** Embeds are loaded from disk via `torch.load`. The three attributes are populated, and the encode block is skipped entirely. This avoids the ~2m 33s overhead of unloading the text encoder. WP9d has its own cache-load path so Mistral does not move for that single encode.

**FIX-06 SKIP-MISTRAL-MOVE**
Under `P20_RESUME_MODE=1`, the following calls are skipped:
*   `text_encoder.to(self.device_torch)` in `flux2_model.load_te` (line 148)
*   `text_encoder[0].to(self.device_torch)` in `load_model` (line 344)
This avoids the 12 GB uint4 Mistral CPU→GPU move (~1m 26s measured). It is safe because FIX-05’s cached embeds ensure `encode_prompt` is not called during boot or training.

**FIX-07 GLOBAL-EMBEDS-CACHE**
The SDTrainer embeds cache location is global, per-trigger: `F:\foundry\var\cache\cond_cache\p20_embeds_<trigger>.pt`. This replaces the previous per-project location (`save_root/p20_sd_trainer_embeds.pt`). The cache is guarded by the foundry’s `.captions` fingerprint sidecar; unverifiable fingerprints are treated as stale and retired at PREFLIGHT. A first-ever run for any trigger populates the cache once. All subsequent training sessions for that trigger (any project, resume, or fresh start) use this cache. FIX-04’s `P20_EMBEDS_CACHE_PATH` env var passes the resolved path to downstream sites.

## Flux2 Boot Stack: Model Memory Management and Cache Preconditions

The boot stack requires that all text embeddings for a trigger be cached prior to the `WP7g` (text encoding) phase. The design constraint is **one model on the GPU at a time**:

1.  The base model (~20 GB) and the Mistral text encoder (~12 GB uint4) must not reside on the GPU simultaneously.
2.  Standard operation assumes the base model is parked on CPU during text embedding caching to prevent OOM errors on 24 GB cards (32 GB total requirement vs. 24 GB available).

### Cache Miss Handling
If a dataset contains uncached captions (a "first-ever trigger"), the boot sequence must enforce memory isolation:
1.  Park the base model on CPU during `WP7g`.
2.  Load Mistral alone to cache text embeddings.
3.  Unload Mistral.
4.  Allow `[P20]` to move the base model back to GPU.

This preserves performance (60–90 s cold move saved) in common cases where embeds are cached, while preventing crashes when new text must be encoded. See PROJECT-BACKLOG §"A FIRST-EVER-TRIGGER run cannot cache its own text embeddings" for implementation details.

### FIX-08: SKIP-MISTRAL-LOAD Optimization
**Condition:** `P20_RESUME_MODE=1` AND the embeds cache exists.

**Action:** Skip all Mistral loading steps (`torch.load`, `quantize()`, `load_state_dict`, `freeze`, move to GPU).

**Mechanism:** Return a `_P20StubTextEncoder` object that implements the required interface (`.to()`, `.eval()`, `.requires_grad_()`). The stub raises an error if `__call__` is invoked, which indicates an attempt to encode against uncached embeds.

**Justification:** Under current config (`disable_sampling=true` + all boot embeds cached), no `encode_prompt` calls occur at boot or during training. This saves ~2m 30s (48s load + 4s freeze + 1m 26s CPU→GPU + downstream shuffle).

### FIX-09: Diagnostic Waypoints for Base Model Movement
**Location:** `BaseSDTrainProcess.py` network-setup loop.

**Implementation:** Added waypoints (`WP3-N1..N4`, `WP3a1..WP3a2`) around `network.force_to`, `network.apply_to`, and `network.prepare_grad_etc` to identify where the base model moves to CUDA.

**Finding:** The base model moves to CUDA before `force_to` due to `NetworkClass(unet=...)` instantiation at `BaseSDTrainProcess.py:1728`.

**Resolution:** Do not prevent this early move. Because the base ends up on CUDA anyway, preventing it would shift the cost back to `WP11`, where `[P20]` performs a resident-move (~2.7s) instead of a cold move (60–90s). This behavior is retained for performance optimization in cached scenarios.

### Deployment paths for Flux2-32B QLoRA AMD/ROCm fixes

All fixes are deployed to the Windows host via `scp` with hash verification. They coexist with the standard recipe (uint4 cache, `[P20]` resident-move, low_vram/quantize block) without conflict.

| File Path | Fixes Applied |
|---|---|
| `F:\ai-toolkit\jobs\process\BaseSDTrainProcess.py` | FIX-04, FIX-09 |
| `F:\ai-toolkit\extensions_built_in\sd_trainer\SDTrainer.py` | FIX-02, FIX-05, FIX-05 EXT |
| `F:\ai-toolkit\extensions_built_in\diffusion_models\flux2\flux2_model.py` | FIX-01, FIX-06, FIX-08 |
| `F:\ai-toolkit\toolkit\dataloader_mixins.py` | FIX-03 |

These fixes deploy via `scp` + sha256 verification (see the FIX table above; general discipline in Doctrine-Build-and-Migration-Discipline).
