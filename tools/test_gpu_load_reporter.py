"""gpu_load_reporter samples a short window, not an instant."""
import importlib.util
import json
import os
import subprocess
import unittest
from unittest import mock

os.environ.setdefault("HAI_URL", "http://10.0.0.5:8099")   # a linked box (documentation-range head); read at import
spec = importlib.util.spec_from_file_location("glr", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                   "gpu_load_reporter.py"))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)                 # main() is guarded: importing pushes nothing


def smi(lines):
    it = iter(lines)

    def run(argv, **kw):
        out = next(it)
        if out is None:
            return subprocess.CompletedProcess(argv, 1, "", "GPU is lost")
        return subprocess.CompletedProcess(argv, 0, out + "\n", "")
    return run


class WindowNotInstant(unittest.TestCase):

    def sample(self, lines):
        with mock.patch.object(R.subprocess, "run", smi(lines)), mock.patch.object(R.time, "sleep", lambda s: None):
            return R.nvidia()

    def test_a_working_card_read_between_requests_is_busy(self):
        s = self.sample(["0, 60.0, 7089", "91, 135.8, 7089", "88, 130.0, 7089", "0, 62.0, 7089", "90, 134.0, 7100"])
        self.assertEqual((s["util_pct"], s["power_w"], s["vram_used_mb"]), (91.0, 135.8, 7100.0))
        self.assertIn("peak of 5", s["source"])

    def test_an_idle_card_stays_idle(self):
        s = self.sample(["0, 18.5, 300"] * 5)
        self.assertEqual(s["util_pct"], 0.0)

    def test_a_card_that_cannot_be_read_is_stale_never_zero(self):
        s = self.sample([None] * 5)
        self.assertTrue(s["stale"])
        self.assertNotIn("util_pct", s)

    def test_one_bad_reading_does_not_void_the_window(self):
        s = self.sample(["10, 50, 1", None, "80, 120, 1", "20, 60, 1", "5, 40, 1"])
        self.assertEqual(s["util_pct"], 80.0)
        self.assertIn("peak of 4", s["source"])


class MemoryReporting(unittest.TestCase):
    def test_the_programdata_reporter_can_load_the_readers_sibling_helpers(self):
        import tempfile
        import pathlib
        import sys
        with tempfile.TemporaryDirectory() as root:
            b = pathlib.Path(root) / "bin"; b.mkdir()
            (b / "gpu_spill.py").write_text("def snapshot():\n import reader_dependency\n return reader_dependency.VALUE\n")
            (b / "reader_dependency.py").write_text("VALUE = {'counter_ok': True}\n")
            try:
                with mock.patch.dict(R.os.environ, {"FOUNDRY_ROOT": root}):
                    self.assertEqual(R.memory_snapshot(), {"counter_ok": True})
            finally:
                sys.path.remove(str(b))
                sys.modules.pop("reader_dependency", None)

    def test_the_report_carries_the_counter_snapshot_and_engine_version(self):
        import contextlib
        memory = {"counter_ok": True, "card_mb": 8192, "adapter_mb": None, "dedicated_mb": None, "shared_mb": None}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(R, "nvidia", return_value={"util_pct": 0, "vram_used_mb": 594}))
            stack.enter_context(mock.patch.object(R, "memory_snapshot", return_value=memory))
            stack.enter_context(mock.patch.object(R, "ollama_version", return_value="0.40.5"))
            stack.enter_context(mock.patch.object(R, "ollama_loaded", return_value=[]))
            sent = stack.enter_context(mock.patch.object(R.urllib.request, "urlopen", return_value=response))
            R.main()
        body = json.loads(sent.call_args.args[0].data)
        self.assertEqual(body["gpu_memory"], memory)
        self.assertEqual(body["ollama_version"], "0.40.5")
        self.assertEqual(body["vram_used_mb"], 594)

    def test_the_report_carries_free_ram_read_from_windows(self):
        r = R.ram()
        self.assertIsNotNone(r, "GlobalMemoryStatusEx must read on a Windows box (positive control)")
        self.assertGreater(r["total_mb"], 1024)
        self.assertTrue(0 < r["free_mb"] <= r["total_mb"])
        import contextlib
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(R, "nvidia", return_value={"util_pct": 0, "vram_used_mb": 594}))
            stack.enter_context(mock.patch.object(R, "memory_snapshot", return_value=None))
            stack.enter_context(mock.patch.object(R, "ollama_version", return_value="0.40.5"))
            stack.enter_context(mock.patch.object(R, "ollama_loaded", return_value=[]))
            stack.enter_context(mock.patch.object(R, "ram", return_value={"free_mb": 20000, "total_mb": 32700}))
            sent = stack.enter_context(mock.patch.object(R.urllib.request, "urlopen", return_value=response))
            R.main()
        self.assertEqual(json.loads(sent.call_args.args[0].data)["ram"], {"free_mb": 20000, "total_mb": 32700})

    def test_unreadable_ram_is_unknown_never_a_number(self):
        with mock.patch("ctypes.windll", create=True) as w:
            w.kernel32.GlobalMemoryStatusEx.return_value = 0
            self.assertIsNone(R.ram())

    def test_a_missing_reader_is_unknown_not_zero_allocation(self):
        with mock.patch.object(R.importlib.util, "spec_from_file_location", side_effect=OSError("unreadable")):
            self.assertIsNone(R.memory_snapshot())

    def test_version_probe_failure_is_unknown_not_a_guessed_version(self):
        with mock.patch.object(R.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertIsNone(R.ollama_version())


class TelemetryWindow(unittest.TestCase):
    """Mothership (AMD) reads the foundry telemetry CSV: the peak of its last rows, not the last row alone."""

    def test_a_working_card_whose_last_row_is_a_gap_is_busy(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "t.csv")
        rows = ["ts,gpu_load,power_w,vram_used_mb"] + ["12:16:%02d,%d,%d,20000" % (i * 11, u, w)
                                                       for i, (u, w) in enumerate([(5, 90), (97, 380), (95, 370),
                                                                                   (96, 375), (1, 95)])]
        open(p, "w", encoding="utf-8").write("\n".join(rows) + "\n")
        with mock.patch.object(R, "TELEMETRY_CSV", p):
            s = R.telemetry_csv()
        self.assertEqual((s["util_pct"], s["power_w"]), (97.0, 380.0))
        self.assertIn("peak of 5 rows", s["source"])


if __name__ == "__main__":
    unittest.main()
