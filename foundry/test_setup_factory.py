"""setup_factory.py lays a factory down from factory_role.json: a dry run changes nothing; --apply with --no-register
writes the code, the folders, the settings and one launcher per service (a gated service skipped when its setting is
unset, a service of another shape or operating system skipped), registers nothing, and reads the worker token from a
file into dm_relay.env, never into lab.env. The shape picks the services and, on Windows, who runs the GPU work."""
import io
import json
import os
import tempfile
import subprocess
import unittest
from unittest import mock

import setup_factory as S

WINDOWS = os.name == "nt"


def _tree():
    t = tempfile.mkdtemp(prefix="hai-tree-")
    for rel in ("foundry/p20_foundry.py", "foundry/p20_health_daemon.py", "foundry/p20_render_worker.py",
                "foundry/p20_stall_rescue.py", "foundry/hai_stall_recorder.py", "foundry/gpu_telemetry_logger.ps1",
                "foundry/foundry_paths.py", "foundry/workspace/a1/a1_task.py", "foundry/hostscripts/ups_power_logger.py",
                "tools/gpu_reporter.py", "tools/gpu_load_reporter.py", "tools/gpu_telemetry_logger_nvidia.ps1",
                "tools/lab_settings.py"):
        p = os.path.join(t, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write("x = 1\n")
    return t


class LayingAFactoryDown(unittest.TestCase):
    def setUp(self):
        self.tree, self.root = _tree(), os.path.join(tempfile.mkdtemp(prefix="hai-factory-"), "f")

    def run_setup(self, *extra, shape="nvidia"):
        return S.main(["--tree", self.tree, "--root", self.root, "--name", "box-1", "--head", "http://head.invalid:8099",
                       "--shape", shape] + list(extra))

    def launchers(self):
        return {os.path.splitext(n)[0][4:] for n in os.listdir(os.path.join(self.root, "etc", "services"))
                if n.startswith("hai_")}

    def test_a_service_that_does_not_register_fails_the_setup(self):
        """The operating system refusing a service makes the setup fail and say so, never report 'set up'."""
        refused = subprocess.CompletedProcess([], 1, stdout="", stderr="Access is denied")
        out = io.StringIO()
        with mock.patch.object(S.subprocess, "run", return_value=refused) as run, mock.patch("sys.stdout", out):
            rc = self.run_setup("--apply", "--units", tempfile.mkdtemp(prefix="hai-units-"))
        self.assertTrue(run.called, "positive control: the registration step ran")
        self.assertEqual(rc, 1)
        self.assertIn("NOT set up", out.getvalue())
        self.assertNotIn("set up at", out.getvalue())

    def test_a_dry_run_changes_nothing(self):
        self.assertEqual(self.run_setup(), 0)
        self.assertFalse(os.path.exists(self.root))

    def test_apply_without_registering_lays_down_code_folders_settings_and_launchers(self):
        token = os.path.join(self.tree, "token.txt")
        open(token, "w").write("  not-a-real-token \n")
        self.assertEqual(self.run_setup("--apply", "--no-register", "--token-file", token), 0)
        for rel in ("bin/p20_foundry.py", "bin/gpu_reporter.py", "bin/lab_settings.py", "bin/ups_power_logger.py",
                    "bin/gpu_telemetry_logger_nvidia.ps1", "workspace/a1/a1_task.py", "jobs/queue", "var/state/telemetry"):
            self.assertTrue(os.path.exists(os.path.join(self.root, rel)), rel)
        lab_env = open(os.path.join(self.root, "etc", "lab.env")).read()
        self.assertIn("HAI_URL=http://head.invalid:8099\n", lab_env)
        self.assertNotIn("not-a-real-token", lab_env, "the token never goes in lab.env")
        self.assertEqual(open(os.path.join(self.root, "etc", "dm_relay.env")).read(), "P20_WORKER_TOKEN=not-a-real-token\n")
        want = {"order_taker", "health", "gpu_reporter", "gpu_load_reporter"} | ({"gpu_telemetry"} if WINDOWS else set())
        self.assertEqual(self.launchers(), want, "the nvidia shape, nothing gated set: no render worker, UPS or cap")

    def test_the_amd_shape_runs_the_stall_rescue_and_recorder_in_place_of_the_health_daemon(self):
        user = ["--user", "lab"] if WINDOWS else []
        self.assertEqual(self.run_setup("--apply", "--no-register", "--set", "FOUNDRY_AI_TOOLKIT=/opt/trainer", *user,
                                        shape="amd"), 0)
        want = {"order_taker", "stall_rescue", "stall_recorder", "gpu_reporter", "gpu_load_reporter"} | \
            ({"gpu_telemetry"} if WINDOWS else set())
        self.assertEqual(self.launchers(), want)
        tele = os.path.join(self.root, "etc", "services", "hai_gpu_telemetry.cmd")
        if WINDOWS:
            self.assertIn("gpu_telemetry_logger.ps1", open(tele).read(), "the amd telemetry logger, not the nvidia one")

    @unittest.skipUnless(WINDOWS, "the logged-on user runs GPU work on Windows only")
    def test_the_amd_shape_needs_the_user_who_runs_its_gpu_work(self):
        with self.assertRaises(SystemExit):
            self.run_setup(shape="amd")

    def test_an_unknown_shape_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_setup(shape="tpu")

    def test_gated_services_are_set_up_when_their_setting_is_given_and_a_setting_fills_an_argument(self):
        self.run_setup("--apply", "--no-register", "--set", "FOUNDRY_COMFY_LAUNCH=/opt/comfy/run.sh",
                       "--set", "FOUNDRY_GPU_POWER_LIMIT_W=180", "--set", "FOUNDRY_UPS_LOGGER=1")
        got = self.launchers()
        self.assertIn("render_worker", got)
        self.assertIn("gpu_power_limit", got)
        self.assertEqual("ups_logger" in got, WINDOWS, "the UPS logger reads a USB device through Windows")
        cap = [n for n in os.listdir(os.path.join(self.root, "etc", "services")) if n.startswith("hai_gpu_power_limit")][0]
        self.assertIn("nvidia-smi -pl 180", open(os.path.join(self.root, "etc", "services", cap)).read())

    def test_the_task_recipe_is_the_labs(self):
        s = {"name": "order-taker", "kind": "daemon", "run": ["p20_foundry.py", "--queue"], "log": "var/log/q.log"}
        ps1 = S.task_ps1("HAI Factory order-taker", r"C:\f\etc\services\hai_order_taker.cmd", s)
        for must in ("-MultipleInstances IgnoreNew", "-RestartCount 3", "New-TimeSpan -Seconds 0", "-AtStartup",
                     "-RepetitionInterval (New-TimeSpan -Minutes 15)", '$t.Repetition.Duration = ""',
                     '-UserId "SYSTEM" -LogonType ServiceAccount'):
            self.assertIn(must, ps1)
        units = S.unit_files("hai-factory-gpu-reporter", "/f/etc/services/hai_gpu_reporter.sh",
                             {"name": "gpu-reporter", "kind": "every", "minutes": 1})
        self.assertIn("OnUnitActiveSec=1min", units["hai-factory-gpu-reporter.timer"])

    def test_gpu_work_run_as_a_user_runs_interactively_from_logon(self):
        s = {"name": "order-taker", "kind": "daemon", "run": ["p20_foundry.py", "--queue"], "log": "var/log/q.log"}
        ps1 = S.task_ps1("HAI Factory order-taker", r"C:\f\etc\services\hai_order_taker.cmd", s, "lab")
        for must in ('-AtLogOn -User "lab"', '-UserId "lab" -LogonType Interactive -RunLevel Highest',
                     "-MultipleInstances IgnoreNew", "-RestartCount 3"):
            self.assertIn(must, ps1)
        self.assertNotIn("SYSTEM", ps1)
        self.assertNotIn("-AtStartup", ps1)

    def test_each_kind_of_service_has_its_command(self):
        py = S.command(r"C:\f", "python.exe", {"run": ["gpu_reporter.py"]})
        ps = S.command(r"C:\f", "python.exe", {"run": ["gpu_telemetry_logger.ps1"]})
        exe = S.command(r"C:\f", "python.exe", {"run": ["nvidia-smi", "-pl", "180"]})
        self.assertTrue(py.startswith('"python.exe" -u "') and py.endswith('gpu_reporter.py"'), py)
        self.assertTrue(ps.startswith("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "), ps)
        self.assertEqual(exe, "nvidia-smi -pl 180")

    def test_every_shape_the_services_name_is_declared(self):
        m = json.load(open(S.MANIFEST, encoding="utf-8"))
        named = {sh for s in m["services"] for sh in s.get("shapes", [])}
        self.assertTrue(named, "positive control: services name shapes")
        self.assertLessEqual(named, set(m["shapes"]))
        for sh in m["shapes"]:
            names = [s["name"] for s in m["services"] if not s.get("shapes") or sh in s["shapes"]]
            self.assertEqual(len(names), len(set(names)), "one service per name within the %s shape" % sh)


class SettingUpAHead(unittest.TestCase):
    """--role head takes the quick-start's steps (settings.env from the example, the folders, the worker token, then
    docker compose up); an existing settings file and token are kept; head+factory links the factory to that head."""

    def setUp(self):
        self.tree = _tree()
        self.base = os.path.join(self.tree, "examples", "one-machine")
        os.makedirs(self.base)
        open(os.path.join(self.base, "docker-compose.yml"), "w").write("services: {}\n")
        open(os.path.join(self.base, "settings.env.example"), "w").write("BRAIN_MODEL=qwen3:8b\n# PC1_REFERENCE_BOX=x\n")

    def head(self, *extra):
        with mock.patch.object(S.subprocess, "run") as run, mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            rc = S.main(["--role", "head", "--tree", self.tree] + list(extra))
        return rc, run, out.getvalue()

    def test_a_dry_run_changes_nothing(self):
        rc, run, out = self.head()
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.base, "settings.env")))
        self.assertFalse(run.called)
        self.assertIn("would start the head: docker compose -f", out)

    def test_apply_writes_the_settings_folders_and_token_and_starts_the_head(self):
        rc, run, _out = self.head("--apply", "--head-set", "PC1_REFERENCE_BOX=bighonker", "--head-set", "OWNER_USER=Alex")
        self.assertEqual(rc, 0)
        text = open(os.path.join(self.base, "settings.env"), encoding="utf-8").read()
        self.assertIn("BRAIN_MODEL=qwen3:8b\n", text)
        self.assertIn("\nPC1_REFERENCE_BOX=bighonker\n", text, "a commented-out setting is set in place")
        self.assertIn("OWNER_USER=Alex\n", text, "a new setting is added")
        for f in ("library", "state", "usercorpora"):
            self.assertTrue(os.path.isdir(os.path.join(self.base, f)), f)
        token = open(os.path.join(self.base, "state", "secrets", "worker_token"), encoding="utf-8").read()
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.assertEqual(run.call_args[0][0], ["docker", "compose", "-f", os.path.join(self.base, "docker-compose.yml"),
                                               "up", "-d", "--build"])

    def test_an_existing_settings_file_and_token_are_kept(self):
        open(os.path.join(self.base, "settings.env"), "w").write("MINE=1\n")
        os.makedirs(os.path.join(self.base, "state", "secrets"))
        open(os.path.join(self.base, "state", "secrets", "worker_token"), "w").write("kept")
        rc, _run, _out = self.head("--apply", "--no-start")
        self.assertEqual(rc, 0)
        self.assertEqual(open(os.path.join(self.base, "settings.env")).read(), "MINE=1\n")
        self.assertEqual(open(os.path.join(self.base, "state", "secrets", "worker_token")).read(), "kept")

    def test_no_start_starts_nothing_and_a_failed_start_fails_the_setup(self):
        _rc, run, _out = self.head("--apply", "--no-start")
        self.assertFalse(run.called)
        with mock.patch.object(S.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "no docker")), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(S.main(["--role", "head", "--tree", self.tree, "--apply"]), 1)

    def test_head_plus_factory_reports_to_that_head_with_its_token(self):
        root = os.path.join(tempfile.mkdtemp(prefix="hai-factory-"), "f")
        with mock.patch.object(S.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = S.main(["--role", "head+factory", "--tree", self.tree, "--root", root, "--name", "my-pc",
                         "--shape", "nvidia", "--apply", "--no-start", "--no-register"])
        self.assertEqual(rc, 0)
        token = open(os.path.join(self.base, "state", "secrets", "worker_token"), encoding="utf-8").read()
        self.assertIn("HAI_URL=http://localhost:8099\n", open(os.path.join(root, "etc", "lab.env")).read())
        self.assertEqual(open(os.path.join(root, "etc", "dm_relay.env")).read(), "P20_WORKER_TOKEN=%s\n" % token)

    def test_a_factory_still_needs_its_root_name_and_shape(self):
        with self.assertRaises(SystemExit):
            S.main(["--tree", self.tree])


class SettingUpTheTabletHead(unittest.TestCase):
    """--role tablet-head: tolkien.env from the example, the folders, the host services when --tablet-units gives the
    installer's argument, then docker compose up; the steps only a person can take are listed first."""

    def setUp(self):
        self.tree = _tree()
        self.base = os.path.join(self.tree, "examples", "tablet-head")
        os.makedirs(self.base)
        open(os.path.join(self.base, "docker-compose.yml"), "w").write("services: {}\n")
        open(os.path.join(self.base, "tolkien.env.example"), "w").write("HAI_URL=\n")
        inst = os.path.join(self.tree, "fellowship", "rigby", "install_rigby_host_units.sh")
        os.makedirs(os.path.dirname(inst))
        open(inst, "w").write("echo DONE\n")
        self.installer = inst

    def run_it(self, *extra):
        with mock.patch.object(S.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = S.main(["--role", "tablet-head", "--tree", self.tree] + list(extra))
        return rc, run, out.getvalue()

    def test_a_dry_run_lists_what_only_a_person_can_do_and_changes_nothing(self):
        rc, run, out = self.run_it()
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("would first, by hand:"), 3)
        self.assertIn("leave the host's services (run again with --tablet-units", out)
        self.assertFalse(os.path.exists(os.path.join(self.base, "tolkien.env")))
        self.assertFalse(run.called)

    def test_apply_writes_its_settings_and_runs_the_host_installer_only_when_given_its_argument(self):
        rc, run, _out = self.run_it("--apply", "--no-start", "--head-set", "HAI_URL=http://head.invalid:8099")
        self.assertEqual(rc, 0)
        self.assertEqual(open(os.path.join(self.base, "tolkien.env")).read(), "HAI_URL=http://head.invalid:8099\n")
        for f in ("secrets", "library"):
            self.assertTrue(os.path.isdir(os.path.join(self.base, f)), f)
        self.assertFalse(run.called)
        rc, run, _out = self.run_it("--apply", "--no-start", "--tablet-units", "dir:user:8090:/models:/mnt/a")
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_args_list[0][0][0], ["bash", self.installer, "dir:user:8090:/models:/mnt/a"])


if __name__ == "__main__":
    unittest.main()
