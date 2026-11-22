# Measure your own box

HAI's model choices and context windows come from measurements taken on the hardware they run on. The lab's research
gives you the starting point for any model on any card ([tuning-guide.md](tuning-guide.md)). For a card like one of
the lab's, HAI can start from that card's measured rows (below). Your own measurements then confirm the start and
replace it: a model that fits on one card at a given context may not fit on yours, so the final word is your card's.

## Start from the lab's measurements

The lab's measured menu ships in `menu/reference/`. A device that names the lab machine its card resembles places
from that machine's rows until its own exist. It gets that machine's windows, memory profiles and offload entries
too. Each borrowed row is marked with the machine it came from, and your own rows win model by model as your factory
measures them. In the lab's menu, `mothership` is the 24 GB AMD card, and `bighonker` and `softserve` are the 8 GB
NVIDIA cards (their hardware is in [tuning-guide.md](tuning-guide.md), section 10).

1. In `examples/one-machine/settings.env` (your copy of `settings.env.example`, from the quick-start), uncomment the
   two lines under "Start from the lab's measurements". They
   point the head at the menu (the compose file mounts `menu/reference/` at `/menu`) and name the closest machine:

   ```
   HAI_POLICY_TABLE=/menu/policy.json
   PC1_REFERENCE_BOX=bighonker
   ```

   With a roster file, the same thing is the device's `reference_box` field.
2. Pull the models that machine was measured with, under the same names. The menu writes each as its Ollama name
   (`menu/reference/model-matrix.md`). A row for a model your machine does not have is set aside.
3. Give the head its factory ([quick-start.md](quick-start.md), "Give the head its factory"). The factory reports
   which models the machine holds, and the head places only on what a machine has reported.

The lead seat is not borrowed: on your head it goes to your biggest card.

## How large a context fits on your card

`foundry/fit_probe.py` finds the largest context each model in your Ollama store holds fully on the card:

```
python foundry/fit_probe.py fit.json --host http://localhost:11434 --models qwen3:8b
```

It starts at a known context (or `--start N`), doubles until the model spills off the card into shared memory, then
bisects back to the largest context that stays fully resident. It stops at the first spill instead of using a cap, and
it clears the card of other loaded models before each probe so the reading matches a real run.

## Why "fully on the card" is the line

When a model does not fit, Ollama does not fail. It quietly runs part of the model on the CPU, every request still
succeeds, and everything is many times slower. The only reliable check is Ollama's own report: in `/api/ps`, a model's
`size_vram` must equal its `size`. Check that before trusting any speed number.

## Two habits that keep the numbers honest

- **Change one thing at a time.** If two runs differ in more than the thing you are testing (the card was busy for one
  of them, the prompt changed, a different model answered), there is no result, only a story.
- **Count the limits you hit.** If a share of the answers stopped at a token cap or a timeout, the score is measuring
  the limit, not the model. Re-run with room before believing it.
