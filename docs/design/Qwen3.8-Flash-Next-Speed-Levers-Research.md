# Qwen3.8-Flash-Next speed levers — community research, verified (MTP, expert cache, exllamav3, Vulkan, RAM 1:1)

**2026-09-30 evening.** Research pass (a subagent's web sweep, then the decision-driving claims checked against their
sources by Claude) on how to make Flash-Next generate faster on Mothership (7900 XTX 24 GB, Ryzen 7 5800X, 32 GB
DDR4, Windows, ROCm). Measurements and the current best config: [[Qwen3.8-Flash-Next-on-Mothership]]. Narrative:
2026-09-30-claude.

Baseline at the time: q2k via `llama-server` run directly, `-ngl 99 --n-cpu-moe 28 -ot per_layer_token_embd=CPU
--no-mmap -t 8` → **15.3 tok/s generation**; prefill **~174 tok/s** at N=32–34 (4k-token prompt).

**Measured since (2026-09-30 night → 2026-10-01):** the two top rows are no longer "usable today / untested" —
**MTP** (shared-Q4_K_M head, draft 2) is the biggest single lever measured (+45%), **Vulkan** is the faster
backend on this box (+9% at equal split before its CPU repack, +~10% more with it), and together they read
**28.4 / 26.4 tok/s** at N=32 against the morning's 15.3. The expert-cache build (PR #27861, Vulkan) ran as an
overnight order. Numbers, tables and the full lever ledger: [[Qwen3.8-Flash-Next-on-Mothership]]. ⚠ One finding
this research could not have seen: Unsloth's **ROCm** zip ships a CPU backend with no SIMD — every number taken on it
is void; its Vulkan zip is fine.

## Second pass 2026-10-01 13:45 — the two sources nobody had mined, read in full
**DocShotgun MoE offload guide** and the **Flash-Next speed thread (HF discussion #3)**; the fusion claims then
verified by a subagent against `gh api` and the thread's raw JSON (its first summarizer pass had fabricated one
attribution — trust the primary-source results below). What they add, each now a row in the project doc's
"arsenal, combined":
| trick | source says | for us |
|---|---|---|
| **VRAM margin** | ~1 GB free vs a full card: "3–4× difference in decode" (16 GB cards) | our runs carry 0.1–0.28 GB shared spill; untested |
| **`-lzm` lazy mode** | "master mmaps lazy tensors; halves prefill and adds 50 s load", workaround `-lzm off` | our logs: `per_layer_token_embd … lazy read enabled` (default `auto`); prefill-pack arm |
| **big prefill batches** | `-b 4096 -ub 4096` (guide); 4×3090 `-ub 2048` → 1,029 tok/s prefill; `GGML_OP_OFFLOAD_MIN_BATCH` | we tested only 256/512 |
| **cache tuning** | `--moe-expert-cache 48 --moe-expert-cache-inserts 2`; q4_0 KV (+0.4% ppl) frees VRAM → "+25% with 120 slots/layer" | cache-sizing step |
| **`--reasoning-effort low`** | stops ~1.6k unwanted thinking tokens | served config (answer time, not tok/s) |
| **ngram-mod speculation** | +11% (24.7 → 27.5) | our engine has it; one `--spec-type` at a time, MTP wins |
| **hyper-connection fusion** | ggml ops `DSV4_HC_*` (PR #25585, extended for qwen4exp in **#28901**, merged 2026-09-16) with Vulkan shaders | **already in b11160**; +2–4%, bit-identical |
| **sparse attention decode** | open PR **#28213** (gather into a compact buffer; existing ops only → Vulkan-portable): +6% at 31k ctx, +19% at 62k, +50% at 130k | long-context serving lever; invisible at 8k |

## Verdicts, ranked by evidence (as researched 2026-09-30 evening — see the measured note above)

| lever | what the source actually says | for us |
|---|---|---|
| **MTP speculative decoding** (Unsloth fork) | MTP README: B200, greedy — UD-Q4_K_XL 83.2 → 138.8 tok/s (1.67×), UD-IQ1_S 90.1 → 120.9 (1.34×); gain shrinks at higher temperature and at high concurrency. Flags `--spec-type draft-mtp --spec-draft-n-max 2` and **`-md <head>` must be passed explicitly** (heads sit in `MTP/`, which auto-discovery skips) | **Usable today, on disk.** Engine + head downloaded with the owner's OK; see below. Not yet measured cleanly |
| **GPU hot-expert cache** `--moe-expert-cache N` (ggml-org/llama.cpp **PR #27861**, draft, opened 2026-08-28) | PR thread: author 18.4 → 24.2 tok/s (+31%, 48 slots/layer, ~4.1 GiB VRAM) on Flash-Next; RX 6950 XT **8.2 → 12–13 tok/s** (128 slots, ~85% hit); RX 9070 XT +31% on a 35B MoE | Real but a project: build llama.cpp from a draft PR with ROCm on Windows, and it would not be the same build as the MTP fork |
| **Vulkan instead of ROCm** | Strix Halo iGPU (gfx1151, unified memory, Linux): Vulkan 11.50 vs ROCm 1.79 tok/s | Untested here. Different hardware class (iGPU, unified memory, Linux) — the magnitude will not transfer; a cheap A/B if we ever want one |
| **RAM 4200 2:1 → 3800/3600 1:1** | No MoE/llama.cpp benchmark found either way | Measure before/after; direction unknown (latency improves, peak bandwidth drops) |
| **Threads 16** | — | Untested cleanly (an earlier 16-thread run lost with other apps up) |
| **exllamav3-rocm-moe** (loxdude) | Built for gfx1100 + this model, 45–83 tok/s on a 7900 XTX + **7950X3D, 60 GB DDR5** | **Out:** README requires **Linux, ROCm 7.x** and an **AVX-512 VNNI/VBMI** CPU kernel with no AVX2 path; the 5800X (Zen 3) has no AVX-512 |
| **Strata** (Niko Veit) | v0.1.13 prefill 572 → 1,290 tok/s on Q2_0 via 8k-token chunked, overlapped expert streaming | since v0.1.37 (2026-10-02) also an AMD Windows HIP build listing gfx1100 (7900 XTX), community-written, untested by its author — a Mothership candidate; RAM fit (32 GB vs its 48 GB Q2_0 setup) unmeasured |
| **moe-autopilot** (JigSawPT) | Offline hot-expert profile pinned to VRAM, +26–32% decode, prefill −10%, CUDA/Blackwell only | Not usable; same idea as PR #27861 |
| Memory compression off | General Windows guidance only | **Not a lever — measured already OFF** (see the project doc) |
| NUMA / CCD pinning | Multi-socket / multi-CCD advice | Not applicable (5800X is one CCD) |
| `ffn_down_exps` below IQ4_NL | Width 640 is not divisible by 256: only block-32/64 formats fit | Architectural floor, matches our cooks |
| Separate small draft model (non-MTP) | Nothing found — all speculative decoding for this model uses its own MTP head | — |

## Corrections made when checking the sweep's claims
- **"Dual RX 6950 XT: 8.2 → 18.9 tok/s (2.3×)"** came from a `freenode.net/article/…` page (freenode is an IRC network,
  not a publication). The PR thread itself reports the RX 6950 XT at **8.2 → 12–13 tok/s**. Use the PR's number.
- **"No ROCm prebuilt for the MTP fork"** is wrong: `unslothai/llama.cpp` release **`b11160-mix-a6922cc`**
  (2026-09-25, 47 assets via the GitHub API) ships `app-b11160-mix-a6922cc-windows-x64-rocm-gfx110X.zip` (169.7 MB).
  The README's "`b10715-mix-86bd2d3` or newer" is satisfied by it. (The web view lists only 10 assets; the API lists all.)
- **exllamav3-rocm-moe** "verify an AVX2 fallback" → checked: there is none, and it is Linux-only.
- **The `-lm` flag rename** (from an `openclawdc.com` blog): on our binaries, ollama's bundled `llama-server`
  (`0f3a71be1`) still takes `--no-mmap`; the Unsloth fork (b11160) takes **`-lm none`** instead. Each ladder arm states
  its own load flag.

## What we have on disk for MTP
| what | where | notes |
|---|---|---|
| Unsloth llama.cpp fork **b11160**, Windows ROCm gfx110X (commit `a3c12db9d`) | `C:\ProgramData\HAI\tools\llama-unsloth-b11160\` | `ggml-hip.dll` sits beside the exe, so it finds the GPU without `GGML_BACKEND_PATH`; setting it anyway prints a harmless "failed to find ggml_backend_init". Has `--spec-type draft-mtp`, `-lm`; **no** `--moe-expert-cache` |
| MTP head `mtp-Qwen3.8-Flash-Next-shared-Q4_K_M.gguf` (1,907,151,936 B, sha256 `f521868a…`) | `F:\Models\Qwen3.8-Flash-Next-work\MTP\` | "shared" = borrows the main model's embedding/output, so no quant matching. Unsloth recommends **shared-Q8_0** (2,786,568,256 B) — not downloaded |

Other heads in the repo's `MTP/` folder: BF16 7.77 GB, Q4_K_M 2.79 GB, Q8_0 4.14 GB, shared-BF16 5.23 GB.
