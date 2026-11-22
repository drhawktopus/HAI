# Fellowship Gauntlet Tournament Order — the full model×SKU×gauntlet matrix, run autonomously through Tolkien

**Status: TOURNAMENT COMPLETE 2026-08-31** — see [[Fellowship-Gauntlet-Conclusion-2026-08-31]] for the canonical closeout (three-tier tiny-worker verdict, 34 hard-fit exclusions, 10 recovery envelopes, 16 attempted-but-lost). This doc remains the **as-designed authority** for how the tournament was structured; the "BUILD IN PROGRESS" build-sequence below is historical (executed) and preserved for reproducibility.

⚠ **Roster in the SKU-lane table below is the 7-tablet Fellowship at tournament time (08-29→31). The current Fellowship is 11** ([[Tablet-Cluster-INDEX]]) — the same 7 plus **Sméagol** (Samsung SM-T560NU, armv7l/32-bit, arch-neutral text tasks only) and **Gimli / Boromir / Faramir** (three additional TB-X606F, class-B pool). Any future re-run of the tournament expands the B-pool to five tablets (frodo · legolas · gimli · boromir · faramir), leaves A/C unchanged, keeps sam solo, and adds an armv7 lane for Sméagol on models that build for 32-bit. Do not silently rewrite the historical roster — measurement provenance depends on it.

This is the #34 **area-B** deliverable ("tablet tiny-model worker kind") in its concrete form: an autonomous
Tolkien (Tablet Foundry) order that runs **every candidate model through its appropriate gauntlet on every SKU**,
scoring correctness + speed + fit, with all data persisted to rigby. Supersedes the ad-hoc division tournaments
(which produced a patchwork — different models on different single tablets). Why: 2026-08-29-claude-fellowship.

## The deliverable — a full matrix, not a patchwork
the owner, 2026-08-29: *"run every AI model on every tablet."* The earlier tournaments used "correctness is
deterministic / SoC-independent" as a reason to skip cells — which is true for the ANSWER but discards the half
that is NOT deterministic: **does the model run on this SKU, and how fast.** The deliverable is every cell actually
RUN, recording `ran / ok / wall_s / fit`.

## SKU lanes (the owner's pooling allowance)
Same-hardware tablets are equivalent, so a pair **splits** the work rather than duplicating it:

| lane | tablets | RAM/node | notes |
|---|---|---|---|
| **A-pool** | aragorn, gandalf | 2.79 GB | the only lane the 1.7 GB VLMs fit |
| **B-pool** | frodo, legolas, **sam** | 1.76 GB | sam IS Class B (proven by the gauntlet's isolated sam run — it matched B; the owner 2026-09-14) |
| **C-pool** | merry, pippin | 1.82 GB | 4-core |

**Three hardware classes: A, B, C.** The gauntlet ran sam alone only as a diagnostic, to disprove a recurring wrong
theory that something was wrong with it; it matched Class B (Fellowship-Gauntlet-Conclusion-2026-08-31). Its gauntlet
rows keep the label `lane=sam` only so that check stays auditable — it is not a class or a lane for new work.

Correctness is captured once (SKU-invariant); **speed + fit are per-lane** (the reason lanes exist).

## Model set (13) — straggler-checked against rigby (the model source)
MiniCPM5-1B · Qwen2.5-0.5B · Qwen2.5-1.5B · Qwen2.5-Coder-0.5B · Qwen2.5-Coder-1.5B · Qwen2.5-Coder-3B ·
Qwen3.5-0.8B · Qwen3.5-2B · Qwen3.5-4B · gemma-4-E2B · Qwen2-VL-2B · SmolVLM2-500M · SmolVLM2-2.2B.
**Excluded by size:** Qwen2.5-7B (~4.5 GB), Qwen2.5-14B (~9 GB) — exceed the 2.79 GB max node RAM.
**Model→gauntlet map (proposed):** the 10 text models each run **chat + tool + coding**; the 3 VLM models run **VLM**.
Fit is DISCOVERED where borderline (attempt, record OOM); models clearly too big for a lane are excluded by RAM
arithmetic and RECORDED as "excluded, RAM X < model Y" — a measured exclusion, never a silent skip.

## Mechanism — why this is a payload + one small fan-out change, not core surgery
Reading `tolkien-build/` (see [[Fellowship-Order-Taker-Design]]):
- **The fan-out is payload-agnostic.** A `map`-kind order (`tablet_fulfil.run_order`) ships the payload to each
  tablet and runs `python <payload> items.txt out.json` **on-node**. It does not care what the payload does.
- **No inference-refusal gate in intake.** The "model inference unsuitable" line in
  [[Fellowship-Workload-Classification-and-Scheduling-Logic]] is scheduler AUTO-ROUTING guidance; the order
  validator (`tablet_foundry._validate`) accepts `kind:"map"` with `name`/`payload`/`control`. An LLM-gauntlet
  map order just runs.
- **Orders run on ALL healthy tablets** (`tablet_runner.py:267` builds `hosts` from `fleet_health()`, no per-order
  pin). The ONE change for lanes: an **opt-in SKU-affinity** in `run_order` — partition the work queue by each
  cell's SKU tag and pin each worker to its host's SKU. Guarded by a spec field (e.g. `sku_affinity`), so every
  existing production order (sweep/map/triage) is byte-for-byte unaffected.

### Cell format (the seam — producer and consumer must agree)
`<SKU>~<model_basename>~<division>~<taskid>` — e.g. `A~Qwen2.5-Coder-1.5B-Instruct-Q4_K_M~coding~fizzbuzz`.
⚠ **Delimiter is `~`, NOT `|`** — the foundry `map` validator REJECTS any item containing `|` or a newline, so a
`|`-delimited order is HELD at intake (`HELD ... item(s) ... containing '|'`, verified 2026-08-29). `run_order` reads
field 0 (SKU) for placement; the payload parses fields 1–3 to run+score. `model_basename` is the gguf name WITHOUT
`.gguf` (e.g. `Qwen2.5-1.5B-Instruct-Q4_K_M` — keep the `-Instruct`, it is in the filename).

### Payload contract
On-tablet, `python payload items.txt out.json`. Emits a JSON list of rows; each row MUST carry `item` (the cell
string — the foundry's coverage validator counts distinct `item`) plus `ran/ok/wall_s/…`. The foundry adds `_host`
to every row, so per-tablet identity is free. A positive-control cell rides every order (a known-answer task; its
row must contain the control's `expect` substring or the run is VOID).

### Proven
`scratchpad/fellow_gauntlet_probe.py` (saved at `rigby:~/fellowship-data/payloads/`) ran 4 **tool** cells on
gandalf via the exact contract on 2026-08-29 — all scored correctly (grammar-constrained JSON via `--grammar-file`,
21–26 s each), map-row + control validated. The json-schema flag (`-j/-jf`) is a broken stub on the tablet build;
raw `--grammar-file` (GBNF) works — see [[Fellowship-Performance-Baselines-and-Benchmarking]] §Tool/Agent division.

## Data persistence — rigby (the owner: "save it all to rigby so you don't hold it in context")
Durable store: **`/mnt/fellowship/`** (`results/`, `harnesses/`, `payloads/`) — the dedicated **931 GB `sdb` HDD**
(ext4, label `fellowship`, 870 GB free), set up 2026-08-29 via `Lab\bin\run_remote.py` with a guarded format script
(rigby sudo = the lab **base standard**, in the network-credentials locker; `run_remote.py` feeds it to `sudo -S`
stdin, never printed). **`~/fellowship-data` symlinks to it**, so either path resolves. fstab uses `nofail` so a bad
disk never blocks rigby's headless boot. Seeded with `tool_results_20260829.json`, `vlm_real_results_20260829.json`,
all 5 division harnesses, and the proven payload; gauntlet-run results + telemetry accumulate here as JSONL. The
foundry's own per-batch checkpoints land separately at `/opt/arrstack/tablet-foundry/var` on the root disk
(container-writable); a follow-on can bind-mount `/mnt/fellowship` into the container so the foundry writes results
straight to the HDD.

## Build sequence (incremental — the Docker redeploy is the checkpoint)
1. Payload: **tool ✓ (proven)** + chat.
2. **Opt-in SKU-affinity** in `run_order` + unit test (existing orders unaffected).
3. **Deploy** — Docker rebuild on rigby via the two-build-lane procedure ([[Fellowship-Order-Taker-Design]]
   §Deployment; **`build` + `up -d`, never `restart` — the code is baked into the image**). *Pause for the owner.*
4. **Live decoy** across all 7 (pools split, sam solo, per-SKU rows land) — prove the whole autonomous loop before
   the full field.
5. Expand payload to **coding** (generate → exec against asserts on-node) + **VLM** (mtmd + images pre-staged to the
   class-A pool). Payload-only — no more foundry edits.

## Guardrails carried in
- Foundry sync verified before any edit (`tolkien-build` git-clean, running image built after the source commit,
  no mid-edit collision) — [[RUNBOOK]] mirror-deploy discipline.
- Assert non-zero counts; validate scorers against a known-positive before trusting a negative (the behavioral-run
  three-checker-bug lesson).
- There is **no guard preventing a too-big model load from OOM-killing a node** (bit the VLM run — OOM-killed
  aragorn's sshd; adb-root control plane held). The payload must RAM-check before load and record OOM rather than
  crash the node; a foundry-level load guard is a hardening follow-on.
