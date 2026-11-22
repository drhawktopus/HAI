# Research note — "Qwen 3.8 27B Just Hit 200 Token/s on Mac M5 (Lithos Metal)" (Kai, 2026-10-16): an external trained drafter, measured honestly

**EXTERNAL SOURCE NOTE — not a lab measurement.** What a third-party video claims, tagged by how far each claim was checked. This is evidence for #36's lever 2, **external drafting** ("external MTP"): a separate small drafter guesses tokens and the main model checks them. It is Apple-silicon (Metal) evidence only; nothing here installs on the lab's NVIDIA, AMD or CPU boxes. The lab's own drafting results are in [[36-Split-Inference-Helpers-Read-and-Draft-Design]]; the project hub is [[34-CHARTER]].

Filed 2026-10-16 by the side session at the owner's request. Raw files: `Claude Home\research\video-L7noXCBSObc\`. Same channel name as the Flash-Next video cited in [[Qwen3.8-Flash-Next-on-Mothership]] (Kai, `@kaiexplainsYT`; I did not compare channel IDs).

**Tags:** **[D]** stated in the video · **[V]** checked against a primary source · **[L]** compared with a lab measurement · **[A]** arithmetic redone here · **[?]** named but not opened.

## The video

| | |
|---|---|
| Uploaded | 2026-10-16 · 19 min 46 s · 10 chapters · 959 views at pull time |
| Captions | auto-generated English only (names garbled: "Lithos Metal" appears as "Llama Metal", "DSpark" as "Deep Spark") |
| Description | unusually complete: the repo, README, the study file at a pinned commit, the DSpark paper, the Qwen card and every cited thread |
| Thesis | the "200 tok/s" headline is a **projection**; the team's own measured run is about **80 tok/s**, still double the hardware ceiling; the gain swings with what is being written, and the agent bill is dominated by **reading** |

## The claims and what the primary sources say

| Claim [D] | Check |
|---|---|
| A small drafter (1.1 GB) guesses **7** tokens; the big model checks all in one read plus one trusted token = 8 rows | **[V]** README: DSpark proposes **seven** tokens, verified with one anchor; study: "five-layer draft model", BF16, no size given — the **1.1 GB is [?]** |
| 168 / 127 tok/s (128 / 32K context) are "target compute projections" assuming six accepted tokens per eight-row pass, excluding drafting, acceptance and rewind | **[V]** README says exactly this: "not end-to-end rates" |
| Their own run, Oct 3 (five days before the blog): 4 prompts × 128 tokens, ~150 rounds, **~2.5 accepted per round**, ~80 tok/s | **[V]** study: 40-core M5 Max, 48 GB, target `nvidia/Qwen3.8-27B-NVFP4`, drafter `RadixArk/Qwen3.8-27B-DSpark`; **152 rounds, 2.401 accepted/round (BF16 drafter), 2.510 (first NVFP4 drafter)**; **12.46–12.47 ms of GPU time per delivered token**. The study never states tok/s; 1000 ÷ 12.47 ≈ **80** is a derived figure. Excludes prefill and compilation; whether rewind is excluded is not stated. The study itself calls the prompt set small and "not a universal acceptance-quality claim" |
| 200 needs ~6 of 7 guesses right; 2.5 is the measured rate | **[A]** consistent: 80 vs 168 projection = the acceptance gap |
| DSpark came from a DeepSeek paper, **60–85 % faster per user** in DeepSeek's serving | **[V]** arXiv 2607.05147 abstract: parallel drafting backbone plus a light sequential module, "confidence-scheduled" verification length; **60–85 % per-user speedup at matched throughput versus an MTP-1 baseline** in DeepSeek-V4 serving. The abstract does not say whether the drafter must be trained per target |
| An untuned fallback on an M4 Pro: English at **2 tok/s with drafting vs 7 without** | **[?]** — "when guesses miss, the drafter makes it slower than baseline" |
| Agent session: **196,000 tokens read, 31,000 written** (8 tasks, 168 turns, after caching) — 6:1 | **[?]** AgentPerf-Local, not opened |
| The shipped release reads prompts **374 tok/s vs oMLX's 948** (4K prompt; a 16K prompt starts in 43 s vs 17 s); the whole agent session takes ~18 min vs ~13 min despite faster writing; merged fixes would bring it to ~10 min | **[A]** redone: 31,000 ÷ 55 = 9.4 min + 196,000 ÷ 900 = 3.6 min ≈ **13 min** (oMLX); 31,000 ÷ 59 = 8.8 min + 196,000 ÷ 374 = 8.7 min ≈ **17.5 min** (Lithos today); at 80 tok/s with oMLX-speed reading ≈ **10 min**; at 200 ≈ **6 min**. The reading figures are the owner's **[?]** |
| One generation at a time (a second request gets 429); Apple-silicon only; the full tuned path exists for one chip (40-core M5 Max) | **[V]** README: "one generation at a time", Apache 2.0, 32K default context, other chips listed as unmeasured/probe-derived |
| A bug report (Oct 14, M3 Max) shows drafted output **diverging from plain output at temperature 0** | **[?]** GitHub issue 54, not opened. Speculative decoding is supposed to be output-identical, so this is a correctness flag, not a speed one |

## Against the lab's own drafting data [L]

| Question | Video | Lab |
|---|---|---|
| Bandwidth ceiling | 614 GB/s ÷ 16 GB ≈ 38; a good plain engine reaches ~80 % | Same law. Mothership's 7900 XTX ≈ 960 GB/s ÷ 15.3 GB ≈ 63; the 3.8 on Vulkan with **built-in MTP** measured a median 64.3. The lab has **no MTP-off cell for the 27B** (see [[Research-Local-Explained-24GB-KV-Cache-Video-2026-10-13]]) |
| External drafter vs the model's own MTP head | external 5-layer DSpark drafter: ~2.4–2.5 accepted of 7, ~2.1× the ceiling; vs oMLX using Qwen's **built-in** drafting it wins on code (+7 %, +25 % at 32K) and loses on English (−10 %) | On rigby the lab found external drafting **superseded by the model's own MTP head** (draft 2: 94–100 % acceptance on code, +19–24 % write; draft 3 loses). A tablet drafter lost (1.87 vs 2.38 tok/s on a 14B) |
| What caps drafting | drafting is cheap, acceptance sets the gain; wrong guesses cost | On rigby **checking k tokens costs ~3× a single token** (no AVX2, compute-bound), so the checker caps it. The lab explicitly left open "where checking IS cheap — a GPU checker, e.g. the 3.8 on the 7900 XTX" (step 5) |
| Content type | code drafts well, prose does not (59 vs 34) | the lab's best acceptance is on code (94–100 %), the same split |
| Negative value on misses | English slower with drafting on | the lab's draft 3 losing on rigby is the same shape |

**Read [I]:** this is the first evidence the lab has seen on its open step-5 question — an external, target-trained drafter on a bandwidth-bound GPU-class checker — and it says: **a modest edge over the model's own MTP head on code, a loss on prose, a large swing with context, and the headline is mostly projection**. It is Apple Metal, one chip, a 4-prompt n=1 study, with an owner's thread for the content split; nothing about it ports directly. The DSpark drafter is trained for one target (`Qwen3.8-27B-DSpark`); whether any lab engine can load it is unknown. (One search result I saw earlier mentioned DSpark support for Nemotron 3.5 in llama.cpp release notes **[?]**.)

## Transferable lessons (beyond this model)

1. **Ask what was counted** — a projection that assumes six of seven guesses, a sum across four requests, a document copied back out of the prompt (381 tok/s on an RTX 3090 where ordinary chat ran ~133). Same lesson as the earlier Mac and VRAM videos.
2. **Ask what was being written** — drafting speed is content-dependent, so a number needs its text type attached.
3. **Time the whole task.** The engine with the faster decode headline finished the agent's work ~5 minutes later because of reading speed. Decode tok/s is the wrong headline for agents; the lab's A1 harness already measures wall time per task.
4. **Speculative decoding must be output-identical** — a drafted-vs-plain divergence at temperature 0 is a bug; the lab's KLD and slot-handoff checks in #36 are the same kind of test.
5. **The fastest token is the one never written** — the 5.5× thinking-token multiplier makes effort level a bigger lever than an engine swap (see the lab's effort-level audit).

## Not known

The drafter's size and licence; whether an Ollama or llama.cpp path exists for a DSpark-style drafter on the 3.8; acceptance on the lab's real agent text (A1 transcripts) rather than 4 short prompts; the owner measurements (code/English split, prefill) beyond the video's relay.

## Name repairs (caption → likely)

"Llama Metal / Lithos Metal" = lithos-metal · "Deep Spark" = DSpark · "Quen / Quinn" = Qwen · "OMLX" = oMLX · "Deep Flash 2 from Z Lab and Inko" = DFlash 2 (z-lab) · "Splash" = Splash · "Llama 2" in the comparison chart passage = llama.cpp · "1M5 Max owner" = an M5 Max owner.

## Files

`Claude Home\research\video-L7noXCBSObc\` — `L7noXCBSObc.en.srt` · `transcript.txt` · `L7noXCBSObc.description` (carries the full source list) · `L7noXCBSObc.info.json`. Pulled with yt-dlp inside the Pinchflat container on Sundae.
