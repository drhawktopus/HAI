"""Production launch config per model. These values represent the measured best settings for production runs; they are static and do not change per order unless an order explicitly overrides a setting for testing (e.g., setting a value to null to disable it).

This file contains data only (no imports) so it can be shared with the menu builder. Ensure changes are based on measurements."""

# Vision config is for chat windows only; LAUNCH orders remain priced on the base model row.
VISION = {"Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf": "mmproj-model-f16.gguf"}

LAUNCH = {
    "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf": {
        "gpu": "gtx1060", "fit_target": 1024, "build": "cuda", "ubatch": 2048, "parallel": 1,
        "spec_type": "draft-mtp", "draft_max": 2, "op_offload_min_batch": 256,
        "engine_env": {"LAB_CUDA_GRAPHS_PRE_VOLTA": 1}},
}
