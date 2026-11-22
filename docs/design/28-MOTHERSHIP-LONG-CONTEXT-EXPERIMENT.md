# Mothership long-context experiment — "more world in the model's head" vs "smarter model"

**Filed 2026-09-05** by secondary chit-chat session (Claude 4.7) at the owner's ask. Not scheduled — a
fun experiment for a free evening. Adjacent to but distinct from the [gaming-mode-brain entry in
PROJECT-BACKLOG.md](../Reference/PROJECT-BACKLOG.md) (2026-08-29): that one asks *can the 14B
substitute for the 27B when the card is game-busy*; this one asks *can a smaller-model-bigger-context
setup beat the 27B for lab work by seeing the whole shape of a project at once*.

## The question

For lab tasks specifically (planning, debugging across files, understanding "the shape" of a
project), does **more model** or **more world in the model's head at once** matter more? We
routinely feel a step-change when Claude/Codex/ChatGPT can see enough of the lab to reason about
its structure; the local stack usually gets a handful of RAG chunks. Worth measuring whether
14B-with-huge-context beats 27B-with-moderate-context on Mothership's 24 GB card.

## KV math (Qwen3 dense, GQA: 8 KV heads, 128 head-dim)

Per-token KV cost:

| model | layers | fp16 | q8 | q4 |
|---|---|---|---|---|
| 8B/9B (`qwen3-vl:8b`, `qwen3.5:9b`) | 36 | 72 KB | 36 KB | 18 KB |
| 14B (`qwen3:14b`) | 40 | 80 KB | 40 KB | 20 KB |
| 27B (`qwen3.6:27b`) | 64 | 131 KB | 66 KB | 33 KB |

Weight sizes (Q4_K_M, Mothership inventory 2026-08-29): 9B ≈ 5 GB · 14B = 9.3 GB · 27B = 17.4 GB.

**All-VRAM caps on 24 GB (weights + KV + ~2 GB framework scratch):**

| model | q4 KV | q8 KV |
|---|---|---|
| 9B | ~900k ctx | ~500k ctx |
| 14B | ~600k ctx | ~300k ctx |
| 27B | ~150k ctx | ~75k ctx |

Numbers are ceilings — needle-in-haystack recall will fall off well before these limits
(qwen dense degrades noticeably past ~40–60k middle-third recall).

## Prefill is the wall, not VRAM

Prefill is O(n²) — every input token attends to every other. It's massively parallel (compute-bound
on GPU), but the total work grows quadratically. Rough time-to-first-token on 14B fully-resident,
extrapolated from flux2-doc per-token cost (**estimates, need measured baselines**):

| ctx | est. TTFT (all VRAM) |
|---|---|
| 50k | ~25 s |
| 200k | ~100–130 s |
| 400k | ~4–7 min |
| 800k | ~20+ min |

**Generation is memory-bandwidth-bound.** Every emitted token re-reads the whole KV. VRAM
bandwidth ~800 GB/s on the 7900 XTX vs system RAM ~50 GB/s vs PCIe ~30 GB/s. **If the KV spills
to RAM, every generated token pays that bus penalty** — a 200k KV half-in-RAM drops 14B gen from
~40 tok/s to ~5 tok/s. Half-in-RAM 500k → ~1 tok/s. That's the wall people don't warn about.

## The saving grace — prompt caching

ollama caches the KV between calls with the same prefix. A frozen preamble ("here's the runbook
+ the project I'm working on") pays prefill **once**; every followup only prefills the new user
turn against the giant cached KV. That is the workflow shape worth actually testing.

## The workflow hypothesis (the owner's, 2026-09-05)

**"Project mode" for HAI:** at session start, load a frozen preamble of ~50–150k tokens containing
`RUNBOOK.md` + the active project's LLM-Project scope + relevant recent journals. Pay the prefill
tax once (2–5 min). Then chat all evening against the cached KV — model has the whole shape of the
work, no per-turn RAG lottery.

**Where it wins vs current RAG:** cross-references (a decision in Journal-08-15 conflicting with
a Reference doc — chunk-level RAG can't spot that because the two never land in the same window),
full-shape reasoning, no retrieval misses on ambiguous queries.

**Where it bites:**
- **Cache stability is everything.** Prefix must be byte-identical across turns. Any timestamp,
  variable tool-list, or dynamic RAG injection at the *top* of the prompt kills the cache and you
  re-prefill every call → zero benefit. Needs a mode where the frozen prefix is sacred.
- **Middle-third recall dip.** A 150k preload ≠ 150k of perfect recall. Beginning and end stay
  sharp; middle gets fuzzy. So "sees the shape" is real, "remembers every fact" is not.
- **Competes with existing RAG stack.** `rag.py` already retrieves against Runbook + Reference +
  LLM-Project per turn. This preloads them statically instead. Not a replacement — a different
  mode for a different workload (deep-project vs quick-lookup).
- **Model eviction resets everything.** If another model loads (BigHonker fails over, a caption
  job runs), the pinned KV is evicted and next turn re-prefills. Needs long `keep_alive` and
  probably keeping this model off the fleet scheduler while the session is active.

**Interesting hybrid:** frozen prefix = stable stuff (runbook, project scope, roadmap); dynamic
RAG still injects recent journals / retrieval hits at the *end* of the prompt where the cache
tolerates changes. Best of both, frozen half stays cache-valid.

## Practical caps (my best guess before measurement)

The sweet-spot where the experiment is usable, not a party trick:

- **14B @ ~200k q8 KV or ~300–400k q4 KV**, all VRAM-resident, ~2–5 min one-time prefill, fast
  cached-followup gen.
- **9B @ ~500k q4 KV** if you want to push it — recall will be the limit, not memory.
- **27B stays at ~64k** (current setup) or push to ~150k q4 KV if you want to compare like-for-like
  on wall time.

Anything past those with spill is a demo, not a tool.

## Cheapest useful test (recipe, ~one evening)

1. **Baseline first** — actually measure Mothership's prefill tok/s and gen tok/s at each of
   {50k, 100k, 200k} on 14B with q4 KV cache. We do not have these numbers; extrapolating from
   flux2 is not a measurement.
2. **Pick a real project** the owner is working on that week.
3. Build a static preamble (~80–120k tokens): `RUNBOOK.md` + that project's LLM-Project doc(s) +
   last 2 weeks of journals filtered to that project. Freeze it.
4. Load it into 14B on Mothership with `keep_alive=8h`, `OLLAMA_KV_CACHE_TYPE=q4_0`,
   `OLLAMA_NUM_PARALLEL=1`. Pay the prefill once.
5. Chat against it for a real working session. Compare feel + latency + answer quality vs current
   RAG-per-turn HAI on the same project.
6. Optional: same test at 27B with a smaller preamble (~60k) for like-for-like wall-time
   comparison.

## Prerequisites before running

- [ ] Confirm current ollama build on Mothership supports `OLLAMA_KV_CACHE_TYPE=q4_0` (it should,
      but verify — a silent fp16 fallback would blow the KV budget by 4×).
- [ ] Confirm flash attention status on Mothership's ROCm build (halves prefill compute if
      enabled; check `OLLAMA_FLASH_ATTENTION` and whether the ROCm/hipBLAS build wired it in for
      RDNA3).
- [ ] Measured baseline prefill + gen rates at 50/100/200k on 14B. Nothing here means anything
      without them.
- [ ] Pin the model off the fleet scheduler for the duration (a caption job evicting the KV
      wastes the whole test).

## What this is NOT

- Not a proposal to change HAI's default routing.
- Not a proposal to retire RAG — this and RAG do different jobs.
- Not urgent. Filed as a fun evening experiment.

## Cross-references

- `PROJECT-BACKLOG.md` §2026-08-29 gaming-mode-brain (14B on Mothership, gaming-VRAM constraint,
  measured Civ 6 numbers)
- Doctrine: `feedback-model-residency-arithmetic` (silent CPU offload trap)
- Doctrine: `gotcha-verify-gpu-health-before-benchmarking` (`size_vram==size` before any timing
  claim)
- `LLM-Project/Flux2-32B-QLoRA-AMD-ROCm-Windows.md` — has the per-token KV cost data we're
  extrapolating from
