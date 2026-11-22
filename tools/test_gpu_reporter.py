"""Tests AMD GPU verdict logic using byte-exact fixture data derived from real device records, and verifies the NVIDIA path remains unchanged."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("HAI_URL", "http://10.0.0.5:8099")   # a linked box (documentation-range head); read at import
import gpu_reporter as g  # noqa: E402

HEALTHY = json.loads('[{"name":"AMD Radeon RX 7900 XTX","status":"OK","problem":0,"present":true,'
                     '"driver":"32.0.32015.12012","id":"PCI\\\\VEN_1002\\u0026DEV_744C\\u0026SUBSYS_53041849'
                     '\\u0026REV_C8\\\\6\\u002628CF2CBF\\u00260\\u002600000019"}]')
FAILED = [{"name": "AMD Radeon RX 7900 XTX", "status": "Error", "problem": 43, "present": True,
           "driver": "32.0.21043.1000", "id": "PCI\\VEN_1002&DEV_744C"}]
# Phantom record: device off-bus (CM_PROB_PHANTOM, problem=45).
GONE = [{"name": "AMD Radeon RX 7900 XTX", "status": "Unknown", "problem": 45, "present": False, "driver": None,
         "id": "PCI\\VEN_1002&DEV_744C"}]


class AmdVerdict(unittest.TestCase):
    def test_healthy_card_reads_ok_with_its_driver(self):
        ok, detail = g.amd_verdict(HEALTHY)
        self.assertIs(ok, True)
        self.assertIn("32.0.32015.12012", detail)

    def test_a_failed_device_is_lost(self):
        ok, detail = g.amd_verdict(FAILED)
        self.assertIs(ok, False)
        self.assertIn("problem code 43", detail)

    def test_a_card_off_the_bus_is_lost(self):
        ok, detail = g.amd_verdict(GONE)
        self.assertIs(ok, False)
        self.assertIn("phantom", detail)

    def test_an_old_phantom_record_beside_a_healthy_card_is_healthy(self):
        ok, _ = g.amd_verdict(GONE + HEALTHY)
        self.assertIs(ok, True)

    def test_no_amd_adapter_is_no_verdict(self):
        self.assertIsNone(g.amd_verdict([])[0])

    def test_an_unclear_present_state_is_no_verdict(self):
        self.assertIsNone(g.amd_verdict([{"name": "x", "status": "Degraded", "problem": 0, "present": True}])[0])


class Dispatch(unittest.TestCase):
    def _run(self, stdout="", rc=0):
        return mock.Mock(stdout=stdout, stderr="", returncode=rc)

    def test_no_nvidia_smi_asks_windows_and_parses_a_single_object(self):
        def fake(argv, **kw):
            if argv[0] == "nvidia-smi":
                raise FileNotFoundError
            return self._run(json.dumps(HEALTHY[0]))          # PowerShell renders one device as an object
        with mock.patch.object(g.subprocess, "run", side_effect=fake):
            ok, detail, source = g.probe()
        self.assertEqual((ok, source), (True, "windows-pnp"))

    def test_a_failed_device_query_is_no_verdict_never_lost(self):
        def fake(argv, **kw):
            if argv[0] == "nvidia-smi":
                raise FileNotFoundError
            return self._run("", rc=1)
        with mock.patch.object(g.subprocess, "run", side_effect=fake):
            self.assertIsNone(g.probe()[0])

    def test_nvidia_lost_phrase_still_wins(self):
        with mock.patch.object(g.subprocess, "run",
                               return_value=self._run("Unable to determine the device handle for GPU0", rc=15)):
            ok, _, source = g.probe()
        self.assertEqual((ok, source), (False, "nvidia-smi"))

    def test_no_verdict_pushes_nothing(self):
        with mock.patch.object(g, "probe", return_value=(None, "x", "windows-pnp")), \
                mock.patch.object(g.urllib.request, "urlopen") as up:
            g.main(["gpu_reporter.py", "mothership"])
        up.assert_not_called()

    def test_a_verdict_is_pushed_with_its_source(self):
        with mock.patch.object(g, "probe", return_value=(False, "gone", "windows-pnp")), \
                mock.patch.object(g.urllib.request, "urlopen") as up:
            up.return_value.__enter__.return_value.read.return_value = b"{}"
            g.main(["gpu_reporter.py", "mothership"])
        body = json.loads(up.call_args[0][0].data)
        self.assertEqual(body, {"host": "mothership", "healthy": False, "detail": "gone", "source": "windows-pnp"})


if __name__ == "__main__":
    unittest.main()
