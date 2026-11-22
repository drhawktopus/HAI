# Local model archive on Z: — big GGUF models we already have (Nemotron, Qwen3.8-Flash-Next, GLM, Qwen3.5-122B) — `Z:\Archives\Local-Models`

Before downloading a large model, look here. These are archived GGUF files on Softserve's HDD array (the
Plex array — keep bulk reads off it while Plex is busy), downloaded 2026-09-11/12, **not installed on any box**:
no ollama store and no factory declares them, so they are invisible to the desk and absent from the #34 menu until
someone copies one to a box and measures it. Found 2026-09-29 when the owner asked about Nemotron and Qwen3.8-Flash-Next
and a check of the ollama stores alone answered "not downloaded" — wrong. Every file below was checked byte-for-byte
against the download manifest that day (23 entries); the retried ones by SHA-256 on 2026-09-29.

| model | quant | size | vision file | notes |
|---|---|---|---|---|
| Qwen3.8-Flash-Next (125B MoE, 6B active + 51B n-gram table) | UD-IQ3_XXS, 3 parts | 76.3 | mmproj-F16 0.8 | needs llama.cpp with qwen4exp support (merged 2026-08-27) |
| NVIDIA-Nemotron-3-Super-120B-A12B (MoE, 12B active) | UD-IQ3_XXS, 3 parts | 52.7 | — | |
| GLM-5.3-Flash | UD-IQ3_XXS, 4 parts | 112.1 | mmproj-F16 1.1 | `Shard_Rewrite` compatibility copies kept beside the originals |
| Qwen3.5-122B-A10B (MoE, 10B active) | UD-IQ3_XXS | 41.7 | mmproj-F16 0.8 | |
| Qwen3-Next-80B-A3B-Thinking (MoE, 3B active) | IQ4_XS | 39.7 | — | |
| Qwen3-Coder-Next | UD-IQ4_XS | 35.8 | — | |
| Qwen3.8-27B | UD-Q4_K_M | 15.3 | mmproj-F16 0.9 | same model as Mothership's ollama `qwen3.8:27b` |

## Retried 2026-09-29 — the 2026-09-12 "large-three" batch is now complete

| model | quant | size | vision file |
|---|---|---|---|
| GLM-4.5-Air | IQ4_XS, 2 parts | 56.3 | — |
| Llama-4-Scout-17B-16E-Instruct | IQ4_XS, 2 parts | 53.7 | mmproj-F16 1.6 |

## 2026-10-03 — #34 scope challengers + small specialists (`downloads-2026-10-03-scope`, 5/5 SHA-256-verified)

| model | file | size (GB) | for |
|---|---|---|---|
| NVIDIA Nemotron-3-Nano-30B-A3B (MoE, ~3B active) | IQ4_XS (bartowski) | 18.13 | rigby (tight: rigby's Qwen3.6 MoE is 16.8 GB on a 23 GB box) |
| MiniCPM-V 4.6 (1.3B vision) | Q4_K_M + mmproj-f16 (openbmb) | 0.53 + 1.11 | rigby / class-A tablets |
| MiniCPM5-1B (1.08B, tool calling) | Q4_K_M (openbmb) | ~0.7 | rigby / tablets — marked to check (PROJECT-BACKLOG) |
| Qwen3-Reranker-0.6B | Q8_0 (ggml-org) | 0.64 | reranking for HAI's RAG — marked to check (mainline llama.cpp support unverified) |

## Records

Each batch folder (`downloads-2026-09-11`, `-09-12-additions`, `-09-12-large-three`) holds `manifest.json` (URL,
pinned revision, exact bytes per file), `status.json` (state, verified count, errors) and per-file curl logs.
`storage-budget.json` caps the archive at 1.5 TB (projected 526 GB after the 09-12 batches). Verify a file against
its manifest size before using it.

## Related

- [[34-CHARTER]] area A (the 24 GB and 8 GB model tournaments — ran 2026-09-28 → 10-03 on the installed models; none
  of the archived models above has been measured yet)
- PROJECT-BACKLOG "SIDE QUEST: Qwen3.8-Flash-Next on Mothership"
- [[Machine-Softserve]]
