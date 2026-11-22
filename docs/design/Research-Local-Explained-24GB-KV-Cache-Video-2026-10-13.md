# Research note — "Is 24GB All You Need For Serious Local LLM?" (Local Explained, 2026-10-13): KV-cache budgeting for Qwen3.8-27B on 16 GB vs 24 GB

**EXTERNAL SOURCE NOTE — not a lab measurement.** Everything below is what a third-party YouTube video claims, tagged by how far it has been checked. Nothing here is a lab result; the lab's own numbers live in [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]], and [[36-Split-Inference-Helpers-Read-and-Draft-Design]].

Filed 2026-10-14 by the side session at the owner's request (research pool for [[34-CHARTER]] — the 24 GB Mothership tier — and #36). Raw files (captions, info JSON, description, cleaned transcript) are in `Claude Home\research\video-0LO0clJSgZY\`.

**Certainty tags:** **[D]** stated in the video, unchecked · **[V]** checked against a primary source · **[A]** arithmetic re-done here from the video's own inputs · **[?]** source named in the video but not found.

## The video

| | |
|---|---|
| Title | Is 24GB All You Need For Serious Local LLM? |
| Channel | Local Explained |
| Uploaded | 2026-10-13 · 19 min 54 s · 18 chapters |
| Captions | YouTube **auto-generated English only** (no creator captions). The audio appears to be machine-translated or dubbed, so proper names are garbled in the captions (see "Name repairs") |
| Thesis | 24 GB is enough for a serious local coding agent **only if** you count what fills the card after the model loads (the KV cache). The author says the 16 GB part is community-measured and **the 24 GB part is his own calculation, not a measurement** |

## What it claims (chapter order)

1. **[D]** Micro Center, early Oct 2026: RTX 5090 98 tok/s vs RTX 5070 Ti under 9 tok/s on the same model and job; the 5070 Ti hit its memory limit at about 8,000 tokens with 61 % of the work done.
2. **[D]** A viral earlier video ("16 GB is all you need for serious AI", 585 K views) ran Qwen3.8-27B on an RTX 5060 Ti 16 GB at **45.6 tok/s**; context set to 64 K on the 5060 Ti vs 262 K on a 5090 with the same file.
3. **[V]** The model fits because of ISTA-DASLab's GSQ-RCO quant: **11.8 GB** (IQ3_S) against 17.44 GB for standard Q4. See "Verified".
4. **[D]** Two things sit on the card: weights (fixed) and the KV cache (grows). **64 KB per token** on Qwen3.8-27B → 32 K tokens = 2 GB, 128 K = 8 GB, 262 K ≈ 16 GB. Mistral Devstral Small: **160 KB/token**.
5. **[A]** 16 GB card, 11.8 GB model: about **47 K tokens** at 16-bit KV; about **110 K** with vision off and KV at 8-bit. Extras counted: 0.35 GB MTP piece, 0.93 GB vision projector, about 1 GB llama.cpp margin.
6. **[D]** Agents spend tokens fast: Claude Code-style tools load 30–32 K tokens of instructions before you type; a Kanban app task filled 87 % and a Blender task 96 % of a 128 K window (111 K and 123 K tokens).
7. **[D]** Why the same card reads 3 tok/s in one review and 70 in another: (a) spill to system RAM (a 4060 Ti owner with KV in system RAM got 6–7 tok/s); (b) memory bandwidth — tok/s ceiling = bandwidth ÷ file size (5060 Ti 448 GB/s ÷ 10.93 GB 3-bit file ≈ 41; one owner measured 35, 85 % of it); (c) MTP — 27.4 → 45.6 tok/s on a 5060 Ti, about 70 tok/s with MTP up to 32 K context for one custom build, switched off beyond that because drafts need cache space.
8. **[D]** Prompt-reading speed is the unspoken number: 5060 Ti about **738 tok/s** → a fresh 30 K-token agent start is about 41 s of silence; an RX 9060 XT 16 GB read 32 K at 149 tok/s (over 3 min).
9. **[A]** The 24 GB case: about 11.7 GB free → **about 175 K tokens at 16-bit**, over 300 K with vision off and 8-bit KV; both of Luke's agent tasks fit with about 50 K spare; Devstral about 64 K tokens instead of 11 K. Q4 (17.44 GB) loads but leaves about 6 GB (about 90 K tokens) for roughly the same task scores.
10. **[D]** What 24 GB does not fix: speed (bandwidth), prompt reading time, and the "load the biggest quant" temptation (the author says it halves your cache and cuts the bandwidth ceiling by about a third for no measured gain).
11. **[D]** Price/bandwidth table (this week, 2026-10): RX 9060 XT 16 GB $470 / 320 GB/s; RX 9070 $650; RTX 5060 Ti 16 GB $789 / 448 GB/s (launch $429, +84 %; the 8 GB version only +24 %); RX 9070 XT $790 / 640 GB/s. An RTX 5070 Ti read 32 K more than 3× faster than an RX 9070 XT in llama.cpp while writing under 10 % faster.
12. **[D]** 16 GB Macs expose about 12.7 GB to the GPU by default (about 14 K tokens left); Mac mini 153 GB/s; laptop "5070 / 5060 Ti / 4060 Ti" parts are sold with 8 GB or 12 GB under the same name.
13. **[D]** Buy vs rent: OpenRouter Qwen3.8-27B about **$2.95 per million output tokens**; home electricity at 180 W and 35 tok/s about $0.26 per million → a $789 card pays back after about 294 M tokens (97 days nonstop, about 10 months at 8 h/day); with a $0.16/M model it is 2.9 years nonstop.
14. **[D]** Over 24 GB: two 16 GB 4060 Ti ($1,578, 32 GB) — one user went 27 → 46 tok/s by changing how the model is split; shared presets from 3-bit/64 K on one card to 6-bit/131 K on two.
15. **[D]** Four rules: buy on bandwidth not fame; run a model that leaves room; turn off what you do not use (vision file, auto-loaded extras; plug the monitor into integrated graphics to free about 0.5 GB); give the remainder to the cache.

## Verified against a primary source [V]

| Claim in the video | Model card |
|---|---|
| 11.8 GB quant | IQ3_S, 3.50 bpw, **11.8 GB** ✅ (IQ2_XS 8.4 · IQ2_S 9.3 · IQ3_XXS 10.1 GB) |
| 0.93 GB vision file | `mmproj-Qwen3.8-27B-BF16.gguf` **0.9 GB** ✅ |
| 0.35 GB MTP piece | every quant has an `-mtp` build **about 0.35 GB larger** ✅ |
| math 30/30 for both | AIME25 **100.00** for BF16 and IQ3_S ✅ |
| coding tie | LiveCodeBench v6 **85.71 = 85.71** ✅ |
| science exam: one more miss out of 198 | GPQA-Diamond 89.90 → **89.39** (0.51 pt = exactly 1/198) ✅ |
| "no loss of task" | the card says "task-lossless operating point" ✅. Caution: it is a benchmark-recovery claim (zero-shot average 100.2 %), not bit-identical output; the card's table does not state sample sizes |

## Arithmetic re-done [A]

Inputs taken from the video; KV 64 KB/token read as 65,536 bytes.

| Case | Re-computed | Video says |
|---|---|---|
| 16 GB: 17.18 − 1.0 margin − 11.8 − 0.35 − 0.93 | **3.1 GB → about 47 K tokens** | 47 K ✅ |
| 128 K tokens × 64 KB | 8.0 GiB | 8 GB ✅ |
| 24 GB: 25.77 − 1.8 margin − 11.8 − 0.35 − 0.93 | **10.9 GB → about 166 K** | 11.7 GB, about 175 K ⚠ |
| 24 GB with a **1.0** margin (as on the 16 GB card) | 11.7 GB → about 178 K | matches the video's 11.7 / 175 K |
| 24 GB with Q4 17.44 GB | about 5.3 GB (about 80 K) with the same deductions | "about 6 GB, about 90 K" ⚠ |

So the 24 GB headline reproduces only with a 1.0 GB margin; the spoken "1.8 gigabyte margin" is either a caption/dub error or an inconsistency. The conclusion (175 K-class at 16-bit) survives within about 6 %. The author flags the 24 GB number as his calculation.

## Compared with the lab's own data (2026-10-14)

Lab side: Mothership's 7900 XTX 24 GB serving `qwen3.8:27b` (Q4_K_M, MTP on, flash attention, q8_0 KV, one slot). Sources: [[RUNBOOK]] § "Tested maximum context" (fit rows `fit-20260929`, ollama 0.34.0 ROCm), [[RUNBOOK-Ollama-Host-Operations]] (0.40.2 Vulkan verification 2026-10-10), [[HAI-LLM-Execution-Context-Mechanics]], [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]].

| Question | The video | The lab | Read |
|---|---|---|---|
| Does 24 GB hold this model with a large window? | yes: about 175 K tokens at 16-bit KV, 300 K+ at 8-bit with the 11.8 GB quant [A] | **yes: 123,904 tested ceiling; standing setting 122,880**, `size_vram == size`, MTP on | Same direction. The lab runs the heavier Q4_K_M file (15.3 GB model buffer), so its ceiling is lower than the video's number, which assumes the 11.8 GB file |
| Weights | IQ3_S 11.8 GB (GSQ-RCO), "task-lossless" on the model card | Q4_K_M, 15.3 GB buffer. **The lab has not tried GSQ-RCO** | Untested option, about 3.5 GB less weight on Mothership. Nothing here says change the champion |
| Does the video's method predict the lab's ceiling? | weights + KV + about 1 GB | For `qwen3.6:27b` yes: 14.8 + 6.26 KV + 1.0 compute ≈ 22 of 23.4 GB at 188,416. For `qwen3.8:27b` **no**: the same method predicts about 21 GB at 123,904 (with the 0.48 GB MTP draft cache), 2+ GB under the card, yet ollama spilled at 124,928 | The ceiling is not "the card is full" for the 3.8. Cause not established (see next row) |
| KV cost per token | 64 KB at 16-bit (= 32 KB at 8-bit) — **[V] correct for Qwen3.8-27B: the official config gives 16 full-attention layers × 4 KV heads × head_dim 256 × 2 (K,V) × 2 B = 65,536 B = 64 KiB at f16, about 34 KiB at q8_0** (see "Official architecture check") | `qwen3.6:27b`: 33 KB/token at q8_0 (6.26 GB at 188,416) — matches. **`qwen3.8:27b` fit rows: ollama's resident `size` grows only 17.54 → 17.83 GB from 65,536 to 122,880 tokens, about 5 KiB/token** | The video and the official architecture agree, and so does the lab's 3.6. The lab's 3.8 fit rows are the outlier: the architecture predicts about 1.9 GiB of growth over that span at q8_0, and `size` shows 0.28. So ollama's reported `size` under-counts the 3.8's cache; it is not evidence the 3.8 keeps less KV |
| Where does the cliff come from? | VRAM exhaustion | At the spill step, `size` jumps 17.83 → 18.73 GB (+0.9 GB) and `size_vram` falls 17.83 → 17.57 GB: one layer goes off-card while ollama still shows about 5 GB of the 24 GB unused | The 3.8's ceiling is a step in ollama's memory plan, not capacity. It was measured 2026-09-29 on 0.34.0 ROCm; I found **no re-fit on 0.40.2 Vulkan** (the 10-04 Vulkan re-fit of gemma3:27b moved its row), so it may have moved |
| Speed model: tok/s ≤ bandwidth ÷ file size | holds: 5060 Ti 448 GB/s ÷ 10.93 GB ≈ 41, owner 35 (85 %) | 7900 XTX about 960 GB/s ÷ 15.3 GB ≈ 63 plain-decode ceiling [A]. **Measured median 64.3 tok/s** (Vulkan, MTP on, thinking off, CHAT1 cells); 53.1 on ROCm; A1 median 57 | Measured sits at the plain ceiling, so MTP is what puts it there — consistent with the video. I found **no MTP-off cell for the 27B**, so the lab cannot yet state MTP's gain. 960 GB/s is the spec figure |
| MTP trades against context | on a 16 GB card MTP is switched off past about 32 K because drafts need cache | MTP stays on to 122,880 on 24 GB; its cost is a 0.48 GB draft cache at 123,904 plus about 0.5 GB larger model buffer (15.3 vs 14.8 GB) | The video's 24 GB assumption ("MTP can stay on, not measured") is **supported by lab data**: about 1 GB, not a trade-off |
| A big window costs speed | not claimed directly; the video's cost is reading a full window | 16,384 vs 122,880 window on identical short work: 39.1 vs 39.1 tok/s, 75/75 identical answers (a different question: allocated, not filled). `nemotron-3-nano` pays about 9 % at 1 M | Not in conflict. The lab has not measured a **filled** 100 K window |
| Reading time (prefill) | 5060 Ti about 738 tok/s → 30 K agent start ≈ 41 s | **No qwen3.8:27b prefill figure on Mothership in the docs.** (Flash-Next 125B: 50–900 tok/s; rigby and tablets measured separately) | Gap. A `speedcheck`-style cell with a 30 K prompt gives the lab's number |
| Agents fill windows fast | 30 K start; Kanban 111 K, Blender 123 K tokens | HAI's tool schema alone is about 13 K tokens, `app.py` about 30 K; the 2026-06-29 self-edit prompt (44.9 K) truncated and failed at 32 K and ran at 65 K; one runaway thinking answer filled exactly 65,536 (2026-09-29); rigby's A1 hit `exceed_context_size_error` at 32,854 vs 32,768 | Same failure class, seen repeatedly. Note the Blender task (123 K) is at the lab's 122,880 window |
| Micro Center story (fast card stops at 8 K) | a card stops when its window fills | The lab's "count the rows that hit a limit" rule; void rows are rerun with room | Same lesson |
| Buy vs rent, prices, Macs, laptops | the video's economics | no lab counterpart (the lab owns the cards) | Out of scope |

**Bottom line.** Where the lab has data it supports the video's main claim: 24 GB holds this model with a six-figure window and MTP on, and tok/s sits where the bandwidth rule puts it. What does not line up: the lab's 3.8 ceiling is lower than the video's arithmetic, and the lab's 3.8 fit rows show about 5 KiB/token where the official architecture says 64 KiB (f16) or about 34 KiB (q8_0) — so ollama's `size` under-counts the 3.8's cache, and the ceiling's true cause (KV precision, MTP buffers, or the memory plan) is open. Not measured by the lab: GSQ-RCO IQ3_S, prefill of this model on Mothership, MTP off, and a filled 100 K window.

- **Dense 27B** (not Flash-Next, which is the separate 125B-A6B MoE): 64 layers in 16 blocks of 3 Gated DeltaNet + 1 Gated Attention → **16 full-attention layers**, 48 linear layers (fixed-size recurrent state, no growing cache). Full attention: 24 query heads, **4 KV heads, head_dim 256**. Hidden 5120, FFN 17,408, native context 262,144 (1 M with YaRN), 1 MTP layer. "Built on the architectural foundation of Qwen3.5"; the card describes **no architectural difference from Qwen3.6-27B**.
- **KV per token** = 16 × 4 × 256 × 2 × 2 B = **64 KiB (f16)**, ≈ 34 KiB at q8_0. This matches the video's 64 KB, Backburner's 34 KB at 8-bit, and the lab's `qwen3.6:27b` measurement (6.26 GB at 188,416 = 33 KiB at q8_0).
- **MTP draft cache check:** one MTP layer = 1 × 4 × 256 × 2 × 2 B = 4 KiB/token at f16 → 0.47 GiB at 123,904 tokens. The runbook's logged **0.48 GB** matches that exactly. So the runner log does contain accurate cache lines for the 3.8; the *main* KV buffer line sits beside the draft one and was not recorded. At f16 the main cache would be 7.6 GiB at 123,904, at q8_0 about 4 GiB — whether the lab's runner uses q8_0 for the 3.8 is therefore **open** (the draft cache looks f16).
- **"Is the 3.8 more efficient?"** The card gives benchmark gains over 3.6 (Terminal Bench 2.1 73.0 vs 63.4, SWE-bench Pro 61.7 vs 53.5, GPQA Diamond 89.2 vs 87.8) and says "preserved thinking" (on by default) improves KV-cache utilization, but gives no token counts, VRAM or speed figures. Memory-wise, per the card, no change from 3.6. Lab-side evidence of efficiency is in thinking length: with room to think, the 3.6 hit the cap on 27 of 75 answers while the 3.8 "thinks briefly" (doctrine 13a). Effort levels per the card: `xhigh` (default), `medium`, `low`; ollama's `think: true` renders medium (see the effort-level audit).

**Correction (2026-10-14).** An earlier version of this note said the video's 64 KB/token "agrees with the lab's own figure". That figure (about 34 KB/token at 8-bit, 16 of 64 layers) is in the #36 doc but comes from the *Backburner* video's captions, not a lab measurement. The lab's own KV measurement is `qwen3.6:27b`'s 6.26 GB at 188,416 tokens; the 3.8's rows disagree, as above.

Not claimed: that any lab setting should change. This note files the source and the comparison.

## Sources named in the video — resolution status

None are linked in the description or on screen as URLs.

| # | Source as the video names it | Status |
|---|---|---|
| 1 | ISTA-DASLab GSQ-RCO quant of Qwen3.8-27B | **[V] found** — model card and two arXiv papers above |
| 2 | Micro Center side-by-side, 5090 vs 5070 Ti, early Oct 2026 (98 vs under 9 tok/s; 8 K tokens; 61 %) | **[?]** two searches found nothing; the video itself says it does not know the store's configuration |
| 3 | "16 GB is all you need for serious AI", 585 K views, presenter garbled in captions as "Manolo Remedy"; 45.6 tok/s on a 5060 Ti | **[?]** not found; the captions leave the creator's name unrecoverable |
| 5 | Mistral Devstral Small at 160 KB/token (caption: "Dev Stroll") | **[?]** not checked against the model config |
| 6 | A YouTuber with "a whole series on AI in 16 gigabytes" (caption: "Bart Selatica") — Claude Code loads 30–32 K tokens first | **[?]** name unrecoverable from the captions |
| 7 | "Luke's Devlab" channel — Kanban 87 %, Blender 96 % of a 128 K window | channel identified: **Luke's Dev Lab** (`lukesdevlab.com`). Those two figures are not in his 2026-10-14 video, which tested a different model (Flash-Next Coder) — see [[Research-Lukes-Dev-Lab-Strata-Flash-Next-Coder-Video-2026-10-14]]; the 87 %/96 % source video is still **[?]** |
| 8 | Owners' measurements: 4060 Ti (KV in RAM, 6–7 tok/s), 5060 Ti (35 tok/s of a 41 ceiling; 738 tok/s prompt; MTP 27.4 → 45.6 and about 70), RX 9060 XT 16 GB (149 tok/s prompt at 32 K), two 4060 Ti (27 → 46) | **[?]** individually unidentified; the video calls them "individual user reports: illustrative, not averages" |
| 9 | Prices and bandwidth figures, week of 2026-10-13; OpenRouter $2.95 / M | **[?]** time-sensitive, not checked |

Resolving the **[?]** rows needs the video's pinned comment, the channel's community post, or the author — worth asking the channel for a source list if any of those figures will be used in a lab decision.

## Name repairs (caption → likely)

Caption text is machine-translated, so read these as guesses: "Quinn / Qwen 3.827B / 3.8-27 B" = Qwen3.8-27B · "Das Lab, Austrian Institute of Science and Technology" = ISTA-DASLab (verified) · "GSQRCO" = GSQ-RCO (verified) · "RX 960 XT / RX 9600 XT" = RX 9060 XT · "RX 9700 / RX 9700 XT" = RX 9070 / RX 9070 XT (not verified) · "648 Mbps" = GB/s · "4070 Ti" in the closing recap = the 5070 Ti from the opening · "Dev Stroll" = Devstral.

## Files

`Claude Home\research\video-0LO0clJSgZY\` — `0LO0clJSgZY.en.srt` (raw auto-captions) · `transcript.txt` (de-duplicated, one block per minute, MM:SS) · `0LO0clJSgZY.description` · `0LO0clJSgZY.info.json` (yt-dlp metadata incl. chapters) · pulled by yt-dlp inside the Pinchflat container on Sundae (the lab's existing method; Softserve's own IP was rate-limited with HTTP 429 on captions).
