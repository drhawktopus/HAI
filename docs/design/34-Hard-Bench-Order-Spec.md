# Project 34 — Hard Bench order spec (Phase 7 prerequisite) — DRAFT for the owner, 2026-09-26

**Status:** RUNNING (the owner's "run it", 2026-09-26) — calibrations done for all three divisions, coding matrix done,
vlm size question open (§ "VLM at 2×"). Written the night Phase 5 closed, from his brainstorm:
*"the real question overall that we're addressing is closer to… the bigger tasks."*
**Project:** #34 area B. **Feeds:** Phase 7 (swarm vs Voltron by task class) and the Phase 5 retest queue
(refiner layouts on tasks the solver does not already ace). **Sits after:** [[34-Phase-5-Appraisal-Closeout]].

## Filling the GPU menu — `menu_fill.py` (its scheduled task "HAI P34 menu fill" is gone: the measurement CLOSED 2026-10-10)

**State (2026-10-26):** Softserve has no "HAI P34 menu fill" task; the menu itself still REFRESHES from every
delivery ("HAI P34 menu refresh", 15 min). A cell is ordered through the filler's own builder (`menu_fill.rigby_submit`
for rigby), never a hand-written spec, so the order carries the grid's fields and lands in the filler's ledger.
**Placement epochs (hardware changes under the same model file):** `menu_fill.RIGBY_PLACEMENT[<file>]["since"]`. A
delivery of that file finished before it no longer counts as measured (its cell is re-ordered), and `build_menu`
supersedes its rows once the same setting has a newer delivery (until then they still price). First use: rigby's 1060
riser x1 → x16 (2026-10-26, reads 3.5×, [[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]] § 8): the 20 thinking-off
cells of the MTP file re-ordered with `Claude Home` scratchpad `rigby_reprice_x16.py --submit`. Pinned:
`test_menu_fill.PlacementEpoch`.

**What the menu is FOR (the owner, 2026-10-02):** in production, Kirk and Spock place each order on the arm that is
efficient in **both time and real-world energy dollars**, by urgency — *"some things would be OK to run as low and
slow and some things would be OK to be needed right away."* Every row is therefore a priced entry (quality, wall,
joules) for ONE box.

**Scope rule (the owner 2026-09-29, refined 2026-10-02): every model that FITS the box, on every box, at every knob, until
that box's own data stops an arm.** A model that cannot generate text (an embedding model) is out, decided from
ollama's capability list, never by a session's judgment. On the 8 GB boxes (BigHonker, Softserve) only models that
fit run — *"8B class ish"*; *"we're not running the 35B on big Honker"* (held in `menu_fill_hold.json`). Also the owner
(09-29): *"I want the whole order list to be built out and then run ... I don't want it to depend on you or your
attention span or your memory."*

**Depth per arm: three cells, every arm still covered (the owner 2026-10-02).** Why the 09-28/29 "huge thing / every model on
every box at every knob" rule existed, in the owner's words: *"The whole thing was me saying it because you kept collapsing
it that day to way way less than what we wanted since we were working on multiple lanes."* It guards **coverage of
ARMS** (model × family × thinking on each box) against a session quietly dropping them — that guard stands. What is cut is
DEPTH per arm, after counting what the remaining cells would add (2026-10-02: ~50 greedy repeats with identical answers,
~260 extra samples of already-priced arms, ~216 cells of never-measured arms) and checking the charter (a reading graduates
when it "reproduces once"; §52 needs a price at a good setting, not a variance map; §44–48 rank work-shaped tasks above more
benchmark knobs): each arm is ordered at **greedy once + 0.6 twice** (`menu_fill.CORE_KNOBS`: 0.0/1, 0.6/1, 0.6/4 — greedy
is deterministic, so one is enough; the second 0.6 is the reproduction). Cells already delivered at other knobs stay on
the menu; 40 queued extras were cancelled through the desk (`hard-bench\noncore_cancels_20261002.json`). the owner: *"I'm
honestly just trying to get this project moved along."*

**Plus one cell per arm at the VENDOR's temperature (the owner 2026-10-03, every model, the earlier ones included: *"if we
didn't do that for the previous models either then that's a bit of a miss"*).** The uniform 0.0/0.6 pair compares models
on equal terms; the vendor cell prices each one at the setting it was tuned for (doctrine 13a). Seed set 1, beside the
core three (`menu_fill.vendor_temp`, `core(cells, carried)`):
- **Source = the model's own tag.** Official ollama tags carry the vendor's sampling; the hf.co pulls got configured tags
  with their card / generation_config values, temperature included (`configure_new_models.py`: Nemotron 3 Nano 30B 1.0,
  Ornith 1.5 0.6 — its card, over the config's 1.0 — MiniCPM-V 4.6 0.7, MiniCPM5 0.9). The filler reads it from
  `/api/show` every run, so a re-tagged model re-prices without a code change.
- **A tag carries ONE value, the thinking-mode one**, so a card's different value is named in the filler: thinking OFF
  = 0.7 for Qwen3 / 3.5 / 3.6 / 3.8 / Qwen3-VL (each card's instruct setting) and MiniCPM5; tool calls = 0.6 for
  Nemotron 3 Nano. rigby's llama.cpp files carry nothing, so `RIGBY_VENDOR` names them (Qwen2.5 0.7 per its
  generation_config; the 3.6 MoE 1.0 thinking / 0.7 off). Only TEMPERATURE moves on rigby: a Tolkien `llm` order
  carries no other sampling field.
- **Already a core knob = no extra cell** (Qwen3 thinking 0.6, Nemotron tool 0.6, GLM-OCR 0.0). **No stated
  temperature = no cell**, printed by `plan` per box (medgemma, joycaption — specialists held anyway).
- **An AUTOMATIC stop leaves the arm's vendor cell open** (`open_vendor_cells` adds it as `except`): the stop rule
  judged the arm at the uniform knobs only, which is exactly the gap. A HUMAN hold is a ruling and still holds it,
  including the 2026-10-02 stop-rule entries in `menu_fill_hold.json` (the owner's call to open those).
- Its rows join the arm's price like any other cell (a priced-out vendor cell counts as a second priced-out cell).

**Every verdict is PER BOX** (the owner 2026-10-02): the boxes differ in hardware and therefore in power draw, which is
why each is measured separately. A result on one box never stands in for another, and "reproduced" means
reproduced on THAT box. Cross-box comparisons are the routing question, not the price.

**When an arm stops being filled — exactly two reasons, each judged on the arm's own box** (an arm = model × task
family × thinking setting on one box; the owner 2026-10-02: *"Continuing running these tests is actually detrimental to
the overall idea because that would be wasting energy on known 'bad' configurations"*):
1. **It cannot fit:** its answers hit the context limit at the model's TESTED MAXIMUM on that box, so no un-void
   measurement can exist there (doctrine 13a).
2. **It is priced out:** its answers cost ≥ 10× that box's cheapest measured solve of the family (§ "Price
   ceilings" below), and that holds on ≥ 2 task families and reproduces once ON THAT BOX.

**Quality is a price too (the owner 2026-10-03)** — an arm not ACCEPTABLE for a family (one-shot on the GPU fleet; the set
with a real production selector on the Fellowship; sub-agent value judged separately), on its box and across the fleet,
is priced out by another name ([[34-CHARTER]] § "How results shape action" — read it before proposing any cull).
**Priced out per CORRECT answer (live 2026-10-03, `menu_fill.PRICE_BASIS = "solved"`):** besides the per-answer test
below, a cell is priced out when ≥ `PRICED_SHARE` of its priced tasks cost ≥ 10× their cheapest cold solve PER SOLVE —
per task, the MEDIAN answer's wall × answers per solve (a task the cell never solved costs without limit); the same
≥ 2 families + reproduction then applies. It only ADDS stops: cap rows are void in it but the per-answer test still
sees them, so it can never reopen an arm. Why the median: the reference is a task's single fastest cold solve, and
a mean stopped Mothership's qwen3-vl:8b vision (100% pass, its cheapest vision helper) on one slow answer in the
preview. Preview on 39,631 GPU rows: 10 arms added, 9 of them joycaption (already held by the specialist ruling),
one new — BigHonker qwen3.5:9b tool thinking-on (87% pass, 5.1 s per solve against a ~7 s ceiling); none dropped
(`hard-bench\preview_price_basis.py`; pinned in `test_menu_fill.py` `PerSolvedPrice`). **rigby too (live 2026-10-03,
`menu_fill.rigby_stops`):** rigby answers one-shot like a GPU box, so it gets cannot-fit plus the per-SOLVED price — not
the per-answer test, which its own per-order brake covers. Preview (`preview_rigby_solved.py`, 18,176 rows): 13 arms,
2 already held; the rest reproduce the 10-02 rigby review's own verdicts (Qwen2.5-14B on f1/f2/tool "firm", the MoE
thinking-on tool "soft") plus the bottom rungs (Qwen2.5-0.5B, Coder-0.5B; 0.5B on W1 1% at 2,359 s per solve); the
champions (MoE thinking-off, 7B, Coder-3B) and the 1.5B on W1 are untouched. **The tablets are NOT priced this way
yet:** they are judged per CLASS, one-shot until a production selector exists (majority vote measured 2026-10-03 gains
≤ 1 task on C1/W1; S10b is the real one), with instrument failures (`answer_boundary:*`) excluded and format failures
reported apart — at the menu rebuild, not by the filler.
**"Dominated" is NOT a reason to stop** (the owner 2026-10-02: the winning config and box *"might be busy with other
work, and to utilize the lab properly it might be beneficial for star fleet command to place it on a different box
with slightly worse pricing (but similar/same/better quality) in order to get things done concurrently"*). An arm
under the 10× line with comparable quality is a fallback entry Spock needs — including a model that is often
already resident (BigHonker's chat model qwen3.5:9b). A stopped arm reopens when a NEW task family is added: one
cell of it is cheap, and a harder family may be where the slow arm finally earns its cost.

**The filler applies the stop rule ITSELF, as prices arrive (the owner 2026-10-02):** *"we want a smart filler, not just one
that blasts every single combo"* — *"By smart filler I mean that it was supposed to adjust as we get prices in and cancel
those obviously bad orders."* So every top-up re-reads each box's delivered rows, applies the two stop reasons above per
arm on its own box, holds what qualifies (its OWN file, `menu_fill_auto_holds.json` — the human holds file has one
writer, the owner's rulings), and cancels that arm's already-queued orders through the desk (`POST /p20/order/cancel`:
a not-yet-collected order is cancelled at the desk, a collected one gets a factory cancel control). Every automatic
stop and cancel is logged with its evidence. Breadth-first ordering (every model and knob once before more seeds)
already makes the first prices arrive early.
How it decides (`menu_fill.arm_stops`, live 2026-10-02, desk endpoint gate 2,546 OK): **cannot fit** = ≥
`FIT_VOID_SHARE` **0.25** of an arm-family's rows ended at the context cap AND the cap is the model's tested max on that
box (the 10-02 reviews' gap: every arm the owner stopped for this sat at ≥28%, every arm kept at ≤12%); **priced out** = in a
cell, ≥ `PRICED_SHARE` **0.5** of the answers cost ≥ 10× THEIR TASK's cheapest cold solve on that box — in ≥ 2 families,
with a second priced-out cell in one family (the reproduction — another temperature or seed set is a fresh sampled
run, and breadth-first ordering reaches a second temperature long before a second seed set). Both shares are owner calls, the owner's to change, like
`PRICE_X`. **The champion is never auto-stopped** (`menu_fill.CHAMPION = {"mothership": "qwen3.8:27b"}`, the owner
2026-10-03: *"we WANT the high numbers stats for the 3.8 too, we need to know how well the champ does"*): its price
list at every knob is the point, so `arm_stops` skips it on Mothership — before this, the rule had stopped its
thinking-on f1, f2 and vlm2x cells (a hole: the rule had no notion of the champion). Human holds still apply (its 1x
vision is held by the owner's ruling). First run reproduced the human stop list exactly (qwen3-vl:8b coding and qwen3.5:9b thinking-on coding on
both 8 GB boxes) and added qwen3.5:9b vision thinking-on (BigHonker vlm2x; Softserve vlm + vlm2x). Cancels go through
`POST /p20/order/cancel {id, why, source}` (Kirk's desk): `pending` → cancelled at the desk; `claimed` → one factory
`cancel` control (a running order is never cut — its price line bounds it); finished → left alone. Each order is
cancelled once (`cancel_sent` in `menu_fill_auto_holds.json`).

**Mothership is the lab's top end — measured differently (the owner 2026-10-02):** *"Motherships testing should differ
slightly- it's the top end for the lab. So we measure how expensive for our lab 'frontier' model, the 3.8 27b … if the
3.8 has vision we can pretty much lose most of the other models on mothership like the 3.5 and the 3.6 since they're
supposed to be in the same tier, but 3.8 is kicking butt most of the time."* So on Mothership: **(1) the champion /
control `qwen3.8:27b` ([[34-CHARTER]] §10) is priced at every knob** — its price list is the point; **(2) helpers stay
only where they beat it on price on this box** — on the current families `qwen3-coder:30b` (cheapest correct coder,
0.5–0.8 s/solve vs the 3.8's 1.4–1.9) and `qwen3-vl:8b` (cheapest tool / 2× vision), plus `qwen3.6:35b-a3b`; **(3) a
same-tier model the 3.8 beats ON THIS BOX is held** (`qwen3.6:27b`, `qwen3:14b`). This does not contradict "dominated
is not a reason to stop": the fallback value of a dominated arm comes from ANOTHER box being free, and a model on
the SAME card cannot run while that card is busy with the 3.8. Also held: the duplicate tag `qwen3.8:27b-mtp-q4_K_M`
(same weights, digest `22130167c4c2`) and the Flash-Next ollama builds (the owner 09-29: pre-preliminary). Every hold
carries its evidence in `menu_fill_hold.json`. **Vision is priced at 2× (the owner 2026-10-02: "we want 2x vision"):** the
1× family is void by method for the Qwen 3.5/3.6/3.8-family models — their 1× prompts read ~175–183 tokens, the image
never registers (qwen3-vl:8b reads ~1,110 and keeps its 1× cells); held on Mothership for qwen3.8:27b and
qwen3.6:35b-a3b (qwen3.6:27b is held as a model) and on both 8 GB boxes for qwen3.5:9b, queued 1× cells cancelled through
the desk (`vlm1x_cancels_20261002.json`). The table rebuild must mark those models' delivered 1× rows void.

**Specialists are priced ONLY on the job they would do (the owner 2026-10-02):** *"we should price them specifically for
what we would use them for, not EVERYTHING under the sun"*. huihui qwen3-vl-abliterated (8B, 32B) is the P20
caption-mode model and joycaption the P20 dataset captioner — their price is a CAPTIONING family; medgemma (27B = the
nursing tutor's `NURSING_CRITIQUE_MODEL`; 4B) is a MEDICAL model — its price is a medical family. So all their generic
cells (coding, tool, vision quizzes) are held on every box (model holds, 9 keys) and their queued generic orders were
cancelled through the desk 2026-10-02 (60 controls, `hard-bench\specialist_cancels_20261002.json`). The captioning and
medical families are a to-do (charter § "Where #34 stands"), not part of the generic menu. ⚠ The current families are too easy to separate the 3.8's quality (nearly every arm
passes ~1.0), so they price it but cannot show where it is worth its price — the missing piece is a harder,
work-shaped family (the charter's §44–48 "judge on WORK"; the GPU menu also has no chat/generalist division yet).

**Grid per box** (read live from the box's ollama `/api/tags` + `/api/show` capabilities): every "completion" model
× family (coding-f1, coding-f2, tool; plus vlm 1× and vlm-2x for "vision" models) × thinking (off; plus on for
"thinking" models) × temperature (0.0 0.3 0.6 0.9) × seed set (seed-base 1 4 7). 2026-09-29: BigHonker 7 models /
672 cells, Softserve 5 / 432, Mothership 11 / 1,008. Context: the model's tested max on that box; an untested model
runs at the box's bench standard (6,144 on the 8 GB boxes, 8,192 on Mothership) and the reducers count every row
that hit it (doctrine 13a) so the cell re-runs once the fit is measured.

**Top-up, not a dump** (how the filler ran while its task existed; `python menu_fill.py topup` still does this): every 15 min (task "HAI P34 menu fill" on Softserve: AtStartup + 15-min repetition, IgnoreNew,
RestartCount 3, no time limit; wrapper `hard-bench\menu_fill_topup.cmd`, log `menu_fill_topup.log`) it counts each
box's outstanding orders and submits the next missing cells until the box has 25 waiting — breadth first (every
model and knob once before more seeds). "Already ordered" comes from every order record ever written, so it never
duplicates; a new model on a box is picked up automatically.

**Holds (`menu_fill_hold.json`, five forms, 2026-10-02).** A hold is a decision and carries its evidence:
`"<box>": "<why>"` holds a whole box; `"<box>/<model>": "<why>"` one model's cells; **`"<box>/<model>/<family>/<thinking>"`
one ARM** — the unit the stop rule judges (model × family × thinking on one box); family is `f1 f2 tool vlm vlm2x` or `*`
(`coding-f1` is accepted), thinking is `off on *`; rigby is `rigby/<gguf>/<family>/<thinking>`. A value is the why string
or `{"why": …, "until": "YYYY-MM-DDTHH:MM", "except": ["0.6/4"]}`: `until` lapses by itself at that local time; `except`
names `<temperature>/<seed set>` cells a model or arm hold leaves open. `python menu_fill.py plan` prints how many grid
cells each model/arm hold stops and warns when a key stops none (a filter that selects nothing must say so). Pinned by
`hard-bench\test_menu_fill.py` (35 tests; the per-box counts below are asserted there against each box's real model list).

**The stop list ordered 2026-10-02** (the three 2026-10-02 menu reviews hold the evidence; cells = grid cells stopped
per box; 4 temperatures × 3 seed sets = 12 cells per arm):

| arm | BigHonker | Softserve | rigby | reason (the rule's two, judged per box) |
|---|---|---|---|---|
| qwen3.5:9b f1 + f2, thinking on | 12 + 12 | 12 + 12 | — | cannot fit: 31–50% of answers end at the 7,168 tested max |
| qwen3-vl:8b f1 + f2, off AND on | 24 + 24 | 24 + 24 | — | cannot fit: the model ignores `think:false`, answers end at the 5,120 tested max |
| Qwen3.6-35B-A3B f1 + f2, thinking on | — | — | 12 + 12 | priced out: 723 of 747 rows, both families, two seed sets |
| qwen3:8b f1 + f2, thinking on | 11 + 11 | 11 + 11 | — | PRELIMINARY price-out (22–50×) from ONE cell per family per box; see the repeat cell below |

Not held (and pinned as not held): qwen3.5:9b thinking off, tool, vision; qwen3-vl:8b tool and vision; qwen3:8b off; the
MoE's tool cells and thinking-off cells; rigby's 7B and 14B. Dominated arms stay (the rule above).
**The repeat cell (qwen3:8b thinking on):** the stop rule needs "reproduced on that box", and each box has one delivered
cell per family (t0.6, seed set 1). The two arm holds per box carry `"except": ["0.6/4"]`: exactly ONE repeat cell per family
per box (a new seed set, the same temperature) is left open, ordered by the filler like any cell and carrying the price
line. When it lands: if the arm is again ≥ 10× the box's cheapest solve, delete the `except` (the arm is stopped); if
not, delete the hold. The earlier queued orders for this arm (they had no price line) were cancelled by slip.
**Standing holds.** Mothership held for the whole menu until the owner says (Flash-Next, 2026-09-29), and
`mothership/qwen3.8-flash-next:125b-a6b-q4_K_M` held on its own (pre-preliminary); BigHonker + Softserve held since
2026-10-01 08:40 on the owner's pause until he resumed them (2026-10-02 ~10:00: box holds removed, the cancel slips below were
read on the first runner pass), and `bighonker/qwen3.5:35b` (2026-10-02:
*"only models that fit, 8B class"*). **The cancel slips** for the stop list (BigHonker 73 queued orders + the stopped
9B f1 order, Softserve 75 + 1; the qwen3:8b thinking-on orders included) were dropped in each box's `jobs\queue` as
`p34-stoplist-NNN.ctl.json` while the factories were paused; a paused factory reads no slips — they are read at the TOP of
the first loop pass after the runner next starts, before any order is picked, so no stop-list order can start before its
cancel is read (the older `pause-*.ctl.json` requeue slips run first and the cancel then retires what they restored).
On 2026-10-02 that is what happened: the desk shows 74 BigHonker and 77 Softserve orders `cancelled` (error text `cancelled on
<box> by operator slip: …`; the desk recorded them as `failed` until the same day's fix, and the records were migrated through
`p20_queue.mark`).
**`OFFLOAD_OK`** (in `menu_fill.py`) names the (box, model) pairs
whose orders carry `infer_ollama --offload-ok` — a model larger than the card, partial residency by design; named by
the owner per box, never inferred from size ([[Qwen3.8-Flash-Next-on-Mothership]]).
**The ledger knows what happened to an order (fixed 2026-10-02).** Per order record: *delivered* (its `out.json`, or the
newest resumed `<folder>.<stamp>` folder — three resumed cells had read as outstanding forever), *outstanding* (the desk
record is pending / held / claimed / running, or `failed: stopped by operator slip`, or there is no desk record yet, or
the desk could not be read) or *dead* (status `cancelled`, or the legacy `failed: cancelled on <box> by operator slip: …`
= dead:cancelled; `failed` for any other reason, e.g. the payload exited non-zero = dead:retired; and since 2026-10-04
`done` with no `out.json` = dead:retired too — a factory ships before it reports done, so that is a failed delivery: the
10-02 long-path ships left 8 BigHonker cells reading outstanding for two days).
`menu_boxstate.py` reads the DESK's own record of every factory order — `State\p20-render-queue\<id>.json`, kind
`factory_order`, matched by `order_name` (the box-side name; Softserve's has no `_softserve` suffix) — read-only, local to
Softserve, so one derivation serves all three GPU boxes with no ssh and no credentials (a first version read each box's
queue/archive over ssh; the scheduled task runs as SYSTEM and cannot). A *stopped* order is not dead: a requeue slip may
resume it and the desk cannot see slips on a box, so `plan` prints stopped orders for a human to requeue or cancel. A dead
cell no longer counts as outstanding and is re-orderable unless held, as attempt N (`-rN` in its tag, its own product
folder); a cell retired without delivering twice is PARKED and printed for a human (machinery, not the cell), and a
human cancel never parks it.
Coding cells go through `hard_bench_fleet_orders.py --model-name`; tool/vision
cells through `hard_bench_fleet_tv.py cell` (each cell its own rung in `fleet-tv\rungs_extra.json`, knobs and price line
carried into the reduce rows and the menu). `python menu_fill.py plan` prints the price line per box × family, and grid /
missing / outstanding / dead / held per box.

**Rigby is in the fill (2026-09-30).** Grid: every GGUF in Tolkien's model manifest (`tabfoundry-jobs\rigby-models.json`)
× coding-f1, coding-f2, tool × thinking (off; also on for `RIGBY_THINKING`, named) × the same 4 temperatures × seed sets
1/4/7 (seed = replicate, so seed set 4 is `items` at replicates 4–6). Submitted as Tolkien `llm` orders through the tablet
INBOX (`hardbench-rigby-m-<model>-<fam>-t<temp>-<think>-s<seed>.<stamp>`, recorded in
`campaigns\hard-bench-20260927\rigby\submissions.json`), `ctx` 32,768 stated on every order (the payload default 2,048
capped the MoE: rigby-12, 29 f2 rows on `length`), and `price_ceiling_s` = the rule below, computed from rigby's
delivered results at submit time (none when no task in the order has a measured rigby solve yet). The ledger reads every
`outbox\hardbench-rigby-*.results.json`, so rigby-1…20 count: a cell is delivered when every (task, replicate) pair is
measured (answered or priced out; stopped, errored, or capped below 32,768 = re-run) and a contributing order passed its
control; a partial cell re-orders only its missing pairs. Top-up keeps **2 × the task interval (30 min) of MEASURED work**
queued (per-answer mean for that model × family from delivered rows; an unmeasured cell counts as one interval) — rigby is
a serial lane, so a queue by count would either starve it on 2-minute cells or bury a special order behind hours of MoE.
Hold it with `menu_fill_hold.json` key `"rigby"`, `"rigby/<gguf>"` or one arm `"rigby/<gguf>/<family>/<thinking>"`
(the MoE's thinking-on f1 and f2 arms are held since 2026-10-02). The tablet shapes are still not in the fill.

**Price ceilings — "priced out" is a measured cost ratio, never a round number** (the owner 2026-09-29: *"It's based on real
costs not arbitrary time"*; doctrine 13a). An answer is priced out once it has cost **10× the cheapest measured solve of
that task family on the SAME box** (the row prices that box; "is another box cheaper" is the routing question, not
the price). **"Cheapest solve" = the cheapest ARM's cost per solve** (2026-10-07, the owner: *"the Priceline should
definitely be computed from the intelligent one. I don't want us to be throwing out results for the top-tiers
unjustly"*): per arm (model × thinking) on the task, the wall of ALL its cold answers ÷ the ones it got right — the
charter's "per solved" metric, failures charged. Before this, the single fastest correct answer by ANY arm set the line,
so a fast mostly-wrong arm (Nemotron 30B thinking-off, C1: 0.4 s, 36 % right) set a ~4 s line that priced out its own
97 %-right thinking arm — and the 3.8 survived the same arithmetic only by its champion exemption. Re-judged that day:
15 arm-families un-stopped (Nemotron 30B C1/W1 on, Gemma 3 27B C1/W1, qwen3:8b C1 on, …), none newly stopped
(`menu_price.reference`, test `a_fast_mostly_wrong_arm_does_not_set_the_line_for_a_smart_one`). Rigby's energy is metered (the rack's smart plug, ≈ 99 W working / 54 W above idle — §"Rigby (S5r) energy");
its draw is flat across models, so its time ratio IS its energy ratio. GPU boxes carry energy (4 Hz board power).
A priced-out answer is a stated measurement (`priced_out`), never a "fail", and it is NOT a cap row (`done_reason`
"length", VOID by doctrine 13a). **Every order carries its price line (GPU since 2026-10-02):** Tolkien llm orders as
`price_ceiling_s`, GPU orders as `infer_ollama --price-ceiling-s` — the payload closes the stream once an answer has cost
more than that, keeps the partial text, never grades it, and records `priced_out: true` / `done_reason` "priced_out";
a `control*` item is never cut. One value per order (the worst-case cheapest solve across the order's tasks), computed
by the filler PER BOX per family from that box's own delivered rows (`menu_price.py`; one box's rows never set another's).
One classifier (`menu_price.row_status`) serves the reducers, `build_menu.py` and the tests: priced_out rows are
measurements (counted not-solved, own `priced_out` column), capped rows are void (`usable` False, `void` = cap, left
out of `coverage.md`). First use: `hardbench-rigby-16r4-qwen36moe-f2-ctx32k-priced`
— rigby's Qwen2.5-14B solves these family-2 tasks at ≤ 69 s → **690 s**; the MoE's own solves ran median 198 s with
runaways of 2.7 h (correct) and 3.6 h (hit the 32,768 cap, failed), so on family 2 the MoE is priced out against the
14B on its own box. **The ceiling is ADJUSTABLE BY CONSTRUCTION — it tracks the data** (the owner 2026-09-30: *"probably meant
to be an adjustable ceiling as we find more data"*). The 690 s here and rigby-20's 86 s were the rule applied with ONE
reference model each (the 14B; the small coders); the menu filler recomputes it from EVERY delivered result on the box
at each submission — per task, the cheapest COLD solve by any model; the order's ceiling is 10× the worst of those.
**The reference excludes cache-warm rows (2026-10-02; the owner did not veto the review's option).** A row whose prompt came
out of the server's prompt cache read almost none of it, so its wall time prices a cache hit, not the task: rigby's tool
ceiling had fallen to 12 s, below the 14.5 s the MoE needs just to read a cold tool prompt. A row is warm when the prompt
tokens it processed are under half (`WARM_FRACTION` 0.5, in `menu_price.py`) of that (box, family, task, model)'s cold
size; on rigby's result files the ratio is bimodal (10,377 rows under 0.25, 3,442 at 0.65 or more, none between), and a
solved row with no prompt count is left out and counted, never assumed cold. ollama's GPU rows report the whole prompt
every time, so no GPU row reads warm and the rule moves nothing there. **The reference, 2026-10-02** (order ceiling in
seconds, before → after the warm-row fix; `python menu_fill.py plan` prints it live):

| box | f1 | f2 | tool | vlm | vlm2x |
|---|---|---|---|---|---|
| BigHonker | 19 → 19 | 24 → 24 | 7 → 7 | 53 → 53 | 67 → 67 |
| Softserve | 17 → 17 | 20 → 20 | 6 → 6 | 31 → 31 | 54 → 54 |
| Mothership | 11 → 11 | 9 → 9 | 3 → 3 | 7 → 7 (23 of 24 tasks priced) | 37 → 37 |
| rigby | 47 → 62 | 64 → 192 | 12 → 26 | — | — |

The sub-second GPU solves are 0.1 s-resolution wall times, so those ceilings are tight by construction (the Softserve
review's caveat); the 10× stays the owner's call. The reference falls when a cheaper cold solve arrives and rises when warm rows
are excluded, and each order carries the value of ITS OWN tasks, so **per-order values are not monotonic** (rigby's log:
f1 184 → 55 → 47 s; a small partial order can carry 63 s and a full one 98) — the earlier "a ceiling only tightens" was
true of the reference's floor, not of what each order carried. The **10×** is Claude's owner call (2026-09-30 04:49,
the owner: *"Idk, what makes sense?"*) and stays the owner's to change (`PRICE_X` in `menu_fill.py`).

## Thinking EFFORT (xhigh / medium / low) — what "thinking on" means per model (2026-10-07)

`on` is NOT one setting across models. **qwen3.8** (and its renderer family) has reasoning efforts xhigh (vendor
default) / medium / low; ollama 0.34.0 renders `think: true` and an unset think as **medium**, and only the API string
`"high"` (or `"max"`) as xhigh (`"xhigh"` itself is rejected by 0.34.0's `api.ThinkValue`). So the menu's
`qwen3.8:27b … on` rows are MEDIUM-effort data, and the champion's ceiling is the separate **`xhigh`** arm:
`menu_fill.EFFORT_LEVELS` adds it to the grid, `submit_cell` passes `--think-level xhigh`, the submitters forward it,
`infer_ollama.OLLAMA_THINK` sends `"high"`, and `menu_fill.think_label` labels every row (one derivation, also used
by `build_menu`). A1: `submit_a1.py --effort=xhigh` (name suffix `-exhigh`). Every other model's `on` is already its
maximum (audit `hard-bench\effort_audit_20261007.md`). The champion also never carries a price line (`CHAMPION`):
the old `on` rows on f1/f2/tool include 88 answers the 3–8 s lines cut mid-thought. Re-prove the rendering after any
ollama upgrade (first-turn prompt tokens: xhigh − medium ≈ the effort sentence).

## Why (one paragraph)

Phase 5 measured ensembles on the Phase 4 task sets and found the floor: one tablet already scores 1.0 on chat
and tool and ~0.86 on coding and vision, with the misses identical on every tablet. Voting, best-of-N and
critic/refiner stages had no room to move, so the result reads "ensembles buy latency." That is true on that
bench and says nothing about harder work. Phase 7 asks what a pile of tablets can do that one cannot; it needs
a bench where one tablet fails often enough for an ensemble to show up. This order builds that bench and runs
the strategies that can plausibly move on it. Same machinery as Phase 5 end to end; the only new thing is the
task pool.

## Order (inputs · method · deliverables · cleanup)

**Inputs**
- Candidate task pool per division (coding, tool, vlm; chat only if a selector exists — see caveat), 3–5× the
  target count, drawn from the existing payload families with harder instances (multi-step code with hidden
  tests, tool calls needing two arguments derived from context, vision cells with two facts to read). Gold and
  deterministic checks per task, same contract as the Phase 5 payloads.
- Models held constant at the Phase 4 champions per role (Coder-1.5B coding, Qwen2.5-1.5B general/tool,
  SmolVLM2-500M vlm).
- **Decoding SAMPLED, not greedy.** Phase 5's payload ran `--temp 0 --seed 1` in every division, so same-model
  tablets could only disagree through hardware noise — an ensemble of identical answers has nothing to vote
  over. Here every division decodes at temperature ~0.6 with `seed = replicate + 1`: `map_swarm_code.py` and
  `map_tool_swarm.py` sample this way natively, and the vlm division does through the generated
  `map_vlm_swarm_hard.py` (`--temp 0.6 --seed <item seed>`; the frozen `map_vlm_swarm.py` stays greedy). This is
  why the calibration runs 5 seeds per task: with sampling, a per-task pass RATE exists; at temperature 0 it
  would be a single 0/1 and "middle band" would be meaningless.
- **Execution order:** coding first, started 2026-09-26 late on the owner's "run it"; tool and vlm calibrations
  submitted 2026-09-27 evening (§ "Tool and vlm pools").

**Method — two stages, the first gates the second**
1. **Calibration (under an hour of wall time, the whole Fellowship at once).** "Single-tablet pass rate" means
   one tablet per run, not one tablet for the whole job: every (task, seed) pair is an independent
   single-tablet run, so the ~300 pairs are ONE map order dealt across every healthy node (Tolkien's standard
   `map.window` / `distinct_workers` dealing — the owner, 2026-09-26: *"let's just be smart and do the same test
   concurrently on multiple tablets… no need for the others to sit idle"*). Each row records its host, as the
   Phase 5 reducer already does, so any per-SKU drift in pass rate is visible rather than averaged away.
   Keep the tasks whose pass rate over 5 seeds lands in the 0.3–0.7 band, ranked, take the top K per division
   (K ≈ 6–8). If a division yields fewer than 4 such tasks, that division is reported as "no middle band at this
   model size" and dropped — that is a finding, not a failure.
2. **Ensemble matrix on the kept tasks.** Strategies that can plausibly move only:
   independent swarm (control); best-of-N with the REAL verifier (coding, tool); majority vote (vlm, coding).
   N ∈ {1, 3, 5, 9}, **3 seeds per cell** so each cell reports a spread. Refiner layouts EXCLUDED until the
   transcript-capture defect is fixed (closeout §3.1); when it is, they join as a follow-on slice.
   Chat and vlm best-of-N EXCLUDED (oracle without a selector — same caveat as Phase 5).

**Deliverables**
- Calibration table: task, single-tablet pass rate over 5 seeds, kept/dropped.
- Per-cell results with spread (min/mean/max over seeds), any-tablet-right rate, wall time — the Phase 5
  `summary.md` shape with a seeds column.
- The same closeout shape as Phase 5 (layer 1 code reduce → layer 2 grader audit on real-reply failures →
  layer 3 HAI draft, supervisor audit, the owner rules).

**Cleanup**
- Windows retire through Tolkien's standard contract; product dir `tabfoundry-jobs\campaigns\hard-bench-<date>\`;
  no hand-placed files.

## Size and cost (rough, to be replaced by the calibration's own numbers)

Calibration: pool of ~60 tasks × 5 seeds ≈ 300 single-tablet runs dealt across ~10 healthy nodes ≈ 30 runs
per tablet — under an hour of wall time at Phase 5's ~1 min per window.
Matrix: 3 divisions × ≤3 strategies × 4 N × ~7 tasks × 3 seeds ≈ 750 arms — about a quarter of Phase 5's
1,172, so roughly a couple of days of tablet time with holds. Zero new code beyond the task pool and a
`seeds` column in the reducer.

## Calibration result — coding (ran 2026-09-26 23:59 → 2026-09-27 00:22 CT, on "run it")

Two passes (5 seeds on all 21 tasks, then 5 NEW seeds on the 11 borderline ones): **160/160 runs, 17/17
windows usable, controls green**, dealt across 10 nodes (Sméagol unreachable, correctly not offered).
Products: `tabfoundry-jobs\campaigns\hard-bench-20260926\calibration.md` (+ `.csv`, `_rows.csv`).

| bucket | tasks (10-seed pass rate where measured) |
|---|---|
| always pass (1.0) | add_reverse_param, fix_average_empty, fix_binary_search, fix_dedupe_order, int_to_roman, merge_intervals, rle_encode, roman_to_int, rotate_matrix, search_insert |
| in band 0.3–0.7 | caesar 0.6 · valid_ipv4 0.6 · longest_common_prefix 0.5 · top_k_words 0.3 |
| rarely right 0.1–0.2 | flatten 0.2 · balanced_brackets 0.1 · eval_rpn 0.1 · rle_decode 0.1 |
| never (0.0) | group_anagrams · parse_csv_line · word_wrap |

Per-host pass rates sit within binomial noise at n≈16 per node (no host effect claimable); wall 30–47 s.
**A middle band EXISTS for the 1.5B coder** — the risk named below did not materialise for coding.

## Matrix result — coding (ran 2026-09-27 07:59 → 08:58 CT on the owner's "let's try out the 8")

**120/120 arms, 540 tablet runs, 55/55 windows usable, controls green.** One physical arm per (task, N,
repeat); the three strategies are selectors over the same rows. Products:
`tabfoundry-jobs\campaigns\hard-bench-20260927\matrix.md` + `matrix_cells.csv`.

| N | per-tablet accuracy (swarm mean) | **best-of-N with the verifier** | majority of verdicts | majority of canonical answers | wall max s |
|---|---|---|---|---|---|
| 1 | 0.417 | 0.417 | 0.417 | 0.417 | 33.2 |
| 3 | 0.417 | **0.708** | 0.417 | 0.042 | 38.9 |
| 5 | 0.383 | **0.750** | 0.458 | 0.083 | 46.4 |
| 9 | 0.403 | **0.917** | 0.417 | 0.042 | 49.1 |

(means of the 8 in-band task means; N=1 rows are 3 samples per task, so read the N=1 column as ≈0.4 ± noise;
pooled per-tablet accuracy over all 432 in-band samples is **0.400**.)

**PRELIMINARY readings (one division, one model size, one seed block per cell; no energy or verification
cost measured yet — the owner, 2026-09-27: *"we shouldn't be saying authoritative things if we can't prove
authoritative things"*). A reading graduates when it holds across a second task family and reproduces once.**
1. **With a verifier, best-of-N rose the way independent draws predict.** From the calibration pass rates
   (1−(1−p)^N, mean over the 8 tasks): 0.31 / 0.59 / 0.71 / 0.83 at N = 1/3/5/9; observed 0.42 / 0.71 /
   0.75 / 0.92 — same shape, observed slightly above because this seed block's per-tablet rate (0.40) sits
   above calibration's (0.31). Consistent with independent tries; not yet proof of it (one block, one family).
2. **Per-tablet accuracy is flat in N** (0.38–0.42) — adding tablets does not make any one tablet better,
   exactly as Phase 5 found; the gain is entirely in SELECTION.
3. **Majority vote does nothing here** (0.42–0.46 by verdict), and majority-by-canonical-answer is ~0 with
   59 of 96 cells tied: sampled code answers almost never match textually, so answer-voting is the wrong
   selector for code. Voting needs a verifier or a canonicaliser (an AST-level or test-behaviour equivalence),
   which is a Phase 8 tool, not a tablet property.
4. **Cost:** window wall max rises 33 → 49 s from N=1 to N=9 (+48%) for +0.5 absolute success. That is the
   trade Phase 7 gets to price: 9 tablets and 1.5× latency buy 92% vs 42% on tasks a single tablet fails more
   often than not.
5. **Anchors moved and it is the calibration's n, not the grader:** `rotate_matrix` ("always", 5/5 seeds)
   reads 0.85 per tablet over 54 matrix samples; `group_anagrams` ("never", 0/10 seeds) reads 0.17 — the
   0.0/1.0 labels at n ≤ 10 were extremes of small samples. The reducer's footnote now says so; treat
   "always/never" from ≤10 seeds as "≥0.8" / "≤0.2".
6. **Scope:** coding only, one model size, one seed block per (N, repeat). Tool and vlm calibrate in their own
   lanes (§ "Tool and vlm pools"); chat/vlm additionally need a selector before best-of-N means anything for them.

## Size ladder + fleet rungs — first results (2026-09-27 afternoon; PRELIMINARY, same rule as above)

**0.5B coder rung (tablets), landed 15:12** (product `hard-bench-20260927\matrix-05b.md` + `matrix_cells-05b.csv`;
its first reduce had overwritten the 1.5B `matrix.md` — both rebuilt from their manifests 2026-09-28, the 1.5B
table identical to the one below): 104 of 120 arms usable — 16 arms VOID at Tolkien's own contract
("N/M measured; 1 unaccounted for; distinct-worker FAIL": one tablet's row missing from a window; Sméagol
was correctly never offered) — so the 0.5B numbers carry fewer repeats per cell than the 1.5B run:

| N | per-tablet accuracy (0.5B) | best-of-N with verifier (0.5B) | 1.5B for comparison |
|---|---|---|---|
| 1 | 0.333 | 0.333 | 0.417 |
| 3 | 0.111 | 0.190 | 0.708 |
| 5 | 0.256 | 0.444 | 0.750 |
| 9 | 0.333 | 0.682 | 0.917 |

Reading, held loosely: the 0.5B is a worse single attempt (≈0.3) and its best-of-9 (0.68) lands about where the
1.5B's best-of-3 does — i.e. on this bench 9 × 0.5B ≈ 3 × 1.5B in success, which is the compute-matched
question in one row. The N=3 dip is the VOID arms, not a property of the model.

**Fleet rungs as factory orders (same 10 tasks, 3 rounds, production sampling):**
- **27B on Mothership** (`num_ctx` 8192, fully resident): 24/24 in-band attempts pass, 3.7 s per answer — the
  ceiling the tablets are priced against.
- **9B on BigHonker** (`hardbench-fleet-9b.20260927-182814`, `num_ctx` 6144 = the box's production context,
  34/34 layers on GPU): 33/33 rows, per-attempt 0.750 on the 8 in-band tasks, best-of-3 1.000, 5.2 s per answer.

| rung (same 8 in-band tasks, sampled) | per attempt | best-of-3 | best-of-9 | s / answer |
|---|---|---|---|---|
| 0.5B coder, tablets | 0.33 | 0.19 (VOID-thinned) | 0.68 | ~45 |
| 1.5B coder, tablets | 0.40 | 0.71 | 0.92 | ~40 |
| 9B, BigHonker (6144) | 0.75 | 1.00 | — | 5.2 |
| 27B, Mothership | 1.00 | 1.00 | — | 3.7 |

PRELIMINARY (one task family, 3 rounds on the fleet rungs).

**Energy per solved task (2026-09-27 night).** GPU rungs meter themselves inside the order (board power,
`infer_ollama.py`): **9B ≈ 600 J per solved task (≈ 350 J above idle); 27B ≈ 1,200 J.** Tablets are measured at
the wall: Softserve's APC (NS 1500M2, 900 W, 9 W per load step) also powers Rigby, which powers the tablets. Five
work stretches today, each paired with the idle 5 minutes before it (Home Assistant's `sensor.softserve_load`
history × Tolkien's result-file windows, `energy_history.py`): extra draw **6.3–11.0 W for whole-Fellowship work**
(1.7 W for a 2-tablet run). The controlled run (50 attempts of the 1.5B coder, 20 solved, 9 min) read +9.5 W →
**≈ 250 J per solved task at the wall — a FLOOR**: during it gandalf and merry drew on their batteries and pippin
fell 100 → 97 %, so part of the work was paid from batteries the wall never saw. Not like-for-like with the GPU
numbers (wall vs board). Products: `campaigns\hard-bench-20260927\energy\`.

**Rigby with the GTX 1060 on the x16 riser — measured 2026-10-30: working adds ≈ 78 W at the wall** (card ≈ 15 +
CPU ≈ 63), the value in `menu_fill.RIGBY_PLACEMENT` (was 85 W, four APC pairings in the x1 era). Method (the owner: *"look at
softserve activity to differentiate"*): `hard-bench\apc_split.py` regresses the APC's per-second load (Softserve's own
telemetry) on rigby's 1060 board power and CPU % (`hard-bench\rigby_telemetry_bins.py`, Tolkien telemetry) plus
Softserve's GPU W and CPU %, over 2026-10-26 15:00 → 10-30 14:00 (26,040 ten-second bins; R² 0.92–0.97, stable from 10 s
to 2 min bins). Controls: Softserve's GPU 1.20 W per board W; rigby's CPU 1.40 W/% × its 40-point swing = 56 W, the
plug's independent 54–56 W below. The 1060 idles at 27 W board (~33 W wall) around the clock — a standing cost no J/solve
shows. Its 600 W supply is on the APC (the card's slope matches Softserve's). Re-run after any hardware change.

**The menu prices the WHOLE BOX at the wall per answer — since 2026-10-30** (the owner 10-17, again 10-30: *"that's…part of
the spec for the menu"*; his definition: the TOTAL draw while the job runs, since some day the boxes sleep between
jobs, with the extra-over-idle and the card shown beside it). `menu-cells.csv`: `energy_j` = whole box (what the
`least_energy` lens prices), `wall_extra_j` = that minus the box's idle, `card_j` = the card meter (the old
`energy_j`), `wall_src` = `measured` / `modelled` / `modelled (no window)`. Code: `hard-bench\menu_wall.py`.
- **Measured** where the box's own wall log covers ≥ 90 % of the answer's `t0`–`t1`: Mothership's CyberPower log,
  BigHonker's APC columns (×4.5 W), Softserve's APC (×9 W) minus rigby's era idle + working extra while a Tolkien order
  ran. 2026-10-30 build: 531 / 144 / 400 answers.
- **Modelled** otherwise (≈97 % of answers predate the wall logs): each box's line wall W = a + k × card W (+ c × CPU %
  on Mothership, the one box whose telemetry records CPU), fitted on 60 s bins (`wall_calibration.json`):
  Mothership 91 + 1.14 × card + 0.71 × CPU% (R² 0.984, 16,309 min, idle 99 W); BigHonker 43.5 + 1.25 × card (R² 0.980,
  idle 70 W); Softserve 75 + 1.28 × card (R² 0.63 — Plex/Hyper-V CPU is not in its telemetry; idle 96 W). The card
  slopes 1.14–1.28 are PSU loss (positive control); Softserve's matches the morning's independent regression (1.20).
- **Validation** on the measured answers: model ÷ measured median 0.996 / 0.979 / 1.016 (p10–p90 0.92–1.11 /
  0.96–1.02 / 0.95–1.17), and the answer's own `gpu_j` equals the telemetry card watts (1.000), so calibrating on
  telemetry and applying on `gpu_j` is one instrument. Holding out the answers' own minutes reproduces it; ⚠ holding
  out the whole of 10-18 does NOT on Mothership (reads 8 % high, CPU term 0.87–1.39 by week): the CPU term is fixed only
  by heavy-CPU minutes (`cpu_over_30_bins`, 644 in this fit). Re-run `menu_wall.py calibrate` after hardware changes.
- **rigby** = idle for the placement's era (76 W on the x16 riser: plug 46.9 + the 600 W supply ~29) + the placement's
  working 78 W = 154 W; unplaced models = the era's idle + the 54 W plug marginal (`menu_policy.rigby_watts`).
  **Tablets are priced at their port's SPEC ceiling** (the owner 2026-10-30: *"go off of their spec sheet"*): 2.5 W per
  tablet × wall (a USB data port, 500 mA × 5 V, not raisable in software — a burst above it is battery the same port
  refills), × n tablets for an ensemble row (`menu_wall.TABLET_PORT_W`, `tablet_j`). A CEILING; the measurement above
  (~2 W extra per working tablet) is a FLOOR. Same rule in `reduce_chat1` and the A1 reducer (`reduce_a1`), which
  also price GPU rows at the wall and rigby as its whole box since 2026-10-30.
- The logs reach Softserve by `menu_wall.sync` (build_menu runs it): one ssh per box running `wall_tail.py`, which
  reads each log only past the bytes already cached — a growing log costs its new bytes.
- **What it showed (the owner: *"no way Rigby is the most costly … it's the least power hungry hardware"*)**: rigby IS the
  lowest-watt box and the dearest per W1 solve, because energy is watts × time. Same file (35B-A3B MTP, thinking off,
  0.6): rigby 102 s × 154 W = 15.7 kJ per solve; BigHonker 58 s × ~186 W = 10.9 kJ; Mothership's own 35B-A3B 9.4 s ×
  ~418 W = 3.9 kJ. Over idle only, rigby ~8.0 kJ, BigHonker ~6.8 kJ, Mothership ~3.0 kJ.

**Rigby (S5r) energy — metered 2026-09-29/30 on the rack's smart plug.** Rigby (and the tablets it charges over USB) sits on
the rack's smart plug (Govee H5086) since 2026-09-29 ~14:00. HA and the Govee API expose only on/off for it
(HomeAssistant-Energy); the wattage comes from the **Govee app's CSV export** (hourly kWh). Joined with Tolkien's
result files (`finished` − `wall_s` = each order's window): **working ≈ 97–101 W at the wall, idle ≈ 41–46 W →
≈ 54 W marginal** (plug 99 − 43 = 56; cross-check on the APC's STOP edges, where Softserve's own load cancels:
+53.1 W at 16r4's 01:16 stop, +51.1 W at rigby-20's 08:52 stop). Positive control: the 01:00 hour held 16 min of
work + 44 idle, which predicts 56.5 W; the plug read 56.6. The APC START edge at 08:08 (+123 W) is VOID — Softserve's
own orders resumed at 08:00 in the same window — and APC hour means are unusable (Softserve's work sits inside them).
⚠ The draw was measured under the MoE (plus 14 min of the 14B, which read slightly higher); the small models are
ASSUMED to draw the same until a plug hour holds one of them alone.

| rigby, controls excluded | fam | solved/rows | J per solve @ wall (99 W) | J above idle (54 W) |
|---|---|---|---|---|
| Coder-0.5B | f2 | 30/72 | 382 | 208 |
| Coder-1.5B | f2 | 42/72 | 550 | 300 |
| Coder-3B | f2 | 59/72 | 803 | 438 |
| Qwen2.5-7B (plain; draft variants within 2 %) | f2 | 60/72 | 1,680 | 916 |
| Qwen2.5-14B (plain) · spec-draft · ngram | f2 | 66/72 | 3,171 · 2,860 · 3,960 | 1,730 · 1,560 · 2,160 |
| Qwen3.6-35B-A3B MoE (5 orders, runaways incl.) | f2 | 108/146 | 65,226 | 35,578 |
| Coder-0.5B · Coder-1.5B | f1 | 11/30 · 9/30 | 1,967 · 1,308 | 1,073 · 713 |
| Qwen2.5-7B | f1 | 25/30 | 2,111 | 1,151 |
| Qwen2.5-14B | f1 | 21/31 | 5,861 | 3,197 |
| MoE (rigby-20, ceiling 86 s) | f1 | 0/30, all priced out | — | — (order VOID: control capped too) |

Against the GPU rungs above (9B ≈ 600 J board, 27B ≈ 1,200 J board), rigby's 7B costs ~2.8× the 9B per solve at the
wall, and its 14B ~2.6× the 27B — not like-for-like (wall vs board), and one round per cell. **The price ceiling does
not change**: rigby's draw is flat across the models measured, so "10× the cheapest solve on the same box" in seconds
and in joules is the same ratio. Script: `scratchpad\rigby_joules.py` pattern (reads every
`results/hardbench-rigby-*.results.json` on rigby).
**3B coder on the tablets: does not fit any class — its rung is Voltron / the Tolkien host, not individual
tablets** (the owner, 2026-09-27). Class B/C: every attempt hit the 400 s generation timeout. Class A (gandalf,
aragorn; 2.9 GB RAM): the weights alone are 1,840 MiB against 1,712 MiB MemAvailable at idle; in all three
class-A windows Android's low-memory killer took Termux (worker + sshd) mid-generation ("Connection closed by
remote host", aragorn logged `system_server_lowmem` inside the window), and the Softserve Termux re-armer then
rebooted both nodes. The 54 held 3B matrix windows were cancelled by one prefix slip.

## Tool and vlm pools (2026-09-27 evening)

Same shape as coding: a candidate pool 3–5× K, gold proven before any tablet time, calibration at 5 seeds per
task through the standard Tolkien windows, then the band. All files in
`Claude Home\phase5-prep\commission-artifacts\hard-bench\`; payloads are GENERATED next to the frozen ones
(frozen files never edited — Phase 5 manifests pin their sha).

| | tool | vlm |
|---|---|---|
| pool | `hard_tool_tasks.py` — 24 tasks, 8 tools with near-neighbours (read vs search log; restart vs stop vs scale), derived arguments (words→numbers, "double it", negations, distractor numbers), a 3-argument tool, 5 no-fit traps | `hard_vlm_tasks.py` — 24 cells on 22 images rendered from code, each carrying a distractor fact (a second number, other colours/labels); numeric and letter golds use `word` match |
| payload | `map_tool_swarm_hard.py` via `gen_tool_hard_payload.py` (arm `tool-hard`; GBNF + PREAMBLE written with `repr()` of their runtime text and byte-checked) | `map_vlm_swarm_hard.py` via `gen_vlm_hard_payload.py` (arm `vlm-hard`; the sampling knob; control `n_number` kept out of the task table) |
| model | Qwen2.5-1.5B-Instruct-Q4_K_M | SmolVLM2-500M-Video-Instruct-Q8_0 |
| gold proof | every gold call scores full marks and one wrong call per task is refused, through the payload's own `score_tool`, plus `process_item` driven with llama stubbed | every image asserts no element leaves the margin and no text overlaps, then was checked by eye; gold passes and a distractor answer fails through the payload's own `_vlm_score` and `run_vlm_cell` path; images served byte-identical from rigby's store (`vlm_set/h_*.png`) |
| calibration | `hard_bench_calibrate.py submit --lane tool` — run `hardbench-tool-cal-r2.20260927-193946`, 120 items | `--lane vlm` — run `hardbench-vlm-cal.20260927-193946`, 120 items |

Reduce: `hard_bench_calibrate.py reduce <manifest>` writes `calibration-tool.*` / `calibration-vlm.*` beside
the coding files.

**Calibration results.** Tool (1.5B, 5 seeds, twice): 18 tasks always right, 6 never, none between — sampling does
vary the answers (6 tasks gave different answers across seeds) but never turns a wrong call right, so tool has
**no middle band at 1.5B** and leaves the ensemble matrix. VLM (SmolVLM2-500M, 10 seeds on the borderline tasks):
12 always, 5 never, 7 sometimes (0.2–0.9). The vlm matrix is **majority vote only** (best-of-N excluded, no
selector), and a vote can only move a task answered right sometimes, so it runs on all 7 "sometimes" tasks —
deliberately not coding's 0.1–0.7 band, whose reason for reaching down was best-of-N with a verifier. Run
`hardbench-vlm-matrix.20260927-215020`, `hard_bench_matrix_vlm.py`.

**Fleet rungs on the same pools** (`hard_bench_fleet_tv.py`, temperature 0.6, 3 rounds, graded by the tablet
payloads' own scorers; products `campaigns\hard-bench-20260927\fleet-tv\`):

| rung | per attempt | always / never / sometimes | J per solved (GPU board) |
|---|---|---|---|
| tool, qwen3.5:9b (BigHonker) | 0.89 | 19 / 1 / 4 | 94 |
| tool, qwen3.6:27b (Mothership) | 1.00 | 24 / 0 / 0 | 313 |
| vlm, qwen3-vl:8b (BigHonker) | 1.00 | 24 / 0 / 0 | 264 |
| vlm, qwen3.8:27b (Mothership) | 0.89 | 20 / 1 / 3 | 140 |
| vlm, qwen3.6:27b (Mothership) | 0.53 | 11 / 10 / 3 | 297 |

For tool calls size is the lever, not attempts.

**Grader ruling (the owner, 2026-09-28): tool arguments match STRICTLY** (case-insensitive exact string, as
`score_tool` does). The one contested cell is `scale_double` ("The search service has 3 replicas right now; double
that."): the 1.5B answered `{"service": "search_service", "replicas": 6}` in 4 of 5 tries against gold `"search"`.
The prompt lists no service names, so this is a naming question the task left open, not a model reasoning error;
it stays a miss. Truncation was ruled out first: all 120 failed tablet answers (tool 30, vlm 1× 50, vlm 2× 40)
equal the raw result files byte-for-byte, none is near the vlm `-n 32` cap, and every tool answer is complete JSON. **The vlm rows above are VOID as model comparisons: the method
handicapped them.** A one-variable probe (`vision_factor_probe.py`, 3 known-answer cells × image scale 1×/2×/4× ×
thinking off/on, 3 rounds each) found: at the pool's 480×320 the Qwen 27Bs' vision encoder spends only ~130 prompt
tokens on the image (175 total vs ~625 at 2× and ~2,425 at 4× — the counts DO include the image) and they cannot
read the digits; at **2× both qwen3.6:27b and qwen3.8:27b score 9/9 with thinking on**; the only other misses were
counting with thinking OFF (qwen3.6 at 1× and 4×, qwen3.8 at every scale). The vlm pool is re-rendered at 2× and
every vlm rung — tablets included — re-run with thinking on (§ "VLM at 2×" below). Mothership's energy comes from
its 11-s telemetry log, so its per-solved figures are coarser than BigHonker's 4 Hz meter. Every hard payload cuts the model's answer at the measured boundary in
[[Fellowship-Inference-Output-Format]] and stores it whole; a row whose output shape is unrecognised is an
instrument failure (`answer_boundary:…`), excluded from pass rates and counted in the reduce header. The first
tool calibration (`hardbench-tool-cal.20260927-191559`) predates that and stored only llama-cli's spinner as its
answers; its pass rates were scored on the full text (18 tasks 5/5, 6 tasks 0/5, none between), and the -r2 run
repeats the same seeds with the answers kept.

## VLM at 2× (2026-09-27 night; PRELIMINARY, one run per rung)

The vlm pool served at 960×640 (`hard_vlm_tasks.py` renders each cell at 480×320, where the margin and overlap
checks run, and serves a nearest-neighbour upscale named `h_*_2x.png`). Fleet rungs ran with thinking ON
(`infer_ollama.py --think`); tablets as before.

| rung | per attempt | always / never / sometimes | J per solved | product |
|---|---|---|---|---|
| qwen3.6:27b, Mothership | 1.00 | 24 / 0 / 0 | ≈4,200 (telemetry-csv, coarse) | `fleet-tv\fleet_tv.md` row `vlm2x-27b-q36` |
| qwen3.8:27b, Mothership | 1.00 | 24 / 0 / 0 | ≈940 (telemetry-csv, coarse) | row `vlm2x-27b-q38` |
| qwen3-vl:8b, BigHonker @ `num_ctx` 4096 (2026-09-28) | 1.00 | 24 / 0 / 0 | ≈260 (nvidia-smi@4Hz; 1× row 264) | row `vlm2x-8b-bh4k` |
| qwen3-vl:8b, Mothership @ 8192 (2026-09-28) | 1.00 | 24 / 0 / 0 | ≈470 (telemetry-csv; different card) | row `vlm2x-8b` |
| SmolVLM2-500M, tablets (5 seeds) | — | 13 / 6 / 5 (1×: 12 / 5 / 7) | — | `calibration-vlm-2x.md` (1×: `calibration-vlm.md`) |

**qwen3-vl:8b never saw a bigger image.** Ollama resizes its input to a fixed budget: 1,109 prompt tokens per
question at 1× and 1,111 at 2× (the 27Bs went ~175 → ~630). Its 2× row is a repeat of the 1× question with
thinking on, not a test of image size. Its first 2× order (2026-09-27) was refused on BigHonker only because the
ORDER asked for `num_ctx` 8192, a chat-sized window that does not fit the 8 GB card (~6.3 GB needed, ~5.6 free);
the largest request is ~1,800 tokens (1,115 prompt + 681 output). Context is sized per job (the owner, 2026-09-28): at
4096 on BigHonker it ran 24/24 with every answer ending normally (`done_reason: stop`). Softserve's 3070 Ti runs the
same order after the 4× tablet run (its UPS is the tablets' energy meter).

Readings, held loosely: the Qwen 27Bs' 1× misses were the method (images too small, thinking off), not the models.
Thinking has a real price: qwen3.6 spent roughly 4× qwen3.8's energy per solved task (one run; the size of the gap
is rough). The tablet model barely moved at 2×; its task mix reshuffled (`h_word_second` rose from never to 0.6,
`h_corner` from sometimes to never) rather than improved. The 4× tablet run tests whether larger images help it at
all (`--lane vlm4x`, payload `map_vlm_swarm_hard_4x.py`, images `h_*_4x.png`).

**4× tablet run (2026-09-28, `hardbench-vlm4x-cal.20260928-083048`, 1920×1280, the owner's hunch that SmolVLM2 reads
better from larger images): it does not.** Always / never / sometimes 14 / 4 / 6; pooled 77/112 = 0.688 against
0.677 (1×, 105/155) and 0.667 (2×, 80/120) — flat within noise. Per-task shifts (`h_corner` never → 0.6,
`h_light_yellow` → always) are n = 5 cells; counting stays never at every size. Cost: ~190 s per answer vs ~44 s
(2×) and ~30 s (1×). The image does reach the model (encode time rises with size), it just does not help. Window 11
was VOID at Tolkien's contract (merry took two items; all 8 answers were correct), so 4× is slightly understated
(85/120 = 0.708 if counted) — the conclusion is unchanged. Product: `hard-bench-20260928\calibration-vlm4x.md`.

Products per image size are separate files: `calibration-vlm.*` is the 1× run (three manifests: `…193946`,
`…-re…201218`, `…-p2w8…204044`), `calibration-vlm-2x.*` the 2× run, `calibration-vlm4x.*` the 4× run. The 2× reduce
originally wrote over the 1× file of the same name; the reducer now refuses to overwrite a product from a different
run (`reduce --tag`). The generated payload `map_vlm_swarm_hard.py` now holds the 2× image names (sha `bd18710…`);
the 1× generation (sha `7a9275e…`) is pinned only in its manifests.

## Small shapes + temperature knob (2026-09-28; PRELIMINARY, one run each)

Tablets, 1.5B coder, the 10 matrix tasks × seeds 1–5 at temperature 0.0 / 0.3 / 0.6 / 0.9 (runs
`hardbench-code-cal-{t00,t03,t06rep,t09}.20260928-1137xx`, 50/50 rows each; product
`hard-bench-20260928\small_shapes.md`, `hardbench_small_shapes.py`):
- **Temperature:** pass rate 0.500 / 0.360 / 0.320 / 0.360. Greedy (0.0) is the best SINGLE attempt, but every seed
  gives the same verdict on every task (10/10 tasks identical across 5 seeds), so N > 1 adds nothing at 0.0 — the
  ensemble gain (best-of-9 0.917 at 0.6) needs sampling.
- **Reproduction:** the 0.6 arm's verdicts equal the 2026-09-26 calibration on all 50 (task, seed) pairs, despite the
  2026-09-27 answer-read fix in between and (likely) different tablets. **Clean same-payload reproduction, 15:18**
  (`hardbench-code-cal-t06repro`, payload sha pinned identical `33b718d5…`, same tasks, seeds and model): **42/42 pairs
  identical — same verdict AND byte-identical answer text**, on whichever tablet drew the item. A seeded tablet run is
  deterministic. (8 of 50 rows missing: window 05 was voided by a staging-cleanup bug in the new rigby lane, fixed
  the same hour — [[Fellowship-Order-Taker-Design]] §13.)
- **Size knob, family 2:** Coder-0.5B 0.42 per attempt vs Coder-1.5B 0.57 (120 rows each, `calibration-coding-f2-05b.md`
  vs `calibration-coding-f2.md`); the 0.5B band holds only 2 tasks, so it gets no ensemble matrix.
- **Node class:** wall per item A (gandalf, aragorn) 28.2 s · B (the X606F six) 30.6 s · C (merry, pippin) 44.5 s;
  pass rates 0.34 / 0.40 / 0.38 (same within noise). A class-C tablet costs ~1.5× the time for the same work.

Fleet, same 10 tasks, seeds pinned (seed = 1 + round), 3 rounds (`hard-bench-20260928\fleet-{t00,t03,t06,t09}\fleet.md`):
- **9B (BigHonker):** per attempt 0.750 / 0.875 / 0.917 / 0.833; best-of-3 0.750 / 1.000 / 1.000 / 1.000 (greedy: the three
  tries are identical); J per solved 1496 (warm re-run; first run 1512) / 625 / 629 / 1253. The 0.0 cost is NOT a cold
  load — a warm re-run (idle baseline 22.5 W) reads the same 8.9 s: median output is ~110–140 tokens at every
  temperature, but greedy writes a 2,875-token answer for `rle_decode` on all 3 tries (deterministic), and at 0.9 one
  answer on `longest_common_prefix` ran 6,047 tokens to the context limit. The cost differences are single-task
  rambles, not the knob moving every answer. ⚠ **Read J per solved beside the MEDIAN per attempt** (the owner doubted the
  9B-costlier rows, rightly): a typical 9B answer is ~250–300 J at ~120 W, a typical 27B answer ~900–930 J at
  ~310–350 W — the 27B costs ~3× per answer. The 9B's J/solved exceeds the 27B's only at 0.0 and 0.9, where three
  48-s `rle_decode` failures (6,383 J each) or one 103-s ramble (13,501 J) dominate the sum. The fleet reduce now
  prints the median column. (27B meter = Mothership's telemetry CSV at ~11 s against ~3 s answers: coarse per row,
  sound over a run.)
- **27B (Mothership):** 1.000 at every temperature, ~3.2–3.5 s per answer, 1021–1134 J per solved.

## Second coding task family — "existing code" (2026-09-28)

`hard_tasks_f2.py`: 24 tasks — 12 debug (one realistic bug each) + 12 modify (add behaviour without breaking old
calls), 119 checks; self-test proves every reference passes, every ORIGINAL (the code the prompt shows) fails ≥1
check, and every modify-task ORIGINAL passes its regression checks (the checks can tell right from wrong).
Payload `map_swarm_code_hard_f2.py` (`gen_hard_payload.py --pool f2`, references 24/24 through the payload grader).
- **Fleet, 0.6, seeds pinned, 3 rounds** (`hard-bench-20260928\fleet-f2-t06\fleet.md`): 9B per attempt 0.819, best-of-3
  0.917, 4.2 s, 628 J per solved; 27B 1.000, 2.0 s, 663 J per solved. The 9B's never-right tasks: `mod_keep_positive`
  (genuine: `include_zero or n > 0` keeps negatives too) and `mod_flatten_one` (1 genuine miss; 2 tries ran ~5,900
  tokens of commentary to the context limit — length-limited, one of them with correct code inside).
- **Tablets, 1.5B coder, 0.6, 5 seeds** (`hardbench-codef2-cal.20260928-115852`, 120/120 rows, 0 instrument failures,
  no generation cap — `TOKEN_LIMIT = -1` — so no answer can be cut; product `hard-bench-20260928\calibration-coding-f2.md`):
  11 always right (all 12 modify tasks but three, plus `dbg_binary_to_int`, `dbg_word_freq`; the tablets ace
  `mod_flatten_one`, the task the 9B rambled on), 6 never (all debug; most with partial check credit), band 0.1–0.7:
  `dbg_count_evens` 0.6, `dbg_is_palindrome` 0.4, and 0.2 for `dbg_append_to_all`, `dbg_running_total`, `mod_greet`,
  `mod_keep_positive` → 6 tasks (≥ 4, so the family keeps its matrix; `dbg_count_vowels` at 0.8 is outside the band).
- **Tablet ensemble matrix, family 2** (`hardbench-code-matrix-f2.20260928-122416`): the 6 band tasks + anchors
  `dbg_word_freq` (always) and `dbg_dedupe_sorted` (never), N ∈ {1,3,5,9} × 3 repeats = 96 arms / 432 runs / 44 windows,
  seeds from replicate 10 (`hard_bench_matrix.py --pool f2`, products `hard-bench-20260928\matrix-f2.md` +
  `matrix_cells-f2.csv`). Done 13:36 (35 windows paused 12:34–12:38 while merry + pippin dozed, then released).
  Across the 6 band tasks: best-of-N **0.611 / 0.778 / 0.889 / 0.944** at N 1/3/5/9; majority verdict 0.61 / 0.56 /
  0.39 / 0.39; the per-tablet rate from all 54 samples per task (swarm mean at N 9) is 0.41 — N 1 alone is 3 samples
  per task and reads high. Same shape as family 1: checking and picking the best is the lever, voting is not.
  Anchors held: `dbg_word_freq` 1.00 at every N, `dbg_dedupe_sorted` 0.00 except one N 3 arm (0.11).
- **Fleet temperature, family 2** (`fleet-f2-t03`, `fleet-f2-t09`; t09 graded on the box by the order, 150/150 rows):
  9B per attempt 0.875 / 0.819 / 0.778 at 0.3 / 0.6 / 0.9, best-of-3 0.958 / 0.917 / 0.917; 27B 1.000 at all three.
  Typical cost (median J per attempt): 9B ~130 J, 27B ~613 J.

## Work family W1 — harder, work-shaped tasks (charter step 2, 2026-10-02)

**Why:** on f1/f2/tool the top models sit at 100 %, so the menu prices COST but cannot see QUALITY at the top end
(standings 2026-10-02, `Datasets\Project34-Menu\standings.md`). the owner approved the shape the same night (*"we defo need
to look into quality, because I have a strong feeling we will see 3.8 27B show its stripes there"*): three
deterministic parts; writing / explanation stay judged and out of W1 (judge calibration is parked).
`hard-bench\hard_tasks_w1.py`, 24 tasks, 64 checks, family name **`w1`** (`work-w1` in the table):
- **code (8):** implement / fix to a spec - TTL+LRU cache with an injectable clock, a five-bug interval merge, SemVer
  precedence sort, cron next-fire (DOM/DOW OR rule), token bucket, RFC 6901 JSON pointer, a strict duration parser,
  deterministic topological order; hidden checks, the f1/f2 contract.
- **reason (8):** one exact answer as `ANSWER = <value>` (x²−y²=2025 pairs, a 2-machine makespan, KV-cache bytes, a
  dice probability, weekdays with a holiday, blocked lattice paths, an ETA, a 4-box logic puzzle); every answer was
  computed by brute force, not by hand.
- **ops (8):** the lab's own incidents as `ANSWER = ("<cause>", "<fix>")` from five options each - Pi-hole's SQLite on
  CIFS, macvlan host isolation, NUM_PARALLEL KV spill on 8 GB, MAX_PATH on a UNC ship, the `python3` Store stub, a
  restart running a stale image, pydantic v2 `float = None` rejecting null, a `schtasks /sc once` re-firing.
Every W1 prompt carries its own format line (exactly ONE fence in the whole answer, a ```python block at the end),
so its payload has no "return only the function" suffix. **Graded as WRITTEN since 2026-10-03** (the owner: lenient scoring,
after reading the answers): `grade_jobs.answer_code` takes the LAST fence that assigns ANSWER, else the last bare
`ANSWER = ...` line, with `NOT_STATED` (C1's sentinel) defined; two DIFFERENT ANSWER values is a hedge and fails; code
tasks keep the first fence. Why: the "first fence is graded" rule came from the shared extractor, not from production.
An audit of all 408 GPU W1/C1 format failures found 200 right answers in another wrapper (20 an illustrative fence -
19 of them qwen3.8:27b thinking-on showing a shell command while explaining - 144 a bare ANSWER line, 36 an unquoted
NOT_STATED) and ZERO hedges; it was the cause of "3.8 thinking-on worse on ops" and "the MoE 50% on ops". Following
the one-fence instruction stays visible as `menu_standings` `format_fail`, never as a wrong answer. One grader:
the menu, `infer_ollama` in-order grading, and rigby (`tolkien_llm.grade`, baked into the tablet image from the GPU
build via `COPY --from=foundry`) all call `answer_code` (scripts: `hard-bench\fence_audit.py`, `bare_answer_audit.py`). **Instrument proof:**
`selftest()` - every REFERENCE passes all 64 checks AND every planted NEGATIVE (a plausible wrong answer per task)
fails at least one - run by `gen_hard_payload.py --pool w1` through each generated payload's own grader (it caught a
broken check of mine on its first run). **Where it runs:** the filler orders it like f2 on every GPU box and rigby
(`menu_fill.FAMS` has `w1`; first top-up 23:08 placed 47 cells: Softserve 18, BigHonker 17, Mothership 10, rigby 2);
the Fellowship as four calibration lanes `w1-c15b`, `w1-c05b`, `w1-q352bnt`, `w1-q3508bnt` (`hard_bench_calibrate.py`,
5 replicates, 480 runs), made at the f2 overnight lanes' measured setting (ctx 16,384, generation ended on idleness,
never a clock) because W1 answers carry a method. W1 has no price history yet, so its first cells run without a
price ceiling. Reading graduates per the rule above (≥2 families, reproduced).
**Measured at full capability (the owner 2026-10-03, "for quality we want to see what it comes up with"):** W1 carries NO
per-answer price ceiling (`menu_fill.UNCEILED`; a W1 reference from a fast model would cut a thinking 27B mid-answer)
— **on the frontier box (Mothership) only, since 2026-10-04** (`UNCEILED_BOXES`). On the helper boxes (BigHonker,
Softserve, rigby) W1 carries the box's price line like any family: there a W1 cell is a FINAL-ANSWER starting suggestion,
not the helper's price — [[34-CHARTER]] "an arm is not culled for failing alone", helper value is measured by the S10
shapes ([[34-S10-Rigby-Lead-Fellowship-Design]]) and specialists are priced on their real job. Trigger, the owner: *"a helper
model running for 2.6 hours is its own indicator"* (MiniCPM-V 4.6 thinking-on, one 46-min answer to its full 262,144
context; its one-shot W1 thinking-on arm is held on both 8 GB boxes as a final-answer verdict only).
Every answer on every family is bounded only by the box's tested context (`infer_ollama` sends `num_predict = num_ctx`,
2026-10-03: without it ollama shifted the window and a looping answer never ended); a row that reaches it is void by
cap and feeds the "cannot fit" stop rule. The arm-level stop rules (cannot fit / priced out) still judge W1 from its
delivered rows. **Held:** `*/qwen3-vl:8b/w1/*` on all three GPU boxes - the model thinks despite `think:false`; on
BigHonker 26 of 35 W1 answers ended at its 5,120 context with no answer, and on Mothership (222,208 context) the
first W1 answer streamed 100k+ chunks for 47 min before it was stopped. Also held `mothership/qwen3.5:9b/vlm/*` (the
1x-vision ruling, extended when the 9b was pulled to Mothership). **Order:** promising W1 arms first (Mothership's 3.8
and coder-30b; the 8 GB boxes' qwen3:8b and qwen3.5:9b) via priority control slips; the new 9b menu cells run after.
**At-cap brake — the cannot-fit rule inside the order (the owner 2026-10-03, "Rigby sounds good"):** with no price line, a
model that loops cost a whole order (a qwen2.5-0.5b W1 answer on rigby ran 70 min to its 32,768 context). So every
order of an UNCEILED family states the rule's share — `at_cap_brake` = `FIT_VOID_SHARE` 0.25 (Tolkien llm spec field;
`infer_ollama --at-cap-brake` on the GPU boxes) — and the factory stops the arm once the 95% lower bound (Wilson) of
its at-cap share reaches it: 2 of 2 answers at the context stop it, 1 of 2 or 1 of 8 does not. Controls, errors and
priced rows never count, and an answer counts whether or not its grade ran; "at cap" is `finish_reason` "length" in an
order run at the grid's own context (the order's ctx from its result cohort; a smaller order context measured the cap,
13a — token sums cannot tell: a cached prompt reports 1 token). The W1 graders take the program on stdin since
2026-10-03 (`python3 -c` raised E2BIG on a 32k-token answer and left those rows ungraded and re-asked). The
stopped arm's rows ship as usual and the between-order rule then holds it. **Rigby is now judged by the cannot-fit
rule too** (`arm_stops(rigby rows, judge_price=False)` — it had never been; its price brake stays per order), and
`menu_fill` stops a stopped arm's outstanding rigby orders with a tablet stop slip.
**A deploy-stopped rigby order is still outstanding (2026-10-03).** `fleet_deploy_tolkien` stops a running order,
which ships a partial results file, then ALWAYS requeues it from its checkpoint. `menu_fill.rigby_outstanding` keeps an
order outstanding while its newest results file is a deploy's stop (`fleet_deploy_tolkien.is_deploy_stop` — the
tool's own cause text, one derivation), or unreadable; any other stop counts as delivered and its unmeasured pairs
are re-ordered. Before this, a requeued order's remaining pairs could be ordered a second time.
**A loop ends an answer — no progress, never a clock (the owner 2026-10-03: *"the item is not good with that model and
config, and we are looking for the next rung up"*).** A model that repeats itself is not thinking longer, so waiting
for it to fill the context (80 min on a tablet, 65 on rigby) buys nothing. Rule, identical on every factory
(`infer_ollama.looping`, `tolkien_llm.looping`, the tablet payloads' idle runner): **both** of the output's last two
4,000-char windows, **digits collapsed to `0`**, compress (zlib 9) below **0.10**. Collapsing digits catches a
COUNTING loop — rigby's 0.5B W1 answer wrote `1.0.2402`, `1.0.2403`, … for 66 min to the context: 0.125 raw (it
slipped under the raw rule), 0.013 collapsed. Measured 2026-10-03 on every W1 + C1 answer >= 8,000 chars: it flags 21
of the 253 that ran to the context and **0 correct answers among 571 genuine finishes** (one wrong genuine finish is
flagged; the next-lowest genuine finish scores 0.166). The `legit_lowest` fixture scores 0.173 / 0.104 raw (one window
near the line, so ONE window would not be safe), and a correct ops answer whose last window alone dipped to 0.149 is
left alone. Fixtures are those real answers plus the counting loop, byte-verbatim (`loop_fixtures.json`, both repos;
the tablet generator's selftest reads the same file).
A cut answer ends `done_reason`/`finish_reason` **`loop`** and is **scored as the model's answer** (graded as written,
usually a fail) — unlike void-by-cap, more room would not have helped, so it counts against that model + config. It
also counts as cannot-fit evidence (the at-cap brake and `arm_stops`), because it would have burned the context.
Tablets: the runner stops llama-cli and closes the reply with llama-cli's own stats marker so the answer reader takes
exactly what the model wrote; the row is stamped `finish_reason: "loop"`.
**Tablet W1 is dealt as one PULL order per arm** (`hard_bench_calibrate.py redeal`, 2026-10-03): the first submission
used the calibration windows (8 pairs pinned to 8 distinct tablets, one window at a time), so one looping tablet held
its window and the other nine idled behind it. W1's pairs are independent single-tablet runs (Method 1, the owner 2026-09-26:
*"no need for the others to sit idle"*), so the undelivered 472 pairs went out as four plain `map` orders
(`*-cal-pull.20261003-062917`; the first attempt as `map.window` without a cohort was HELD on `KeyError` and idled the
tablets 02:07-06:29 — `redeal` now runs Tolkien's own `spec_defects` before the inbox): a free tablet pulls the next pair, a straggler holds only its own, and the standard speculative
re-run gives a stuck pair a second tablet. Each row still records its host. The tablet W1 payloads also bound every
answer at the context (`TOKEN_LIMIT = CONTEXT`, was -1 = context shift forever).

### W1 ops, letter-shuffled re-run (`w1ops`, the owner 2026-10-03: *"the B answer shuffle is fine"*)
The original 8 `ops_*` tasks put the correct cause at **B** in all 8 and the correct fix at B in 7, so a model that
leans on "B" scores without reading — those ops rows are **letter-biased and are not read for standings**. The
re-run (`hard_tasks_w1ops.py`) keeps every incident and option text and only moves the letters, under **new ids
`<id>_sh`**: correct causes/fixes spread over A-E (`TARGET` in the file, explicit, no task keeps its old pair). Each
prompt is parsed back into its options and rebuilt with W1's own `_mc`, asserted byte-identical before any letter
moves. Payload `map_swarm_code_hard_w1ops.py` (`gen_hard_payload.py --pool w1ops --suffix _w1ops`; references 8/8,
selftest PASS — every planted wrong answer fails).
**Ordering:** one ops-only order per W1 cell a box has DELIVERED, with that cell's own knobs, derived through
`menu_fill`'s constants (`submit_w1ops.py <box> [--dry]`; champion first, no brake for it), **skipping any arm a
human hold or an automatic stop covers** (champion exempt from automatic stops only) — the filler's auto-cancel never
sees w1ops orders, so the submitter must not create them. `audit_w1ops.py <box> [--cancel]` re-checks what is out. **Orders land in
`campaigns\w1ops-<day>\fleet-s-<box>-...`, NOT under `hard-bench-*`** — the menu readers class any `-w1`/`_w1` name there
as W1, so a w1ops row there would silently join the live W1 cells and the filler's stop rules. The menu rebuild reads
`w1ops-*` on purpose: ops from the `_sh` rows, the other 16 W1 tasks from the original cells. **Wired 2026-10-03:**
`build_menu.fleet_orders` also reads `campaigns\w1ops-*` and grades those rows with the order's own `--grade` payload
(`map_swarm_code_hard_w1ops.py`); `supersede_letter_biased_ops()` marks every original `ops_*` W1 row, from every
source (GPU, rigby, tablets), `usable False` / void "letter-biased (superseded by the w1ops _sh rows)" and asserts it
matched some. Same pass, two more fixes the rebuild needs: a tablet row the payload flagged `answer_boundary:*` (it
could not find the reply - 243 class-C C1 rows from the morning windows, re-dealt) is void "instrument", never graded
as the model's answer (it had been re-graded from an empty answer as a fail); and `menu_standings` reports
`format_fail` (answers that did not parse/run: "format:", SyntaxError, NameError) beside success, so "could not answer
in the protocol" is never read as "answered wrong".
Status: Mothership's 29 cells submitted 2026-10-03 14:50. BigHonker, Softserve: after their queues drain. Tablets and
rigby: after their W1 pulls finish (their payloads are read at staging, so a W1 order still queued must not see a
changed task table).

### Versioned tasks — changing a task's text without touching its results (`task_versions.py`, 2026-11-17)

**Rule (the owner, 2026-11-17):** *"Preserve the historical benchmark results and their provenance as they are. If changing
the task text affects comparability, version the benchmark task and distinguish the original results from future
runs."* The first use took the lab's real addresses out of the text that ships publicly (the public sanitization
ruleset): `ops_macvlan_host` named Pi-hole's real LAN address in its question and options, and `valid_ipv4` had a
real-looking LAN address in a hidden check. The mechanism, the same shape as the `_sh` ids above:
- **A new id for the new text** (`ops_macvlan_host_v2`, `ops_macvlan_host_v2_sh`, `valid_ipv4_v2`, with neutral
  `10.0.0.x` addresses); `task_versions.SUPERSEDES` records which id each replaces. Options, answers and checks are
  otherwise unchanged; `test_task_versions` proves it against the payload that ran the original (old and new are
  equal once IPv4 addresses are masked, and only the original names the lab's `LAN_PREFIX`).
- **The old payload files are never rewritten.** Each new text is a new generation (`PAYLOAD_GENERATIONS`:
  `map_swarm_code_hard_v2.py`, `_w1_v2.py`, `_w1ops_v2.py`); the originals keep their sha256s (pinned in the order
  manifests), so a queued order still reads the table it was built for, and every historical row is graded by the file
  that ran it (`build_menu.Grader.grade` tries the named file first; the grade cache keys are unchanged).
- **New orders run the newest generation** (`hard_bench_fleet_orders.PAYLOAD`, `menu_fill.rigby_submit`, through
  `task_versions.newest_payload`).
- **The menu keeps the ids apart; the filler treats them as one question.** `build_menu` rows keep their own id (so an
  original result is never read as a new one) and every rule on a task covers the ids it supersedes
  (`supersede_letter_biased_ops`); `menu_fill` maps a row to its current id for coverage and price, so a cell
  measured on the original is not re-run for the version.
- **To version another task:** give the new text a new id in its task file, add the pair to `SUPERSEDES`, generate the
  payload under a new name (`gen_hard_payload.py --pool <pool> --suffix <new>`) and add it to `PAYLOAD_GENERATIONS`.
  The size-ladder and temperature payloads (`_05b`, `_3b`, `_t0x`, the tablet-lane `_w1_*`) are frozen instruments
  nothing submits; they keep the original text.

## Chat/generalist family C1 — what the menu cannot see yet (charter step 2, approved 2026-10-03)

**Why:** without a chat/generalist division *"the menu prices quality it cannot see"* (34-CHARTER, still to do (2)).
W1 already carries the heavy reasoning, so C1 leans on four dimensions no family measures. **Approved by the owner
2026-10-03 04:48 ("Sounds good")**, ~20 tasks built exactly like W1 (references, planted wrong answers, a selftest,
all four factories), `hard_tasks_c1.py`:

| dimension | tasks | what it tests | graded |
|---|---|---|---|
| instruction adherence | 5 | several literal constraints at once: exact counts, required / forbidden words, output shape | **character-exact** on the reply text — the constraint IS the skill |
| context use | 5 | answer from a supplied document of a few thousand words, combining facts from different places | meaning-exact |
| hallucination | 4 | the answer is NOT in the supplied text, or asks about something that does not exist; right = `"NOT_STATED"` | meaning-exact |
| correction | 3 | a previous wrong answer + the user's pushback (some pushbacks right, some wrong): fix or politely hold | meaning-exact |
| reasoning | 3 | traps (bat-and-ball / sisters class) at GPU difficulty | meaning-exact |

**Meaning-exact, not character-exact (the owner asked, 04:48):** the model answers in a canonical form so free prose is
never compared by characters — numbers/dates as VALUES (`ANSWER = 391`), names and phrases as a pick from listed
options (`ANSWER = "C"`) or normalized (case, spacing, punctuation) against a short accepted list, "not in the text"
as one defined token the prompt spells out (`ANSWER = "NOT_STATED"`), correction as a choice plus a value
(`ANSWER = ("keep", 42)` / `("revise", 40)`). Instruction adherence is the one deliberate exception: the reply goes in
`ANSWER = """…"""` and is checked literally. **Format failures stay separate from wrong answers** in the results (no
`ANSWER` block / unparseable → the grader's error field; parsed but wrong → checks failed), because a model that knew
the answer but broke the format is a different finding. Same grading contract as W1 (the payload's `score()` runs
the one python block + lambda checks in a bounded child), so C1 runs wherever W1 runs.
Not in C1: judged writing / explanation (`write-1`, `explain-1` in `workspace\flashnext\quality\prompts-v1.json` —
they need a judge; judge calibration is parked).
**Built and ordered 2026-10-03 ~09:55.** 20 tasks (5/5/4/3/3), two original documents (a ferry operator's annual
report ~2,000 words, a clinic IT migration plan ~1,600 words — at the low end of "a few thousand"; longer mostly adds
tablet prefill time), every answer fact computed, each document carrying distractors (a switchboard extension beside the
harbourmaster question, an annual fuel average beside a monthly-price question). Selftest: 20/20 references pass, 20/20
planted wrong answers fail; anti-gaming measured — "always NOT_STATED" passes 2 of 4 hallucination tasks, "always keep"
1 of 3 correction tasks. Payloads `map_swarm_code_hard_c1.py` (GPU grading + rigby) and `_c1_{c15b,c05b,q352bnt,
q3508bnt}.py` (tablets, ctx 16,384, idle cut-off, answers bounded at the context), each passing the generator's
controls incl. a 300 kB answer. Wired like W1 (`menu_fill` FAMS/grid/history/rigby/`--pool c1`; `build_menu` family
`chat-c1`; coverage; standings), detected by exact tokens (`_c1.` `_c1_` `-c1-` — bare `c1` also matches W1's tablet
lane `c15b`). `build_menu.format_failure` labels a python block with no `ANSWER =` as `format: …` by reading its syntax
tree (never running it). Tablets: four plain `map` pull orders from the start (`hard_bench_calibrate.py submit --pull`,
Tolkien's own `spec_defects` asserted clean before the inbox). GPU boxes and rigby: the menu fill orders C1 cells
breadth-first from its 10:08 top-up. C1 is a priced family (not UNCEILED — no ruling made it one).
**Held: `mothership/qwen3-vl:8b/c1/*`** (the owner 2026-10-03, *"we can call this a failure"*): its first C1 cell streamed
~62k tokens on its second answer in 17 min against Mothership's 222,208 tested context — the same think-despite-
`think:false` runaway as W1. A failure of that model + config on this box, not a measurement to wait out; the 8 GB
boxes' 5,120 context bounds it, so its C1 is measured there.

## S7 model knob — qwen3.8:27b vs qwen3.6:27b on coding (2026-09-28; PRELIMINARY, one run each)

Same box (Mothership), same items, 0.6, seeds pinned, 3 rounds; `hard_bench_fleet_orders.py --rungs 27b38`, every
row graded on the box. Products `hard-bench-20260928\fleet-f2-t06-q38\fleet.md`, `fleet-f1-q38\fleet.md`.

| pool | qwen3.6:27b per attempt · best-of-3 · wall · median J | qwen3.8:27b per attempt · best-of-3 · wall · median J |
|---|---|---|
| family 2 (24 tasks) | 1.000 · 1.000 · 2.0 s · 613 J | 0.972 · 1.000 · 1.2 s · 404 J |
| family 1 (8 in-band tasks) | 1.000 · 1.000 | 1.000 · 1.000 · 2.3 s · 593 J |
| tool (24 tasks, `fleet-tv`) | 1.00 per attempt, 24 always right, 313 J per solved | 1.00, 24 always right, 222 J per solved |

**Replicate with fresh seeds** (`fleet-f2-t06-rep2`, seed-base 101, 225/225 rows graded on the box): 9B 0.847 /
best-of-3 0.958 / 3.5 s / ~132 J median; 3.6 1.000 / 1.000 / 1.9 s / ~604 J; 3.8 0.986 / 1.000 / 1.2 s / ~360 J —
every rung within a few points of its first run, so the ordering holds across two seed sets. A third 3.8 run
(`fleet-f2-t06-rep3`, seed-base 201 — the first order shipped by the factory itself, `{ORDER_OUT}` → `deliver_to`):
0.986 / 1.000 / 1.4 s / ~319 J.

On this pool 3.8 is as accurate at best-of-3, faster, and cheaper per answer on family 2. The idle baselines of the two
runs differed (38 W vs 64 W on the telemetry CSV), so compare the median per-attempt J, not "above idle".

## Every model the GPU factories hold, family 2 (2026-09-28; PRELIMINARY, one run each)

Same 24 tasks, 0.6, seeds pinned, 3 rounds, graded on the box (`fleet-f2-t06-models\<order>\out.json`, shipped by
the factory). Per attempt · best-of-3 · s per answer · median J per attempt:

| model | box | per attempt | best-of-3 | s / answer | J / answer |
|---|---|---|---|---|---|
| **qwen3-coder:30b** (MoE, ~3B active) | Mothership | **1.000** (on-box grade said 0.667 — see below) | 1.000 | **0.5** | **37** |
| **qwen3.6:35b-a3b** (MoE, ~3B active) | Mothership | 0.986 | 1.000 | 0.7 | 144 |
| qwen3:14b | Mothership | 0.972 | 1.000 | 0.8 | 276 |
| **qwen3:8b** | BigHonker | **0.931** | 0.958 | 0.9 | 97 |
| (for reference) qwen3.8:27b / qwen3.5:9b | Mothership / BigHonker | 0.97–0.99 / 0.78–0.85 | 1.00 / 0.92–0.96 | 1.2 / ~3.5 | ~360 / ~130 |

**Grader defect found and fixed (not a model result):** qwen3-coder:30b opens its code fence as "```python code"
(echoing the prompt's words), and the payload's extractor regex required a newline straight after "```python", so
it graded the fence line as code — 24 correct answers scored as `SyntaxError`. `regrade_fence.py` re-grades with a
fence-tolerant extractor: coder-30b 0.667 → **1.000**; qwen3.6:35b-a3b, qwen3:14b, qwen3:8b and qwen3.8:27b unchanged
(0 verdicts moved — the fix does not inflate models that fence normally). `gen_hard_payload.py` now writes the
tolerant extractor into every new payload; payloads already pinned in queued work keep theirs until regenerated.

The two MoE models (~3B active of 30–35B) are the standout: 27B-class accuracy at a fraction of the time and energy.
qwen3:8b beats qwen3.5:9b on this family at the same answer length (~47 tokens median), ~4× faster per answer —
the cause of the speed gap is not yet measured.

## rigby alone (S5r) — the Tolkien head as its own rung (2026-09-28; PRELIMINARY, one run each)

Tolkien orders of kind `llm`, arm `rigby`: llama-server on rigby's CPU (i7-3770 since 2026-08-28, 24 GB DDR3-1600, no AVX2; GT 730 2 GB beside it), the tablets' own payload
prompt, 0.6, 2048 context, generate-to-EOS, 3 seeds, graded by the same scorer inside the order. This is NOT Voltron —
no tablet is involved. Results in the Tolkien outbox (`hardbench-rigby-<n>-*.results.json`); every run usable,
control passed, all answers measured, one answer of 205 cut by the 2048 context.

| model | family 1 (10 tasks × 3) | family 2 (24 tasks × 3) | speed |
|---|---|---|---|
| Qwen2.5-Coder-0.5B | 0.37 (11/30) | — | ~41 tok/s, 5.5 s/answer |
| Qwen2.5-Coder-1.5B | 0.30 (9/30) | 0.58 (42/72) | ~20 tok/s, 2.7–3.8 s |
| Qwen2.5-Coder-3B | 0.77 (23/30) | **0.82 (59/72)** | ~10 tok/s, 6–10 s |
| Qwen2.5-7B (general) | — | **0.83 (60/72)** | ~4.7 tok/s, 12 s |
| Qwen2.5-14B (general) | — | **0.92 (66/72)** | ~2.4 tok/s, 22 s median (answers ~45 tokens) |

On family 2 the general 7B is level with the Coder-3B at twice the time per answer — the coding specialist is the
better buy at that size. The 14B is a real step up: 0.92, missing only `dbg_max_subarray_sum` and `mod_flatten_one`,
above the 9B on BigHonker (0.78–0.88) and near the 27B — at ~2.4 tok/s, which is rigby's memory-bandwidth ceiling for
a 9 GB model (every token reads the whole model; the 08-28 journal measured rigby at ~76% of DDR3-1333).

**Reading, not writing, is rigby's bottleneck on big models.** The 14B reads a cold prompt at only **~4.2 tok/s** (the
i7-3770 (Ivy Bridge) has no AVX2, so prompt processing is compute-bound): a 170-token task costs ~40 s to read before the ~20 s
of writing, 60–80 s in all. The "22 s" median holds because llama-server's prompt cache reused almost every prompt
(48 of 73 rows read ≤ 1 new token — replicates repeat the same task). Speculative decoding speeds writing only.

For family 2 the 3B on rigby's CPU is level with the 9B on BigHonker (0.78–0.88 per attempt) — the model that fits no
tablet runs usefully on the head.

**Thinking ON with room — qwen3.6:27b on Mothership, family 2 (2026-09-28/29).** The first thinking-on cell ran at
num_ctx 6,144 and read 0.653: 27 of 72 answers were cut at the context (doctrine class 13a). Re-run at 65,536 it hit
two more clocks — `infer_ollama`'s flat 600 s request timeout (7 rows) and the stall demon's 900 s static-unit window
(stopped the order mid-row) — both replaced (streamed replies ended only on silence; the payload's own `[beat]`).
Final, all 72 answered: **71/72 = 0.986**, control 3/3; one row is a runaway thinking loop that filled the whole
65,536-token context in 39 min (counted, not hidden). Answers ran to 28,555 tokens and 919 s. qwen3.8:27b on the same
pool at 6,144: 72/72, longest answer 1,666 tokens, 0 at a limit — 3.8 thinks briefly, 3.6 at length.
**qwen3.8 MTP (multi-token prediction) is ON BY DEFAULT on Mothership — plain `qwen3.8:27b` already runs it; the
`qwen3.8:27b-mtp-q4_K_M` tag is the same thing under a second name.** Both tags are the same blobs
(`sha256-f5f1dd89…` + projector `ac3714bf…`) with identical parameters, including `draft_num_predict 4`
(`ollama show --parameters`). ollama 0.34.0 turns that into `--spec-type draft-mtp --spec-draft-n-max 4` on every
qwen3.8 llama-server launch, and `serve.log` (`C:\Users\<user>\AppData\Local\Ollama\serve.log`) shows it working for
BOTH tags: `creating MTP draft context` → `speculative decoding context initialized`, then per-request
`draft acceptance` 0.78-1.00, mean accepted length ~4.2-4.9 tokens (e.g. the 09-28 18:39 plain-tag load: 293 of 330
drafted tokens accepted). The A/B pairs therefore compared MTP against MTP: family 1 thinking off 33/33 answers
identical (both 33/33 pass), family 2 thinking off 75/75 identical (both 73/75), family 2 thinking on at 65,536
72/72 identical at 54.2 vs 54.5 tokens/s — same seeds, and speculative decoding is lossless, so identical output was
the expected result. Every qwen3.8 row in this spec was measured WITH MTP. **Not priced: MTP's speedup** — no
MTP-off baseline exists (it would need a Modelfile with `draft_num_predict 0`). ⚠ The `unused tensor
blk.64.nextn.* -- ignoring` / `no implementations specified for speculative decoding` lines in the same log belong to
OTHER qwen35-architecture models' loads, not qwen3.8's — read the segment after the qwen3.8 runner start, not a
log-wide grep.

**Speculative decoding on rigby (round 3, family 2, 2026-09-28; one run each, same reducer as the baselines).** Orders
10/11 (`-draft05b`) loaded the 0.5B draft but never speculated (no `spec_type`), so they are plain replicates of 8/9 —
which gives the run-to-run spread: 14B 2.38 / 2.36 tok/s, 7B 4.72 / 4.72.

| model | speculation | accuracy | gen tok/s (median) | s/answer (median) | order wall | draft acceptance |
|---|---|---|---|---|---|---|
| Qwen2.5-14B | none (8 + 10) | 0.92 | 2.38 / 2.36 | 22.2 | 36 min | — |
| Qwen2.5-14B | draft 0.5B (13) | 0.92 | **2.86** | 17.7 | 33 min | 0.88 |
| Qwen2.5-14B | n-gram (15) | 0.92 | 1.96 | 33.1 | 44 min | 0.13 |
| Qwen2.5-7B | none (9 + 11) | 0.83 | 4.72 / 4.72 | 12.0 | 17 min | — |
| Qwen2.5-7B | draft 0.5B (14) | 0.83 | **5.11** | 10.3 | 17 min | 0.89 |

A 0.5B draft speeds WRITING (+20% on the 14B, +8% on the 7B) with accuracy and the missed tasks unchanged; n-gram
speculation is slower (these answers are short and rarely repeat). Whole-order time barely moves because rigby's
bottleneck is READING the prompt (~2.3 tok/s on the 14B). **Qwen3.6-35B-A3B (12) is VOID:** 28 of 72 answers hit
the 2,048-token context (a thinking model; doctrine class 13a), so its 0.61 measures the cap. Re-run needs a tested
context for rigby's RAM first. Its speed is real: ~6 tok/s writing, ~14.7 tok/s reading — faster than the dense 14B
on both.

**Voltron (S5), the same Coder-3B on family 1** (`hardbench-voltron-coder3b-f1`, one order, both arms, 62/62 answers,
both controls passed): rigby + 10 tablets loaded it in 126 s and answered **21/30 at 1.18 tok/s, ~89 s per answer**,
against rigby alone **23/30 at 10.4 tok/s, ~10 s**. Accuracy is the same within noise; Voltron is ~9× slower at a
size rigby holds. Cleanup verified: no rpc-server left listening on any tablet. Per the owner (2026-09-28) Voltron stops
here: priced, capped; the only open question is a model too big for rigby's 23 GB (parked). Rigby's energy is metered on the rack's smart plug since 2026-09-29 (§"Rigby (S5r) energy");
Voltron's own per-answer figure still needs a plug hour that holds a Voltron run.

## Decisions only the owner can make

0. **The band, now with data (replaces the 0.3–0.7 proposal):** strict 0.3–0.7 keeps 4 tasks;
   **recommended 0.1–0.7 keeps 8** — rarely-right tasks are where best-of-N with a verifier has the most to
   prove and majority vote cannot help, the very contrast the matrix is for. Plus two anchors
   (`rotate_matrix` always-pass, `group_anagrams` never-pass) as positive/negative controls → 10 tasks.
   Matrix then ≈ 10 tasks × 3 strategies × 4 N × 3 seeds = 360 arms, ~1 day of Fellowship time.

1. Go / no-go, and when (the Fellowship is idle now; cameras work is paused).
2. Whether chat gets a selector built first so it can join (Phase 8-shaped work), or stays out.
3. The middle band (0.3–0.7 proposed) and K per division.

## Open risk, stated

A 1.5B model may have no middle band on some divisions — tasks are either easy or impossible for it. The
calibration stage exists to find that out cheaply before any matrix time is spent; if it comes back empty,
the honest conclusion is that ensembles of this model size are not the lever, and Phase 6 (Voltron: pooling
tablets to serve a BIGGER model) is.
