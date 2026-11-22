# LLM Orchestrator - Host Infrastructure & Routing Config



## Host routing, models & escalation (LOCKED 2026-06-14)

### Model placement (current models, June 2026)
| Host | GPU / VRAM | Role | Model |
|---|---|---|---|
| **BigHonker** (<bighonker-address>) | RTX 3070 / 8 GB, idle | **PRIMARY brain** | `qwen3:8b` (+ `medgemma:4b` ⁑) |
| **Softserve** (<softserve-address>) | RTX 3070 Ti / 8 GB, shares w/ Plex | **failover brain** | `qwen3:8b` (+ `medgemma:4b` ⁑) |
| **Mothership** (<mothership-address>) | RTX 7900 XTX / 24 GB, WoL | heavyweight tier | `qwen3.6:27b`, `qwen3:14b` (+ `medgemma:27b` ⁑) |
> ⁑ **`medgemma`** (Google Gemma-3 medical, multimodal text+image) staged **2026-06-19** for the
> **Riley nursing-tutor feature (backlog #10)** — `medgemma:4b` (3.1 GB) on the 8 GB cards, `medgemma:27b`
> (16.2 GB) on Mothership. **NOT yet wired into routing** — pending a MedGemma-vs-Qwen3 bake-off (medical
> fine-tunes don't always beat strong generalists; all clinical facts must be retrieval-grounded regardless).
> Ollama only packages MedGemma **1.0** (May 2025); SOTA **1.5** (Jan 2026, ~91% MedQA) not yet on Ollama.
> Eval set: `Z:\Workspace\riley-tutor\med-eval-seed.md`.
> Brain runs `qwen3:8b` in **non-thinking** mode (fast routing). Pick the current-best ~8B on
> ollama.com/library at build time; `qwen3:8b` is the known-good baseline. Heavyweight = current-best
> ~27–32B that fits 24 GB Q4 (`qwen3.6:27b`).
>
> The brain is a **reasoning agent**, not a router: it reads RAG, decides/sequences tool calls, runs a
> multi-turn tool loop, and synthesizes the answer. So context budget matters (system+tools ~1K + RAG
> chunks ~2–6K + tool results ~1–2K each + accumulating history).
>
> ⚠️ **CRITICAL — tune `num_ctx` on the 8 GB cards.** qwen3:8b's *default* context is **40K**, which
> balloons the footprint to ~11 GB → spills to CPU (verified on BigHonker: 46%/54% CPU/GPU). The
> orchestrator MUST set `options.num_ctx`. **8192 is the safe floor** (verified ~6.2 GB, **100% GPU**,
> ~1 GB headroom) — *not* a ceiling. Push to the **max that still holds 100% GPU per card** (BigHonker,
> dedicated → likely ~12–16K; Softserve, shares w/ Plex → keep lower, ~8–12K). Verify with `ollama ps`
> (PROCESSOR must read 100% GPU). Keep RAG injection lean + trim tool results to protect the budget;
> genuinely context-heavy reasoning is an **escalation trigger** (→ Mothership 27B / Claude), not
> something to force onto the 8 GB brain. (Mothership's 24 GB has ample context room; verified
> `qwen3.6:27b` @ num_ctx=32768 = 100% GPU.)
>
> 🟥 **Mothership is AMD (RX 7900 XTX, ROCm via AMD AI Bundle), not NVIDIA.** GPU checks there use
> `ollama ps` (vendor-neutral) — **`nvidia-smi` does NOT work**; AMD uses `rocm-smi`/`amd-smi`. So
> `hosts.py`'s load-check can use Glances only where it runs — **Softserve and Mothership;
> BigHonker runs no Glances — not installed, never a decision of the owner's (2026-10-25)** (`scheduler._glances` treats its absence as unknown and
> falls back to other sources) — and **needs an AMD-aware signal for Mothership** (amd-smi, or
> simplest: poll Ollama `/api/ps` to see if a model is loaded/busy). Don't assume a single
> Glances-GPU path covers all three hosts.

### Routing priority: `BigHonker → Softserve → Mothership → Claude`
The flip from the original (Softserve-first) is deliberate: **keep Softserve's GPU free for Plex.**
BigHonker's 3070 ≈ Softserve's 3070 Ti for an 8B model, so no perf loss. Same load-aware `pick_host()`,
just reordered, with two rules that make "BigHonker usually warm" true:
1. **Keep BigHonker's PC awake during active hours** (Windows power plan) so it's reachable with no WoL
   delay — but **let the model UNLOAD when idle** (short `keep_alive`, ~5 min) so Avery keeps her full
   GPU for web/YouTube. It's a *shared* machine, so we don't pin the model resident 24/7. Next query
   after idle cold-loads ~3–5 s, covered by the "🧢 thinking cap…" status frame. (We kept the capable
   8B over 4B precisely because it isn't resident all the time.) Overnight the PC may sleep → late
   queries take the WoL path.
2. **Yield to Avery.** BigHonker runs no Glances — check its GPU state via the box's own
   nvidia-smi push (`POST /fleet/gpu-state`, read back through `scheduler.gpu_health()`); if her
   usage (esp. Lightroom = GPU) pushes load up, fail over to Softserve so we don't lag her
   machine. So it's "BigHonker first *unless she's using it*."
- Softserve is used for the brain **only** when BigHonker is asleep/busy AND Softserve has Plex
  headroom (its Glances shows GPU free-VRAM/encoder OK). If Plex is also maxed, prefer Mothership or
  the wait-with-status path over fighting Plex.

### Escalation — Claude is EXPLICIT-ONLY (hard rule)
- **Claude (Tier 2) is invoked ONLY when the user explicitly asks** — the `claude:` / "ask claude"
  rule pre-filter. It is **never** auto-escalated. This is the cost guardrail (Claude spends the owner's
  plan usage).
- The `escalate_to_claude` tool does **not** call Claude. When a local model judges a task beyond it,
  the tool returns a **suggestion**, and the orchestrator replies to the user with an *offer*
  ("this looks beyond me locally — reply `claude` to send it") and stops. Claude runs only after the
  user's explicit confirmation.
- Mothership's 27B is the local "reinforcement" the system reaches for on its own; Claude is always a
  human-gated step.

### Status frames (the "thinking cap" UX)
The orchestrator streams **status frames** before the answer so slow paths feel responsive (the owner is
fine waiting *with feedback*). Only show a wait message when the path will take >~2–3 s; otherwise just
stream the answer.
```
{"type":"status","msg":"🧢 putting on my thinking cap…"}     # cold model load
{"type":"status","msg":"📟 waking BigHonker…"}               # WoL (~30–90 s)
{"type":"status","msg":"🏋️ calling in the heavyweight (Qwen3.6 27B)…"}  # → Mothership
{"type":"status","msg":"📞 phoning a friend (Claude)…"}       # only after user said 'claude'
{"type":"token","text":"…"}                                  # then the streamed answer
```
Trivial over SSE/websocket on the future web page; for HA Assist, fire a "working on it" announcement.

---
