# Library section triage rubric — how HAI classifies documentation sections (manual / doctrine / narrative / record / credential)

One section of a library document in, one classification out. The classifier decides **REGISTER
from the text alone; it never decides whether a claim is TRUE** — present-tense claims about
system state get FLAGGED, and a human reviewer verifies them against live instruments. This split
is what makes the task delegable: register is decidable from the text; truth is not.

Consumers: library-triage orders (`LLM-Project\30b-LIBRARY-TRIAGE-AS-AN-ORDER.md`), graded
against `Lab\hai-evals\library-triage\goldset.jsonl`. Filing doctrine behind the tiers:
`Runbook\LIBRARY-CHARTER.md`.

## The output contract — exactly three lines, nothing else

```
TIER: <manual | doctrine | narrative | record | credential | unsure>
VERIFY: <yes | no> | "<strongest current-state claim, quoted verbatim from the section>"
ACTION: <one sentence: what the reviewer should do with this section>
```

If VERIFY is `no`, write `VERIFY: no | none`. Do not add greetings, reasoning, or extra lines.

## TIER — pick exactly one

- **manual** — states what a thing IS or HOW to operate it, in present tense, usable today:
  procedures, commands, settings, configuration tables, gotchas phrased as standing rules.
  Reviewer action: keep in the document.
- **doctrine** — a design rule with its reason: "X is refused because Y", "never do X — it costs
  Z". Epistemic labels (PROVEN / hypothesis / rejected-because) are doctrine, not narrative.
  Reviewer action: keep in the component's manual.
- **narrative** — the story of how something was discovered, debugged, or fixed: dates as plot,
  who was wrong, session play-by-play, "then we tried". Reviewer action: move to a work journal.
- **record** — a dated measurement, closure note, or milestone whose date or named instrument
  scopes it ("measured 2026-08-13", "CLOSED 2026-08-06", a benchmark table naming its tool).
  Reviewer action: keep as-is — it is history that is honest about being history.
- **credential** — a password, key, token, or other secret appears in the text. Reviewer action:
  redact to a pointer at the credentials locker. **Never repeat the secret in your answer** —
  quote at most the surrounding words.
- **unsure** — you cannot decide from the text alone. Say so. An honest `unsure` beats a guess
  and costs the reviewer one look; a confident wrong label costs a wrong edit.

## VERIFY — independent of TIER

`yes` = the section asserts facts about CURRENT system state that could have drifted: node
counts ("all five nodes"), rosters, IPs, hostnames, versions, "X runs on Y", "X is wired into
Z". Quote the single strongest such claim verbatim — the reviewer checks it against the live
system before the section is trusted.

`no` = nothing in it can drift: pure procedure, pure design rule, or a claim already scoped by
its date ("verified 2026-08-13 on legolas" is a record of that day, not a claim about today).

Reviewer note (not the classifier's job): the preferred FIX for a flagged claim is usually to
restate it as the design fact plus a pointer to the conformance instrument — not to re-pin
today's observed value (LIBRARY-CHARTER filing rule 3, corollary). Observed values stay pinned
only where the value itself is the content, e.g. a benchmark.

## Boundary rules

- Dated claim → **record**, VERIFY `no`. Undated present-tense claim → VERIFY `yes`.
- **record** vs **narrative**: a record states the result and its date; narrative tells the
  finding as a story. "19.25 GFLOP/s, `burn.py 1024 20`, 2026-08-13" is a record. "We spent the
  evening chasing the slow node" is narrative.
- **manual** vs **doctrine**: manual says *how*; doctrine says *why / never / always*.
- A checklist item struck through with a dated closure ("~~X was broken~~ CLOSED 2026-08-06") is
  a **record** even when it contains present-tense words.
- Judge ONLY the text given. Do not use outside knowledge about the lab. Do not browse or call
  tools. If the section is a fragment that depends on unseen context, answer `unsure`.

## Worked examples

Section: "To re-arm the watchdog run `rearm_wifi_watchdogs.ps1`; it checks the script exists
before starting it, because starting a nonexistent file returns exit 0."
```
TIER: manual
VERIFY: no | none
ACTION: Keep — operator procedure with its failure-mode guard.
```

Section: "All six nodes now carry the persist prop. Full procedure: RUNBOOK § 'GIVING A NODE
DURABLE adb'."
```
TIER: manual
VERIFY: yes | "All six nodes now carry the persist prop"
ACTION: Keep, but verify the node count and that the referenced RUNBOOK section still exists.
```

Section: "2026-08-17 ~20:21 — six of six on copper, both hardware blockers cleared."
```
TIER: record
VERIFY: no | none
ACTION: Keep as-is — dated milestone.
```
