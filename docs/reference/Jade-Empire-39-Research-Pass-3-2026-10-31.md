# Jade Empire #39 — Research Pass 3 (2026-10-31): why the recompile draws black on AMD, the FSR 4 bridge proven on RDNA 3, archive tools

Part of [[39-CHARTER]] Phase 1; follows [[Jade-Empire-39-Research-Pass-2-2026-10-31]]. HAI run `b03487fa…` (thread
`f29699b9ffe2`, ClaudeSupervisor), Mothership Flash-Next, 55 turns, 92 page reads, one compaction. This was the first
pass with HAI's public page reader (`fetch_url`, commit `8ec9164`). Two earlier attempts the same evening were void,
and each exposed a lab bug that is now fixed:
- `ff85eccc…` ended at `length`: llama-server windows reported only the newly read tokens as the prompt size, so
  compaction never fired (fix `71a8b8c`).
- `4c4e450e…` was not offered `fetch_url` by the tool selector (fix `bf21fe2`: `fetch_url` is CORE beside
  `web_search`).

## Part 1 — Claude's audit (what holds, what was corrected)

**Checked against the source, and it holds.** Claude compared every code quote against the repo at `4d8459e`:
- `fb_bind` (d3d9_frame.odin:149–167) names all 4 draw buffers, the unused ones `GL_NONE`, and fatals on an
  incomplete framebuffer, so the FBO reports complete on this driver.
- `clear_rect` (:231) clears with `glClearBufferfv(GL_COLOR, i)` after a scissor.
- `RT_MAX = 4`.
- `d3d9_fade.odin:51` already works around one AMD Windows bug (a scaled blit showed its source unscaled).
- Present copies by drawing through a program pipeline.

**Checked against the issue pages, and it holds:**
- **AMD-Gfx-Drivers #76** (opened 2025-11-06, closed 2026-05-11). An RX 7900 XT on driver 25.9.1 used exactly
  `GL_EXT_memory_object(_win32)` and `GL_EXT_semaphore(_win32)` with the matching Vulkan win32 externals. The
  problem was `glSignalSemaphoreEXT` stalls (GL+interop 1080 fps vs Vulkan-only 3050). AMD reverted its first fix on
  2026-01-06 (TDR/hangs), and the fix shipped in Adrenalin 26.5.1: interop went from 48.5% to 7.1% of the frame.
- So **OpenGL↔Vulkan interop works on AMD Windows on RDNA 3**, fixed for performance well before Mothership's
  26.10.2. This is the first real-hardware evidence for the "GL renderer + Vulkan FSR 4 pass" route. It is still not
  a shipped upscaler doing it.
- **#119** (opened 2026-10-28, open, no AMD reply): fragment output silently dropped, with no GL error. It needs a
  multisampled framebuffer AND alpha-to-coverage AND a `gl_SampleMask` write plus a `discard` in the shader.

**Corrected:**
- HAI's table calls #119 "RX 7900-class". The page states no GPU or driver.
- Because #119 needs multisampling and the lab ran `FSAA=0`, it **probably does not explain our black frames**. HAI's
  own ranking (D3) already left it out; the D2 row's "closest match in shape" overstates it.

**Not yet tested (the ranking is HAI's, the tests are cheap):**
1. Name only the live draw buffer(s) in `fb_bind` instead of 4 with three `GL_NONE`.
2. A dummy colour attachment with depth-only framebuffers (#102 pattern).
3. A plain linked program instead of the program pipeline for the copy.

Each is a few-line patch to the recompile, built with the existing build order and checked with the launch probe's
lit-pixel count. Nothing has been patched or run. Running the game was out of scope for the day, so the next launch
waits on the owner.

**Archive tools for the census:**
- xoreos-tools (`unrim`/`unerf`/`unkeybif`, GPL-3.0, Windows-buildable).
- The recompile's own `tools/rim` (RIM V1.0 list/extract, built by the same tree).

Either gives a set-and-forget census order over the 1,028 `.rim` / 63 `.mod` / 3 `.bif`. NeoERF's site did not
resolve. Still no Jade MDL writer or `.txb`/`.tpc` writer found; "JadeBlender" has no standalone repo.

## Part 2 — HAI's report (verbatim; sha256 prefix 44167a3e3a9b0bc3 of the text as returned)

I was doing the pass-3 research: opening the recompile's renderer source at that commit, then hunting issue trackers for AMD Windows OpenGL silent-black patterns, plus FSR 4 routes and model tools. I got a solid read of the frame path (frame, msaa, fade files quoted in full), a good set of AMD driver issue reports, and partial B/C material; web search engines were throttled throughout so some searches never landed. Below is what I can actually source.

---

# Campaign #39 — Research Pass 3 report

**Method note:** `fetch_url` worked this pass (raw.githubusercontent.com, github.com issue pages, AMD release notes). The `web_search` backend was throttled for most of the pass; where I say "could not find," that means the searches I ran returned nothing relevant, not that nothing exists. NeoERF's site did not resolve from here at all.

---

## (a) Section D — the black screen

### D1. The frame path as read (commit `4d8459e92755607d436ca5ca274a5b1384708259`)

**One framebuffer for everything.** `port/src/win32/d3d9_frame.odin`:

```odin
frame_init :: proc() {
    gl.PixelStorei(gl.UNPACK_ALIGNMENT, 1)
    gl.PixelStorei(gl.PACK_ALIGNMENT, 1)
    gl.CreateFramebuffers(1, &fbo)
    gl.CreateFramebuffers(2, &blit_fbo[0])
```

The back buffer is **not** a renderbuffer — it is a GL *texture*. `frame_reset` creates it as a D3D surface (`surface_new(p.width, p.height, D3DFMT_A8R8G8B8, D3DUSAGE_RENDERTARGET, D3DPOOL_DEFAULT)`), and the resource layer (`d3d9_res.odin`) gives every surface a `gl` texture name; render targets carry `scale` ("GL storage is scale x w/h"). Attachments are made with **named/DSA calls only**:

```odin
img_attach :: proc(f, a: u32, im: Gl_Img) {
    if im.layer >= 0 { gl.NamedFramebufferTextureLayer(f, a, im.tex, im.level, im.layer) }
    else             { gl.NamedFramebufferTexture(f, a, im.tex, im.level) }
}
```

**`fb_bind` is the critical function** (d3d9_frame.odin):

```odin
fb_bind :: proc() {
    if fb_dirty {
        ms_bound = ms_pick()
        bufs: [RT_MAX]u32
        for i in 0 ..< u32(RT_MAX) {
            ms_attach(gl.COLOR_ATTACHMENT0 + i, bind.rt[i], ms_bound)
            bufs[i] = bind.rt[i] != 0 ? gl.COLOR_ATTACHMENT0 + i : gl.NONE
        }
        gl.NamedFramebufferTexture(fbo, gl.DEPTH_STENCIL_ATTACHMENT, 0, 0)
        ms_attach(depth_attachment(bind.depth), bind.depth, ms_bound)
        gl.NamedFramebufferDrawBuffers(fbo, RT_MAX, &bufs[0])   // always 4 entries
        st := gl.CheckNamedFramebufferStatus(fbo, gl.DRAW_FRAMEBUFFER)
        if st != gl.FRAMEBUFFER_COMPLETE { x86.fatal("d3d9: framebuffer incomplete ...") }
        fb_dirty = false
    }
    ms_mark()
    gl.BindFramebuffer(gl.DRAW_FRAMEBUFFER, fbo)
}
```

Note two things: the status check **does** exist and would have fataled — so the FBO reports `FRAMEBUFFER_COMPLETE` on this driver. And `gl.NamedFramebufferDrawBuffers(fbo, RT_MAX, ...)` is called with **4 draw buffers even when only rt[0] exists**, the rest set to `gl.NONE`.

**Clears** never touch the default framebuffer: `Dev_Clear` → `clear_rect`, which does `gl.Scissor(...)` and then `gl.ClearBufferfv(gl.COLOR, i, &c[0])` per bound render target (d3d9_frame.odin). So the clear colour goes into the back-buffer texture.

**MSAA path** (`d3d9_msaa.odin`): twins are `TEXTURE_2D_MULTISAMPLE` created with `gl.TextureStorage2DMultisample(..., fmt_must(r.format).internal, ...)`. `ms_pick()` returns false unless *every* bound attachment has a live twin ("GL framebuffers cannot mix sample counts"), then resolves and attaches single-sample textures. With MSAA off in the lab, `ms_samples = 0` and this path is inert.

**Present** (`Dev_Present`, d3d9_frame.odin): `smaa_frame(false)`, `soft_touch()`, `ms_read(backbuf)`, optional `frame_dump` (which reads back with `gl.GetTextureImage(b.gl, 0, gl.RGBA, gl.UNSIGNED_BYTE, ...)` — this is the read that showed all zeros), then:

```odin
fade_present(b.gl, i32(b.w) * k, i32(b.h) * k, ww, wh)
_ = sdl.GL_SwapWindow(window)
```

`fade_present` (d3d9_fade.odin) draws into a **separate** framebuffer `fade.fbo` whose colour attachment is `fade.tex`, an RGBA8 texture created in `fade_resize` with `gl.TextureStorage2D(..., gl.RGBA8, w, h)` and cleared black via `gl.ClearNamedFramebufferfv`. The actual copy shader exists because of a known AMD bug, quoted verbatim:

```odin
// Present copies by drawing, not glBlitFramebuffer: AMD's Windows driver
// showed a scaled blit's source unscaled (a 1.5x crop of the title screen).
```

The copy uses `gl.BindProgramPipeline(fade.pipe)` + `gl.UseProgramStages(fade.pipe, gl.FRAGMENT_SHADER_BIT, fade.copy)`, `gl.BindSampler(FADE_UNIT, fade.lin)`, `gl.BindTextureUnit(FADE_UNIT, src)`, then `gl.DrawArrays(gl.TRIANGLES, 0, 3)`. The VS is a `gl_VertexID` triangle that redeclares `out gl_PerVertex { vec4 gl_Position; };`.

So the whole chain — clear → draw → SMAA → copy-to-window texture → swap — runs on immutable-storage textures attached to DSA framebuffers via program pipelines. Nothing ever draws to the default framebuffer.

### D2. Known AMD Windows GL silent-black patterns (issue trackers)

| Pattern | Project / issue URL | AMD driver version | Symptom & fix |
|---|---|---|---|
| Fragment output silently dropped in **multisample framebuffers** when a shader contains `discard` + writes `gl_SampleMask`, with alpha-to-coverage on | GPUOpen-Drivers/AMD-Gfx-Drivers **#119** (opened 2026-10-28) | RX 7900-class, GL 4.6 core context; repro is "a red quad into a 4x MSAA FBO cleared to black — framebuffer stays black" | **No GL error raised**; removing the discard or the sample-mask write fixes it. Open as of this pass. Closest match in shape to our symptom (FBO reads back exactly its clear value, no error). |
| Depth-only attachments render nothing | AMD-Gfx-Drivers **#102** "[OpenGL] OVR_multiview depth-only framebuffer renders only one layer unless a color attachment is present" (2026-06-04), RX 7800 XT / Vega, driver 26.6.1; labelled *fixed internally* | AMD-specific; works on NVIDIA and Mesa llvmpipe. Workaround in the repro: **add a colour attachment**. | Directly relevant to the autodepth-only case. |
| Uniform values mis-bound across shader stages when shaders are loaded as **SPIR-V** (two `layout(location=N) uniform uint` alias) | AMD-Gfx-Drivers **#85** (2026-01-07, Windows 11 + RX 7600, 25.12.1), *fixed internally* Feb 2026 | Same shaders work when compiled from GLSL source; fail via SPIR-V. | This renderer compiles GLSL source (`glsl_compile`), not SPIR-V — so it is *not* this pattern, but it shows AMD's pipeline/stage-uniform handling is a live bug area. |
| SPIR-V matrix uniforms reported as `GL_FLOAT_VEC4` instead of `GL_FLOAT_MAT4` | AMD-Gfx-Drivers **#2** (2024-05-17, RX 6800S, Windows) | fixed in published release by **24.10.1**. | Not our path (no SPIR-V), but same theme: AMD's non-GLSL shader entry points are unreliable. |
| Buffer upload corruption: `glMapBufferRange` returns "invalid enum 0x3" and the index buffer is garbage | AMD-Gfx-Drivers **#74** + CaffeineMC/sodium **#3318** (Nov 2025) | Introduced in **25.10.2**; affected RDNA 1–3; Sodium shipped a workaround in 0.8.1; AMD "fix for RDNA 3 & 4" by 26.1.x, RDNA 2 later. Workaround found by users: **rename javaw.exe** (application-profile dependent). | Shows the driver's GL path can silently corrupt/kill draws with no meaningful error, and that fixes take months to reach public Adrenalin. |
| Scaled `glBlitFramebuffer` shows source unscaled | The recompile's own comment in `d3d9_fade.odin` (no external URL) | AMD Windows driver, unversioned | Workaround already applied: draw instead of blit. |
| GL 4.6 bindless texture crash | AMD-Gfx-Drivers **#47** (fixed), **#27** (fixed) | 25.2.1 / 24.10.1 | Bindless textures are a known AMD Windows failure mode; not used here. |
| WineD3D/GL black screen on AMD | forum.winehq.org t/39186, discuss.cachyos.org #30395, AMD-Gfx-Drivers **#36** (GoldSrc black screen, fixed 25.6.1) | various | General "black screen, no error" reports exist but none isolate DSA framebuffers specifically. |

**What I could *not* find:** any issue report naming `glClearNamedFramebufferfv`, `glCreateProgramPipelines` or `glClipControl` as an AMD silent-black cause (searches for those returned unrelated hits). RPCS3/Dolphin/xemu searches on GitHub returned game-specific issues, none matching. The plaught-armor repo has **no issues at all** (`api.github.com/.../issues` → empty), so no upstream report exists to compare with.

### D3. Ranked list of calls in this renderer that match a known pattern

Ranked by how well the code matches an observed AMD failure mode:

1. **`gl.NamedFramebufferDrawBuffers(fbo, RT_MAX, &bufs[0])` with 4 slots, three of them `gl.NONE`** — d3d9_frame.odin, inside `fb_bind`. Multi-draw-buffers where the extra buffers are explicitly NONE is legal but is exactly the kind of DSA framebuffer configuration AMD's GL front-end has been caught mishandling (see #102: attachments silently not written). Cheap to test: pass only the count of live targets.
2. **Depth attachment via `gl.NamedFramebufferTexture(fbo, gl.DEPTH_STENCIL_ATTACHMENT, ...)` on an immutable-storage texture, sometimes with no colour target bound** — d3d9_frame.odin `fb_bind`/`depth_attachment_of`, and `ms_attach(depth_attachment(bind.depth), ...)`. Matches #102's "depth-only attachment renders nothing" pattern; Jade Empire's autodepth is a real case of this.
3. **Program-pipeline draws (`gl.BindProgramPipeline(dg.pipe)` / `gl.UseProgramStages(... FRAGMENT_SHADER_BIT, ...)`) with per-stage uniform updates via `gl.ProgramUniform1f`** — d3d9_fade.odin `fade_present`/`fade_copy`, and the same shape in `d3d9_draw.odin`. Matches AMD's documented stage-uniform aliasing bug (#85) even though that one is SPIR-V-triggered.
4. **Clears as `gl.ClearBufferfv(gl.COLOR, i, ...)` after `gl.Scissor`** — d3d9_frame.odin `clear_rect`. The lab result ("not even the clear colour") means either the FBO's colour attachment isn't actually writable or the scissor/viewport is empty at clear time; AMD scissor+multi-buffer clears are not documented as broken, but this is where a black buffer would first become visible.
5. **Immutable storage (`gl.TextureStorage2D` RGBA8 / `TextureStorage2DMultisample`) + `gl.GetTextureImage` readback** — d3d9_fade.odin `fade_resize`, d3d9_msaa.odin `ms_reset`, d3d9_frame.odin `frame_dump`. Matches AMD's immutable-texture/bindless bug history (#47, #27). If the texture storage is silently mis-allocated, every readback is zeros with no error.
6. **`glClipControl(gl.UPPER_LEFT, gl.ZERO_TO_ONE)`** — d3d9_device.odin/d3d9_glsl.odin (from pass 2). No AMD issue found in this pass; ranked last precisely because there is *no* evidence for it. Worth ruling out by toggling, not by assuming.

**Deliverable conclusion:** the two highest-value lab tests are (1) replace `NamedFramebufferDrawBuffers(fbo, RT_MAX, ...)` with a live-count draw buffer list, and (2) bind a dummy colour attachment whenever a depth-only framebuffer is used — both are one-line changes that map directly to AMD issues #102/#74-class behaviour.

---

## (b) Section B — FSR 4 routes, what the pages actually say

**B1. GL→Vulkan interop.** The Khronos spec text I opened (`registry.khronos.org/OpenGL/extensions/EXT/EXT_external_objects.txt`, "Version Last Modified Date: August 29, 2023, Revision: 10") states the mechanism plainly: *"Memory objects can not be created directly within the GL. They must be imported from an API capable of allocating abstract memory, such as Vulkan."* Semaphores likewise *"may only be created by importing an external semaphore handle into the GL"*, and `SignalSemaphoreEXT` *"will insert a semaphore signaling operation in the GL command stream, and flush the command stream as if Flush were inserted immediately after"* — i.e. signalling costs a flush. Texture layouts map 1:1 to Vulkan image layouts (`LAYOUT_COLOR_ATTACHMENT_EXT` ↔ `VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL`, etc.).

**The one real AMD Windows project case I found:** GPUOpen-Drivers/AMD-Gfx-Drivers **#76**, "Low Vulkan <-> OpenGL interop performance" (opened Nov 6, 2025; closed May 11, 2026). Author's setup: **"Hardware RX 7900 XT, driver 25.9.1"**, using `GL_EXT_memory_object`, `GL_EXT_memory_object_win32`, `GL_EXT_semaphore`, `GL_EXT_semaphore_win32` — *"As far as i'm aware this is all that i need to achieve high performance Vulkan to and from OpenGL interoperability."* Their finding: *"on AMD hardware i'm getting suboptimal performance... large amounts of time being spent somewhere inside glSignalSemaphoreEXT... my hunch is that cross-api semaphores aren't really implemented and it does a queue flush."* AMD measured GL+interop at 1080 fps vs VK-only 3050 fps on the 7900 XT. A fix landed: **Adrenalin 26.5.1** verified — *"the interop goes from taking up 48.54% of the frame down to 7.10%. FPS up from 84 to 106."* Note the fix was **reverted once** (Jan 6, 2026) due to TDR/hang issues before shipping properly. So: GL→Vulkan interop on AMD Windows *works*, but it is young and has had driver bugs as recently as this year. Sample code links found are NVIDIA-authored (`nvpro-samples/gl_vk_simple_interop`) — I did not find a shipped AMD-verified upscaler using this route. **UNVERIFIED** for an FSR4-on-RX-7900-XTX-through-GL-ext path specifically.

**B2. OptiScaler.** From pass 2's fetch, still the honest answer: OptiScaler hooks DX11/DX12/Vulkan games that already call DLSS/FSR2+/XeSS. Jade Empire (D3D9, no upscaler call) is outside its interception model; DXVK does not generate motion vectors. I did not re-open the wiki this pass (search throttled), so treat the "can it synthesise depth/vectors" question as **UNVERIFIED**.

**B3. gamegpu.com 2026-09-25 page and the FSR SDK input list.** I could not open gamegpu.com this pass — say plainly: search engines were throttled and I did not get a fetch of that page, so its content is **UNVERIFIED**. What *is* sourced: the AMD FSR SDK manual page fetched in pass 2 (`gpuopen.com/manuals/fsr_sdk/techniques/super-resolution-ml/`) lists the ML upscaling inputs as colour, depth, motion vectors, exposure (optional), reactive mask (FSR4), with motion vectors in pixel space `[-width,-height]..[width,height]` and camera jitter required (Halton sequence); DX12 needs CS_6_6, Vulkan needs SPIR-V 1.5; RX 7900 XTX runs the INT8 model.

---

## (c) Section C — model/archive tools

| Tool | Where | Licence | Reads / writes | Windows CLI | Last update seen |
|---|---|---|---|---|---|
| **kmdlswap** | github.com/jryantomes/kmdlswap | not verified this pass | Jade Empire mesh import ("From Jade Empire" tab, 158 heads, 112 bodies); vendored "JadeBlender" reader code; Jade node header is 60 bytes vs KOTOR's 80 | Blender add-on → runs on Windows via Blender | pass-2 finding; date unverified |
| **"JadeBlender"** | GitHub repo search for `JadeBlender` returned **0 repositories** (api.github.com/search/repositories, this pass) | — | — | — | absent as a standalone repo today |
| **xoreos-tools** (`unrim`, `unerf`, `unkeybif`) | xoreos project | GPL-3.0, C++ | Aurora-engine `.rim`/`.erf`/`.bif`; NWScript disassembly via ncsdis/ncsdecomp | yes (C++, builds on Windows) | unverified this pass |
| **NeoERF** | neoerf.com — **did not resolve from here**; could not open | unknown | `.erf` editing incl. Jade variants — unverified | unknown | unknown |
| **The recompile's own `tools/rim`** | plaught-armor/jade-empire-recomp, `tools/` | GPL-3.0 | listed in pass 2 as a rim tool; I did not open its source this pass | builds with the project (Windows build succeeded) | commit 4d8459e9 |

**Gap:** I found no current Jade Empire MDL *writer* and no `.txb`/`.tpc` texture-writing tool in any page I opened. That is "searched kmdlswap/JadeBlender/GitHub repo search," not "nothing exists."

---

## (d) What only a lab test can settle

1. Whether `NamedFramebufferDrawBuffers(fbo, RT_MAX, ...)` with three NONE buffers is the black-frame cause — swap to live-count and read back the texture.
2. Whether binding a dummy colour attachment alongside depth fixes the autodepth path (AMD issue #102 workaround).
3. Whether `glGetTextureImage` on an immutable RGBA8 texture returns real data or zeros independent of drawing — isolates storage vs framebuffer completeness.
4. Whether disabling `glClipControl(UPPER_LEFT, ZERO_TO_ONE)` changes anything — no external evidence either way.
5. Whether the program-pipeline draws reach the FBO at all: draw a solid quad to `fade.fbo` with a normal linked program instead of a pipeline and see if the window shows it.
6. Whether `GL_EXT_memory_object_win32`/`GL_EXT_semaphore_win32` actually function on 26.10.2 (declared ≠ proven), given AMD's own interop fix history (#76, shipped ~26.5.1).

_Stopped after 3 turns with no new work, inside your 4 hours budget: this is where the work stood, not a finished answer._
