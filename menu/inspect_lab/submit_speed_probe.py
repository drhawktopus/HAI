"""Rigby speed probe: the MoE's slow agent turn (rewrite code already in the context, at ~2k and ~12k tokens) under
four server settings, one factor at a time - CPU, CPU + n-gram speculation, the GT 730 holding the ten full-attention
layers (+ their KV), and both. Each is its own Tolkien `llm` order (payload speed_rewrite_probe.py, same model, ctx,
reasoning and temperature as the A1 orders), so the winner sets the server for every later rigby A1 arm.

    python submit_speed_probe.py [--dry]

Placement: full attention lives in blocks 3,7,...,39; with everything else
overridden to CPU the card holds 191 MiB of weights plus the KV of those ten layers (~0.67 GB at 32,768, f16)."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the menu folder
import menu_paths as MP  # noqa: E402   the tablet desk: a setting
INBOX = MP.need("INBOX")
FULL_ATTN = "3|7|11|15|19|23|27|31|35|39"
GPU_ATTN = {"gpu_layers": 99,
            "override_tensor": [r"blk\.(?!(%s)\.).*=CPU" % FULL_ATTN, "ffn_=CPU", r"output\.=CPU"]}
BASE = {"kind": "llm", "model": "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf",
        "payload": "/ssdshared/tabfoundry-jobs/payloads/speed_rewrite_probe.py",
        "tasks": ["rewrite_2k", "rewrite_12k"], "replicates": 1, "arms": ["rigby"], "ctx": 32768,
        "reasoning": "off", "temperature": 0.6}
# first pair: gt730-attn wrote +25% at 12k but READ 5x slower (2.8 vs 14.4 tok/s) - llama.cpp's default
# op offload streams the host-resident weights to the card for every prompt batch; --no-op-offload keeps them home.
GPU_ATTN_NOOFF = dict(GPU_ATTN, op_offload=False)
ARMS = [("cpu", {}), ("cpu-ngram", {"spec_type": "ngram-simple"}),
        ("gt730-attn", GPU_ATTN), ("gt730-attn-ngram", dict(GPU_ATTN, spec_type="ngram-simple")),
        ("gt730-attn-nooff", GPU_ATTN_NOOFF), ("gt730-attn-nooff-ngram", dict(GPU_ATTN_NOOFF, spec_type="ngram-simple"))]


def main():
    stamp = time.strftime("%Y%m%d-%H%M")
    only = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--only=")), None)
    # --ctx=N: a CONTEXT probe - the KV is allocated at load, so a clean load + both rewrites at N is the tested size
    # (rigby's agentic turns pass 32,768, a limit set for one-shot menu tasks that never used more than 5,278 tokens
    # and was never measured for A1)
    ctx = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--ctx=")), None)
    for label, extra in ARMS:
        if only and label not in only:
            continue
        spec = dict(BASE, **extra)
        if ctx:
            spec["ctx"] = ctx
        spec["name"] = "speedprobe-rigby-moe-%s%s.%s" % (label, "-ctx%d" % ctx if ctx else "", stamp)
        spec["why"] = ("the owner 2026-10-06: use rigby's GT 730 for the model itself and run the later rigby arms on the "
                       "best settings. One factor per arm vs the cpu arm: %s." % (label,))
        if ctx:
            spec["why"] = ("CONTEXT PROBE (2026-10-08): does the A1 placement (%s) load and answer at ctx %d? The "
                           "largest that does is rigby's tested A1 context." % (label, ctx))
        if "--dry" in sys.argv:
            print("DRY", spec["name"], {k: v for k, v in extra.items()})
            continue
        path = os.path.join(INBOX, spec["name"] + ".tjob.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=1)
        os.replace(path + ".tmp", path)
        print("submitted", spec["name"])


if __name__ == "__main__":
    main()
