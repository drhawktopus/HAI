# YouTube Torznab Pipeline (yt-torznab)

> **Promoted into the library 2026-08-19** from Claude-private memory (`reference_yt_torznab`,
> written 2026-05-22). It lived only in one assistant's memory, where HAI, Codex and the owner could not
> reach it. Content below is unchanged apart from this banner; **status as of the last edit was
> DISABLED-manual-only since 2026-06-08 and has NOT been re-verified since** - treat operational
> details as a starting point, not current state.

> **⚠️ DISABLED 2026-06-08 (manual-only).** the owner turned the automation OFF after it grabbed ~28GB of trash. To reactivate: (1) Prowlarr indexer #18 "YouTube (Last Resort)" `enable=true`; (2) uncomment the `#DISABLED-YT-PIPELINE` `*/30` auto-import cron in <user>'s crontab. Containers (yt-torznab, pinchflat) + the 5 legit Pinchflat sources are left running. The architecture below still describes how it works when re-enabled.

Allows Sonarr to automatically download YouTube-only shows via Pinchflat, using the same search/grab flow as any other indexer. Auto-import monitor handles moving files from staging to the Sonarr library with correct episode naming.

## Pages in this document

- **YouTube Torznab Pipeline - Overview & Configuration** — see [[YouTube-Overview-Configuration]]
- **YouTube Torznab Pipeline - TVDB ID Threading & Reindexing** — see [[YouTube-TVDB-ID-Threading-Reindexing]]
- **YouTube Torznab Pipeline - Auto-Import & Pinchflat Integration** — see [[YouTube-Auto-Import-Pinchflat-Integration]]
- **YouTube Torznab Pipeline - YouTube-Originals Gate** — see [[YouTube-YouTube-Originals-Gate]]
- **YouTube Torznab Pipeline - Troubleshooting & Credentials** — see YouTube-Troubleshooting-Credentials
