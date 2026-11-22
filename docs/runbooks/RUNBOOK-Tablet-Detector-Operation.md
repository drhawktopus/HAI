# RUNBOOK — Tablet Detector Operation (ncnn)



## Tablet detector — object detection on the Fellowship (ncnn, port 8801)

**The detector is live on every fellow** — ncnn, port 8801, inside the `fellow` agent, so it
inherits the Termux:Boot supervisor, respawn policy and reboot survival rather than adding a
second daemon.

**Detection is event-driven bursts, not a continuous stream.** NVR motion events are the trigger;
a burst task executes on a fellow, pulls the recorded clip and decodes it. There are **no
standing decode assignments** — no node holds an assignment or runs a standing `hwdec`/`ffmpeg`
process. A burst takes its parameters per request and reads the clip's own geometry rather than a
caller hint, so live encoder changes do not affect it. Detail:
2026-08-13-claude-cameras2 (burst design) and 2026-08-14-claude-cameras
(decode try-chain).

⛔ **There is no Frigate and no DeepStack shim** — the Frigate `detectors:` wiring, the six-entry
shim on `:8802` and the `cpu1` floor went with Frigate (see the Cameras section; the wiring's
design record is archived in `Archive\runbook-cuts-20260821\`). Two design rules outlive it:
* **Per-frame camera traffic never routes through Kirk.** A blocking forward inside the
  orchestrator's event loop multiplies every unrelated caller's latency ~100x;
  `tests/test_fellowship_seam.py` asserts the route cannot grow back. Serialisation of this shape
  is invisible to unit tests (mocking the forward removes the very blocking that causes it) —
  wall time under real concurrency is the only honest check.
* **A detector must never answer 200 with an empty prediction list on failure.** A consumer
  cannot tell that from an empty room, so a broken node reads as a quiet house forever. Fail with
  a 5xx.

### Check it (all read-only, run from Softserve)

```bash
cd /e/SSDShared/orchestrator-build && python tablet_foundry.py --detect-status
```

```bash
cd /e/SSDShared/orchestrator-build && python tablet_foundry.py --detect-selftest
```

`--detect-selftest` is the one that means something. It runs a **known-positive frame** through the
real path on every node and asserts the expected label comes back. ⚠ **A clean start proves
nothing here**: an empty prediction list is the correct answer to an empty room AND the symptom of
a dead subprocess, a missing shared library or a mis-decoded frame — three distinct real faults
that all present identically as "the house is empty".

Both need the shared token: `TABLET_FELLOW_TOKEN=$(ssh gandalf cat '~/.fellow_token')`.

### Change or reinstall the detector

`detect_install()` pushes binary, model and control frame from `TABLET_DETECT_STAGE`
(default `/ssdshared/tabfoundry-detect`, i.e. `E:\SSDShared\tabfoundry-detect`). Staging is
deliberately NOT in `orchestrator-build` — that is the docker build context and 22 MB of weights
would ride into every image build.

Tolkien (the agents' home) runs on rigby since 2026-08-28, so run these INSIDE its container — it holds the
deployed `tablet_foundry.py`, the cluster key and the wire routes:

```bash
ssh rigby 'docker exec -w /app tablet-foundry python tablet_foundry.py --install-detect'
ssh rigby 'docker exec -w /app tablet-foundry python tablet_foundry.py --deploy'
```

`--deploy` copies `tablet_foundry.py` and kills the agent; the boot supervisor respawns it 5 s
later with the new code. **Deployed is not running — always re-run `--detect-selftest` after.**
⚠ A tablet with a FULL disk refuses the copy (`scp: write remote "tablet_foundry.py": Failure`) — check
`df -h ~` on it first (2026-10-11: the #36 rpc-server weight cache had filled gandalf/aragorn/merry/pippin).

### Pause / resume the detector — Tolkien's switch (when the cameras pipeline is paused)

the owner, 2026-10-11: *"if the detection pipeline is paused we should have Tolkien tell the tablets to kill the process,
and whenever we need it again we can have him turn them back on."* The agent normally starts the detector BY
DETECTION; a pause marker (`~/detect.paused`) is the one exception, so a pause survives the agent's respawn and a
reboot, and the detector reports `available: false, why: "paused by Tolkien (…)"` — never "broken".

```bash
ssh rigby 'docker exec -w /app tablet-foundry python tablet_foundry.py --detect-pause --why="cameras paused"'
ssh rigby 'docker exec -w /app tablet-foundry python tablet_foundry.py --detect-resume'      # [--hosts=a,b]
```

**State at 2026-10-11 ~08:30 CT (PAUSED on all ten, every one reads "ready to resume"):** `ncnn_detect` and
`hwdec` were reinstalled with `--install-detect` on the seven tablets that had lost them (cause unknown; the agent
log now carries the date on every line, so a recurrence can be dated). To resume: `--detect-resume`, then
`--detect-selftest`.

A paused tablet still checks its files: it reports `paused by Tolkien (…); ready to resume`, or
`…; ALSO missing <file> - --install-detect before resuming`. Read that `why` (`fellow_health`) before a resume.

### Rebuilding `ncnn_detect`

Cross-compiled by `Lab\fellowship\ncnn-detect\build_detect.sh` inside the **`ndkbuild`**
container on Sundae, reusing the ncnn static libs already built there (do NOT rebuild ncnn; it is
~20 min). Source: `ncnn-detect\ncnn_detect.cpp`.

⚠ **`-static-libstdc++` is not optional.** The NDK links `libc++_shared.so` by default and Termux
does not ship it, so the binary compiles, passes `file`, and then dies at exec with
`CANNOT LINK EXECUTABLE`. The build script asserts on `readelf -d` output for exactly this.

⚠ **CPU, never Vulkan.** Measured on gandalf, same binary, same models: `mobilenet_ssd` 93 ms CPU
vs **1723 ms** Vulkan; `nanodet_m` 62 vs 738. The GPU is ~18x slower than the tablet's own CPU on
this workload — enabling it looks like an optimisation and costs an order of magnitude.

### Gotchas that already cost time

* ⚠ **`ETXTBSY`** — the agent keeps the binary RUNNING, so a direct `scp` over it works exactly
  once (fresh tablet) and fails on every upgrade after. `detect_install` writes `.new` then
  renames; do the same by hand if you ever have to.
* ⚠ **Benchmarking a detector: feed it what its CONSUMER feeds it.** The pipeline classifies
  motion-region **crops**, so score crops — whole-frame scoring rates an animal corpus 1/24 that
  scores 10/24 framed as crops, and reads as "the tablets cannot find pets".
* ⚠ **Detection quality is bounded by the ENCODE that feeds it — bitrate is part of source
  quality, not just resolution.** A starved low-bitrate encode drowns in IR grain at night, so an
  animal scoring **0.0 on every model** is an encoder-starvation signature before it is a model
  IR-domain gap: replaying the same clip at a higher bitrate restores the detection. **Before
  blaming any model on any camera, score a MAIN-stream crop of the same moment first** — and
  never compare event COUNTS between a live stream and a replayed concat (clip cuts break object
  tracking, so the two numbers count different things). Method + full evidence:
  Journal 2026-08-09-claude-cameras.
* ⚠ **nihui's `examples/yolo11.cpp` decoder is INCOMPATIBLE with `yolo export format=ncnn`
  (ultralytics) models** — ultralytics bakes the detect-head decode into the graph (`out0` =
  84x2100: 4 box coords + 80 sigmoid scores), nihui expects his own pnnx export (raw 144-ch DFL
  rows) and **segfaults**; it also hard-codes `use_vulkan_compute = true` (66 s PowerVR init, then
  crash — and Vulkan is the documented 18x-slower path here anyway). Working scorer for
  ultralytics exports: `Lab\fellowship\ncnn-detect\yolo11_score.cpp`.

### Scoring a corpus of images against the detector — as an ORDER (`map_detect`)

Scoring frames is a job with inputs, a method, deliverables and cleanup, so it is an **order** —
never an ad-hoc script driving the fleet from outside.

**Payload:** `E:\SSDShared\tabfoundry-jobs\payloads\map_detect.py` — item is an image **URL**; the
payload fetches it and asks the node's **resident** detector on `127.0.0.1:8801`, so it cannot
drift from what production sees. Rows carry `item`, `top`, `best`, `n`, `dets`, `error`. Transport
failures are ROWS, never crashes, and a 200-with-no-predictions ("looked, found nothing") is kept
in a different field from "could not ask".

**Publishing inputs:** items are URLs, so images must be web-reachable. `cameras.lan` is served by
Caddy from **`/opt/arrstack/cameras` on Sundae** (mounted `/srv/cameras` in the container). Put
experiment corpora under `bench/<run-id>/` — NOT in `full/`, which is the household timeline's own
data.

**Submit** through `tablet_orders.submit_map` from the orchestrator container (it validates the
payload whitelist, item characters, MAX_ITEMS=500 and the presence of a control, then drops the
spec atomically). Do not hand-compose a spec — that skips every one of those checks.

⚠ **DO NOT put the control in `items`.** The factory appends it itself
(`tablet_fulfil.run_order`: `items = items + [ctl_item]`). Listing it too makes expected = N+1
while distinct measured = N, and the run is VOIDed as INCOMPLETE with one item "unaccounted for" —
a bookkeeping ghost indistinguishable from a genuinely lost item.

Worked example end to end: `Lab\cameras\build_paired_corpus.py` → publish →
`submit_paired_order.py` → `analyse_paired_results.py` (which **refuses to analyse a VOID run** —
`usable` is the factory's own verdict). 109 images, 5 nodes, **20 seconds**, control PASS.

### Detector latency: where the ~200 ms goes

⚠ **It is NOT the network.** Same image, idle Fellowship: on-tablet `127.0.0.1:8801` and
container→tablet over WiFi measure the same (~145-148 ms median) — **the node itself is the
cost**, and only ~93 ms of it is inference. The rest is the fellow's own Python
HTTP/multipart/JPEG handling, which is the single biggest lever on detection latency.

* **sam is ~1.4x slower than its peers (~207 vs ~148 ms) and it is a STABLE property** — slowest
  in every measured round with a tiny spread. A real per-node difference, cause undiagnosed.
* **frodo is FLAKY, not slow** — it drops off the network; any single-round frodo number is
  unconfirmed until re-measured.
* **Rank stability is per-claim**: the slowest node is stable round to round, the fastest swaps.
* The detector is **deterministic** — the same image returns identical scores everywhere (spread
  0.000) — so repeats of one image add nothing; sample more scenes/framings instead. Latency
  measurements DO need rounds separated in time (Android thermal state and doze drift).

Scripts: `Lab\cameras\wholeframe-test\` (`latency_rounds.py` is the one to reuse).

### Choosing a detector model: why WHOLE-FRAME detection does not work, and yolo11 is rejected

⚠ **Do not "just send the full frame", and do not reach for a bigger-input model to make that
work — both fail, measured.** This is the most expensive wrong instinct in this subsystem: it
looks obviously right and it is obviously wrong. **A cat is ~0.5% of a 1080p frame**, so at a
300 px model input it is ~15 px and gone — at ANY source resolution (704x480 through 4K, with
correct letterboxing); filling the input, the same cat scores ~0.93. **Crop-then-classify is the
mechanism, not a workaround** — the pipeline crops the motion region, so whole-frame is a path it
never takes.

**BITRATE matters; RESOLUTION alone does not** — settled by a PAIRED test (each pair holds frame
and crop fixed so framing chaos cancels): compression alone separates detection (p=0.017) while
resolution alone does not (p=0.15). ⚠ **Low bitrate causes outright MISSES, not just lower
confidence**, and **more resolution can even HURT** (a native-resolution crop can score 0.000
where its own downsampled copy scores 0.8+). ⚠ **Test an intervention in the regime where it
would actually be used**: at 100% crop fill both arms sit at ceiling and no difference can
appear — the marginal cells are where the answer lives.

**Framing dominates any SINGLE measurement, and it is chaotic** — one cat swings 0.49 ↔ 0.98 on a
20 px crop difference, while the detector itself is deterministic (same image x25 across five
tablets: spread 0.000). The fleet is exact and uniform; the model's response to framing is what
is erratic. Two operational consequences:
1. **A single framing's score does not generalise.** Sample across FRAMINGS and FRAMES — repeats
   are worthless because the detector is deterministic.
2. **A fixed confidence threshold is inherently fragile.** No tuning of a 0.7 threshold rescues a
   detection that swings 0.49-0.98 on crop geometry. **Aggregate over the object TRACK plus dwell
   time** rather than trusting any single frame's confidence.

⚠ **Scope: every arm above is NIGHT IR, one cat, one scene. There is no daylight measurement.**

**yolo11 REJECTED — it is worse and slower, not blind:** same HQ crop, same file: mobilenet@300
(production) **0.932** vs yolo11n@640 letterboxed **0.773**, at several times mobilenet's ~93 ms
inference. `yolo11_bench.cpp` letterboxes correctly (aspect-preserving resize + grey 114 pad, pad
subtracted before undoing the scale) — ultralytics models require letterbox, and a squashed 16:9
input invalidates the bench.
⚠ Model and input size remain confounded (yolo11 is COCO-trained, ours VOC), so this rejects
*yolo11*, not every large-input model. Speed comparisons must be same-node — sam runs ~1.4x
slower than its peers, so cross-node timings are not comparable.

Bench tooling, kept and reusable: `Lab\fellowship\ncnn-detect\yolo11_bench.cpp` (takes
model prefix, imgsz, image, floor, reps; reports median/min/max ms AND every detection) +
`build_bench.sh`; models staged at `E:\SSDShared\tabfoundry-bench\`. Runner executes inside the
`tablet-foundry` container — ⚠ **the module is at `/app`; `/tabfoundry` is the DATA dir.**

⚠ **Two harness rules, each the difference between a measurement and a confident wrong answer:**
(1) a wall of "no cat" is exactly what a broken binary looks like — **run a positive control
through the same binary**, because only a positive makes the negatives evidence; (2) **look at
the crop before you score it** — a mis-matched template hands the scorer floor tile, and "no cat"
then means the cat was absent, not undetected.

### Reading container CPU (`docker stats`)

⚠ `docker stats` normalises to **100% = ONE core**; Sundae has **6**, so a container "at 35%" is
a third of one core, ~6% of the box — not a third of the machine. And Sundae is a Hyper-V guest
ON Softserve, so those cores are Softserve's silicon; never count both. Detection/encode CPU also
tracks scene ACTIVITY (night IR grain generates motion), so compare medians of several rounds,
never two single windows.

---
