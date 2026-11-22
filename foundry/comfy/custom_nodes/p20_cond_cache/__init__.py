"""[P20] Conditioning cache nodes — save/load a CLIPTextEncode result to disk.

Why: the preview prompt never changes between checkpoint previews, but every preview would otherwise pay the full
text-encoder load (a 24B encoder is ~15.6 GB) + encode just to reproduce an identical conditioning tensor. Bake it
once with P20SaveConditioning, then preview graphs use P20LoadConditioning and skip the CLIP loader entirely
(~2 min + 15.6 GB of I/O saved per preview).

Cache files are torch.save'd lists of [tensor, dict] pairs, tensors moved to CPU on save; the sampler
moves them to the device on use. One .cond file per (prompt, TE) pair — re-bake if the prompt or the
text-encoder model ever changes.
"""
import os
import torch

# The foundry's own cond cache: this node writes the .cond files where the foundry and p20_bake_cond.py look for
# them (<FOUNDRY_ROOT>\var\cache\cond_cache; the foundry's default root when FOUNDRY_ROOT is unset).
CACHE_DIR = os.path.join(os.environ.get("FOUNDRY_ROOT") or r"F:\foundry", "var", "cache", "cond_cache")


class P20SaveConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "conditioning": ("CONDITIONING",),
            "name": ("STRING", {"default": "preview"}),
        }}

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "P20"

    def save(self, conditioning, name):
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, name + ".cond")
        cpu = []
        for t, d in conditioning:
            dc = {}
            for k, v in d.items():
                dc[k] = v.detach().to("cpu") if torch.is_tensor(v) else v
            cpu.append([t.detach().to("cpu"), dc])
        torch.save(cpu, path)
        print("[P20-cond] saved %s (%d entries)" % (path, len(cpu)))
        return (conditioning,)


class P20LoadConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "name": ("STRING", {"default": "preview"}),
        }}

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "load"
    CATEGORY = "P20"

    def load(self, name):
        path = os.path.join(CACHE_DIR, name + ".cond")
        data = torch.load(path, map_location="cpu", weights_only=False)
        print("[P20-cond] loaded %s (%d entries)" % (path, len(data)))
        return (data,)


NODE_CLASS_MAPPINGS = {
    "P20SaveConditioning": P20SaveConditioning,
    "P20LoadConditioning": P20LoadConditioning,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "P20SaveConditioning": "P20 Save Conditioning",
    "P20LoadConditioning": "P20 Load Conditioning",
}
