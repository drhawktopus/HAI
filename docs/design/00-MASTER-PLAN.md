# Local-LLM Orchestrator Project — Master Plan

**Last updated:** 2026-06-15
**Goal:** Cut down on paid Claude usage by routing everyday queries and coding tasks through
**local LLMs first**, escalating to Claude Code only for genuinely complex work.

> This is the human-readable plan. The companion file **[[01-DIY-RUNBOOK]]** has the exact
> step-by-step commands you can run yourself (especially useful when Claude hits a session limit).
> Claude's own working copy of this plan lives at
> `C:\Users\<user>\.claude\plans\lazy-hatching-glacier.md`.

## Current status

This file records the original design, but the system is now substantially deployed. For the
authoritative current state and next work, use CURRENT-PROJECTS.

- Phase C is deployed: the Sundae FastAPI orchestrator, three-host routing, BM25 RAG, persistent
  profile history, streamed status, safe Markdown, and context compaction are live.
- Phase D is deployed: the `home_ai_conversation` Home Assistant Assist integration is verified
  for the owner, Avery, Jordan, and Riley.
- Phase E is deployed: live HA state/control tools are available. Room-wide commands use
  domain-scoped area targeting, such as `area="office", domain="light"`, so unrelated devices
  in the area are not affected.
- RAG freshness was repaired on 2026-06-15 by updating the indexed status documents and reloading
  the corpus.
- Remaining work is validation and hardening: test a single-device command and a routine Plex
  question, strengthen compaction against invented technical details, and build the Claude
  executor only if the owner explicitly chooses to proceed.

---

## The vision (how it should work)

You ask a question or give a task (by voice via Home Assistant, or by text). An **orchestrator**
decides who handles it:

```
Your query
   │
   ▼
ORCHESTRATOR  (small always-on service on SundaeStackarr)
   ├─ Home Assistant device command?  → HA built-in intents (Tier 0, instant, free)
   ├─ General question / simple task?  → a LOCAL LLM (Tier 1, free)
   │     picks the least-busy GPU machine:
   │        1. Softserve  (skip if Plex is hammering the GPU)
   │        2. Mothership (wake-on-LAN if asleep; skip if you're gaming)
   │        3. BigHonker  (wake-on-LAN; skip if Avery using it)
   │     …and feeds it relevant facts from the doc library (RAG)
   └─ Complex coding / infra task?     → Claude Code (Tier 2, uses your plan, on purpose only)
```

The point: **most things never reach Claude**, so you stop burning the paid allowance on stuff a
local model can handle.

---

## The machines (GPU hosts for Tier 1)

| Host | GPU | VRAM | Notes |
|------|-----|------|-------|
| **BigHonker** (<bighonker-address>) | RTX 3070 | 8 GB | **Primary brain → `qwen3:8b`** (num_ctx 8192). Avery PC; SSH ✅; short keep_alive so her GPU stays free. MAC `<mac-address>` (WoL). |
| **Softserve** (<softserve-address>) | RTX 3070 Ti | 8 GB | **Failover brain → `qwen3:8b`** (num_ctx 8192). Also Plex + Hyper-V host. Ollama **native Windows (NOT WSL)**. |
| **Mothership** (<mothership-address>) | **AMD** RX 7900 XTX | 24 GB | **Heavyweight → `qwen3.6:27b`** (num_ctx 32768). ROCm via AMD AI Bundle. SSH ✅; WoL; don't interrupt gaming. |

Model sizing rule: **8 GB cards → use 8–9B models** (e.g. `llama3.1:8b`) so the whole model fits
on the GPU. Bigger models (14B, 27B) spill to CPU and crawl. Mothership's 24 GB could run bigger
models later if we want.

---

## Phase status

| Phase | What | Status |
|-------|------|--------|
| **A** | Documentation library for local LLMs (`Z:\Documentation\`) | ✅ DONE (2026-06-13) |
| **B** | Right-size the Tier-1 model on Softserve (`llama3.1:8b`, 100% GPU) | ✅ DONE (2026-06-13) |
| **C** | Orchestrator (routing + RAG), multi-host failover, and Wake-on-LAN | ✅ DEPLOYED |
| **D** | Wire the orchestrator into Home Assistant Assist | ✅ DEPLOYED + VERIFIED (2026-06-15) |
| **E** | Live Home Assistant device-state and control tools | ✅ DEPLOYED + VERIFIED (2026-06-15) |
| **F** | Claude Code escalation path for hard tasks | Deferred; explicit opt-in only |

### What "done" looks like for A and B
- **A:** `Z:\Documentation\` has `Reference\` (5 consolidated docs the local LLM reads),
  `Reference\Credentials\` (secrets, kept OUT of normal context until a task needs them), and
  `Notes\` (where the local LLM writes a log when it completes a task on its own).
- **B:** Ollama runs natively on Windows on the GPU hosts. BigHonker and Softserve use
  `qwen3:8b`; Mothership uses `qwen3.6:27b`. All were verified at 100% GPU with bounded context.

---

## What you can do yourself vs. what needs Claude

**You can do solo (see [[01-DIY-RUNBOOK]]):**
- Finish the SSH server setup on BigHonker
- Install/manage Ollama models on any host (pull, remove, test GPU placement)
- Install Ollama + the Tier-1 model on Mothership and BigHonker
- Install the load-monitor (Glances) on Mothership (as built: BigHonker runs none — not installed, never a decision of the owner's (2026-10-25); its RAM comes from its own minute report)
- Test Wake-on-LAN to BigHonker
- Run all the verification checks

**Needs deliberate implementation work:**
- Compaction-summary hardening against invented entity IDs or technical claims.
- Optional Phase F Claude executor, only when the owner explicitly chooses to build it.

---

## Key decisions already made (so we don't re-litigate)

- **No paid Anthropic API in Home Assistant.** The HA "Claude" Assist pipeline uses pay-per-token
  API credits (NOT covered by your Claude subscription) — you're disabling it. Claude Code
  escalation (Tier 2) uses your normal Claude plan allowance instead.
- **Orchestrator runs in Docker on SundaeStackarr** (fits the existing always-on stack; keeps it
  off the GPU hosts).
- **Tier-1 model = `llama3.1:8b`** on the 8 GB cards. **Orchestrator model = likely Qwen** (revisit).
- **Docs:** `Z:\Homelab Docs\` stays Claude's memory mirror (don't hand-edit). `Z:\Documentation\`
  is the local-LLM library. They are separate on purpose.
- **No Claude-powered nightly doc sync** — the library is curated periodically; once the local
  orchestrator exists, the local model maintains it for free.
- **Tailnet access goes through the orchestrator, never raw Ollama.** Goal: anyone on the tailnet can
  talk to the system via a future web page — but that page is a UI **on the orchestrator** (with
  auth/rate-limit), which mediates the models. Ollama's 11434 has no auth, so it stays LAN-locked to
  the orchestrator host (and is exposed on Softserve's Tailscale IP by mirrored WSL networking, so the
  firewall allow-list is mandatory). Full networking logic + trust boundaries: [[02-PHASE-C-BUILD]]
  §1b / §1b-NET.
