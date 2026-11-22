# Medical reviewer tool: candidate models, small local medical LLMs, MedGemma 4B / II-Medical 8B / 35B A3B results (2026-11-06)

**Status: deployed 2026-11-06 19:07 CT (commit `5118979`); the own-window and report-record fix `75c7cb3` went live at 20:22 CT. A draft read
has one live proof, on one turn, from before that fix (below). A second read in an open window has not been run live since the fix, and a lookup
with no draft has never run live.** The selector gives the six LTT runs the same tool list with the tool in, so it stayed off those
jobs. The older MedGemma review still runs only inside Think Really Hard, behind a checkbox (`q.medical_critique`).

**The tool as built (`medical_review.py`):** an in-line helper for whichever model is leading, in the shape the owner and Claude work in:
hand it off, keep working, the note arrives. The lead gives `medical_review` its draft or claim and carries on. II-Medical 8B reads it
with no tools on a free box, and the read reaches the lead as a message on a later turn; the lead checks what it raises with its own
evidence search. A run does not end before the report arrives. With no draft, a lookup job goes to the menu's `tool` row on the 8 GB tier
with read-only tools, under `a1_harness._Errands`' rules, through the factory window, which ends a turn at its size. Plain code looks up
every PMID and PMC ID. A text too long to read whole is refused, not cut. Always offered on a nursing turn, otherwise by relevance.
**It is all back end** (the owner: "that should all be backend, not user side"): the report reaches the model and never the person. The tool
call itself shows in the live transcript like any other tool's.
**Not done:** the 8B is only on BigHonker, and BigHonker's GPU losses on 2026-11-06 are unexplained; no medical family on the menu.
Leads have ignored errands in every earlier test (3 runs, 0 errands), and the proof below told the lead to call it, so whether a lead calls
it unprompted is still open, and so is whether it answers better on real nursing turns.

## First live proof of the medical_review hand-off (2026-11-06, one turn, a synthetic question)
A planted unsafe note (potassium chloride 40 mEq by IV push over one minute), nursing persona, ClaudeSupervisor, lead pinned to
`softserve/qwen3.5:9b`, reader `ii-medical:8b-q4km` on BigHonker. Chats "Claude's proof" and "Claude's proof 2".

**Measured on that one turn:**
- The tool returned in 50 ms and the lead kept working: three evidence searches and a `catch_up`.
- The read took 23 s (cold 8B load included). The report was delivered internally, and the lead's next turn used it.
- The report's text appears in no stream event, not in the result, not in the saved chat (marker `SECOND PERSPECTIVE`, which the rereads confirm is in every report).
- BigHonker's card stayed healthy through this read and three more.

**Inferred from timing, not separately instrumented:** the wait for open errands ran. The lead's turn ended at 30.4 s, the read finished about 36 s, and the next turn started at 36.1 s.

**What it found:**
- **Telling a lead to write first ends the turn.** The first attempt said "write your draft, then hand it off"; the lead wrote it and stopped, with the tool offered. Text with no tool call ends a turn. With the tool calls asked for first, it called the tool. One run each way.
- **The reader's own open window refused its next read.** `scheduler.place` lets only chat kinds join Kirk's own window, so a second read got "no reader free" until the window closed. Fixed in `75c7cb3` (live, not yet proven live), with the same gap in the spill gate's already-resident test.
- **Nothing kept what the helper said.** The person never sees it and a run's ledger goes when it ends. Fixed in `75c7cb3`: each report goes to `State\diag`, which is not RAG-indexed.
- **The final answer gave a wrong potassium rate, "≤5 mEq/min", and "at least 10-fold" where 40 mEq in a minute against 10 mEq/hr is 240-fold.** The lead's draft had said ≤10 mEq/hr. The original report is lost, but three rereads of the same draft all gave ≤10-20 mEq/hr or ≤10 mEq/hr, so the wrong rate most likely came from the lead. Inferred, not proven. ⚠ **Do not copy how those rereads were run:** I piped the script into `docker exec` on the live orchestrator, and at 19:20 that appears to have started a second Kirk, which resumed live runs. Use a throwaway container with the gate's redirects.
- **The lead did not check what the reader raised.** All its searches came before the report, by my prompt's order, and it passed the concerns on labelled as coming from the medical review. Its line that it could not find articles came from its own long queries: PubMed holds zero for all three as written, and `search_evidence` found papers on "IV potassium chloride hypokalemia".
- **The 8B files agreement as concerns:** two of three rereads list "the text correctly says..." style items at high severity, so a lead reading a list of concerns gets noise.
- **The lead narrates the helper on the page:** "Step 1: Calling medical_review", "I'm continuing to wait on the medical review report". The report stays hidden, but those are its own streamed words, and the tool status shows too.
- **Cost:** the pinned 9B took Softserve's card, so McCoy's model was no longer loaded afterwards; his next consult pays a cold load.

## What the owner asked for
A tool the lead model can call while it is working, so the lead stays active and a second model reviews. This is an
evolution of the older review that only looked at the final answer.
- *"we would have the medical review as a tool for the models to use, like the 27b could ask the med model to do a review
  while the 27b is still active"*
- *"it could be offered as a review, but it doesnt mean its athoratative. just another perspective with a caveat"*
- *"speed is still somewhat imporant since it would be in line as the model on mothership is working and chatting"*

## How to grade a candidate
1. Does it catch a planted error?
2. Does it leave the correct twin of that answer alone?
3. How long does the lead wait?

A pair (an error and its clean twin) has to be scored together, because a model can find the error and still give the
wrong verdict on the clean copy (arXiv 2608.16643, abstract only). Three controls are a first look, not a selection test.

## Measured here (order `medreview-20261106-121453`, BigHonker, same 13 items)
Two planted errors, one correct answer, ten real answers from a nursing user's chats. Raw files are on the jobs share,
`tabfoundry-jobs\campaigns\medreview-20261106-121453\`, deliberately outside the library. The findings on the real answers
were not read.

| Reviewer | Planted errors caught | Correct control | Real answers flagged | Mean time per item |
|---|---|---|---|---|
| `medgemma:4b` | 2 of 2 | Flagged it, and misread it | 9 of 10 | 6.8 s |
| `qwen3.6:35b-a3b-ud-q3kxl-mtp`, thinking off | 2 of 2 | Passed it | 8 of 10 | 49.8 s |
| same, thinking on | 2 of 2 | Passed it | 8 of 10 | 191.5 s (52-300 s) |
| `ii-medical:8b-q4km` (Q4_K_M, fully on the 3070 up to a 25,600 window by fit probe; `size_vram == size` in the order) | 2 of 2, with the right protamine dose | Passed it | 8 of 10 | 22.1 s (10-35 s) |

- MedGemma 4B also corrected a heparin error with protamine 50 mg per 100 units, about fifty times the standard dose: the
  right drug with an invented dose. It is unsafe as a source of corrections. If it is ever used, pass on concerns and not
  corrections.
- On the 35B, thinking added nearly four times the wait and changed nothing on the controls. The two runs disagreed on 2 of the 10 real answers: each flagged 8, but not the same 8.
- **⚠ This test sandbagged the reviewers, and its flags on the real answers cannot be used.** Its prompt gave no date and no evidence, and 3 of the 10 answers were cut at 9,000 characters. Both 35B runs marked three answers critical, almost entirely for "fabricated, future-dated citations" (2025, 2026, in November 2026). Checked against NCBI: **all 21 distinct PMIDs and PMC IDs in those answers exist, with the authors and titles the answers gave** (the claims inside each paper were not checked). So those critical findings were the reviewer's own hallucination, stated with confidence, and one even invented a corrected date for a real paper. Lessons for the tool: state today's date; a model with no lookup must not judge whether a citation exists (plain code can check an ID against NCBI); the reviewer's job is the clinical reasoning.
- Whether the 8 of 10 and 9 of 10 flags are justified is unknown. A high flag rate is not by itself a false-positive rate.
- These ran on one round, so treat them as a hypothesis.

## II-Medical 8B is not a tool model (found 2026-11-06, my setup error)
Its vendor's own chat template (295 characters, `tokenizer_config.json`) has no tools and no thinking, and the GGUF matches it. To test it as an errand helper I gave it `qwen3:8b`'s tool template, which made ollama list it as tool-capable. That says the template accepts tools, not that the model was trained to use them. Result: it did look things up on the few items that finished, and on the same item in both tests it ran away with nothing reaching the client. In one run it wrote over 26,000 tokens, past its 25,600-token window, with the engine's context shift discarding its brief. Without the graft, on its own template and with no tools, 42 rows ran with no runaway. The graft is undone. My probe also left off the lab's own guard, `num_predict = num_ctx`, so nothing ended the turn at the window.
**The GPU loss is a separate open question:** BigHonker's 3070 was lost at 13:59 (then an unclean restart) and at 17:14 today, both during 8B work, but the first came before the graft existed, so the graft does not explain it. Two events; cause not found.

## Corrected rerun, 2026-11-06 afternoon (`campaigns\medreview2-20261106-133623`, BigHonker, thinking off)
The reviewer is told today's date and not to judge whether a source exists, and answers go whole. Eight pairs of my own nursing facts, a wrong answer and its correct twin, run twice with different seeds (16 of each), plus ten real answers HAI gave a nursing user, once. No row hit a limit. This still reviews FINISHED answers: it says which reviewer judges clinical content and how long the wait is, not how the in-line tool behaves.

| Reviewer | Wrong answers caught | Correct left alone | Seconds, pairs (worst) | Seconds, real answers (worst) | Real answers flagged | On the card |
|---|---|---|---|---|---|---|
| `ii-medical:8b-q4km` | 15 of 16 | 14 of 16 | 14.9 (58) | 19.2 (41) | 3 of 10 | yes |
| `qwen3.6:35b-a3b-ud-q3kxl-mtp` | 16 of 16 | 15 of 16 | 17.4 (34) | 31.9 (83) | 5 of 10 | no, a declared split |
| `medgemma:4b` | 16 of 16 | 7 of 16 | 3.3 (8) | 7.9 (37) | 5 of 10, 2 unparsed | yes |

- **MedGemma 4B is out as a reviewer:** nine false alarms on correct answers, including calling correct insulin mixing unsafe.
- **II-Medical 8B** missed the cloudy-before-clear insulin error once of two. Read by hand, its three flags on real answers held no clear error: one was a non-clinical turn (the assistant's name), one marked a low-dose steroid definition High with wrong arithmetic (it put 40 mg methylprednisolone at about 100 mg hydrocortisone; the standard conversion gives 200), and one graded citations despite the instruction. So a flag means "go check this", never a correction.
- **The 35B** scored best on the pairs, but 4 of its 10 real reviews still use fake-source wording despite the instruction (a word match; not read) and it takes the whole 8 GB box plus RAM.
- Only the pairs have an answer key: what any reviewer missed on the passed real answers is unknown. Two rounds, questions written by me.

**What the tool needs, from this:** state today's date; plain code, not a model, checks whether a cited ID exists; the main model checks a flag with its own evidence search before acting on it; call it only on clinical content; and price the reviewers on this job as a medical family (the owner's 2026-10-02 rule for specialists), so Spock can place one on a free box. The main model's own box may be the one the reviewer lives on, so placement matters.

## Candidates from published sources (a Sonnet web research run, 2026-11-06; not independently checked by me)
Every score is a multiple-choice exam, mostly the authors' own evaluation, so they say little about reviewing. **No source
found measures a small model as a reviewer, its rate of invented doses, or how often it flags a correct answer.**

| Model | Size and build | Fit on the small boxes | Published MedQA / MedMCQA / PubMedQA | Source |
|---|---|---|---|---|
| II-Medical-8B-1706 | 8B, Qwen3-8B base. Q4_K_M 5.1 GB. Card says Apache-2.0 and "not suitable for medical use" | Q4 on the 8 GB cards with a modest window; the 1060 is tight | 88.61 / 74.44 / 79.8 (authors' own; plain Qwen3-8B on the same table 81.38 / 66.53 / 73.9) | huggingface.co/Intelligent-Internet/II-Medical-8B-1706 and -GGUF |
| MedGemma 1.5 4B | 4B only, created 2026-01-13. Q4_K_M 2.49 GB. Gated, Health AI Developer Foundations terms | Fits all three | 69.1 / 59.8 / 68.2 (Google's card; report arXiv 2604.05081) | huggingface.co/google/medgemma-1.5-4b-it, unsloth GGUF |
| MedGemma 4B | 4B. ollama 3.3 GB | Fits all three | 64.4 MedQA | Google's card |
| Gemma-3-27B-MeditronFO | 27B. No GGUF seen | Softserve or rigby with heavy CPU offload, too slow for in-line use | 77.61 / 63.71 / 75.80; HealthBench 58.0 vs MedGemma 27B 55.9; preferred in 58.6 % of judged pairs | arXiv 2605.16215, epflight.github.io/MeditronFO |
| Apertus-8B-MeditronFO | 8B. No GGUF seen | Would fit at Q4 by arithmetic | 60.7 / 52.0 / 71.4 (project page gives MedQA 58.44) | huggingface.co/EPFLiGHT/Apertus-8B-MeditronFO |

- **MedGemma 1.5:** real, 4B only. The "about 91 % MedQA" figure seen on blogs is wrong for it (card: 69.1), and no 27B 1.5 was seen.
- **MedGemma 27B scores conflict because the setups differ:** Google's card has 87.7 zero-shot and 89.8 best-of-5; MeditronFO's table has 86.10.
- **General models:** on these tables a general 8B beats the small specialist (Qwen3-8B 81.38 vs MedGemma 4B 64.4, different harnesses). At 27B the specialist wins on MedQA (76.2 vs 86.1). No primary medical scores were found for Qwen3.5-9B, Qwen3.6-35B-A3B or Nemotron 3 Nano 4B, so only our own set can answer for those.
- **Could not verify:** release dates for II-Medical and Apertus, the II-Medical GGUF's licence, whether its scores reproduce outside its authors' harness, the full Health AI terms, the per-model numbers in arXiv 2608.16643, whether a MedGemma 1.5 ollama tag exists.

## Next steps
II-Medical 8B is installed on BigHonker and held out of the menu filler (`menu_fill_hold.json`, as MedGemma's specialists rule). The in-line shape has not been tested. Installing a model by hand sets off the menu filler within minutes unless it is held first ([[RUNBOOK-Compute-and-Training-Order-Submission]] § "LOCAL testing of a new model").
