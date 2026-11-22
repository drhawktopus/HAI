# Doctrine — INDEX

The lab's operating doctrine — the "classes" of mistake we've paid for and the rules that keep us from paying twice. Doctrine is project-agnostic; project-specific runbooks and reference pages live elsewhere in `Reference\` and `Runbook\`.

## Where doctrine actually lives

**Declarative laws — RAG-indexed, grouped by topic:**
- Doctrine-State-and-Artifacts — how state files, artifacts, caches, and claims behave (one name = one artifact, single-writer stores, stated claims vs absent markers, fingerprinted caches).
- Doctrine-Guards-and-Supervision — how guards, watchdogs, and supervision must behave (dead reporter ≠ dead worker, supervision is factory machinery not session-side, exit-on-verdict, three-fates: wait/archive/hold-for-human, cleanup belongs to the end-seer).
- Doctrine-Measurement-and-Verification — how measurements, comparisons, and instruments must behave (one variable or no comparison, no arbitrary limits — a binding timeout/context/token cap measures the cap, assert the count on zero-shaped results, verify to the working end state, declared ≠ present ≠ visible).
- Doctrine-Build-and-Migration-Discipline — how to build and migrate without breaking the lab (build LAST after checking the library, enumerate consumers on any migration/fact change, text through shells corrupts, docs win over memories).

**Case law + dated vignettes:** `E:\SSDShared\CLAUDE.md` — auto-loaded into every Claude session; not RAG-indexed by design (it's the working-doctrine reference for Claude sessions in real time). HAI reads it on demand when the case-law behind a class is needed (e.g., drafting a portfolio doctrine page, teaching another agent from actual incidents). Search the CLAUDE.md `THE CLASSES` section and its "Before I X, read Y" lookup table when you need "what happened the last time this bit us."

**HAI system-prompt priming:** a compact 18-rule operating cheat-sheet is injected into HAI's homelab system prompt so the meta rules (library-first, measurement-is-an-order, tools-inform-don't-block) and the top-of-mind laws prime every response — not just when a rule happens to be retrieved. Full text of the primed cheat-sheet lives with the HAI system-prompt config; sync it with the CLASSES table on doctrine edits.

## Related operating docs

- [[OPERATING-BRIEF]] — how to work IN the lab: orders vs freelancing, elevation, ssh mechanics, three ways "I can't" mean different things.
- [[HAI-INVARIANTS]] — HAI-specific standing rules that carry the same weight as doctrine within HAI's operating scope.

## Editing rules

Doctrine changes are load-bearing. When adding a new class or amending an existing one:
1. The declarative rule goes in the correct topical sibling above.
2. The case-law and the owner's quote go in `CLAUDE.md`'s CLASSES table (dated).
3. If the change affects the top-of-mind rules, update HAI's system-prompt cheat-sheet in the same pass — otherwise HAI keeps operating on the old primed set.
4. If a memory or an older doc contradicts the new rule, fix or retire it in the same pass — Class 21 (docs win, stale notes get cross-referenced).
