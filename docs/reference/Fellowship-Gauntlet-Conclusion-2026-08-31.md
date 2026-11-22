# Fellowship Gauntlet conclusion — full text + VLM matrix synthesis

**Date:** 2026-08-31  
**Project:** #34 area B, Phase 4 matrix closeout  
**Status:** **GAUNTLET CANONICAL CLOSEOUT COMPLETE.** The original 13 envelopes remain verbatim and
retain their shipped usability flags. A completed recovery audit classified all 103 original absent
cells; 53 genuinely missing runnable cells were recovered through 10 complete, usable,
control-green envelopes and overlaid without replacing an original row. The remaining 50 are
intentionally unrecovered audit limits: 34 hard-fit exclusions and 16 attempted-but-lost/unrecorded
cells. Zero cells are ambiguous or coverage-accounting artifacts.

## Governing sources and decision rule

This report applies, rather than replaces, the existing design and rubrics:

- [[Fellowship-Gauntlet-Tournament-Order]] — full model×SKU requirement, SKU lanes, fit/speed purpose, control and coverage contract.
- [[34-CHARTER]] — four divisions, deterministic verification, §45–48 behavior criteria, cheapest reliable intelligence principle.
- [[Fellowship-Performance-Baselines-and-Benchmarking]] — authoritative Phase 4 division rubrics, champion scorecards, behavior evaluation, and earlier VLM real-photo validation.
- [[Native-Intelligence-Review-Campaign-Machinery]], [[PROJECT-REGISTRY]], and PROJECT-BACKLOG — place in Project #34, Project #35, and the Delegation epic (#12 → #34 → #35).
- [[Fellowship-Gauntlet-Results-for-HAI-2026-08-30]] — text-result retrieval manifest and source hashes.

The primary decision rule is the original rubric. The matrix contributes measured model×lane fit and startup-inclusive wall time and checks whether the earlier quality ordering survives broader hardware coverage. Supplemental Pareto/routing analysis is explicitly labeled and does not redefine a champion.

## Executive conclusion

The Fellowship has three credible tiny-worker tiers:

1. **Reliable general/tool worker — Qwen2.5-1.5B.** It remains the practical default across tablet classes: matrix chat 8/8 on every lane, composite tool 8/8 on A/B/C and 4/4 of the measured Sam cells, plus the authoritative Phase 4 behavior score of 6/6.
2. **Reliable coding worker — Qwen2.5-Coder-1.5B.** It remains the coding champion: authoritative 7/8 on the broader eight-task suite, behavior 6/6, and matrix 5/5 coding + 8/8 composite tool on every lane.
3. **Fast specialist tier — Qwen2.5-0.5B and Coder-0.5B.** They are excellent only inside narrower envelopes. General 0.5B scored 8/8 on the simple matrix chat set at 6.1–7.5 s median, but failed both no-tool declines. Coder-0.5B scored 5/5 on the matrix's simple implementation-only code set, but the authoritative broader suite scored it 4/8. They are cheap workers, not replacements for the 1.5B defaults.

For vision, **SmolVLM2-500M is the only broadly deployable VLM**: it completed every lane with the
same 5/7 quality pattern (A median 25.3 s; B 28.6 s; C 45.5 s; Sam 28.0 s). **Qwen2-VL-2B remains
the class-A-only quality specialist**: the recovered A matrix is 6/7 at 83.3 s median, corroborating
the earlier 6/7 synthetic and 5/5 real-photo results. SmolVLM2-2.2B earns no role: recovered A is
the same 5/7 as 500M at 155.9 s median, and it is RAM-excluded from B/C/Sam.

No heavier text model earns default routing. Gemma E2B works cleanly on A but is far slower without improving this task set. Qwen3.5-2B, Coder-3B, and Qwen3.5-4B show timeouts, incomplete coverage, or storage/RAM pressure severe enough to make them poor workers. Qwen3.5-2B retains its original class-A chat-champion status, but only for quality-sensitive chat where its higher cost and weaker not-found recovery are acceptable.

## 1. Conclusions required by the original Gauntlet rubrics

### 1.1 Original Phase 4 champions remain authoritative

| Division | Controlling conclusion | Matrix effect |
|---|---|---|
| Chat/generalist | A: **Qwen3.5-2B no-think**; B/smaller: **Qwen2.5-1.5B**, both 8/10 in the original suite | Qwen2.5-1.5B is corroborated at 8/8 on every lane. Qwen2.5-0.5B also gets 8/8, but only on the reduced short-answer set. Recovered Qwen3.5-2B A is 6/8 on that reduced set, so it does not supersede the broader original champion ruling. |
| Coding | **Qwen2.5-Coder-1.5B**, authoritative 7/8 across all three hardware classes | Corroborated at 5/5 on every lane. Coder-0.5B also gets 5/5, but the matrix omitted the broader suite's debug, modify, and parse cases on which the 0.5B model fell to 4/8. |
| Tool/agent | **Qwen2.5-1.5B**, authoritative 6/6 selection · 6/6 arguments · 2/2 decline; Coder-1.5B and Qwen3.5-2B tied on the easy routing set | Qwen2.5-1.5B and Coder-1.5B again clear every measured composite cell. The matrix confirms that selection-only `ok` is insufficient: MiniCPM selects 6/8 but passes only 3/8 once arguments are included. |
| VLM | Quality: **Qwen2-VL-2B** (6/7 synthetic, 5/5 real photos). Deployable: **SmolVLM2-500M** (5/7) | Recovered A repeats Qwen2-VL at 6/7 and both Smol models at 5/7. Smol500 is now 5/7 on all four lanes; the two 2B VLMs remain arithmetically RAM-excluded from B/C/Sam. |
| Behavior §45–47 | Qwen2.5-1.5B 6/6; Coder-1.5B 6/6; Qwen3.5-2B 5/6 due to blind repeat after not-found | Not rerun by the matrix; no change. The original behavior set remains small/easy and should be treated as a cleared floor, not proof of excellence. |

### 1.2 Why the matrix cannot silently redefine quality

- **Chat:** 8 short factual/arithmetic/language cells, rather than the original 10-task broader chat evaluation.
- **Coding:** 5 implementation functions only. The authoritative 8-task set also contains debug, modification, and parsing work.
- **Tool:** 8 easy, grammar-constrained cells. The grammar-off control and harder ambiguity/recovery work are outside this matrix payload.
- **VLM:** the original seven synthetic cells are preserved, but A never ran in these envelopes.
- **Sampling:** one deterministic execution per task×lane; useful for fit and wall time, not a runtime distribution.

Accordingly, a reduced-set tie is corroboration, not a dethroning event.

### 1.3 Completeness and usability verdict

Canonical reconstruction from the submitted generators, original row `item` keys, recovery audit,
and exact recovery overlay gives:

| Scope | Expected cells | Present rows | Ran | Quality pass | Quality fail | Failed/timeouts | Recorded exclusions | Missing |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Text matrix | 604 | 554 | 470 | 405 | 65 | 56 | 28 | **50** |
| VLM matrix | 84 | 84 | 42 | 31 | 11 | 0 | 42 | **0** |
| **Total** | **688** | **638** | **512** | **436** | **76** | **56** | **70** | **50** |

Text controls are extra to the 604 matrix cells. Four text controls are absent; only these text envelopes are usable as shipped: MiniCPM5-1B, Qwen2.5-0.5B, Coder-0.5B, and Coder-1.5B.

The 103-cell original absence set is preserved as an audit contract in the canonical v2 JSON and
cell CSV. Recovery rows are accepted only when the audit category is
`genuinely-missing-runnable`, and only as overlays into an originally absent item. The 53 recovered
rows comprise 32 class-A text cells and all 21 class-A VLM cells.

The remaining 50 row-absent cells are deliberately retained, not a recovery queue:

| Audit classification | Cells | Meaning |
|---|---:|---|
| hard-fit-exclusion | **34** | Arithmetic/storage/headroom evidence establishes that the cell should not run: Gemma C tool (8), Coder-3B A/C tool (10), and Qwen3.5-4B A chat/tool (16). |
| attempted-lost-unrecorded | **16** | Work was attempted but no per-cell row survived: Qwen2.5-1.5B Sam tool (4) and Coder-3B A/C coding/tool (12). Absence is preserved rather than reconstructed. |
| ambiguous | **0** | No cell lacks a defensible classification. |
| coverage-accounting-artifact | **0** | The four stale `never_measured` entries were already present rows, not canonical gaps. |

Every original VLM envelope remains VOID as shipped, but the recovery layer is complete: 10/10
recovery envelopes are complete, usable, and control-green. All 23 source-envelope SHA-256 values
(13 original + 10 recovery) were unchanged across reduction.

**Routing integrity:** all 638 present canonical rows are on the correct SKU lane; **0 routing
mismatches**. Every model/task that ran on at least two lanes has the same rubric pass/fail result;
**0 fixed-seed correctness inconsistencies**. This validates the original claim that quality is
SKU-invariant while fit and speed are not.

**Coverage-instrument anomaly:** `gauntlet_m_qwen2515b` declares eight `never_measured` items, but four of them are present in its rows; canonical reconstruction finds four actual missing cells, consistent with `65 expected − 61 measured`. Downstream analysis must reconstruct coverage from expected item sets and row keys rather than blindly count `never_measured`.

### 1.4 Rubric scorecards from the matrix

The following scores are pooled across lanes only to expose repeated consistency; lane-level tables are in the analysis artifacts.

| Division/model | Pass / ran | Expected | Interpretation |
|---|---:|---:|---|
| Chat — Qwen2.5-0.5B | 32/32 | 32 | Fast ceiling on this simple set; not a broader champion claim. |
| Chat — Qwen2.5-1.5B | 32/32 | 32 | Strong corroboration of the default generalist. |
| Chat — MiniCPM5-1B | 28/32 | 32 | Repeats the same plural-of-mouse miss on every lane. |
| Chat — Qwen3.5-0.8B (A recovery) | 6/8 | 8 | Slower and weaker than the established 0.5B/1.5B defaults. |
| Chat — Qwen3.5-2B (A recovery) | 6/8 | 8 | Reduced-set result; does not override its broader original 8/10 class-A ruling. |
| Coding — Coder-0.5B | 20/20 | 20 | Simple implementation specialist only; broader rubric remains 4/8. |
| Coding — Coder-1.5B | 20/20 | 20 | Corroborates the authoritative champion. |
| Tool — Coder-1.5B | 32/32 | 32 | Complete composite pass on every lane. |
| Tool — Qwen2.5-1.5B | 28/28 | 32 | Complete composite pass on every measured cell; four Sam cells remain intentionally attempted-lost/unrecorded. |
| Tool — Qwen2.5-0.5B | 24/32 | 32 | Arguments are correct, but both no-tool declines fail on every lane. |
| Tool — Coder-0.5B | 24/32 | 32 | Two systematic misses per lane: `querydb_order` and `nofit_weather`. |
| Tool — MiniCPM5-1B | 12/32 | 32 | Only 1/6 argument cells passes per lane; selection-only scoring would overstate it as 24/32. |
| Tool — Qwen3.5-0.8B (A recovery) | 5/8 | 8 | Three log/database routing misses; no worker role earned. |
| Tool — Qwen3.5-2B (A recovery) | 8/8 | 8 | Clean but materially slower than the 1.5B default. |
| VLM — Qwen2-VL-2B (A recovery) | 6/7 | 7 | Confirms the class-A quality-specialist role. |
| VLM — SmolVLM2-2.2B (A recovery) | 5/7 | 7 | Same quality as 500M at much higher latency. |
| VLM — SmolVLM2-500M | 20/28 | 28 | 5/7 on every lane; systematic misses are spatial color and last-word order. |

## 2. Supplemental findings from the same measurements

### 2.1 Practical capability and routing map

These are analysis recommendations; **the model-size ceiling is IMPLEMENTED in Tolkien's routing (2026-10-07):** classes B
(sam included — its own SKU lane, class B hardware) and C are never dealt a model above 1.5B; class A takes the 2B
(`tablet_runner.CLASS_MODEL_CEILING_B`, `_apply_class_rules`; the model is read from the order's `model` or the payload's
`MODEL_NAME_FIXED`; an unstated size is logged and not blocked). Evidence: warm Qwen3.5-2B on gimli/sam/boromir/faramir
ran 0.22–0.29 tok/s, 12–55 min a turn, while class A served it at an 8 s median. Warm chat at ≤1.5B suits every class
(Coder-1.5B warm median: A 3.4 s · B 4.5 s · C 4.6 s — `Datasets\Project34-Menu\chat1.md`).

| Tablet class | Default general/tool | Fast/cheap | Coding | VLM | Highest-cost tolerable specialist |
|---|---|---|---|---|---|
| **A — nominal 4 GB, 2.79 GB visible** (aragorn/gandalf) | **Qwen2.5-1.5B** | Qwen2.5-0.5B for simple short-answer work | **Coder-1.5B**; Coder-0.5B only for simple functions | **SmolVLM2-500M** default (5/7, 25.3 s); **Qwen2-VL-2B** quality specialist (6/7, 83.3 s) | Qwen3.5-2B no-think remains a quality-chat specialist under the broader original rubric; its recovered reduced matrix is 6/8 chat and 8/8 tool. |
| **B — 1.76 GB** (frodo/legolas) | **Qwen2.5-1.5B** | Qwen2.5-0.5B | **Coder-1.5B** | **SmolVLM2-500M** | None of the tested heavier models earns routine placement. |
| **C — 1.82 GB / 4 core** (merry/pippin) | **Qwen2.5-1.5B**, background/queued | Qwen2.5-0.5B | **Coder-1.5B**, background/queued | **SmolVLM2-500M**, latency-tolerant | None; the core-count penalty makes heavy models especially poor. |
| **Sam solo — 1.76 GB** | **Qwen2.5-1.5B** (four tool cells still missing) | Qwen2.5-0.5B | **Coder-1.5B** | **SmolVLM2-500M** | None. Its earlier ~25% GFLOPS deficit again does not appear as a general inference penalty. |

### 2.2 Speed and hardware effects

Selected startup-inclusive median wall times per cell:

| Model/division | A | B | C | Sam |
|---|---:|---:|---:|---:|
| Qwen2.5-0.5B chat | 6.1 s | 6.6 s | 7.5 s | 6.5 s |
| Qwen2.5-1.5B chat | 10.4 s | 11.7 s | 15.9 s | 10.8 s |
| Qwen2.5-1.5B tool | 23.5 s | 25.1 s | 40.5 s | 26.2 s* |
| Coder-0.5B coding | 9.8 s | 10.7 s | 13.5 s | 10.0 s |
| Coder-1.5B coding | 18.6 s | 24.5 s | 28.9 s | 15.6 s |
| Coder-1.5B tool | 22.7 s | 24.6 s | 38.0 s | 23.9 s |
| SmolVLM2-500M | 25.3 s | 28.6 s | 45.5 s | 28.0 s |

\* Four of eight Sam tool cells are present.

- For the complete shared text arms, C is generally **1.24–1.71× slower than A**, depending on model/division. It remains useful for queued parallel throughput, not latency-sensitive single requests.
- B and Sam are broadly similar for inference; the standalone Sam lane was worth measuring and does not justify a blanket Sam penalty.
- Smol500 vision encoding consumes a median **19.5/28.6 s on B, 34.6/45.5 s on C, and 19.7/28.0 s on Sam** (about 68–76% of wall time), reinforcing the prior finding that vision encode dominates.
- The nominal 4 GB class materially matters more for **fit/headroom** than for light-model speed. Gemma chat completes 8/8 on A at 58.4 s median, but only 1/8 on B and Sam (seven timeouts each); C records eight storage-headroom exclusions. Recovered A evidence confirms it as the only defensible home for the 2B VLM specialists.

### 2.3 Pareto interpretation

Using rubric yield/reliability (maximize) and median wall time (minimize):

- **Simple chat:** Qwen2.5-0.5B is the measured Pareto winner on every lane. Broader-rubric quality still keeps Qwen2.5-1.5B as the default.
- **Simple coding:** Coder-0.5B is the measured speed frontier; Coder-1.5B returns to the quality frontier when the authoritative debug/modify/parse cases are restored.
- **Tool:** two frontiers exist on every measured lane: Qwen2.5-0.5B (~half the latency, 6/8) and Coder-1.5B (8/8). Qwen2.5-1.5B is the practical generalist default because it avoids maintaining a separate coding model and also has the original 6/6 behavior score.
- **VLM on B/C/Sam:** Smol500 is the only runnable candidate. On A, the recovered field measures the real tradeoff frontier directly: Smol500 5/7 at 25.3 s versus Qwen2-VL 6/7 at 83.3 s.

### 2.4 Models/combinations that should not be routed

| Combination | Evidence-backed disposition |
|---|---|
| Qwen3.5-4B on any Fellowship lane | **Blacklist pending a materially different runtime/model build.** No chat cell completes; B/Sam tool cells mostly time out; C records storage exclusions; all 16 A cells are audited hard-fit exclusions. |
| Qwen2.5-Coder-3B on B/C/Sam | **Do not route.** B/Sam coding complete only 3/5 at ~134–141 s median and time out twice; tool calls take ~232–259 s. Its remaining A/C gaps are explicitly split between hard-fit and attempted-lost evidence; absence is not treated as a successful run. |
| Gemma E2B chat on B/C/Sam | **Do not route.** Seven of eight B/Sam chat cells time out; C is storage-excluded. A runs but is ~5–10× slower than the 0.5B/1.5B defaults without a quality gain on this set. |
| Qwen3.5-2B on B/C/Sam | **Do not use as a routine worker.** Tool quality is clean but takes ~110–194 s median; B/Sam chat time out 6/8 and C chat is only 6/8 at ~167 s. Retain only the earlier class-A chat-specialist role. |
| Qwen3.5-0.8B | **No route preference.** It is slower and less accurate than Qwen2.5-0.5B/1.5B on every completed lane. |
| MiniCPM5-1B tool work | **Do not route.** Composite 3/8 per lane; it routinely omits required arguments. |
| Qwen2.5-0.5B or Coder-0.5B as an unconstrained agent | **Do not route.** Each has systematic decline/tool-choice failures despite fast execution. |
| Qwen2-VL-2B or SmolVLM2-2.2B on B/C/Sam | **Hard exclusion.** All 42 cells are pre-load RAM exclusions, preventing the OOM/sshd failure seen in earlier work. |
| SmolVLM2-2.2B on A | **No production role.** Earlier quality equals 500M (5/7) at roughly six times the wall time and with swap pressure. |

`model-too-big-for-node` in the text payload is a **storage-headroom** exclusion, not a RAM measurement. `model-too-big-for-node-ram` in the VLM payload is the explicit RAM-arithmetic guard. The result rows carry no general RAM, temperature, battery, or power telemetry; no energy ranking is supportable.

### 2.5 Individual versus distributed use

- The evidence supports **independent task distribution**: same-task quality is invariant across lanes, while paired same-SKU nodes can split queues and C nodes add useful background throughput.
- It does **not** support running every task as an ensemble. The only Phase 5 first cut remains the earlier 5-way Qwen2.5-1.5B result: majority rescued one random miss and could not repair a shared systematic misconception.
- Deterministic verification should remain the first choice for code, tool schemas, and fixed-answer vision cells. Use best-of-N or heterogeneous specialists only where the verifier and expected gain are defined.
- No concurrency/load-once experiment is present here. Wall times include CLI/model startup, so a persistent server may change latency materially but is not measured by this field.

### 2.6 What Starfleet can honestly advertise

The Fellowship factory can honestly advertise:

- seven local, open-weight, CPU tablet workers for independent, latency-tolerant microtasks;
- reliable 1.5B general/tool and coding specialists with deterministic verification;
- a fast 0.5B tier for narrow, pre-classified work;
- SmolVLM2-500M image reading on the 2 GB-class tablets as well as class A, with known weaknesses in spatial color and positional text order;
- class-A-only, paced Qwen2-VL quality vision as an experimental specialist, not a general SLA;
- measured lane-specific startup-inclusive medians and hard exclusions, with no claim yet about energy, concurrent saturation, persistent-server latency, or full 13-order coverage.

It should not advertise generic autonomous agents, 2B VLM availability on small tablets, heavy 3B/4B text inference, or ensemble intelligence as production capabilities.

## 3. Recommendations requiring later implementation or policy changes

Nothing in this section was implemented by this analysis.

1. **Do not reopen the 50 intentionally unrecovered cells as cleanup work.** Hard-fit exclusions are
   negative results; attempted-lost cells remain absent evidence. Any new measurement is a separately
   authorized experiment, not Gauntlet recovery.
2. **Fix the coverage report race/semantics.** Derive `never_measured` from final expected-minus-row keys after workers settle; add a regression for rows that arrive from a retiring worker.
3. **Publish empirical allow/deny constraints to Tolkien/Spock only after approval.** Candidate fields: model, division, SKU, max concurrency, required headroom, p50/p95 wall time, hard-exclusion reason, and evidence/source hash.
4. **Keep routing deterministic-first.** A model may advise classification, but declared task type, schema, resource guard, verification, timeout, and escalation remain deterministic.
5. **Restore the full quality rubric in any champion-deciding rerun.** The matrix's five coding implementations and easy tool set are suitable fit probes, not a replacement tournament.
6. **Measure persistent VLM service behavior.** Load-once latency, repeated image memory growth, enforced downscale, one-at-a-time pacing, and recovery after failure must be measured before a VLM worker SLA.
7. **Add telemetry before energy/resource policy.** Capture MemAvailable/swap before and after, peak RSS where possible, thermal state, battery/power, and explicit model-load versus inference time. Current results support time/fit rules, not energy optimization.
8. **Run Phase 5 only behind its existing the owner gate.** Use the measured 0.5B/1.5B specialists for heterogeneous solver/verifier or best-of-N experiments; compare against one stronger 1.5B attempt and preserve deterministic checks.
9. **Keep Project #35 separate.** These measurements may later define hardware/routing constraints for trained native-intelligence components, but they do not authorize training, adapter deployment, or routing changes.

## Evidence limits and retest queue

This is not a remaining recovery queue. If later campaigns need stronger evidence, the highest-value
new experiments are:

1. a full-rubric cross-lane confirmation for the two 0.5B models before granting them anything beyond narrow specialist routes;
2. repeated runtime rounds for p95/spread and persistent-server comparisons;
3. harder tool ambiguity and recovery cases for the three models that cleared the easy tool ceiling;
4. persistent VLM load-once and memory-growth measurements before an SLA.

Do not interpret the anomalous C advantage for Qwen3.5-2B as a hardware win: B/Sam medians are computed from the two cells that escaped six timeouts, so the samples are not comparable.

## Traceability and generated artifacts

Raw source envelopes remain verbatim in:

`E:\SSDShared\tabfoundry-jobs\outbox\gauntlet_m_*.results.json`  
`E:\SSDShared\tabfoundry-jobs\outbox\gauntlet_vlm_*.results.json`  
`E:\SSDShared\tabfoundry-jobs\outbox\gauntlet_recovera_*.results.json`  
`E:\SSDShared\tabfoundry-jobs\outbox\gauntlet_vlm_recovera_*.results.json`

The compact, reproducible analysis package is filed under:

`Documentation\Datasets\Fellowship-Gauntlet-2026-08-31\`

- `analyze_full_gauntlet.py` — canonical matrix reconstruction, exact audit validator, recovery overlay, and scorer.
- `full-gauntlet-analysis.json` — v2 canonical analysis: all 23 source filenames/bytes/SHA-256,
  103 audit classifications, 53 recovery overlays, 50 retained absences, envelope verification,
  pooled/lane scores, Pareto flags, failure reasons, and anomalies.
- `full-gauntlet-model-lane.csv` — model×division×lane scores, reliability, median wall/encode time, tool submetrics.
- `full-gauntlet-cells.csv` — all 688 expected cells with state/source plus original-absence,
  audit-category/evidence, forward-hardware-class, and recovery-overlay fields.
- `recovery-audit.json` / `.csv` / `.md` — retained 103-cell audit and human-readable summary.
- `recovery-plan.json` — the exact 53-cell recovery plan and ten-envelope sequence used.
- `closeout-verification.json` — final canonical counts, source-integrity verdicts, and durable
  package hashes.
- `gen_vlm_recovery_a.py` — historical first recovery generator retained for traceability.

The reducer applies the tool rubric as `tool selection AND required arguments` for fittable tasks; the payload's top-level `ok` records selection only. This distinction is necessary to reproduce the 3/8 MiniCPM composite result and avoid a false 6/8 pass.
