# Doctrine — Build & Migration Discipline

Project-agnostic operating doctrine for building, changing, and migrating things in this lab. These
are declarative laws; the dated case-law behind each lives in the lab's private working notes and the work
journals. For how to *work* in the lab, see [[OPERATING-BRIEF]]. Sister pages:
[[Doctrine-State-and-Artifacts]], [[Doctrine-Guards-and-Supervision]],
[[Doctrine-Measurement-and-Verification]].

## Building is the last step

Before building a mechanism, a script, a theory, or a clean-up pass, search the library, read the
component's own docstrings, and ask the operator — in that order. The lab has usually already solved it. The
tell that you skipped this: you are explaining WHY you chose an approach in a solved area and cannot
name the doc or docstring it came from. If you cannot QUOTE the requirement, you invented it — and
that binds on the operator's goals too: a filter, cap, or target you cannot quote them asking for is yours,
not his. Prefer the lab's own mechanism over a new one; a parallel mechanism is a second thing that
can drift.

## A migration enumerates the consumers of what changed

Every migration — a moved path, a renamed file, or a changed FACT — ends by enumerating its
consumers: code, data, docs, scheduled tasks, and memory. Grep for the superseded path or claim
across all of them, then grep again the next day. A dated status block is replaced AT RESOLUTION TIME
by whoever resolves it, never left for a later sweep — a stale claim re-teaches the wrong thing to
whoever hits it first. Before editing and deploying a mirror, prove it is in sync with a whole-tree
hash diff, not a spot check on one file.

## Text through shells corrupts silently

Author any content containing backslashes, backticks, or regexes with the Write and Edit tools, never
a shell heredoc — a heredoc eats escape sequences and the result parses fine and is silently wrong.
Then verify the BYTES (repr, hash, `node --check`), not that it parsed. To run remote code, pipe a
local file into the remote interpreter's stdin rather than inlining it through quoting layers, which
eat backslashes and can produce one-line no-ops that exit 0.

## Docs beat memories; titles beat buried asides

When a doc and a memory disagree, the doc wins — memories are snapshots, the library is maintained,
often more recently than a memory's own age suggests. Fix the stale memory in the same pass. A fact
is only "written down" if a stuck session searching the obvious term would LAND on it: retrieval is
per-chunk and the first H1 is prepended to every chunk, so the section TITLE is the lever — it must
contain the words someone would type, including the error they are staring at. A journal entry and a
resume bullet are not documentation. When a fix supersedes an older note, cross-reference the old one
in the same pass.
