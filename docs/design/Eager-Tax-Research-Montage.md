# Eager-Mode Inference on Windows + ROCm (RDNA3)

PyTorch inference on Windows with ROCm backends is launch-overhead bound, not compute-bound, for the RDNA3 gfx1100 architecture.

## Design Doctrine

To maximize throughput on this stack, kernel gaps must be minimized. Theoretical ceiling improvements are approximately 2x by closing these gaps alone.

## Conformance

**Stack:** Windows 11, PyTorch (ROCm nightly), triton-windows.
**Instrument:** See per-session journal for current benchmark measurements.
**Observation:** Inference is bound by launch overhead, not GEMM compute capacity.

## Eager-mode acceleration strategies

### Compiler support on Windows ROCm
Triton-windows includes an AMD/HIP backend. As of version 3.7.1-windows.post27, support for recent AMD consumer GPUs is generally stable, with one known RDNA3 fused-MoE codegen fault irrelevant to current models. `torch.compile` via Inductor on Windows+AMD has historically hit LLVM assertion failures; status verification requires direct testing.

### Concurrency and graph capture
Running multiple frames on separate HIP streams or pipelining stages hides launch gaps, potentially achieving up to ~1.8x throughput in pure eager mode at 53% utilization. hipGraph capture (`torch.cuda.graphs`) exists in HIP to amortize launch overhead. Graph capture compatibility with Windows TheRock wheels requires verification.

### WSL2 production path
WSL2 is an officially supported production path for ROCm 7.2.1 + PyTorch 2.9.1 on RX 7900 XTX (Adrenalin 26.1.1 WSL driver). It provides access to the Linux toolchain: working `torch.compile`, aotriton SDPA, and TunableOp/hipBLASLt. WSL2 inference runs below native Linux performance and benefits from larger batches; it reduces kernel count via compilation rather than reducing launch overhead.

### ONNX Runtime on Windows-AMD
Microsoft ships an AMD MIGraphX execution provider as a Windows 11 26H1 AI component (v2.2606.1.0). A new plugin-EP architecture (`onnxruntime-ep-amdgpu`) supports AMDGPU/ROCm, DirectML, and MIGraphX providers. BiRefNet is an anti-case due to poor export of deform_conv and resize ops; ONNX inference for this model is slower than PyTorch. Hiera/SAM2-class encoders export cleanly.

### ZLUDA exclusion
ZLUDA is excluded from consideration. Commercial funding ended at v6 (reverted to hobby status). PyTorch support is incremental and not robust. `torch.compile` and flash-attention paths are explicitly incompatible.

### Eager-mode micro-optimizations
The following zero-risk optimizations stack cumulatively:
* bf16 autocast + channels_last (convs)
* MIOpen find-mode tuning (fully supported on Windows in TheRock)
* TunableOp/hipBLASLt GEMM tuning (5–22% gain observed on Linux; Windows untested)
* HAGS toggle
* Batching
* `torch.inference_mode`
* Elimination of per-frame Python/sync overhead

### Priority actions
1. Execute two-stream pipelining and hipGraph-capture experiments (no new dependencies).
2. Upgrade triton-windows to 3.7.1.post27 and test `torch.compile` on the current nightly.
3. Stand up WSL2 as a controlled A/B baseline.

Expected practical ceiling: ~2x throughput improvement (6.5 → ~3.3 s/frame), reachable via combination of top three strategies.

## torch.compile on Windows + ROCm (gfx1100)

**Design Fact:** The lab uses `torch.compile` to fuse kernels for launch-bound workloads, targeting a 1.3–2x speedup. The implementation relies on TheRock wheels and triton-windows.

**Stack Requirements:**
- **Triton Version:** v3.7.1-windows.post27 or newer. This version marks AMD consumer GPU support as "generally stable" with one known RDNA3 fused-MoE kernel fault (triton#10808) that does not affect vision models.
- **PyTorch Version:** Torch 2.12 nightly (July 14 build) or newer. This provides Inductor support for Windows/clang-cl via TheRock wheels.
- **Environment Variables:** Set in the virtual environment before compilation:
  ```bash
  CC=clang-cl
  CXX=clang-cl
  ROCM_HOME=$(rocm-sdk path --root)
  DISTUTILS_USE_SDK=1
  ```
- **Initialization:** Run `rocm-sdk init` in the venv.

**Implementation Procedure:**
1. Ensure triton-windows is at v3.7.1-windows.post27+. The official repository is `triton-lang/triton-windows`; previous repositories (`woct0rdho/triton-windows`) are archived.
2. Wrap models using fixed input shapes:
   ```python
   torch.compile(m, mode="max-autotune-no-cudagraphs", dynamic=False)
   ```
3. If `max-autotune` causes crashes (known history on RDNA3), fallback to standard compilation or disable autotune.
4. Verify SDPA dispatch if applicable: Check if the nightly build dispatches SDPA to aotriton using `torch.backends.cuda.sdp_kernel` or by profiling a single attention op. Note that aotriton gfx1100 support is promoted out of experimental for both Linux and Windows wheels.

**Risk Mitigation:**
- **LLVM Assertion Failures:** Historical issue with Inductor on Jan 2026 builds; verify against current nightly.
- **Pointer Access Faults (TheRock #1278):** `torch.compile` may fail with "Pointer argument (at 0) cannot be accessed from Triton" on gfx1100/Windows. This is version-sensitive. If encountered, verify toolchain versions match the working recipe.
- **Autotune Crashes:** Start without `max-autotune` if flaky behavior persists on RDNA3 hardware.

**Measurement Protocol:**
- Record per-frame JSONL (s/frame) for A/B testing.
- Record power/load CSV; expect wattage to rise toward 300+ if kernel gaps close.
- Count compiled graphs and graph breaks using `torch._dynamo.utils.compile_times()` and `TORCH_LOGS=graph_breaks`.

## Avenue 2: hipGraph capture and multi-stream pipelining (eager, no compiler)

### Design Doctrine
The workload uses fixed shapes (2048² and 1024²) with a static operator sequence. This structure mandates capture-once/replay-per-frame execution via `torch.cuda.graphs` wired to hipGraph on ROCm. The system runs two HIP streams concurrently to keep two frames in flight, converting idle time into throughput. Keyer and segmentation stages pipeline across streams (keyer on stream A frame N while segmentation on stream B frame N-1).

### Implementation Constraints
* **Platform:** Windows support in TheRock wheels is undocumented. Assume no support until proven otherwise.
* **API Limitations:** ROCm lacks newest CUDA-13.1 graph-annotation APIs, but this does not affect plain capture/replay.
* **Known Quirks:** Event-record-inside-graph behavior requires handling per rocm-systems #2380.

### Operational Procedure
1. **Capture:** Execute static buffers with warmup iterations. Disable dropout and dynamic control flow (inference mode).
2. **Replay:** Update inputs via `static_input.copy_(frame)` for each frame.
3. **Stream Management:** Maintain two HIP streams to sustain concurrent frame processing.

### Conformance & Measurement
* **Throughput Target:** ~1.8x gain (bounded by 53%→100% utilization headroom). Graphs eliminate per-kernel CPU dispatch cost; literature reports 1.5–2x+ for small-kernel regimes.
* **Metrics:**
  * Frames/sec (not s/frame; latency per frame may rise while throughput doubles).
  * Power/load CSV: sustained ~100%/300W+.
  * VRAM high-water mark.
* **Risk Profile:**
  * Graph capture failure on Windows is expected to fail fast at capture.
  * Stream concurrency poses minimal risk; worst-case VRAM pressure from two frames in flight is accommodated by 24GB capacity (models are small relative to LLMs).

## Avenue 3 — WSL2 + ROCm

**Performance architecture:**
WSL2 inference incurs per-launch overhead via dxgkrnl/GPU-PV, which is higher than native Linux. The design rationale for using WSL2 is that `torch.compile` reduces kernel launch counts sufficiently to mitigate this overhead. Eager workloads (e.g., per-frame batch=1) may perform slower under WSL2 than on native Linux due to unmitigated launch costs.

## Design Doctrine: Graph Execution for Latency Reduction

ONNX Runtime (ORT) executes a pre-optimized static graph, reducing host round-trips per frame compared to eager PyTorch. This addresses the latency tax of frame-by-frame inference.

### Execution Provider Policy

- **DirectML:** Mature on Windows-AMD; not deprecated. Active investment continues via Windows ML.
- **MIGraphX:** Supported on RDNA3-Windows via plugin architecture (requires ORT ≥1.24, Python ≥3.12). Binary releases are pending; distribution occurs through Windows Update components.
- **ROCm:** Classic ROCm Execution Provider remains Linux-only. AMDGPU (ROCm-based) provider exists in the plugin repo but is distinct from the classic Linux EP.

### Model-Specific Conformance Rules

**BiRefNet: Do Not Export via Standard Pipeline**
- ONNX inference is documented as ~75–90% slower than PyTorch on A100/CUDA.
- Causes: `torchvision::deform_conv2d` unsupported by standard exporters (requires community workaround via `grid_sample`); opset-19 Resize falls back to CPU on some Execution Providers.
- Action: Keep BiRefNet in PyTorch. Test pre-exported community ONNX only if numerical parity is verified against reference frames; do not assume compatibility.

**Hiera-Base Encoder: Export Candidate**
- SAM2 image encoder (Hiera) has maintained exporters that split encoder/decoder.
- Action: A custom keyer head on top of Hiera may export if it avoids exotic ops. Verify numerical parity.

### Measurement and Validation

- **Performance:** Measure per-frame s/frame via A/B testing against eager PyTorch baseline.
- **Accuracy:** Generate output diff maps vs. torch reference on a fixed frame set. Visual verification is required due to subjective matting/keying quality; PSNR alone is insufficient.
- **Resource Utilization:** Monitor power CSV and GPU utilization to confirm graph execution efficiency.

## Eager-mode micro-optimization directives

All optimizations below are individually testable and reversible. Implementation follows design doctrine: verify conformance via profiling or benchmarking; do not assume performance gains.

### Pipelining
Two-in-flight pipelining applies to eager mode. See `Avenue 2` for implementation details.

### Batch sizing per forward pass
Increase batch size to maximize GPU utilization, constrained by VRAM capacity:
- **Hiera**: Batch 2–4 accommodates 2048² activations on 24GB VRAM.
- **BiRefNet**: Batch 2+ accommodates 1024² activations.
Output remains deterministic per frame despite frame-ordering interactions with the render pipeline. AMD WSL guidance to increase batch size applies natively.

### Precision casting
Apply `torch.autocast('cuda', dtype=torch.bfloat16)` globally to leverage RDNA3 bf16 throughput (77 TFLOP/s) and halve memory traffic compared to fp32. Verify no silent fp32 fallbacks by profiling dtype of top operations. Perform quality eye-checks on matting edges after casting.

### Inference mode
Use `torch.inference_mode()` instead of `no_grad()` to remove autograd bookkeeping overhead on every operation.

### MIOpen kernel tuning (BiRefNet)
1. **Baseline**: `torch.backends.cudnn.benchmark=True` triggers MIOpen find.
2. **Exhaustive tune**: Set `MIOPEN_FIND_MODE=NORMAL` and `MIOPEN_FIND_ENFORCE=SEARCH` for a one-time exhaustive search. This writes a user find-db reused on subsequent runs. Verify persistence of the find-db file (check `%LOCALAPPDATA%` or ROCm home).
3. **Conformance**: Confirm MIOpen support on Windows via TheRock (`windows_support.md`).

### Memory format
Convert conv-heavy BiRefNet models and inputs to `torch.channels_last` memory format (`model.to(memory_format=torch.channels_last)`). MIOpen NHWC kernels typically outperform default formats on modern hardware; measure performance on gfx1100/Windows before adopting as standard.

### GEMM tuning (TunableOp / hipBLASLt)
Enable `PYTORCH_TUNABLEOP_ENABLED=1` and `TORCH_BLAS_PREFER_HIPBLASLT=1`. Verify that TunableOp writes a tuning CSV to confirm activation; on Windows, it may no-op. Expect 5–22% improvement on Linux; validate actual gain on gfx1100.

### HAGS toggle
Toggle Hardware-Accelerated GPU Scheduling (HAGS) at the Windows OS level to shift from WDDM software scheduling to on-GPU scheduling. This reduces per-launch tax. Test both states. Close all 3D/video apps sharing the queue during renders.

### Host-side overhead reduction
Profile CPU-side bottlenecks with `torch.profiler` (47% idle time indicates serial CPU tax). Target:
1. Eliminate per-frame `.item()`/`.cpu()` syncs.
2. Pre-allocate tensors and use `copy_()` instead of per-frame allocation.
3. Move image decode to a prefetch thread.
4. Use pinned memory (`pin_memory()`) and non-blocking transfers (`non_blocking=True`) for H2D copies.

### SDPA backend verification
Log `torch.backends.cuda.sdp_kernel` selection to determine if `F.scaled_dot_product_attention` dispatches to aotriton/flash or falls back to the math path on Windows gfx1100.
- If math-path: Adopt triton-windows flash-attention route from `Avenue 1` without full `torch.compile`.
- **RDNA3 constraint**: Disable autotune (`FLASH_ATTENTION_TRITON_AMD_AUTOTUNE=FALSE`). Community benchmarks indicate SageAttention may outperform default or flash-attn on RDNA3; validate via profiling.

### Regression containment
Ambient risk exists for Windows-ROCm performance regressions (e.g., ROCm #5834, #5725). Maintain a June nightly environment (torch 2.10+rocm7.14.0a20260611) in a separate venv for A/B testing wheel-vs-wheel regressions.

## Experiment protocol and test matrix

All performance experiments use a fixed 50-frame test clip. Metrics recorded per frame are `s/frame` in JSONL format; power, clock, and load data are recorded in CSV over a 10-second window. Reports must include the median `s/frame`, watts, GPU-load percentage, and a qualitative eye-check of output quality on three reference frames. Experiments vary only one parameter at a time.

| # | Experiment | Expected gain | Cost | Measurement / success criterion |
|---|-----------|---------------|------|-------------------------------|
| 1 | `torch.inference_mode` + pre-allocated buffers + pinned/async H2D + kill per-frame syncs (profile first with torch.profiler) | 5–15% | 0.5 day | s/frame ↓; profiler shows gaps between ops shrink |
| 2 | bf16 autocast audit (confirm no fp32 fallbacks) + `cudnn.benchmark=True` | 0–20% (if any fp32 lurks) | hours | s/frame; dtype histogram from profiler; quality eye-check |
| 3 | **Two frames in flight on two streams** (or stage pipelining keyer‖seg) | **1.3–1.8x throughput** | 1 day | frames/min ↑; GPU load sustained >85%; watts → 300+ |
| 4 | **hipGraph capture/replay** of each model's forward (static shapes) | 1.3–2x | 0.5–1 day (go/no-go in 30 min) | capture succeeds on Windows; replay s/frame; if capture throws → document and stop |
| 5 | Batch=2–4 frames per forward (if pipeline tolerates latency) | 1.2–1.6x throughput | 0.5 day | frames/min; VRAM high-water < 22GB |
| 6 | MIOpen exhaustive tune for BiRefNet (`MIOPEN_FIND_ENFORCE=SEARCH`, one long run, then persistent find-db) | 5–20% on the seg stage | hours (one slow tuning pass) | seg-stage ms ↓ on second run; find-db file persists across reboot |
| 7 | TunableOp + `TORCH_BLAS_PREFER_HIPBLASLT=1` | 5–22% on GEMM-heavy keyer (Linux figures; Windows unproven) | hours | tuning CSV written; s/frame ↓; no-op = finding too |
| 8 | HAGS on↔off A/B | 0–10% | 30 min + 2 reboots | s/frame both states |
| 9 | **Upgrade triton-windows → 3.7.1.post27 (triton-lang org) + try `torch.compile`** on torch 2.12 nightly (env: clang-cl, ROCM_HOME, DISTUTILS_USE_SDK=1; start `mode="default"`, `dynamic=False`; then max-autotune-no-cudagraphs) | 1.3–2x if it compiles | 0.5 day; go/no-go fast | compiles without LLVM assert; graph-break count; s/frame; file a TheRock issue either way — they're actively triaging gfx1100 Windows reports |
| 10 | SDPA backend audit; if math-path → triton flash-attn (autotune OFF) vs SageAttention on the keyer | 10–30% on attention stage | 1 day | per-stage timing; quality eye-check (Sage is int8-flavored — verify matte edges) |
| 11 | **WSL2 stand-up**: Ubuntu 24.04 + Adrenalin 26.1.1 WSL driver + ROCm 7.2.1 + torch 2.9.1; run identical clip eager, then compiled | eager: possibly ±0; compiled: 1.3–2x vs Windows eager | 0.5–1 day | same-clip A/B: native-eager vs WSL-eager vs WSL-compiled; decide if WSL becomes the render path |
| 12 | ONNX export of Hiera keyer → ORT DirectML EP (and MIGraphX EP when consumable) | 1.2–1.8x on keyer stage (speculative) | 2–3 days incl. parity validation | keyer-stage ms; per-pixel diff vs torch reference; abort if export friction exceeds a day |
| 13 | (Moonshot) Native Linux partition for render jobs if WSL2 proves the Linux stack wins big but WSL overhead eats it | up to full Linux-stack gain | days + operational split | same-clip A/B vs WSL2 numbers |
| — | ZLUDA | — | — | **Rejected** — unfunded hobby project, torch support partial, compile+flash-attn incompatible |

Experiments #1–#5 are pure-eager and compose; execute them first to establish a new baseline before compiler experiments (#9, #11) to avoid double-counting gains. Experiments #3 and #4 partially overlap (both fill idle gaps); measure #4 on top of #3 and retain the superior combination.

## Instrumentation Standards

Every experiment run produces standardized telemetry artifacts:
1. **Power/Load Metrics:** 10-second cadence CSV logs for power, clock speed, load average, and VRAM usage.
2. **Frame-Level Metadata:** Per-frame JSONL logs recording seconds-per-frame (`s/frame`).
3. **Joined Data:** Post-processing joins per-stage power/load data with frame metadata.

## Required Telemetry Additions

The following instrumentation gaps must be closed for current campaigns:

### Sub-second GPU Busy Sampling
10-second cadence averages obscure gap structure (e.g., distinguishing many 100µs gaps from a single 3s stall). Implement a 100ms-cadence load sampler during a 10-frame window to characterize gap distribution. 

### Host-side Op/Launch Tracing
Attribute idle time to Python, dispatch, or GPU serial execution by capturing CPU and CUDA/HIP activities. Use `torch.profiler` for per-op wall time and inter-op gaps. Add a `--profile` flag to the runner to dump one Chrome trace every N frames. 

### Per-stage GPU Timing via Events
Wall-clock timing conflates CPU wait with device execution. Replace or supplement wall-clock metrics with `torch.cuda.Event(enable_timing=True)` pairs around keyer, segmentation, and auxiliary stages. Log these event-pair durations into the existing JSONL.

### Kernel-level Profiling Constraints
Kernel-level profiling on Windows is unsupported (`rocprofiler` is Linux-only; Radeon GPU Profiler is graphics-oriented). Accept event-level granularity as the native standard for Windows runs. Perform any kernel-level deep-dive analysis inside WSL2/Linux where `rocprofiler` functions.

### Quality Regression Harness
Quantitative quality checks are required for experimental avenues that may shift output subtly (bf16, Sage, ONNX). Implement a fixed 3–5 frame reference set with automated diff-map generation. Ensure every experiment’s quality check reduces to a single command invocation.

### VRAM High-water Marking
Sample-based CSV logs do not capture peak VRAM usage caused by batching or streams. Log `torch.cuda.max_memory_allocated()` into the JSONL at clip end to record high-water marks per experiment.

## Source index

See `Journal/YYYY-MM-DD-claude.md` for the discovery narrative and evaluation of these sources.

The following external references govern configuration, compatibility, and performance tuning for ROCm, Triton, ONNX Runtime, and ZLUDA on Windows/WSL2/AMD hardware. Links are canonical; if a link breaks, verify the archive copy in `Archive/Eager-Tax-Research-Montage-pre-rewrite`.
