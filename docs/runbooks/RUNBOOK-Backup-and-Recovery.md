# RUNBOOK — Backup & Recovery



## Backup & Recovery

### What's Backed Up

**GitHub (weekly, private repo `sundaestackarr-config`, branch `backup`):**
- `docker-compose.yml`
- Pi-hole config (`pihole.toml`, `dnsmasq.conf`)
- Cleanuparr DB (seeker config)
- qBittorrent settings
- JukeBot `config.json`
- All system configs (netplan, Docker daemon, fstab, systemd timers, scripts incl. `push-lan-to-minuteman.sh`)
- `restore.sh` automated restore script
- `README.md` restore guide

**Branch layout:** remote `main` belongs to `/opt/arrstack` (the live tracked config repo); the weekly backup script (`/opt/arrstack-backup`) force-pushes a fresh **orphan snapshot** to the `backup` branch each run — two repos sharing `main` with unrelated histories make backup pushes fail silently, which is why the branches are split. `prowlarr.db` (121MB) and `cleanuparr/events.db` (63MB) are excluded from GitHub (100MB blob limit; the script regenerates `.gitignore` every run) — they're still committed to the backup repo's **local** `main` history and covered by the weekly local rsync.

**Local rsync (weekly, Sunday 5:15am):**
- Full snapshot of `/opt/arrstack/` including large databases:
  - `sonarr/sonarr.db` — all monitored shows, history
  - `radarr/radarr.db` — all monitored movies, history
  - `lidarr/lidarr.db` — music library

Pi-hole `gravity.db` is **not** backed up — it's rebuildable in minutes with `docker exec pihole pihole -g`.

**Claude Code transcripts (daily 03:30, Softserve, since 2026-09-26):** additive copy of
`C:\Users\<user>\.claude\projects` (every session `.jsonl` + memory) to
`Z:\Archives\claude-transcripts\softserve\`. Never pruned, so sessions Claude Code's own 30-day cleanup
deletes survive there. Status, restore steps and re-registration: [[RUNBOOK-Claude-Transcript-Backup]].

### ⚠ The local backup goes to the SSD, NOT the Z: array — and secrets need root to be captured

**Destination:** `/mnt/ssdshared/Backup/SundaeStackarr` =
`\\<softserve-address>\SSDShared\Backup\SundaeStackarr` = **`E:\SSDShared\Backup\SundaeStackarr`** on
Softserve — the **1 TB SSD**, not `Z:` (the 46 TB HDD array). the owner's stated intent is that backups
live on the HDD array — moving them there is a deliberate change needing a new CIFS mount **and**
an update to the restore commands, so do not "correct" the path in the script alone.

**Three properties of the backup unit, each load-bearing:**

1. **The unit runs as `User=root`.** `/opt/arrstack/secrets/*` are `root:root 0600`, so a non-root
   unit silently backs up ZERO secrets (`Permission denied (13)`) — including
   **`mothership_ed25519`** and **`tabcluster_key`**, the SSH keys for the GPU box and the entire
   Fellowship — and a restore would rebuild the stack with no API keys and no SSH access to
   anything.
2. **The exit code is propagated and the script asserts the destination secret count matches the
   source.** An unconditional Discord "💾 Local backup complete" renders rsync exit 23 as a clean
   success and systemd records `status=0/SUCCESS`. *"Captured everything" and "captured nothing"
   must never look identical.*
3. **Permanent exit-23 noise is handled structurally, because noise is what hides a real failure:**
   - `whisper-cache/` (regenerable uv/HuggingFace cache full of symlinks CIFS cannot create) is
     **excluded**;
   - **`--no-D`** (never copy devices or specials) stops rsync recreating qBittorrent's live unix
     socket via `mknod`, which CIFS refuses — a flag rather than a per-path exclusion, so the next
     special file does not re-break it.

Also added a `mountpoint -q /mnt/ssdshared` guard: writing 16 GB into an *unmounted* mountpoint
fills the VM's own root disk and still looks like a successful backup.

### `git status` in the backup copy shows "uncommitted changes": they are NOT work — never commit there

`E:\SSDShared\Backup\SundaeStackarr` is the weekly rsync of `/opt/arrstack`, `.git` included, so git on
Softserve reads it as a repo with a dozen modified files. Measured 2026-11-14: **10 of 12 were mode
changes** (CIFS drops the executable bit, `100755 → 100644`), one was the `claudediscordbot` pointer
from before that week's commit, and one was `yt-torznab/server.py.bak- `, a name ending in a space
that Windows cannot store (untracked from the repo that day). `git -c core.fileMode=false status`
shows the real picture. Uncommitted work lives in `/opt/arrstack` on Sundae itself
(`sudo git status`, and `git rev-list --left-right --count @{u}...HEAD` for unpushed commits —
`arrstack-github-backup.sh` force-pushes an orphan snapshot to the `backup` branch and never pushes
`main`, so a hand commit on `main` waits for a hand push).

### Weekly backup FAILED with `rsync failed with exit 24` / "some files vanished before they could be transferred" — not a failure since 2026-11-15
rsync's exit 24 means a file that existed when the list was built was gone when its turn came. On 2026-11-15 05:15 CT
Lidarr closed `lidarr/logs.db-wal` and `logs.db-shm` mid-run and `/usr/local/bin/arrstack-local-backup.sh` reported the
whole backup FAILED (a transient property of a live tree reaching the catch-all that fails the run). Everything that
still existed had been copied. The script now turns exit 24 into a sentence in the completion notice ("Some files were
closed by their app while copying") and a journal line; **every other non-zero rsync exit still fails the run**. Re-run
the same morning: the same two files vanished again and the run ended `Result=success` (script backup
`.bak-20261115-140906`). To run it by hand: `sudo systemctl start arrstack-local-backup.service` (about five minutes),
then `systemctl show -p Result arrstack-local-backup.service`.

### Pi-hole's query database is backed up as a consistent SNAPSHOT, never copied raw (since 2026-11-14)

**Why:** `pihole-FTL.db` (~3.3 GB, WAL mode, written every minute) copied raw killed the WHOLE run on
2026-10-25 and 11-01 — rsync exit 11, `close failed … Input/output error (5)` on the `soft` CIFS mount —
so nothing after it was synced those weeks; and even a "successful" raw copy took the db and its `-wal`
at different moments. **Now:** the main rsync excludes `pihole-FTL.db` / `-wal` / `-shm`; a separate step
streams SQLite's `.dump` (one read transaction: consistent under WAL, never blocks Pi-hole's writer)
through `zstd` to `/var/tmp`, checks it (both exit codes, `zstd -t`, ends in `COMMIT;`), copies it under
a temp name with a sha256 match and renames it to **`pihole-snapshot/pihole-FTL.sql.zst`**. Its verdict is
its own: a failed snapshot makes the run **PARTIAL (exit 3)** and says why, never "complete".
Pi-hole's own community says the same thing (never copy the live file; use FTL's built-in sqlite:
`VACUUM INTO` or `.backup`); those write a full copy to disk first (≥2.3 GB on a root disk at 91 %),
which is why this streams a dump instead. Teleporter deliberately omits the query history.
**Measured 2026-11-14:** dump 101 s, 535 MB; DNS answered 64/64 probes during it (max 149 ms). **Restore
proven** the same day into a throwaway container from Pi-hole's own image: 266 s, `quick_check: ok`,
`query_storage` 36,959,433 rows vs 36,962,834 live (the gap = queries after the dump), `network` 94/94,
`network_addresses` 19/19.

**Restore the query history** (Pi-hole stopped, no `pihole-FTL.db` in place):
```bash
zstd -dc /mnt/ssdshared/Backup/SundaeStackarr/pihole-snapshot/pihole-FTL.sql.zst \
  | docker run --rm -i -v /opt/arrstack/pihole/etc-pihole:/r --entrypoint pihole-FTL pihole/pihole:latest sqlite3 /r/pihole-FTL.db
```
Without it Pi-hole simply starts a fresh history; settings and lists come from the rsync'd tree.

**Verify a run properly** (`Result=success` alone is not enough — check the artifact):
```bash
sudo systemctl start arrstack-local-backup.service
systemctl show arrstack-local-backup.service -p Result -p ExecMainStatus   # want success / 0
ls /mnt/ssdshared/Backup/SundaeStackarr/secrets/ | wc -l    # must EQUAL: sudo find /opt/arrstack/secrets -type f | wc -l
ls -la /mnt/ssdshared/Backup/SundaeStackarr/pihole-snapshot/   # pihole-FTL.sql.zst, dated this run
```
(The script itself asserts the secret count against the source; 24 = 24 on 2026-11-14 — an older "want 11"
here was a roster that went stale.) Backups of earlier scripts: `*.bak-20260812-*` and `*.bak-20261114` on Sundae.

# 2. Create SMB credentials
sudo tee /etc/smb-credentials << EOF
username=<user>
password=<password>
domain=SOFTSERVE
EOF
sudo chmod 600 /etc/smb-credentials

# 3. Run restore script (copies system configs, starts stack)
sudo bash /opt/arrstack-backup/restore.sh

# 4. Mount media shares
sudo mount -a

# 5. Rebuild Pi-hole blocklists
docker exec pihole pihole -g

# 6. Restore large databases from local backup (if Softserve intact)
rsync -av //<softserve-address>/SSDShared/Backup/SundaeStackarr/sonarr/ /opt/arrstack/sonarr/
rsync -av //<softserve-address>/SSDShared/Backup/SundaeStackarr/radarr/ /opt/arrstack/radarr/
rsync -av //<softserve-address>/SSDShared/Backup/SundaeStackarr/lidarr/ /opt/arrstack/lidarr/
docker compose -f /opt/arrstack/docker-compose.yml restart sonarr radarr lidarr
```

### Manual Backup Trigger

```bash
sudo /usr/local/bin/arrstack-github-backup.sh   # push config to GitHub now
sudo /usr/local/bin/arrstack-local-backup.sh    # rsync to E:\SSDShared\Backup now (NOT Z:)
```

### Home AI Project #11 media tools

**Deployed tool surface** (admin = gated to `ADMIN_USERS`, all writes are preview→`confirm_action`):

| Service | Read tools | Write tools (admin + confirm) |
|---------|-----------|-------------------------------|
| Sonarr | `sonarr_lookup`, `sonarr_releases`, `sonarr_import_candidates` | `sonarr_search`, `sonarr_grab`, `sonarr_manual_import` |
| Radarr | `radarr_lookup`, `radarr_releases`, `radarr_import_candidates` | `radarr_search`, `radarr_grab`, `radarr_manual_import` |
| Prowlarr | `prowlarr_search` | — |
| qBittorrent | `qbit_torrents` | `qbit_action` (pause/resume/recheck), `qbit_add`, `qbit_remove` (destructive) |
| Seerr/Overseerr | `seerr_requests` | `seerr_approve`, `seerr_decline` (destructive), `seerr_add` |
| Plex (on Softserve) | `plex_sections` | `plex_refresh` (scan one library or all; `force`=metadata) |
| Quality (Sonarr/Radarr) | `quality_profiles`, `custom_formats` | `custom_format_add` (ReleaseTitle-regex CF), `quality_profile_score` (set a CF's score in a profile — the "make it stick" lever) |
| Lidarr (music, API v1) | `lidarr_lookup`, `lidarr_albums` (bounded — library is huge) | `lidarr_search` (ArtistSearch/AlbumSearch) |
| Media file | `media_probe` (ffprobe dual-audio, scoped to RO `/media`) | — |
| Disk (Phase 4) | `disk_usage` (free space on `/media` array + `/downloads` SSD staging + `/docs`; optional `du` subfolder sizes, timeout-bounded) | — |
| Docker (Phase 4, via `hostops` broker) | `docker_status`, `docker_logs`, `git_status` | `docker_restart`/`docker_stop`/`docker_start` (PROTECTED set refused), `caddy_reload`, `pihole_restartdns`, `recyclarr_sync` (dry-run default), `watchtower_run` (one-off --run-once), `git_commit` (LOCAL only, no push) |
| Config edits | `read_config`, `disk_usage` | `write_config` (homepage/Caddy edits, preview→confirm→backup) |
| Framework | `confirm_action` (approve/deny/list pending) | — |

**3-layer write guard:** deterministic risk flags (Tier 1, in `pending_actions.assess_risk`) → small-model
intent check (Tier 2, `action_review.py`, on MED/HIGH only) → human `confirm_action` (Tier 3). Every
preview/confirm/deny is audited to `/docs/State/audit/ops-audit.jsonl`.

**Implementation files:** `pending_actions.py`, `arrclient.py`, `qbitclient.py`, `seerrclient.py`,
`plexserver.py`, `action_review.py`, `media_tools.py`, `infra.py` (disk), `hostops.py` + `hostops_sidecar.py`
(the host-ops broker), `tools.py`.

**Host-ops broker (Phase 4):** the orchestrator is socket-free; a separate **`hostops`** container holds
`/var/run/docker.sock` and exposes a narrow, **token-authed** (`x-hostops-token` ← `/opt/arrstack/secrets/hostops_token`)
API on arrnet only (`http://hostops:9120`, NOT host-published). It only does whitelisted ops (ps/logs/restart),
validates container names, and refuses the PROTECTED set. **Every future host/docker capability = one new sidecar
endpoint + one thin tool** (Caddy reload, image pull, Watchtower, Pi-hole, git, Recyclarr are the next ones).
**⚠️ QUALITY PROFILES — PRESERVE THE HAND-CRAFTED ONES (the owner, 2026-06-27).** Some Radarr/Sonarr quality
profiles + custom-format scores are **hand-crafted** (the owner's own + earlier the owner-and-Claude work) and must be
preserved. **Recyclarr can overwrite them**, so know exactly what it touches before syncing:
- `recyclarr.yml` (`/opt/arrstack/recyclarr/recyclarr.yml`) currently MANAGES the Radarr profiles **`4K WEB`**
  and **`1080p WEB`** with **`reset_unmatched_scores: enabled: true`**. That flag **zeroes any custom-format
  score in THOSE two profiles that isn't explicitly defined in recyclarr.yml** — so a sync can wipe hand-tuned
  scores in 4K WEB / 1080p WEB. **Profiles with other names are NOT touched** (recyclarr only manages the named
  ones). `delete_old_custom_formats` is NOT set, so it won't delete your custom formats (only re-score the
  managed profiles).
- **ALWAYS dry-run first:** `docker compose --profile tools run --rm recyclarr sync --preview` shows the diff
  and changes nothing. The HAI **`recyclarr_sync` tool DEFAULTS to a dry-run** (`apply=false`); only
  `apply=true` runs the real sync (and its confirm preview warns about reset_unmatched_scores + names the
  managed profiles). Review the dry-run diff before ever applying.
- For **targeted, non-destructive** tweaks to a single profile/format, prefer the HAI tools
  `quality_profile_score` / `custom_format_add` (they touch only what you name) over a full recyclarr sync.
- If a sync ever clobbers a profile: Radarr/Radarr keep DB backups (`/config/Backups/`), and the config repo
  history has prior states; restore from there.

**One-off-container endpoints** (recyclarr/watchtower/git) use `_run_oneoff` = the `compose run --rm` equivalent
(create→start→wait→logs→rm), and **pull-if-missing** (Engine `create` doesn't auto-pull). `git_commit` is
**local-only, no push** (the owner's choice — no GitHub credentials on the broker; push stays manual / nightly
backup); the commit message rides an env var (`MSG`), not the command string, so it can't shell-inject. The git
one-off runs `alpine/git` as uid 1000 (<user>) against `/opt/arrstack:/repo`. **Restart the broker after editing
`hostops_sidecar.py`: `docker restart hostops`** (the `.py` is a `:ro` mount
read at process start; `docker compose up -d hostops` is a no-op when the service def is unchanged, so it won't
pick up code edits — use `docker restart`). Exec endpoints (`/caddy/reload`, `/pihole/restartdns`) run FIXED
commands only (never request-supplied). lan.list editing: mounted at `/hostcfg/pihole`, edit via `write_config`
then apply with `pihole_restartdns`.

**Secrets** (root-only files on Sundae `/opt/arrstack/secrets/`, mounted `:ro` at `/run/secrets/*`, pointed to
by `*_APIKEY_FILE`/`PLEX_TOKEN_FILE` env in `docker-compose.override.yml` — **never inline a key in code/docs**;
`config._secret_value` reads them): `sonarr_apikey`, `radarr_apikey`, `prowlarr_apikey` (from each arr's
`config.xml`), `lidarr_apikey` (Lidarr `config.xml`), `seerr_apikey` (from the seerr container's
`settings.json` `main.apiKey`), `plex_token` (from Softserve's
`HKCU\Software\Plex, Inc.\Plex Media Server\PlexOnlineToken`), `qbit_password` (the qBittorrent WebUI login,
`QBIT_PASS_FILE`; the user name stays the plain `QBIT_USER` setting). qBit lives in the expressvpn netns; reach it at
`expressvpn:8080` / `172.18.0.1:8080`. ⚠ qBit's `AuthSubnetWhitelist` trusts `<lan-subnet>/24, 172.18.0.0/16`, so
from the stack (and the LAN) every API call works WITHOUT a login and `/auth/login` answers `204` with no cookie for
ANY password, right or wrong: a login test from inside the stack cannot prove a password. The password only matters to a
caller outside those subnets. (Until 2026-11-15 the qBit password sat in `config.py` as a default value; the
publish check now refuses any password, token or key written as a setting's default.)

**Running the suite:** tests aren't baked into the image (`Dockerfile` does `COPY *.py`), so
`docker cp /opt/arrstack/orchestrator/tests orchestrator:/app/tests`, then
`docker exec -w /app -e EMBED_ENABLED=0 -e PYTHONPATH=/app orchestrator python -m unittest discover -s tests -t tests -p 'test_*.py'`.
The **confirmation UI**: `GET /pending` / `POST /pending/act` (confirm|deny) / `GET /audit` endpoints + a web-UI pending-actions panel (risk-colored, ✓Approve/✗Deny, polls 6s) + admin-gated 📋 Audit toggle — every write tool's pending action is click-to-approve. Optional/parked: proactive push-notifications, YouTube tooling. **Non-goal:** no general unsandboxed shell tool for HAI.
