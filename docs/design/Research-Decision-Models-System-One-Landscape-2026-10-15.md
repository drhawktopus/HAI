# Research note — Decision models ("System One"): what they are, how good the evidence is, and where they fit the lab

**EXTERNAL RESEARCH NOTE — desk research, nothing measured here.** Built 2026-10-15 from the Alberta Tech video ([[Research-Alberta-Tech-Jev-Decision-Model-Video-2026-10-15]]), vendor pages, model cards and press reports, then read against the lab's own design docs. No order was run and none is queued. the owner's prompt: the video describes models that make typed decisions — what the lab called "hardcoded with LLM assist" for HAI and Star Fleet Command — and he wants them researched further because they look interesting and fast.

**Evidence tags:** **[A]** vendor or model-card primary · **[B]** independent report or press · **[C]** secondary or search-snippet only, not opened · **[L]** the lab's own docs · **[I]** my inference.

## 1. What a decision model is

An interface, plus models trained for it: **send a state (text or structured data) and one or more typed questions; get back typed answers with a probability or distribution per option**, to be read by code, not by people. Question types: **choice** (pick one of 2–26 options), **yes/no** (TypeSafe calls it *Noul*), **score** (a level on a rubric). Calling code acts above a confidence threshold and escalates below it. [A: TypeSafe via InfoQ; Pydantic AI docs]

The category is new (TypeSafe's launch was 2026-09-15 [B]) and several implementations already exist. They are not all the same kind of thing:

| Model | Maker | What it is | Evidence |
|---|---|---|---|
| **Jev** | TypeSafe AI (closed API) | First "System One model". Architecture undisclosed; trained with "Reinforcement Learning for Calibrated Decisions". All questions answered in **one parallel pass**. 32 K context, **$0.042/M input, output free**, 70–500 ms end to end (vendor) | [A]/[B] |
| **tev1** (4B; a 0.8B is also named) | Together AI (open, license "being finalized") | **SFT of Qwen3.5-4B** to choose one of 2–24 labeled options; outputs **a single letter** the code maps to a key. The card calls it a "Jev-inspired experiment", **standard next-token head**, experimental. Settings: temperature 0, max_tokens 8, thinking off. Dev eval: **880/1,000 (88.0 %)** main set, 300/300 policy-transfer, 1,300/1,300 valid outputs, "not an independent benchmark" and no untuned-Qwen baseline | [A] card |
| **Nimble 9B** | Bespoke Labs (Apache 2.0) | Qwen3.5-9B + LoRA; reads the **logits of the permitted candidate tokens** rather than generating text, so the probabilities are the model's own token odds. Reported in an independent local routing test at **77.1 %** vs the base model's **76.3 %** | [C] — the evaluation blog returned HTTP 403 and was not opened |
| **Clef / Clef-flash** (9B) | Cloudflare (Apache 2.0, Oct 1) | "Fully Jev-API compatible"; Clef-flash on Workers AI, median **38.8 ms vs Jev's 524 ms**, per **Cloudflare's own run** of the Jev Decision Index; The Register noted Cloudflare scored its own models | [C] |
| Laya, CLM | named in the Pydantic AI docs | other servers speaking the same API | [A] name only |

**Run locally:** Ollama **0.35.0 or later** serves them at `POST /v1/systemone` (client base URL **without** `/v1`, e.g. `http://localhost:11434`); models `nimble`, `tev1`. Limits per the Pydantic AI docs: **26 options per choice, 26 score levels, 64 questions per request, 64 KiB request**; an over-limit request errors rather than truncating. Whether the endpoint's yes/no probability format matches Jev's is **[?]** — I did not find the wire schema; Ollama's own OpenAPI file did not list the route. The lab already records that Mothership (0.40.2) and the small boxes can serve these today (see §3).

## 2. How good is the evidence

A ladder, from marketing to measurement. The gap between rungs is the finding.

| Claim | Source | Rung |
|---|---|---|
| "193× faster, 444× cheaper, cannot hallucinate" | vendor headline, relayed by the video | headline. **193.6×** is the figure InfoQ calls the headline without naming a claimant; **444×** I could not find |
| Real-world speedups | OpenChamber analysis of **12,759 launch tweets** (via InfoQ [B]) | **median 7×** faster, **median 30×** cheaper, median latency **76 ms** (upper quartile 270 ms) |
| Vercel safety classifier | Vercel engineer, via InfoQ [B] | **5–18×** faster than the LLM it replaced, more accurate (the video says "CEO" and quotes the top of the range) |
| Accuracy vs a strong LLM | Bryo AI CTO via InfoQ [B] | Gemini "slightly more accurate" on email classification but **10–20× more expensive**; he valued Jev as **the only one returning a real probability** |
| "Cannot hallucinate" | HN commenter, Armin Ronacher via InfoQ [B] | the guarantee is on the **shape** of the output, not its correctness; the design "delegates the hallucination problem a little bit to the user" |
| Accuracy of the open models | tev1 card [A]; Nimble vs base [C] | tev1 88.0 % on its own dev set; Nimble roughly equal to its own base model on one routing test |
| What the vendor says it is bad at | TypeSafe's "jaggedness" page via InfoQ [B] | **counting, arithmetic, date comparison, large noisy state** — "keep maths in code" |

**Reading the ladder [I]:** the speed and cost wins are real but an order of magnitude, not two. On accuracy the best claim is "about as good as a general LLM on a classification task, much cheaper". The distinctive value is **not intelligence**; it is (a) a **closed output set** the code can switch on, (b) a **probability per option** (so "unsure" exists), and (c) **very few output tokens**, so cost and latency are dominated by reading the prompt. The Nimble result, if it holds, says the same thing: a decision-model fine-tune adds interface, not accuracy over its base.

Calibration is the open question the sources keep raising: the Pydantic AI docs say outright that **"each model's confidence is its own, and a threshold tuned on one model does not carry over to another"** and advise measuring accuracy, the hand-off rate and thresholds on your own labelled examples. The tev1 card says calibration, prompt-injection robustness and out-of-distribution behaviour are not comprehensively evaluated.

## 3. How it lines up with what the lab already decided [L]

**the owner's framing, in his words** ([[HAI-Agent-Surface-Design]]): *"we need smart routing decisions and smart logic"*; *"the AI assisted Starfleet command route it to a tablet or Rigby (by sending it to Tolkien and Tolkien intelligently routes it)"*; the advisor *"advise[s] Spock on placement as well as … triage"*. The lab's structural answer is **McCoy** (Phase 8): one small advisor model on Softserve, **proposes → deterministic validation → execute or reject**; the model's authority is never final.

| Where the docs already say it | What it says |
|---|---|
| [[HAI-Agent-Surface-Design]] — McCoy table | Kirk triage (hard? family? chat vs job? sure?), Spock placement, Tolkien tablet class / tries / escalation, result comparison — each with a deterministic check |
| [[HAI-LLM-Execution-Context-Mechanics]] | "The orchestrator NARROWS; the model PICKS from the narrowed set. **Not a hardcoded keyword router (a regex knows match/no-match, never UNSURE).**" |
| [[34-A1-Agentic-Build-Family-Spec]] | the finish gate: "the model proposes, code decides" |
| [[34-Phase-7-Placement-Policy-Design]] | for the cascade's `reason_*` / `ops_*` tasks "a decision model (`/v1/systemone` yes/no with probabilities, McCoy's candidate) judging a worker's answer is the natural fit; measured before adopted" |
| [[34-DECISIONS-AND-OPEN-ITEMS-REGISTER]] (the owner's calls, 2026-10-10) | **decision models for the teeny tier — candidate, not adopted**; Ollama ≥ 0.35 serves `/v1/systemone`; tev1 0.8B ≈ 0.8 GB / 4B ≈ 4.5 GB, Clef Flash 9B, Nimble 9B; fits McCoy's triage and the placement family/urgency call; evaluate as an order against the lab's classify gold set before any routing change |
| `Journal\2026-10-09-claude.md` | McCoy's triage family (`advisor_tasks.py`, **48 gold rows**): v1 best **qwen3:8b 31/48 (65 %) at 0.5 s on Softserve**; a `/v1/systemone` arm (tev1 0.8B / 4B) planned as one more order through `submit_advisor.py` once the payload has a systemone path |

**The connecting idea [I]:** the lab's doctrine already puts the *decision* in code and the *judgment* in a small model. What was missing is a model interface that returns **"how sure"** in a form code can threshold. A decision model is that interface. The "UNSURE" that a keyword router cannot express is just the probability vector's flat shape or a low top probability. That is the strongest reason this category matters to the lab, more than speed.

**Decision-shaped places seen in the docs (all hypotheses until measured):** Kirk's triage, Spock's placement advice, Tolkien's tablet-class call, escalate-or-not, the cascade's non-code selectors, and HAI's choice among a narrowed tool set. Places I have **not** read that look similar: the library-triage verdicts, the claim-review mechanism, and the GPU patrol's "is this an AI job" call.

## 4. Questions the research raises (design questions, not decisions)

1. **Interface vs weights.** If a decision model's edge is the interface, then a lab model with logprobs read over the option tokens might capture most of it. A one-variable comparison on the 48 gold rows — the same weights wrapped two ways, then the dedicated models — would separate the two. Not designed or queued.
2. **Calibration is the product.** For code to "act above a threshold, escalate below it", the probabilities must mean something. The lab's rule (class 7): baselines and thresholds come from values observed on its own gold rows, never from a vendor card. Any threshold from one model does not carry to another.
3. **Residency.** The lab runs one model per card and Softserve (8 GB 3070 Ti, Plex host) is McCoy's home. A 4B decision model (~4.5 GB [L]) competes for that card; the 0.8B (~0.8 GB [L]) might coexist; measured residency costs are 4–22 s [L]. Whether a decision model belongs on the card, on the CPU, or on a different box is open.
4. **Where not to use it.** TypeSafe's own limits (counting, arithmetic, dates, large noisy state) match the lab's deterministic-first instinct: those checks stay in code. This is the same line as McCoy's "deterministic validation".
5. **Safety shape.** A closed output set bounds what a prompt injection can *do* (the video's "cannot be told to ignore previous instructions"), not whether the chosen option is *right*. The tev1 card says to treat text in `state` as data. For the lab's routing, the blast radius of a wrong-but-valid option is the thing to price (a mis-routed request, not an arbitrary action).
6. **Licenses and provenance.** tev1's weights license was "being finalized" at card time; Nimble and Clef are Apache 2.0 per the sources; Jev is a closed hosted API — **out of scope: the owner, 2026-10-16, "I want local only"** (local candidates: tev1, Nimble 9B, and Clef-flash if its weights run locally, plus a lab-trained model). Cloudflare's post (as relayed by one article, [C]) says Clef and Clef-flash are post-trained from Qwen bases; the same article flags the "Qwen3.8-27B" label as possibly a typo.
7. **Reading cost is the cost.** With ≤ 8 output tokens, a decision call's time is its prompt processing. On the lab's compute-poor tiers (rigby has no AVX2; tablets) a long state is exactly the expensive part — the same reading-cost point as the earlier video notes.

## 5. What is not known

- Whether the open models match a good small LLM on the lab's own routing rows — **no data**.
- Whether `/v1/systemone` yes/no output matches Jev's format; the wire schema was not found.
- Whether the route actually answers on the lab's boxes: the Ollama host runbook has Mothership on 0.40.2 (and the register has BigHonker and Softserve on it), above the 0.35 minimum, but no one has called `/v1/systemone` there.
- Independent speed numbers for 0.8B/4B/9B on the lab's cards.
- The Nimble routing test and the Cloudflare numbers — both secondary.
