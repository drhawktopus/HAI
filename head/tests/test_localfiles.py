"""Behavioral tests for the read_config tool (allow-listed, read-only file reads)."""
import os
import tempfile
import unittest
from pathlib import Path

import config
import localfiles


class ReadConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_dirs = config.CONFIG_READ_DIRS
        self._orig_max = config.CONFIG_READ_MAX_BYTES
        self._allowed = os.path.join(self._tmp, "allowed")
        os.makedirs(self._allowed)
        self._cfg = os.path.join(self._allowed, "services.yaml")
        Path(self._cfg).write_text("gpu:\n  field: gpu.0.proc\n", encoding="utf-8")
        # A secret living OUTSIDE the allow-list must never be readable.
        self._secret = os.path.join(self._tmp, "secret.env")
        Path(self._secret).write_text("HA_TOKEN=supersecret\n", encoding="utf-8")
        config.CONFIG_READ_DIRS = [self._allowed]

    def tearDown(self):
        config.CONFIG_READ_DIRS = self._orig_dirs
        config.CONFIG_READ_MAX_BYTES = self._orig_max

    def test_reads_allowed_file(self):
        out = localfiles.read_config(self._cfg)
        self.assertIn("gpu.0.proc", out)

    def test_no_path_lists_files(self):
        out = localfiles.read_config()
        self.assertIn("services.yaml", out)
        self.assertNotIn("secret.env", out)

    def test_rejects_outside_allowlist(self):
        out = localfiles.read_config(self._secret)
        self.assertIn("Refused", out)
        self.assertNotIn("supersecret", out)

    def test_rejects_traversal(self):
        out = localfiles.read_config(os.path.join(self._allowed, "..", "secret.env"))
        self.assertIn("Refused", out)
        self.assertNotIn("supersecret", out)

    def test_missing_file_message(self):
        out = localfiles.read_config(os.path.join(self._allowed, "nope.yaml"))
        self.assertIn("Not a file", out)

    def test_truncates_large_file(self):
        config.CONFIG_READ_MAX_BYTES = 10
        out = localfiles.read_config(self._cfg)
        self.assertIn("[truncated]", out)


class WriteConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.CONFIG_WRITE_DIRS
        self._allowed = os.path.join(self._tmp, "allowed")
        os.makedirs(self._allowed)
        self._cfg = os.path.join(self._allowed, "services.yaml")
        Path(self._cfg).write_text("widget:\n  metric: gpu\n", encoding="utf-8")
        self._secret = os.path.join(self._tmp, "secret.env")
        Path(self._secret).write_text("HA_TOKEN=supersecret\n", encoding="utf-8")
        config.CONFIG_WRITE_DIRS = [self._allowed]

    def tearDown(self):
        config.CONFIG_WRITE_DIRS = self._orig

    def _baks(self):
        return [f for f in os.listdir(self._allowed) if ".bak-" in f]

    def test_preview_does_not_write(self):
        out = localfiles.write_config(self._cfg, "metric: gpu", "metric: gpu:nvidia0", confirm=False)
        self.assertIn("PREVIEW", out)
        self.assertIn("gpu:nvidia0", out)  # diff shows the change
        self.assertEqual(Path(self._cfg).read_text(encoding="utf-8"), "widget:\n  metric: gpu\n")
        self.assertEqual(self._baks(), [])  # no backup on preview

    def test_confirm_writes_and_backs_up(self):
        out = localfiles.write_config(self._cfg, "metric: gpu", "metric: gpu:nvidia0", confirm=True)
        self.assertIn("APPLIED", out)
        self.assertIn("metric: gpu:nvidia0", Path(self._cfg).read_text(encoding="utf-8"))
        self.assertEqual(len(self._baks()), 1)  # exactly one timestamped backup

    def test_old_not_found(self):
        out = localfiles.write_config(self._cfg, "nonexistent text", "x", confirm=True)
        self.assertIn("not found", out)
        self.assertEqual(self._baks(), [])

    def test_ambiguous_old(self):
        Path(self._cfg).write_text("a: 1\na: 1\n", encoding="utf-8")
        out = localfiles.write_config(self._cfg, "a: 1", "a: 2", confirm=True)
        self.assertIn("ambiguous", out)
        self.assertEqual(self._baks(), [])

    def test_outside_allowlist_refused(self):
        out = localfiles.write_config(self._secret, "HA_TOKEN=supersecret", "HA_TOKEN=hacked", confirm=True)
        self.assertIn("Refused", out)
        self.assertIn("supersecret", Path(self._secret).read_text(encoding="utf-8"))

    def test_identical_old_new_noop(self):
        out = localfiles.write_config(self._cfg, "metric: gpu", "metric: gpu", confirm=True)
        self.assertIn("No change", out)


class WriteConfigWiringTests(unittest.TestCase):
    """write_config must be wired into tools + admin-gated (source checks)."""

    @classmethod
    def setUpClass(cls):
        cls.tools_src = Path("tools.py").read_text(encoding="utf-8")
        cls.app_src = Path("app.py").read_text(encoding="utf-8")

    def test_schema_and_dispatch(self):
        self.assertIn('"name": "write_config"', self.tools_src)
        self.assertIn('if name == "write_config":', self.tools_src)

    def test_in_brain_and_heavy(self):
        for prefix in ("BRAIN_TOOLS = [", "HEAVY_TOOLS = ["):
            line = next(l for l in self.tools_src.splitlines() if l.startswith(prefix))
            self.assertIn("_WRITE_CONFIG", line)

    def test_admin_gated(self):
        # write_config must be in the admin-gated set (so non-admins can't call it).
        self.assertIn('"write_config"', self.tools_src.split("_ADMIN_TOOLS")[1][:120])

    def test_system_prompt_preview_first(self):
        self.assertIn("write_config", self.app_src)
        self.assertIn("preview", self.app_src.lower())


if __name__ == "__main__":
    unittest.main()
