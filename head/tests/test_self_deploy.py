"""Project #12 Phase 0 — self-deploy executor tests.

The deploy state machine (hostops_deploy) is exercised end to end with the container runner faked (no
docker) and the health/smoke checks patched, against real temp-dir canonical/workspace/backup trees. Covers
the happy path, canary failure (live untouched), live-verify failure -> auto-rollback, no-diff rejection,
cooperative cancel, the promote/cancel API guards, the file-manifest round-trip, plus the client + risk +
tool wiring.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import hostops
import hostops_deploy
import pending_actions
import tools


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class FakeRunner:
    """Stands in for the sidecar's _run_oneoff: records each `docker compose` script and returns success."""
    def __init__(self):
        self.scripts = []
        self.fail_on = None   # substring -> return non-zero

    def __call__(self, image, cmd, binds, env, user, network, entrypoint=None, wait_timeout=None):
        script = cmd[0]
        self.scripts.append(script)
        if self.fail_on and self.fail_on in script:
            return 1, "boom"
        return 0, "ok"

    def saw(self, substr):
        return any(substr in s for s in self.scripts)


class DeployStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.canonical = os.path.join(self.tmp, "canonical")
        self.workspaces = os.path.join(self.tmp, "workspaces")
        self.backups = os.path.join(self.tmp, "backups")
        # canonical baseline
        _write(os.path.join(self.canonical, "app.py"), "v1")
        _write(os.path.join(self.canonical, "tools.py"), "t1")
        _write(os.path.join(self.canonical, "tests", "test_x.py"), "orig")
        # workspace: app.py changed, newmod.py added, the rest identical
        ws = os.path.join(self.workspaces, "orchestrator-real")
        _write(os.path.join(ws, "app.py"), "v2")
        _write(os.path.join(ws, "newmod.py"), "new")
        _write(os.path.join(ws, "tools.py"), "t1")
        _write(os.path.join(ws, "tests", "test_x.py"), "orig")
        os.makedirs(self.backups, exist_ok=True)
        # point the module at the temp trees
        self._orig = (hostops_deploy.CANONICAL_DIR, hostops_deploy.WORKSPACES_DIR, hostops_deploy.BACKUPS_DIR,
                      hostops_deploy.COMPOSE_DIR, hostops_deploy.COMPOSE_NETWORK)
        hostops_deploy.CANONICAL_DIR = self.canonical
        hostops_deploy.WORKSPACES_DIR = self.workspaces
        hostops_deploy.BACKUPS_DIR = self.backups
        # the stack folder and compose network are settings with no default (DEPLOY_COMPOSE_DIR / _NETWORK): a fixture
        # here, since the runner is faked and the gate container sets neither
        hostops_deploy.COMPOSE_DIR = "/stack"
        hostops_deploy.COMPOSE_NETWORK = "stack_net"
        hostops_deploy._CURRENT = None
        self.runner = FakeRunner()
        hostops_deploy.set_runner(self.runner)

    def tearDown(self):
        (hostops_deploy.CANONICAL_DIR, hostops_deploy.WORKSPACES_DIR, hostops_deploy.BACKUPS_DIR,
         hostops_deploy.COMPOSE_DIR, hostops_deploy.COMPOSE_NETWORK) = self._orig
        hostops_deploy._CURRENT = None

    def test_unset_compose_settings_fail_the_deploy_with_the_reason(self):
        # a head with no DEPLOY_COMPOSE_DIR / DEPLOY_COMPOSE_NETWORK cannot run compose: the deploy ends failed, says why
        hostops_deploy.COMPOSE_DIR = ""
        with patch.object(hostops_deploy, "_wait_healthy", return_value=True), \
             patch.object(hostops_deploy, "_smoke", return_value=True):
            hostops_deploy.start("orchestrator-real", "Alex")
            st = self._wait(lambda s: s["phase"] in hostops_deploy._TERMINAL)
        self.assertEqual(st["phase"], "failed", st)
        self.assertIn("DEPLOY_COMPOSE_DIR", st["error"])

    def _wait(self, predicate, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = hostops_deploy.status()
            if predicate(st):
                return st
            time.sleep(0.02)
        self.fail("timed out; last status=%r" % hostops_deploy.status())

    def _canon(self, rel):
        p = os.path.join(self.canonical, rel)
        return Path(p).read_text() if os.path.exists(p) else None

    # --- file manifest primitives ---
    def test_source_files_scope(self):
        files = hostops_deploy._source_files(os.path.join(self.workspaces, "orchestrator-real"))
        self.assertIn("app.py", files)
        self.assertIn("newmod.py", files)
        self.assertIn(os.path.join("tests", "test_x.py"), files)

    def test_file_diff(self):
        changed, added = hostops_deploy._file_diff(
            os.path.join(self.workspaces, "orchestrator-real"), self.canonical)
        self.assertEqual(changed, ["app.py"])
        self.assertEqual(added, ["newmod.py"])

    def test_promote_normalizes_perms_to_world_readable(self):
        # a 0600 workspace file must become 0644 in canonical (else a non-root rebuild bricks the image)
        import stat
        ws = os.path.join(self.workspaces, "orchestrator-real")
        os.chmod(os.path.join(ws, "app.py"), 0o600)
        hostops_deploy._promote(ws, self.canonical, ["app.py"])
        mode = stat.S_IMODE(os.stat(os.path.join(self.canonical, "app.py")).st_mode)
        self.assertTrue(mode & stat.S_IROTH, "promoted file must be world-readable; got %o" % mode)

    def test_snapshot_restore_roundtrip(self):
        dest = os.path.join(self.backups, "snap1")
        hostops_deploy._snapshot(self.canonical, dest)
        # mutate canonical (simulate a promote: overwrite app.py, add newmod.py)
        _write(os.path.join(self.canonical, "app.py"), "v2")
        _write(os.path.join(self.canonical, "newmod.py"), "new")
        hostops_deploy._restore(dest, self.canonical)
        self.assertEqual(self._canon("app.py"), "v1")          # overwrite reverted
        self.assertIsNone(self._canon("newmod.py"))            # added file removed

    # --- full state machine ---
    def test_happy_path(self):
        with patch.object(hostops_deploy, "_wait_healthy", return_value=True), \
             patch.object(hostops_deploy, "_smoke", return_value=True):
            hostops_deploy.start("orchestrator-real", "Alex")
            st = self._wait(lambda s: s["phase"] == "awaiting_promote")
            # live untouched yet; canary built; canonical already promoted
            self.assertTrue(self.runner.saw("build orchestrator-canary"))
            self.assertFalse(self.runner.saw("build orchestrator\n") or self.runner.saw("up -d --force-recreate orchestrator\n"))
            self.assertEqual(self._canon("app.py"), "v2")
            self.assertEqual(self._canon("newmod.py"), "new")
            self.assertTrue(st["awaiting_promote"])
            # promote -> cutover -> live verify -> done
            self.assertEqual(hostops_deploy.promote()["promoting"][:1], st["job"][:1])
            self._wait(lambda s: s["phase"] == "done")
        self.assertTrue(self.runner.saw("up -d --force-recreate orchestrator"))
        self.assertTrue(self.runner.saw("rm -sf orchestrator-canary"))   # canary torn down

    def test_no_diff_rejected(self):
        # make workspace identical to canonical
        ws = os.path.join(self.workspaces, "orchestrator-real")
        _write(os.path.join(ws, "app.py"), "v1")
        os.remove(os.path.join(ws, "newmod.py"))
        hostops_deploy.start("orchestrator-real", "Alex")
        st = self._wait(lambda s: s["phase"] in hostops_deploy._TERMINAL)
        self.assertEqual(st["phase"], "failed")
        self.assertIn("no source changes", st["error"])

    def test_canary_failure_leaves_live_untouched(self):
        with patch.object(hostops_deploy, "_wait_healthy", return_value=False), \
             patch.object(hostops_deploy, "_smoke", return_value=True):
            hostops_deploy.start("orchestrator-real", "Alex")
            st = self._wait(lambda s: s["phase"] in hostops_deploy._TERMINAL)
        self.assertEqual(st["phase"], "failed")
        self.assertFalse(self.runner.saw("up -d --force-recreate orchestrator\n"))  # live never rebuilt
        self.assertEqual(self._canon("app.py"), "v1")          # canonical restored
        self.assertIsNone(self._canon("newmod.py"))

    def test_live_failure_auto_rolls_back(self):
        calls = {"live": 0}

        def health(url, log, cc):
            if url == hostops_deploy.CANARY_HEALTH_URL:
                return True
            calls["live"] += 1
            return calls["live"] > 1   # cutover check fails; post-rollback check passes

        with patch.object(hostops_deploy, "_wait_healthy", side_effect=health), \
             patch.object(hostops_deploy, "_smoke", return_value=True):
            hostops_deploy.start("orchestrator-real", "Alex")
            self._wait(lambda s: s["phase"] == "awaiting_promote")
            hostops_deploy.promote()
            st = self._wait(lambda s: s["phase"] in hostops_deploy._TERMINAL)
        self.assertEqual(st["phase"], "rolled_back")
        self.assertEqual(self._canon("app.py"), "v1")          # rolled back to original
        self.assertIsNone(self._canon("newmod.py"))

    def test_cancel_at_gate_unwinds_clean(self):
        with patch.object(hostops_deploy, "_wait_healthy", return_value=True), \
             patch.object(hostops_deploy, "_smoke", return_value=True):
            hostops_deploy.start("orchestrator-real", "Alex")
            self._wait(lambda s: s["phase"] == "awaiting_promote")
            self.assertTrue(hostops_deploy.cancel()["cancelling"])
            st = self._wait(lambda s: s["phase"] in hostops_deploy._TERMINAL)
        self.assertEqual(st["phase"], "cancelled")
        self.assertFalse(self.runner.saw("up -d --force-recreate orchestrator\n"))  # never cut over to live
        self.assertEqual(self._canon("app.py"), "v1")          # canonical restored
        self.assertIsNone(self._canon("newmod.py"))

    def test_single_deploy_at_a_time(self):
        with patch.object(hostops_deploy, "_wait_healthy", return_value=True), \
             patch.object(hostops_deploy, "_smoke", return_value=True):
            hostops_deploy.start("orchestrator-real", "Alex")
            self._wait(lambda s: s["phase"] == "awaiting_promote")
            second = hostops_deploy.start("orchestrator-real", "Alex")
            self.assertIn("already in progress", second["error"])
            hostops_deploy.cancel()

    def test_api_guards(self):
        self.assertIn("no deploy", hostops_deploy.status()["error"])
        self.assertIn("no deploy", hostops_deploy.promote()["error"])
        self.assertIn("no deploy", hostops_deploy.cancel()["error"])
        self.assertEqual(hostops_deploy.start("../evil", "Alex")["error"], "invalid workspace id")


class SelfDeployClientTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()

    def tearDown(self):
        pending_actions._clear_for_tests()

    def test_self_deploy_is_high_risk_and_gated_until_confirm(self):
        with patch("hostops._request", return_value={"job": "j1", "phase": "preflight"}) as req:
            pv = hostops.self_deploy_preview("orchestrator-real", user="Alex")
            self.assertEqual(pv["status"], "pending")
            self.assertTrue(pv["destructive"])
            self.assertEqual(pv["risk"], "HIGH")
            self.assertIn("CANARY", pv["preview"])
            req.assert_not_called()
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(req.call_args.kwargs.get("json_body"),
                         {"workspace": "orchestrator-real", "user": "Alex"})

    def test_promote_is_high_risk_pending(self):
        pv = hostops.deploy_promote_preview(job="j1", user="Alex")
        self.assertEqual(pv["status"], "pending")
        self.assertEqual(pv["risk"], "HIGH")
        with patch("hostops._request", return_value={"ok": True}) as req:
            pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(req.call_args.kwargs.get("json_body"), {"job": "j1"})

    def test_cancel_is_immediate_not_pending(self):
        with patch("hostops._request", return_value={"ok": True, "cancelling": "j1"}):
            out = hostops.deploy_cancel("j1")
        self.assertIn("Cancellation requested", out)

    def test_status_renders_awaiting_promote(self):
        data = {"job": "j1", "phase": "awaiting_promote", "awaiting_promote": True,
                "diff": {"changed": ["app.py"], "added": []}, "logs": ["a", "b"]}
        with patch("hostops._request", return_value=data):
            out = hostops.deploy_status()
        self.assertIn("awaiting your 'promote to live'", out)
        self.assertIn("phase: awaiting_promote", out)

    def test_status_failure_is_soft(self):
        with patch("hostops._request", side_effect=hostops.HostOpsError("boom")):
            self.assertIn("Could not get deploy status", hostops.deploy_status())


class SelfDeployRiskWiringTests(unittest.TestCase):
    TOOLS = Path("tools.py").read_text(encoding="utf-8")

    def test_self_deploy_metadata_is_high(self):
        level, reasons = pending_actions.assess_risk("self.deploy", True, {"self_deploy": True})
        self.assertEqual(level, "HIGH")
        self.assertTrue(any("redeploys the orchestrator" in r for r in reasons))

    def test_tools_registered_gated_dispatched(self):
        admin = self.TOOLS.split("_ADMIN_TOOLS = {")[1].split("}")[0]
        for n in ("self_deploy", "deploy_status", "deploy_promote", "deploy_cancel"):
            self.assertIn(f'"name": "{n}"', self.TOOLS)
            self.assertIn(f'if name == "{n}":', self.TOOLS)
            self.assertIn(f'"{n}"', admin)
        for prefix in ("BRAIN_TOOLS = [", "HEAVY_TOOLS = ["):
            line = next(l for l in self.TOOLS.splitlines() if l.startswith(prefix))
            self.assertIn("_ENG_PHASE0_TOOLS", line)

    def test_non_admin_denied(self):
        self.assertIn("Permission denied",
                      tools.run_tool("self_deploy", {"workspace": "orchestrator-real"}, "", user="Jordan"))


if __name__ == "__main__":
    unittest.main()
