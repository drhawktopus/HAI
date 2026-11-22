"""Verify the stale workspace guard is enforced at the coding_task start boundary, shared by all lanes."""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import coding_client  # noqa: E402
import config  # noqa: E402
import overnight  # noqa: E402
import tools  # noqa: E402

LIVE = {"app.py": "LIVE", "tools.py": "T"}


def _ws(files, ahead=False):
    return {"files": [{"rel": k, "sha256": v} for k, v in files.items()], "ahead": ahead}


class _Live(unittest.TestCase):
    def setUp(self):
        p = patch.object(overnight, "_source_manifest", lambda root: dict(LIVE))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(config, "OVERNIGHT_WORKSPACE_STALE_CHECK", True)
        p.start()
        self.addCleanup(p.stop)


class StaleReasonTests(_Live):
    def test_a_workspace_behind_live_is_refused_with_the_files_named(self):
        with patch.object(coding_client, "workspace_source_hashes", lambda ws: _ws({"app.py": "OLD", "tools.py": "T"})):
            why = overnight.stale_reason("orchestrator-real")
        self.assertIn("STALE vs live: 1 source file(s) differ (app.py)", why)
        self.assertIn("sync-workspace.sh", why)

    def test_in_sync_or_ahead_or_another_workspace_is_allowed(self):
        with patch.object(coding_client, "workspace_source_hashes", lambda ws: _ws(dict(LIVE))):
            self.assertIsNone(overnight.stale_reason("orchestrator-real"))
        with patch.object(coding_client, "workspace_source_hashes",
                          lambda ws: _ws({"app.py": "NEW-WORK", "tools.py": "T"}, ahead=True)):
            self.assertIsNone(overnight.stale_reason("orchestrator-real"), "undeployed work is not stale")
        with patch.object(coding_client, "workspace_source_hashes", lambda ws: self.fail("asked the runner")):
            self.assertIsNone(overnight.stale_reason("some-other-repo"), "only the self workspace has a live twin")

    def test_a_broken_check_fails_open(self):
        def boom(ws):
            raise OSError("runner down")
        with patch.object(coding_client, "workspace_source_hashes", boom):
            self.assertIsNone(overnight.stale_reason("orchestrator-real"))

    def test_the_overnight_guard_is_the_same_derivation(self):
        with patch.object(coding_client, "workspace_source_hashes", lambda ws: _ws({"app.py": "OLD", "tools.py": "T"})):
            self.assertEqual(app._make_stale_guard()({"workspace": "orchestrator-real"}),
                             overnight.stale_reason("orchestrator-real"))


class DeckLagRowTests(_Live):
    """Asserts that the Deep Work tile displays workspace lag and never resolves to 'could not tell'."""

    def _view(self, hashes):
        app._WS_LAG.update({"at": 0.0, "view": None})
        self.addCleanup(app._WS_LAG.update, {"at": 0.0, "view": None})
        with patch.object(coding_client, "workspace_source_hashes", hashes):
            return app.absurd_status(user="ClaudeSupervisor")["workspace"]

    def test_each_state_has_its_own_row(self):
        self.assertEqual(self._view(lambda ws: _ws(dict(LIVE))), "✅ in sync with live")
        self.assertIn("STALE: 1 file(s) behind live", self._view(lambda ws: _ws({"app.py": "OLD", "tools.py": "T"})))
        self.assertIn("undeployed work", self._view(lambda ws: _ws({"app.py": "NEW", "tools.py": "T"}, ahead=True)))
        self.assertEqual(self._view(lambda ws: {"error": "runner down"}), "❔ could not check")

    def test_the_row_is_cached_between_polls(self):
        calls = []
        self._view(lambda ws: calls.append(ws) or _ws(dict(LIVE)))
        with patch.object(coding_client, "workspace_source_hashes", lambda ws: calls.append(ws) or _ws(dict(LIVE))):
            app.absurd_status(user="ClaudeSupervisor")
        self.assertEqual(len(calls), 1)


class CodingTaskStartDoorTests(_Live):
    def _start(self):
        started = []
        with patch.object(coding_client, "task", lambda action, **k: started.append(action) or {"ok": True}):
            out = tools.run_tool("coding_task", {"action": "start", "workspace": "orchestrator-real",
                                                 "objective": "x"}, "x", user="Alex")
        return out, started

    def test_start_on_a_stale_workspace_is_refused_before_the_runner_is_asked(self):
        with patch.object(coding_client, "workspace_source_hashes", lambda ws: _ws({"app.py": "OLD", "tools.py": "T"})):
            out, started = self._start()
        self.assertEqual(started, [])
        self.assertIn("REFUSED: workspace 'orchestrator-real' is STALE", json.loads(out)["error"])

    def test_start_on_a_synced_workspace_goes_through(self):
        with patch.object(coding_client, "workspace_source_hashes", lambda ws: _ws(dict(LIVE))):
            out, started = self._start()
        self.assertEqual(started, ["start"])


if __name__ == "__main__":
    unittest.main()
