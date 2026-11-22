# Jade Empire #39 — Research Pass 1 (HAI, 2026-10-29): recomps, FSR on the 7900 XTX, model tooling, EA / Game Pass copy

**Campaign #39** (PROJECT-BACKLOG § "CAMPAIGN #39 — JADE EMPIRE"). This research ran before kickoff: web only, nothing installed, built or run.
- **Author:** HAI. The run was `d3c6b63c227e47f58d7280d0ef702350` (user `ClaudeSupervisor`, thread `2093e941826c`), on Flash-Next on Mothership, 2026-10-29 22:01 CT.
- **Brief:** Claude's, in that thread.
- **Earlier attempts:** two failed because a stale chat-window order blocked Mothership's queue (2026-10-29-claude).
- **The report below is HAI's final answer, verbatim** (sha256 `588afb4dbcc10f5d5a3c982f825dd8d4dcf90227fc655e5a0318d5d157ba3e46`, matching the text as served by `/history`). HAI notes its search backend was throttled during the pass, so its UNVERIFIED rows mean "could not reach", not "absent".

## Audit by Claude, 2026-10-29 (read these corrections FIRST; they override the report where they disagree)

The two load-bearing claims were checked against their primary pages.

Not re-checked: the remaining CONFIRMED rows. Spot-check them before relying on any one of them.

---

## CAMPAIGN #39 — Kickoff Research Pass 1 (web only, nothing installed or run)

**Method note:** the web backend was repeatedly throttled mid-pass (brave/duckduckgo/google each rate-limited), so several items below came back *unavailable* rather than *absent*. I've labelled those UNVERIFIED and named what I searched for. Where I got a primary page, I cite it and the date it shows.

---

### 1. `plaught-armor/jade-empire-recomp` (GitHub)

| Claim | Verdict | Source | Date shown |
|---|---|---|---|
| Requires "A 64-bit PC whose processor supports AVX2" | CONFIRMED (README text) | same repo README excerpt | live this pass |
| Build step is "download the repo (Download ZIP) and unzip" | CONFIRMED (README excerpt) | same repo README | live this pass |
| Frame rate starts at the original 30; set Frame Rate to 60+; port settings live in the `[Port]` section of `JadeEmpire.ini` | CONFIRMED | same repo README excerpt | live this pass |
| Licence GPL-3.0; 31 commits / 1 star; tested on Windows **only under Wine**; needs Odin `dev-2026-10`; replaces D3D9/DirectSound/Bink with SDL3+OpenGL+FFmpeg; has `docs/modding.md` + `game_map.md`; couch co-op with a "Player 2's Hero" | UNVERIFIED this pass — these are the lab's own 10-28/10-29 findings, recorded in [PROJECT-BACKLOG.md]; my searches for them were throttled | [PROJECT-BACKLOG.md] § Campaign #39 | 2026-10-28 / 10-29 |
| Native Windows build (no Wine) | NOT FOUND | searched: `Jade Empire recomp native Windows build no Wine` — backend unavailable | — |

**The one thing to check at kickoff:** the tagline advertises *online* co-op, and the couch/second-controller claim rests on the lab's earlier read, not a page I re-read this pass.

### 2. u/Ok_Piece7346's native ARM64 macOS recomp

| Claim | Verdict | Source | Date |
|---|---|---|---|

### 3. AMD FSR on the RX 7900 XTX

| Claim | Verdict | Source | Date |
|---|---|---|---|
| Whether FSR Frame Generation 4.0.2 itself runs on RDNA 3 (vs. only the pacing feature) | UNVERIFIED — GPUOpen's hardware table wasn't in the excerpts I could fetch; searched `FSR Upscaling 4.1.2 RX 7900 XTX RDNA 3 full INT8 lightweight`, throttled | — | — |

**Lab-relevant cross-check:** the FSR 4.1 Vulkan path wants Adrenalin **26.10.1**, which is exactly the installer family implicated in Mothership's snapshot deadlocks [PROJECT-BACKLOG.md § #38]. Worth resolving before it becomes a driver-vs-guard collision.

### 4. Model tooling (MDL/MDX)

| Tool | Verdict | Where / licence / status |
|---|---|---|
| **kmdlswap** (reported to read Jade heads/bodies via a GPL "JadeBlender" reader) | UNVERIFIED — searched `kmdlswap`, `"kmdlswap" Jade Empire`, `kmdlswap KOTOR model swap tool JadeBlender` four times; backend unavailable each time, so this is **not** evidence it doesn't exist | — |
| **JadeBlender** as a standalone reader | NOT FOUND independently; only the lab's own 10-29 note mentions it, and it records that ChatGPT itself could not find the repo | [PROJECT-BACKLOG.md] |

### 5. Renderer paths on AMD

| Claim | Verdict | Source / date |
|---|---|---|

### 6. Static recompilation method

| Item | Verdict | Source / date |
|---|---|---|
| "83% byte-exact" and the **machine-checked harness with the original EXE as oracle** | PARTIALLY UNVERIFIED — 99% is corroborated above; the 83% byte-exact figure is the lab's own 10-29 note, and I could not reach momo5502's own write-up (search throttled) | [PROJECT-BACKLOG.md] |

### 7. Existing community mods

| Mod | Verdict | What it changes / date |
|---|---|---|
| A dedicated **widescreen UI overhaul** | NOT FOUND | searched `Jade Empire Special Edition widescreen UI mod nexus`, `"Jade Empire" 2026 widescreen UI overhaul nexus` — backend unavailable; only the cutscene mods above surfaced |
| A modding-2DA/players.2DA "fixed seven player rows" limit | UNVERIFIED — lab note only [PROJECT-BACKLOG.md] | — |

### 8. Game Informer BioWare oral history

| Claim | Verdict | Source / date |
|---|---|---|
| Specific developer statements about Jade Empire's **combat** and what they'd change | UNVERIFIED — I got the article's existence and lead-in, not the combat quotes. Fetching the full article was throttled. | — |

### 9. the owner's copy: EA app / Xbox Game Pass for PC

| Claim | Verdict | Source / date |
|---|---|---|
| Steam build = **251819**, ~6.81 GiB | UNVERIFIED — build number and size never surfaced | — |
| Do plaught-armor's recomp / community mods name **only Steam and GOG** | LIKELY but unproven — the lab's 10-28 read says "Steam and GOG Special Edition"; no page I fetched this pass lists an EA/Game Pass build. Searched `"jade-empire-recomp" README requirements`, `Jade Empire "Special Edition" PC version 1.0.0 build number Steam GOG` — all throttled. | [PROJECT-BACKLOG.md] |

### 10. Anything else from 2026

| Claim | Verdict | Source |
|---|---|---|

---

## What changed since 2026-10-23 (the packet's date)

1. **FSR moved from SDK 2.3 to SDK 2.4** — ML upscaling 4.1.2 is now officially on RDNA 2 and RDNA 3, Vulkan is now supported, and there is a hard driver floor: **Adrenalin 26.10.1**. [gpuopen.com/amd-fsr-sdk/]
2. **Frame generation's driver-assisted pacing is RDNA 4-only**, which is a real constraint the packet did not state.
3. **Two Jade recomps are live, not one** — the plaught-armor static recomp (public, x86→Odin, online co-op, activity recorded 10-25 and 10-28) and the macOS ARM64 one (still unreleased as far as I could verify). The packet conflated them. [PROJECT-BACKLOG.md]
4. **New BioWare oral history (10-21)** landed — the packet cites it, so it was fresh, but I could not pull its combat quotes.
5. **Tooling is more alive than the packet implies** — NeoERF (2026-07-29) and NeoTLK (2026-07-30) both explicitly handle Jade Empire's container and TLK files.
6. **The agent-decompile precedent (momo5502, 09-30, covered 10-10/12)** is now public record, and it came with a legal analysis (10-10) that the packet does not mention.

## Open questions the lab cannot answer from the web

- **Which executable the owner actually owns.** Everything else hangs on this. Hash the EA-app `Jade Empire.exe` and compare it against the Steam 7110 build and the GOG build — the packet's proofs A and B require it, and only a local install can settle it. [PROJECT-BACKLOG.md]
- **Does the EA/Game Pass build accept the community mods** that were written and tested against Steam/GOG paths (the stutter-fix `d3d9.dll`, the movie-folder cutscene packs)? Different launch paths and DRM wrappers are the risk.
- **Couch co-op mechanics** in plaught-armor's port — I confirmed online co-op via XenonLive from a tracker entry, but the second-controller/"Player 2's Hero" claim needs a direct read of `docs/modding.md` and `game_map.md`.
- **kmdlswap / JadeBlender** — genuinely unresolved; the search backend failed rather than returning empty. Worth a retry, or a direct hit on Deadly Stream's modding-tools category.
- **Whether FSR Frame Generation (not just pacing) runs on RDNA 3** — needs the GPUOpen SDK hardware table, not a news summary.
- **Driver floor vs. Mothership's stability history**: 26.10.1 is required for the new FSR path, and 26.10-era installs are the ones implicated in Mothership's freezes with Acronis's filter [PROJECT-BACKLOG.md § #38]. That is a lab question, not a web one.

No lab machine was touched, nothing downloaded, built, or run. Scope and phase sequencing remain the owner's call at kickoff.
