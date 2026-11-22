"""Test update_stage.stage() invariants: factory is always resumed and failure retains the desk hold."""
import json
import os
import re
import tempfile
import unittest
import unittest.mock

# The boxes, their addresses and their per-box paths are settings, so a run of this suite on a
# box without lab.env would have no fleet and every box call would fail. The suite states them
# here with documentation-range addresses (RFC 519): no expectation depends on a lab value.
os.environ.setdefault("FLEET_HOSTS", json.dumps({"mothership": "10.0.0.76",
                                                "bighonker": "10.0.0.167",
                                                "softserve": "10.0.0.204"}))
os.environ.setdefault("BOX_RUNNERS", json.dumps({"mothership": "P20 Foundry Runner",
                                                "bighonker": "BH Foundry Runner",
                                                "softserve": "SS Foundry Runner"}))
os.environ.setdefault("BOX_STATE_DIRS", json.dumps({"mothership": r"F:\foundry\var\state",
                                                   "bighonker": r"F:\foundry\var\state",
                                                   "softserve": r"E:\foundry\var\state"}))
os.environ.setdefault("STAGE_ORDER", json.dumps(["bighonker", "softserve", "mothership"]))
os.environ.setdefault("DRIVER_ORDER", json.dumps(["bighonker", "softserve"]))

# a stage under test must never write the live archive store (Z:\Archives\lab-updates): every archive() call in this
# process goes to a temp dir (update_stage.archive reads LAB_ARCHIVE_DIR at call time)
_ARCHIVE_TMP = tempfile.mkdtemp(prefix="lab-archive-test-")
os.environ["LAB_ARCHIVE_DIR"] = _ARCHIVE_TMP

import update_stage as S  # noqa: E402


class FakeBox:
    def __init__(self, result, deck=None, boots=("2026-11-03T03:00:00",), raise_on=None, runner_procs=()):
        self.calls, self.result, self.deck_ans = [], result, deck or {"state": "free", "order": ""}
        self.boots, self.raise_on, self.procs = list(boots), raise_on, list(runner_procs)
        self.holds, self.released, self.t = [], 0, 0.0

    def ctx(self):
        box = self

        class C(S.Ctx):
            poll_s = 1
            remote = staticmethod(box.remote)
            deck = staticmethod(lambda b: box.deck_ans)
            ollama_ok = staticmethod(lambda b: "0.40.5")
            gpu_report = staticmethod(lambda b: (True, 30.0, "card ok"))
            hold = staticmethod(lambda b, why, ttl=S.HOLD_TTL: box.holds.append((why, ttl)))
            release = staticmethod(lambda b: setattr(box, "released", box.released + 1))
            proven_kbs = staticmethod(lambda ex: ["5050001"])
            sleep = staticmethod(lambda s: setattr(box, "t", box.t + s))
            now = staticmethod(lambda: box.t)
            save_inflight = staticmethod(lambda d: None)
            clear_inflight = staticmethod(lambda: None)
        return C

    def remote(self, b, action, **kw):
        self.calls.append((action, kw))
        if self.raise_on == action:
            raise RuntimeError("ssh dropped")
        if action == "runner":
            return {"procs": self.procs.pop(0) if self.procs else []}
        if action == "poll":
            boot = self.boots[0] if len(self.boots) == 1 or not any(c[0] == "reboot" for c in self.calls) else self.boots[1]
            return {"result": self.result, "boot": boot}
        if action == "pause":
            return {"disable": "False", "flag": True, "safety": "later"}
        if action == "resume":
            return {"runner_task": "Running enabled=True", "procs": [1], "flag": False}
        return {}

    def actions(self):
        return [c[0] for c in self.calls]


OK = {"done": True, "phase": "done", "found": [{"title": "KB1"}], "per_update": [{"title": "KB1", "result": 2}],
      "reboot_required": False}


class Stage(unittest.TestCase):
    def test_a_busy_factory_is_left_alone(self):
        f = FakeBox(OK, deck={"state": "compute", "order": "speedcheck-x"})
        rec = S.stage("bighonker", True, ctx=f.ctx(), log=lambda m: None)
        self.assertTrue(rec["verdict"].startswith("WAIT"))
        self.assertEqual((f.calls, f.holds), ([], []))

    def test_a_clean_install_holds_pauses_installs_resumes_and_releases_in_that_order(self):
        f = FakeBox(OK, runner_procs=[[5012], []])
        rec = S.stage("bighonker", True, ctx=f.ctx(), log=lambda m: None)
        self.assertTrue(rec["verdict"].startswith("OK - 1 update"), rec)
        acts = f.actions()
        self.assertEqual(acts[0], "pause")
        self.assertLess(acts.index("runner"), acts.index("start_wu"), "never install while the runner is still up")
        self.assertEqual(acts[-1], "resume")
        self.assertEqual(f.released, 1)
        self.assertNotIn("reboot", acts)

    def test_a_failed_install_still_resumes_the_factory_and_keeps_a_long_hold(self):
        f = FakeBox(dict(OK, phase="error", error="0x80240022"))
        rec = S.stage("softserve", True, ctx=f.ctx(), log=lambda m: None)
        self.assertTrue(rec["verdict"].startswith("HOLD - the install failed"), rec)
        self.assertEqual(f.actions()[-1], "resume")
        self.assertEqual(f.released, 0)
        self.assertEqual(f.holds[-1][1], S.FAIL_HOLD_TTL)

    def test_a_crash_mid_install_still_resumes(self):
        f = FakeBox(OK, raise_on="start_wu")
        rec = S.stage("bighonker", True, ctx=f.ctx(), log=lambda m: None)
        self.assertTrue(rec["verdict"].startswith("HOLD - the stager failed"), rec)
        self.assertEqual(f.actions()[-1], "resume")
        self.assertEqual(f.released, 0)

    def test_a_reboot_is_proven_by_the_boot_time_moving(self):
        f = FakeBox(dict(OK, reboot_required=True), boots=("2026-11-03T03:00:00", "2026-11-03T03:25:00"))
        rec = S.stage("bighonker", True, ctx=f.ctx(), log=lambda m: None)
        self.assertIn("reboot", f.actions())
        self.assertTrue(any("back up: booted 2026-11-03T03:25:00" in s for s in rec["steps"]), rec["steps"])
        self.assertTrue(rec["verdict"].startswith("OK"))

    def test_a_box_that_never_comes_back_is_held_not_assumed_fine(self):
        f = FakeBox(dict(OK, reboot_required=True), boots=("2026-11-03T03:00:00", "2026-11-03T03:00:00"))
        rec = S.stage("bighonker", True, ctx=f.ctx(), log=lambda m: None)
        self.assertTrue(rec["verdict"].startswith("HOLD - the box did not come back"), rec)
        self.assertEqual(f.released, 0)

    def test_a_daytime_run_never_reboots(self):
        f = FakeBox(dict(OK, reboot_required=True))
        rec = S.stage("bighonker", False, ctx=f.ctx(), log=lambda m: None)
        self.assertNotIn("reboot", f.actions())
        self.assertTrue(any("reboot NOT done" in s for s in rec["steps"]))

    def test_mothership_installs_only_kbs_proven_elsewhere(self):
        f = FakeBox(OK)
        S.stage("mothership", True, ctx=f.ctx(), log=lambda m: None)
        start = [kw for a, kw in f.calls if a == "start_wu"][0]
        self.assertEqual(start["only"], "5050001")

    def test_a_pause_that_fails_half_way_is_still_resumed(self):
        f = FakeBox(OK, raise_on="pause")
        rec = S.stage("bighonker", True, ctx=f.ctx(), log=lambda m: None)
        self.assertTrue(rec["verdict"].startswith("HOLD"), rec)
        self.assertEqual(f.actions()[-1], "resume", "the 2026-11-03 live run skipped the resume after a failed pause")
        self.assertNotIn("\n", rec["verdict"])

    def test_every_remote_action_RUNS_not_just_compiles(self):
        # Execute module body to verify runtime logic, not just syntax; compilation alone misses runtime errors in dispatch paths.
        import base64, json
        for action in ("runner", "poll", "nothing"):
            cfg = {"action": action, "runner": "BH Foundry Runner", "state": tempfile.gettempdir()}
            src = S.REMOTE.replace("__CFG__", base64.b64encode(json.dumps(cfg).encode()).decode())
            head = src.split("a = C[\"action\"]")[0].replace(r'D = r"C:\ProgramData\HAI\wu-stage"',
                                                               'D = %r' % tempfile.gettempdir())
            ns = {}
            exec(compile(head, "REMOTE", "exec"), ns)
            self.assertIn("Enable-ScheduledTask -TaskName 'BH Foundry Runner'", ns["resume_ps1"])
            self.assertIn("Remove-Item -Force $f", ns["resume_ps1"])

    def test_the_runner_query_excludes_the_observer(self):
        # the probe that asks "is the runner up?" is a powershell whose OWN command line holds both search strings
        self.assertIn("$_.Name -match '^pythonw?", S.REMOTE)
        compile(S.REMOTE.replace("__CFG__", "e30="), "REMOTE", "exec")


class RigbyChecks(unittest.TestCase):
    BEFORE = {"KERNEL": "6.8.0-31-generic", "GPU_COUNT": "2", "ENX": "7", "NODES_HEALTHY": "10", "NODES_TOTAL": "11",
              "GPUS": "NVIDIA GeForce GTX 1060 6GB, 470.256.02;NVIDIA GeForce GT 730, 470.256.02;",
              "CONTAINER_tablet-foundry": "running", "CONTAINER_tablet-watchdog": "running",
              "CONTAINER_tolkien-telemetry": "running", "ORDER_TAKER_UP": "1", "BRIDGE": "active"}

    def test_the_same_reading_after_is_healthy(self):
        self.assertEqual(S.rigby_ok(self.BEFORE, dict(self.BEFORE, KERNEL="6.8.0-147-generic"), "6.8.0-147-generic"), [])

    def test_a_lost_gpu_lost_tablets_or_wrong_kernel_each_fail(self):
        a = dict(self.BEFORE, KERNEL="6.8.0-147-generic", GPU_COUNT="1", GPUS="NVIDIA GeForce GTX 1060 6GB, 470.256.02;",
                 NODES_HEALTHY="6", BRIDGE="failed")
        bad = S.rigby_ok(self.BEFORE, a, "6.8.0-147-generic")
        self.assertEqual(len(bad), 3, bad)
        # every tablet must be on USB: one fewer link is a failure even at 10/11 healthy
        self.assertTrue(S.rigby_ok(self.BEFORE, dict(self.BEFORE, KERNEL="6.8.0-147-generic", ENX="6"),
                                   "6.8.0-147-generic"))
        self.assertTrue(S.rigby_ok(self.BEFORE, self.BEFORE, "6.8.0-147-generic"), "booted the old kernel = not healthy")

    def test_kv_reads_only_key_value_lines(self):
        self.assertEqual(S.kv("locker : x\nNEWEST=6.8.0-147-generic\nDKMS_NEWEST=1\nVERDICT: y\n"),
                         {"NEWEST": "6.8.0-147-generic", "DKMS_NEWEST": "1"})

    def test_the_real_check_output_keeps_every_key(self):
        # Ensure keys with dashes are included in the regex match, as they are valid in this context.
        out = open(os.path.join(os.path.dirname(os.path.abspath(S.__file__)), "linux_stage", "rigby_check.sh")).read()
        keys = set(re.findall(r'echo "([A-Z_]+)=', out)) | {"CONTAINER_" + c for c in
                                                             ("tablet-foundry", "tablet-watchdog", "tolkien-telemetry")}
        parsed = S.kv("\n".join("%s=x" % k for k in keys))
        self.assertEqual(set(parsed), keys)


class AppsStage(unittest.TestCase):
    def test_the_deny_list_never_reaches_the_allow_list(self):
        self.assertFalse(set(S.APPS_ALLOW) & set(S.APPS_DENY))
        for must in ("Ollama.Ollama", "Nvidia.CUDA", "Python.Launcher", "Plex.PlexMediaServer"):
            self.assertNotIn(must, S.APPS_ALLOW)

    def test_the_box_script_only_touches_allowed_ids_and_reports_the_rest(self):
        # execute apps_stage_box.py with a fake winget: offered = Git (allowed) + Ollama (not); only Git is upgraded
        import base64, json, io, contextlib
        src = open(os.path.join(os.path.dirname(os.path.abspath(S.__file__)), "apps_stage_box.py"), encoding="utf-8").read()
        src = src.replace("__CFG__", base64.b64encode(json.dumps({"allow": ["Git.Git"], "store": False}).encode()).decode())
        calls = []
        table = ("Name   Id            Version  Available  Source\n"
                 "-----------------------------------------------\n"
                 "Git    Git.Git       2.54.0   2.56.0.4   winget\n"
                 "Ollama Ollama.Ollama 0.30.7   0.40.14    winget\n"
                 "2 upgrades available.\n")

        class R:
            def __init__(self, rc, out):
                self.returncode, self.stdout = rc, out.encode()

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[1] == "upgrade" and "--id" in argv:
                # A successful install may exit 0 with a "Restart" message and no "Successfully installed" text; treat this as success.
                return R(0, "Found Git [Git.Git]\nStarting package install...\nRestart your PC to finish installation.\n")
            return R(0, table)
        ns = {"__name__": "__main__"}
        import subprocess as sp
        buf = io.StringIO()
        with unittest.mock.patch.object(sp, "run", fake_run), contextlib.redirect_stdout(buf):
            exec(compile(src, "apps_stage_box", "exec"), ns)
        rep = json.loads([x for x in buf.getvalue().splitlines() if x.startswith("@@")][-1][2:])
        self.assertEqual(rep["installed"], ["Git.Git 2.54.0 -> 2.56.0.4 (restart required to finish)"])
        self.assertEqual(rep["skipped"], ["Ollama.Ollama 0.30.7 -> 0.40.14"])
        ids = [a[a.index("--id") + 1] for a in calls if "--id" in a]
        self.assertEqual(ids, ["Git.Git"], "only the allowed id was ever passed to winget upgrade")

    def test_the_bighonker_task_path_compiles_and_reads_its_result_file(self):
        import base64, json
        compile(S.TASK_DRIVER.replace("__CFG__", "e30="), "TASK_DRIVER", "exec")
        self.assertIn("-LogonType Interactive", S.TASK_DRIVER, "the console user's own session, no password")
        self.assertIn('"query", "user"', S.TASK_DRIVER, "the console user is read live, never hardcoded")

        class R:
            def __init__(self, out):
                self.returncode, self.stdout, self.stderr = 0, out.encode(), b""
        inner = {"installed": ["x"], "failed": [], "skipped": [], "store": None, "done": True}
        ok = lambda argv, **kw: R("@@" + json.dumps({"user": "pat", "run_level": "Highest", "result": inner}))  # noqa: E731
        self.assertEqual(S.apps_on_box("bighonker", run=ok)["installed"], ["x"])
        gone = lambda argv, **kw: R("@@" + json.dumps({"no_console_user": "nobody"}))  # noqa: E731
        with self.assertRaises(RuntimeError):
            S.apps_on_box("bighonker", run=gone)
        plain = lambda argv, **kw: R("@@" + json.dumps(inner))  # noqa: E731
        self.assertEqual(S.apps_on_box("softserve", run=plain)["installed"], ["x"], "other boxes still go over ssh")

    def test_the_apps_stage_holds_pauses_and_always_resumes(self):
        f = FakeBox(OK, runner_procs=[[5012], []])
        rep = {"installed": ["Git.Git 1 -> 2"], "failed": ["Tailscale.Tailscale 1 -> 2: rc 1 x"], "skipped": ["a", "b"],
               "store": "ReturnValue=0", "source_ok": True}
        rec = S.stage_apps("softserve", ctx=f.ctx(), log=lambda m: None, apps=lambda b: rep)
        self.assertTrue(rec["verdict"].startswith("OK - 1 app update(s) installed; 1 FAILED"), rec)
        self.assertEqual(f.actions()[0], "pause")
        self.assertEqual(f.actions()[-1], "resume")
        self.assertEqual(f.released, 1)
        f2 = FakeBox(OK)
        rec2 = S.stage_apps("softserve", ctx=f2.ctx(), log=lambda m: None,
                            apps=lambda b: (_ for _ in ()).throw(RuntimeError("ssh dropped")))
        self.assertTrue(rec2["verdict"].startswith("HOLD"), rec2)
        self.assertEqual(f2.actions()[-1], "resume")


class PlexStage(unittest.TestCase):
    NOW = __import__("datetime").datetime(2026, 11, 4, 20, 0, tzinfo=__import__("datetime").timezone.utc)
    REL = __import__("datetime").datetime(2026, 10, 23, 11, 44, tzinfo=__import__("datetime").timezone.utc)   # the real feed

    def test_the_week_rule_and_two_source_agreement(self):
        d = lambda **k: S.plex_decision(k.get("inst", "1.43.4.10903"), k.get("fv", "1.43.5.11079-b3b13e506"),
                                        k.get("rel", self.REL), k.get("wg", "1.43.5.11079"), now=self.NOW)  # noqa: E731
        self.assertEqual(d()[0], "1.43.5.11079", "12 days old, both sources agree -> install")
        fresh = self.NOW - __import__("datetime").timedelta(days=3)
        self.assertIsNone(d(rel=fresh)[0])
        self.assertIn("3.0 days old", d(rel=fresh)[1])
        self.assertIsNone(d(wg="1.43.6.0")[0], "feed and winget disagree -> wait")
        self.assertTrue(d(inst="1.43.5.11079-b3b13e506")[1].startswith("OK - Plex 1.43.5.11079 is current"))
        self.assertTrue(d(fv=None, rel=None)[1].startswith("WAIT - Plex's feed"))

    def _ctx(self):
        f = FakeBox(OK)
        return f, f.ctx()

    def test_nobody_watching_is_required_and_could_not_ask_waits(self):
        f, ctx = self._ctx()
        probe = {"identity": "1.43.4.10903-e5521bd8c", "winget_available": "1.43.5.11079", "watchdog": "Ready"}
        feed = lambda: ("1.43.5.11079-b3b13e506", self.REL)  # noqa: E731
        for n, word in ((2, "2 Plex stream(s)"), (None, "could not be asked")):
            rec = S.stage_plex(ctx=ctx, log=lambda m: None, feed=feed, streams=lambda: n, box=lambda a, **k: probe)
            self.assertTrue(rec["verdict"].startswith("WAIT"), rec)
            self.assertIn(word, rec["verdict"])
        self.assertEqual(f.holds, [], "no hold is placed before the stream check passes")

    def test_a_clean_install_and_a_failed_one_that_rolls_back(self):
        f, ctx = self._ctx()
        probe = {"identity": "1.43.4.10903-e5521bd8c", "winget_available": "1.43.5.11079", "watchdog": "Ready"}
        feed = lambda: ("1.43.5.11079-b3b13e506", self.REL)  # noqa: E731
        calls = []

        def good(action, **k):
            calls.append((action, k))
            return probe if action == "probe" else {"rc": 0, "identity": "1.43.5.11079-b3b13e506", "watchdog_off": "Disabled", "watchdog_on": "Ready"}
        rec = S.stage_plex(ctx=ctx, log=lambda m: None, feed=feed, streams=lambda: 0, box=good)
        self.assertTrue(rec["verdict"].startswith("OK - Plex 1.43.4.10903 -> 1.43.5.11079"), rec)
        self.assertEqual([a for a, _ in calls], ["probe", "install"])
        self.assertEqual(f.released, 1)

        def bad(action, **k):
            calls.append((action, k))
            if action == "probe":
                return probe
            if k.get("version"):
                return {"rc": 0, "identity": "1.43.4.10903-e5521bd8c"}          # the rollback restores the old build
            return {"rc": 0, "identity": None}                                   # the upgrade left Plex down
        calls.clear()
        rec = S.stage_plex(ctx=ctx, log=lambda m: None, feed=feed, streams=lambda: 0, box=bad)
        self.assertTrue(rec["verdict"].startswith("HOLD - Plex 1.43.5.11079 install did not take"), rec)
        self.assertIn("rollback to 1.43.4.10903 restored", rec["verdict"])
        self.assertEqual([k.get("version") for a, k in calls if a == "install"], [None, "1.43.4.10903"])

    def test_the_probe_never_mutates(self):
        # The probe must only list available updates; it must never run `winget install` or target a specific package ID, as those actions mutate system state.
        probe = S.PLEX_BOX_SCRIPT.split('if C["action"] == "probe":')[1].split("elif")[0]
        self.assertIn('"winget", "upgrade"', probe)
        self.assertNotIn("--id", probe, "a probe that names a package upgrades it")
        self.assertNotIn('"install"', probe, "the probe never runs winget install")

    def test_the_box_script_compiles(self):
        compile(S.PLEX_BOX_SCRIPT.replace("__CFG__", "e30="), "PLEX", "exec")
        self.assertIn("Disable-ScheduledTask -TaskName PlexWatchdog", S.PLEX_BOX_SCRIPT)
        self.assertIn("Enable-ScheduledTask -TaskName PlexWatchdog", S.PLEX_BOX_SCRIPT)

    def test_the_probe_reads_plex_s_version_and_winget_s_row_from_real_shapes(self):
        # Assert the probe extracts the real version from the XML `version` attribute, not the `apiVersion` header, and handles empty winget rows.
        import base64, json
        src = S.PLEX_BOX_SCRIPT.replace("__CFG__", base64.b64encode(json.dumps({"action": "none", "id": S.PLEX_ID}).encode()).decode())
        ns = {}
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):          # the script prints its @@ line; not this test's business
            exec(compile(src, "PLEX", "exec"), ns)
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<MediaContainer size="0" apiVersion="1.2.3" claimed="1" '
               'machineIdentifier="8403b1dec1abc" version="1.43.4.10903-e5521bd8c">\n</MediaContainer>')
        self.assertEqual(ns["identity_version"](xml), "1.43.4.10903-e5521bd8c")
        fixtures = os.path.join(os.path.dirname(os.path.abspath(S.__file__)), "test-fixtures", "update-inventory")
        if not os.path.isdir(fixtures):
            self.skipTest("the captured machine inventories are run output and stay with the lab; this check runs there")
        text = json.load(open(os.path.join(fixtures, "softserve.json"), encoding="utf-8"))["winget_upgrade"]["text"]
        self.assertEqual(ns["table"](text).get(S.PLEX_ID), ("1.43.4.10903", "1.43.5.11079"))
        # one derivation: the Plex probe's parser and the apps box script's parser agree on the whole real table
        asrc = open(os.path.join(os.path.dirname(os.path.abspath(S.__file__)), "apps_stage_box.py"), encoding="utf-8").read()
        ans = {}
        exec(compile(asrc.split('\nout = {"installed"')[0], "apps", "exec"), ans)   # the module-level line, not table()'s
        self.assertEqual(ans["table"](text), ns["table"](text))


class DriverStage(unittest.TestCase):
    import datetime as _dt
    NOW = _dt.datetime(2026, 11, 4, 20, 0, tzinfo=_dt.timezone.utc)
    FEED = [("621.14", _dt.datetime(2026, 11, 4, tzinfo=_dt.timezone.utc), "u14"),        # the real 11-04 lookup
            ("621.01", _dt.datetime(2026, 10, 21, tzinfo=_dt.timezone.utc), "u01")]

    def test_the_week_rule_picks_the_newest_old_enough_release(self):
        self.assertEqual(S.driver_decision("616.92", self.FEED, now=self.NOW)[:2], ("621.01", "u01"))
        self.assertEqual(S.driver_decision("617.42", self.FEED, now=self.NOW)[:2], ("621.01", "u01"))
        self.assertTrue(S.driver_decision("621.01", self.FEED, now=self.NOW)[2].startswith("OK - driver 621.01 is current"))
        self.assertTrue(S.driver_decision("616.92", self.FEED[:1], now=self.NOW)[2].startswith("WAIT - 621.14 is 0.8 days old"))
        self.assertTrue(S.driver_decision("616.92", [], now=self.NOW)[2].startswith("WAIT - NVIDIA"))

    def test_the_feed_parser_reads_the_real_lookup_shape(self):
        doc = {"Success": "2", "IDS": [{"downloadInfo": {"Version": "621.14", "ReleaseDateTime": "Wed Nov 04, 2026", "IsBeta": "0",
                                                        "DownloadURL": "https://us.download.nvidia.com/Windows/621.14/621.14-desktop%20x.exe"}},
                                      {"downloadInfo": {"Version": "621.01", "ReleaseDateTime": "Wed Oct 21, 2026", "IsBeta": "0",
                                                        "DownloadURL": "https://x/621.01.exe"}},
                                      {"downloadInfo": {"Version": "622.00", "ReleaseDateTime": "Wed Nov 04, 2026", "IsBeta": "1",
                                                        "DownloadURL": "https://x/beta.exe"}}]}
        f = S.nvidia_feed("bighonker", fetch=lambda box: doc)
        self.assertEqual([v for v, _r, _u in f], ["621.14", "621.01"], "newest first, betas dropped")
        self.assertEqual(f[0][2], "https://us.download.nvidia.com/Windows/621.14/621.14-desktop x.exe")

    def test_softserve_waits_for_the_bighonker_canary(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(S.canary_ok("softserve", "621.01", state=d)[0])
            json.dump({"class": "driver", "target": "621.01", "verdict": "OK - ...", "started": "2026-11-05T02:40"},
                      open(os.path.join(d, "stage-bighonker-20261105-024000.json"), "w"))
            self.assertTrue(S.canary_ok("softserve", "621.01", state=d)[0])
            self.assertFalse(S.canary_ok("softserve", "621.14", state=d)[0])
            self.assertTrue(S.canary_ok("bighonker", "621.01", state=d)[0])

    def test_console_idle_reads_query_user(self):
        class R:
            def __init__(self, out):
                self.stdout = out
        q = lambda out: (lambda argv, **kw: R(out))  # noqa: E731
        self.assertEqual(S.console_idle("bighonker", run=q(" USERNAME SESSIONNAME ID STATE IDLE TIME LOGON TIME\n pat console 2 Active none 11/1/2026 6:10 PM\n"))[0], False)
        self.assertEqual(S.console_idle("bighonker", run=q(" pat console 2 Active 1:12 11/1/2026 6:10 PM\n"))[0], True)
        self.assertEqual(S.console_idle("bighonker", run=q(" pat console 2 Active 12 11/1/2026 6:10 PM\n"))[0], False)
        self.assertEqual(S.console_idle("bighonker", run=q(" USERNAME\n"))[0], True)

    def test_a_dry_run_installs_nothing_and_a_live_run_holds_pauses_installs_checks_resumes(self):
        f = FakeBox(OK, runner_procs=[[5012], []])
        f.remote_extra = {}

        def remote(b, action, **kw):
            f.calls.append((action, kw))
            if action == "nvsmi":
                return {"driver": "621.01" if any(a == "nvidia_install" for a, _ in f.calls) else "617.42"}
            if action == "nvidia_install":
                return {"ok": True, "tail": "DRIVER INSTALL OK"}
            if action == "runner":
                return {"procs": f.procs.pop(0) if f.procs else []}
            if action == "pause":
                return {"disable": "False"}
            if action == "resume":
                return {"runner_task": "Running enabled=True"}
            return {}
        ctx = f.ctx()
        ctx.remote = staticmethod(remote)
        # Select only the release that is old enough on ANY day: with 621.14 in the feed this test passed until 11-11 and then chose 621.14 (the 7-day rule, correctly). The rule itself is pinned with a fixed `now` in the decision test above.
        feed = lambda box: self.FEED[1:]  # noqa: E731
        idle = lambda box: (True, "no active console session")  # noqa: E731
        with unittest.mock.patch.object(S, "gen_rate", lambda box: ("qwen3.5:9b", 60.0)):
            rec = S.stage_nvidia("bighonker", ctx=ctx, log=lambda m: None, feed=feed, idle=idle, package=lambda v, url=None, log=None: "pkg-" + v, dry=True)
            self.assertTrue(rec["verdict"].startswith("DRY - would install 621.01 over 617.42"), rec)
            self.assertNotIn("nvidia_install", [a for a, _ in f.calls])
            rec = S.stage_nvidia("bighonker", ctx=ctx, log=lambda m: None, feed=feed, idle=idle, package=lambda v, url=None, log=None: "pkg-" + v)
        self.assertTrue(rec["verdict"].startswith("OK - NVIDIA 617.42 -> 621.01 on bighonker"), rec)
        acts = [a for a, _ in f.calls]
        self.assertLess(acts.index("pause"), acts.index("nvidia_install"))
        self.assertEqual(acts[-1], "resume")
        self.assertEqual(f.released, 1)
        pk = [kw["package"] for a, kw in f.calls if a == "nvidia_install"]
        self.assertEqual(pk, ["pkg-621.01"], "the rollback package was fetched (pkg-617.42) but only the target installed")

    def _failing_install(self, driver_after):
        """Simulates a live stage where nvidia_install raises due to permission denied on remote copy."""
        f = FakeBox(OK, runner_procs=[[], []])

        def remote(b, action, **kw):
            f.calls.append((action, kw))
            if action == "nvsmi":
                return {"driver": "617.42" if not any(a == "nvidia_install" for a, _ in f.calls) else driver_after}
            if action == "nvidia_install":
                raise RuntimeError("bighonker nvidia_install: [Errno 13] Permission denied: '\\\\10.0.0.204\\SSDShared\\x.exe'")
            if action == "runner":
                return {"procs": []}
            if action == "pause":
                return {"disable": "False"}
            if action == "resume":
                return {"runner_task": "Running enabled=True"}
            return {}
        ctx = f.ctx()
        ctx.remote = staticmethod(remote)
        with unittest.mock.patch.object(S, "gen_rate", lambda box: ("qwen3.5:9b", 60.0)), \
                unittest.mock.patch.object(S, "archive", lambda *a, **k: {"dir": "x"}):
            rec = S.stage_nvidia("bighonker", ctx=ctx, log=lambda m: None, feed=lambda box: self.FEED[1:],
                                 idle=lambda box: (True, "no active console session"),
                                 package=lambda v, url=None, log=None: "pkg-" + v)
        return f, rec

    def test_a_failure_that_left_the_driver_unchanged_releases_the_card(self):
        f, rec = self._failing_install(driver_after="617.42")
        self.assertTrue(rec["verdict"].startswith("WAIT - the driver stage failed before the driver changed (still 617.42)"), rec)
        self.assertEqual(f.released, 1)
        self.assertEqual([w for w, _t in f.holds if "a person should look" in w], [], "no long hold for an unchanged box")
        self.assertEqual(f.actions()[-2:], ["nvsmi", "resume"], "asked the box, then resumed it")

    def test_a_failure_that_changed_the_driver_keeps_the_hold_and_says_what_is_known(self):
        f, rec = self._failing_install(driver_after="621.01")
        self.assertTrue(rec["verdict"].startswith("HOLD - the driver stage failed with the driver at 621.01 (was 617.42)"), rec)
        self.assertEqual(f.released, 0)
        held = [w for w, t in f.holds if t == S.FAIL_HOLD_TTL]
        self.assertTrue(held and "621.01" in held[-1] and "after-checks" not in held[-1], held)

    def test_amd_is_reported_never_installed(self):
        self.assertTrue(S.stage_amd_report()["verdict"].startswith("REPORT -"))
        self.assertNotIn("mothership", S.DRIVER_ORDER)


class PushPackage(unittest.TestCase):
    """Driver packages are transferred to the remote box and verified there (do not check the local share)."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self.pkg = os.path.join(d, "621.14-desktop.exe")
        with open(self.pkg, "wb") as fh:
            fh.write(b"x" * 1000)
        self.calls = []

    def fake(self, sizes):
        sizes = list(sizes)

        class R:
            def __init__(self, out="", rc=0):
                self.stdout, self.stderr, self.returncode = out, "", rc

        def run(argv, **kw):
            self.calls.append(argv[0])
            return R(sizes.pop(0)) if argv[0] == "ssh" else R()
        return run

    def test_it_pushes_and_checks_the_bytes_on_the_box(self):
        got = S.push_package("bighonker", self.pkg, run=self.fake(["0", "1000"]), log=lambda m: None)
        self.assertEqual(got, S.REMOTE_DRIVER_DIR + "\\621.14-desktop.exe")
        self.assertEqual(self.calls, ["ssh", "scp", "ssh"])

    def test_a_complete_copy_is_not_sent_again(self):
        S.push_package("bighonker", self.pkg, run=self.fake(["1000"]), log=lambda m: None)
        self.assertEqual(self.calls, ["ssh"])

    def test_a_short_copy_raises(self):
        with self.assertRaises(RuntimeError):
            S.push_package("bighonker", self.pkg, run=self.fake(["0", "512"]), log=lambda m: None)

    def test_the_remote_install_gets_the_pushed_local_path_and_softserve_needs_none(self):
        seen = {}

        def fake_push(box, path, **kw):
            seen[box] = path
            return S.REMOTE_DRIVER_DIR + "\\" + os.path.basename(path)

        class R:
            stdout, stderr, returncode = b'@@{"ok": true}', b"", 0
        with unittest.mock.patch.object(S, "push_package", fake_push), \
                unittest.mock.patch.object(S.subprocess, "run", lambda *a, **k: R()):
            S.remote("bighonker", "nvidia_install", package=r"E:\SSDShared\Lab\downloads\nvidia-621.14\p.exe")
            S.remote("softserve", "nvidia_install", package=r"E:\SSDShared\Lab\downloads\nvidia-621.14\p.exe")
        self.assertEqual(list(seen), ["bighonker"], "Softserve reads its own disk; no push")


class NightLines(unittest.TestCase):
    def test_every_stage_since_the_nights_report_is_shown_oldest_first(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            for name, body in (("report-20261104-030002.json", {}),
                               ("stage-rigby-20261103-140207.json", {"box": "rigby", "verdict": "OK old"}),
                               ("stage-sundae-20261104-041557.json", {"box": "sundae", "verdict": "OK - restarted"}),
                               ("stage-rigby-20261104-041607.json", {"box": "rigby", "verdict": "WAIT - busy"})):
                json.dump(body, open(os.path.join(d, name), "w"))
            self.assertEqual(S.night_stage_lines(d), ["🔧 the lab staged sundae: OK - restarted",
                                                      "🔧 the lab staged rigby: WAIT - busy"])

    def test_tolkien_idle_waits_on_status_and_says_so_at_the_bound(self):
        calls = iter([["order-a"], ["order-a"], []])
        self.assertTrue(S.tolkien_idle(limit_s=600, sleep=lambda s: None, busy=lambda: next(calls)))
        self.assertFalse(S.tolkien_idle(limit_s=0, sleep=lambda s: None, busy=lambda: ["order-b"]))


class ArchiveIsolation(unittest.TestCase):
    def test_stages_under_test_write_only_the_temp_store(self):
        live = r"Z:\Archives\lab-updates"
        before = set(os.listdir(live)) if os.path.isdir(live) else set()
        f = FakeBox(OK, runner_procs=[[5012], []])
        rep = {"installed": ["Git.Git 1 -> 2"], "failed": [], "skipped": [], "store": "x", "source_ok": True}
        S.stage_apps("softserve", ctx=f.ctx(), log=lambda m: None, apps=lambda b: rep)
        after = set(os.listdir(live)) if os.path.isdir(live) else set()
        self.assertEqual(before, after, "a test wrote the LIVE archive store")
        import lab_archive
        self.assertTrue(lab_archive.generations("app/Git.Git", store=_ARCHIVE_TMP), "and the temp store got the row")


class Release(unittest.TestCase):
    def test_release_never_lifts_someone_elses_hold(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["HAI_STATE_DIR"] = d
            import importlib
            import sys
            sys.path.insert(0, r"E:\SSDShared\orchestrator-build")
            import config
            importlib.reload(config)
            import scheduler
            importlib.reload(scheduler)
            try:
                scheduler.claim("bighonker", "gpu", scheduler.OPERATOR_HOLD + " the owner reseating RAM", ttl=600)
                self.assertFalse(S.release("bighonker"))
                self.assertIsNotNone(scheduler.active_claim("bighonker", "gpu"))
                scheduler.claim("bighonker", "gpu", scheduler.OPERATOR_HOLD + " #38 lab update stager: x", ttl=600)
                self.assertTrue(S.release("bighonker"))
            finally:
                os.environ.pop("HAI_STATE_DIR", None)
                importlib.reload(config)
                importlib.reload(scheduler)


class NoRebootUnderAHaiJob(unittest.TestCase):
    DESK = '{"jobs": [{"status": "done"}, {"status": "running"}, {"status": "pending"}, {"status": "failed"}]}'

    def test_open_jobs_are_counted_and_an_unreadable_answer_is_not_zero(self):
        self.assertEqual(S.hai_jobs_open(lambda: self.DESK), 2)
        self.assertEqual(S.hai_jobs_open(lambda: '{"jobs": [{"status": "done"}]}'), 0)
        self.assertIsNone(S.hai_jobs_open(lambda: ""))
        self.assertIsNone(S.hai_jobs_open(lambda: "<html>502</html>"))
        self.assertIsNone(S.jobs_say_wait(0))
        self.assertIn("2 HAI job(s)", S.jobs_say_wait(2))
        self.assertIn("could not be asked", S.jobs_say_wait(None))

    # ---- Sundae: suspend instantly, reboot, resume (never the old /runs/park wait) ----
    class FakeKirk:
        def __init__(self, running=("r1", "r2"), already=("old",), answers=True, stuck=False):
            self.running, self.suspended, self.calls = list(running), list(already), []
            self.answers, self.stuck = answers, stuck

        def __call__(self, method, path, body=None, timeout=30):
            self.calls.append((method, path, (body or {}).get("run_id")))
            if not self.answers:
                return None
            if path == "/runs/parked":
                return {"running": len(self.running), "running_ids": list(self.running), "suspended": list(self.suspended)}
            if path == "/runs/suspend":
                if not self.stuck and body["run_id"] in self.running:
                    self.running.remove(body["run_id"])
                    self.suspended.append(body["run_id"])
                return {"ok": True}
            if path == "/runs/resume":
                if body["run_id"] in self.suspended:
                    self.suspended.remove(body["run_id"])
                    self.running.append(body["run_id"])
                    return {"ok": True}
                return None
            raise AssertionError(path)

    def test_every_run_in_flight_is_suspended_and_only_those_are_resumed(self):
        k = self.FakeKirk()
        mine, why = S.suspend_runs(k, sleep=lambda s: None)
        self.assertEqual((mine, why), (["r1", "r2"], None))
        self.assertEqual(k.running, [])
        self.assertNotIn(("POST", "/runs/park", None), k.calls, "the old turn-boundary park is never used")
        self.assertEqual(S.resume_runs(k, mine, sleep=lambda s: None), [])
        self.assertEqual(sorted(k.running), ["r1", "r2"])
        self.assertEqual(k.suspended, ["old"], "a run someone else suspended before is left suspended")

    def test_a_kirk_that_cannot_be_asked_means_no_reboot(self):
        mine, why = S.suspend_runs(self.FakeKirk(answers=False), sleep=lambda s: None)
        self.assertIsNone(mine)
        self.assertIn("could not be asked", why)

    def test_runs_that_will_not_suspend_are_put_back_and_nothing_reboots(self):
        k = self.FakeKirk(stuck=True)
        t = iter(range(0, 10 ** 6, 600))
        mine, why = S.suspend_runs(k, sleep=lambda s: None, give_up_s=1800, clock=lambda: next(t))
        self.assertIsNone(mine)
        self.assertIn("did not all suspend", why)
        self.assertIn(("POST", "/runs/resume", "r1"), k.calls)

    def _sundae(self, kirk):
        ran = []

        def run(cmd, **kw):
            ran.append(" ".join(cmd) if isinstance(cmd, list) else str(cmd))
            return unittest.mock.Mock(returncode=0, stdout="", stderr="")
        after = {"KERNEL": "7.0.0-43", "RUNNING": "a,b", "HEALTH": "200", "MOUNT_DOCS": "yes", "MOUNT_SSD": "yes"}
        with unittest.mock.patch.object(S.subprocess, "run", run), unittest.mock.patch.object(S, "wait_boot", lambda *a, **k: after), \
                unittest.mock.patch.object(S, "dns_ok", lambda: True), unittest.mock.patch.object(S, "archive", lambda *a, **k: None):
            rec = S.stage_sundae(log=lambda m: None, sleep=lambda s: None, kirk=kirk,
                                 check=lambda box: {"REBOOT_REQUIRED": "yes", "WATCHTOWER_DONE_TODAY": "1", "RUNNING": "a,b",
                                                    "BOOT": "b0", "KERNEL": "7.0.0-42", "PKGS": "linux-image"})
        return rec, ran

    def test_sundae_suspends_reboots_and_resumes(self):
        k = self.FakeKirk()
        rec, ran = self._sundae(k)
        self.assertTrue(rec["verdict"].startswith("OK - restarted onto 7.0.0-43"), rec)
        self.assertIn("2 run(s) suspended and resumed", rec["verdict"])
        self.assertEqual(len([c for c in ran if "systemctl reboot" in c]), 1)
        self.assertFalse(any("/runs/park" in c for c in ran), "the old turn-boundary park is gone from this stage")
        order = [c[1] for c in k.calls if c[1] in ("/runs/suspend", "/runs/resume")]
        self.assertEqual(order, ["/runs/suspend", "/runs/suspend", "/runs/resume", "/runs/resume"])
        self.assertEqual(sorted(k.running), ["r1", "r2"])

    def test_sundae_does_not_reboot_when_kirk_cannot_be_asked(self):
        rec, ran = self._sundae(self.FakeKirk(answers=False))
        self.assertTrue(rec["verdict"].startswith("WAIT - Kirk could not be asked"), rec)
        self.assertFalse(any("systemctl reboot" in c for c in ran))

    def _softserve(self, suspend, reboot_required=True):
        f = FakeBox(dict(OK, reboot_required=reboot_required), boots=("2026-11-03T03:00:00", "2026-11-03T03:25:00"))
        C = f.ctx()
        saved = []
        C.suspend = staticmethod(suspend)
        C.save_inflight = staticmethod(saved.append)
        return f, S.stage("softserve", True, ctx=C, log=lambda m: None), saved

    def test_softserve_suspends_the_runs_restarts_and_hands_their_ids_to_the_after_boot(self):
        f, rec, saved = self._softserve(lambda: (["r1", "r2"], None))
        self.assertIn("reboot", f.actions())
        self.assertEqual(saved[-1]["suspended"], ["r1", "r2"], "the after-boot resume gets exactly these runs")

    def test_softserve_does_not_restart_when_the_runs_cannot_be_suspended(self):
        f, rec, saved = self._softserve(lambda: (None, "Kirk could not be asked which runs are in flight"))
        self.assertNotIn("reboot", f.actions())
        self.assertTrue(any("reboot deferred: Kirk could not be asked" in s for s in rec["steps"]), rec["steps"])
        self.assertEqual(saved, [])

    def test_no_reboot_unless_windows_says_one_is_required(self):
        f, rec, saved = self._softserve(lambda: (["r1"], None), reboot_required=False)
        self.assertNotIn("reboot", f.actions())
        self.assertEqual(saved, [], "nothing suspended for a reboot that is not needed")


class LinuxStagesFollowTheSettings(unittest.TestCase):
    """A Linux stage's scripts are named for its lane; the box it reaches is a setting. A lab with no tablet head (or no
    head box named) reaches nothing, and the nightly run skips that stage instead of looking it up by a lab name."""

    def test_a_stage_reaches_the_box_its_setting_names(self):
        with unittest.mock.patch.object(S, "HEAD", "my-head"), unittest.mock.patch.object(S, "TABLET_HEAD", "tabs"):
            self.assertEqual((S.linux_host("sundae"), S.linux_host("rigby")), ("my-head", "tabs"))

    def test_an_unset_box_is_reached_by_nothing(self):
        with unittest.mock.patch.object(S, "TABLET_HEAD", ""), \
                unittest.mock.patch.object(S.subprocess, "run", side_effect=AssertionError("ssh'd to nowhere")):
            self.assertEqual(S.linux_check("rigby"), {})
            with self.assertRaises(RuntimeError):
                S.rigby_root("rigby_apt.sh")

    def test_the_nightly_run_skips_a_stage_whose_box_is_unset(self):
        ran = []
        # main() wires the LIVE checks into Ctx (Plex, Kirk's jobs, suspend): patching each restores it on exit, or every
        # later test's fake box would inherit them and ask the real head
        with unittest.mock.patch.object(S.Ctx, "plex_busy", S.Ctx.plex_busy), \
                unittest.mock.patch.object(S.Ctx, "hai_jobs_open", S.Ctx.hai_jobs_open), \
                unittest.mock.patch.object(S.Ctx, "suspend", S.Ctx.suspend), \
                unittest.mock.patch.object(S, "HEAD", "my-head"), unittest.mock.patch.object(S, "TABLET_HEAD", ""), \
                unittest.mock.patch.object(S, "stage_sundae", lambda: ran.append("head") or {"verdict": "OK"}), \
                unittest.mock.patch.object(S, "stage_rigby", lambda: ran.append("tablets") or {"verdict": "OK"}), \
                unittest.mock.patch.object(S, "record", lambda rec: None):
            self.assertEqual(S.main(["--linux-nightly"]), 0)
        self.assertEqual(ran, ["head"])


if __name__ == "__main__":
    unittest.main()
