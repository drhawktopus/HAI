# The lab engine

The model server every factory runs is upstream llama.cpp (the commit the current ollama release pins), with the lab's
own patches applied and ollama's compatibility layer added, built once per hardware shape. Flash-Next on the AMD box
runs a second engine, Strata, whose Python server carries two lab patches of its own. This folder is the source for
both: the patches, the scripts that apply them and build the engines, and the orders that prove a new build before it
serves.

The patches exist for one standard: a run that stops anywhere (a deploy, a restart, a cut answer) continues token
for token where it stopped. Upstream llama.cpp saves a slot's KV cache, but not the pending token, the sampler's random
state or the drafter's state, and it cannot continue an answer that was cut mid-turn; the patches add each of those.

## What is here

| Path | What it is |
|---|---|
| `patches/` | Every lab patch to an engine source, one Python script each: `python <patch> <source tree or file>`. Each finds the exact upstream text it changes and exits non-zero when an anchor does not match (an upstream change it was not written for). |
| `apply_ollama_compat.py` | Adds ollama's llama.cpp compatibility layer (`llama/compat` in the ollama source) to a patched tree. |
| `make_revision_tree.py` | Cuts a lab revision's tree: a fresh copy of the previous revision plus one patch (how `620d222-r5` was made). |
| `build_cuda114.sh` | The tablet head's build: CUDA 11.4 in NVIDIA's image, for a Pascal card on driver 470. Run by the engine pipeline. |
| `install_cuda13.ps1` | The CUDA 13 compile toolchain on the Windows build box (no driver). |
| `ollama_slotpath.py` | Points a GPU box's ollama launcher at a slot-save folder (`LLAMA_ARG_SLOT_SAVE_PATH`), so every runner can save and restore. |
| `submit_*.py` | The proof orders, each sent through the order desk: the lossless-resume probe, the history probe, the engine build order, the Vulkan diagnostics. |
| `diag/` | Makers of one-off diagnostic and proof trees (a revision's tree plus one change, to test it alone). |
| `strata/serve/` | The tests the lab adds to Strata's `serve/` folder for its two server patches. |
| `rigby/` | The tablet head's own build scripts: the expert hot-store build, the async-RPC builds for splitting a model across the tablets (including the Android arm64 build), the profiler build. |
| `research/split-inference/` | The analysis tools from the split-inference project (closed): routing skew, expert ranking, GGUF comparisons, turn-length and cache-reuse readers. |

The source trees themselves (`llama.cpp-<commit>`, `ollama-<version>-src`, `strata-*`) and the downloaded release
archives live beside these files but are not versioned: they are derived, rebuilt from upstream plus `patches/`.

## The patches

| Patch | Engine | What it adds | Carried by |
|---|---|---|---|
| `slot_pending_token_patch.py` (#0) | llama.cpp | The slot save reports the slot's pending token, the one no cache holds yet. | every factory |
| `slot_resume_patch.py` (#3) | llama.cpp | The slot save carries what a lossless continue needs beyond the KV cache (the drafter's state), and `lab_resume` restores it. | every factory |
| `slot_rng_patch.py` (#2) | llama.cpp | The slot save carries the sampler's random state. | every factory |
| `slot_continue_patch.py` (#4) | llama.cpp | A cut answer is continued by repeating the very request that was cut; no special client. | every factory |
| `vk_fa_dead_cells_patch.py` (#5) | llama.cpp | The Vulkan cooperative-matrix attention shader no longer reads stale cache cells (the AMD card repeated itself without it). | every factory |
| `cuda_graphs_pre_volta_patch.py` | llama.cpp | CUDA graphs on pre-Volta cards, gated by `LAB_CUDA_GRAPHS_PRE_VOLTA`. | the tablet head |
| `strata_continue_patch.py` | Strata `serve/server.py` | The mid-token resume on Strata: a turn cut and saved, restored with `lab_continue`, continues. | Flash-Next |
| `strata_prefix_tools_patch.py` | Strata `serve/server.py` | A saved shared start may end inside the tool list (`{"tools": K}`). | Flash-Next |
| `hotstore_block_patch.py` | llama.cpp PR #26563 | The expert hot store keeps whole expert chains on the GPU, gated by `LAB_HOTSTORE_BLOCK`. | the tablet head's hot-store build |
| `sched_profile_23b0202.py`, `make_sched_profile_23b0202.py` | llama.cpp | A scheduler profiler (`GGML_SCHED_PROFILE=N`), off unless set; the second derives the first from the AMD box's profiler patch. | the profiler builds |
| `rpc_*_patch.py` | llama.cpp ggml-rpc | Async RPC for a model split across many machines: overlapped copies, async input posting, async weight loading, and a trace. | the split-inference builds (closed project) |

The engine pipeline applies #0, #3, #2, #4 and #5 in that order (`PATCH_ORDER` in `engine_pipeline.py`), then the
compatibility layer. On the AMD box, Flash-Next's lab server is the file `workspace/flashnext/strata_server_lab.py`
in the factory code (the two Strata patches applied to upstream `serve/server.py`, which is what the plan installs
before the engine starts).

## How a new engine reaches the factories

`engine_pipeline.py` (in the lab tools) runs nightly. When a new ollama release has been out for 7 days it downloads
the llama.cpp commit that release pins, applies the patches above (a patch that no longer applies holds the pipeline
with the patch's name: that is a rebase, and a person does it), adds the compatibility layer, has the Windows build box
build the engines through an order, proves lossless resume on one box, rolls out box by box with the same proof on each,
and builds the tablet head's engine last:

    build_cuda114.sh <short commit>:<engine folder>

The pipeline copies the patched tree and `patches/cuda_graphs_pre_volta_patch.py` into the tablet head's engine folder
(`TABLET_HEAD_ENGINE_TREE`) first, then runs this through `run_remote.py` (as root, which is why the script takes the
folder as an argument rather than reading `$HOME`).

Settings it reads (`lab.env`): `LAB_ENGINE_DIR` (this folder), `ENGINE_PATCHES_DIR` (optional; defaults to this
folder's `patches/`), `TABLET_HEAD_NAME`, `TABLET_HEAD_ENGINE_TREE`, `TABLET_HEAD_ENGINE_ARCHIVE_PREFIX`,
`ENGINE_PROBES`.

## The tablet head's own scripts (`rigby/`)

Run them through `run_remote.py <tablet head> rigby/<script>.sh`. They run as root through sudo, so each takes the build
user's home from `SUDO_USER` (`BUILD_HOME`, overridable) and works under `$BUILD_HOME/llama-build-rpcasync`, the
folder that also holds the patch copies they read. `build_slot_pending.sh`, `build_slot_resume.sh`,
`build_cuda_graphs.sh` and `build_cuda114_latest.sh` predate the engine pipeline, which now does their job;
`build_hotstore.sh` and `build_hotstore_block.sh` make the hot-store build the tablet head can still be asked for
(`build: hotstore` in an order).
