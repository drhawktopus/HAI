import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import infra
import tools


class DiskUsageTests(unittest.TestCase):
    def setUp(self):
        self.orig = dict(config.DISK_ROOTS)
        config.DISK_ROOTS = {"media": "/media", "downloads": "/downloads", "docs": "/docs"}

    def tearDown(self):
        config.DISK_ROOTS = self.orig

    def test_fmt_bytes(self):
        self.assertEqual(infra._fmt_bytes(0), "0.0B")
        self.assertEqual(infra._fmt_bytes(1536), "1.5KB")
        self.assertIn("TB", infra._fmt_bytes(45 * 1024**4))

    def test_no_path_lists_all_roots(self):
        with patch("infra.shutil.disk_usage", return_value=(45 * 1024**4, 33 * 1024**4, 12 * 1024**4)):
            out = infra.disk_usage()
        self.assertIn("media (/media):", out)
        self.assertIn("downloads (/downloads):", out)
        self.assertIn("docs (/docs):", out)
        self.assertIn("free of", out)
        self.assertIn("73% used", out)   # 33/45

    def test_path_scoping_refuses_outside(self):
        out = infra.disk_usage("/etc/passwd")
        self.assertIn("must be inside", out)

    def test_path_free_space_no_sizes(self):
        with patch("infra.os.path.realpath", side_effect=lambda p: p), \
             patch("infra.os.path.exists", return_value=True), \
             patch("infra.shutil.disk_usage", return_value=(1000, 600, 400)):
            out = infra.disk_usage("/media/Movies")
        self.assertIn("filesystem", out)
        self.assertNotIn("Subfolder sizes", out)

    def test_sizes_runs_du(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout="1.2T\t/media/Movies/ShowA\n800G\t/media/Movies\n", stderr="")
        with patch("infra.os.path.realpath", side_effect=lambda p: p), \
             patch("infra.os.path.exists", return_value=True), \
             patch("infra.os.path.isdir", return_value=True), \
             patch("infra.shutil.disk_usage", return_value=(45 * 1024**4, 0, 45 * 1024**4)), \
             patch("infra.subprocess.run", return_value=fake):
            out = infra.disk_usage("/media/Movies", sizes=True)
        self.assertIn("Subfolder sizes", out)
        self.assertIn("ShowA", out)

    def test_sizes_timeout_is_friendly(self):
        with patch("infra.os.path.realpath", side_effect=lambda p: p), \
             patch("infra.os.path.exists", return_value=True), \
             patch("infra.os.path.isdir", return_value=True), \
             patch("infra.shutil.disk_usage", return_value=(1000, 0, 1000)), \
             patch("infra.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="du", timeout=25)):
            out = infra.disk_usage("/media", sizes=True)
        self.assertIn("timed out", out)

    def test_wired_read_only(self):
        T = Path("tools.py").read_text(encoding="utf-8")
        self.assertIn('"name": "disk_usage"', T)
        self.assertIn('if name == "disk_usage":', T)
        admin_block = T.split("_ADMIN_TOOLS = {")[1].split("}")[0]
        self.assertNotIn('"disk_usage"', admin_block)   # read-only, not gated
        for prefix in ("BRAIN_TOOLS = [", "HEAVY_TOOLS = ["):
            line = next(l for l in T.splitlines() if l.startswith(prefix))
            self.assertIn("_DISK_USAGE", line)

    def test_dispatch_ungated_for_casual_user(self):
        with patch("tools.infra.disk_usage", return_value="OK"):
            self.assertEqual(tools.run_tool("disk_usage", {}, "", user="Jordan"), "OK")


if __name__ == "__main__":
    unittest.main()
