# YouTube Torznab Pipeline - YouTube-Originals Gate



## YouTube-Originals Gate (added 2026-05-30)

The adapter answers tvsearch ONLY for genuine YouTube shows — otherwise it would grab
*any* show from YouTube whenever no real release was momentarily available. In `do_GET`'s
tvsearch branch, when `tvdbid` is present it fetches the Sonarr series once (`sonarr_get`)
and allows iff:

```
"youtube" in (series.network or "").lower()         # primary, automatic
  OR ( network is blank/null AND 13 in series.tags )  # tag-13 'youtube-source' override, null-net only
```

Otherwise → `build_search_xml([])` + return (empty result, no grab; also skips the YT API
search = saves 100 quota units). **Fail-closed**: tvdbid present but Sonarr lookup empty →
deny. A real network (Tokyo MX, SYFY…) always denies even if stray-tagged 13 — the tag only
rescues the ambiguous null-network case (e.g. Coulda Been Love, tvdb 460486). network="YouTube"
covers ADC/Helluva/Guinevere automatically, so Overseerr-added requests Just Work with no tag.

- **Null-network + untagged** → deny **and** `notify_discord()` ping so admin can tag it in
  Sonarr if it's a real YT original. `notify_discord` posts native `{"content":…}` to the
  Watchtower webhook **base** URL — strip the `/slack` suffix AND set a non-default
  `User-Agent` header or Cloudflare returns **403**. URL via `DISCORD_WEBHOOK_URL` env on the
  yt-torznab compose service (defaults to the Watchtower hook).
- One tvsearch gate covers the whole pipeline (no second gate at `/torrents/add` needed): if
  no release is offered, Sonarr never grabs, so no Pinchflat source is created.
- **Rejected approach:** restricting Sonarr indexer #24 to tag 13 — would block freshly
  Overseerr-added originals before anyone tags them.

## ⚠️ OPEN PROJECT: Pinchflat gate bypass (2026-06-01)

**Status: Pinchflat stopped 2026-06-01 pending investigation. Do NOT restart until gate is fixed.**

**Problem:** Despite the yt-torznab YouTube-originals gate (added 2026-05-30), Pinchflat accumulated 7 bogus sources (43–49) containing 351 files of clips, mashups, and commentary videos for non-YouTube-native shows: Henry Danger, Mentes Criminales (Criminal Minds clips in Spanish), Deadliest Warrior (YouTube commentary), colgados en filadelfia (It's Always Sunny clips), High Maintenance web shorts, American Dad TBS mashups, Kaiju No. 8 S2. All deleted 2026-06-01.

**Root cause unknown.** The gate should block all non-YouTube-network shows — but these sources were created. Possibilities:
1. Sources created before the gate was added (2026-05-30) and weren't cleaned up
2. Gate has a bug allowing certain patterns through (null network + no tag 13 slipping past)
3. Sources added directly via Pinchflat web UI bypassing the yt-torznab gate entirely

**Legit remaining sources (IDs 3, 8, 10, 11, 39):** Helluva Boss ×2, Amazing Digital Circus, Knights of Guinevere, Coulda Been Love — all confirmed YouTube-native.

**To investigate at resume:**
- Check server.py gate logic for edge cases (null network handling, tag lookup errors)
- Check how sources 43-49 were created — yt-torznab grab vs direct Pinchflat UI
- Consider adding a Pinchflat allowlist (only permit sources from known YouTube-network tvdbids)
- Consider auditing sonarr for any remaining series with tag 13 that shouldn't have it
