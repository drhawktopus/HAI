"""HAI's media add-on: lookups, queues, releases, imports, requests, quality profiles and custom formats for the user's
own Sonarr, Radarr, Lidarr, Prowlarr, qBittorrent and Seerr instances, through their own APIs (arrclient, qbitclient,
seerrclient). Every change is a preview first: it returns a pending action the user confirms.

Part of the media add-on (tools_ext_media plugs these into HAI's tools). It reads nothing from media_tools, which keeps
the file probe and the Plex tools."""
import json
import os

import arrclient
import arr_config
import hostops
import httpx
import pending_actions
import qbitclient
import seerrclient


def _client(base_url, api_key):
    return arrclient.ArrClient(base_url, api_key, timeout=arr_config.ARR_TIMEOUT)


def _format_series(series):
    title = series.get("title") or series.get("sortTitle") or "?"
    year = series.get("year") or "?"
    monitored = "monitored" if series.get("monitored") else "unmonitored"
    path = series.get("path") or "no path"
    stats = series.get("statistics") or {}
    count = stats.get("episodeFileCount")
    total = stats.get("episodeCount")
    if count is not None and total is not None:
        eps = f"{count}/{total} episodes"
    else:
        eps = "episode count unknown"
    return f"{title} ({year}) — id {series.get('id', '?')}, {monitored}, {eps}, {path}"


def sonarr_lookup(term, limit=8):
    term = (term or "").strip()
    if not term:
        return "sonarr_lookup needs a search term."
    try:
        client = _client(arr_config.SONARR_URL, arr_config.SONARR_APIKEY)
        results = client.series()   # Sonarr's /series ignores ?term=; it returns the whole library, so filter here
    except arrclient.ArrError as exc:
        return f"Sonarr lookup failed: {exc}"
    tl = term.lower()
    matched = [s for s in (results or [])
               if tl in (s.get("title") or "").lower() or tl in (s.get("sortTitle") or "").lower()]
    if not matched:
        return f"No Sonarr series in the library matched {term!r}."
    lines = [_format_series(s) for s in matched[: max(1, min(int(limit or 8), 20))]]
    return "Sonarr matches:\n" + "\n".join(lines)


def sonarr_queue(limit=10):
    try:
        client = _client(arr_config.SONARR_URL, arr_config.SONARR_APIKEY)
        data = client.queue()
    except arrclient.ArrError as exc:
        return f"Sonarr queue lookup failed: {exc}"
    records = data.get("records", data if isinstance(data, list) else []) if data else []
    if not records:
        return "Sonarr queue is empty."
    out = []
    for item in records[: max(1, min(int(limit or 10), 25))]:
        title = item.get("title") or (item.get("series") or {}).get("title") or "?"
        status = item.get("status") or item.get("trackedDownloadStatus") or "?"
        size = item.get("sizeleft") or item.get("size") or 0
        out.append(f"{title} — {status}, size/left={size}")
    return "Sonarr queue:\n" + "\n".join(out)


def prowlarr_search(query, limit=8, categories=None):
    query = (query or "").strip()
    if not query:
        return "prowlarr_search needs a query."
    params = {"query": query}
    if categories:
        params["categories"] = categories
    try:
        client = _client(arr_config.PROWLARR_URL, arr_config.PROWLARR_APIKEY)
        results = client.get("search", params=params)
    except arrclient.ArrError as exc:
        return f"Prowlarr search failed: {exc}"
    if not results:
        return f"No Prowlarr releases matched {query!r}."
    lines = []
    for r in results[: max(1, min(int(limit or 8), 20))]:
        title = r.get("title") or r.get("guid") or "?"
        indexer = r.get("indexer") or r.get("indexerId") or "?"
        seeders = r.get("seeders", "?")
        size = r.get("size", 0)
        protocol = r.get("protocol", "?")
        lines.append(f"{title} — {indexer}, {protocol}, seeders={seeders}, size={size}")
    return "Prowlarr releases:\n" + "\n".join(lines)


def json_debug(value):
    return json.dumps(value, indent=2, sort_keys=True)


def _int(value, label):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be an integer")


def _sonarr_search_command(scope, series_id=None, season_number=None, episode_ids=None):
    scope = (scope or "").strip().lower()
    if scope == "series":
        sid = _int(series_id, "series_id")
        return "SeriesSearch", {"seriesId": sid}, f"Trigger Sonarr series search for series id {sid}."
    if scope == "season":
        sid = _int(series_id, "series_id")
        season = _int(season_number, "season_number")
        return "SeasonSearch", {"seriesId": sid, "seasonNumber": season}, f"Trigger Sonarr season search for series id {sid}, season {season}."
    if scope in {"episode", "episodes"}:
        ids = episode_ids or []
        if isinstance(ids, str):
            ids = [x.strip() for x in ids.split(",") if x.strip()]
        ids = [_int(x, "episode_ids") for x in ids]
        if not ids:
            raise ValueError("episode_ids is required for episode search")
        return "EpisodeSearch", {"episodeIds": ids}, f"Trigger Sonarr episode search for episode ids {ids}."
    raise ValueError("scope must be series, season, or episodes")


def sonarr_search_preview(scope, series_id=None, season_number=None, episode_ids=None, user=None):
    """Register a confirm-gated Sonarr search command. Does not execute on preview."""
    if not arr_config.SONARR_APIKEY:
        return {"status": "error", "message": "Sonarr API key is not configured for write-capable tools."}
    try:
        command, payload, preview = _sonarr_search_command(scope, series_id, season_number, episode_ids)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    def _execute():
        client = _client(arr_config.SONARR_URL, arr_config.SONARR_APIKEY)
        return client.command(command, payload, wait=False)

    return pending_actions.register(
        "sonarr.search",
        preview + f"\nCommand: {command}\nPayload: {json_debug(payload)}",
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "sonarr", "command": command, "payload": payload},
    )


def _format_release(r):
    title = r.get("title") or "?"
    quality = (((r.get("quality") or {}).get("quality")) or {}).get("name") or "?"
    langs = ", ".join(l.get("name", "?") for l in (r.get("languages") or [])) or "?"
    seeders = r.get("seeders", "?")
    size = r.get("size", 0)
    flag = " [REJECTED: " + "; ".join(r.get("rejections") or [])[:120] + "]" if r.get("rejected") else ""
    return (f"{title}{flag}\n    quality={quality}, languages={langs}, seeders={seeders}, "
            f"size={size}, indexerId={r.get('indexerId')}, guid={r.get('guid')}")


def sonarr_releases(series_id, season_number=None, episode_id=None, limit=10):
    """Read-only interactive release search — lists the actual grabbable releases (with languages, so the
    user can spot dual-audio) plus the guid + indexerId needed to grab one. Downloads nothing."""
    if not arr_config.SONARR_APIKEY:
        return "Sonarr API key is not configured."
    try:
        params = {"seriesId": _int(series_id, "series_id")}
        if season_number is not None:
            params["seasonNumber"] = _int(season_number, "season_number")
        if episode_id is not None:
            params["episodeId"] = _int(episode_id, "episode_id")
    except ValueError as exc:
        return str(exc)
    try:
        client = _client(arr_config.SONARR_URL, arr_config.SONARR_APIKEY)
        results = client.get("release", params=params)
    except arrclient.ArrError as exc:
        return f"Sonarr release search failed: {exc}"
    if not results:
        return "No releases found for that series/season."
    results = sorted(results, key=lambda r: (bool(r.get("rejected")), -(r.get("seeders") or 0)))
    lines = [_format_release(r) for r in results[: max(1, min(int(limit or 10), 20))]]
    return ("Sonarr interactive releases (pick one and call sonarr_grab with its guid + indexerId):\n"
            + "\n".join(lines))


def sonarr_grab_preview(guid, indexer_id, title=None, user=None):
    """Register a confirm-gated grab of ONE specific Sonarr release (POST /release → download client).
    Additive (adds a download); never touches existing torrents."""
    if not arr_config.SONARR_APIKEY:
        return {"status": "error", "message": "Sonarr API key is not configured for write-capable tools."}
    guid = (guid or "").strip()
    if not guid:
        return {"status": "error", "message": "guid is required (from sonarr_releases)."}
    try:
        iid = _int(indexer_id, "indexer_id")
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    payload = {"guid": guid, "indexerId": iid}
    preview = (f"Sonarr will GRAB this release and send it to the download client (adds a new download):\n"
               f"  {(title or guid)[:140]}\n  indexerId={iid}\n  guid={guid}")

    def _execute():
        client = _client(arr_config.SONARR_URL, arr_config.SONARR_APIKEY)
        return client.post("release", payload)

    return pending_actions.register(
        "sonarr.grab",
        preview,
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "sonarr", "action": "grab", "guid": guid, "indexerId": iid},
    )


def _manualimport_items(folder=None, download_id=None, series_id=None):
    params = {"filterExistingFiles": "true"}
    if folder:
        params["folder"] = folder
    if download_id:
        params["downloadId"] = download_id
    if series_id is not None:
        params["seriesId"] = series_id
    return _client(arr_config.SONARR_URL, arr_config.SONARR_APIKEY).get("manualimport", params=params) or []


def _ep_labels(item):
    eps = item.get("episodes") or []
    return ",".join(f"S{e.get('seasonNumber')}E{e.get('episodeNumber')}" for e in eps)


def sonarr_import_candidates(folder=None, download_id=None, series_id=None, limit=15):
    """Read-only: list the files Sonarr could manually import from a folder or download, with their proposed
    episode mapping, quality, languages, and any rejections. Imports nothing."""
    if not arr_config.SONARR_APIKEY:
        return "Sonarr API key is not configured."
    if not (folder or download_id):
        return "sonarr_import_candidates needs a folder or download_id."
    try:
        items = _manualimport_items(folder, download_id, series_id)
    except arrclient.ArrError as exc:
        return f"Sonarr manual-import lookup failed: {exc}"
    if not items:
        return "No import candidates found (folder empty, or everything already imported)."
    importable = sum(1 for it in items if (it.get("episodes") and not it.get("rejections")))
    lines = [f"Sonarr import candidates: {len(items)} file(s), {importable} importable (mapped to an episode, no rejections)."]
    for it in items[: max(1, min(int(limit or 15), 40))]:
        q = (((it.get("quality") or {}).get("quality")) or {}).get("name") or "?"
        langs = ",".join(l.get("name", "?") for l in (it.get("languages") or [])) or "?"
        rej = "; ".join(r.get("reason", "") for r in (it.get("rejections") or []))
        flag = f"  [REJECTED: {rej[:80]}]" if rej else ""
        lines.append(f"  - {(it.get('relativePath') or '?')[-90:]} -> {_ep_labels(it) or 'UNMAPPED'} [{q}, {langs}]{flag}")
    return "\n".join(lines)


def sonarr_manual_import_preview(folder=None, download_id=None, series_id=None, import_mode="move",
                                 include_rejected=False, user=None):
    """Confirm-gated manual import. Rebuilds the candidate list server-side and imports the files that are
    mapped to an episode (skipping rejected ones unless include_rejected). The model only points at a
    folder/download; we assemble the ManualImport payload here so it's correct."""
    if not arr_config.SONARR_APIKEY:
        return {"status": "error", "message": "Sonarr API key is not configured for write-capable tools."}
    if not (folder or download_id):
        return {"status": "error", "message": "folder or download_id is required."}
    import_mode = (import_mode or "move").strip().lower()
    if import_mode not in ("move", "copy"):
        return {"status": "error", "message": "import_mode must be 'move' or 'copy'."}
    try:
        items = _manualimport_items(folder, download_id, series_id)
    except arrclient.ArrError as exc:
        return {"status": "error", "message": f"manual-import lookup failed: {exc}"}
    files, skipped = [], 0
    for it in items:
        eps = it.get("episodes") or []
        if not eps or (it.get("rejections") and not include_rejected):
            skipped += 1
            continue
        files.append({
            "path": it.get("path"),
            "seriesId": (it.get("series") or {}).get("id") or series_id,
            "episodeIds": [e.get("id") for e in eps],
            "quality": it.get("quality"),
            "languages": it.get("languages"),
            "releaseGroup": it.get("releaseGroup"),
            "indexerFlags": it.get("indexerFlags") or 0,
            "releaseType": it.get("releaseType"),
        })
    if not files:
        return {"status": "error", "message": f"No importable files (need an episode mapping + no rejections). {skipped} skipped."}
    sample = "\n".join(f"  - {os.path.basename(f['path'] or '?')} -> episode ids {f['episodeIds']}" for f in files[:8])
    preview = (f"Sonarr will MANUAL-IMPORT {len(files)} file(s) into the library (mode={import_mode}; "
               f"{skipped} skipped: unmapped/rejected).\nSample:\n{sample}"
               + ("\n  ...(more)" if len(files) > 8 else ""))

    def _execute():
        return _client(arr_config.SONARR_URL, arr_config.SONARR_APIKEY).command(
            "ManualImport", {"importMode": import_mode, "files": files}, wait=False)

    return pending_actions.register(
        "sonarr.manual_import",
        preview,
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "sonarr", "action": "manual_import", "count": len(files), "import_mode": import_mode},
    )


# --- Radarr (movies) — mirrors the Sonarr pipeline above; uses /movie + MoviesSearch + a single movie
# mapping per import (vs Sonarr's per-episode mapping). Same admin-gating + confirm-on-write via pending_actions.
def _format_movie(movie):
    title = movie.get("title") or movie.get("sortTitle") or "?"
    year = movie.get("year") or "?"
    monitored = "monitored" if movie.get("monitored") else "unmonitored"
    path = movie.get("path") or "no path"
    have = "downloaded" if movie.get("hasFile") else "missing"
    return f"{title} ({year}) — id {movie.get('id', '?')}, {monitored}, {have}, {path}"


def radarr_lookup(term, limit=8):
    term = (term or "").strip()
    if not term:
        return "radarr_lookup needs a search term."
    if not arr_config.RADARR_APIKEY:
        return "Radarr API key is not configured."
    try:
        client = _client(arr_config.RADARR_URL, arr_config.RADARR_APIKEY)
        results = client.movies()   # Radarr's /movie ignores ?term=; returns the whole library, so filter here
    except arrclient.ArrError as exc:
        return f"Radarr lookup failed: {exc}"
    tl = term.lower()
    matched = [m for m in (results or [])
               if tl in (m.get("title") or "").lower() or tl in (m.get("sortTitle") or "").lower()]
    if not matched:
        return f"No Radarr movie in the library matched {term!r}."
    lines = [_format_movie(m) for m in matched[: max(1, min(int(limit or 8), 20))]]
    return "Radarr matches:\n" + "\n".join(lines)


def radarr_releases(movie_id, limit=10):
    """Read-only interactive release search for a movie — lists grabbable releases (with languages, so the
    user can spot dual-audio) plus the guid + indexerId needed to grab one. Downloads nothing."""
    if not arr_config.RADARR_APIKEY:
        return "Radarr API key is not configured."
    try:
        params = {"movieId": _int(movie_id, "movie_id")}
    except ValueError as exc:
        return str(exc)
    try:
        client = _client(arr_config.RADARR_URL, arr_config.RADARR_APIKEY)
        results = client.get("release", params=params)
    except arrclient.ArrError as exc:
        return f"Radarr release search failed: {exc}"
    if not results:
        return "No releases found for that movie."
    results = sorted(results, key=lambda r: (bool(r.get("rejected")), -(r.get("seeders") or 0)))
    lines = [_format_release(r) for r in results[: max(1, min(int(limit or 10), 20))]]
    return ("Radarr interactive releases (pick one and call radarr_grab with its guid + indexerId):\n"
            + "\n".join(lines))


def radarr_grab_preview(guid, indexer_id, title=None, user=None):
    """Register a confirm-gated grab of ONE specific Radarr release (POST /release → download client).
    Additive (adds a download); never touches existing torrents."""
    if not arr_config.RADARR_APIKEY:
        return {"status": "error", "message": "Radarr API key is not configured for write-capable tools."}
    guid = (guid or "").strip()
    if not guid:
        return {"status": "error", "message": "guid is required (from radarr_releases)."}
    try:
        iid = _int(indexer_id, "indexer_id")
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    payload = {"guid": guid, "indexerId": iid}
    preview = (f"Radarr will GRAB this release and send it to the download client (adds a new download):\n"
               f"  {(title or guid)[:140]}\n  indexerId={iid}\n  guid={guid}")

    def _execute():
        client = _client(arr_config.RADARR_URL, arr_config.RADARR_APIKEY)
        return client.post("release", payload)

    return pending_actions.register(
        "radarr.grab",
        preview,
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "radarr", "action": "grab", "guid": guid, "indexerId": iid},
    )


def radarr_search_preview(movie_id, user=None):
    """Register a confirm-gated Radarr MoviesSearch (automatic search for a monitored movie). Does not
    execute on preview."""
    if not arr_config.RADARR_APIKEY:
        return {"status": "error", "message": "Radarr API key is not configured for write-capable tools."}
    try:
        mid = _int(movie_id, "movie_id")
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    payload = {"movieIds": [mid]}
    preview = f"Trigger Radarr automatic search for movie id {mid}.\nCommand: MoviesSearch\nPayload: {json_debug(payload)}"

    def _execute():
        client = _client(arr_config.RADARR_URL, arr_config.RADARR_APIKEY)
        return client.command("MoviesSearch", payload, wait=False)

    return pending_actions.register(
        "radarr.search",
        preview,
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "radarr", "command": "MoviesSearch", "payload": payload},
    )


def _radarr_manualimport_items(folder=None, download_id=None, movie_id=None):
    params = {"filterExistingFiles": "true"}
    if folder:
        params["folder"] = folder
    if download_id:
        params["downloadId"] = download_id
    if movie_id is not None:
        params["movieId"] = movie_id
    return _client(arr_config.RADARR_URL, arr_config.RADARR_APIKEY).get("manualimport", params=params) or []


def radarr_import_candidates(folder=None, download_id=None, movie_id=None, limit=15):
    """Read-only: list the files Radarr could manually import from a folder or download, with the proposed
    movie mapping, quality, languages, and any rejections. Imports nothing."""
    if not arr_config.RADARR_APIKEY:
        return "Radarr API key is not configured."
    if not (folder or download_id):
        return "radarr_import_candidates needs a folder or download_id."
    try:
        items = _radarr_manualimport_items(folder, download_id, movie_id)
    except arrclient.ArrError as exc:
        return f"Radarr manual-import lookup failed: {exc}"
    if not items:
        return "No import candidates found (folder empty, or everything already imported)."
    importable = sum(1 for it in items if (it.get("movie") and not it.get("rejections")))
    lines = [f"Radarr import candidates: {len(items)} file(s), {importable} importable (mapped to a movie, no rejections)."]
    for it in items[: max(1, min(int(limit or 15), 40))]:
        q = (((it.get("quality") or {}).get("quality")) or {}).get("name") or "?"
        langs = ",".join(l.get("name", "?") for l in (it.get("languages") or [])) or "?"
        rej = "; ".join(r.get("reason", "") for r in (it.get("rejections") or []))
        flag = f"  [REJECTED: {rej[:80]}]" if rej else ""
        mv = (it.get("movie") or {}).get("title") or "UNMAPPED"
        lines.append(f"  - {(it.get('relativePath') or '?')[-90:]} -> {mv} [{q}, {langs}]{flag}")
    return "\n".join(lines)


def radarr_manual_import_preview(folder=None, download_id=None, movie_id=None, import_mode="move",
                                 include_rejected=False, user=None):
    """Confirm-gated manual import for Radarr. Rebuilds the candidate list server-side and imports files
    mapped to a movie (skipping rejected ones unless include_rejected). The model only points at a
    folder/download; we assemble the ManualImport payload here."""
    if not arr_config.RADARR_APIKEY:
        return {"status": "error", "message": "Radarr API key is not configured for write-capable tools."}
    if not (folder or download_id):
        return {"status": "error", "message": "folder or download_id is required."}
    import_mode = (import_mode or "move").strip().lower()
    if import_mode not in ("move", "copy"):
        return {"status": "error", "message": "import_mode must be 'move' or 'copy'."}
    try:
        items = _radarr_manualimport_items(folder, download_id, movie_id)
    except arrclient.ArrError as exc:
        return {"status": "error", "message": f"manual-import lookup failed: {exc}"}
    files, skipped = [], 0
    for it in items:
        movie = it.get("movie") or {}
        if not movie or (it.get("rejections") and not include_rejected):
            skipped += 1
            continue
        files.append({
            "path": it.get("path"),
            "movieId": movie.get("id") or movie_id,
            "quality": it.get("quality"),
            "languages": it.get("languages"),
            "releaseGroup": it.get("releaseGroup"),
            "indexerFlags": it.get("indexerFlags") or 0,
            "releaseType": it.get("releaseType"),
        })
    if not files:
        return {"status": "error", "message": f"No importable files (need a movie mapping + no rejections). {skipped} skipped."}
    sample = "\n".join(f"  - {os.path.basename(f['path'] or '?')} -> movie id {f['movieId']}" for f in files[:8])
    preview = (f"Radarr will MANUAL-IMPORT {len(files)} file(s) into the library (mode={import_mode}; "
               f"{skipped} skipped: unmapped/rejected).\nSample:\n{sample}"
               + ("\n  ...(more)" if len(files) > 8 else ""))

    def _execute():
        return _client(arr_config.RADARR_URL, arr_config.RADARR_APIKEY).command(
            "ManualImport", {"importMode": import_mode, "files": files}, wait=False)

    return pending_actions.register(
        "radarr.manual_import",
        preview,
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "radarr", "action": "manual_import", "count": len(files), "import_mode": import_mode},
    )


# --- Seerr (Overseerr) — the request surface friends already use. Reads are open; approve/decline/add are
# admin-gated + confirm-on-write via pending_actions. Titles are looked up best-effort so a confirm is human.
def _seerr_client():
    return seerrclient.SeerrClient(arr_config.SEERR_URL, arr_config.SEERR_APIKEY, timeout=arr_config.SEERR_TIMEOUT)


def _seerr_title(client, media):
    """Best-effort human title for a request's media (fail-soft → ''). Overseerr request payloads carry
    tmdbId + type but not the title, so look it up for the displayed page only."""
    if not isinstance(media, dict):
        return ""
    tmdb = media.get("tmdbId")
    mtype = media.get("mediaType")
    if not tmdb or mtype not in ("movie", "tv"):
        return ""
    try:
        detail = client.get(f"{mtype}/{int(tmdb)}")
    except seerrclient.SeerrError:
        return ""
    return (detail or {}).get("title") or (detail or {}).get("name") or ""


def _requested_by(req):
    by = req.get("requestedBy") or {}
    return by.get("displayName") or by.get("username") or by.get("plexUsername") or "?"


def seerr_requests(filter="all", limit=10):
    """Read-only. List Overseerr requests (id, type, title, who requested, request + availability status).
    filter: all | pending | approved | available | processing | unavailable. Use to find a request id."""
    if not arr_config.SEERR_APIKEY:
        return "Seerr (Overseerr) API key is not configured."
    take = max(1, min(int(limit or 10), 25))
    try:
        client = _seerr_client()
        data = client.list_requests(filter=(filter or "all"), take=take)
    except seerrclient.SeerrError as exc:
        return f"Seerr request lookup failed: {exc}"
    results = (data or {}).get("results", data if isinstance(data, list) else []) or []
    if not results:
        return f"No Seerr requests matched filter {filter!r}."
    lines = []
    for r in results[:take]:
        media = r.get("media") or {}
        title = _seerr_title(client, media) or f"tmdbId {media.get('tmdbId', '?')}"
        rstat = seerrclient.REQUEST_STATUS.get(r.get("status"), str(r.get("status")))
        mstat = seerrclient.MEDIA_STATUS.get(media.get("status"), "")
        mtype = media.get("mediaType") or r.get("type") or "?"
        lines.append(f"#{r.get('id')} [{mtype}] {title} — requested by {_requested_by(r)}, "
                     f"request={rstat}" + (f", media={mstat}" if mstat else ""))
    return "Seerr requests:\n" + "\n".join(lines)


def _seerr_request_preview(request_id, verb, user=None):
    if not arr_config.SEERR_APIKEY:
        return {"status": "error", "message": "Seerr (Overseerr) API key is not configured for write tools."}
    try:
        rid = _int(request_id, "request_id")
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    # Look up the request so the preview names what's being acted on.
    title = ""
    try:
        client = _seerr_client()
        req = client.get_request(rid)
        title = _seerr_title(client, (req or {}).get("media") or {})
    except seerrclient.SeerrError:
        req = None
    if req is None:
        return {"status": "error", "message": f"No Seerr request #{rid} found (nothing to {verb})."}
    label = title or f"tmdbId {((req.get('media') or {}).get('tmdbId'))}"
    preview = f"Seerr will {verb.upper()} request #{rid}: {label} (requested by {_requested_by(req)})."

    def _execute():
        c = _seerr_client()
        return c.approve(rid) if verb == "approve" else c.decline(rid)

    return pending_actions.register(
        "seerr." + verb,
        preview,
        _execute,
        user=user,
        destructive=(verb == "decline"),
        metadata={"service": "seerr", "action": verb, "request_id": rid},
    )


def seerr_approve_preview(request_id, user=None):
    return _seerr_request_preview(request_id, "approve", user=user)


def seerr_decline_preview(request_id, user=None):
    return _seerr_request_preview(request_id, "decline", user=user)


def seerr_add_preview(media_type, media_id, seasons=None, user=None):
    """Confirm-gated: add a new Overseerr request for a movie or tv show by TMDB id (admin shortcut)."""
    if not arr_config.SEERR_APIKEY:
        return {"status": "error", "message": "Seerr (Overseerr) API key is not configured for write tools."}
    media_type = (media_type or "").strip().lower()
    if media_type not in ("movie", "tv"):
        return {"status": "error", "message": "media_type must be 'movie' or 'tv'."}
    try:
        mid = _int(media_id, "media_id")
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    if isinstance(seasons, str):
        seasons = [s.strip() for s in seasons.split(",") if s.strip()]
    if seasons:
        try:
            seasons = [_int(s, "seasons") for s in seasons]
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
    title = ""
    try:
        client = _seerr_client()
        title = _seerr_title(client, {"tmdbId": mid, "mediaType": media_type})
    except seerrclient.SeerrError:
        pass
    label = title or f"tmdbId {mid}"
    preview = (f"Seerr will ADD a {media_type} request: {label}"
               + (f" (seasons {seasons})" if seasons else "") + ".")

    def _execute():
        return _seerr_client().add_request(media_type, mid, seasons=seasons)

    return pending_actions.register(
        "seerr.add",
        preview,
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "seerr", "action": "add", "media_type": media_type, "media_id": mid, "seasons": seasons},
    )


# --- Phase 3: quality-profile + custom-format management (Sonarr/Radarr — they share the v3 API). This is the
# "make dual-audio STICK" part: define a custom format that matches release titles by regex, then give it a
# score in a quality profile so those releases are preferred. Lidarr is intentionally excluded (API v1, different
# shape). All writes are admin-gated + confirm-on-write.
_ARR_SERVICES = {"sonarr": ("SONARR_URL", "SONARR_APIKEY"), "radarr": ("RADARR_URL", "RADARR_APIKEY")}


def _arr_service(service):
    s = (service or "").strip().lower()
    spec = _ARR_SERVICES.get(s)
    if not spec:
        raise ValueError("service must be 'sonarr' or 'radarr'.")
    key = getattr(arr_config, spec[1])
    if not key:
        raise ValueError(f"{s} API key is not configured.")
    return s, getattr(arr_config, spec[0]), key


def _spec_summary(sp):
    impl = (sp.get("implementation") or "?").replace("Specification", "")
    val = next((f.get("value") for f in (sp.get("fields") or []) if f.get("name") == "value"), "")
    neg = "NOT " if sp.get("negate") else ""
    return f"{neg}{impl}({val})" if val != "" else f"{neg}{impl}"


def quality_profiles(service, limit=10):
    """Read-only. List a service's quality profiles (id, name, cutoff) and any custom-format scores set in
    each — use to find the profile id + see what's already weighted."""
    try:
        s, url, key = _arr_service(service)
    except ValueError as exc:
        return str(exc)
    try:
        profs = _client(url, key).get("qualityprofile") or []
    except arrclient.ArrError as exc:
        return f"{service} quality-profile lookup failed: {exc}"
    if not profs:
        return f"No {s} quality profiles found."
    lines = []
    for p in profs[: max(1, min(int(limit or 10), 25))]:
        scored = [fi for fi in (p.get("formatItems") or []) if fi.get("score")]
        sc = ", ".join(f"{fi.get('name')}(id {fi.get('format')})={fi.get('score')}" for fi in scored[:8]) or "no custom-format scores set"
        lines.append(f"id {p.get('id')}: {p.get('name')} — cutoff {p.get('cutoff')}; {sc}")
    return f"{s} quality profiles:\n" + "\n".join(lines)


def custom_formats(service, limit=25):
    """Read-only. List a service's custom formats (id, name, match spec). Use to find a format id, or to check
    whether a dual-audio format already exists before adding one."""
    try:
        s, url, key = _arr_service(service)
    except ValueError as exc:
        return str(exc)
    try:
        cfs = _client(url, key).get("customformat") or []
    except arrclient.ArrError as exc:
        return f"{service} custom-format lookup failed: {exc}"
    if not cfs:
        return f"No {s} custom formats defined."
    lines = []
    for cf in cfs[: max(1, min(int(limit or 25), 50))]:
        specs = "; ".join(_spec_summary(sp) for sp in (cf.get("specifications") or [])) or "(no specs)"
        lines.append(f"id {cf.get('id')}: {cf.get('name')} — {specs}")
    return f"{s} custom formats:\n" + "\n".join(lines)


def custom_format_add_preview(service, name, regex, user=None):
    """Confirm-gated: create a release-title-regex custom format. Creating it changes nothing on its own —
    you then score it in a quality profile (quality_profile_score) to make it take effect."""
    try:
        s, url, key = _arr_service(service)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    name = (name or "").strip()
    regex = (regex or "").strip()
    if not name or not regex:
        return {"status": "error", "message": "name and regex are both required."}
    body = {
        "name": name,
        "includeCustomFormatWhenRenaming": False,
        "specifications": [{
            "name": name,
            "implementation": "ReleaseTitleSpecification",
            "negate": False,
            "required": True,
            "fields": [{"name": "value", "value": regex}],
        }],
    }
    preview = (f"{s} will CREATE a custom format '{name}' that matches release titles by regex:\n  /{regex}/\n"
               f"(This adds nothing by itself — give it a score in a quality profile to make it prefer/avoid "
               f"those releases.)")

    def _execute():
        return _client(url, key).post("customformat", body)

    return pending_actions.register(
        f"{s}.custom_format_add", preview, _execute, user=user, destructive=False,
        metadata={"service": s, "action": "custom_format_add", "name": name})


def quality_profile_score_preview(service, profile_id, format_id, score, user=None):
    """Confirm-gated: set a custom format's score within a quality profile (the lever that makes a format
    actually prefer/avoid matching releases). Looks up the current score so the preview shows old -> new."""
    try:
        s, url, key = _arr_service(service)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    try:
        pid = _int(profile_id, "profile_id")
        fid = _int(format_id, "format_id")
        sc = _int(score, "score")
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    try:
        prof = _client(url, key).get(f"qualityprofile/{pid}")
    except arrclient.ArrError as exc:
        return {"status": "error", "message": f"could not load {s} profile {pid}: {exc}"}
    if not prof:
        return {"status": "error", "message": f"No {s} quality profile id {pid}."}
    target = next((fi for fi in (prof.get("formatItems") or []) if fi.get("format") == fid), None)
    if not target:
        return {"status": "error", "message": f"Custom format id {fid} is not listed in profile '{prof.get('name')}'."}
    old, cfname = target.get("score"), target.get("name")
    preview = (f"{s} will set custom format '{cfname}' (id {fid}) score in quality profile "
               f"'{prof.get('name')}' (id {pid}): {old} -> {sc}.")

    def _execute():
        fresh = _client(url, key).get(f"qualityprofile/{pid}")   # re-fetch to PUT a current object
        for fi in (fresh.get("formatItems") or []):
            if fi.get("format") == fid:
                fi["score"] = sc
        return _client(url, key).put(f"qualityprofile/{pid}", fresh)

    return pending_actions.register(
        f"{s}.quality_score", preview, _execute, user=user, destructive=False,
        metadata={"service": s, "action": "quality_score", "profile_id": pid, "format_id": fid, "score": sc})


# --- Lidarr (music) — same *arr shape but API v1. Read tools are kept BOUNDED on purpose: Lidarr's full
# library payload is huge (its homepage widget is intentionally absent because the API returns >4MB), so we
# filter/cap client-side and never dump the whole library. Write (search) is admin-gated + confirm-on-write.
def _lidarr_client():
    return arrclient.ArrClient(arr_config.LIDARR_URL, arr_config.LIDARR_APIKEY, timeout=arr_config.ARR_TIMEOUT, api_version="v1")


def _format_artist(a):
    name = a.get("artistName") or a.get("sortName") or "?"
    monitored = "monitored" if a.get("monitored") else "unmonitored"
    st = a.get("statistics") or {}
    albums = st.get("albumCount")
    if st.get("trackCount") is not None:
        tracks = f"{st.get('trackFileCount', 0)}/{st.get('trackCount')} tracks"
    else:
        tracks = "track count unknown"
    return f"{name} — id {a.get('id', '?')}, {monitored}, {albums} albums, {tracks}, {a.get('path') or 'no path'}"


def lidarr_lookup(term, limit=8):
    """Read-only. Find artists already in the Lidarr library by (partial) name; bounded summary only."""
    term = (term or "").strip()
    if not term:
        return "lidarr_lookup needs a search term."
    if not arr_config.LIDARR_APIKEY:
        return "Lidarr API key is not configured."
    try:
        artists = _lidarr_client().get("artist")   # whole library; filter client-side + cap (large payload)
    except arrclient.ArrError as exc:
        return f"Lidarr lookup failed: {exc}"
    tl = term.lower()
    matched = [a for a in (artists or [])
               if tl in (a.get("artistName") or "").lower() or tl in (a.get("sortName") or "").lower()]
    if not matched:
        return f"No Lidarr artist in the library matched {term!r}."
    lines = [_format_artist(a) for a in matched[: max(1, min(int(limit or 8), 20))]]
    return "Lidarr matches:\n" + "\n".join(lines)


def lidarr_albums(artist_id, limit=15):
    """Read-only. List albums for one Lidarr artist (id from lidarr_lookup), bounded."""
    if not arr_config.LIDARR_APIKEY:
        return "Lidarr API key is not configured."
    try:
        aid = _int(artist_id, "artist_id")
    except ValueError as exc:
        return str(exc)
    try:
        albums = _lidarr_client().get("album", params={"artistId": aid})
    except arrclient.ArrError as exc:
        return f"Lidarr album lookup failed: {exc}"
    if not albums:
        return f"No albums found for Lidarr artist id {aid}."
    lines = []
    for al in albums[: max(1, min(int(limit or 15), 30))]:
        st = al.get("statistics") or {}
        have = f"{st.get('trackFileCount', 0)}/{st.get('trackCount', '?')}"
        mon = "monitored" if al.get("monitored") else "unmonitored"
        lines.append(f"id {al.get('id')}: {al.get('title') or '?'} ({al.get('albumType') or '?'}) — {mon}, {have} tracks")
    return f"Lidarr albums for artist {aid}:\n" + "\n".join(lines)


def _lidarr_search_command(scope, artist_id=None, album_ids=None):
    scope = (scope or "").strip().lower()
    if scope == "artist":
        aid = _int(artist_id, "artist_id")
        return "ArtistSearch", {"artistId": aid}, f"Trigger Lidarr search for artist id {aid}."
    if scope in ("album", "albums"):
        ids = album_ids or []
        if isinstance(ids, str):
            ids = [x.strip() for x in ids.split(",") if x.strip()]
        ids = [_int(x, "album_ids") for x in ids]
        if not ids:
            raise ValueError("album_ids is required for an album search")
        return "AlbumSearch", {"albumIds": ids}, f"Trigger Lidarr search for album ids {ids}."
    raise ValueError("scope must be 'artist' or 'album'")


def lidarr_search_preview(scope, artist_id=None, album_ids=None, user=None):
    """Register a confirm-gated Lidarr search command (ArtistSearch / AlbumSearch). Does not execute on preview."""
    if not arr_config.LIDARR_APIKEY:
        return {"status": "error", "message": "Lidarr API key is not configured for write-capable tools."}
    try:
        command, payload, preview = _lidarr_search_command(scope, artist_id, album_ids)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    def _execute():
        return _lidarr_client().command(command, payload, wait=False)

    return pending_actions.register(
        "lidarr.search",
        preview + f"\nCommand: {command}\nPayload: {json_debug(payload)}",
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "lidarr", "command": command, "payload": payload},
    )


def _qbit_client():
    return qbitclient.QbitClient(arr_config.QBIT_URL, arr_config.QBIT_USER, arr_config.QBIT_PASS, timeout=arr_config.QBIT_TIMEOUT)


def _torrent_lines(torrents, limit):
    lines = []
    for t in torrents[: max(1, min(int(limit or 10), 30))]:
        name = (t.get("name") or "?")[:90]
        h = t.get("hash") or "?"
        pct = round(float(t.get("progress") or 0) * 100, 1)
        state = t.get("state") or "?"
        category = t.get("category") or ""
        size = t.get("size") or 0
        dlspeed = round(float(t.get("dlspeed") or 0) / 1_000_000, 2)
        lines.append(f"{name} — hash {h}, {pct}%, {state}, category={category}, size={size}, down={dlspeed} MB/s")
    return lines


def qbit_torrents(filter=None, category=None, hashes=None, limit=10):
    try:
        with _qbit_client() as client:
            torrents = client.torrents(filter=filter, category=category, hashes=hashes)
    except qbitclient.QbitError as exc:
        return f"qBittorrent lookup failed: {exc}"
    if not torrents:
        return "No matching qBittorrent torrents."
    return "qBittorrent torrents:\n" + "\n".join(_torrent_lines(torrents, limit))


def _qbit_action_plan(action, hashes):
    action = (action or "").strip().lower()
    if action not in {"pause", "resume", "recheck"}:
        raise ValueError("action must be pause, resume, or recheck")
    hashes_value = qbitclient._hashes(hashes)
    return action, hashes_value, f"qBittorrent will {action} torrent hash(es): {hashes_value}"


def qbit_action_preview(action, hashes, user=None):
    try:
        action, hashes_value, preview = _qbit_action_plan(action, hashes)
    except (ValueError, qbitclient.QbitError) as exc:
        return {"status": "error", "message": str(exc)}

    def _execute():
        with _qbit_client() as client:
            method = getattr(client, action)
            return method(hashes_value)

    return pending_actions.register(
        "qbit." + action,
        preview,
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "qbit", "action": action, "hashes": hashes_value},
    )


def qbit_add_preview(url, category, paused=True, savepath=None, user=None):
    url = (url or "").strip()
    category = (category or "").strip()
    savepath = (savepath or "").strip() or None
    if not url:
        return {"status": "error", "message": "url is required."}
    if not (url.startswith("magnet:") or url.startswith("http://") or url.startswith("https://")):
        return {"status": "error", "message": "url must be a magnet:, http://, or https:// URL."}
    if not category:
        return {"status": "error", "message": "category is required so the download stays scoped."}

    preview = [
        "qBittorrent will add this URL/magnet.",
        f"Category: {category}",
        f"Paused: {bool(paused)}",
    ]
    if savepath:
        preview.append(f"Save path: {savepath}")
    preview.append(f"URL: {url}")

    def _execute():
        with _qbit_client() as client:
            return client.add_url(url, category=category, paused=bool(paused), savepath=savepath)

    return pending_actions.register(
        "qbit.add",
        "\n".join(preview),
        _execute,
        user=user,
        destructive=False,
        metadata={"service": "qbit", "action": "add", "category": category, "paused": bool(paused), "savepath": savepath},
    )


_ACTIVE_DL_STATES = {"downloading", "stalleddl", "metadl", "forceddl", "queueddl", "allocating", "checkingdl"}


def qbit_remove_preview(hashes, delete_files=False, user=None):
    """Confirm-gated DESTRUCTIVE removal of torrent(s) by hash. The preview LOOKS UP each torrent and shows
    its name/state/progress (and warns on incomplete/active ones) so a confirmation can't blind-delete a live
    download. delete_files also removes the downloaded data."""
    try:
        hashes_value = qbitclient._hashes(hashes)
    except qbitclient.QbitError as exc:
        return {"status": "error", "message": str(exc)}
    delete_files = bool(delete_files)
    try:
        with _qbit_client() as client:
            found = client.torrents(hashes=hashes_value)
    except qbitclient.QbitError as exc:
        return {"status": "error", "message": f"Could not look up those torrents: {exc}"}
    if not found:
        return {"status": "error", "message": f"No qBittorrent torrent matched hash(es): {hashes_value}. Nothing to remove."}
    head = (f"qBittorrent will REMOVE {len(found)} torrent(s)"
            + (" AND DELETE their downloaded files" if delete_files else " (downloaded files kept)") + ":")
    lines = [head]
    active = []
    for t in found:
        pct = round(float(t.get("progress") or 0) * 100, 1)
        state = (t.get("state") or "?")
        lines.append(f"  - {(t.get('name') or '?')[:90]} [{state}, {pct}%] hash {t.get('hash')}")
        if pct < 100.0 or state.lower() in _ACTIVE_DL_STATES:
            active.append(t.get("name"))
    if active:
        lines.append(f"  WARNING: {len(active)} of these are incomplete / possibly active downloads — "
                     "confirm ONLY if you truly mean to drop them.")

    def _execute():
        with _qbit_client() as client:
            client.remove(hashes_value, delete_files=delete_files)
            return {"removed": hashes_value, "delete_files": delete_files}

    return pending_actions.register(
        "qbit.remove",
        "\n".join(lines),
        _execute,
        user=user,
        destructive=True,
        metadata={"service": "qbit", "action": "remove", "hashes": hashes_value, "delete_files": delete_files},
    )


def downloads():
    try:
        with httpx.Client(timeout=8) as c:
            c.post(f"{arr_config.QBIT_URL}/api/v2/auth/login",
                   data={"username": arr_config.QBIT_USER, "password": arr_config.QBIT_PASS},
                   headers={"Referer": arr_config.QBIT_URL})
            tors = c.get(f"{arr_config.QBIT_URL}/api/v2/torrents/info", params={"filter": "active"}).json()
        if not tors:
            return "No active torrents right now."
        lines = []
        for t in tors[:8]:
            pct = round(t.get("progress", 0) * 100)
            dl = round(t.get("dlspeed", 0) / 1e6, 1)
            lines.append(f"{(t.get('name','?'))[:45]} — {pct}% ({dl} MB/s, {t.get('state','?')})")
        return f"{len(tors)} active torrent(s): " + "; ".join(lines)
    except Exception:
        return "Couldn't reach qBittorrent."


def recyclarr_sync_preview(apply=False, user=None):
    """Confirm-gated Recyclarr sync via a one-off container (the hostops broker runs it). DEFAULTS TO A DRY-RUN
    (`sync --preview`) that shows what WOULD change and applies nothing — protecting hand-crafted quality profiles. Only
    apply=True runs the real sync, which (per recyclarr.yml) manages the 4K WEB / 1080p WEB profiles with
    reset_unmatched_scores ON — it will ZERO any custom-format score in those profiles that isn't in
    recyclarr.yml. Profiles with other names are untouched."""
    apply = bool(apply)
    dry_run = not apply

    def _execute():
        return hostops._request("POST", "/recyclarr/sync", json_body={"preview": dry_run},
                                timeout=arr_config.RECYCLARR_SYNC_TIMEOUT)

    if dry_run:
        preview = ("DRY-RUN of Recyclarr sync (`sync --preview`): shows what it WOULD change in Sonarr/Radarr "
                   "quality settings and applies NOTHING. Use this first; pass apply=true to actually sync.")
    else:
        preview = ("APPLY Recyclarr sync: pushes the curated custom formats + scores from recyclarr.yml into "
                   "Sonarr/Radarr. ⚠️ It manages the '4K WEB' and '1080p WEB' profiles with "
                   "reset_unmatched_scores=ON — any custom-format score in THOSE profiles that isn't defined "
                   "in recyclarr.yml is RESET TO 0 (this can wipe hand-tuned scores). Profiles with other "
                   "names are not touched. Run a dry-run (apply=false) first and review the diff.")

    return pending_actions.register(
        "recyclarr.sync",
        preview,
        _execute,
        user=user,
        destructive=apply,   # the real apply gets the risk banner; a dry-run is harmless
        metadata={"service": "recyclarr", "action": "sync", "apply": apply},
    )
