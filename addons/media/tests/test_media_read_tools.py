import unittest
from unittest.mock import patch
from pathlib import Path

import media_tools
import tools


import arr_tools
class FakeArr:
    def __init__(self, base_url, api_key, timeout=15.0):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def series(self, term=None):
        # Sonarr returns the WHOLE library regardless of term; sonarr_lookup must filter client-side.
        return [
            {"id": 7, "title": "Bleach", "year": 2004, "monitored": True, "path": "/media/tv/Bleach",
             "statistics": {"episodeFileCount": 366, "episodeCount": 366}},
            {"id": 9, "title": "Adventure Time", "year": 2010, "monitored": True, "path": "/media/tv/AT",
             "statistics": {"episodeFileCount": 292, "episodeCount": 292}},
        ]

    def queue(self):
        return {"records": [{"title": "Bleach S01E01", "status": "downloading", "sizeleft": 123}]}

    def get(self, path, params=None):
        self.last_path = path
        self.last_params = params
        return [{"title": "Bleach dual audio", "indexer": "IndexerA", "protocol": "torrent", "seeders": 42, "size": 99}]


class MediaReadToolTests(unittest.TestCase):
    def test_sonarr_lookup_filters_to_matching_series(self):
        with patch("arr_tools.arrclient.ArrClient", FakeArr):
            out = arr_tools.sonarr_lookup("bleach")
        self.assertIn("Sonarr matches", out)
        self.assertIn("Bleach (2004)", out)
        self.assertIn("366/366 episodes", out)
        self.assertIn("/media/tv/Bleach", out)
        self.assertNotIn("Adventure Time", out)   # whole-library result filtered down to the term

    def test_sonarr_lookup_no_match(self):
        with patch("arr_tools.arrclient.ArrClient", FakeArr):
            out = arr_tools.sonarr_lookup("nonexistent-show-xyz")
        self.assertIn("No Sonarr series", out)

    def test_sonarr_queue_formats_records(self):
        with patch("arr_tools.arrclient.ArrClient", FakeArr):
            out = arr_tools.sonarr_queue()
        self.assertIn("Sonarr queue", out)
        self.assertIn("Bleach S01E01", out)
        self.assertIn("downloading", out)

    def test_prowlarr_search_formats_releases(self):
        with patch("arr_tools.arrclient.ArrClient", FakeArr):
            out = arr_tools.prowlarr_search("bleach dual audio")
        self.assertIn("Prowlarr releases", out)
        self.assertIn("Bleach dual audio", out)
        self.assertIn("seeders=42", out)

    def test_tools_dispatch_read_wrappers(self):
        with patch("arr_tools.sonarr_lookup", lambda term, limit=8: f"sonarr {term} {limit}"), \
             patch("arr_tools.prowlarr_search", lambda query, limit=8, categories=None: f"prowlarr {query} {limit} {categories}"):
            self.assertEqual(tools.run_tool("sonarr_lookup", {"term": "bleach", "limit": 3}, "", user="Jordan"), "sonarr bleach 3")
            self.assertEqual(tools.run_tool("prowlarr_search", {"query": "bleach", "limit": 2, "categories": "5000"}, "", user="Jordan"), "prowlarr bleach 2 5000")


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


class MediaReadToolWiringTests(unittest.TestCase):
    def test_schema_and_tool_lists(self):
        for n in ("sonarr_lookup", "prowlarr_search"):
            self.assertTrue(_offered(n), n)
            self.assertTrue(_dispatched(n), n)

    def test_read_wrappers_are_not_admin_gated(self):
        self.assertFalse(_admin_gated("sonarr_lookup"))
        self.assertFalse(_admin_gated("prowlarr_search"))

if __name__ == "__main__":
    unittest.main()
