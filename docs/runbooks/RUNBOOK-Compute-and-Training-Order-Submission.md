# RUNBOOK — Submitting Compute & Training Orders to the Fleet



### 🖥 CPU THROUGHPUT OF THE FLEET — Mothership and BigHonker's recorded CPU BENCHMARK numbers (img/s)

**If you are looking for "how fast is Mothership's / BigHonker's CPU", these are the numbers, and
they are in img/s — not GFLOP/s.** Measured 2026-08-06 as a fair fight against the Fellowship:
tesseract 5.5.0 OCR watermark detection, same images, same `tessdata_best`, control verified every
run, concurrency swept. Full table, method and caveats: **[[Fellowship-Crunch-Integration]]
§10b**.

| host | best | at |
|---|---|---|
| **Mothership** (16 cores) | **1.50 img/s** | 12-way (0.67 s/img) |
| **BigHonker** (i5-9600K, 6c/6t) | **0.458 img/s** | 6-way; 8-way gains nothing, no HT |
| **Fellowship** (five-node measurement; the Fellowship is now seven) | **~0.36–0.40 img/s** | per-node rates summed; six-node est. ~0.45–0.50 UNMEASURED; no seven-node figure exists |
| **mordecai** (i7-3770, 4c/8t) | **0.145 img/s** | 8-way |
| **rigby** (i5-3470, 4c/4t) | **0.115 img/s** | 8-way |
| **mordecai + rigby AS ONE UNIT** | **0.282 img/s** | 95% of the sum; 24 units dealt 13/11 by rate |

⚠ **There is NO HPL/GFLOP-per-second figure for any GPU host** — do not convert between the two
scales, and do not quote a spec-sheet number in place of either. ⚠ Mothership's 1.50 repeated one
control image (caching benefit); the other arms ran the real sample.

#### ⚠ A SHALLOW BATCH UNDERSTATES EVERY HOST — these are COMPARISON numbers, not CAPACITY numbers (2026-08-16)

**Every figure above was taken on a 6-image batch, and 6 images against an 8-worker pool is ONE WAVE
WITH A LONG TAIL** — workers finish the cheap images and idle waiting on the slowest, where a real queue
backfills. Measured on identical hardware, images and day: **mordecai 0.145 → 0.167 (+15%), rigby
0.115 → 0.130 (+13%)**, purely by going from 6 units to 13/11.

**Rank hosts with the table above; promise a corpus ETA with a deep-queue rate.** The pessimism is
largely common-mode so ratios mostly survive — but **not exactly**, because the tail penalty scales with
workers-vs-batch-size, and BigHonker ran 6-way on 6 images where the basement boxes ran 8-way.

#### ⚠ `burn.py` SILENTLY UNDERSTATES ANY HYPERTHREADED (SMT) HOST BY ~40% (2026-08-16)

**If a node reads unexpectedly slow on `burn.py`, check SMT before believing it.** `burn.py` spawns one
process per **`os.cpu_count()`**. Every tablet's `cpu_count` equals its physical core count (no tablet
has SMT), so the policy was never exercised against hyperthreading. Measured **twice independently** on
mordecai (4c/**8t**): **102.7 vs 65 GFLOPS** (original finding, 2026-08-15) and **103.18 vs 57.42–64.11**
(replication, 2026-08-16) — **~38–44% low**. It does not error; it reads exactly like a slow node, and it
**inverted the true order between mordecai and rigby**. Run one process per **physical** core for FP
benchmarks, and pin the worker count in any cross-fleet `burn.py` comparison involving an SMT box.

⭐ **The SMT penalty is workload-ordered — quote only the measured cells:**
**dense matmul −44% · OCR / task-farm ~0 to +2%.**
🛑 **HPL is UNMEASURED — do not quote a figure for it.** The 8-rank arm never produced a number
(`--bind-to core` cannot bind 8 ranks to 4 cores), so *"HT hurts HPL"* is an unearned assertion —
no SMT-vs-HPL number exists. ⚠ Do not read a task-farm gain from
oversubscription as an SMT gain either — rigby (4c/4t, **no SMT at all**) gains the same ~5% going 4→8
workers, so on both boxes that is scheduling overlap.

#### ⭐ THE AXIS INVERSION — name the axis or the ranking is meaningless (2026-08-16)

The same two boxes against the Fellowship (six-node, as measured), the same night:

| axis | basement pair vs Fellowship |
|---|--:|
| dense matmul (`burn.py`, same instrument both sides) | **≈2.0×** |
| HPL | ≈2.8× |
| **OCR / task farm (img/s)** | **≈0.55×** |

**A ~3.7× swing, and every number is correct.** Six tablets field 40 cores against the pair's 8 physical:
throughput work that scales with core count goes to the Fellowship, per-core floating-point width goes to
the desktops. **Production work — OCR, captioning, embedding — is the task-farm class**, i.e. the axis
where the desktops look worst. ⇒ **Any "host X ≈ host Y" claim must state its axis.** Full detail and the
tier-verdict consequences: [[HOST-ONBOARDING-Basement-Desktops]] §C1c.

⚠ **A measurement that is not in the library did not happen, as far as the next session is
concerned** — record numbers here or in the component doc the moment they are taken.

### 🖥 GENERAL COMPUTE AS A FACTORY ORDER — `mode:"compute"` (formerly `"cpu"`; fleet-wide)

**Run any compute script AS A REAL FACTORY ORDER, so it is patrol-exempt and needs no
window-climbing.** The foundry runs it as a child of its own runner, which `gpu_owner` exempts as
"the running order's own process" (verified live). Serial in the runner, so it cannot collide with
a render order. Renamed 2026-08-08 (the owner: "cpu" was *"highly misleading"* — payloads routinely use
the GPU); `"cpu"` still accepted as a legacy alias. The **`load`** field carries the honest
classification: `"gpu" | "mixed" | "cpu"` (default `mixed`).

**Submit to the FLEET (preferred — see the fleet order taker section below):** steps with
`requires: ["order.compute", ...]` route by capability; weights-locality caps
(`weights.flux2-dev`, `weights.klein-4b`, detected from the model-cache tree per box) place a step
where its data lives. Direct single-factory submission (a `.job.json` in that box's `jobs\queue`)
still works:

```json
{"name": "my_job", "mode": "compute", "load": "gpu", "initiator": "direct",
 "payload": "{FOUNDRY_ROOT}\\workspace\\prolora\\script.py", "args": ["arg1"],
 "python_exe": "F:\\ai-toolkit\\venv\\python.exe"}
```

* **PATH TOKENS (2026-08-08): `{FOUNDRY_ROOT}` `{OUTPUT}` `{MODEL_CACHE}`** are expanded by the
  box that runs the order (payload, args AND python_exe) — orders never carry drive letters
  (Softserve's factory is E:, its peers F:). `spec_defects` expands them too, so a tokenized
  payload is validated correctly at intake.
* **SHIPPING — `{ORDER_OUT}` + `deliver_to`, the same schema as caption and render orders
  (2026-09-28).** Point the payload's output at **`{ORDER_OUT}`** — the order's own folder,
  `{OUTPUT}\compute\<order name>`, one per order because order names are unique — and declare
  **`deliver_to`** as a destination FOLDER on a UNC path (`\\<softserve-address>\SSDShared\...`; the runner
  cannot see mapped drives). When the child exits (done, failed or stopped — anything but a rc-2
  refusal, which retries under the same name) the factory copies the folder to
  **`deliver_to\<order name>\`**, never overwriting: an existing destination gets a timestamp suffix,
  and a failed delivery is DM'd with the output left in `{ORDER_OUT}` and reported to the desk as
  **failed** ("ran clean but SHIP FAILED … output kept at …"), never done. The copy uses Windows long-path
  form, so a destination past 260 characters ships on boxes with `LongPathsEnabled 0` (BigHonker; before
  2026-10-02 eight long-named joycaption orders failed there and the desk was told done). **Never hand a payload a fixed
  output path on the share** — two orders then write one file (2026-09-28: the hard-bench driver did
  exactly that, and a later submit could replace an earlier run's product). The desk's reply names each
  step (`steps[].order`), which IS the order name on the box, so the delivered path is
  `deliver_to\<step order>\<file>` — record it from the reply, never glob for it.
  ```json
  {"spec": {"name": "my-eval.20260928-1400", "mode": "compute", "load": "gpu",
            "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py",
            "args": ["\\\\<softserve-address>\\SSDShared\\...\\items.20260928-1400.json", "{ORDER_OUT}\\out.json", "--model", "qwen3.5:9b"],
            "progress_glob": "{ORDER_OUT}\\out.progress\\*.done", "progress_total": 75,
            "deliver_to": "\\\\<softserve-address>\\SSDShared\\tabfoundry-jobs\\campaigns\\my-eval"},
   "requires": ["order.compute", "model.qwen3.5:9b"]}
  ```
* `python_exe` optional; a FLEET submission fills it per host from `scheduler.HOST_COMPUTE_PY`
  (the torch venv per box — a host property; an explicit value in the spec wins).
* **The Inspect AI venv — `{FOUNDRY_ROOT}\venv-inspect` on every GPU factory (since 2026-10-15).** A1 compute orders
  name it explicitly (`"python_exe": "{FOUNDRY_ROOT}\\venv-inspect\\Scripts\\python.exe"`, set by `submit_a1.py`): their
  model calls go through Inspect's `lab` provider with Inspect's conversation compaction, the same call path rigby's
  Tolkien image runs ([[34-A1-Agentic-Build-Family-Spec]] § the gap list). Python 3.12, pinned to rigby's image:
  `inspect_ai==0.3.276`, `tiktoken==0.14.0`, with tiktoken's `o200k_base` encoding cached in `venv-inspect\tiktoken-cache`
  (compaction's token estimate on ollama). Create or repair it (idempotent; prints `VERDICT OK` only when the pins are in
  force): `ssh <box> "py -3.12 - <FOUNDRY_ROOT>" < E:\SSDShared\mothership-build\setup_inspect_venv.py`. When rigby's
  image moves to a new Inspect version, bump `PINS` there and re-run it on all three boxes in the same pass.
  Softserve's `{FOUNDRY_ROOT}\venv` is a different venv (torch 2.11 + cu128) — do not install Inspect into it.
* **A payload's own alarm: print `ALERT: <text>` (since 2026-10-08).** `p20_foundry` DMs each distinct `ALERT:` line
  ONCE per order through the desk (`[foundry:desk] ALERT <order>: <text>`); Tolkien speaks the same words through
  `notify_desk`. A payload whose progress line carries `void N` beside `solved X/Y` gets `· void N` on the tile — rows
  that measured the instrument, not the model (A1: `a1_validity`). Use it for "my result is not a measurement",
  never for routine progress (one DM per verdict).
* **`progress_glob` + `progress_total` — DECLARE THESE ON ANY COMPUTE ORDER THAT RUNS LONGER
  THAN MINUTES (effectively mandatory, upgraded 2026-08-09; "optional" 2026-08-08):** the order
  DECLARES its progress — a token-expandable file pattern to count and the expected total (e.g.
  `"progress_glob": "{OUTPUT}\\compute\\svd_dev_full\\*.npz", "progress_total": 160`). The beat
  thread then publishes step / rate / ETA, and the homepage factory tile shows real numbers.
  ⚠ An order WITHOUT it is invisible to the tile, the operator, AND the demon's `watch.compute`
  arm (v6.2) — it runs unwatched, and an overnight wedge in it costs hours (the r448 capture sat
  6.5 h on one cond before the arm existed). The daemon logs such orders as UNWATCHABLE once.
  Short quick payloads (a minutes-long fit) may legitimately skip it. Optional companion
  `progress_stale_after` (seconds, default 900): how long progress may sit static before the
  demon calls it wedged and respawns via stop slip + requeue slip (shards bank; round-capped 8).
  Static AT `progress_total` is never a wedge (final assembly looks like that).
* The factory APPENDS the order name to the child's argv (patrol identification) — a positional
  script must tolerate an extra trailing arg.
* **STOP a running compute order with the standard `<order>.stop.json` slip** — wired 2026-08-08
  (the 08-07 build forgot it; the first compute order that needed stopping had to be taskkilled
  and archived as FAILED). A stopped order notifies "STOPPED", keeps partial output, retires clean.
* Heartbeat fixed same pass (task #8 closed): child runs `-u`, heartbeat on a timer thread — a
  silent `torch.load` no longer makes CURRENT_RUN look stale.
* ⚠ **The foundry SUITE must never DM the operator** — 2026-08-08 three suite runs sent the owner real
  "ORDER HELD — capability gap" DMs for synthetic test orders (capability tests call
  `assess_capability` → `notify_desk`, and Mothership holds live relay creds). Fixed at the class
  level: `test_foundry.py` stubs `_dm_creds` at import. If phantom capability DMs ever reappear,
  check whether a NEW test file forgot this stub.
* **Chained steps release on a COMPLETION receipt (2026-08-08):** `report_fleet_result()` POSTs
  done/failed to the desk when a compute order ENDS; `after_ids` successors release on done and
  HOLD on failed/stopped (fail-closed). ⚠ Only COMPUTE orders report so far — a chain step of
  another kind (train/caption/probe) never reports and its successors hold forever; wire
  `report_fleet_result` into that kind's completion path the first time a chain needs it.
* **DEPLOYED FLEET-WIDE 2026-08-08** (17a4cb82): all three factories; verified via live fleet
  orders (`prolora_svd_full` split dev→Mothership / klein→BigHonker by weights capability;
  `prolora_transfer_chain` queued-while-busy + chained through completion receipts).

### A share that is DOWN makes a compute order WAIT, never FAIL — `user name or password is incorrect` / `OSError: [Errno 22] Invalid argument: '\\<softserve-address>\...'`

2026-10-09 07:45: ~4 min after Mothership's reboot the Softserve share refused the session's credentials, and the
runner took all 12 queued orders in two minutes, each dying reading its plan/payload off `\\<softserve-address>\SSDShared`,
each archived FAILED (the share answered fine in-session at 07:48). Since 08:01 (all three GPU factories,
`p20_foundry.unreachable_shares`): every `\\host\share` an order names (payload, args, anywhere in the spec) is
checked before the spawn, and again when a child dies; a share that is unreachable raises `LaneBusy` — the order
stays queued untouched and runs when the share answers. A failure with the share up still fails and retires.
Recovery from a blip that predates the fix: requeue slips per archived order (`{"action":"requeue","order":…}`),
highest priority first; A1 orders resume from their kept checkpoints. ⚠ The desk's menu filler re-places a failed
menu cell as `<cell>-r2` within minutes — after requeuing, read the queue back and cancel whichever copy is the
duplicate. Tolkien does not have this hole: an order whose payload cannot load is HELD by name, not archived.

**The SHIP half of the same seam (2026-10-09 13:27, Ornith's stopped partial could not ship: `ship FAILED
(OSError(22, 'The user name or password is incorrect')) - output kept at F:\foundry\output\compute\<order>`; the
order retired and nothing ever delivered it):** a failed `ship_compute` now leaves `<order out>\.unshipped.json`
(`{name, deliver_to, error, at}`) beside the kept output and the order taker's `reship_kept` pass, between orders,
delivers every marked folder on the first pass its share answers (same `unreachable_shares` derivation; a share still
down = WAIT, quiet), logs `RESHIPPED <order>: N file(s) -> <dest>` and DMs once (`COMPUTE ORDER DELIVERED LATE`). An
order that retired BEFORE the marker existed (or whose marker a human removed) is marked by a control slip,
`{"action":"reship","order":"<name>","why":"…"}` — the desk reads `deliver_to` from the newest archived spec and marks
the kept output; it FAILS (slip consumed, nothing marked) when there is no archived spec, no `deliver_to`, or no
kept output. The marker never travels with the delivery.

**A desk record stuck at `claimed` after the order DELIVERED — the REPORT half of the same seam (2026-10-17).** A
factory tells the desk an order's fate with one `POST /p20/jobs/<id>/result`. Until 2026-10-17 that was a single try:
BigHonker's `speedcheck-…-p37long` delivered at 20:01 CT 10-16 while the orchestrator was mid-recreate, its POST was
refused (`[PREFLIGHT] WARNING: fleet completion report failed (… 10061 … actively refused)` in the order's
`.foundry.log`), and the record read `claimed` for 14 h — a deploy at the wrong second leaves a lie on the desk and
holds any `after_ids` successor forever. **Now:** a refused report is KEPT as `<state>\desk_reports\<desk id>.json`
(`p20_foundry.DESK_REPORTS`; Tolkien: `<root>/state/desk_reports`, `tablet_foundry.DESK_REPORTS`) and the order
taker's `resend_desk_reports` pass, between orders, resends it once the desk answers — desk still down = WAIT, quiet,
the pass stops at the first connection failure; logs `RESENT desk report: <outcome> for step <id>`. If the desk
ANSWERS with a refusal (an HTTP error — e.g. the record is gone) that one marker is set aside as `.refused` for a
human and the rest still go. **A record stuck from BEFORE this** (no marker exists): confirm the delivery (the order's
`out.json` / progress markers under its `deliver_to`, and the `.foundry.log` line above), then close it through the
desk's front door inside the orchestrator: `p20_queue.mark('<id>', 'done', reconciled='<evidence>')` — never by
editing the record file. Tests keep their markers out of a live state dir (`test_foundry.py` redirects
`F.DESK_REPORTS` at load; pinned by `TestDeskReportsNeverLive`), because the runner would resend a fixture's report.

**Every desk order reports its end, not only compute orders (2026-10-26).** Until then only `run_compute_order` told
the desk an order SUCCEEDED; a desk training / render / probe order that finished left its record `claimed` forever
(found on the six Krea resume-proof arms, closed with the `p20_queue.mark` door above). The order taker now reports
when it sees an order end: `done`, or `cancelled` for one a stop slip ended early (it shipped what existed — an
operator's decision, which holds successors like `failed`); an order that reported itself is not told twice
(`job.desk_reported`). Pinned: `TestATransientNeverConsumesAnOrder` (+4).

### An order's path by HOSTNAME (`\\SOFTSERVE\…`) WAITS forever in PREFLIGHT — the factory's service account cannot see it; use the IP form `\\<softserve-address>\…` (2026-10-23)

A factory's order taker runs as a service account with no credential for `\\SOFTSERVE`; the IP form reaches the same
share (every `submit_*` script writes `\\<softserve-address>\SSDShared\...`). An order whose `args` or `deliver_to` names
the share by HOSTNAME logs `[PREFLIGHT] input share(s) \\SOFTSERVE\SSDShared unreachable from this box -> the order
WAITS, untouched` every 32 s and never fails - the 2026-10-23 16:54 reranker order sat 40 min that way while BigHonker
read "free" on the deck. Cancel it at the desk (`POST /p20/order/cancel`) and resubmit by IP. The desk record stays
`claimed` the whole time: a claimed order with an idle tile is the tell.

### A TOLKIEN order's model goes in rigby's `/home/owner/models` (Tolkien's `/models` mount), NOT `/mnt/fellowship/models` (the TABLET store) (2026-10-23)

`tolkien_llm` resolves `spec["model"]` against `TOLKIEN_MODELS` = the container's `/models`, which is rigby's
`/home/owner/models` (`docker inspect tablet-foundry` lists the mounts). `/mnt/fellowship/models` is the tablets' store
(the one `rigby-models.json` does NOT list). A model copied to the wrong one fails the order's server launch with
`failed to open GGUF file '/models/<name>' (No such file or directory)` - the 2026-10-23 18:58 embed order. Copy with
`scp` to the right path, `sha256sum` it on rigby against the source, and the next capability push lists it.

### Compute payload authoring rules — `ModuleNotFoundError` for a module beside the payload (embedded-python `._pth` interpreters)

Rules every `mode:"compute"` payload must follow. These are properties of the compute lane,
not suggestions:

* **Sibling imports need a self-insert.** A factory's compute interpreter can be an
  **embedded Python distribution** (a `python3XX._pth` beside the exe — Mothership's
  `F:\ai-toolkit\venv` is one). A `._pth` REPLACES `sys.path` with exactly its listed
  entries: the script's own directory is never added and `PYTHONPATH` is ignored. A payload
  importing a module deployed beside it in `bin` must therefore start with
  `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` before the sibling
  import. The symptom of a missing insert is `ModuleNotFoundError` for a file that is
  provably present and hash-verified in `bin`. Test the property with `python -P`, which
  strips the script dir the same way.
* **A fail-soft import needs a positive pin.** `try: import x / except: x = None` hides the
  miss above completely — a test must assert the module actually bound (run the payload
  under `-P` and check), or the fallback runs everywhere and the reuse is fiction.
* **The failure DM carries the reason.** The runner streams every payload's output; on a
  non-zero exit, the failure DM and the desk report carry the child's last non-empty line
  (a refusal message or a traceback's final `Error:` line). "see log" appears only when the
  child died printing nothing. Payloads should therefore make their last line before any
  exit state the reason plainly.
* **Refusal is EXIT CODE 2 and names the condition** (`REFUSED: <what> - <why>` as the last line, then
  `sys.exit(2)`); a refusal is a property of the run's preconditions, not of the order. **The foundry
  HOLDS a refused order** (since 2026-09-28, `REFUSED_RC` in `p20_foundry.py`): it is not reported failed
  and not retired; it writes `<order>.job.refused.json` beside the spec, releases the lane, steps the order
  aside for 10 min so the orders behind it run, then offers it again. One DM on the first refusal and every
  6th after. ⚠ `raise SystemExit("message")` exits **1**, not 2 — print the reason, then `SystemExit(2)`.
  Any other non-zero exit is still FAILED and retired (a broken order must not block the queue).
* **A long payload PRINTS its progress: lines starting `[tag] N/M ` on stdout.** This is
  the same channel every other order kind uses (the caption pass publishes its own
  `framing N/M` count) — the worker reports, nothing re-derives. The runner parses those
  lines off the stdout stream it already reads (`COMPUTE_PROG_RE` in `p20_foundry.py`)
  and publishes step/rate/ETA to the tile; the worker's printed total beats any stale
  denominator. `triage_rewrite.py`'s per-section `[rewrite] 3/8 <id> ok in 42s` line is
  the reference shape. Fallbacks, in order: a payload that prints nothing but writes one
  `.done` marker per unit under `<out>.progress\` (cleared at start, removed after the
  ship, `_total` beside the markers as denominator) is counted by glob; a declared
  `progress_glob` + `progress_total` in the spec always wins over both conventions; the
  desk also auto-fills the glob + slice size on parallel items orders at compose time.
  An order with none of these shows phase only, and the stall demon says UNWATCHABLE
  once rather than staying silently green — a published step makes any compute order
  watchable regardless of what the spec declares.

### 🪟 HELPER WINDOW ORDERS — `bin\helper_window.py`: ONE helper order per lead run, pieces as item files (2026-10-06)

When a lead (an A1-team run, later HAI's live delegation) hands out many small pieces to a helper box, one desk order
per piece pays the desk's fixed cost every time (pick → spawn → residency check → ship ≈ 12 s, measured 2026-10-06
16:20; 20–33 s per piece from the lead's clock). A **helper window** pays it once: the lead's delegator places ONE
compute order on the helper box and the worker holds the model resident, answering item files as they appear.

- **Payload** `{FOUNDRY_ROOT}\bin\helper_window.py <items_dir> {ORDER_OUT}\out.json --model M --num-ctx N
  [--temperature T] [--think] [--seed-base S] [--lead-order <desk record id>]` — deployed by `fleet_deploy.py
  --no-restart helper_window.py` (every GPU factory's `bin`; it imports `infer_ollama` beside it for the same
  residency check, streamed chat with the loop cut, and power meter).
- **Items** `<items_dir>\<id>.item.json` = `{"id", "prompt"[, "system"]}` → the worker writes `<id>.out.json` beside it
  (one infer_ollama-shaped row: answer, thinking, tokens, `done_reason`, `wall_s`, `gpu_j` when metered live) and a
  `{ORDER_OUT}\out.progress\<id>.done` so the deck shows items answered. Write items `tmp + os.replace`; the worker
  waits a poll for a half-written one.
- **Closing** — `CLOSE` in `items_dir` (the lead's `finally`), or the lead's own desk order no longer alive
  (`--lead-order`, asked every 30 s: a STATUS, never a clock). A window whose lead died without either sits in the
  lane until the operator's stop slip — the lead's order id is in its queued `.job.json` (`fleet_order_id`), which
  is where `a1_order.own_desk_id` reads it. At close the worker writes `out.json` (all rows, `closed_by`, `open_s`,
  energy) and the foundry ships it to `deliver_to` like any compute order.
- **Submit** like any pinned compute order: `only: [<helper box>]`, `requires: ["order.compute", "model.<M>"]`,
  `progress_glob: "{ORDER_OUT}\\out.progress\\*.done"` (no `progress_total` — items are not known ahead),
  `notify: "failures"`. The A1 client is `a1_delegate.WindowDelegator` (plan `team.window: true`,
  `submit_a1.py --team-window`); it asks the desk about the WINDOW's status every 20 s while waiting on an item
  (held / ended → an ERROR the lead sees), and a refused window fails every piece fast.
- **Refusals** are infer_ollama's: rc 2 when the model does not fit the card right now (the foundry holds the window
  and retries); the engine hanging on an item holds it the same way. Rows with an HTTP error are rows, not crashes.
- Design and prices: [[34-A1-Agentic-Build-Family-Spec]] § "Helper WINDOW". Dealing registry: the window is always
  `only`-pinned, so `compose_orders`' size reserves do not apply (an unpinned window would deal size-blind — pin it).

### 🖼 PAGE RENDER ORDERS — a job's page in a browser on a real GPU: `bin\render_order.py`, `venv-render`, capability `render.browser` (2026-11-05)

`coding_render` on a workspace `.html/.htm/.svg`, optionally with a `?query` or `#fragment` that the page is opened with (2026-11-06), sends ONE compute order per call (`gpu_render.py`): the runner bundles the page's folder, starting as many levels up as the folder's own files name `../` and never above the workspace (narrowing level by level if that is over the file limit), the
bundle goes to `tabfoundry-jobs\renders\<id>\`, Spock (`place("page")`) names a free box declaring `render.browser`, never the box serving the job,
the factory ships pictures + `result.json`, the runner files them under `renders/` beside the page, and Kirk deletes the folder. About 10 s a call
warm. No free box: the runner draws in software and the result says so (`drawn_on`, `why_not_a_gpu`). STL and public web addresses stay in the runner.

- **Add a box:** `ssh <box> "py -3.12 - <FOUNDRY_ROOT>" < setup_render_venv.py`, deploy `render.py netguard.py render_order.py` with `fleet_deploy`, then
  run `render_order.py prove {ORDER_OUT}` AS AN ORDER (`python_exe {FOUNDRY_ROOT}\venv-render\Scripts\python.exe`): it writes `venv-render\webgl_proof.json`,
  and the box declares `render.browser` from its next push only if every one of 12 fresh launches opened WebGL on a real card. Scratchpad examples:
  `prove_order.py`. ⚠ `render.py` and `netguard.py` are the coding runner's bytes (`test_fleet_deploy.TheRenderToolIsOneSource`).
- **State 2026-11-06 01:35:** all four offer pages. BigHonker's RTX 3070 through Vulkan (Direct3D 11 as an order: 1 to 8 of 12, `SwapChain11` `0x887A0022`) goes first by Spock's order; Mothership's 7900 XTX through Direct3D 11, never for a job it serves; Softserve's 5700G through Direct3D 11 (Windows' default adapter, chosen so page renders stay off McCoy's and Plex's card; the 3070 Ti is an option: Softserve through `--use-angle=vulkan --enable-features=Vulkan` draws on it, 12 of 12, measured 2026-11-06); rigby's GTX 1060 through Vulkan, Tolkien order kind `page.render` (`tolkien_page.py`), last, only when Tolkien's report says free. On rigby the browser runs as an unprivileged user and its libraries are a named list out of `/hostlib`: without the EGL, GL and display set Chromium falls to SwiftShader silently.

| contract | where it is satisfied |
|---|---|
| desk dealing | placed by `scheduler.place("page")`, then pinned to that host; no deal, no size reserve |
| stop → ships what exists | a stop ends the browser; the caller finds no `result.json` and draws in software, saying why |
| progress | one `[render] 1/1` line; seconds long |
| retirement / cleanup | the page copy is removed before shipping; Kirk removes `renders\<id>` by name, whatever happened |
| capability declaration | `render.browser`, per push, from the proof file (`p20_foundry.render_browser_proven`) |
| host-priority yielding | kind `page` has `yields_to_live`, and takes the Plex gate on Softserve; a busy lane is passed over, except a page's own record (`scheduler.page_on_lane`) |

### 🧪 EVALUATION CAMPAIGNS ARE COMPUTE ORDERS — `eval_campaign` composes `infer_ollama` orders (2026-09-27)

**An evaluation of a model on this fleet is a factory order, never a Kirk-side call.** the owner, 2026-09-27:
*"Kirk is literally supposed to be submitting orders to the foundry. There is no other option... no blind
spot."* Until that day Kirk fulfilled each eval step ITSELF (`_eval_step_worker` → `_run_query`, calling the
box's ollama around its foundry): no claim, no tile, invisible to the police. That executor is **retired**.

**Two ways in, both through the desk:**
* **`POST /p20/order` with `spec.kind: "eval_campaign"`** — pins + rounds + control + evidence contract
  (full field table: [[HAI-Core-Architecture-API-Contracts]] §`eval_campaign`). Kirk validates, writes the
  items to `E:\SSDShared\tabfoundry-jobs\campaigns\eval\<campaign_id>\`, and composes ONE `infer_ollama`
  compute order per (kind × ≤25-pin batch): `chat` → `BRAIN_MODEL`, `chat27b` → `HEAVY_MODEL`, **or the model
  you pin with `"requires": ["model.<name>"]`** (honoured — a model comparison is the same campaign with one
  requirement changed). Rows come back through `GET /p20/eval/<id>?user=<admin>` with `served_model` per row.
* **A raw compute order** (what `hard_bench_fleet_orders.py` does) when you want your own items format —
  same payload, same routing, no campaign bookkeeping.

```json
{"spec": {"kind": "eval_campaign", "name": "decoy-pong", "user": "ClaudeSupervisor", "rounds": 1,
          "pins": [{"id": "p1", "kind": "chat", "effort": "normal", "prompt": "Reply with the single word pong",
                    "prompt_sha256": "<sha256 of the prompt>"}, "..."],
          "control": {"pin": "p1", "expect": "pong", "rationale": "trivial known-good"},
          "evidence": {"require_served_identity": true, "require_deep_trace": true, "void_on_ctx_downgrade": true}},
 "requires": ["model.qwen3.5:9b"]}
```

**What it measures — say it out loud:** the MODEL on the prompt as given. No Kirk grounding, tools or
deliberation. `effort: "think_hard"` is **refused at intake** (`refused: effort_unsupported`), and a model no
factory declares is refused by name (`refused: capability_gap`), both with nothing queued. HAI-pipeline evals
(tool trails, deep traces) need a pipeline-as-an-order payload that does not exist yet.

**Watch it like any order:** the factory tile shows the order + `[infer] N/M` progress; the load reads
`accounted · foundry order: '<name>'`; `/hai/inflight` shows nothing, because Kirk made no call. Stop it with
the standard `.stop.json` slip (the campaign then fails and names the partial-rows file).

### "is already open at the desk" — an order's name must be unique while it is open; a cancel addresses the NAME (2026-11-05)

`POST /p20/order` refuses a second order under a name that is still pending, held or claimed. The factory files an order's output and delivery under its name, and once an order is
collected the desk's cancel reaches the factory **by name**. So two open orders under one name cannot be told apart. 2026-11-05: two probes submitted in the same second took one
name, the desk accepted both, and the cancels meant for the queued copies stopped the running arm and a different queued arm instead. **A name with a stamp needs more than seconds
in it** when a script submits in a loop (`submit_resume_probe.py` and `submit_tool_shape.py` add a random suffix). A name comes free once its order is done, failed or cancelled, so
retrying after an end is fine. Read the queue back after a cancel: the reply `"status": "claimed", "how": "factory control"` means a request was sent, not that it landed where you meant.

### How to SUBMIT AN ORDER TO THE FLEET without naming a factory — the fleet order taker

**Vocabulary.** the owner: *"When I say desk, we mean order taker, and in the context of the fleet it
is the orchestrator and scheduler. Which is exactly the component that does the decomposition."*

> **THE FLEET ORDER TAKER IS THE ORCHESTRATOR + SCHEDULER. IT ALREADY EXISTS.** It is not a new
> component and must not be rebuilt as one. `scheduler.place_order` / `chunk` / `compose_orders` /
> `factory_capabilities` are its working parts, live since 2026-08-02.
> **Per-factory** order takers (`p20_foundry.py --queue`, one per box) are a *different* thing —
> they consume specs from that box's own `jobs\queue`. Fleet level decides WHO builds; factory level
> builds. Submitting "to a machine" goes to the per-factory taker; submitting "to the fleet" goes
> here ([[P20-MULTI-FACTORY-FLEET]] §opening, the owner's own framing).

**Use this instead of copying a `.job.json` into any factory's `jobs\queue` by hand.** Hand-placing
picks the machine for the fleet, which is the customer walking onto the shop floor. the owner:
*"one complete order given to the fleet order desk."*

> ⚠ **THERE ARE TWO ORDER-TAKING SPOTS AND BOTH ARE LEGITIMATE.** the owner's ruling: *"there's
> supposed to be two order taking spots... We can
> directly submit orders to mothership's factory order taker, and we can also submit to star fleet
> command. We prefer star fleet but on occasion there may be reasons we don't want to involve the
> whole mechanism. I'll explicitly say when we want to do that, but I don't want to lose that
> ability."*
>
> **And neither one is a bypass** — *"There's never a bypass of the order system. Like star command
> passes the order to the order takers at the factories lol."* Starfleet does not replace the
> factory desks; it **feeds** them. So the layers are: STARFLEET COMMAND (this endpoint) on top,
> the per-factory desks below, and an order reaches a factory desk either way.
>
> **What IS a bypass: starting a foundry process against a spec file.** That submits an order to
> nobody — no queue, no lane claim, no control slips, no retirement, no archive. That is the defect
> removed from `queue_training` on 2026-08-15 (next section).
>
> **For a session, "manual control" means PIN THE HOST AT THE DESK** (`"only": ["mothership"]`),
> not hand-placing a file: the order still passes through capability checks, lane state and typed
> refusals, and the hook `no_factory_floor.py` still blocks hand-placed `*.job.json`. the owner himself
> retains the direct-to-factory route and will say when he wants it.

```bash
curl -s -X POST http://<sundae-address>:8099/p20/order -H 'Content-Type: application/json' -d @order.json
```

The body is `{"spec": <the whole .job.json>}`. Optional: `requires` (defaults to the spec's own),
`kind` (defaults to inferred), `rush`, **`parallel`** (split across every capable factory) and
**`items`** (the work to chunk when parallel). **The submitter never names a host.**

⚠ **Author the body as a FILE (Write tool / editor) and POST with `-d @file.json` — never inline
JSON with backslashes through a shell.** A 2026-08-21 chain submission put a literal TAB into a
UNC arg via bash escaping; the poisoned job was only caught by byte-reading the queued spec.

**Library triage (30b) — a worked TWO-LANE example, proven 2026-08-22 (goldcal).** The rules lane
runs on the Fellowship (`triage.extract.v1`); the model lane is a compute order any GPU factory
with `triage.rubric.v1` can fill — calibration showed every tier ≥10/11 on gold, so leave classify
UNPINNED and let the desk route it. Sequencing between orders is `after_exists` (the classify
order stays invisible until the extract handoff lands) — no chain step needed:

```json
{"spec": {"name": "triage-myrun-extract", "kind": "triage.extract",
          "docs": ["Reference/Some-Doc.md"], "notify": "failures",
          "deliver_to": "/ssdshared/library-triage/myrun.extract.json"},
 "requires": ["triage.extract.v1"], "only": ["fellowship"]}
```
(Tolkien honours `"notify": "failures"` on every order kind since 2026-10-06 — `tablet_runner.dm_quiet`: a shipped
order sends no DM; VOID, HELD and stopped always DM. Before that the field was documented here but unread on the
Fellowship side. A1-team pieces use it so one lead run is one DM, not one per delegated brief.)
```json
{"spec": {"name": "triage-myrun-classify", "mode": "compute", "load": "gpu",
          "payload": "{FOUNDRY_ROOT}\\bin\\triage_classify.py",
          "args": ["\\\\<softserve-address>\\SSDShared\\library-triage\\myrun.extract.json",
                   "\\\\<softserve-address>\\SSDShared\\library-triage\\myrun.candidates.json"],
          "after_exists": "\\\\<softserve-address>\\SSDShared\\library-triage\\myrun.extract.json",
          "notify": "failures"},
 "requires": ["triage.rubric.v1", "order.compute"]}
```

Notes that keep this honest: model + `num_ctx` come from each box's `triage_local.json`
(factory customizations, never order fields); **Softserve's row is `qwen3:8b`** — chosen 2026-08-22 when the 9b
spilled there (4.9/6.3 GB @ 6144, twice). ⚠ That spill was Softserve's OLLAMA, not its card: it ran 0.30.7 with no
flash attention and an f16 KV cache. Brought to fleet parity 2026-09-28, Softserve holds the 9b fully up to 7,168 and
qwen3:8b up to 17,408 — exactly like BigHonker (RUNBOOK §"Fleet VRAM residency map"). the owner: "change to whatever is
correct" → the row stays **qwen3:8b** on merit (0.93 vs the 9b's 0.82–0.83 on both coding families, ~4× faster per
answer) with **`num_ctx` 17,408 — its tested max** (was the 8,192 default; doctrine 13a); args are UNC because mapped drive letters are per-logon-session; **`"notify": "failures"`** suppresses the started/complete DMs (failures and
stops always DM — severity never opts out); in the merged candidates, **tier comes from the
model, verify authority is the rules lane's `rules_verify`** (calibration: models 7–9/10 on
verify, rules mining 16/16 exact).

**Big classify jobs: opt into the fleet spread.** Add top-level `"parallel": true` +
`"items": [<the residual b64 items, read from the extract handoff>]` to the classify submission
— the DESK deals weighted shares across every capable GPU factory (`spec["items"]` → the
foundry passes `--items-file` → the payload runs its share only, writing
`<out>.share-<order>.json`). Recombine with
`python triage_classify.py --merge <extract> <out>` — it REFUSES an incomplete share set.
Proven 2026-08-22 (728/728 across two factories). Items exist only after extract ships, so the
parallel classify submits post-extract rather than riding an `after_exists` chain.

**`selfeng.overnight` — the overnight self-engineering trigger as a top-layer order (06b, signed
2026-08-15).** Kirk is the executor (no factory ever pulls it); the desk fires
`/self-eng/overnight/start` when the window arrives. Exactly ONE of `window` (operator-supplied —
**the expected mode while the the utility feed is broken**; the owner's manual no-go times as a field) or
`window_mode: "cheapest_4h"` (computed — permitted only when the price data COVERS the window;
`energy.window_covered`, coverage not fetch-age, because the utility publishes once daily ~6pm for the
next day). Spec fields: `name`, `kind: "selfeng.overnight"`, `max_seconds`, `dry_run`, `user`,
and the window. Fates are typed: WAIT (pre-window / no coverage / busy / no eligible items,
`hold_reason` recorded) · FIRE (marked `done` with `gate` + `fired_at` BEFORE launch) · EXPIRE
(operator window passed unfired). The legacy `scheduled.json` one-shot still works but is
DEPRECATED — submit an order instead. Design + invariants: [[06b-OVERNIGHT-TRIGGER-AS-AN-ORDER]].

```bash
curl -s -X POST http://<sundae-address>:8099/p20/order -H 'Content-Type: application/json' -d '{
  "spec": {"name": "selfeng_tonight", "kind": "selfeng.overnight", "max_seconds": 21600,
            "dry_run": false, "user": "the owner",
            "window": {"start": "2026-08-15T21:00", "end": "2026-08-16T02:00"}}}'
```

⚠ **THE DECOMPOSITION IS `scheduler.compose_orders`, AND IT HAS EXISTED SINCE 2026-08-02 — DO NOT
REBUILD IT.** [[Fellowship-Order-Taker-Design]] §1: *"The scheduler half is done."* It turns ONE
template into per-factory specs, filling per-HOST properties from the scheduler's own tables
(`HOST_CAPTION_MODEL`, `HOST_CAPTION_WORKERS`) and the item slice from `chunk()` — the owner, 2026-08-02:
*"That's supposed to be the scheduler's job to split the work up."* A caller that materialises
per-host specs by hand is retyping fleet properties into orders, which has already shipped a corpus
captioned by two different models. The response's `steps` array is one entry per factory.

| endpoint | who calls it | what it does |
|---|---|---|
| `POST /p20/order` | anyone submitting work | **`scheduler.compose_orders`** — one template in, per-factory specs out, each enqueued |
| `GET /p20/jobs?host=<box>` | every factory's order taker | **already existed** — serves that box's buildable work |
| `POST /p20/jobs/<id>/claim?host=<box>` | the collecting factory | compare-and-set so two factories cannot take one order |

**Routing is by CAPABILITY, never hostname.** `bake.klein` lands on the box holding the Qwen3-4B
encoder; `render.klein` lands on the box holding the klein base. Move a model and the routing
follows with no code change. If no factory holds the stated capability the order is **REFUSED at the
desk, by name** (`{"ok": false, "requires": [...]}`) and nothing is queued — a capability gap is
named up front instead of failing in preflight ten minutes later on a box that was never able.

**Delivery is PULL, and there is deliberately NO second listing endpoint.** No factory has an
inbound port and Mothership cannot reach BigHonker, so the desk holds the order and
`collect_fleet_orders()` in the order-taker loop asks for it, claims it, and writes it into that
factory's own `jobs\queue`. From there it is an ordinary order — capability assessment, spec
defects, priority, retirement all behave exactly as if a human had dropped the file in.

⚠ **A factory order rides in the SAME `p20_queue` store as render jobs**, so the render worker
enforces an ALLOW-list (`RENDER_KINDS = ("", "render", "quick_preview")`, `p20_render_worker.py`).
Without it the worker hands an order spec to `run_job`, renders nothing usable, and **marks the job
done — destroying an order no factory had collected yet.** `""` is not an oversight: ordinary person
renders carry no `kind` key at all. **Any new job kind must be added to that list or it will be
silently skipped by every render worker** — which is the correct failure direction, but it is a
deny-by-default, so check there first when a new kind "never runs".

⚠ The spec is staged as `<name>.job.json.incoming` and `os.replace`d into place. The order taker
**watches that directory**, so a spec written in place is read half-formed — and a truncated JSON
reads as a broken spec, which gets **ARCHIVED**, silently destroying a good order.

Tests: `orchestrator-build/test_fleet_desk.py` (21, incl. pins that `pending()` stays kind-agnostic
so the duplicate listing endpoint cannot grow back).

**How the desk picks factories — declared capabilities, tiers, and the item deal**

* **Routing is by CAPABILITY, never by hostname.** Each factory pushes its declared set on
  every run-state beat: the static file-detected capabilities, plus live `model.<name>` caps
  read from its own ollama `/api/tags` (60 s cache; ollama unreachable = no model caps that
  beat — a box whose ollama is down cannot serve a model), plus `triage.ctx.<N>` from its
  `triage_local.json` row. An order states `requires: [...]`; only declaring factories are
  eligible. Pin a model with `requires: ["model.<name>"]` — never a host. A model pulled onto
  a box joins its declaration on the next beat with no restart or code change.
  **Models served by an engine other than ollama (Strata — Flash-Next) declare the same way** (2026-10-18, the owner:
  *"Usually, we just have things advertised"*): `p20_foundry.strata_served_caps()` reads this box's row of
  `<root>\workspace\flashnext\strata_served.json` (`{host: {model: {"plan", "ctx"}}}`) and declares `model.<name>` only
  when the plan reads, its Strata install and every shard are on disk, and the plan serves the row's tested context
  (`--context`). Mothership declares `model.qwen3.8-flash-next-strata-q2_0` (plan
  `strata-serve-q2_0-v045.20261018.json`, 262,144). Ship both files with `fleet_deploy --workspace flashnext
  strata_served.json` / `--workspace "flashnext\plans" <plan>`; the orchestrator's `config.MODEL_NUM_CTX` carries the
  same context. The order still names the plan (`infer_ollama --strata <plan>`) — the declaration says the box can
  serve it, the plan says how. Tests: `test_foundry.StrataServedDeclares`.
* **The Plex host places LAST for orders** (`ORDER_HOST_DEPRIORITY`, a dominating tier, not a
  score — a tradeable score inverts under load). `place()`'s ranking still decides within a
  tier; non-card factories sit behind every x86 box.
* **Hard routing pins**: the request-level `only: [host]` / `exclude: [host]` fields ride
  through to placement. Use `only` for a per-box order (e.g. verify probes addressed to one
  box); everything else routes itself.
* **A LOST CARD OR A PLEX-OWNED SOFTSERVE IS UNAVAILABLE, NOT BUSY — the order is REROUTED or HELD
  BY KIRK, never run on CPU** (the owner, 2026-09-28: *"If the computer is not available, then Kirk is
  supposed to re-route to a similar device … if we can't work, then the mid tier orders should be held
  by Kirk until we have available resources on machines."*). Built the same day, after BigHonker's card
  died four minutes into a qwen3:8b order that then ran on CPU at a 6 h ETA:
  - **At placement:** a busy capable factory still gets the order queued (WAIT), but a factory whose card
    is lost (`gpu_health`) or Softserve while Plex needs it (below) is skipped — one check,
    `scheduler.order_host_unavailable()`, used by the busy-fallback AND the parallel re-add. If every capable
    factory is unavailable the desk answers `{"ok": true, "held": true}` and keeps the order in status
    **`held`** (never offered to a factory); `_held_orders_tick()` re-places it every scheduler pass (300 s).
    A capability GAP (nobody can build it) is still refused — held is only for "capable but unavailable".
  - **After placement:** the factory asks `GET /fleet/availability?host=<me>` before launching any order
    and every ~60 s while a compute order runs. A lost card (GPU orders, before or during) or Plex
    transcoding on Softserve (every order) → the factory ends the child and **hands the order back**
    (`POST /p20/jobs/<id>/handback`); the desk re-places it on another capable, available factory or holds it.
    Partial output stays in the order's own folder; nothing ships; nothing is reported failed. A factory that
    just handed an order back is not offered it again for 15 min.
  - **PLEX ON SOFTSERVE — ONE SIGNAL, EVERY ORDER KIND, CPU INCLUDED** (the owner, 2026-09-28: *"It can't take
    resources away from plex if plex is active"*). The signal is `scheduler.plex_transcoding()`: Tautulli's live
    transcode count ≥ `FAILOVER_MAX_TRANSCODES` (1; `SOFTSERVE_MAX_TRANSCODES` until 2026-11-17). Placement (`order_host_unavailable`), the factory's start
    check and its mid-run check all read it, served as `plex_active`. It covers CPU orders because the one
    measured harm to Plex (2026-08-26) was **CPU contention** — a software transcode starved by a lab order's CPU
    threads while the GPU encoder sat at 0%. The utilisation gate (`_plex_gate`) still applies to GPU orders at
    placement, but it cannot see a CPU transcode and reads "free" whenever our 8B is resident, so it is never the
    Plex signal on its own. If Tautulli cannot answer, `plex.activity()` asks **Plex itself** (`/status/sessions`
    with the orchestrator's `PLEX_TOKEN`; a video or audio transcode decision counts, the same thing Tautulli
    counts — the owner, 2026-09-28: *"it can just check actual plex"*); only when neither answers is it could-not-tell,
    which never acts (a positive signal is needed to hand back). How often this bites: a transcode is running ~23% of the time, ~45–52% at 19:00–21:00 —
    [[Plex-Usage-Profile-Softserve]].
  - **A PERSON ON THE CARD MAKES IT UNAVAILABLE TOO — on every factory** (the owner, 2026-09-28: sharing *"with
    priority going by far"* to the user; *"a similar feature should live on all factories"*). One signal,
    `scheduler.user_on_card(host)` → `"user"` / `"clear"` / `None` (could not tell — never a user), folded into
    `order_host_unavailable()`, so placement and the factory's `start_ok` both honour it. It reuses the lab's
    existing detectors, no new thresholds: **every box** — load accounting reads `unaccounted` (card busy with no
    foundry order, claim or Kirk call for two pushes); **Mothership** — also the gaming guard
    (`mothership.gaming_busy`: low free VRAM with no ollama model resident, which catches a game idling in a menu),
    asked only when nothing of ours holds the card; **Softserve** — load that Plex's own transcodes explain is left to
    the Plex rule above, so Plex is never double-counted as "a user".
    Cached 60 s per host (the load reporter pushes once a minute). ⚠ **Scope today: START time only** — an order
    already running keeps running when a user arrives (mid-run is lost-card / Plex-transcoding only); mid-run
    user detection and pause-and-share are the next step, sized from the logger + Plex usage history first.
  - **Could-not-tell never acts:** an unreachable desk means the factory keeps the order and WAITs; a
    train order WAITs on its own box (its checkpoints live there); a local (non-desk) order WAITs.
  - **A PINNED order is never re-assigned — it WAITS for its own card** (the owner, 2026-09-28: *"If it's something we
    specifically chose Softserve for then it's not to be re-assigned"*). An order submitted with `only: [host]` is
    stamped `pinned_to` by `compose_orders`; on that box the factory treats "card unavailable" (lost card, Plex
    needing Softserve's card) as WAIT: before launch it stays queued (`LaneBusy`), mid-run it ends the child and stays
    queued under its own name. Desk-routed (unpinned) orders are still handed back and re-routed, as above.
  - **A slow ComfyUI stop is a WAIT, never a failure; a real failure reaches the desk** (2026-10-03, foundry
    `eb70179`). `stop_comfy`'s PowerShell timed out in preflight twice on Mothership and the catch-all archived two
    healthy W1 orders as FATAL without telling the desk, so their records stayed `claimed` and the menu counted them
    in flight forever. Now: on a timeout the port decides (8189 free = stopped; held or unreadable = `LaneBusy`, the
    order stays queued); every FATAL reports `failed` to the desk; and a desk cancel of an order already ARCHIVED on
    the box reports `cancelled` — the front door for closing a stale `claimed` record (`POST /p20/order/cancel`).
  - **Waiting loses nothing: `infer_ollama` resumes** (2026-09-28). An order that restarts under its own name lands in
    the same `{ORDER_OUT}` and the payload keeps every (item, round) already answered in `out.json.partial.jsonl`,
    re-running only errored or missing rows — the same "resume and skip what is done" convention the stall demon's
    compute respawn (stop slip → requeue slip) relied on for shard-writing payloads; `infer_ollama` had lacked it, so
    a respawned inference order used to redo every row. To re-run just the errored rows of a FINISHED order, drop
    `{"action":"requeue","order":"<order name>","why":"…"}` as a `.ctl.json` in that box's `jobs\queue` (its
    `{ORDER_OUT}` folder must still exist; delivery lands beside the first, timestamp-suffixed, never overwriting).
  - **No request clock: `infer_ollama` streams and ends only on SILENCE** (2026-09-29, doctrine class 13a). Its
    flat 600 s request timeout killed 7 of 72 thinking-on rows mid-answer on 2026-09-28. Replies now stream;
    `IDLE_S` (600) is a socket-read window that every streamed token resets, so an answer still being written is
    never cut off and a server that has gone silent still ends the row. Pinned in `test_foundry.py`
    (`test_a_long_answer_still_being_written_is_never_cut_off`, `test_real_silence_still_ends_the_request`).
  - **A payload that works inside one long unit must BEAT, or the stall demon calls it a wedge** (2026-09-29). The
    demon's `watch.compute` calls a GPU order wedged when its unit count is static past `progress_stale_after`
    (default 900 s) and respawns it — and a respawn restarts the unit in flight, so a unit that honestly needs
    longer can never finish (a 919 s thinking answer was stopped that way). A payload prints `[beat] N` (any
    increasing N) while it is producing; the runner parses it (`COMPUTE_BEAT_RE`) and publishes `work_beat` in
    `CURRENT_RUN`; the demon treats a moving beat as work (verdict `working`). `infer_ollama` beats every 30 s
    while tokens stream. A wedged card stops the stream, so the beat stops and the wedge rule applies as before.
  - **A delivered compute order counts as shipped** (2026-09-29): `ship_compute` records `delivered` (files
    shipped), one of `SHIPPED_COUNT_KEYS`; before that, every `deliver_to` order was logged "SHIPPED NOTHING".
  - **DMs:** `↪️ ORDER REROUTED` only. A hold at submission and a release from `_held_orders_tick` send NO DM
    since 2026-09-29 (orchestrator gate 2531, "quietholds"; the owner: *"I have like a bazillion notifications"* — a
    268-order menu submit had sent one DM per held order). Holds are visible on the deck and in the desk's
    order records; `tests/test_fleet_desk.py` pins one DM per reroute and none per hold.
  - **A declared model is a GUARANTEE that it fits on that box up to a specific maximum context** — normally
    measured on the box before the capability is declared (the owner 2026-09-28: *"It's supposed to be a guarantee
    that it fits at a maximum of specific context. Remember usually we test the models on the box before we
    ever let it declare capability officially."*). ⚠ **Not yet how the code works:** factories declare
    `model.<name>` straight from their ollama store (`/api/tags`), untested and with no max context, so a model
    pulled "for future use" is offered to the desk with nothing saying what context it can take. The measured
    maxima live in RUNBOOK §"Fleet VRAM residency map" (`fit_probe.py` orders), not yet in the declaration. Until the
    declaration carries a tested max context, an order that does not fit is refused by the payload's residency
    check (rc 2) and held on that box.
* **Parallel orders deal items throughput-weighted; size-aware ONLY for registered payloads.**
  `chunk()` splits by content hash (a re-run places identically), weighted by measured per-kind
  rates (`TRIAGE_CLASSIFY_S_PER_ROW` for classify; caption rates for caption). Per-host budgets
  derive from each box's declared `triage.ctx.<N>` — but that size-aware deal engages only for
  payload basenames in `compose_orders`' size-reserve registry (`scheduler.py`: currently
  `triage_classify`, `triage_rewrite`, `native_history_review`). A payload NOT in the registry
  is dealt size-blind (hash + throughput only) and survives only because a well-built payload
  carries its own oversize belt (skip → errored row → residual → bigger tier). For registered
  payloads: an item a box cannot take is never dealt there, and an item NO box fits refuses the
  whole order at compose time, naming the item — "accepted, then errored" is a routing defect.
  Weights come from observed rates, never estimates.
* ⛔ **A NEW parallel order/work type MUST include a "teach the desk to deal it" step** (the owner,
  2026-08-26: *"if we're adding new order types with new work types, we need to always make sure
  we include a step to teach the system how to split these orders up intelligently"*). Concretely:
  register the payload basename + its prompt-reserve tokens (rubric template + completion
  overhead, measured not guessed) in the `compose_orders` size-reserve registry, add a
  `SizeAwareDealing` test for it in `tests/test_scheduler.py`, and record its observed per-row
  rate once one exists. Skipping this step is how the NI Part-2 pilot dealt 10.9–12.5k-token
  items to a 6144-ctx box (logged in RUNBOOK-DEFECT-LOG). This is row 1 of the full
  checklist below.

### 🧭 PLACED ORDERS — send a job FAMILY + TIER + LENS and the placement table (`policy.json`) picks the box, model, effort and context; residency cost; the table regenerates itself

The #34 Phase 7 placement path ([[34-Phase-7-Placement-Policy-Design]]). An order that names **no host and no model**
is placed by Spock from `Datasets\Project34-Menu\policy.json`, and the desk fills the model and its settings from the
SAME table row that ranked the chosen box. Use it whenever "which box, which model" should follow the measured menu
instead of a pin. A pin (`only`) still wins: it filters before the table orders anything.

**The body** — `POST /p20/order` with `family` (a menu job family: `coding-f1`, `coding-f2`, `work-w1`, `chat-c1`,
`chat-turn`, `tool`, `vlm`, `vlm-2x`), `tier` (`8gb` or `lead`; the triage tier) and optionally `lens` (`fastest`,
`least_energy`, `best_quality`; omitted = `fastest` for an interactive kind, else `least_energy` — `scheduler.policy_lens`,
one rule for placement and tokens). `requires` carries only `order.compute`; the args carry tokens:

```json
{"spec": {"name": "piece-x", "mode": "compute", "load": "gpu", "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py",
          "args": ["<items.json>", "{ORDER_OUT}\\out.json", "--model", "{POLICY_MODEL}", "--rounds", "1",
                   "--num-ctx", "{POLICY_NUM_CTX}", "{POLICY_TEMPERATURE_ARGS}", "{POLICY_THINK_ARGS}"]},
 "requires": ["order.compute"], "family": "coding-f2", "tier": "8gb", "lens": "least_energy", "user": "ClaudeSupervisor"}
```

`{POLICY_MODEL}` / `{POLICY_NUM_CTX}` become values; `{POLICY_THINK_ARGS}` / `{POLICY_TEMPERATURE_ARGS}` expand to
zero or more argv elements (`--think-level high`, `--temperature 0.6`; a vendor temperature adds nothing). The spec
gets `policy_row` (what was chosen) and the route records `policy`, so a held order is placed the same way. The desk
**refuses** — never a default model — when the order names no family/tier, the table has no row for the chosen host,
the host does not declare the model, or the model has no tested context there (`config.MODEL_NUM_CTX`).

**A1 helpers use it** (`a1_delegate.py`): a team block `{"placed": true, "family": "coding-f2", "tier": "8gb",
"urgency": "background"}` sends each piece as a placed order (`PlacedDelegator`); add `"window": true` for ONE placed
helper window per run (`PlacedWindowDelegator`; measured 10-10: first item 14.3 s incl. the load, warm items 1.1 s).
Pinned team blocks (`helper` + `helper_model`) are unchanged: they price ONE helper, never a placement.

**Residency is a COST on the `fastest` lens** (`scheduler.residency_cost`, live 10-10). For each eligible host the
table lists, Spock asks ollama `/api/ps`: the row's model resident = no cost; not resident = the row's MEASURED
`load_s` is added to its seconds per solve. A load nobody measured, or a host that did not answer, is charged nothing
and says so in the placement's `why` (`cold, load unmeasured` / `residency unknown`). Home rows stay ahead of overflow
rows whatever the load. `least_energy` and `best_quality` ignore residency (a load's energy is not measured).

**Where `load_s` comes from:** every `infer_ollama` order records its clean load in `out.json` `placement`
(`cold`, `load_s` = ollama's own `load_duration`; `cold: false` = it found the model warm and measured nothing). To
measure a pair on purpose: `hard-bench\submit_cold_load.py` (one order per GPU box × model the table can place, at
its tested context, `--cold-load` evicts first, two passes so each reading is repeated; pairs with no tested context
are skipped). `menu_policy.load_prices()` takes the median per box × model into each row's `load_s` / `load_n`.
First measured 10-10: 4–22 s on BigHonker/Softserve, the two passes within ~1 s of each other.

**Where `read_tok_s` (the re-read half of a reload) comes from:** GPU boxes — `menu_policy.read_prices()` over
delivered `out.json` rows that READ ≥ 2,000 tokens with ollama's own `prompt_eval_duration`. **rigby — since
2026-10-26, `menu_policy.rigby_read_prices()` over Tolkien `*.results.json` in the tablet outbox** (`prompt_tokens` ≥
2,000, `prompt_ms`, `cache_n == 0` so a cache-served row cannot pass, rigby arm only, and no delivery from before the
file's placement epoch `menu_fill.RIGBY_PLACEMENT[...]["since"]`). Before that rigby's rows read `None` whatever it
delivered — the GPU reader only knew `out.json` — so the x16 riser's 3.5× reading was invisible to placement. First
value: the 1060's MTP file **270.2 tok/s, n=16** (x16, 2026-10-26). Pinned: `test_menu_policy.ReReadPrice`.

**The table regenerates itself** — scheduled task **"HAI P34 menu refresh"** on Softserve (every 15 min, AtStartup +
repetition, IgnoreNew, RestartCount 3, S4U; installer `hard-bench\register_menu_refresh_task.ps1`, run via
`ssh softserve`). `menu_refresh.py` fingerprints what the menu reads (delivered results under `campaigns\` and
`outbox\`, the payloads, the hold/void files, role summaries, chat1 standings; in-flight progress and partial files
excluded) and, only when it changed, runs the documented chain `build_menu.py` → `menu_standings.py` →
`menu_policy.py` (≈15 s with the grade cache warm). Spock re-reads `policy.json` on its mtime. A failed step leaves the
old table in force and does not advance the fingerprint, so the next run retries; `menu_refresh.log` names the step.

```bash
python menu_refresh.py --force
```

**Where the menu finds the lab (2026-11-18, the v0 publish review).** Every folder, mirror, box name and box table the
shipped menu files read goes through `hard-bench\menu_paths.py`: the Lab tools from the machine environment variable
`LAB_TOOLS_DIR` (`E:\SSDShared\Lab\bin` on Softserve; a checkout's `tools` sibling otherwise), then `lab_settings` for
`TABLET_JOBS_HOST` (the tablet desk's share as this box sees it; campaigns, outbox, inbox, payloads and chat-windows
hang off it), `TABLET_JOBS_SEEN` (the same share as Tolkien's container sees it), `MENU_DIR`, `GAUNTLET_CSV`,
`DOCS_STATE_LOCAL` (the library's State folder by a path every identity sees; the S4U task cannot see a mapped drive),
`VLM_HARD_SET_DIR`, `TABLET_HEAD_NAME`, `TABLET_HEAD_STACK_DIR`, `CODE_MIRRORS`. The box tables (each box's tested
contexts, bench standard, shape, tier, the champion, the fill and standings orders, the tablet classes, the tiers'
homes and overflow) are `hard-bench\boxes.json` (`MENU_BOXES_FILE` to point elsewhere); a new fit probe result is
recorded there, never in `menu_fill.py`. The wall's `WALL_BOXES` setting names how each box is read (`parse`: `ups`
or `telemetry`, `local`, `ups_w_per_pct`, `subtract_tablet_head`). `python menu_paths.py` prints what resolves.
A menu run with a setting missing refuses with its name (`menu_paths.need`).

Run from `hard-bench` to rebuild now. State: `Datasets\Project34-Menu\refresh-state.json`. The task's own output:
`hard-bench\menu_refresh_task.log`.

### The model matrix — every model on every box, by quality, speed and cost (`model-matrix.csv`, `model-matrix.md`)

**Where:** `Datasets\Project34-Menu\model-matrix.csv` (the point cloud: one row per measured config, 955 on 2026-10-30)
and `model-matrix.md` (the readable page: per model, per box and job, the winning config on each lens). the owner's picture
(2026-10-30): *"every model on every winning config that's best for each box has a place for quality, speed, cost ... a
point cloud of dots for where each combo/config sits."* A dot is **box × job × config** (thinking, temperature, build);
a model has many dots. `standings.csv` is the raw per-setting table; `policy.json` holds, for Spock, **each model's own three lens winners
on each box**, the box's winner first (since 2026-11-07, the owner's picture above; until then it held only each box's winner, so a
model that lost every lens on its box, Flash-Next on Mothership's chat, never reached the weighing however hot it was); the
matrix is every config, with the same verdicts.

**Built by `hard-bench\menu_matrix.py`, kept current by `menu_refresh.py`** — not a chain step: after the chain, the
matrix runs whenever its output is missing or older than `policy.json` or its own code; a failure is logged
(`FAILED at menu_matrix.py`), the run exits 1, and only the matrix retries (the placement table is never held up by it).
Rebuild by hand: `python menu_matrix.py` from `hard-bench` (≈15 s; `--out-dir DIR` writes elsewhere).

**One derivation, not a second one.** Accepted / refused and why comes from `menu_policy.classify` (stop verdicts, VOID,
oracle, never-solved, thinking-off-loses; `model-matrix.csv` column `why_not`); prices carry `menu_policy.entries`'
numbers plus the same stamps Spock's table has (`load_s`, `read_tok_s`, `order_overhead_s`, `j_order`, `first_out_s`);
ranks use `menu_policy.LENS_KEYS`, the very keys that pick each box's winner. `test_menu_matrix.rank_one_is_the_policy_winner`
pins that the config ranked 1 on a lens for a box IS the row `policy.json` holds there; a live check on 2026-10-30
matched 94 of 94 policy winners and every family's refused count.

**Columns that matter:** `quality` (success) and `quality_low` (95% Wilson lower bound, the lab's quality, checklist B4);
`s_per_solve` (speed); `j_per_solve` (cost, electricity) with `j_source` measured / estimated (rigby at its placement
watts) / blank; `rank_quality`, `rank_speed`, `rank_cost` with their pool sizes `of_*` (the fastest lens ranks only
configs inside the interactive line, the cost lens only configs with a joules figure); `model_best_*` = this config is
the model's best on that lens for that box and job (the "winning config" view); `pareto` = nothing else on that box and
job is at least as good on quality, speed and cost and better on one. **No combined score exists on purpose** (checklist
A2: exactly three lenses, weighed together per request by Spock and McCoy, never a fourth, never a merged number).

**Traps:** quality is only comparable within a job (each job has its own test and many settings sit at 100%). Model
identity is by NAME, through the `IDENTITY` table in `menu_matrix.py`: the same model is an ollama tag on a GPU box and a
`.gguf` file on rigby, and the Fellowship's rows carry the tablet class and a warm flag inside the name (split into
`tablet_class` / `warm`). **When a new model joins the menu, add its name to `IDENTITY`**; until then it shows under its
raw name with build `unmapped` and the page header and the run's log line list it (the refresh is not stopped, and
`test_menu_matrix.test_every_model_string_in_the_live_standings_is_mapped` fails). Not in the matrix: the embed family and
the A1 role/lead rows (both only in `policy.json`).

**Current limit — the Fellowship tier is not a placed destination yet.** `scheduler.place()` ranks the GPU cards (and
tablets only for CPU work), so a placed order with `tier: "fellowship"` cannot land on rigby or the tablets: it is
placed on that tier's OVERFLOW rows (an 8 GB box or Mothership, each with its caveat). The home rows inform but are
not chosen. Fellowship pieces still go as Tolkien `llm` orders (`a1_delegate.RigbyDelegator`, pinned to
rigby's lane). Build plan D in [[34-Phase-7-Placement-Policy-Design]].

### ⛔ ADDING A NEW ORDER/PAYLOAD KIND — walk the FACTORY-CONTRACT CHECKLIST, every row

The factory's contracts were each written against the payload kinds that existed at the time, and
they hold for those kinds *structurally* (renders ship continuously; trainers checkpoint; cache
builders bank shards that a same-order requeue consumes). **A new payload family inherits every
contract and satisfies none of them automatically** — and a contract that silently doesn't apply
fails exactly like one that doesn't exist. 2026-08-26 produced two live instances in one day, both
on the NI reviewer (the first assembled-deliverable payload): the desk dealt it size-blind, and a
stop taskkilled it before its "ships what exists" assembly step could run.

When a new order kind / payload family is added, walk each row and name where it's satisfied:

| contract | the question to answer for THIS payload |
|---|---|
| **desk dealing** | is its basename + measured prompt-reserve in the `compose_orders` size-reserve registry, with a `SizeAwareDealing` pin? are its per-row rates recorded once observed? |
| **stop → ships what exists** | when the beat-thread taskkills it mid-run, is everything it produced consumable afterwards? (an assembled-deliverable payload needs an explicit ship-from-checkpoint path — `--ship-checkpoint` in `native_history_review.py` is the reference shape) |
| **progress** | does it print `[tag] N/M ` lines (or declare `progress_glob`/`progress_total`) so the tile + `watch.compute` can see it? |
| **retirement / cleanup** | does every artifact it writes beside the spec (state, log, items, sidecars of its own) leave the queue with the order? |
| **capability declaration** | do the boxes that can run it declare the capability it `requires`, and does the desk refuse-by-name when none do? |
| **host-priority yielding** | if it can land on a host with a PRIORITY CONSUMER (Softserve→Plex; any box→a person on the card), yielding is FACTORY machinery, built 2026-09-28: the factory checks `/fleet/availability` before launch and every ~60 s while a compute order runs, and on Plex transcoding (Softserve, every order kind, CPU included) ends the child and hands the order back or, if pinned, keeps it queued (§"PLEX ON SOFTSERVE" above). **The payload's obligation is to RESUME**: a restarted order lands in the same `{ORDER_OUT}` and must skip what it already finished (`infer_ollama`'s `out.json.partial.jsonl` is the reference shape), or every yield redoes the whole job. **Cooperative, not exclusionary** (the owner, 2026-08-26: *"I want softserve to be IN if available… I don't want to shunt things off from it if it can pitch in, that's the point of the lab"*): do NOT exclude Softserve from parallel deals. Still open: a person arriving MID-run on a card (start-time only today). |

The checklist is cheap (minutes per row); each skipped row has cost a real incident.

**Worked answer — `infer_ollama.py` (inference as an order; `mothership-build\`, deployed to every GPU
foundry's `bin`; consumers: `eval_campaign`, `hard_bench_fleet_orders.py`), walked 2026-09-27:**

| contract | where it is satisfied |
|---|---|
| desk dealing | `SIZE_RESERVES["infer_ollama"] = 1200` (PROVISIONAL, not yet measured) + `test_scheduler` `test_infer_ollama_is_dealt_size_aware`. `eval_campaign` does not use parallel deals (one order per ≤25-pin batch). Per-row rates: not recorded yet — record from the first campaigns' `wall_s`. |
| stop → ships what exists | every row is appended to `<out>.partial.jsonl` as it lands; a stop leaves those rows but NO `out.json` (no ship-from-checkpoint step). `eval_campaign` then fails the campaign and names the partial file — partials are evidence, not a product. |
| progress | prints `[infer] N/M …` (parsed by `COMPUTE_PROG_RE`) AND writes `<out-stem>.progress\<id>.r<round>.done`; eval orders also declare `progress_glob`/`progress_total`. |
| retirement / cleanup | items/out live under `tabfoundry-jobs\campaigns\eval\<campaign_id>\` — deliverables, kept by design; the `.progress\` markers and `.partial.jsonl` are byproducts nothing removes yet (small; left beside the product they explain). |
| capability declaration | `requires ["order.compute", "model.<name>"]`; each factory declares `model.<name>` live from its own `/api/tags`; no declaring factory → refused by name at the desk (and at `eval_campaign` intake as `capability_gap`). |
| host-priority yielding | the payload asserts full residency and exits 2 on a CPU spill, which the foundry HOLDS and retries (it does not fail the order). Exception since 2026-09-29: an order carrying **`--offload-ok`** (a model larger than the card, e.g. Qwen3.8-Flash-Next) runs as placed after the clean load and records `placement {size_vram, size, offload}` in `out.json`; nothing-on-the-card is still refused. The menu filler adds it only for models named in `menu_fill.OFFLOAD_OK` ([[Qwen3.8-Flash-Next-on-Mothership]]). Yield on Plex demand is the factory's (mid-run beat check); on restart the payload resumes from `out.json.partial.jsonl`, re-running only errored or missing rows. Size `--num-ctx` to the JOB, not to chat: a single-image check needs ~1,800 tokens, and a chat-sized 8192 does not fit `qwen3-vl:8b` on an 8 GB card. |
* **A busy capable factory is a valid placement** — its queue holds the order (WAIT, not a
  capability gap). Refusal is reserved for orders no factory can build.

**Worked answer — `helper_window.py` (one helper order per lead run, items answered as they appear; § "HELPER WINDOW
ORDERS"), walked 2026-10-06:**

| contract | where it is satisfied |
|---|---|
| desk dealing | always `only`-pinned to the helper box by its client (`a1_delegate.WindowDelegator`), so no deal happens and no size reserve applies; the spec carries no `items`. Not in `SIZE_RESERVES` — an UNPINNED window would deal size-blind, so pin it (stated in the client's docstring). Per-item rates: the out rows' `wall_s`, first window arm 2026-10-06 (`…-must-win`). |
| stop → ships what exists | every answered item is written beside its item file on the share the moment it lands (the lead already has it) AND appended to `<out>.partial.jsonl`; a stop leaves those, no `out.json`. |
| progress | `{ORDER_OUT}\out.progress\<id>.done` per item + `progress_glob` in the spec (no `progress_total`: items are not known ahead); prints `[window] N <id> …` per item and `[window] closed (<why>)`. |
| retirement / cleanup | `{ORDER_OUT}` (partial, progress markers, out.json) ships with the order; the items dir `workspace\a1\windows\<window>\` is a byproduct kept beside the lead's results (briefs and answers, read by `delegation_corpus.py`). |
| capability declaration | `requires ["order.compute", "model.<helper model>"]`, declared live by each factory; refused by name at the desk when none holds the model (the client records `refused` and fails every piece fast). |
| host-priority yielding | the factory may end the worker on Plex demand (Softserve) and restart it in the same `{ORDER_OUT}`: the worker RESUMES — rows in the partial file are kept, items with an out file are not re-asked, only new items are answered (`test_a_restarted_window_keeps_what_it_answered`). Residency: `infer_ollama._assert_resident` once at start, rc 2 → the foundry holds and retries; the lead keeps polling the window's status meanwhile. |

### 📝 MENU GRADING — grade the Project 34 menu's coding answers on ANY of the four factories (`grade_jobs.py`)

The menu build (`hard-bench\build_menu.py`) grades every saved coding answer with ONE instrument,
`E:\SSDShared\mothership-build\grade_jobs.py` (fence-tolerant extraction + the payload's own checks). Grades are
cached in `hard-bench\grade_cache.jsonl`, keyed by payload sha + grader fingerprint + task + answer sha, so a rebuild
only grades NEW answers, and a change to the scoring logic re-keys everything (nothing stale is reused).

```bash
python build_menu.py                          # rebuild; new answers graded one at a time (fine for a few hundred)
python build_menu.py --workers 8              # grade the new answers on 8 local processes first (0 = every core)
python grade_order.py submit                  # thousands of new answers: a compute order, Mothership, every core
python grade_order.py submit --factory tolkien    # ... or a Tolkien map order across the Fellowship
python grade_order.py status                  # the order's tile line + whether the grades are delivered
python grade_order.py import                  # merge the delivered grades into the cache and build the menu
```

Every box proves itself before grading: `score()` runs answers with `python3` from PATH, and a box where that is not
a real interpreter grades everything "fail" with no error. So each run first grades the scorers' own `control_add` on
a known-right and a known-wrong answer (refuse / error rows if wrong), the GPU order re-grades 8 answers already graded
elsewhere (refuse on any disagreement), and a Tolkien order carries a control item that must read `GRADE_CONTROL_OK`;
the importer refuses a whole results file with any control wrong or any self-test failure.
**Proven 2026-10-02 on 60 real answers:** Softserve serial = Softserve 6-process (0 differences); Mothership 52 in 2 s
on 16 workers (0 differences, 8/8 controls); the Fellowship 53 rows across 7 tablets in 6.4 s (0 differences,
control OK). Build progress: one line per grade (bar, grade x/y, cached vs new, elapsed, ETA) to a timestamped log.

**Factory-contract checklist (§ above), `grade_jobs.py`:** desk dealing — not dealt in parallel (one pinned GPU order;
Tolkien pulls its own batches), no size reserve needed · stop → ships what exists — GPU mode flushes each grade to
`grades.jsonl` in `{ORDER_OUT}`, Tolkien checkpoints rows per batch; both import · progress — `[grade] N/M` ·
cleanup — jobs/items files stay in `tabfoundry-jobs\campaigns\menu-grading\` beside the grades they produced ·
capability — GPU `order.compute` (every GPU factory), Tolkien kind `map` (`support_files` stages the scorers) ·
yielding — a restarted GPU order resumes from its `grades.jsonl`.

### 🏋 SUBMIT A TRAINING ORDER (LoRA) — `mode:"train"`, pinning a factory, and HAI's `queue_training`

**A training run is an ORDER like any other.** There is no separate training pipeline and nothing to
launch: submit to Starfleet Command with `requires: ["train"]` and it passes the order to a
factory's own order taker, exactly as it does for renders and compute.

```bash
curl -s -X POST http://<sundae-address>:8099/p20/order -H 'Content-Type: application/json' -d '{
  "spec": {"name": "subject_flux2_v1", "mode": "train",
           "launcher": "F:\\ai-toolkit\\run_subject.bat",
           "log": "F:\\foundry\\var\\log\\subject.log",
           "output_dir": "F:\\foundry\\var\\work\\subject",
           "total_steps": 3000, "preview_every": 250, "initiator": "direct"},
  "requires": ["train"]}'
```

* ⚠ **`mode: "train"` must be IN THE SPEC, not only in `requires`.** `scheduler.kind_for_order`
  resolves a spec with no mode to the **caption** band, so the moment that order is requeued,
  archived-and-restored, or read by anything not carrying the same `requires`, a multi-hour training
  run is sized for a captioner's VRAM envelope. A test asserting `kind_for_order(spec, ["train"])`
  cannot catch this — `requires` alone satisfies that branch. Ask what **the spec alone** resolves
  to (`kind_for_order(spec, [])`).
* **MANUAL CONTROL = `"only": ["mothership"]`** — a hard pin, validated against `scheduler.CARDS`
  (never a hostname list retyped by the caller, which is how a rename becomes a silent
  misplacement). Leave it off and Starfleet places it. An unknown factory is refused by name with
  nothing queued.
* **A BUSY CARD IS NOT A REFUSAL** (doctrine 9: a busy lane means WAIT). The desk returns `ok:true`
  with `why: "mothership (score 0.0) capable but busy - queued at the factory (WAIT, not a
  capability gap)"`. Only a genuine capability gap refuses.
* `initiator` is a **NOTIFICATION** field, not a routing one: `direct` DMs the owner on milestones,
  `hai` is silent and reports in HAI chat.
* ⚠ **The desk SUFFIXES the order name with its host** (`owner_krea2_v1` → `owner_krea2_v1_mothership`), but the
  trainer runs under `config.name` in its own yaml. Since 2026-10-26 the foundry takes the trainer's name from the
  config the order's launcher actually runs (`trainer_name(job)`), for process identity, checkpoints and the final
  file. Before that, every desk-placed training order launched a trainer the foundry could not see ("trainer
  process not visible 60s after launch") — found on the first Krea 2 order, which was aborted before it launched.
  `output_dir` must still be `<training_folder>\<config.name>`.
* **Krea 2 training (`base: "krea2"`)**: add `"base": "krea2"` and `requires: ["train", "train.krea2"]`; the
  launcher runs `D:\ai-toolkit-krea2\venv\python.exe -u run.py <config>` (the Krea-ready ai-toolkit beside the FLUX.2
  one, STOP_NOW patch applied). Its checkpoints render through `p20_krea_render`, no Mistral bake. Worked example:
  `D:\krea2-train\config\owner_krea2_v1.yaml` + `run_owner_krea2_v1.bat`.
* **A Krea 2 resume is BYTE-IDENTICAL to never having stopped** (proven 2026-10-26; the owner: *"the same level of
  scrutiny as we did with text LLMs, where the resumed output is byte identical"*). `D:\krea2-train\setup\
  p20_apply_full_resume.py` (applied to `D:\ai-toolkit-krea2`, marker `[P20-RESUME]`, idempotent, upgrades an older
  copy in place) adds what upstream ai-toolkit lacks: at every save a `p20_resume_<step>.pt` sidecar beside the
  checkpoint (the adapter at its TRAINING precision — upstream saves float16 while it trains float32 — every RNG, the
  data position, and `next_step`); on resume it loads those and starts at `next_step`; `P20_TRAIN_SEED` seeds a run.
  **The config needs `num_workers: 0`** in its dataset (persistent workers draw their seed at a different moment in a
  resumed process). The log says which kind of resume happened: `[P20-RESUME] full state loaded …` (byte-identical) or
  `no full-state sidecar … NOT byte-identical` (a checkpoint from before the patch). **The trainer ends its own process
  the moment the job returns** — `D:\krea2-train\setup\p20_apply_hard_exit.py` (applied to `D:\ai-toolkit-krea2\
  run.py`, marker `[P20-EXIT]`, backup `run.py.p20bak-exit`, `--revert`; tests `test_hard_exit.py` beside it): the log's
  last line is `[P20-EXIT] job returned (exit code N) - ending the process now`. Why: § "FOUNDRY HELD", item 2.
* **KREA START-UP: point configs at `F:\krea2-models` and set `P20_TE_LAZY=1` in the launcher** (2026-10-27; start
  273 → 163 s, −40 %, on every launch and every relaunch after a preview pause). Measured start of a D:-based run:
  imports 30 s · model state dict 141 s · text encoder + VAE 47 s · first step 17 s. Two levers:
  - `F:\krea2-models` = verified NVMe copies (sha256, 15 files) of the model, `Qwen3-VL-4B-Instruct` and `Qwen-Image`
    (the D: originals under `D:\krea2-train\models` / `hf` stay). Set `name_or_path`, `text_encoder_path`, `vae_path`.
  - `[P20-TECACHE]` (`D:\krea2-train\setup\p20_apply_te_cache.py`, opt-in by env): the 8.9 GB encoder is not loaded at
    start; the trainer's static prompts (blank / unconditional / trigger) come from a content-addressed cache
    (`P20_TE_CACHE_DIR`, default beside the encoder: `F:\krea2-models\p20_te_static_cache`; key = the encoder files'
    names/sizes/mtimes + length + layers + dtype + prompt, re-checked inside each entry); anything uncached (a new
    trigger, a caption whose per-item cache is missing) loads the real encoder on demand — the log says which. The
    FLUX version's failure (one file keyed by trigger, crash when captions changed) cannot happen here.
  - **Proof** (`kt_a..d`, Avery recipe, seed 1234, 6 steps): all 512 adapter tensors, the optimizer and the full
    resume state byte-identical to the eager D: control (only the `name` metadata differs); start-up 273 (eager) ·
    273 (lazy, cache cold: encoder loaded on demand 46 s) · 228 (cache warm) · 163 s (warm + F:).
  - ⚠ Not in Defender's exclusion list (2026-10-27): `F:\krea2-models`, `D:\krea2-train`, `D:\ai-toolkit-krea2` — real-
    time scanning reads every byte of the model at load (the fleet standard excludes model/work folders:
    RUNBOOK-GPU-Host-Power-and-Glances § Defender). the owner's to apply; Claude does not change security settings.
  - **Proof** (`D:\krea2-train\proof`, configs `kp_*` in `D:\krea2-train\config`, seed 1234, 40 steps): two
    uninterrupted runs are byte-identical to each other (768 state tensors, ROCm is deterministic here), and a run
    stopped at 21 and resumed matches both — every tensor, every RNG, the data position, and the shipped float16
    `.safetensors` (512 tensors; only the name metadata differs). Instruments: `Claude Home` scratchpad
    `compare_proof.py`, `compare_shipped.py`.
  - ⚠ **A checkpoint's step label means two things** — a save inside the loop (and STOP_NOW's) is labelled with the
    step index it just trained (resume at N+1); the save after a run that ran to its end is labelled with the step
    COUNT (resume at N). The first version always resumed at N+1 and the proof caught it: the resumed run made 39
    updates against 40, every adapter tensor off by up to 1.9e-4, while the step-20 states were identical. The
    sidecar now records `next_step` when it is written.

**HAI's admin-only `queue_training` tool submits exactly this** (default Starfleet; optional `host`
argument pins hard). It never launches a foundry process itself — a foundry process started against
a spec file submits an order to nobody: no queue, no lane claim, no control slips, no retirement,
no archive. That invariant is pinned as behaviour, not as a path assertion
(`TheBypassIsGone.test_never_launches_a_foundry_process`).
Tests: `orchestrator-build/tests/test_queue_training_order.py` (15, red-proofed).

### 📝 LEAVE WORK FOR HAI TO PICK UP — `propose_work`, the proposals store, and why nothing self-arms

**Ask HAI in chat.** *"prep the changes you'd need for X"*, *"add that to your backlog"*, *"propose
a fix for later"*, *"what work is queued up for you?"* — all land on the **`propose_work`** tool
(`action: list | propose`). Retrieval is measured, not assumed: all five phrasings surface it on the
narrow-context brain (14–22 tools offered of 100) while an unrelated request does not.

**ADMIN ONLY, and mechanically unable to arm anything.** Three independent locks, none of which is
a prompt instruction:

| lock | where | what it stops |
|---|---|---|
| `approved_overnight` forced `False` | `backlog.propose()` | a proposal filing itself armed |
| the approved backlog is a **`:ro` bind mount** | `/mnt/workspace/hai-self-engineering:/selfeng:ro` — verified live, `rw=false` | HAI's container writing the approval source **at all** |
| `SANDBOX_BLOCKED_TOOLS` | `tools.py` | an **unattended overnight run** filing proposals into its own future queue — the self-feeding loop `feedback-backlog-deferral-doctrine` forbids. This one is DOCTRINE, not resources |

**Two files, two different owners — do not conflate them:**

* `proposals.yaml` (**rw**) — what HAI suggests. Filing here commits nothing.
* `backlog.yaml` (**ro**) — what the owner has approved. `promote.py --approve` is how an item crosses
  over, and that is a human act. Eligibility reads **only** this file.

⚠ **This is HAI's OWN queue and is NEVER auto-fed from PROJECT-BACKLOG** — different
owners, different purposes (the owner, 2026-07-06). A human hand-copies an item across when it is
actually wanted.

⚠ **`propose()` REFUSES rather than fail-softing.** A fail-soft read that returns an empty backlog
on ANY error, followed by a whole-file write-back, silently **deletes every existing proposal** on
a single unparseable byte. `_load_for_write()` therefore raises on an unreadable file (an ABSENT file is
still a genuine fresh start — that half is pinned as a positive control, so the guard is not merely
"refuse everything"). **Generalise it: fail-soft read + whole-file write = silent data loss, and
the fail-soft half is invisible at the call site.**

**If the store ever looks wrong,** the tool says `Could not file the proposal (nothing was
written): …` and preserves the file. Read the parse error; do not "fix" it by deleting the store.

Code: `tools.py` `_propose_work` / `_PROPOSE_WORK`, `backlog.py` `propose()` / `_load_for_write()`.
Tests: `tests/test_propose_work.py` (17, red-proofed 11 RED / 6 GREEN against the pre-change image).
Live decoy 2026-08-16: filed a real proposal, all 3 existing proposals survived, restored
byte-identical (sha `7115f917…` in = out).

### P20 RENDER WORKER — what it is, why it must stay up, how it protects itself

**It is not just HAI's person-render worker. It is the ONLY writer of the stall daemon's mercy data.**
`p20_render_worker.py` runs `_external_render_watch()`, which attaches a websocket progress reporter
to renders it did NOT launch — i.e. the foundry's preview / ladder / probe renders, which go through
the same SLIM script under the same ComfyUI client-id. That reporter writes
`F:\foundry\var\state\telemetry\render_progress.json`, which is the **only** input to the daemon's
`render_nearly_done()` (spare anything past 70% instead of axing it).

**No worker ⇒ no progress file ⇒ `render_nearly_done()` returns False ⇒ "no mercy data = normal
policy" ⇒ the daemon kills the :8189 owner at ANY completion percentage.** Measured cost: ~2m40s
renders become ~10.5 min each under repeated kills.

**Protection (all verified live):**
| piece | detail |
|---|---|
| singleton | `acquire_singleton()` — a live worker makes a second start exit ("another render worker is live … so we do not double-poll"). Mirrors the rescue daemon's lock exactly. **Required** before the repeating trigger, or every repeat spawns a duplicate. |
| self-heal | task `P20 Render Worker` has **`AtStartup` + a 15-minute repeat** (StartBoundary deliberately in the PAST, `2020-01-01`, so the repetition starts immediately rather than waiting for a future clock time). Costs nothing when healthy — the singleton makes each repeat a no-op — and revives it within 15 min when dead. |
| liveness | `render_worker.heartbeat` (ts + pid), beaten from the **watch thread every ~5 s**, deliberately NOT from the main loop — the main loop blocks inside `run_job()` for the whole length of a person render, so a main-loop heartbeat goes stale during healthy work and becomes noise. It is also the correct semantics: the heartbeat answers "can this worker publish render progress?", and that capability IS that thread. |
| announcement | the foundry's PREFLIGHT now logs `render worker: OK (heartbeat Ns ago) - mercy rule has data`, or a WARNING naming the consequence and the restart command. |
| clean detach | the progress file is REMOVED on detach — left in place it reads `100%` while the NEXT render is still model-loading, letting the daemon spare a brand-new render on the previous one's data. A knowingly-wrong value has no business in a safety path, staleness guard or not. |

**Check it:** `Get-ScheduledTask -TaskName 'P20 Render Worker' | Get-ScheduledTaskInfo` →
`NextRunTime` should be ≤15 min out. Heartbeat should be <120 s old. Or just start any order and
read the PREFLIGHT line.
**Restart:** `schtasks /run /tn "P20 Render Worker"` (safe at any time — the singleton refuses a
duplicate, so it only STARTS a dead worker). **To put NEW CODE into a live worker:** `fleet_deploy.py
p20_render_worker.py` — it asks the worker to exit between jobs (`RENDER_WORKER_RESTART`) and relaunches it; see
[[RUNBOOK-Orchestrator-Deploy-Procedure]] § "THE RENDER WORKER IS RESTARTED TOO".
⚠ **A dead worker announces nothing by itself** — the heartbeat and the PREFLIGHT line exist to
say so. ⚠ Do NOT add `p20_render_worker` to a "is GPU work in progress"
process filter — it is now permanently resident, so that would abort every foundry deploy forever.
What blocks a deploy is a live TRAINER or an in-flight SLIM render.

### STOP THE ORDER THAT IS RUNNING RIGHT NOW — cancel a live probe/render

**Do not reach for the factory-wide pause to stop ONE bad order.** Three controls, three scopes:

| you want | drop this | scope |
|---|---|---|
| stop the order running NOW | `F:\foundry\jobs\queue\<order>.stop.json` = `{"target":"<order>"}` | THAT order only; the queue behind it carries on |
| pull an order that has NOT started | `<any>.ctl.json` = `{"action":"cancel","order":"<name>"}` | one queued order; a desk order is also closed AT THE DESK as status `cancelled` (error text "cancelled on &lt;box&gt; by operator slip: &lt;why&gt;"; chained successors still hold, because `cancelled` is a dead status exactly like `failed`) — closed at the desk since 2026-09-29 (before which its desk record stayed `claimed` forever), recorded as `cancelled` and not `failed` since 2026-10-02. The desk's result endpoint (`POST /p20/jobs/<id>/result?status=done\|failed\|cancelled`) maps ONE way via `p20_queue.is_operator_cancel`: an explicit `cancelled`, or the legacy `failed` + "cancelled on … by operator slip" that a factory not yet restarted on the new `p20_foundry.py` still sends. `stopped by operator slip` (the stop slip) is a different fate and stays `failed` |
| cancel a DESK order from software, any box, no ssh | `POST http://<sundae-address>:8099/p20/order/cancel` = `{"id":"<desk id>","why":"…","source":"…"}` | one order by its desk record (`Z:\Documentation\State\p20-render-queue\<id>.json`): `pending` or `held` (not yet collected — `held` since 2026-10-21, before which it returned `ok:false` "no host" and the order stayed held, then ran when placed) → cancelled at the desk; `claimed` → one factory `cancel` control the box collects (cannot stop a running order); finished → left alone. Built 2026-10-02 for the #34 menu filler's stop rule ([[34-Hard-Bench-Order-Spec]]) |
| stop the whole factory | `F:\foundry\var\state\STOP_AFTER_RENDER` (Softserve: `E:\foundry\…`) | EVERY order. A render/training order stays QUEUED and runs again on resume; a **compute order runs to its end first** (the flag is read between orders). **One-shot:** the runner consumes the flag on exit, and the runner task re-fires every 15 min (Mothership too — "P20 Foundry Runner", 2026-10-26) — to stay paused see §"PAUSE A FACTORY UNTIL A TIME" below. ⚠ A `{"action":"pause"}` **ctl slip** only SETS this flag when the order taker reads slips — which is BETWEEN orders (`run_queue`'s loop) — so it cannot pause a training that is running now; to set a running training aside (e.g. to front-load other orders), write the flag itself |

**A stop slip on a live HAI chat window does not kill it (since 2026-11-05).** An order whose args carry `--live`
(`helper_window`, a chat window) gets a `YIELD` marker in its items folder instead (`p20_foundry.yield_live_window`): the
turn in flight finishes, the chat's state is saved under its own file, and the window exits, so nothing is cut. A slow answer
can take minutes to reach that point; the log says `live window: asked to finish its turn, save and exit`. **A SECOND stop
slip ends it now, the old way.** Every other order stops as before. Requeueing a window that already yielded is harmless: it
reads its own CLOSE and does not start an engine.

⚠ **THE STOP SLIP MUST BE VALID JSON — an empty file is silently IGNORED.** `stop_order()` does
`json.load()` on every `*.stop.json`; an empty or malformed file raises, is caught as "unreadable
stop order — ignoring it", and the order keeps running (verified 2026-08-26: an empty slip sat unread
while a 1,117-row compute order ran on for 15 minutes). Write `{"target":"<order>"}` to stop that one
order, or `{}` to stop whatever is running now; optional `"why"` is logged, optional `"at_step":N`
(training only) waits for a step. Author it with a tool that writes real bytes, never `type nul >`.

The stop slip is honoured at the next boundary the run already checks: a render/train run at its
render boundary (the render in flight completes, so no work is wasted); a **compute order on the
beat thread's stop-poll, ~15 s** (`_beat()` in `run_compute_order`, which taskkills the payload tree
on a match). The order then retires and the lane clears — a compute stop taskkills the child, so the
queue record can read `failed` rather than `stopped`; the partial checkpoint is preserved either way.
Resume after a factory-wide pause: `schtasks /run /tn "<box> Foundry Runner"` — the task is **"P20 Foundry
Runner"** on Mothership, **"BH Foundry Runner"** on BigHonker, **"SS Foundry Runner"** on Softserve.

**Scope:** `*.stop.json` is read by BOTH the training watch and the render loop (at the same
boundary that honours the operator pause) — it stops any RUNNING order. The `cancel` slip is for
QUEUED orders only; a cancel read between orders cannot stop the one in flight (the owner: *"If cancel
orders are read between orders then that makes them entirely useless"* — hence the stop channel).

### "FOUNDRY HELD — a trainer will not stop" (`TrainerUnstoppable`) — and a trainer that survives every kill (2026-10-26)

**What the hold means:** the foundry dropped STOP_NOW and the trainer kept stepping past the allowance, so it holds the
order untouched and the runner exits (nothing launches onto a card it does not control). Two different causes seen on
2026-10-26, with different cures:

1. **The flag landed while the trainer was still BOOTING** (a stop slip arrived during latent caching). The trainer
   consumes STOP_NOW at its start step, where it cannot save, so the request is lost and it trains on (`kres_1024`:
   flag 19:05:55, steps 1–7 at ~80 s, held 19:16:20; a second stop request was honoured at step 9 — that one was the
   stall daemon's own "lossless recycle", 19:17:58, a false stall on a healthy Krea trainer, fixed in daemon v6.3:
   RUNBOOK-Foundry-and-Rescue-Daemons § "Krea 2 on the stall daemon"). Cure: the trainer
   honours a second request at a step boundary; then relaunch the runner (a deploy does it). Avoid it by not stopping
   a training order that has not reached step 1.
2. **The trainer saved its last step and then never exited — FIXED 2026-10-26 night (`[P20-EXIT]`).** Seen three
   times that day on Krea 2 (PID 2240 morning, 5512 at 19:21, 10360 at 20:36): one thread left, spinning a CPU core,
   ~80 MB working set; `Stop-Process` and an elevated SYSTEM `taskkill /F /T` both "succeed" and the process stays;
   the console showed an AMD **driver timeout**. Where it sat: in **interpreter shutdown**, after the job had returned
   — one thread left, and the patch's 120 s exit-watchdog thread never fired, because Python stops daemon threads
   when shutdown begins. The fix ends the process itself (TerminateProcess on its own handle) as soon as `main()`
   returns, so the GPU runtime's teardown never starts; exit codes are kept. Proof: three 6-step [512, 1024] orders
   (`kexit_1..3`, the shape that hung) each logged `[P20-EXIT] job returned (exit code 0)` the same second as their
   final save, no process left. **If it ever recurs (no `[P20-EXIT]` line, process alive after the final save):
   reboot Mothership** — `ssh mothership "shutdown /r /f /t 0"` worked with a hung trainer (2026-10-26 20:54, back in
   seconds; it cannot help a box whose ssh is already dead, PROJECT-BACKLOG 10-24); verify by `LastBootUpTime`; the
   order taker's scheduled task ("P20 Foundry Runner") starts it on its next repetition (21:00 after a 20:54 boot),
   and a CURRENT_RUN claim older than the boot reads as clear. Nothing is lost — the checkpoint and `p20_resume_<step>.pt` are written before the exit.

### DEPLOY WHILE AN ORDER RUNS — `fleet_deploy` pauses and redeploys by itself (since 2026-10-17)
An order in flight is never a reason to wait for a deploy (the owner: *"We have live deploy methods that are supposed to
allow for us to deploy anytime safely regardless of run status"*). Until 2026-10-17 the GPU `fleet_deploy` printed
`REFUSED - an order is IN FLIGHT` and sessions waited; now, for a restart deploy, it does steps 3–4 below itself: a stop
slip naming exactly that order (`fleet_deploy-<stamp>.stop.json`, `target` = the order; it keeps its answered rows), a
wait on the box's DECK until the order leaves the lane, the deploy, then — in a `finally`, so a failed deploy still
gives it back — a requeue slip for the order's newest archived generation (`<name>.job.json.<stamp>`). It prints
`pause-and-redeploy: stop it, deploy, requeue it` and the steps. Not requeued: an order whose own customer had already
dropped a stop slip for it. A stop the factory does not honour within `WAIT_STOP_S` (1800 s, Tolkien's bound) deploys
nothing and requeues nothing (`DEFERRED`). Tolkien's deploy always worked this way.
**A deploy's stop is a PAUSE: the desk hears no verdict for it, on all four factories (since 2026-11-07).** Before, the
stop told the desk `failed: stopped by operator slip` (55 GPU desk records carried it) until the requeued run reported, so
a Kirk caller waiting on the order's status (a render, a Fellowship helper turn) read a deploy as a failure, and the
desk's rule blocked any chained successor. Now the deploy's stop slip carries `"pause": true` exactly when the deploy will
requeue the order: not one its customer had already stopped, and never a chat window, which Kirk reopens on the verdict.
The factory then sends no verdict for an order every one of whose stop slips was a pause, and marks it reported so its
order taker cannot call it done; the requeued run's own end reports. A customer's stop still reports failed. GPU:
`fleet_deploy` REMOTE `stop` + `p20_foundry.report_stopped` (foundry `667f2d0`). Tolkien: `fleet_deploy_tolkien.comes_back`
(one derivation with `to_requeue`) + `tablet_runner.retire_order` (Tolkien `6879d84`, tool `4b543cf`); rigby's reservation
stop is a pause too. **Proven by live decoys 2026-11-07:** Tolkien 21:17, two in-flight orders stopped, zero results at Kirk
(the deploy before it, on the old code, sent three "failed"); Mothership 21:39, the `sleep_decoy.py` compute order
(`workspace\decoy`) stopped mid-run, requeued, desk record still `claimed`, no result POST. **Proven live 2026-10-17
10:37 CT** on Mothership's running `cascade-lead-checks-…-sb4` order (two quick BigHonker decoys finished before a
deploy could reach them — a decoy must outlast the deploy's lane check): `REFUSED … IN FLIGHT … - pause-and-redeploy`
→ `STOP SLIP fleet_deploy-20261017-103715.stop.json` → the order log `STOP slip honoured … partial output shipped` →
deploy → `REQUEUE SLIP … (from ….job.json.20261017-103729)`, the generation the stop had just archived; tests:
`test_fleet_deploy.GpuPauseAndRedeploy` (7).
**⚠ The stop also PAUSES THE LANE first (since 2026-10-18).** BigHonker 07:12: the stop ended one long A3B cell, the
order taker picked up the NEXT queued order before the deploy wrote its own pause slip, and the deploy then saw an order
in flight and refused — one cell interrupted, nothing deployed. The stop step now writes `zz_fleet_deploy_pause.ctl.json`
(the same slip the restart writes, one name: `PAUSE_SLIP`) before the stop slip, so the runner exits instead of starting
the next order; the deploy relaunches it. Test `test_the_stop_pauses_the_lane_first_so_the_next_order_never_starts`;
proven on the retry (07:15, BigHonker `OK - deployed and running new code`). ⚠ **Never wrap `fleet_deploy` in a short
`timeout` (2026-10-26):** pause-and-redeploy waits on the deck for up to `WAIT_STOP_S` (1800 s), so `timeout 300 python
fleet_deploy.py …` killed it mid-wait — stop slip and `zz_fleet_deploy_pause.ctl.json` already written, nothing deployed,
no requeue in its `finally` (a killed process runs no `finally`), and the log empty because output was buffered. Run it
in the background with `python -u` and no timeout; to recover from an interrupted one, run the SAME deploy again — it
re-takes the documented path (re-stop, wait, deploy, requeue). A requeue slip is applied BETWEEN orders —
read the queue back before saying an interrupted order is back.

### RUN A MODEL THAT IS SERVED BY STRATA, NOT OLLAMA — `infer_ollama --strata <plan>` (menu cells, since 2026-10-18)
Flash-Next (#34 Phase 2) is served by Strata (its own OpenAI-compatible server), which the box's ollama does not list.
A menu cell for it is an ordinary `infer_ollama` order with `--strata <serving plan>`: the order starts Strata through
`workspace\flashnext\strata_arm.py`'s one lifecycle (prepare → enter = unload ollama + frontier mode → launch), checks
`/props` n_ctx == `--num-ctx` (else rc 2, held), answers over the openai wire (`infer_ollama._chat_openai` — the same
copy the A1 lead's calls use via `lab_provider.openai_chat`), and stops Strata by any route. The plan's vendor sampler
lies under the order's own temperature/seed (as an ollama tag's defaults would); ollama's JSON-schema `format` becomes
`response_format` — Strata VALIDATES after writing where ollama CONSTRAINS (its `/props` says so), measured as served.
Since 2026-10-18 ~12:40 Mothership DECLARES `model.qwen3.8-flash-next-strata-q2_0` (§ "How the desk picks factories",
`strata_served.json`), so an order may route by `requires: ["model.qwen3.8-flash-next-strata-q2_0"]`; the menu's
builders still pin (`--only-host` / `--host`), which also works. Model + plan are named in `menu_fill.STRATA`; current
serving plan `workspace\flashnext\plans\strata-serve-q2_0-v045.20261018.json` (Strata v0.1.45, on the box since 12:38). ⚠ The menu's on-delivery top-up orders a
model's missing cells with the menu's price lines unless the model is on the champion's terms
(`menu_fill.is_champion`: `CHAMPION` + `PRELIM_CHAMPION`, both from the menu's `boxes.json`).

### PAUSE A FACTORY UNTIL A TIME (overnight, heat, a person on the box) — stop now, keep the answers, resume by itself
Done 2026-09-29 23:02 on BigHonker and Softserve (the owner: *"pause them now please, they can checkpoint the orders"*,
until 08:00 — BigHonker's room runs warm). The pause flag alone is NOT enough: it waits for the running compute
order to finish, and the runner task (repetition every 15 min, IgnoreNew) restarts the runner after it exits.
Per box, in this order:
1. **Flag + disable the task** — `STOP_AFTER_RENDER` in the state dir, then `Disable-ScheduledTask '<box> Foundry
   Runner'` (a running instance is not touched; the disabled task cannot re-fire). Script:
   `…\Claude Home` scratchpad `overnight_pause.py` (env `RUNNER`, `STATE`), run via `ssh <box> "python.exe -"`.
2. **Resume task** — a one-shot SYSTEM task (`HAI resume foundry <date>`) at the resume time runs
   `C:\ProgramData\HAI\resume_foundry_<date>.ps1` = `Enable-ScheduledTask` + `Start-ScheduledTask` for the runner.
   Write the .ps1 FILE and point the task at it: the task name has spaces, and an inline `-Command` with nested
   quotes failed to register (PowerShell ParameterBindingException, nothing half-registered).
3. **To stop NOW instead of at the order's end:** a `{}` stop slip (`<any>.stop.json` = `{"why": "…"}`) — the
   compute order ends within ~15 s, its answered rows are kept (`out.json.partial.jsonl`, shipped to `deliver_to`),
   the runner then reads the flag between orders and exits (`OPERATOR PAUSE (flag between orders)`).
4. **Requeue slip for the stopped order** — `<any>.ctl.json` = `{"action":"requeue","order":"<name as archived>"}`,
   dropped AFTER the stop has retired the order (the requeue restores the newest archived spec; before retirement it
   finds nothing). It sits unread until the runner starts; the order then resumes and `infer_ollama` skips every
   answered row. A stop is not a cancel — no `.cancelled` tombstone, so the requeue is not refused. Take the exact
   name from the box's `jobs\archive` (Softserve archives WITHOUT the `_softserve` suffix the deck shows).
5. **Menu filler hold** that lapses by itself: `menu_fill_hold.json` entry `{"why": …, "until": "YYYY-MM-DDTHH:MM"}`
   (also one arm: `"<box>/<model>/<family>/<thinking>"` — [[34-Hard-Bench-Order-Spec]] § "Holds").

### HOLD A BOX OUT OF PLACEMENT (operator hold — bad RAM, a driver, a reseat): chat, embeds and orders go elsewhere
Different from pausing its factory: a paused factory leaves Kirk's chat-window orders to that box waiting in line, so
users wait; a HOLD makes Spock and every Kirk-side caller skip the card and serve from the other boxes. It is a `gpu`
claim whose holder starts with `scheduler.OPERATOR_HOLD` (`"operator:"`): `scheduler.claim(<box>, "gpu",
"operator: <why>", ttl=<seconds>)`, written with the orchestrator mirror's own module against the live claim dir
(`HAI_STATE_DIR=Z:\Documentation\State`). Script: Claude scratchpad `hold_mothership.py` (`release` to lift it).
Verify from the object in force: `docker exec orchestrator python -c "import scheduler; print(scheduler.active_claim(
'mothership','gpu'))"`. ⚠ **Before 2026-10-25 the box's own idle run-state push released ANY gpu claim within a
minute** — an operator hold placed while Mothership's RAM corrupted reads vanished at the next push; since then the
idle report leaves an `operator:` claim alone (`tests/test_fleet_run_state.py`). Lapses by its ttl, or release it.

⚠ **Slips dropped on a PAUSED factory sit unread** (no runner process, task disabled) and are all read at the TOP of the
runner's first loop pass after it starts — oldest first, before intake and before any order is picked — so a `cancel`
slip staged during a pause is applied before the order it names can start (verified in `run_queue` 2026-10-02: 150
cancel slips staged on BigHonker/Softserve for the #34 stop list). A stopped order's older `requeue` slip runs first and a
later `cancel` retires what it restored; each cancel also reports to the desk (one call per slip, so an unreachable desk
slows that first pass but never lets an order run).
Verify: the queue log shows the stop, then `OPERATOR PAUSE`; the task reads `Disabled`; the resume task's
`NextRunTime` is the resume time. Clean-up after it fires: the resume task and its .ps1 stay behind (harmless; a
past one-shot never re-fires) — delete them by name when convenient.

### Third-party tools as payloads — `run_tool.py` (the runner appends the order name to argv)
`run_compute_order` launches `[python_exe, -u, payload] + args + [job.name]` — the trailing order name marks the
process as the factory's own for the GPU police. Tools that parse their own arguments strictly
(`convert_hf_to_gguf.py`: "unrecognized arguments"; `llama-quantize.exe`) reject it. The shim
`F:\foundry\workspace\flashnext\run_tool.py` drops the last argv and runs the real tool: a `.py` target via
`runpy` (its folder on `sys.path`, `__name__ == "__main__"`), a `.exe` target via `subprocess`. Order shape:
`"payload": "{FOUNDRY_ROOT}\\workspace\\flashnext\\run_tool.py", "args": ["<tool>", "<tool args…>"]`, plus
`"python_exe"` when the tool needs another interpreter (the converter runs on `F:\ai-toolkit\venv\python.exe`,
which has torch/transformers). Used for the Flash-Next conversion and quantize orders, 2026-09-29
([[Qwen3.8-Flash-Next-on-Mothership]]).

### LOCAL testing of a new model = orders at THAT box's factory desk, not the fleet desk (the owner, 2026-09-29)
*"Just submit these locally to the mothership factory… This is the local testing phase that we do before it's
supposed to be advertised to the fleet"* and *"skip the fleet desk and use the factory desk"*. This is the direct
route the "TWO ORDER-TAKING SPOTS" ruling reserves for the owner's say-so: write the spec with the Write tool, copy it
into the box's `jobs\queue` as `<name>.job.json.incoming`, then rename it to `<name>.job.json` (the order taker
watches the directory; a spec written in place is read half-formed). The `no_factory_floor.py` hook blocks the
copy unless the command STARTS with `owner_OK=1` — only when the owner has directed this route for this work. Keep such
orders out of `campaigns\hard-bench-*` (use their own campaign folder) so the menu ledger does not count them, and
hold the model out of the menu filler (`menu_fill_hold.json` key `"<box>/<model>"`).

**After a box REBOOTS mid-order (2026-10-07):** `CURRENT_RUN.json` still names the order that was running, and
`fleet_deploy` used to refuse every deploy on it ("an order is IN FLIGHT"). It now compares the claim's mtime with the
box's boot time (kernel uptime): a claim written before the boot names a process the reboot killed, so the lane reads
clear (`CURRENT_RUN claim predates this boot - nothing can hold it`); a box whose boot time cannot be read keeps the
claim. The runner re-enters its own order on start. ⚠ A pause flag (`STOP_AFTER_RENDER`) left from before the reboot is
read by the restarted runner, which then exits at once — clear your own flag, or let fleet_deploy relaunch it.

**Plex-coexistence orders (Softserve):** an order with `coexists_with_plex: true` runs during a transcode (every other
order waits it out); only a lost card stops it. For measurements whose subject IS running beside Plex — the advisor's
`--passes plex` (`submit_advisor.py plex --passes plex` sets it). Do not use it to skip Plex for ordinary work.

⚠ **Never pause and deploy on the same lane within a few minutes.** `fleet_deploy` asks the factory
to pause as part of deploying, so doing both mid-order leaves a stale `CURRENT_RUN` claim with no
live worker and **the stall daemon fires — correctly**. Let the order reach a boundary, confirm
`current=None`, then deploy.

### STOP / HOLD / RELEASE a TABLET order (Tolkien, the Fellowship) — `.stop.json` and `.ctl.json` in the tablet INBOX

**Never rename a `.tjob.json` to `.HELD` by hand in rigby's queue, and never kill the runner.** That
was the only way to stop a tablet order until 2026-09-27; it is floor work, and renaming a RUNNING
window's spec breaks its own retirement (`window archive failed (FileNotFoundError) - spec left in
queue`). Tolkien now takes the GPU foundries' slip vocabulary. Drop a JSON slip in the SAME inbox as
orders — `E:\SSDShared\tabfoundry-jobs\inbox\` — and watch for the `[fellowship:desk]` DM.

| you want | drop this in the tablet inbox | scope |
|---|---|---|
| STOP an order (queued, held, or RUNNING) | `<order>.stop.json` = `{"why":"..."}` | that order |
| STOP every window of a run | `<anything>.stop.json` = `{"prefix":"<run-id>","why":"..."}` | every queued/held/running order whose name starts with the prefix |
| HOLD a queued order (park it) | `<order>.ctl.json` = `{"action":"hold"}` | → `<order>.tjob.json.HELD` |
| HOLD every queued window of a run | `<anything>.ctl.json` = `{"action":"hold","prefix":"<run-id>"}` | queued matches; the running one is left alone |
| RELEASE a held order (run it) | `<order>.ctl.json` = `{"action":"release"}` | `.HELD` → queue; replaces re-submitting a held copy |
| CANCEL | `{"action":"cancel", ...}` | identical to the stop slip |
| run it NEXT | `<order>.ctl.json` = `{"action":"priority"}` (or `"value": N`) | front of the queue |
| RESUME a stopped (or finished) order | `<order>.ctl.json` = `{"action":"requeue","why":"..."}` | the NEWEST `archive/<order>.tjob.json.*` goes back in the queue under its own name; a rigby-lane `llm` order then skips every answer already in its checkpoint. Exact name only (a prefix is refused); refused if that name is queued, held or running |
| see what a bulk slip WOULD do | add `"dry_run": true` | nothing changes; the DM lists the count |
| RESERVE rigby's lane for a team run (pause rigby for one order) | `<any>.lane.json` = `{"action":"reserve","lane":"rigby","by":"<owner order>","owner_id":"<desk id>"}` | stops the rigby order running now (checkpoint kept), then starts ONLY orders whose spec says `"reservation":"<owner order>"`; a 2nd owner is REFUSED. Sent automatically by an A1 team run whose helper is rigby (`a1_delegate.RigbyDelegator.reserve`) — never by hand |
| RELEASE the reservation | `<any>.lane.json` = `{"action":"release","lane":"rigby","by":"<owner order>"}` | lifts it; every order it stopped is requeued ONCE IT HAS RETIRED (a requeue sent while it still runs is refused). Tolkien ALSO releases on its own when the owner's desk record has ended by any route; "could not ask the desk" keeps it. State: `rigby_reservation.json`, `rigby_requeue_pending.json` in Tolkien's state dir |

- **`<order>` is the queue name** = the spec file name minus `.tjob.json` (the spec `name` for every
  inbox order). `"order": "<name>"` in the body overrides the file name.
- **`prefix` must be ≥ 8 characters** or the slip is REFUSED by name (a two-letter prefix would sweep
  the whole queue — rigby's queue holds hundreds of `.HELD` files).
- **Slips must be valid JSON** (`{}` works). A half-written slip gets 120 s of grace, then is refused.
- **Timing:** idle, slips route on the next poll (≤ 30 s, after new orders are taken, so a slip may
  name an order submitted in the same pass). While an order RUNS, the runner re-reads the inbox every
  **5 s**: a hold/release/priority for another order applies immediately; a stop for the running one
  ships a results file with **`"stopped": true`** plus the finished rows, and retires the spec as
  `archive/<order>.tjob.json.stopped-<stamp>`. Never-measured items are listed in `never_measured`
  (rigby lane: `[arm, task, replicate]`, ready for an `items` re-run).
  - **RIGBY LANE (`kind: llm`) — a stop STOPS (since 2026-09-29):** the answer in flight is cut within one
    streamed token (llama-server abandons the generation when the connection closes); its partial text
    ships with `stopped: true`, ungraded. Every finished row is also appended to
    `state/<order>.checkpoint.jsonl` as it lands, and **an `llm` order that runs again under its own name
    resumes from it (since 2026-10-02)**: answered and priced-out pairs are kept (marked `resumed: true`),
    stopped / errored / missing pairs and the control run again; the log says `resuming <order> from its
    checkpoint - N finished answer(s) kept`. Two routes: a `requeue` slip after a stop, or a Tolkien
    recreate mid-order (the spec is still in the queue). A complete order moves its checkpoint to
    `archive/<order>.checkpoint.jsonl.<stamp>`. **Deploy without waiting:** stop slip (or just recreate) →
    deploy → `requeue` slip. Tablet `map`/window orders do NOT resume (their rows have no stable pair key).
  - **TABLET WINDOWS — a stop STOPS (since 2026-10-03, `tests/test_stop_in_flight.py`):** nothing new is dealt AND every batch already on a
    tablet is ended (`tablet_fulfil`: `<host> STOP: ended the batch in flight in <stage>`, via
    `kill_attempt_cmd`), speculative copies included; the finished rows ship with `stopped: true`. Window
    orders do not resume from a checkpoint — a stopped item comes back only as a new order.
- **One DM per slip**, e.g. `STOPPED <order> (running): <why> -- shipped N finished row(s) flagged
  stopped:true -> <results>`; `STOPPED prefix <run-id>: 55 order(s) (1 running, 54 held): <why>`;
  `HELD <order> (queued): <why>`; `REFUSED <slip>: prefix 'abc' is empty or shorter than 8 characters
  … - nothing changed`. A stopped running order does NOT also send its WINDOW SHIPPED/VOID DM.
- **No TTL problem here** (unlike the GPU `*.stop.json`): every tablet slip is consumed and archived
  (`archive/<slip>.<done|refused|unknown|dryrun|unreadable>-<stamp>`) — a slip for an unknown order
  is archived with a DM, so it can never ambush a later order of the same name.

Full contract (states × actions, field names, archive records): [[Fellowship-Order-Taker-Design]] §11.

### "CONTROL requeue FAILED: was CANCELLED by the operator" — an operator cancel OUTRANKS an automated requeue

**What you are seeing, and how to get past it deliberately:**

```
CONTROL <slip>.ctl.json FAILED: RuntimeError("'<order>' was CANCELLED by the operator at
<time> -- refusing to requeue it. An automated remedy must never undo a human cancel.
To bring it back deliberately, send the same slip with "force": true.")
```

`cancel` now leaves a **tombstone** (`jobs\archive\<order>.cancelled`) and `requeue` refuses to
cross it. To genuinely bring the order back: `{"action":"requeue","order":"<name>","force":true}`
— that logs `FORCED past the operator cancel` and clears the tombstone (a forced requeue is a
fresh intent, so the next cancel starts clean).

**Why the tombstone exists.** The stall daemon's `watch.compute` arm (below) remedies a wedged
compute order with *stop slip → requeue slip*, and its "did the order leave the queue?" test
cannot tell **"I stopped it"** from **"a human cancelled it"** — without the tombstone, the
automated requeue restores an order the operator just cancelled and keeps re-running it. Two
behaviours follow:

* A cancel for an order that is **archived but not running** is now RECORDED rather than refused:
  `CONTROL cancel <order>: not queued (archived N generation(s)) - recorded the cancel, so an
  automated requeue will NOT bring it back`. The operator's intent survives the remedy window.
* A cancel for the **RUNNING** order still points at the stop channel (that message is correct —
  use `<order>.stop.json`), and it now names what is holding the card.

⚠ **A stop slip has no TTL.** `stop_order()` matches purely on target name, so a slip that outlives
its order sits in `jobs\queue` and stops the NEXT order of that name the instant it starts. If you
place one by hand and the order ends some other way, remove your slip.

### `watch.compute` — the demon supervises COMPUTE orders (daemon v6.2)

The stall daemon's other two senses are training (telemetry step column) and render (ComfyUI
queue); a `mode:"compute"` order matches **neither**, so without this arm it runs completely
unsupervised and an overnight wedge in it costs hours.

**How it decides** (`compute_watch_decide`, pure + unit-pinned, and the loop only acts on it):

| it sees | verdict |
|---|---|
| progress step advanced | `progress` — baseline resets |
| static, inside the window | `watching` |
| static **at** `progress_total` | `assembly` — NEVER a wedge (the npz write looks exactly like this; r448 sat 13 min at 40/40) |
| static past the window, `load: gpu\|mixed` | **`wedged`** → stop slip → (order retires, shards banked) → requeue slip → order resumes and skips loadable shards |
| static past the window, `load: cpu` | `cpu_static` — REPORTS only; respawn is not the documented cure for CPU work |
| `CURRENT_RUN` beat stale | `runner_stale` — the RUNNER is the sick one; reports, never acts |
| order declares no `progress_glob` | `unwatchable` — says so once rather than staying silently green |

**The window is `progress_stale_after` in the order spec (default 900 s).** Round-capped at 8
respawns, after which it stands down and DMs — respawn that is not converging needs a human.
Live-proven 2026-08-09 with a decoy order: four clean wedge → stop → requeue → restart cycles.

### 📊 DECK `s/item` RATE and `~Nm left` — where the tile's number comes from, and the three ways it lied (2026-10-08/09)

The desk (`GET /p20/factory/<host>`) shows a rate and a remaining time. **Which object computed it decides whether to
believe it** — read this before quoting an ETA to the owner:

1. **The factory publishes a rate** (preferred). GPU factories: `p20_foundry.unit_rate(marks)` — the MEDIAN seconds
   per unit from `(time, count)` marks taken each time the order's `[tag] N/T …` progress line rises
   (`COMPUTE_PROG_RE`), the first mark being the first observed count. Tolkien's rigby lane: `tablet_runner.llm_eta`
   — the MEDIAN of this run's FRESH answers (`median_unit_s`), kept answers from a resume and the CONTROL answer
   excluded. Both read `· N s/item · ~Nm left` on the tile.
2. **No published rate → the desk derives `elapsed since started_at / step_n`** (`app.py`, "a rate the job did not
   publish can still be DERIVED"). Correct for a fresh captioning job; **wrong for anything resumed** — `started_at`
   is the order's first start, so it spans every stop, pause and freeze, and divides by rows a resume KEPT.
3. **Last resort: the rate the desk itself observed** between two sightings of a rising step (`_PROGRESS_SEEN`).

The lies, each now closed: **(a)** mean instead of median — one 180-min sgf-parsing made every remaining exercise
"69 min" (10-08; median since). **(b)** the control answer as the pace — rigby's R2-rest had one fresh answer, the
172 s control, and read "~25m left" for eight 1–6 h exercises (10-09; `controls` filed apart, no rate until a real
item lands). **(c)** no mark at a RESUME — `resuming: N kept` matched no progress regex, so the foundry had one
mark, published no rate, and the desk fell back to (2): Ornith read **7,928 s/item** across the 01:47–07:41 freeze
while its units ran 8–21 min (10-09; `a1_order` now prints `[a1] N/T resumed · solved …` at resume, the same line the
marks come from). **Rule for readers:** a rate whose unit is `it`/`img` on a compute order, or that is far above the
order's own per-unit log lines, is (2) — read the order's `[a1] …` lines for the real pace. **Rule for payload
authors:** print the `[tag] N/T` line at START/RESUME as well as per unit, or the desk's fallback is what the owner sees.

### ⚡ DECK SPEED (`tok/s`) IS LIVE — the ETA is the one that does median math (standard since 2026-10-10)

the owner's rule: *"Speed is supposed to be LIVE but the eta is the one we do math for."* Two tile fields, two derivations:

| tile field | what it is | who computes it |
|---|---|---|
| **Speed** (`· N tok/s` in the phase) | tokens per second over the **last 60 s** of the answer(s) streaming **now**; between generations (no tick for 90 s) the median of the last ten answers' rates | GPU factories: `p20_foundry.live_toks` over the payload's `[beat] N` lines, then `toks_phase`. Tolkien: `tablet_runner.live_toks` per answer in flight, summed by `live_rate` (the warm arm streams one answer per node), then `tps_phase` |
| **ETA** (`s/item · ~Nm left`) | median seconds per unit × units left | the section above |

Both live-rate copies use one rule and are pinned by tests (`test_foundry.LiveToks`, `tests/test_tps_phase.LiveRate`): a
delta faster than 5,000 tok/s is skipped (the A1 delegator prints the clock on its beat line), and a count that goes
DOWN is a new baseline (a payload process restarted). The deck only parses the number (`app.py` `_factory_view`).
⚠ **The regression this closes:** until 10-10 both factories sent only the median of recent ANSWERS, and on an A1
order one answer is an exercise of up to hours, so the tile froze on one exercise's rate. Do not move Speed back to a
median, and do not put live math into the ETA.

## A HAI job as a desk order: start it at a time, or after another job (`POST /hai/jobs`, "don't start before")

A HAI job can go on the fleet desk instead of being started by a script or a Claude session (the owner 2026-11-07: *"Submit it as an
order that doesnt start until 11pm under my user"*, then *"Yes, build it"*). `POST /hai/jobs` with `{"query": <the /query/stream
request>, "name": ..., "not_before": <epoch seconds or null>, "after": [<desk order ids>]}` puts a `hai.job` order on the desk
(`hai_jobs.py`, Kirk `57bea42`). Kirk's taker (every `HAI_JOBS_TICK_S`, 30 s; the serving Kirk only) starts it once its time has
come and every `after` order is `done`, exactly as `/query/stream` starts a run: its own run ledger, its own live feed, so it
survives a restart, and its budget clock starts when it starts. Its run's end marks the order `done` (an answer or a budget
stop), `cancelled` (a Stop) or `failed` (an error); the run's route is in `run_route`. No factory can take it
(`requires: ["hai.job"]`, `host: "kirk"`, the `selfeng.overnight` pattern). Read them with `GET /hai/jobs?user=<user>`;
`POST /hai/jobs/cancel {"id", "user"}` cancels a waiting one or stops a running one (its user or an admin). Proven live 2026-11-07
19:57-19:58 (`research\ltt-jarvis-prompts\haijob_proof.py`: A started at its time, B after A).

**A failed job blocks its chain unless the chain says otherwise: `go_on_after_failure`.** By the desk's rule a `failed`
predecessor marks every successor `blocked` (never released). For a SET of independent runs chained only for their running
order (a benchmark rerun set), submit each job with `"go_on_after_failure": true`: a failed predecessor then releases it as a
done one does (`p20_queue._releases`, one derivation for `blocked_by` and `_block_successors_of`), so one bad run costs one run,
as `run_chain.py` did (Kirk `fb302bc`; found in the 2026-11-07 night-set preflight, where one error would have idled Mothership
until morning). A **cancelled** predecessor (a Stop) still blocks the rest, so stopping a set stays the operator's decision;
to drop one queued job and keep the set going, cancel the running one and resubmit the next without `after`. Proven live
2026-11-07 21:13 (`goon_proof.py`: A failed with route `error`, B started at the next tick and finished).

**Shipping and blind jobs.** `"ship": {"workspace", "path"}` ships the job's folder out of the shared workspace (the runner's
`/files/ship` into `/app/shipped`) when its run ends, before the order is marked, so a job chained after it never sees it (the owner:
*"factory orders have shipment so they can just ship outside to a different area"*; a failed shipment is recorded as
`ship_error` and never fails the run). `"blind": true` hides every open coding task (`rag-hide.json` `tasks`) just before it
starts, and waits on the desk if the tasks cannot be listed. Proven live 2026-11-07 20:34 (`ship_proof.py`). The LTT night
set uses all three: `research\ltt-jarvis-prompts\submit_set.py`.

## A render waits while a GPU compute order holds the card (chat window, benchmark): `compute_holds_card`, and "only 0 MiB of VRAM free"

Since 2026-11-06 the render worker fetches no jobs while `CURRENT_RUN.json` says `state: compute` with a GPU or mixed load. A queued image waits at the desk until the order or window ends; a CPU order does not block it. The worker's published `gpu_status` is unchanged, so placement reads what it always read. Before this, an image could claim the card under a live chat window: the engine's read fell about twelvefold and it exited with `only 0 MiB of VRAM free`.

**A long prompt read is a status, not silence.** `infer_ollama._chat_openai` asks the engine for `return_progress`, and the engine's keep-alive comments count too, so a read moves the worker's `[beat]` and reaches the window's `.stream.jsonl`, which Kirk's clock reads. To watch a read: `prompt_progress` rows in that stream, or `reading the prompt:` lines in the window's `server.log`. rigby's window (`tolkien_llm`) builds its own request and does not have this yet.

**Restart only the render worker while an order runs:** `python fleet_deploy.py --no-restart --worker p20_render_worker.py`. It exits between jobs and its task relaunches it. A plain deploy refuses while an order is in flight.

**A window being opened is a status too (`chat_window._box_reporting`).** Once the factory holds a window's order and before its first row, Kirk's clock does not run while the box's own load report is fresh. A wedged open is the factory's to end; a dead box stops reporting and the clock applies. **rigby reports too (Kirk `4165c46` + Tolkien `4b9220c`, live 2026-11-07; the reporter reads the 1060 under the host loader since Tolkien `e27dd57`, and a push with no card sample logs STALE):** Tolkien's telemetry container runs the GPU boxes' own `gpu_load_reporter.py` as `rigby` every minute; Kirk takes it (`scheduler.RIGBY_HOST`) and accounts its load from Tolkien's free-spot report (`rigby_busy`). Before that a rigby window waiting in its lane or loading was cut at 240 s: on 2026-11-08 two cuts cancelled a parked turn's re-asks while it waited behind another window, and the window then spent the turn's one continue on an attempt already cancelled. Fixed too: an item whose `.cancel` exists when the window reaches it is never started (foundry `2558b11`).

**A deploy does not wait for a gap** (the one answer, with its four exceptions: [[RUNBOOK-Orchestrator-Deploy-Procedure]] § "Before ANY deploy: no park, no wait, no hold"). Kirk: gate, recreate, no park; a window keeps writing and the turn reconnects. Foundry: `--no-restart` (`--worker` for the render worker); the window's next open carries the code. Tolkien: `fleet_deploy.py <file>` ships the images, holds intake (`DEPLOY_HOLD` in the inbox, ignored after an hour), stops and parks what runs, swaps the containers, requeues and releases. Kirk reopens a chat window himself and its order waits for the swap, so the turn picks up where it parked.

## A turn that stops on `length` is not an answer: `TURN OUT OF ROOM`, compaction, and a run that "finished" mid-sentence

A turn that ends with `done_reason` `length`, no tool call and the window at 0.9 or more compacts, its partial text summarised with the rest, and is asked again (`TURN OUT OF ROOM` in the notes, `out_of_room` in the job's feed). A model that fills the window three turns running with no tool call (`CODING_IDLE_TURNS`) ends: a job with a ChatError, a chat with what it got. Before 2026-11-07 the half thought became the run's answer and the run read as finished. **A run's exit code says the run ended, not that the job is done: read its last `TURNMETA` line and open what it made.**

## A job's context on rigby (and every box): the 49,152 window, a tool SET, one summary line at 90%, a summary that keeps the running record

**Read this before changing rigby's window or when a job "keeps summarising" / `CONTEXT COMPACTED` every few turns.**
Three things decide how much room a job has to work in; all three are deliberate, each with its history.

**1. The window: a pinned JOB on rigby gets `config.TOLKIEN_JOB_CTX` = 49,152; a chat keeps its menu row's window. Do not raise it without new data.**
Measured 2026-11-07 on the GTX 1060 on its x16 riser, the standard 1060 order, one rep per arm, thinking off, `hard-bench\submit_rigby_window.py`, controls within 1%:

| Window | Read tok/s | Write tok/s |
|---|---|---|
| 32,768 | 299-315 | 17.7-18.6 |
| 40,960 | 298-313 | 17.5-18.3 |
| 49,152 | 297-312 | 17.4-18.2 |
| 65,536 | 286-304 | 12.7-15.1 |

65,536 was run on the 35B on 2026-11-05 and taken back the same day: a third slower, and slower again as the window fills (about 13 tok/s falling to 9-10 with depth, against 17 at any depth at 32,768; the larger window's memory takes about 770 MiB of the card's room for layers). the owner: *"a third slower is bad, because it only gets slower as the context fills up"*, *"let the menu serve the context size"*; 11-07, *"a conservative context increase if you think we can get away with it"* gave 49,152. It was proposed again and reverted on 2026-11-12 (Kirk `5a2c22f` / `8d5e8e8`) by a session that had not read this. At 32,768 and Max effort a job's prompt sits near 21,000 after a summary and a turn runs out before it acts. ⚠ Read 1060 numbers by date: the card arrived 2026-10-13 on a PCIe x1 riser and moved to x16 on 10-26/27, so an older number can be the riser's.

**2. The tools: a job that opens a coding workspace holds a SET** (`ollama_client._coding_tools_after_open`, 2026-11-12). At the first message Starfleet picks from the general pool as for any request ([[HAI-LLM-Execution-Context-Mechanics]] § Tool selection). When a JOB's `coding_workspace` open succeeds, its block becomes: the permitted coding toolkit, the always-offered tools (`tool_selection.CORE_TOOL_NAMES`: web search and page fetch among them) and whatever the job has already called. The first message's other picks are let go, named to the model in the tool result and in the notes (`CODING TOOLSET loaded after workspace open: N tools … let go …`). A chat's open still only adds. Why: box2-p (rigby 35B, 2026-11-12) was handed `36/136 tools, ~11777/14745 tok` (`docker_stop`, `git_commit`, `write_config` among them), the toolkit was added on top and nothing was ever dropped, in a job that called coding tools and `fetch_url` only. the owner 2026-08-26: *"it's supposed to see sets"*.

**3. The summary line: 90% of the window on every box** (`ollama_client._compact_line`; the owner 2026-10-09 *"summarize only when full"*, 2026-11-12 *"summarizing way too early instead of towards the end of the window"*). From 2026-11-07 to 11-12 rigby alone summarised at the window minus 20,480, the A1 family's reserve for one whole reply. In A1 that reserve is paired with an output cap of the same size (34-A1 spec § Runaway guard); HAI's loop has no such cap, and on 49,152 the line sat at 58%: **box2-p was summarised 34 times in 104 turns, two or three turns apart, two to six minutes each, and five turns still ran out of room.** What makes the late line safe is the section above: a turn that fills the window is summarised with its thinking and asked again (`TURN OUT OF ROOM` / `out_of_room`). Before that existed (11-05), a 90% line on rigby ended runs on `length`.

**4. The summary keeps a running record** (`ollama_client.COMPACT_RUNNING_RECORD`): it carries the earlier summary forward and keeps REQUIREMENTS, TRIED, FILES, LAST CHECK and NEXT, with no length cap. A summary is written from the last summary plus the turns since, so one that tells only the latest episode forgets the job a little more each time (box2-p's tenth knew only the bug it had just fixed).

**5. The runaway guard — `TURN RUNAWAY` in the notes, `runaway` in a job's feed (rigby jobs only, Kirk `b1c8c2c`, 2026-11-12).** A job's turn on rigby that has THOUGHT `config.TOLKIEN_RUNAWAY_THINK` (20,480) tokens and called no tool is stopped where Kirk reads it (which ends the generation on the box), its thinking is summarised with the conversation and the turn is asked again, told to act: the recovery of a full window (the section above), only sooner. `CODING_IDLE_TURNS` runaways in a row with no tool call end the job with "thought past 20480 tokens N turns running". The A1 family has had this since 10-15 (34-A1 spec § Runaway guard); it did not cross to HAI's loop with the summary line on 11-07 and nobody decided that (the owner 11-12: *"a missing cross cut if we made it for benchmarks but not real usage"*). **The figure is measured for this regime**, the 35B with thinking on (`scratchpad\turn_gap_all.py`, the 13 saved runs): of 224 turns that went on to call a tool none thought past 15,760 tokens (p99 11,881); runaways thought until the window ended, 24,667 to 30,212 (box2-r: 38 minutes for one). It counts thinking, not the whole reply (one real turn wrote 22,329 tokens because its tool call carried a whole file), and never cuts a turn that has already called a tool. **No other box has a guard, on purpose:** the GPU boxes' thinking models have real turns as long as their runaways (qwen3.8:27b 46,111; xhigh 71,288), so a cap there would cut real thinking (same spec).

**6. "Stopped after 3 turns with no new work" — the first trip explains and restarts the count (every box, Kirk 2026-11-12 night).** A turn is idle when every call in it repeats one already made on an unchanged workspace (`_LoopProgress`). `CODING_IDLE_TURNS` (3) idle turns in a row is a TRIP. **The first trip no longer ends the loop:** the model is told what it repeated and why that cannot work, and the count starts again (`IDLE_RESETS = 1`, `IDLE RESET` in the notes, `idle_reset` in a job's feed, `idle_resets` in its telemetry); only a trip after that ends it. This is the A1 harness's rule (`a1_harness.NO_PROGRESS_NUDGES = 1`, since October: "the first trip of either rule nudges the model (what it is doing, why it cannot work); only a trip after the nudges ends the exercise"), which never crossed to HAI's loop — the same missed cross-cut as the runaway guard (item 5). Every idle turn is also told which calls it repeated (`ollama_client._idle_nudge`) and what the next trip does; before, one generic line on the first idle turn only. The reset count is checkpointed, so a restart does not hand a stuck job a second reset. When the repeat is `coding_test` the warning adds the tool's own rule at the moment it matters: the profile runs test files and nothing else, so to run a script call it from a `test_*.py` in the job's folder. **The STALL rule (A1's `NO_PROGRESS_STALL`, carried 2026-11-12 night on the owner's word, `config.CODING_STALL_TURNS` = 8):** a turn makes PROGRESS when the workspace reaches a state it has never been in, or the model makes an observation it has not made on this state; 8 tool-calling turns in a row without either is a trip (`_LoopProgress.stall_turns`), sharing the one reset with the repeat rule (`[NO PROGRESS: …]` explains, then `ended: "stall"`, "the code kept returning to versions already tried"). The state is `{path: sha256}` from the edit tools' own results; an edit with no hash counts as a new state, so the rule can only under-count. Turns with no tool call are not counted: their own guards own them. **Why it exists:** A1, 2026-10-05, qwen3:8b rewrote one exercise back and forth for 4.6 h because every edit counted as progress; HAI's repeat rule cannot see that either (each rewrite has new arguments). **Why 8:** A1 validated it on 207 replayed exercises (its solves' longest idle run 4; "no better score for 8 turns" and "new state only" were tried and rejected for cutting real solves). Re-measured for HAI's jobs before porting: `Claude Home` scratchpad `stall_replay.py --calls-only` over the 21 saved runs (1,444 tool-calling turns) — the longest stretch a model came back from was **3**, so 8 keeps A1's margin. (Counting no-call turns too, the old box2-p's out-of-room spells read up to 9; those are why no-call turns are left to their own guards.) **Why:** box2-o (11-11, 35 min) and box2-s (11-12, 4 h of 8) were both the 35B re-running the unit profile to run a script it had written, ended by this rule; box2-s had used the test-file route itself earlier in the same run. The line repeats what the `coding_test` description has said since 11-11 23:45 (`f86320a`); it adds no fact the job was not already given.

**7. Every stop a JOB's model causes hands off; none fails the job (Kirk, 2026-11-13).** One table, `ollama_client._STOP_WHY`: deadline, idle, stall, `looped` (the factory's loop rule three times in a row), `runaway` (the runaway guard past its retries), `out_of_room` (a full window turn after turn), `text_calls` (tool calls still written as text after three notes) and `empty` (an empty answer). Each ends the loop the same way: the model is asked for a handoff (`_explain`), the run is marked stopped (`ended` in its telemetry), a budgeted run's answer says "_Stopped …: this is where the work stood, not a finished answer._", and the folder ships as always. Before, the last five RAISED for a job: the run read FAILED with no word of its work (box2-u, 11-13: the loop rule at step 62, a rectangle shipped, no answer), and after three text calls the fourth broken call BECAME the job's answer. Faults of the machinery still raise (the desk refusing a window, a transport failure, rejected media, the busy gate); a CHAT still raises on a loop or an empty answer. the owner: *"that's why I said to look for missed cross cuts"* — the first sweep compared Kirk with A1 and never compared Kirk's own exits with each other.

**8. A job's work in a git-IGNORED folder survives a coding-task merge (coding-runner `_carry_ignored`, 2026-11-14).** `hai-scratch/.git/info/exclude` holds `box2/` (the 11-06 blind setup, on the undo-when-the-set-ends list), and every task worktree shares it. A merge carries commits and a checkpoint commits only what git sees, so box2-x's generator, 8 tests and STL were removed with its worktree while the reply said `merged: true` (journal 2026-11-14 07:28). Now, after the merge and before the worktree goes, its ignored files (not caches, not the runner's hidden paths) are COPIED to the same paths in the base — not committed, so the blindness the exclude exists for holds — and a failed copy removes nothing. Audit event `worktree_carried_ignored`. Live control on the CIFS mount: `copy2` of a throwaway file into `/app/workspaces/hai-scratch`, ok, removed. Discard is unchanged.

**9. A run a GUARD stopped stays resumable for a week; rotated saves are ARCHIVED (Kirk, 2026-11-14).** the owner: *"we want guard stops to be resumable for a while"*. When the loop stops a job on a guard (item 7's table, except `deadline`), its call is marked `stopped` and `run_ledger.CallSlot.done` keeps the whole resume point (step, loop state, tool list) beside the handoff. `resumable()` counts such a save like an answerless one: it is kept `RESUMABLE_KEEP_S` (a week) outside the rolling `RUN_SAVES_KEEP` (5), and `restore_save` (POST `/runs/resume`, admin) sets the handoff aside and continues the call from its last checkpoint with the budget it had left. Saves leaving the rolling window (or aging out) are MOVED to `State\hai-runs-archive\`, never deleted, and `restore_save` looks there too. Why: box2-s (idle stop, 11-12) wrote a handoff, counted as finished, and its save was `rmtree`'d after five newer finished runs — no archive and no shadow copy of Z:, so its run could not be continued (journal 2026-11-14).

**How to read a run against this:** count `context_compacted`, `out_of_room` and `runaway` in its saved feed (`State\hai-runs-saved\<run>\feed.json`; `Claude Home` scratchpad `feed_summary.py` did it for box2-p, box2-q and run-s). Many summaries a few turns apart = the room is too small for what the job puts in it; many `out_of_room` = turns too big for the room left at the line; `runaway` = the model deliberating instead of acting. The text this replaced: `Archive\runbook-rigby-job-window-and-summary-line-superseded-20261112.md`.
