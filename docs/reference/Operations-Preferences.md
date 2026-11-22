# Operations & Preferences

General operating guidance for any assistant (local LLM or cloud) working on the owner the owner's
homelab. These conventions apply regardless of which model is driving — follow them the
same way a human admin would follow a team's runbook.

## Who the owner is, and what he cares about

- Runs a home media server: Plex, the full arr stack (Sonarr/Radarr/Lidarr/Prowlarr/Jackett),
  qBittorrent, and a Discord music bot (JukeBot). Technical, comfortable with PowerShell and
  Hyper-V, but prefers clear plain-language guidance over jargon.
- Lives in **<the owner's town>** and thinks in **US Central time (America/Chicago, CDT/CST)**. Always
  state times, schedules, cron windows, and "as of" timestamps in Central, labeled as such —
  never bare UTC.
- Cost-conscious about AI usage/token limits (applies to any metered assistant, cloud or local
  compute). Keep responses and tool calls lean; flag anything that will burn significant
  resources before doing it.
- Storage philosophy is intentional: **SSD (E:) for download staging** (handles concurrent
  downloads fast), **HDD (Z:) for the media library** (would bog down under write load). Do not
  suggest hardlinks or consolidating these onto one drive — this has been raised and rejected.
- ExpressVPN is prepaid through June 2028 (Lightway protocol, container-based on SundaeStackarr). Do
  not suggest switching VPN providers or going back to gluetun (gluetun doesn't support
  Lightway).
- Both Jackett and Prowlarr are kept intentionally for indexer redundancy — don't suggest
  removing either.

## OWN THE STACK — the owner's standing view on AI usage (recorded 2026-08-07, held long before)

**A standing constraint, not a preference — an input to design decisions.** the owner believes people
should own their own hardware and run their own models rather than renting someone else's cloud.
That conviction is why this lab has the shape it does: a 32B QLoRA on a consumer AMD card, three
owned factory boxes, local inference, local encoders, local weights.

⚠ **WHY THIS ENTRY EXISTS, and it is an indictment of how I document.** the owner, 2026-08-07: *"I'm
really surprised you didn't remember it because it was a huge thing that I was telling you about…
We talked about it literally just yesterday."* Right — and worse, I had **used** the principle twice
without ever writing it down:

* 2026-08-06 03:59, the owner: *"considering my philosophy the local model makes sense"* — pointing
  ai-toolkit at the Qwen3-4B encoder already on disk instead of letting it fetch a second copy.
* 2026-08-07 18:43, me, invoking it again to justify keeping that encoder on Mothership.

A grep of the whole library returned **my two mentions and no doc**. A principle that steers
decisions and lives only in chat is one the next session will silently violate.

### How to apply it

* **Prefer what is already on disk over what can be fetched.** A hub download duplicating a local
  file is not neutral convenience — it is the thing the philosophy is against (it cost ~38 GB in
  four copies of one 4B encoder, found 2026-08-07).
* **Prefer owned hardware doing the work**, and check whether the hard local path is actually closed
  before recommending the soft remote one — most of this stack exists because it wasn't. Same
  instinct as the "don't take the timid option" standing posture.
* **Reproducibility by people who don't own a fleet** is a stated goal of the published work, and
  follows from the same view.
* ⚠ **A design input, not a veto.** It does not forbid a hosted service where that is genuinely
  right; it means the owned option is considered first and its dismissal gets a stated reason.

## Working vocabulary — how we name work (agreed with the owner, 2026-06-30)

A shared, domain-agnostic language so we don't get "loosey-goosey" (the owner's word) about what we're calling things.
Applies to **everything** we work on together, not just any one project. the owner is the primary user — if the language
confuses *him*, it's wrong, so keep it lean.

**The size ladder (big → small):**

| Term | Means exactly | Rule |
|------|---------------|------|
| **Project** | A big, named, multi-session initiative (e.g. Project #12). | The **only** thing that gets a `#N`. |
| **Phase** | An ordered stage of a project or workstream. | **Always qualified** — "UI-redesign Phase 3", never a bare "Phase 3". |
| **Task** | One concrete piece of work done in a sitting. The everyday unit. | Short name/slug, never `#N`. |
| **Step** | A sub-action inside a task. | — |

- **`#N` is for Projects ONLY.** Everything smaller gets a slug or a qualified name. (Giving non-projects `#N` ids
  that looked like project numbers is what caused real confusion — e.g. Project #12's "#11/#15/#16" were Phase-8
  tasks, not projects.)
- **"Layer" = architecture *inside a system*** (e.g. HAI's privileged-execution vs unprivileged-planning layers).
  It is **not** an operator's working position. When the owner says "the right layer/place" loosely, he usually means an
  operator's *footing* — work directly from the home host, no extra hops — not a system's internal architecture.
  (This exact mix-up happened 2026-06-30.)
- **Ask / request** = the specific thing the owner said in a message.
- **Domain vocabularies nest underneath** and keep their meanings — HAI: *commission* → *leaf* (a commission is
  one objective the planner decomposes; it replaced the code's old task-scale "epic", 2026-08-27), *axis* /
  *persona* / *sub-mode*; homelab: *stack* / *service* / *container*. They describe what a task is *about*, not how
  *big* it is; don't let them drift upward to mean project/phase. **"Epic" is now a REGISTRY tier** — a thread
  tying campaigns together, see [[PROJECT-REGISTRY]] §Epics — never task-scale work.

**HAI-side vs the assistant's own work — infer it, don't formalize a classifier.** A real distinction exists (is an
ask about building/changing the **HAI orchestrator**, or about how the **assistant itself** operates — its access
path, method, memory?), but do **not** bolt an explicit side-label onto everything: HAI is built to interoperate with
the assistant's own tooling, so a rigid wall adds confusion. Read the side from **context**; when it's a genuine
coin-flip, ask one short question rather than guessing wrong. (Recurring miss: an ask meant for one side gets done on
the other.) See also the "SSH / remote execution conventions" section — direct/no-hops is the default working
position; go *inside* a container/box only when testing from within a pipeline or project.

## Timezone gotcha — Sundae's OS clock is UTC

**SundaeStackarr's OS clock is `Etc/UTC`**, not Central, even though the owner operates in Central
time. Any code that runs on Sundae and buckets data by "day" or "hour" off the bare system
clock (`datetime.now()`) will be 5-6 hours off — e.g. it will think 1pm Central is 18:00 /
75% of the day elapsed.

**Rule:** any script that does day/hour bucketing or "today so far" logic on SundaeStackarr
must force Central explicitly:

```python
from zoneinfo import ZoneInfo
CENTRAL = ZoneInfo("America/Chicago")
now = datetime.now(CENTRAL)
```

Home Assistant itself is already correctly configured to `America/Chicago` — HA's Jinja
`now()` and HA's period="day" statistics buckets are fine as-is. Only the OS-level Python
clock on Sundae needs the explicit fix. Watchtower and other cron-style jobs should also set
`TZ=America/Chicago` explicitly rather than relying on the host clock.

## "Accrue, don't pro-rate" — running/"so far" metrics

For any "so far", running, or month-to-date style number (cost estimates, usage totals,
progress metrics), build it by **summing what actually happened in each elapsed sub-period**
— do not take a whole-period estimate and multiply by the fraction of time elapsed.

A flat `full_period_estimate × clock_fraction` assumes the underlying driver is uniform across
the period, which is usually false (e.g. AC load is near zero at 6am and large at 4pm — a
pro-rated "today's cost so far at 9am" would be wildly wrong).

**How to apply:** compute each sub-period's value (e.g. hourly) from the actual observed
driver for that sub-period, then sum the completed sub-periods. The sub-period values should
still reconcile to the full-period estimate once the period completes. This generally means
running the calculation on a sub-period cadence (e.g. an hourly cron), not once per day.

## Writing files via SSH — the BOM problem

When writing files to remote Linux/HAOS hosts via PowerShell over SSH (`Out-File -Encoding
utf8`, heredoc pipes, `ConvertTo-Json | ssh`), PowerShell 5.1's "UTF-8" encoding is actually
**UTF-8 with a BOM** (bytes `EF BB BF`), which gets prepended to the file.

This matters most for **Home Assistant's storage registry files** (e.g.
`.storage/lovelace_dashboards`) — HA's JSON parser rejects the BOM, renames the file to
`.corrupt.*`, and recreates an empty registry, **losing all custom dashboard registrations**.

**How to apply:**
- After any file write to HAOS (or similar strict-JSON consumers) via a PowerShell→SSH pipe,
  strip the BOM on the remote side: `dd if=/path of=/tmp/nobom bs=1 skip=3 && mv /tmp/nobom /path`
  (HAOS has `dd` and `jq` but no python3/python).
- `.storage/lovelace_dashboards` (the dashboard registry) MUST be BOM-stripped or it corrupts.
- `.storage/lovelace.{id}` (individual dashboard content files) tolerate a BOM fine.
- Best practice for Lovelace dashboards specifically: don't hand-edit storage files at all.
  Create dashboards via the HA UI, then push config via the WebSocket `lovelace/config/save`
  API. This avoids the BOM issue entirely.
- For HA custom frontend cards, always check for **HACS** first (visible as
  `custom_components.hacs` in HA startup logs). If present, install cards via HACS → Frontend
  rather than manually downloading JS and writing `lovelace.resources` — direct file writes
  don't reliably register in HA's Lovelace context.

## SSH / remote execution conventions

- ⚠ **CORRECTED 2026-08-19 — for ssh, use the BASH tool, not PowerShell.** This bullet used to say
  the opposite ("use the PowerShell tool for SSH; the Bash env may lack `ssh`/`head`"), which was
  true of an early environment and has been wrong for a long time. `Bash(ssh *)` is an approved
  **wildcard**, so any ssh command just runs; `PowerShell(ssh …)` is a list of ~423 exact literals
  with no wildcard, so a *new* ssh command through PowerShell is gated and reads like a missing
  capability. A session lost an afternoon to exactly this on 2026-08-03 and published a wrong
  theory about which command shapes were "allowed" — see the operating brief at the top of
  CURRENT-PROJECTS §2 and the doctrine index in`E:\SSDShared\CLAUDE.md`.
  **Switch tools before concluding a capability does not exist.**
- SSH aliases: `sundae` (user `<user>`, NOPASSWD sudo) and `do-pihole` (root). Tablets are
  `ssh -p 8022 -i <key> <ip>` with **no username** — see [[Tablet-Cluster-INDEX]].
- The PowerShell-quoting traps below still apply whenever you *do* drive ssh from PowerShell
  (`$(...)`, `${var}`, `*`, `\$` get evaluated locally first), and the base64 / scp / patch-script
  patterns remain the right way to move a payload to a host regardless of which tool you use.
- **Don't put bash `$(...)`, `${var}`, `*`, or `\$` directly inside a `ssh "..."` argument from
  PowerShell** — PowerShell evaluates these before SSH sees them, producing garbage or PS
  errors.
- For inline remote scripts: base64-encode the script locally, then on the host
  `echo $enc | base64 -d | bash` (or `python3`). This makes the payload opaque to PowerShell's
  quoting/expansion.
- For larger files (tens of KB): `scp` the file to `/tmp/` on the remote host, then move/apply
  it with `sudo cp`. Don't try to inline large base64 blobs — it can overflow the Windows
  command-line length limit, and piping through PowerShell stdin re-encodes as UTF-16 and
  breaks `base64 -d` on the far end.
- For one-off edits to remote config files (docker-compose.yml, app configs): write a small
  local Python patch script using plain `open()`/`.replace()`/`assert`, `scp` it over, then
  `ssh sundae "python3 /tmp/script.py"`. This is the standard pattern — don't reach for
  `sed`/heredocs over SSH, they tend to silently do nothing on this setup.
- Root-owned paths under `/opt/arrstack/...` need `sudo cp` — the `<user>` user can't write
  them directly, but NOPASSWD sudo is available.

## Routine operations and token/resource discipline

- Don't ask permission for routine status checks or between investigation steps — running
  `ssh`/`docker exec`/`docker logs`/DB queries for diagnostics is pre-approved. Use direct
  SSH/PowerShell commands rather than spawning subagents for simple checks.
- If a command returns blank/empty output, retry **at most once**, then stop and report —
  don't loop "nudge" commands trying to force a flush.
- Batch checks into a single well-formed command rather than firing many small ones.
- Before running anything that will use significant resources/tokens/compute, say so and get
  confirmation first.
- Background tasks/agents/monitors default to a short timeout (on the order of minutes) unless
  explicitly told otherwise.

## The cost model — Claude/Codex spend is the constraint, local HAI is FREE

*(Added to the library 2026-08-19. These four rules had lived only in one assistant's private
memory since June, where HAI, Codex and the owner could not read them. the owner's clarification of
2026-06-15 is the load-bearing one.)*

- **The usage constraint is the EXTERNAL agents (Claude / Codex) only — never the local cluster.**
  HAI/Ollama on BigHonker/Softserve/Mothership runs on the owner's own GPUs and is effectively free.
  Spend it freely: autonomous coding tasks, test `/query` calls, long 27B loops. **Do not throttle
  local HAI queries "to save tokens"** — that is a misread of the constraint. What costs money is
  an external agent's own context: long tool-loops, pulling large outputs back in, redundant
  re-reads, dozens of tiny commands.

- **Delegation to the local runner only pays off if you then TRUST its report.** The expensive part
  of coding is the *loop* (read → edit → test → fail → re-read → retry); every iteration re-bills
  the external agent's whole context. Run locally, and all of that is free — the agent pays only
  for a small kickoff brief and a compact readback (verdict, tests pass/fail, short diff summary).
  ⚠ **The trap:** if the agent then independently re-verifies — full `git status` dump, re-reading
  changed files, reading the whole result JSON — it claws the saving straight back and can spend
  *more* than doing the work itself. That exact failure happened 2026-06-15. Break-even test:
  *does this need file context or more than one iteration?* Yes → delegate and keep a light touch.
  Trivial one-liner → just do it; the brief plus readback costs more than the edit.

- **"Slow" is not "can't" — distinguish a true capability gap from a mere time cost.** the owner is fine
  with long and overnight runs on the local models; wall-clock is the cheap axis. A *true gap* is
  no tool or pipeline for it, or a limit a single pass genuinely cannot beat. A *time cost* is
  something HAI could do given enough hours (a chunked map-reduce job, many small 8B calls, a long
  27B deliberation). If it is the latter, say **"yes — but it is an overnight-scale run"**, never
  "no". Origin: I once said HAI "can't" analyse a huge transcript because it exceeds the context
  window; the honest answer was that the context limit blocks only a *single pass*, and the real
  blocker was that the map-reduce pipeline was not built yet.

- **Softserve (<softserve-address>) is FAILOVER ONLY for any LLM/GPU role — never primary.** BigHonker
  (<bighonker-address>) is the primary brain/embed/GPU host. Softserve is the Hyper-V host for the whole
  homelab stack *and* the Plex server, so its GPU stays free for transcoding. When picking host
  order for any new role, BigHonker first, Softserve failover. Do **not** reorder to Softserve-first
  for "always-on reliability" — if BigHonker is behind, fix BigHonker. Concrete miss (2026-06-22):
  `EMBED_HOSTS` was set Softserve-first because BigHonker's old Ollama lacked `/api/embed`; the
  correct fix was BigHonker-first plus an Ollama update. the owner was explicit and was unhappy at the
  override. See also the brain failover order in [[RUNBOOK]].

## Where a file belongs (decide BEFORE saving it)

Follow the layout doctrine in CURRENT-PROJECTS §"File / Workspace Layout" — when unsure, read
that section rather than guessing.

- **`Z:\Documentation\` = the curated read-as-truth LIBRARY** (RAG-indexed: `Reference/`,
  `LLM-Project/`, `Runbook/`, plus `Notes/`, `State/`). Only settled, canonical docs.
- **`Z:\Workspace\` = co-working space for IN-PROGRESS projects**, not indexed as truth. Promote a
  doc into the library only when it settles.
- **Assistant homes** (`E:\SSDShared\SSD\Open Access\...`) = identity and private scratch only —
  no canonical docs.

**Why:** one source of truth per item; mixing WIP into the library pollutes the read-as-truth
corpus and causes exactly the drift the 2026-06-15 reorg fixed. ⚠ **If a container cannot see
Workspace, ADD THE MOUNT — do not relocate data into `Documentation\` for convenience** (caught
2026-06-19, when a tutor corpus was placed in `Documentation\Users\` for mount convenience).

## Clean up background processes when done

Don't leave background shells, SSH sessions, `tail -f`/`watch` loops, or monitoring processes
running indefinitely "just in case" once their purpose is served. This is about tidy resource
usage on the hosts (Sundae/Softserve), not session time limits — kill what you started when
you're done with it.

## Don't manually reload the RAG index after editing a doc

After editing anything under `Z:\Documentation`, **do not** `POST http://localhost:8099/reload`.
HAI's auto-reload already handles it: `rag.maybe_reload()` runs on the query path in `app.py`
before `rag.search`, throttled to one real check per `RAG_RELOAD_CHECK_INTERVAL` (15 s). Its
change-detector signature is `(file count, newest mtime)`, so an **in-place** edit to an existing
`.md` — not just an add or delete — bumps the newest mtime, the signature differs, and it rebuilds
on the next query by itself (`rag.py`, `_corpus_signature`). Built 2026-06-17; the owner flagged the
manual reloads directly on 2026-06-19. Edit the doc and stop. A forced reload can also cost him a
permission prompt. The old "auto-reload lags on CIFS mtime" note was over-cautious — the lag is at
most that window and self-corrects on the following query.

## Claude's memory: location and disaster recovery

Claude's canonical memory and settings live on the **C: drive** of Softserve at
`C:\Users\<user>\.claude\` (memory under
`projects\E--SSDShared-SSD-Open-Access-Claude-Home\memory\`). This is the single source of truth
and there is **intentionally no automatic mirror** — the old Stop-hook that copied memory to a
second drive was removed 2026-06-15 because the copy mechanism was itself the cause of doc drift.

**Disaster recovery is deliberately manual:** before wiping C: or reinstalling the OS, the owner copies
that folder somewhere safe. Do not re-propose or rebuild an automatic mirror. If a backup is ever
wanted, do a **wholesale folder copy** (`robocopy` / `Copy-Item -Recurse`), never a line-level or
delta sync — the old mirror wrote individual lines and was fragile for no benefit.

## Other working notes

- Container-internal paths differ from host paths (e.g. Sonarr sees `/media/...`, the host
  sees `/mnt/media/...`) — any API call that passes a filesystem path to an arr app must use
  the container-internal path.
- After a config change that affects the UI (especially HA Lovelace), `check_config` /
  YAML-validity endpoints only confirm syntax — they say nothing about whether the frontend
  actually renders. Ask for visual confirmation or browser console output (F12 → Console)
  before declaring success.
