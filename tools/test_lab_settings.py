"""Reader precedence proof for lab_settings.py, runnable with plain python.

Covers the acceptance criteria: environment beats file; file beats default; a missing file
gives the default; an empty default means "not set". Uses a temp dir - the real settings file
is never touched, and this test never needs a factory or a share.
"""
import json
import os
import tempfile
import unittest

import lab_settings as S


class TestLabSettings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lab-settings-test-")
        self.path = os.path.join(self.dir, "lab.env")
        self._saved_env = os.environ.get("LAB_SETTINGS_FILE")
        os.environ["LAB_SETTINGS_FILE"] = self.path
        for name in ("HAI_URL", "OVERNIGHT_DM_URL", "FLEET_HOSTS", "LAN_PREFIX", "PROBE_HOST", "FELLOWSHIP_ROSTER",
                     "FELLOWSHIP_USB_SEGMENT"):
            os.environ.pop(name, None)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("LAB_SETTINGS_FILE", None)
        else:
            os.environ["LAB_SETTINGS_FILE"] = self._saved_env
        for name in ("HAI_URL", "OVERNIGHT_DM_URL", "FLEET_HOSTS", "LAN_PREFIX", "PROBE_HOST", "FELLOWSHIP_ROSTER",
                     "FELLOWSHIP_USB_SEGMENT"):
            os.environ.pop(name, None)

    def _write(self, lines):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def test_environment_beats_file(self):
        self._write(["PROBE_HOST=10.0.0.2"])
        os.environ["PROBE_HOST"] = "10.0.0.3"
        self.assertEqual(S.setting("PROBE_HOST", ""), "10.0.0.3")

    def test_file_beats_default(self):
        self._write(["PROBE_HOST=10.0.0.2"])
        self.assertEqual(S.setting("PROBE_HOST", "10.0.0.9"), "10.0.0.2")

    def test_missing_file_gives_default(self):
        os.environ["LAB_SETTINGS_FILE"] = os.path.join(self.dir, "no-such-file.env")
        self.assertEqual(S.setting("PROBE_HOST", "10.0.0.9"), "10.0.0.9")
        self.assertEqual(S.head_url(), "")
        self.assertEqual(S.fleet_hosts(), {})

    def test_empty_default_means_not_set(self):
        self.assertEqual(S.setting("PROBE_HOST", ""), "")
        self.assertEqual(S.head_url(), "")
        self.assertEqual(S.fleet_hosts(), {})
        self.assertEqual(S.tablet_hosts(), {})

    def test_tablets_come_from_the_roster_minus_the_usb_segment(self):
        roster = os.path.join(self.dir, "roster.json")
        with open(roster, "w", encoding="utf-8") as fh:
            json.dump({"tablets": {"t1": {"address": "10.0.0.11", "serial": "a"},
                                   "t2": {"address": "10.99.0.5", "serial": "b"}}}, fh)
        self._write(["FELLOWSHIP_ROSTER=" + roster, "FELLOWSHIP_USB_SEGMENT=10.99.0"])
        self.assertEqual(S.tablet_hosts(), {"t1": "10.0.0.11"})
        self.assertEqual(sorted(S.fellowship_roster()), ["t1", "t2"])

    def test_a_roster_that_is_set_but_unreadable_raises(self):
        self._write(["FELLOWSHIP_ROSTER=" + os.path.join(self.dir, "no-such-roster.json")])
        with self.assertRaises(OSError):
            S.tablet_hosts()

    def test_usb_segment_default_and_trailing_dot(self):
        self.assertEqual(S.usb_segment(), "10.55.0")
        self._write(["FELLOWSHIP_USB_SEGMENT=10.99.0."])
        self.assertEqual(S.usb_segment(), "10.99.0")

    def test_empty_value_in_file_counts_as_unset(self):
        self._write(["PROBE_HOST="])
        self.assertEqual(S.setting("PROBE_HOST", "10.0.0.9"), "10.0.0.9")

    def test_table_from_environment_and_file(self):
        self._write(['FLEET_HOSTS={"head": "10.0.0.1", "stage": "10.0.0.2"}'])
        self.assertEqual(S.fleet_hosts(), {"head": "10.0.0.1", "stage": "10.0.0.2"})
        os.environ["FLEET_HOSTS"] = '{"head": "10.0.0.5"}'
        self.assertEqual(S.fleet_hosts(), {"head": "10.0.0.5"})

    def test_unparseable_table_is_unset(self):
        self._write(["FLEET_HOSTS=not json at all"])
        self.assertEqual(S.fleet_hosts(), {})

    def test_head_url_trailing_slash_stripped(self):
        os.environ["HAI_URL"] = "http://10.0.0.1:8099/"
        self.assertEqual(S.head_url(), "http://10.0.0.1:8099")

    def test_installed_copy_reads_the_lab_env_beside_it(self):
        # an installed copy (C:\ProgramData\HAI) has no LAB_SETTINGS_FILE: the lab.env beside the module is its file
        os.environ.pop("LAB_SETTINGS_FILE", None)
        beside = os.path.join(self.dir, "lab.env")
        with open(beside, "w", encoding="utf-8") as f:
            f.write("HAI_URL=http://10.0.0.5:8099\n")
        real = S.__file__
        try:
            S.__file__ = os.path.join(self.dir, "lab_settings.py")
            self.assertEqual(S.settings_file(), beside)
            self.assertEqual(S.head_url(), "http://10.0.0.5:8099")
            os.remove(beside)
            self.assertEqual(S.settings_file(), S.DEFAULT_FILE, "no file beside it: the lab's shared file")
        finally:
            S.__file__ = real


    def test_shell_exports_keep_posix_values_out_of_msys_path_conversion(self):
        # Git Bash rewrites an exported POSIX path for native programs (python.exe) unless the variable is named in
        # MSYS2_ENV_CONV_EXCL: /opt/x arrived as C:/Program Files/Git/opt/x and the space broke a remote cd (11-18).
        self._write(["HEAD_NAME=sundae", "HEAD_STACK_DIR=/opt/arrstack", 'CODE_MIRRORS={"orchestrator": "E:\\\\mirror"}'])
        for name in ("HEAD_NAME", "HEAD_STACK_DIR", "CODE_MIRRORS"):
            os.environ.pop(name, None)
        out = S.shell_exports(["HEAD_NAME", "HEAD_STACK_DIR", "CODE_MIRRORS.orchestrator:posix"])
        lines = out.split("\n")
        self.assertEqual(lines[0], "export HEAD_NAME='sundae'")
        self.assertEqual(lines[1], "export HEAD_STACK_DIR='/opt/arrstack'")
        self.assertTrue(lines[2].startswith("export CODE_MIRRORS_ORCHESTRATOR='/"), lines[2])
        self.assertEqual(len(lines), 4, "one exclusion line after the exports")
        self.assertIn("HEAD_STACK_DIR;CODE_MIRRORS_ORCHESTRATOR", lines[3])
        self.assertNotIn("HEAD_NAME;", lines[3], "a plain value is not a path: not excluded")
        self.assertNotIn("\r", out)

    def test_shell_exports_without_posix_values_adds_no_exclusion(self):
        self._write(["HEAD_NAME=sundae"])
        os.environ.pop("HEAD_NAME", None)
        self.assertEqual(S.shell_exports(["HEAD_NAME"]), "export HEAD_NAME='sundae'")


if __name__ == "__main__":
    unittest.main()
