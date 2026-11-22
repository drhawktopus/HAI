# Library currency verification — proving docs are current, not just findable

Findability is not currency. The restructure makes a doc retrievable; it does not make it true. A
findable-but-stale page is worse than a missing one, because the tier contract promises that
Reference is current (see [[LIBRARY-CHARTER]] rule 10). Currency is verified against the live lab by
instruments — never assumed from the source text a summarizer read, and never judged by a model's
opinion. This is the repeatable method behind that rule.

## The method, per domain

1. **Extract + flag.** The `triage.extract.v1` rules lane sections the domain's docs and mines
   present-tense claims (`verify` / `claims` per row): rosters, IP:port pairs, versions, paths,
   scheduled tasks, service URLs, "X runs on Y". High-recall regex is what this stage is for. Runs
   as a read-only fleet extract order (Fellowship or an x86 CPU factory).
2. **Type.** `build_verify_claims(domain)` types each mined claim into an instrument-answerable
   probe — `port_open` / `http_reach` / `path_fresh` / `task_exists` — or leaves it `judgment`.
   Conservative by design: only a pattern the instrument can provably answer is typed; everything
   else stays judgment. Probes are LAN-only, the prober is a box whose own IP is not the target
   (cross-wire, never loopback), and gold controls are seeded so the instrument is validated before
   any negative is believed.
3. **Verify against the live lab.** `triage_verify` runs the mechanical probes read-only on a
   factory box, grading each doc line **CURRENT** (still true) / **STALE** (no longer true) /
   **UNREACHABLE** (no answer — could be down, could be stale, a human decides) / **CANNOT-TELL**
   (could not measure — never rendered as a definite state). A gold control that misses its expected
   verdict voids every other verdict on that box: a broken instrument's negatives cannot be trusted.
4. **Dispose.** Every non-CURRENT mechanical verdict AND every `judgment` claim (which gets no
   mechanical verdict) is resolved against a **primary source**: rewrite to current, archive the
   dead era, or confirm current with evidence. The judgment claims — a room map, a roster, a framing
   of "which system does the work" — are where the dangerous rot hides; they are eyeballed, not
   skipped. A conservative typer also produces mechanical false positives (a command line or a
   checklist step that merely contains an IP:port); a non-CURRENT verdict is reviewed, not counted
   blindly.
5. **Done-bar.** `verify_gate(domain)` passes: VERIFY ran, and every real claim is CURRENT or
   dispositioned. Nothing in the domain describes an old era; zero undispositioned STALE.

## The gate and the truth-feed

`verify_gate(domain)` (in `library_restructure`) refuses a split, index, or restructure until the
VERIFY lane has run and every claim is resolved. `verified_state_block(domain)` renders the live
verdicts plus a hand-authored current-truth anchor and feeds them INTO the fleet synth/rewrite
prompts (`triage_synthesize`, `triage_rewrite`), so a draft anchors to reality rather than to the
stale source it is rewriting. This is the "give the blind reviewer the ability to go look" fix: the
model proposes structure, the instruments decide truth.

## Tools and staging

- `build_verify_claims` (`mothership-build`) — the claim typer (reads an extract or a candidates file).
- `triage_verify` (fleet `bin`) — the read-only VERIFY instrument.
- `verify_gate` / `verified_state_block` (`library_restructure`) — the gate and the truth-feed.
- Staging: `\\<softserve-address>\SSDShared\library-triage\s30-<domain>-verify.{claims,verdicts.<box>,dispositions}.json`.

## Routing

VERIFY orders are read-only cpu work; route them to **BigHonker or Mothership, never softserve**
(the Plex host and the main session's own host). The Fellowship runs the CPU extract but cannot
reach the `\\<softserve-address>\SSDShared` gold control, so verify never routes there.

See also [[Library-Section-Triage-Rubric]] (how claims are flagged) and
[[30b-LIBRARY-TRIAGE-AS-AN-ORDER]] (the fleet order that carries the extract + classify chain).
