# Doctrine — State & Artifacts

Project-agnostic operating doctrine (the "classes") for how state and files behave in this lab.
These are declarative laws; the full dated case-law behind each one lives in the lab's private working notes
(auto-loaded into Claude sessions, not RAG-indexed) and the work journals. For how to *work* in the
lab — orders, elevation, not freelancing — see [[OPERATING-BRIEF]]. Sister pages:
[[Doctrine-Guards-and-Supervision]], [[Doctrine-Measurement-and-Verification]],
[[Doctrine-Build-and-Migration-Discipline]].

## One name = one artifact

Timestamp every generation of a rotated, archived, or backed-up file. Never let the first or oldest
copy keep the bare canonical name — the most obvious filename must not point at the stalest content.
A same-named file in an adjacent directory is a decoy: retire it in the same migration that orphaned
it. Readers pick the newest generation *explicitly*, never by "newest file in the directory," and
retirement covers every artifact a run produced (logs, sidecars, state files, staging dirs), not
just the big one.

## A single-writer store has exactly one writer

Before adding a second producer to a store that one writer owns, audit what the first writer actually
writes and merge per-key — never replace whole records. An unaudited second writer silently clobbers
the first. If a POST or append target has a contract, a new writer inherits that contract's schema
and obligations.

## A stated claim proves HELD; its absence proves nothing

A lock file, a run-claim, or a residency flag proves a resource is *held* when present. Its absence
proves only that no claim was written — never that the resource is free. Before taking any resource
(a card, a lane, a port, a file), confirm free-or-held with the OS or the owning process, not from
the absence of a marker. A dead reporter leaves no claim while its work runs on.

## Derived caches need fingerprints

Any derived or cached artifact (embeddings, conditioning, latents, an index) must carry a fingerprint
of its inputs, checked before use. Unverifiable means stale: retire and re-derive rather than
reasoning about whether it is still good. A cache gated only by "the file exists" will feed a
mismatched input into a consumer that then fails several frames away. Move it aside, do not delete,
so a bad artifact can still be diagnosed.

## When content ships in two formats, the loader decides which is real

When a payload carries the same content in two encodings or namings, only the consuming code decides
which one is live — verify against that one, not the copy you happened to open first. Ask the code
what it builds (a class attribute, an import, a constructor). The tell that you checked the wrong
copy is a filter, mapping, or selector that silently matches nothing: make it print its match count
and assert non-zero, because "selected nothing" and "selected correctly" are identical at the output.
