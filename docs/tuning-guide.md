# Tuning guide: good starting configs for any model on any hardware

This is the lab's model research turned into rules. They are meant to outlast the models they were measured on: a
model released tomorrow, or a card the lab never had, still starts from them. The rules carry; the numbers are the
lab's, there to show how large each effect was where it was measured. Each section names the documents with the full
measurements (`docs/design/`, `docs/runbooks/`, `docs/reference/`), and the measured menu itself ships in
`menu/reference/` (958 configurations: every model, on every machine it ran on, by job, with quality, time and energy).

## 1. What decides a config

Four things about the hardware:

1. **The card's memory (VRAM).** What fits fully on the card runs at the card's speed.
2. **System RAM: how much and how fast.** A mixture-of-experts model can keep most of itself in RAM on purpose. Then
   RAM bandwidth, not the card, sets the writing speed.
3. **The CPU: its vector instructions and its physical cores.** The CPU-side half of a split model runs there, and
   AVX2 decides which fast kernels exist.
4. **The card's runtime and its link.** CUDA, ROCm, Vulkan or SYCL decide which engines and features you get, and
   the PCIe link decides how fast a split model reads a prompt.

Four things about the model, all on its model card or in its file:

1. **Dense or mixture-of-experts, and how many parameters are active per token.** A 35B-A3B model holds 35B but reads
   about 3B per token. That is what lets it run partly from RAM.
2. **Its attention layout.** Hybrid models with only a few full-attention layers carry a small context cache. The
   lab's 35B-A3B, with 10 full-attention layers of 40, needed 702 MiB of cache at 32K tokens.
3. **The file's bytes per token.** These come from the quantization and the active parameters, and writing speed
   follows them (section 5).
4. **Its controls.** Whether it thinks, which effort levels its vendor defines and which one is the default, and
   whether it ships a multi-token-prediction (MTP) head.

## 2. Will it fit, and where

- **A dense model must fit fully on the card, with its context window.** When it does not, Ollama does not fail. It
  quietly runs part of the model on the CPU, every request still succeeds, and everything is many times slower. In
  `/api/ps`, `size_vram` must equal `size`. The context cache comes out of the same VRAM, so a large default window
  can push layers off the card. `foundry/fit_probe.py` finds the largest window that stays resident
  (`docs/measure-your-own-box.md`).
- **A mixture-of-experts model can be split on purpose.** The attention and shared parts of every layer go on the
  card and the experts go in RAM. The lab ran one 35B-A3B model three ways:
  - an 8 GB card with 32 GB of DDR4: about 6.8 GB on the card, about 11.5 GB in RAM, a 65,536-token window;
  - a 6 GB card with 24 GB of DDR3 and a CPU without AVX2: 16.7 tokens per second writing, level with the best
    public result for that card;
  - a 24 GB card.

  A 125B-A6B model ran on the 24 GB card with 64 GB of RAM. "An 8 GB card holds one small model" turned out to be a
  rule about dense models only.
- **The experts a model touches must fit in free RAM, or the machine pages, and paging becomes the wall.** Varied
  prompts touch more experts than repeated ones, so judge a split on varied work.
- **There is a quality floor.** The lab kept CPU-side experts at Q3 or above, because quality drops steeply below
  that.
- **Two devices: give each its own model.** Separate models on separate devices add up. One machine served a
  35B-class model at about 16 tok/s on its card while the devices beside it served small models at about 50 tok/s in
  total, at the same time.
  Splitting one model's layers across two cards of different speeds runs them in series, and the slow card sets the
  pace: −68% on the lab's pair.

## 3. Where a token's time goes

This model of a split decode explains every measurement the lab took (`docs/design/Qwen3.8-Flash-Next-on-Mothership.md`,
"how the levers interact"; `docs/design/36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13.md`, section 2):

- **G, the GPU side.** A hybrid MoE runs thousands of small GPU operations per token, so it is bound by per-op launch
  cost and the card's clock, not by its compute. Every extra token one pass serves spreads G thinner.
- **C, the CPU-side experts.** The bytes of the experts in use, divided by RAM bandwidth, then by kernel efficiency
  and thread count.
- **P, paging.** It appears when the touched experts exceed free RAM.
- **Hand-offs.** The CPU and GPU take turns about 30 to 80 times per token, at about 1 ms each.

**Every win moved work to the GPU, or spread one CPU read over more tokens. Every loss moved work to the CPU.** Use
this to predict a lever before you spend a run on it.

Reading a prompt (prefill) is different. With experts in RAM, llama.cpp copies them to the card for each micro-batch,
so the PCIe link binds. Moving a 6 GB card from an x1 riser to a full x16 link made reading 3.5 times faster.

## 4. Starting settings by job, from every model measured

These come from the whole menu, comparing rows that differ in one setting only: same model, same file, same machine,
same job. The rows compared are those with a quality, its lower bound and a time per solved answer (920 of the 958 in
`menu/reference/model-matrix.csv`), so the counts below reproduce from that file.

**Thinking on or off depends on the job, not the model.** Across 251 such pairs, thinking cost a median of 5 times the
time per solved answer. What it bought:

| job (`menu/` task pool) | pairs | thinking clearly better | thinking off clearly better | within each other's band | time cost of thinking (median) |
|---|---|---|---|---|---|
| short questions with one checked answer (`chat-c1`) | 36 | 29 | 0 | 7 | 5.9× |
| work-shaped tasks: code to a spec, exact reasoning, troubleshooting (`work-w1`) | 26 | 17 | 0 | 9 | 2.8× |
| image questions (`vlm`, `vlm-2x`) | 44 | 10 | 1 | 33 | 1.0× and 2.1× |
| tool calls (`tool`) | 53 | 14 | 10 | 29 | 4.9× |
| write a function (`coding-f1`) | 43 | 3 | 13 | 27 | 10.8× |
| fix or change existing code (`coding-f2`) | 49 | 11 | 19 | 19 | 14.1× |

"Clearly better" means one row's lower confidence bound sits above the other's score. So:

- start reasoning, work and image jobs with thinking on;
- start function-sized coding with thinking off;
- for tools, choose by how hard the calls are.

Two cautions. A thinking row is only as good as the effort the engine actually ran (section 7). And longer agentic
coding is its own job, measured separately (`menu/a1/`).

**Temperature barely matters for quality.** 0.6 against 0.0: 210 of 239 pairs within each other's band, and the time
per solved answer moved by a median of 1% (half the pairs between 6% faster and 13% slower at 0.6). 0.7 against 0.0: 84 of 103 within band, with 0.0 ahead in 15 and 0.7 in 4. Start at the vendor's recommended
value.

**The job decides the size you need:**

- Function-sized coding saturates small models: a 4B scored 98 to 100% on an 8 GB card, about 1 to 3 s per solved
  task, and a 1B scored 63 to 70%.
- Work-shaped tasks and short checked questions separate them. A 27B scored 98% (work) and 100% (questions); the
  35B-A3B split onto an 8 GB card scored 88% and 95%; an 8B scored 44 to 65%.

Pick the smallest model whose row holds the quality you need; `menu/reference/standings.md` lists each job's best
row per machine.

## 5. The arsenal: every lever, kept as data

The lab's hardest lesson about speed: the levers are a FAMILY, and tuning goes wrong when it collapses to the one
lever that matches today's loudest number. So each machine's levers are kept as data:

- `foundry/workspace/flashnext/arsenal.json` (45 levers on the 24 GB card);
- `engine/research/split-inference/rigby_arsenal.json` (38 levers on the 6 GB card).

Each lever records its full form, the forms actually tested, a status (done, partial, open, closed) and its result,
plus the couplings between levers. Read them as status boards, not lists of wins: of the 24 GB card's 45 levers, 15
are done (proven), 4 partial, 15 open, 9 closed, 1 measured and 1 waiting on a decision; of the 6 GB card's 38, 18 are
done, 11 closed, 1 partial, 1 priced, and 7 out of scope or not applicable. A lever counts as tried only when its FULL
form has run. `foundry/workspace/flashnext/plan_lint.py` refuses a
measurement plan that names no lever, and each run prints the levers whose full form has never run. Start your own
arsenal from these two files.

The levers, grouped by what they attack (section 3):

| lever | what it does | what it measured in the lab | watch out for |
|---|---|---|---|
| **Multi-token prediction (MTP)** | The model's own draft head proposes tokens and the model checks them in one pass (`--spec-type draft-mtp --spec-draft-n-max 2`, the head passed with `-md`) | The biggest single lever: +45% on repeated work and +10% on varied prompts on the 24 GB card; +19 to 24% writing code on the 6 GB card, with the head accepting 94 to 99.6% | Pays only above about 82% acceptance. On the split models two-token drafts beat three. Ollama runs it where the model's published tag carries a draft head (from 0.34, qwen3.8 runs it by default, 4-token drafts); for other models serve with llama-server (`engine/`). Check whether a new model ships a head |
| **An engine built for the model** | A different inference engine for one large MoE (Strata, for Flash-Next) | The 24 GB card with 64 GB of RAM, varied prompts: Strata wrote a median 68.4 tok/s against llama.cpp's 26.9 (2.5x) and read 838 to 843 against 143 to 147 (5.8x). Its own `--low-ram auto` chose a 131,072-token context; the model's trained 262,144 cost 1.8 GB more RAM and wrote within the spread | Read what an engine's automatic mode chose before accepting it. Measure the answers' quality too: a different engine often means a different quant (`docs/design/Qwen3.8-Flash-Next-on-Mothership.md`) |
| **The backend** | CUDA, ROCm, Vulkan or SYCL for the same card | The 24 GB AMD card: Vulkan wrote 9 to 20% faster than ROCm, while ROCm kept the faster prefill. The 6 GB NVIDIA card: CUDA beat Vulkan, 12.4 to 14.2 tok/s writing | There is no universal winner. Measure every backend your card has, and keep a long-prompt route if reading differs |
| **Placement of a split model** | Which parts sit on the card | Attention and shared parts of every layer on the card is the right shape. Whole layers on the card lost on both cards (12.5 against 28.4 tok/s on the 24 GB card). `--n-cpu-moe N` keeps the first N layers' experts on the CPU, about 1 ms per token each | Raise N until the model fits, no further |
| **Expert cache** | Keeps the most-used experts on the card. Expert use is skewed (on the 24 GB card, 48 slots, 9% of a layer's experts, served 58% of reads), so VRAM spent on cache slots across every layer beats VRAM spent on whole layers: that is what keeps a bigger card paying | +31% warm on the 24 GB card without a repack (level with one, and a loss on varied prompts); the 8 GB card with 1 GiB wrote 26.5 to 32.9 against 22.9 to 24.2 tok/s; lost at every size on the 6 GB card once it had an x16 link (best case −27%) | Under Ollama, `LLAMA_ARG_MOE_CACHE_MIB` set for the whole server makes every dense model fail to load. A pinned, saved hot set tied or lost, because which experts are hot depends on the task |
| **Bytes per token** | The quantization of the CPU-side experts | Writing followed bytes per token, not the quant's kernels. On the 8 GB card a K-quant at 3.85 bits per weight and an IQ file at 3.88 wrote alike (26.1 to 26.4 against 25.6 to 26.3 tok/s), and larger files lost roughly in proportion to their size | Tuning is still specific to each file: re-measure a new quant rather than assume it behaves like its neighbour |
| **Repack at load** | Decode once into the CPU's fast layout (`--no-host` on llama.cpp lets it repack CPU-side experts) | About +10% writing on the 24 GB card. `--no-host` wrote +10% (35B-A3B) and +19 to 25% (dense 27B) on the 8 GB card, but read 93 to 97% slower | Needs AVX2. With `--no-host` it is a per-job option for writing-heavy work, not a default |
| **File-backed experts** | Memory-map the model from a fast drive (`-lm mmap`) rather than load it as anonymous memory | +30 to 40% on varied prompts. On Windows, anonymous memory past RAM went to the pagefile on the system drive and back | Put the model on your fastest idle drive |
| **RAM speed** | Memory clock and controller ratio (BIOS) | The 24 GB card's box wrote 28.1 against 30.1 tok/s after its RAM went to 1:1 | The CPU term is bandwidth-bound, so faster RAM is a real lever for a split model |
| **Threads** | CPU threads for the experts | Physical cores or fewer: 4 of 6 cores won on one box, 8 of 8 cores on another, 4 of 4 on the third; hyperthreads added nothing | Measure two or three settings |
| **Keep the clock up during a split decode** | NVIDIA's Windows driver parks a card that idles between bursts at about 450 MHz; a clock floor (`nvidia-smi -lgc <floor>,<max>`) keeps it working | The 8 GB card: 20.3 to 21.7 tok/s parked, 25.3 to 26.6 with a 1965 MHz floor. The AMD card ramped by itself, so no lever there | This repository sets no clock on your card; it is your choice. A fully resident model keeps the card busy and never shows it |
| **Micro-batch size for reading** (`-ub`) | Bigger micro-batches spread the copy over the link | The 6 GB card on its x1 link read 37.6, then 59.4, then 85.5 tok/s at 512, 1024 and 2048. The 24 GB card read 11% faster at 512 than at 256, writing unchanged. The 8 GB card (the split 35B-A3B, `-b`/`-ub` 2048): reading 1,590 against about 650 tok/s on a 4K prompt, writing 4 to 6% slower | Writing can lose a little: size it for your link and your job (reading-heavy work wants it bigger) |
| **Small reads stay on the CPU** (`GGML_OP_OFFLOAD_MIN_BATCH`) | Batches below the threshold are not streamed to the card | On the x1 link a 47-token read went from 6.5 s to 3.1 s at 256 | Only matters on a slow link |
| **The cache's number format** | A q8_0 cache with flash attention halves the context cache (Ollama: `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_FLASH_ATTENTION=1`) | The lab runs it under Ollama. On an old 2 GB card it cost speed (f16 3.12 tok/s, K at q8_0 2.55, K and V at q8_0 2.10, at 24K). On a hybrid model the cache is small and the saving buys little | Measure it on your card |
| **One request at a time on a small card** | `OLLAMA_NUM_PARALLEL=1`, or `-np 1` on llama-server | A second slot shares the weights but multiplies the context cache. On 8 GB it spills to the CPU | Two parallel streams is a throughput lever (about +14% in total, inferred), not a latency one. Measure it where the headroom is real |
| **Do expensive work once** | Keep a model loaded between jobs; save the read state that every chat shares (`docs/design/Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start.md`); prompt-prefix reuse | A first call always includes the load: the 6 GB box loaded its 35B in 60 s cold and 12 s warm | Never time the first call |
| **The PCIe link** | Seat the card at full width | x1 to x16: reading 3.5× | Check the link under load (`nvidia-smi`, or sysfs on Linux) |
| **n-gram speculation** | Drafts from text already seen | −34% on a split model: each checked token pays for its own CPU-side experts | Keep MTP instead |

Closed by measurement, so you need not spend the runs again: pinning the threads to physical cores (Windows already
spreads them: 29.5 against 30.1 tok/s for its control), Windows' hardware-accelerated GPU scheduling (left at its
default; not a lever), a quantized context cache on a hybrid model whose cache is already small (702 MiB at 32K on the
6 GB card: little to buy), whole layers on the card, and a pinned hot-expert set (task-specific routing).

## 6. Check the engine before the first measurement

- **Read its compile-time CPU features.** `llama-server -m nonexistent.gguf -v` prints `system_info` before it touches
  a model, and `AVX2 = 1` and `FMA = 1` must be there. One vendor build saw the GPU but shipped a CPU backend with no
  vector instructions, so the CPU-side experts ran scalar and every number taken on it was void.
- **Check what an engine needs.** Some engines need AVX-512 and have no AVX2 path
  (`docs/design/Qwen3.8-Flash-Next-Speed-Levers-Research.md`).
- **On Vulkan with experts on the CPU, pass `--no-host`.** Otherwise the experts land in the card's host-visible
  memory and context creation runs out of memory.
- **Use an engine as shipped, or build it.** Backend libraries from different compilers do not load into each other.

## 7. A new model: what to read before the first run

1. **Its architecture** (section 1): dense or MoE, active parameters, attention layout. That gives you its fit and its
   likely bottleneck.
2. **Its vendor's chat template, not just the capability list.** A capability list says what a template accepts, not
   what the model was trained to do. A model given tools its own template lacks once ran away on a single item, past
   its window.
3. **Its effort controls and their default.** A setting's name is not its value. One engine mapped "thinking on" to
   medium effort while the vendor's default was xhigh, so every "thinking on" result for that model was
   taken below its real level. Prove what the engine renders: the prompt-token count differs between levels.
4. **Whether it ships an MTP head**, and in which file.
5. **The engine version it needs.** Read the engine's validation at the version you have installed, not on its main
   branch.

Then fit it (section 2), set thinking and temperature from its job (section 4), and walk the arsenal (section 5).

## 8. Measure honestly

These are the lab's doctrine (`docs/doctrine/`); each one cost a void result once.

- **Prove the model is fully where you think it is** (`size_vram == size`) before quoting any speed.
- **Discard the first call**: it includes the load.
- **Change one thing at a time, under the same load.** A probe taken on a free card, compared against one taken on a
  busy card, measures occupancy, not your change.
- **Break the prompt cache between arms.** Put a per-arm tag first, or cold-load. Two arms sharing prompt text a
  minute apart once read +50% when the honest gain was +22%.
- **Count the answers that hit a limit** (a token cap, a timeout). If a real share did, the score measures the limit,
  so re-run with room.
- **Record every resource**: GPU clock, use, power and memory; CPU per core; free RAM and paging; disk queue. The
  resource you do not print is the one that gets missed.
- **Run it twice** before believing it, and report the spread.

In HAI a measurement is an order the factory runs (`docs/orders.md`). The lab's menu tools turn the results into the
placement table the head reads (`menu/`).

## 9. A card the lab never had

Sections 1 to 8 carry to any card: an Intel Arc, a newer AMD, anything else. The magnitudes do not. Start like this:

1. Find which llama.cpp backends your card has. Intel cards have SYCL and Vulkan; the lab has measured neither. Check
   your CPU and the engine build for AVX2 (section 6).
2. Pick the model from your VRAM and RAM (section 2) and its job (section 4). Find its largest resident window with
   `foundry/fit_probe.py`.
3. Copy an arsenal file, and walk the levers one at a time with the method in section 8.
4. Let your factory measure what you settle on. Your own rows then decide where HAI places work.

## 10. A card like one of the lab's

The lab measured on four machines:

| card | RAM | CPU | its research |
|---|---|---|---|
| AMD RX 7900 XTX, 24 GB (ROCm and Vulkan, Windows) | 64 GB DDR4-3600 | Ryzen 7 5800X | `docs/design/Qwen3.8-Flash-Next-on-Mothership.md`, `docs/reference/Machine-Mothership.md` |
| NVIDIA RTX 3070, 8 GB (CUDA, Windows) | 32 GB DDR4-4000 | Core i5-9600K, 6 cores | `docs/reference/Machine-BigHonker.md`, `docs/runbooks/RUNBOOK-Ollama-Host-Operations.md` |
| NVIDIA RTX 3070 Ti, 8 GB (CUDA, Windows), shared with a media server | 48 GB DDR4-3200 | Ryzen 7 5700G | `docs/reference/Machine-Softserve.md` |
| NVIDIA GTX 1060, 6 GB (CUDA, Linux) | 24 GB DDR3 | Core i7-3770 (AVX, no AVX2) | `docs/design/36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13.md`, `docs/reference/Fellowship-Order-Taker-Design.md` |

If your card is close to one of the first three, the head can place from that machine's measured rows until yours
exist. Each borrowed row is marked with the machine it came from, and your own rows win model by model as they land.
The two settings are in `docs/measure-your-own-box.md`, "Start from the lab's measurements". The fourth machine runs
its models through Tolkien and the lab engine, and the head does not borrow its rows yet. Its standard launch is in
`docs/reference/Fellowship-Order-Taker-Design.md`, "placement fields".

## 11. When one model is not the answer

`docs/design/34-Shape-Catalogue.md` prices every way the lab runs a job on one scorecard: one model, N attempts with a
checker picking the best, teams of roles, a larger lead model handing pieces to small ones. What held:

- More attempts buy quality only when something can check the answers. With hidden tests choosing, nine attempts by a
  1.5B coder rose from 0.42 to 0.92; majority voting added about nothing. Where nothing can check the answer, which is
  most of production, more attempts bought nothing.
- Small devices earn their keep beside a bigger model, on their own work, not inside its pass.
