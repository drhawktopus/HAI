# CorridorKey Ops — install, runners, offload kit (both boxes)

*Created 2026-07-23. Single source for operating CorridorKey on Mothership + BigHonker.
Install/debug history: Journal 2026-07-23. Eager-tax analysis: [[Eager-Tax-Research-Montage]].*

## Installs

| | Mothership (7900 XTX, ROCm) | BigHonker (RTX 3070, CUDA) |
|---|---|---|
| Path | `F:\CorridorKey` | `F:\CorridorKey` |
| Torch | **vendor transplant** from production embedded py312 (7.15.0a20260714) | `--extra cuda` (2.8.0+cu128) |
| Launch | `uv run --no-sync python ...` — **`--no-sync` is LOAD-BEARING** (bare `uv run` re-syncs the lockfile → silently reverts to CPU torch) | `uv run --extra cuda python ...` |
| Collision gate | ON (default; `corridorkey_gated.py` hold + quiet-card handshake — MES-poison class) | `--no-gate` (no MES bug; worst case = ordinary contention) |
| ffmpeg | `C:\ffmpeg\bin\ffmpeg.exe` | on PATH |

Masters live in `E:\SSDShared\mothership-build\` (canonical); deploy by scp + MD5 verify to BOTH boxes.

## Runner — `corridorkey_run.py` (v1.2)

- `python corridorkey_run.py <clip> [--pipelined] [--max-frames N] [--no-gate] [--log-every N]`
- **`--pipelined` = the surgery win**: prefetch + GPU-only main loop + 3-worker writer pool.
  Verified 2026-07-23: Mothership 6.15→1.25 s/f (~5×), BigHonker 9.81→2.52 s/f (~3.9×).
  Root cause both boxes: synchronous ~25MP PNG writes serialized the loop (CPU/IO, not kernels).
- Telemetry: `telemetry\<clip>-<ts>.jsonl` (events) + `telemetry\STATUS.json` (live snapshot).
- `CORRIDORKEY_FORCE_COMPILE=1` enables torch.compile despite upstream's stale Windows skip (+4.5% ROCm).

## Offload kit (2026-07-23) — on-box automation so Claude isn't polled

All three in `F:\CorridorKey\` on both boxes; DM-to-the owner via the hawkadminbot relay
(`http://<sundae-address>/dm` + `/dm-file`, Host `hawkadmin.lan`, Caddy → :8477 — the same
Component-3 route the P20 foundry uses). Creds in `F:\CorridorKey\notify.json`
(dm_token/user_id, values from `F:\foundry\etc\dm_relay.env`; NOT in git/masters).

- **`ck_notify.py`** — `python ck_notify.py "msg" [--file img.jpg]` one-shot DM;
  `--watch` tails STATUS.json and DMs stage changes + every 25%, exits when STATUS stale 15m.
  Failures never raise (notify must not kill a run). Posts prefixed `[hostname]`.
- **`corridorkey_postrun.py <clip>`** — stitch comp mp4 (if missing) + 1080p proxy +
  12-frame filmstrip JPG (evenly spaced, frame 0 → ~92%) + per-stage stats from the newest
  jsonl → DM w/ filmstrip, then DM the video: 1080p proxy if ≤8MB (relay cap), else a
  size-targeted 720p `_dmcut.mp4` (~7.2MB bitrate budget from duration); if even that
  busts the cap, DMs the on-disk path instead. `--no-video` / `--no-dm` to suppress.
  Chain after runs: `... corridorkey_run.py <clip> --pipelined && ... corridorkey_postrun.py <clip>`
- **`corridorkey_bench.py <clip> [--frames 40]`** — runs stock vs `--pipelined` legs,
  parses s/f, DMs one summary line. Auto-detects box (`_rocm_sdk` in venv → `--no-sync`+gate;
  else `--extra cuda`+`--no-gate`) — replaces hand-orchestrated bench sessions.

Division of labor (the owner, 2026-07-23): **Claude authors/decides; scripts+HAI execute and report;
Claude gets summoned at judgment points** (eye-checks, surgery, what-next). DMs preferred over
channel posts while testing.

## Bench discipline

Single-variable legs on a cloned `bench-key-*` clip folder (Input + AlphaHint copied) — never
re-key a delivered clip's folder in place. Existing benches: `bench-key` (Mothership),
`bench-key-bh` (BigHonker). Quality check: `corridorkey_bench_diff.py` (PSNR; both boxes).
