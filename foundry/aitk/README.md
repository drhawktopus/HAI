# The trainers: the lab's changes to ai-toolkit

The AMD box trains with two ai-toolkit trees, each upstream plus the lab's changes:

| Tree (setting) | Upstream | The lab's changes |
|---|---|---|
| FLUX.2 (`FOUNDRY_AI_TOOLKIT`) | the ROCm fork `cupertinomiranda/ai-toolkit-amd-rocm-support` at `9aeb17f` | `ai-toolkit-lab.patch` |
| Krea 2 (`FOUNDRY_AITK_KREA2`) | `ostris/ai-toolkit` at `73ebae1` | `krea2/ai-toolkit-krea2-lab.patch`, installed by `krea2/setup/` |

Apply a patch in a checkout of its upstream commit with `git apply`. Each was made from the tree the factory trains
with and reproduces it (comments aside; the lab's paths read settings, with the lab's own values as defaults).

What the changes do, by marker in the code:
- **Resume and start-up** (`[P20 FIX-..]`, `[P20 CACHE-GATE]`): a resumed or repeated run reuses the cached text
  embeddings and latents instead of loading the 24B text encoder again, and never moves a model it does not need.
- **Control and measurement** (`[P20-INSTR]`): `SAVE_NOW` / `STOP_NOW` flag files in `<FOUNDRY_ROOT>\var\state` (a save
  at the current step; a save and a clean stop, which the factory uses for every pause and yield), and a per-step
  device-versus-wall timing row in `<FOUNDRY_ROOT>\var\state\telemetry\step_instr.csv`. The factory checks for this
  marker before it drives a trainer.
- **Krea 2 only**: byte-identical resume (`[P20-RESUME]`), an exit that does not wait on the GPU runtime's teardown
  (`[P20-EXIT]`), and a deferred text encoder with a content-addressed cache for the static prompts (`[P20-TECACHE]`).

## The scripts beside the trees (`scripts/`, in `FOUNDRY_AI_TOOLKIT` on the box)

| Script | What it is |
|---|---|
| `p20_rescue_burst.py` | The stall rescue's payload: a burst of large GEMMs from a fresh process that flips a stalled trainer back to full speed (`p20_stall_rescue.py` runs it; `FOUNDRY_BURST` can name another). |
| `p20_apply_instrumentation.py`, `p20_apply_mistral_bufferfix.py` | The appliers that wrote `[P20-INSTR]` and `[P20-BUF]`; the patch already carries both. |
| `p20_burst.py`, `p20_rescue_poke.py`, `p20_keepwarm.py` | Earlier rescue experiments (a short burst, an alloc-and-free poke, a continuous light load). |
| `p20_kernel_bench.py`, `p20_kernel_probe.py` | Low-bit GEMM benchmarks on the card (run on an idle card). |
| `p20_diag_mistral_cache.py` | Checks the Mistral uint4 cache loads. |

## The Krea 2 setup (`krea2/setup/`, in `<P20_KREA2_TRAIN_DIR>\setup` on the box)

`run_krea2_setup.cmd [python.exe]` runs `krea2_trainer_setup.py` once: it clones the pinned upstream commit, copies the
lab's ROCm interpreter, installs upstream's requirements constrained to the ROCm torch stack (`constraints.txt`,
`requirements_krea2.txt`), proves the card and the model import, and stages the text encoder and VAE. Then the four
appliers (or the patch) add the lab's changes. `test_hard_exit.py` tests the exit applier against `run_orig.py`
(upstream's `run.py` as the Windows checkout has it, CRLF).

Settings: `FOUNDRY_ROOT`, `FOUNDRY_AI_TOOLKIT`, `FOUNDRY_AITK_KREA2`, `P20_TRAIN_DIR` (the FLUX.2 run's models and
caches), `P20_KREA2_TRAIN_DIR`.
