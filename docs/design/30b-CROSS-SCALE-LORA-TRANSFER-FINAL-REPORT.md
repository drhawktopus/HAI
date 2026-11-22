# Cross-scale LoRA transfer: FLUX.2-dev 32B → klein-4B results and status

Weight-space transfer between FLUX.2-dev (32B) and klein (4B) carries no actionable information. Depth-paired spectral transfer of adapters is statistically indistinguishable from shuffled block correspondence. See §10 for the null distribution controls.

Activation-space correspondence in the early double blocks (0, 2, 4) is positive relative to a 12-draw null distribution, with z-scores of +5.8, +5.0, and +2.6 respectively. Deep double blocks (5, 7) do not clear their own noise baseline. All 20 single-block comparisons are empty. The mean double-block margin is +0.19, stable across two captures and three null distributions.

Method rule: one derangement constitutes a single null draw, not a full null distribution. See §12 for full numbers and controls.

Adversarial review of this report: see [[30c-CROSS-SCALE-TRANSFER-ADVERSARIAL-REVIEW]].
Literature survey and chronological context: see `Archive\stage8-30-cross-scale-preview-20260822\30-CROSS-SCALE-ADAPTER-PREVIEW-RESEARCH.md` §0–§0g.

## LoRA Cross-Scale Transfer Design Doctrine

The lab maintains a fast preview pipeline to approximate FLUX.2-dev (32B) checkpoint quality on RTX 3070-class hardware without pausing the primary training job on Mothership.

The pipeline converts each 32B checkpoint’s LoRA into an adapter compatible with klein-4B, which fits within the 8 GB VRAM of the preview machine. This allows rendering to occur on a separate node while training continues uninterrupted.

Constraint: The conversion cost per checkpoint must be seconds of linear algebra. The method must not require teacher renders from the 32B model, as this would reintroduce training pauses.

## Why models cannot share adapters directly

Cross-scale LoRA transfer between FLUX.2-dev (32B) and FLUX.2-klein (4B) is unsupported because the underlying architectures differ in dimensionality, preventing direct weight mapping.

| model | double blocks | single blocks | inner dim | text encoder |
|---|--:|--:|--:|---|
| FLUX.2-dev 32B | 8 | 48 | 6144 | Mistral-Small-3.2-24B |
| FLUX.2-klein-4B | 5 | 20 | 3072 | Qwen3-4B |

LoRA adapters consist of low-rank factors shaped to specific weight matrices. Because klein and dev differ in depth, width, and text encoder architecture, every layer mismatch results in incompatible tensor shapes. Direct loading is structurally impossible.

## Tooling for LoRA Transfer

All source code resides in `E:\SSDShared\SSD\Open Access\Claude Home\prolora-flux2\` and is deployed to `{FOUNDRY_ROOT}\workspace\prolora\` on Mothership and BigHonker.

| component | function |
|---|---|
| `build_svd_cache.py` | Computes per-layer SVD of base models, outputting `.npz` files (U, S, Vh, shape). Supports two modes: randomized rank-k (fast) and **full economy SVD** (all directions; any k becomes a runtime slice). |
| `block_matcher.py` | v2: Performs relative-depth pairing (8→5, 48→20), endpoint-aligned and monotone. |
| `projection.py` | Executes spectral-index projection with `capture_fraction` and a factored transfer path. |
| `assembler.py` | Contains `assemble_multi`, which walks the checkpoint and emits klein-shaped LoRAs for multiple k values in a single pass over the caches. |
| `run_transfer.py` | Driver script; outputs `ck1200_to_klein.k{N}.safetensors`. |
| `renorm_adapter.py`, `build_denoise_ladder.py`, `build_v4.py` | Post-hoc variants for renormalization, pathway filtering, and dose scaling. |
| `cond_align_probe{,2,3,4}.py` | Conditioning-space alignment tests: v1–v2 (void), v3 (under-powered), v4 (powered). |
| `bake_pairs_bulk.py` | Bakes N prompts through both encoders for paired-conditioning tests. |

## Reusable Infrastructure

The following infrastructure was built alongside this result and is independent of the specific transfer outcome:

* `mode:"compute"` factory orders with `load` classification.
* Stop slips, path tokens, declared progress, and completion receipts for chained steps.
* Queue-while-busy placement at the fleet desk.
* Detected `weights.*` capabilities.

See `Runbook\` §"GENERAL COMPUTE AS A FACTORY ORDER" for operational details.

<!-- ARCHIVED: See Archive/cross-scale-lora-transfer-20260821/4.1-impossibility-argument.md -->

### Cross-width LoRA transfer pairing strategy

No published method measures cross-width subspace similarity between LoRA adapters. Methods that gate transfer on subspace similarity (LoRA-X arXiv 2501.16559, ProLoRA arXiv 2506.04244) rely on alignment metrics that lack discriminative power when ambient dimensions differ or exceed the rank. Computing a direction correspondence map from weights alone is provably impossible for differing widths: with orthonormal bases, the minimum-norm solution `P = U_t·U_sᵀ` yields zero residual for any pair of subspaces of rank `r` where both ambient dimensions are ≥ `r`, resulting in no discriminative power.

Weight-only statistics for degree-of-shared-structure exist and discriminate; `capture_fraction` (see LLM-Project/30b-CROSS-SCALE-LORA-TRANSFER-FINAL-REPORT.md §4.3) is one such metric.

Block pairing in this lab uses relative depth based on distillation lineage (student model *klein* was distilled from teacher model *dev*, so student depth tracks teacher depth monotonically). This avoids reliance on invalid matching cost matrices (LoRA-X Appendix D marks different-dimension pairs as INVALID) or name-based heuristics (Cross-LoRA arXiv 2508.05232 pairs layers by key name).

**Design fact:** All cross-width LoRA pairing in this library uses relative depth alignment derived from distillation lineage.

### Impossibility proof reference

The impossibility of establishing a transfer map via subspace similarity for differing widths is established by the Procrustes alignment property: any `r` orthonormal vectors map onto any other `r` orthonormal vectors when both ambient dimensions are ≥ `r`. See LLM-Project/30b-CROSS-SCALE-LORA-TRANSFER-FINAL-REPORT.md §4.2 for further mathematical detail.

### 4.2 The transfer

For a matched module, with rank-k left/right singular bases of the corresponding base weights:

```
ΔW_t = U_t,k · (U_s,kᵀ · ΔW_s · V_s,k) · V_t,kᵀ          ΔW_s = B_s·A_s   (the source LoRA)
```

This is spectral-index correspondence: source singular direction *i* ↔ target singular direction
*i*. It is what the papers' cross-dim construction degenerates to once the alignment step is seen
to be vacuous.

**Factored implementation (required):** The source LoRA has rank 32. The dense path materialises `U_t·U_sᵀ` — 2.7 GB per mlp module at full rank — and its first live run sat >15 minutes inside a single module before being stopped.

### 4.3 Measurable transfer quality

Because exact similarity gating is not computable, the pipeline reports `capture_fraction` as the proxy metric:

```
capture_fraction = ‖U_s,kᵀ · ΔW_s · V_s,k‖_F / ‖ΔW_s‖_F
```

This value represents the fraction of the source LoRA's energy visible to the rank-k source bases. Because U/V columns are singular-value ordered, a **full** SVD cache allows every k to be treated as a slice. This enables the capture-vs-k curve to be generated via a runtime sweep rather than a full rebuild.

### SVD cache construction for LoRA transfer

The library requires full-economy singular value decomposition (SVD) caches rather than randomized or thumbnail approximations. Randomized rank-256 caches capture <10% of the concept variance, leading to adapter failures that are misattributed to the transfer method rather than the data truncation.

Conformance instrument: Verify cache generation mode in the pipeline configuration. Full-economy SVDs must be generated for all models before adapter training.

| model | layers | mode | duration |
|---|--:|---|--:|
| dev-32B | 160 | full economy | ~92 min |
| klein-4B | 80 | full economy | 374 s |

See `LLM-Project/30b-CROSS-SCALE-LORA-TRANSFER-FINAL-REPORT.md` §5.3 for concept variance analysis.

### 5.2 Capture fraction vs per-shape chance baseline

The capture fraction measures the proportion of a dev-trained LoRA’s energy lying within its own top-k subspace: `‖U_sᵀ·ΔW_s·V_s‖/‖ΔW_s‖`. Because every term is source-side, the metric reflects training dynamics (gradient descent concentration in dominant directions), not cross-model geometry between dev and klein.

The chance baseline for a random k-dim source basis on an m×n matrix is `k/√(mn)`, calculated per shape. Pooling across shapes is invalid; it produces a false "below-chance tail" that was rejected during adversarial review.

| module class | n | k=256 | k=1024 | k=3072 |
|---|--:|--:|--:|--:|
| txt_attn.proj | 5 | 0.197 (**4.73×**) | 0.349 (2.09×) | 0.625 (1.25×) |
| txt_mlp.2 | 5 | 0.076 (4.47×) | 0.176 (2.59×) | 0.372 (1.82×) |
| img_mlp.2 | 5 | 0.074 (4.35×) | 0.209 (3.07×) | 0.415 (2.03×) |
| txt_attn.qkv | 5 | 0.089 (3.70×) | 0.212 (2.20×) | 0.404 (1.40×) |
| img_attn.qkv | 5 | 0.083 (3.45×) | 0.218 (2.27×) | 0.402 (1.39×) |
| img_mlp.0 | 5 | 0.050 (2.94×) | 0.145 (2.13×) | 0.305 (1.49×) |
| img_attn.proj | 5 | 0.117 (2.81×) | 0.304 (1.82×) | 0.604 (1.21×) |
| txt_mlp.0 | 5 | 0.039 (2.29×) | 0.110 (1.62×) | 0.254 (1.24×) |
| linear1 | 20 | 0.030 (2.20×) | 0.087 (1.57×) | 0.209 (1.25×) |
| linear2 | 20 | 0.042 (2.02×) | 0.134 (1.60×) | 0.312 (1.25×) |
| **all modules (median)** | 80 | **2.81×** | **1.83×** | **1.30×** |

All 80 modules exceed the per-shape chance baseline at every k. Incremental spectral shells also exceed chance (1.19×–8.94×).

The capture fraction serves as a source-side truncation bound: rank-k projection discards `(1 − capture)` of the LoRA’s energy before klein involvement. This correctly predicted the emitted-adapter norm deficit in §5.3 (ratios 0.047 / 0.134 / 0.288 ≈ capture fractions).

Testing cross-model geometry requires different measurements: principal angles between dev and klein bases under an honest common embedding, or `‖U_t,kᵀ·ΔW_t‖/‖ΔW_t‖` (how much of the projected delta lands in klein’s own meaningful subspace).

### Emitted adapter strength vs native klein LoRA

| adapter | median ‖B·A‖ | ratio vs native |
|---|--:|--:|
| k=256 | 0.126 | **0.047** |
| k=1024 | 0.326 | **0.134** |
| k=3072 | 0.710 | **0.288** |

The ratios approximate the capture fractions. The projection retains the captured component's energy and discards the remainder; consequently, an un-renormalized transferred adapter has a magnitude proportional to what truncation removes.

### 5.4 Render dose ladder

Renders use **seed 42 only** (`probe_seeds` was not used). There is no shuffled-block-pairing control and no native klein LoRA render; conclusions are single draws.

60 renders total on BigHonker (klein-4B Q8 GGUF, baked Qwen3 conditioning, 1024²). Renders are **bit-deterministic** — repeat cells under identical settings differ by mean|diff| = **0.000**; all differences are adapter signal.

Effective dose = weight-renorm factor × render strength.

| configuration | effective dose | outcome |
|---|--:|---|
| k=256/1024/3072 un-renormalized @ 0.6 | 0.03–0.17× | indistinguishable from bare klein |
| renormalized @ 0.6 | 0.6× | slight drift, concept absent |
| image-only @ 2.5× | 2.5× | keeps responding (8.0 units); does not recover gender |

Pairwise change magnitudes (mean|diff| over 6 prompts):

| comparison | change | reading |
|---|--:|---|
| duplicate configs (control) | **0.5** | fp16 rounding only ⇒ weight-renorm and the strength knob are **exactly interchangeable**; the LoRA path is linear |
| txt pathway IN vs OUT, equal dose | **9.3** | the text deltas do substantial work |
| image-only dose 1.5× → 2.5× | **8.0** | image pathway not saturated |
| full adapter 0.6× → 1.5× | ~15 | the dose response that reveals the concept |

---

### Image and text pathway roles in LoRA transfer

The image-side parameters (`img_attn.*`, `img_mlp.*`) encode body form and build. This path operates in latent space without an encoder, avoiding encoder mismatch. It transfers consistently and responds to training dose.

Configurations maximizing concept representation minimize prompt obedience. Removing the text pathway increases obedience but reduces concept fidelity. This is a design trade-off along one axis, not a bug to fix. All tested configurations slide along this trade-off curve.

### Conditioning-space alignment: negative result

Cross-scale LoRA transfer does not use conditioning-space alignment to re-base weights (e.g., $\Delta W_{klein} = Q \cdot \Delta W_{dev} \cdot P$). This pathway was discarded because empirical testing showed no retrievable correspondence between dev-conditioning and klein-conditioning tensors.

#### Test results

Four probes were executed to test for a map between conditioning spaces. The protocol used paired prompts (same string) to establish correspondence where weight-space pairing is absent.

| Probe | Protocol details | Result | Status |
|---|---|---|---|
| v1 | R² of klein→dev map, deranged control | Real 0.9634 / Shuffled 0.9628 | Void — inert metric. Control scores matched real scores; conditioning is ~99–99.9% prompt-independent bulk, so the fit captured padding. |
| v2 | Mean-centering + retrieval | 6/6 retrieval, held-out R² = 1.0000 | Void — leak. Centering used the mean over all prompts including held-out ones. Residuals summed to zero, making the held-out target arithmetically determined by the training set. |
| v3 | Train-only centering + train-only basis + random-Gaussian arm (n=6) | Real 3/6 (chance 0.167, p≈0.07); Random 0/6 | Protocol clean; under-powered, suggestive only. |
| v4 | Same protocol as v3, n=40 paired prompts, pre-registered criteria | Real 2/40 = 0.050 (chance 0.025, p = 0.26); Top-3 4/40; Random control 1/40 | Negative — effect absent. |

#### Underlying data properties

* **Tensor dimensions:** dev 15360-dim / klein 7680-dim over ~39–45 tokens.
* **Variance share:** Prompt-specific variance is 0.62% (dev) and 0.04% (klein). Semantics constitute a tiny residual on a large shared bulk.
* **Probe v4 detail:** The first five folds all retrieved the same candidate: a degenerate map predicting the mean.

#### Conclusion

The n=6 "3/6" result from probe v3 was noise. Real results (2/40) and random noise controls (1/40) in probe v4 differed by only one hit. The control landing on chance confirms protocol honesty; real data landing beside it confirms the absence of an effect. No reusable map exists between these conditioning spaces.

## 6. Cross-Scale LoRA Transfer Results

No configuration tested has produced a usable preview for dev→klein transfer. The negative result is geometric, not a scaling error.

### Literature context: Width preservation in LoRA transfer

Published positive results for LoRA weight transfer preserve model width. Cross-dimension transfer (distilled models with different widths) fails due to absent validity gates, insufficient echo signals, and foreign conditioning.

| Model relationship | Transfer outcome | Source / Validation |
|---|---|---|
| Identical architecture | Works; direct load | Community (dev↔schnell, dev→Chroma) |
| Depth-pruned, same width | Works with similarity gates | LoRA-X, ProLoRA (SDXL→SSD-1B) |
| Distilled, same width | Degrades; requires spectral arbitration | CASA (arXiv 2605.01929) |
| Distilled, different width | **Fails**: no valid gate, insufficient echo, foreign conditioning | THIS WORK |

The cross-dimension construction described in LoRA-X §4.2.2 lacks validation in either source paper. This work provides that validation: the result is negative.

## Corpus-health forecasting via Klein LoRA

**Option B** trains a Klein LoRA on the standard corpus (~41 min runtime). It serves as a **corpus-health forecaster** to detect caption bugs, crop collapse, quality-axis problems, and rough concept-binding issues before committing resources to full 32B Mothership training (approx. 13 h).

**Constraint:** This model does not preview actual 32B checkpoints. It must never be described as providing checkpoint previews.

## True checkpoint previews

True previews of 32B checkpoints require sampling the 32B function, retaining an irreducible cost. The cheapest honest forms implemented in the lab are:
1. Periodic pause-and-render.
2. Quick-preview transport carrying real dev renders when a pause is acceptable.

## Infrastructure independence

All infrastructure components—fleet compute orders, chained steps, transfer pipeline, and Klein render path—are independent of training outcomes. These systems are hardened by eight first-use seam fixes.

## Failure Modes in LoRA Transfer Evaluation

Four failure modes were identified during cross-scale evaluation. Each represents a protocol defect that produces misleading metrics if uncaught.

| # | Metric | Failure Signal | Consequence |
|---|---|---|---|
| 1 | `basis_similarity` (weight-space) | All pairs flat within 0.141–0.143 range | Shipped adapter with hollow basis |
| 2 | Pooled chance baseline | Regime mismatch per shape | Invented "below-chance spectral tail" artifact |
| 3 | Conditioning R² | Real value (0.9634) ≈ shuffled control (0.9628) | False null rejection; premature termination of lead |
| 4 | Mean-centering leak | Held-out R² = 1.0000 exactly | False positive; invalidation of downstream work |

Two standing diagnostic tells indicate protocol audit is required before accepting any metric:

1. **Real ≈ Control**: The observed value closely matches the null or shuffled baseline.
2. **Suspiciously Perfect**: Values reach theoretical limits (e.g., exact 1.0) without justification.

**Protocol Requirement**: Always run the null arm first to establish control baselines before interpreting experimental results.

### Probe Methodology

The probe protocol evaluates depth-paired spectral transfer against shuffled block correspondence. It consists of a single `mode: probe` order executed on BigHonker (`render.klein`). The order spec is archived as `prolora_controlled`; the execution card is built by `build_probe_card.py`.

#### Test Arms

| arm | adapter | isolation target |
|---|---|---|
| **BASE** | none (bare klein-4B) | Baseline model performance unaided |
| **REAL** | `ck1200_to_klein.k3072.renorm` | Depth-paired spectral transfer |
| **NULL** | Same weights as REAL, block correspondence **shuffled** | Variable control: does depth pairing carry information? |

#### Experimental Configuration

The REAL and NULL adapters contain byte-for-byte identical per-block deltas (same spectrum, norm, rank, and renormalisation). The only variance is the routing of dev blocks to klein blocks.

- **Prompts:** 3
- **Seeds:** 3 (42, 777, 1234)
- **Strength:** 1.5
- **Cells:** 36 total, all filled.

### 10.3 Directional equivalence: delta-image analysis

Magnitude parity is insufficient to establish transfer fidelity; perturbations of equal size may differ in kind. Delta images (`arm - base`, same prompt and seed) are compared via cosine similarity to determine if the transferred perturbation preserves direction.

#### Design fact: Transferred LoRA produces global luminance shift, not semantic change

The cross-scale LoRA transfer method reproduces the *direction* of the base adapter's latent perturbation but manifests visually as a uniform render adjustment rather than semantic content injection.

- **Directional alignment**: `cosine(Δreal, Δnull) = +0.540` (range: +0.125 to +0.888).
  - Interpretation: Independent perturbations yield cosine ~0. Values >0 indicate significant directional overlap; the transferred adapter applies the same perturbation vector as the source.
- **Visual signature**: Both REAL and NULL arms show near-achromatic positive wash.
  - REAL mean RGB shift: `[+14.6, +15.0, +12.8]`
  - NULL mean RGB shift: `[+22.9, +20.2, +19.9]`
  - Effect: Brightening and flattening of the render.

#### Baseline contrast: Native adapter behavior

Native (non-transferred) adapters exhibit categorical difference:
- Mean RGB shift: `[-0.03, +3.60, +5.33]`
- Pattern: Near-zero mean with high per-cell variance.
- Effect: Changes semantic content within the picture.

This variance pattern is the signature of an adapter carrying a distinct concept.

### Spectral-index transfer verdict

Spectral-index transfer via depth-paired blocks carries no information from dev to klein. The output is a norm-matched perturbation producing a brightness/contrast wash, which persists after block routing destruction.

Prior interpretations of emergent concepts under artifacts are incorrect; observed effects were the base model combined with the wash.

This empirically confirms §4.1’s impossibility argument: no correspondence map is derivable from weights alone. The heuristic relative depth mapping is not a substitute for such a map.

### 10.6 Scope limitations and open questions

The cross-scale LoRA transfer analysis is bounded by the following constraints:

* **Block-agnostic delta hypothesis.** The observed null result indicates that delta effects are block-agnostic; routing deltas to specific blocks does not improve performance. A stricter null test using random orthonormal frames with matched spectrum would strengthen this claim.
* **Limited hyperparameter sweep.** Results derive from a single source checkpoint (ck1200), rank k=3072, and strength 1.5. The dose ladder analysis in §5.4 found no strength at which the concept appears; this probe did not re-sweep that parameter space.
* **Weight-space only.** The analysis tests weight-space correspondence only. It does not test activation-space correspondence, which is identified as the next investigation vector (see LLM-Project/30b-CROSS-SCALE-LORA-TRANSFER-FINAL-REPORT.md §11).

---

## Activation experiment results: double-stream split analysis

The activation experiment (§11) produced a positive result in the double stream, replicated at two resolutions. Analysis requires reading the doubles/singles SPLIT; never use the pooled mean. The pooled margin (+0.038 / +0.039) is dominated by 20 non-predictive single-block fits and appears null. The doubles alone are strongly positive at both resolutions.

<!-- triage: manual, verify true -->
### Activation Map Fitting Methodology

The activation map fitting process maps per-block hidden-state activations from the dev-32B model to the klein-4B model on 40 alignment conditions (`align01..align40.cond`) at timestep 0.5.

**Data Capture**
Executions use `workspace\prolora\capture_activations.py` with orders `prolora_actcap_{dev,klein}_*`. Captures target 25 depth-paired blocks: dev doubles {0,2,4,5,7} map to klein doubles {0..4}; dev singles {0,2,5,…,47} (sampled every ~2.5) map to klein singles {0..19}. Two independent capture runs exist:
- **r320**: 400 image tokens/sample, yielding 16,000 rows.
- **r448**: 784 tokens/sample, yielding 31,360 rows. Requires `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True` (recipe item-7 spill fix) to fit in memory.

Captures use per-condition shards (atomic tmp+replace, params-fingerprinted) to ensure wedge respawns bank progress.

**Model Fitting**
Execution uses `fit_activation_map.py` with orders `prolora_actfit_r{320,448}`. The fit applies ridge regression (λ=1000) from dev activations to klein activations per block pair. Constraints and guards:
- **Holdout**: 10 of 40 conditions held out by prompt.
- **Centering**: Train-only centering applied.
- **Null Validation**: Deranged-pairing null included in the same pass (no fixed points, per §10).
- **Non-finite Guard**: Drops fp16-overflow rows matched in both arms; refuses runs if >0.1% of rows are non-finite (see §12.5).

**Verification Outputs**
Verdicts are stored in `F:\foundry\output\prolora\actfit_verdict_r320.json` and `..._r448.json`. Train rows/dimensions after holdout:
- r320: **1.95**
- r448: **3.83**

<!-- FILE: LLM-Project/30b-CROSS-SCALE-LORA-TRANSFER-FINAL-REPORT.md -->
<!-- REWRITE OF SECTION 12.2 -->

### 12.2 Result — the split, and the depth profile

Cross-scale LoRA transfer efficacy is defined by a positive mean margin in held-out $R^2$ (real minus null) for double blocks, and zero/negative margins for single blocks.

| stream | n | r320 mean margin (real − null, held-out R²) | r448 mean margin | read |
|---|---|---|---|---|
| **double blocks** | 5 | **+0.192** | **+0.179** | real held-out $R^2$ 0.69–0.89 on EVERY double, at BOTH resolutions; nulls 0.47–0.76 |
| single blocks | 20 | −0.0004 | +0.0045 | dead zero at both densities; real held-out $R^2$ **negative** in both arms (overfit) |

The mean margin is stable across measurement methods. The value holds as a design fact of the transfer mechanism.

### 12.2 Per-block correspondence profile (30b LoRA)

Correspondence between teacher and student is strongest in early double blocks and decays with depth. By double block 5 and 7, correspondence becomes unmeasurable against noise.

**Design fact:** A single random derangement constitutes a *null draw*, not a null distribution. Per-block significance claims require a null distribution (multiple independent draws) to establish spread and z-score thresholds. Mean-aggregated metrics may tolerate single draws; individual block metrics cannot.

**Implementation:** `fit_activation_map.py --null-draws K`
*   `K=1` reproduces earlier single-draw verdicts.
*   Results must record `null_seed` and `null_draws`; the null configuration is part of the result, not a hidden constant.

**Measurement results:**
Evaluated against a null distribution of 12 independent derangements (`actfit_verdict_r448_nulldist12.json`). Blocks clear or fail to clear based on z-score thresholds derived from this distribution.

| Block | Real Held-out R² | Null Mean ± SD (12 draws) | Margin | Z-score | Verdict |
|---|---|---|---|---|---|
| double_blocks.0→0 | +0.893 | 0.517 ± 0.065 | +0.376 | +5.8 | Significant |
| double_blocks.2→1 | +0.871 | 0.607 ± 0.053 | +0.264 | +5.0 | Significant |
| double_blocks.4→2 | +0.827 | 0.640 ± 0.071 | +0.187 | +2.6 | Significant |
| double_blocks.5→3 | +0.787 | 0.733 ± 0.111 | +0.054 | +0.5 | Not distinguishable |
| double_blocks.7→4 | +0.736 | 0.659 ± 0.115 | +0.076 | +0.7 | Not distinguishable |
| singles (20) | −0.36 … −0.07 | — | +0.005 mean | 1/20 > 2 | No signal |

**Interpretation constraints:**
*   A margin between two non-predictive fits is not evidence.
*   Example: `single_blocks.10→4` shows z=+5.4 but real held-out R² is −0.241 (no predictive power). The high z-score results from an unusually tight null spread (± 0.013) and is not a valid finding.

### 12.3 Replication of per-block margin profiles and null-seed dependency

Per-block margin profile correlation is high when the random number generator seed is identical across captures, but collapses when seeds differ. This confirms that observed correlations were driven by shared null terms rather than reproducible signal.

| Comparison condition | Doubles r | Singles r |
|---|---|---|
| Two captures, same null draw (seed 4242) | +0.999 | +0.972 |
| Two null draws, same capture | +0.249 | +0.485 |

The **mean** margin replicates across captures and seeds (+0.192 / +0.179 / +0.191). Held-out R² per block is stable regardless of seed (0.736–0.893 on doubles).

Design rule for replication tests: Verify that the metric being tested actually varies between runs. Correlation between two runs only validates features that differ; if runs share a null term or seed, high correlation may reflect that shared component rather than discovered structure.

### Design Doctrine: Cross-Scale LoRA Transfer — Preservation Limits

**Double-block activation preservation.** Distillation preserves dev function in klein where streams are separate. A linear map from dev double-block activations predicts klein's held-out double-block activations significantly better than the deranged control. This correspondence holds across two resolutions, confirming the design prediction in §11.

**Single-stream non-preservation.** Single fits do not predict held-out data (negative R² at 1.95 and 3.83 train rows/dim). Increasing row density does not restore predictive power. The apparent "early positive / late negative" pattern observed in earlier analyses was an artifact of paired runs sharing the same seeded derangement, not a property of the blocks themselves. Across 12 independent draws, single streams show no signal against a proper null (mean margin +0.005; only one block exceeded z=2, which had a negative real R²). Single stream transfer is currently unmeasurable at these sample sizes.

## Fp16 overflow handling in aggregate fits

Deep dev single blocks may reach the fp16 maximum value (65,504), producing infinite values. Klein max (2,352) does not produce infinities.

The fit pipeline applies the following rules to aggregates over fp16-derived data:

1. **Finiteness gate**: Any aggregate must pass a finiteness check before downstream consumption.
2. **Non-finite row handling**: Non-finite rows are dropped from both arms of the comparison.
3. **Rejection threshold**: The fit refuses if more than 0.1% of rows are dropped due to non-finite values.

## Double-stream transfer protocol §12.6

The validated double-block deltas (indices 0, 2, 4) constitute the sufficient statistic for activation-fitted correspondence. These maps are derived from the fitted double-stream models in §12.2a and conform to the cost constraints established in §11.

To probe actual transfer fidelity:

1. Map a development adapter’s double-block deltas through the activation-fitted correspondence using only indices 0, 2, and 4.
2. Leave single-stream depths at depth pairing or zero.
3. Execute the probe harness defined in §10: shuffled null baseline, native-Klein baseline, and three seeds.

The existing §10 machinery enables this probe at low cost. Indices 5 and 7 are excluded; their maps are statistically indistinguishable from scrambled pairings, which §10 demonstrates have no transfer value.

**Finding:** Distillation preserves function in the double stream only; weight geometry does not reveal this preservation, but activations do.
