# Video intake: PS5 local models and implications for Xbox One X

This is a research intake, not a console benchmark or installation. No console firmware, settings, software, or hardware was changed. No inference job was submitted for this intake.

Evidence labels: **Video** = claim in the supplied video's captions/description; **Primary** = checked against the project/vendor documentation; **Lab** = previously recorded lab measurement; **Inference** = interpretation; **Unverified** = not established by the reviewed sources. The English captions contain transcription errors in model and tool names; use the linked source documents for spellings.

## Assessment for the owner

**The missing evidence is a working Xbox One X execution path, not an argument that consoles cannot run models.** The PS5 projects demonstrate that a console can become an inference server when the operating-system, driver and memory-access pieces exist. I did not find a primary demonstration of that complete native-Linux/GPU-inference path on an Xbox One X in the sources reviewed here. That is a research gap, not proof of impossibility.

## What was checked from the PS5 video

| Video claim | Finding |
|---|---|
| A jailbroken PS5 runs a quantized Qwen 3.8 27B at about 20 tokens/s | **Primary: developer-reported.** PS5LM's console notes identify the test console and quant, report 35–50 prompt tokens/s and approximately 21 generated tokens/s on a short prompt. They also report a substantially slower generation figure after the web UI's longer prompt. These are measurements of that setup, not a universal PS5 speed. |
| A tiny Claude Code edit took 613 seconds | **Primary: developer-reported.** The CLI notes document the edit, model, context and cache setup. They attribute most of that duration to the initial approximately 18k-token prompt. |
| Useful memory depends on how the program runs | **Primary.** The native GPU app has a different allocation path from a small payload. The project records an approximately 11.44 GiB Vulkan heap for the native app and tighter practical payload limits. |
| Switching to Linux gives about 200 prompt tokens/s and 16 generated tokens/s | **Video; specific figures not independently reproduced or fully verified here.** Native PS5 Linux is documented, but this video's comparison also changes quantization/setup, so it is not a controlled fivefold operating-system-only speedup. |
| Two PS5s can cooperate on the 27B | **Primary: separate project report.** The cluster repository documents native Ubuntu and llama.cpp RPC across two GPUs, using a specific IQ3_S file, vision and full 262K context at roughly 20 generated tokens/s. It is not one PS5's capability and is not an Xbox result. |
| The large Flash-Next model runs split across a PC and PS5 | **Video; not independently checked in this intake.** Do not turn this into a measured capability of the lab's Xbox. |

## Four different Xbox claims that must remain separate

| Route | Evidence and relevance to One X |
|---|---|
| Native UWP app/game in Developer Mode | **Primary: a documented software route with resource limits.** Microsoft's published foreground budgets are 1 GB for apps and 5 GB for games; available CPU/GPU resources and DirectX feature levels differ by classification. The documentation also notes a debugger exception to memory enforcement. These are documented budgets, not allocations measured on the owner's console. |
| Linux emulated inside a UWP app | **Primary: exists for Series X\|S.** XboxInstallOtherOS uses QEMU software CPU emulation, without an app-accessible hypervisor. Its optional graphics path is Virgl → ANGLE → Direct3D 11, reporting OpenGL 2.1. That establishes accelerated drawing in that project; it does not establish Vulkan compute, an accelerated llama.cpp backend, or support for Xbox One X. |
| A SystemOS kernel exploit | **Primary: Collateral Damage targets specified Xbox One/Series kernel builds.** Its README documents SystemOS kernel code execution. That does not, by itself, establish native Linux boot, a hypervisor escape, unrestricted GPU memory or a working inference driver. |
| Native Linux replacing the console OS for a session, with GPU compute | **Unverified for One X in this intake.** This is the relevant analogue to the PS5 Linux demonstration. A Linux terminal inside an emulated machine is insufficient evidence for this route. |

**Important nuance:** Microsoft's table gives UWP games access to available GPU cycles and lists DirectX support. Therefore, “Developer Mode means no GPU whatsoever” would also be an overstatement. The remaining question is whether the specific APIs, driver features and memory budget can support an inference implementation. A graphics demonstration alone does not answer that.

Likewise, “12 GB installed” must not be advertised as “12 GB available for model weights.” Runtime buffers, cache, the application, operating-system reservations and the chosen application class all matter. Report allocated and resident memory from the actual process rather than adding advertised or virtual heap sizes together.

## Corrections and limits worth retaining

1. **Measure completion time, not just text-generation speed.** The supplied video illustrates a large initial prompt dominating a tiny edit. For a persistent session, distinguish uncached input, reused cache, generation, tool execution and model load. The project's CLI notes explicitly say later turns reuse cache; a fixed full-prompt penalty on every tool call would overstate the cost.
2. **The bandwidth calculation is only an idealized ceiling.** The video uses approximately 448 GB/s divided by a 6.8 GB model and calls the result about 61 tokens/s. Using those rounded decimal inputs gives about 66. Neither number predicts achievable One X throughput; effective bandwidth and computation still matter. This arithmetic difference does not undermine the broad point.
3. **The Linux comparison is not one variable.** Different quantization, backend and runtime settings prevent attributing the full reported difference to Linux alone. It supports investigating software constraints, not dismissing compute limits.
4. **Match firmware to the exact project.** PS5LM app prerequisites and the native Linux loader's supported firmware list are separate. The loader lists specific supported versions rather than promising every intervening release. No firmware advice for the owner's Xbox follows from that PS5 list.
5. **Price and purchasing recommendations were not evaluated.** The video discusses PS5, PC GPUs and BC-250 hardware, but buying equipment is outside this intake. No price or purchase claim is adopted here.

## Relation to the existing lab

**Lab:** the documented Rigby x1→x16 rerun raised approximately 2k-token prompt reading from about 101.5 to 349.7 tokens/s, while its matched standard order's total duration fell from approximately 1117 to 950 seconds. That supports separating reading, writing and total task time, rather than equating a component speedup with an equal reduction in completion time. Source: `36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13.md`, section 8.

The broader 20-cell repricing comparison inspected today accumulated approximately 8h23m runtime before and 7h20m after, about 63 minutes less. This is useful local context, not a console forecast; the engine revision and workload details must remain attached to any causal claim. The existing lab machinery for placement, model windows and pause/resume would be relevant only after a console has a proven executable backend. An Xbox browser connected to HAI is a client, not an inference worker.

## Proportionate next step, if the owner wants to pursue One X

Start with an inventory of the actual console: exact hardware model, OS/kernel build, Developer Mode availability, and which software route is acceptable while retaining its gaming role. Then establish the process's real memory budget and available compute APIs. Only after that choose a small model and a narrowly scoped native inference baseline, if feasible. Measure cold load, uncached prompt processing, warm-prefix processing, generation, total request completion, memory and sustained stability.

This is a suggested research sequence, **not a queued implementation, exploit attempt, porting project or benchmark campaign**. A native UWP/backend route may be worth examining without Linux; it is not asserted to work. Software-emulated Linux should not be treated as the default inference route merely because it can show a desktop.

## Intake files and remaining unknowns

Cached artifacts: full video metadata/description, original English VTT, timestamped readable transcript, and selected primary-project documentation. No audio/video download was needed. The existing Pinchflat route retrieved the captions; its cookies were reported stale and the downloader attempted to save to a read-only cookie mount, but the downloaded VTT was successfully recovered. No credentials were changed.

Still unknown: the owner's exact One X firmware/build and app resources; any currently working One X GPU inference port; native Linux GPU-driver support on that machine; One X model throughput; the video's Linux benchmark under a fully matched configuration; sustained console power/thermal behavior. No success or failure on these is inferred from PS5 or Xbox Series results.


## Dedicated One X follow-up

The subsequent primary-source/code review is [[Research-Xbox-One-X-Compute-Feasibility-and-xllama-2026-10-27]]. It identifies xllama as a Series-S-tested native UWP porting candidate, an AVX2 build barrier, and an unmeasured coherent-memory requirement on One X. It also records the owner's drive-less, disassembled console and the search for earlier lab research. No One X implementation or measurement is claimed.
