"""fleet_deploy's remote pause wait. REMOTE is a script run on each factory, so its pieces are tested here by
slicing them out of the string and executing them, the same way the tablet payload HELPER is tested."""
import ast
import datetime
import json
import os
import tempfile
import unittest
import unittest.mock        # used as unittest.mock below: imported here, never borrowed from another test's import

import fleet_deploy as fd


def _pause_outcome():
    src = fd.REMOTE[fd.REMOTE.index("def pause_outcome"):fd.REMOTE.index("_slip = ")]
    ns = {}
    exec(src, ns)
    return ns["pause_outcome"]


class LaunchersAreFoundInTheHostsTree(unittest.TestCase):
    """Each factory's launchers live in the Lab repository as hosts\\<box>\\launchers\\*; the door finds them by listing
    that tree, so no box name is written into the tool."""

    def test_each_launcher_maps_to_its_own_boxs_folder(self):
        with tempfile.TemporaryDirectory() as d:
            for box, names in (("pc-a", ("run_a.cmd", "run_a.vbs")), ("pc-b", ("run_b.cmd",))):
                os.makedirs(os.path.join(d, box, "launchers"))
                for n in names:
                    open(os.path.join(d, box, "launchers", n), "w").close()
            os.makedirs(os.path.join(d, "pc-c"))                     # a box with host scripts but no launchers
            got = fd.launcher_homes(d)
        self.assertEqual(got, {"run_a.cmd": os.path.join(d, "pc-a", "launchers"),
                               "run_a.vbs": os.path.join(d, "pc-a", "launchers"),
                               "run_b.cmd": os.path.join(d, "pc-b", "launchers")})

    def test_no_hosts_tree_adds_nothing(self):
        self.assertEqual(fd.launcher_homes(os.path.join(tempfile.gettempdir(), "no-such-hosts-tree-xyz")), {})


class OutOfTreeHomes(unittest.TestCase):
    """The stall-rescue daemon runs from F:\\ai-toolkit on Mothership only; `bin` copies elsewhere are decoys.
    The table is the FLEET_ELSEWHERE setting; the lab's table is the fixture here, so these tests read no settings file."""
    LAB_TABLE = {"p20_stall_rescue.py": {"mothership": "F:\\ai-toolkit"}, "comfy_launch.bat": {"mothership": "F:\\"},
                 "ollama-watchdog.ps1": {"mothership": "C:\\ProgramData\\HAI"}, "frontier_mode.py": {"mothership": "C:\\ProgramData\\HAI"},
                 "hai_stall_recorder.py": {"mothership": "C:\\ProgramData\\HAI"}, "ups_power_logger.py": {"mothership": "C:\\ProgramData\\HAI"},
                 "gpu_load_reporter.py": {h: "C:\\ProgramData\\HAI" for h in ("mothership", "bighonker", "softserve")},
                 "lab_settings.py": {h: "C:\\ProgramData\\HAI" for h in ("mothership", "bighonker", "softserve")}}

    def setUp(self):
        self._saved = fd.ELSEWHERE
        fd.ELSEWHERE = {n: dict(h) for n, h in self.LAB_TABLE.items()}

    def tearDown(self):
        fd.ELSEWHERE = self._saved

    def test_the_table_is_the_fleet_elsewhere_setting(self):
        with unittest.mock.patch.object(fd._P, "setting_json",
                                        side_effect=lambda n, d=None: {"x.py": {"abox": "Q:\\x"}, "bad": 3} if n == "FLEET_ELSEWHERE" else d):
            self.assertEqual(fd._elsewhere(), {"x.py": {"abox": "Q:\\x"}}, "a row that is not a table is dropped")
        with unittest.mock.patch.object(fd._P, "setting_json", side_effect=lambda n, d=None: d):
            self.assertEqual(fd._elsewhere(), {}, "unset: everything lives in bin")

    def test_comfy_launcher_goes_only_to_its_actual_mothership_home(self):
        take, homes = fd.files_for("mothership", {"comfy_launch.bat": {}})
        self.assertEqual(list(take), ["comfy_launch.bat"])
        self.assertEqual(homes, {"comfy_launch.bat": "F:\\"})
        for host in ("bighonker", "softserve"):
            self.assertEqual(fd.files_for(host, {"comfy_launch.bat": {}})[0], {})

    def test_the_daemon_goes_to_its_real_home_on_mothership(self):
        take, homes = fd.files_for("mothership", {"p20_stall_rescue.py": {}, "p20_foundry.py": {}})
        self.assertEqual(sorted(take), ["p20_foundry.py", "p20_stall_rescue.py"])
        self.assertEqual(homes, {"p20_stall_rescue.py": "F:\\ai-toolkit"})

    def test_boxes_that_do_not_run_it_do_not_get_it(self):
        for host in ("bighonker", "softserve"):
            take, homes = fd.files_for(host, {"p20_stall_rescue.py": {}, "p20_foundry.py": {}})
            self.assertEqual(sorted(take), ["p20_foundry.py"], host)
            self.assertEqual(homes, {}, host)

    def test_gpu_reporter_uses_programdata_on_all_three_factories(self):
        self.assertIn("gpu_load_reporter.py", fd.MIRROR_SUBDIR)
        for host in ("bighonker", "mothership", "softserve"):
            take, homes = fd.files_for(host, {"gpu_load_reporter.py": {}})
            self.assertEqual(list(take), ["gpu_load_reporter.py"])
            self.assertEqual(homes, {"gpu_load_reporter.py": "C:\\ProgramData\\HAI"})

    def test_frontier_mode_ships_from_hostscripts_to_programdata_on_mothership_only(self):
        self.assertTrue(os.path.isfile(os.path.join(fd.MIRROR, fd.MIRROR_SUBDIR["frontier_mode.py"], "frontier_mode.py")))
        take, homes = fd.files_for("mothership", {"frontier_mode.py": {}})
        self.assertEqual(homes, {"frontier_mode.py": "C:\\ProgramData\\HAI"})
        for host in ("bighonker", "softserve"):
            self.assertEqual(fd.files_for(host, {"frontier_mode.py": {}})[0], {}, host)

    def test_the_remote_dest_honours_a_home_and_defaults_to_bin(self):
        src = fd.REMOTE[fd.REMOTE.index("HOMES = {"):fd.REMOTE.index("def ps(")]
        ns = {"os": __import__("os"), "ROOT": "F:\\foundry", "CFG": {"homes": {"p20_stall_rescue.py": "F:\\ai-toolkit"}}}
        exec(src, ns)
        self.assertEqual(ns["dest"]("p20_stall_rescue.py"), __import__("os").path.join("F:\\ai-toolkit", "p20_stall_rescue.py"))
        self.assertEqual(ns["dest"]("p20_foundry.py"), __import__("os").path.join("F:\\foundry", "bin", "p20_foundry.py"))
        ns["CFG"] = {}
        self.assertEqual(ns["dest"]("test_foundry.py"), __import__("os").path.join("F:\\foundry", "tests", "test_foundry.py"))
        self.assertEqual(ns["dest"]("test_foundry_ext_figure.py"),
                         __import__("os").path.join("F:\\foundry", "tests", "test_foundry_ext_figure.py"))


class PauseWait(unittest.TestCase):
    def test_remote_script_parses(self):
        ast.parse(fd.REMOTE)

    def test_runner_gone_is_exited(self):
        self.assertEqual(_pause_outcome()([], None), "exited")

    def test_an_order_that_raced_in_is_waited_out_never_killed(self):
        self.assertEqual(_pause_outcome()([9956, 18868], "hardbench-x state=compute"), "in_flight")

    def test_runner_alive_with_no_order_keeps_waiting(self):
        self.assertIsNone(_pause_outcome()([9956], None))

    def test_a_stale_claim_with_no_runner_is_exited(self):
        self.assertEqual(_pause_outcome()([], "hardbench-x state=compute"), "exited")

    def test_deferred_ends_the_script_before_any_kill(self):
        """No observable behaviour off-box: the in-flight branch must exit before the taskkill escalation."""
        r = fd.REMOTE[fd.REMOTE.index("def pause_outcome"):]      # the runner's section (the worker's comes earlier)
        self.assertLess(r.index('if outcome == "in_flight":'), r.index('"taskkill"'))
        self.assertLess(r.index('if outcome == "in_flight":'), r.index('"schtasks", "/end"'))


def _worker_after_wait():
    src = fd.REMOTE[fd.REMOTE.index("def worker_after_wait"):fd.REMOTE.index("def stated_worker")]
    ns = {}
    exec(src, ns)
    return ns["worker_after_wait"]


class RenderWorkerRestart(unittest.TestCase):
    def test_the_worker_can_restart_while_an_order_runs(self):
        """Ensure worker restart logic is present so it doesn't wait for the order queue to drain."""
        self.assertIn('if RESTART or CFG.get("worker"):', fd.REMOTE)

    """2026-10-26: a p20_render_worker.py deploy printed NEW CODE LIVE while the render worker (not in HOLDERS) kept the
    old code, and a Krea 2 order rendered through the FLUX.2 path. The worker is restarted too - never mid-job."""

    def test_outcomes(self):
        f = _worker_after_wait()
        self.assertEqual(f(False, False), "exited")
        self.assertEqual(f(False, True), "exited")
        self.assertEqual(f(True, True), "deferred", "a worker with a bake/render child is never killed")
        self.assertEqual(f(True, False), "kill_idle")

    def test_mid_job_returns_before_any_kill_and_a_restart_is_requested_on_every_restarting_deploy(self):
        r = fd.REMOTE[fd.REMOTE.index("def restart_worker"):fd.REMOTE.index("if not RESTART:")]
        self.assertLess(r.index('if outcome == "deferred":'), r.index('"taskkill"'))
        self.assertLess(r.index("os.remove(WORKER_FLAG)"), r.index('"schtasks", "/run"'),
                        "the relaunched worker must never read its predecessor's flag")
        self.assertIn('rep["worker"] = restart_worker()', fd.REMOTE)

    def test_a_worker_is_never_stopped_unless_its_own_box_task_can_relaunch_it(self):
        """Worker task names vary by host; killing a worker requires a valid local task to relaunch it."""
        r = fd.REMOTE[fd.REMOTE.index("def restart_worker"):fd.REMOTE.index("if not RESTART:")]
        self.assertLess(r.index("if not task_exists(WORKER_TASK)"), r.index("io.open(WORKER_FLAG"))
        self.assertLess(r.index("if not task_exists(WORKER_TASK)"), r.index('"taskkill"'))
        tasks = {b["host"]: b.get("worker_task", "MISSING") for b in fd.FLEET}
        self.assertEqual(tasks, {"mothership": "P20 Render Worker", "bighonker": "BH Foundry Worker",
                                 "softserve": None})


class StaleClaimIsNotInFlight(unittest.TestCase):
    """Stale claims without a live trainer process must not be treated as in-flight; see CLAIM_STALE_AFTER."""

    def lane_with(self, minutes_ago, procs):
        d = tempfile.mkdtemp(prefix="fd_lane_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        os.makedirs(os.path.join(d, "var", "state"))
        os.makedirs(os.path.join(d, "jobs", "queue"))
        when = (datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)).isoformat()
        with open(os.path.join(d, "var", "state", "CURRENT_RUN.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "alex_krea2_v1_mothership", "state": "training", "updated": when}, f)
        src = fd.REMOTE[fd.REMOTE.index("def lane"):fd.REMOTE.index("# ---- PAUSE-AND-REDEPLOY")]
        ns = {"os": os, "io": __import__("io"), "json": json, "glob": __import__("glob"), "datetime": datetime,
              "ROOT": d, "rep": {}, "CLAIM_STALE_AFTER": 1800, "procs": lambda: procs,
              "boot_epoch": lambda: None}
        exec(src, ns)
        return ns["lane"]()[1], ns["rep"]

    def test_a_stale_claim_with_no_trainer_is_not_in_flight_but_a_fresh_one_or_a_live_trainer_is(self):
        trainer = [{"pid": 2240, "started": "", "cmd": r"venv\python.exe -u run.py D:\x\alex_krea2_v1.yaml"}]
        cur, rep = self.lane_with(120, [])
        self.assertIsNone(cur)
        self.assertIn("claim_stale_no_trainer", rep)
        self.assertIsNotNone(self.lane_with(5, [])[0], "a fresh claim is in flight")
        self.assertIsNotNone(self.lane_with(120, trainer)[0], "a live trainer keeps a stale claim in flight")

    def test_the_threshold_is_the_foundrys(self):
        import re
        src = open(os.path.join(os.path.dirname(os.path.abspath(fd.__file__)), "p20_foundry.py"), encoding="utf-8").read()
        m = re.search(r"^CLAIM_STALE_AFTER = (\d+)", src, re.M)
        self.assertIsNotNone(m)
        self.assertIn("CLAIM_STALE_AFTER = %s" % m.group(1), fd.REMOTE)


class ClaimPredatesBoot(unittest.TestCase):
    """A claim timestamped before the current boot must be rejected, preventing stale CURRENT_RUN locks from blocking deploys after a reboot."""

    def _lane(self, claim_mtime, boot):
        import os
        import json as _json
        import tempfile as _tf
        root = _tf.mkdtemp()
        os.makedirs(os.path.join(root, "var", "state"))
        os.makedirs(os.path.join(root, "jobs", "queue"))
        p = os.path.join(root, "var", "state", "CURRENT_RUN.json")
        with open(p, "w", encoding="utf-8") as f:
            _json.dump({"name": "arm_x", "state": "compute"}, f)
        os.utime(p, (claim_mtime, claim_mtime))
        src = fd.REMOTE[fd.REMOTE.index("def boot_epoch"):fd.REMOTE.index("# ---- PAUSE-AND-REDEPLOY")]
        import datetime as _dt
        import glob as _glob
        import io as _io
        import time as _time
        ns = {"os": os, "json": _json, "io": _io, "glob": _glob, "time": _time, "datetime": _dt,
              "ROOT": root, "rep": {}, "CLAIM_STALE_AFTER": 1800, "procs": lambda: []}
        exec(src, ns)
        ns["boot_epoch"] = lambda: boot
        return ns["lane"](), ns["rep"]

    def test_a_claim_written_before_the_boot_is_not_in_flight(self):
        (q, cur), rep = self._lane(claim_mtime=1000.0, boot=2000.0)
        self.assertIsNone(cur)
        self.assertIn("arm_x", rep["claim_predates_boot"])

    def test_a_claim_written_after_the_boot_is_in_flight(self):
        (q, cur), rep = self._lane(claim_mtime=3000.0, boot=2000.0)
        self.assertIn("arm_x", cur)
        self.assertNotIn("claim_predates_boot", rep)

    def test_cannot_ask_the_boot_time_keeps_the_claim(self):
        (q, cur), rep = self._lane(claim_mtime=1000.0, boot=None)
        self.assertIn("arm_x", cur, "could not ask is never free")


class TolkienStopAnswers(unittest.TestCase):
    """Tolkien waits on its own stop-slip answer, not the claim file, and only requeues what it stopped."""

    def setUp(self):
        import fleet_deploy_tolkien as T
        self.T = T
        self.n = "a1v22-rigby-qwen7b-hai-off.20261006-0200"

    def test_a_running_order_this_deploy_stopped_is_ours(self):
        ln = ("02:31:42  [TFOUNDRY] [DESK] STOPPED %s (running): fleet_deploy 20261006-023252: stop for a deploy "
              "-- shipped 1 finished row(s) flagged stopped:true -> x.results.json" % self.n)
        self.assertEqual(self.T.slip_answers([self.n], ln), {self.n: "stopped"})

    def test_an_already_finished_order_is_not_ours_to_requeue(self):
        ln = ("02:33:46  [TFOUNDRY] [DESK] STOP %s: already finished (retired 2026-10-06 02:31:42 -> "
              "%s.20261006-023142.results.json) - nothing changed" % (self.n, self.n))
        self.assertEqual(self.T.slip_answers([self.n], ln), {self.n: "finished"})

    def test_no_answer_yet_is_absent_and_other_orders_do_not_count(self):
        other = "[DESK] STOPPED a1v22-rigby-coder3b-hai-off.20261006-0155 (running): why"
        self.assertEqual(self.T.slip_answers([self.n], other + "\nidle; 10/11 nodes healthy"), {})

    def test_a_name_that_prefixes_another_does_not_match_it(self):
        ln = "[DESK] STOPPED %s-v2 (running): why" % self.n
        self.assertEqual(self.T.slip_answers([self.n], ln), {})

    def test_the_answers_are_read_from_rigbys_own_clock(self):
        """Answers are timestamped using rigby's own clock to avoid clock skew with remote sources."""
        asked = []
        stamp = self.T.rigby_since(ssh=lambda host, cmd: asked.append((host, cmd)) or (0, "2026-10-26T00:47:25Z"))
        self.assertEqual(stamp, "2026-10-26T00:47:25Z")
        self.assertEqual(asked[0][0], "rigby", "the clock asked is the one the log lines carry")

    def test_an_unreadable_rigby_clock_fails_closed(self):
        for answer in ((255, ""), (0, "Mon Oct 26 01:17:55 AM UTC 2026")):
            with self.assertRaises(RuntimeError):
                self.T.rigby_since(ssh=lambda host, cmd, a=answer: a)


class TolkienStopsEveryRunningOrder(unittest.TestCase):
    """A deploy must stop and requeue every running fleet order, not just the oldest one listed at the top level of the claim."""

    def _claims(self, fleet, rigby):
        import json
        from unittest import mock
        import fleet_deploy_tolkien as T
        files = {T.CURRENT[0]: fleet, T.CURRENT[1]: rigby}

        def ssh(host, cmd, **kw):
            path = cmd.split(" cat ")[1].split(" ")[0]
            return 0, json.dumps(files[path]) if files[path] is not None else ""
        with mock.patch.object(T, "_ssh", side_effect=ssh):
            return T._current_orders()

    def test_every_order_in_the_fleet_claim_and_rigbys(self):
        fleet = {"name": "a", "orders": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
        self.assertEqual(self._claims(fleet, {"name": "r"}), ["a", "b", "c", "r"])

    def test_the_old_single_order_claim_still_reads(self):
        self.assertEqual(self._claims({"name": "a"}, None), ["a"])


class TolkienCustomerStop(unittest.TestCase):
    """A deploy must not requeue an order that the customer has explicitly requested to stop."""

    def setUp(self):
        import fleet_deploy_tolkien as T
        self.T = T
        self.n = "bench-36-r2-pipelined-reading.20261010"
        self.customer = ("21:00:00  [TFOUNDRY] STOP requested for RUNNING %s by %s.stop.json (#36 round 2 is superseded "
                         "by 2b/2c (resized). It came back only through fleet_deploy's pause+requeue; stop it for good - "
                         "do not resume.): no new work will be started; a rigby-lane answer in flight is cut, tablet "
                         "batches in flight finish" % (self.n, self.n))
        self.deploy = ("20:53:40  [TFOUNDRY] STOP requested for RUNNING %s by %s.stop.json (%s): no new work will be "
                       "started; a rigby-lane answer in flight is cut, tablet batches in flight finish"
                       % (self.n, self.n, T.stop_why("20261010-205313")))

    def test_a_customer_stop_is_seen(self):
        self.assertEqual(self.T.customer_stopped([self.n], self.customer), {self.n})

    def test_a_deploys_own_stop_is_not_a_customer_stop(self):
        self.assertEqual(self.T.customer_stopped([self.n], self.deploy), set())

    def test_other_orders_do_not_count(self):
        self.assertEqual(self.T.customer_stopped(["a1v22-other.20261010"], self.customer), set())


class A1ParityAcrossAllFourFactories(unittest.TestCase):
    """Ensure every A1 fleet module ships to rigby or is explicitly declared fleet-only in FLEET_ONLY."""
    FLEET_ONLY = {
        "a1_order.py": "the fleet's order entry point; rigby's is a1_payload.py via Tolkien (same rules, imported)",
        "a1_delegate.py": "fleet helpers are desk orders; rigby delegates to tablets through a1_fellowship.py",
    }

    def test_every_fleet_a1_module_ships_to_rigby_or_is_declared_fleet_only(self):
        import fleet_deploy_tolkien as T
        fleet = {os.path.basename(src) for src in fd.A1_GROUP.values()}     # a source may sit beside a1 (inspect_lab)
        missing = sorted(fleet - set(T.RIGBY_A1) - set(self.FLEET_ONLY))
        self.assertEqual(missing, [], "fleet A1 modules that never reach rigby (ship them, or declare why not)")
        self.assertTrue({"a1_validity.py", "a1_stop.py", "a1_branch.py", "a1_harness.py"} <= set(T.RIGBY_A1))

    def test_shared_modules_come_from_the_same_source_file(self):
        import fleet_deploy_tolkien as T
        shared = 0
        if not fd.A1_SRC:
            self.skipTest("A1_SRC unset on this head: no A1 payload source to compare")
        for box_name, src in fd.A1_GROUP.items():
            name = os.path.basename(src)
            if name in T.RIGBY_A1:
                shared += 1
                self.assertEqual(os.path.normpath(T.rigby_a1_src(name)), os.path.normpath(os.path.join(fd.A1_SRC, src)),
                                 "one source file, both doors")
        self.assertGreaterEqual(shared, 7, "the shared A1 modules (harness, validity, stop, branch, ollama, chat, "
                                           "lab_provider) - a join that matched nothing proves nothing")


class TheRenderToolIsOneSource(unittest.TestCase):
    """Factory copies of render.py and netguard.py are byte-identical to the runner's sources to ensure identical rendering and network policy enforcement."""

    def test_the_factorys_copies_are_the_runners_bytes(self):
        here = os.path.dirname(os.path.abspath(fd.__file__))
        build = os.path.join(os.path.dirname(here), "orchestrator-build")
        for mine, theirs in (("render.py", os.path.join("coding-runner", "render.py")), ("netguard.py", "netguard.py")):
            with open(os.path.join(here, mine), "rb") as a, open(os.path.join(build, theirs), "rb") as b:
                self.assertEqual(a.read(), b.read(), "%s differs from orchestrator-build\\%s" % (mine, theirs))


class RigbyRunsTheFleetsWindowProgram(unittest.TestCase):
    """Rigby's chat window is helper_window's program, baked into Tolkien's image. This test verifies the wiring: it ensures that modules imported by the deployment code are explicitly baked into the Docker image via COPY --from=foundry, preventing drift where code expects a module that isn't present in the final container."""

    def _image_bakes(self):
        import fleet_deploy_tolkien as T
        with open(os.path.join(T.MIRROR, "Dockerfile"), encoding="utf-8") as f:
            lines = [ln.split() for ln in f if ln.startswith("COPY --from=foundry ")]
        self.assertTrue(lines, "zero COPY --from=foundry lines read - the parse is broken, not the image")
        return {name for ln in lines for name in ln[2:-1]}

    def test_the_image_bakes_exactly_what_the_tolkien_deploy_ships(self):
        import fleet_deploy_tolkien as T
        self.assertEqual(self._image_bakes(), set(T.FOUNDRY_SHARED))

    def test_a_shared_file_from_another_tree_is_found_where_fleet_deploy_ships_it(self):
        """gpu_load_reporter.py must be served from the tree fleet_deploy ships it from and match the copy Mothership runs."""
        import fleet_deploy_tolkien as T
        here = os.path.dirname(os.path.abspath(fd.__file__))
        self.assertIn("gpu_load_reporter.py", T.FOUNDRY_SHARED)
        for name, (src, mothership) in T.FOUNDRY_ELSEWHERE.items():
            self.assertEqual(os.path.normcase(os.path.normpath(src)),
                             os.path.normcase(os.path.normpath(os.path.join(here, fd.MIRROR_SUBDIR[name]))), name)
            self.assertEqual(mothership, (fd.ELSEWHERE.get(name) or {}).get(T.SOURCE_BOX, ""), name)
            self.assertTrue(os.path.exists(T.foundry_src(name)), name)

    def test_the_window_program_and_its_imports_are_among_them(self):
        import fleet_deploy_tolkien as T
        here = os.path.dirname(os.path.abspath(fd.__file__))
        with open(os.path.join(here, "helper_window.py"), encoding="utf-8") as f:
            hw = ast.parse(f.read())
        with open(os.path.join(here, "infer_ollama.py"), encoding="utf-8") as f:
            io_ = ast.parse(f.read())
        own = {os.path.splitext(n)[0] for n in os.listdir(here) if n.endswith(".py")}
        # every sibling module the window program imports at module level, and theirs: baked, or Tolkien cannot import it
        need = {"helper_window"}
        for tree in (hw, io_):
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    need |= {n for n in names if n in own}
        self.assertEqual(need, {"helper_window", "infer_ollama", "gpu_spill"}, "the window program's import chain moved")
        self.assertLessEqual({n + ".py" for n in need}, set(T.FOUNDRY_SHARED))
        with open(os.path.join(T.MIRROR, "tolkien_llm.py"), encoding="utf-8") as f:
            self.assertIn("import helper_window as HW", f.read())


class TheLoopRuleIsOneSource(unittest.TestCase):
    """Verifies the loop rule definition is byte-identical between infer_ollama.py and tolkien_llm.py to prevent logic drift."""
    START, END = "# --- LOOP RULE:", "# --- END LOOP RULE"

    def _block(self, path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual((text.count(self.START), text.count(self.END)), (1, 1), "the markers, once each, in %s" % path)
        return text[text.index(self.START):text.index(self.END)]

    def test_the_marked_block_is_identical_in_both_trees(self):
        import fleet_deploy_tolkien as T
        fleet = self._block(os.path.join(os.path.dirname(os.path.abspath(fd.__file__)), "infer_ollama.py"))
        rigby = self._block(os.path.join(T.MIRROR, "tolkien_llm.py"))
        self.assertIn("def repeating(text):", fleet)
        self.assertIn("def looping(text):", fleet)
        self.assertEqual(fleet, rigby)


class RigbyA1Door(unittest.TestCase):
    """`--rigby-a1`: the share Tolkien loads the A1 payload from is gated, pinned, and never reverts a hand edit."""

    def setUp(self):
        import fleet_deploy_tolkien as T
        self.T = T

    def test_every_payload_file_has_a_source(self):
        import os
        missing = [n for n in self.T.RIGBY_A1 if not os.path.exists(self.T.rigby_a1_src(n))]
        self.assertEqual(missing, [])

    def test_the_pin_is_read_and_an_unreadable_pin_is_none(self):
        self.assertEqual(self.T.read_pin(self.T.RIGBY_A1_PIN)[1], 0)
        self.assertGreater(self.T.read_pin(self.T.RIGBY_A1_PIN)[0], 0)
        self.assertIsNone(self.T.read_pin(self.T.RIGBY_A1_PIN + ".nope"))

    def test_the_verdict_needs_a_green_run_of_exactly_the_pinned_size(self):
        v = self.T.suite_verdict
        self.assertEqual(v("....\n----\nRan 52 tests in 3.1s\n\nOK\n", (52, 0)), "ok")
        self.assertIn("PIN MISMATCH", v("Ran 46 tests in 3.1s\n\nOK\n", (52, 0)))          # a module failed to import
        self.assertIn("PIN MISMATCH", v("Ran 52 tests in 3.1s\n\nOK (skipped=1)\n", (52, 0)))
        self.assertIn("not green", v("Ran 52 tests in 3.1s\n\nFAILED (errors=6)\n", (52, 0)))
        self.assertEqual(v("Traceback ...\n", (52, 0)), "no 'Ran N tests' line")
        self.assertEqual(v("Ran 52 tests\n\nOK\n", None), "no readable gate pin")

    def test_a_hand_saved_share_copy_is_never_reverted(self):
        h = self.T.hand_edited
        self.assertTrue(h("hand", "shipped", "src"))
        self.assertFalse(h("shipped", "shipped", "src"), "what the door shipped last is not a hand edit")
        self.assertFalse(h("src", "shipped", "src"))
        self.assertFalse(h(None, None, "src"), "a file not yet in the share is a new file")
        self.assertTrue(h("hand", None, "src"), "no manifest: the deploy decides on mtime")


class TolkienDeployHoldsIntake(unittest.TestCase):
    """Intake must be paused until after the swap completes; turns reopened before the swap are invalid."""

    def setUp(self):
        import fleet_deploy_tolkien as T
        self.T = T

    def test_nothing_starts_from_before_the_stop_until_after_the_swap(self):
        import ast
        import inspect
        fn = next(n for n in ast.walk(ast.parse(inspect.getsource(self.T)))
                  if isinstance(n, ast.FunctionDef) and n.name == "deploy")

        def lines(name):
            return sorted(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name)

        def text(s):
            return sorted(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str) and s in n.value)
        hold, ship, stop, swap = lines("hold_intake")[0], text("docker save")[0], text(".stop.json")[0], text("--force-recreate")[0]
        self.assertLess(ship, hold, "the images ship first, so the hold is only the stop and the swap")
        self.assertLess(hold, stop)
        self.assertLess(stop, swap)
        release = lines("release_intake")
        self.assertTrue(any(r > swap for r in release), release)
        self.assertGreaterEqual(len(release), 2, "the finally releases it on every route")

    def test_the_hold_is_written_and_released_and_neither_can_raise(self):
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "DEPLOY_HOLD")
            with mock.patch.object(self.T, "DEPLOY_HOLD", p):
                self.T.hold_intake("s1", lambda m: None)
                self.assertTrue(os.path.exists(p))
                self.T.release_intake(lambda m: None)
                self.assertFalse(os.path.exists(p))
                self.T.release_intake(lambda m: None)
            with mock.patch.object(self.T, "DEPLOY_HOLD", os.path.join(d, "missing", "x")):
                said = []
                self.T.hold_intake("s1", said.append)
                self.assertIn("could not", said[0])

    def test_a_window_is_kirks_to_reopen_and_any_other_stopped_order_is_requeued(self):
        win = "hai-win-rigby-x_fellowship"
        answers = {win: "stopped", "bench": "stopped", "done": "finished", "alex": "stopped"}
        self.assertEqual(self.T.to_requeue([win, "bench", "done", "alex"], answers, {"alex"}), ["bench"])

    def test_a_stop_is_marked_a_pause_exactly_when_this_deploy_will_requeue_it(self):
        """Pause and requeue must align; mismatch blocks verdicts."""
        import ast
        import inspect
        win = "hai-win-rigby-x_fellowship"
        names = [win, "bench", "alex"]
        self.assertEqual([n for n in names if self.T.comes_back(n, {"alex"})], ["bench"])
        self.assertEqual(self.T.to_requeue(names, {n: "stopped" for n in names}, {"alex"}),
                         [n for n in names if self.T.comes_back(n, {"alex"})], "one derivation")
        fn = next(n for n in ast.walk(ast.parse(inspect.getsource(self.T)))
                  if isinstance(n, ast.FunctionDef) and n.name == "deploy")
        stops = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_slip"
                 and any(isinstance(a, ast.Constant) and a.value == ".stop.json" for a in c.args)]
        self.assertEqual(len(stops), 1, "deploy writes one kind of stop slip")
        body = stops[0].args[2]
        keys = [k.value for k in body.keys]
        self.assertIn("pause", keys)
        self.assertEqual(ast.unparse(body.values[keys.index("pause")]), "comes_back(name, customer)")


class TolkienRequeueRace(unittest.TestCase):
    """Requeue occurs inside the singleton handoff, immediately after recreate. It runs once per order and must never raise, even if triggered from a `finally` block."""

    def setUp(self):
        import fleet_deploy_tolkien as T
        self.T = T

    def test_each_stopped_order_is_requeued_once_and_a_failed_slip_never_raises(self):
        from unittest import mock
        seen, logs = [], []

        def slip(name, suffix, body):
            if name == "boom":
                raise OSError("share gone")
            seen.append((name, suffix, body["action"]))
        requeued = set()
        with mock.patch.object(self.T, "_slip", side_effect=slip):
            self.T.requeue_stopped(["a", "boom", "b"], requeued, "s1", logs.append)
            self.T.requeue_stopped(["a", "boom", "b"], requeued, "s1", logs.append)   # the finally's second pass
            self.T.requeue_stopped(["c"], set(), "s1")                  # the default log exists too (no NameError)
        self.assertEqual(seen, [("a", ".ctl.json", "requeue"), ("b", ".ctl.json", "requeue"),
                                ("c", ".ctl.json", "requeue")])
        self.assertEqual(requeued, {"a", "b"})
        self.assertEqual(sum("REQUEUE SLIP FAILED for boom" in m for m in logs), 2)

    def test_the_requeue_comes_before_the_handoff_wait_in_deploy(self):
        import ast
        import inspect
        fn = next(n for n in ast.walk(ast.parse(inspect.getsource(self.T)))
                  if isinstance(n, ast.FunctionDef) and n.name == "deploy")
        calls = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") ==
                 "requeue_stopped"]
        wait = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "order taker up" in n.value and "grep" in n.value]
        recreate = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and "--force-recreate" in n.value]
        self.assertTrue(calls and wait and recreate, (calls, wait, recreate))
        self.assertTrue(any(recreate[0] < c < wait[0] for c in calls), (recreate, calls, wait))


def _remote_action(root, **cfg):
    """Run the REAL remote script for one stop/requeue action against a temp foundry root; return its report."""
    import base64
    import contextlib
    import io
    import json
    base = {"root": root, "task": "T", "files": {}, "deploy": True, "restart": True, "stamp": "20261017-120000",
            "holders": [], "homes": {}}
    base.update(cfg)
    src = fd.REMOTE.replace("__CFG__", base64.b64encode(json.dumps(base).encode("utf-8")).decode("ascii"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            exec(compile(src, "REMOTE", "exec"), {"__name__": "__remote__"})
        except SystemExit:
            pass
    hit = [x for x in out.getvalue().splitlines() if x.startswith("@@")]
    assert hit, "the action printed no report: %r" % out.getvalue()[-300:]
    return json.loads(hit[-1][2:])


class GpuPauseAndRedeploy(unittest.TestCase):

    def setUp(self):
        import shutil
        import tempfile
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for d in (("jobs", "queue"), ("jobs", "archive"), ("var", "state")):
            os.makedirs(os.path.join(self.root, *d))

    def _write(self, rel, body):
        import json
        p = os.path.join(self.root, *rel)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(body, f)
        return p

    def test_the_stop_names_its_order_and_reports_a_customers_own_stop(self):
        self._write(("jobs", "queue", "old.stop.json"), {"target": "another_order", "why": "x"})
        self._write(("jobs", "queue", "fleet_deploy-1.stop.json"), {"target": "", "why": "fleet_deploy 1: stop for"})
        rep = _remote_action(self.root, action="stop", target="arm_x", why="fleet_deploy W")
        import json
        slip = os.path.join(self.root, "jobs", "queue", "fleet_deploy-20261017-120000.stop.json")
        with open(slip, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"target": "arm_x", "why": "fleet_deploy W", "pause": True},
                             "requeued after the deploy: a pause, so the desk hears no verdict for the stop")
        self.assertEqual(rep["customer_stopped"], [], "another order's slip and a deploy's own slip are not the customer")
        self._write(("jobs", "queue", "alex.stop.json"), {"target": "arm_x", "why": "superseded"})
        self.assertEqual(_remote_action(self.root, action="stop", target="arm_x", why="fleet_deploy W")
                         ["customer_stopped"], ["alex.stop.json"])
        with open(slip, encoding="utf-8") as f:
            self.assertIs(json.load(f)["pause"], False, "its customer stopped it: never requeued, so not a pause")

    def test_a_chat_windows_stop_is_never_a_pause(self):
        """Kirk reopens a parked window on its order's verdict; a window that told the desk nothing would leave him waiting."""
        _remote_action(self.root, action="stop", target="hai-win-mothership-x", why="fleet_deploy W")
        import json
        with open(os.path.join(self.root, "jobs", "queue", "fleet_deploy-20261017-120000.stop.json"), encoding="utf-8") as f:
            self.assertIs(json.load(f)["pause"], False)

    def test_the_stop_pauses_the_lane_first_so_the_next_order_never_starts(self):
        """Stop must write the lane pause slip (zz_fleet_deploy_pause.ctl.json) before the runner checks for queued orders, preventing the next job from starting."""
        import json
        _remote_action(self.root, action="stop", target="arm_x", why="fleet_deploy W")
        q = os.path.join(self.root, "jobs", "queue")
        with open(os.path.join(q, "zz_fleet_deploy_pause.ctl.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["action"], "pause")
        self.assertEqual(sorted(os.listdir(q)), ["fleet_deploy-20261017-120000.stop.json", "zz_fleet_deploy_pause.ctl.json"])

    def test_the_requeue_takes_the_newest_archived_generation_and_refuses_when_there_is_none(self):
        import json
        import time
        self.assertTrue(_remote_action(self.root, action="requeue", target="arm_x", why="w")["result"]
                        .startswith("REQUEUE FAILED"))
        old = self._write(("jobs", "archive", "arm_x.job.json.20261010-100000"), {"name": "arm_x", "gen": 1})
        os.utime(old, (time.time() - 100, time.time() - 100))
        self._write(("jobs", "archive", "arm_x.job.json.20261017-101933"), {"name": "arm_x", "gen": 2})
        self._write(("jobs", "archive", "arm_x.job.state.json"), {"not": "a spec"})
        rep = _remote_action(self.root, action="requeue", target="arm_x", why="w")
        self.assertIn("arm_x.job.json.20261017-101933", rep["result"])
        with open(os.path.join(self.root, "jobs", "queue", "fleet_deploy-20261017-120000-requeue.ctl.json"),
                  encoding="utf-8") as f:
            self.assertEqual(json.load(f)["action"], "requeue")

    def _driver(self, deck, deploy=None, customer=(), calls=None):
        calls = [] if calls is None else calls

        def run(box, cfg):
            calls.append(cfg.get("action") or "deploy")
            if cfg.get("action") == "stop":
                return {"result": "STOP SLIP x", "customer_stopped": list(customer)}
            if cfg.get("action") == "requeue":
                return {"result": "REQUEUE SLIP for arm_x"}
            if deploy:
                return deploy()
            return {"result": "OK - deployed and running new code", "steps": []}
        seq = iter(deck)
        rep = fd.stop_deploy_requeue({"host": "mothership"}, {"files": {"a": 1}}, "arm_x", "S", run=run,
                                     order_on_deck=lambda h: next(seq), sleep=lambda s: None, wait_s=5, poll_s=0)
        return rep, calls

    def test_stop_then_deploy_then_requeue_when_the_lane_frees(self):
        rep, calls = self._driver(["arm_x", "arm_x", ""])
        self.assertEqual(calls, ["stop", "deploy", "requeue"])
        self.assertTrue(rep["result"].startswith("OK"))
        self.assertTrue(any("requeue: REQUEUE SLIP" in s for s in rep["steps"]), rep["steps"])

    def test_an_order_its_customer_stopped_is_never_requeued(self):
        rep, calls = self._driver([""], customer=["alex.stop.json"])
        self.assertEqual(calls, ["stop", "deploy"])
        self.assertTrue(any("NOT requeued" in s for s in rep["steps"]), rep["steps"])

    def test_a_stop_the_factory_never_honours_deploys_nothing_and_requeues_nothing(self):
        import itertools
        rep, calls = self._driver(itertools.repeat("arm_x"))
        self.assertEqual(calls, ["stop"])
        self.assertTrue(rep["result"].startswith("DEFERRED"), rep["result"])

    def test_a_deploy_that_fails_still_gives_the_order_back(self):
        def boom():
            raise RuntimeError("ssh died")
        calls = []
        with self.assertRaises(RuntimeError):
            self._driver([""], deploy=boom, calls=calls)
        self.assertEqual(calls, ["stop", "deploy", "requeue"], "the finally gave the order back")


class FoundrySuiteAfterAGpuDeploy(unittest.TestCase):
    """Exit 0 is green; exit 3 is a pin mismatch; any other exit or no reply fails closed."""

    def test_only_a_clean_exit_is_green_and_no_reply_fails_closed(self):
        self.assertEqual(fd.suite_verdict({"rc": 0}), (True, "GREEN"))
        self.assertEqual(fd.suite_verdict({"rc": 3}), (False, "PIN MISMATCH"))
        self.assertEqual(fd.suite_verdict({"rc": None}), (False, "COULD NOT RUN"))
        self.assertEqual(fd.suite_verdict({"rc": 1}), (False, "RED (exit 1)"))

    def test_the_remote_script_runs_the_runbooks_way_and_compiles(self):
        src = fd.SUITE % r"F:\foundry"
        compile(src, "suite", "exec")
        self.assertIn("error::SyntaxWarning", src)
        self.assertIn("FOUNDRY_ROOT=root", src)
        self.assertIn('os.path.join(root, "tests")', src)

    def test_a_box_that_does_not_answer_is_could_not_run(self):
        with unittest.mock.patch.object(fd.subprocess, "run",
                                        return_value=unittest.mock.Mock(stdout=b"no marker", stderr=b"ssh: refused")):
            s = fd.run_suite({"host": "bighonker", "root": r"F:\foundry"})
        self.assertIsNone(s["rc"])
        self.assertEqual(fd.suite_verdict(s)[1], "COULD NOT RUN")
        with unittest.mock.patch.object(fd.subprocess, "run",
                                        return_value=unittest.mock.Mock(stdout=b'@@{"rc": 0, "tail": ["OK"]}', stderr=b"")):
            self.assertEqual(fd.run_suite({"host": "x", "root": "r"})["rc"], 0)


class RetireDoor(unittest.TestCase):
    """Retire archives first, verifies the copy's hash, then removes the original. Files still imported by other modules are refused and left in place to prevent breaking dependencies."""

    def _run(self, root, names, *sub):
        import base64, contextlib, io
        src = fd.RETIRE.replace("__ARGS__", base64.b64encode(
            json.dumps([root, names, "STAMP"] + list(sub)).encode("utf-8")).decode("ascii"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(src, "RETIRE", "exec"), {"__name__": "__remote__"})
        return json.loads([x for x in buf.getvalue().splitlines() if x.startswith("@@")][-1][2:])["files"]

    def test_archives_then_removes_refuses_an_imported_file_and_reports_absent(self):
        with tempfile.TemporaryDirectory() as root:
            b = os.path.join(root, "bin")
            os.makedirs(b)
            for nm, body in [("stray.py", "x = 1\n"), ("used.py", "y = 2\n"), ("user.py", "from used import y\n")]:
                with open(os.path.join(b, nm), "w") as f:
                    f.write(body)
            got = self._run(root, ["stray.py", "used.py", "gone.py"])
            self.assertTrue(got["stray.py"].startswith("retired"))
            self.assertFalse(os.path.exists(os.path.join(b, "stray.py")))
            with open(os.path.join(b, "_retired", "STAMP", "stray.py")) as f:
                self.assertEqual(f.read(), "x = 1\n")
            self.assertTrue(got["used.py"].startswith("REFUSED - imported by user.py"))
            self.assertTrue(os.path.exists(os.path.join(b, "used.py")))
            self.assertEqual(got["gone.py"], "absent")

    def test_retire_takes_bare_names_only(self):
        self.assertEqual(fd.retire([r"..\x.py"]), 2)

    def test_a_workspace_folder_retires_there_and_bin_is_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            b, w = os.path.join(root, "bin"), os.path.join(root, "workspace", "flashnext")
            os.makedirs(b)
            os.makedirs(w)
            for d in (b, w):
                with open(os.path.join(d, "dup.yaml.example"), "w") as f:
                    f.write("a: 1\n")
            got = self._run(root, ["dup.yaml.example"], "workspace/flashnext")
            self.assertTrue(got["dup.yaml.example"].startswith("retired"))
            self.assertFalse(os.path.exists(os.path.join(w, "dup.yaml.example")))
            self.assertTrue(os.path.exists(os.path.join(w, "_retired", "STAMP", "dup.yaml.example")))
            self.assertTrue(os.path.exists(os.path.join(b, "dup.yaml.example")))
        for sub in ("workspace/..", "workspace", "workspace/a/b", "etc"):
            self.assertEqual(fd.retire(["x.py"], sub=sub), 2, sub)


class AGreenDeployCommitsTheMirror(unittest.TestCase):

    def repo(self, root, name):
        import subprocess
        d = os.path.join(root, name)
        os.makedirs(d)
        for argv in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@t"]):
            subprocess.run(["git", "-C", d] + argv, check=True, capture_output=True)
        return d

    def write(self, d, name, text):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(text)
        return os.path.join(d, name)

    def test_only_the_carried_files_are_committed_each_in_its_own_repo(self):
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            a, b = self.repo(root, "a"), self.repo(root, "b")
            carried = [self.write(a, "x.py", "1"), self.write(b, "y.py", "2")]
            self.write(a, "other.py", "not part of this deploy")
            with unittest.mock.patch("builtins.print"):
                got = fd.commit_mirrors(carried, "Deployed")
            self.assertEqual(len([h for _t, h in got if h]), 2, got)
            tracked = subprocess.run(["git", "-C", a, "ls-files"], capture_output=True, text=True).stdout.split()
            self.assertEqual(tracked, ["x.py"], "a file the deploy did not carry stays uncommitted")
            status = subprocess.run(["git", "-C", a, "status", "--porcelain"], capture_output=True, text=True).stdout
            self.assertIn("other.py", status)

    def test_nothing_new_is_said_and_never_raises(self):
        with tempfile.TemporaryDirectory() as root:
            a = self.repo(root, "a")
            p = self.write(a, "x.py", "1")
            with unittest.mock.patch("builtins.print"):
                fd.commit_mirrors([p], "first")
                again = fd.commit_mirrors([p], "second")
                outside = fd.commit_mirrors([os.path.join(root, "loose.py")], "nowhere")
            self.assertEqual(again, [(os.path.realpath(a), None)])
            self.assertEqual(outside, [])

    def test_a_commit_git_refuses_is_said_never_reported_as_nothing_new(self):
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            a = self.repo(root, "a")
            subprocess.run(["git", "-C", a, "config", "--unset", "user.email"], check=True)
            subprocess.run(["git", "-C", a, "config", "user.useConfigOnly", "true"], check=True)
            p = self.write(a, "x.py", "1")
            with unittest.mock.patch.dict(os.environ, {"GIT_AUTHOR_EMAIL": "", "GIT_COMMITTER_EMAIL": "",
                                                       "EMAIL": ""}), \
                    unittest.mock.patch("builtins.print") as said:
                got = fd.commit_mirrors([p], "Deployed")
            words = " ".join(str(c.args[0]) for c in said.call_args_list)
            self.assertEqual(got, [], "a refused commit is no commit")
            self.assertIn("NOT committed", words)
            self.assertNotIn("nothing new", words)

    def test_a_file_edited_during_the_deploy_is_never_committed_as_deployed(self):
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            a = self.repo(root, "a")
            kept, edited = self.write(a, "kept.py", "1"), self.write(a, "edited.py", "1")
            shipped = fd.mirror_snapshot([kept, edited])
            self.write(a, "edited.py", "2 - written while the deploy ran")
            with unittest.mock.patch("builtins.print") as said:
                got = fd.commit_mirrors([kept, edited], "Deployed", shipped=shipped)
            words = " ".join(str(c.args[0]) for c in said.call_args_list)
            self.assertEqual(len([h for _t, h in got if h]), 1, got)
            tracked = subprocess.run(["git", "-C", a, "ls-files"], capture_output=True, text=True).stdout.split()
            self.assertEqual(tracked, ["kept.py"], "the shipped file is committed; the edited one is not")
            self.assertIn("changed during the deploy - NOT committed", words)

    def test_a_failing_git_never_replaces_the_deploy_verdict(self):
        def boom(*_a, **_k):
            raise OSError("git is gone")
        with unittest.mock.patch("builtins.print"):
            self.assertEqual(fd.commit_mirrors(["x.py"], "Deployed", run=boom), [])


class TheFleetTableIsASetting(unittest.TestCase):
    """fleet_deploy's boxes come from the FLEET_TABLE setting (host -> root, task, worker_task); the box this tool runs
    on is `local` by its own name; no table means no fleet, and main says so instead of acting."""
    TABLE = {"alpha": {"root": "D:\\foundry", "task": "Alpha Runner", "worker_task": "Alpha Worker"},
             "beta": {"root": "E:\\foundry", "task": "Beta Runner", "worker_task": None}}

    def test_the_table_becomes_the_fleet_and_this_box_is_local(self):
        with unittest.mock.patch.object(fd._P, "setting_json", lambda name, default=None: self.TABLE if name == "FLEET_TABLE" else default), \
                unittest.mock.patch.dict(os.environ, {"COMPUTERNAME": "BETA"}):
            fleet = fd._fleet()
        self.assertEqual([(b["host"], b["root"], b["task"], b["worker_task"], b["local"]) for b in fleet],
                         [("alpha", "D:\\foundry", "Alpha Runner", "Alpha Worker", False),
                          ("beta", "E:\\foundry", "Beta Runner", None, True)])

    def test_no_table_means_no_fleet_and_main_refuses(self):
        with unittest.mock.patch.object(fd._P, "setting_json", lambda name, default=None: default):
            self.assertEqual(fd._fleet(), [])
        with unittest.mock.patch.object(fd, "FLEET", []), unittest.mock.patch("builtins.print") as p:
            self.assertEqual(fd.main(), 2)
        self.assertIn("FLEET_TABLE", " ".join(str(a) for c in p.call_args_list for a in c.args))


if __name__ == "__main__":
    unittest.main()

