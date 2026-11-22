"""The USB reach probe against a real listener: the positive control (a listening address is reported), the negative
(nothing listening reports an empty list), and the error contract (an unknown segment never reads as "none answer")."""
import os
import socket
import subprocess
import sys
import unittest

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usb_reach_probe.py")


def run(env_extra, timeout="0.5"):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FELLOWSHIP_USB_")}
    env.update(FELLOWSHIP_USB_BRIDGE="no-such-bridge-x", **env_extra)
    return subprocess.run([sys.executable, PROBE, timeout], capture_output=True, text=True, env=env, timeout=60)


class TestUsbReachProbe(unittest.TestCase):
    def test_a_listening_address_is_reported(self):
        srv = socket.socket()
        srv.bind(("127.0.0.5", 0))
        srv.listen(8)
        try:
            p = run({"FELLOWSHIP_USB_SEGMENT": "127.0.0", "FELLOWSHIP_USB_PORT": str(srv.getsockname()[1])})
        finally:
            srv.close()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("5", p.stdout.split()[1:])
        self.assertTrue(p.stdout.startswith("USB_REACH"))

    def test_nothing_listening_reports_an_empty_list(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()                                 # a port nothing listens on
        p = run({"FELLOWSHIP_USB_SEGMENT": "127.0.0", "FELLOWSHIP_USB_PORT": str(port)})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), "USB_REACH")

    def test_an_unknown_segment_is_an_error_the_caller_cannot_read_as_none(self):
        p = run({})
        self.assertEqual(p.returncode, 2)
        self.assertFalse(p.stdout.startswith("USB_REACH"), p.stdout)


if __name__ == "__main__":
    unittest.main()
