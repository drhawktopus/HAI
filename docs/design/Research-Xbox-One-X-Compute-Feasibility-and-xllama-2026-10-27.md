# Xbox One X as a dedicated compute worker

Research date: 2026-10-27. Scope: the owner's **Xbox One X**, not Xbox Series X, original Xbox, or the 2013 Xbox One. The console is disassembled and has no system drive. Its previous 2 TB SSD is now Mothership's Operations drive and is not an available donor. Preserving gaming use is not a requirement. Hardware modification is acceptable in principle, but this research does not authorize purchases, installation, firmware changes or exploit attempts.

## Finding

This remains a **porting candidate**, not a ready One X package. The pinned source reviewed here targets Series S and has an immediate CPU instruction-set incompatibility. Its GPU backend also has a memory-architecture requirement that needs a console measurement. I found no demonstrated complete native-Linux/GPU-inference setup or released Bliss hardware exploit for One X in the reviewed primary sources.

## Existing lab research search

At the owner's request I searched the live documentation, journals, archives, pre-move documentation tree, selected state artifacts, Claude Home research folder, and available recent chat titles. Search terms included Xbox, Xbox One X, Scorpio, Jaguar, UWP, DirectML, xllama, Bliss and the xllama author's name.

No earlier One X compute write-up was located in that scope. This does **not** establish that the owner never discussed or documented it elsewhere. Older Xbox matches were primarily controller/power behavior, Home Assistant integration, and quick-resume inspiration. `LLM-Project/Eager-Tax-Research-Montage.md` contains prior ONNX/DirectML research, but its target is Windows/ROCm on Mothership's RDNA3 GPU; it is not a One X execution path. The earlier video intake from today is retained separately as `Research-Kai-PS5-Local-Models-Xbox-One-X-Implications-2026-10-27.md`.

## Route comparison

| Route | Evidence | Verdict for this One X |
|---|---|---|
| Native UWP CPU inference | Existing Xbox llama.cpp/ONNX project; publicly documented Developer Mode | Strongest first software baseline, after a compatible rebuild and hardware restoration. Throughput unknown. |
| Native UWP GPU inference using Direct3D 12 | Microsoft engineer documents D3D12 in Xbox One Game mode; xllama has custom ggml D3D12 code | Credible engineering path. CPU compatibility, GPU device/shader support, memory properties and actual allocation must pass on One X. |
| Linux inside the XboxInstallOtherOS app | Project documents QEMU software CPU emulation, experimental OpenGL drawing, Series X\|S | Does not provide a demonstrated One X native Linux or GPU compute route. Adds an emulation layer rather than resolving access. |
| Native Linux through Bliss/modchip | Original research presentation explicitly identifies the 2013 Xbox One Fat and distinguishes hardened One S/X designs | No demonstrated One X implementation found. Treat as new security/hardware research, not a known install procedure. |
| Extract the APU/use the motherboard as a PC GPU | No vetted One X conversion design or working inference demonstration located | No practical foundation found to recommend this over the UWP route. Rehousing the board does not itself provide a boot or driver solution. |

## The useful new foundation: xllama

Source review was pinned to Git tree revision **`666924a25179d722139ff201f214f1bc04d45cb9`**. Selected source files and documentation were cached without installing or executing the project. Search snippets and an older cached README described broader support than the pinned README; the explicit tested-platform statement takes precedence.

### 1. Confirmed source-level barrier: AVX2

The release settings in both `uwp/ggml-uwp.vcxproj` and `uwp/xllama.vcxproj` include `/arch:AVX2`. The top-level CMake defaults also force AVX2, F16C and FMA for its portable configuration. These are Series-era assumptions, not an Xbox One compatibility baseline.

**Implication:** do not install xllama's existing Series package and interpret an early crash as proof that inference is impossible. A compatibility build must audit the application, ggml, intrinsics and dependencies together. Changing one compiler flag is not proof the entire dependency chain is compatible.

### 2. Documented GPU opening: Direct3D 12 in Game mode

The shaders use packed integer-dot operations identified in source as SM 6.4. API-level overlap is encouraging, but neither numerical correctness nor instruction throughput on the older GPU follows from the Series S result. Shader compilation and a tiny numerical comparison are earlier questions than end-to-end large-model speed.

### 3. A second concrete barrier to check: coherent shared memory

**One X's value is unknown here.** Shared physical GDDR5 does not prove this API flag is true. If it is false or the query fails, the current backend will not initialize; an alternative buffer/synchronization path would be engineering work. It would not establish that the GPU is fundamentally unusable.

### 4. Accessible memory is the sizing constraint

A small, compatible model is the sensible first feasibility target; approximately 0.3–3B is a **research sizing range**, not a promised supported catalogue. Actual weights, cache, buffers and working set determine fit. No One X tokens/sec or task-completion estimate is justified yet.

## What the original Bliss researcher actually said

This does not prove One X will never be unlocked. It does mean the original presentation is not a justification for buying or building a One X “Bliss chip.” I found no later primary One X demonstration that changes that conclusion in this pass. SystemOS kernel exploits and firmware-specific retail UWP deployment are separate capabilities, not proof of native Linux boot.

## Rebuilding the actual console

No need has been established for a large SSD, an eGPU, RAM replacement or a modchip just to answer the first compatibility questions. Use an appropriate spare boot drive if available before spending on a speculative conversion. Mothership's Operations drive remains unrelated to this rebuild.

## Proposed first milestone

**Answer whether a One X-compatible native UWP process can execute a small model and access the hardware D3D12 device.** That is a finite milestone, rather than promising a complete Linux conversion.

Once the console is reassembled, the practical sequence would be:

1. Record model and OS build, activate the supported developer route, and designate the feasibility package Game.
2. Start with an instruction-compatible CPU build; record CPU features and actual process memory allowance.
3. Read the actual hardware adapter, feature/shader capabilities, `CacheCoherentUMA`, and GPU budget. Treat software fallback as a failed GPU experiment.
4. Only if supported, run one small compute operation against a CPU reference and then one small model. Preserve the existing xllama architecture rather than inventing a second inference stack.
5. Measure the user's outcome: cold load, first response and total task time, plus sustained stability. Prompt/decode rates explain those durations; they do not replace them.

If the hardware GPU path is viable, its existing network API is a possible later bridge to HAI. Compatibility with the lab's scheduling/ownership machinery would be a separate integration task, not automatic from exposing an endpoint.

**Nothing in that sequence has been executed on the owner's console.** No package was built, model downloaded, hardware purchased or modified, firmware installed, or benchmark submitted. The deliverable of this pass is the concrete candidate, its two major compatibility barriers, primary evidence, and a bounded next milestone.
