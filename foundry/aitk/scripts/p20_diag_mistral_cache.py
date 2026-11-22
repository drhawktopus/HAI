"""[P20] Diagnose WHY the Mistral uint4 cache load fails.

Failure (every launch, ~4-5 min wasted each):
  NotImplementedError: Cannot copy out of meta tensor; no data!
in flux2_model.load_te, at `text_encoder.to(device)` AFTER load_state_dict(cache, assign=True).

Hypothesis: `state_dict()` OMITS non-persistent buffers by definition, so those tensors are never in the cache and
stay on the meta device; the subsequent .to(device) then throws.

This script is CHEAP ON PURPOSE — it builds the model on the META device (allocates no real weights) and only reads
the cache's KEY LIST via mmap, so it never materializes the 14 GB. Safe to run while training occupies the GPU:
CPU-only, minimal RAM, touches nothing.
"""
import os, sys, torch


def _need(name):
    """A setting this setup script cannot run without (an environment variable): its value, or a refusal naming it."""
    v = os.environ.get(name, "")
    if not v:
        sys.exit("set %s (see this script's docstring)" % name)
    return v

MISTRAL_PATH = os.path.join(_need("P20_TRAIN_DIR"), "mistral")
CACHE = os.path.join(_need("P20_TRAIN_DIR"), "mistral-uint4.pt")

print("=" * 78)
print("[P20] Mistral uint4 cache diagnosis")
print("=" * 78)

from transformers import AutoConfig, Mistral3ForConditionalGeneration

cfg = AutoConfig.from_pretrained(MISTRAL_PATH)
with torch.device("meta"):
    te = Mistral3ForConditionalGeneration(cfg)
print("built on meta OK")

sd_keys = set(te.state_dict().keys())
param_keys = set(n for n, _ in te.named_parameters())
buf_keys = set(n for n, _ in te.named_buffers())
print("\nmodel: %d state_dict keys | %d params | %d buffers" % (len(sd_keys), len(param_keys), len(buf_keys)))

# THE CORE CHECK: buffers that state_dict() does not carry == non-persistent == never in any cache
non_persistent = sorted(buf_keys - sd_keys)
print("\n--- NON-PERSISTENT BUFFERS (excluded from state_dict -> stay META after load) : %d ---"
      % len(non_persistent))
for k in non_persistent[:40]:
    print("   ", k)
if len(non_persistent) > 40:
    print("    ... +%d more" % (len(non_persistent) - 40))

# params/buffers that would remain meta if we only assign what's in state_dict
print("\n--- params NOT in state_dict (would stay meta): %d ---" % len(sorted(param_keys - sd_keys)))
for k in sorted(param_keys - sd_keys)[:20]:
    print("   ", k)

# Cross-check against what the cache actually stores (mmap = lazy, no 14GB load)
try:
    print("\n--- reading cache keys (mmap, no materialization) ---")
    cached = torch.load(CACHE, map_location="cpu", weights_only=False, mmap=True)
    ckeys = set(cached.keys()) if hasattr(cached, "keys") else set()
    print("cache has %d keys" % len(ckeys))
    missing = sorted((param_keys | buf_keys) - ckeys)
    print("\n--- MODEL TENSORS ABSENT FROM CACHE (these are what break .to(device)): %d ---" % len(missing))
    for k in missing[:40]:
        tag = "non-persistent-buffer" if k in buf_keys and k not in sd_keys else ("buffer" if k in buf_keys else "param")
        print("    %-60s [%s]" % (k, tag))
    if len(missing) > 40:
        print("    ... +%d more" % (len(missing) - 40))
    extra = sorted(ckeys - (param_keys | buf_keys))
    print("\n(cache keys not present in the un-quantized model: %d - expected, quantization renames Linear weights)"
          % len(extra))
except Exception as e:
    print("cache key read failed (%r) - the non-persistent-buffer list above is still the answer" % (e,))

print("\n" + "=" * 78)
print("FIX GUIDANCE: do NOT to_empty()/zero-fill these - a wrong rotary inv_freq silently produces garbage")
print("embeddings. Either (a) save them into the cache explicitly, or (b) re-run the owning module's buffer")
print("init to recompute them on a real device before .to(device).")
print("=" * 78)
