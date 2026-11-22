"""TABLET_STAGE_URL: Tolkien hands every payload the stage server's address as FELLOW_STAGE_URL, a tablet's own
FELLOW_STAGE_URL wins, and with no setting (or an unusable one) the launch line is exactly what it was before."""
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_fulfil


def _launch_cmd(stage_url):
    """The command the order would run on a tablet, with ssh and scp stubbed."""
    seen = []

    def ssh(host, cmd, timeout=None):
        if "items.txt out.json" in cmd:
            seen.append(cmd)
            return 1, "stop here"
        return 0, ""

    spec = {"kind": "map", "name": "stage-test", "payload": "/x/grade_jobs.py", "batch": 2,
            "items": ["a", "b"], "control": {"item": "ctl", "expect": "ok"}}
    with mock.patch.object(tablet_fulfil, "STAGE_URL", stage_url), \
            mock.patch.object(tablet_fulfil, "_scp", lambda *a, **k: (0, "")), \
            mock.patch.object(tablet_fulfil, "_ssh", ssh), \
            mock.patch.object(tablet_fulfil, "log", lambda *a, **k: None):
        try:
            tablet_fulfil.run_order(spec, ["h1"])
        except Exception:
            pass
    assert seen, "the payload was never launched: the test would measure nothing"
    return seen[0]


class TheLaunchLine(unittest.TestCase):
    def test_the_setting_reaches_the_payload(self):
        cmd = _launch_cmd("http://stage.example:8078/")
        self.assertIn('FELLOW_STAGE_URL="${FELLOW_STAGE_URL:-http://stage.example:8078}" python grade_jobs.py '
                      'items.txt out.json', cmd)

    def test_no_setting_leaves_the_line_as_it_was(self):
        cmd = _launch_cmd("")
        self.assertNotIn("FELLOW_STAGE_URL", cmd)
        self.assertRegex(cmd, r"^cd \S+ && python grade_jobs\.py items\.txt out\.json$")

    def test_an_unusable_value_is_not_passed(self):
        for bad in ('http://x:1"; rm -rf ~ #', "ftp://stage.example", "http://stage.example:8078/models", "$(id)"):
            self.assertNotIn("FELLOW_STAGE_URL", _launch_cmd(bad), bad)


@unittest.skipUnless(shutil.which("sh"), "no POSIX shell here")
class TheShellKeepsATabletsOwnValue(unittest.TestCase):
    def _run(self, env):
        with mock.patch.object(tablet_fulfil, "STAGE_URL", "http://stage.example:8078"):
            prefix = tablet_fulfil._stage_env()
        self.assertTrue(prefix, "positive control: a usable setting produces a prefix")
        out = subprocess.run(["sh", "-c", prefix + "printenv FELLOW_STAGE_URL"], env=env, capture_output=True,
                             text=True, check=True).stdout.strip()
        return out

    def test_the_setting_fills_in_when_the_tablet_has_none(self):
        env = {k: v for k, v in os.environ.items() if k != "FELLOW_STAGE_URL"}
        self.assertEqual(self._run(env), "http://stage.example:8078")

    def test_a_tablets_own_value_wins(self):
        env = dict(os.environ, FELLOW_STAGE_URL="http://10.55.0.1:8078")
        self.assertEqual(self._run(env), "http://10.55.0.1:8078")


if __name__ == "__main__":
    unittest.main()
