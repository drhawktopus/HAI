# Plex usage profile on Softserve — when Plex transcodes, how many at once, hardware vs software (measured 2026-09-28)

What the lab knows about how much of Softserve Plex actually uses, for sizing how lab work shares the box
with it. Plex has priority on Softserve (the owner, 2026-09-28: sharing *"with priority going by far to plex"*;
*"It can't take resources away from plex if plex is active"*). The live rule that enforces it is in
[[RUNBOOK-Compute-and-Training-Order-Submission]] §"How the desk picks factories"; this page is the
measured history behind any future sharing thresholds. Aggregates only — no per-user data.

## Sources and spans

| source | span | what it can and cannot say |
|---|---|---|
| Tautulli `get_history` (Sundae, :8181) | 2026-06-02 → 2026-09-29, 118.8 days, 2,237 grouped sessions (3,328 ungrouped plays) | decisions, timing, concurrency |
| Tautulli `get_stream_data` | same span, all 1,520 transcode sessions, 0 fetch errors | per-session codec, hardware/software, subtitle, HDR |
| Glances on Softserve `/api/4/gpu/history` | ~10 days, 30 s samples | whole-card util/VRAM; cannot attribute load to Plex |
| Plex Media Server logs | ~1–2 days (10 MB rotation) | no trend value |
| per-second GPU logger (`E:\foundry\var\state\telemetry\gpu_telemetry-*.csv`) | from 2026-09-28 22:20 | has `enc_util`, `dec_util`, `vram_used_mb`; the instrument for per-transcode cost going forward |

## What Plex does with a stream

- Sessions: **67.9% transcode**, 31.5% direct play, 0.6% direct stream (last 30 days: 84.3% transcode).
  LAN sessions transcode more than WAN ones (83.9% vs 50.4%), so bandwidth is not the driver.
- **Of the 1,520 transcodes, 83.3% re-encode the VIDEO** (53.1% video + audio, 30.2% video only); 16.4% are
  audio-only transcodes with the video copied. The video re-encodes are mostly **codec conversions to
  H.264**: source codecs HEVC 608, AV1 444, VC-1 111, H.264 103. Largest single causes: AV1→H.264 (416),
  HEVC→H.264 (365), subtitle burn-in alone (146). Client mix is dominated by Roku and Vizio SmartCast TV apps.
- **82.0% ran the full NVENC + NVDEC hardware pipeline; 17.2% were fully software (CPU).** The software share
  is not caused by concurrency (70% of software transcodes started with no other transcode running) — more
  likely codec/profile combinations the hardware path does not take.
- HDR/Dolby Vision source in 6.8% of transcodes; tone-mapped to SDR in 1.7% of all transcodes.

## When

- Probability of ≥1 active transcode by local hour (America/Chicago, fixed UTC−5, so up to an hour off in
  winter): **lowest 08:00 (6.7%) and 09:00 (8.8%)**, 04:00–07:00 at 10–13%; **highest 21:00 (51.8%),
  20:00 (44.8%), 19:00 (43.8%)**. Saturday and Sunday carry about twice a weekday's sessions.
- Session length (watched seconds): median 17.7 min, p75 25.6 min, p90 64 min, max 3.7 h.

## How many at once

- Something is playing 43.2% of the span; **a transcode is running 23.2%** of it.
- While any transcode runs: one transcode 88.5% of that time, two 10.6%, three or more 0.9%.
- Peaks in 118 days: **6 concurrent streams, 5 concurrent transcodes.**

## What a transcode costs

**Not measured yet.** Idle baseline from the per-second logger: 175 MB VRAM, 17–18 W, P5 clocks, enc/dec 0%.
Whole-card Glances history (not Plex-attributed): mean util 0.36%, 1.0% of samples above 5%. The one
measured harm to Plex from lab work (2026-08-26) was **CPU contention** — a software transcode starved by a
lab order's CPU threads while the GPU encoder sat at 0% — which is why the Plex rule covers CPU orders on
Softserve, not just GPU ones. The next measurement: read the per-second logger's `vram_used_mb`,
`enc_util`, `dec_util` during a known hardware transcode and a known software one.

## See also

[[Machine-Softserve]] · [[RUNBOOK-Compute-and-Training-Order-Submission]] · [[RUNBOOK-Foundry-and-Rescue-Daemons]] (GPU logger)
