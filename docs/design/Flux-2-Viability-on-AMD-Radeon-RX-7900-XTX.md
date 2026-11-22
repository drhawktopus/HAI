# Flux 2 Viability on AMD Radeon RX 7900 XTX

Flux 2-dev is viable and reliable on the 7900 XTX GPU when using the optimized ROCm stack. The system supports repeated generation without deadlock or OOM errors, and survives mixed-workload cycles with LLMs.

## Optimization Stack

The viability relies on specific tooling and configurations installed on drive `F:`:
- **ComfyUI Instance:** `patientx-cfz/comfyui-rocm` (Torch 2.12+, ROCm 7.15).
- **Accelerators:** `triton-windows 3.7`, `sageattention 1.0.6`, flash-attn/aiter ROCm wheels, `ComfyUI-INT8-Fast-ROCM` node.
- **Build Tools:** VS Build Tools (MSVC 14.44 + Win SDK 10.0.26100) are required for Triton JIT compilation (`stdlib.h`).
- **Launch Environment:** Runs inside `vcvars64` environment, fully separate from the #19 ComfyUI instance.

## Performance Characteristics

- **Throughput:** Flux 2-dev GGUF-Q4 achieves ~15x speedup via Triton/torch.compile compared to eager mode (145 s/step).
  - Cold start (first gen, incl. load/compile): ~190–203 s.
  - Warm generation: ~93 s per image (~1.5 min).
- **Bottlenecks:** Attention mechanisms are not the bottleneck; FFN/linear layers dominate. `sageattention` and `flash-attn` provide no measurable speed or visual improvement on this hardware.

## Reliability and Memory Management

Flux 2-dev exceeds VRAM capacity (36 GB model > 24 GB VRAM), requiring encoder unloading. Repeated generation previously deadlocked due to VRAM fragmentation in the ROCm allocator.

- **Fix:** Set `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True` in `F:\comfyui-rocm\launch_flux2test.bat`. This resolves contiguous block allocation failures on reload.
- **Status:** Generation is repeatable and stable. No swap-wedge or OOM errors occur on the dev model.
- **Further Optimization:** Persistent tuned-kernel cache (`PYTORCH_TUNABLEOP_ENABLED=1`) or pinning Mistral encoder to CPU may reduce overhead, but are not required for stability.

## Mixed Workload Coordination

The system supports bidirectional coordination between 27B LLM chat and Flux 2 image generation on the shared GPU:
1. **LLM → Image:** Evict LLM (`keep_alive=0`) before launching ComfyUI.
2. **Image → LLM:** Free ComfyUI memory (`/free` command) before loading LLM, as idle ComfyUI retains ~18 GB VRAM.

**Integration Requirement:** The orchestrator must be updated to wire Flux 2’s generation path through the same evict/free coordination logic used for #19, pointing to the optimized F: bundle.

## Alternative Models

Flux 2-Klein (9B/4B) offers an int8 prequantized variant (`vistralis/*` + `bertbobson/ComfyUI-INT8_ConvRot`) at 9.4 GB. This fits fully in VRAM, eliminating swap entirely. Int8/Klein is an optional speed lever for sub-1.5 min/image targets, not a reliability requirement.

#### Engine selection for identity slots
Flux 2 dev-Q3 is the base model for the true-model path (LoRA). Flux.1 Kontext remains the engine for family-photo mode due to speed advantages (132 s vs 297 s cold start on D1 arm benchmarks at seed 424242). Flux 2 uses the same `ReferenceLatent` path as Flux.1, allowing drop-in substitution.

#### Training environment and feasibility
The training target is a 24 GB 7900 XTX GPU. QLoRA (4-bit NF4 base) with gradient checkpointing requires approximately 20–22 GB VRAM, fitting within the hardware constraints. The "80 GB" figure represents a comfortable/cloud standard, not a hard floor.

The host environment is Windows native. ROCm 7.2 enables native Windows support for AMD GPUs; Linux/WSL2 is the fallback path due to potential native-toolchain bugs. The gfx1100 architecture lacks Flash-Attn support; use Triton for attention mechanisms. The host has 32 GB RAM, which limits swap-friendly operations.

Execution sequence:
1. Build training environment (`ostris/ai-toolkit` with `cupertinomiranda/ai-toolkit-amd-rocm-support` fork).
2. Attempt native Windows QLoRA training first to avoid RAM constraints of WSL2.
3. If native fails, fall back to WSL2.
4. Execute heavy GPU operations during low-energy-cost periods (night trough); perform low-GPU setup tasks during price spikes.

Exclusions:
- No synthetic images or ChatGPT-4o family renders. Synthetic images are excluded to prevent poisoning identity with model hallucinations.
- `previews/` pool is contaminated with zoo/PC-build frames; never use it for gid→file joins. Use `final_candidates.csv` `path` col as the source of truth.

Dependencies:
- Install `pillow-heif` on the mothership virtual environment to handle HEIC inputs.

## Person-record schema design doctrine

The person record schema (`person_record.py`) defines the data structure for individual identity artifacts. The schema is definitive; enforcement logic is deferred per standing call.

### Field definitions

- **`models`**: A list of trained adapter entries. A person may have multiple image and video LoRAs plus retrains. Each entry derives from a `_MODEL_DEFAULT` template with the following attributes:
  - `kind`: `image_lora` | `video_lora`
  - `base`, `path`, `trigger`, `steps`, `rank`, `resolution`
  - `manifest`, `trained`, `status` (`training` | `ready` | `retired`)
  - `quality`, `notes`

- **`consent.scope`**: Defines usage permissions and validation.
  - `uses`: Subset of `CONSENT_USES` (`personal`, `named`, `public`, `commercial`).
  - `allowlist`: List of permitted entities.
  - Metadata: `granted_by`, `date`, `notes`.
  - Validation: `grant_consent()` sets `granted=True` only if any use is granted and validates uses against the scope.

### Helper functions

- **Store-only helpers**: `add_model()`, `set_arcface_anchor()`, `grant_consent()` modify records without enforcing policy.
- **Normalization**: `record()` deepcopies and normalizes `consent.scope`. This ensures partial or legacy records return a full shape, preventing `KeyError` in future gating code.

### Privacy doctrine (Avery)

Per decision cards:
- Avery’s consent is restricted to **"named"** usage with an allowlist.
- **Public and commercial uses are denied**.
- Personal use flows through the allowlist.
- This matches the "never post her renders" privacy posture.
- Consent confirmation with Avery is required before enforcement becomes meaningful.

### Conformance

- Current consumer: `identity.py` (`get_heritage`). Behavior is unchanged.
- Future TODOs (see PROJECT-20-ROADMAP):
  - Tier-gating and expressiveness clamping.
  - Consent enforcement wiring.
  - Automatic registration of models and computation of arcface anchors upon training completion.
