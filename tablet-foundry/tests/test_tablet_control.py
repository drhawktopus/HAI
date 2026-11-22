"""Tablet control plane — auth gate + routing.

Tests BEHAVIOUR (status codes, which op runs, node fan-out), never source text. The adb layer is
patched so no real subprocess runs (see test_no_real_subprocess.py): the node ops
(status/reboot/usb_restore) are replaced with fakes, so control_app is exercised without touching a
device. Auth is the load-bearing property here -- reboot-a-tablet is destructive, so an unset or
wrong token must never reach an op.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tablet_control as TC  # noqa: E402
import tablet_foundry as _tf  # noqa: E402

_WIRE = mock.patch.object(_tf, "WIRE_PREFIX", "192.0.2.")    # the wire subnet is a setting; these tests use a documentation one


def setUpModule():
    _WIRE.start()


def tearDownModule():
    _WIRE.stop()


TOKEN = "test-token-do-not-ship"


def _fake_status(name):
    return {"node": name, "adb": "root", "usb_config": "rndis,adb", "rndis0": "192.0.2.9/24"}


def _fake_restore(name):
    return {"node": name, "ok": True, "usb_config": "rndis,adb"}


def _fake_reboot(name, restore=True):
    return {"node": name, "ok": True, "boot_s": 40}


class Auth(unittest.TestCase):
    def setUp(self):
        os.environ["TOLKIEN_CTL_TOKEN"] = TOKEN

    def test_no_token_configured_is_503(self):
        os.environ.pop("TOLKIEN_CTL_TOKEN", None)
        try:
            code, obj = TC.control_app("GET", "/fellowship/status", b"", TOKEN)
            self.assertEqual(code, 503)
            self.assertFalse(obj["ok"])
        finally:
            os.environ["TOLKIEN_CTL_TOKEN"] = TOKEN

    def test_missing_token_is_401(self):
        code, obj = TC.control_app("GET", "/fellowship/status", b"", "")
        self.assertEqual(code, 401)
        self.assertFalse(obj["ok"])

    def test_wrong_token_is_401(self):
        code, _ = TC.control_app("POST", "/fellowship/reboot",
                                 json.dumps({"target": "all"}).encode(), "not-the-token")
        self.assertEqual(code, 401)

    def test_serve_refuses_without_token(self):
        """Reboot is destructive: no token must mean NO LISTENER, never an open one."""
        started, why = TC.control_serve(port=0, token="")
        self.assertFalse(started)
        self.assertIn("token", why.lower())


class Routing(unittest.TestCase):
    def setUp(self):
        os.environ["TOLKIEN_CTL_TOKEN"] = TOKEN

    def test_unknown_target_is_400(self):
        code, obj = TC.control_app("POST", "/fellowship/reboot",
                                   json.dumps({"target": "nosuchnode"}).encode(), TOKEN)
        self.assertEqual(code, 400)
        self.assertFalse(obj["ok"])

    def test_unknown_endpoint_is_404(self):
        code, _ = TC.control_app("GET", "/fellowship/nope", b"", TOKEN)
        self.assertEqual(code, 404)

    @mock.patch.object(TC, "status", _fake_status)
    def test_status_defaults_to_all(self):
        code, obj = TC.control_app("GET", "/fellowship/status", b"", TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(len(obj["nodes"]), len(TC.tf.TABLET_NET))

    @mock.patch.object(TC, "status", _fake_status)
    def test_status_target_group(self):
        code, obj = TC.control_app("GET", "/fellowship/status?target=class-a", b"", TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual({n["node"] for n in obj["nodes"]}, {"gandalf", "aragorn"})

    @mock.patch.object(TC, "usb_restore", _fake_restore)
    def test_usb_restore_single_node(self):
        code, obj = TC.control_app("POST", "/fellowship/usb-restore",
                                   json.dumps({"node": "gandalf"}).encode(), TOKEN)
        self.assertEqual(code, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual([r["node"] for r in obj["results"]], ["gandalf"])

    @mock.patch.object(TC, "reboot", _fake_reboot)
    def test_reboot_group_fans_out(self):
        code, obj = TC.control_app("POST", "/fellowship/reboot",
                                   json.dumps({"target": "class-b", "restore": False}).encode(), TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(sorted(r["node"] for r in obj["results"]),
                         ["boromir", "faramir", "frodo", "gimli", "legolas", "sam"])

    @mock.patch.object(TC, "off", lambda name: {"node": name, "ok": True, "off": True})
    def test_off_routes_and_fans_out(self):
        code, obj = TC.control_app("POST", "/fellowship/off",
                                   json.dumps({"target": "class-a"}).encode(), TOKEN)
        self.assertEqual(code, 200)
        self.assertTrue(all(r["off"] for r in obj["results"]))
        self.assertEqual(sorted(r["node"] for r in obj["results"]), ["aragorn", "gandalf"])

    def test_off_requires_token(self):
        code, _ = TC.control_app("POST", "/fellowship/off",
                                 json.dumps({"node": "gandalf"}).encode(), "")
        self.assertEqual(code, 401)

    def test_octet_map_matches_roster(self):
        """Every rostered node has a segment octet, and no stray octets exist -- a missing octet
        would make usb_restore KeyError mid-request."""
        self.assertEqual(set(TC.SEG_OCTET), set(TC.tf.TABLET_NET))

class ClassRules(unittest.TestCase):
    """Classes B (sam included) and C are dealt nothing above 1.5B; class A holds the 2B."""

    def setUp(self):
        import tablet_runner
        self.tr = tablet_runner
        self.fleet = ["gandalf", "frodo", "sam", "merry", "pippin"]

    def kept(self, spec):
        return self.tr._apply_class_rules("o", spec, self.fleet)[0]

    def test_sizes_read_from_real_model_names(self):
        for name, b in (("Qwen3.5-2B-Q4_K_M", 2.0), ("Qwen2.5-Coder-1.5B-Instruct-Q4_K_M", 1.5),
                        ("Qwen3.5-0.8B-Q4_K_M", 0.8), ("SmolVLM2-500M-Video-Instruct-Q8_0", 0.5),
                        ("Qwen3.6-35B-A3B-UD-Q3_K_XL", 35.0), ("mystery", None)):
            self.assertEqual(self.tr.model_size_b(name), b, name)

    def test_a_2b_order_reaches_class_a_only(self):
        self.assertEqual(self.kept({"model": "Qwen3.5-2B-Q4_K_M"}), ["gandalf"])

    def test_sam_is_class_b_hardware(self):
        self.assertEqual(self.tr.tablet_class("sam"), "B")

    def test_warm_chat_at_or_under_the_ceiling_reaches_every_class(self):
        self.assertEqual(self.kept({"model": "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M", "arms": ["warm"]}), self.fleet)

    def test_a_map_payload_declares_its_model(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write('TASKS = {}\nMODEL_NAME_FIXED = "Qwen3.5-2B-Q4_K_M"\n')
        try:
            self.assertEqual(self.kept({"payload": f.name}), ["gandalf"])
        finally:
            os.remove(f.name)

    def test_an_unstated_model_is_not_blocked(self):
        self.assertEqual(self.kept({"payload": "/nope.py"}), self.fleet)


class RootShell(unittest.TestCase):
    """A setsid'd Termux process is killable only as root, and adb is not root after a reboot."""

    def test_root_is_ensured_before_the_command_runs(self):
        order = []
        with mock.patch.object(TC, "_ensure_root", side_effect=lambda ip: order.append("root") or True), \
             mock.patch.object(TC, "_adb_sh", side_effect=lambda ip, cmd, timeout=45: order.append(cmd) or "ok"):
            self.assertEqual(TC._adb_root_sh("192.0.2.9", "kill 1"), "ok")
        self.assertEqual(order, ["root", "kill 1"])

    def test_no_root_never_runs_the_command(self):
        with mock.patch.object(TC, "_ensure_root", return_value=False), \
             mock.patch.object(TC, "_adb_sh") as sh:
            self.assertEqual(TC._adb_root_sh("192.0.2.9", "kill 1"), "NOROOT")
        sh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
