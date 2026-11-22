"""Proof for the ONE settings reader in this workspace (foundry_paths.setting).

Precedence: environment, then <FOUNDRY_ROOT>\\etc\\lab.env, then the caller's default.
Run with plain Python, no factory and no network: a temp directory stands in for the foundry.
Rule, not story: every real address the factory uses is a setting; nothing else reads lab.env.
"""
import os
import tempfile
import unittest

ROOT = os.environ["FOUNDRY_ROOT"] = tempfile.mkdtemp(prefix="foundry-test-")
ETC = os.path.join(ROOT, "etc")
os.makedirs(ETC, exist_ok=True)

import foundry_paths as P

LAB = os.path.join(ETC, "lab.env")

HEAD = "http://10.0.0.1:8099"


def write_lab(lines):
    with open(LAB, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def clear_env(*names):
    for n in names:
        os.environ.pop(n, None)


class ReaderTest(unittest.TestCase):
    def setUp(self):
        clear_env("HAI_URL", "FLEET_HOSTS", "NOT_SET_ANYWHERE", "EMPTY_IN_FILE")
        # the reader binds its file at import; in a shared process another module imported it first, against the
        # box's real root, so point it at this suite's temp file for the test and put it back after
        self._lab_env = P.LAB_ENV
        P.LAB_ENV = LAB

    def tearDown(self):
        P.LAB_ENV = self._lab_env

    def test_environment_beats_the_file(self):
        write_lab(["HAI_URL=%s" % HEAD])
        os.environ["HAI_URL"] = "http://10.0.0.2:8099"
        self.assertEqual(P.head_url(), "http://10.0.0.2:8099")

    def test_file_beats_the_default(self):
        write_lab(["HAI_URL=%s" % HEAD])
        self.assertEqual(P.head_url(), HEAD)

    def test_missing_file_gives_the_default(self):
        if os.path.exists(LAB):
            os.remove(LAB)
        self.assertEqual(P.setting("HAI_URL", "http://10.0.0.9:8099"), "http://10.0.0.9:8099")

    def test_empty_default_means_not_set(self):
        write_lab([])
        self.assertEqual(P.setting("NOT_SET_ANYWHERE", ""), "")
        self.assertEqual(P.head_url(), "")
        self.assertEqual(P.fleet_hosts(), {})

    def test_an_empty_value_is_not_set(self):
        write_lab(["EMPTY_IN_FILE="])
        self.assertEqual(P.setting("EMPTY_IN_FILE", "fallback"), "fallback")

    def test_a_table_is_json(self):
        write_lab(['FLEET_HOSTS={"mothership": "10.0.0.76", "bighonker": "10.0.0.167"}'])
        self.assertEqual(P.fleet_hosts(), {"mothership": "10.0.0.76", "bighonker": "10.0.0.167"})
        os.environ["FLEET_HOSTS"] = '{"solo": "10.0.0.1"}'
        self.assertEqual(P.fleet_hosts(), {"solo": "10.0.0.1"})

    def test_unparseable_table_is_not_set(self):
        write_lab(["FLEET_HOSTS=mothership=10.0.0.76"])
        self.assertEqual(P.fleet_hosts(), {})


if __name__ == "__main__":
    unittest.main()
