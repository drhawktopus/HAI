"""Tests for the Mothership gaming-busy probe (Phase 2 chunk 2).

`config` is stdlib-only (checked directly); `mothership` is light (config + notes + subprocess) so its
threshold/fail-open logic is exercised by monkeypatching the SSH probe; `app`/`Dockerfile` wiring is
verified statically."""
import unittest
from pathlib import Path
from unittest import mock

import config

APP = Path("app.py").read_text(encoding="utf-8")
# The Dockerfile is the build context, not copied into the runtime image — guard for in-container runs.
_DF = Path("Dockerfile")
DOCKERFILE = _DF.read_text(encoding="utf-8") if _DF.exists() else ""


class MothershipConfigTests(unittest.TestCase):
    def test_defaults(self):
        """Verifies default config values by running in a clean environment.

        Note: the devices' PC<n>_SSH_* settings are cleared from the environment so the deploy gate's
        override (an .invalid host) cannot mask the true default. A box's SSH target, user and
        key are settings, never literals: with no settings there is no target, so the neutral
        defaults are empty and the probe fails open.
        """
        import os
        import subprocess
        import sys
        import re
        env = {k: v for k, v in os.environ.items() if not re.match(r"PC\d+(_VM\d+)?_(SSH_|MIN_FREE_MB)", k)}
        out = subprocess.run([sys.executable, "-c", "import config as c; print(c.LEAD_BOX_MIN_FREE_MB, "
                              "repr(c.LEAD_BOX_SSH_HOST), repr(c.LEAD_BOX_SSH_USER), repr(c.LEAD_BOX_SSH_KEY))"],
                             capture_output=True, text=True, env=env, cwd=os.path.dirname(config.__file__) or ".",
                             timeout=60)
        self.assertEqual(out.stdout.split(), ["19000", "''", "''", "''"], out.stderr[-400:])

    def test_the_ssh_command_drops_an_unset_key_or_user(self):
        import lead_ssh
        with mock.patch.object(config, "LEAD_BOX_SSH_KEY", ""), mock.patch.object(config, "LEAD_BOX_SSH_USER", ""), \
                mock.patch.object(config, "LEAD_BOX_SSH_HOST", "lead.invalid"):
            self.assertEqual((lead_ssh._key_args(), lead_ssh._target()), ([], "lead.invalid"))
        with mock.patch.object(config, "LEAD_BOX_SSH_KEY", "/k"), mock.patch.object(config, "LEAD_BOX_SSH_USER", "u"), \
                mock.patch.object(config, "LEAD_BOX_SSH_HOST", "lead.invalid"):
            self.assertEqual((lead_ssh._key_args(), lead_ssh._target()), (["-i", "/k"], "u@lead.invalid"))


class GamingBusyTests(unittest.TestCase):
    def test_threshold_and_fail_open(self):
        import lead_ssh
        oc = lead_ssh.ollama_client
        orig_free, orig_loaded = lead_ssh.gpu_free_mb, oc.models_loaded
        try:
            oc.models_loaded = lambda h: []           # nothing in Ollama -> a low GPU looks like a game
            lead_ssh.gpu_free_mb = lambda: None      # undetermined -> fail-open (not busy)
            self.assertFalse(lead_ssh.gaming_busy())
            lead_ssh.gpu_free_mb = lambda: 5000      # low free + NO Ollama model -> a game holds it -> busy
            self.assertTrue(lead_ssh.gaming_busy())
            lead_ssh.gpu_free_mb = lambda: 23000     # plenty free -> not busy
            self.assertFalse(lead_ssh.gaming_busy())
            # Low free BUT an Ollama model is resident -> it's Ollama's VRAM (swappable), NOT a game.
            lead_ssh.gpu_free_mb = lambda: 5000
            oc.models_loaded = lambda h: ["medgemma:27b"]
            self.assertFalse(lead_ssh.gaming_busy())
        finally:
            lead_ssh.gpu_free_mb, oc.models_loaded = orig_free, orig_loaded


class FreeVramComesFromTheBoxsOwnReport(unittest.TestCase):

    def _rec(self, age_s=10, ok=True):
        import datetime
        ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_s)).isoformat()
        return {"util_pct": 1.0, "ts": ts, "gpu_memory": {"card_mb": 24560, "adapter_mb": 23012, "counter_ok": ok}}

    def _free(self, rec):
        import lead_ssh
        import scheduler
        ran = []

        def ssh(cmd, **k):
            ran.append(cmd)
            return mock.Mock(returncode=0, stdout='{"free_mb": 777}', stderr="")
        with mock.patch.object(scheduler, "gpu_load", lambda host: rec), \
                mock.patch.object(lead_ssh.subprocess, "run", ssh):
            return lead_ssh.gpu_free_mb(), len(ran)

    def test_a_fresh_report_answers_with_no_logon_to_the_box(self):
        self.assertEqual(self._free(self._rec()), (1548, 0))

    def test_no_report_an_old_one_or_a_failed_counter_still_asks_the_box(self):
        import scheduler
        for rec in (None, self._rec(age_s=2 * scheduler.USER_CACHE_S + 30), self._rec(ok=False)):
            self.assertEqual(self._free(rec), (777, 1), rec)


class WarmPreferenceTests(unittest.TestCase):
    def test_warm_detection(self):
        import lead_ssh
        oc = lead_ssh.ollama_client
        orig_r, orig_m = oc.reachable, oc.models_loaded
        try:
            oc.reachable = lambda h: False
            self.assertFalse(lead_ssh.warm())            # asleep -> not warm
            oc.reachable = lambda h: True
            oc.models_loaded = lambda h: []
            self.assertFalse(lead_ssh.warm())            # The fixture must use the configured heavy model, not a literal string, to match warm()'s logic against config.HEAVY_MODEL.
            oc.models_loaded = lambda h: [lead_ssh.config.HEAVY_MODEL]
            self.assertTrue(lead_ssh.warm())             # heavy model resident -> warm
        finally:
            oc.reachable, oc.models_loaded = orig_r, orig_m


class RestartOllamaTests(unittest.TestCase):
    def test_restart_ollama_ssh_outcomes(self):
        import lead_ssh
        orig = lead_ssh.subprocess.run

        class _R:
            def __init__(self, rc): self.returncode = rc; self.stdout = ""; self.stderr = ""
        try:
            # box reachable + task triggered -> True, and it runs the documented scheduled task
            captured = {}
            def ok(cmd, **kw): captured["cmd"] = cmd; return _R(0)
            lead_ssh.subprocess.run = ok
            self.assertTrue(lead_ssh.restart_ollama())
            # Sent as a robust -EncodedCommand (no fragile nested quotes through ssh->cmd->powershell)
            self.assertIn("-EncodedCommand", " ".join(captured["cmd"]))
            self.assertIn(lead_ssh._RESTART_PS_B64, " ".join(captured["cmd"]))
            import base64
            self.assertEqual(base64.b64decode(lead_ssh._RESTART_PS_B64).decode("utf-16-le"),
                             "Start-ScheduledTask -TaskName 'Ollama Serve'")
            # box asleep/unreachable (ssh raises) -> False so caller falls through to WOL
            def boom(cmd, **kw): raise OSError("connect timeout")
            lead_ssh.subprocess.run = boom
            self.assertFalse(lead_ssh.restart_ollama())
            # non-zero rc -> False
            lead_ssh.subprocess.run = lambda cmd, **kw: _R(255)
            self.assertFalse(lead_ssh.restart_ollama())
        finally:
            lead_ssh.subprocess.run = orig


class WiringTests(unittest.TestCase):
    def test_ensure_mothership_checks_busy(self):
        self.assertIn("import lead_ssh", APP)
        self.assertIn("if lead_ssh.gaming_busy():", APP)

    def test_ensure_mothership_self_heals_ollama(self):
        # Asserts the self-healing recovery path (restart_ollama) is triggered, not specific implementation details.
        import app
        from unittest import mock
        calls = []
        with mock.patch.object(app.wol, "wake", lambda *_: None), \
                mock.patch.object(app.time, "sleep", lambda *_: None), \
                mock.patch.object(app.ollama_client, "reachable_direct", lambda *_: False), \
                mock.patch.object(app.ollama_client, "port_open", lambda *a, **k: False), \
                mock.patch.object(app.lead_ssh, "restart_ollama", lambda: calls.append(1) or False), \
                mock.patch.object(app.config, "WAKE_TIMEOUT", 0.05), mock.patch.object(app.config, "WAKE_POLL", 0):
            self.assertFalse(app._ensure_lead())
        self.assertEqual(len(calls), 1)

    def test_auto_prefers_warm_mothership(self):
        self.assertIn("def _dispatch_lead(", APP)
        # behaviour: the test below; `_mccoy_lane is None` = McCoy did not decide this turn (not an active user)
        self.assertIn("if not quick_8b and _mccoy_lane is None and _warm_lead_free():", APP)
        self.assertIn('config.LEAD_BOX_LABEL + " already warm"', APP)

    def test_the_warm_shortcut_never_takes_a_claimed_card(self):
        import app
        import comfy_client
        order = {"host": "mothership", "resource": "gpu", "holder": "digest-test-mo_mothership"}
        with mock.patch.object(app.lead_ssh, "warm", return_value=True), \
                mock.patch.object(comfy_client, "render_busy", return_value=False), \
                mock.patch.object(app, "notes"):
            with mock.patch("scheduler.occupied_for", return_value=None):
                self.assertTrue(app._warm_lead_free())
            with mock.patch("scheduler.occupied_for", return_value=order):
                self.assertFalse(app._warm_lead_free())
        with mock.patch.object(app.lead_ssh, "warm", return_value=False):
            self.assertFalse(app._warm_lead_free())

    def test_dispatch_to_mothership_refuses_on_render_busy(self):
        """Verify that _dispatch_to_lead blocks large model loads by default
        when the GPU is busy rendering. Quality-critical modes opt in via
        allow_on_busy=True. This test acts as a lock-in to prevent accidental
        removal of this guard."""
        # Signature has the opt-in param
        self.assertIn("allow_on_busy=False", APP)
        # Entry-point check raises _LeadBusy when render_busy() and not opted-in
        self.assertIn("if not allow_on_busy and _cc.render_busy():", APP)
        self.assertIn("raise _LeadBusy()", APP)
        # The force_lead call site (self-eng/absurd/nursing/force_27b) passes True
        self.assertIn("allow_on_busy=True", APP)

    def test_ssh_client_available(self):
        """The Mothership probe SSHes out, so the SSH client must be present.

        Verified against whichever environment the suite runs in: when the build
        context is available (Dockerfile present) confirm the recipe installs it;
        in the runtime image (Dockerfile not copied in) confirm the `ssh` binary
        is actually on PATH — a stronger check that the install really happened."""
        import shutil
        if DOCKERFILE:
            self.assertIn("openssh-client", DOCKERFILE)
        else:
            self.assertIsNotNone(
                shutil.which("ssh"), "ssh client not on PATH in the runtime image"
            )


class TrainerProbeTests(unittest.TestCase):
    """The probe must detect actual trainers, not any process in the ai-toolkit venv.

    This guards against false positives from unrelated venv processes (e.g., stall rescue,
    render workers) or self-matching PowerShell queries that would cause `queue_training`
    to incorrectly refuse all runs."""

    @staticmethod
    def _query():
        """Just the PowerShell query, not the comments around it — the comments quote the OLD
        pattern on purpose, and a scan of the whole body matches that prose instead of the code."""
        import re
        import lead_ssh
        body = lead_ssh._TRAINER_RUNNING_BODY
        m = re.search(r"ps = \((.*?)\)\n", body, re.S)
        assert m, "could not isolate the probe query"
        return m.group(1)

    def test_probe_is_narrowed_to_the_trainer_entry_point(self):
        q = self._query()
        self.assertIn("run.py", q, "the probe must key on the trainer entry point")
        self.assertIn("Name -eq 'python.exe'", q,
                      "restricting to python.exe is what stops the query matching itself")

    def test_probe_does_not_require_the_ai_toolkit_directory(self):
        """The batch runner `cd`s into the project root before executing, so the live process
        path is relative (`venv\\python.exe ... run.py`) and lacks 'ai-toolkit'.
        Requiring the full path would make the probe blind to running trainers,
        risking concurrent runs on the same GPU."""
        q = self._query()
        self.assertNotIn("ai-toolkit*run.py", q,
                         "requiring the directory makes the probe blind to the real trainer")

    def test_the_always_on_daemons_no_longer_match(self):
        """These two run from the ai-toolkit venv forever. If they match, nothing can ever train."""
        import re
        q = self._query()
        self.assertNotIn("'*ai-toolkit*'", q,
                         "the bare ai-toolkit wildcard matches both always-on daemons")
        self.assertNotIn("run_*.bat", q,
                         "the launcher wildcard also matched the probe's own command line")
        # Simulate PowerShell's -like against the real command lines, so this test fails if the
        # pattern is ever loosened back to something the daemons satisfy.
        m = re.search(r"-like '([^']*run\.py[^']*)'", q)
        self.assertIsNotNone(m, "no run.py pattern found in the probe")
        rx = re.compile(m.group(1).replace("*", ".*"), re.I)
        trainer = r"F:\ai-toolkit\venv\python.exe -u run.py config\concept_flux2_v1.yaml"
        self.assertTrue(rx.search(trainer), "a real trainer would no longer be detected")
        for daemon in (r"F:\ai-toolkit\venv\python.exe  -u F:\ai-toolkit\p20_stall_rescue.py",
                       r"F:\ai-toolkit\venv\python.exe  -u F:\flux2-train\p20_render_worker.py"):
            self.assertFalse(rx.search(daemon), "daemon still matches: %s" % daemon)


if __name__ == "__main__":
    unittest.main()
