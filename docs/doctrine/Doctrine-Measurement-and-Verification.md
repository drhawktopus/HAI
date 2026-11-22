# Doctrine — Measurement & Verification

Project-agnostic operating doctrine for measuring, comparing, and verifying. These are declarative
laws; the dated case-law behind each lives in the lab's private working notes and the work journals. For how
to *work* in the lab, see [[OPERATING-BRIEF]]. Sister pages: [[Doctrine-State-and-Artifacts]],
[[Doctrine-Guards-and-Supervision]], [[Doctrine-Build-and-Migration-Discipline]].

## One variable, or it isn't a comparison

Before stating any A-beats-B conclusion, list everything that differs between the two cells. If more
than the thing under test differs, there is no result — only a story. Same occupancy, same
instrument, same config. Resolve a named confound by finding the case where the two variables
SEPARATE, before shipping any fix built on it. Re-read the config or prompt list before interpreting
results, not after, and build in a reproducibility cell — repeat one prior measurement at the same
inputs; if it is not identical, something believed constant is not, and every comparison in the batch
is void.

## No arbitrary limits — don't sandbag: a timeout, context or token cap that binds measures the cap, not the model

Every timeout, context size, token cap, budget or rate limit comes from a measurement or a documented tested
value — for a model, the box's tested maximum context for that model — never a round number that looked
reasonable. In a measurement a limit is a hidden variable: when it binds, the cell measures the limit and reads
exactly like a real result. Size limits to what the thing actually uses in that mode, up to its tested maximum.
Before believing any score, count the rows that hit a limit (`done_reason`/`finish_reason` "length", tokens at
the cap, a timeout error, reasoning never closed); a real share hit voids the cell — re-run it with room and
nothing else changed. In operations, a clock that kills work is supervision by clock: end work on demonstrated
idleness, never on elapsed time ([[Doctrine-Guards-and-Supervision]]). The operator's rule: *"no arbitrary limits"*.
Case law: a 27B model with thinking on read 0.667 on a task set because 27 of 75 answers were cut at a
6,144-token window; the same cell with room (65,536, streamed, ended only on silence) read 0.986. Two more limits were
found binding on the way: a flat request timeout, then a static-unit window in the stall guard.

## A measurement is an order

Anything with inputs, a method, deliverables, and cleanup is an order — write the spec and let the
factory run it, do not write a script. The tell that you skipped this is that you are about to call
factory internals directly, or to bolt a measurement onto another job's lifecycle (it will inherit
that lifecycle and be eaten by it). Declare product versus byproduct, or the byproducts rot in a
shared location.

## Rank-based allocation, never absolute-property bands

Allocate re-splittable work by RANK within the set being allocated, never by an absolute property of
each item. A band keyed on an absolute value starves whoever finishes first the moment the set is a
filtered remainder — and "re-run the leftovers" is the normal case, not an edge case. Ranking the
remainder re-balances; a fixed band inherits a slice that may be empty.

## Verify to the working end state

Prove the thing at the end state that matters, and identify the object actually in force — it is a
different object from the one you wrote. Compile is not correct · deployed is not running · shipped is
not on disk · a mirror is not synced unless just hashed · a config table is not the emitted stream ·
"restarted" is not a new PID · an image is not the running container. Either prove the in-force object
POSTDATES your change (both sides carry a clock), or measure it directly (when the in-force thing has
no timestamp).

## An instrument that measured nothing looks like one that found nothing

A clean result and a broken instrument are indistinguishable at the output — both return "nothing,"
and confidence fills the gap. Assert the count: a comparison states how many pairs it compared and
fails on zero; a matcher states how many it matched and fails on zero. For an enumeration, dump the
whole thing to a file and search the file — never let a bounded `head`/`tail` window be the artifact
you cite. Validate the instrument against a known positive before trusting a negative. When reading
back a window to answer "did we already decide this," read the newest match first and read both
speakers.

## n = 1 is a hypothesis — run it again

Run a measurement again before believing it; a clean-looking number is what stops you checking, which
is exactly when it is wrong. Ask what KIND of repetition the question needs — a deterministic
instrument needs more scenes, not more repeats; a latency or load number needs rounds separated in
time. Report the spread and whether ranges overlap, not just a delta, and remember rank stability is
per-claim (the slowest node can be stable while the fastest swaps).

## An unwired capability fails exactly like an absent one

A capability that exists in code but is not enabled in the allow-list, table, spec-field, or manifest
behaves identically to one that was never built. Before declaring a feature present OR absent, grep
the thing that ENABLES it, not just the code that implements it, and search the component that OWNS
the concern. An empty result and an error result are different evidence — an absent VALUE never proves
an absent FEATURE, and a live decoy proves a capability is ON but can never prove absence (ask what
the device *supports*, not only what it *did*). Declared is not present is not visible.
