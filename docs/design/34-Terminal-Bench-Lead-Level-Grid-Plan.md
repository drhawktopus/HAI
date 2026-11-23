# #34 Terminal-Bench 2.0 — the LEAD-LEVEL grid (what level must the lead be, alone or with helpers) — PLAN 2026-10-10

Why: the Phase 7 placement rule is concluded ([[34-CHARTER]] Q6, 10-10); what stays open is what LEVEL the lead must
be for agentic work, alone or with helpers below it, priced on time · quality · energy (the owner 10-10: *"part of the
project was trying to figure out at what level the lead has to be… still choose the most energy efficient and most
best fitting… Make sure you're not missing the forest for the trees"*). Terminal-Bench 2.0 is the A1 task set
approved 10-04 for multi-file, explore-and-run work ([[34-A1-Agentic-Build-Family-Spec]] decision 2) — the work where
delegation can matter ([[Subagent-Delegation-Published-Guidance]] rule 7). The earlier team arms measured the wrong
shape and decide nothing (A1 spec § "THE SUB-AGENT TESTS TO DATE MEASURED THE WRONG SHAPE").

## The harness — adopt, do not build (doctrine: "Aider's own benchmark.py… adopt the engine")
Read 2026-10-10 (sources below): Terminal-Bench 2.0's official harness is **Harbor** (`uv tool install harbor`;
Docker is the default sandbox). Smoke test with the oracle agent: `harbor run -d terminal-bench/terminal-bench-2 -a
oracle -l 5`. An agent is `-a <name>`, a model `-m <provider/model>` (LiteLLM-style names), agent settings
`--ak key=value`, agent env `--ae KEY=value`, results under `./jobs` (`harbor view ./jobs`). **Terminus-2** is the
benchmark's own generic agent (the solo arms); errands need a **custom agent** (Harbor: subclass its agent base;
`agents/custom-agents.md`).

## Installed + smoke-tested on Sundae (2026-10-10 19:3x)
- Harbor 0.24.0 in `~/harbor-venv` (needed Ubuntu's `python3.14-venv`). **Always run it as `~/tbench/harbor`** — the
  wrapper sets `HARBOR_TELEMETRY=off`: Harbor reports every command and job to PostHog BY DEFAULT (runtime, flags,
  models, task counts, tokens, rewards; not prompts or paths); `off` is in its own `_DISABLED_VALUES`
  (`harbor/telemetry.py`, read 10-10). The first smoke run predates the wrapper and reported one oracle job.
- **Oracle positive control: 3/3, 0 exceptions, 2 min 16 s** (`harbor run -d terminal-bench/terminal-bench-2 -a
  oracle -l 3`, results `~/tbench/jobs/2026-10-11__00-34-04`). **Disk: ~1 GB per task** (23 → 20 GB free on `/`), so
  the 89-task set needs per-task image cleanup or more disk (the VG has 43 GB unallocated; Docker on Sundae also holds
  ~43 GB reclaimable build cache + unused images — not pruned without the owner's nod).
- Each task carries its own agent timeout sized for fast cloud models — a binding limit for local leads (13a): runs
  stretch it (`--agent-timeout-multiplier`) and count every trial that hits it.

## pretend is OK"*)
Out of the box Harbor does NOT fit: its agent calls a box's ollama directly (a factory blind spot the police would
have to wave through) and every task carries a wall-clock timeout (an arbitrary limit). So Harbor keeps only what it
is good at — building each task's container on Sundae and grading it with the hidden tests — and the model side goes
through the lab:
- **A MODEL WINDOW order on the lead's box** (the helper window, `bin\helper_window.py`, taught to answer
  OpenAI-format chat requests over HTTP — the lab has no such endpoint today, checked 10-10). While it runs, every
  request Harbor's agent makes is that order's own work: the factory claims the card, the deck shows the order, the
  police see a registered compute order. No exemption, nothing to pretend.
- **Lab settings, not defaults:** the window applies effort, temperature and the tested context from the placement
  table row (the placed-order machinery), and meters GPU joules per request.
- **No clocks:** Harbor's per-task timeouts are stretched out of the way; a trial ends on the lab's status rules (no
  progress / loop), counted per trial (13a).
- **The Harbor side is an order too:** a Softserve compute order drives the run on Sundae, so the run is visible on
  the deck end to end.
**Transport without opening a port on any GPU box:** a small RELAY on Sundae (local to Harbor, part of the
Softserve driver order) serves the OpenAI chat endpoint Harbor's agent calls and turns each request into a window
ITEM on the share (`messages` [+ `tools`]); the window on the lead's box answers it with the file protocol it already
uses (errand turns, 10-10). Terminus-2 (read in the installed source, `agents/terminus_2/terminus_2.py`) takes
`api_base` (`--ak api_base=http://127.0.0.1:<port>/v1`, model `openai/<name>`), `temperature`, `reasoning_effort`,
`llm_kwargs`; `max_episodes` defaults to 1,000,000, so no turn cap — the window's lab settings rule regardless.
Not chosen: writing our own runner for the tasks (our A1 harness needed five rebuilds and leaked twice; the official
grader keeps our numbers comparable).

## First end-to-end decoy (2026-10-10 19:40–20:05) — the fit WORKS; two findings
Harbor (Terminus-2, task 1 of the set: a MIPS emulator in JavaScript) on Sundae → `tb_relay.py` → a model-window
order on BigHonker (qwen3:8b, ctx 17,408, think off, t0.7) → answers back. The deck showed the window order live
(`21 / 2000`, 29 tok/s); every model call was the order's own work. 65 requests in 23 min. Findings:
1. **The 8 GB lead hit its OWN context ceiling on a long task:** the conversation grew to 15,886 tokens by step 22
   and then sat at ~16,300 of 17,408 on every turn, with `loop` answers appearing — the model at its tested maximum
   (13a: a model's own ceiling is its result, not a limit we imposed). A lead-level fact the grid exists to price.
2. **Ending a stuck trial by CLOSING the window = an Harbor EXCEPTION, not a graded fail** (`ServiceUnavailableError`,
   0 trials graded). So the relay ends a no-progress trial the way the agent ends itself: on the A1 loop cutter's
   rule (3 loop-cut answers in a row), it answers Terminus-2's own `task_complete` — the trial ends normally, Harbor
   GRADES it, and the relay log records "ended by the lab's no-progress rule". No clock.

## First arm (qwen3:8b on BigHonker, thinking on, 2026-10-10 22:17–23:4x) — two more holes, both fixed
1. **The relay had only two of the A1 cutter's three no-progress rules.** Trial 1 (`cobol-modernization`) opened
   `nano` on turn 2 and never left it: 96 of 115 turns typed shell commands INTO the editor (the same keystrokes up to
   22 turns in a row), and 72 turns sat at ≥95% of its 17,408 context. Every keystroke changes the screen, so the stall
   rule (8 turns with no new terminal output) was blind. Fixed: `tb_relay.REPEAT_END = 3` — A1's
   `NO_PROGRESS_REPEAT` (same action 3 turns in a row), counted on the agent's non-empty keystrokes; turns that only
   wait stay with the stall rule. Replayed on every window so far: it would have ended this trial on turn 5, and fires
   nowhere else except the 10-10 decoy's identical triple append. The model WAS trying this time (commands on 101 of
   111 turns). The trial was stopped by slip, so Harbor recorded it `CancelledError`, NOT a grade (its progress bar's
   "Mean 0.000" counts the cancelled trial). Resubmitted unchanged but for the fixes: `tb-bi-qwen3-8b-th.20261010-2336`.
2. **A stop slip skipped the driver order's cleanup and left the relay orphaned on Sundae.** The slip ends
   `tb_order.py` outright (its `finally` never runs). The chain built 10-10 covered the rest — the window saw its lead
   order `failed` at the desk, wrote CLOSE, `tb_run.sh` TERMed Harbor, Harbor removed its container and graded — but
   the relay survived: the closed ssh made `tb_run.sh`'s first `echo` a SIGPIPE, and dash dies on an untrapped SIGPIPE
   without running its EXIT trap (proven on Sundae's `/bin/sh` both ways). Fixed: the trap covers HUP + PIPE and the
   relay is killed before the first write.

## RESULT — qwen3:8b on BigHonker, thinking on, spot set: 0 / 10 (`tb-bi-qwen3-8b-th.20261010-2336`, 2026-10-11)
Graded 10, exceptions 0, calls at ≥95% ctx **0**, 96 model calls, 33.7 min model time, 269 kJ GPU; 3 trials ended
by the repeat rule (cobol, prove-plus-comm — the identical `sed` + `coqc` and the identical error three turns running —
and raman, confirming the model's own claim), the rest by the model's own `task_complete`. **The instrument is not the
cause** (the owner, 10-11: *"is it not getting feedback… something feels off"* — checked): every turn carries the terminal
output (e.g. `bash: 7z: command not found`) and the model's thinking reads it correctly. **What the 8B does:** it claims
done early and confirms it — Terminus-2's own format example shows `"task_complete": true`, 3 of 10 trials claimed done
on the FIRST answer before any output, and 7 of 9 "Are you sure you want to mark the task as complete?" checks were
answered done again (crack-7z: while planning to install 7z). **Matches the published level:** papers running plain
Qwen3-8B under Terminus-2 report ≈2.5% on Terminal-Bench 2.0 (arXiv 2602.21193; Terminal-Lego, arXiv 2606.03461) — 0/10
is that level. The 8 GB lead does not carry agentic terminal work alone; next, the 3.8 as the positive control through
our own relay (the oracle never touches it).

## RESULT — qwen3.8:27b on Mothership, TRUE xhigh, spot set: 8 / 10 (`tb-solo-q38-xhighfix.20261017-1515`, 2026-10-17, register A4)
Graded 10, exceptions 0, calls at the 122,880 ctx **0**, no-progress ends **0**, 199 model calls (all `stop`), 96.2 min
model time, **1,923.5 kJ** GPU (all 199 rows metered). Passed: cobol-modernization, constraints-scheduling,
crack-7z-hash, fix-git, kv-store-grpc, mteb-leaderboard, overfull-hbox, prove-plus-comm. Failed:
configure-git-webserver, raman-fitting. Effort proved by effect (+42 prompt tokens, register § EFFORT SANDBAG #2).
**The 10-11 arm (`tb-mo-qwen3.8-27b-th.20261011-0039`, 7/10) ran MEDIUM, not xhigh** — the foundry mapped xhigh to
ollama's `"high"`, which 0.40.2 renders as medium; it is void and moved to `Archive\34-terminal-bench-history-2026-10.md`.
It still served as the positive control for the relay path: the 8B's 0/10 is the 8B.

| lead (same 10 tasks, same relay) | solved | model time | GPU energy | per solve |
|---|---|---|---|---|
| qwen3.8:27b TRUE xhigh, Mothership (A4, 10-17) | 8 / 10 | 96.2 min | 1,923.5 kJ | 12.0 min · 240 kJ |
| qwen3:8b think on, BigHonker | 0 / 10 | 33.7 min | 269.3 kJ | — (no solve) |
| nemotron-3-nano:4b think on, BigHonker (`…-0204`) | 1 / 10 | 15.6 min | 106.5 kJ | 15.6 min · 107 kJ |
| Qwen3.5 9B think on, BigHonker, ctx 14,336, summarize-when-full (`…-0705`) | 5 / 10 | 155.3 min | 1,129.9 kJ | 31 min · 226 kJ |

**SOLO VERDICT — re-read 2026-10-23 on the true-xhigh run (Q6, owed since EFFORT SANDBAG #2):** agentic terminal
work needs a 24 GB-class lead. The 3.8 at xhigh solves 8/10 against the best 8 GB lead's 5/10 (Qwen3.5 9B) and takes
12.0 min per solve against 31. **Energy per solve is now about equal** (240 kJ vs 226 kJ): the 10-11 claim that the 3.8
also won on energy came from the MEDIUM run and does not hold — quality and time decide it. The older and smaller 8 GB
models do not carry the work (Qwen3 8B 0/10, nemotron 1/10). The errand arm of 10-17 is a second solo replicate (same 8
tasks, 78.3 min, 1.58 MJ), so one-run time on Mothership spreads 78–96 min. Since 2026-10-18 the AGENTIC lead is
Flash-Next (`policy.json` crown, register Phase 2 reopened); this table answers the lead LEVEL, which that does not
change. The 9B's solves: cobol, configure-git-webserver (which the 3.8 failed), fix-git, kv-store-grpc,
prove-plus-comm. n = 1 trial per task per lead. Its arm ran with Terminus's proactive summarization OFF (the owner 10-11:
summarize only when full) and the relay's context-full answer (`context_length_exceeded` → Terminus's own fallback
summarization; decoy proven on Sundae through Harbor's LiteLLM wrapper). The errand arms below decided
`lead_defaults.delegation`.

nemotron solved configure-git-webserver (which the 3.8 failed) and nothing else; 172 calls, 0 at ctx, 0 exceptions;
**112 of 172 answers arrived under `thinking`** and reached Terminus only through the relay fix below. Its run kept
the relay without the parse-error stall rule (crack-7z: 109 turns, 94 rejected), so its score was not cut by it.

n = 1 trial per task per lead; the gap (7 vs 0, no task the 8B solved) is not run-to-run noise, but per-task
differences between future arms near 7/10 will need repeats. **Energy note:** an AMD window meters at close, so the
order's summary read `gpu_j None`; `tb_order.price` now takes the window's delivered rows by id (`test_tb_order`
14) and names the file (`gpu_j_from`) when it is not delivered yet.

**Answer filed under thinking — caught on nemotron-3-nano:4b's first trial (10-11 02:0x), fixed, arm re-run.** With
thinking on, nemotron often never closes its think block, so ollama files the WHOLE answer (valid Terminus JSON) under
`thinking` and leaves `content` empty — the doctrine gotcha "empty content + non-zero eval_count = wrong field". The
relay sent only `answer`, Terminus saw nothing ("No valid JSON found"), and the stall rule ended cobol after 12 calls:
**9 of 12 answers dropped**. Audit of every window (scratchpad `tb_empty_answers.py`): nemotron 9, the qwen3:8b arm
1 (a genuinely empty answer, no JSON in its thinking), the 3.8 arm 0 — **both earlier results stand.** Fix: the relay
takes `answer`, else `thinking` (the lab's extraction rule), logs `answer_from_thinking`, and the order's summary
counts `answers_from_thinking` (`test_tb_relay` / `test_tb_order`, 15). Stopped arm `…-0200`; re-run `…-0204`.
The stop also proved the 10-10 cleanup fix live: window CLOSE → Harbor TERM → relay gone, Sundae 0 processes and 0
task containers with nothing done by hand.

**Rejected turns count as stall turns (10-11 02:2x).** nemotron's crack-7z: 94 of 109 turns were REJECTED by
Terminus (prose deliberating about the JSON, the turn ending before any JSON) — nothing ran, but each error screen
differed, so the seen-rule never fired and there were no keystrokes to repeat. A1 already counts a no-action turn as
a stall turn; the relay now does the same, keyed on Terminus-2's own parse-ERROR prefix `Previous response had parsing
errors:` (installed `terminus_2.py`; warnings-only answers still run and are not counted). Replayed on every window:
it ends 3 trials early, all fails, and cuts none of the 8 solves to date (A1's 8 cut 0 of 118). `test_tb_relay` 16.

**The 9B was missing from the 8 GB tier** (the owner 10-11: *"we skipped the 9B with good settings that gives us more
context on BH… We can't only go by size of parameters"*). qwen3.5:9b's tested context on BigHonker is 7,168 — too
small for this work (the 3.8 used ~19k) — but ollama 0.40.2 (10-10) stores it at 5.50 GB instead of 6.31, so its
tested max may rise (register 10-10 09:5x). Fit probe `fitprobe-bighonker-ollama0402.20261011-020800` (qwen3.5:9b,
qwen3:8b, qwen3-vl:8b; `bin\fit_probe.py`, the lab's own tool) runs first; the 9B's arm runs at what it measures.
**Measured 10-11 02:3x** (desktop holding 461 MB): **qwen3.5:9b 14,336** (was 7,168; spills at 15,360) · qwen3:8b
17,408 (unchanged) · **qwen3-vl:8b 4,096 — DOWN** (spills at its table value 5,120; orders there are refused by the
residency assert, not silently offloaded; changing the vision model's context is left to the owner). `menu_fill.TESTED_CTX`
BigHonker 9B → 14,336. Arm `tb-bi-qwen3.5-9b-th.20261011-0241` (think on — the resolver says split; t0.3).

**Terminus-2 never knew the lead's context limit — its own context management was OFF (10-11 03:0x; sandbag
class "a harness can sandbag as easily as it can leak").** Terminus-2 summarizes its history when free tokens drop
below `proactive_summarization_threshold` (8000) of the limit LiteLLM reports; `openai/lab-window` is not in
LiteLLM's table, so Harbor's wrapper fell back to **1,000,000** (probe on Sundae: 1,000,000 without `model_info`,
14,336 with it) and summarization could never fire. Terminus's own docstring says custom models must pass
`model_info`. Found when the 9B (14,336) solved cobol while running INTO its wall: 6 answers cut by length, 7 calls at
≥95% ctx, then rejected turns and a stall end. **Earlier arms unaffected:** qwen3:8b and nemotron had 0 calls at ctx,
the 3.8's largest prompt was ~19k of 122,880. Fix: `tb_run.sh` takes the lead's tested ctx and passes `--ak
model_info={"max_input_tokens": ctx, "max_output_tokens": ctx, …}` (dash expansion parsed by Harbor's own
`parse_kwargs`, proven on Sundae); `tb_order.run_cmd` (`test_tb_order`, 17). The 9B arm `…-0241` was stopped after its
first trial (cobol 1/1) and re-run whole.

**Effort level — the window fix landed 10-11, but every Mothership "xhigh" order from 10-10 to 10-16 still ran
MEDIUM.** The window was launched with a bare `--think`, which qwen3.8 renders as MEDIUM; `a1_delegate._window_body`
now sends `--think-level` when the team names one (`helper_think_level`; `test_a1_delegate.WindowEffortLevel`),
`submit_tb --think=xhigh|medium|low`. A second mapping then still sent ollama `"high"` for xhigh, which ollama 0.40.2
(installed 10-10) renders as medium — found 10-16 and fixed the same day (register § EFFORT SANDBAG #2,
`infer_ollama.OLLAMA_THINK`). The first TRUE-xhigh Terminal-Bench runs are A4, 2026-10-17.

## ERRAND ARM RESULT — the lead works alone (3 runs, 0 errands; re-read at TRUE xhigh 10-17 and 10-19)
| arm | effort | solved | errands | model time · energy |
|---|---|---|---|---|
| errands, look-only (`tb-errand-q38-xhighfix.20261017-1515`, A4) | TRUE xhigh | 8/10, the same 8 tasks as solo | **0** (helper 0 calls) | 78.3 min · 1.58 MJ |
| errands + fetch + simple commands (desk `b9199b7f057b`, B8, 10-19) | TRUE xhigh | 7/10 (mteb-leaderboard ran away at the context ceiling, scored) | **0** (helper 0 calls) | — |
| errands, look-only (`…-th-err-bi-qwen3.5-9b.20261011-1921`) | MEDIUM (void, archived) | 7/10 | 0 | — |

The note reached the lead (decoy 3's first prompt carries it; B8's decoy trajectory shows the full commands offer);
answer key locked (`/tests` absent while the agent works). **`lead_defaults.delegation` = "the lead works alone"**
(decided 2026-10-11, confirmed 10-17 and 10-19, in `policy.json`): a lead strong enough for this work does its own
looking; the 8 GB leads are not strong enough to lead it (0–5/10). Consistent with the published guidance (most coding
tasks fit multi-agent poorly).

## ERRAND ARMS — design (2026-10-11; the owner: Track A closes WITH these, then the main session joins #36)
Decides `lead_defaults.delegation` on the right shape ([[Subagent-Delegation-Published-Guidance]]: bounded jobs,
parallel, tools, conclusion back; one grep = do it yourself). Read in the installed Harbor 0.24.0 source: Terminus-2
has **no tool calls** (the model answers JSON with keystrokes), Harbor loads a custom agent by `--agent-import-path
module:Class`, and `environment.exec(command)` runs a command in the SAME task container apart from the agent's tmux
session. Build:
1. **`LabTerminus(Terminus2)`** — Terminus-2 unchanged except ONE optional response field, `"errands": [{"brief": …}]`,
   added to its prompt template (one paragraph: bounded look-up jobs; a single command → run it yourself). Every
   other byte of Terminus's prompt and loop stays the published one.
2. **Errand runner** — each brief goes to the HELPER's model window through a second relay (its own port and items
   dir, a window order on the helper's box: visible, metered, no factory blind spot). The helper runs a bounded
   look-only loop: commands through `environment.exec`, a whitelist of read commands (the A1 `ERRAND_READONLY` family:
   ls, cat, head, tail, grep, find, wc, stat, file), the A1 look-first and answer-now nudges, the A1 no-progress rules.
3. **Reports** land in the lead's NEXT observation (`Errand reports: …`), errands run in parallel with the lead's own
   turn (A1's async shape).
4. **Answer key:** Harbor's verifier copies the tests in only at grading (check 5 below — confirm on one task that
   `/tests` is absent while the agent runs, before the first errand arm).
5. **First arm:** the 3.8 lead (Mothership, xhigh) + Qwen3.5 9B helpers (BigHonker, 14,336) on the spot set, against
   the 3.8 solo's 7/10 (time · quality · energy, both windows metered). One variable: errands on/off.
6. **Second arm — FETCH + simple COMMANDS (#34 checklist B8, built 2026-10-17):** the same errand arm with ONE variable
   changed, `submit_tb.py --errand-mode=commands` (plan `errand_mode`, env `ERRAND_MODE`, LabTerminus `--ak
   errand_mode`). The helper may also `{"run": [...]}` — one simple command (allowed programs, pipes, redirection; no
   `; && ||`, no absolute path / `~` / `..`, no installer) in its PRIVATE COPY of the container's working directory
   (`/tmp/lab-errand-<n>`, made by `cp -a` on its first run; a container whose working directory is `/` gets no runs)
   — and `{"fetch": "<url>"}` one page (curl or wget in the container; benchmark sources such as `terminal-bench`,
   `tbench`, `laude-institute` refused). The key stays locked: Harbor copies the tests in only at grading, and
   `fetch_audit.py` (run on Sundae by `tb_run.sh`, beside the pinned dataset) marks a trial whose fetched text holds a
   distinctive line of its task's `tests/` or `solution/` as a LEAK; `tb_order.price` VOIDs leaked and unauditable
   trials (never solved, never graded). Each errand record carries `runs` and `fetches` (url + the text the helper
   saw). Runs after the first arm's verdict.

## Checks BEFORE the first scored run (each is a trap the lab has paid for)
1. **The model route:** how Terminus-2 reaches a LOCAL model (LiteLLM `ollama_chat/<model>` + the endpoint) — read from
   its installed source, not assumed; prove one round trip.
2. **Effort reaches the model** ("A SETTING'S NAME IS NOT ITS VALUE", 10-07): the 3.8 at xhigh — ollama renders
   `think: "high"` as xhigh, `true` as MEDIUM. Prove from the rendered prompt / a token diff.
3. **Context is the tested max, not a default** (13a): `num_ctx` per box × model from `config.MODEL_NUM_CTX`; count
   every turn that hits it before believing any score.
4. **Prompts against the source** ("diff your prompts against ITS SOURCE CODE"): we run Terminus-2 unmodified for
   the solo arms; any wrapper changes nothing it sends.
5. **The answer key locked:** task tests stay out of the agent's sandbox (Harbor's verifier design); confirm on one
   task that the agent cannot read them.
6. **Oracle positive control** passes on the chosen host before any model runs.
7. **It runs as ORDERS** (doctrine 14): one order per arm on the factory that owns the Docker host; the model calls go
   to the lead's box through its foundry (12e visibility). Harbor would otherwise call a box's ollama DIRECTLY — a
   blind spot the police flag; the fit is the helper-window pattern: an order on the lead's box holds the lane and
   keeps the model resident while Harbor sends it requests, and that order meters the GPU joules (Harbor has no
   energy measure).

## The task set — 89 tasks (4 easy · 55 medium · 30 hard; read 10-10 from every `task.toml`, `~/tbench/tasks.json`)
None needs a GPU; all but two (`mcmc-sampling-stan`, `rstan-to-pystan`: 4 CPU, 8 GB) fit 1–2 CPU and 2–4 GB; all
allow internet. **The SPOT SET, by rule (not by hand):** all 4 easy + the 5 shortest-expert-time medium tasks from 5
different categories + the shortest hard — `fix-git`, `prove-plus-comm`, `cobol-modernization`, `overfull-hbox`,
`crack-7z-hash`, `mteb-leaderboard`, `raman-fitting`, `constraints-scheduling`, `kv-store-grpc`,
`configure-git-webserver`. Every difficulty is present, so the outcome can move at each lead level; the full 89 only
for the levels that earn it. (The 10-10 decoy ran `make-mips-interpreter` — HARD, ~480 expert-minutes; its fail says
little about the 8 GB lead beyond the context finding.)

## The grid (evolutionary: promising first, controls last, stop rules on clear losers)
1. **Solo, each lead level:** the 3.8 on Mothership (xhigh, 122,880) → the 8 GB lead (the table's `agentic` / coding
   lead on BigHonker or Softserve, tested ctx) → the rigby lead **after Track B settles rigby's speed** (#36: "settle
   rigby's new speed BEFORE pricing rigby work").
2. **With errands, where they can move the answer:** a smaller lead just short alone; a big lead whose time or energy
   errands could cut. Helpers from the tiers below that lead; the lab's errand rules (bounded jobs, one grep = do it
   yourself, look first, answer key locked).
3. **Priced per solve, three lenses** → `policy.json` `roles.leads`, so Spock chooses the lead level like everything
   else.

## The Docker host — SOFTSERVE, inside Sundae (the owner 2026-10-10)
the owner: *"soft serve … is also like the main hub of the entire lab so things that need to talk to multiple parts of
the lab should live on there too. Rigby is a weird special exemption with Tolkien and the fellowship due to Tolkien
being the controller for it all."* A Terminal-Bench run talks to several parts of the lab (task containers + the
lead's box + helper boxes), so Harbor and its task containers live on Softserve, in the Sundae VM (the one with
Docker). Plex needs no extra guard: Sundae is held to 6 vCPUs at relative weight 80 and Plex runs natively on the
host ([[Machine-SundaeStackarr]] § "CPU containment"). Lab-critical things live on Softserve too (the owner 10-10,
memory `feedback-lab-critical-lives-on-softserve`). Docker exists only on Sundae and rigby (checked 10-10); rigby is
Tolkien's Fellowship exemption, not a batch home. (An earlier same-day reading that put the containers on rigby is
archived: `Archive\tbench-plan-superseded-rigby-host-20261010.md`.)
