# HAI Orchestrator — Harness Internals (read-as-truth reference)

**Purpose:** ground-truth notes on how HAI's orchestrator/coding harness actually works, so we don't
re-derive things we built.

> ⚠ **AGE WARNING (added 2026-08-14).** The bulk of this file was **verified live 2026-06-29** and is
> ~7 weeks old. Two sections were found stale on 2026-08-14 and are corrected inline (the Kirk/Spock
> names; `HEAVY_NUM_CTX`). **Treat every value here as a HISTORICAL claim: read the doc for the
> DESIGN and the WHY, then verify the current value against live config before repeating it.**
> the owner's rule, 2026-08-14: *docs first, then live state, and you must be able to explain why live
> differs from docs — stale docs get corrected, a bad/rogue deployment gets flagged LOUDLY.*
> Sections NOT re-verified on 2026-08-14 are marked by their absence of a correction note.

Verified live 2026-06-29 (Claude) during Project #12 work. Update this when the
harness changes. Canonical source = Sundae `/opt/arrstack/orchestrator/` (+ `coding-runner/`); the deploy
broker lives in the external `hostops` container (`hostops_deploy.py`).

## Pages in this document

- **HAI Orchestrator - Core Architecture & API Contracts** — see [[HAI-Core-Architecture-API-Contracts]]
- **HAI Orchestrator - LLM Execution & Context Mechanics** — see [[HAI-LLM-Execution-Context-Mechanics]]
- **HAI Orchestrator - Autonomous Behavior & Self-Engineering** — see [[HAI-Autonomous-Behavior-Self-Engineering]]
- **HAI Orchestrator - Deployment Lifecycle & Sidecars** — see [[HAI-Deployment-Lifecycle-Sidecars]]
- **HAI Orchestrator - Operations, Testing & Known Issues** — see [[HAI-Operations-Testing-Known-Issues]]
