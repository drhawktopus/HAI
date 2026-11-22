# ComfyUI Installed Model Inventory

## Mothership Path
`E:\ComfyUI\ComfyUI\models`

## Checkpoints
| kind | file | size |
|---|---|---|
| checkpoint | `flux1-dev-fp8` | 17.3 GB |
| checkpoint | `RealVisXL_V5.0_fp16` | 6.9 GB |
| checkpoint | `sd_xl_base_1.0` | 6.9 GB |

## Diffusion Models
| kind | file | size |
|---|---|---|
| diffusion_model | `flux1-dev-kontext_fp8_scaled` | 11.9 GB |

## Adapters and Encoders
* `pulid_flux_v0.9.1`
* `t5xxl_fp8`
* `ae.safetensors` (Flux VAE)
* `ip-adapter SDXL`
* `controlnet-union-sdxl`
* `CLIP-ViT-H`

## Absent Components
The following tools are **not** installed:
* OneTrainer
* kohya
* ai-toolkit
* sd-scripts

Flux 2 is **not** installed.

## Base-model selection and hierarchy

The image generation pipeline runs on **Flux.1 dev** with **Flux Kontext** (method "D1"). This is the current base model for generation.

### Model availability and capability
- **Installed:** Flux.1 dev, RealVisXL V5, `sd_xl_base_1.0`.
- **Not installed:** Flux 2.
- **Capability hierarchy (photorealism):** Flux.1 dev > RealVisXL V5 > SDXL.
- **LoRA compatibility:** LoRAs must target the active generation base. Current LoRAs plug into Flux Kontext/D1.

### ROCm maturity gates
Image generation support on ROCm is distinct from LoRA training support on ROCm. Support for one does not imply support for the other. Training is more experimental than inference.

### Fallback strategy
Use **RealVisXL V5** as the primary ROCm fallback if Flux.1 dev performance is insufficient or unstable. It is lighter and has proven ROCm compatibility.

### Flux 2 viability on 7900 XTX

The NVIDIA RTX 7900 XTX (RDNA3) hardware lacks native fp8/fp4 compute support. Consequently, fp8 and bf16 Flux 2 models are incompatible. The operational path for Flux 2 testing is GGUF quantization via the ComfyUI-GGUF node (`city96`). This requires a GGUF encoder, as standard Mistral encoders do not run on this hardware.

`F:` has ~27 GB free (2026-10-26), which is why Krea 2 lives on `E:` and `D:` (below).

#### Environment configuration
- **Software:** ComfyUI 0.27.0, frontend 1.45.20, templates 0.11.6, PyTorch 2.9.1+rocm7.2.1.
- **VRAM:** 25.75 GB available.
- **Path mapping:** `E:\ComfyUI\ComfyUI\extra_model_paths.yaml` maps `flux2_nvme` to `F:\ComfyModels\`.
- **Launch:** Scheduled task "ComfyUI Server" executes `E:\ComfyUI\launch_comfy.bat`. Logs at `E:\ComfyUI\comfyui.log`.
- **Node requirements:** `custom_nodes/ComfyUI-GGUF` and `gguf` 0.19.0 installed.

Restart the scheduled task after model downloads to ensure the GGUF node loads with updated paths.

#### Workflow specification
Build workflow using:
- `UnetLoaderGGUF`
- Mistral GGUF CLIP loader
- `flux2-vae`
- CFG: 1.0
- Steps: 20–30

#### Performance verdict
Flux 2 is impractical on the 7900 XTX. The full GGUF stack loads end-to-end (Mistral GGUF to `Flux2TEModel`; tekken tokenizer rebuilt from metadata; Q4 diffusion `full load: True`, 19.58/21.17 GB usable, no per-step offload, no OOM). However, inference speed is ~145 s/step (~48 min per 1024px image), which is ~70× slower than Flux 1 dev (~2 s/step). The cause is the 32B model size combined with on-the-fly GGUF dequantization without Triton kernels on this ROCm build.

**Design decision:** Train LoRAs on Flux 1 dev. A Flux 2 LoRA would inherit the excessive inference cost. Flux 2 models (52 GB total) remain parked on `F:\ComfyModels\` for potential re-testing if Triton-ROCm support or larger hardware becomes available. Re-test recipe is staged (`submit_test.py`, node install, paths); only faster kernels are required to re-run.

## Krea 2 — official weights, where they live (2026-10-26)

| file | where | use |
|---|---|---|
| `krea2_turbo_fp8_scaled.safetensors` (13.1 GB) | Mothership `E:\ComfyUI\ComfyUI\models\diffusion_models` | **the render model** (`p20_krea_render.UNET`) — loads whole on the 24 GB card |
| `krea2_turbo_bf16.safetensors` (26.3 GB) | same | measured 2026-10-26: does NOT fit — ComfyUI streams ~2 GB/step from RAM, the draw matches the wedge signature and the stall daemon killed both renders; kept for the record and a future bigger card |
| `qwen3vl_4b_bf16.safetensors` (8.9 GB) | `...\models\text_encoders` | Krea's encoder (CLIPLoader type `krea2`) |
| `qwen_image_vae.safetensors` (0.25 GB) | `...\models\vae` | Krea's VAE |
| `krea2_turbo_lora_rank_64_bf16.safetensors` | `...\models\loras` | Krea's own Turbo LoRA (for Raw-trained adapters) |
| `krea2_raw_bf16.safetensors` (26.3 GB) | Mothership `D:\krea2-train\models` | training base |

## ⚠ 2026-09-14 correction — Flux 2 is NOT parked (supersedes the "Performance verdict" above)

The verdict above describes the ComfyUI GGUF Flux 2 workflow only. Since July 2026 the lab trains Flux.2 [dev] (32B) on the 7900 XTX through the QLoRA recipe (`Flux2-32B-QLoRA-AMD-ROCm-Windows.md`, uint4 via optimum-quanto, 6.9 s/it at 448 px) and renders Flux.2 with those adapters through the foundry's quantized render path (Mothership declares `render.flux2`; every person adapter served by the render tool is a Flux.2 adapter; the Peggy proof renders ran 2026-07-24). Rendering is slow and the path is still being tuned, but "impractical / parked / train on Flux 1 dev" is superseded. the owner, 2026-09-14: "Half of the work I was doing was proving that I can run these big models on my cards… getting Flux 2 to work as well… still in development… another project in progress."
