# YouTube Torznab Pipeline - Overview & Configuration



## Full Pipeline

```
Sonarr search → Prowlarr (indexer priority 50, last resort)
  → GET yt-torznab:9797/api?t=tvsearch&tvdbid=X&season=1
  → yt-torznab: YouTube Data API → finds playlist/channel
  → Returns RSS: "Show S01 WEBRip.2160p [YouTube]" size=20GB pubDate=now
  → Sonarr grabs (score=-1000 via YouTube Source format, fine if no competition)
  → POST yt-torznab:9797/qbt/api/v2/torrents/add (fake qBit)
    → Sonarr sends multipart/form-data with the .torrent in field name="torrents"
      (NOT a urls= param). FIXED 2026-05-29: adapter now parses multipart via
      extract_torrent_part() → extract_announce(); prior parse_qsl-only code
      missed it and wrote blank entries. (Also handles urls=/dl? direct + Prowlarr proxy.)
    → announce URL carries: cid, ctype, curl, title, tvdbid
    → is_in_pinchflat(cid)? → YES: reindex_pinchflat_source() (mark downloaded items prevent_download=1, queue indexer)
                             → NO:  add_to_pinchflat() (insert source, queue indexer)
  → Pinchflat: downloads 4K with cookies, mkv container, sponsorblock=mark
    → files land in /downloads/shows/{name}/Season YYYY/ (= /mnt/media/youtube-staging/ on host)
  → check_completions() thread (every 120s):
    → finds media_items WHERE media_filepath IS NOT NULL not in imported_items.json
    → find_sonarr_series(source_name, tvdbid) → direct TVDB lookup if tvdbid present
    → match_sonarr_episode(video_title, series_title, episodes)
    → shutil.move to /mnt/media/TV Shows/{series}/Season {N}/{series} - S01E05 - Episode Title WEBRip-2160p [YouTube].{ext}
    → POST Sonarr /api/v3/command RescanSeries
    → adds item_id to /app/imported_items.json
```

## Key Config

| Item | Value |
|------|-------|
| Service | `yt-torznab` on `arrnet`, port 9797 |
| Server file (source) | `E:\SSDShared\SSD\Open Access\Claude Home\yt-torznab-server-pending.py` |
| Server file (deployed) | `/opt/arrstack/yt-torznab/server.py` |
| HOST_IP | `<sundae-address>` (host-reachable IP for torrent announce URLs) |
| SONARR_URL | `http://172.18.0.1:8989` (Docker bridge gateway) |
| TV_ROOT | `/mnt/media/TV Shows` (host path, mounted into container) |
| Torznab endpoint | `/api` (apikey=<secret>) |
| Fake qBit endpoint | `/qbt` |
| Prowlarr indexer | id=**18** in Prowlarr, synced as id=**24** in Sonarr |
| Sonarr download client | id=2, host=yt-torznab, port=9797, urlBase=/qbt |
| Release quality tag | `WEBRip.2160p`, fake size=21474836480 (20GB) |
| Custom format | "YouTube Source" id=19, score=-1000 |
| Staging path (Sonarr container) | `/media/youtube-staging/` |

## Volume Mounts (docker-compose)

```yaml
volumes:
  - /opt/arrstack/yt-torznab:/app
  - /opt/arrstack/pinchflat/db:/pinchflat-db
  - /mnt/media:/mnt/media          # required for import monitor
```
