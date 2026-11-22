# Library section triage rubric — how HAI classifies documentation sections (manual / doctrine / narrative / record / credential)

One section of a library document in, one classification out. The classifier decides **REGISTER from the text alone; it never decides whether a claim is TRUE** — present-tense claims about system state get FLAGGED, and a human reviewer verifies them against live instruments. This split is what makes the task delegable: register is decidable from the text; truth is not.

Consumers: library-triage orders (see [[30b-LIBRARY-TRIAGE-AS-AN-ORDER]]), graded against `Lab\hai-evals\library-triage\goldset.jsonl`. Filing doctrine behind the tiers: see [[LIBRARY-CHARTER]].

## The HAI triage output contract — exactly three lines, nothing else

The assistant must output exactly the following three lines, with no greetings, reasoning, or extra text:

```
TIER: <manual | doctrine | narrative | record | credential | unsure>
VERIFY: <yes | no> | "<strongest current-state claim, quoted verbatim from the section>"
ACTION: <one sentence: what the reviewer should do with this section>
```

If VERIFY is `no`, the output must be `VERIFY: no | none`.

## TIER — pick exactly one

- **manual** — states what a thing IS or HOW to operate it, in present tense, usable today: procedures, commands, settings, configuration tables, gotchas phrased as standing rules. Reviewer action: keep in the document.
- **doctrine** — a design rule with its reason: "X is refused because Y", "never do X — it costs Z". Epistemic labels (PROVEN / hypothesis / rejected-because) are doctrine, not narrative. Reviewer action: keep in the component's manual.
- **narrative** — the story of how something was discovered, debugged, or fixed: dates as plot, who was wrong, session play-by-play, "then we tried". Reviewer action: move to a work journal.
- **record** — a dated measurement, closure note, or milestone whose date or named instrument scopes it ("measured 2026-08-13", "CLOSED 2026-08-06", a benchmark table naming its tool). Reviewer action: keep as-is — it is history that is honest about being history.
- **credential** — a password, key, token, or other secret appears in the text. Reviewer action: redact to a pointer at the credentials locker. **Never repeat the secret in your answer** — quote at most the surrounding words.
- **unsure** — you cannot decide from the text alone. Say so. An honest `unsure` beats a guess and costs the reviewer one look; a confident wrong label costs a wrong edit.

## VERIFY — independent of TIER

Set `VERIFY` to `yes` when the section asserts facts about current system state that may drift. Examples include node counts (e.g., "all five nodes"), rosters, IPs, hostnames, versions, or statements linking components (e.g., "X runs on Y", "X is wired into Z"). Quote the single strongest such claim verbatim; the reviewer must check it against the live system before trusting the section.

Set `VERIFY` to `no` when nothing in the section can drift. This includes pure procedure, pure design rules, or claims already scoped by their date (e.g., "verified 2026-08-13 on legolas" is a record of that day, not a claim about today).

### Reviewer action for flagged claims

The preferred fix for a claim flagged as `yes` is to restate it as a design fact plus a pointer to the conformance instrument. Do not pin today's observed value (see LIBRARY-CHARTER filing rule 3, corollary). Pin an observed value only when the value itself is the content, such as in a benchmark.

A `VERIFY: yes` claim is not closed by findability. It stays open until the **VERIFY lane** resolves it — a CURRENT verdict from `triage_verify`, or a primary-source disposition for a claim no instrument can mechanically check. A doc can be perfectly retrievable and still describe a system that no longer exists (see [[LIBRARY-CHARTER]] rule 10); the restructure that shipped it is not done until every flagged claim is dispositioned.

## Boundary rules

Classification logic for library sections:

- **Dated claims**: Classify as `record`. Set VERIFY to `no`.
- **Undated present-tense claims**: Set VERIFY to `yes`.
- **Record vs. narrative**:
  - A record states a result and its date (e.g., "19.25 GFLOP/s, `burn.py 1024 20`, 2026-08-13").
  - Narrative tells the finding as a story (e.g., "We spent the evening chasing the slow node").
- **Manual vs. doctrine**:
  - Manual describes *how* to perform actions.
  - Doctrine describes *why*, *never*, or *always* constraints.
- **Strikethrough checklist items**: Items struck through with a dated closure (e.g., "~~X was broken~~ CLOSED 2026-08-06") are classified as `record`, even if they contain present-tense words.

Constraints:
- Judge only the text provided. Do not use outside knowledge about the lab. Do not browse or call tools.
- If the section is a fragment that depends on unseen context, answer `unsure`.

## Worked examples

Section: "To re-arm the watchdog run `rearm_wifi_watchdogs.ps1`; it checks the script exists before starting it, because starting a nonexistent file returns exit 0."
```
TIER: manual
VERIFY: no | none
ACTION: Keep — operator procedure with its failure-mode guard.
```

Section: "Every node must carry the persist prop. Full procedure: [[RUNBOOK]] §'GIVING A NODE DURABLE adb'."
```
TIER: manual
VERIFY: yes | "Every node carries the persist prop"
ACTION: Verify conformance using `check-persist-prop.sh`. Confirm referenced RUNBOOK section exists.
```

Section: "2026-08-17 ~20:21 — six of six on copper, both hardware blockers cleared."
```
TIER: record
VERIFY: no | none
ACTION: Move to Journal (narrative milestone). Remove from manual.
```
