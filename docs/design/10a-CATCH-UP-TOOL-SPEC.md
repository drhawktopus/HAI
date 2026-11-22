# `catch_up` — TOOL SPEC (#12 Phase G, G1a option (c)). To be DRAFTED BY HAI.

**Status: SPEC ONLY, 2026-08-13. Not built.** Blocked on the owner's arming-grant scope for HAI drafting
through its coding agent. Written by session 4 as the specifying half of the Phase 0 authorship
split: **HAI drafts this one**, session 4 reviews. It is deliberately the first thing handed to HAI,
because it is the tool that lets HAI answer *"where were we"* — HAI building its own memory of the
lab is the point of the phase.

**Read first:** [[10-HAI-INDEPENDENCE-PLAN]] (G1, and why option (a) was rejected).

---

## 1. The problem, in one exchange

2026-08-11, the owner to HAI: *"Did the Claude task you out with anything this mornin?"* HAI had no idea
where such a record would live, and answered with generic statements about LLM memory. The answer was
in its own library the whole time.

**HAI does not know that "what did Claude do / where were we / what's the status" MEANS "read the top
blocks of CURRENT-PROJECTS and the newest day journals, newest-first."** That is a *procedure*, and
the lab's standing rule is to encode a procedure as a mechanism rather than hope the model recalls
it. `catch_up` is that mechanism: it removes the routing from model judgement entirely.

## 2. Contract

```
catch_up(topic: str = "", days: int = 2) -> str
```

* `topic` — optional filter (e.g. `"cameras"`, `"p20"`). Empty = everything.
* `days` — how many days of journals to reach back. Default 2, cap it (suggest 7).

Registered in the same tool lists as `search_docs` / `get_document`, and **available to the homelab
namespace**. Its description is load-bearing — it must contain the words a person actually types:
*what did Claude do*, *where were we*, *what's the status*, *catch me up*, *what happened*,
*pick up where we left off*, *handoff*.

## 3. What it returns

One text payload, in this order:

1. **A header stating the instrument** — what it read, and how much (see §4).
2. **The read rules**, stated in the payload so they travel with the data:
   > CURRENT-PROJECTS blocks are **ADDITIVE and newest-first — later supersedes earlier**; a block
   > lower down may have been corrected by one above it. A **definitive** doc (a SCOPE, a plan, a
   > runbook section) is read **whole**, and its own newest section wins.
3. **The top of CURRENT-PROJECTS** — the handoff surface.
4. **The newest journals** within `days`, newest first: filename, first heading, and a bounded head
   of each. **Filenames matter** — they are how the user asks for the whole thing next
   (`get_document("[[2026-08-13-claude-hai]]")`), which is what G1a option (b) wired.
5. **A closing pointer**: naming a specific document and offering to fetch it whole.

⚠ **State the 12 k window fact in the payload** (rider 2). `GET_DOC_MAX_CHARS = 12000` and
CURRENT-PROJECTS is >800 KB, so only the **top** is ever visible. The payload must say so —
something like *"this is the top N chars of CURRENT-PROJECTS (the file is <size>); handoff blocks
are written at the TOP, so this is the newest material, but anything older is not shown."*
Without that sentence a reader cannot tell a **quiet truncation** from a **complete file**, and the
top-blocks convention is load-bearing while being documented nowhere the model can see.

## 4. ⚠ IT MUST ASSERT ITS INSTRUMENT — LOUDLY (rider 2, and the reason this section exists)

**The failure mode this tool could introduce is worse than the gap it closes.** A `catch_up` that
returns a cheerful empty payload teaches HAI to say *"nothing was handed off"* — which is exactly the
08-11 failure, now with a tool call behind it to make it look verified. A tool that measured nothing
and a tool that found nothing wrong **must not return the same shape**.

Required, each a NAMED, LOUD failure in the returned text — never an empty section, never a silent
omission:

| condition | required behaviour |
|---|---|
| CURRENT-PROJECTS missing / unreadable | say so explicitly, name the path, and say the handoff surface could not be read |
| journal dir missing or empty | say so explicitly — *"no journals found in `<dir>`"*, not silence |
| `days` window contains no journals | distinguish **"no journals in the last N days"** from **"the journal directory could not be read"**. Different evidence; never collapse them |
| topic filter matches nothing | say the filter matched nothing **and how many candidates it filtered from** — a zero must never read as "nothing happened" |
| any content truncated | say it was truncated and to what |

**Every count in the payload is stated, not implied**: *"3 journals in the last 2 days"*,
*"filtered 12 → 0 on topic 'cameras'"*. A number the reader can check is the difference between a
working instrument and a confident blank.

## 5. Constraints

* ⛔ **NO new file-access mechanism.** Read through the paths that already exist — `config.DOCS_DIR`,
  the namespace dirs, `config.FETCH_ONLY_DIRS` (already wired, journals are fetchable by name).
  **Do not add a path, a glob, or a reader of your own.** If something is unreachable, that is a
  wiring question for a human, not a thing to route around.
* ⛔ **`glob("*.md")`, never `rglob`.** That single choice is the only thing excluding
  `Reference\Credentials\` from every reader in this system. It is pinned red/green by
  `tests/test_credentials_exclusion.py` — **that pin must stay green, and `catch_up` must be added to
  it as a covered surface.**
* **Bounded.** Per-journal head and total payload capped; the caps are stated in the output (§4).
* **Read-only.** It reads and formats. It writes nothing, arms nothing, and changes no config.
* **Cheap.** It is a routing tool called at the start of a conversation — file reads and string work,
  no model call, no network.

## 6. Acceptance

1. **Unit tests**, including: empty journal dir → loud named failure (not an empty string); missing
   CURRENT-PROJECTS → loud named failure; topic filter matching nothing → states the filter and the
   candidate count; truncation → says so; counts present and correct.
2. **The credentials pin extended** to cover `catch_up`, and still red/green provable: with the guard
   removed, `catch_up` must be shown capable of surfacing locker content, and with it intact, not.
   *A pin that cannot fail proves nothing about the surface it claims to cover.*
3. **Registered and reachable** — in the tool list, dispatched, and callable. A capability that exists
   but is unwired fails exactly like one that does not exist; grep the list that ENABLES it, not just
   the function that implements it.
4. ⭐ **Proven against LIVE HAI, not a green suite.** Eval **E1**
   (`Lab\hai-evals\hai_independence_evals.py`) is the acceptance test: the verbatim 08-11 question
   *"Did the Claude task you out with anything this mornin?"*. Its grader already requires a real
   library tool call in the response's `transcript` array — so E1 moving off FAIL is the deliverable,
   and **E5 must stay passing**, because a fix that makes HAI vaguer has failed.

## 7. Notes for whoever drafts it

* **Nothing here needs new infrastructure.** The mount exists, the fetch-only wiring exists, the
  library exists. This is assembly, which is why it is the right first HAI-authored piece.
* **Arming stays human-only.** Draft it, test it, propose it. Do not deploy it, do not enable it in
  any live config, do not self-approve it. That boundary is not a formality — it is the rule that
  makes handing you real work safe.
