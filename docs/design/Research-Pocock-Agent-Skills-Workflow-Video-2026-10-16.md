# Research note — "Ship like the best engineers: Matt Pocock's AI workflow" (The Hype Kevin, 2026-10-09): the grill → spec → tickets → build → review → retro loop, against how this lab works

**EXTERNAL SOURCE NOTE — not a lab measurement.** An explainer of Matt Pocock's open-source agent-skills repo, checked against the repo itself and read against the lab's own docs. It is about **how work is specified, split, built, reviewed and improved**, not about any one model, so it bears on the whole lab (Claude's own sessions, HAI's coding lanes, A1, the self-engineering and decompose drivers). Related lab docs: [[12-AUTO-DECOMPOSE-DRIVER-DESIGN]], [[06-SELF-ENGINEERING-AGENT-PLAN]], [[Subagent-Delegation-Published-Guidance]], [[34-A1-Agentic-Build-Family-Spec]].

Filed 2026-10-16 by the side session at the owner's request. Raw files: `Claude Home\research\video-Q3uuE2adUT8\`.

**Tags:** **[D]** stated in the video · **[V]** checked against the repo or its release notes · **[L]** the lab's own docs · **[M]** I measured it for this note · **[I]** my inference · **[?]** not opened.

## The video

| | |
|---|---|
| Uploaded | 2026-10-09 · 9 min 5 s · 17 chapters · 419,296 views at pull time |
| Captions | **creator-written English** (accurate; the auto-captions were also kept as `*.auto.en.srt`). Narration is an AI clone of the creator's own voice; the coffee-subscription "Pause button" feature is the creator's illustration, the ten-ticket run and pull requests are real (from Matt's `course-video-manager` repo) |
| Subject | `mattpocock/skills`, v1.3 (shipped 2026-10-04) |

## The flow, as the video describes it (and the repo confirms)

1. **Grill.** Before any code, the agent interviews you. It maps the plan as a **tree of decisions**, finds the **frontier** (decisions whose parents are settled) and asks **all of them in one numbered round**, each with the agent's **recommended answer**, worded so "yes" accepts it. Facts are the agent's job (a helper agent reads the code); decisions are the user's. It may not start until the frontier is empty and you agree you share understanding. Typical: ~6 questions on a small change, ~20 on a normal one, 46 once. **[V]** the `grilling` skill says exactly this ("Ask the whole frontier in one round", "Finding facts is your job, never the user's", "Do not act on it until the user confirms").
2. **Glossary** (`grill-with-docs`): the interview keeps a project glossary — one word per thing, with "words to avoid". `wait-what` re-pitches the last message in plain English using that glossary. **[V]** both exist.
3. **Ungrillable questions** (what should the screen look like) go to a **throwaway prototype** with several unlike screens; you pick; the answer returns to the interview. **[V]** `prototype` skill.
4. **`/to-spec`** writes the settled decisions as a numbered list of user stories, agrees the **test seams** with you first (ideal count: one), and publishes as an issue labelled ready-for-agent. The spec is the destination. **[V]** (the repo page is itself unclear whether `to-spec` interviews or only synthesises).
5. **`/to-tickets`** slices the spec into **tracer-bullet** tickets — each a vertical slice through every layer — with blocking edges. Sizing: each slice "sized to fit in a single fresh context window". **[V]** the skill says that; it gives **no token number**. The video's "past ~150,000 tokens the agent stops reasoning sharply — the smart zone, a term from Dex Horthy" and "divide the job's tokens by 150,000" are **[D]** only.
6. **Build:** `implement` one ticket per clean chat, or `implement-spec` (new in 1.3): one agent per ready ticket, each in its own worktree, all landing on one integration branch. **Test first** at the agreed seam. The refactoring step was dropped because the agent "never, ever, ever did it". **[V]** release notes: "The goal is now the integration branch, not a PR."
7. **Review at two desks:** `code-review` hands the diff to **two fresh agents in parallel** — a **spec** reviewer (missing / unrequested / looks-done-but-wrong) and a **standards** reviewer (documented standards plus Fowler's smell baseline). **The spec reviewer is not given the standards; the standards reviewer is not given the spec.** Reports are shown separately, never merged ("reporting them separately stops one axis from masking the other"). **[V]** per the skill file.
8. **`/pr`** writes the PR body: the smallest explanatory picture, before/after proof, and a **merge-danger** call — one-way or two-way door, blast radius. **[V]** (adapted from Dex Horthy's `show-me`).
9. **`/retro`** (new in 1.3), before you clear the chat: the agent reads back through the session logs and suggests changes to its **environment** — navigation pointers, automated checks, standards, steering files that are large or no-ops, tool economy, missing information. **Rule: a mechanical violation gets a deterministic check ("default to building the check over writing the rule"); written standards keep only judgement calls.** Presented in severity order; **it only suggests** — the user picks. **[V]**; the video's "don't put retro on autopilot, an agent fixing its own findings chases false positives" is **[D]** (the skill text is silent on it).
10. **Hygiene:** the spec is closed and discarded after shipping — "a projection of decisions now living in the code, tests and glossary"; skills that models no longer need are **deleted** (`resolving-merge-conflicts` was removed in 1.3). 16 of the 27 skills are user-invoked only, so the agent never sees them until you type the name; the full installed set cost **660 tokens** in the author's tutorial **[?]**.

**Checks on the numbers [V]:** the repo is MIT, shows **~293.8 K stars**, and lists **27 skills — 16 user-invoked, 11 model-invoked** (the video's "sixteen of the twenty-seven" reproduces). `grill-me`'s "more than a million installs" and the 41-story / ten-ticket / 28-commit / ~14,000-line run were not opened **[?]**.

## Against the lab [L]

**Where the lab already earned the same rule — independently:**

| Video | The lab's own record |
|---|---|
| "You wrote one sentence, so it filled every gap with a guess" | **Packet-contract rule** ([[12-AUTO-DECOMPOSE-DRIVER-DESIGN]]): "the coding workspace is the model's ENTIRE world — any contract whose source lives outside it must be embedded in the packet VERBATIM, or the model invents a plausible dialect and its own mocks pin the invention green". Commission 3 also invented a row-array key. Same failure, same fix: state the decisions before building |
| Tickets sized to a fresh context; the agent worsens as it holds more | **Leaf-sizing rule** (same doc): left alone the planner sized leaves by topic, "and ~90 minutes in the 27B degraded into malformed tool calls ... and burned both attempts"; "the driver's whole premise is a fresh context per leaf". The lab measured a degradation; it has **not** measured quality against window fill (see below) |
| Review by an agent that did not write the code | **BLIND REVIEWER CLASS** (CLAUDE.md doctrine; [[03-CODING-AGENT-PLAN]]): "a reviewer judges ONLY what it is handed". The video adds a second half the lab's rule does not state: **split the reviewers by axis and hide each axis from the other** |
| "A mechanical violation gets a deterministic check" | The lab's **hooks** (`no_factory_floor.py`, `no_file_authoring_in_shell.py`, `no_long_foreground_calls.py`, `no_unbracketed_pattern_kill.py`) and doctrine 5a ("which is why this is now a HOOK, not a row"). The lab applies the rule; it does so **after** a mistake costs a round-trip, not as a routine pass |
| The spec is deleted after shipping | **Doctrine 21**: the live library holds current state only; superseded text moves to `Archive\` |
| "Decisions are yours, facts are the agent's" | CLAUDE.md "library first, then docstrings, then ask the owner"; "DON'T invent the owner's goals — say 'I'm assuming X' out loud"; "decide within the agreed frame" |
| One word for one thing; `wait-what` | Memory files for working vocabulary and component names; the owner's plain-language rule for the main session (and today's "break it down more concisely") |

**Where they differ, and the tension to resolve [I]:**

- **Asking vs not asking.** The lab's rules push the agent to *stop asking* what the library or the frame already answers. The grill pushes it to *ask the whole frontier up front*. They reconcile if the grill is limited to what the lab's rule leaves: genuine decisions, **batched in one numbered round with a recommended answer** ("yes, yes, no") — which also suits the owner multitasking.
- **Always-loaded vs load-on-demand.** The video's skills cost nothing until called. The lab's steering is the opposite: `E:\SSDShared\CLAUDE.md` is **146,011 characters (about 36,500 tokens at 4 characters per token) and 117 table lines**, and the memory index adds **17,042 bytes (~4,300 tokens)**, loaded into **every** session **[M]**. Against the video's ~150 K "smart zone" **[D]** that is about a quarter before any work starts; I did not check this session's actual window, so the share may be smaller. The retro skill names exactly this case — "global steering file that is large", "no-op steering instructions", "default to building the check over writing the rule". Whether any lab row is a no-op, or could become a hook or an on-demand skill, was **not examined**. The Skill tool is present in these sessions.
- **Rows only accumulate.** The doctrine index has an add rule ("when a new class of mistake costs a round-trip, add a row") and no retire rule; the one precedent (5a: a row became a hook) was ad hoc. Matt deletes skills when the models catch up.
- **Retro is routine; the lab's equivalent is reactive.** The lab's journals and "repeating a mistake → remove the environmental inconsistency" rule are the same instinct, triggered by a failure rather than by the session ending.

## Candidate adoptions (design questions, not decisions; none queued)

1. **A suggest-only end-of-session retro** for substantial sessions: read the transcript, list environment fixes in severity order (new hook, pointer, retired row), the owner picks. The lab already has the machinery (hooks, transcript search); this would be a habit and a prompt, not a build. Matt's own caveat applies: suggest, never auto-apply.
2. **Frontier-style batching for design-heavy asks:** one numbered round, recommended answers, facts looked up first. A change of style, no infrastructure; fits the lab's "condensed responses, end with the ask".
3. **Two-axis blind review** for the lab's own code changes and for HAI's critic lane: a spec reviewer and a standards reviewer, each fresh, each missing the other's input. The video's counter-intuitive extra — *hide the coding standards from the implementer* (Matt's Sep 18 post **[?]**) — is a one-variable arm A1 could test.
4. **A merge-danger line in deploy procedures:** "one-way or two-way door, blast radius" costs one sentence per deploy and matches how the lab already thinks about orchestrator and factory deploys.
5. **A context-budget look at the steering files** (the 36.5 K-token figure above): which rows are mechanical enough for a hook, which belong on demand. A question for the owner, since CLAUDE.md is his file and the lab's own rule is that docs win.
6. **Measure quality against window fill on the local 3.8** (the "smart zone" for *our* models). The lab has the degradation anecdote (90 min, 27B) and the 122,880 window setting but no filled-window quality curve; the same gap came up on the VRAM video. It would feed Phase 7's placed-window cost.
7. **A shared glossary page** in the vault instead of two memory files.

## Cautions

The "~150,000 tokens" smart-zone figure and the "past it, reasoning degrades" claim are the video's (and Dex Horthy's), not the skill's, and are not measured on any model the lab runs. The star counts, install counts and the 660-token figure are the creator's. The creator calls the flow a one-person author's habits, shipped for others to adapt; "adapt" is the repo's own word.

## Files

`Claude Home\research\video-Q3uuE2adUT8\` — `Q3uuE2adUT8.en.srt` (creator captions) · `Q3uuE2adUT8.auto.en.srt` · `transcript.txt` · `Q3uuE2adUT8.description` (carries the source list: the repo, release, X posts, real spec/PR/merge-danger examples) · `Q3uuE2adUT8.info.json`. Pulled with yt-dlp inside the Pinchflat container on Sundae (`--write-subs` for the creator captions).
