# Benchmark Result: LoRA vs. Full Fine-Tune (Klein Scope)

**Status:** Definitive benchmark record.

## Design Fact
Full fine-tuning of the Klein model using `adafactor` at learning rate `3.0e-5` with an effective batch size of 1 and no gradient accumulation results in model divergence. The LoRA rank-128 configuration under identical corpus conditions produces coherent outputs.

## Experimental Parameters
| Parameter | Arm 1 (LoRA) | Arm 2 (Full Fine-Tune) |
|---|---|---|
| **Method** | Rank 128 adapter | Full weight update |
| **Optimizer/LR** | adamw8bit @ 1e-4 | adafactor @ 3.0e-5 |
| **Step Rate** | 1.22 s/it | 1.46–1.70 s/it |
| **Wall Clock** | 41 min | 1 h 35 m (incl. 2 pauses) |
| **Final Loss** | ~0.65 | ~1.09 |

## Failure Analysis
The failure of Arm 2 is attributed to the application of an `ai-toolkit` template designed for an 8B Flex model (requiring 48 GB VRAM) to a 4B model at batch size 1. The resulting noisy gradients from aggressive full-weight updates destroy model coherence.

## Measured Convergence Behavior
- **Observation:** Visual inspection suggested mode collapse at step 500.
- **Measurement:** Mean pairwise cross-prompt difference on an 8x8 downsample scored **49.9** at step 500 (typical baseline: 120–148). This indicates reduced prompt dependence, not identical outputs.
- **Correction:** Outputs are distinct; diversity returns by step 1250 (score: 141.2). The correct classification is **partial convergence**, not mode collapse.

## Next Experimental Steps (if revisiting Full FT)
To evaluate full fine-tuning fairly on this architecture:
1. Reduce learning rate by ~10x (e.g., `3e-6`).
2. Increase effective batch size via gradient accumulation.
3. Isolate variables (one change per run).

**Note:** Results are specific to this configuration. They do not refute full fine-tuning as a general method, only its viability under these specific hyperparameters and resource constraints.

<!-- FILE: 29-BESPOKE-KLEIN-SCOPE -->
<!-- SECTION: ### LoRA safety prior overwrite capability
A LoRA can overwrite a base model's safety prior if training exceeds 250 steps (§8d).

### CK peak performance benchmark range
CK peak performance lands approximately at ck1000. This aligns with BFL's published band of 750-1500 and v11's elected ck1200.

### 4B full fine-tune resource requirements
A 4B full fine-tune fits on a 24 GB card at 1.4x the cost of a LoRA (§8e). Memory capacity is sufficient; learning rate constraints are the limiting factor for this configuration.

## 9. Initial Validation Protocol: Teacher-Render Probe

The initial validation step uses a 10-cell teacher-render probe and one Klein LoRA at minimal scale. This configuration tests whether Klein learns the concept from teacher output and establishes a baseline comparison between Klein and teacher renders.

**Resource Estimate:**
* ~1 hour of Mothership compute
* ~1 hour of Klein training

This probe informs subsequent decisions regarding scale, licensing, and blending strategies.
