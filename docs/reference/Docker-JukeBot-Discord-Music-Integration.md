# SundaeStackarr - JukeBot Discord Music Integration



## JukeBot (ClaudeDiscordBot)

### Configuration

The `claudediscordbot` container utilizes the following mount points and internal paths:
* **Config**: `/opt/arrstack/claudediscordbot/config.json`, containing tokens and IDs (refer to credentials reference).
* **State** (queue, volume, NP): Mounts host path `/state` into container path `/opt/arrstack/claudediscordbot-state/`.
* **Music**: Host mount `/mnt/media/Music` maps to internal path `/music`.

The bot connects to Lidarr via gateway `http://172.18.0.1:8686`. Audio encoding defaults to 48 kHz stereo, Opus codec at 256 kbps with a 1 MB read-ahead buffer (absorbs SMB hiccups). Default volume level is set to 25. The internal index file resides at `/state/index_cache.json`, holding approximately 14,400 tracks (2026-11-12) and re-indexing every 3600 s.

### Rebuild & restart

To rebuild the `claudediscordbot` container from source without cache and restart it detached:

```bash
cd /opt/arrstack
docker compose build --no-cache claudediscordbot
docker compose up -d claudediscordbot
```

### Commands

This list defines the operational CLI for the media server. All commands apply to the active session.

| Command | Description |
|---------|-------------|
| `/play <query>` | Search and queue a track, artist, or album |
| `/join` | Join current voice channel |
| `/pause` `/resume` `/skip` `/stop` | Playback controls |
| `/volume <0-100>` | Set volume; configuration persists across restarts |
| `/queue` `/clearqueue` `/shuffle` | Queue management operations |
| `/remove <#>` | Remove track from queue by position index |
| `/nowplaying` `/np` | Display currently playing item |
| `/leaveonend` | Toggle auto-disconnect when queue finishes |
| `/radio <artist or genre> [count]` | Queue tracks via fuzzy match on artist/genre (default count: 10) |
| `/random` | Queue one random track |
| `/randomqueue [count]` | Queue N random tracks |
| `/randomqueuealbum <artist> <album>` | Queue an album in random order |
| `/search <query>` | Search library and pick a result to queue |
| `/list_artists` `/list_albums` `/list_tracks` | Browse library contents |
| `/reload_library` | Force full re-scan of media directory |
| `/request album <name>` / `/request track <name>` | Request via Lidarr integration — see `Lidarr-Integration` for configuration details.|

### Persistence
The persistence mechanism persists the following state items:
- The ID of the currently playing message, located at `/state/np_state.json`.
- Full playback context including queue, index, seek position, volume, and voice channel membership. This data is written to `/state/playback_state.json` every 10 seconds.

Upon system restart, these files ensure the bot rejoins the voice channel and resumes operation within approximately 10 seconds.

### Channel auto-leave behavior
The automated departure sequence activates under the following conditions:
**Trigger:** All human participants have departed a specific voice channel.
- The bot waits for 60 seconds before initiating disconnect.
- Upon execution, the bot clears the queue and removes saved state data associated with that session.

### Command lifecycle management
Bot slash-command responses automatically delete themselves after 2 minutes of inactivity or display time.

### Genre library quality constraints
Library genre classification relies on file tagging. Tags are parsed by splitting on `;`, utilizing only the primary genre for playback categorization.
To update this mapping, run `/reload_library` immediately after adding new music files to the collection.

### `/radio` for a genre the library does not have: automatic fetch (radio fetch)

Added 2026-11-12. When `/radio <genre>` matches nothing in the library and the word is on
MusicBrainz's own genre list, the bot downloads a starter set and builds the station itself:

1. **Gate:** `mbIsGenre` checks the query (spaced, hyphenated and joined forms) against
   `musicbrainz.org/ws/2/genre/all`. A misspelt artist is not a genre, so it cannot start downloads;
   it gets the usual "No match" reply with the genre list.
2. **Pick:** `mbGenreAlbums` searches official albums tagged with the genre, drops compilations and
   live sets, ranks by tag votes then by number of releases, and keeps one album per artist.
   MusicBrainz has no popularity score, so the picks can be obscure.
3. **Request:** up to 5 albums (`RADIO_FETCH_ALBUMS`), from at most 15 candidates, go to Lidarr the
   same way `/request` does (artist added unmonitored, the one album monitored and searched).
4. **Arrival:** every 5 minutes the bot asks Lidarr whether each album has all its track files. When
   one lands it re-indexes and posts in the channel the request came from. After a day with nothing
   it posts once which albums Lidarr found no download for; Lidarr keeps looking.
5. **Play:** `/radio <genre>` plays the arrived tracks from then on. The bot keeps its own map of
   genre to exact track files in `/state/radio_fetch.json`, because the files' genre tags rarely
   repeat the word typed. Delete a genre's entry from that file to make the bot fetch it afresh.

Lidarr lookups from the bot wait up to 60 s (they call Lidarr's metadata service); every other
Lidarr call keeps the 5 s LAN timeout. Test: `test_radio_fetch.js` beside the bot source, run in a
throwaway container of the bot image on `arrstack_arrnet`; it only reads.

### Artist resolution logic
The command interface supports fuzzy matching for artist queries (e.g., entering "kendrick" resolves to Kendrick Lamar). Users should provide specific names when fuzzy results are insufficient or fallback genre searches occur.

### `/radio` plays the wrong artist for a genre the library does not have (e.g. "lo fi" → Flo Rida)

`/radio` tries an exact genre, then a fuzzy artist match, then a fuzzy genre, then replies "No match" with the genre list. The artist match is accepted only when its Fuse score is **below 0.24** (`cmdRadio` in `index.js`). The value is measured against the library's 495 artists (2026-11-12): real misspellings score 0.16–0.23 ("kendrik lamar", "the beetles", "smashing pumkins", "lindsay sterling"), while wrong matches score 0.25 and up ("lofi" → Logic 0.25, "jazz" → Bazzi 0.25, "lo fi" → Flo Rida 0.32, "chill" → Childish Gambino 0.32). At the old 0.45 a genre the library lacks played the nearest-spelled artist. The library has no lo-fi genre or artist; request albums to get one.

### Getting a song in when Lidarr says "Unable to parse release" (singles, J-pop, tracker-style names)

Lidarr's metadata profile hides Singles, and releases named in Japanese-tracker style are all rejected
with `Unable to parse release`. Interactive grab of such a release returns **404** (no matched
artist/album), so Lidarr cannot fetch it. Working path (2026-10-08, ALI feat. AKLO "Lost in Paradise"):

Lidarr keeps ALI as artist 1643 with the single as a monitored album; it will not import this copy.

### Lidarr Spotify integration status
**Token lifecycle:** The authentication token expires hourly and is automatically renewed. If renewal fails, users must re-authenticate via Settings → Import Lists.
**Failure diagnosis:** A `/request` response showing "Lidarr is not configured" indicates the startup `validate()` function failed. To investigate, check system logs using:
```
docker logs claudediscordbot | grep -i lidarr
```
