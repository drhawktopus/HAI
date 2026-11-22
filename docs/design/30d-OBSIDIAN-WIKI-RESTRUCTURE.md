# 30d — Obsidian wiki restructure: split map, design rules, and phases

the owner's ask (#30): *"I'd like it to be sort of Wikipedia or Knowledgebase style rather than
segmented documents."* The staged cleanup (stages 0-10, closed 2026-08-22) was the groundwork;
this document owns the restructure itself. Vault: Obsidian 1.13.7 over `Z:\Documentation`
(wikilinks on, `State/`/`Datasets/` excluded from its index).

## Design rules (each is a constraint, not a preference)

1. **Split pages stay FLAT in `Reference\` / `LLM-Project\` / `Runbook\`.** `rag.py` globs
   `*.md` non-recursively in exactly those three directories (subfolders deliberately excluded —
   that is what keeps `Credentials\` out). Flat placement means the restructure needs zero
   indexer changes and cannot regress what is indexed.
2. **One H1 per page; the H1 is the retrieval lever.** `rag.py` prepends the doc's FIRST H1 to
   every chunk, so a second topic under the same roof is served with the wrong label. This is
   the mechanical reason multi-H1 docs split first.
3. **A doc whose extra H1s are PHASES of one topic does not split.** The flash runbook's
   PHASE 1-6 headings are one procedure; every chunk labeled with the doc title is labeled
   correctly. Cosmetic H1→H2 demotion optional, never a split.
4. **Status/tracker files are exempt** (`CURRENT-PROJECTS`, `PROJECT-BACKLOG`): they are rolling
   registers, not knowledge pages; wiki-splitting them would fight their function.
5. **Wikilink conversion comes AFTER splits** — link targets need the post-split page names to
   exist first (sequencing already recorded in #30). Cross-refs stay `see <FILE> §<heading>`
   until then.
6. **Every split is a migration**: enumerate consumers of the old doc name (docs, memory
   pointers, code comments, HAI seed-grounding citations) before moving a byte, grep again
   after, and again the next day. The parent doc never vanishes — it becomes a stub pointing
   at its children, or the domain entry page, so inbound references and RAG citations keep
   landing.
7. **Retrieval is judged by known-answer probes, not a before/after delta.** the owner waived the
   before-baseline (2026-08-22: benchmark-the-bad-stuff declined). After each domain's
   restructure: a fixed probe set of real questions must retrieve the right page (e.g. "how do
   I elevate on Softserve", "tablet USB persistence", "which model does triage rewriting").
8. **A restructure is not DONE until its docs are verified CURRENT.** Findability (a probe
   retrieves the page) is not currency (the page is true now). Before a domain's restructure is
   called done, the VERIFY lane (`triage_verify`) must have run on its docs and every present-tense
   claim it flags must be resolved — a CURRENT verdict or a primary-source disposition. This is the
   gap that shipped a findable-but-stale cameras wiki; the code gate is
   `library_restructure.verify_gate`, and the repeatable method is
   [[Library-Currency-Verification-Method]].

## W1 — the split map (measured 2026-08-22; 78 live docs, 7 multi-H1, 19 single-H1 >25 KB)

### Tier 1 — genuine multi-topic docs, mechanical splits (extra H1 → own page) — ✅ DONE 2026-08-23

Carved via the new `library_carve_h1` tool (H1-seam carve; first H1 stays as parent + links list,
every other H1 → own child). 4 parents → stubs, 9 children, 4 verbatim archives
(`Archive\obsidian-h1carve-*`); retrieval probe 8/8 all rank-1; consumer grep clean (no NAME-ref
breaks). Child names are the slug of each carved H1 (below = as actually created):

| doc | carved into (children) |
|---|---|
| [[09-DENSITY-PLAN]] (3 H1) | keep density plan · [[Mutation-testing-design]] · [[Drive-Capability-Design-and-Status]] |
| [[11-IMAGE-GENERATION-PLAN]] (3 H1) | keep #19 plan · [[GPU-Inventory-and-Placement-Policy]] · [[Image-Generation-P2-Creative-Features]] |
| 29-BESPOKE-KLEIN-SCOPE (3 H1) | keep scope doctrine · Bespoke-Klein-Curation-and-Candidate-Generation-Doctrine · [[Benchmark-Result-LoRA-vs-Full-Fine-Tune-Klein-Scope]] |
⏳ **09-DENSITY next-day consumer re-grep owed** (highest-touch: 2 memory + 3 code refs). §-refs to
repoint in W3 (soft-resolve via stubs now): PROJECT-BACKLOG (29 §6, 12 §Corridor Crew),
PROJECT-20-ROADMAP (12 §Research Brief, 12 §Corridor Crew), Drive-Capability → 09 §Order rationale,
Mutation-testing-design → 09 §Phase B.

Exempt multi-H1: `Runbook\FLASH-NEW-TABLET-…` (phases, rule 3) · PROJECT-BACKLOG
(tracker, rule 4 — its second H1 "★ NEXT PROJECTS" folds into the first as `##`).

**The `library_carve_h1` tool** (shipped 2026-08-23, gate 2119): admin-gated, doc relpath → dry-run
preview → pending→confirm write. Engine: `library_split.split_doc_h1` (deterministic H1-seam cut,
same safety spine as `split_doc`) + `library_restructure.build_h1carve_spec`/`apply_h1carve`. This
is the reusable mechanism for any future multi-H1 doc — including RUNBOOK's oversized sections.

**Consumer counts (measured 2026-08-22 — the migration data for W2).** Every parent below MUST
become a stub, never vanish; all inbound refs then keep resolving:

| doc | doc refs | memory | code | code refs are… |
|---|--:|--:|--:|---|
| `12-PROJECT-20-VIDEO-IDENTITY-RESEARCH` | 5 | 0 | 2 | docstring provenance (`person_record.py:7`) — NOT imports |
| `09-DENSITY-PLAN` | 2 | 2 | 3 | comment provenance (`config.py:522`, `review_risk.py:7`) — NOT imports |
| `11-IMAGE-GENERATION-PLAN` | 3 | 1 | 0 | — |
| `29-BESPOKE-KLEIN-SCOPE` | 3 | 0 | 0 | — |
**Verified: no split requires a code change** — every code reference is a `.md`-provenance
comment/docstring, so a parent stub keeps the pointer valid. `09-DENSITY-PLAN` is the highest-touch
split (2 memory + 3 code refs); do it with the most care and re-grep it the next day.
`11-IMAGE-GENERATION-PLAN`'s parent (#19) is a near-empty title stub already — its real content is
entirely under the two child H1s, so after the split the parent is naturally just an index page.

### Tier 2 — omnibus manuals (single-H1, >25 KB): split per DOMAIN, with the owner, one sitting each

Ordered by leverage; each sitting = split map for that doc → the owner eyeballs → execute → probe:
1. [[RUNBOOK]] (354 KB, 37 H2 sections) — the operator manual. Biggest win AND risk;
   goes LAST, after the pattern is proven on smaller domains. Likely shape: per-domain operator
   pages + a thin master index that keeps the name [[RUNBOOK]].
2. [[HOST-ONBOARDING-Basement-Desktops]] (61 KB) — onboarding procedure vs
   mordecai/rigby host reference vs benchmark records.
3. [[Lorex-Camera-Integration-SCOPE]] (49 KB) + [[HAI-Orchestrator-Internals]]
   (49 KB) — cameras and HAI domains.
4. `LLM-Project` plans (05/06/02, 40 KB each) — plan register vs design-doctrine register.

### Tier 3 — entry points (the Tablet-Cluster-INDEX pattern, generalized)

One `<Domain>-INDEX.md` per domain (tablets already have one; network, cameras, HAI, factory,
media-stack do not). Hand-maintained, thin: what lives where + the 5 questions people actually
ask. These become the wiki's front doors and the graph's hubs.

## The pipeline is built (2026-08-22) — engine + the tablets slice proven

Engine components (all tested; the fleet half deployed to all 3 factories):
- `mothership-build\triage_synthesize.py` — fleet payload, `--task split-map|index`, sibling of
  triage_rewrite (reuses `_chat`/`_assert_resident`), DRAFTS-ONLY. Capability `triage.synthesize.v1`.
- `orchestrator-build\library_split.py` — the deterministic byte-exact apply (fence-aware,
  parent→stub, sha-verified archive, refuses drop/dup/invent/interior-H1). NO model in this path.
- `orchestrator-build\library_probe.py` — the retrieval acceptance gate (`rag.search` per probe).

Tablets slice DONE via the desk path: split-map order (27B) → review → `library_split` applied
2 docs → 9 children + 2 stubs (`Archive\obsidian-split-tablets-20260822`), content conserved,
refs resolve, retrieval probe 8/8. This is the pattern to repeat per domain.

### Phase O wiring (HAI operator tools) — ✅ SHIPPED 2026-08-22

Deployed: the `/mnt/ssdshared/library-triage:/library-triage:rw` mount (canary inherits it via
`<<: *orch_svc`) + `library_restructure.py` and the four admin-gated tools in `tools.py`
(`library_propose_split`/`library_draft_index`/`library_probe` read-class · `library_apply_split`
= pending→confirm write), via the RUNBOOK orchestrator procedure (gate 2069→2099, PIN bumped,
RC=0). HAI drives the whole pipeline through `/query`; the WRITE confirms via `/pending/act`.
⚠ A write `/query` that returns `route:error` may still have STAGED the pending action (the 27B
can fail to assemble its final answer after the tool ran) — check `/pending` before retrying.
The build notes below record the original design (still accurate).

Confirmed from `/opt/arrstack/docker-compose.override.yml` (the orchestrator's real compose, NOT
the media-stack `docker-compose.yml`): the orchestrator container has `/mnt/documentation:/docs`
**RW** and `/mnt/ssdshared:/downloads:ro` (read-only). So:
- `library_apply_split` (writes children into `/docs/Reference`) and `library_probe` (in-process
  `rag.search`) work with NO compose change.
- `library_propose_split` / `library_draft_index` must write a fleet work file to the staging
  share the factories read — the container's SSDShared is read-only, so add ONE volume:
  `/mnt/ssdshared/library-triage:/library-triage:rw`, then recreate the orchestrator (RUNBOOK
  orchestrator deploy procedure).
- Then 4 admin-gated tools in `tools.py` (dispatch in `_run_tool_impl`, gate via `_ADMIN_TOOLS`):
  propose_split / draft_index / probe = read-class; apply_split = the pending→confirm WRITE
  (`pending_actions.register`). Follow `librarian.py` (content-only, code-shaped paths) +
  the media/infra pending pattern. Engine module: `library_restructure.py` (domain registry +
  work-file builders + order submit) calling library_split/library_probe.
