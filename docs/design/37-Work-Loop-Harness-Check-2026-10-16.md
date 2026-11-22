# #37 work loop — adopt-before-building check of agent harnesses (2026-10-16)

**Why this exists.** [[HAI-Agent-Surface-Design]] § "How the agent does WORK" requires, before any new work loop is
built, a check of the CURRENT open-source agent harnesses against the lab's needs, recorded here. Done by the main
session by hand on 2026-10-16 (the owner: no subagents). Depth: three web searches plus the lab's own docs — a first pass,
not an audit; the verified/claimed split below says which claims rest on what.

## The needs (from the charter)

1. **Visible, durable work** — every unit of work is a Starfleet order or a call registered inside the request
   (`ollama_client.track`, doctrine 12e); HAI's runs checkpoint, park, suspend and resume through its own run ledger
   (`run_ledger.py`, #37 2026-10-16).
2. **The tests decide** — the system runs the checks and decides; the model's "done" is a claim (the
   self-engineering worker's verifier → scope gate → hollow-pass check → revert, `overnight_worker.work_item`).
3. **HAI's own tools and contracts** — `coding_*`, `host_admin_*`, `queue_compute`, … with their names and
   descriptions; identity/consent (`person_record.check_use`).
4. **One loop for measuring and for production** — A1 already measures HAI's production coding tools through
   Inspect AI (`agentic-hai` mode); a second production loop would price something production does not do.
Plus requirement 2's steering (take a message mid-task) and a different-model peer review of the ARTIFACT after green.

## What is out there (October 2026)

| harness | shape | local models | fits the needs? |
|---|---|---|---|
| **OpenCode** (MIT, very active) | terminal/desktop app with its own sessions | ollama, llama.cpp, LM Studio | owns its loop, session state and tool set → a second engine beside HAI (needs 1, 3, 4 fail); MCP could expose HAI's tools |
| **Goose** (Linux Foundation AAIF since 12-2025, Rust) | app + MCP extensions, general automation | yes | same as OpenCode; MCP-first is the interesting part |
| **OpenHands** (MIT) | Docker web UI, sandboxed autonomous runs | ollama endpoint | its own runtime and sandbox; a factory beside the factory |
| **Aider** | git-native CLI editor | yes | edit engine, not an orchestrator loop; no custom tool set |
| **Qwen Code** (Apache 2.0) | terminal agent tuned for Qwen coders | ollama | same app shape as OpenCode |
| **mini-swe-agent** (v2) | ~100-line loop, bash only, no tool calling | yes | deliberately tool-less; no place for HAI's tools |
| **smolagents** | library; agents write Python that calls tools | yes | a library (embeddable), but code-as-action replaces HAI's tool-call contracts |
| **Inspect AI `react()`** (UK AISI) | library agent: tool loop until `submit()`, optional extra `attempts` judged by the scorer, `on_continue` when the model stops calling tools | the lab already runs it on ollama (A1) | **already adopted for A1** (need 4); its "attempts judged by a scorer" is exactly "the tests decide"; compaction already adopted (`CompactionSummary`, A1, 10-15) |

## Recommendation

**Do not adopt an app harness as HAI's engine; reuse HAI's own loop and take Inspect's loop semantics, with a gap
list.** Every app harness (OpenCode, Goose, OpenHands, Qwen Code) owns the session, the state and the tools, so it
would be a second engine beside Kirk: its work would not be HAI's run ledger (no park/suspend/resume, need 1), its
tools would not be HAI's (need 3 — MCP could bridge that, at the cost of a second tool surface), and A1 would no longer
measure the loop production runs (need 4). The libraries split: mini-swe-agent has no tools at all, smolagents replaces
tool calls with generated code. Inspect's `react()` is the one already in the lab and it already encodes the charter's
core rule (the scorer decides; extra attempts on failure). So the work-loop redesign is the charter's own plan —
"from parts that already exist (reuse, not a new engine)": HAI's coding loop + the overnight worker's deterministic
verifier + `coding_review` on the diff — with Inspect's semantics as the reference for the attempt/verdict cycle and
A1 as its measurement.

**Gap list (per the doctrine row "adopt part of an engine"), who owns each in HAI's loop:** the verdict (tests run by
the system — the overnight verifier, to be reused in the interactive loop); extra attempts after a failed verdict
(Inspect `attempts` → a repair turn in the SAME loop); "model stopped calling tools" (`on_continue` → HAI's idle/
no-progress cut); context compaction (adopted on A1 10-15 — check the production loop has the same); peer review by a
DIFFERENT model of the diff + full test output (`coding_review`, decorrelated reviewer); steering mid-task (none of the
library loops provides it — HAI's own: a message queued into the run between turns, the same boundary park/suspend use).

## Open for the owner

- None blocking. If you want an app harness tried anyway (OpenCode is the most-used with ollama), it would be an A1
  arm on the same tasks — one variable — not a production switch.
