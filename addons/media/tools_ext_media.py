"""HAI's media add-on, the tool side: the schemas and handlers of the tools that drive the user's own Sonarr, Radarr,
Lidarr, Prowlarr, qBittorrent and Seerr instances (the work is in arr_tools). Read tools are open; every tool that
changes something is admin-only and returns a pending action the user confirms.

An extension of tools: that module loads every module beside it named tools_ext_<name>.py and takes its TOOLS (placed
in the general pools at the anchors PLACE names, so each pool keeps its order), HANDLERS, ADMIN and OPEN. A head
without this file has no media tools."""
import arr_tools
import arr_config


_QBIT_TORRENTS = {"type": "function", "function": {
    "name": "qbit_torrents",
    "description": "Read-only. List qBittorrent torrents with hash, progress, state, category, size, and speed. Use before proposing any qBit action.",
    "parameters": {"type": "object", "properties": {
        "filter": {"type": "string", "description": "qBit filter such as active, paused, downloading, completed, errored."},
        "category": {"type": "string", "description": "Optional qBit category filter, e.g. tv-sonarr."},
        "hashes": {"type": "string", "description": "Optional one or more hashes separated by |."},
        "limit": {"type": "integer", "description": "Max torrents to return, default 10."},
    }},
}}


_QBIT_ADD = {"type": "function", "function": {
    "name": "qbit_add",
    "description": "ADMIN ONLY. Preview adding a magnet/URL to qBittorrent. Requires a category so the download stays scoped. Defaults to paused=true. Does not add until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "url": {"type": "string", "description": "Magnet, http, or https URL to add."},
        "category": {"type": "string", "description": "Required qBit category, e.g. tv-sonarr or test."},
        "paused": {"type": "boolean", "description": "Add paused first. Defaults true."},
        "savepath": {"type": "string", "description": "Optional save path; omit unless explicitly needed."},
    }, "required": ["url", "category"]},
}}


_QBIT_ACTION = {"type": "function", "function": {
    "name": "qbit_action",
    "description": "ADMIN ONLY. Preview a non-destructive qBittorrent action (pause, resume, recheck) for one or more torrent hashes. Does not run until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["pause", "resume", "recheck"]},
        "hashes": {"type": "array", "items": {"type": "string"}, "description": "Torrent hashes to affect. Use qbit_torrents first to get exact hashes."},
    }, "required": ["action", "hashes"]},
}}


_QBIT_REMOVE = {"type": "function", "function": {
    "name": "qbit_remove",
    "description": "ADMIN ONLY. Preview REMOVING torrent(s) from qBittorrent by hash — DESTRUCTIVE. Optionally delete_files to also delete the downloaded data. The preview looks up each torrent and shows its name/state/progress (and warns on incomplete/active downloads). Does not remove until the user approves and confirm_action is called. ALWAYS get exact hashes from qbit_torrents first; never guess a hash.",
    "parameters": {"type": "object", "properties": {
        "hashes": {"type": "array", "items": {"type": "string"}, "description": "Exact torrent hash(es) to remove (from qbit_torrents)."},
        "delete_files": {"type": "boolean", "description": "Also delete the downloaded files from disk. Defaults false (removes the torrent entry only)."},
    }, "required": ["hashes"]},
}}


_SONARR_SEARCH = {"type": "function", "function": {
    "name": "sonarr_search",
    "description": "ADMIN ONLY. Preview a Sonarr search command (series, season, or episodes) and create a pending action token. Does not run until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "scope": {"type": "string", "enum": ["series", "season", "episodes"], "description": "What to search for."},
        "series_id": {"type": "integer", "description": "Sonarr series id for series or season search."},
        "season_number": {"type": "integer", "description": "Season number for season search."},
        "episode_ids": {"type": "array", "items": {"type": "integer"}, "description": "Episode ids for episode search."},
    }, "required": ["scope"]},
}}


_SONARR_LOOKUP = {"type": "function", "function": {
    "name": "sonarr_lookup",
    "description": "Read-only. Search Sonarr for series already known to the library and summarize monitored status, IDs, episode counts, and path. Use before proposing any Sonarr action.",
    "parameters": {"type": "object", "properties": {
        "term": {"type": "string", "description": "Series title or partial title to search in Sonarr."},
        "limit": {"type": "integer", "description": "Max matches to return, default 8."},
    }, "required": ["term"]},
}}


_SONARR_RELEASES = {"type": "function", "function": {
    "name": "sonarr_releases",
    "description": "Read-only. List the actual grabbable RELEASES for a Sonarr series/season/episode (interactive search) with quality, languages (use to spot DUAL-AUDIO), seeders, size, indexerId, and guid. Use this to find a SPECIFIC release, then sonarr_grab to grab it. Downloads nothing.",
    "parameters": {"type": "object", "properties": {
        "series_id": {"type": "integer", "description": "Sonarr series id (from sonarr_lookup)."},
        "season_number": {"type": "integer", "description": "Optional season number to scope the search."},
        "episode_id": {"type": "integer", "description": "Optional Sonarr episode id to scope to one episode."},
        "limit": {"type": "integer", "description": "Max releases to return, default 10."},
    }, "required": ["series_id"]},
}}


_SONARR_GRAB = {"type": "function", "function": {
    "name": "sonarr_grab",
    "description": "ADMIN ONLY. Preview grabbing ONE specific Sonarr release (by guid + indexer_id from sonarr_releases) and create a pending action token. Sonarr sends it to the download client (adds a download; never touches existing ones). Does not grab until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "guid": {"type": "string", "description": "The release guid from sonarr_releases."},
        "indexer_id": {"type": "integer", "description": "The indexerId from sonarr_releases."},
        "title": {"type": "string", "description": "Optional release title for the preview."},
    }, "required": ["guid", "indexer_id"]},
}}


_SONARR_IMPORT_CANDIDATES = {"type": "function", "function": {
    "name": "sonarr_import_candidates",
    "description": "Read-only. List the files Sonarr could MANUAL-IMPORT from a folder or download, with each file's proposed season/episode mapping, quality, languages, and any rejections (empty = importable). Use before sonarr_manual_import to see what would be imported. Imports nothing.",
    "parameters": {"type": "object", "properties": {
        "folder": {"type": "string", "description": "Folder path Sonarr can see (e.g. /media/tv-sonarr/<download>)."},
        "download_id": {"type": "string", "description": "qBittorrent download hash, as an alternative to folder."},
        "series_id": {"type": "integer", "description": "Optional Sonarr series id to scope/aid the mapping."},
        "limit": {"type": "integer", "description": "Max candidates to list, default 15."},
    }},
}}


_SONARR_MANUAL_IMPORT = {"type": "function", "function": {
    "name": "sonarr_manual_import",
    "description": "ADMIN ONLY. Preview a Sonarr MANUAL IMPORT of a folder/download into the library and create a pending action token. Imports every file that maps to an episode (skipping rejected ones unless include_rejected). import_mode 'move' (default) or 'copy'. Run sonarr_import_candidates first. Does not import until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "folder": {"type": "string", "description": "Folder path to import (e.g. /media/tv-sonarr/<download>)."},
        "download_id": {"type": "string", "description": "qBittorrent download hash, as an alternative to folder."},
        "series_id": {"type": "integer", "description": "Optional Sonarr series id to scope the mapping."},
        "import_mode": {"type": "string", "enum": ["move", "copy"], "description": "move (default) or copy."},
        "include_rejected": {"type": "boolean", "description": "Also import files Sonarr flagged with rejections. Default false."},
    }},
}}


_PROWLARR_SEARCH = {"type": "function", "function": {
    "name": "prowlarr_search",
    "description": "Read-only. Search Prowlarr releases/indexers and summarize title, indexer, protocol, seeders, and size. This does not grab or download anything.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Release search query."},
        "limit": {"type": "integer", "description": "Max releases to return, default 8."},
        "categories": {"type": "string", "description": "Optional Prowlarr category filter."},
    }, "required": ["query"]},
}}


_RADARR_LOOKUP = {"type": "function", "function": {
    "name": "radarr_lookup",
    "description": "Read-only. Search Radarr for movies already in the library and summarize monitored status, id, downloaded/missing, and path. Use before proposing any Radarr action.",
    "parameters": {"type": "object", "properties": {
        "term": {"type": "string", "description": "Movie title or partial title to search in Radarr."},
        "limit": {"type": "integer", "description": "Max matches to return, default 8."},
    }, "required": ["term"]},
}}


_RADARR_RELEASES = {"type": "function", "function": {
    "name": "radarr_releases",
    "description": "Read-only. List the actual grabbable RELEASES for a Radarr movie (interactive search) with quality, languages (use to spot DUAL-AUDIO), seeders, size, indexerId, and guid. Use this to find a SPECIFIC release, then radarr_grab to grab it. Downloads nothing.",
    "parameters": {"type": "object", "properties": {
        "movie_id": {"type": "integer", "description": "Radarr movie id (from radarr_lookup)."},
        "limit": {"type": "integer", "description": "Max releases to return, default 10."},
    }, "required": ["movie_id"]},
}}


_RADARR_SEARCH = {"type": "function", "function": {
    "name": "radarr_search",
    "description": "ADMIN ONLY. Preview a Radarr automatic search (MoviesSearch) for a monitored movie and create a pending action token. Does not run until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "movie_id": {"type": "integer", "description": "Radarr movie id to search for."},
    }, "required": ["movie_id"]},
}}


_RADARR_GRAB = {"type": "function", "function": {
    "name": "radarr_grab",
    "description": "ADMIN ONLY. Preview grabbing ONE specific Radarr release (by guid + indexer_id from radarr_releases) and create a pending action token. Radarr sends it to the download client (adds a download; never touches existing ones). Does not grab until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "guid": {"type": "string", "description": "The release guid from radarr_releases."},
        "indexer_id": {"type": "integer", "description": "The indexerId from radarr_releases."},
        "title": {"type": "string", "description": "Optional release title for the preview."},
    }, "required": ["guid", "indexer_id"]},
}}


_RADARR_IMPORT_CANDIDATES = {"type": "function", "function": {
    "name": "radarr_import_candidates",
    "description": "Read-only. List the files Radarr could MANUAL-IMPORT from a folder or download, with each file's proposed movie mapping, quality, languages, and any rejections (empty = importable). Use before radarr_manual_import. Imports nothing.",
    "parameters": {"type": "object", "properties": {
        "folder": {"type": "string", "description": "Folder path Radarr can see (e.g. /media/movies-radarr/<download>)."},
        "download_id": {"type": "string", "description": "qBittorrent download hash, as an alternative to folder."},
        "movie_id": {"type": "integer", "description": "Optional Radarr movie id to scope/aid the mapping."},
        "limit": {"type": "integer", "description": "Max candidates to list, default 15."},
    }},
}}


_RADARR_MANUAL_IMPORT = {"type": "function", "function": {
    "name": "radarr_manual_import",
    "description": "ADMIN ONLY. Preview a Radarr MANUAL IMPORT of a folder/download into the library and create a pending action token. Imports every file that maps to a movie (skipping rejected ones unless include_rejected). import_mode 'move' (default) or 'copy'. Run radarr_import_candidates first. Does not import until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "folder": {"type": "string", "description": "Folder path to import (e.g. /media/movies-radarr/<download>)."},
        "download_id": {"type": "string", "description": "qBittorrent download hash, as an alternative to folder."},
        "movie_id": {"type": "integer", "description": "Optional Radarr movie id to scope the mapping."},
        "import_mode": {"type": "string", "enum": ["move", "copy"], "description": "move (default) or copy."},
        "include_rejected": {"type": "boolean", "description": "Also import files Radarr flagged with rejections. Default false."},
    }},
}}


_SEERR_REQUESTS = {"type": "function", "function": {
    "name": "seerr_requests",
    "description": "Read-only. List Overseerr (Seerr) media requests with id, type, title, who requested, and request/availability status. filter: all | pending | approved | available | processing | unavailable. Use to find a request id before approving or declining.",
    "parameters": {"type": "object", "properties": {
        "filter": {"type": "string", "description": "Request filter: all (default), pending, approved, available, processing, unavailable."},
        "limit": {"type": "integer", "description": "Max requests to return, default 10."},
    }},
}}


_SEERR_APPROVE = {"type": "function", "function": {
    "name": "seerr_approve",
    "description": "ADMIN ONLY. Preview APPROVING an Overseerr request by id (it then goes to Radarr/Sonarr to download) and create a pending action token. Does not approve until the user confirms. Get the request id from seerr_requests.",
    "parameters": {"type": "object", "properties": {
        "request_id": {"type": "integer", "description": "The Overseerr request id (from seerr_requests)."},
    }, "required": ["request_id"]},
}}


_SEERR_DECLINE = {"type": "function", "function": {
    "name": "seerr_decline",
    "description": "ADMIN ONLY. Preview DECLINING an Overseerr request by id and create a pending action token. Does not decline until the user confirms. Get the request id from seerr_requests.",
    "parameters": {"type": "object", "properties": {
        "request_id": {"type": "integer", "description": "The Overseerr request id (from seerr_requests)."},
    }, "required": ["request_id"]},
}}


_SEERR_ADD = {"type": "function", "function": {
    "name": "seerr_add",
    "description": "ADMIN ONLY. Preview ADDING a new Overseerr request for a movie or tv show by TMDB id and create a pending action token. Does not add until the user confirms.",
    "parameters": {"type": "object", "properties": {
        "media_type": {"type": "string", "enum": ["movie", "tv"], "description": "movie or tv."},
        "media_id": {"type": "integer", "description": "The TMDB id of the movie/show."},
        "seasons": {"type": "array", "items": {"type": "integer"}, "description": "Optional season numbers for a tv request."},
    }, "required": ["media_type", "media_id"]},
}}


_QUALITY_PROFILES = {"type": "function", "function": {
    "name": "quality_profiles",
    "description": "Read-only. List a service's quality profiles (id, name, cutoff) and any custom-format scores already set in each. service is 'sonarr' or 'radarr'. Use to find the profile id before scoring a custom format.",
    "parameters": {"type": "object", "properties": {
        "service": {"type": "string", "enum": ["sonarr", "radarr"], "description": "Which service."},
        "limit": {"type": "integer", "description": "Max profiles, default 10."},
    }, "required": ["service"]},
}}


_CUSTOM_FORMATS = {"type": "function", "function": {
    "name": "custom_formats",
    "description": "Read-only. List a service's custom formats (id, name, match spec). service is 'sonarr' or 'radarr'. Use to find a format id, or check whether a dual-audio format already exists before adding one.",
    "parameters": {"type": "object", "properties": {
        "service": {"type": "string", "enum": ["sonarr", "radarr"], "description": "Which service."},
        "limit": {"type": "integer", "description": "Max formats, default 25."},
    }, "required": ["service"]},
}}


_CUSTOM_FORMAT_ADD = {"type": "function", "function": {
    "name": "custom_format_add",
    "description": "ADMIN ONLY. Preview CREATING a release-title-regex custom format in Sonarr or Radarr and create a pending action token. The format does nothing until you score it in a quality profile (quality_profile_score). Does not create until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "service": {"type": "string", "enum": ["sonarr", "radarr"], "description": "Which service."},
        "name": {"type": "string", "description": "Custom format name, e.g. 'Dual Audio'."},
        "regex": {"type": "string", "description": "Regex matched against the release title, e.g. '(?i)\\b(dual[- .]?audio|multi)\\b'."},
    }, "required": ["service", "name", "regex"]},
}}


_QUALITY_PROFILE_SCORE = {"type": "function", "function": {
    "name": "quality_profile_score",
    "description": "ADMIN ONLY. Preview SETTING a custom format's score within a quality profile (the lever that makes a format prefer (positive) or avoid (negative) matching releases) and create a pending action token. Get profile_id from quality_profiles and format_id from custom_formats. Does not change until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "service": {"type": "string", "enum": ["sonarr", "radarr"], "description": "Which service."},
        "profile_id": {"type": "integer", "description": "Quality profile id (from quality_profiles)."},
        "format_id": {"type": "integer", "description": "Custom format id (from custom_formats)."},
        "score": {"type": "integer", "description": "Score to set (positive prefers, negative avoids, 0 neutral)."},
    }, "required": ["service", "profile_id", "format_id", "score"]},
}}


_LIDARR_LOOKUP = {"type": "function", "function": {
    "name": "lidarr_lookup",
    "description": "Read-only. Find music artists already in the Lidarr library by (partial) name; returns id, monitored status, album/track counts, path. Use before any Lidarr action.",
    "parameters": {"type": "object", "properties": {
        "term": {"type": "string", "description": "Artist name or partial name."},
        "limit": {"type": "integer", "description": "Max matches, default 8."},
    }, "required": ["term"]},
}}


_LIDARR_ALBUMS = {"type": "function", "function": {
    "name": "lidarr_albums",
    "description": "Read-only. List albums for one Lidarr artist (artist_id from lidarr_lookup): id, title, type, monitored, track counts.",
    "parameters": {"type": "object", "properties": {
        "artist_id": {"type": "integer", "description": "Lidarr artist id (from lidarr_lookup)."},
        "limit": {"type": "integer", "description": "Max albums, default 15."},
    }, "required": ["artist_id"]},
}}


_LIDARR_SEARCH = {"type": "function", "function": {
    "name": "lidarr_search",
    "description": "ADMIN ONLY. Preview a Lidarr search command (an artist's missing tracks, or specific albums) and create a pending action token. scope 'artist' (with artist_id) or 'album' (with album_ids). Does not run until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "scope": {"type": "string", "enum": ["artist", "album"], "description": "What to search for."},
        "artist_id": {"type": "integer", "description": "Lidarr artist id for an artist search."},
        "album_ids": {"type": "array", "items": {"type": "integer"}, "description": "Album ids for an album search."},
    }, "required": ["scope"]},
}}


_DOWNLOADS = {"type": "function", "function": {
    "name": "check_downloads",
    "description": "Check active qBittorrent downloads (names, progress, speeds). Use for 'what's downloading?'.",
    "parameters": {"type": "object", "properties": {}},
}}


_RECYCLARR_SYNC = {"type": "function", "function": {
    "name": "recyclarr_sync",
    "description": "ADMIN ONLY. Preview running Recyclarr sync (one-off container) and create a pending action token. DEFAULTS TO A DRY-RUN (apply=false) that shows what WOULD change and applies nothing — protecting hand-crafted profiles. apply=true runs the real sync, which manages the '4K WEB'/'1080p WEB' profiles with reset_unmatched_scores ON (zeroes any custom-format score in those profiles not in recyclarr.yml; other profiles untouched). Always dry-run first. Does not run until the user approves and confirm_action is called.",
    "parameters": {"type": "object", "properties": {
        "apply": {"type": "boolean", "description": "false (default) = dry-run (--preview, shows changes, applies nothing). true = actually sync (changes quality settings; can reset unmatched scores in the managed profiles)."},
    }},
}}



def _finish_pending(result, user_text):
    """tools" write-tool post-processing (the semantic check on a MED/HIGH pending action), read when called."""
    import tools
    return tools._finish_pending(result, user_text)


def _h_qbit_torrents(user, args, user_text):
    return arr_tools.qbit_torrents(args.get("filter"), args.get("category"), args.get("hashes"), args.get("limit", 10))

def _h_qbit_add(user, args, user_text):
    return _finish_pending(arr_tools.qbit_add_preview(
        args.get("url"), args.get("category"), args.get("paused", True), args.get("savepath"), user=user,
    ), user_text)

def _h_qbit_action(user, args, user_text):
    return _finish_pending(arr_tools.qbit_action_preview(args.get("action"), args.get("hashes"), user=user), user_text)

def _h_qbit_remove(user, args, user_text):
    return _finish_pending(arr_tools.qbit_remove_preview(args.get("hashes"), args.get("delete_files", False), user=user), user_text)

def _h_sonarr_search(user, args, user_text):
    return _finish_pending(arr_tools.sonarr_search_preview(
        args.get("scope"), args.get("series_id"), args.get("season_number"), args.get("episode_ids"), user=user,
    ), user_text)

def _h_sonarr_lookup(user, args, user_text):
    return arr_tools.sonarr_lookup(args.get("term"), args.get("limit", 8))

def _h_sonarr_releases(user, args, user_text):
    return arr_tools.sonarr_releases(args.get("series_id"), args.get("season_number"), args.get("episode_id"), args.get("limit", 10))

def _h_sonarr_import_candidates(user, args, user_text):
    return arr_tools.sonarr_import_candidates(args.get("folder"), args.get("download_id"), args.get("series_id"), args.get("limit", 15))

def _h_sonarr_manual_import(user, args, user_text):
    return _finish_pending(arr_tools.sonarr_manual_import_preview(
        args.get("folder"), args.get("download_id"), args.get("series_id"),
        args.get("import_mode", "move"), args.get("include_rejected", False), user=user), user_text)

def _h_sonarr_grab(user, args, user_text):
    return _finish_pending(arr_tools.sonarr_grab_preview(
        args.get("guid"), args.get("indexer_id"), args.get("title"), user=user), user_text)

def _h_prowlarr_search(user, args, user_text):
    return arr_tools.prowlarr_search(args.get("query"), args.get("limit", 8), args.get("categories"))

def _h_radarr_lookup(user, args, user_text):
    return arr_tools.radarr_lookup(args.get("term"), args.get("limit", 8))

def _h_radarr_releases(user, args, user_text):
    return arr_tools.radarr_releases(args.get("movie_id"), args.get("limit", 10))

def _h_radarr_import_candidates(user, args, user_text):
    return arr_tools.radarr_import_candidates(args.get("folder"), args.get("download_id"), args.get("movie_id"), args.get("limit", 15))

def _h_radarr_search(user, args, user_text):
    return _finish_pending(arr_tools.radarr_search_preview(args.get("movie_id"), user=user), user_text)

def _h_radarr_grab(user, args, user_text):
    return _finish_pending(arr_tools.radarr_grab_preview(
        args.get("guid"), args.get("indexer_id"), args.get("title"), user=user), user_text)

def _h_radarr_manual_import(user, args, user_text):
    return _finish_pending(arr_tools.radarr_manual_import_preview(
        args.get("folder"), args.get("download_id"), args.get("movie_id"),
        args.get("import_mode", "move"), args.get("include_rejected", False), user=user), user_text)

def _h_seerr_requests(user, args, user_text):
    return arr_tools.seerr_requests(args.get("filter", "all"), args.get("limit", 10))

def _h_seerr_approve(user, args, user_text):
    return _finish_pending(arr_tools.seerr_approve_preview(args.get("request_id"), user=user), user_text)

def _h_seerr_decline(user, args, user_text):
    return _finish_pending(arr_tools.seerr_decline_preview(args.get("request_id"), user=user), user_text)

def _h_seerr_add(user, args, user_text):
    return _finish_pending(arr_tools.seerr_add_preview(
        args.get("media_type"), args.get("media_id"), args.get("seasons"), user=user), user_text)

def _h_quality_profiles(user, args, user_text):
    return arr_tools.quality_profiles(args.get("service"), args.get("limit", 10))

def _h_custom_formats(user, args, user_text):
    return arr_tools.custom_formats(args.get("service"), args.get("limit", 25))

def _h_custom_format_add(user, args, user_text):
    return _finish_pending(arr_tools.custom_format_add_preview(
        args.get("service"), args.get("name"), args.get("regex"), user=user), user_text)

def _h_quality_profile_score(user, args, user_text):
    return _finish_pending(arr_tools.quality_profile_score_preview(
        args.get("service"), args.get("profile_id"), args.get("format_id"), args.get("score"), user=user), user_text)

def _h_lidarr_lookup(user, args, user_text):
    return arr_tools.lidarr_lookup(args.get("term"), args.get("limit", 8))

def _h_lidarr_albums(user, args, user_text):
    return arr_tools.lidarr_albums(args.get("artist_id"), args.get("limit", 15))

def _h_lidarr_search(user, args, user_text):
    return _finish_pending(arr_tools.lidarr_search_preview(
        args.get("scope"), args.get("artist_id"), args.get("album_ids"), user=user), user_text)

def _h_check_downloads(user, args, user_text):
    return arr_tools.downloads()

def _h_recyclarr_sync(user, args, user_text):
    return _finish_pending(arr_tools.recyclarr_sync_preview(args.get("apply", False), user=user), user_text)


def _risk(kind, meta):
    """Risk the add-on's actions carry beyond the head's generic rules (pending_actions.assess_risk)."""
    out = []
    if meta.get("action") == "remove":
        out.append(("MED", "removes torrents from qBittorrent"))
    if kind == "sonarr.manual_import" and meta.get("import_mode") == "move":
        out.append(("MED", "moves files into the library"))
    return out


RISK_RULES = [_risk]
# check_services reaches each of these, listed before the head's own.
SERVICE_CHECKS = {
    "Sonarr": "http://sonarr:8989/ping",
    "Radarr": "http://radarr:7878/ping",
    "Lidarr": "http://lidarr:8686/ping",
    "Prowlarr": "http://prowlarr:9696/ping",
    "qBittorrent": f"{arr_config.QBIT_URL}/api/v2/app/version",
}
CREDENTIAL_NAMES = [("arr-stack", "network")]   # its place in get_credential's list of names


# The general pools' order is kept: each run of these tools goes back right after the tool that preceded it.
PLACE = [
    ("check_services", ["check_downloads"]),
    ("pihole_restartdns", ["recyclarr_sync"]),
    ("disk_usage", ["qbit_torrents", "sonarr_lookup", "sonarr_releases", "sonarr_import_candidates"]),
    ("media_probe", ["prowlarr_search", "sonarr_search", "sonarr_grab", "sonarr_manual_import", "qbit_action", "qbit_add", "qbit_remove", "radarr_lookup", "radarr_releases", "radarr_import_candidates", "radarr_search", "radarr_grab", "radarr_manual_import", "seerr_requests", "seerr_approve", "seerr_decline", "seerr_add"]),
    ("plex_refresh", ["quality_profiles", "custom_formats", "custom_format_add", "quality_profile_score", "lidarr_lookup", "lidarr_albums", "lidarr_search"]),
]
TOOLS = [_QBIT_TORRENTS, _SONARR_LOOKUP, _SONARR_RELEASES, _SONARR_IMPORT_CANDIDATES, _PROWLARR_SEARCH, _SONARR_SEARCH, _SONARR_GRAB, _SONARR_MANUAL_IMPORT, _QBIT_ACTION, _QBIT_ADD, _QBIT_REMOVE, _RADARR_LOOKUP, _RADARR_RELEASES, _RADARR_IMPORT_CANDIDATES, _RADARR_SEARCH, _RADARR_GRAB, _RADARR_MANUAL_IMPORT, _SEERR_REQUESTS, _SEERR_APPROVE, _SEERR_DECLINE, _SEERR_ADD, _QUALITY_PROFILES, _CUSTOM_FORMATS, _CUSTOM_FORMAT_ADD, _QUALITY_PROFILE_SCORE, _LIDARR_LOOKUP, _LIDARR_ALBUMS, _LIDARR_SEARCH, _DOWNLOADS, _RECYCLARR_SYNC]
HANDLERS = {"qbit_torrents": _h_qbit_torrents, "qbit_add": _h_qbit_add, "qbit_action": _h_qbit_action, "qbit_remove": _h_qbit_remove, "sonarr_search": _h_sonarr_search, "sonarr_lookup": _h_sonarr_lookup, "sonarr_releases": _h_sonarr_releases, "sonarr_import_candidates": _h_sonarr_import_candidates, "sonarr_manual_import": _h_sonarr_manual_import, "sonarr_grab": _h_sonarr_grab, "prowlarr_search": _h_prowlarr_search, "radarr_lookup": _h_radarr_lookup, "radarr_releases": _h_radarr_releases, "radarr_import_candidates": _h_radarr_import_candidates, "radarr_search": _h_radarr_search, "radarr_grab": _h_radarr_grab, "radarr_manual_import": _h_radarr_manual_import, "seerr_requests": _h_seerr_requests, "seerr_approve": _h_seerr_approve, "seerr_decline": _h_seerr_decline, "seerr_add": _h_seerr_add, "quality_profiles": _h_quality_profiles, "custom_formats": _h_custom_formats, "custom_format_add": _h_custom_format_add, "quality_profile_score": _h_quality_profile_score, "lidarr_lookup": _h_lidarr_lookup, "lidarr_albums": _h_lidarr_albums, "lidarr_search": _h_lidarr_search, "check_downloads": _h_check_downloads, "recyclarr_sync": _h_recyclarr_sync}
ADMIN = {"recyclarr_sync", "sonarr_search", "radarr_search", "sonarr_manual_import", "seerr_add", "custom_format_add", "sonarr_grab", "seerr_approve", "lidarr_search", "qbit_remove", "qbit_action", "radarr_manual_import", "radarr_grab", "qbit_add", "seerr_decline", "quality_profile_score"}
OPEN = {"check_downloads", "sonarr_releases", "radarr_releases", "sonarr_lookup", "lidarr_albums", "lidarr_lookup", "radarr_import_candidates", "custom_formats", "qbit_torrents", "quality_profiles", "prowlarr_search", "seerr_requests", "radarr_lookup", "sonarr_import_candidates"}
SANDBOX_BLOCKED = set()
