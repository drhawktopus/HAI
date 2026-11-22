# YouTube Torznab Pipeline - Auto-Import & Pinchflat Integration



## Auto-Import Monitor (check_completions)

Background thread running every 120 seconds. This is the PRIMARY import mechanism — the 30-min Sonarr cron (`DownloadedEpisodesScan`) is a fallback only.

**What it does:**
1. Reads all Pinchflat `media_items WHERE media_filepath IS NOT NULL`
2. Skips items already in `/app/imported_items.json`
3. For each new item:
   - Converts container path (`/downloads/...`) to host path (`/mnt/media/youtube-staging/...`)
   - If staging file is gone → return True (already imported, stop retrying)
   - Calls `find_sonarr_series(source_name, tvdbid)` → direct TVDB lookup, fuzzy fallback
   - Calls `match_sonarr_episode(video_title, series_title, episodes)`:
     - Strips show title prefix from video title
     - Strips episode number prefix (`Ep 5:`, `Episode 5 -`, etc.)
     - Fuzzy-matches cleaned title against all Sonarr episode titles (≥50% threshold)
   - Builds filename: `{Series} - S{season:02d}E{episode:02d} - {sonarr_ep_title} WEBRip-2160p [YouTube].{ext}`
   - `shutil.move(staging_file, /mnt/media/TV Shows/{series}/Season {N}/{filename})`
   - `POST /api/v3/command RescanSeries` to register in Sonarr
   - Adds item_id to `/app/imported_items.json`

**State files (inside container at /app/):**
- `downloads.json` — fake qBit torrent state: `{hash: {title, source_id, channel_id, channel_url, tvdbid, state, ...}}`
- `imported_items.json` — list of Pinchflat media_item IDs already imported

## Pinchflat Config

| Setting | Value | Notes |
|---------|-------|-------|
| media_profile 1 name | TV Shows | The profile yt-torznab uses (MEDIA_PROFILE=1) |
| preferred_resolution | 2160p | Downloads 4K when available |
| sponsorblock_behaviour | mark | Adds chapter markers, no re-encoding (fast) |
| media_container | mkv | Required for AV1/VP9 — mp4 remux fails with current ffmpeg |
| cookie_behaviour | all_operations | Used on sources that need YouTube auth |
| Cookies file | `/opt/arrstack/pinchflat/cookies.txt` → `/etc/yt-dlp/cookies.txt` |
