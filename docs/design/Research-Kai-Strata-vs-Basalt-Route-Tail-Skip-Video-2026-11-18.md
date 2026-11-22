# Research note — "Basalt Runs Qwen 3.8 Flash Next At 224 Token/sec" (Kai, 2026-11-18): Strata's route tail skip, Basalt, and whether Mothership is affected

**EXTERNAL SOURCE NOTE, not a lab measurement**, except where tagged [L]. A third-party video's claims, tagged by how far each was checked. **Lab verdict: Mothership's Flash-Next is NOT affected by the route tail skip.** It runs the full model, because Strata 0.1.45 turns the skip on by default only on CUDA builds and on HIP gfx1200/gfx1201 (RDNA4), and Mothership's 7900 XTX is gfx1100 with no override in its config (evidence below). Basalt cannot run on any lab card. The model and its setup on the box: [[Qwen3.8-Flash-Next-on-Mothership]]; the window and hot-start machinery: [[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]].

Filed 2026-11-19 at the owner's request. Raw files: `Claude Home\research\video-wC6jsOU4jII\`. Same channel as [[Research-Kai-Lithos-Metal-DSpark-External-Drafter-Video-2026-10-16]] (channel ID `UCgUfJoPtkt7-RiiuYh4vmGA`, compared in the two `info.json` files).

**Tags:** **[D]** stated in the video · **[V]** checked against a primary source · **[L]** checked on the lab's own box or files · **[A]** arithmetic redone here · **[?]** named but not opened.

## The video

| | |
|---|---|
| Uploaded | 2026-11-18 · 26 min 13 s · 18 chapters · ~101k views at pull time |
| Visuals | motion graphics that restate the narration; no numbers on screen beyond the transcript (checked on 15-second contact sheets) |
| Captions | auto-generated English only (names garbled, see Name repairs) |
| Thesis | the viral "Basalt is much faster than Strata" numbers compare two different behaviours: since 0.1.42 Strata skips some of the model's experts by default on many cards (the **route tail skip**). With both engines on the full model, Basalt on an RTX 50 card saves about **25 % of a real job's time**, not 1.6×. Everyone on Strata should set `STRATA_ROUTE_TAIL_SKIP=0`. |

## What the route tail skip is

Qwen 3.8 Flash-Next is a 125B mixture-of-experts model: 48 layers × 512 experts, 10 routed per token, 24,576 experts in all [D][A]. On a card that cannot hold every expert, Strata either copies a missed expert over PCIe or computes it on the CPU. With the skip on (`STRATA_ROUTE_TAIL_SKIP=7`), a missed expert that every token of a draft-verify window routes at rank 7 or lower is **neither copied nor computed**, so that token is made from fewer than 10 experts [D][V: `docs/QUANTS.md`, `src/program/generate.cpp`].

## The claims and what the primary sources say

| Claim [D] | Check |
|---|---|
| On by default for NVIDIA from engine 0.1.42 (Oct 10) | **[V]** v0.1.42 release notes: "on by default on CUDA"; off "with `--batch` slots, with a peer or helper GPU, and on AMD and Intel" |
| Radeon RX 9000 / R9700 got it the next day | **[V]** v0.1.43 (Oct 11): default on RDNA4 (gfx1200/gfx1201), measured on the R9700; "RDNA3 (gfx11) and older keep their 0.1.42 behaviour: we have no such card to measure on" |
| Off when every expert fits, when the card holds ≥ 90 %, and for some file types | **[V]** engine source: no default when every expert is in VRAM; `STRATA_TAIL_SKIP_MAX_CACHED` default 90 % (60 % on a HIP layer split); off by default for NVFP4 experts |
| RTX 5080 owner (issue #309): Strata 80 → 66 tok/s with the skip off; Basalt 93 full model, 120 with its own skip on; "Strata was cheating a bit there" | **[V]** issue #309: Ryzen 9 5950X, RTX 5080 16 GB, 128 GB DDR4-3200, Windows 11, Basalt 0.3.2; Strata 80 on / 66 off; Basalt prose 92–93 off / 120 on; developer: "Yup, Strata was cheating a bit there haha" (closed 2026-11-10). ⚠ **Not one variable:** Strata ran **IQ3_S**, Basalt ran **IQ3_XXS**, so the video's "1.41× on the full model" also includes a file difference |
| UD-Q4_K_XL KL 0.056 → 0.086 with the skip, 0.115 in RAM-budget mode; UD-IQ4_XS 0.101 with nothing skipped | **[V]** `docs/QUANTS.md`: 0.056 → 0.086 (every expert in RAM, R9700); the 0.115 is a different machine whose own baseline is 0.053 (A4000, 1,500 GPU slots); UD-IQ4_XS 0.101 with no skip |
| On 2- and 3-bit files the skip is close to free (top-1 match 97.5–98.2 %, an 82-problem coding test could not tell) | **[?]** not opened |
| Basalt needs an RTX 50 card as the main GPU, CUDA 13, an R580+ driver; an RTX 40 only as a second card | **[?]** README not opened |
| Real job time: 7.2 → 5.4 min on a 4-turn coding task (both 36/36), about 25 % less waiting | **[?]** AkitaOnRails write-up not opened |
| Basalt control: skip on a capped 5090, 192 → 299 tok/s, chained reasoning −5.2 points | **[?]** |
| Windows regression (124 → 17.7 tok/s on a 5070 Ti), laptops on battery, parallel agents, NVFP4 default slow on 16 GB | **[?]** |
| Strata 0.1.58 raised load RAM 68 → ~115 GB; 0.1.57 dropped an RX 9070 XT 50 → 14 tok/s | **[?]** |

## Mothership: why it is not affected [L]

1. **The rule, in the engine Mothership runs.** In `F:\Strata045\Strata-0.1.45\src\program\generate.cpp`, the default path requires `decode_defaults_arch_ok()`, which on a HIP build returns true only for gfx12 (`gfx_arch_is_gfx12_wmma`) and on CUDA always. Comment above it: the HIP defaults "apply on gfx1200 / gfx1201 only; every other AMD part (gfx11, gfx906, ...) keeps its earlier behaviour".
2. **The card and the build.** A Flash-Next window's `setup.log` (2026-11-18): `AMD Radeon RX 7900 XTX, 24.0 GB VRAM, gfx1100`, "ready-made AMD engine", `engine\strata.exe`, hipBLASLt table `gfx1100-…`. So it is the HIP build on gfx11.
3. **No override.** No `STRATA_ROUTE_TAIL_SKIP` in Strata's config or the lab's launchers (`strata_server_lab.py`, `strata_arm.py`, `helper_window.py`). A read of the Strata 0.1.45 tree (4,460 text files) found the name only in Strata's own source, docs and tests.
4. **Why it would otherwise apply.** The card caches 10,824 of 24,576 experts (44 %) [A], under the 90 % line, with a decode hit rate of about 72–86 % (`server.log`). Without (1)–(3), the skip would be on.
5. **What we did not see directly.** Strata's console wrapper does not pass the engine's own `strata serve:` lines into `server.log`, so the "STRATA_ROUTE_TAIL_SKIP=7 is ON" line the engine prints can be neither seen nor ruled out there. The direct live instrument is the engine's `tail_skipped` counter (`include\strata\core\expert_source.hpp`); whether `/metrics` exposes it is **[?]**.

**Lab speeds for scale [L]:** that window decoded 66–96 tok/s per answer on Strata's Q2_0 pack. Strata lists Q2_0 at mean KL 0.315 and 80.6 % top-1 agreement with Q8_0 (`docs/QUANTS.md`) [V], so on this file the quantization costs far more than the skip would.

## Against the owner's question: are the NVIDIA owners just set up badly?

Mostly not, and it is not one variable. For a model this size, speed follows how many experts fit in VRAM and how fast system RAM feeds the rest, not the GPU's compute.
- **The RTX 5080 owner** had a 16 GB card holding about 18 % of a 3-bit file's experts, on DDR4-3200. Mothership's 24 GB card holds 44 % of a smaller 2-bit file. Mothership's 75–90 against their 66 (full model) reflects the card, file and RAM, not a setup error.
- **The RTX 5090 numbers** (Akita: Strata 223–259 and Basalt 377–400 tok/s on a 2-bit file, at 131K context) are about 3× Mothership even allowing 10–20 % for the skip. They are real, and they follow from 32 GB of VRAM and a newer platform.
- **The comparison with LTT holds for method, not raw speed:** both are cases of an unchecked default (LTT's medium reasoning effort; here a default skip) deciding a published result. The lab's LTT comparison was speed per dollar, with Linus's speeds **estimated** from bandwidth, never measured (`Claude Home\ltt-pitch\data-check.md`).

## Transferable lessons

1. **A tok/s number needs its engine settings.** It is the same lesson as the 2026-10-07 effort-level audit: read what the engine actually ran, not the name of the setting.
2. **One variable.** Even the careful issue #309 compared IQ3_S against IQ3_XXS. The video's "1.41×" still carries that file difference.
3. **Time the whole job, not decode.** The video's 25 % (job) against 1.6× (tok/s) is the same gap the lab's A1 harness measures in wall time.
4. **A vendor default is per architecture and per version.** "Not affected" was true here only because RDNA3 was left out for lack of a test card. That can change in any later release.
5. **Pin the engine.** Mothership's Strata is pinned by its install folder (`F:\Strata045\Strata-0.1.45`).

## Open point

**Before any Strata upgrade on Mothership, by hand or by the lab's updater:** read that version's `decode_defaults_arch_ok()` and route-tail-skip block. If gfx11 has been added to the default, either set `STRATA_ROUTE_TAIL_SKIP=0` in the config's `"env"` or decide to accept the skip, but never take it unknowingly. Also read the release notes for the RAM and speed regressions the video names (0.1.57, 0.1.58) **[?]**.

## Not known

The `/metrics` counter for skipped experts; Basalt's README and benchmark table (not opened); the AkitaOnRails write-up; whether the Adamant fork (RTX 30/40) has been validated since (its README said not, per the video).

## Name repairs (caption → likely)

"root tail skip" = route tail skip · "NV block" = the config's `"env"` block · "Black Wireless" = Blackwell · "Radiance" = Radeon · "Ryzen 95950X" = Ryzen 9 5950X · "DDR 43200" = DDR4-3200 · "UDI Q4" = UD-IQ4_XS · "Akira" = Akita (AkitaOnRails) · "59D" = probably RTX 5090 · "25070 Ti" = two RTX 5070 Ti · "TAD release" = tagged release. The video says Basalt was built by "Fabio Akita"; the repository is `jesdga95/basalt` and Akita's own site published the job-time test **[?]**: who maintains Basalt was not checked.

## Files

`Claude Home\research\video-wC6jsOU4jII\`: `wC6jsOU4jII.mkv` (the whole video, 1080p AV1 + Opus, 26:13, 57.7 MB, decoded end to end with no errors) · `wC6jsOU4jII.en.srt` · `transcript.txt` (one block per minute) · `wC6jsOU4jII.description` (the full source list) · `wC6jsOU4jII.info.json` · `frames\sheet_*.jpg` (one frame every 15 s). Pulled with yt-dlp inside the Pinchflat container on Sundae; the container's YouTube cookies have expired, and the public video downloaded without them.
