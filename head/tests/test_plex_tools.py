"""The head's Plex tools (media_tools.plex_sections and plex_refresh_preview): list the library sections, and a
confirm-gated scan or metadata refresh."""
import unittest
from unittest.mock import patch

import config
import media_tools
import pending_actions
import tools


class FakePlexServer:
    calls = []
    SECTIONS = [{"key": "1", "title": "Movies", "type": "movie"},
                {"key": "2", "title": "TV Shows", "type": "show"}]

    def __init__(self, base_url, token, timeout=15.0):
        self.token = token

    def sections(self):
        return self.SECTIONS

    def refresh(self, section_key=None, force=False):
        self.calls.append({"section_key": section_key, "force": force})
        return [str(section_key)] if section_key is not None else ["1", "2"]


class PlexTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakePlexServer.calls = []
        self.orig = config.PLEX_TOKEN
        config.PLEX_TOKEN = "plex-token"

    def tearDown(self):
        config.PLEX_TOKEN = self.orig
        pending_actions._clear_for_tests()

    def test_sections_list(self):
        with patch("media_tools.plexserver.PlexServer", FakePlexServer):
            out = media_tools.plex_sections()
        self.assertIn("key 1: Movies", out)
        self.assertIn("key 2: TV Shows", out)

    def test_refresh_one_section_previews_then_confirms(self):
        with patch("media_tools.plexserver.PlexServer", FakePlexServer):
            preview = media_tools.plex_refresh_preview(section_key=1, user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("Movies", preview["preview"])
        self.assertIn("scan", preview["preview"])
        self.assertEqual(FakePlexServer.calls, [])
        with patch("media_tools.plexserver.PlexServer", FakePlexServer):
            out = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(FakePlexServer.calls[0], {"section_key": 1, "force": False})

    def test_refresh_all_and_force(self):
        with patch("media_tools.plexserver.PlexServer", FakePlexServer):
            preview = media_tools.plex_refresh_preview(force=True, user="Alex")
        self.assertIn("ALL libraries", preview["preview"])
        self.assertIn("metadata refresh", preview["preview"])

    def test_refresh_bad_section_key(self):
        with patch("media_tools.plexserver.PlexServer", FakePlexServer):
            out = media_tools.plex_refresh_preview(section_key=99, user="Alex")
        self.assertEqual(out["status"], "error")

    def test_missing_token(self):
        config.PLEX_TOKEN = ""
        self.assertIn("not configured", media_tools.plex_sections())

    def test_admin_gated(self):
        self.assertIn("Permission denied", tools.run_tool("plex_refresh", {}, "", user="Jordan"))


if __name__ == "__main__":
    unittest.main()
