# LLM Orchestrator - High-Level Architecture & Roadmap



## Architecture & rationale (locked)

| Piece | Role | Runs on |
|------|------|---------|
| HA Assist | voice/text front-end | HAOS VM (Phase D) |
| **Decider** (orchestrator) | rules + dispatch + RAG fetch + logging; **runs no model** | Sundae (Docker, always-on, CPU-only) |
| **Qwen2.5:7b** | the brain — decides & calls tools | least-busy GPU host |
| **llama3.1:8b** | the helper — Qwen delegates sub-tasks via `ask_helper` | ideally a *different* GPU host |
| **Claude Code** | real escalation for hard/coding work | Softserve, headless, uses the owner's plan |
| Doc library | RAG (`Reference\`), gated `Credentials\`, `Notes\` | Big Shared, mounted on Sundae |

**Flow:** `query → decider (rules) → [claude: / HA-command early-exits] → seed RAG + pick host →
Qwen brain (tools: search_docs · get_credential · ask_helper→llama · escalate_to_claude) → answer → log`.

**Why this shape:**
- **Decider on Sundae, not a GPU box** — it's the always-on front door (HA calls it anytime). If it
  lived on a sleeping machine the assistant would vanish. It runs no model, so it's CPU-trivial.
- **Qwen is the brain, not llama** — the design leans on tool-calling, and Qwen2.5 is markedly better
  at *deciding when* to use tools. Fits a tool-driven, not-very-chatty assistant.
- **llama is a lateral helper, not a higher tier** — llama3.1:8b ≈ qwen2.5:7b in raw ability, so it's
  a peer worker Qwen delegates to (parallelism across machines, specialization, second opinion), NOT
  a capability step up. **Claude is the only real step up.**
- **Helper wants a 2nd GPU host** — qwen (~4.7 GB) + llama (~4.9 GB) + context won't both fit on one
  8 GB card; they'd thrash. So `ask_helper` pays off once BigHonker/Mothership are in the pool. On a
  single host, Qwen just answers directly (skip the helper).
- **Docker, not k8s** — the GPU boxes are sometimes-off Windows gaming PCs (WoL) — a terrible fit for
  k8s's always-on/homogeneous/Linux assumptions. The orchestrator IS the purpose-built micro-scheduler;
  Ollama is the per-host model server. That two-layer combo gives cluster *behavior* without cluster
  *machinery*.

---

## 0. What you're building

A small FastAPI service (Docker container on SundaeStackarr) that exposes `POST /query`. For each
query it:

1. **Rule pre-filter (cheap, code):** explicit `claude:`/`ask claude` prefix → escalate path;
   obvious HA device phrasing → return a "route to HA" signal (HA wiring is Phase D). Everything
   else → the Qwen brain.
2. **Seed RAG (BM25):** keyword-match the query against the `Z:\Documentation\Reference` corpus,
   grab the top few chunks, put them in the prompt.
3. **Qwen brain (tool-calling):** call `qwen2.5:7b` on the least-busy GPU host. Qwen can call tools:
   `search_docs(query)` (more RAG), `get_credential(name)` (gated secrets — explicit only),
   `ask_helper(subtask)` (delegate to llama3.1:8b on another host), and `escalate_to_claude(reason)`.
4. **Answer + log:** return the answer; if it was handled locally, append a one-line note to
   `Z:\Documentation\Notes\YYYY-MM-DD-*.md`.

```
POST /query {"text": "..."}
   │
   ├─ rule pre-filter ─→ [escalate] or [route-to-HA]   (early return)
   │
   ├─ BM25 seed retrieval over Reference\
   │
   ├─ pick GPU host by Glances load  (v1: Softserve only)
   │
   └─ Qwen chat (tools: search_docs / get_credential / escalate_to_claude)
         └─ answer  ──→ log to Notes\ , return JSON
```

---

## 7. Known decisions / guardrails
- v1 = Softserve only for the model; Mothership/BigHonker + WoL failover are Phase E (host list in
  `hosts.py` is already shaped for it).
- Keep `keep_alive` modest (5m) so the model unloads when Plex needs the GPU.
- Never expose Ollama (11434) beyond the firewall allow-list; never port-forward it to the internet.
- Credentials: only via the `get_credential` tool, and every access is logged to Notes.
