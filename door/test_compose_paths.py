"""The compose file hands the app each folder by the CONTAINER's path. A mount whose host side is a ${VAR} from .env,
where the app also reads VAR, must set VAR to the container side in `environment:`: otherwise env_file gives the app
the host's path, the folder is missing inside the container, and the app quietly serves nothing (every transcript list
reads zero, with no error)."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOUNT = re.compile(r"^\s*-\s*\$\{([A-Z][A-Z0-9_]*)[^}]*\}:(/[^:\s]+)", re.M)
SETTING = re.compile(r"^\s*-\s*([A-Z][A-Z0-9_]*)=(\S+)\s*$", re.M)


def unset_mounts(compose_text, app_text):
    """[(VAR, container path)] for each ${VAR} mount the app reads whose container path `environment:` does not set."""
    read_by_app = set(re.findall(r"""env\.get\(\s*["']([A-Z][A-Z0-9_]*)["']""", app_text))
    env = dict(SETTING.findall(compose_text))
    return [(var, inside) for var, inside in MOUNT.findall(compose_text)
            if var in read_by_app and env.get(var) != inside]


class AppFoldersAreContainerPaths(unittest.TestCase):
    def setUp(self):
        self.compose = open(os.path.join(HERE, "docker-compose.yml"), encoding="utf-8").read()
        self.app = open(os.path.join(HERE, "uhura.py"), encoding="utf-8").read()

    def test_the_parser_sees_the_mounts_it_judges(self):
        self.assertIn(("UHURA_TRANSCRIPTS_DIR", "/transcripts"), MOUNT.findall(self.compose),
                      "the ${VAR}:/path mount was not read - a broken parser would pass everything")

    def test_every_host_folder_the_app_reads_is_set_to_its_container_path(self):
        self.assertEqual(unset_mounts(self.compose, self.app), [])

    def test_the_rule_catches_a_compose_without_the_container_path(self):
        broken = self.compose.replace("      - UHURA_TRANSCRIPTS_DIR=/transcripts\n", "")
        self.assertNotEqual(broken, self.compose, "the fixture lost its anchor")
        self.assertEqual(unset_mounts(broken, self.app), [("UHURA_TRANSCRIPTS_DIR", "/transcripts")])


if __name__ == "__main__":
    unittest.main()
