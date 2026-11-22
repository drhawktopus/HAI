# Phase C — Build the Orchestrator (build guide)

**Last updated:** 2026-06-13
**Decisions locked:** Hybrid routing (code rules + Qwen2.5 tool-calling brain) · BM25 keyword RAG ·
FastAPI in Docker on SundaeStackarr · brain model `qwen2.5:7b` · helper model `llama3.1:8b` (Qwen
delegates sub-tasks to it via `ask_helper`) · escalate to Claude Code for real coding.

This is a build-it-yourself guide. It gives you the architecture, the file layout, the non-obvious
code, and exact infra + test steps. Ping Claude only for the hard bits you get stuck on.

> **Historical build guide (June 2026), kept for provenance.** The orchestrator has evolved since;
> the model names below (`qwen2.5:7b`, `llama3.1:8b`) are period-accurate to this build, not current.
> For the CURRENT architecture see [[HAI-Core-Architecture-API-Contracts]]; for current
> model placement + context (`num_ctx`) tuning see [[HAI-LLM-Execution-Context-Mechanics]].

---

## Pages in this document

- **LLM Orchestrator - High-Level Architecture & Roadmap** — see [[02-High-Level-Architecture-Roadmap]]
- **LLM Orchestrator - Host Infrastructure & Routing Config** — see [[02-Host-Infrastructure-Routing-Config]]
- **LLM Orchestrator - Prerequisites & Environment Setup** — see [[02-Prerequisites-Environment-Setup]]
- **LLM Orchestrator - Code Implementation & Docker Packaging** — see [[02-Code-Implementation-Docker-Packaging]]
- **LLM Orchestrator - Testing & Handoff Strategy** — see [[02-Testing-Handoff-Strategy]]
