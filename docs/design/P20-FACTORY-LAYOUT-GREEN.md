# P20 factory layout — the green rebuild (✅ BUILT + LIVE)

**Status: CUT OVER 2026-07-27 23:15. `F:\foundry` is the live factory.** As-built deviations from the
design below: the cutover took blue's CURRENT modules and path-rewrote them into `F:\foundry\bin`
(the earlier copied fork had drifted 5.5 KB behind blue and was retired — one codebase, not two);
`output\image-lora\` holds flat per-run folders rather than the capability/person split; models stay
at `F:\flux2-train` (design-intended: it is now ONLY a model cache). The library path sweep (§30
references) was completed 2026-07-29, along with the post-cutover stragglers that survived the first
restore pass: person-record adapter paths (all 8 pointed at deleted blue output — HAI person renders
were broken until repointed), `dataset-sources.json` never copied to green bin (runner silently fell
back to built-ins), `render_progress.json` reader+writer both still blue (mercy rule dead), and the
telemetry logger's newest-log fallback (removed; stated-run only). Details: Journal 2026-07-28/29.
The rest of this document is the original design, kept as the record of what was decided and why.

the owner, 2026-07-27: *"you have been making folders containing things kind of wherever seems convenient
to you rather than a folder structure that either has already been established or makes sense... the
folder entitled P20 should not have the entire contents of P20's mechanics, that should have
documentation about it."* And on approach: *"what if we just built a second factory now that we know
the correct things? Like do a copy but with the structure fixed."*

## Why in-place migration is the wrong move

Paths are hardcoded literals scattered across the codebase (`COND_DIR`, `LORAS`, `RESCUE_LOG`,
`CURRENT_RUN`, `QUEUE_DIR`, plus scheduled-task command lines and `.cmd` launchers). Moving files
before centralising paths breaks everything at once with no single place to fix it. Building green
alongside means blue keeps working until green has fulfilled a real order.

## What the survey found (2026-07-27)

```
F:\flux2-train\  top level:
  13 loose .py         the mechanics, in the project root
  21 loose .log
  ~45 .bak-<adhoc>     bak-pose-100417, bak-eta-…, bak-onecadence-…  (names invented per edit)
  .env .ps1 .bat .cmd .err .out, 2 .pt caches
jobs\:  spec 6 · state 5 · log 7 · backup 9 · other 6 · + queue\ + archive\
```

Five kinds of thing in `jobs\`; the program's code beside its logs and its secrets.

⚠ **8 of the 13 `.py` files are dead or one-off**, including TWO older copies of the foundry
(`p20_foundry_v01_backup.py`, `p20_foundry_v036_staged.py`) sitting next to the live one. Nothing
prevents running the wrong foundry. Green does not migrate these — retirement is the point.

**Live mechanics (the only things green needs):** `p20_foundry.py`, `p20_dataset.py`,
`p20_render_worker.py`, `p20_preview_render_slim.py`, `p20_bake_cond.py`.

## Proposed structure

```
F:\p20\
  bin\              the 5 live modules, nothing else
  etc\              dm_relay.env, run_*.cmd launchers, paths config
  var\
    log\            every *.log (foundry_queue.log, trainer logs, worker logs)
    state\          CURRENT_RUN.json, telemetry\, stall_rescue.*
    cache\          cond_cache\, mistral\, p20_embeds_*.pt
  jobs\
    queue\          pending orders          <- submission = drop a spec here
    active\         the order being fulfilled (one; the card is one lane)
    archive\        completed + failed orders
    state\          *.job.state.json
    log\            *.job.foundry.log
  output\           checkpoints — the PRODUCTS
  backup\           every .bak-*, in dated subfolders
  doc\              README.md pointing at Z:\Documentation (the docs live there, not here)
```

The project-named folder holds **structure and a pointer to documentation**. Mechanics live in
`bin\`, runtime state in `var\`, orders in `jobs\` — conventional install shape.

## What moves, what is pointed at

| | treatment | why |
|---|---|---|
| 5 live `.py` | **copy** to `bin\` | small; blue keeps its copies until cutover |
| launchers, `dm_relay.env` | **copy** to `etc\` | rewritten to the new paths |
| `mistral\` 48.1 GB | **point at blue** | copying 48 GB to prove a layout is absurd |
| `output\` 26.0 GB | **point at blue** | products; move at cutover, or leave and repoint |
| `cond_cache\` 0.2 GB | **copy** | small, and green should own its cache |
| 8 dead `.py`, ~45 `.bak-*` | **do not migrate** | archive in place; green starts clean |

## `paths.py` is the actual deliverable

One module, every location derived from a single root, nothing hardcoded anywhere else:

```
ROOT = os.environ.get("P20_ROOT", r"F:\p20")
BIN, ETC        = ROOT/bin, ROOT/etc
LOG_DIR         = ROOT/var/log
STATE_DIR       = ROOT/var/state
CACHE_DIR       = ROOT/var/cache          # COND_DIR, MISTRAL, embeds live under here
QUEUE, ACTIVE, ARCHIVE = ROOT/jobs/{queue,active,archive}
OUTPUT          = ROOT/output
```

`P20_ROOT` as an env var is what makes blue/green possible at all: the same code runs either factory
depending on one variable, so green is not a fork that drifts.

## Cutover and rollback

1. Build green, empty. Point the big caches at blue.
2. **Acceptance test: submit a real order** — a small probe — to green's queue and watch it complete
   end to end. The factory testing itself is the same acceptance the queue already provides.
3. Cutover = **disable blue's runner task, enable green's**. Rollback = the reverse. One switch.
4. Blue stays intact and runnable for a stated period before anything is deleted.

## ⚠ The hard rule this creates

**The card is a single lane, so blue and green must NEVER run at the same time.** Two order takers
watching two queues would start two jobs on one GPU — the exact collision class eliminated on
2026-07-27 (`foreign_run_guard`, the stated-run claim, policy 12). Mutual exclusion is enforced two
ways, because a rule that depends on remembering is not a rule:

- Only one runner scheduled task is ever enabled.
- The runner itself checks the stated run / claim on startup and **refuses to consume** if the other
  factory holds the card.

## ⚠ The LIBRARY is a path consumer too — 30 references across 6 docs

Added after the owner pointed out this should already be covered in the library (2026-07-27). Checking
rather than assuming found the gap in this very document: I had scoped the migration to *code*, and
the docs hardcode the same paths.

| doc | `F:\flux2-train` references |
|---|---|
| [[Flux2-32B-QLoRA-AMD-ROCm-Windows]] (the recipe) | 8 |
| [[RUNBOOK]] | 7 |
| CURRENT-PROJECTS | 7 |
| [[P20-FOUNDRY-RUNNER-PLAN]] | 6 |
| PROJECT-20-ROADMAP, [[CorridorKey-Ops]] | 1 each |

**A migration that updates only the code leaves the library lying**, and these are RAG-indexed — the
local model would answer with paths that no longer exist. So doc updates are a migration STEP, not
follow-up tidying: green is not "done" until all 30 references are correct.

This is also an argument for `P20_ROOT`: docs can then say `%P20_ROOT%\jobs\queue`, which stays true
across a future move instead of needing another 30-reference sweep.

**Prior art in the library — this document does not supersede it:**
- [[P20-FOUNDRY-RUNNER-PLAN]] §157 already specifies the queue
  (`jobs\queue\*.job.yaml`, consumed serially, one card = one lane). That is the intake, built
  2026-07-27; this document describes where the rest of the factory lives around it.
- [[P20-FOUNDRY-RUNNER-PLAN]] §126 has the phase state machine, which green inherits unchanged.
- The recipe owns the training mechanics; nothing here changes them.

## Decisions (the owner, 2026-07-27)

**Scope: this is not a P20 factory.** *"the final shape of the factory will be to train multiple AI
models for different things"* — image LoRAs now, text models later on the same rig. So the root is
named for the FUNCTION, not the project, and output is organised by modality from the start.

**Root: `F:\foundry\`.** the owner offered "AI Foundry" or "The Forge". Forge is rejected on a concrete
ground: **Stable Diffusion WebUI Forge is an existing product in this exact domain**, so every
search, doc line and error message would be ambiguous with it — the same defect as a cache keyed
`default`. "Foundry" is already the vocabulary in the code (`p20_foundry.py`, `*.foundry.log`) and in
six library docs, so it costs nothing to keep and re-teaches nobody.

**Models stay put.** `mistral\` and the model caches remain on their current drives — they are there
for speed and have caching configured. Green points at them permanently, not just during transition.
This is not a transitional shortcut; it is the design.

**Blue archives immediately** after cutover, to the Softserve HDD array.
⚠ **Pull from Softserve, do not push from Mothership** — an ssh session on Mothership is a network
logon and cannot see mapped drives or Credential Manager ([[RUNBOOK]] §"SMB from Mothership",
2026-07-26). Run the copy from the Softserve side, or from a Mothership task with `/ru <user> /it`.

## Output layout

Products and byproducts separate, per the product-order model — and organised by modality so a text
model has an obvious home the day it exists:

Today every render lands in `F:\comfyui-rocm\output\` mixed with all ComfyUI history — 33 v7 previews
among unrelated work, distinguishable only by filename prefix. Per-order render folders make "what
did this order ship" answerable by listing one directory.

## Open questions for the owner

- Root name: `F:\p20\` as above, or keep `flux2-train` semantics? The former reads as "the project",
  the latter as "the thing it trains".
- Does `output\` move at cutover, or stay in place permanently with green pointing at it? Moving
  26 GB is cheap on the same volume (rename), expensive across volumes.
- Retention: how long does blue stay runnable after cutover before it is archived?
