# YouTube Torznab Pipeline - TVDB ID Threading & Reindexing



## TVDB ID Threading

The TVDB ID from Sonarr's tvsearch request flows through the entire pipeline to enable accurate series matching at import time:

1. **tvsearch request** — Prowlarr sends `tvdbid=X` in the tvsearch params
2. **dl_url construction** — `tvdbid` added to `/dl?cid=...&tvdbid=X` URL params
3. **/dl endpoint** — embeds `tvdbid` in the announce URL (survives Prowlarr proxy hop)
4. **torrents/add** — extracts `tvdbid` from announce URL, stores in `downloads.json` state
5. **check_completions** — reads `tvdbid` from state, passes to `find_sonarr_series()`
6. **find_sonarr_series(tvdbid=X)** — calls `GET /series?tvdbId=X` directly (no fuzzy matching needed) → falls back to fuzzy name matching only if ID not found

This prevents incorrect series matching for ambiguous show names (e.g., "Coulda Been Love" matching a different show in Sonarr).

## Reindex Behavior (existing sources)

When `torrents/add` receives a grab for a YouTube source that already exists in Pinchflat:
1. `is_in_pinchflat(cid)` returns the existing source_id
2. `reindex_pinchflat_source(source_id)` is called:
   - Marks all current `media_items WHERE media_filepath IS NOT NULL` as `prevent_download=1`
   - Queues a new `MediaCollectionIndexingWorker` Oban job
3. Pinchflat re-indexes the playlist, finds new episodes, queues download only for NEW items (existing are protected)

This prevents re-downloading episodes already in the library when a show gets a new episode.
