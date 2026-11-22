# RDNA inference projects — hipfire and llama-cpp-rdna-boosts (research notes 2026-10-19; PARKED for another day, not ruled out)

**Status: PARKED by the owner 2026-10-19** (*"I think you're ruling it out too fast but I'll save that for another day"*).
A quick first look, taken while chasing the 3.8's prompt-read speed on Mothership. Nothing was installed or measured.
Treat everything below as the projects' OWN claims unless marked otherwise.

**Why it came up:** a community benchmark of Qwen3.8 27B Q4_K_M on an RX 7900 XTX (Sep 2026) reported prefill
192–231 tok/s (llama.cpp Vulkan / Ollama ROCm); a commenter linked both projects as RDNA tuning paths and another said
prefill on this class of card "should be about 5x higher" (unsupported claim, two-card setup). The lab's own 3.8
prefill on Mothership is still unmeasured as a clean number (HAI-call engine log 2026-10-19: 415–660 tok/s on tokens
actually read; the "~2,300" in HAI-Agent-Surface-Design's Quick Resume table holds in its own context, not as a general
speed).

## llama-cpp-rdna-boosts (github.com/stew675/llama-cpp-rdna-boosts)

- **What:** a PATCH SET for llama.cpp, not a fork: 12 patches (block 00 + blocks 01–11) applied with `git am` (or its
  `scripts/apply-all.sh`) onto a clean llama.cpp checkout at commit `097cf33d9`; release `v12-097cf33d9-r88`. MIT,
  ~198 stars. Actively maintained (entries dated 2026-10-05, 10-16, a delivery record `261019.002`). Do NOT use
  `git apply` — it silently drops hunks.
- **GPUs:** RDNA3 `gfx1100` (7900 XTX/XT, 7800 XT) supported; RDNA3.5 gfx1150/1151; RDNA4 gfx1200/1201; RDNA2 gfx1030
  best-effort. Measured on one 7900 XTX among others.
- **Backend / OS:** HIP/ROCm build (`-DGGML_HIP=ON -DGGML_HIP_RCCL=1 -DGPU_TARGETS=gfx1100`); tested on Linux with ROCm
  7.14. Windows is not a documented target, but two users reported Windows issues (so people run it there); a known
  "start-to-start flips" problem on Windows/ROCm traced to hipBLASLt (workaround `ROCBLAS_USE_HIPBLASLT=0`).
- **Claimed gains (mostly relative):** adaptive MTP + `ngram-mod` +13.6 % overall decode (recall +67.5 %); qwen4exp
  decode fusions +2.4 %; derived KQ mask PP512 on gfx1100 −0.2 % to +1.2 % (9B / Gemma 12B); it cut compute-buffer VRAM
  184 → 88 MiB on a 9B at 98k. Single-card MTP draft cap 6 costs 11–14 % vs 9 on code.
- **Caveats:** outputs not bit-identical to stock (block 10 changes fp32 reduction order); `ngram-mod` with
  host-resident experts roughly halves decode; a stale `GGML_CUDA_FA_WMMA_256=0` forces a ~3x slower tile kernel at depth
  on head-256 models; patches are static against the fork point and need rebasing to follow upstream.
- **Open questions for the day it is picked up:** does a Windows HIP build of this series run on Mothership (gfx1100,
  current HIP SDK)? Its MTP work vs ollama's own MTP on the 3.8 (we already write 90–92 tok/s on Vulkan)? What does it do
  for PREFILL specifically on a dense/hybrid 27B — the patches' own prefill deltas are small on gfx1100.
