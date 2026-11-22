# Jade Empire #39 — HAI-led night 2026-10-31/11-01: resource types named, black-screen patches, FSR 4 routes, and how the lab behaved

Part of [[39-CHARTER]] Phase 1. the owner, 2026-10-31 ~23:00: *"I'd like for this to be as HAI led as possible for the night,
I feel like it's a good time for us to log behavior and watch how the lab system works to see if it's working as I
intended."* HAI did the work (thread `f29699b9ffe2`, user ClaudeSupervisor, Background `all_night`). Claude only
watched, through a read-only night watch (notes log by offset, deck changes, saved-run state, every 5 min), except
where a lab bug stopped HAI's work; those were fixed, gated and deployed as below.

## How the lab behaved (the log, summarised)

**What worked as designed, every run:**
- McCoy triaged each job to the lead; Spock placed it on Mothership.
- The lead model resolved to `qwen3.8-flash-next-strata-q2_0` (job-shaped, think `xhigh`), served as a CHAT WINDOW
  factory order. The deck showed Mothership `state=compute` with the window's order name.
- The LIVE claim was released at the end, and the window ended and its folder was retired.
- The other factories sat free or idle and reporting live all night.
- Compaction fired near 91–94% of the 262,144-token window and the run kept going.
- No GPU fault and no stuck order.

**Lab bugs HAI's work ran into, each found from the record, fixed the same night with a test that fails without
the fix, deployed through the full gate:**

| # | What HAI hit | Cause | Fix |
|---|---|---|---|
| 1 | Research pass 3's first run ended mid-sentence at `length` | llama-server windows reported only the newly read tokens (`prompt_n`) as the prompt size, so compaction never fired | `71a8b8c`: prompt = `prompt_n + cache_n` |
| 2 | A re-run answered from search snippets | the tool selector could offer `web_search` without `fetch_url` | `bf21fe2`: `fetch_url` is CORE |
| 3 | Night run 1 (`684c7631…`) redid the previous job | compaction kept the thread's FIRST user message as the brief (an old request in a reused thread); hits any long HAI chat | `029f0fb`: keep the current request |
| 4 | The deploy gate went red at 01:48 CDT | `energy.window_covered` compared times by wall clock; on the fall-back night every window across the repeated hour read "empty" (a production bug that fails closed) | `029f0fb`: walk real hours, pinned at both clock-change dates |
| 5 | HAI reported "I hit the call limit" | the loop's handoff told it "you've used up your tool-call budget" whatever the real reason (here: no new work) | handoff names the real reason |

**Gaps that need the owner, not fixed:**
- HAI cannot create a coding workspace; workspaces are pre-registered (`sample`, `orchestrator-pilot`,
  `orchestrator-real`, `tablet-intake-canonical`). So its patches came back as text in the report, not as files.
- `queue_compute` runs only its 6 whitelisted payloads, so HAI cannot order Jade factory work.
- `web_search` was throttled most of the night (Google CSE "too many requests"); HAI worked around it with direct
  `fetch_url` reads.

## Claude's audit of night run 2 (`d84b7318…`, 36 turns, 58 page reads, one compaction that kept the right brief)

- **E — HOLDS, every row checked against xoreos `src/aurora/types.h` (master):**

  | Type | Name | types.h line |
  |---|---|---|
  | 3028 | BIP, binary lipsync | :197 |
  | 3011 | MAB, binary material | :187 |
  | 2047 | GUI, GFF | :113 |
  | 2057 | SAV | :123 |
  | 2012 | ARE, GFF | :81 |
  | 10 | TXT | :67 |

  So the census's largest unknown (39,407 entries, 314 MB in the `.mod` files) is **lipsync**. The 39
  `override/*.sav` "SAV V3.2" files are GFF V3.2 saves (tag `SAV `, GFF version 3.2), not ERF archives. The census
  tool was right to call them unparsed.
- **F — partly holds:**
  - **Patch 1** (name only the live draw buffers in `fb_bind`) applies to the real lines (`d3d9_frame.odin:149–168`),
    and HAI's caveat is right: `clear_rect` clears by slot index, so the patch is safe only while the live targets
    stay contiguous from slot 0.
  - **Patch 2 is broken as written.** It points the shadow FBO's draw buffer at `COLOR_ATTACHMENT0` without
    attaching a texture, which fails the completeness check (fatal). It also targets shadow maps, and launch 4 ran
    with `JE_SHADOW=0` and was still black, so it tests a path already ruled out.
  - **Patch 3 is a sketch, not code.** `gl.CreateProgramObject` is not a GL call, as HAI says itself.

  Next real test: patch 1. If still black, a plain-program copy written properly, then the draw path HAI did not
  reach (`d3d9_draw.odin`).
- **G — the headline claim is WRONG and corrected here.** HAI said FSR 4 ML upscaling needs an RX 9000 card and the
  7900 XTX "falls back to 3.1.5". The current FSR SDK page (gpuopen.com/manuals/fsr_sdk/techniques/super-resolution-ml/,
  FSR Upscaling 4.1.2 in SDK 2.4.0, dated 2026-10-28) says FSR 4 *"requires an AMD Radeon RX 9000, RX 7000, RX 6000
  Series discrete GPU"*, and lists the **RX 7900 XTX running the Full INT8 model at 4K**. HAI read an older FSR 4
  page. So **FSR 4 runs on Mothership's card**; pass 1's audit already had this right. The route table holds
  otherwise:
  - route A (GL recomp + Vulkan FSR pass via `GL_EXT_memory_object`/`semaphore`, precedent AMD-Gfx-Drivers #76) is
    the only route that gives the lab the motion vectors and jitter FSR needs;
  - its hand-off point is the pre-UI post chain;
  - the work is motion vectors, jitter and a sub-native render scale.
- **H — HAI's own note:** fair, except "hit the call limit", which was the lab's own handoff wording (bug 5).

## HAI's report

Verbatim (sha256 prefix 84bd974512feb3ec) in `Archive/hai-reports-39/night-run-2-d84b7318-2026-11-01.md`, kept out of the
indexed library because its section G repeats the outdated "FSR 4 needs RX 9000" claim corrected above.
