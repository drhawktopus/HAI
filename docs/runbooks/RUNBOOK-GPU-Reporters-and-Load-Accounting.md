# RUNBOOK — GPU reporters on the fleet boxes: "HAI GPU Health Reporter" (alive?) and "HAI GPU Load Reporter" (busy, and ACCOUNTED FOR?) — UNACCOUNTED GPU LOAD, no blind spot

**Date:** 2026-09-27. **Project:** #34 area C (adaptive orchestration) — the visibility class fix.
**Why this page exists:** the health reporter had run on BigHonker since 2026-07-26 and was documented
NOWHERE (a guard nobody has heard of cannot be missed); the load reporter was built 2026-09-27 after Kirk
fulfilled a 66-step eval campaign on BigHonker with every tile reading idle. the owner: *"if something is running,
I need to have visibility into it… no blind spot, no nothing… solved once and for all."*

## The two reporters (both: scheduled task, SYSTEM, every 1 minute, `C:\ProgramData\HAI\`)

| task | script | pushes to | answers | boxes |
|---|---|---|---|---|
| **HAI GPU Health Reporter** | `gpu_reporter.py` (source `E:\SSDShared\Lab\bin\`, tests `test_gpu_reporter.py`) | `POST /fleet/gpu-state` | is the card ALIVE. NVIDIA: nvidia-smi "GPU is lost" phrases only. **AMD (no nvidia-smi): Windows' device record for the `VEN_1002` display adapter** — present + Error/problem code, or only phantom records (off the bus) = LOST; present + OK = healthy; anything else = no verdict. Every verdict carries the **driver version** (#38). "Could not ask" is never "dead". | **Mothership (since 2026-11-02), BigHonker (since 07-26), Softserve** |

**Mothership had NO health reporter until 2026-11-02.** `/gpu/health` reads only pushed verdicts plus a
resident-model-on-CPU check, so with nothing pushed and nothing loaded it returned "ok". On 2026-10-31 it read
`mothership: ok` while the 7900 XTX was first `Error / CM_PROB_FAILED_POST_START` (43) after an AMD driver install,
then off the bus entirely (only `CM_PROB_PHANTOM` records) after two warm reboots. The AMD path is weaker than
nvidia-smi: it sees a failed or missing device, not a hung one (Windows said OK about BigHonker's lost 3070 in July).
The fixtures in `test_gpu_reporter.py` are Mothership's real output from those days.

**Proven 2026-11-02 13:45–13:47 (live decoy):**
- All three boxes installed at sha256 `5b5fe6d0…`, and all three reported healthy. Mothership's task is new
  (`/sc minute /mo 1 /ru SYSTEM`, its python `C:\Users\<user>\AppData\Local\Python\bin\python.exe`), and its stored
  report was re-stamped by the task itself a minute after the manual run.
- A pushed decoy `healthy:false` for mothership made `GET /gpu/health` read **`GPU LOST per Mothership`** within
  0.1 s.
- The real reporter's next minute put it back to `all cards healthy` 25.8 s later, with no alert fired. Script:
  `gpu_decoy.py` in that session's scratchpad.
| **HAI GPU Load Reporter** | `gpu_load_reporter.py` (source `E:\SSDShared\Lab\bin\`) | `POST /fleet/gpu-load` | what the card is DOING: util %, power W, VRAM MB, ollama loaded models, the GPU counter snapshot, and (since 2026-10-25) the box's free/total RAM (`ram`, GlobalMemoryStatusEx - `scheduler.ram_free_gb` reads it where Glances is absent, i.e. BigHonker); `stale=true` when it could not measure | Mothership, BigHonker, Softserve (installed 2026-09-27) |

Load sources: NVIDIA boxes `nvidia-smi --query-gpu=utilization.gpu,power.draw,memory.used`; Mothership
reads the last row of the foundry's own telemetry CSV (`F:\foundry\var\state\telemetry\gpu_telemetry.csv`,
written every ~11 s by the "GPU Telemetry Logger" task — the lab's existing instrument, no new probe) and
reports it stale if the file is older than 120 s. Ollama loaded models come from the box's own `/api/ps`.

## What Kirk does with a load push (the accounting rule)

`scheduler.report_gpu_load` evaluates EVERY push (event-driven, not on the watch tour):

1. `util_pct < 20` → **idle**. `stale` or no util → **no-sample** (rendered as such, never as idle).
2. Busy is **accounted** if any of these owns the card: a foundry run-state (`training_busy`), a scheduler
   `gpu` claim, an in-flight Kirk call on that box (`/hai/inflight`, registered at the Ollama request
   boundary), a Kirk call that ended within 90 s (unload / KV teardown), or — **on Softserve only — a live
   Plex transcode** (Tautulli's count, `plex_transcoding()`; Plex is that card's known user, the same verdict the
   placement side already gave). Added 2026-10-05: before it, every Plex NVENC transcode (encoder ~73 %, util
   22-39 %) DM'd `UNACCOUNTED GPU LOAD on softserve`, and the episodes flapped, because transcode util dips under
   20 % between pushes. A Tautulli that cannot be asked does NOT count as Plex.
3. Busy with none of them on **two consecutive pushes** → **UNACCOUNTED episode**: REPORT tier — the factory
   tile's **Load accounting** row reads `UNACCOUNTED · util N% · ollama: …` and ONE DM fires
   (`🚨 UNACCOUNTED GPU LOAD on <host> …`). Clears when load drops or a claim appears. Never an arrest.

Deck: `GET /p20/factory/<host>` field `accounting` → homepage row "Load accounting" on the three GPU foundry
tiles (`idle (util 0%)` · `accounted · Kirk call: eval_step … (util 62%)` · `UNACCOUNTED …` ·
`no load report` · `load report stale (N min)`). Contract: [[HAI-Core-Architecture-API-Contracts]] §gpu-load.

## Kirk's model calls WAIT on this report — why a long read no longer "times out" (silence watch, 2026-10-17)

A second consumer, since `5d82c73`: every streamed `/api/chat` call Kirk makes (`ollama_client._chat_once_raw` — chat
turns, the agent loop, compaction, the tool-loop hand-off) has **no request clock**. ollama sends nothing, not even
headers, while it reads a prompt, so a silent call asks its box's last load push (`scheduler.gpu_load`, same
thresholds as the accounting: 20 % util, 180 s fresh, 2 reports):
- **card busy** → keep waiting, however long the read takes (a 120k-token re-read on the 3.8 outlasted the old 240 s
  `CHAT_TIMEOUT` and killed HAI's McCoy-wiring job on 2026-10-16 22:36 CT);
- **our model loaded and the card idle on 2 reports since the silence began** → the call is ended as a stall
  (notes: `MODEL CALL STALLED`; the caller sees `ModelStalled`, a `ReadTimeout`);
- **no fresh report** (reporter down, box unknown, model not loaded yet) → the old `CHAT_TIMEOUT` clock applies
  (notes: `MODEL CALL CLOCKED`). **So a dead load reporter quietly brings the 240 s clock back** — if `CLOCKED` lines
  appear for a GPU box, check this task's `Last Result` first.
A call carried past the old clock logs `LONG READ … N s`. The call is ended by shutting its socket down, so ollama sees
the hang-up and stops; Stop works during a silent read the same way. Design + incident: [[37-Closeout]] § "Reopened
2026-10-17 night".

## Install / re-install (all three boxes in one pass — a host-level fix is carried to every peer)

```bash
# from Softserve, Bash tool (ssh softserve = full token for the local box; the interactive token is filtered)
bash <scratchpad>/install_gpu_load_reporter.sh
# per box it does:  scp Lab\bin\gpu_load_reporter.py -> C:\ProgramData\HAI\   then
schtasks /create /f /tn "HAI GPU Load Reporter" /sc minute /mo 1 /ru SYSTEM /rl HIGHEST /tr "\"<python>\" \"C:\ProgramData\HAI\gpu_load_reporter.py\" <host>"
```
Python per box: Mothership `C:\Users\<user>\AppData\Local\Python\bin\python.exe` · BigHonker
`C:\Program Files\Python312\python.exe` · Softserve `C:\Users\<user>\AppData\Local\Programs\Python\Python312\python.exe`.
`/sc minute /mo 1` is a real repeating schedule (not the ONCE-plus-`/run` trap that needs `/sd`); the script
is one-shot so it needs no restart policy — each minute is a fresh run.

## Verify (DECLARED ≠ PRESENT ≠ VISIBLE — do all three)

1. Task present and running: `schtasks /query /tn "HAI GPU Load Reporter" /v /fo LIST | findstr /i "Last Result Next Run"` → `Last Result: 0`.
2. Push arriving: `GET /p20/factory/<host>` → `accounting` is `idle (...)`/`accounted …`, NOT `no load report`
   (that string means the box is not pushing) and NOT `load report stale`.
3. **Live decoy** (the only proof the guard works): from a shell ON the box, run a raw generation nobody
   registered — `curl -s http://127.0.0.1:11434/api/generate -d '{"model":"<loaded model>","prompt":"count to 300 slowly","stream":false}'`
   — within ~2 minutes the tile must read `UNACCOUNTED` and one DM must arrive; then a normal HAI chat
   (`POST /query`) must read `accounted · Kirk call: chat`, never UNACCOUNTED (positive + negative control).

## Proven 2026-09-27 14:48–14:54 (decoy v2; v1 was inconclusive because the instrument was wrong, not the guard)

Six unregistered `/api/generate` calls on BigHonker's ollama from a shell there: box `nvidia-smi` 58–63 %,
reporter pushed 57 %, tile `suspect · busy with no owner, 1 of 2 pushes` on push 1 → `UNACCOUNTED · util 57%
· ollama: qwen3.5:9b` on push 2, exactly one `GPU-ACCOUNTING 🚨 …` line in Kirk's notes (the DM), cleared to
`idle (util 0%)` within a minute of the load ending. Positive control: a 71-s ephemeral HAI chat on the
8 GB tier read `accounted · Kirk call: chat (ephemeral) (util 87%)` and never UNACCOUNTED.
The v1 lesson: a raw call that returns HTTP 500 in 15 s puts no load on the card (the decoy did nothing), and
a 41-s chat can fall entirely between two once-a-minute pushes — print the box's own `nvidia-smi` beside the
tile so the sensor is proven before the verdict is read.

## Traps

- **"Idle" from a dead reporter looks like an idle card.** The tile says `no load report` / `stale` for that
  reason; if you see it, the task on the box has stopped — check `Last Result`, not the card.
- **One util threshold for two vendors** (20 %): idle NVIDIA and AMD cards here read 0–3 %; a model resident
  but not generating reads 0 %. If a vendor's idle floor ever rises, calibrate from the first observed idle
  values (doctrine class 7), do not guess.
- **The grace window is Kirk-side.** A raw call that starts within 90 s of a Kirk call ending is accounted
  as the tail of that call; a decoy must run longer than that to prove the guard.
- **Fellowship tile has no Load accounting row on purpose** — tablets push no GPU load; a permanently blank
  row trains the operator to skip the row that finally matters.
