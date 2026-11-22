# ComfyUI: the lab's own pieces

The factories render through ComfyUI (upstream, plus third-party nodes installed by its own manager). What the lab
adds is here:

| File | Goes to | What it does |
|---|---|---|
| `custom_nodes/p20_cond_cache/__init__.py` | `<ComfyUI>/custom_nodes/p20_cond_cache/` | Save / load a text encoding to disk, so a preview graph skips the text encoder (a 24B encoder is ~15.6 GB to load). The files land in `<FOUNDRY_ROOT>\var\cache\cond_cache`, where the factory looks for them. |
| `custom_nodes/p20_reference_memory/__init__.py` | `<ComfyUI>/custom_nodes/p20_reference_memory/` | Free the finished reference encoders before the diffusion model loads (ComfyUI's own memory manager; values pass through unchanged). |
| `run_comfy_bench.bat` | beside a ComfyUI checkout and its `venv` (the NVIDIA box: `F:\comfy-bench`) | Start that ComfyUI on port 8189, appending to `comfy_bench.log`. The NVIDIA box's scheduled tasks start it. |
| `extra_model_paths.yaml` | `<ComfyUI>/extra_model_paths.yaml` (the AMD box) | Where that box keeps its models (the fast NVMe store first, then the older store). |

The AMD box's launcher is `comfy_launch.bat` at this repo's root (`FOUNDRY_COMFY_LAUNCH` names it); it expects
`comfyui-rocm` beside it. A factory renders only where `FOUNDRY_COMFY_LAUNCH` is set or, on the NVIDIA box, where its
ComfyUI task exists.
