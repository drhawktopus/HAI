# Daily research digest — order spec (DRAFT for the owner, 2026-10-21, nothing queued or built)

**Status: DRAFT. Not built, not queued, not approved.** Written at the owner's request after the Kai intake notes ([[Research-Kai-OpenCode-Console-Breach-Video-2026-10-20]], [[Research-Kai-Local-AI-GPU-Tiers-8-to-24GB-Video-2026-10-21]]). the owner's ask, 2026-10-21: a local setup that does the research and gives a **daily digest**, including an **"Implications for your lab"** section; sources include Kai, Luke's Dev Lab, Local Explained, Nate B. Jones "and other prominent creators/researchers". He is happy to keep watching the real videos; the digest supplements them. **A side quest, no project number** (the owner, 2026-10-21: *"I don't think this is worth a project number… more of a side quest"*); it is tracked in PROJECT-BACKLOG and the 2026-10-21 research journal only.

## What the lab already has that this reuses (checked 2026-10-21)

| Need | Existing mechanism | Verified how |
|---|---|---|
| Model work as a visible, stoppable, resumable unit | **`infer_ollama.py` as an order** (`order.compute` + `model.<name>`; rows appended to `<out>.partial.jsonl`, resumes after a yield; `eval_campaign` submits it) — [[RUNBOOK-Compute-and-Training-Order-Submission]] worked answer, walked 2026-09-27 | Read the runbook; **not yet run for this payload** |
| A recurring background timer that places model calls | The orchestrator's **claim sweep** (4-hourly; reviews placed through Spock; `yields_to_live`; model follows the box via `review_model_for`; calls tracked by `ollama_client.track`) | Named in the doctrine index and [[HAI-INVARIANTS]]; **code not read this pass** |
| Caption pulls from YouTube | **Pinchflat container on Sundae** (`yt-dlp` + `ffmpeg`); Softserve's own IP gets HTTP 429 on timedtext (Journal 2026-10-14). Used again 2026-10-21 for video and audio | Ran it today |
| The intake note format | Tags **[D] [V] [L] [?]** + a "Compared with the lab" table | Six notes filed in `LLM-Project\Research-*` |
| Phone delivery | Discord DM mirror (`NOTIFY:` lines); one notification per verdict | Doctrine index |
| Library retrieval for "implications" | The orchestrator's RAG over `Reference` / `LLM-Project` / `Runbook` | HAI-INVARIANTS |

**Not reusable:** the HAI self-engineering overnight lane ([[06-Overnight-Autonomous-Runs]]) is a code-change lane with a sandbox, not a research lane; "Absurd" is retired (register, 10-05).

## Shape

One scheduled job on the orchestrator host (Sundae), **never a chat session**, once a day in the overnight trough. It COLLECTS and ASSEMBLES (CPU, light) and **submits its two model steps as orders** so the deck shows them and a stop slip works.

1. **Collect** (orchestrator, CPU). Per-source config: feed or API URL, rate limit, short timeout, few retries, cache every record as it lands. Dedupe by canonical URL against a seen-store. Output: `items.jsonl`.
2. **Triage order** (`infer_ollama`, small model on the box the desk picks). Each item scored against the lab's *current* interests; the rest dropped. Structured outputs (JSON schema in `format`), read `content or thinking`.
3. **Read order** (`infer_ollama`, the 27B/3.8 champion on Mothership, thinking on, full context for the model). Top items only; fetch the page or captions; write the note in the intake format: claim · tag · lab comparison.
4. **Implications** (orchestrator): for each item, retrieve lab docs by RAG; every "implication" line **must cite the doc or register row it touches**, or the line says **"no lab data"**. No citation, no implication.
5. **Assemble** the day's note, **publish**, then **notify** (one Discord line: the top item + link).

## Output

`Z:\Documentation\Digest\YYYY-MM-DD.md`, in this order: (1) **Contradicts or threatens a lab result** — empty most days; (2) **Implications for your lab** (2–5 items, each with its citation); (3) **Worth knowing** (one line each, tagged); (4) **Watch list** (new videos from the channels, one-paragraph claim summary, link).

**Not indexed by RAG at first** (`[[gotcha-rag-doc-becomes-policy]]`): unchecked external claims in an indexed folder get served back as current. Items the owner wants kept are promoted by hand to a full `Research-*` intake note.

## Starter sources — RESOLVED AND TESTED 2026-10-21 (each fetched live; HTTP 200, parsed, newest entry read)

| Type | Source | Feed | Result |
|---|---|---|---|
| Video | Kai | `youtube.com/feeds/videos.xml?channel_id=UCgUfJoPtkt7-RiiuYh4vmGA` | 15 entries; newest = today's GPU-tiers video |
| Video | Luke's Dev Lab | `…channel_id=UC6YL31AicZzw1-gap7zBm9A` | 15 entries (channel ID from the 10-14 intake's info JSON) |
| Video | Local Explained | `…channel_id=UCfhnA8UNM2TGaTAoaaemMqA` | 15 entries (ID from the 10-13 intake's info JSON) |
| Video | Nate B. Jones ("AI News & Strategy Daily") | `…channel_id=UC0C-17n9iuUQPylguM1d-lQ` | resolved from the live `youtube.com/@natebjones` page; feed title matches the channel name |
| Blog | Nate's Substack | `natesnewsletter.substack.com/feed` | 20 entries — **same person is likely but not confirmed**; cross-link not read |
| Blog | Simon Willison | `simonwillison.net/atom/everything/` | 30 entries |
| Blog | Sebastian Raschka ("Ahead of AI") | `magazine.sebastianraschka.com/feed` | 20 entries |
| Blog | Nathan Lambert ("Interconnects AI") | `interconnects.ai/feed` | 20 entries |
| Papers | HF daily papers | `huggingface.co/api/daily_papers` | JSON, 50 items |
| Papers | arXiv cs.CL / cs.LG / cs.DC | `rss.arxiv.org/rss/cs.<x>` | **Fetched fine from Sundae and with curl on Softserve; Softserve's Python failed certificate verification.** Run it on Sundae; do not disable verification |
| News | Hacker News (≥100 points) | `hnrss.org/frontpage?points=100` | 19 entries, 3.2 s. **A third-party mirror**; the official Firebase API is the fallback |
| Releases | llama.cpp, ollama, Strata, unsloth | `github.com/<owner>/<repo>/releases.atom` | 10 entries each; newest llama.cpp `b11796`, ollama `v0.40.7`, Strata `0.2.2` |
| Releases | ROCm | `github.com/ROCm/TheRock/releases.atom` | newest "ROCm Core SDK 10.1.0 Release". `ROCm/ROCm` now 301-redirects to `ROCm/legacy-rocm-build`; `ROCm/rocm-systems` and `rocm-libraries` also publish tags (`hip-version_10.2.62940`, `therock-10.1`) |
| Models | ISTA-DASLab | same API, `author=ISTA-DASLab` | JSON; newest `Qwen3-8B-MatGPTQ` (10-21), `Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF` (10-19) |
| Blog | Andrej Karpathy | `karpathy.github.io/feed.xml` | HTTP 200; its newest entries read as old posts (dates not checked), so low yield |

**Unresolved:** none of the originally named sources. The Karpathy feed works but is probably quiet.

**Source quality** is judged by lab relevance and checkability, not popularity. Kai uploads about daily (13–27 min), sponsored segments disclosed.

Source quality is judged by lab relevance and checkability, **not** popularity (Kai's recent sampling: ~1 video a day, 13–27 min, sponsored segments disclosed).

## Factory-contract checklist (the new-order-kind rows)

| contract | answer |
|---|---|
| desk dealing | triage + read are `only`-pinned (no parallel deal, no size reserve needed, as `helper_window` does). **Open:** if ever dealt, register the basename in `SIZE_RESERVES` with a `SizeAwareDealing` pin |
| stop → ships what exists | each scored/read item is appended as it lands; a stop leaves the partials and the assembler publishes what exists, **labelled partial** |
| progress | `[digest] N/M …` lines + `progress_glob` |
| retirement / cleanup | items, caches and partials live under one dated run dir that ships with the order; the digest note is the product, everything else a declared byproduct |
| capability | `requires ["order.compute", "model.<name>"]`; refused by name when no factory holds the model |
| yielding | resumes from `partial.jsonl`; yields to Plex on Softserve and to a person on a card; **never displaces live work** (declare `yields_to_live`) |

## Failure fates (never let a transient condition consume work)

- **WAIT:** box busy, source rate-limited (429), caption pull blocked → retry on the next run; the item stays in the seen-store as *pending*, not *seen*.
- **ARCHIVE:** an item that cannot be parsed → recorded as `unreadable` in the note, with its URL.
- **HOLD FOR owner:** a source that returns zero for several days in a row, or a model that returns nothing → the digest says so loudly.
- **"Could not tell" is never "nothing new".** The note always states **N sources fetched, M failed (names)**; zero items is only ever printed with that line.

## Acceptance (before it is called done — a green suite is not enough)

1. **Positive control:** seed one known item (e.g. the Kai video filed today) and prove it is collected, scored in, and read; assert the matcher and each source resolver count > 0.
2. **Live decoy** on the full path: run once, read the published note back, confirm the Discord line arrived, confirm the deck showed both orders.
3. **Same-seed rerun** to confirm dedupe (second run lists nothing already seen).
4. **Deployed ≠ running:** `verify_deploy.py` on every file changed; confirm the running process postdates it.
5. **Documented:** runbook section whose title contains the words a stuck session would type; a deck tile.

## Limits (no arbitrary limits)

No clock-based timeouts on model calls: end on demonstrated idleness. Context sizes are the model's tested maximum, not chat-sized. Before believing any day's output, count rows that hit a limit (`done_reason: length`, no closing think tag) — a hit means that item is void and is re-run with room.

## Open questions for the owner

1. **Final source list** after the resolved feeds are shown.
2. **Time of day** — overnight trough is the default; does the digest need to be ready by a set hour?
3. **Which model reads** — the 27B champion on Mothership by default; say if you want the 3.8 pinned.
4. **YouTube captions via Pinchflat on Sundae** — confirmed as the pull route (Softserve's IP is blocked); OK to rely on it?

## Verified mechanism (code read 2026-10-21) — this replaces the guess in "Shape" steps 2–3

- **Submitting a model step = one POST** to Kirk, the shape `submit_speedcheck.py` uses: `POST http://<sundae-address>:8099/p20/order` with `{"spec": {"name", "mode": "compute", "load": "gpu", "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py", "args": [<items.json on the UNC share>, "{ORDER_OUT}\\out.json", "--model", M, "--num-ctx", N, …], "progress_glob": "{ORDER_OUT}\\out.progress\\*.done", "progress_total": n, "deliver_to": <share>, "notify": "failures", "priority": …}, "requires": ["order.compute", "model.<M>"], "only": [<box>], "user": …}`. Items file: `{"items": [{"id", "prompt", "system"?}]}`. Output: `out.json` rows with `answer`, `thinking`, `done_reason`, `eval_count`.
- **Context comes from the lab's own tested table** (`menu_fill.TESTED_CTX`), never a number I pick: Mothership `qwen3.8:27b` **125,952**; BigHonker `qwen3.5:9b` **19,968**; Softserve `qwen3.5:9b` 47,104 (Plex host, so it yields). Thinking: `--think --think-level` is a real flag; the champion is priced at xhigh, and "thinking off" is never a default (the owner 10-19).
- **Limits that void rows:** `done_reason: "length"` is void by the lab's own rule; `--price-ceiling-s` exists and the digest should **not** set one (an answer cut at a price line is a cost measurement, not a digest item).
- **The sweep's pattern, read:** `claim_sweep.sweep()` asks `scheduler.place("review")` and never picks a host, returns `None` with a reason when a live HAI job holds the card, and picks the model with `review_model_for(url)` (the model follows the box). Its first run builds a baseline and reviews nothing. It reports status on every run, even clean ones, so a broken sweep reads differently from a healthy library. It notifies through the `[DESK]` relay (`notify_desk`: prints the line, DMs only if the token is set, never silent on refusal). **The digest copies those habits:** baseline-first run, status every run, never pick a host itself, loud relay.
- **Who submits and where it runs:** a small driver script on Sundae that uses the order desk the way every other campaign does — not new scheduler code in the orchestrator. That avoids an orchestrator deploy for v1. (A new `place()` kind would need the full orchestrator deploy procedure; defer unless the desk route proves clumsy.)

## Verified 2026-10-21 (work done, not assumed)

- **Nate's Substack = Nate's YouTube channel:** his YouTube "about" page links to `natesnewsletter.substack.com` and the Substack's about page links back to `youtube.com/@NateBJones`.
- **`Digest\` is NOT RAG-indexed:** `config.py` `RAG_NAMESPACES["homelab"]` is exactly `Reference`, `LLM-Project` and `Runbook`, read with non-recursive `glob("*.md")`; `Journal` is fetch-only. A top-level `Documentation\Digest\` is outside every namespace. (The Research-* intake notes in `LLM-Project` **are** indexed, which is the reason promoting an item there is a deliberate act.)
- **Captions pull unattended for all four channels** through the Pinchflat container: Kai 140 KB, Luke's Dev Lab 141 KB, Local Explained 249 KB, Nate 338 KB of VTT. **Caveat:** Nate's pull wrote a file but the title field I read was channel-level, so which video it was is not confirmed; the larger size is unexplained. Softserve's own IP still hits HTTP 429 on captions (seen again for a fifth video), so Sundae is the pull point.
- **A real read order ran end to end** (`digest-test-mo-qwen38-27b-ctx125952.20261021-084755`, one item: today's GPU-tiers transcript, 3,982 prompt tokens, `qwen3.8:27b`, 125,952 context, `--think-level xhigh`, Mothership, submitted through `POST /p20/order`):
  - Accepted and routed by the desk ("mothership (score 64.0) free"), claimed, visible on the deck (`state: compute`, 63 tok/s write during the run, the box accounting "foundry order … is on this card").
  - **Fully resident** (`size_vram == size`, `offload: false`), cold load 43 s, `done_reason: "stop"` (not `length`), 0 errors, 316.5 s wall, 21,253 generated tokens (about 65,000 characters of thinking, 9,854 of answer) at 68.5 tok/s, 120 kJ on the card.
  - So **one ~15-minute video costs roughly five minutes of Mothership**; ~12 videos a night is about an hour. **Not tested:** a prompt near the context ceiling (this one used 3% of it).
- **Read quality, compared with my own note on the same video:** the model produced **79 claims** against my 14, all grounded in the transcript, none invented that I found. It named the right first checks for the lab (the A3B on 8 GB with 32 GB RAM, the Q4 spill on 16 GB, ISTA vs other 2-/3-bit files, the 27B on AMD rather than a 3090, Bolt's fit). Weaknesses to fix in the prompt: it is **too granular** (the digest wants merged claims), its "how supported" labels are **noisy** (a mechanism marked "opinion", sponsor-product claims marked "vendor table" and the sponsorship **not flagged**), and it repeats ASR garbles ("Kim K3", "Neotron", "ISA"). It has no lab data, as designed; "implications" must come from the RAG step.

## Prompt v2 trial (2026-10-21, order `digest-test-v2-mo-qwen38-27b-ctx125952.20261021-090443`)

Three videos in one order (the breach video, the Qwen Image video, Kai's Opus-5 cost video), each given its **description** (authoritative spelling and links) beside the transcript; same model, context and xhigh thinking as the first trial.

- **Ran clean:** 3/3 rows `done_reason: stop`, 0 errors, fully resident, no cold load (the model was already loaded), 62.6–67.4 tok/s, 114–214 s per item (160 s mean), 180 kJ on the card in total. Prompts were 2,796–6,111 tokens.
- **Fixed what v1 got wrong:** 12 merged claims per video instead of 79; the required format came out every time; **name repairs came from the description and checked out** (the breach description spells "Johansen" and "Hallie", the model repaired "Matt Johansson" and "Lydia Halley" to match; "Kwench" → Qwen, "Vulcan" → Vulkan, "Kimmy" → Kimi); `CHECK WITH` links were lifted from the description's own links; the Opus-cost video's description holds **no links and no sponsor wording** and the model correctly said "none named" and "none disclosed".
- **Faults still to fix in the prompt:** one row says SUPPORT `cited-source` while its CHECK WITH is "none named" (the breach video's claim on provider spend limits) — a cited source needs a link, so the rule must read "`cited-source` only when a link is named". `SPONSOR` was "none disclosed" on all three, which is right for these three, but the **sponsor path has not been exercised since the GPU-tiers video** (the one with a sponsor) was not in this batch.
- **What it cannot do, by design:** it never marks a claim verified, and it has no lab data, so "implications" come from the RAG step, not from this read.
- **Factory behaviour worth knowing:** the order's results were **shipped into a timestamped copy of its folder** (`…_mothership.20261021-091902`), and the bare-named folder kept only the partial rows. The same one-name-one-artifact rule as the rest of the lab: **the driver must pick the newest generation explicitly**, never the bare name. My own watcher looked at the bare path, printed "INCONCLUSIVE", and was wrong; the order had in fact finished at 09:18 with all three rows. The deck was unreadable for a short spell because the orchestrator container restarted about ten minutes before I checked (not by me; a deploy or restart by another session is the likely cause, unconfirmed).

## Things still not verified (read or test before building)

- A read at or near the context ceiling (a long paper or a multi-article batch).
- The sponsor path and the cited-source consistency rule, on one more run (include the GPU-tiers video, which has a sponsor).
- Which video Nate's caption pull actually fetched (see above).
- The RAG "implications" step has not been built or tried; its answers must be checked against the cited docs before anything goes to Discord.
