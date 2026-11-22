# RUNBOOK — Orchestrator Deploy Procedure & Self-Eng Lanes



### Self-eng bail adjudication — Claude, HUMAN-GATED

⚠ **Doctrine (master plan / the owner):** HAI must NEVER spend Claude tokens on its own — external escalation is
**explicit opt-in, per use, sparing**; the local models stay the workhorse. This mechanism is built around that.

When a finished overnight run flags items for a human (`bail_human` / test tampering), the orchestrator writes an
**inert** `<run_id>.adjudicate-request.json` next to the run state (`Z:\Documentation\State\overnight\`, i.e.
`/docs/State/overnight` in-container; kill switch `OVERNIGHT_ADJUDICATE_REQUEST`) and adds a ":scales:" offer line
to the Discord notify. **Nothing runs until the owner acts.** To get Claude's ruling:

```powershell
# Softserve (or just ask Claude in-session):
powershell -File "E:\SSDShared\SSD\Open Access\Claude Home\bail_adjudicator\bail_adjudicator.ps1" [-RunId ovr-…] [-DryRun]
```

One bounded `claude -p` (model in the script header, default `claude-fable-5`; read-only tools:
`Bash(ssh sundae:*),Read,Grep,Glob`; 30-min cap; BelowNormal priority — Plex host) inspects the sandbox workspace
git and rules each outstanding finding REAL vs HALLUCINATED → saved as `<run_id>.adjudication.md`. A failure writes
`.adjudication-failed.txt` and is NOT retried (delete it to retry). **Do NOT register the script as a scheduled
task or watcher** — manual-only is the design, not an omission. Log: `bail_adjudicator.log` next to the script.

### P20 FOUNDRY deploy across ALL FOUR FACTORIES (Tolkien included) → `fleet_deploy.py`

**Deploying a factory module is a TOOL, not a script you rewrite — and it covers all FOUR factories** (the owner
2026-10-02: *"There's 4 factories and if you let one drift that's against doctrine. The factory deploy tool set is
supposed to keep them all aligned"*). Use it; do not hand-roll another one — a hand-rolled deploy is how factories
end up on different versions with nobody knowing.

**"no fleet table: set FLEET_TABLE …" (since 2026-11-18).** The tool's boxes are the `FLEET_TABLE` setting (host →
`root`, `task`, `worker_task`) in `<FOUNDRY_ROOT>\etc\lab.env`, read through `foundry_paths` like every other foundry
setting; `fleet_engine.py` reads its per-box tables the same way (`FLEET_ENGINE_SETS`, `FLEET_ENGINE_LAUNCHERS`,
`FLEET_CONSOLE_LAUNCH`, `FLEET_ENGINE_STAGE`, `RIGBY_ENGINE_DIR`, `RIGBY_BUILD_DIR`). The lab's values live in
Softserve's `E:\foundry\etc\lab.env`. The message means the tool resolved a `FOUNDRY_ROOT` with no `lab.env`: on
Softserve the default is `F:\foundry`, a drive it does not have, so `FOUNDRY_ROOT=E:\foundry` is a MACHINE environment
variable there (set 2026-11-18); a shell or sshd session started before that still needs `export FOUNDRY_ROOT='E:\foundry'`
(Git Bash) in front of the command. Mothership and BigHonker are `F:` and need nothing.

**Per-box foundry settings that used to be account paths in code (2026-11-18, the v0 publish review):** `OLLAMA_SERVE_LOG` (the model server's log when it runs under a user account; Mothership's is in that account's profile, the lab launcher's own `C:\ProgramData\HAI\ollama-serve.log` is always read as well) and `FOUNDRY_FRAMING_PY` (the interpreter carrying torch+torchvision for framing; the runner's own interpreter is the fallback), both in `<FOUNDRY_ROOT>\etc\lab.env` on the box that needs them (Mothership). The stall recorder (`hai_stall_recorder.py`, `C:\ProgramData\HAI` on Mothership, the SYSTEM task "HAI Stall Recorder") reads the same file through `foundry_paths` and is carried by `fleet_deploy` (its `ELSEWHERE` row); after a deploy end and re-run that task. The menu wall (`hard-bench\menu_wall.py`) reads its boxes from `WALL_BOXES` (JSON: per box the interpreter that runs `wall_tail.py` and either the telemetry files or a dated glob with its first day) and Softserve's own telemetry folder from `WALL_LOCAL_TELEMETRY`, both in `Lab\etc\lab.env`; with no `WALL_BOXES` it refuses and names the file. Same pass, the rest of the sheet: `ENGINE_SERVE_LOGS` (`Lab\etc\lab.env`, JSON box → the model server's log path; `live_rate.py` refuses and names the file without it); `FLASHNEXT_ENGINE_DIR` (Mothership's `lab.env`: the ollama bundle whose `lib\ollama` the flashnext ladder scripts drive, read through `foundry_paths` from the workspace; empty = every arm names its own `bin`); `ms_move_ollama_slots.py` takes the engine tree from the launcher's own `-FilePath` line; the installers (`install_stall_recorder_task.ps1`, `install_ups_logger_task.ps1`, `register_engine_task.ps1`) take `-Python` / `-Pythonw` or the box's own interpreter from PATH; `headtohead_blind_pairs.py` writes beside itself or to the folder it is given.

**The head-side doors read the lab from settings too (2026-11-18, slice four of the same review).** `fleet_deploy.py`,
`fleet_deploy_tolkien.py` and `fleet_engine.py` run on the head-side box and read `foundry_paths` (`FOUNDRY_ROOT\etc\lab.env`
there, Softserve `E:\foundry\etc\lab.env`): `HAI_BOX_NAME` (the head's ssh name, the box that builds Tolkien's images),
`HEAD_STACK_DIR` (`/opt/arrstack`), `TABLET_HEAD_NAME` (`rigby`: the lane keeps that name), `CODE_MIRRORS` (the same table the
Lab tools read from `Lab\etc\lab.env`; a checkout's `head`, `foundry`, `tablet-foundry`, `tools` siblings are the defaults),
`LAB_TOOLS_DIR`, `TABLET_INBOX_HOST`, `FLEET_ELSEWHERE` (the out-of-tree table `fleet_deploy` used to carry in code: module →
host → folder), `FOUNDRY_SHARED_SOURCE_BOX` (whose RUNNING copy a shared grader file must equal before Tolkien gets it),
`A1_SRC`, `HARD_BENCH_DIR`, `TABLET_A1_PAYLOAD_HOST` / `_SEEN`, `RIGBY_ENGINE_ARCHIVE_PREFIX`. Unset, each door REFUSES with the
names (`T._settings_missing()`), never ssh-es to nowhere. ⚠ **Git Bash rewrites an exported POSIX path for native programs**
(`/opt/arrstack` reaches `python.exe` as `C:/Program Files/Git/opt/arrstack`, and the space broke the mirror audit's remote
`cd`): `lab_settings.py --sh` therefore names every POSIX-valued variable in `MSYS2_ENV_CONV_EXCL` and writes LF only; a shell
door that exports such a value by hand must do the same. `deploy_foundry.sh` is retired (§ below).

```bash
python E:\SSDShared\mothership-build\fleet_deploy.py --check                   # ALL FOUR: GPU boxes + Tolkien tree
python E:\SSDShared\mothership-build\fleet_deploy.py --check p20_foundry.py     # report only
python E:\SSDShared\mothership-build\fleet_deploy.py p20_foundry.py            # deploy + restart
python E:\SSDShared\mothership-build\fleet_deploy.py --only softserve p20_status.py
python E:\SSDShared\mothership-build\fleet_deploy.py --no-restart p20_caption_qa.py
python E:\SSDShared\mothership-build\fleet_deploy.py tolkien_llm.py tests/test_llm.py   # TOLKIEN (rigby)
python E:\SSDShared\mothership-build\fleet_deploy.py --a1                      # A1 payload -> <root>\workspace\a1, all 3 GPU boxes
python E:\SSDShared\mothership-build\fleet_deploy.py --a1 --check              # (a bare --check includes it)
python E:\SSDShared\mothership-build\fleet_deploy.py --workspace flashnext answers.py   # any per-order payload:
#   mirror workspace\<dir>\<file> -> <root>\workspace\<dir>\<file> on all 3 GPU boxes, no restart
python E:\SSDShared\mothership-build\fleet_deploy.py --rigby-a1                # rigby's A1 payload -> the share Tolkien loads
python E:\SSDShared\mothership-build\fleet_deploy.py --rigby-a1 --check        #   (a bare --check includes it)
python E:\SSDShared\mothership-build\fleet_deploy.py --retire stray.py other.py  # REMOVE files from <root>\bin, all 3 GPU boxes (or --only)
```

**A change that spans a `bin` module and a `--workspace` payload: ship the CALLEE first.** The two are separate runs, so
between them the factory holds one new half. When a caller starts passing a new argument (2026-11-16: `infer_ollama.py`
calling `strata_arm.prepare(..., trust_verified=)`), deploy the payload that accepts it BEFORE the module that sends it;
the other order leaves every start in between raising a `TypeError`. And grep the foundry suite for FAKES of the changed
function (`test_foundry.py` stubs `prepare`), not only for calls: a fake with the old signature turns the suite red on
every box after the files are already deployed.

**Removing a file from a factory is a door too: `--retire` (since 2026-11-02).** It takes bare file names in
`<root>\bin`, archives each one to `<root>\bin\_retired\<stamp>\`, checks that the copy's hash matches, and only then
removes the original. It refuses (and leaves in place) a file that another module in `bin` still imports. It prints
`retired -> <archive path>`, `absent`, or `REFUSED - <why>` per file per box, and exits non-zero on any refusal.
Built because the door could only add: a 10-25 call that named two mirror-side tools (`fleet_deploy_tolkien.py`,
`test_fleet_deploy.py`) left them in `foundry\bin` on all three GPU boxes for a week. They were retired this way on
2026-11-02 (`_retired\20261102-080924\`). Tests: `test_fleet_deploy.RetireDoor`.

⚠ **It happened again 2026-11-15, and the cause is the same: never pass `fleet_deploy` the mirror's whole
changed-files list.** After a comment cleanup touched 79 files, every changed root file went to the door, and eight
mirror-side files (`fleet_deploy_tolkien.py`, `fleet_engine.py`, `p20_stall_rescue_live.py`, `setup_inspect_venv.py`,
`setup_render_venv.py`, `test_fleet_engine.py`, `test_grade_jobs.py`, `test_infer_ollama_markers.py`) landed in
`foundry\bin` on all three GPU boxes. **The door's own output names the mistake: a line `<file> is NEW on this box -
no prior copy to back up` for a file you did not mean to introduce.** Pass only named files whose home is that box
(`fleet_deploy.py --check <file>` shows `ABSENT` for a file that has never lived there). Retiring them needed
`fleet_deploy.py` and `test_fleet_deploy.py` retired in the same call, because they import the others; those two
were already on the boxes from an earlier deploy and do not belong there either (`_retired\20261115-191658\` and the
next stamp). The door's routing rules are in § "Routing" below; read them before the first call, not after a refusal.

**The A1 payload has a front door since 2026-10-08 (`--a1`).** `A1_GROUP` in `fleet_deploy.py` lists the six runtime
files (`a1_order`, `a1_validity`, `a1_branch`, `a1_stop`, `a1_delegate`, and `a1_harness.py` shipped as
`a1_harness_v22.py`, the name plans use); the source is the A1 dev tree (`A1_SRC`, hard-bench\a1, where its suite
runs). Each A1 order spawns its own process, so there is no runner restart and an order already in flight keeps the
copy it started with. **Before this, every A1 change since 10-04 went out by hand `scp`, and Softserve sat on the
pre-tool-fix harness (`225002c7`) for days while Mothership and BigHonker were current — nothing could see the
folder.** ⚠ **Hand deploys are BLOCKED by the `no_factory_floor` hook (rule 5):** an `scp` whose destination is a
factory code tree (`foundry\bin|workspace|tests`, `ai-toolkit`, Tolkien's build dir `/opt/arrstack/tolkien`,
`tabfoundry-jobs\payloads\a1`) — each has a `fleet_deploy` door — or a copy/`sed -i` of code there,
is refused and pointed here. Only the DESTINATION counts: pulling a file down to diff it, or copying a code tree
into a scratch dir, is reading and passes. Slips (`jobs\queue`), plans and data are not code and stay allowed.
Proven on the real commands before it was trusted: a sweep of all 1,822 Bash commands of 10-07/08 blocks 11, every
one a real code push (A1, flashnext, a backup restored onto Mothership), and nothing else; pulls pass. **Rule 5b
(2026-10-09) blocks the same hand deploy through the editor tools**: Write/Edit on a `.py/.ps1/.cmd/.bat/.sh` under
`foundry\bin|workspace|tests` or `tabfoundry-jobs\payloads\a1` is refused (the hook is registered for
`Write|Edit|MultiEdit|NotebookEdit` in `~/.claude/settings.json`; live decoy refused). Edit the mirror / dev tree,
then ship. The orchestrator is not a factory and is out of the rule's scope: its door is § "Manual orchestrator file
edit → deploy" below.

### Rigby's A1 payload → `fleet_deploy --rigby-a1` (gated, since 2026-10-09)

Tolkien loads the A1 payload from the share for every A1 order (the spec's `payload`), so **the share copy IS the
deployed code** — one copy, no drift between boxes, but before 10-09 every save there was an ungated deploy, and it
drifted both ways from its sources (the share had 10-08's validity edits, the dev tree had the newer tests; the share's
own `test_a1_fellowship.py` imported a module that was never copied there). **Sources** (`RIGBY_A1` in
`fleet_deploy_tolkien.py`): `hard-bench\inspect_lab\` — `a1_payload`, `a1_task`, `lab_provider`, `a1_fellowship`;
`hard-bench\a1\` — `a1_harness`, `a1_validity`, `a1_ollama`. **Edit there, never in the share** (rule 5b blocks it).

The door: lane claim → refuse if a share copy was saved by hand since the last deploy (`.deployed.json` manifest in the
share; before the first deploy, a share copy NEWER than its source counts) → **gate**: every `test_*.py` in
`inspect_lab` runs inside rigby's `arrstack-tablet-foundry` image with `--network none` and **no live mount** (the dev
layout is tarred to rigby `/tmp`; Inspect's tokenizer comes from `inspect_lab\fixtures\tiktoken_cache`, the tablet
captures from `hard-bench\fixtures\cli_captures`), count pinned by `inspect_lab\gate_pin.py` → back up + copy +
byte-verify → **read the bytes back through the tablet-foundry container's own mount** → manifest. **It never stops a
running order** (since 2026-10-09 midday): every A1 order loads the payload afresh at its start (`a1_payload.py` drops
its siblings from `sys.modules`), so an order in flight keeps its copy and the next order runs the new one — the
fleet's `--a1` contract. (Stopping cost rigby ~2.8 h of in-flight exercises across four deploys that morning.)
**Rigby A1 checkpoints every turn and resumes like the fleet** (`a1_task.CHECKPOINT`, `<order>\checkpoints\<task>.rN.partial.json`
beside its Inspect logs, replayed by `a1_branch.ReplayChat(…, "end")`), so a stop now costs a turn, not an exercise.
**Parity is pinned:** `test_fleet_deploy.A1ParityAcrossAllFourFactories` fails if a module of the fleet's A1 group
does not ship to rigby (or is not declared fleet-only with a reason). A red gate leaves the share untouched — its first two live runs did exactly that (a
missing staged module, then the offline tokenizer). The share's old test copies are retired to
`payloads\previous\a1-tests-retired-20261009\`.

**Routing:** a file deploys to the family whose mirror holds it — `E:\SSDShared\mothership-build` → the three GPU
factories; `E:\SSDShared\tolkien-build` → Tolkien. A name in both mirrors is refused as ambiguous. **When a fix is a
standard every factory must hold (a guard, a stop/price rule), ship it to BOTH families in the same pass** — the
2026-10-02 hung-engine fix went to `infer_ollama.py` AND `tolkien_llm.py`.

**Each factory's launchers are carried too (since 2026-11-21).** The `.cmd`/`.vbs` files a factory's scheduled tasks
start (Mothership's `run_foundry_queue.cmd`, BigHonker's `bh_runner.cmd`, Softserve's `softserve_runner.cmd`, ...) live
in git as `Lab\hosts\<box>\launchers\`; `fleet_deploy` finds them by listing that tree and `FLEET_ELSEWHERE` names the
folder each runs from on its own box, so each goes to that box only. Edit them there, never on the box, and deploy
with `--no-restart` (nothing imports them). A launcher held by a running `cmd.exe` must not grow past the live copy's
long-running line: `cmd.exe` reads a batch file by byte offset and resumes there when the process exits.

**The Tolkien branch** (`fleet_deploy_tolkien.py`, first run 2026-10-02) replaced the hand procedure (archived in
`Archive\docs-corrected-20261002.md`): claim the
deploy lane → whole-tree hash diff mirror ↔ Sundae `/opt/arrstack/tolkien` (REFUSES on drift outside the deploy;
asserts a non-zero join) → back up + copy + byte-verify → `docker compose build tablet-foundry tablet-watchdog` on
Sundae → the Tolkien gate (`run-tolkien-suite.sh`; a red gate restores Sundae's files and ships nothing) → stop
slips for every order a lane claim names (answered rows are checkpointed) → **wait for Tolkien's ANSWER to each slip
in its log** → `docker save | docker load` Sundae → rigby (one Git Bash pipe — Python handing one ssh's stdout to
the other's on Windows took 33–34 s for the ~173 MB stream; the bash pipe ~8–15 s) → recreate BOTH containers as root
via `run_remote.py` → verify EACH container's bytes (runtime files; tests are gate-mounted, not baked in) and start
time → wait for `order taker up` (since 2026-10-30 the stopped taker RELEASES its lock and the new one takes over at
once — `previous instance … released the lock … on a clean stop`; the 90 s heartbeat-silence rule remains only for an
unclean stop) → requeue slips for the orders THIS deploy stopped → release the lane. **Every line it prints carries its
elapsed time** (`[+ 49.9s] …`). Measured 2026-10-30, a 1-file deploy: lane + drift + copy 3.5 s · build + gate 30 s ·
ship 15 s · recreate 8 s · handover 0 s = **62 s** (was ~217 s that morning: ship 33 s, recreate 15 s, handover 90 s).

**Stop answers, not claim files (2026-10-06).** The two answers, and what each means:

| Tolkien's answer | Meaning | What the deploy does |
|---|---|---|
| `[DESK] STOPPED <name>` | it was running and this deploy stopped it | requeue it after the deploy |
| `STOP <name>: already finished` | it was not running | leave it alone: someone else stopped it, or it finished |

(`fleet_deploy_tolkien.slip_answers`, 4 tests in `test_fleet_deploy.py`.) Before this, the tool waited for the
claim file to clear and requeued every order it had sent a slip to. On 2026-10-06 a stale fleet-lane claim blocked
a deploy for 30 min (`STOPPED SHORT`), and the resume step then requeued an order the operator had stopped on
purpose, which ran again on old code. The order taker now also clears both lane claims at startup, once the
singleton lock is its own (`clear_inherited_claims`, logged): no order of a previous instance can still be running.

**The one answer grader on the Fellowship (2026-10-03).** The tablet image also bakes in `grade_jobs.py` from the GPU
foundries' mirror (`mothership-build`): Dockerfile `COPY --from=foundry`, additional build context `foundry:
./foundry-shared` on Sundae (both tablet builds, `docker-compose.override.yml`). Every Tolkien deploy ships it there
FIRST and only when it is byte-identical to the copy Mothership runs (`F:\foundry\bin`, read with certutil) — so
Tolkien never gets a grader the GPU factories lack — and verifies it in both containers. `fleet_deploy.py
grade_jobs.py` deploys the GPU factories and then rebuilds Tolkien. `--check` prints "shared from the GPU foundries".
**`Dockerfile` and `requirements.txt` are tracked files** (drift, `--check`): name them in the deploy when they change —
on 2026-10-03 a Dockerfile change that was not named left the image without `grade_jobs.py`; the gate refused it. A
runtime file the image should carry but does not reads MISSING. Build files are checked at the build, never inside
the containers.

**Spock's files on the Fellowship.** The tablet image also bakes in `scheduler.py` + `config.py` from the
ORCHESTRATOR build context (Dockerfile `COPY --from=orch`). `--check` compares both rigby containers against the
orchestrator mirror too and exits 1 on any drift. After a Spock change, deploy the orchestrator first, then
`fleet_deploy.py scheduler.py`; the branch refuses until Sundae's orchestrator copy matches the mirror, then
rebuilds, ships and verifies as above.

Covers Mothership, BigHonker and Softserve (which is `E:\foundry`, not `F:` — it has no F: drive) and Tolkien.
Per box it takes a timestamped backup, verifies sha256 after write, runs `ast.parse` on **that
box's own interpreter**, re-checks the lane ON the box (fails closed), asks the runner to exit with a
`{"action":"pause"}` control slip (taskkill only as a last resort after the pause wait), relaunches
the runner task, and then proves the new pid started AFTER the new mtime. **If the runner picks up an
order during the pause wait** (the lane read free at the check, then an order went in flight — the
runner reads the pause only between orders), the tool ends **`DEFERRED`** and kills nothing: the
runner finishes that order, exits on the slip, and the task's 15-min repetition relaunches it on the
files already deployed. Confirm with `--check` after that order. (Before 2026-10-03 this case was
force-killed after 90 s, mid-order.)

**A lane that is never idle (the #34 menu filler keeps BigHonker/Softserve fed):** the tool REFUSES
while an order is in flight, so it can wait forever. Drop your own pause slip first — any
`<name>.ctl.json` = `{"action":"pause","why":"…"}` in `<FOUNDRY_ROOT>\jobs\queue\` — wait for the queue
log's `OPERATOR PAUSE (flag between orders)` line (the in-flight order finishes; the queue is untouched),
then run the tool for that box at once (the runner task's 15-min repetition will otherwise relaunch the
OLD code). Done this way 2026-10-02 on all three boxes. Since 2026-10-02 the tool also relaunches a box
whose files are already in sync but which has NO runner alive while orders wait (before that it printed
"runner left alone" and left a paused factory idle).

**⚠ DEPLOYED IS NOT RUNNING — this is the whole reason the tool exists.** `run_queue` is a single
long-lived `while True: run(job)` that imports the module once, at its own start, so a fix written
to disk is inert until the runner restarts. The check that matters is *does any process holding
this file PREDATE the file* — and the tool restarts on that condition alone, even when it writes
nothing.

**⚠ THE RENDER WORKER IS RESTARTED TOO — `worker …` line in the per-box report (since 2026-10-26).** `HOLDERS`
names only the order taker, so until then a `p20_render_worker.py` deploy wrote the file, restarted the runner and
printed `NEW CODE LIVE: True` while the render worker — a separate long-lived process that imports its modules once —
kept the old code. Found when a Krea 2 order (`render.krea2`) was rendered through the FLUX.2 path under a Krea label
by a worker started the evening before. Now every restarting deploy reads the pid the worker STATES in
`var\state\telemetry\render_worker.heartbeat`; if it predates `p20_render_worker.py` / `foundry_paths.py` /
`foundry_lock.py` it writes `var\state\RENDER_WORKER_RESTART` (the worker exits on it between jobs — a flag older than
the worker is ignored and cleared), waits 90 s, and only if the worker is still alive AND idle (no bake/render child
process) forces it, then relaunches it from the box's OWN worker task (`FLEET[*].worker_task`: Mothership
`P20 Render Worker`, BigHonker `BH Foundry Worker`, Softserve none) and proves the new stated pid postdates the files.
A worker mid-job is never killed (`worker DEFERRED`; it exits on the flag after that job), and **a worker is never
stopped unless its box's worker task exists** — the first version of this step hard-coded Mothership's task name,
stopped BigHonker's idle worker and found nothing to relaunch it with (restarted by hand from `BH Foundry Worker`
within minutes; it only publishes card status there). Pinned by `test_fleet_deploy.RenderWorkerRestart` and
`test_foundry.Krea2WorkerRoute`.

**⚠ It reads the runner's PID from `var\state\runner.lock`, never from a process listing.** On
Softserve the runner's `CommandLine` comes back NULL (different integrity level), so identity by
pattern fails in BOTH directions — loudly ("no runner came back up" about a live runner) and
**silently** (matching a short-lived process and reporting "OK, running new code" about a runner
it never touched). Read the stated value; do not infer it.

**⚠ It excludes itself and its parents from the process table.** `fleet_deploy.py … p20_foundry.py`
puts the module name on its own command line, so an unguarded pattern match makes the tool kill its
own process tree.

Elevation: the local session is NOT elevated (see § **⚡ ELEVATION**), so the tool ssh's
to Softserve like it does to the peers.

### P20 FOUNDRY deploy → the foundry's OWN suite

**The foundry has its own test suite, deliberately SEPARATE from HAI's orchestrator suite.** Not a
preference: the orchestrator is a containerised codebase on Sundae, the foundry is a different
codebase on Mothership, so the orchestrator suite *cannot import the foundry module at all*.

```
ssh mothership "cmd /c F:\foundry\bin\run_foundry_suite.cmd"
```
**On BigHonker / Softserve there is no `run_foundry_suite.cmd`** (checked 2026-10-17: the same command on BigHonker
answers `'F:\foundry\bin\run_foundry_suite.cmd"' is not recognized`). Run the suite file directly, from that box's
root (`F:\foundry`, Softserve `E:\foundry` — the root `fleet_deploy` prints): `FOUNDRY_ROOT=<root>` in the
environment, cwd `<root>\bin`, `python -W error::SyntaxWarning <root>\tests\test_foundry.py` — over ssh, as a piped
script with a list argv (`ssh <box> "python.exe -" < script.py`, the Windows-ssh quoting rule), never inline `cmd /c`.
**The suite pins its OWN count — `EXPECT_TESTS` at the bottom of `test_foundry.py` is the only current number; read it there, never from this page.** A full run (no arguments) that is green but ran a different number exits **3** with `[PIN: MISMATCH - N ran, M expected]`; a match prints `[PIN: ok - N ran]`. Change the constant in the same commit that adds or removes tests; prove the pin with `EXPECT_TESTS=9999` in the environment (must exit 3). ~4 s, exit 0 = green. Added 2026-09-28 after the count on this page went stale for the THIRD time (315/334, 337/353, 365/369 — RUNBOOK-DEFECT-LOG): a number that only moves when its author remembers is not a pin. Runs with `-W error::SyntaxWarning`, so the backslash/escape class of bug FAILS the gate rather than printing a warning nobody reads.

**The suite file lives in `<FOUNDRY_ROOT>\tests\test_foundry.py` on every factory, and `fleet_deploy.py` ships it there** (`HOMES` in its remote script; every other module goes to `bin\`). Until 2026-09-28 `fleet_deploy` only wrote `bin\`, so every box's `tests\` copy was the 2026-08-26 suite — the on-box gate was testing current code with month-old tests. Deploy the test file with the code: `fleet_deploy.py --no-restart test_foundry.py` (no runner imports it).

Softserve and BigHonker have no `run_foundry_suite.cmd`; run the file directly (Softserve also needs `FOUNDRY_ROOT`, since the loader defaults to `F:\foundry`):
```
ssh bighonker 'cd /d F:\foundry\tests && python -W error::SyntaxWarning test_foundry.py'
cd /d E:\foundry\tests && set FOUNDRY_ROOT=E:\foundry&& python -W error::SyntaxWarning test_foundry.py   (on Softserve itself)
```

#### ⚠ PREDICT THE TEST COUNT BEFORE YOU READ IT — a test that cannot be COLLECTED is indistinguishable from one that PASSES

A duplicate class name is legal Python, imports cleanly, and **the later class silently discards
the earlier** — its tests stop existing. `Ran 313 tests ... OK` is exactly what a healthy 315-test
suite also looks like; the only signal is the gap between the predicted count and the printed one.
**Say the number out loud before running the suite, and investigate any gap.** The same trap one
level down is a duplicate *method* name inside a `TestCase` — likelier by accident, because two
tests about one subject want the same name.

**`test_no_shadowed_defs.py` asserts this invariant** and ships to all three factories. It
**discovers** every `test_*.py` beside it and treats them as strict (a hand-maintained file list
goes stale, and a guard not pointed at a file class cannot protect it), and it walks class bodies
as well as module level.

```
ssh mothership "cd /d F:\foundry\bin && python test_no_shadowed_defs.py"     :: exit 1 = a shadowed name
```

⚠ **A test that reimplements the thing it tests is not a test** — an inline copy of the algorithm
asserts a property of the copy and cannot fail whatever production does. Tests call production; a
test that has nothing to call yet says so in an annotation rather than quietly freezing a copy.

⭐ **Safe to run AT ANY TIME** — no GPU, no ComfyUI, no writes to live state (`CURRENT_RUN`, the real
batch registry, the render index). This is the deliberate opposite of the orchestrator gate's known
defect (four of its tests read live hardware, so it goes red whenever the lab is busy — i.e. exactly
when a hotfix is most likely to be needed). Keep any new foundry test hermetic or it inherits that
disease.

**Deploy order for anything under `F:\foundry\bin`:**
1. Lane clear? (`CURRENT_RUN.json` absent, no trainer/render worker alive — **exclude the observer**,
   a `CommandLine -match` filter matches the querying powershell itself)
2. Backup: `p20_foundry.py.bak-<what>-<stamp>`
3. Pull the LIVE file as the baseline and hash it — never edit `mothership-build` blind, it has been
   2.5 KB stale before
4. Patch → `scp` → **verify sha256 + size on the host**
5. **Run the suite** (above). The import test is the one that catches "syntax valid, module dead".
6. **Restart by PID** — `DEPLOYED IS NOT RUNNING`; `run_queue` is `while True: run(job)` and imported
   the module once at process start. Kill the `cmd /c` parent too (`schtasks /end` does not kill the
   child), relaunch via `schtasks /run /tn "P20 Foundry Runner"`.
7. Confirm the new PID **postdates** the file mtime, and `verify_deploy.py` exits 0.
   ⚠ `verify_deploy.py` counts ITSELF (the target path is in its own command line), so it lists one
   extra PID per file. It cannot produce a false STALE, but do not read the count as instance count.
8. Re-run the suite after the restart.

**The one-command shell door (`deploy_foundry.sh`, Mothership only, by `F:\` paths) was retired on 2026-11-18**:
`fleet_deploy.py` (§ above) is the door for every factory and does each step here, pause-and-redeploy included; the
script is archived at `Z:\Archives\retired-code\foundry-precutover-20261118\deploy_foundry.sh`, and its paragraph in
`Archive\runbook-orchestrator-deploy-history-2026-11.md`.

⚠ **The RENDER HARNESSES are already hot-swappable and need NO pause** — `p20_preview_render_slim.py`,
`p20_kontext_edit.py`, `p20_inpaint_edit.py`, `p20_inpaint_slim.py`, `p20_pixel_hash.py` are spawned
FRESH per render, so an scp takes effect on the very next cell. Only the long-lived loops
(`p20_foundry.py`, `p20_stall_rescue.py`) need the pause. Same for `p20_render_worker.py`: scp +
restart that ONE process, no factory pause.

⚠ **STALE PAUSE FLAG.** Step 2 only asks for a pause if an order taker is actually running, and
step 7 DELETES any leftover flag before restarting. An unconsumed `STOP_AFTER_RENDER` (runner
already down when the deploy started) is read by the freshly started runner one second later —
`OPERATOR PAUSE (flag between orders)`, clean exit — so **the deploy reports success while the
factory sits idle.** Deleting is safe there and only there, because the pause has already done its
job.

**Queue control (2026-08-01) — the operator's levers; NEVER manual process surgery
(doctrine-table row "stop, pause, or REORDER factory work by hand"):**
- **Pause**: `type nul > F:\foundry\var\state\STOP_AFTER_RENDER` — the runner finishes the render
  it is on (renders have no mid-denoise checkpoint), or during TRAINING triggers the trainer's own
  LOSSLESS checkpoint pause (STOP_NOW; ck+optimizer saved within a step or two). Either way: order
  STAYS QUEUED, lane + claim + lock released, flag consumed, clean exit, DM sent.
  **Resume**: `schtasks /run /tn "P20 Foundry Runner"`.
- **Priority**: `"priority": N` field on any order spec — higher runs first; no field = FIFO.
  "Run this small one first" is a NUMBER, not surgery.
- **Runner singleton**: `F:\foundry\var\state\runner.lock` (+ `.heartbeat`, touched per poll).
  A second start defers; a pid-alive-but-heartbeat-stale holder is announced LOUDLY as wedged with
  the kill remedy. Two order takers racing one queue is the failure this prevents.
- **Kontext edit cells**: per-prompt `"kontext": {"source": "<abs path>", "guidance": 2.5}` — the
  prompt `text` becomes the edit INSTRUCTION; routes to `F:\foundry\bin\p20_kontext_edit.py`
  (the #19 round-7 winner graph). Models come from `E:\ComfyUI\ComfyUI\models` via the
  `generic_estore` mapping in `F:\comfyui-rocm\extra_model_paths.yaml` (diffusion_models +
  text_encoders wired 2026-08-01; NOTE the nested ComfyUI\ComfyUI). No Mistral cond bake for edit
  prompts. `steps` per-prompt field works on both cell types (SLIM arg9 / kontext arg5).

**What the suite covers:** module import + public surface · `render_key` purity across
text/seed/checkpoint/strength/base_only (the property `RENDER_INDEX` reuse rests on) ·
`batch_guard` like-for-like enforcement including the **contract that a spec mismatch is NOT
`LaneBusy`** (that seam mis-typed consumes good orders) · probes must not retire the trainer's
embeds cache.
**Known gaps:** it tests contracts, not integration — it cannot catch a wedged ComfyUI or a
daemon/foundry disagreement about what a stall is. Seam coverage is currently one test; `CURRENT_RUN`
and the daemon contract are the next worth adding, because seams are where this lab's worst bugs live.

### ⭐ THE ASSISTANT MANAGER — a local model reviews EVERY main-session reply before the owner sees it

**What it is.** A **Stop hook** on Softserve. When the main session finishes a turn, its draft reply
— together with WHAT owner ASKED and the TOOL CALLS ACTUALLY RUN that turn — goes to a locally-placed
model, which blocks the reply if it finds either of two things. Built 2026-08-12 at the owner's
direction: *"I need to feel that your responses in general are all being checked."*

| | |
|---|---|
| hook | `~\.claude\hooks\assistant_manager.py` (registered under `Stop` in user `settings.json`) |
| **the owner's switch** | `python ~\.claude\hooks\assistant_manager_ctl.py status \| on \| off \| main <session-id>` |
| config | `assistant_manager.conf.json` — `enabled`, `main_session_id` |
| log | `assistant_manager.log` — every verdict, every skip, every failure |

**The two jobs, and the FIRST is the one that matters.**
1. **SCOPE — is he doing what was asked?** Doing something different, doing substantially MORE than
   asked (a request to CHECK is not a request to rebuild), quietly dropping part of it, raising a
   topic the owner has parked, or touching another session's work when only asked to inspect it. *This is
   the half that has never produced a false positive, and the half the owner actually wanted.*
2. **CLAIM — does the tool record support the draft?** Verification wider than the check, facts in
   no tool output, cause asserted from correlation, absence declared from one probe. *Noisier —
   every clear false positive so far has been a CLAIM.*

**Design points that are load-bearing:**
* **NO LENGTH GATE.** A length gate is Claude's own judgement re-entering a mechanism built
  because that judgement is unreliable — and it has a hole: *"Sure, I'll start on the render
  census"* is 38 characters, runs no tools, and is exactly the drift this exists for.
* **IT MUST BE GROUNDED** — it gets the request + the tool record, never the prose alone. A blind
  reviewer catches nothing: a confident draft always READS well-evidenced. Same lesson as
  `review_context.py`.
  ⚠ An EMPTY tool record is **evidence** (nothing was checked → flag "verified"); a TRUNCATED
  record is **not** (the support may be in the part it wasn't shown).
* **STRUCTURED OUTPUT** (`format` + JSON schema), not a hand-rolled `json.loads(txt[idx:...])` —
  a hand-rolled parse fails live and sends a reply out unreviewed.
* **IF IT CANNOT REVIEW, IT SAYS SO** and makes the session disclose it — never a silent unchecked
  reply. It exits non-zero once; `stop_hook_active` guarantees the retry passes, so a brain outage
  cannot deadlock a session.
* **MAIN SESSION ONLY, AND NO CONFIG MEANS REVIEW NOTHING** — user-level registration would apply
  it to every session on the account, including ones that never opted in.

**Overruling it is legitimate and expected** — say so in one line and carry on; do not pad an
answer to satisfy it. Both `quote` and `ask` are structurally required of it: a block with an
empty reason field is unanswerable.

### The documentation claim sweep — the narrower sibling that reviews the LIBRARY

**What it is.** A local model reads a DIFF of every Reference/Runbook/LLM-Project doc that changed
since the last sweep and lists **assertions about live system state that cite no evidence**. Built
2026-08-12 at the owner's direction: *"instead of having to rely on you to flag things, we could add a
secondary reader to be the one that catches it."*

**Why a timer.** The thing being checked is wrong facts entering the library, so "Claude remembers
to ask for a review" is the unreliable step being routed around — hence a TIMER, not a command
anyone has to remember.

| piece | where |
|---|---|
| reviewer | `claim_review.py` — the reviewer's instructions, prompt and parser; **makes no model call**, **never a gate** |
| sweeper | `claim_sweep.py` — mtime + its own snapshots (the library has no git); submits ONE desk order per sweep and collects it |
| schedule | `hai-claim-sweep.timer` → `hai-claim-sweep.service` → `/usr/local/bin/head_sweep.sh claims/sweep` (git: `Lab\bin\head-timers\head_sweep.sh`, installed by `Lab\bin\install_head_timers.sh`; settings `/etc/default/hai-head`), every 4 h, `Persistent=true`; the script POSTs the orchestrator's door `/claims/sweep` (worker token read from the container itself) |
| snapshots + findings | `/docs/State/claim-review/{snapshots,findings}` |
| order files | `<jobs share>/campaigns/claim-review/<YYYYMMDD>/claim-review-*` (items, out.json / results.json, Fellowship payload); pending order `/docs/State/claim-review/pending.json` |
| config | `CLAIM_REVIEW_ENABLED`, `_MIN_DIFF`, `_MAX_DIFF`, `_STATE_DIR` in `config.py` — **no host or model knob** |

**THE REVIEW IS A DESK ORDER, PLACED BY THE MENU (since 2026-10-30).** What the work needs decides its shape: nobody
waits on it (anything inside the 4-hour cycle is on time), quality matters (a reviewer that flags noise gets muted), it
runs six times a day forever (electricity matters), and it must never disturb a person or live HAI work. So each sweep
submits **one** background order through Kirk's in-process desk (`app.p20_order`): family `work-w1`, tier
**`fellowship`** (background work leans on Tolkien — the owner 2026-10-30: *"I'd prefer for background stuff we lean into
things under Tolkien: Rigby and the fellowship"*; a dedicated box never interferes with a person at a desktop),
urgency `background`, kind `review`, every prompt led by today's date (`fellowship_turn.today_line` — without it rigby
called a dated proof "in the future"), with BOTH forms — the GPU factories' `infer_ollama` (the reviewer's instructions
as each item's system prompt; `{POLICY_MODEL}` / `{POLICY_NUM_CTX}` / `{POLICY_THINK_ARGS}` /
`{POLICY_TEMPERATURE_ARGS}` / `{POLICY_OFFLOAD_ARGS}` filled from the row the weighing chose) and the Fellowship's
(`fellowship_turn.payload_tasks_text`, one task per changed doc). The desk weighs the menu and the box's factory runs it
in its queue — claimed, on the tile, stoppable with the standard slip, the factory's own admission applied (doctrine
12e). The weighing on that tier: rigby's `Qwen3.6-35B-A3B-MTP` HOME (q 0.82, its row's own 1060 settings, 32k),
BigHonker's 35B A3B and Mothership's 3.8 overflow; the tablets carry no usable `work-w1` row (the 1.5B coder, 5 %), so
for this work the Fellowship is rigby. **The menu's energy for rigby is time (x16-era deliveries, 2026-10-26) × a fixed
85 W measured 2026-10-13, before the x16 riser** — the watts were never re-metered; its W1 items are long answers
(~102 s), while a real review ran 18 s. Live proof 2026-10-30 13:50: order `3521aac82100` placed on the Fellowship,
Tolkien ran it on rigby (18.1 s, 16 tok/s), the control passed, the receipt collected 4 findings. `review` keeps
`yields_to_live` (Spock never places it on a box a live HAI job holds, B12) and the envelope of any GPU compute order
(6 GB) — the legacy 16 GB envelope left Mothership the only box, and on 2026-10-30 at 12:10 the sweep loaded the 3.8
straight onto Mothership's ollama while the owner was on the desktop; the card reset 10 s later (the owner: *"The hard coded
requirement is from legacy builds"*). A held order WAITS at the desk (background work never routes around a hold).
**Collection is status, never a clock:** the desk's completion receipt (`app.p20_job_result` → `claim_sweep.on_order_result`)
or the next sweep reads the order's fate. Done → findings to the day's file and one desk note naming the box and the
model the delivery says served it; a doc whose row gave no usable answer stays for the next sweep (said). Failed /
cancelled / gone → `NOT a clean result`, nothing advanced. Still at the desk → the sweep submits nothing new (`SWEEP DID
NOT SUBMIT`), never stacks a second order. A done order whose file is not on the share yet gets one more look, then is
reported. A snapshot advances only to the text that was REVIEWED. **The GPU form's answer is held to
`claim_review.SCHEMA` (ollama structured output) — and the schema is the `unbacked` LIST ONLY:** without a schema the
35B once quoted `{"already_running": true}` unescaped and a whole doc's findings were unparseable; with `unbacked` +
`note`, the grammar emitted `note` FIRST (keys come out alphabetically), the model wrote "all claims are backed" before
reading one, and the same diff went from 12 findings to 0. Do not add a summary key ahead of the list. **Live proof
2026-10-30:** a forced sweep (13:21) placed order `c1d6d22b15db` on BigHonker; its factory claimed it, the tile showed
`compute | claim-review-…_bighonker | 1 / 2`, it served `qwen3.6:35b-a3b-ud-q3kxl-mtp` split as declared (6.8 of 18.8 GB
on the card, cold load 26 s, 15 tok/s), the receipt collected it and the desk note named the box and model; the
list-only schema re-review of the same diff (13:31) parsed 12 findings in 144 s. **The assistant-manager Stop hook** (disabled) made a
direct call through the retired per-box review model; it now refuses to place and says why — move it onto the menu's
weighing (`scheduler.place_weighed`) before re-enabling it. Pinned by `tests/test_claim_review.TheReviewIsAnOrder`.
Superseded text: runbook-orchestrator-deploy-superseded-2026-10 § "Moved 2026-10-30".

**It runs IN the orchestrator process, through the door `POST /claims/sweep` — never as its own `docker exec … python`.**
The order has to cross the desk in the process that owns it. The door takes the worker token (`X-P20-Token`; the script
reads it with `docker exec orchestrator printenv P20_WORKER_TOKEN`), runs one sweep at a time (a second call returns
`{"already_running": true}`) and returns the sweep's result, which lands in `journalctl -u hai-claim-sweep.service`.

**Reading the output.** It reports EVERY run, including clean ones (`clean: N changed, N reviewed,
0 unbacked`), because silence must not be able to mean "the sweep is broken". Findings are a
WORKLIST, not a verdict — the write already happened and RAG has already reloaded, and a small model
judging a claim is itself fallible. First run on a fresh state dir takes a BASELINE and reviews
nothing (~77 docs), otherwise every document reads as new and the first report is a wall.

**Check it / run it now:**
```bash
systemctl list-timers hai-claim-sweep.timer --all
sudo systemctl start hai-claim-sweep.service  # force a sweep
journalctl -u hai-claim-sweep.service -n 20
```

Live-decoy verified 2026-08-12: placed on bighonker, real model call, flagged
*"the merry tablet has no hardware HEVC decoder"* as uncited while leaving an adjacent claim that
cited ffprobe + a dump path alone.

### ⛔ Before ANY deploy: no park, no wait, no hold — the four exceptions (one answer; Kirk, foundry, Tolkien)
**Validated changes deploy NOW, through the normal door, while unrelated work runs** (the owner 2026-11-07: *"Authorized,
validated changes should deploy promptly through the established mechanisms, even while unrelated jobs run. A proposed
delay requires an actual identified blocker"*). The doors are lossless: Kirk recreates with no park (every layer in
[[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] § "What a HAI restart keeps"); foundry files go out with
`fleet_deploy --no-restart` (new windows and orders pick them up); Tolkien's `fleet_deploy` holds intake, swaps, and
requeues (`DEPLOY_HOLD`, [[RUNBOOK-Compute-and-Training-Order-Submission]] § the Tolkien deploy hold). **The only reasons
a deploy waits:**
1. **A red gate** (failed test, pin mismatch, red foundry suite). Fix it; never override it.
2. **A human approval the change still needs** (a promotion the owner has not given, a design call that is his).
3. **A DEMONSTRATED recovery gap**: a restart has been SEEN to lose something (a `cancelled, nobody is coming back`
   line, an `interrupted by a restart` result). Fix that layer in the same pass; a wait in a deploy script is never the
   fix (2026-11-07: `wait_no_compaction.py` hid three real holes for a day).
4. **An approved protected experiment, inside its approved window only** (2026-11-07: the overnight set, from the chains'
   start to each half's end). Before the window opens, deploy. the owner: *"Don't tell them to hold deploys until the 11pm
   chains start"*, *"bro the freeze is in several hours"*. A comparison between two runs is NOT a reason to hold a deploy
   unless the owner made code-identity part of that experiment; on 11-07 it was not (*"I don't give a flying […] … just
   deploy"*).
Anything else ("it might disturb X", "safer after the set", "to keep both sides the same") is an assumption, not a blocker:
deploy, then verify the live end state. **A SIDE SESSION ASKING YOU TO HOLD IS NOT AN EXCEPTION, and neither is a proof or
measurement that the owner did not declare protected.** A side session the main session started works FOR it (the owner: *"That's not
your peer, that's your underling"*): its asks are proposals the main session decides, never holds it must grant. Exception 4 is
the owner's to grant. A side session's proof that a restart can break is a recovery gap to fix (exception 3 only once it is SEEN),
and its client retries a refused call and reads status
(2026-11-07 19:56: I granted the fork a 15-minute Kirk hold for its rigby proof and then held a rename for it; the owner: *"Why does
restarting Kirk affect it?"*, *"You literally told me that the habit was addressed and it has happened at least twice since
then"*). The lane claim is not a hold: wait for a HELD lane (`deploy_claim.py status`), never deploy across it. **The case law, as decisions:** Kirk during an active compaction → deploy (the
compaction is checkpointed, `671da33`, proven 2026-11-07 18:05) · a validated change hours before a scheduled freeze →
deploy now · a change during the protected window that would alter what the experiment measures → hold to the window's
end, say so with the window · a red gate → do not deploy, fix it · a shared factory change → it reaches ALL FOUR factories
through `fleet_deploy` (Tolkien included); a copy of common behaviour on one factory is drift to fold into one
implementation, never a reason to ship to three and stop (the case: rigby's own copy of the window program, retired
2026-11-08 — rigby's `run_window` now calls `helper_window.serve`, so a window change is one file and two deploys, GPU boxes
first, then `fleet_deploy_tolkien`).

### Manual orchestrator file edit → deploy (ad-hoc, non-HAI-driven)

Applies when a **human** (Claude or the owner) edits a single orchestrator source file and needs it live —
distinct from the automated `self_deploy`/`deploy_promote` state machine described in
[[HAI-Orchestrator-Internals]] (§ Self-deploy state machine), which is HAI-authored + canary-gated.

Canonical live source: `sundae:/opt/arrstack/orchestrator/` (root-owned). Mirror: `E:\SSDShared\orchestrator-build\`
on Softserve. `<user>` cannot write live directly → need `sudo`.

**The two wrappers that run this procedure (`E:\SSDShared\Lab\bin\`, since 2026-11-11) — two SEPARATE steps, never chained:**
1. `bash /e/SSDShared/Lab/bin/deploy_orch.sh <tag> <files relative to the mirror…>` (run on Softserve, in the
   background): claims the deploy lane (step 0 below), audits the whole mirror (`orch_mirror_audit.py`), backs up,
   copies, builds the image and runs the pinned gate. Prints verdict lines only. It does NOT restart Kirk. On a
   GREEN gate (build_rc=0, gate_rc=0, `[PIN: ok`) it COMMITS the files it carried in the mirror (`mirror committed
   <hash>`), since 2026-11-14; before that, no deploy door committed and two days of deployed work sat uncommitted.
   `fleet_deploy.py` does the same for the foundry, Tolkien and `--workspace` mirrors (`commit_mirrors`).
2. Read its last lines: `gate_rc=0`, `OK`, `[PIN: ok - N collected…]` with N the count you predicted. Only then:
   `ssh sundae "HEAD_STACK_DIR=<…> HEAD_DOCS_MOUNT=<…> RUNUSER=<…> bash -s <files to byte-check>" < /e/SSDShared/Lab/bin/orch_recreate.sh`,
   the three values from `eval "$(python /e/SSDShared/Lab/bin/lab_settings.py --sh HEAD_NAME HEAD_STACK_DIR
   HEAD_DOCS_MOUNT OWNER_USER)"` (`RUNUSER=$OWNER_USER`), exactly as `deploy_kirk_green.sh` passes them. Without them
   the script stops at its first check (`HEAD_STACK_DIR is not set (the caller exports it)`) before restarting anything.
   A plain restart (lossless at any moment, § "Before ANY deploy") that then reads back what the notes say happened to
   live windows and runs.
   ⚠ `orch_recreate.sh` runs ON Sundae through that `ssh … < file` form. Run as `bash orch_recreate.sh` on Softserve it
   restarts nothing and ends in a JSON traceback (2026-11-11 23:40).
   ⚠ Never `deploy_orch.sh … | tail && ssh … orch_recreate.sh`: the pipe's exit code is `tail`'s, so Kirk restarts on
   a RED gate (2026-11-12 10:09; stopped before it got there).
   **Both steps as one command: `bash /e/SSDShared/Lab/bin/deploy_kirk_green.sh <tag> <files…>`** (in `Lab\bin` since
   2026-11-14; used for that day's deploys). It runs `deploy_orch.sh`, prints only the verdict lines, and recreates Kirk
   ONLY when the door printed `mirror committed` (or `nothing new to commit`), `gate_rc=0` and `[PIN: ok`; otherwise
   `== NOT GREEN: Kirk not recreated`. The door's full output is kept in `Lab\bin\deploy-logs\`. When tests were added,
   move the pin first: `python E:\SSDShared\Lab\bin\bump_pin.py <old> <new> "<what the new tests pin>"`. Since
   2026-11-15 the pin file holds the count and nothing else; the note is appended to
   `Lab\bin\deploy-logs\gate_pin_history.log` (the pin's history before that day:
   `Z:\Archives\code-preclean\orchestrator\tests\gate_pin.py.preclean-manual-20261115`). A story in a comment or
   docstring fails the gate (`tests/test_no_new_stories.py`): state the rule in the code, put the story in the journal.
2a. **Do not edit a mirror file while ANY door that may carry it is running — including `fleet_deploy` for Tolkien,
   which carries `scheduler.py` and `config.py`.** 2026-11-17 00:0x: an edit to `config.py` made during a Tolkien
   deploy was committed by `fleet_deploy` as "Deployed to Tolkien" (it committed the mirror's bytes, not what it
   shipped), and 14 s later the head door's step 0 saw live differ from that commit, took it for another writer's
   deploy and ADOPTED LIVE back over the edit, so the next gate ran a half-renamed set (13 red). Fixed in
   `fleet_deploy` (`8fc84a7`): it snapshots what it ships and never commits a file that changed during the deploy,
   and Tolkien's deploy leaves the shared files' commit to the head's door. The rule still stands: wait for the
   door's verdict, then edit.
3. A change that was gated and then abandoned leaves Sundae's files and image AHEAD of the running container: run
   `deploy_orch.sh` again with the reverted files so the next restart, anyone's, does not pick it up (2026-11-12).
4. **Doc's self_deploy leaves the mirror behind, and step 0 now ADOPTS that (2026-11-14).** self_deploy promotes Doc's
   workspace into live and never touches `E:\SSDShared\orchestrator-build`. `orch_mirror_audit.py --deploying <files>`
   (what `deploy_orch.sh` runs) pulls every file where LIVE moved and the mirror is clean at its last commit and is not
   in this deploy, checks each one's sha256 against live, and commits ONLY those paths ("Adopt live: N file(s)…").
   It still REFUSES a real conflict: a file the mirror has uncommitted edits to, which live also changed. Fix that by
   hand (`--adopt-live` pulls without committing; or carry the file in the deploy if the mirror's copy is the one
   meant to win). Tests: `Lab\bin\test_orch_mirror_audit.py` (4).
The GPU police on Softserve know a `.sh` as a program since 2026-11-12 (before, a deploy that named `ollama_client.py`
was reported as an AI job on the card and DMed the owner).

### ⚠ STEP 0 FOR EVERY ORCHESTRATOR DEPLOY: CLAIM THE LANE, THEN AUDIT THE WHOLE MIRROR

**MULTIPLE SESSIONS DEPLOY TO THIS HOST — the claim serializes them.** A `docker compose build` +
`up -d` rebuilds an image and restarts every consumer of it. Checking "is anything running right
now?" is a SNAPSHOT WITH A RACE — a build takes minutes, and another session can start a
16-minute 27B run a second after you looked.

```bash
python "E:\SSDShared\Lab\bin\deploy_claim.py" status
python "E:\SSDShared\Lab\bin\deploy_claim.py" acquire "<who you are>" "<what you are shipping>"
#   ... deploy ...
python "E:\SSDShared\Lab\bin\deploy_claim.py" release "<who you are>"     # even if it FAILED
```

Same semantics as the factory's `CURRENT_RUN.json`: **missing = free · present = HELD, do not deploy
· stale = reported LOUDLY, never silently cleared** (a stale claim means a deploy died halfway, which
is exactly when a second one must not start; `release --force` is a deliberate human act). Claim file
lives on Sundae at `/opt/arrstack/.deploy-claim.json`.

### ⛔ MIRROR DRIFT KEEPS COMING BACK: the drift you FIND is yours to finish, in that pass ("not mine" is not an exit)

**The rule (the owner, 2026-11-15):** *"if they need to be deployed, […] deploy them … figure out why they're only in the
mirror, and properly finish out whatever is pending. I'm tired of having […] always sitting in my […] mirrors."*
Whoever's audit finds drift — any mirror, any direction, any author — resolves every file before the session moves on:
**deploy it** through its door if it belongs live, **adopt it** into the mirror if live moved, or **retire it** (archive,
then `git rm`) if it is dead. A journal line, a backlog entry or "flag for its author" is not a resolution.

**Why it kept coming back — the record, read 2026-11-15** (every fix before this one closed a costume, not the cause):
1. **Deploys checked only their OWN files.** Step 0 was used as "is the drift exactly my files?", and the answer was
   written down as success: *"mirror drift = exactly my 3 files (+ the same 4 pre-existing)"* (2026-08-29). The four
   files finished on 2026-11-15 (`register_lora.py`, the two critic benchmarks, `coding-runner/deploy/update_compose.py`)
   were first flagged *"Loose end (NOT mine) … Flag for its author"* on 2026-08-23 and *"Pre-existing mirror drift for
   the owner to investigate (NOT mine, NOT touched by this deploy)"* on 2026-08-29, then seen by every deploy for 84 days.
   Their author was a past session; nobody else owned them.
2. **Edits were parked in the mirror on purpose.** *"rides the next deploy window; deliberate mirror-ahead drift on
   config.py, recorded"* (2026-08-15), *"it rides the next orchestrator deploy"* (2026-10-09), *"flagged to the
   supervisor for the deploy lane rather than pushed by me"* (2026-08-14, Tolkien). A park, against § "Before ANY
   deploy: no park, no wait, no hold". "Recorded" in a journal is not a queue: nothing ever reads it back.
3. **More than one writer, and doors that carry less than the mirror.** Doc's `self_deploy` writes live and never the
   mirror (2026-11-10; step 0 adopts it since 11-14, the writer still does not). `ship_uhura.sh` carried `uhura.py`
   alone, so three Uhura commits never reached Sundae (fixed 2026-11-15: it ships every tracked file). Every door only
   COPIES, never deletes (§ 8b). A second copy with no rule (`E:\SSDShared\onboarding-app`, a July snapshot of a service
   whose home is Sundae's config repo) drifted silently until it was retired on 2026-11-15.
4. **The instrument tolerated it.** `orch_mirror_audit.py` exited 0 with files sitting only in the mirror, so a clean
   exit meant nothing about undeployed work. Since 2026-11-15 a COMMITTED mirror-only file is drift and step 0 refuses
   a deploy that does not carry it (uncommitted ones are work in progress and are reported only).
5. **Drift is only ever seen at deploy time.** No audit runs between deploys, so drift waits for the next person to
   deploy, who is then tempted by rule 1 to call it someone else's. `fleet_deploy.py --check` printing
   `STALE config.py` on rigby's three containers (2026-11-15) is the same shape: reported, nobody's.

**The one test:** at the end of a session that deployed or edited a mirror, `orch_mirror_audit.py` reads `0 differ;
0 only on live; 0 only in the mirror`, `fleet_deploy.py --check` reports nothing STALE, and the Uhura audit
(`E:\SSDShared\hai-release\instruments\uhura_mirror_audit.py`) reads 0. Anything else is unfinished work, not
background noise.

### ⭐ TWO BUILD LANES — THE FELLOWSHIP BUILDS SEPARATELY. Know which lane your file is in.

The consumer table:

| build context | image(s) | consumer(s) | mirror | suite |
|---|---|---|---|---|
| `./orchestrator` | `arrstack-orchestrator` | **`orchestrator` only** | `E:\SSDShared\orchestrator-build` | `run-suite.sh` |
| `./tolkien` | `arrstack-tablet-foundry`, `arrstack-tablet-watchdog` | **`tablet-foundry` + `tablet-watchdog`** | `E:\SSDShared\tolkien-build` | `run-tolkien-suite.sh` |
| `./orchestrator/coding-runner` | `coding-runner` | `coding-runner` | `E:\SSDShared\orchestrator-build\coding-runner` | **171 tests, zero skips, verified 2026-11-14**, including real-browser controls. Gate the newly built image in a disposable container with tests mounted and no production workspace/state mounts; runtime source must come from that image. The command (2026-11-14; `-t tests` matters — `-t .` fails "Start directory is not importable" and a grep on that output prints NOTHING, which is not a pass): `ssh sundae "sudo docker run --rm --network none -v /opt/arrstack/orchestrator/coding-runner/tests:/app/tests:ro -e PYTHONPATH=/app -w /app arrstack-coding-runner:latest python -m unittest discover -s tests -t tests > /tmp/runner-gate.log 2>&1; echo rc=\$?; grep -E '^Ran |^OK|^FAILED' /tmp/runner-gate.log"` — build first with `deploy_orch.sh <tag> coding-runner/<files>`, recreate with `ALSO=coding-runner` on `orch_recreate.sh`. Then perform the live CIFS-worktree Git/test/shipping control, remove only its disposable task, and verify the running image/file hashes. The image contains Playwright/browser and existing native dependencies. A runner rebuild replaces runtime-installed packages, so preserve completed work first. Owner/chat-aware job shipping couples this consumer to HAI's app/client: deploy and verify the runner before the HAI client; roll back the client before the runner. See isolated job delivery below. |


### Isolated job delivery and blind admission (2026-11-09)

The task's existing owner/chat provenance identifies unfinished isolated work.
If that worktree still holds the job folder, automatic job shipping REFUSES
before any copy or removal. A refused merge keeps the original worktree and
base intact. Resolve/finish the merge, or explicitly preserve and isolate
recovery evidence outside worker mounts through the operator procedure; do not
ship an unmerged worktree as an accepted final product. Multiple matching
worktrees also refuse. A successfully merged folder uses the existing base
file_ship door. Non-job legacy shipping remains unchanged. This corrects the
earlier 2026-11-09 path that bypassed worktree preservation; two red-before-fix
regressions, the complete 169-test built-image suite, and a live refused-merge /
preserved-worktree / resolved-merge / shipment control establish the correction.
Shipping receipts prove transport and isolation, not design quality, review
acceptance, or test correctness.
A failed shipment preserves its receipts and the model run's original outcome.
A BLIND successor remains pending, with an explicit hold reason, until
predecessor artifact isolation is verified; go_on_after_failure still applies to
model failures when shipping succeeded. Never start a supposedly blind job over
an unverified previous folder. Preserve the original order/spec when releasing
the hold; no replacement job or reset budget.

Held historical worktree archives belong outside coding-worker mounts, not
under the runner's mounted /state. Preserve file hashes and restoration maps;
task records/branches and original experimental shipments remain unchanged.

A tablet commit cannot rebuild the image HAI's brain runs from, and a chat deploy does not
disturb the Fellowship. `tablet_orders.py` (Kirk's desk-side client for the shared jobs dir)
stays ORCHESTRATOR-side; the six `tablet_*`/`tolkien_*` runtime modules and the Fellowship tests
live in `./tolkien`.

⚠ **NAMED COUPLING: `stream_clip.py` → orchestrator + coding-runner.**
The coding-runner's Dockerfile `COPY stream_clip.py ./` from the orchestrator build context (its
context is already `./orchestrator`), because the runner's output cap and the orchestrator's
tool-reply cap are **two layers of one idea**, and two independent implementations of one idea
drift — both cutting from the HEAD deletes every test verdict HAI receives (`Ran N`, OK/FAILED,
tracebacks). **THE RULE: a change to `stream_clip.py` rebuilds BOTH `orchestrator` and
`coding-runner`**, then verify the hash in each:

```bash
sudo docker compose build orchestrator coding-runner
sudo docker compose up -d orchestrator coding-runner     # must say "Recreated", not "Running"
for c in orchestrator coding-runner; do
  printf '%-16s ' "$c"; sudo docker exec "$c" sha256sum /app/stream_clip.py; done
# and prove the module RESOLVES there rather than to a shadow copy:
for c in orchestrator coding-runner; do
  sudo docker exec "$c" python -c 'import sys;sys.path.insert(0,"/app");import stream_clip;print(stream_clip.__file__)'; done
```

⚠ **NAMED COUPLING: `scheduler.py` + `config.py`.** The Fellowship head imports the
placement brain rather than reimplementing it (its own documented design), so the tolkien
Dockerfile COPYs those two files **from the orchestrator's build context** at build time
(`additional_contexts: orch: ./orchestrator` — one canonical copy on disk, nothing hand-synced).
**THE RULE: a change to `scheduler.py` or `config.py` rebuilds BOTH images**: the orchestrator
by this procedure, THEN `python E:\SSDShared\mothership-build\fleet_deploy.py scheduler.py` from
Softserve, which rebuilds the tablet image on Sundae and ships it to rigby, where its two
containers run. A tablet-only change goes through `fleet_deploy.py <tolkien file>`; an
orchestrator-app change rebuilds orchestrator only. `fleet_deploy.py --check` compares rigby's
two containers against BOTH mirrors, so a Spock change that never reached the Fellowship
shows as `STALE scheduler.py`.

⚠ The seam test also crosses deliberately: `run-tolkien-suite.sh` bind-mounts
`orchestrator/tablet_orders.py` READ-ONLY so `test_tablet_map` keeps grading the real desk client;
Kirk-side seam police (tablet_map tool wiring, no-detect-route-in-Kirk) live in the ORCHESTRATOR
suite as `tests/test_fellowship_seam.py`.

```bash
# tablet/Fellowship change (tablet-foundry + tablet-watchdog RUN ON RIGBY since 2026-08-28; build
#   context on Sundae). Run FROM SOFTSERVE - fleet_deploy routes tolkien-build files to its Tolkien
#   branch: lane claim -> whole-tree drift refusal -> copy+byte-verify -> build both -> Tolkien gate ->
#   stop slip for a running order -> save|load Sundae->rigby -> recreate BOTH as root (run_remote.py)
#   -> verify each container's bytes AND start time -> wait for 'order taker up' -> requeue slip.
python E:\SSDShared\mothership-build\fleet_deploy.py tolkien_llm.py tests/test_llm.py
#   ⚠ Sundae's tablet-foundry/tablet-watchdog are profile-gated (`moved-to-rigby-20260828`) and MUST
#   stay down: never `up -d` them on Sundae (naming a profile-gated service STARTS it -> a second
#   foundry double-writing run-state and hitting the NVR). The tool only recreates on rigby.
# orchestrator change:
sudo docker compose build orchestrator && sudo docker compose up -d --force-recreate orchestrator
# scheduler.py / config.py change: the tablet image BAKES THEM IN (Dockerfile `COPY --from=orch`).
#   Deploy the orchestrator as above FIRST, then from Softserve ship them to rigby's two containers:
python E:\SSDShared\mothership-build\fleet_deploy.py scheduler.py      # refuses until Sundae's copy matches the mirror
# VERIFY EVERY CONSUMER: `fleet_deploy.py --check` compares BOTH rigby containers to the tolkien AND
# orchestrator mirrors (exit 1 on any drift). "Container X Running" means LEFT ALONE, not updated.
```

⚠ **AFTER A DESK RECREATE, WAIT FOR EVERY FACTORY TO DECLARE AGAIN before submitting any
parallel/fleet order.** The recreated desk knows no capabilities until each factory's next
run-state push (~1 min beat); `place_order` correctly excludes an undeclared factory
("cannot-tell is not free") — so a parallel order submitted too soon is silently dealt across a
PARTIAL fleet with no error anywhere. 2026-08-22: Mothership missed a 3-way classify split this
exact way. Check is one GET per factory: `GET /p20/factory/<host>` → `capabilities` non-empty
and `reporting: live`.

**Any multi-consumer image:** count the consumers from the compose file
(`grep -n 'build:' docker-compose*.yml`), recreate every one, verify every one — a consumer the
deploy misses keeps serving the previous image with no error anywhere.

**Then audit the WHOLE mirror before editing anything** — `E:\SSDShared\Lab\bin\mirror_audit.py`.
It walks each pair's tree recursively (`tests\` included) and asserts per-subtree coverage, so a
zero-pair join cannot read as clean; skips are declared per run (`backups/`, `__pycache__/`,
`workspaces/`, `.git`, `*.bak*`).
⚠ **The mirror can be AHEAD of live, not just behind** — ahead means a deploy **publishes another
session's in-progress work**; behind means it silently reverts a shipped fix. Both directions are
failures. ⚠ The audit **asserts how many files it actually compared** — a comparison that joins
zero pairs prints zero drift and reads exactly like "mirror clean".

Step-by-step (byte-verified):

1. **Back up the exact live file first** (Sundae): `ssh sundae "sudo cp /opt/arrstack/orchestrator/<f> /opt/arrstack/orchestrator/<f>.bak.<tag>"`.
   the owner expects the pre-deploy version to exist somewhere. The mirror is now a LOCAL git repo (baseline
   commits + a full copy on `Z:\Archives`), so the MIRROR side has history — but the LIVE file on Sundae
   is a separate artifact, and this `.bak` stashes its pre-deploy state. Commit the mirror before editing.
2. **Edit the mirror on Softserve** (`E:\SSDShared\orchestrator-build\<f>`) — pure LF only; if patching from
   Python on Windows, use `write_bytes()` or `open(..., newline='\n')` (a plain `open('w')` writes CRLF
   platform-default and flips the whole file). Verify: `python -c "print(open(r'E:\SSDShared\orchestrator-build\<f>','rb').read().count(b'\r'))"` → 0.
   ⚠ Every file in the mirror is pure LF. If one ever reads CRLF, that is drift to fix (a
   Windows-side edit that did not set `newline=''`), **not** a second valid convention to preserve.
3. **Compute local sha256** (later comparison): `Get-FileHash <f> -Algorithm SHA256`.
4. **scp to Sundae `/tmp`** (<user>-writable): `scp E:\SSDShared\orchestrator-build\<f> sundae:/tmp/<f>.new`.
   **⚠ scp target basename MUST match the `cp` source basename in the next step** (a mismatch silently copies a stale file).
5. **`sudo cp` into place + chmod** (Sundae): `ssh sundae "sudo cp /tmp/<f>.new /opt/arrstack/orchestrator/<f> && sudo chmod 0644 /opt/arrstack/orchestrator/<f>"`.
   Do NOT run `sed 's/\r$//'` blindly — mirror is already pure-LF, and the `\r` gets eaten through PS→ssh→bash quoting and silently deletes trailing `r` from every line.
6. **Verify byte-identical** (Sundae): `ssh sundae "sha256sum /opt/arrstack/orchestrator/<f>"` — must match step 3.
7. **Syntax check in a writable dir** (root-owned pkg dir can't take pyc): `ssh sundae "cp /opt/arrstack/orchestrator/<f> /tmp/_check.py && python3 -m py_compile /tmp/_check.py"`.
8. ⚠ **GATE BEFORE YOU RECREATE (2026-10-04).** `run-suite.sh` runs `docker compose run --rm … orchestrator`, which
   starts a fresh container from the NEWLY BUILT image while the running `orchestrator` keeps serving the old one. So
   the order is: `sudo docker compose build orchestrator` → **step 10's gate** (+ the served-page check below on a
   throwaway) → only on a green, pinned gate `sudo docker compose up -d --force-recreate orchestrator`. On a red gate,
   restore the `.bak` files and rebuild — production never ran the red code. The old order (recreate, then gate) let a
   red change (agent-surface step 1: 7 failures, 6 errors, a test file that could not import) serve production for ~5
   minutes before the rollback.
   **Rebuild + up -d — build the image(s) YOUR FILE lives in** (see the ⭐ TWO IMAGES consumer
   table above): orchestrator change → `build orchestrator` + recreate it; tablet/Fellowship
   change → `fleet_deploy.py <file>` from Softserve (builds here, ships to rigby, recreates both
   there); **`scheduler.py`/`config.py` → the orchestrator here, THEN `fleet_deploy.py scheduler.py`**
   (the named coupling). "Running" in the up output means NOT recreated;
   "Recreated" is what you want. Verify with `sudo docker exec <c> sha256sum /app/<f>` for EACH
   container that imports the file you changed. The orchestrator's Python code is **baked into the image at build time**, not bind-mounted, so `docker compose restart` alone runs the OLD code (the container reuses its cached image layers) — the file on the host can be correct while the container serves pre-fix code until a rebuild. Confirm the new file is live: `ssh sundae 'sudo docker exec orchestrator sha256sum /app/app.py'` should match the host file. Matches [[HAI-Control-Surface]] guidance + the `feedback-orchestrator-deploy` memory. (For tests/ changes only, the tests dir IS bind-mounted at run-suite time — no rebuild needed.)
   **Top-level sidecars are also fingerprint consumers (2026-11-10 Doc access repair).**
   Even a module HAI does not import is read by `overnight._source_manifest` when it hashes
   `/app/*.py` for the shared coding-start staleness guard. Updating `hostops_sidecar.py`
   in its bind-mounted service and syncing the workspace while leaving HAI's baked copy
   older makes the correctly synced workspace read as stale. Include HAI's image fingerprint
   in that deployment's consumer audit; align the gated app image and verify the existing
   `/absurd/status?user=the owner` workspace-lag surface. Do not bypass the guard, exclude the
   file, or revert the workspace behind deployed source. A successful sync alone does not
   establish the app/workspace comparison. Verified repair: already gated image
   `f9e8f341b9f7`, 3749 tests / 2 expected skips, ordinary the owner coding-tool start/read/complete
   control, no source changes. Evidence: `research/doc-coding-restore-20261110/REPORT.md`.

8a. ⚠ **A HASH PROVES THE BYTES ARRIVED — IT DOES NOT PROVE THEY WERE MEANT TO SHIP.** When the
   change comes from ANOTHER SESSION the mirror is a moving target: a sha quoted mid-message while
   editing continues ships an intermediate the author has already superseded, with **every byte
   check passing** — the right bytes for the wrong version. **Handoff protocol both directions:
   ONE sha, stated as READY, nothing edited after it — and the deployer asks "is this still the
   one?" before building.** Byte verification answers *did it arrive intact*; it never answers
   *is this the version you meant*.
   ⚠ **And the statement must be PULL-BASED, not push-based.** Cross-session messages queue behind
   in-flight turns, so "I re-stated the shas" and "the deployer HAS the shas" are different claims.
   Protocol: the author writes the sha set into their CURRENT-PROJECTS block (durable, pull-based);
   the message only points at it; the deployer re-reads the BLOCK, not the message, immediately
   before building.
8b. ⚠ **DELETE anything the change REMOVED — `fleet_deploy` and every step above only ever COPY.**
   A file dropped from the mirror still sits in `/opt/arrstack/orchestrator`, stays in the docker
   **build context**, and is baked straight back into the new image; the component looks deleted
   in git and keeps running in production. A RENAMED test is worse: `tests/` is bind-mounted at
   gate time, so both names are discovered and the suite **double-counts**. Back the file up
   first, then
   `ssh sundae "sudo rm -f /opt/arrstack/orchestrator/<f>"`, and confirm it is gone **inside the
   container** after the rebuild, not just on the host. **The expected test count is the
   self-check: if you forget a deletion, the number tells you.** Same rule as retiring an
   archived artifact — one name, one live artifact.
9. **Health check**: poll `http://<sundae-address>:8099/health` until 200.
   ⚠ **A recreate needs NO park and NO wait: every run survives it at any moment.** What survives, layer by layer, with
   the commit and live proof of each, is ONE table: [[Quick-Resume-Flash-Next-Window-Pickup-and-Hot-Start]] § "What a HAI
   restart keeps, by layer". Whether anything may delay a deploy at all is § "Before ANY deploy: no park, no wait, no hold"
   below. What to read after the recreate: `CHAT WINDOW picked up`, `RUN RESUMED <id>`, `turn … reconnected after the
   restart`; never `item … cancelled, nobody is coming back for it` for a run that came back (that is a defect: find the
   layer). `RUN RESUMED` proves the run restarted, not that it stayed on its box: on a window job the proof is the `picked
   up` line; otherwise the next `weighed placement` / `McCoy` lines show no overflow. Still true and still needed: a run
   SUSPENDED on purpose (`POST /runs/suspend`) stays suspended through a recreate (`GET /runs/parked` lists it, only
   `POST /runs/resume` wakes it); saves in `State/hai-runs-saved/` and `State/hai-runs-pinned/` are never touched; an
   overnight / self-eng drive resumes by itself (`OVERNIGHT RESUMED …`, off switch `OVERNIGHT_RESUME_AFTER_RESTART=false`);
   a sync `POST /query` caller's HTTP response is lost. `POST /runs/park` still exists: use it to HOLD a run for a reason
   of its own (an operator wants it stopped at a turn boundary), never as a deploy step. The superseded "PARK them first"
   text is in runbook-orchestrator-deploy-superseded-2026-11.
10. **Run the deploy-gate suite** (bind-mounts `tests/`, tees to `/opt/arrstack/suite-logs/`):
    `ssh sundae "sudo /opt/arrstack/run-suite.sh"`. Wrapper sets `HAI_NOTES_DIR=/tmp/hai-test-notes` so
    test runs don't pollute the live notes vault.
11. **Sync the coding workspace** (LAST step, see next section):
    `ssh sundae "sudo /opt/arrstack/sync-workspace.sh"` — refuses on dirty/extras; commits a
    `sync-to-live baseline` in the `orchestrator-real` workspace. Skipping this makes the staleness
    check refuse the next overnight/self-eng item AND, since 2026-10-16 (#37 / #34 register R2), every
    `coding_task` start on `orchestrator-real` from ANY lane (chat, Absurd, decomposed) — one function,
    `overnight.stale_reason`. The Deep Work tile's **Workspace** row shows the state (in sync / N files of
    undeployed work / STALE / could not check). ⚠ Before syncing over HAI's own unmerged work, save it:
    `git -C <workspace> format-patch --stdout <baseline>..HEAD > Z:\Documentation\State\<name>.patch` and
    prove it rebuilds HEAD in a throwaway copy (done for McCoy 2026-10-16).

Container-name note: compose service key is `orchestrator`; container name is `orchestrator`.
(Compare Seerr where the service key is `overseerr` but container name is `seerr` — historical.)

**⚠ A BUSY MOTHERSHIP DOES NOT BLOCK AN ORCHESTRATOR DEPLOY — only step 10 is affected.** Steps
1-9 and 11 touch only Softserve and Sundae; the rebuild is unaffected. **Step 10 gains exactly
one extra failure:**

`test_scheduler.ResidencyIsNotReservationTests::test_warmth_cannot_overturn_a_policy_tier` —
asserts the chat placement order is `[bighonker, mothership, softserve]`, but with the card genuinely
busy the scheduler **correctly drops Mothership**, giving `[bighonker, softserve]`. The policy under
test (warmth cannot overturn a tier) is right; the test verifies it against **live host
availability** instead of a fixed host set, so a working lab fails it.

> ⚠ **THE CLEAR-LANE GATE IS FULLY GREEN — THERE ARE NO "KNOWN BASELINE REDS", AND ANY RED IS
> YOURS.** the owner's standing rule: *"No- we don't go over red tests. No skipping."* **Expect zero
> failures on a clear lane; investigate every red.** The busy-lane
> `test_warmth_cannot_overturn_a_policy_tier` flip is the one documented lane-dependent failure
> (plausible-but-unverified); the proper fix is mocking the host set in that one test.
> ⚠ **2026-10-02: a second lane-dependent family was found and fixed** — with Mothership running a compute order
> (`mothership.gaming_busy()` True, model not warm), `app._ensure_lead()` returns False, `_run_think_hard` returns before
> `chat()`, and the four `test_coding_job_toolset.TheThinkHardSeam` tests error with `IndexError` on an empty capture list. Proven
> a lane effect, not a code regression, by a control (same tests, `gaming_busy` forced free = green). Fixed by pinning
> `_ensure_lead` in that class's `setUp`. **Tell for the next one:** a test that asserts on what was handed to `chat()` goes red
> when the code path never reaches `chat()` because a live host probe said "busy".

⚠ **NO UNIT TEST MAY WAIT ON A NETWORK TIMEOUT — enforced in `tests/_no_live_probes.py`.** A
suite whose tests call the REAL fleet and then wait for it to not answer drifts to minutes; if
the gate's wall time creeps from tens of seconds toward minutes, something new is dialling out —
profile it. **How (2026-10-25):** run a COPY of the gate with unittest's per-test timing, so the isolation env is
identical: `sed 's/python -m unittest discover -s tests -t tests/python -m unittest discover --durations 40 -s tests -t tests/' /opt/arrstack/run-suite.sh > /tmp/run-suite-durations.sh`
(the flag goes AFTER `discover`), run it, read the "Slowest test durations" block. Durations that are exact multiples of
one number are a timeout. ⚠ It crept unnoticed from ~65 s (early October) to 155 s, 389 s while Mothership hung, and
197 s after the 10-24 `.invalid` ssh redirect: Docker's resolver waits **16.0 s** to fail that name, nine tests paid it
~10 times. The suite's ssh guard (`tests/test_zzz_no_live_model_calls.py`) now answers an `.invalid` ssh at once with
ssh's own rc-255 failure → **37 s**. Watch the `Ran N tests in X s` line on every gate.

**The five live calls now pinned in one shared module** (add any new probe HERE, in the same pass
that adds it to the code):

| pinned call | why it cost | measured |
|---|---|---|
| `scheduler.gpu_health` | ollama probe per host | 3 hosts × 4 s = the tell-tale **12.03 s** |
| `ollama_client.model_state` | `/api/ps`, `REACH_TIMEOUT` | 4 s |
| `scheduler._glances` | behind `ram_free_gb`/`cpu_load_pct` | 4 s |
| `app.failover_available` | → `ollama_client.reachable` | 4 s, in **9 tests across 5 modules** |
| `mothership._lead_python` | **SSHes into Mothership from a unit test** | 26 calls × 0.48 s in ONE class; `test_dataset` was 42 s |

**Three traps, all of the shape "the fix looked right and changed nothing":**
1. **Pin the DEADLINE, not the poll.** Shrinking `WAKE_POLL` to 1 ms saves zero seconds: the loop
   runs to `BRAIN_WAKE_WAIT` either way — it stops napping and starts spinning over the same
   window. Busy-waiting is strictly worse than waiting.
2. **Failing fast is useless if the code RETRIES.** Killing the transport leaves `chat()`
   retrying — `sleep(3 * (attempt + 1))` for attempts 0 and 1 = 9 s of pure sleep. `CHAT_RETRIES`
   must be pinned too.
3. **PROFILE, do not read the source and guess.** `cProfile` sorted by `tottime` names the true
   culprit in one run; "obviously correct" edits made from reading the source move nothing.

⚠ Never "fix" a slow test by raising a timeout, marking it slow, or parallelising the suite: these
tests share a claim directory with PRODUCTION, so concurrency would race live state and invent a
new flake. And never pin the thing a file is actually TESTING — `training_busy` in
`test_stated_run_guard`, `gpu_health` in `test_gpu_health`/`test_gpu_lost_report` (both use
`start_no_live_probes(skip=…)` for exactly this). A mock over the subject makes a suite pass while
checking nothing, which is worse than slow.

**ASSERT THE TEST COUNT, NOT "OK".** A suite that discovers nothing also prints OK. The gate is
`unittest discover -s tests -t tests`, which only collects `TestCase` subclasses **inside
`tests/`** — so a pytest-style module (module-level `def test_*`, fixtures, `monkeypatch`) dropped
into `tests/` is discovered as **ZERO tests while looking like coverage**, and any test file left
at the mirror ROOT is never run at all. Know the number you expect before you run it — the last
clear-lane run's count, from the tee'd logs in `/opt/arrstack/suite-logs/`. If you added N tests
and the count did not move by N, your tests are not running, whatever the suite says. ⚠ And do
**not** "fix" root-level
test files by pointing the gate at pytest — pytest also collects
`backups\pre-progress-status-20260616-111900\test_runner.py`, a stale June copy (29 methods), which
is the adjacent-decoy trap. Move the file into `tests/` and write it as a `TestCase` instead.

### Post-deploy workspace sync

The `orchestrator-real` coding workspace must match live before any overnight/self-eng run, and live only
changes at deploy time — so the sync is a **deploy step**, not a run-time job. LAST step of every
orchestrator deploy:

```bash
ssh sundae sudo /opt/arrstack/sync-workspace.sh
```

Copies top-level `*.py` + `tests/*.py` + `genetic_mesh/*.py` live→workspace (skips `*.bak*`), **plus the
non-Python `ASSETS` the suite reads** (see the next section), commits a `sync-to-live baseline`
in the workspace git. Safety: **refuses a dirty workspace** (mid-task); workspace-only files are *warned*
about, never deleted (they may be undeployed HAI work — review by hand). Deliberately NOT run at run start:
the normal workspace state is "clean but ahead of live" (finished work awaiting review) and a blind
run-start sync would regress the tree over it.

### coding-runner suite RED while the gate is GREEN → test-environment parity

**Symptom:** HAI's workspace suite reports failures/errors that `run-suite.sh` does not — so HAI
cannot tell its own regression from the room's background noise, and neither can a reviewer.

Parity has **four** layers. Three are in the image/service, the fourth is the workspace TREE:

| layer | what breaks | where the fix lives |
|---|---|---|
| pip deps | runner installed only its own lean list → `ModuleNotFoundError` at collection | `coding-runner/Dockerfile` installs the ORCHESTRATOR's `requirements.txt` too |
| apt deps | `mediapipe` dlopens GLES at import; `ffprobe` needed | `ffmpeg libgles2 libegl1 libgl1 libglib2.0-0` |
| runtime env | `ADMIN_USERS` unset → 2 failures | `docker-compose.override.yml`, coding-runner service |
| **workspace tree** | **non-`.py` artifacts frozen at workspace creation** | **`ASSETS` in `sync-workspace.sh`** |

⚠ **The fourth is the one that hides.** Any non-`.py` file NOT on the `ASSETS` list stays frozen
at its workspace-creation-day content — `tests/test_identity.py` reads three such files
CWD-relative, so a frozen copy turns permanently red (or worse, silently wrong).
⚠ **A STALE `Dockerfile` is worse than an absent one**: that test guards its check with
`pathlib.Path("Dockerfile").exists()`, so a June copy makes the check FIRE against wrong content.
⚠ The run-start staleness guard above does **not** cover this either — its scope is also
top-level `*.py`.

The existing `sync-workspace.sh` copies its named `ASSETS` (root requirements/Dockerfile/model/question data plus coding-runner requirements/Dockerfile/registry) and recursively copies the fixed `ASSET_TREES=(ui tests/fixtures)`. It preserves dirty-workspace and workspace-only-Python refusals, never deletes unrelated files, and commits the sync baseline. Missing upstream assets produce a loud warning. The landmarker remains gitignored. Add new runtime/test asset trees to this same synchronization path, not a separate copier.

For `orchestrator-real` and `orchestrator-pilot`, the registered unit command is prefixed with `env HAI_THINK_CAPS_PROBE=0`. This matches the gate's thinking-capability probe isolation. It affects that test subprocess only; live model thinking and `REACH_TIMEOUT` remain unchanged. Tests of the probe explicitly enable and mock it. `REACH_TIMEOUT=0` is not the switch for `can_think`.

Measured 2026-11-10 at 3,770 collected tests:

| execution | result | skips |
|---|---|---|
| full deployment gate | green | 2 existing expected skips |
| registered workspace unit profile | green | 23 enumerated skips |
| runner's own suite | 169 green | 0 |

The workspace skip manifest is preserved in `research/doc-workflow-parity-20261110/workspace-verification.json`: absent `/docs` (2), FLAME assets (4), Lab/bin scanner (5), MICA artifacts (1), gate-only assertions (7), missing policy table (1), missing P20 token (1), unavailable retrieval measurement (1), and the existing unproven-rule skip (1). These are unmeasured cases, not passes. Do not mount the private documentation/secret layers into the coding runner to erase skips. The full deployment gate remains mandatory.

Count skip records with indentation and either quote style: skipped subtests can be indented, and reasons containing apostrophes are double quoted. Match the manifest total to the suite's `skipped=N`; do not claim a partial list is complete.

⚠ **BUILD BEFORE SUITE.** `run-suite.sh` mounts ONLY `tests/`; every app module comes from the
IMAGE. A suite run before the rebuild tests the OLD code — a new module fails
`ModuleNotFoundError` and an edited one passes/fails on its previous contents.

### A NEW NON-.py FILE needs its own Dockerfile COPY line — "FileNotFoundError: /app/<file>" / tests vanish from the gate

The orchestrator image copies `*.py`, `genetic_mesh/` and named files only (`Dockerfile`). A module that loads a data
file at import (2026-10-17: `mccoy.py` opens `mccoy_questions.json`) passes every test in HAI's coding workspace — the
file sits beside it there — and then FAILS TO IMPORT in the image, taking `import app` (the whole orchestrator) with it.
The gate's collection pin caught it (`collected 1981 (pinned 2825), skipped 34` — every test module that imports `app`
vanished); production never ran that image. **Rule: a deploy that adds any non-.py file the code reads ships the
Dockerfile line in the same deploy** (`COPY mccoy_questions.json .`), and the deploy's file list comes from
`git diff --name-only <live commit> HEAD`, which includes the Dockerfile.

### The deploy gate PINS its own test count — "skipped" is not "passed"

**the owner's rule, and the reason this exists:** *"if it's deploying it must pass all the tests."*
A unittest exit code means **"nothing that RAN failed"** — it is silent about how much ran, and
it treats a SKIP exactly like a PASS. Both gaps are invisible in the summary line.

**Measured with a decoy** (a deliberately broken `app.py` shadowed into a throwaway container —
the live orchestrator untouched):

| | collected | result |
|---|---|---|
| healthy | **1939** | `OK (skipped=2)` |
| broken `app.py` | **1545** | `FAILED (failures=137, errors=60, skipped=23)` |

**394 tests vanished from COLLECTION and 21 more turned into skips.** That run went red for
other reasons — a broken `app.py` does NOT sneak through — but the near-miss is the lesson:
`Ran 1545` reads as a perfectly normal suite. A change that only destroyed collection, without
breaking whatever survived, would have printed a clean `OK` on a suite missing a fifth of
itself, and the gate would have certified the deploy. Six test classes sit behind a
`try: import app / except: _APP_IMPORTABLE = False` guard that converts breakage into skips.

**Fix, live in `run-suite.sh`:** the gate asserts its own counts after the run. **Since 2026-10-09
the pin is a FILE that ships with the tests it counts: `tests/gate_pin.py` in each mirror
(`E:\SSDShared\orchestrator-build\tests\gate_pin.py` → `/opt/arrstack/orchestrator/tests/`,
`E:\SSDShared\tolkien-build\tests\gate_pin.py` → `/opt/arrstack/tolkien/tests/`), read by the gate
script (`PIN_FILE`). That file is the only current pin — read it there, never from this page.**
Before 10-09 the numbers sat at the top of the script on Sundae and every bump was a hand `sed -i`
there (the floor hook had to exempt it); now a bump travels in the same deploy as the test change,
and the script never changes for it. A missing or unreadable pin file fails the gate CLOSED
(`[PIN: CANNOT EVALUATE - no readable EXPECT_TESTS/EXPECT_SKIPS …]`, exit 3). The env overrides
still win, for decoys only. (The counts in this section are the decoy experiment's readings, not
the pin.)

Mismatch ⇒ `[PIN: MISMATCH …]` and **exit 3, even when unittest itself returned 0**. Unreadable
or missing capture ⇒ fails CLOSED (`CANNOT EVALUATE` — a gate that could not measure is never a
pass). A genuine suite failure still wins the exit code; the pin only decides an otherwise-green
run.

**When you legitimately add or remove tests, update `tests/gate_pin.py` in the same deploy.** That
is the entire point — a reviewed one-line act instead of silent drift. **Decoy-verified both
directions**: `EXPECT_TESTS=9999 sudo /opt/arrstack/run-suite.sh` → `MISMATCH`, exit 3 while the
suite itself passed; unmodified → `[PIN: ok - … collected, … skipped]`, exit 0. Use that same
override to re-prove the pin after touching it; never to force a pass.

**The 2 pinned gate skips are both known, and neither is a silent hole:**
1. a retrieval test that self-reports COULD-NOT-MEASURE because the gate passes
   `EMBED_ENABLED=0` (its own message notes the sibling index-membership tests carry the
   guarantee). **Open decision:** running the gate with embeddings enabled would retire this
   skip at the cost of a heavier gate — not taken unilaterally.
2. the deliberate `Rule (3) is UNPROVEN` skip — a measured decision not to pin an unproven
   rule, documented in `test_persona.py` and [[HAI-Orchestrator-Internals]].

Any THIRD skip now fails the gate.

**The Tolkien gate pins too (since 2026-10-04) — `run-tolkien-suite.sh`, the same block.** It had
no pin until then: it printed `OK (skipped=1)` for days while
`test_tolkien_cameras…test_the_conversion_does_NOT_depend_on_the_process_timezone` skipped itself
(it only discriminates off Chicago time, and the tablet-foundry service runs in Chicago time). That
test now forces `TZ=Asia/Tokyo` itself, so the Tolkien pin is **0 skips** — any skip fails it, and
so does a test file vanishing from collection. Numbers live in `tolkien-build\tests\gate_pin.py`
(same rule: change it in the same deploy as the test change; `fleet_deploy` ships it with the tests). `fleet_deploy_tolkien.py` already treats the
pin's `MISMATCH` line as red. Decoy: `EXPECT_TESTS=9999 sudo ./run-tolkien-suite.sh` → exit 3.

### The Tolkien gate must not touch the live order desk (TABLET_INBOX / TABLET_OUTBOX / TABLET_FOUNDRY_ROOT)

The gate runs `docker compose run tablet-foundry`, which inherits that service's mounts: Sundae's `/mnt/ssdshared`
as `/ssdshared` and `/opt/arrstack/tablet-foundry` as `/tabfoundry`. `tablet_foundry.INBOX` / `OUTBOX` / `ROOT`
default to the **live shared jobs dir** (`/ssdshared/tabfoundry-jobs/…`, the inbox the live Tolkien on rigby reads)
and Tolkien state. Until 2026-10-10 the gate redirected only `HAI_STATE_DIR`, so any test that forgot to patch a store
wrote to the live desk. Found when a new bench test, run locally, dropped `trace/b1/pipe.log` into the live outbox (on
Windows `/ssdshared/…` resolves to `E:\SSDShared\…`). Fixed at the container boundary: the gate sets
`TABLET_INBOX=/tmp/gate-inbox TABLET_OUTBOX=/tmp/gate-outbox TABLET_FOUNDRY_ROOT=/tmp/gate-tabfoundry` (proven 663/663
OK with them), and **`tests/test_gate_isolation.py` fails the suite when any of `tf.INBOX/OUTBOX/ROOT/STATE_DIR` is
live** (proven both ways). **Running the Tolkien tests locally:** set the same three env vars to scratch dirs, or that
test fails — by design. A new store in `tablet_foundry` gets a `TABLET_*` env knob, a redirect here, and a line in that
test. Script backup: `.bak-20261011-*-gate-isolation` on Sundae; copy in `E:\SSDShared\Backup\SundaeStackarr\`.

### coding_test returned the least useful window of its own output — HAI could not see WHY tests failed

**Symptom:** HAI runs `coding_test`, the suite is red, and HAI cannot say which test broke —
it burns turns guessing, or chases phantom failures. `returncode` tells it *whether*; nothing
told it *what*.

**Cause: TWO truncation layers, both cutting from the HEAD**, on a payload whose answer is at the
end. `python -m unittest -v` writes its per-test chatter first and its `Ran N` line, its
`OK`/`FAILED` verdict and every traceback **last** — to **stderr**.

| layer | cap | what it did |
|---|---|---|
| runner — `coding-runner/runner.py` | `max_output_bytes = 128_000`, head-cut | a real `unit` run produced 127,936 chars of stderr; **the verdict was already gone here** |
| orchestrator — `coding_client.compact()` | `limit=24000`, head-cut of the SERIALISED JSON | cut 129,590 → 24,000, keeping `... ok` lines |

Net: HAI received ~24k of passing-test lines, no verdict, no traceback, no failure names — from
the tool it grades **all of its own work** with.

**Fix: `stream_clip.py`, ONE module imported by BOTH layers** (see the coupling note above).
Keeps head **and tail**, split 1:2 toward the tail (`HEAD_FRACTION = 3`) because that is where the
answer is, with a middle marker naming exactly how many characters/bytes were dropped so a
clipped stream can never read as a whole one. `compact()` no longer cuts the serialised string —
it clips the **stream fields** and re-serialises, so the reply stays **valid JSON** and
`returncode` / `timed_out` / the runner's own `*_truncated` flags survive intact.

**Pinned:** `tests/test_stream_clip.py`, 18 tests. The load-bearing pins
build a payload shaped like the real one (chatter first, verdict last) and assert the ANSWER
arrives. They assert PROPERTIES, never source text — a future rewrite of the clipping strategy
stays green if and only if it still delivers the verdict. **Red-proof verified**: monkey-patching
the old head-cut back in turns **12 of 18 red, including all six load-bearing ones.**

⚠ **The general rule, worth more than the fix:** *when you cap output, cap it where the ANSWER
isn't.* Ask what the consumer needs from this payload and keep that end. And **two independent
truncations of one stream compose into something neither author intended** — that is why this is
one shared module and not two tidy local fixes.

### The deploy gate can REACH PRODUCTION — isolate at the container boundary, not per-test

**A test suite that can reach production is not a gate, it is a second writer.** The gate runs
`docker compose run --rm orchestrator` with the **live `/docs` mounted writable** and the
container's **real Discord credentials** present. Everything that keeps a test off production
state is therefore per-test discipline — a mock somebody has to remember — except where an env
var makes it structural.

**Root cause — module identity, not configuration.** A test that calls `importlib.reload()` on a
module replaces the OBJECT in `sys.modules`. Other test modules bound the ORIGINAL object at
discovery time, and `mock.patch("mod.fn")` resolves **through `sys.modules`** — so the patch lands
on the new object while the code under test still calls the old one. **The patch silently does
nothing and no error is raised anywhere.** Here that let `test_selfeng_order`, whose entire
assertion is that `_overnight_start` must NOT be called, call the real one.

⚠ **The diagnosis trap:** the obvious hypothesis is a leaked env var or module attribute — but
`QUEUE_DIR` and the env can probe **identical** while `id(p20_queue)` and `id(scheduler)` have
both **CHANGED**. A fix that restores attributes leaves the gate identically red. **If a module
is reloaded, restore the module OBJECTS:**

```python
self._saved = {"p20_queue": sys.modules.get("p20_queue")}   # setUp
for name, mod in self._saved.items():                        # tearDown, BEFORE rmtree
    sys.modules[name] = mod if mod is not None else sys.modules.pop(name, None)
```

**⭐ 2026-09-26 — ONE KNOB NOW MOVES EVERY WRITE STORE, AND A PIN KEEPS IT THAT WAY.** `config.STATE_DIR`
(env `HAI_STATE_DIR`, default `DOCS_DIR/State`) is the root of every store the orchestrator WRITES —
claims, gpu reports, run-state, rescue, watches, overnight, history, memory, voice, diag, queues, caches,
audit, absurd/remedy/escalation/contribution/user-plan dirs (29 builders in 12 modules were re-rooted; the
per-store env names below still work and now default under it). Both gate scripts pass
`-e HAI_STATE_DIR=/tmp/gate-state`. `tests/test_gate_state_isolation.py` (3 tests, gate-only, skips
locally) walks every loaded app module and fails on any UPPERCASE str/Path attribute still rooted at
`DOCS_DIR/State` that is not on its declared read-only allow-list — so a new store added without rooting at
`STATE_DIR`, or a gate script that drops the `-e`, turns the gate red instead of writing production.
**Deliberately live in the gate (read-only inputs, on the allow-list):** `PROFILES_FILE`,
`PERSON_RECORDS_FILE`, `flame.FLAME_DIR` (assets), `tools.DATASET_SETS_FILE`, `tools.STASH_CATEGORIES_FILE`
(curated), plus function-local reads of `insightface/` models and the foundry-written `current-run.json`.
**Adding a store:** root it at `config.STATE_DIR`; if it is a curated INPUT the orchestrator never writes,
root it at `DOCS_DIR` and add it to the allow-list with that justification. Positive control for the pin:
`HAI_NOTES_DIR=x HAI_STATE_DIR=/docs/State/decoy python -m unittest discover -s tests -t tests -p
test_gate_state_isolation.py` must FAIL naming all ~24 stores (from Git Bash set `MSYS_NO_PATHCONV=1`, or
the leading `/docs` is silently rewritten to a Git install path and the decoy never arrives). Shipped
2026-09-26 late: gate **2433 OK / 2 skipped**, tolkien 291 OK, orchestrator + both rigby consumers recreated,
production `STATE_DIR=/docs/State` (unchanged paths, verified in-container). ⚠ Tests that redirect
`config.DOCS_DIR` to a temp dir and build fixtures under `State/…` must redirect `config.STATE_DIR` too
(`os.path.join(tmp, "State")`) — seven dataset tests went red on exactly that in the first gate run.

**Structural isolation shipped before the single knob** — each is an env the code already honours, set once
in `run-suite.sh` where no individual test can opt out (kept: they still redirect their stores, now under
`STATE_DIR` by default anyway):

| store | flag in `run-suite.sh` | covers |
|---|---|---|
| HAI notes / DM watch loop | `-e HAI_NOTES_DIR=/tmp/hai-test-notes` | the notes watcher only — **NOT** a direct call to a notifying function |
| p20 render queue | `-e P20_QUEUE_DIR=/tmp/gate-queue` | verified: a gate run writes nothing to the real queue |
| **fleet run-state (the placement truth)** | `-e HAI_FLEET_RUN_STATE=/tmp/gate-fleet-run-state.json`<br>`-e HAI_STATED_RUN=/tmp/gate-fleet-run-state.json` | added 2026-09-26 (leak shape 3). BOTH vars, ONE path: `app.py` writes `FLEET_RUN_STATE`, `scheduler.py` reads `STATED_RUN`, and `test_stated_run_guard` asserts they are equal — redirect one alone and the gate goes red. Positive control: `docker compose run … python -c 'import app, scheduler; print(app.FLEET_RUN_STATE, scheduler.STATED_RUN)'` prints the tmp path twice. Live decoy: one gate run under the fixed script with a 1-s sampler on the share → mothership record never changed, notes name-change count unchanged. |
| **every other WRITE store under `/docs/State`** (audit-driven, same evening) | `-e REMEDY_STATE_DIR=/tmp/gate-remedies`<br>`-e ESCALATION_STATE_DIR=/tmp/gate-escalations`<br>`-e ABSURD_STATE_DIR=/tmp/gate-absurd`<br>`-e CONTRIB_STATE_DIR=/tmp/gate-contributions`<br>`-e USER_PLANS_DIR=/tmp/gate-user-plans`<br>`-e HAI_FLEET_WATCH_FILE=/tmp/gate-fleet-watches.json`<br>`-e HAI_RESCUE_STATE=/tmp/gate-rescue-state.json`<br>`-e AUDIT_FILE=/tmp/gate-audit.jsonl`<br>`-e CONTRIB_NOTIFY_WEBHOOK=` | **The audit is one grep and should be re-run whenever a new store is added:** every `os.environ.get("…")`/`os.getenv("…")` in `app.py`/`scheduler.py`/`config.py` whose default sits under `DOCS_DIR`/`/docs`, versus the `-e` list in `run-suite.sh`. Anything missing is a leak waiting for a test to use it — the remedy dir from leak shape 1 was STILL unredirected six weeks later (its test isolates itself, which is the discipline that fails). Read-only inputs (`DOCS_DIR`, `PROFILES_FILE`, `PERSON_RECORDS_FILE`, `EMBED_CACHE_DIR`) stay live on purpose: grounding tests read the real library. Gate under the widened list: 2430 OK, 2 skipped, as pinned. |
| **HAI self-eng store** | `-e SELFENG_PROPOSALS=/tmp/gate-proposals.yaml`<br>`-e SELFENG_COMPLETED=/tmp/gate-completed.json`<br>`-e SELFENG_PLANS_DIR=/tmp/gate-plans` | without it, any test calling `backlog.propose()`, `record_completed()` or the planner writes the owner's real review queue through the live `/selfeng-proposals` rw mount — per-test redirection is precisely the discipline that fails. `SELFENG_BACKLOG` deliberately left alone: the kernel already makes it unwritable (`rw=false`, verified on the live mount). |
| **eval campaign files** (live rw `/tabjobs` share) | `-e EVAL_CAMPAIGN_ROOT=/tmp/gate-eval-campaigns` | added 2026-09-27 with eval campaigns-as-compute-orders: `app.EVAL_CAMPAIGN_ROOT` defaults to `/tabjobs/campaigns/eval`, and the gate inherits the production `/tabjobs` **rw** mount — the same "not under /docs, so the State audit misses it" shape. `test_eval_order.py` also patches the root to a temp dir; the env is the boundary no test can opt out of. **Widen the audit grep to every rw mount in the compose override, not just `/docs`.** |
| embeddings | `-e EMBED_ENABLED=0` | keeps the gate light (costs 1 honest skip) |

⚠ **`EMBED_ENABLED=0` means the gate CANNOT test live tool retrieval, and that is not a gap to
paper over.** With embeddings off, `tool_selection.select()` deliberately falls back to
`CORE_TOOL_NAMES` (`tests/test_tool_selection.py::test_embeddings_unavailable_falls_back_to_core…`),
so any pin asserting "the selector surfaces tool X" is structurally red in the gate — and the
suite's own selection tests inject **synthetic** vectors via `_Patched`. Faking it that way would
test the fixture, not the real description↔embedding relationship. **So retrievability of a
non-CORE tool is a LIVE measurement, not a gated one:** run it against the deployed container and
record the numbers. Instrument: `retrieval_probe.py` — prints per-phrasing hits, the
offered/total counts, whether narrowing actually engaged, **and a negative control**, because a
selector that returned everything would "pass" every positive line and mean nothing.

⚠ **`HAI_NOTES_DIR` is NOT a DM firewall and must not be cited as one.** It gates the **watch
loop**; a test that calls a notifying function directly walks straight past it.
**✅ NOTIFICATION PATH RESOLVED 2026-08-30:** production continues to reference credentials from
the lab locker/secret layer. `/opt/arrstack/run-suite.sh` explicitly supplies empty
`HAWKADMIN_DM_TOKEN`, `OVERNIGHT_DM_TOKEN`, `OVERNIGHT_NOTIFY_WEBHOOK`, and
`ABSURD_NOTIFY_WEBHOOK` values to the disposable gate container, so the gate cannot inherit a live
notification channel even if a mock is missed. Production notification behavior is not globally
disabled. Installed wrapper SHA-256 `d2ed50e388101c5611a1aad03474301624fb2d2c0e58df94adbe849171ed32ce`;
backup `/opt/arrstack/run-suite.sh.bak.p12-close-20260830-1534`; live-path proof **Ran 2394, OK,
skipped=2**. `/docs` remains live/writable and still requires the per-test state-directory
discipline below; notification isolation does not claim to solve that separate boundary.

**If the gate DOES leak into the queue, unwind it through the front door:** `p20_queue.mark(job_id,
"expired", note="…")` — the queue's own transition function, the same one the tick uses. **Never
hand-delete a queue file** (that is walking the factory floor). Select on
`order_name` + `initiator` + `status == "pending"`, print what changed, and **assert the selector
matched something** so a silent zero cannot read as success.

### The gate runs `tests/run_suite.py` — the same runner a stranger runs (example users, no network)

Since 2026-11-16 `run-suite.sh` runs `python tests/run_suite.py`, not `unittest discover`. The runner, in the head's
own tree, does before any test imports: every write store under a fresh temp folder, every notification channel empty,
the suite's own EXAMPLE users (`tests/fixtures/example_users.json` + the user settings, example names only, never the
household's), and it refuses any non-loopback connection, any name lookup but localhost, and any `ssh`/`scp`. It sets
`HAI_TEST_ISOLATED=1`; the tests that check the isolation itself (`test_chat_window`, `test_mccoy`, `test_swap_route`,
`test_zzz_no_live_model_calls`) accept it as well as the old `/tmp/gate*` paths. The `-e` list in `run-suite.sh` stays
(belt and braces); the gate's own `[PIN: ...]` line is still the verdict the doors read. The runner prints its own
`[COUNT: ...]` line and every skip with its reason.

- **The pin is the lab tree's.** `tests/gate_pin.py` `OPTIONAL_TESTS` holds the test count of each file a head may not
  carry (the private extensions that stay home, the media add-on's tests that ship in `addons/media`); the runner
  subtracts the absent ones, and `tests/test_gate_pin_counts.py` keeps those numbers true. **Adding a test to one of
  those files means updating its count there too**, or the lab gate goes red on that test.
- **Proven both ways:** lab gate 4049 / 2 skipped; the PUBLIC tree in a stranger-like container (no lab mounts, no lab
  settings, its own network) 3877 / 20 skipped, green. The 20 are could-not-measure-here (no library mounted, no lab
  tools folder, no FLAME assets), each printed with its reason.
- **The owner is a setting too** (`OWNER_USER`; the lab's is the owner, set for the head, the coding runner and each
  factory's `lab.env`): owner-only features key on it and prompts name the approver through `config.OWNER` /
  `config.OWNER_S`. The runner and the gate set `OWNER_USER=the owner` (the author's name ships). Never write
  `user == "the owner"` in code; compare with the setting.
- **A test that needs users sets its own, or uses the example ones**; none may rely on the lab's real users. The
  example values in the runner and in `run-suite.sh` are generated from the live settings by
  `hai-release\review\doc-jobs\gate_user_env.py` (the publish script's example names, case kept).

### The device roster file (`HAI_ROSTER_FILE`): where the scheduler's boxes, tablets and per-box tables come from

`scheduler.py` holds no device. `CARDS`, `RAM_RESERVE_GB`, `CPU_BUSY_PCT`, `CHAT_TIER`, `CAPTION_S_PER_IMG`,
`HOST_CAPTION_MODEL`, `HOST_CAPTION_WORKERS`, `HOST_COMPUTE_PY`, `TRIAGE_CLASSIFY_S_PER_ROW`,
`TRIAGE_REWRITE_S_PER_SECTION`, `ORDER_HOST_DEPRIORITY` and `TABLETS` are DERIVED at import, under those same names, from
one JSON file: the lab's is **`Z:\Documentation\State\device-roster.json`** (`/docs/State/device-roster.json` in the
containers), named by `HAI_ROSTER_FILE` in the compose override (Kirk) and rigby's `tolkien.env` (Tolkien, which imports
the scheduler). `config.py` reads it once (`config.ROSTER`, `config.roster_role`, `config.roster_box_at`), so the
settings, the scheduler and Tolkien share one copy.
- **Roles are jobs a machine does:** `BRAIN` = the device with role `brain`, `FAILOVER` = `failover`, `HEAD` = `head`
  (where Kirk and Spock run), `ADVISOR_HOME` (McCoy's box, `KINDS["advisor"]["home"]`) = `advisor`, `TOLKIEN_BOX`
  (also `chat_window.TOLKIEN_BOX`, `gpu_render.TOLKIEN_BOX`) = `tolkien` (the box Tolkien runs on: a GPU factory whose
  own lane is reached through Tolkien's desk, so it is in the roster but never in `CARDS`; no such device = None; not
  the Fellowship, which is the tablets), `config.ACQUISITION_HOST` = `acquisition` (the box whose dataset agent builds
  sets into its record's `dataset_root`, `tools.ACQUISITION_SET_ROOT`; none = None). `tolkien` and `acquisition` are
  OPTIONAL; every other role no device claims falls to the `brain` device. `brain` and `failover` belong to the legacy
  brain ladder.
- **The lead is a SEAT, not a role.** No device carries a "lead" role (a roster that still lists one is read with it
  ignored). The model holding the seat is the menu's crown (`policy.json` `roles.crown`); the machine it sits on is
  **`config.LEAD_BOX`** (`scheduler.LEAD_BOX`, label `config.LEAD_BOX_LABEL`) = the crown's `box` when that is a card in
  the roster, else the card with the most memory (`config.lead_box`; a one-device head: the one device). Read once at
  import. The values that depend on where the seat is are `config.LEAD_BOX_*`, each read from that machine's own
  settings (below): `_SSH_HOST` / `_SSH_USER` / `_SSH_KEY`, `_SWAP`, `_WAKE_SETTLE`, `_MIN_FREE_MB`, `_KEEP_ALIVE`,
  `_BRAIN_MODEL` / `_BRAIN_NUM_CTX` (the legacy ladder's small model on that machine).
- **Machine settings are per device, `PC<n>_<SETTING>`.** Physical machines are numbered PCs: a record's `pc` field, or
  without one its place in the roster, counting machines only (the lab: Mothership PC1, BigHonker PC2, Softserve PC3,
  rigby PC4). **A VM is not a PC:** a record whose `flags.parent` names another device is a VM on that PC,
  `PC<n>_VM<m>` (the lab's Sundae is `PC3_VM1`). Tablets are not numbered. `config.device_setting(name, KEY)` reads a
  device's setting; the ones read today: `_NAME` (renames the device: its name, lowercased, and its label as typed;
  VMs follow their PC), `_MAC` (Wake-on-LAN, `config.device_mac`; empty = never woken), `_GLANCES` (its Glances address;
  empty = no live metrics), `_ALIASES` (comma-separated words a person may call it by, beside the record's `aliases`:
  `config.roster_aliases`, used by `check_host` and the wake tool), and the `LEAD_BOX_*` ones above. The lab's values
  are in the compose override (`PC1_MAC`, `PC1_GLANCES`, `PC1_SSH_*`, `PC1_SWAP`, `PC2_MAC`, `PC2_ALIASES`,
  `PC3_GLANCES`). The names before 2026-11-17 were `LEAD_MAC` / `BRAIN_MAC`, `LEAD_GLANCES` / `FAILOVER_GLANCES`,
  `LEAD_SSH_*`, `LEAD_SWAP`, `BRAIN_ALIASES` (and before 2026-11-16 `MOTHERSHIP_*` / `BIGHONKER_*`).
- **A record:** `name` (lowercase), `label`, `roles`, optional `pc` / `vm`, `url_setting` (the head setting holding its
  model-server address, e.g. `HEAVY_HOST`; legacy) or `url`, `gpu`, `vram_gb`, `ram_gb`, `cpu`, `flags`
  (`big_jobs_only_above_gb`, `cpu_heavy_ok`, `hosts_services`, `parent`, `container_env`), the tuning fields
  (`ram_reserve_gb`, `cpu_busy_pct`, `chat_tier`, `caption_s_per_img`, `caption_model`, `caption_workers`, `compute_py`,
  `triage_classify_s_per_row`, `triage_rewrite_s_per_section`, `order_deprioritize`), `model_contexts` (each model's
  measured fully-resident window on this machine, `{"<model>": <tokens>}`: the fallback when the placement table
  cannot be read; config.py merges it into `MODEL_NUM_CTX`), `aliases`, `mac` (a MAC written in
  the record, else its `_MAC` setting), `window_gpu_clock` (the card clock its chat window holds,
  `config.WINDOW_GPU_CLOCK`; `HAI_WINDOW_GPU_CLOCK` still overrides), and `why` (the reason for each hand-set value).
  A `tolkien` device needs only `name`, `label`, `roles`. Tablets are the `tablets` list (`name`, `label`, `cpu`,
  `ram_gb`, `gflops`, and `arch` / `runtime_bits` / `direct_cpu_eligible` / `provisional` where they apply).
- **To change a value or add a device or tablet:** edit that file; it takes effect when Kirk and rigby's
  `tablet-foundry` next start. No code change, no gate.
- **Fail-loud rules:** a named file that is missing, unreadable or invalid STOPS THE IMPORT (Kirk will not start, and
  says why: `RosterError`), never a quiet fallback; so does a repeated `pc` / `vm` number, or two `_NAME` settings that
  give two devices one name. Only a head with no file at all and no `HAI_ROSTER_FILE` gets the one-device roster: one
  device `local` (PC1) holding every role, its model server at `BRAIN_HOST`, card sizes unmeasured; McCoy is resident
  on no card there.
- **The gates do not read the live file.** The head gate (`tests/run_suite.py`) and the Tolkien gate
  (`run-tolkien-suite.sh`, a read-only mount, like `tablet_orders.py`) both use `orchestrator-build\tests\fixtures\
  lab_roster.json`, a copy of the lab's roster; `tests/test_roster.py` proves it derives exactly the literal tables the
  code held before (same keys, values and order) and that `place()` answers the same on both. The gate blanks the
  lead machine's `PC1_SSH_*` / `PC1_SWAP` (it carries the live environment).
- **The code names no model (since 2026-11-17).** Every model setting's default is empty (`BRAIN_MODEL`,
  `HEAVY_MODEL`, `FAILOVER_MODEL`, `PC<n>_BRAIN_MODEL`, `MCCOY_MODEL`, `VLM_MODEL`, `EMBED_MODEL`, the overnight
  planner/critic/ladder, the nursing and medical reviewers, `ADVERSARY_CRITIC_MODELS`, `TRIAGE_REWRITE_MODEL_SCOPE`
  as JSON): a model comes from the placement table (the menu) or a pin. The lab's choices are its settings: the
  compose override AND rigby's `tolkien.env` (Tolkien runs the same config). A head that reaches a box with no model
  says so (its factory measures by itself) and never sends an empty model name; with no embedding model, retrieval
  is BM25 only.
- **Place from the lab's reference menu before your own is measured (`reference_box`, since 2026-11-17).** A
  device's `reference_box` field (or its `PC<n>_REFERENCE_BOX` setting, e.g. `PC1_REFERENCE_BOX=mothership`) names the
  box in the placement table whose card resembles yours: in the lab's reference menu `mothership` (24 GB), `bighonker`
  or `softserve` (8 GB). `config.apply_reference` (one function; every reader of the table goes through
  `config.load_policy`) gives the device a copy of that box's rows right after each original, plus its windows
  (`contexts`), memory profiles (`model_fits`) and offload entries, wherever the device has no measured row of its own
  for that model; each copy carries `reference_box`. Your own rows win model by model as the menu measures them. The
  crown (`roles`) is not copied: on your head the lead seat sits on your biggest card. The lab names no reference, so
  its table reads unchanged (proven live: `TABLE_AS_READ SAME` over 2,058 rows).
- **Two weighing rules that make a borrowed table safe (`scheduler.weigh_rows`, 2026-11-17).** (1) `box_here`: a row
  for a box this head does not have (not a card in its roster; a Fellowship row with no Tolkien box) is not weighed, so
  a table measured elsewhere sets no quality need and wins no place. (2) `declares_model`: a card row whose box states
  its capabilities without `model.<name>` is set aside before the quality need, with the note "does not declare
  model.<name>"; it is the same function the order check (`resolve_policy_tokens`) uses, and it compares names as
  ollama resolves them (`model_tag`: `nomic-embed-text` = `nomic-embed-text:latest`, which is how the lab's three
  boxes declare it). A box that has stated nothing is left to `place()`'s own rule. Live on the lab both rules
  removed zero rows.
- ⚠ Not the same file as rigby's `tablet-intake/roster.json` (the intake tool's serials), and not the head's
  `roster.py` (the CHAT-PROFILE roster: users, from `PROFILES` plus admin-added ones). The device roster is
  `config.ROSTER`.

### A new compose SETTING or MOUNT did not reach the container: the door does not write the LIVE override

The stack reads `/opt/arrstack/docker-compose.override.yml` on Sundae. The mirror's `orchestrator-build\docker-compose.
override.yml` is a copy of it, and `deploy_kirk_green.sh` / `deploy_orch.sh` copy a named file into the BUILD folder
`/opt/arrstack/orchestrator/` only, so a gate can pass and Kirk run green while a new environment line or volume is
absent (`docker exec orchestrator printenv <NAME>` prints nothing). Found 2026-11-17 with `CODE_MIRRORS` and the menu's
`/code-mirror/menu` mount. **The way:** pull the live file and diff it against the mirror (the only differences must be
your own lines); back the live file up to `/opt/arrstack/orchestrator/backups/docker-compose.override.yml.pre-<tag>-<stamp>`;
copy the mirror's file over it; compare sha256 on both sides; `docker compose config -q`; then `orch_recreate.sh` and read
the value inside the container. For environment lines alone, `hai-release\review\doc-jobs\apply_lab_env.py` (adds names
not yet set after an anchor) and `copy_setting.py` (a rename: the new name gets the old one's value) are the tools. A
setting whose default goes neutral in the same change must be live BEFORE that code runs (settings first).

### The head's settings file (`settings.env`, `HAI_SETTINGS_FILE`): settings without Compose, and what happens to a bad line

Since 2026-11-17 (plan step 2.1, Kirk gate 4086) the head reads one settings file itself, at the very top of
`config.py`, before any setting is read (`config.load_settings`, `config.SETTINGS`).
- **Where:** `HAI_SETTINGS_FILE`, else `settings.env` in the state root (`HAI_STATE_DIR`, else `DOCS_DIR/State`).
  Those three come from the environment only (the file cannot name its own place). **The lab has no settings file**
  (checked 2026-11-17: none at `/docs/State/settings.env`, none named), so the lab runs on its compose files exactly as
  before. A stranger's head uses it; the installer (plan M7) and the settings screen (M8) write it.
- **The lab's wall clock is a setting (2026-11-18):** `config.LAB_TZ` is `HAI_TZ`, else the container's `TZ`, else `UTC`, and `config.LAB_ZONE` is that zone (an unknown name falls back to UTC and says so once; `tests/test_lab_tz.py`). Notes, daily files, memory dates, the librarian's headers, cameras and the energy window read it; no module names a zone. The lab's value is the `TZ=America/Chicago` the compose already sets; the public example ships `TZ=UTC`. Tolkien's cameras do the same (`TOLKIEN_CAMERAS_TZ`, else `TZ`, else UTC) and the media add-on's recyclarr runs in `RECYCLARR_TZ`, else `TZ`, else UTC.
- **Format:** one `NAME=value` per line, the same as the public `examples/one-machine/settings.env.example` and a Docker
  `env_file`. Blank lines and `#` lines are skipped, `export NAME=value` is accepted, one pair of matching quotes around
  a value is removed.
- **The environment always wins.** A name already in the environment is left alone; every other name is PUT INTO the
  environment, so every `os.getenv` in the head, and in anything it starts, reads it the same way.
- **A line it cannot read STOPS THE HEAD** with `SettingsError: <path> line N is not NAME=value` (or `is not a setting
  name`, or `sets NAME again`). Fix the line and start again; a file read half-right is worse than one not read.
- **A name nothing reads** (most likely a typo, `BRIAN_HOST`) does not stop it: the startup log line `settings file
  <path>: N name(s) supplied, M set by the environment instead; NOT READ by anything (a typo?): …` names it. With no
  file the line is `settings file: none at <path>`. The known names come from the code itself (`config.settings_known`:
  every constant `getenv`/`environ` read and every `_secret_value` pair in the head's non-test files).
- **The owner and the Law persona are settings (2026-11-19):** `OWNER_USER` names the owner (the public code says
  "the owner" in prose and uses the example owner `Alex` where a test needs a user name; the lab's override sets the owner);
  `LAW_STATE` and `LAW_STATE_SOURCES` name the Law persona's state and where its law is read (the lab's: Illinois; the
  ILCS at ilga.gov, illinoiscourts.gov), and unset it speaks of "your state". The head door passes `OWNER_USER` to
  `orch_recreate.sh` as `RUNUSER`. The story detector's name signal reads `OWNER_USER` too; the head's own story test
  turns it off, because the suite's owner is an example name.
- **Secrets are not values in this file;** their place is: `NAME_FILE=/path` for any secret the head reads through
  `_secret_value` (every one does; `tests/test_secrets_take_a_file.py` fails the gate on one that does not).
- Tests: `tests/test_settings_file.py` (format, each error naming its line, the environment winning, a typo listed,
  and a head whose ONLY setting is in the file reading it at import; the same head with a broken line not starting).

### Self-eng correctness guards (D0)

Two deterministic DONE/START gates make unattended runs trustworthy. Both **fail open** (never halt the
factory) and have kill switches:

- **Run-start staleness guard** (`OVERNIGHT_WORKSPACE_STALE_CHECK`, default on) — at each item start the
  orchestrator hashes its own `/app` (= live), asks the coding-runner for the workspace's source hashes +
  `ahead` flag (`POST /files/source_hashes`), and refuses the item if the workspace is BEHIND live with no
  undeployed commits (a deploy landed without a `sync-workspace.sh`). The complement to the deploy-time sync:
  sync PUSHES live→workspace at deploy; this guard CATCHES a missed sync at run start. Scope = top-level *.py.
- **Acceptance-delta guard** (`OVERNIGHT_ACCEPTANCE_DELTA_CHECK`, default on) — a verifier-green item whose
  acceptance NAMES new tests but whose diff adds none is refused DONE and flagged for a human (routed through
  the same bail-adjudication channel). Closes the `rc=0`-via-inert-scaffolding hole.

**Mothership's small-brain failover** is `PC1_BRAIN_MODEL=qwen3:14b` (named `MOTHERSHIP_BRAIN_MODEL` until 2026-11-16) — the config default
must name a model that is actually pulled on Mothership (the 8B-class models live on the small
cards, not there).

**Pulling a large model to Mothership:** `keep-awake.ps1` holds the box awake on model-resident / `/self-eng/busy`
/ SSH-session (see the power-management section above), but a long `ollama pull` may not keep a model "resident",
so it does **not** reliably cover downloads — Mothership can idle-sleep mid-pull. Temporarily pin sleep first
(`powercfg /change standby-timeout-ac 0`), pull, then **RESTORE** with `powercfg /change standby-timeout-ac 30`.

**Model inventory is live state — read it, never recall it:** `curl http://<host>:11434/api/tags`
per box. Design decisions that stand regardless of the current pull set: a grounded bake-off
chose **`qwen3.6:27b`** for the nursing-tutor feature (it beat MedGemma on synthesis + critical
appraisal, and MedGemma lacks Ollama tool-calling), so MedGemma is NOT the primary responder;
`medgemma:27b` is used in exactly one place — the optional nursing **medical-critique** pass (a
MedGemma "second opinion" inside Think-Really-Hard — see the nursing tutor section below), which
runs with `tools=[]`, so MedGemma's lack of tool-calling doesn't matter there.
