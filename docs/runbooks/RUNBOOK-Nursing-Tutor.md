# RUNBOOK — HAI Nursing Tutor (Riley / Morgan)



### Home AI — Nursing tutor (Riley / Morgan)

A per-user evidence-based-practice (EBP) research + study assistant for nursing users, layered on the
orchestrator. Lives in `E:\SSDShared\orchestrator-build` (`app.py`, `pubmed.py`, `config.py`, `tools.py`).
Project docs: `Z:\Workspace\riley-tutor\` (`REQUIREMENTS.md`, `blueprints/`, `bakeoff/`, `med-eval-seed.md`).

**Who:** a user is a "nursing user" when their RAG namespace matches `config.is_nursing_namespace(ns)` (any
`nursing*` namespace). Currently **Riley** (`nursing`) and **Morgan** (`nursing_morgan`).

**Per-user namespaces + corpora** (isolated; each indexes only its own `*.md`):
| User | Namespace | Container path | Host folder |
|------|-----------|----------------|-------------|
| Riley | `nursing` | `/usercorpora/riley` | `Z:\Workspace\riley-tutor\corpus\` |
| Morgan | `nursing_morgan` | `/usercorpora/morgan` | `Z:\Workspace\morgan-tutor\corpus\` |

Mounted **read-WRITE** into the orchestrator via `docker-compose.override.yml` (`/mnt/workspace/<name>-tutor/
corpus:/usercorpora/<name>:rw`; the `Workspace` SMB share → Sundae `/mnt/workspace`). These nursing corpus
mounts are **`:rw`** so the self-serve upload endpoint can write into them — the
underlying Workspace CIFS mount is `rw` and the container's root writes fine. RAG **auto-reloads** on file
change — no `/reload` needed. The homelab library (`nursing`≠`homelab`) never cross-contaminates.

**Modes** (nursing users pick one in the UI dropdown — it replaces the homelab Quick/Normal/Force-27B for
them; sent as `Q.mode`):
| Mode | Behavior |
|------|----------|
| **Reference** | brief, cited lookup |
| **Tutor** | teach / quiz, grounds facts |
| **EBP Research** (default) | synthesize + critically appraise + APA 7th, step-by-step |
| **Educator** | write NCLEX-style test items in Riley blueprint format: Bloom's level + NCLEX category per item, marked answers w/ rationale + a real `search_evidence` citation, ending in the blueprint tables. Brief encodes `blueprints/FORMAT-SPEC.md`. NOT a rename of EBP — a 4th, additive mode. |

**A mode is the brief; it routes nothing (since 2026-11-06).** On Auto, McCoy reads a nursing turn and Spock's weighing places it, as for any user, so a held Mothership overflows instead of queueing. A persona turn stays off the Fellowship rows, because those run a turn bare, without its brief or tools. An exact Model pin still wins.

Prompt = `NURSING_BASE` + the mode's `NURSING_MODE_BRIEFS` entry (assembled by `_nursing_system`). `/modes`
exposes `nursing_modes` + `nursing_users`; the UI's `rebuildModeOptions()` swaps the dropdown per user.

**Medical critique (MedGemma second opinion):** in a nursing Think-Really-Hard, a UI
checkbox **"Medical critique"** (shown only in a nursing sub-mode, nested under "Allow deep thinking" like
Deep Trace) adds an independent **`medgemma:27b`** clinical critique ON TOP of the normal qwen critique (a
2nd critic, not a replacement). It checks drug/dose/route/interaction/contraindication accuracy, lab ranges,
scope-of-practice, over-certainty, and fabricated citations, and is merged into the refinement
(`deliberation.merge_critiques`; medical findings tagged `source=medical`). Sent as `Q.medical_critique`;
runs in `_run_think_hard` gated on `nursing_mode and q.medical_critique and config.NURSING_CRITIQUE_ENABLED`.
Costs 1–2 cold 27B model swaps on Mothership (24GB can't hold both 27Bs) — nursing think-hard therefore gets
an extra pass + larger budget (`NURSING_THINK_HARD_MAX_PASSES`=4, `NURSING_THINK_HARD_BUDGET_SECONDS`=900).
Degrades safely (MedGemma absent / bad JSON → "unavailable", the qwen critique + first pass still stand).
Config: `NURSING_CRITIQUE_MODEL`(=`medgemma:27b`), `NURSING_CRITIQUE_ENABLED`(server kill-switch).

**Admin preview:** admins (`ADMIN_USERS`, i.e. the owner) get the 3 nursing modes *appended*
to their homelab modes (so the owner's dropdown shows Quick / Normal / Force-27B + EBP / Tutor / Reference).
Picking a nursing sub-mode runs the **full** nursing experience even though the admin's own namespace is
homelab: `_run_query_core` flips `nursing_mode` on when `q.mode in NURSING_MODES and q.user in ADMIN_USERS`
and re-grounds `ns` to `config.NURSING_PREVIEW_NAMESPACE` (default `nursing` = Riley corpus; blank
disables). `/modes` exposes `admin_users` to drive the UI append. Gated to admins so ordinary users can't
opt in. Verified live: the owner + Reference → BigHonker 8B with real PubMed/APA citations.

**Evidence / citations — `search_evidence` tool** (`pubmed.py`): NCBI PubMed E-utilities (esearch→esummary→
efetch), filtered to ≤5yr peer-reviewed + free-full-text, returns each hit's real **abstract** + a copy-ready
**APA 7th** reference + PMID/PMC link. Relaxation ladder (drop free-full-text → widen years → unrestricted
with an "OLDER than your window" warning); throttled with a 429 backoff (`NCBI_API_KEY` optional for 10/s).
Config: `PUBMED_EUTILS_URL`, `EVIDENCE_RECENCY_YEARS`(5), `EVIDENCE_MAX_RESULTS`(5), `EVIDENCE_ABSTRACT_CHARS`
(700). The model is told to cite ONLY what the tool returns — fabrication-proof by construction. CINAHL =
users send PDFs manually (paywalled).

**The user's own library — `search_docs` vs `get_document`:** `search_docs`
returns short relevant *excerpts* from the user's corpus (grounding); **`get_document(filename)`** (tools.py
`_get_document`) returns the **FULL text** of one of their own docs (cap `GET_DOC_MAX_CHARS`=12000; fuzzy
filename match; pick-list if ambiguous/missing; Credentials excluded). The model is told to use get_document
when asked to review/analyze/continue/quote a WHOLE document, and a forceful NURSING_BASE block tells it the
user has a PERSISTENT searchable library + durable memory — so it must NEVER give stock "I can't access your
files / I don't keep anything between sessions" denials, and must never ask the user to paste a document
that's already in their library (it fetches it).

**Add a new nursing user (recipe):** in `config.py` — add `Name:nursing_<name>` to the `USER_NAMESPACE`
default, a `nursing_<name>` entry to `RAG_NAMESPACES` (`/usercorpora/<name>`), and `Name` to `PROFILES`; create
the corpus folder `Z:\Workspace\<name>-tutor\corpus\`; add the `:rw` mount to `docker-compose.override.yml`;
rebuild + `up -d`. (`is_nursing_namespace` auto-recognizes `nursing*`, so modes/prompt/routing just work.)

**Add documents — upload, then add to the library on purpose (since 2026-11-14):** 📎 beside the message box (or
paste/drop) attaches any file; it is only STORED (original bytes in the corpus folder's private `.uploads\`), never
read or indexed unasked. To make a paper searchable for the tutor, tap the chip's **📚** before sending (or ask Doc
"add this to my library"): then `ingest.markdown_for` extracts the text (pypdf / python-docx), structures it to
markdown (section labels → `##` headers; title + provenance note), writes it into the user's `…-tutor\corpus\`
folder, and reloads the namespace. Doc can still read any stored file on request (`look_at_upload`). The old
source/draft/rubric chooser is gone from the page (the API's `library=true` + `intent` still accepts them).
Full behaviour: Reference/HAI-Control-Surface § "Attaching files and photos to a message"; the previous text is in
`Archive\upload-legacy-docs-superseded-20261114.md`.
Gated to any **writable personal namespace** (`_writable_corpus_dir` → `is_writable_namespace` —
nursing users Riley/Morgan AND general users like Jordan); `UPLOAD_MAX_BYTES`=20MB; the 📎 button
shows for everyone in `/modes` `upload_users`. Scanned/image PDFs (no text layer) are rejected with a clear "needs OCR" error.
Admins/others can still add docs by dropping `.md` straight into a corpus folder (RAG auto-reloads). Deps:
`pypdf`, `python-docx`, `python-multipart`. NOTE the chunker splits on markdown headers, so header-less
content collapses into one oversized chunk — the upload path auto-promotes section labels; for hand-dropped
`.md`, give it `#`/`##` headers. Each chunk is also prefixed with its document title (`rag.py`) so retrieval +
the model know which document a mid-document chunk belongs to.

**Retrieval — HYBRID BM25 + semantic:** `rag.py` fuses keyword (BM25) ranking with an
embedding cosine ranking via **Reciprocal Rank Fusion**, so paraphrase hits (his wording ≠ the query's) are
found, not just exact-token matches. Embeddings come from **`nomic-embed-text`** via Ollama `/api/embed` on
`EMBED_HOSTS` (BigHonker → the always-on Softserve), pulled on both; computed per chunk at load and **cached**
on disk (`/docs/State/embed-cache/<ns>.json`, keyed by chunk-text hash → only new chunks re-embed). Each
result carries `score` (BM25) + `sim` (cosine); the app keeps a chunk if `score>=RAG_MIN_SCORE` **or**
`sim>=EMBED_MIN_SIM` (0.55) — both absolute, so off-topic queries still ground in nothing. **Fail-soft:** if
no embed host is reachable (3s connect timeout) it silently falls back to BM25-only — identical to the old
behavior. Config: `EMBED_ENABLED` (kill-switch), `EMBED_MODEL`, `EMBED_HOSTS`, `EMBED_MIN_SIM`, `RRF_K`(60).
Dep: `numpy` (in-memory vectors; no vector DB — the corpus is small). Run the test suite with `EMBED_ENABLED=0`
to keep BM25 tests off the network. EMBED_HOSTS is **BigHonker-first** (Softserve is the Plex host — failover
only; its Ollama works but is never the primary). BigHonker's Ollama must be ≥ the version that has `/api/embed`
(0.30.8 confirmed working); older builds 404 and embeds fall back to Softserve, then BM25-only.

**RAG context cap — important:** the injected chunks are capped (`RAG_CHUNK_MAX_CHARS`=1600
per chunk, `RAG_CONTEXT_MAX_CHARS`=6000 total). WHY: the 8B's `num_ctx` is 8192; an over-budget prompt (system
+ tool schemas + big RAG chunks) fills the whole window → the model gets ~1 token to generate → a degenerate
1-token stub ("The"/"###", `done_reason='length'`, `prompt_tokens`≈8191). The trigger is corpus growth (an
uploaded journal PDF + drafts = big chunks). `ollama_client` logs a `SHORTANS` line (with done_reason +
prompt_tokens) whenever an answer comes back <40 chars — watch for it; if it recurs, the context budget or a
specific large chunk is the cause. (A larger `num_ctx` via KV-quant — backlog — would add headroom too.)

**Mode-switch tip:** for nursing users, `_mode_suggestion(nmode, text)` keyword-detects when
a request fits a DIFFERENT mode (EBP-intent: literature review/synthesis/PICOT/capstone; Educator-intent:
NCLEX/quiz/test items/blueprint) and appends a one-line "**Tip:** switch to X mode" to the answer. Deterministic
(orchestrator-side, `diag["mode_hint"]` → applied in `_run_query`), NOT a model nudge — the 8B won't reliably
self-suggest. Mode descriptions (`MODE_INFO`) are plain "use this when…" wording, model-free.

**Per-user memory:** conversation transcript + a durable compacted summary per profile (`/docs/State/history/
<user>.json` + `.summary.json`); the summary preserves preferences/corrections. NOTE: the chat **"New chat"**
button (`/history/reset`) deletes BOTH — but it does NOT touch the durable memory below.

**Durable per-user memory — cross-session continuity:** `usermemory.py` stores persistent
facts the assistant should ALWAYS know about a user (project topic/PICOT, due dates, preferences) at
`/docs/State/memory/<user>.md` (= `Z:\Documentation\State\memory\<user>.md`, editable by hand). It **survives
"New chat"** (separate file) and is **injected into every query** (even ephemeral). Populated by the
`remember` tool (the model calls it when the user states a durable fact / says "remember …"; ungated, all
users). View `GET /memory?user=`; clear `POST /memory/reset?user=` — exposed as the UI **"Forget memory"**
button (distinct from "New chat"). Distinct from the admin-set `roster` profile note (static bio). Riley
is seeded with his capstone project. Cap `USERMEMORY_MAX_CHARS`=4000 (keeps most-recent). Not a RAG corpus
dir → never indexed, injected directly.

**Voice-matching:** `voiceprofile.py` stores a per-user **writing-voice sample** (an
excerpt of the user's OWN prose) at `/docs/State/voice/<user>.md` (cap `VOICE_SAMPLE_MAX_CHARS`=1200,
editable by hand). **Set on purpose** (since 2026-11-14): the attachment chip's **✍** toggle (`POST /voice/sample`)
or asking Doc ("use my paper as my writing sample", `use_as_writing_sample`), from any upload or library document,
through `tools.set_writing_sample`. When ANY user with a sample has a **drafting intent** (`_DRAFTING_INTENT`
regex; nursing-only until 2026-11-14, the owner: "I don't think voice profile should be locked to nursing"), the
orchestrator injects the sample + a "match the STYLE only, never reuse its content" instruction (nursing adds "keep
clinical facts accurate and cited") — gated to drafting so it's not in every prompt. `GET /voice?user=`,
`POST /voice/reset?user=`.
Riley is seeded from his capstone prose.

---

**Outcome signal (the owner, 2026-09-12, not previously recorded):** one of the nursing pilot users reported A grades on the coursework the tutor supported. Anecdotal, one student, no formal measure; recorded so the pilot has at least one outcome on file. The portfolio pilots page states it without a name.
