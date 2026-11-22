# RUNBOOK — ComfyUI Image Generation Operation



## ComfyUI Image Generation on Mothership (Project #19)

**⚠ WHICH ComfyUI IS LIVE (read 2026-11-02 by the #38 inventory, [[Lab-Update-Inventory-38]]): the foundry renders
through `F:\comfyui-rocm`** (`foundry_paths.COMFY_ROOT`, launcher `F:\comfy_launch.bat`, port 8189). Its Python is
`F:\comfyui-rocm\python_env` (torch 2.12.0 + ROCm 7.15 alpha, the same as both ai-toolkit trees), it has 10 custom
nodes, and it ships its own updater `comfyui-rocm-updater.bat`. Never run that updater outside #38's staged path.
The `E:\ComfyUI` tree below is the ORIGINAL #19 install (torch 2.9.1 + ROCm 7.2.1) and is not what the factory uses.
BigHonker's klein ComfyUI is `F:\comfy-bench\ComfyUI` (venv `F:\comfy-bench\venv`, launcher `run_comfy_bench.bat`).

Original #19 setup — headless ComfyUI + ROCm 7.2.1 on Mothership's RX 7900 XTX (Windows):

**Layout (#19, not the factory's):** app `E:\ComfyUI\ComfyUI` (git) · venv `E:\ComfyUI\venv` (Python 3.12
user-install, NOT the system 3.14) · models `E:\ComfyUI\ComfyUI\models\checkpoints\` (sd_xl_base_1.0 +
flux1-dev-fp8) · log `E:\ComfyUI\comfyui.log` · launcher `E:\ComfyUI\launch_comfy.bat`.

**Service:** SYSTEM scheduled task "ComfyUI Server" (onstart). API: `http://<mothership-address>:8188`
(LAN-only firewall rule "ComfyUI-LAN"). Health: `curl http://<mothership-address>:8188/system_stats`.
**Manual relaunch:** ssh sessions to this box are elevated (see § **⚡ ELEVATION**), so
`ssh mothership "schtasks /run /tn \"ComfyUI Server\""` is expected to work but has not been
re-measured on this task specifically. The measured, session-independent relaunch is WMI
(`Start-Process` over ssh silently no-ops):
`powershell -Command "(Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments
@{CommandLine='cmd /c E:\ComfyUI\launch_comfy.bat'}).ReturnValue"` (0 = started). The onstart task
still covers reboots. OLD (wrong for ssh use): `schtasks /end ... & schtasks /run ...` (or kill the
python on :8188 first if wedged). Mothership asleep = WoL first, same as the 27B.

**Measured:** SDXL 1024²@25st: 35s cold / 11s warm / **16s with qwen3:14b resident
(coexist mode WORKS — 14B survives)**. Flux dev fp8 1024²@20st: ~205s cold, renders legible in-image
text. VRAM: 14B=9.2GB; SDXL and the 14B fit TOGETHER on the 24GB card; Flux and the 27B each need the
card alone (swap-on-demand).

**Gotchas:** ROCm SDK wheels must install BEFORE the torch wheels (hidden 2nd docs page; torch wheel
deps on `rocm==7.2.1`). Never pipe the server's stdout through anything that closes (a `head` stalled
it silently — listening but unresponsive). Driver floor for ROCm 7.2.1 wheels = Adrenalin 26.2.2.
xformers-dependent custom nodes = experimental on ROCm, avoid.
**PuLID-Flux passes can KILL the ComfyUI process** (memory exhaustion suspected on a
Flux+PuLID+EVA-CLIP co-load). A "timed out after 420s" from comfy_client followed by
unreachable-after-WoL = check for a DEAD ComfyUI, not a sleeping box; Ollama answering
on :11434 while :8188 is silent is the tell. Relaunch = the WMI one-liner above. **launch_comfy.bat
OVERWRITES comfyui.log on start** — capture the log tail BEFORE relaunching or the crash evidence
is gone.

**img2img restyle recipe (A/B-measured):**
SDXL img2img destroys subject identity above ~denoise 0.5 (0.6 melted a robot into an urn; 0.5
preserved it). So photo restyles use ALL of: (1) tool-side deterministic `source_image="latest"`
attachment when the user's own words say "my photo" (never trust the 8B to pass the param);
(2) style-only sampler prompt for pure restyles — the model CANNOT see the photo, and any subject
it invents ("a person") WINS the diffusion and replaces the real one; (3) preset strengths all 0.5;
(4) monochrome styles (sketch) grayscale the SOURCE bytes first (`comfy_client.prep_source`, PIL,
fail-open) because prompt/negatives can't remove color at subject-safe strengths. Pillow is an
orchestrator dependency for this (`pillow==12.3.0`).

**ControlNet restyle (SUPERSEDES the img2img recipe for preset restyles):**
`controlnet-union-sdxl-promax.safetensors` (xinsir, 2.5GB) in `models\controlnet` + core nodes only
(Canny → SetUnionControlNetType "canny/…" → ControlNetApplyAdvanced str 0.85 → KSampler denoise 1.0).
Edges lock subject+composition; full denoise = total style transform, zero identity loss (A/B-proven
on the robot). Orchestrator picks this path for style-preset restyles on the fast tier when
`/models/controlnet` lists the model (fail-closed); explicit user strength = classic img2img; best/
Flux restyles = img2img fallback (union model is SDXL-only). Audit field `mode=cn|i2i|t2i`. The union
model also covers openpose/depth/tile for the future identity work.

**Wallpaper preset:** `RealESRGAN_x4plus.pth` in `models\upscale_models`; size=
"wallpaper" → wide gen → 4x model upscale → lanczos 2560x1440 (~45s fast tier end-to-end).


## HAI queued renders — ownership repair (2026-10-20)

HAI's Flux.2 user/identity renders require render.flux2, including old records that predate capability fields. The worker must win the existing atomic desk claim before unloading/baking/rendering. A Klein-only worker cannot take those jobs. An empty encoder list reported by another factory was previously able to mark a healthy Mothership render failed; do not diagnose that as missing weights without checking claimed_by and both worker logs.

The Mothership F:\comfy_launch.bat now uses absolute interpreter/main paths and explicit extra_model_paths.yaml. Relative python_env\python.exe main.py hid factory identity from GPU patrol. Do not disable patrol or set GPU_SHORE_LEAVE/GPU_NO_ARREST to accommodate HAI. Deploy the canonical launcher through fleet_deploy (Mothership-only real home F:\), and p20_render_worker.py through the same tool, which since 2026-10-26 also restarts the live render worker between jobs and reports its fresh PID on a `worker …` line ([[RUNBOOK-Orchestrator-Deploy-Procedure]] § "THE RENDER WORKER IS RESTARTED TOO"). The foundry runner does not import that worker module.

The named render heartbeat now explains pushed GPU load and reserves its card through the scheduler. Unknown, stale, foreign and finished records do not explain new load. Closing proof: turtle775b004bb6cb, claimed only on Mothership, real load accounted, PNG delivered to the owner's library. Journal2026-10-20-codex-p34 records validation and remaining #34 work.


## Krea 2 renders — `render.krea2`, the Krea 2 · Mothership renderer (2026-10-26)

**What it is.** HAI's Images menu offers **Krea 2 · Mothership** (`render_options` key `krea2_mothership`). A Krea
order carries `requires: ["render.krea2"]` and `render_model: "krea2"`; Mothership's render worker routes it to
`F:\foundry\bin\p20_krea_render.py` instead of the FLUX.2 path. Krea encodes its prompt IN the graph (Qwen3-VL-4B,
CLIPLoader type `krea2`), so a Krea order has **no cond bake** — the worker skips `p20_bake_cond.py` for it. The graph
is ComfyUI's own `image_krea2_turbo_t2i` template minus its prompt-enhancer branch: **Turbo fp8_scaled** (Krea's own
release; the bf16 does not fit the card — streamed from RAM it draws 190-198 W at clk >3100, the published wedge
signature, and the stall daemon killed both bf16 renders on 2026-10-26, which is correct behaviour for a guard that
cannot tell streaming from a wedge; the daemon's post-kill burst then read as `UNACCOUNTED GPU LOAD` for a minute,
because GPU accounting does not know the daemon's own bursts), 8 steps, CFG 1,
euler/simple, the positive zeroed for the negative, optional `LoraLoaderModelOnly` for a Krea-trained adapter. Same
client id as the slim renderer, so the worker's progress reporter and the stall daemon's mercy rule see Krea renders.

**Who declares it.** `p20_foundry` declares `render.krea2` only when the three files `p20_krea_render` names
(`UNET`/`CLIP`/`VAE` — one derivation) are in a folder the foundry's ComfyUI reads: its own `models\` tree or any
`extra_model_paths.yaml` entry (`unet`/`clip` count as their legacy names). On Mothership they are in
`E:\ComfyUI\ComfyUI\models` via `generic_estore`. Check it on the deck: `GET /p20/factory/mothership` →
`capabilities` contains `render.krea2`.

⚠ **`render.krea2` missing from the deck on a box that has the files → check the INTERPRETER, not the files.**
Mothership's live runner is the per-user python (`C:\Users\<user>\AppData\Local\Python\…`), which has **no PyYAML**,
while the suite gate (`run_foundry_suite.cmd`) runs on the ai-toolkit venv, which does. The first deploy read the
mapping only through PyYAML, so on the runner's interpreter it saw no mapped store: the deploy-time suite (PATH
python) went red on Mothership while the gate interpreter was green — two runs of one suite disagreeing because the
interpreter differed. Fixed the same day: `read_extra_model_paths` falls back to a two-level reader of ComfyUI's
format, pinned by `Krea2Capability` tests both with and without PyYAML.

**Identity on Krea.** A person renders on Krea 2 only with an adapter whose person-record `base` is `krea2`; until one
is trained, a Krea identity request is refused with the base the person does have (no FLUX.2 substitution, no
stranger). Training them: `queue_training` with `base: krea2` requires the factory capability `train.krea2`
(a Krea-ready ai-toolkit at `foundry_paths.AI_TOOLKIT_KREA2`, default `D:\ai-toolkit-krea2`); weights in
[[ComfyUI-Installed-Model-Inventory]] § "Krea 2".
