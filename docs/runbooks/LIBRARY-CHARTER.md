# LIBRARY CHARTER — where documentation lives, and how it is filed

The documentation system has five tiers. Every piece of writing belongs to exactly one.

| tier | location | contents | test |
|---|---|---|---|
| **Runbook** | `Runbook\` | Operator's manual. Current state only: what things are, how to bring them up, how to work with them. | An operator can run the lab from it today. |
| **Supporting docs** | `Reference\` · `LLM-Project\` | Full per-component manuals, each including the component's **design doctrine** — why it is built this way, what rules govern changes to it. | Runbook + supporting docs alone are sufficient to run everything AND rebuild the lab from scratch, by a reader with no other context. |
| **Archive** | `Archive\` | Superseded documents and sections, moved out of the library — never banner-marked in place. | The library contains nothing describing an old era. |
| **Work journals** | `Journal\` | The live per-session record: narrative, reasoning, discovery paths, exact change sequences. The ONLY tier where narrative belongs. | Fills the gaps the final documented state does not carry. |
| **Assistant memory** | per-assistant | Pointers into the library, not duplicates of it. | — |

"The library" means the Runbook plus the supporting docs. "Get the library up to date" additionally
means confirming the journals got written.

## Filing rules

1. **Documents state what IS.** No commentary, no narrative, no discovery stories, no
   "this previously said X" — those produce false grep/RAG hits. The story goes in the journal;
   the document carries only the correct current statement. This applies to code comments too.
2. **Findability is the acceptance test:** a stuck session searching the obvious term — including
   the error message it is staring at — must LAND on the answer. Section titles carry the search
   terms; an in-body aside is invisible.
3. **Live status never lives in a document.** Documents point at the status tracker that serves it.
   Conclusions age well; measurements do not.
   ⭐ Corollary (the owner, 2026-08-21): **state the DESIGN FACT, not the current-state observation.**
   "Every Fellowship node carries the persist prop" is a spec — a node without it is a defect to
   fix, and the sentence never drifts. "All seven nodes currently carry it" is an observation that
   goes stale the day the fleet changes. Where a claim must be checkable, name the conformance
   instrument beside the design fact rather than pinning today's observed value. Pin an observed
   value only when the value itself is the content (a benchmark, a dated measurement record).
4. **Every file is either definitive or additive, and says which.** Definitive files (manuals) are
   edited in place and are always current. Additive files (journals, logs) accrete and are dated.
   A reader must never have to guess the register.
5. **Superseded content moves to the Archive** — whole documents, or sections cut during a rewrite.
   Never leave stale content under a warning banner: a banner is one missed glance away from being
   read as current.
6. **Sensitive material** is sanitised to near-data or lives in person records; never in indexed
   docs. Test: a third-party reader, years later, drawing only correct conclusions.
7. **Doctrine changes need proof.** Established doctrine and schema stand unless the replacement is
   provably better.
8. **Nothing lives only in an assistant's private workspace.** Tools, instruments, and knowledge
   the lab depends on go in the shared tree — the lab must be runnable by its own systems (HAI),
   not dependent on any one assistant.
9. **Earned knowledge files twice, in the same pass.** Work that discovers a constraint, a
   procedure, or a design fact writes the narrative into the day's journal AND the reusable fact
   into the owning manual, correctly framed (declarative, no war story). A lesson that exists
   only in a journal — or only in a code comment — is not documented: journals are not searched
   as manuals, and comments are not indexed at all.
10. **A restructure is not DONE until its docs are verified current.** A split, index, or
    restructure completes only after the VERIFY lane has run on its docs and every present-tense
    claim it flags is resolved — a CURRENT instrument verdict, or a primary-source disposition.
    Findability (the doc is retrievable) is not currency (the doc is true now): a findable-but-stale
    page is worse than a missing one, because the tier contract promises that Reference is current.
    The conformance gate is `verify_gate` in `library_restructure`; the instrument is `triage_verify`.

## RAG indexing (what HAI retrieves)

The index covers exactly `Reference\`, `LLM-Project\`, `Runbook\` — top level only, subdirectories
excluded. `Notes\`, `State\`, `Journal\`, `Archive\` are not indexed (`Journal\` is fetchable by
name). Retrieval is per-chunk and each chunk is prefixed with the file's first H1, so **the section
title is the retrieval lever**. A guide in an indexed directory becomes policy the local model
enforces. When a plain-language duplicate of an indexed doc is needed, it goes in an unindexed area
(`Notes\`).

## Archive convention

`Z:\Documentation\Archive\` — one dated subfolder per event (`<topic>-<YYYYMMDD>\`), each with an
`ARCHIVE-README.md` stating what it holds, why it was archived, and what replaced it. Moves are
copy → SHA256-verify → delete source. Archive, never purge; deletion is the owner's call only.

Related roots that are NOT documentation archives: `Z:\Archives\` (big-artifact parking) and
`Z:\Workspace\_archive\` (retired scripts/configs) — see the README in each.

## Journal convention

One journal per session per day: `Journal\YYYY-MM-DD-claude.md` for the main session; side sessions
add a suffix on the same date (`YYYY-MM-DD-claude-<topic>.md`), never tomorrow's date, and never
append to another session's file. Journals are written as the work happens, not reconstructed at
wrap-up. Corrections are recorded as corrections.

## Delegated triage (HAI-assisted)

Section classification can be delegated to HAI per [[Library-Section-Triage-Rubric]],
placed as `library.triage` orders at the Kirk desk ([[30b-LIBRARY-TRIAGE-AS-AN-ORDER]];
pattern precedent `06b`). HAI decides REGISTER from the text alone and flags live claims; truth
verification against live instruments, edits, and archives stay with the reviewer, and scope
rulings stay with the owner. Each batch is an explicit order — nothing triages automatically.

Flagged claims are checked by the **VERIFY lane** — read-only fleet orders (`triage_verify`) that
grade each claim CURRENT / STALE / UNREACHABLE / CANNOT-TELL against the live lab. A claim the
instrument cannot mechanically check (a room map, a roster, "X runs on Y") carries no verdict and
must be dispositioned against a primary source. Register is decidable from the text; currency is
not — it comes from the instruments, never from a summarizer's opinion (rule 10).
