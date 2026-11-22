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
class FakeSonarrClient:
    calls = []

    def __init__(self, base_url, api_key, timeout=15.0):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def command(self, name, body=None, wait=False):
        self.calls.append({"name": name, "body": body, "wait": wait, "key": self.api_key})
        return {"id": 44, "status": "queued", "name": name}

    def post(self, path, body=None, params=None):
        self.calls.append({"post": path, "body": body, "key": self.api_key})
        return {"status": "grabbed", "guid": (body or {}).get("guid")}

    # manual-import candidates: a=importable, b=rejected, c=unmapped
    IMPORT_ITEMS = [
        {"path": "/media/a.mkv", "relativePath": "Season 17/a.mkv", "series": {"id": 31}, "seasonNumber": 17,
         "episodes": [{"id": 2764, "seasonNumber": 17, "episodeNumber": 14}],
         "quality": {"quality": {"name": "WEBDL-1080p"}}, "languages": [{"name": "Japanese"}],
         "releaseGroup": "GRP", "indexerFlags": 0, "rejections": []},
        {"path": "/media/b.mkv", "relativePath": "Season 17/b.mkv", "series": {"id": 31}, "seasonNumber": 17,
         "episodes": [{"id": 2765, "seasonNumber": 17, "episodeNumber": 15}],
         "quality": {"quality": {"name": "WEBDL-1080p"}}, "languages": [{"name": "Japanese"}],
         "releaseGroup": "GRP", "indexerFlags": 0, "rejections": [{"reason": "already imported"}]},
        {"path": "/media/c.mkv", "relativePath": "Season 17/c.mkv", "series": {"id": 31}, "episodes": [], "rejections": []},
    ]

    def get(self, path, params=None):
        self.calls.append({"get": path, "params": params})
        return self.IMPORT_ITEMS if path == "manualimport" else []


class SonarrSearchPreviewTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakeSonarrClient.calls = []
        self.orig_key = arr_config.SONARR_APIKEY
        arr_config.SONARR_APIKEY = "sonarr-secret"

    def tearDown(self):
        arr_config.SONARR_APIKEY = self.orig_key
        pending_actions._clear_for_tests()

    def test_series_search_registers_pending_action_without_executing(self):
        with patch("arr_tools.arrclient.ArrClient", FakeSonarrClient):
            preview = arr_tools.sonarr_search_preview("series", series_id=7, user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("SeriesSearch", preview["preview"])
        self.assertEqual(FakeSonarrClient.calls, [])
        with patch("arr_tools.arrclient.ArrClient", FakeSonarrClient):
            out = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(FakeSonarrClient.calls[0]["name"], "SeriesSearch")
        self.assertEqual(FakeSonarrClient.calls[0]["body"], {"seriesId": 7})

    def test_season_and_episode_payloads(self):
        season = arr_tools.sonarr_search_preview("season", series_id="7", season_number="2", user="Alex")
        self.assertIn('"seasonNumber": 2', season["preview"])
        eps = arr_tools.sonarr_search_preview("episodes", episode_ids=[10, "11"], user="Alex")
        self.assertIn('"episodeIds"', eps["preview"])
        self.assertIn("11", eps["preview"])

    def test_missing_key_or_bad_scope_returns_error(self):
        arr_config.SONARR_APIKEY = ""
        out = arr_tools.sonarr_search_preview("series", series_id=7, user="Alex")
        self.assertEqual(out["status"], "error")
        arr_config.SONARR_APIKEY = "sonarr-secret"
        out = arr_tools.sonarr_search_preview("bad", series_id=7, user="Alex")
        self.assertEqual(out["status"], "error")

    def test_tools_dispatch_and_same_user_confirmation(self):
        with patch("arr_tools.sonarr_search_preview", lambda *args, **kwargs: {"status": "pending", "action_token": "tok"}):
            out = tools.run_tool("sonarr_search", {"scope": "series", "series_id": 7}, "", user="Alex")
        self.assertEqual(json.loads(out)["action_token"], "tok")


class SonarrGrabPreviewTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakeSonarrClient.calls = []
        self.orig_key = arr_config.SONARR_APIKEY
        arr_config.SONARR_APIKEY = "sonarr-secret"

    def tearDown(self):
        arr_config.SONARR_APIKEY = self.orig_key
        pending_actions._clear_for_tests()

    def test_grab_previews_then_confirms_post_release(self):
        with patch("arr_tools.arrclient.ArrClient", FakeSonarrClient):
            preview = arr_tools.sonarr_grab_preview("abc-guid", 3, title="Bleach S02 Dual Audio", user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("GRAB", preview["preview"])
        self.assertIn("abc-guid", preview["preview"])
        self.assertEqual(FakeSonarrClient.calls, [])  # nothing grabbed on preview
        with patch("arr_tools.arrclient.ArrClient", FakeSonarrClient):
            out = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(FakeSonarrClient.calls[0]["post"], "release")
        self.assertEqual(FakeSonarrClient.calls[0]["body"], {"guid": "abc-guid", "indexerId": 3})

    def test_grab_validation_errors(self):
        self.assertEqual(arr_tools.sonarr_grab_preview("", 3, user="Alex")["status"], "error")        # no guid
        self.assertEqual(arr_tools.sonarr_grab_preview("g", "x", user="Alex")["status"], "error")      # bad indexer_id
        arr_config.SONARR_APIKEY = ""
        self.assertEqual(arr_tools.sonarr_grab_preview("g", 3, user="Alex")["status"], "error")        # no key


class SonarrReleasesReadTests(unittest.TestCase):
    def test_releases_lists_languages_and_guid(self):
        class FakeRel:
            def __init__(self, *a, **k): pass
            def get(self, path, params=None):
                return [{"title": "Bleach S02 Dual Audio", "guid": "g1", "indexerId": 5, "seeders": 40,
                         "size": 123, "languages": [{"name": "Japanese"}, {"name": "English"}],
                         "quality": {"quality": {"name": "WEBDL-1080p"}}, "rejected": False}]
        orig = arr_config.SONARR_APIKEY
        arr_config.SONARR_APIKEY = "k"
        try:
            with patch("arr_tools.arrclient.ArrClient", FakeRel):
                out = arr_tools.sonarr_releases(31, season_number=2)
            self.assertIn("Japanese, English", out)   # dual-audio visible
            self.assertIn("guid=g1", out)
            self.assertIn("indexerId=5", out)
        finally:
            arr_config.SONARR_APIKEY = orig


class SonarrManualImportTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakeSonarrClient.calls = []
        self.orig_key = arr_config.SONARR_APIKEY
        arr_config.SONARR_APIKEY = "sonarr-secret"

    def tearDown(self):
        arr_config.SONARR_APIKEY = self.orig_key
        pending_actions._clear_for_tests()

    def test_candidates_read_only_summarizes(self):
        with patch("arr_tools.arrclient.ArrClient", FakeSonarrClient):
            out = arr_tools.sonarr_import_candidates(folder="/media/x")
        self.assertIn("3 file(s), 1 importable", out)
        self.assertIn("S17E14", out)
        self.assertIn("REJECTED", out)        # the rejected one is shown

    def test_import_imports_only_mapped_unrejected_then_confirms(self):
        with patch("arr_tools.arrclient.ArrClient", FakeSonarrClient):
            preview = arr_tools.sonarr_manual_import_preview(folder="/media/x", user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("MANUAL-IMPORT 1 file(s)", preview["preview"])   # only 'a' (b rejected, c unmapped)
        self.assertIn("2 skipped", preview["preview"])
        self.assertEqual([c for c in FakeSonarrClient.calls if "post" in c or c.get("name")], [])  # no import on preview
        with patch("arr_tools.arrclient.ArrClient", FakeSonarrClient):
            applied = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(applied["status"], "applied")
        cmd = next(c for c in FakeSonarrClient.calls if c.get("name") == "ManualImport")
        self.assertEqual(cmd["body"]["importMode"], "move")
        self.assertEqual(len(cmd["body"]["files"]), 1)
        self.assertEqual(cmd["body"]["files"][0]["episodeIds"], [2764])
        self.assertEqual(cmd["body"]["files"][0]["seriesId"], 31)

    def test_include_rejected_imports_more(self):
        with patch("arr_tools.arrclient.ArrClient", FakeSonarrClient):
            preview = arr_tools.sonarr_manual_import_preview(folder="/media/x", include_rejected=True, user="Alex")
        self.assertIn("MANUAL-IMPORT 2 file(s)", preview["preview"])   # a + b; c still unmapped -> skipped

    def test_import_validation(self):
        self.assertEqual(arr_tools.sonarr_manual_import_preview(user="Alex")["status"], "error")   # no folder/dl
        out = arr_tools.sonarr_manual_import_preview(folder="/x", import_mode="delete", user="Alex")
        self.assertEqual(out["status"], "error")   # bad mode

    def test_admin_gated(self):
        self.assertIn("Permission denied", tools.run_tool("sonarr_manual_import", {"folder": "/x"}, "", user="Jordan"))


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


class SonarrSearchWiringTests(unittest.TestCase):
    def test_schema_tool_lists_and_admin_gate(self):
        self.assertTrue(_offered("sonarr_search") and _dispatched("sonarr_search"))
        self.assertTrue(_admin_gated("sonarr_search"))

    def test_grab_and_releases_wired(self):
        for n in ("sonarr_grab", "sonarr_releases"):
            self.assertTrue(_offered(n) and _dispatched(n), n)
        self.assertTrue(_admin_gated("sonarr_grab"))        # grab is admin-gated
        self.assertFalse(_admin_gated("sonarr_releases"))   # read-only, not gated

    def test_manual_import_wired(self):
        for n in ("sonarr_manual_import", "sonarr_import_candidates"):
            self.assertTrue(_offered(n) and _dispatched(n), n)
        self.assertTrue(_admin_gated("sonarr_manual_import"))          # import is admin-gated
        self.assertFalse(_admin_gated("sonarr_import_candidates"))     # candidates is read-only

if __name__ == "__main__":
    unittest.main()
