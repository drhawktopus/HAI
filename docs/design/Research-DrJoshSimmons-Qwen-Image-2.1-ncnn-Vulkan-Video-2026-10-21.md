# Research note — "This 31GB AI Model Runs on a 2GB GPU?" (Dr. Josh C. Simmons, 2026-10-18): Qwen Image 2.1 through an ncnn/Vulkan port

Filed 2026-10-21 at the owner's request. Raw files (captions, info JSON, description, cleaned transcript) are in `Claude Home\research\video-9OLsKWuVsbk\`. Softserve's own IP got HTTP 429 on the captions again; they came through the Pinchflat container on Sundae.

**Certainty tags:** **[D]** stated in the video, unchecked · **[V]** checked against a primary source · **[L]** compared with a lab fact · **[?]** named but not found.

## The video

| | |
|---|---|
| Title | This 31GB AI Model Runs on a 2GB GPU? |
| Channel | Dr. Josh C. Simmons (33,300 subscribers; playlist "The People Controlling AI"; newsletter and Discord in the description) |
| Uploaded | 2026-10-18 16:00 UTC · 4 min · 8 chapters · 4,462 views at pull time |
| Captions | YouTube auto-generated English; names garbled ("Kwench", "Vulcan", "Ni Hui" for Qwen, Vulkan, nihui); the description has the correct names, settings and links |
| Thesis | The "2 GB" figure is the GPU's video-memory budget, not the model size; the weights stay in system memory and the GPU works in chunks. The images are very good and the wait is real |

## Claims

| # | Claim | Tag | Note |
|---|---|---|---|
| 4 | "2 GB" is the VRAM budget, not the model size; weights stay in system memory at BF16; the GPU handles generation in small chunks, and the decoder is tiled when memory is tight | **[V, partly]** | README: "Low-VRAM mode and VAE tiling adjust automatically to available GPU memory." The BF16-in-system-memory detail is the video's reading of the author's claim; I did not find it in the README text I read |
| 5 | The published requirement is still 16 GB of system RAM | **[V]** | README: Linux/macOS minimum 16 GB RAM and any Vulkan GPU; recommended 32 GB RAM and a 16 GB card with matrix hardware. **Windows has its own rule the video leaves out:** (half of system RAM) + (GPU memory) ≥ 16 GB, because Vulkan can only use half of system RAM under WDDM |
| 6 | The author's RTX 3060 table: 1024² with small decoder tiles adds ~1 GB over baseline; larger tiles more; 2048² crosses 2 GB | **[V]** | README table matches: 1024² with 256 tiles **1,027 MiB**; 1024² with 512 tiles 1,487; 2048² with 512 tiles 3,411; 2048² with 1024 tiles 4,971. **Caveat the video does not state:** the README calls these "historical measurements", 2 steps, "before prefix KV caching", and "not current minimum VRAM guarantees" |
| 7 | His test on an M4 Max Mac Studio (64 GB unified): 1024², 40 steps, seed 42 took **13:58**, peak **25.5 GB**; an edit (8 steps) 8:04 / 27.6 GB; 768² runs 7:33–7:56 / ~26–27 GB; the port chose the GPU by itself and never entered low-VRAM mode | **[D]** | The description gives the full settings and seeds. One run each, no spread. Peak is process RSS on unified memory, not VRAM (he says so). He tested release **20260923**; the repo has since published **20261004** and **20261010**, and the README's table predates prefix KV caching, so his timings may be out of date [V on the releases] |
| 8 | The sign text "THE FUTURE IS LOCAL" rendered correctly | **[D]** | One image, one seed; I have not seen it |
| 9 | Transparent backgrounds failed in both tests (opaque glass bottle and red mug, ~3:20 each, 32 GB peak) | **[D]** | Two prompts only, 8 steps. Not enough to say the feature is broken in the port |
| 10 | On a real 2 GB card the wait "could take hours" | **[D]** | His speculation, not a measurement. The README's own table does not give times |

## Compared with the lab [L]

- **No lab data on this model.** The lab's image measurements are FLUX.2/Klein on the 7900 XTX under ROCm; there is no Qwen-Image run, so no speed or quality comparison is possible.
- **Hardware fit by the README's Windows rule** (half system RAM + GPU memory ≥ 16 GB), using the lab's own figures: Mothership (32 GB RAM, 24 GB card) clears it easily; BigHonker (16 GB RAM, 8 GB card) sits **exactly on the line** (8 + 8 = 16). I did not check Softserve's or rigby's RAM for this note.
- **Why it might matter:** the port runs through Vulkan, not ROCm/HIP. The lab's long driver and wedge investigation was ROCm-specific, so a Vulkan image path on the 7900 XTX is a different failure surface. That is a hypothesis; nothing here tests it, and nothing has been queued.

## Sources named — resolution status

| Source | Status |
|---|---|
| `QwenLM/Qwen-Image-2.1` (GitHub) | **[?]** not read; the QwenLM org's repo list shows `Qwen-Image-2.1` pushed 2026-10-14 |
| `nihui/qwenimage-ncnn-vulkan` (README, requirements, memory table) | **[V]** README fetched; tables and rules above |
| `huggingface.co/Qwen/Qwen-Image-2.1/discussions/33` (the developer's explanation of the 2 GB figure) | **[?]** not fetched |
| `huggingface.co/nihui-szyl/qwen-image-ncnn` (converted files, 31.2 GB) | **[V]** exists, public; size not summed |
| Apple "unified memory" tech talk (developer.apple.com) | **[?]** not fetched |

## About the source

Single-presenter channel with newsletter and Discord links; the measurements are one run each and his own, and he invites readers to report their own times. The accurate-to-the-README parts (the table, the 16 GB rule) are solid; the Windows RAM rule and the "historical, pre-KV-cache" caveat are the two things the video does not pass on. It is a modest, honest test video, not a benchmark.
