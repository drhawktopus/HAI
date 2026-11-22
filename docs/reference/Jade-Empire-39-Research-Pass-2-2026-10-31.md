# Jade Empire #39 — Research Pass 2 (2026-10-31): the recompile's renderer read from source, FSR 4 routes, model tools

**Campaign #39** ([[39-CHARTER]]). Phase 1 evidence for the lab's adopt-or-patch call.
- **Part 1 (read FIRST): Claude's reading of the recompile's own source and docs** at commit
  `4d8459e92755607d436ca5ca274a5b1384708259`. This is the same tree the build order compiled on Mothership
  (`F:\jade39\recomp\<commit>`). Claude read a separate download of it on Softserve. Nothing was run.
- **Part 2: HAI's report, verbatim.** Run `97e8eefa6d7640d8a69d38660543d19e` (user `ClaudeSupervisor`, thread
  `2093e941826c`) ran 18:30–19:02 UTC on Mothership: 176 tool steps, 172 of them `web_search`. Its sha256 is
  `c179ef15857ff4da4c25113aa2dcf0fc5589de9101e1f95af691033766fec03c`.
  An earlier attempt (`d6feeb36…`) was cut at 5 tool steps and discarded; the cause was fixed the same day
  ([[Drive-Capability-Design-and-Status]], commit `90efe2f`).

## Part 1 — what the recompile's source says (Claude, 2026-10-31)

**Why HAI's section A is empty: on this pass HAI could not open public web pages.** Its `fetch_url` reached only
allow-listed LAN `host:port` targets, so the web was reachable only as `web_search` snippets, and Claude's brief told
it to use `fetch_url` on GitHub. Searches were also heavily throttled. **Fixed the same day (commit `8ec9164`):**
`fetch_url` now reads public pages read-only, refusing every LAN, tailnet and loopback address. The LAN-only limit had
been a gap, not a decision; the owner: *"read only is chill"*. A later research pass can read the repo itself.

**Size and layout.** 38,357 lines of hand-written Odin. The game's own code is not in the repo: `tools/x86rec`
translates the user's exe at build time.
- `port/src/win32/` (64 files): the replaced Windows, D3D9, DirectSound, input and net layers.
- `port/src/native/` (46 files, plus `coop/`): game-side additions such as co-op heroes, AI, settings and the
  widescreen GUI.
- `re/`: 7,504 Ghidra function names, 3,027 named functions, NWScript function and handler tables.
- `docs/`: `rendering.md` (702 lines), `recompiler.md`, `hle.md`, `modding.md`, `game_map.md`,
  `exe_inventory.md`, `unpack.md`.

**Renderer** (`docs/rendering.md`, `port/src/win32/d3d9_*.odin`):
- **API:** OpenGL **4.6 core** (`d3d9_device.odin`: `CONTEXT_MAJOR 4, MINOR 6, PROFILE_CORE`), GLSL `#version 460
  core`, `ClipControl(UPPER_LEFT)`, SDL3 window. No Vulkan anywhere in the tree.
- **The game's D3D9 calls are reimplemented on GL.** The game still sees the D3D9 sizes it asked for; every
  enhancement lives on the GL side.
- **A post-process point BEFORE the UI already exists.** SMAA 1x (§9), FidelityFX CAS sharpening (§14) and ambient
  occlusion (§15) run as one chain at the first `StretchRect` out of the back buffer, the glow capture at guest
  `005ce7b8`, "which comes before the UI". Subtitle text is byte-identical with the chain on and off. An upscaler
  would sit at this point.
- **Scene depth is readable.** The auto depth surface is multisampled and resolved once per combat frame (the
  `StretchRect` at `005e58c0`). AO and soft particles already read it and linearise it with the projection's
  `_33`/`_43`.
- **The projection matrix is captured.** `Dev_SetTransform` stores every transform, and the projection goes to
  `ssao_projection`. Game vertex shaders take projection × world-view in constants (e.g. `vs098` vc0..3).
- **No motion vectors and no projection jitter exist today.** For static geometry they can be derived from depth plus
  the previous and current view-projection (the standard TAA reprojection). Skinned characters need their previous
  bone or world matrices per draw. That is the real engineering for FSR on this route, and it is not yet sized.
- **Render scale is integer SUPERSAMPLING, not sub-native rendering** (§1). `JE_SCALE=N` (≤ 8; auto picks the
  smallest integer that covers the display height, ≤ 4) renders every target at N× and Present downsamples. FSR
  needs the opposite, a sub-native internal size, so fractional scales below 1 are new work.
- **Widescreen and ultrawide are already handled** (§13). The 3D view is hor+ from `ScrW`/`ScrH`. The GUI, laid out
  on a 640×480 virtual screen, is pillarboxed to a centred 4:3 instead of stretched (`native/widegui.odin`, whenever
  W·3 > H·4). This is the stretch the owner sees on the stock game.
- **Frame rate:** the engine runs on elapsed time. 30 and 60 fps were measured to give the same walk distance and
  combat timing, and 144 fps holds after the build fixes (§5).

**Built-in automation: a control channel** (`port/src/win32/control.odin`, client `tools/jectl`, `docs/modding.md` §3).
Commands run between frames on the game thread: keys and pad, `exec RESREF` (run a compiled script as the player),
`peek`/`poke` memory, `objs`, `area`, `usepoint`, `anims`, **`shot PATH` (screenshot)**, and `set KEY VALUE` (any
`[Port]` setting). `tools/jeplay` (2,153 lines) drives scripted play. This is the packet's §32 "machine-checked
harness" largely already built: the lab can drive, screenshot and check the game without the owner at the controls.

**What this means for the gate (evidence, not the choice).**
- **Extending the recompile** gives source control of a GL 4.6 renderer that already has a pre-UI pass, readable
  depth and the projection. Two pieces of work stand between it and FSR 4:
  - **motion vectors**, with jitter and sub-native scale;
  - **a way to run FSR 4 from GL.** FSR 4 is DX12/Vulkan only, so either a Vulkan pass shares the GL frames through
    `GL_EXT_memory_object`/`GL_EXT_semaphore`, or the renderer moves to Vulkan. Whether AMD's Windows GL driver
    exposes those extensions on the 7900 XTX is UNVERIFIED. It is one query in a running GL context, so a launch can
    answer it.
- **The original exe** (DXVK + OptiScaler) is HAI's route C. The game calls no upscaler for OptiScaler to replace, and
  DXVK does not create motion vectors. On this route those are the hard parts, and we would not own the source to add
  them.

**Open, needs a launch:**
- Does it run on the 7900 XTX? The log prints `d3d9: GL <version> on <renderer>`.
- Does AMD's GL driver offer the memory-object and semaphore interop extensions?
- How much of the game plays through?

---

## Part 2 — HAI's report (verbatim)

One correction before reading: its route-A row dates the build "2026-10-13"; it was **2026-10-31**.

## CAMPAIGN #39 — Phase 1, Research Pass 2 (report)

**Method and its limits, stated up front.** The web backend throttled hard through most of this pass (brave "too many requests", duckduckgo CAPTCHA, google CSE suspended, wikipedia HTTP errors) — roughly 35 of ~45 queries returned nothing usable. That is *unavailable*, not *absent*, and I've labelled every such row UNVERIFIED. A handful of queries did land, and those are cited with dates. Also: this toolset gives me `web_search` but **no page-fetch tool**, so I could not open raw.githubusercontent.com or any URL directly — I can only see search snippets. That is the single reason Section A is empty: the repo's source at that commit was never readable from here.

**Pass 1 items I am NOT repeating** (Claude's audit, 2026-10-29, settled them): FSR SDK 2.4's hardware table (ML Upscaling 4.1.2 on RX 9000/7000/6000; Frame Generation 4.0.2 + Swapchain 4.0.0 need RX 9000, everything else including the 7900 XTX gets Frame Generation (Swapchain) 3.1.7); SM 6.6 requires DX12 Agility SDK 1.4.9+, Vulkan uses SPIR-V 1.5; Vulkan support is new and at parity with DX12; there is **no driver floor** for upscaling or Vulkan — only Driver-Assisted Frame Pacing needs Adrenalin 26.10.1 and an RX 9000; plaught-armor's README supports Steam and GOG only ("Origin/EA app and retail disc versions have not been tried"); couch co-op and UDP-47000 online co-op are as described; DXVK has Jade Empire SE as a tested title (#3182, DXVK 2.2's YUY2/UYUY fix).

---

## (a) Section A — the recompile's renderer

| Item | Finding |
|---|---|
| **A1. Where the renderer lives, how D3D9 calls are translated, GL version/profile** | **UNVERIFIED.** I could not reach the repo tree, any `.odin` file, or the README at commit `4d8459e92755607d436ca5ca274a5b1384708259`. Searched, all throttled: `plaught-armor jade-empire-recomp renderer odin opengl source`, `"jade-empire-recomp" opengl`, `jade-empire-recomp docs modding`, `raw.githubusercontent.com plaught-armor jade-empire-recomp README.md`, `"Jade Empire" Odin port "OpenGL 4" renderer SDL3`. What *is* established (from the charter and the completed build, [39-CHARTER.md]): the port replaces the Windows/D3D9/DirectSound/Bink layer with **SDL3 + OpenGL + FFmpeg**, in **Odin**, GPL-3.0. The build's own output corroborates SDL3: it dropped `jade.exe`, `play.bat`, **`SDL3.dll`**, five FFmpeg DLLs, two licence files and `JadeEmpire.unpacked.exe` into the game folder and changed nothing. Nothing tells us the GL version, whether it's core or compatibility profile, or which files hold the renderer. |
| **A2. Depth buffer, motion vectors, jitter, a pre-UI point** | **UNVERIFIED — and this is the load-bearing gap for the adopt decision.** The charter's gate assumes it: *"the renderer becomes source, so FSR depth and motion vectors become ours to emit."* That premise is currently an assumption, not an observation. No file or function name can be given. Note what is *not* in evidence either: the declared settings list (frame rate, render scale, anti-aliasing, sharpening, ambient occlusion, draw distance, shadows, fullscreen) contains **no** depth or motion-vector entry, so the port does not advertise them. |
| **A3. What render scale / AA / sharpening actually do** | **UNVERIFIED.** Only the README-level claim exists ("stored in the `[Port]` section of `JadeEmpire.ini`"). Whether render scale is a framebuffer-size reduction, whether AA is MSAA or a post-process, and what sharpening is applied to — all require reading the code. |
| **A4. `docs/modding.md` and `game_map.md` in full** | **UNVERIFIED.** I never got either file. Their existence is a lab note from 2026-10-28, not a re-confirmed read. Searched for both by name; throttled. |
| **A5. Codebase size and the translated-vs-hand-written split** | **UNVERIFIED.** Only metadata is known: 31 commits, 10-24 → 10-25, 1 star, no releases, no issues, co-authored with Claude [39-CHARTER.md]. A 76 s Odin build of the whole tree suggests a modest codebase, but that is an inference from build time, not a line count. |

**The cheapest experiment that settles A** (and it needs no web): the source is already on Mothership at `F:\jade39\recomp\<commit>` from order `jade39-p1-recomp-build.20261031` [39-CHARTER.md]. One read-only order can answer A1–A5 deterministically: list the tree, count lines by extension, and grep for the specific things FSR needs — `glRenderbuffer`/depth attachment, `glClearDepth`, `glTexImage` formats, `glGetShaderSource`/`#version`, MSAA vs a post-process pass, and where the HUD/UI draw call sits in the frame loop. That is a far better use of a factory slot than more web searching, and it converts the adopt decision from inference to evidence.

---

## (b) Section B — routes for FSR 4

**B1, FSR 4 SDK facts (settled by pass 1's audit; not re-verified here).** Supported APIs: **DirectX 12 and Vulkan**. **No OpenGL path exists in the SDK** — this is what the charter itself states ("OpenGL has no official FSR 4 path") and is consistent with GPUOpen's DX12/Vulkan sample split. Exact inputs (colour, depth, motion vectors, reactive mask, exposure, jitter) are in the GPUOpen manual; I could not re-open it this pass, so treat the input list as carried from the packet/pass-1 rather than freshly verified. On RDNA 3 (7900 XTX): ML upscaling 4.1.2 yes; frame generation only the 3.1.7 Swapchain, not 4.0.2.

**B2, OpenGL → Vulkan interop.** **UNVERIFIED.** Searches for `FSR upscaling OpenGL support GPUOpen`, `"FSR 4" Vulkan only OpenGL not supported`, `GL_EXT_memory_object AMD Windows OpenGL driver support Vulkan interop`, and `FSR2 OpenGL port implementation Vulkan conversion project` all returned nothing usable. I have no source for whether AMD's Windows OpenGL driver on RDNA 3 exposes `GL_EXT_memory_object`/`GL_EXT_semaphore`, and no source for any shipping project that ran FSR 2/3/4 from an OpenGL renderer through interop. Do not plan on this route until someone proves it.

**B3, the alternative routes — and the strongest live evidence found this pass, on OptiScaler:**

| Route | What it needs | What exists today (with sources) | Main risk |
|---|---|---|---|
| **A. Extend the recompile: emit depth + motion + jitter in its own GL renderer, then call FSR** | Source access to the renderer (we have it, GPL-3.0); a way to run FSR's DX12/Vulkan ML kernel from an OpenGL app | The recompile **builds on real Windows** (order `jade39-p1-recomp-build.20261031`, 2026-10-13 13:22 CT; `jade.exe --version` → `jade: build aadbdf1d2f5691aa (Windows, x86-64-v3), net protocol 13`) [39-CHARTER.md]. FSR's ML upscaler runs on RDNA 3 via DX12/Vulkan (pass 1, GPUOpen 2026-10-28) | **The renderer's internals are unknown** (A1–A3 unverified). GL→Vulkan/DX12 handoff for an ML pass is the unsolved part; see B2. Biggest unknown in the whole campaign. |

---

## (c) Section C — model/texture tool leads

| Tool | Where | Licence | Read / Write | Last update |
|---|---|---|---|---|
| **kmdlswap** | **Not located.** Searched `kmdlswap`, `"kmdlswap" Jade Empire`, `kmdlswap KOTOR model swap tool JadeBlender`, `kmdlswap JadeBlender GPL reader KOTOR`, `kmdlswap deadlystream` — every query throttled | UNVERIFIED (reported GPL via its JadeBlender reader) | UNVERIFIED | UNVERIFIED |
| **JadeBlender** (the reader kmdlswap reportedly uses) | Not found independently; only the lab's own note mentions it, and that note records ChatGPT also could not find the repo [PROJECT-BACKLOG.md] | UNVERIFIED | Reportedly read (heads and bodies) | UNVERIFIED |
| **Any current MDL/MDX writer, or a Jade texture (TXB) writer** | **Could not find one.** Searched `Jade Empire MDX model editor tool export writer`, `Jade Empire Blender add-on import model`, `Jade Empire modding texture format TXB tool convert writer`, `"Jade Empire" MDX model format tool xoreos JadeMdl reader writer` — all throttled | — | — | — |

The pass-1 conclusion stands and is the important one for Phase 3/4: **a reader exists; a writer does not (as far as anyone has found).**

---

## (d) Open questions only a real build or playtest can answer

1. **Does the recomp's GL renderer expose a depth buffer, and can it cheaply derive motion vectors** from previous/current view-projection matrices for static geometry? — Read the source at `F:\jade39\recomp\4d8459e…` (already on Mothership). No web search can answer this.
2. **Where in the frame is the UI drawn**, i.e. is there a clean pre-UI point to insert an upscale pass? — Same source read.
3. **Does the recomp actually run on real Windows with an AMD GPU?** It *builds* (proven); it has never been launched here, and the README only ever claimed Wine. First launch is the test.
4. **Does render scale in `[Port]` reduce the internal framebuffer or scale output only?** Determines whether an FSR-style pipeline is even expressible.
5. **On a 3440×1440 ultrawide vs 3840×2160 TV, does the SDL3/OpenGL port handle aspect ratio and refresh modes** the way the stock `d3d.log` fallback failure showed the original does? — Needs a real launch on each display.
6. **Frame generation:** upscaling 4.1.2 on RDNA 3 is settled; whether *any* frame generation worth enabling exists on the 7900 XTX (FG 3.1.7 Swapchain) is a performance measurement, not a web fact.
7. **How much of the game is playable start to finish** in the recomp — the charter's own list (new game, load a save, a fight, a cutscene, raise the frame rate, couch co-op with two controllers).
8. **Does OptiScaler inject into a DXVK-translated D3D9 title that never called an upscaler?** Only answerable by an actual test on Jade Empire under DXVK.

**Bottom line for the decision, stated as evidence not a choice:** route A (extend the recompile) is the only one where depth and motion vectors are *ours to author*, and its feasibility now hinges entirely on Section A — which I could not read. Route C has the most live precedent (OptiScaler + DXVK/VKD3D + Vulkan FSR4, dated Feb–Jul 2026), but its documented requirement is a game that *already calls* DLSS2/FSR2+/XeSS. Route D is available today with zero code, and is spatial-only. Route B is bounded by an unmeasured port size. The next factory order should read the source tree, not search the web.
