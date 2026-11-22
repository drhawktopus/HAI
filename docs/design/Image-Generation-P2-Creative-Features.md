# Image Generation: P2 Creative Features

**Style Presets and Restyle**
The system provides style presets for sketch, watercolor, oil, anime, and photo. Strengths for these styles are set to 0.5 based on A/B testing. Mono-style sources use grayscale input. Img2img is superseded by ControlNet canny restyle using union-sdxl-promax. Edges lock subject identity at full denoise strength to enable total style transform with zero identity loss. Mode selection (`cn`, `i2i`, `t2i`) is audited.

**Resolution and Wallpaper Support**
The system supports 2560x1440 (standard) and 3440x1440 (ultrawide) resolutions. RealESRGAN 4x tail handles upscale processing. Size parameters are auto-attached from user input. Wallpaper-class requests auto-upgrade to the best quality tier.

**Batch Processing**
Batches support counts of 1–4. Word-form backstops apply. Best-tier batch size is capped at 2. Seeds are prime-strided. All tags are returned in the response.

**Deterministic Quality Tiers**
Quality tiers are deterministic tool-side:
*   **Fast**: SDXL default.
*   **Best**: Flux, triggered by text-in-image content or explicit "best" request.
*   **Graceful degrade**: Applied under quota constraints.

Doctrine: Tool-side processes handle deterministic tasks; the 8B model handles creative components only.

**Tiled Diffusion (4K/Print)**
Tiled diffusion is designed for real 3840x2160 and print detail. RealESRGAN is used as an enlarger but cannot add detail; tiling re-diffuses in overlapping patches to generate new detail. The processing shape is: base image → RealESRGAN 2x upscale → tiled img2img refine (denoise 0.25–0.35) → Lanczos resize.

Implementation uses Ultimate-SD-Upscale-class custom nodes. A new "4k" size preset is planned (the owner's build order). Quality assurance focuses on seams in gradient skies. VRAM usage must be measured before enabling for all users.

## Identity & inheritance: Flagship configuration

### Inheritance engines (The RUNG Ladder)
Inheritance quality is controlled by the `engine` parameter. The canonical naming scheme replaces previous internal labels (0/2a/2b/2c).

| Rung | Engine ID | Description |
|---|---|---|
| Comparator | `baseline` | Plain blending. No inheritance logic. Used as a yardstick for comparison. |
| Rung 1 | `lmk` | Mosaic sampling. Per-region parent feature picks based on 478 landmarks. |
| Rung 3 | `neural` | Neural pixel fitting. Uses MICA arcface estimator (pixel-level reading) within FLAME space. Model derived from `mica.tar`, converted to ONNX. Verified accuracy margin: 7e-7. |

**Engine election**: The default engine is determined by the owner’s vote following a bake-off comparison. Rung 2 and Rung 3 share the same inheritance input for estimator-only comparison. A rerun at strength ~0.8 is available if needed. Losers remain selectable via explicit `engine=` override.

### Genetics-informed mode
Genetics logic is implemented in `genetics.py` as an orthogonal overlay on Rungs 2 and 3. Traits are measured from parent photos to ensure heritage-honest results (e.g., coily-haired parents do not produce straight-haired children).

#### Tier-1 traits (Active)
Detected via landmarks/pixels without new dependencies.
*   **Sex**: 50/50 probability.
*   **Skin tone**: Real-valued luminance blend between parents.
*   **Features**: Lip fullness, nose width, eyebrow thickness.
*   **Hair texture**: Straight/wavy/curly/coily via edge-energy analysis.
*   **Implementation**: Trait rolls lead the child prompt to ensure rendering priority.

#### Probability odds engine
*   **Primary**: Live web search + Qwen3:14B to generate probability distributions per pairing. Results are cached.
*   **Fallback**: Static tables for offline operation.
*   **Failover chain**: Brain → BigHonker → Mothership.
*   **User interface**: Story surfaced to user upon calculation.

### Configuration axes (Routing & Effort)
The system uses two configuration axes, bound per-worker-thread in `tools.py` via `set_request_effort/_routing`. This pattern mirrors cancellation handling and requires no signature changes to `chat()` or `run_tool()` in `app.py`.

#### Routing Axis (`request_routing`)
Determines which estimator reads the parent photos. Chain degrades from neural → flame → lmk as assets allow. Explicit `engine=` overrides automatic selection.

| Routing Value | Selected Engine |
|---|---|
| `quick_8b` | Rung 2 (FLAME coordinate fit) |
| `auto` or `force_27b` | Rung 3 (Neural MICA) — De-facto everyday default |

#### Effort Axis (`request_effort`)
Determines genetics depth and detection capabilities.

| Effort Level | Genetics Odds Source | Trait Detection Scope | Extras |
|---|---|---|---|
| `Normal` | Static tables | Tier-1 only | None |
| `Hard` | Researched (web + 14B) | Tier-1 + Tier-2 VLM | None |
| `Absurd` | Researched | Tier-1 + Tier-2 VLM | Multi-sample siblings, gap-filling questions (not yet built) |

### Gap-filling flow
After initial measurement, HAI flags unmeasurable traits (e.g., height, build). Instead of silent guessing, the system offers to ask clarifying questions or request fuller photos.

### Open items
*   Final engine vote confirmation.
*   Multi-upload picker UX for two named parents.
*   Avery access and quota policy definition.

<!-- FILE: Runbook/Image-Generation-Pipeline.md -->

## Image Generation Pipeline Configuration

The image generation pipeline supports interchangeable rendering backends. The current active configuration is defined by the following design facts:

### Checkpoint Selection
The primary checkpoint for SDXL-based rendering is **RealVisXL_V5.0**. The `IMG_SDXL_CHECKPOINT` variable must point to this model. SDXL base remains a supported alternative but is not the default.

### Scene and Identity Handling
The pipeline supports three scene/identity configurations:
1. **Standard SDXL**: Uses native SDXL capabilities.
2. **Hybrid Flux Scene**: Combines a Flux-based scene model with SDXL identity inpaints.
3. **Full Flux + PuLID**: Uses Full Flux combined with PuLID for identity preservation.

### Promotion Criteria
Rendering quality comparisons are performed using controlled parent inputs. Candidates are evaluated via timed runs and manual eyeball judgment. The highest-quality candidate is promoted to the default pipeline configuration.

### Weight Availability
RealVisXL and PuLID weights are stored on Mothership.

## Invariants
Softserve never hosts the model. A gaming-busy guard activates before any load. Generation is local-only; no cloud generation occurs. Generated images reside on the docs share under State/. The 27B parameter model remains the GPU’s primary tenant; image generation always yields to it. Custom nodes dependent on xformers are avoided because they are experimental on ROCm.

## Archive Cuts
- [archive] None.

## Decision points — Status

### Closed decisions
- **Placement:** Mothership/ComfyUI-ROCm.
- **Model tier:** Both SDXL (fast) and Flux (best).
- **Access:** All users with quotas; admins and test users have unlimited access.
- **P0 scheduling:** Fully remote.
- **Tier-2 VLM install:** Qwen3-VL:8B on BigHonker.
- **Rung engine election:** Superseded by the ROUTING split (auto/heavy=neural, quick=FLAME). Validation of real-photo judgment requires post-deploy testing.
- **CN-0.8 bake-off sheet:** Sharpened version delivered alongside the 0.55 version in `rung2-prototype/`.

### Open decisions (the owner)
- Deploy go for the axis-split build.
- P4 pipeline bake-off.
- Tiling build.
- Wallpaper tier default selection (current status: "best" is current; "fast" appeared comparable).
- Real-photo rung validation post-deploy.
