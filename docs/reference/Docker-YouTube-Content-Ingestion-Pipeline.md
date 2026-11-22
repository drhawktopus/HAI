# SundaeStackarr - YouTube Content Ingestion Pipeline



## YouTube Pipeline Configuration

The YouTube content ingestion pipeline consists of the `yt-torznab` and `Pinchflat` containers. These services remain active to maintain the five legitimate Pinchflat sources.

Automated ingestion via Sonarr is disabled by default to prevent uncontrolled storage consumption. The pipeline operates in manual-only mode unless explicitly re-enabled.

To reactivate automated YouTube ingestion:
1. Enable Prowlarr indexer #18 "YouTube (Last Resort)" by setting `enable=true`.
2. Uncomment the `#DISABLED-YT-PIPELINE` entry in <user>'s crontab to restore the `*/30` auto-import schedule.

### How it works (when enabled)

Sonarr uses `yt-torznab` (port 9797) as a custom Torznab indexer that fakes a qBittorrent API and
hands grabs to Pinchflat for downloading:

```
Sonarr tvsearch (tvdbid=X) → yt-torznab gate check → YouTube Data API search
  → fake torrent release → Sonarr grab → yt-torznab fake qBit (/qbt)
  → Pinchflat downloads 4K (mkv, sponsorblock=mark) into staging
  → yt-torznab check_completions() thread (120 s poll) → matches series/episode
  → shutil.move into /mnt/media/TV Shows/... → Sonarr RescanSeries
```

### Gate logic (`/opt/arrstack/yt-torznab/server.py`)

**Required:** Every `tvsearch` request **requires** a `tvdbid`. Absence results in an empty response, blocking RSS polls and Prowlarr sync.
- With valid `tvdbid`: The system fetches the Sonarr series. Access is permitted only if:
    1. The network name contains "youtube" (case-insensitive), **OR**
    2. Network is null/blank AND the Sonarr tag `13` (`youtube-source`) is present.
- Fail-closed: Requests failing these checks return empty results.
- Alerting: Denied requests for shows with a null-network trigger a Discord ping to prompt manual tagging.

### Configuration parameters and paths

| Parameter | Value / Path | Notes |
|-----------|--------------|-------|
| Service name | `yt-torznab` | Deployed on arrnet, port 9797 |
| Server binary | `/opt/arrstack/yt-torznab/server.py` | Local path on host |
| HOST_IP | `<sundae-address>` | Used in torrent announce URLs |
| SONARR_URL | `http://172.18.0.1:8989` | Bridge gateway address |
| TV_ROOT | `/mnt/media/TV Shows` | Host mount path |
| Torznab endpoint | `/api` | API entry point |
| Fake qBit endpoint | `/qbt` | Emulated client interface |
| Prowlarr indexer ID | `18` | Synced into Sonarr as ID `24` |
| Release tag | `WEBRip.2160p` | Used for filtering/matching |
| Fake size | `20 GB` | Simulated torrent size |
| Custom format name | `YouTube Source` | ID `19`, score `-1000` |
| Staging directory (container) | `/media/youtube-staging/` | Maps to host path |
| Staging directory (host) | `/mnt/media/youtube-staging/` | Mount target |

### State files

Located in container path `/app/`:

- `downloads.json`: Tracks fake qBit torrent state.
- `imported_items.json`: Stores Pinchflat media_item IDs already imported.

### Pinchflat Configuration

Pinchflat is configured for high-resolution archival and specific media handling constraints.

| Setting | Value / Path |
|---------|-------------|
| Default Media Profile | `TV Shows` (`MEDIA_PROFILE=1`, used by yt-torznab) |
| Preferred Resolution | 2160p |
| Media Container | `mkv` (Required for AV1/VP9 support; mp4 remux fails with current ffmpeg) |
| SponsorBlock Behavior | `mark` (Chapter markers only; no re-encode) |
| Cookie Behavior | `all_operations`; cookies stored at `/opt/arrstack/pinchflat/cookies.txt` |
| Database Path | `/opt/arrstack/pinchflat/db/pinchflat.db` |

#### Active Sources and Import Rules

Auto-import is disabled. The following sources are configured in Pinchflat:

| ID | Name | State |
|----|------|-------|
| 3 | helluva boss in order | enabled |
| 8 | The Amazing Digital Circus | enabled |
| 10 | Knights of Guinevere | enabled (playlist reaction videos have `prevent_download=1`) |
| 11 | Coulda Been Love Season 1 | disabled (season complete, 7 episodes downloaded) |
| 39 | Helluva boss playlist in order | enabled |

#### Tagging Overrides

Tag 13 (`youtube-source`) is an override tag applied only to specific shows:
*   **Network `YouTube`**: Helluva Boss, Knights of Guinevere, The Amazing Digital Circus, Exandria Unlimited.
*   **Network `null`**: Coulda Been Love (requires explicit tagging due to null network metadata).

Only these shows should carry the `youtube-source` tag.

### Troubleshooting

```bash
docker logs yt-torznab --tail=50 -f
docker logs pinchflat  --tail=50 -f

# Gate smoke tests
curl "http://localhost:9797/api?t=tvsearch&tvdbid=391246&apikey=<key>"      # Helluva Boss → 1 item
curl "http://localhost:9797/api?t=tvsearch&q=The+Amazing+Race&apikey=<key>" # no tvdbid → 0 items
```

- **"Sign in to confirm you're not a bot"** → YouTube cookies expired (~1–2 months); re-export and
  copy to `/opt/arrstack/pinchflat/cookies.txt` (bind-mounted, no restart needed).
- **AV1/VP9 postprocessing failure** → ensure Pinchflat media profile `media_container='mkv'`.
- **YouTube API 403** → daily 10k quota exhausted (resets midnight Pacific).
