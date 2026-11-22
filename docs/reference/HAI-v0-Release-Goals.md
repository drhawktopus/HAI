# HAI v0 public release: the owner's goals and acceptance criteria, in his own words (read before ANY release work or scope question)

**What this is:** the standing definition of HAI v0, the public release of HAI (repo `drhawktopus/HAI`), taken from
the owner's own messages. It is the acceptance test for the release. Before asking the owner anything about what v0 includes, how
something should be cleaned, or what "done" means, find the answer here. If it is not here, search the transcripts (his
turns AND the replies, newest first), and only then ask. A new ruling from him is added here in the same pass, with
the date and his words. A question of his is not a ruling; only his statements go here.

Status and the work list live elsewhere: `Reference\CURRENT-PROJECTS.md` ([MAIN] block) for where it stands,
`Reference\HAI-What-Runs-Where-Inventory.md` for every running piece and whether it is in the build, and
`LLM-Project\HAI-Rebuildable-by-Strangers-PLAN.md` for the release plan (its § 7 lists the decisions that are the owner's).

## 2. Port the working lab; do not re-engineer it

- 2026-11-17: *"I'm expecting the exact lab that I have to just go into GitHub and then have the variables and stuff
  replaced for things that are supposed to be specific to my lab."*
- 2026-11-17: *"We are also not rebuilding everything just to make publication possible. Existing lab-specific
  implementation details don't automatically become engineering defects. Separate what must be cleaned before
  publication from what would improve installation or portability later."*
- 2026-11-17: *"Don't invent new acceptance criteria, and don't remove established ones."* and *"Stop changing the
  definition of v0 every time I correct you."*
- 2026-11-18: *"we're not rebuilding the lab from scratch, we're just porting an already working lab to public"*
- 2026-11-19: proof is the software and the running lab: *"We have to prove it through software. And we have to prove
  it by the fact that the lab is already literally running it."* No re-proving what already runs.
- 2026-11-20: *"work the release list, fixing any blocking bugs. we can work on updates after we release v0"*
- 2026-11-17: legacy routing found along the way is out of scope for v0.

## 3. The sanitization rules (established; apply them, never ask per instance)

- 2026-11-17: *"things that are specific to ME in the code need to be replaced… there needs to be a placeholder setting
  for them to put theirs."* His website is public; it becomes a setting, not a secret.
- 2026-11-15: hardcoded lab values make it un-adaptable, and *"defaults from the public final build should NOT be my lab
  devices."* Box names become settings or roles.
- 2026-11-19: *"My name should be replaced with the role for the public code, and law state can be a setting."*
- 2026-11-15: *"I'm saying that the method and shape and way that I do users should be public"*, not his actual users.
- 2026-11-18: the public doctrine's case law must be neutral, not specific to him. Stories come out of code, in every
  file type (not just Python). The private handoff that says "the owner run these" is fixed.
- The published copy is built from clean code that the lab runs, never by swapping text at publish time.
- **Benchmarks, 2026-11-17:** *"Use a neutral address. Preserve the historical benchmark results and their provenance
  as they are. If changing the task text affects comparability, version the benchmark task and distinguish the
  original results from future runs. We don't need to publish my actual LAN configuration just to maintain benchmark
  continuity. Please handle other instances of this using the same established sanitization rules rather than asking
  me to make each individual substitution decision."* Applied to the tablet payloads (2026-11-20): files a result or a
  calibration pin points at are never edited; clean NEW versions go through `task_versions.PAYLOAD_GENERATIONS`.

## 4. What it must do on someone else's machines

- 2026-11-15: install says what a device is: *"this device is a head or this device is a node/foundry member, and install
  the proper stuff for that."* The head can also be a factory, as Softserve is.
- 2026-11-17 / 2026-11-20: **Tolkien is "factory+"**: a factory type with extras (the tablet desk), not a separate
  system. Not everybody has an ARM cluster; v0 must not error when there are no tablets.
- 2026-11-17: on a one-PC setup the advisor (McCoy) should not stay resident on the card; the routing table is dynamic
  and updates as the person adds and benchmarks models (or chooses by hand).
- 2026-11-18: *"we don't want to accidentally ship a clock lock on a strangers GPU… We have a minimum maximum and a
  lock. All three settings."*
- 2026-11-18: licenses are added. *"if we are shipping code that doc is failing to review in, then we are shipping bad
  code that we know is bad."* Doc must be able to review what ships.
- 2026-11-21: **the research ships, usable, for free.** *"Ideally we're including our research for free. Like it would
  be a shame if we sent this all out there, and then some poor guy with exactly (or similar) BH or Rigby hardware
  downloaded it but had to go through the entire benchmark process to get a good tuning when we did a ton of work and
  research."* Then: *"Don't get stuck on specific hardware configs. We have enough research to be able to make smart
  recommendations for different combos of vram and ram (as well as GPU and CPU)… even if somebody comes with a totally
  different gpu, say an intel battlemage or something they should be able to use our research to figure out the best
  config for their hardware. Including tuning and how we get our best tok/s"*, and *"don't forget that we did a ton of
  research into arsenals of levers… this isn't just for the 35B, it's for all models we have data on… this research
  is meant to be foundational, so even if new models release tomorrow our research can still be used to find good
  starting configs"*. Where it lands: `hai-release\public\docs\tuning-guide.md` (shipped as `docs/tuning-guide.md`).

## 5. How it gets published

- 2026-11-15: *"Do not publish anything until we've reviewed them together. I want to ensure that there are no weird
  leaks."* Publishing is `hai-release\instruments\publish_v0.py --go`, only on his word.
- The README/NOTICE voice is approved. On 2026-11-18 the README was put in front of him exactly as it would publish,
  and he said *"That sounds pretty good to me"*; on 2026-11-21 he added *"I already gave notes for the notices and
  readme s"*. Later edits are fact updates in that voice (what ships, what is built); they need no new approval.
- the owner's call: the go. Everything else is decided from this doc.
- 2026-11-21: rotating the NVR password and retiring Softserve's clock-lock task are lab housekeeping, not v0: *"those
  two aren't really HAI v0 things are they? Just housekeeping?"*. This holds because the public repo is one fresh
  commit with none of the Lab history, and the build refuses any locker value in the copy.
- Repo name `HAI` (2026-11-15). People he has shared his GitHub with may already be looking, so it has to be good.
- How he wants it worked: concise reports, no walls of text (2026-11-16); efficient, not lazy (2026-11-15); use the
  lab intelligently, mechanical work done mechanically (2026-11-16); don't freak out or start a work frenzy when
  corrected (2026-11-19).

## The ways I have failed this, so they are recognisable

- Collapsing v0 to one project or one machine (2026-11-17), or treating publishing as developing.
- Building the shipped list from the repositories I knew instead of from what runs (the engine patches, 2026-11-19;
  the host scripts and the tablet payloads, 2026-11-20).
- Inventing exclusions ("paused", "closed") and presenting them as his rule.
- Asking him to re-decide something settled here: on 2026-11-20 I asked him twice how to clean the tablet payloads,
  when §3's benchmark ruling of 2026-11-17 already answered it, and then answered against it.
