# RUNBOOK — Known Quirks & Gotchas



## Known Quirks & Gotchas

### `docker exec orchestrator python -` can start a second Kirk: "RUN RESUMED" and "LIVE claim released at startup" with no restart
Importing `app` in the live container runs Kirk's startup in that process: it takes the claims and windows and resumes every interrupted run beside the real Kirk. Scripts reach it indirectly, because `chat_window` does `from app import ...` when it opens a window, so anything that makes a model call can trigger it. 2026-11-06 19:20: a read-only script did this and drove two live benchmark runs for about 100 s (one tool call ran twice; a model call on Mothership then sat 404 s). The tell in the Notes is those two lines while `docker inspect -f '{{.State.StartedAt}}' orchestrator` has not moved. Until the guard `app._is_the_serving_kirk` is deployed: never make a model call from a script inside the live container. Read or run such a thing in a throwaway container with the gate's redirects (`HAI_NOTES_DIR`, `HAI_STATE_DIR`), or through HAI's own API.

### Disk Space in Arr Apps
Sonarr/Radarr/Lidarr only show local filesystems in the Disk Space page. The CIFS mounts (`/mnt/media`, `/mnt/ssdshared`) don't enumerate there but root folder free space IS correct — check via Settings → Root Folders.

### apt + VPN
No longer an issue — the host-level VPN has been removed, so apt resolves DNS normally:
```bash
sudo apt install <package>
```

### qBittorrent Password Reset
If the container is recreated fresh and loses config, a temp password is generated in the logs. Reset via:
```bash
docker logs qbittorrent 2>&1 | grep -i "temporary password"
# Then set real password via API
```

### Indexer Circuit Breakers

The arr apps trip indexer circuit breakers when searches return errors (even transient ones). Symptoms: "All indexers temporarily unavailable" in health. The health check at 4:30am and noon clears these automatically. To clear manually:
```python
import sqlite3
for path in ['/opt/arrstack/sonarr/sonarr.db','/opt/arrstack/radarr/radarr.db','/opt/arrstack/lidarr/lidarr.db']:
    db=sqlite3.connect(path); db.execute('DELETE FROM IndexerStatus'); db.commit(); db.close()
```
The threshold is hardcoded — not configurable. Root causes addressed: removed dead/duplicate Jackett indexers, wired FlareSolverr to Cloudflare-protected indexers.

### Test All Indexers Spins Slowly

The "Test All" button in Sonarr/Radarr Settings → Indexers will hang briefly on Internet Archive (slow Jackett response, often takes 60–100s through FlareSolverr). This is expected — wait it out. Magnet Cat is similarly slow and may show a red test result despite being functional.

### Lidarr Spotify
Auth token expires hourly, auto-renews on sync. If it fails: Settings → Import Lists → the owner's Spotify Playlists → Authenticate with Spotify.

### Jackett Indexers Directory

Jackett expects configured indexer JSON files at `/config/Jackett/Indexers/` inside the container (i.e. `/opt/arrstack/jackett/Jackett/Indexers/` on the host). If Jackett shows no configured indexers after a restore, check whether the files landed at `/opt/arrstack/jackett/Indexers/` (one level too high) and move them:

```bash
mv /opt/arrstack/jackett/Indexers /opt/arrstack/jackett/Jackett/Indexers
docker restart jackett
```

### JukeBot (ClaudeDiscordBot) Rebuild
```bash
cd /opt/arrstack
docker compose build --no-cache claudediscordbot
docker compose up -d claudediscordbot
```

### Pi-hole Client Visibility
The Netgear RS300 doesn't expose a DHCP DNS field — it hands out only its own IP (<router-address>) and relays DNS itself, **racing/distributing queries across all configured upstream DNS slots** (no strict failover — this is why a non-filtering server in any slot causes ad leaks). All LAN queries appear in Pi-hole as coming from <router-address>; per-device stats are impossible unless:
- Pi-hole takes over DHCP (router's DHCP disabled), OR
- Devices are manually pointed at <pihole-address>

### Bridge Containers → Host-Networked Services

**Historical note:** the old host-level ExpressVPN's Network Lock used to block Docker bridge containers (172.18.0.0/16) from reaching `<sundae-address>` (the host's LAN IP), forcing all inter-container API calls to go via `172.18.0.1` (Docker bridge gateway) instead. The host VPN is now removed, so this restriction **no longer applies** — but the existing `172.18.0.1`-based config below still works fine (Docker's published-port DNAT covers the bridge gateway IP too) and was left as-is rather than churned for no functional gain.

**This does NOT affect browser access** — requests from your PC/laptop come in from outside and are routed differently. Only container-to-container calls are affected.

**Existing config (still works, no need to change):**
- Sonarr/Radarr/Lidarr → qBittorrent: `http://172.18.0.1:8080` (qBittorrent now lives in the `expressvpn` container's netns — `172.18.0.1:8080` still reaches it via published-port DNAT, alternative: `http://expressvpn:8080`)
- Sonarr/Radarr/Lidarr → Prowlarr (indexers): `http://172.18.0.1:9696/{id}/`
- Sonarr/Radarr/Lidarr → Jackett: `http://172.18.0.1:9117/...`
- Prowlarr → Radarr: `http://172.18.0.1:7878`
- Prowlarr → Sonarr: `http://172.18.0.1:8989`
- Prowlarr → Lidarr: `http://172.18.0.1:8686`
- Cleanuparr → qBittorrent: `http://172.18.0.1:8080`
- Cleanuparr → arr apps: `http://172.18.0.1:{port}`

**For new connections:** prefer Docker DNS service names on `arrnet` (e.g. `http://flaresolverr:8191`, `http://expressvpn:8080`) — simpler and no longer dependent on host VPN state. `172.18.0.1` still works as a fallback for any of the existing entries above.

### SundaeStackarr Disk — "No space left on device" during a deploy / image build
- Layout since 2026-10-10 (the owner moved the VG with `pvmove`, then `vgreduce` + `pvremove` of the old PVs): `ubuntu-vg`
  is ONE 200 GB PV (`SundaeStackarr-data.vhdx`, a dynamically growing file on Softserve's `E:`); the root LV is 157 GB,
  so **~43 GB VFree sits unallocated in the VG** (measured 2026-10-23). `sda3` (77 GB, on the boot disk) is the old PV,
  empty, outside the VG. Read the live numbers with `df -h /` + `sudo vgs` + `sudo pvs`, never from this page.
- ⚠ **Extending the LV into that VFree is a HOST decision, not a guest one:** the vhdx grows with every block the guest
  writes, and `E:` (all of SSDShared) had 69.5 GB free on 2026-10-23 with the vhdx at 155 GB. Ask the owner before
  `lvextend`, and say both numbers.
- **The usual cause is image builds, and the fix is cleanup, not space:** 2026-10-23 five orchestrator / Tolkien
  builds in one morning took `/` to 0 free (88 GB of images, 46 GB dangling; 35 GB build cache) and the deploy claim
  could not be written. `sudo docker builder prune -f` + `sudo docker image prune -f` (dangling only) gave back 33 GB
  in seconds with every container running. Run both as the CLEANUP step of a deploy day.
- To add capacity beyond the VG: attach another virtual disk on the Hyper-V side, then
  `pvcreate → vgextend → lvextend -l +100%FREE → resize2fs` — all online, zero downtime. Before
  rationing space, inventory unallocated capacity first (`lsblk` + `vgs`).

### Softserve's sshd: `Connection reset` right after key exchange, nothing in its log, every address (2026-11-05)
- Seen 20:34 CT 2026-11-05, after a day of `fleet_deploy` runs; `ssh softserve` reset on link-local, LAN and loopback alike, while `Get-Service sshd` read Running
  and the OpenSSH log showed nothing past the last good session. Cause NOT found. `fleet_deploy --only softserve` reports `NO REPLY`.
- A Claude session on Softserve cannot restart it: its own token is refused, and ssh-to-self (the usual elevation) is the thing that is broken. Elevated PowerShell at the
  box, `Restart-Service sshd -Force`, cured it.
- Seen again 2026-11-06, between 13:32 CT (an `ssh softserve` that worked) and 13:55 CT (`fleet_deploy` `NO REPLY`), again on a day of heavy ssh from this box by two sessions. Same signs. Cause still NOT found. While it lasts a `fleet_deploy` leaves Softserve on the OLD files while the others take the new, so read each box's verdict before going on to anything that relies on the new files.

### HAI's chat page HANGS the in-app browser tools (find / read_page / screenshot "did not finish rendering")
- **Symptom:** on `hai.lan` / `http://<sundae-address>:8099/` (HAI's chat page) the desktop app's Browser pane tools
  wait forever: `screenshot` fails with *"timed out after 5s: the page did not finish rendering"*, and `find` /
  `read_page` simply never return — **23 minutes on 2026-10-17** until the owner stopped it (*"we've had this issue in the
  lab before"* — the #37 side session's browser check was stopped the same way on 10-16).
- **Cause (most likely):** the page holds live connections open by design — `/query/watch` and `/query/stream` feeds
  with heartbeats, plus the deck refresh — so it never reaches the "finished loading" state those tools wait for.
- **Do instead:** do NOT drive HAI's chat page with the in-app browser. For a layout change: unit tests on
  `app.CHAT_HTML` + `node --check` on the served script (the deploy script does both), then **the owner checks on his
  phone** — he is the phone. For page behaviour, call the API the page calls (`/history`, `/prefs/pin`,
  `/query/watch`) with curl. If a browser check is genuinely needed, ask the owner first and set a short limit on every
  call; a call that has not returned in a minute is hung, not slow.

### HAI job dies with "Mothership failed: timed out" / "CONTEXT COMPACTION failed (ReadTimeout)" / it vanished from its chat
- **Before 2026-10-17 `5d82c73`:** every model call had a 240 s clock (`CHAT_TIMEOUT`), and ollama sends nothing while
  it reads a prompt, so a whole-window re-read on the 3.8 (after a compaction, a park/resume, a failed compaction)
  timed out three times and killed the run — HAI's McCoy-wiring job, 10-16 22:36 CT. The error route also dropped the
  user's message, so the job vanished from its chat without a trace.
- **Now:** a silent call waits while its box's load report says the card is busy; it is ended only as a stall (our
  model loaded, card idle on 2 reports: notes `MODEL CALL STALLED`) or, when the box has NO fresh load report, by the
  old clock (`MODEL CALL CLOCKED` — check the box's "HAI GPU Load Reporter" task first). A run that ends without an
  answer keeps its message in the chat marked `⚠ Not finished.` with the reason. Details:
  [[RUNBOOK-GPU-Reporters-and-Load-Accounting]] § "Kirk's model calls WAIT on this report".

---
