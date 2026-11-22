# Desk time / system time — where a Fellowship order's time goes before and around the work (Tolkien pickup delay, inbox wait, per-order overhead)

the owner's follow-up mini project from #34 (2026-10-23: *"'desk time' or 'system time' etc is taking up a bunch of time"*;
2026-10-25: *"15sec-almost 4 minutes is a lot…"*). Baseline handed on from #34 ([[34-CAMPAIGN-CONCLUSION]] residuals,
[[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] § E): rigby desk overhead 10.2 s, per-order server start 3.0 s, a tablet 28.6 s,
**Tolkien inbox pickup 15–229 s**, the 3.8's cold load ~45 s, a new chat window ~9 s to first output, McCoy's first call
after idle ~2.5 s.

## Measured 2026-10-25 (read-only; desk records + Tolkien's archive/results)

There are TWO doors into Tolkien, and they behave differently:

| door | who uses it | how Tolkien sees it | measured pickup |
|---|---|---|---|
| **the desk** (`/p20/jobs?host=fellowship`, HTTP) | HAI's chat turns (`fellowship_turn`), titles, placed delegates | asked every 1 s between passes (`tablet_runner._idle_wait`, QUICK_POLL_S) | `created → claimed` median **0.9 s**, p90 1.45 s, max 45 s (n = 88 `hai-fel-` orders, 10-23 → 10-25; two unexplained outliers, 16 s and 45 s, no logs left) |
| **the inbox** (`tabfoundry-jobs\inbox\*.tjob.json`, a share) | order submitters (menu filler, embed checks, Claude's orders) | read ONLY by the 30 s full pass (`drain_inbox` in `run_queue`) — not by the 1 s ask | unblocked orders since 10-23: median **17.8 s**, p90 28.9 s, max 247 s (n = 16); 13 menu-filler drops on 10-23 evening: 0.7 – 26.1 s, the full pass's phase |

The big inbox numbers (247 s, 200 s, 236 s) line up with Tolkien being RECREATED: a fresh runner waits for the old
runner's heartbeat to go quiet (~90 s; 72 s measured on 10-25 01:53) before it takes the lane — inferred for the
10-23 cases (their container logs are gone), measured for today's recreate.

After pickup, a HAI turn on rigby spends `claimed → done` median **10.4 s** (p90 20.5 s): the per-order llama-server
start (~3.0 s), the run's control (~2.4 s), the turn itself, the delivery. That per-order start + control is most of
the 10.2 s "desk overhead" the menu prices on Fellowship rows (`order_overhead_s`).

## Fixed 2026-10-25

- **The inbox joined the 1 s ask** (`tablet_runner._fleet_quick_check`; Tolkien gate 962, deployed 13:54Z). drain_inbox
  is single-writer (its own lock) and the stop watches already called it every 5 s while an order ran.
  **Proof:** an embed-check order (`submit_embedcheck_fellowship.py`) dropped 13:58:11.86Z, Tolkien "picking up" at
  13:58:11.80Z — inside the same second (the two clocks agree within ~1 s).

- **A live chat window no longer sleeps 3 s before serving** (`helper_window`: the idle-power sample was a fixed
  `time.sleep(3)` between the model load and the first turn of EVERY new window; its only reader is the bench rung
  report). A LIVE window now serves at once and takes the sample from its first quiet gap (`idle_gap`); measurement
  windows keep the up-front sample. fleet_deploy to all three GPU factories 2026-10-25 13:46 (foundry suite 553 green
  on each). Live: a new Mothership 3.8 window's helper started 13:48:44 (after the deploy) and went from the clean load
  straight to its first turn; one cold sample (72.7 s to first output, 20.1 s of it reading 14,169 tokens) cannot
  isolate 3 s from load variance — the code path and the tests are the proof.

## Fixed 2026-10-30

- **rigby's model load after a restart:** the 35B (17.2 GB, SATA SSD) loads in ~60 s cold vs ~12 s from the page cache.
  A host timer now reads it back into the page cache while rigby is idle (evictable, never locked — the owner: the warmer,
  not a lock): [[RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04]] § "rigby's MODEL WARMER". Live decoy: 0 % → 100 % cached in
  43 s. Found by a 4-hourly claim-review order that paid 60 s of load for 18 s of work after rigby restarted at ~02:08.
- **A Tolkien deploy: ~217 s → 62 s** (the owner: *"surely the lab can do it faster than that"*; *"shipping should be fast,
  my network is strong"*). Measured first (`fleet_deploy_tolkien` now stamps every line with its elapsed time):
  - **Handover 90 s → 0 s.** A clean stop makes the order taker RELEASE its lock and exit; the next one takes a released
    lock at once unless the old one beat after the release (never two takers). Tolkien `dc7255a`, gate 1008; proven on
    two deploys: *"previous instance … released the lock 1s ago on a clean stop - taking over"*.
  - **Recreate 15 s → 8 s.** Every container's PID-1 Python now handles SIGTERM (`tablet_foundry.install_stop`); PID 1
    ignores it without a handler, so docker waited its 10 s grace before SIGKILL.
  - **Ship 33–34 s → 15 s.** Not the network (~96 MB/s: 664 MB through both ssh hops in 6.9 s) and not docker
    (save 3.9 s, the stream 173 MB; load 5.8 s): Python handing one ssh's stdout to the other's stdin on Windows moved
    173 MB in 22–27 s (a 4 MiB chunk copy in Python: 402 s). Now one Git Bash pipe (mothership-build `82e536d`).
  - Left: build + gate ~30 s (the 1008-test suite is ~26 s of it).

## The GPU factories' new-window open, measured 2026-10-25 (n = 13 windows, foundry + desk logs, ±1 s)

Every new window in the sample RELOADED its model (the previous window ended at `--idle-ttl` = the box's keep_alive,
and the model unloaded with it): first output 67–77 s (Mothership qwen3.8), 22–25 s (BigHonker nemotron 4B / qwen3:8b),
48.5 s (BigHonker split 35B). Fixed overhead before the load: open → desk create ~0.4 s, create → claim ~1.0 s,
collected → "picking up" 3–4 s (the runner's full pass — patrols, capability announce — runs before the pickup),
picking up → spawn ~4 s; open → helper start 8–13 s (Mothership), 6–8 s (BigHonker). After that: the 3 s idle sample
(now removed for live windows), the residency probe (~1.7 s on BigHonker), the clean load, the prompt read. Warm turns
on an open window: first output ≈ prompt read + 0.5–1.2 s.

## The pass before a pickup, and the desk it asks: measured and fixed 2026-11-14 (the owner: "We may as well do it, and is that sequencing still going to achieve the same effect and guards that I am aiming for?")

**Every guard still runs before every pickup, in the same order: control slips (stop / cancel / priority), the capability
assessment, the trespass patrol, the GPU police.** Nothing was moved behind the pickup. The 10-25 proposal to run the
patrols after it (archived: `Archive\desk-time-open-item-0-resolved-20261114.md`) was not built: measured, the patrols
were not where the time went, and the police cannot safely run beside a launch (it could see the factory's own
just-started window before the lane claim exists).

**The runner now says what its pass spent, on every pickup** (`p20_foundry.pass_report`, the `[QUEUE] picking up … (N
queued; pass …)` line in `<foundry>\var\log\foundry_queue.log`). Mothership, same day:

| | pass before the pickup | collected → picking up |
|---|---|---|
| before | `pass 2.4 s: controls 0.9, collect 0.6, patrol 0.5, police 0.4` | 2–4 s (every window that day) |
| after | `pass 1.0 s: patrol 0.5, police 0.4` | 1 s |

What changed, and where:
- **The factory** (`p20_foundry.run_queue`, all three GPU boxes, mothership-build `5932747`): the pass that follows the
  quick poll's collect no longer asks the desk for orders a second time (the same question within about a second).
  Pinned: `test_the_pass_after_a_quick_collect_keeps_every_guard_and_does_not_ask_the_desk_twice`.
- **The desk** (`p20_queue._shared_records`, Kirk `e06016b` + `d06a8f3`): the half seconds in `controls` and `collect`
  were Kirk's, not the network's (15 ms round trip from Mothership). Every ask from every factory (about four a second)
  parsed all 4,853 queue records (median 0.77 s, worst 4.86 s in a quiet minute) and opened all 450 control slips
  (median 1.05 s); work log line `DESK POLL slow` (any ask of 0.3 s or more, by part). Now a record is read and parsed
  once per change of its file, callers get copies of what they asked for, and asks that arrive together share one scan
  unless Kirk wrote a record meanwhile. Steady state after: an occasional 0.4–0.8 s when the share's attribute cache
  expires (about every 30 s).
- **Wrong guesses, each refuted by one measurement before anything was built on it:** the GPU police's process
  enumeration (0.42–0.46 s, not 3); Windows proxy lookups in the factory's web client (a GET to Kirk is 15 ms).

**The first desk ask after a Kirk restart: 13–14.6 s → 0.36–0.52 s (2026-11-14 evening; the owner: "We gotta fix the desk
poll after restart. We have an archive for a reason...and we also know how to use caches").** Both, in `p20_queue`:
- **The saved cache.** The stopping Kirk writes its parsed records to `<queue>\cache\parsed-records.json`
  (`save_snapshot`, from the shutdown hook; also every 10 minutes while something changed); the next process loads that
  ONE file before its first scan and re-reads only a record whose fingerprint moved (`_load_snapshot`). A stale or missing
  file costs a read, never a wrong answer: every entry is checked against its file. The page's own listing
  (`all_jobs`) takes a record's bytes from the same cache instead of opening it again.
- **The archive.** Finished factory orders (`done`, `failed`, `cancelled`, `expired`) and collected control slips older
  than `ARCHIVE_AFTER_DAYS` (30) are MOVED, never deleted, to **`State\p20-render-queue-archive\<YYYY-MM>\`** (slips under
  its `controls\`), beside the queue - the same shape as Kirk's run saves (`hai-runs-saved` → `hai-runs-archive`). A
  sweep runs two minutes after Kirk starts and then daily (`app._desk_housekeeping`, log line `DESK ARCHIVE`). Kept in
  place: renders and quick previews (the gallery lists them with no cutoff), anything not finished, and any record a
  waiting order still names in `after_ids`. `p20_queue.get(id)` finds an archived record, so a lookup by id is unchanged
  (checked live: `GET /orders/card/00c54772c471`, a September order, answered from the archive). First sweep: 2,263
  orders and 343 slips moved; 2,594 records and 107 slips left in the scanned folders.
  **The 30 days: the owner's ruling, 2026-11-14** (*"thirty seems fine"*). It began as the builder's number (no reader was
  found looking further back); it is not a measurement.

**Still open here:**
1. **Picking up → the window program: ~2–3 s** (the order's spec, the card check that asks the desk, preflight, the
   interpreter starting). Not measured by step.

3. **The two unexplained desk-path collects** (16 s, 45 s) — need Tolkien to log what its full pass spent time on.
4. **Pickup is not logged with the drop time** — Tolkien's container log lives ~7 h and is lost on recreate; an
   "inbox saw X (written at T)" line would make this measurable from Tolkien's own record.
