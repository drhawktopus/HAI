# RUNBOOK — GPU Police Operation & Stand-Down



### GPU Police — "UNREGISTERED AI JOB ON THE CARD" alerts, and how to stand it down

Every factory's order taker runs `gpu_patrol()` on its own beat (same component as the file
patrol, second domain). It answers ONE question: **is there an AI workload on this factory's card
that nothing placed?** An unregistered job is invisible to the stall daemon and can starve a
legitimate trainer while the daemon reads the resulting occupancy-starved probes as a dying card.

**If you get the alert.** It names the pid and command line. Two tiers, and the alert says which:

| tier | what it means | what happened |
|---|---|---|
| `(report only)` | AI-adjacent, unaccounted for | **nothing was killed** |
| arrested | matched a compute entrypoint (`GPU_ARREST_SIGNS`: `run.py`, `main.py`, `llama-server`, …) | process tree killed |

**Operator controls** — both live in that factory's `STATE_DIR`, and both are documented precisely
so the patrol never polices them:

```
type nul > F:\foundry\var\state\GPU_SHORE_LEAVE    :: stand down entirely - the card is yours
type nul > F:\foundry\var\state\GPU_NO_ARREST      :: alarm only, never kill
```

(Softserve's root is `E:\foundry`, not `F:`.) You get ONE alert per offender set, not one per poll.

> 🛑 **THOSE TWO FLAGS ARE owner'S, NOT A SESSION'S. A Claude session may not create either one
> without his explicit permission** — "the police keep firing at me" is never the justification, it
> is the signal. the owner's standing ruling (2026-08-15): *"You are not allowed to stand the police
> down on your own without my permission"*, and the patrol keeps its kill tier on purpose: *"it
> kinda helps me to zone in on weak points."* A guard firing on legitimate work means that work has
> no front door. **Fix the matcher, or build the door. Never mute the alarm.**

**What it will NOT do:** act on an inconclusive read ("could not tell" is never rendered as
"clear" or "trespass"); arrest anything not positively identified as AI compute; or touch the
factory's own components, ComfyUI at this factory's configured path, the orchestrator's ollama
service, or HAI's watchdogs.

**Ordinary desktop use is invisible.** BigHonker is somebody's desktop — Lightroom, Photoshop,
games and Resolve match nothing and never raise an alarm, however much GPU they use. Pinned in
`test_somebody_using_the_desktop_never_trips_the_police`.

#### ⚠ Maintainer rules — the matcher's design, and how it must be tested

**Detection design (each rule is load-bearing; the incident record behind them is archived in
`Archive\runbook-cuts-20260821\`):**

* **One derivation for both tiers.** Candidacy and arrest both match via `_sig_hit(ident, signs)`
  on the process's IDENTITY (executable + first script entrypoint). Two implementations of one idea
  drift into disagreement.
* **Token boundaries on script signatures.** A signature ending in `.py`/`.bat`/`.cmd`/`.ps1` must
  **equal the basename of the FIRST script token** — never substring-match (`"run.py" in
  "sudo_run.py"` is `True`). Binary/directory signatures (`ollama`, `ai-toolkit`, `llama-server`)
  legitimately stay substring-matched.
* **Match what a process IS, never its arguments** — a filename in an output path is not a
  workload. **Inline code is text, not an entrypoint**: `_identity_of` stops collecting script tokens at
  `-command` / `-c` / `-encodedcommand` / `-ec` (`INLINE_CODE_SWITCHES`). 2026-09-28 Mothership: AMD Software's
  hidden "is ComfyUI installed?" PowerShell probe (`-Command "& { $ComfyUIDir = … 'main.py' … }"`, no executable
  token on its command line) was ARRESTED as `main.py`; there is no ComfyUI under `AMD\ai_bundle` to disable —
  the probe is AMD's, harmless, and now not even a candidate.
* **ollama's model-STORE commands are not jobs on the card** (2026-09-29, `b6e5ae3`): `ollama create / pull /
  push / rm / cp / list / show / ps` only move files and metadata — the server does any GPU work, and what it
  loads is the fleet service's. They are not candidates (`OLLAMA_STORE_SUBCOMMANDS`). **`ollama run` still is**:
  it puts a model on the card. The 12:33 report of a hand `ollama create` (importing Flash-Next) was this.
* **The factory's own deploy gate is factory-owned** (2026-09-29): `run_foundry_suite.cmd` runs
  `<FOUNDRY_ROOT>\tests\test_foundry.py` from the training venv, and `ai-toolkit` in that path made the police's
  own documented gate an "unregistered AI job" (the 09:14 report). Exempted path-qualified to THIS factory's tests
  dir in `FACTORY_OWNED_SIGNS`; a same-named script anywhere else is still reported.
* **`_norm_cmd()` collapses whitespace runs and strips quotes** — real service invocations carry
  irregular spacing, and a signature missing on one collapsed space demotes an exempt service to
  UNREGISTERED and exposes its children to arrest.
* The kill log prints `norm= ident= matched= chargeable=` **untruncated** (since 2026-09-28; `norm` used to be cut
  at 200 characters, which dropped exactly the `main.py` token behind the false arrest above), so an incident is
  diagnosable from the log alone.

**Testing rules:** feed the REAL invocation strings, copied **byte-verbatim** from the launcher
`.bat` or the patrol's own log — retyping tidies away exactly the seam under test — and verify with
a **live decoy**, never the unit suite alone: the suite has been green while the guard was blind
and while it was over-reaching.

⚠ **When the police arrest something of yours, the first question is "what is my identity string,
really?", not "is the guard broken?"** And treat any instrument the police may have killed as
returning corrupt evidence — a killed `find` returns empty, which reads exactly like "no such
file". A `taskkill /F /T` on a dispatcher also SIGHUPs whatever remote process it was driving
(a kill mid-`apt` leaves dpkg interrupted on the target box).

**Known and deliberately NOT fixed:** `_gpu_processes()` enumerates every process, and the
card-scoping downgrade is skipped when the card reports nothing resident — so an idle card can
still arrest. the owner's ruling: the over-reach is a deliberate discovery mechanism ("it kinda helps
me to zone in on weak points"); fix matchers and build front doors, never the alarm. Lab bring-up
on un-wired hardware having no front door is **PROJECT #33** in PROJECT-BACKLOG.

**Verify the matcher on a box** (read-only, plain python so the probe is not itself an AI job;
prints `__file__` because `cd F:\…` does not change drive and a stray `.py` in the ssh landing
directory shadows the deployed module and answers with the wrong file):

```
ssh mothership "python.exe -c \"import sys;sys.path.insert(0,r'F:\foundry\bin');import p20_foundry as F;print(F.__file__);print(F._sig_hit(F._identity_of(r'python.exe sudo_run.py pinky openmpi_install.sh'),F.GPU_ARREST_SIGNS))\""
```

`None` is correct. Swap in `venv\python.exe -u run.py config\x.yaml` and it must return `run.py`.

⚠ **`pinky` inside that string is CORRECT and must not be "fixed" to `rigby`.** The box was renamed
`pinky` → `rigby` on 2026-08-16, but this is a **byte-verbatim copy of a command line that was
really arrested**, kept because the doctrine's own rule for these fixtures is *"paste it from the
patrol's own log and pin the ugly bytes — a retyped invocation is an idealised one."* Retyping it
under the new hostname would pin a string nothing ever ran. A library-wide rename pass on
2026-08-16 skipped this line deliberately for exactly that reason.

**Verify it can still see a trainer** (safe, read-only, no decoy needed):

```
ssh <box> "cd /d F:\foundry\bin && python -c "import p20_foundry as F; print(F._parse_proc_line('1\x1fSEP\x1f...'))""
```

Easier: run the suite on the box — it pins both real launcher shapes verbatim.

```
ssh mothership "cd /d F:\foundry\bin && python test_foundry.py"
ssh softserve  "cmd /c \"set FOUNDRY_ROOT=E:\foundry&& cd /d E:\foundry\bin && python test_foundry.py\""
```

⚠ **Softserve needs `FOUNDRY_ROOT=E:\foundry` or the suite cannot even find the module** — the
loader defaults to `F:\foundry`.

#### Declared limitation, named rather than silently missed

A model loaded into the **already-running** ollama service by a hand-made API call is not
distinguishable at process level from one the scheduler placed — both are the same server process.
Catching that needs `/api/ps` cross-checked against what the scheduler believes it placed. The
patrol does not do it. **GPU-residency confirmation** (only report a process that actually holds a
GPU context) is the next tightening and is NOT deployed: Mothership has neither `nvidia-smi` nor
`rocm-smi` (AMD — that card is monitored via LibreHardwareMonitor), and Softserve returns an empty
list, so gating on it could silently blind the patrol on two of three factories.
