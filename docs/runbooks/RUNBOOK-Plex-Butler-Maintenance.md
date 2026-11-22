# RUNBOOK — Plex Butler Maintenance Window



## Plex Butler maintenance window (thumbnails / markers / loudness)

**Symptom:** Softserve pegged ~90-100% CPU midday, fans/disk loud, **no active Plex plays**, and
`homepage.lan` sluggish/failing — because host CPU saturation starves the **SundaeStackarr VM** (arr stack +
homepage live inside it). Culprit was **Plex Butler running scheduled maintenance in the middle of the day.**

**Root cause shape:** the Butler *window* drifts to daytime (`ButlerStartHour=12`, `ButlerEndHour=17`) instead
of overnight. The Generate* tasks are `scheduled` (not on-media-add) — they run inside whatever window is set.
It is a **once-daily window, NOT a frequency** (the "12" is just a noon start hour;
`ScheduledLibraryUpdateInterval` is separately `86400`=daily).

**Fix (live, via Plex API — no restart, reversible):** token is in the registry, not Preferences.xml on Windows:
```powershell
$tok = (Get-ItemProperty 'HKCU:\Software\Plex, Inc.\Plex Media Server').PlexOnlineToken
Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:32400/:/prefs?ButlerStartHour=2&ButlerEndHour=6&X-Plex-Token=$tok"
```
Set to **2–6 AM**. Read current settings: `GET http://127.0.0.1:32400/:/prefs?X-Plex-Token=$tok` (look for
`ButlerStartHour`/`ButlerEndHour`, and `GenerateBIFBehavior` etc. = `scheduled`). Changing the window mid-run doesn't
kill in-flight transcoders instantly, but Plex stands down once outside the window (CPU dropped 97%→7% within minutes).
If it ever drifts back to a daytime window, this is where to look (a stray click in Plex → Settings → Scheduled Tasks).
