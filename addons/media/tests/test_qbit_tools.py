import json
import unittest
from pathlib import Path
from unittest.mock import patch

import media_tools
import pending_actions
import tools


import arr_tools
class FakeQbit:
    calls = []

    def __init__(self, base_url, username, password, timeout=15.0):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def torrents(self, filter=None, category=None, hashes=None):
        return [{
            "name": "Ubuntu ISO",
            "hash": "abc123",
            "progress": 0.5,
            "state": "pausedDL",
            "category": category or "test",
            "size": 12345,
            "dlspeed": 2000000,
        }]

    def pause(self, hashes):
        self.calls.append(("pause", hashes))
        return None

    def resume(self, hashes):
        self.calls.append(("resume", hashes))
        return None

    def recheck(self, hashes):
        self.calls.append(("recheck", hashes))
        return None

    def add_url(self, url, category=None, paused=False, savepath=None):
        self.calls.append(("add", url, category, paused, savepath))
        return "Ok."

    def remove(self, hashes, delete_files=False):
        self.calls.append(("remove", hashes, delete_files))
        return None


class QbitToolTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakeQbit.calls = []

    def tearDown(self):
        pending_actions._clear_for_tests()

    def test_qbit_torrents_formats_listing(self):
        with patch("arr_tools.qbitclient.QbitClient", FakeQbit):
            out = arr_tools.qbit_torrents(filter="paused", category="test")
        self.assertIn("qBittorrent torrents", out)
        self.assertIn("Ubuntu ISO", out)
        self.assertIn("hash abc123", out)
        self.assertIn("50.0%", out)

    def test_qbit_action_preview_and_confirm(self):
        with patch("arr_tools.qbitclient.QbitClient", FakeQbit):
            preview = arr_tools.qbit_action_preview("resume", ["abc123"], user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("resume", preview["preview"])
        self.assertEqual(FakeQbit.calls, [])
        with patch("arr_tools.qbitclient.QbitClient", FakeQbit):
            out = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(FakeQbit.calls, [("resume", "abc123")])

    def test_qbit_action_rejects_bad_action(self):
        out = arr_tools.qbit_action_preview("delete", ["abc123"], user="Alex")
        self.assertEqual(out["status"], "error")
        self.assertIn("pause, resume, or recheck", out["message"])

    def test_qbit_add_preview_requires_category_and_confirms(self):
        out = arr_tools.qbit_add_preview("magnet:?xt=abc", "", user="Alex")
        self.assertEqual(out["status"], "error")
        with patch("arr_tools.qbitclient.QbitClient", FakeQbit):
            preview = arr_tools.qbit_add_preview("magnet:?xt=abc", "test", user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertIn("Category: test", preview["preview"])
        self.assertIn("Paused: True", preview["preview"])
        self.assertEqual(FakeQbit.calls, [])
        with patch("arr_tools.qbitclient.QbitClient", FakeQbit):
            applied = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(FakeQbit.calls, [("add", "magnet:?xt=abc", "test", True, None)])

    def test_qbit_add_rejects_non_url(self):
        out = arr_tools.qbit_add_preview("not a url", "test", user="Alex")
        self.assertEqual(out["status"], "error")
        self.assertIn("magnet", out["message"])

    def test_tools_dispatch(self):
        with patch("arr_tools.qbit_torrents", lambda *args: "listed"), \
             patch("arr_tools.qbit_action_preview", lambda *args, **kwargs: {"status": "pending", "action_token": "tok"}), \
             patch("arr_tools.qbit_add_preview", lambda *args, **kwargs: {"status": "pending", "action_token": "addtok"}):
            self.assertEqual(tools.run_tool("qbit_torrents", {"filter": "active"}, "", user="Jordan"), "listed")
            parsed = json.loads(tools.run_tool("qbit_action", {"action": "pause", "hashes": ["abc"]}, "", user="Alex"))
            add = json.loads(tools.run_tool("qbit_add", {"url": "magnet:?xt=abc", "category": "test"}, "", user="Alex"))
        self.assertEqual(parsed["action_token"], "tok")
        self.assertEqual(add["action_token"], "addtok")


class QbitRemoveTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()
        FakeQbit.calls = []

    def tearDown(self):
        pending_actions._clear_for_tests()

    def test_remove_is_destructive_shows_torrent_and_confirms(self):
        with patch("arr_tools.qbitclient.QbitClient", FakeQbit):
            preview = arr_tools.qbit_remove_preview(["abc123"], delete_files=True, user="Alex")
        self.assertEqual(preview["status"], "pending")
        self.assertTrue(preview["destructive"])
        self.assertIn("Ubuntu ISO", preview["preview"])              # shows exactly what will be removed
        self.assertIn("DELETE their downloaded files", preview["preview"])
        self.assertIn("WARNING", preview["preview"])                  # 50% -> incomplete warning
        self.assertEqual(FakeQbit.calls, [])                          # nothing removed on preview
        with patch("arr_tools.qbitclient.QbitClient", FakeQbit):
            applied = pending_actions.confirm(preview["action_token"], user="Alex")
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(FakeQbit.calls, [("remove", "abc123", True)])

    def test_remove_no_match_errors_and_does_nothing(self):
        class Empty(FakeQbit):
            def torrents(self, filter=None, category=None, hashes=None):
                return []
        with patch("arr_tools.qbitclient.QbitClient", Empty):
            out = arr_tools.qbit_remove_preview(["nope"], user="Alex")
        self.assertEqual(out["status"], "error")
        self.assertIn("Nothing to remove", out["message"])

    def test_remove_admin_gated(self):
        self.assertIn("Permission denied", tools.run_tool("qbit_remove", {"hashes": ["x"]}, "", user="Jordan"))


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


class QbitToolWiringTests(unittest.TestCase):
    def test_schema_tool_lists_and_gating(self):
        for n in ("qbit_torrents", "qbit_action", "qbit_add", "qbit_remove"):
            self.assertTrue(_offered(n), n)
            self.assertTrue(_dispatched(n), n)
        self.assertFalse(_admin_gated("qbit_torrents"))
        self.assertTrue(_admin_gated("qbit_action"))
        self.assertTrue(_admin_gated("qbit_add"))
        self.assertTrue(_admin_gated("qbit_remove"))        # destructive -> admin-gated

if __name__ == "__main__":
    unittest.main()
