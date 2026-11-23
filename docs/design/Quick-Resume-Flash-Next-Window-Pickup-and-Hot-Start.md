# Quick resume — window pickup across a HAI restart, Flash-Next state on Strata, the hot start (design + status, 2026-11-04)

**Why this exists.** the owner's standard for any save, pause or deploy is Xbox Quick Resume: the exact live state, picked up
exactly, bit identical. It was met for ollama's engine and rigby in October (register B11, B11b;
[[RUNBOOK-Ollama-Host-Operations]] § SLOT SAVE, § LAB ENGINE). It was never met for the model jobs actually run on:
Flash-Next, on Strata. On 2026-11-04 two restarts of HAI cost a real job its deliverable, and the hot start the owner asked for
on 2026-10-25 turned out to be in no document. the owner: *"the bit identical resume that we spent ages building for the other
stuff"*, *"Fix it, build it"*, *"All three as one plan"*.

## What a HAI restart keeps, by layer (current state, 2026-11-11)

A Kirk deploy needs no park and waits for nothing: every layer below survives a restart at any moment. A deploy script
that waits for a quiet moment is covering a hole; find the layer and fix it here instead (2026-11-07: mine waited out
compactions for a day). The 11-04 table this replaced is in `Archive\quick-resume-restart-table-2026-11-04.md`.

**A resumed call gets its OWN saved record** (since 2026-11-16, `dc28c3e`): each top-level `chat()` save carries a
signature (the first line of its system prompt + its sorted tool names), and on resume a call takes the first record
not yet handed out with its own signature (`run_ledger._top_key_for`). A call that does not recur on resume (a thread
compaction that already rewrote the history) can no longer shift the run's loop onto its record; records saved before
signatures keep positional matching. Tests: `test_run_resume.ATopLevelCallIsMatchedToItsOwnRecord`. The hole it closed
is in `Archive\quick-resume-known-hole-call-identity-superseded-20261116.md`.

**Two different things are called a park; only one is the owner's.** `/runs/park` is the OLD deploy park: it waits for the turn
or tool in flight to END before it holds (a 27B turn at Max: 10 minutes). Nothing here needs it, and it must not go back
into a restart. The instant one is `/runs/suspend` (`chat_window.park_turn`): it cuts mid-token in seconds and the turn
continues from that token (§ "The mid-turn dump on the fleet boxes"). the owner, 2026-11-11: *"I want the bit identical stuff
so that I can do the instant … pauses and resumes whenever and not have to wait forever on things."* On 11-11 a plain
restart lost a run (the window row below), and for three restarts afterwards the old wait was used instead of fixing and
returning to the plain restart. The tool is `Lab\bin\orch_recreate.sh`: it restarts and then reads what the notes say.

| layer | how it survives |
|---|---|
| The conversation (messages, tool results) | run ledger, saved before every model turn and between tools |
| The tool list the conversation was sent | **every conversation's block is kept on disk WITH ITS TEXT** (`State\tool-blocks\<conversation key>.json`, `ollama_client._held_get/_held_put`, 11-14) and resent verbatim by the next process; a resumed job also carries its names in the ledger (11-04). Before 11-14 the held list of an ordinary chat lived only in Kirk's memory, so every deploy made the next turn of every open chat pick afresh and re-read everything (§ "Thaw, then season") |
| The window (engine + model + read state) | adopted at startup from `WINDOW.json` + `OWNER.json` (11-04). **The stopping Kirk announces the restart** (`chat_window.going_down` on shutdown, Kirk `1d7c51b`, 11-11): each open window's last beat declares the 60 s period, so it waits 180 s for the next process. Before that only a deploy that called `/runs/park` announced it, and a plain recreate depended on Kirk being back inside 45 s: on 11-11 it was silent 46 s, the Flash-Next window was not picked up, and run-q was lost 4 h in. Proven live 11-11 12:55 with NO park: `restart announced to 1 open window(s)`, picked up at 34 s, `RUN RESUMED`, `TOOL LIST kept across the restart: 41 of 41`, a `coding_render` cut by the restart `waiting for the same work` and resumed |
| A pinned job whose window is NOT there after a restart (a crash, a longer outage) | it waits for the lab order holding the card, then reopens: `_pinned_brain` asks the desk (`_wait_while_held`) before `_ensure_lead`, whose gaming check reads the lab's own Strata engine as a game once its window belongs to a previous process (11-11; before it the pin was refused and the job ended). Covered by a test, not yet seen live |
| An answer being generated | the window keeps writing; the resumed run sends the identical request and `chat_window._claim_item` reconnects it, same bytes (11-04) |
| The job's `live` claim | the previous process's claims are released at startup (11-04) |
| **A compaction in flight** | checkpointed BEFORE it starts with the cut turn, and after each finished piece, so the resumed run redoes only the piece in flight with the identical request, which the window reconnects (`671da33`, 11-07; with two runs back on one window, every item any of them reconnected is kept, `0f90d0d`). Proven live 18:05 on rigby: restart 9 s into a summary, `COMPACTION redone after a restart`, the summary reconnected "still being written", `CONTEXT COMPACTED` |
| **The tool loop's own state** (finish gate, cut / text-call counts, idle detector, the run's counters) | saved with every checkpoint and restored (`671da33`, 11-07; before it a resumed coding job could hand over untested code) |
| **A tool call in flight** | a tool that only reads (`ollama_client.REPLAYABLE_TOOLS`), and `coding_test` (a fresh verdict on the same code), runs again; `coding_render` records the GPU order it placed and the resumed call waits for that same order (`tools.resume_tool`, `gpu_render.finish_page`); `coding_create` / `coding_patch` / `coding_file` (`tools.CHECKED_WRITES`) read their effect back from the workspace: already made = the call's result, said so; not made = it runs once (a patch is judged by the hash it was read at); anything else, or a runner that cannot be asked, is said plainly, never guessed (`94c23eb` + `a51785c`, 11-07; run-f had lost seven renders, box2-i two `coding_git` results). Render proven live 17:53: restart with page order `881184d94a70` queued on BigHonker, `waited for the same work`, real pictures filed, no second order. The write check is covered by tests, not yet seen live |
| **The notes added to a turn's request** (the clock line, the final-turns nudge) | checkpointed with the request before it is sent, so a resumed run sends the same bytes and the window reconnects the turn (`c947873`, 11-07; before it, at 18:36 the clock said "2 min used" where the cut request said "1 min", and a long turn was thrown away) |
| Not covered yet | a tool whose effect cannot be read back from the workspace and that records no handle (`coding_task`, `coding_checkpoint`, orders other than a render) cut mid-call: reported, not re-run. Each can record a handle the way `coding_render` does |

## Part 1 — HAI picks its windows back up (every factory; Kirk only)

The survey named this on 2026-10-21 (#5, `Claude Home\research\quick-resume-survey-20261021.md`): adopt the window; card,
model and RAM cache all survive; no dump needed. Nothing is restored, so the result is identical by construction.

1. **A window writes down what it is.** `WINDOW.json` beside `OWNER.json` at open (host, model, size, desk id, strata), and
   the heartbeat carries the runs using it.
2. **A parked deploy tells windows to wait.** A window ends when its owner is silent for 3 periods, and the owner declares
   its own period. While parked, Kirk declares a longer one, sized from the measured restart gap. A restart that does not
   arrive in time ends the window: the fallback is today's behaviour, never worse.
3. **Startup adopts, before any run resumes.** A folder with `WINDOW.json`, no CLOSE, a live owner and a desk order not
   terminal is registered again, its beat taken over at once. Its users are kept only for runs that are really resuming; a
   window nobody resumes goes IDLE and ends by its own rule.
4. **A resumed run goes back to its own window.** It skips triage and weighing: its box and model are already decided, and
   re-asking is how it got sent to a smaller window. The window closing path stays as the fallback for a window with no
   `WINDOW.json`.
5. **An answer still being written is reconnected, not asked again.** The window writes every chunk to the item's stream
   file on the share whether or not anyone reads it. A resumed turn whose messages equal an unfinished (or just finished)
   item follows that item, so the model never notices. Anything unfinished that no resumed run claims is cancelled.
6. **The tool list survives.** The ledger's checkpoint carries the tool names; a resume sends exactly that list.

**Proof, live, on a real job:** `CHAT WINDOW picked up`, same desk id, same engine PID before and after; the next turn
reads only its new tokens (engine `cache_n` / `prompt_n`); a recreate during generation reconnects with no second item.

## Part 2 — Flash-Next's own state when the ENGINE stops (Mothership; foundry)

**Strata 0.1.45 (what Mothership serves) already has it, and it is the same API as the ollama windows:** with
`--slot-save-path DIR` (or `"slot_save_path"` in its config), `POST /slots/0?action=save|restore {"filename": ...}` writes
and reads a session file: the running state, one checkpoint, every attention layer's K/V and the draft layer's K/V (Strata
`docs\DETAILS.md` § Session files; source `E:\SSDShared\lab-engine\strata-0.1.45`). About 1.2 GB per 63K tokens. A file is
bound to the engine version and the settings that change what it means; anything else is refused and reads as today.
⚠ **Strata's own docs say this has never been run on Windows, HIP or AMD.** That is the first measurement.

- The plan gains `slot_save_path` (Mothership's NVMe, the existing slot folder); `apply_serving` writes it into the config.
- `helper_window`: a Strata window uses `ConvSlots` like every other (`a.host` gives the port). Save when the chat goes idle,
  at a chat switch and at the window's close; restore when a chat comes back.
- Kirk sends `conv` for Strata and rigby windows too, and records the chat's home.
- **A factory stop no longer kills a live window.** Today a stop slip ends the process tree, so nothing is saved. A live
  window gets a `YIELD` marker instead: it finishes the turn in flight, saves, and exits. The deck wait stays status-based.

⚠ **What "identical" means here, said plainly.** Strata's docs: *"Exact future token replay across restarts is not
guaranteed: expert residency and CPU/GPU rounding can change later output."* So the proof order measures it, one variable at
a time: uninterrupted vs restored, same prompt, fixed sampler, on this card. On ROCm this box HAS been byte-identical before
(the llama.cpp ROCm bundle; the Krea resume, 10-26). On Vulkan it never was (the 3.8, proven statistically on 10-22). If
token equality fails on Strata, the lab's distribution method (r26, `engine_resume_dist_probe`) is the fallback, and the
report says which was reached. The rigby patches (#0 pending token, #2 RNG, #3 MTP) have no Strata equivalent, so cutting
an answer in the middle across an ENGINE restart is not lossless; a live window therefore finishes its turn before it stops.

## Part 3 — the hot start (every factory; foundry + Tolkien)

the owner, 2026-10-25: *"some sort of pre-saved state that the models load up… Essentially like a customized quick resume"*, on
each box's fastest drive; 2026-11-04: the model *"already has the system prompt 'read'"* and appends the new part.

**The mechanism** is a slot file holding a prompt's shared start, keyed by what it was read from (model, size, effort, the
leading system messages, the tool list), built while the card is idle and restored when a chat has no state of its own.
Never worse: a file that does not match reads as today. Per engine: Strata pins the boundary (`strata_prefix`, and a save
keeps the pinned checkpoint); ollama and rigby are fed the exact rendered text and the slot saved there.

**MEASURED 2026-11-04 (`first_turn_tools_probe.py`, replaying selection on 27 real first turns):**
- The prompt renders **tools first, then system text** (Strata `serve\chat_template.jinja`; the effort sentence comes first of
  all). So the shared start ends at the first tool that differs, and the system prompt is behind it.
- System text is byte-stable: 9,235 chars plain, 11,404 on the lead path, per user, no date in it.
- **26 of 27 first turns had a different tool list.** Always shared: the 12 core tools, ~2,300 tokens. A standing list that
  covers 90 % of first turns would be 70–83 tools, ~17–20K tokens, against a 5,990-token budget on the 8 GB boxes.
- Read speeds: 27B 683 tok/s, A3B on BigHonker 1,486, 9B 2,609–2,843, rigby's A3B 299.

**So what a hot start can save depends on the tool list. the owner chose on 2026-11-14 ("Let's build the master chat"):** the
block is the always-offered core and Starfleet's picks arrive in the conversation, so the whole start (the tools and the
system text) is shared and tool growth stops re-reading (§ "Thaw, then season" → "The master start"). Starfleet still
picks for every message, so "a dynamic toolset that is chosen by starfleet" and "it's supposed to see sets" hold. The
three options as they stood before the choice are in `Archive\quick-resume-hot-start-options-20261104.md`.

## Build order and where each proof runs

1. Part 1 deploys to Kirk; proved on the running cookie job (the owner: verify the infrastructure on it, then clear it).
2. The cookie job is stopped through the front door and cleared, freeing Mothership.
3. Parts 2 and 3 go through `fleet_deploy` and Tolkien's gate; proof orders on the free card.
4. A fresh cookie run starts on the finished setup.

## Status

**Part 1: LIVE and PROVEN, 2026-11-05 00:07 CT** (orchestrator gate 3413, on the cookie job's Flash-Next window on Mothership,
turn generating, no park at all). The service was gone for 18 s (`recreate 06:06:53`, health `06:07:11`). HAI's own log:
`CHAT WINDOW picked up … desk=af16be56cd9c (claimed, 1 item(s), owner silent 17 s …)`, `TOOL LIST kept across the restart: 32
of 32 tools`, `turn t00001-afa8b020 reconnected after the restart (still being written), not asked again`. Same window folder,
same desk id, one item, and the engine kept writing straight through (context 98K → 103K). The answer that straddled the restart
reached the run (its tool call ran at 00:08). **The next turn read 10,635 new tokens in 9.3 s and reused 80,555** (engine
`prompt_n` / `cache_n`); the same window's first turn, before the fix was in force, had read all 80,560 in 67.9 s.

**Part 3, the hot start: bit identical to a cold read on Softserve, 2026-11-05 00:47 CT.** 9B at 47,104, real tools and a real system
prompt, every arm its own window and its own engine load, temperature 0. The first build, cut where the shared text ended (2,268 tokens):
read 1.7–1.9 s against 2.4–2.7 s cold over 6 rounds, ranges not overlapping, **and the answers differed**. The control separated it: cold =
cold and hot = hot at every comparison, hot ≠ cold at the same character per tool list (67, 315). The hot start itself changed the
answer, so the instrument was sound. Cause, from the engine's source: reads fall in multiples of `n_batch` from token 0 and rounding depends on
where those batches fall. Fix: cut on that grid with a tail that lands the rollback checkpoint on the cut (RUNBOOK-Ollama-Host-Operations §
THE HOT START). Same tool lists, one variable changed: **hot = cold on both lists, cold = cold, hot = hot**, read 1.7–1.9 s against
2.4–2.6 s. Saves about 0.7 s on this model. Tool: `Lab\bin\hot_start_proof.py --controls`.
**rigby, a second engine and a different grid, 01:10 CT** (A3B at 32,768, MTP, `-ub 2048`): the same flip. Old cut: hot ≠ cold at 138 and
39. New cut: **hot = cold on both lists**, and the hashes equal the cold runs from before the fix. First read 16.5 s against 22 s, 2,048
tokens already read. ⚠ rigby's real chat turns carry no tools or system message, so this is proved and ready, not yet saving anything there.

**Part 2, Flash-Next's state across an engine stop: BIT IDENTICAL, 2026-11-05 01:32 CT** (order `strata-resume-probe.20261105-012114`,
`workspace\flashnext\strata_resume_probe.py`, delivered to `campaigns\quickresume-20261105`). A 114,511-token conversation on the serving plan.
Save 2.0–20.5 s, 1.98 GB; restore 0.9–1.9 s. **Two full stops and starts of the engine, restore, next turn: read 53 tokens in 0.45 s, against
114,564 tokens in 78.6 s with no restore, and every answer equal to its reference at temperature 0 and at the serving temperature 0.6 with a
fixed seed.** The engine also repeats itself with no restore at all on this plan (`--adapt-every 0 --pcie-frac 0`), which is what makes the
comparison meaningful; the two temperatures' texts differ, so the check can tell answers apart. Strata's docs warn neither of these is
guaranteed: on this card, at this plan, both hold. It was the first run of session files on Windows, HIP or AMD by their docs' own account.

⚠ **Session files must not go on Mothership's C:.** The first live proof saved to `C:\ProgramData\HAI\slots`: the 2.2 GB file landed at 00:41:19, the
box's telemetry stopped at 00:41:09 and did not resume until 00:57:12, the call never returned in its 600 s, and the stuck job's run ended unfinished.
That is the system-volume stall of [[RUNBOOK-GPU-Host-Power-and-Glances]] § sleep-crash and `runs/mothership-hang-20261104/FINDINGS.md`. An
engine kill 15 minutes earlier caused no gap, so the save is the likely trigger, not the teardown (inferred from timing; no dump). On F: (`F:\HAI\slots`,
the plan's `slot_save_path`), the same size saves in 2–20 s with **no stall in the load report across two probe runs and six engine starts**.
**Ollama's chat saves on Mothership moved to F: the same morning, 2026-11-05 06:04** (the owner: "Move them to F:"; `Lab\bin\ms_move_ollama_slots.py`, 26 files, 15.86 GB, 100 s). Proved the way the stall appeared
(`Lab\bin\ollama_long_save_proof.py`): a 95,392-token 27B chat, first read 296.8 s; saved as its window closed, **4.19 GB in 108 s to F: with no telemetry gap over 40 s and no service-timeout or disk-reset events**; a new window
restored it in 2.6 s and read in 0.39 s (`state_from: own`), and its next answer was right. The save is still slower than Flash-Next's (2 GB in 2–20 s on the same disk); not traced. A window can spend that long closing on a very long chat, against a 5-minute re-read.
A save overwrites the chat's file in place, so a save that dies midway costs a full re-read of that chat, never a wrong answer (the engine clears the slot on a failed restore).

**The whole path, through real desk orders, 01:38 CT** (`Lab\bin\flashnext_yield_proof.py`): a chat on a live Strata window, the standard stop slip, the
window finished, saved and exited by itself **23.5 s** after the slip, no stall. A new window with a new engine: the turn's own row says
`state_from: own`, **27 tokens read in 0.23 s with 6,666 reused**, where its cold first read took 11.3 s. A second slip still ends a window at once.

**Part 3 on Mothership's 27B, 02:08 CT** (Vulkan, 125,952, cut 2,048): hot = cold = itself on both lists, at temperature 0 and at 0.6 with a fixed seed, 16 arms,
no divergence. Engine call 8.1 s against 10.9 s; the state work before it costs 1.4 s, so **a new chat's first turn nets 1.4 s** (the 570 MB file restores in about 0.9 s).

**What the hot start is worth, measured:** 27B 1.4 s net. rigby's A3B 5.5 s off the read, but its real chat turns carry no tools today, so nothing there has a shared
start yet. Softserve's 9B nothing: a restore (1.9 s) loses to re-reading 2,048 tokens (0.8 s), and the turn's `read_tok_s` makes the window choose the re-read by
itself. BigHonker: not run, a logged-in desktop held the card (the fit rule keeps the 9B off beside ~1 GB of desktop). Flash-Next: skipped when only part of the tool list is
shared (`helper_window.HotStarts._spec`: Strata pins at a message boundary, and a cut inside the tool list needs a raw-text or
tokenizer entry it has no endpoint for); a chat whose whole tool list matches the primer does restore it. **The real fix, the owner
2026-11-07** (*"the real fix is to patch the engine so that we can save anytime"*, parked for that day as worth ~1.6 s): a Strata
patch that reads raw text up to an exact token and saves there, the same kind of change as `strata_continue_patch.py`, whose
mid-answer save already proves the session file can hold any point. **BUILT the same evening and DEPLOYED 18:36 CT at the owner's word (Kirk master `a51785c`; foundry `helper_window.py` + `strata_server_lab.py`
with --no-restart, foundry pin `72a857e`): live from each box's next window and next engine launch; not yet proven on the
card:** Strata already pinned at an exact token (`strata_prefix` {"tokens": N}); the gap was that nothing could ask for "through
the first K tools". `lab-engine\patches\strata_prefix_tools_patch.py` adds `{"tools": K}` to the lab server's
`resolve_prefix` (Strata's suite 872 OK, 19 skipped, `serve\test_lab_prefix_tools.py`), and `HotStarts._spec` asks for it
(mirror `ad2a188`, hot-start tests 23 OK). Same evening, Kirk `984f29e`: a factory's YIELD may now park a Flash-Next turn
mid-token too (gated off by a "Strata cannot continue a cut" check that stopped being true on 11-06). **The larger gain, the whole start shared, is the master start of 2026-11-14** (§ "Thaw, then season" → "The master start": 5,120 tokens already in the engine for a new chat on the 27B, where the core-only start held 2,560).

**Mothership's 27B resumes token for token, 2026-11-05 07:01–07:06** (`engine_resume_probe.py --fresh-load --hang-up`, the owner's go: "Trace it after cookie"). Cut the way a real stop cuts, hanging up on a streamed whole answer, then save,
evict, reload, restore, continue with `lab_resume`: **LOSSLESS in all five arms, 160 tokens**: temperature 0.6 at cuts 34, 63, 65 and 94, temperature 0 at 67, read 0 tokens. The controls behaved: without `lab_resume` the temperature 0.6 arms diverge
1 to 11 tokens past the cut, and the whole answer run again from a fresh load matches 160 of 160 with the same draft counts. Compared on tokens; the per-token numbers are blank here (below).

**The "NOT LOSSLESS" recorded at 02:10 was the probe's own cut.** Asking for exactly `cut` tokens limits the engine's last drafted batch at the cut, which an uninterrupted run never does. Cut that way, temperature 0.6 drifts at a repeatable token (105 at cut 60, three times, also 121 and 122) while temperature 0 stays lossless. One variable, the way of cutting, separates those from the hang-up arms. **Measured:** the cut method. **Inferred:** the mechanism, rounding that follows batch shape (the same family as the read grid in § hot start); drafting off was not run.

**Three traps in that morning's probe, all fixed in it:** `--fresh-load` evicted only between runs, so each order after the first started on the last order's runner and voided itself (`differs at 27`); it evicts first now. A per-token number comparison reported "identical" while 159 of 160 rows were blank, because with drafting on the server records no candidates for drafted tokens: it now counts `rows_with_numbers`, and a claim that the numbers matched was withdrawn the same hour. And a same-engine second run still differs from a fresh one (10-22, 11-05): `--fresh-load` is what makes the comparison valid.

**Chat quick save and resume is live on all four factories and on Strata; this one path is narrower.** `lab_resume`, continuing an answer from the middle, is called only by rigby's whole orders (`tolkien_llm._resume_continue`), by no chat window. Chats do not need it for the normal stops: pickup never stops the engine, and a stop slip finishes the turn first and saves. A crash or forced kill leaves no save to resume from, so the turn is asked again on any box. **What wiring it into chats would need:** the engine runs 3–7 tokens past the hang-up before it notices, and a client must hold those or `lab_resume` refuses; the probe borrowed them from its reference. The 10-22 distribution proof (floor 0.00023, claim 0.00025) still holds for what it measured.

**F: against C:, same probe, same 317 MB save:** 4.7–5.7 s to F:, 30.6–31.5 s to C: last night (10-21 measured 725 MB in 3.1 s on C:; why C: slowed was not traced, and it no longer carries these).

## The short-list test (the owner's go, 2026-11-05: "Test a short list")

**Why:** the tool block renders first, so a block chosen per request means no two chats share a start and a block that grows re-reads the conversation. Fix the block and the whole start can be saved
once per box, and growth stops costing anything. **What binds it:** the owner 2026-10-21, *"a dynamic toolset that is chosen by starfleet"* (a freeze idea was rejected), 2026-10-19 *"No arbitrary limits on tools"*, and
08-26 *"it's supposed to see sets"*. Nothing real changes until quality is measured.

**Three shapes, one model, the same request** (`workspace\toolshape\tool_shape_probe.py`, ordered with `Lab\bin\submit_tool_shape.py`): **A** today, Starfleet's selection is the block. **B** Starfleet's selection arrives in the conversation: the block is
the core tools + `use_tool`, the definitions at the end of the user's turn. Same chooser, so the 10-21 rule holds, and this shape is the lab's own, not Anthropic's. **C** the model finds its own: core + `find_tools` (BM25 over the pool) + `use_tool`, families in the system text.
27 hand-labelled requests including 3 where no tool is right, plus real first messages scored by agreement with A. Stops at the first real tool; nothing is executed.

**Results on Softserve's 9B @ 47,104, 2026-11-05 03:06 CT** (5 runs of 39 requests × 3 shapes, thinking off seeds 7/8/9, on seeds 7/8; `campaigns\toolshape-20261105`). ⚠ One small model, and not what jobs run on: the 27B and Flash-Next at their real effort come after the cookie run, and nothing real changes before that.

| shape | labelled right of 27 | read | whole turn, thinking off / on |
|---|---|---|---|
| A today | 20, 21, 21 / 20, 19 | 2.83 s | 3.7–4.2 s / 5.8–5.9 s |
| B Starfleet's choice in the conversation | 21, 22, 23 / 22, 22 | 0.83–1.03 s | 1.65–1.78 s / 3.0–3.15 s |
| C finds its own | 9, 9, 9 / 11, 12 | 0.22–0.30 s | 1.03–1.11 s / 2.9–3.4 s |

**B got at least as many right as A in every run, and a new chat's turn takes about half the time, before any hot start**: reads fall because the fixed block is already in the engine. **C does not work on this model:** with thinking off it never called `find_tools`; with thinking on it did on 6–7 rows and reached the right tool on 2–3. That is this model, not a verdict on the idea. **The control:** an arm agrees with itself across seeds 32–36 of 39; A and B agree 27–33 of 39, so B really does choose differently. **What it changes, read row by row:** better: `code_propose_cleanup` 3 of 5 against 0, "show me the candidates" 5 of 5 against 3, `docker_logs` and `library_carve_h1` sometimes where A never did. **Worse: a request for a real person's picture went to `generate_image` 5 of 5, where A chose `render_person` 3 of 5.** Different with no clear winner: "reboot mothership" (A answers without a tool, B calls `check_host` or `wake_host`); "export my transcript" (A answers, B looks in the docs). Both shapes miss the same hard ones ("Restart the sonarr container" never reached `docker_restart`).

**Results on Mothership's 27B @ 125,952, thinking xhigh, 2026-11-05 07:57 CT** (2 runs, seeds 7 and 8, the same 39 requests × 3 shapes, 47 minutes in all, no errors). A different tool pool than the 9B's (the 27B's own, 132 tools), so compare within a model, not across.

| shape | labelled right of 27 | read | whole turn |
|---|---|---|---|
| A today | 20, 20 | 10.5 s | 18.1–20.7 s |
| B Starfleet's choice in the conversation | 20, 20 | 3.3–4.0 s | 9.2–11.4 s |
| C finds its own | 20, 19 | 1.6–1.8 s | 5.8–9.8 s |

**On the 27B, B matched A on every labelled request in both runs and takes about half the time.** The 9B's one regression does not appear: the real-person picture went to `render_person` 6 of 6, every shape. **C works here, unlike on the 9B:** it used `find_tools` on 17–18 rows and reached the right tool on 11–12, gaining the two library lookups A and B both miss (`sonarr_lookup`, `radarr_lookup`), losing `quality_profiles` both runs and `disk_usage` and `plex_sections` once each. **The control:** an arm agrees with itself across the two seeds A 38, B 36, C 31 of 39; A and B agree 32–34 of 39. So C wanders more. All three miss the same hard ones: "Turn off the living room lights" never got a tool, "Propose a cleanup" and "Carve the H1 headings" went to `search_docs`, the docker logs and restart requests to `docker_status`.
**Results on Flash-Next (Strata, `qwen3.8-flash-next-strata-q2_0`), thinking xhigh, 2026-11-05 09:04 and 09:22 CT** (2 runs, seeds 7 and 8, the same 39 requests × 3 shapes, no errors; `toolshape-mo-xhigh-s7.20261105-084555-f827`, `…-s8.20261105-084600-3a91`. These landed after this section was first written and were not recorded until 2026-11-14, when the section still said Flash-Next had not been run).

| shape | labelled right of 27 | read | whole turn |
|---|---|---|---|
| A today | 18, 19 | 5.9 s | 8.0–8.3 s |
| B Starfleet's choice in the conversation | 20, 20 | 6.5–6.6 s | 8.7–8.8 s |
| C finds its own | 20, 19 | 5.3 s | 8.0 s |

**On Flash-Next, B was right at least as often as A in both runs; it is not faster on a fresh chat there** (each request is its own conversation and this engine did not reuse the fixed block between them). **How B's calls arrive, every model:** the system text told the model to call a described tool through `use_tool`, and it mostly called it by its own name instead, with the tool absent from the request's list: 10–11 such calls a run on the 27B, 16–17 on Flash-Next, 21–24 on the 9B, every one returned by the engine as a parsed call. On ollama's Qwen parser the arguments of such a call arrive as text (0 typed of 21 on the 27B); Strata typed some. That is the live decoy behind `ollama_client.can_season` (§ "Thaw, then season").
**What this test does not show:** two seeds and one tool choice each, stopping at the first real tool; nothing was executed. No row records its stop reason or thinking, so xhigh reaching the model is inferred from the engine accepting it, and no token cap was set. It measures single fresh turns, so it says nothing about a held conversation gaining a tool.

## Thaw, then season: a conversation's tool block is never rewritten (the owner 2026-11-14; LIVE on Kirk, proven on the 27B)

the owner, 2026-11-14: *"why can't it just resume, and once it's resumed it gets a little update? … instead of trying to add
new stuff to something 'frozen', thaw it out and then start cooking"*, and *"the running models only got tools at the
beginning of the conversation and they never got updated, that's … not really dynamic 'in session'"*.

**What was wrong.** The tool block renders above the conversation, so anything that changes it costs a read of the whole
chat. Two things changed it: (1) every Kirk restart, because an ordinary chat's held list lived in Kirk's memory (the 11-04
fix covered resumed jobs only): the owner's 15:29 turn restored its saved state in 0.8 s and then re-read 23,253 tokens in
37.7 s, 12 minutes after a deploy; (2) Starfleet adding a tool mid-chat (the 10-21 rule made that rarer, 56 % → 14 % of
follow-ups, never cheaper).

**What it is now** (`ollama_client.sticky_tools`, Kirk `a337ca6` + `db334d0`):
- **Thaw.** The block a conversation was sent is kept on disk with its exact text (`State\tool-blocks\<key>.json`) and
  resent verbatim: a restart, or a deploy that rewords a tool, changes nothing above the conversation.
- **Season a rewording.** A held tool whose wording changed is described once at the END of the turn (`TOOL_UPDATE_NOTE`).
- **Season growth.** A tool Starfleet adds to a held conversation is described at the END of the turn (`TOOL_ADDED_NOTE`,
  log `TOOLS SEASONED`) and remembered as that conversation's seasoned tools; the block does not grow. Its call arrives by
  name with text arguments, and Kirk types them from the tool's schema (`_typed_args`).
- **Only on an engine proven to parse a call to a tool it was not listed** (`can_season`): an ollama model whose Modelfile
  says `PARSER qwen3.5` or `qwen3-coder` (the 3.8 27B, the 3.5 9B and 35B) and Flash-Next. The proof is the short-list
  test's shape B above. BigHonker's `qwen3.6:35b-a3b-ud-q3kxl-mtp` and `qwen3:8b` have no parser line (ollama's generic
  parser matches listed names only, `tools/tools.go findTool`) and rigby is unproven: they keep the old growth, one
  re-read. A conversation that moves to such an engine takes its seasoned tools into that engine's block.
- **An index outage adds nothing to a held conversation** (`info["outage"]` from `tool_selection.select`): the selector's
  whole-pool fallback is not a pick. A NEW conversation in an outage still gets the whole pool.
- A job's workspace open still REPLACES its block with the coding set (11-12): a different, deliberate rule.

**Live proofs, 2026-11-14** (machine user ClaudeSupervisor, chat `5297fa4ccb73`, Mothership 27B; tool
`scratchpad season_live_proof.py`, read seconds from the window's own turn files):
| what | result |
|---|---|
| A resumed job keeps its block's text across a restart | bh-d (BigHonker 35B) restored from its save at 16:28: `TOOL LIST kept across the restart: 40 of 40 tools (40 with their held text)` |
| A held chat gains a tool with no re-read | turn 2, 16:33:31 `TOOLS SEASONED … (block kept as sent, 1 tools)`; the engine read for **3.8 s**, against 17.7 s for that chat's full read. The model's thinking named the seasoned tool (`disk_usage`) and its real scope |
| A seasoned tool is called and runs, after a Kirk restart | turn 3, 16:38: the model called `disk_usage` by name (never in its block), ollama parsed it, Kirk typed `sizes` as a boolean and ran it; the answer (8.4 TB free on /media) is right |

**What the third proof exposed, fixed the same hour.** That turn came seconds after the restart, embeddings were out (an
embed 400, below), the selector handed over all 138 tools, and 90 definitions were seasoned into the chat: 35,930 tokens,
a 68 s read, and they stay in that chat's text. Fix: the outage rule above. The embed 400 itself: after each restart one
embed got `400 Bad Request` on two boxes, each was marked down for a minute, and every turn in that minute lost
Starfleet's pick. A 400 no longer cools a box (it is the request's), and the server's words and the request's shape are
logged. **That log line named the cause on its first restart** (16:47:19: `texts=1 longest=122678152 chars | server said:
{"error":"tokenize error:"}`): one chunk of 122,952,707 characters in jordan library (`dump-ban5003-202611091352.md`, a
SQL dump uploaded 11-09, no headings, so one chunk), sent whole at every startup: 22 s, refused on every box, and that
namespace left with no semantic index since 11-09. Six guesses made before the body was logged (long inputs up to 40,000
chars, empty strings, big batches, a NUL, emoji) had all answered 200. Fix: `embeddings.EMBED_MAX_CHARS` (65,536, four
times the measured point where this model's 2,048-token read is already full): a longer text is sent cut. The file is
untouched; whether a 122 MB dump belongs in a searchable library is its owner's and the owner's call.

### The master start: a new chat's first turn (the owner 2026-11-14: "Let's build the master chat"; LIVE, proven on the 27B)

the owner: *"one master one that gets things added to it? Like the seasonings after we flash thaw it?"*, *"Make sure we
aren't making the seed static, so that every new thing still gets a different start during normal operations. Just the
input prompt side"*. It is his own hot start (10-25 *"some sort of pre-saved state that the models load up"*; 11-05 the
model *"basically already has the system prompt 'read' and appending the new part"*; 11-07 *"pre-saved states of the live
things that way the models can do quick reads and not have to reread huge things"*) with the one thing that kept it small
removed: 26 of 27 first turns had a different tool block, so the saved start ended inside it.

**What it is** (`ollama_client.sticky_tools`, Kirk `0820f53` + `6388ed0`):
- A NEW chat's block is the selector's always-offered set (`info["core"]`: core tools, capability entries, a persona's
  own), in the pool's order: the same for every new chat of a user on a box. What Starfleet picked for THIS message rides
  at its end (`TOOL_ADDED_NOTE`), exactly like later growth. Log line `MASTER START`.
- Kirk tells the window the WHOLE block is shared (`_CALL_CONTEXT.master_tools` → `chat_window.open_stream(shared_tools=)` →
  the item's `hot_tools` = the block's length), so `helper_window.HotStarts` saves the start through the tools AND the
  leading system messages. Nothing in the window changed: the file is still named by its own text, so a deploy that
  rewords a core tool or the system prompt makes a new one by itself. One per user (the system text is the user's),
  path and effort, where the old core-only start was shared by all; `HOT_KEPT` is 8 per model and size.
- **Only the prompt is shared.** Neither request body Kirk builds carries a seed or a temperature, and a live window sets
  a seed only for a measurement order (`--seed-base`). Pinned by a test that captures the request as sent.
- **Not a job** (its block becomes its coding set at the workspace open, 11-12, and its first message would carry every
  seasoned definition for good), **not an index outage**, **not an engine that cannot be called by description**
  (`can_season`: BigHonker's `qwen3.6:35b-a3b-ud-q3kxl-mtp`, `qwen3:8b`, rigby): those keep the block Starfleet picked.
- A picked `render_person` stays in the block (`MASTER_KEEPS_IN_BLOCK`): the one regression the short-list test found is
  on the consent path. That chat reads cold, as before.

**A conversation is its chat id AND its opening** (`conversation_key`, `_held_for`). The first live proof asked the same
question in two new chats; the opening alone made them one conversation and the second was sent NO definitions for its
picks. Harmless while tools sat in the block, a missing-tool defect once they ride in the conversation (live from
16:17 for growth until `6388ed0` at 19:52). A helper call inside a chat keeps its own identity (same id, other
messages); a chat already under way adopts the record its opening was held under; a first turn Kirk knows only by its
opening (a script, an eval repeating one prompt) starts its own.

**Live proof, 2026-11-14 20:00–20:04 CT** (`scratchpad master_live_proof.py`, ClaudeSupervisor chats `933a98e2d118`,
`50067a1f6f39`, the SAME question, Mothership 27B @ 125,952): the same 14-tool block and system text; `MASTER START` and
29 picks seasoned for each; `hot_tools` 14; no seed on either; **the two answers differ**. The engine's own log
(`serve.log`): `restored context checkpoint (… n_tokens = 5120)`, then **8,693 of the prompt's 13,813 tokens evaluated**,
in 15.9 s and 21.9 s (548 and 397 tok/s; the rate moved, the count did not). The master file is 690 MB, restored in
0.43 s; the old core-only start was 2,560 tokens. Without it the same prompt read 13,813 tokens in 20.2 s (683 tok/s).
**Why the rate moved, measured 2026-11-14 night.** (a) Alone on the card the same read does NOT drift: three cold orders
of six long documents (`campaigns\speed-20261114\speedcheck-mo-qwen3.8-27b-ctx125952-readrate-{a,b,c}`, 18 of 18 rows,
no limit hit, fully on the card) read 700–723 tok/s with under 1 % spread per document. (b) A read's rate is tokens over
a time that includes two ~150 MB engine checkpoints made near the end (about 3 s of the 15.9), so a shorter read prints a
lower rate for the same card speed. (c) The 397 read was slow in EVERY batch from the first (1.25 s per 512 tokens
against 0.77): the telemetry rows inside it show the card's memory clock at 1,538 MHz and 266–272 W, against 2,487 MHz
and 372–404 W inside the 548 read. Today's telemetry has that low memory clock under load in about five stretches
(14:50–14:59, 16:38, 20:03, 20:37), and the slow reads in `serve.log` fall in the same minutes. It clears by itself.
**Its trigger is NOT measured**; the orders did not reproduce it, and nothing overlapped the 20:03 read in the window's
own log (the save before it finished two and a half minutes earlier). Readers (read-only, bounded):
`serve_log_read_rates.py`, `serve_log_around_pair.py`, `telemetry_memclock_tally.py` in
`Z:\Archives\claude-session-tools\2026-11-14-quick-resume-session-93f571e7\a849\`, where every `scratchpad` script this
document names from 2026-11-14 is kept.
⚠ Kirk's first-output line printed the prompt's SIZE as what was read ("read 13813 tokens in 21.9 s") and made a
working master look unused for two minutes; it now prints the engine's `prompt_eval_cached_count`.

**What it does not do.** The picks still cost their read on every new chat (29 definitions, about 7,000 tokens here),
only later in the prompt. It is not on Flash-Next jobs, BigHonker's 35B or rigby (above), so the owner's *"Why would we not do
the same thing for flash next that we would do for the other ones"* is met for chat turns on engines that can be called
by description, and not beyond: rigby needs the short-list decoy run there, the 35B a parser line in its Modelfile.

## The mid-turn dump on the fleet boxes: LIVE on all four factories for ollama and rigby models, NOT for Flash-Next (the owner's spec; engine patch #4, a cut answer continues when the same request is asked again)

the owner, 2026-11-05: *"What happened to mid turn dumps à la Xbox quick resume?"*, *"I told you I want it on all 4 factories so that's just a missing feature I've already dictated as a design spec"*, *"How explicit do I need to be when I keep asking for this feature to be implemented?"*. His 10-17 words (*"I've been asking for #1 this whole time"*) were built for rigby only. **Done means wired and proven on all four factories, not an engine proof on one.**

**What exists.** rigby: a stop cuts the answer mid-token, dumps the slot with the pending token, and the requeued order continues the SAME call from that token (`tolkien_llm.dump_on_stop` → `prepare_resume` → `restore_for_cut` → `continue_for`, lab patches #0/#2/#3). The three fleet boxes run the same patched engine and the engine-level call is proven lossless there (Mothership 27B 5 of 5, BigHonker 35B 3 of 3, BigHonker 9B, Softserve 8B). **What was missing:** nothing on the fleet called it. A suspend (`/runs/suspend`) and a stop slip both wait for the turn to END, then save; at max effort a 27B turn runs 20+ minutes.

**Why rigby's code does not transfer** (`ollama-0.40.5-src\llm\llama_server.go`): ollama sits between HAI and the engine. For models with an Ollama renderer (the 3.8: `--no-jinja --chat-template chatml`) it renders the prompt in Go, sends `/completion` and parses the output in Go; it passes no `lab_resume` field and no continuation prompt. Mirroring that parser in the window was the first plan; it is archived (`Archive\quick-resume-superseded-20261105.md`).

**The design being built: the ENGINE continues, the client only repeats its request** (lab patch #4, `lab-engine\patches\slot_continue_patch.py`):
- `POST /slots/<id>?action=restore {"filename": …, "lab_continue": true}` arms the slot for one request, only when patch #3 armed it (the save landed mid-answer and everything restored). The reply says `lab_continue_armed`.
- The next completion whose prompt is exactly the head of the restored state (the cut request, asked again) is neither read nor written afresh: the cut answer's tokens are replayed to the client through the normal output path (so ollama's Go parser, or the engine's own chat parser, sees the answer from its first token), the sampler is advanced over them, the saved random state is put back (patch #2), and generation goes on from the pending token (patch #3).
- Any other request runs as before on the restored prefix and the arming is spent: never worse than a re-ask.
- So for Kirk nothing changes in shape: a turn that was cut is re-asked, as it already is after a restart. The engine makes that re-ask a continuation.

**Engine identity for this: a LAB REVISION.** A new lab patch on the same upstream commit is its own source tree and tag on all four factories (`lab-engine\llama.cpp-620d222-r<N>`, tree `Ollama-0.40.5-lab620d222-r<N>`, rigby's build dir of the same name; `fleet_engine.TAG_RE`). Build: `python lab-engine\submit_labengine.py 620d222-r3`. Canary: `fleet_deploy.py --engine <order dir> --only bighonker`.

**State, 2026-11-05 16:10 CT** (all four factories serve `620d222-r4+compat`; Mothership went on at 15:49 after the owner ran the operator checklist at its console, [[RUNBOOK-Ollama-Host-Operations]] § "Mothership operator checklist"):
| step | state |
|---|---|
| `620d222-r2`, patch #4 as first written | the old lab_resume arm LOSSLESS; the re-ask arm **NOT LOSSLESS** on the 9B: armed, then a fresh answer was written. Cause: the engine's own prompt cache (`get_available_slot`, `f_keep < 0.5`) swaps a slot's state out when the request keeps under half of what the slot holds (29 prompt tokens against 90 held). A short prompt with a long answer so far is exactly that shape |
| `620d222-r3`, + the prompt-cache guard | **BigHonker 35B A3B: CONTINUES LOSSLESS** (same seed identical, other seed identical, the unarmed control differs at token 2; 0 tokens read; 6.3–6.9 s against the reference's 11.2 s, since 62 of 160 tokens are replayed). **BigHonker 9B: still NOT LOSSLESS.** Cause: the 9B is image-capable (its runner has `--mmproj`), so every slot is `has_mtmd`, and the patch declined such a slot. The lead's 27B is image-capable too (`/api/show` capabilities: vision) |
| `620d222-r4`, + image-capable models | the answer so far is always text; the prompt is walked by index as the engine's own `init_sampler` walks it. Source tree made by the patch script on a fresh copy (differs from r3 in one file); build order `labengine-620d222-r4-compat.20261105-120737`. **BigHonker, 12:41: CONTINUES LOSSLESS on both** - the 9B (image-capable) and the 35B A3B: same seed identical, other seed identical, 0 tokens read, the unarmed control differs at token 2; the old lab_resume arm still LOSSLESS (orders `resumeprobe-bi-qwen35-9b.20261105-123626-af32`, `…-35b-a3b-ud-q3kxl-mtp.20261105-123628-2e00`; one run each). Next: the other three factories (rigby builds its own from the same tree) |
| HAI side, Kirk | LIVE 12:28 (`55e8aa5`, gate 3446): the reader takes a `cut` row as parked (`chat_window.TurnParked`), the chat loop asks the turn again with no retry spent, a suspended run stops on the checkpoint before that turn, `/runs/suspend` parks the turn in flight (`park_turn`), items carry `cut_ok` only where it can work. Inert until the windows carry the foundry side |
| HAI side, the windows | LIVE on the three GPU factories 12:43 (`4b8198b`, foundry suite 663 GREEN on each) and on rigby 13:14 (`a5bc47e`, Tolkien gate 1040). `helper_window.py` § CUT: a `.cut` marker, or a YIELD on a `cut_ok` turn -> hang up, save the chat mid-answer, row `cut`; the chat's next restore asks the engine to continue. rigby ran a second copy of this code (`tolkien_llm` `WindowSlots` / `_window_turn`) until 2026-11-08, against the fleet rule ([[P20-MULTI-FACTORY-FLEET]]; the owner 2026-11-05: "That's why they share a source code ... so we ideally DONT have to do 4 copies"). **Built 11-08, deploy pending:** rigby's `run_window` keeps its launch and calls `helper_window.serve` on its own llama-server (`a.llama`); the copy is retired (foundry `ca82a8e`, Tolkien `bb0a21d`) |
| LIVE PROOF through HAI (`midturn_proof.py`: a real pinned turn, `/runs/suspend` mid-answer, resume) | **BigHonker 35B**: parked 9.5 s after the ask, same window; and parked in 8.6 s, the card served another model, a NEW window continued. **Softserve 9B** (image-capable, ollama's renderer path, the 27B's path): parked in 2.1 s, the card served another model, a new window continued. **rigby 35B**: parked in 2.7 s, same window (rigby has one pinnable model). **Again 2026-11-08 20:17 on the GPU boxes' own window program** (rigby's copy retired, [[RUNBOOK-Ollama-Host-Operations]] § RIGBY CHAT WINDOW; run `b07b85c049e24513a67fff0afecdb734`, chat `a87a044f56cf`): parked 2.3 s after the ask at 1,893 chars; a NEW window (`…-201904-b5b7`) loaded in 15.3 s, restored 15,111 tokens in 0.3 s ("the engine WILL continue the cut answer") and finished at 7,708 chars, the 1,893 an exact prefix: PROVEN. **Mothership 27B, 16:11** (run `8964948a14904d67b2ed4d168d851012`, chat `fdfa29ad0484`): parked 7.4 s after the ask, 52 s into a turn at 125,952; the card served Nemotron Nano 30B for 139 s; a NEW window (`hai-win-mothership-qwen3.8-27b-20261105-161506-a588`) continued and passed the park point 79.6 s after the resume (the 27B's reload). Every run on all four boxes: the text before the park is an exact prefix of the finished turn; the window's row says `own, continuing the cut answer`; nothing re-read; no seed sent. One run per arrangement |
| the proof (`engine_resume_probe.py --continue --hang-up --fresh-load`) | restore with `lab_continue`, ask the SAME request: the whole answer must come back identical, while the same re-ask without `lab_continue` differs. The same-seed arm alone passed on r2; the other-seed arm caught it. **Since 16:05 the probe names each arm for what the SERVER did before the re-ask**, because that turned out to decide it: `right_after_the_cut` (the same server, straight after the cut: a turn parked and asked again in one window), `after_a_fresh_load` (the card was given away and came back), `same_seed_after_a_fresh_load`, `other_seed_after_a_different_answer`, and a control with no park in it at all, `plain_ask_after_a_different_answer` (the reference request, same seed, an erased slot, asked of a server that has just written something else) |
| **Mothership 27B at 125,952, the engine proof** (seven runs 15:51–16:10; orders `791a8b7f9597` … `0309c1166aac`) | **The two cases a real park produces are bit-identical, every run they were measured (4 of 4): `right_after_the_cut` and `after_a_fresh_load`.** `same_seed_after_a_fresh_load` identical too (3 of 3), so the seed is not a factor. **A continue that follows a DIFFERENT answer written by the same server drifts 42–58 tokens after the cut** (the replayed part is always exact): 7 of 7 for the arm that follows the not-resumed control's answer, 3 of 3 for `other_seed_after_a_different_answer`, and 3 of 7 for the arm that follows an answer differing only in its last third. `plain_ask_after_a_different_answer` differs from the reference at token 15, 2 of 2, with no park and no restore anywhere in it. ⚠ That control was not a cold read, so it does not show that the drift is the engine's rather than the resume's (2026-11-08, § External review F2: the memory prompt cache survives a slot erase). The 27B's runner drafts (`--spec-type draft-mtp --spec-draft-n-max 4 --spec-draft-backend-sampling`), BigHonker's 9B and 35B passed the arm that follows a different answer, so this is measured per box and model. What it means in use: a park and resume around a deploy, or with the card handed to another model, continues exactly; if the SAME model answers another chat in between, the continued turn is exact up to the cut and then drifts. Open: which state an answer leaves behind that the restore does not reset (§ External review F2) |
| Flash-Next (Strata) | **NOT DONE. Quick resume means the mid-token resume** (the owner 2026-11-05: "ITS NOT DONE IF ITS NOT THE MID TOKEN RESUME. You're describing a properly built pause at turn boundary which is old ass news"). What Flash-Next has is the turn-boundary pause: its state is saved when a turn has ENDED and restored bit-identical (Part 2 above). A factory's YIELD on a Flash-Next turn waits for the whole turn. **A `/runs/suspend` does not, measured 2026-11-06:** it cuts the turn within about 10 s and the turn starts over when asked again, losing what it had written (three parks of one run, the same step each time, about 14 minutes of writing at the first; `chat_window.park_turn` writes `.cut` for any window, against its own docstring. Checklist D4a). Strata's session file has no pending token or sampler state, so the mid-token resume needs a change in Strata's own source (`lab-engine\strata-0.1.45`); INSTALLED on Mothership 2026-11-06 and working through HAI's own path on a short turn; token-identical on the card through a direct client; a LONG conversation is not yet proven: see the section "Flash-Next mid-token resume" at the end of this doc. Never write "done" for a box that only has the turn-boundary pause |

**Things the engine side has already taught:** the engine runs 3–7 tokens past a hang-up, and the slot file holds them (the window takes its partial answer from the slot, not from what the client received). A proof whose control cannot fail proves nothing: at a fixed seed a re-ask rewrites the same answer by itself.

**Proof it needs before it is called done:** on each of the four boxes, a real job turn cut mid-thought through `/runs/suspend`, the card given to another model, then resumed: the finished turn token-identical to an uncut control at the serving temperature.

## External review, 2026-11-08 (ChatGPT, `Notes\GPT-Handoffs\20261108-003526-quick-resume-findings-to-verify…`): status per finding, checked against the code

| finding | status | why |
|---|---|---|
| F1 Flash-Next mid-token identity at 100K+ tokens | **PROOF OUTSTANDING** | only the ~9,600-token direct proof and one short HAI park exist (§ Flash-Next mid-token resume, "Left" 1); main's, needs Mothership free |
| F2 27B drift after the same server wrote another answer | **FIXED IN THE ENGINE 2026-11-11: lab patch #5 (`lab-engine\patches\vk_fa_dead_cells_patch.py`), revision `620d222-r5`, serving on all four factories** | Cause: Mothership's Vulkan coopmat attention shader (`flash_attn_cm1.comp`; batches and the grouped-query mode a batch of one uses) let DEAD KV cells, masked for every row and holding an earlier answer's leftovers, move the result: their attention weights are exactly zero, yet their V values changed the rounding of the weighted sum on this card. The patch loads zeros for the V of a cell no real row can see; coopmat stays on. Proof on the serving engine, 27B at 122,880, q8_0 KV, MTP on: resume proof LOSSLESS 3 of 3 (`resumeprobe-mo-qwen38-27b.20261111-193616-b6c0`, `-193900-8ee0`, `-193903-6fe8`), whole-turn continue CONTINUES LOSSLESS with its control failing as it must (`-193949-34bf`), history probe REPEATS (`enginehistory-mo-qwen38-27b-r5.20261111-193628-184a`). How it was found, every cut and the dead ends: `Journal\2026-11-11-claude.md` § ROOT CAUSE. Price, 27B at 122,880 on an idle card with a normal desktop (`enginespeed-qwen38-27b-620d222-r4-vs-620d222-r5.20261112-001442-e397`, 4 reads each): r5 reads 622.1–622.7 tok/s against r4's 638.7–639.3 (2.6 % slower) and writes 45.1–45.3 against 46.2–46.9 (about 3 %); card memory identical. A leaner form of the patch is proven and no faster, so no r6 was cut (`vk_fa_dead_cells_patch.py` docstring). The row as it stood open: `Archive\quick-resume-f2-27b-drift-open-row-superseded-20261111.md`. |
| F3 a writing tool cut mid-call | **PROOF OUTSTANDING, being built** | main's, 2026-11-07: a cut `coding_patch` / `coding_create` / `coding_file` checks the file against what the call would have made (done: report done; not done: run once); orders get a handle like `coding_render` |
| F4 an interrupted save loses the last good checkpoint | **FIXED, `526e587`, LIVE on Mothership, BigHonker and Softserve 2026-11-07 (`fleet_deploy --no-restart`, helper_window `39ee964f…` on all three, foundry suite 688 green on each); new windows carry it, not yet exercised by a real save** (no lost answer had been seen) | `helper_window.save_slot` asked the engine to write the chat's own file in place, so a save that died midway left neither the old state nor the new. Now the engine writes `<name>.new`, and only a file that exists afterwards replaces `<name>`; Strata's `.labturn` sidecar moves with it or is cleared; a failed save keeps the last good file. Foundry suite 688 OK. rigby gets the same save with its window moving onto `helper_window` (Tolkien `bb0a21d`, deploy pending) |
| F5 a hand-off to a different model is not a KV resume | **agrees with the design** | a run moving to another model or box carries its conversation (run ledger), and the new model reads it; only the same model on a compatible engine resumes its exact state |
| Strata could not pin inside the tool list (the review's own correction of a 11-07 Claude claim) | **built and deployed 18:36 CT 11-07, not proven on the card** | `strata_prefix {"tools": K}` (see § the hot start) |

## What each proof is

| claim | proof | where |
|---|---|---|
| HAI restart loses nothing | engine never stopped; next turn read only new tokens | Flash-Next window, live answer, no park |
| Flash-Next across an engine restart | token identical at temperature 0 and 0.6, read 53 of 114,564 tokens | Mothership, two restarts |
| Flash-Next through a real stop slip | window saved and exited in 23.5 s; new window `state_from: own`, read 27 tokens | Mothership, real orders |
| Hot start = cold start | token identical, with cold = cold and hot = hot as controls | Softserve 9B, rigby A3B, Mothership 27B |
| Mid-answer cut on the 27B | token identical, 5 arms, cut by hanging up on a streamed answer, controls diverging as they should. Not wired into a real path on the GPU boxes | Mothership |
| Ollama's chat save on Mothership, on F: | 95,392 tokens saved, 4.19 GB in 108 s, no telemetry gap; restored in 2.6 s, read in 0.39 s | Mothership, real windows |

## Flash-Next mid-token resume: what Strata's source says, the patch, what is left (2026-11-06, INSTALLED, proven on a short conversation; a long one is not)

the owner, 2026-11-05: *"ITS NOT DONE IF ITS NOT THE MID TOKEN RESUME"*. Read from `E:\SSDShared\lab-engine\strata-0.1.45\Strata-0.1.45`. The source claims below are from reading it; the only thing run so far is the stand-in engine, with no GPU and no model.

**It looks much smaller than the llama.cpp work.** The rigby patches had to save a pending token and the random state, because llama.cpp's sampler carries state. Strata's does not:
- **No random state to save.** `sampler.hpp`: Philox is "counter-based on (seed, token index)". `generate.cpp:15193`: "The draw is Philox(seed, position)... a seed gives the same text whether a token comes from this path or from the speculative loop". So the seed and the position are all of it, and the drafter's guesses do not change the text.
- **The catch on the seed:** a request with no seed gets a clock-based one (`generate.cpp:13258`), and HAI's windows send none, so today a re-ask draws differently. The server has to choose the seed, send it, and keep it.
- **The server already continues generation by extending the prompt.** `serve\server.py` `_run` calls `engine.generate(prompt, max_new - n, ...)` again for its reasoning-budget continuation. The path exists.
- **The server is Python, between the HTTP request and the engine.** `_run` owns the parser, the budget and the token loop, so replay can happen there with no C++ change expected. In the llama.cpp design this needed engine patch #4 because ollama's Go parser sits outside.
- **The window side already exists.** `helper_window` writes the `cut` sidecar and calls `continue_fn`, and reports `lab_continue_armed`. Strata just does not answer it yet.

**The patch, in the shape of patch #4:** `lab-engine\patches\strata_continue_patch.py` (12 exact replacements, nothing written unless all match), applied to the copy `E:\SSDShared\lab-engine\strata-0.1.45-lab` (`serve\server.py` sha256 `9eb13d12...` unpatched, `63815262...` patched). The seed is chosen only when `slot_save_path` is on, so a server without session files is unchanged. What it does:
1. The server always sends a seed: the request's own, else one it chooses, the same draw behaviour as today.
2. It keeps each turn's prompt ids, generated ids and seed. On a save after a cut turn it writes them in a sidecar beside the session file.
3. `POST /slots/0?action=restore {"filename": ..., "lab_continue": true}` arms the slot, and the reply says `lab_continue_armed`.
4. The next request whose rendered prompt equals the saved prompt runs through `_run` as usual, except that the saved ids are fed through the same loop (parser, budget, repeat guard) as if the engine had just written them, so the caller receives the whole answer. Then `GEN` goes on with prompt + generated ids and the same seed.
5. Any other request runs as before and the arming is spent: never worse than a re-ask.

**Tested so far, on a stand-in engine whose output is a function of (seed, position):** `serve\test_lab_continue.py`, 17 tests. Strata's own server tests: 848 pass unpatched, 865 patched (the same 848 plus the 17), 19 skipped both ways. Seven deliberate breaks of the patch were each caught by at least one test. This proves the server's half only.

**THE PROOF ON THE CARD, 2026-11-06 (`workspace\flashnext\strata_continue_proof.py`, Mothership, serving plan, thinking on at xhigh, temperature 0.6, 700 tokens, one short conversation of about 9,600 tokens, a real socket hang-up at 300 pieces):** verdict IDENTICAL, by character over 1,886 characters. Controls: the engine repeats itself with nothing of mine in it (yes), a different seed differs (yes), the plain request across an engine restart repeats (yes). Three cut arms all equal their uninterrupted reference: same engine, no seed in the request with the server's own chosen seed, and across a full engine stop and start. A continued answer read 0 prompt tokens and took 4.8-5.0 s against 18.9 s for the first whole answer, which read 9,615. Save 0.36 s (9,919 tokens, 388 MB), restore 0.41 s. **What this does NOT cover:** a long conversation (Run A is 230K), a cut during a tool call or a read, and HAI's own path (a window, `park_turn`, a resume through Kirk). The installed server was the vanilla one before and after, hash checked.

**INSTALLED 2026-11-06, and how:** the serving plan names its server (`"server_py": "strata_server_lab.py"`), and `strata_arm.install_server` checks the bytes at every launch and writes them if they differ, so the plan and the workspace payload (`fleet_deploy --workspace flashnext`) are the door. Rollback: remove the key and unzip the vanilla file from `Strata-v0.1.45-source.zip`. Installed `serve\server.py` is `63815262...`. `helper_window.retain` and the array fetch carry `.labturn`. ⚠ `strata_continue_proof.py` refuses unless it finds the vanilla file, so a re-run needs that check changed first.

**THE FIRST REAL HAND PARK THROUGH HAI, 2026-11-06 15:41 (`research\ltt-jarvis-prompts\runs\parktest-153002`, a pinned throwaway turn):** `/runs/suspend` cut it mid-answer (feed: turn 47, 2,229 characters of thinking, `discard`), a stop slip ended the window and its engine, `/runs/resume` opened a new window on a new engine, and the first turn after it (turn 48) wrote 8,002 characters that open with every one of the cut's 2,229 and go on to a finished answer. No uninterrupted reference exists for this one, since HAI's windows send no seed; the identity is from the direct proof above, and this shows the path. A stop slip on an idle window took 5 to 5.5 minutes each time, which looks like its idle rule and was not looked into.

**Left:**
1. A long one: a cut at 100K+ tokens, where the save is gigabytes and the docs' warning about replay has more room to bite.
   **The server test for it fails one assertion (run 2026-11-19, 24 tests: 23 pass).** The test file now holds a
   class `ALongConversation` (5 tests, history 200,000, answer 12,000, cut at 9,000; added after the 17 counted
   above). `test_the_engine_is_handed_the_whole_history_and_every_token_written` fails: after the restore the
   engine's last call was handed 200,000 ids, the history alone, where the test wants history + the cut
   (>= 209,000). The four others pass, including character-for-character, which cannot tell a continue from a
   regeneration here: the stand-in, like Strata's sampler, gives the same text for the same seed and position.
   So it is open whether the long path continues from the cut or writes the answer again (correct text, the whole
   answer's cost). How it was run: upstream Strata 0.1.45 + `lab-engine\patches\strata_continue_patch.py` +
   `strata_prefix_tools_patch.py` (byte-identical to the server in force, `strata_server_lab.py`, apart from its
   licence header and two comments), the tests from `lab-engine\strata\serve`, in Strata's own `.venv` on
   Mothership from a temporary copy (`serve` and `tools` only; Softserve's Pythons lack `regex` and `jinja2`).
2. Kirk's `park_turn` docstring is corrected in the mirror, live at the next deploy. Decide `cut_ok` for the factory's YIELD.

**What a long run can still change:** Strata's docs say exact replay "is not guaranteed: expert residency and CPU/GPU rounding can change later output". It held here at this size. The save at 230K tokens is untested (about 1.2 GB per 63K tokens; never on C:). If a long cut differs, say so and report the distribution method's result, as for the other engines.

**The proof, when Mothership's card is free:** cut it the way the real thing is cut (a hang-up on a streamed answer, doctrine row "test that a resume is lossless"). Same request, same seed: uninterrupted vs cut, saved and restored, plus uninterrupted vs itself as the control that says whether the engine repeats itself at all. Name the arms by what the server did before them, print how many rows carried data, and prove it through a real order and window, then on a real park.

**Until this ships:** `chat_window.park_turn` writes `.cut` for a Strata window although its docstring says such a window lets the turn finish, so a park on a Flash-Next turn loses the turn in flight. Measured 2026-11-06 off the deck's context count: up to about 60K tokens at one park. Whether to honour the docstring in the meantime (the person waits for the turn) or keep the cut (the job pays) is the owner's trade.
