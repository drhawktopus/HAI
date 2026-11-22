import json
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import hostops
import hostops_sidecar
import pending_actions
import tools


class SidecarLogicTests(unittest.TestCase):
    def test_demux_framed_stream(self):
        # docker non-TTY frame: [stream(1) 0 0 0 size(4 BE)][payload]
        payload = b"hello world\n"
        frame = bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload
        self.assertEqual(hostops_sidecar._demux_logs(frame), "hello world\n")

    def test_demux_unframed_raw(self):
        self.assertEqual(hostops_sidecar._demux_logs(b"plain tty logs"), "plain tty logs")

    def test_valid_name(self):
        self.assertTrue(hostops_sidecar._valid_name("caddy"))
        self.assertTrue(hostops_sidecar._valid_name("wol-sidecar"))
        self.assertFalse(hostops_sidecar._valid_name("../etc"))
        self.assertFalse(hostops_sidecar._valid_name("a/b"))
        self.assertFalse(hostops_sidecar._valid_name(""))

    def test_protected_defaults(self):
        for c in ("hostops", "orchestrator", "pihole"):
            self.assertIn(c, hostops_sidecar.PROTECTED)


class HostOpsClientTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()

    def tearDown(self):
        pending_actions._clear_for_tests()

    def test_docker_status_filters(self):
        data = {"containers": [{"name": "caddy", "state": "running", "status": "Up 2h"},
                               {"name": "homepage", "state": "running", "status": "Up 2h"}]}
        with patch("hostops._request", return_value=data):
            out = hostops.docker_status("cad")
        self.assertIn("caddy: running", out)
        self.assertNotIn("homepage", out)

    def test_docker_logs(self):
        with patch("hostops._request", return_value={"name": "caddy", "tail": 50, "logs": "line1\nline2"}):
            out = hostops.docker_logs("caddy", 50)
        self.assertIn("last 50 lines", out)
        self.assertIn("line2", out)

    def test_restart_preview_destructive_then_confirms(self):
        with patch("hostops._request", return_value={"restarted": "caddy"}) as req:
            pv = hostops.docker_restart_preview("caddy", user="Alex")
            self.assertEqual(pv["status"], "pending")
            self.assertTrue(pv["destructive"])
            self.assertIn("Restart the 'caddy'", pv["preview"])
            req.assert_not_called()   # nothing happened on preview
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(out["result"], {"restarted": "caddy"})

    def test_restart_refuses_protected(self):
        for c in ("pihole", "orchestrator"):
            self.assertEqual(hostops.docker_restart_preview(c, user="Alex")["status"], "error")

    def test_restart_needs_name(self):
        self.assertEqual(hostops.docker_restart_preview("", user="Alex")["status"], "error")

    def test_request_failure_is_soft(self):
        with patch("hostops._request", side_effect=hostops.HostOpsError("boom")):
            self.assertIn("Could not reach", hostops.docker_status())
            self.assertIn("Could not get logs", hostops.docker_logs("caddy"))

    def test_stop_destructive_refuses_protected_start_benign(self):
        # stop is destructive + refuses protected; start is benign and allowed
        for c in ("pihole", "orchestrator", "hostops"):
            self.assertEqual(hostops.docker_stop_preview(c, user="Alex")["status"], "error")
        with patch("hostops._request", return_value={"stop": "caddy"}):
            pv = hostops.docker_stop_preview("caddy", user="Alex")
        self.assertTrue(pv["destructive"])
        with patch("hostops._request", return_value={"start": "pihole"}):
            pv = hostops.docker_start_preview("pihole", user="Alex")   # start IS allowed on protected
        self.assertEqual(pv["status"], "pending")
        self.assertFalse(pv["destructive"])

    def test_watchtower_run_destructive(self):
        with patch("hostops._request", return_value={"exit_code": 0, "output": "done"}) as req:
            pv = hostops.watchtower_run_preview(user="Alex")
            self.assertTrue(pv["destructive"])
            self.assertIn("RECREATES", pv["preview"])
            pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(req.call_args.kwargs.get("timeout"), config.WATCHTOWER_RUN_TIMEOUT)

    def test_git_status_read(self):
        with patch("hostops._request", return_value={"output": " M caddy/Caddyfile\n---\nabc123 prev"}):
            out = hostops.git_status()
        self.assertIn("Caddyfile", out)

    def test_git_commit_local_no_push(self):
        with patch("hostops._request", return_value={"committed": True, "exit_code": 0, "output": "1 file changed"}) as req:
            pv = hostops.git_commit_preview("edit homepage tile", user="Alex")
            self.assertEqual(pv["status"], "pending")
            self.assertFalse(pv["destructive"])
            self.assertIn("NO push", pv["preview"])
            req.assert_not_called()
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(req.call_args.kwargs.get("json_body"), {"message": "edit homepage tile"})

    def test_git_commit_needs_message(self):
        self.assertEqual(hostops.git_commit_preview("", user="Alex")["status"], "error")

    def test_caddy_reload_preview_then_confirms(self):
        with patch("hostops._request", return_value={"container": "caddy", "exit_code": 0, "output": "ok"}) as req:
            pv = hostops.caddy_reload_preview(user="Alex")
            self.assertEqual(pv["status"], "pending")
            self.assertFalse(pv["destructive"])
            self.assertIn("Reload Caddy", pv["preview"])
            req.assert_not_called()
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")

    def test_pihole_restartdns_preview_then_confirms(self):
        with patch("hostops._request", return_value={"container": "pihole", "exit_code": 0, "output": ""}) as req:
            pv = hostops.pihole_restartdns_preview(user="Alex")
            self.assertEqual(pv["status"], "pending")
            self.assertIn("Pi-hole", pv["preview"])
            req.assert_not_called()
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")


class HostOpsWiringTests(unittest.TestCase):
    TOOLS = Path("tools.py").read_text(encoding="utf-8")

    def test_read_tools_ungated(self):
        admin = self.TOOLS.split("_ADMIN_TOOLS = {")[1].split("}")[0]
        for n in ("docker_status", "docker_logs", "git_status"):
            self.assertIn(f'"name": "{n}"', self.TOOLS)
            self.assertIn(f'if name == "{n}":', self.TOOLS)
            self.assertNotIn(f'"{n}"', admin)

    def test_restart_gated_and_dispatched(self):
        admin = self.TOOLS.split("_ADMIN_TOOLS = {")[1].split("}")[0]
        for n in ("docker_restart", "docker_stop", "docker_start", "caddy_reload", "pihole_restartdns",
                  "watchtower_run", "git_commit"):
            self.assertIn(f'"name": "{n}"', self.TOOLS)
            self.assertIn(f'if name == "{n}":', self.TOOLS)
            self.assertIn(f'"{n}"', admin)
        for prefix in ("BRAIN_TOOLS = [", "HEAVY_TOOLS = ["):
            line = next(l for l in self.TOOLS.splitlines() if l.startswith(prefix))
            self.assertIn("_INFRA_PHASE4_TOOLS", line)

    def test_admin_gated_at_dispatch(self):
        self.assertIn("Permission denied", tools.run_tool("docker_restart", {"name": "caddy"}, "", user="Jordan"))


class HostAdminSidecarTests(unittest.TestCase):
    def test_load_hosts_parses(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"rigby": {"host": "10.0.0.82", "user": "root", "key": "/x/k",
                                 "description": "d"}}, fh)
            path = fh.name
        with patch.object(hostops_sidecar, "HOSTS_FILE", path):
            hosts = hostops_sidecar._load_hosts()
        self.assertIn("rigby", hosts)
        self.assertEqual(hosts["rigby"]["user"], "root")

    def test_load_hosts_missing_is_empty(self):
        with patch.object(hostops_sidecar, "HOSTS_FILE", "/no/such/file.json"):
            self.assertEqual(hostops_sidecar._load_hosts(), {})

    def test_reads_pass_the_guard(self):
        for cmd in ("whoami", "uname -a", "heimdall detect", "fastboot devices",
                    "heimdall print-pit", "sha256sum /tmp/x", "getprop | grep ro.product"):
            self.assertIsNone(hostops_sidecar._looks_destructive(cmd), cmd)

    def test_mutations_are_flagged(self):
        for cmd in ("rm -rf /tmp/x", "heimdall flash --RECOVERY r.img", "fastboot erase userdata",
                    "dd if=a of=b", "mkfs.ext4 /dev/x", "apt install foo", "cat a > b"):
            self.assertIsNotNone(hostops_sidecar._looks_destructive(cmd), cmd)


class HostAdminClientTests(unittest.TestCase):
    def setUp(self):
        pending_actions._clear_for_tests()

    def tearDown(self):
        pending_actions._clear_for_tests()

    def test_list_omits_credentials(self):
        data = {"hosts": [{"name": "rigby", "host": "10.0.0.82", "user": "root", "description": "d"}]}
        with patch("hostops._request", return_value=data):
            out = hostops.host_admin_list()
        self.assertIn("rigby", out)
        self.assertNotIn("key", out.lower())     # no credential path ever surfaces

    def test_run_sends_nondestructive(self):
        with patch("hostops._request", return_value={"host": "rigby", "exit_code": 0, "output": "root"}) as req:
            out = hostops.host_admin_run("rigby", "whoami")
        self.assertEqual(req.call_args.kwargs["json_body"]["destructive"], False)
        self.assertIn("root", out)

    def test_exec_preview_then_confirm_sends_destructive(self):
        with patch("hostops._request", return_value={"host": "rigby", "exit_code": 0, "output": "ok"}) as req:
            pv = hostops.host_admin_exec_preview("rigby", "apt-get install -y heimdall-flash", user="Alex")
            self.assertEqual(pv["status"], "pending")
            self.assertTrue(pv["destructive"])
            req.assert_not_called()
            out = pending_actions.confirm(pv["action_token"], user="Alex")
        self.assertEqual(out["status"], "applied")
        self.assertEqual(req.call_args.kwargs["json_body"]["destructive"], True)

    def test_exec_needs_host_and_command(self):
        self.assertEqual(hostops.host_admin_exec_preview("", "whoami", user="Alex")["status"], "error")

    def test_admin_gated_at_dispatch(self):
        self.assertIn("Permission denied",
                      tools.run_tool("host_admin_exec", {"host": "rigby", "command": "whoami"}, "", user="Jordan"))
        self.assertIn("Permission denied",
                      tools.run_tool("host_admin_run", {"host": "rigby", "command": "whoami"}, "", user="Jordan"))


if __name__ == "__main__":
    unittest.main()
