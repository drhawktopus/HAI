# Research note — "I Tested Every Best Local AI For GPU (8GB TO 24GB VRAM)" (Kai, 2026-10-21): the file matters more than the model name, MoE-in-RAM on 8 GB, ISTA's GSQ-RCO builds

**EXTERNAL SOURCE NOTE — not a lab measurement.** What a third-party YouTube video claims, tagged by how far each claim was checked. It is **sponsored** (ChatLLM by Abacus AI, disclosed at 6:18 and in the description). Speeds in it are, by its own description, "self-reported by the people linked". The lab's own per-box file work is in [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] (Phase 3, "File per box, measured"); related intakes: [[Research-Lukes-Dev-Lab-Strata-Flash-Next-Coder-Video-2026-10-14]], [[Research-Local-Explained-24GB-KV-Cache-Video-2026-10-13]], [[Research-Kai-OpenCode-Console-Breach-Video-2026-10-20]].

Filed 2026-10-21 at the owner's request. Raw files (captions, info JSON, description, cleaned transcript) are in `Claude Home\research\video-ugKAkCVBcz0\`.

**Certainty tags:** **[D]** stated in the video, unchecked · **[V]** checked against a primary source · **[L]** compared with a lab measurement · **[?]** named but not found.

## The video

| | |
|---|---|
| Title | I Tested Every Best Local AI For GPU (8GB TO 24GB VRAM) |
| Channel | Kai (`@kaiexplainsYT`) |
| Uploaded | 2026-10-21 10:30 UTC · 16 min 1 s · 12 chapters · 5,242 views at pull time (about three hours old) |
| Captions | YouTube auto-generated English; model names garbled ("Quinn", "Neotron", "ISA"); the description has the correct names and links |
| Thesis | Tier lists say "biggest model that fits"; the real levers are **which file you download** (who made the quant) and **whether it fits fast memory**, with mixture-of-experts models as a separate rule |

## Claims

| # | Claim | Tag | Note |
|---|---|---|---|
| 1 | Decode speed ceiling = memory bandwidth ÷ bytes read per token: a 3090 (936 GB/s) with a 16.46 GB Q4 27B gives ~57 tok/s; one owner measured ~41 | **[D]** | Standard memory-bound arithmetic; sound as a ceiling |
| 2 | Spilling into DDR4 (~51 GB/s dual channel) is ~18× slower than 3090 VRAM; every token waits on the slowest memory holding any weight | **[D, L]** | Consistent with the lab's measured BigHonker figures: CPU-side RAM "~20-28 GB/s effective" on a 27B Q4_K_M that writes ~2 tok/s |
| 3 | A MoE with ~3B active of 35B (Qwen 3.6 35B A3B, 22 GB file) reads under 2 GB per token, so it can live mostly in system RAM; on an RTX 2060 6 GB + 32 GB DDR4 one user gets 23 tok/s | **[D, L]** | **The lab agrees and has measured more.** BigHonker (RTX 3070, 8 GB) runs the A3B at **22.6–26.4 tok/s writes** with a tuned launch (register Phase 3), ~18 on ollama's stock launch. Same shape, same range |
| 5 | The same model at the same 2-bit level from two uploaders: one fixed 16 of 20 real bugs, the other 7, and the worse one was the **bigger** file (8.98 vs 8.42 GB); what matters is how bits are assigned | **[D]** | Source is an X post (PatchEval, @DogukanUrker, on one RTX 3060): **[?]** not fetched. The direction matches the lab's own "FILE per box" finding (below), but this specific 16/20 vs 7/20 is unreplicated |
| 7 | 16 GB cards: the default Q4_K_M (16.46 GB) does not fit once Windows takes 1–2.3 GB of VRAM, so it **silently spills** and speed collapses with no error | **[D, L]** | Silent spill is a documented lab hazard (the residency rule: `size_vram < size` is a silent CPU offload). Lab runs no 16 GB card; the general mechanism is confirmed |
| 9 | 24 GB: plain Q4_K_M with 128K context at 8-bit cache is ~21 GB, ~41 tok/s on a 3090 | **[D, L]** | Mothership (7900 XTX, 24 GB) ran the 27B at **73.5 tok/s** on real work and 64.3 on Vulkan in lab runs ([[Research-Lukes-Dev-Lab-Strata-Flash-Next-Coder-Video-2026-10-14]] comparison). Different card, engine and speculative decoding; not the same number |
| 11 | Thinking tokens, not tok/s, set wall time: on an 8 GB card Qwen3.8-27B spent ~20,000 thinking tokens on one prompt, ~80 minutes at 4.2 tok/s vs ~8 minutes on a 3090; Bolt used 12,577 reasoning tokens before code | **[D, L]** | The lab has the same lesson: it records thinking length per model (Flash-Next 69 K vs 27B 25 K on one code task) and prices **minutes per task**, not tok/s |
| 13 | LFM2.5-24B-A2B runs 87 tok/s on a 3060 but fixed only 8 of 20 bugs; Liquid pitches it for tool calls | **[D]** | X post, **[?]** not fetched |
| 14 | Final advice by card: 8 GB with 32 GB RAM → Qwen 3.6 35B A3B experts-in-RAM (else Gemma 4 12B Q3 for chat); 12 GB → ISTA 3-bit or 2-bit IQ2_XS; 16 GB → ISTA IQ3_S 11.77 GB; 24 GB → plain Q4_K_M, wait on Bolt | **[D]** | Per-card advice; lab-comparable only for 8 GB and 24 GB |

## Compared with the lab [L]

| Question | Video | Lab | Read |
|---|---|---|---|
| Is "the file, not the model name" real? | The headline claim | **Yes, measured on the lab's own boxes.** The A3B's UD-Q4_K_M is **slower** than the IQ file at the IQ file's tuned launch (writes 22.6–25.0 vs 25.6–26.3; reads 922–957 vs ~1,590); a same-size bartowski Q3_K_M ties the IQ file, so **decode follows bytes**, and "K kernels are faster per byte" was withdrawn | Strong agreement on the principle. The lab's reading is more specific: bits per weight and where the weights sit (pinned host memory) decide speed |
| Does a MoE run well from RAM on an 8 GB card? | 23 tok/s on a 6 GB 2060 | 22.6–26.4 write on BigHonker's 8 GB 3070; ~29–33 in the earlier Phase 3 reading | Agrees |
| Does a dense 27B on 8 GB crawl? | 4.2 tok/s on a 4060 8 GB | 27B on BigHonker writes **~1.7–2.4 tok/s** (MTP2 1.88–2.01); reads ~8–10 tok/s with repack | Same conclusion (unusable for agentic work), different number; box, RAM speed and engine differ |
| 27B GSQ-RCO on 12/16 GB | Best build per tier | **Not in lab data.** The lab has no 12 or 16 GB card, and its GSQ-RCO exposure is the Flash-Next Q2_0 | Nothing to compare; would matter only if a 12–16 GB box joins |
| Quality of a low-bit quant | ISTA's own AIME/GPQA tables, one X post | "Quality unmeasured for non-IQ files"; BACKLOG item 1: **answer quality of Strata's GSQ-RCO Q2_0 is unmeasured** | Neither side has an independent quality number for GSQ-RCO. The video's strongest quality claim rests on ISTA's own table and one repro |
| Bolt 74B-A9B "24GB" | Wait a few weeks | Not tested | Candidate for Mothership's 24 GB only if the lab wants a dense-ish 24 GB challenger; flagged below |

## Implications for the lab

1. **No change to current files or placement.** The video's 8 GB advice is what BigHonker and rigby already do (A3B, IQ-class file, experts in host RAM). Nothing here contradicts the register.
2. **One genuinely new candidate:** NVIDIA's Nemotron 3.5 Bolt 74B-A9B (24 GB build). It would be a Mothership challenger on the menu; it is a **new family, so it needs the full menu treatment** (and the video's own advice is to wait for real-world reports). Not queued — the owner's call.
3. **The 27B GSQ-RCO build is the same lab (ISTA) the Flash-Next Q2_0 comes from.** If the lab ever wants a smaller-file 27B for a 12–16 GB card it does not have, this is where to start. Low priority today.
4. **Confirms an existing rule to keep:** price **minutes per task**, count thinking tokens (the 20,000-token / 80-minute example), and prove residency before quoting a speed.

## About the source

Third Kai video filed. Uploaded at 10:30 UTC again (like the 08-23 video; the 10-20 breach video was 15:30 UTC). Source quality: the sourcing is unusually open (a link per claim, speeds marked self-reported, "take the exact numbers with some skepticism" said on air), and this video's central claim is independently supported by the lab's own measurements. The sponsor segment is a plain product pitch and does not touch the findings.
