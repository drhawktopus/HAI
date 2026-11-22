import json
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import arr_config
import media_tools
import pending_actions
import tools


import arr_tools
# --------------------------------------------------------------------------- Radarr

class FakeRadarrClient:
    calls = []
    MOVIES = [
        {"id": 12, "title": "Dune", "sortTitle": "dune", "year": 2021, "monitored": True,
         "hasFile": True, "path": "/movies/Dune (2021)"},
        {"id": 13, "title": "Dune: Part Two", "sortTitle": "dune part two", "year": 2024,
         "monitored": True, "hasFile": False, "path": "/movies/Dune Part Two (2024)"},
        {"id": 14, "title": "Arrival", "sortTitle": "arrival", "year": 2016, "monitored": False,
         "hasFile": True, "path": "/movies/Arrival (2016)"},
    ]
    RELEASES = [{"title": "Dune.2021.Dual.1080p", "guid": "rg1", "indexerId": 4, "seeders": 30,
                 "size": 9, "languages": [{"name": "English"}, {"name": "French"}],
                 "quality": {"quality": {"name": "Bluray-1080p"}}, "rejected": False}]
    IMPORT_ITEMS = [
        {"path": "/media/a.mkv", "relativePath": "Dune/a.mkv", "movie": {"id": 12, "title": "Dune"},
         "quality": {"quality": {"name": "Bluray-1080p"}}, "languages": [{"name": "English"}],
         "releaseGroup": "GRP", "indexerFlags": 0, "rejections": []},
        {"path": "/media/b.mkv", "relativePath": "Dune/b.mkv", "movie": {"id": 12, "title": "Dune"},
         "quality": {"quality": {"name": "Bluray-1080p"}}, "languages": [{"name": "English"}],
         "rejections": [{"reason": "already imported"}]},
        {"path": "/media/c.mkv", "relativePath": "Dune/c.mkv", "movie": None, "rejections": []},
    ]

    def __init__(self, base_url, api_key, timeout=15.0):
        self.api_key = api_key

    def movies(self, term=None):
        return self.MOVIES

    def get(self, path, params=None):
        self.calls.append({"get": path, "params": params})
        if path == "release":
            return self.RELEASES
        if path == "manualimport":
            return self.IMPORT_ITEMS
        return []

    def post(self, path, body=None, params=None):
        self.calls.append({"post": path, "body": body})
        return {"status": "grabbed", "guid": (body or {}).get("guid")}

    def command(self, name, body=None, wait=False):
        self.calls.append({"name": name, "body": body})
        return {"id": 9, "name": name, "status": "queued"}


class RadarrReadTests(unittest.TestCase):
    def setUp(self):
        self.orig = arr_config.RADARR_APIKEY
        arr_config.RADARR_APIKEY = "radarr-secret"

    def tearDown(self):
        arr_config.RADARR_APIKEY = self.orig

    def test_lookup_filters_client_side(self):
        with patch("arr_tools.arrclient.ArrClient", FakeRadarrClient):
            out = arr_tools.radarr_lookup("dune")
        self.assertIn("Dune (2021)", out)
        self.assertIn("Dune: Part Two", out)
        self.assertNotIn("Arrival", out)
        self.assertIn("id 12", out)

    def test_releases_shows_languages_and_guid(self):
        with patch("arr_tools.arrclient.ArrClient", FakeRadarrClient):
            out = arr_tools.radarr_releases(12)
        self.assertIn("English, French", out)
        self.assertIn("guid=rg1", out)
        self.assertIn("indexerId=4", out)

    def test_candidates_counts_importable(self):
        with patch("arr_tools.arrclient.ArrClient", FakeRadarrClient):
            out = arr_tools.radarr_import_candidates(folder="/media/x")
        self.assertIn("3 file(s), 1 importable", out)
        self.assertIn("REJECTED", out)

    def test_missing_key(self):
        arr_config.RADARR_APIKEY = ""
        self.assertIn("not configured", arr_tools.radarr_lookup("dune"))


class RadarrWriteTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakeRadarrClient.calls = []
        self.orig = arr_config.RADARR_APIKEY
        arr_config.RADARR_APIKEY = "radarr-secret"

    def tearDown(self):
        arr_config.RADARR_APIKEY = self.orig
        pending_actions._clear_for_tests()

    def test_search_previews_then_confirms_command(self):
        with patch("arr_tools.arrclient.ArrClient", FakeRadarrClient):
            preview = arr_tools.radarr_search_preview(12, user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("MoviesSearch", preview["preview"])
        self.assertEqual(FakeRadarrClient.calls, [])
        with patch("arr_tools.arrclient.ArrClient", FakeRadarrClient):
            out = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(FakeRadarrClient.calls[0]["name"], "MoviesSearch")
        self.assertEqual(FakeRadarrClient.calls[0]["body"], {"movieIds": [12]})

    def test_grab_previews_then_posts_release(self):
        with patch("arr_tools.arrclient.ArrClient", FakeRadarrClient):
            preview = arr_tools.radarr_grab_preview("rg1", 4, title="Dune Dual", user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("GRAB", preview["preview"])
        self.assertEqual(FakeRadarrClient.calls, [])
        with patch("arr_tools.arrclient.ArrClient", FakeRadarrClient):
            out = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(FakeRadarrClient.calls[0]["post"], "release")
        self.assertEqual(FakeRadarrClient.calls[0]["body"], {"guid": "rg1", "indexerId": 4})

    def test_manual_import_only_mapped_unrejected(self):
        with patch("arr_tools.arrclient.ArrClient", FakeRadarrClient):
            preview = arr_tools.radarr_manual_import_preview(folder="/media/x", user="Alex")
        self.assertIn("MANUAL-IMPORT 1 file(s)", preview["preview"])
        self.assertIn("2 skipped", preview["preview"])
        with patch("arr_tools.arrclient.ArrClient", FakeRadarrClient):
            applied = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(applied["status"], "applied")
        cmd = next(c for c in FakeRadarrClient.calls if c.get("name") == "ManualImport")
        self.assertEqual(len(cmd["body"]["files"]), 1)
        self.assertEqual(cmd["body"]["files"][0]["movieId"], 12)

    def test_admin_gated(self):
        self.assertIn("Permission denied", tools.run_tool("radarr_grab", {"guid": "g", "indexer_id": 1}, "", user="Jordan"))


# --------------------------------------------------------------------------- Seerr

class FakeSeerrClient:
    calls = []
    REQUESTS = {"results": [
        {"id": 41, "status": 1, "type": "movie",
         "media": {"tmdbId": 693134, "mediaType": "movie", "status": 2},
         "requestedBy": {"displayName": "Riley"}},
        {"id": 42, "status": 2, "type": "tv",
         "media": {"tmdbId": 1399, "mediaType": "tv", "status": 5},
         "requestedBy": {"username": "jordan"}},
    ]}
    TITLES = {("movie", 693134): "Dune: Part Two", ("tv", 1399): "Game of Thrones"}

    def __init__(self, base_url, api_key, timeout=15.0):
        self.api_key = api_key

    def list_requests(self, filter=None, take=20, skip=0, sort="added"):
        return self.REQUESTS

    def get(self, path, params=None):
        # title lookups: "movie/693134" / "tv/1399"
        parts = path.split("/")
        if len(parts) == 2 and parts[0] in ("movie", "tv"):
            t = self.TITLES.get((parts[0], int(parts[1])), "")
            return {"title": t} if parts[0] == "movie" else {"name": t}
        return None

    def get_request(self, rid):
        return next((r for r in self.REQUESTS["results"] if r["id"] == int(rid)), None)

    def approve(self, rid):
        self.calls.append({"approve": int(rid)})
        return {"id": int(rid), "status": 2}

    def decline(self, rid):
        self.calls.append({"decline": int(rid)})
        return {"id": int(rid), "status": 3}

    def add_request(self, media_type, media_id, seasons=None):
        self.calls.append({"add": (media_type, int(media_id), seasons)})
        return {"id": 99, "type": media_type}


class SeerrTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakeSeerrClient.calls = []
        self.orig = arr_config.SEERR_APIKEY
        arr_config.SEERR_APIKEY = "seerr-secret"

    def tearDown(self):
        arr_config.SEERR_APIKEY = self.orig
        pending_actions._clear_for_tests()

    def test_requests_list_resolves_titles_and_who(self):
        with patch("arr_tools.seerrclient.SeerrClient", FakeSeerrClient):
            out = arr_tools.seerr_requests("all")
        self.assertIn("#41", out)
        self.assertIn("Dune: Part Two", out)
        self.assertIn("requested by Riley", out)
        self.assertIn("request=pending", out)
        self.assertIn("Game of Thrones", out)

    def test_request_status_enum_matches_overseerr(self):
        # Verified against live Overseerr data: request.status 5=completed (NOT media-available); 4=failed.
        import seerrclient
        self.assertEqual(seerrclient.REQUEST_STATUS,
                         {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"})

    def test_approve_previews_then_confirms(self):
        with patch("arr_tools.seerrclient.SeerrClient", FakeSeerrClient):
            preview = arr_tools.seerr_approve_preview(41, user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("APPROVE request #41", preview["preview"])
        self.assertIn("Dune: Part Two", preview["preview"])
        self.assertEqual(FakeSeerrClient.calls, [])
        with patch("arr_tools.seerrclient.SeerrClient", FakeSeerrClient):
            out = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(FakeSeerrClient.calls[0], {"approve": 41})

    def test_decline_is_destructive_and_confirms(self):
        with patch("arr_tools.seerrclient.SeerrClient", FakeSeerrClient):
            preview = arr_tools.seerr_decline_preview(42, user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("DECLINE request #42", preview["preview"])
        self.assertEqual(preview.get("destructive"), True)
        with patch("arr_tools.seerrclient.SeerrClient", FakeSeerrClient):
            out = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(FakeSeerrClient.calls[0], {"decline": 42})
        self.assertEqual(out["status"], "applied")

    def test_add_previews_then_confirms(self):
        with patch("arr_tools.seerrclient.SeerrClient", FakeSeerrClient):
            preview = arr_tools.seerr_add_preview("movie", 693134, user="Alex")
        self.assertIn("ADD a movie request", preview["preview"])
        self.assertIn("Dune: Part Two", preview["preview"])
        with patch("arr_tools.seerrclient.SeerrClient", FakeSeerrClient):
            pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(FakeSeerrClient.calls[0], {"add": ("movie", 693134, None)})

    def test_add_bad_type(self):
        self.assertEqual(arr_tools.seerr_add_preview("album", 1, user="Alex")["status"], "error")

    def test_unknown_request_id(self):
        with patch("arr_tools.seerrclient.SeerrClient", FakeSeerrClient):
            out = arr_tools.seerr_approve_preview(999, user="Alex")
        self.assertEqual(out["status"], "error")

    def test_admin_gated(self):
        self.assertIn("Permission denied", tools.run_tool("seerr_approve", {"request_id": 41}, "", user="Jordan"))


# --------------------------------------------------------------------------- Phase 3: quality/custom formats

class FakeQualityClient:
    calls = []
    PROFILES = [{"id": 1, "name": "HD-1080p", "cutoff": 9,
                 "formatItems": [{"format": 5, "name": "Dual Audio", "score": 0},
                                 {"format": 6, "name": "x265", "score": 25}]}]
    CFS = [{"id": 5, "name": "Dual Audio",
            "specifications": [{"name": "Dual Audio", "implementation": "ReleaseTitleSpecification",
                                "negate": False, "fields": [{"name": "value", "value": "dual.audio"}]}]}]

    def __init__(self, base_url, api_key, timeout=15.0):
        self.api_key = api_key

    def get(self, path, params=None):
        self.calls.append({"get": path})
        if path == "qualityprofile":
            return self.PROFILES
        if path == "customformat":
            return self.CFS
        if path.startswith("qualityprofile/"):
            import copy
            return copy.deepcopy(self.PROFILES[0])
        return []

    def post(self, path, body=None, params=None):
        self.calls.append({"post": path, "body": body})
        return {"id": 9, "name": (body or {}).get("name")}

    def put(self, path, body=None, params=None):
        self.calls.append({"put": path, "body": body})
        return body


class QualityToolTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakeQualityClient.calls = []
        self.orig = arr_config.SONARR_APIKEY
        arr_config.SONARR_APIKEY = "k"

    def tearDown(self):
        arr_config.SONARR_APIKEY = self.orig
        pending_actions._clear_for_tests()

    def test_quality_profiles_read(self):
        with patch("arr_tools.arrclient.ArrClient", FakeQualityClient):
            out = arr_tools.quality_profiles("sonarr")
        self.assertIn("id 1: HD-1080p", out)
        self.assertIn("x265(id 6)=25", out)   # scored CF shown

    def test_custom_formats_read(self):
        with patch("arr_tools.arrclient.ArrClient", FakeQualityClient):
            out = arr_tools.custom_formats("sonarr")
        self.assertIn("id 5: Dual Audio", out)
        self.assertIn("ReleaseTitle(dual.audio)", out)

    def test_bad_service(self):
        self.assertIn("sonarr", arr_tools.quality_profiles("plex"))   # error mentions valid services

    def test_custom_format_add_previews_then_posts(self):
        with patch("arr_tools.arrclient.ArrClient", FakeQualityClient):
            pv = arr_tools.custom_format_add_preview("sonarr", "Dual Audio", "(?i)dual.audio", user="Alex")
        self.assertEqual(pv["status"], "pending")
        self.assertIn("CREATE a custom format", pv["preview"])
        self.assertEqual(FakeQualityClient.calls, [])
        with patch("arr_tools.arrclient.ArrClient", FakeQualityClient):
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        post = next(c for c in FakeQualityClient.calls if "post" in c)
        self.assertEqual(post["post"], "customformat")
        self.assertEqual(post["body"]["specifications"][0]["fields"][0]["value"], "(?i)dual.audio")

    def test_quality_score_previews_old_to_new_then_puts(self):
        with patch("arr_tools.arrclient.ArrClient", FakeQualityClient):
            pv = arr_tools.quality_profile_score_preview("sonarr", 1, 5, 100, user="Alex")
        self.assertEqual(pv["status"], "pending")
        self.assertIn("0 -> 100", pv["preview"])
        self.assertIn("Dual Audio", pv["preview"])
        with patch("arr_tools.arrclient.ArrClient", FakeQualityClient):
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        put = next(c for c in FakeQualityClient.calls if "put" in c)
        self.assertEqual(put["put"], "qualityprofile/1")
        scored = next(fi for fi in put["body"]["formatItems"] if fi["format"] == 5)
        self.assertEqual(scored["score"], 100)

    def test_quality_score_unknown_format(self):
        with patch("arr_tools.arrclient.ArrClient", FakeQualityClient):
            out = arr_tools.quality_profile_score_preview("sonarr", 1, 999, 50, user="Alex")
        self.assertEqual(out["status"], "error")

    def test_admin_gated(self):
        self.assertIn("Permission denied", tools.run_tool("custom_format_add", {"service": "sonarr", "name": "x", "regex": "y"}, "", user="Jordan"))
        self.assertIn("Permission denied", tools.run_tool("quality_profile_score", {"service": "sonarr", "profile_id": 1, "format_id": 5, "score": 1}, "", user="Jordan"))


# --------------------------------------------------------------------------- Lidarr (music, API v1)

class FakeLidarrClient:
    calls = []
    ARTISTS = [
        {"id": 7, "artistName": "Radiohead", "sortName": "radiohead", "monitored": True,
         "path": "/music/Radiohead", "statistics": {"albumCount": 9, "trackFileCount": 100, "trackCount": 120}},
        {"id": 8, "artistName": "Boards of Canada", "sortName": "boards of canada", "monitored": False,
         "path": "/music/BoC", "statistics": {"albumCount": 4, "trackFileCount": 40, "trackCount": 40}},
    ]
    ALBUMS = [{"id": 21, "title": "Kid A", "albumType": "Album", "monitored": True,
               "statistics": {"trackFileCount": 10, "trackCount": 10}}]

    def __init__(self, base_url, api_key, timeout=15.0, api_version="v3"):
        self.api_key = api_key
        self.api_version = api_version

    def get(self, path, params=None):
        self.calls.append({"get": path, "params": params, "ver": self.api_version})
        if path == "artist":
            return self.ARTISTS
        if path == "album":
            return self.ALBUMS
        return []

    def command(self, name, body=None, wait=False):
        self.calls.append({"name": name, "body": body})
        return {"id": 5, "name": name}


class LidarrTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakeLidarrClient.calls = []
        self.orig = arr_config.LIDARR_APIKEY
        arr_config.LIDARR_APIKEY = "lidarr-secret"

    def tearDown(self):
        arr_config.LIDARR_APIKEY = self.orig
        pending_actions._clear_for_tests()

    def test_lookup_filters_and_uses_v1(self):
        with patch("arr_tools.arrclient.ArrClient", FakeLidarrClient):
            out = arr_tools.lidarr_lookup("radio")
        self.assertIn("Radiohead", out)
        self.assertNotIn("Boards of Canada", out)
        self.assertIn("9 albums", out)
        self.assertEqual(FakeLidarrClient.calls[0]["ver"], "v1")   # Lidarr is API v1, not v3

    def test_albums_read(self):
        with patch("arr_tools.arrclient.ArrClient", FakeLidarrClient):
            out = arr_tools.lidarr_albums(7)
        self.assertIn("Kid A", out)
        self.assertIn("10/10 tracks", out)

    def test_search_previews_then_confirms(self):
        with patch("arr_tools.arrclient.ArrClient", FakeLidarrClient):
            pv = arr_tools.lidarr_search_preview("artist", artist_id=7, user="Alex")
        self.assertEqual(pv["status"], "pending")
        self.assertIn("ArtistSearch", pv["preview"])
        self.assertEqual(FakeLidarrClient.calls, [])
        with patch("arr_tools.arrclient.ArrClient", FakeLidarrClient):
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(FakeLidarrClient.calls[0]["name"], "ArtistSearch")
        self.assertEqual(FakeLidarrClient.calls[0]["body"], {"artistId": 7})

    def test_album_search_payload(self):
        pv = arr_tools.lidarr_search_preview("album", album_ids=[21, "22"], user="Alex")
        self.assertIn("AlbumSearch", pv["preview"])
        self.assertIn("21", pv["preview"])

    def test_bad_scope_and_missing_key(self):
        self.assertEqual(arr_tools.lidarr_search_preview("bad", user="Alex")["status"], "error")
        arr_config.LIDARR_APIKEY = ""
        self.assertIn("not configured", arr_tools.lidarr_lookup("radio"))

    def test_admin_gated(self):
        self.assertIn("Permission denied", tools.run_tool("lidarr_search", {"scope": "artist", "artist_id": 7}, "", user="Jordan"))


class ArrClientVersionTests(unittest.TestCase):
    def test_default_is_v3_and_v1_override(self):
        import arrclient
        c3 = arrclient.ArrClient("http://x", "k")
        self.assertEqual(c3._url("series"), "http://x/api/v3/series")
        c1 = arrclient.ArrClient("http://x", "k", api_version="v1")
        self.assertEqual(c1._url("artist"), "http://x/api/v1/artist")
        self.assertEqual(c1._url("api/v1/album"), "http://x/api/v1/album")   # no double-prefix


# --------------------------------------------------------------------------- wiring

def _pool_names(pool):
    return [t["function"]["name"] for t in pool]


def _offered(name):
    """In both general pools (the media add-on places its tools there)."""
    return all(name in _pool_names(p) for p in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS))


def _dispatched(name):
    """Handled: by an extension's handler, or by the head's own dispatch."""
    return name in tools._EXT_HANDLERS or ('if name == "%s":' % name) in Path(tools.__file__).read_text(encoding="utf-8")


def _admin_gated(name):
    return name in tools._ADMIN_TOOLS


class Phase2WiringTests(unittest.TestCase):
    def test_read_tools_ungated_and_dispatched(self):
        for n in ("radarr_lookup", "radarr_releases", "radarr_import_candidates",
                  "seerr_requests", "plex_sections", "quality_profiles", "custom_formats",
                  "lidarr_lookup", "lidarr_albums"):
            self.assertTrue(_offered(n) and _dispatched(n), n)
            self.assertFalse(_admin_gated(n), n)

    def test_write_tools_gated_and_dispatched(self):
        for n in ("radarr_search", "radarr_grab", "radarr_manual_import",
                  "seerr_approve", "seerr_decline", "seerr_add", "plex_refresh",
                  "custom_format_add", "quality_profile_score", "lidarr_search"):
            self.assertTrue(_offered(n) and _dispatched(n), n)
            self.assertTrue(_admin_gated(n), n)

    def test_tools_in_both_lists(self):
        for n in ("radarr_lookup", "radarr_grab", "seerr_requests", "seerr_approve", "plex_sections", "plex_refresh",
                  "quality_profiles", "custom_formats", "custom_format_add", "quality_profile_score",
                  "lidarr_lookup", "lidarr_albums", "lidarr_search"):
            self.assertTrue(_offered(n), n)

if __name__ == "__main__":
    unittest.main()
