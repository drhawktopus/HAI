"""The head's settings file (plan step 2.1): NAME=value lines read before any setting, the environment always wins, a
malformed file names its line and stops the head, a name the head never reads is listed (not an error). The last test
starts a head whose ONLY setting, BRAIN_HOST, is in the file (nothing in the environment), so the file is shown to
reach config at import, not just the parser."""
import os
import subprocess
import sys
import tempfile
import unittest

import config

HEAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _file(text):
    fd, path = tempfile.mkstemp(suffix=".env")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class TheFormat(unittest.TestCase):
    def test_lines_comments_export_and_quotes(self):
        got = config.parse_settings('# a comment\n\nBRAIN_HOST=http://a:1\nexport NUM_CTX = 4096\n'
                                    'BRAIN_MODEL="qwen3:8b"\nEMPTY=\nURL=http://x/?a=b\n')
        self.assertEqual(got, {"BRAIN_HOST": "http://a:1", "NUM_CTX": "4096", "BRAIN_MODEL": "qwen3:8b",
                               "EMPTY": "", "URL": "http://x/?a=b"})

    def test_each_mistake_names_its_line(self):
        for text, line, words in (("A=1\nnot a setting\n", 2, "is not NAME=value"),
                                  ("A=1\nlower=2\n", 2, "is not a setting name"),
                                  ("A=1\n\nA=2\n", 3, "sets A again (first set on line 1)")):
            with self.assertRaises(config.SettingsError) as cm:
                config.parse_settings(text, "f")
            self.assertIn("f line %d" % line, str(cm.exception))
            self.assertIn(words, str(cm.exception))


class TheEnvironmentWins(unittest.TestCase):
    def test_the_file_fills_only_what_the_environment_does_not_set(self):
        path = _file("BRAIN_HOST=http://from-file:1\nBRAIN_MODEL=from-file\n")
        env = {"BRAIN_MODEL": "from-env"}
        try:
            rep = config.load_settings(path, env)
        finally:
            os.unlink(path)
        self.assertEqual(env, {"BRAIN_MODEL": "from-env", "BRAIN_HOST": "http://from-file:1"})
        self.assertEqual((rep["found"], rep["applied"], rep["environment_won"]), (True, ["BRAIN_HOST"], ["BRAIN_MODEL"]))

    def test_no_file_is_fine(self):
        env = {}
        rep = config.load_settings(os.path.join(tempfile.mkdtemp(), "settings.env"), env)
        self.assertEqual((rep["found"], env), (False, {}))

    def test_where_the_file_is(self):
        self.assertEqual(config.settings_path({"HAI_SETTINGS_FILE": "/x/s.env", "HAI_STATE_DIR": "/s"}), "/x/s.env")
        self.assertEqual(config.settings_path({"HAI_STATE_DIR": "/s"}), os.path.join("/s", "settings.env"))
        self.assertEqual(config.settings_path({"DOCS_DIR": "/d"}), os.path.join("/d", "State", "settings.env"))


class AnUnknownName(unittest.TestCase):
    def test_a_typo_is_listed_and_a_real_setting_and_a_secret_file_are_not(self):
        rep = {"applied": ["BRIAN_HOST", "BRAIN_HOST", "HA_TOKEN_FILE"], "environment_won": []}
        self.assertEqual(config.settings_unknown(rep), ["BRIAN_HOST"])

    def test_the_known_list_sees_the_head(self):
        known = config.settings_known()
        self.assertGreaterEqual(len(known), 200, "only %d names - the scan is not seeing the head" % len(known))


class AHeadWhoseOnlySettingIsInTheFile(unittest.TestCase):
    def test_config_reads_it_at_import_and_a_broken_file_stops_the_head(self):
        root = tempfile.mkdtemp(prefix="settings-")
        state = os.path.join(root, "state")
        os.makedirs(state)
        base = {"PATH": os.environ.get("PATH", ""), "HOME": root, "PYTHONPATH": HEAD, "HAI_STATE_DIR": state,
                "DOCS_DIR": os.path.join(root, "docs"), "HAI_TEST_ISOLATED": "1"}
        probe = "import config; print('GOT', config.BRAIN_HOST, config.SETTINGS['found'], config.SETTINGS['applied'])"
        with open(os.path.join(state, "settings.env"), "w", encoding="utf-8") as fh:
            fh.write("# one setting\nBRAIN_HOST=http://127.0.0.1:9\n")
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=base, cwd=HEAD,
                             timeout=120)
        self.assertIn("GOT http://127.0.0.1:9 True ['BRAIN_HOST']", out.stdout, out.stderr[-1500:])
        with open(os.path.join(state, "settings.env"), "w", encoding="utf-8") as fh:
            fh.write("BRAIN_HOST=http://127.0.0.1:9\nthis line is wrong\n")
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=base, cwd=HEAD,
                             timeout=120)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("settings.env line 2 is not NAME=value", out.stderr)


if __name__ == "__main__":
    unittest.main()
