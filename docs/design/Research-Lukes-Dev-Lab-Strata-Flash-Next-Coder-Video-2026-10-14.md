# Research note — "Strata Coder tested - Local LLM setup" (Luke's Dev Lab, 2026-10-14): Qwen3.8-Flash-Next Coder under Strata on a 16 GB card with 32 GB RAM

**EXTERNAL SOURCE NOTE — not a lab measurement.** What a third-party YouTube video claims, tagged by how far each claim was checked. This is about **Qwen3.8-Flash-Next** (the 176B-total, ~6B-active MoE, run by Strata), **not** the dense Qwen3.8-27B — the two are separate models and the numbers do not transfer. The lab's own Flash-Next work is in [[Qwen3.8-Flash-Next-on-Mothership]]; the project hub is [[34-CHARTER]].

Filed 2026-10-15 by the side session at the owner's request. Raw files (captions, info JSON, description, cleaned transcript) are in `Claude Home\research\video-09GB4LtXUok\`. Companion intake: [[Research-Local-Explained-24GB-KV-Cache-Video-2026-10-13]] (the 27B and KV-cache video, which cites this channel).

**Certainty tags:** **[D]** stated in the video, unchecked · **[V]** checked against a primary source · **[L]** compared with a lab measurement · **[?]** named but not found.

## The video

| | |
|---|---|
| Title | Strata Coder tested - Local LLM setup |
| Channel | Luke's Dev Lab (captions render it "Leaky's Dev Lab"; site `lukesdevlab.com`, written "leaksdevlab.com" in captions) |
| Uploaded | 2026-10-14 · 18 min 32 s · 14 chapters · 42,191 views at pull time |
| Captions | YouTube **auto-generated English only**; the speech appears machine-translated, so names and some numbers are garbled (see "Name repairs") |
| Thesis | Strata, an inference engine for Qwen3.8-Flash-Next, runs the "Coder" build fast enough on a 16 GB card with only 32 GB of RAM to be a practical coding model; it is held back on tool-heavy tasks |

## Results as stated [D]

| Test | RTX 2000 16 GB | RTX 5090 |
|---|---|---|
| Reading speed, uncached | a little over **70 tok/s** | just under **200 tok/s** |
| Reading speed, cached profile | "starts near 8 thousand and rises to 479 thousand" (a cache effect; units garbled) | "starts near 22 thousand, over a million" |
| Decode | **42–43 tok/s** | about **182 tok/s** |

- **Memory (needle) test:** context filled to 128 K (the most his VRAM allows), data inserted at 0/25/50/75/100 % depth, three runs each: **15 of 15 found**.
- **Reasoning suite (his own):** easy 100 %, medium 100 %, hard 92 %, expert 58 %.
- **HumanEval Remix (100 Python tasks):** **99/100**; he says the test is now too easy.
- **Agentic builds (context compactions, share of context used):** Kanban — 2 compactions, 69.2 %, "one of the best Kanban results I've seen"; sand physics — 1 compaction, 63 %, needed a re-prompt and got stuck in a loop once; dungeon crawler — 0 compactions, 64.8 %, no help needed; Blender (MCP) — 3 compactions, 3 extra prompts, fixated on screenshot tooling; Godot (MCP) — 5 compactions, 67.8 %, repeated fixes (blurry render, reversed controls, a crash on death, an unreachable key).
- **Verdict:** performance is the strength; "reliable", needs help on MCP-heavy tasks.
- **Comparisons he makes:** Flash-Next Q3 K XL on plain llama.cpp had acceptable decoding but "pre-filling was a nightmare" — Strata is "definitely better than llama.cpp"; a dense 27B "does very well for Blender but runs about four times slower" on this hardware.

## Compared with the lab's own Flash-Next data [L]

Lab side: Mothership (7900 XTX 24 GB, 32 GB RAM until the layout-C swap), Strata v0.1.39 Windows HIP with its own **Q2_0** build in low-RAM mode, 2026-10-04, preliminary (one run per arm). Source: [[Qwen3.8-Flash-Next-on-Mothership]].

| Question | Video | Lab | Read |
|---|---|---|---|
| Strata beats llama.cpp on Flash-Next? | yes; llama.cpp prefill "a nightmare" | **yes**: decode ~2.3–2.7× after warm-up (Strata last-5 median 45.8 vs llama.cpp 19.8 tok/s); prefill 449–681 vs 84–135 (~5×) | Agrees in direction and rough size |
| Warm-up | not mentioned directly ("cached profile" rises) | Strata was **slower than llama.cpp for the first 3 prompts** while its expert cache filled; load 285 s; a repeated prompt ties (30.5 vs 30.2) | The video's "cached profile" is that same warm-up effect; neither side's headline decode figure is a cold, varied-prompt figure |
| Decode speed | 42–43 tok/s (16 GB card) | varied prompts 7.3 → 54.1 (rising), median of last five 45.8 on a 24 GB card; real thinking-on work 8.8–13.1 tok/s (llama.cpp-class engine, 2026-10-02) | Same range as the best warm Strata rows; the lab's real-work rate is a different regime |
| Reading speed | ~70 (uncached) / <200 (5090) | 449–681 on Mothership | The two disagree by 3–10×. Strata's own Coder page claims 1,420–2,180 on an RX 9070 XT 16 GB / RTX 5070 12 GB. Three different quants, cards and harnesses; **not comparable and not reconciled** |
| "Dense 27B is ~4× slower" | on his 16 GB card with a heavier file | Reverse on Mothership: the 27B ran the same prompts at **73.5 tok/s vs Flash-Next 10.6** (real work), and 64.3 tok/s for the 3.8-27B on Vulkan | A dense model reads its whole file per token; a MoE reads only active experts. Which wins depends on whether the dense file fits the card's fast memory. On 24 GB it does; on his 16 GB card it does not |
| Flash-Next uses more tokens | 5 compactions on Godot, 63–69 % of context on every build | the lab recorded Flash-Next thinking longer (code-1: 69 K vs 25 K tokens for the 27B) | Consistent |
| Coding benchmarks saturate | HumanEval Remix 99/100, "should be more difficult" | the lab's family-2 coding set is **saturated on Mothership** (every model 97–100 %) | Both ends see the same ceiling; neither separates models |
| Quality of the low-bit quant | 99 % / 92 % / 58 % on his own suite | **"Not measured: answer quality"** for the lab's Q2_0 Strata build | The video's suite is a data point for the Coder build only, not for the lab's quant |

**Not in the lab's data:** the Coder (IQ1_M, 256-of-512-expert) build — the lab has run Strata's Q2_0 and its own `q2mix` cook, not this one. Strata's table puts Coder in the 32 GB-RAM tier, which is Mothership's current RAM.

## Sources named — resolution status

| # | Source | Status |
|---|---|---|
| 1 | Strata models page `docs/MODELS.md#coder` | **[V]** fetched 2026-10-15: Coder row and the prompt-reading and decode figures above |
| 2 | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder` model card | **[?]** HTTP 401 on fetch; no search hit |
| 3 | `lukesdevlab.com` comparison site (his results and other models) | **[?]** not opened |
| 4 | His earlier Flash-Next-on-llama.cpp attempt (Q3 K XL) | **[?]** another video of his; not located |
| 5 | A "Loop Police" extension for the Pi coding agent (loop detection) | **[?]** named in passing |
| 6 | The Strata project's own benchmark claims | the lab has its own page for them: [[Qwen3.8-Flash-Next-on-Mothership]] |

## Name repairs (caption → likely)

"Leaky's Dev Lab / Leaksdevlab" = Luke's Dev Lab · "Coda" = Coder · "GSQR / GSM RCO" = GSQ-RCO · "Das Lab / DS Lab" = ISTA-DASLab · "Flash Next" = Qwen3.8-Flash-Next · "RTX 2080" and "RTX 2000" = most likely the same 16 GB 70 W card (chapter title: RTX 2000) · "compressions / simplifications" = context compactions · "seals" = compactions · "the Pi" = the Pi coding agent · "Sandbox of Layers" = a sand-physics sandbox.

## Files

`Claude Home\research\video-09GB4LtXUok\` — `09GB4LtXUok.en.srt` (raw auto-captions) · `transcript.txt` (de-duplicated, one block per minute) · `09GB4LtXUok.description` · `09GB4LtXUok.info.json` · pulled by yt-dlp inside the Pinchflat container on Sundae (cookies mount is read-only, so yt-dlp's closing cookie save fails harmlessly after the files are written).
