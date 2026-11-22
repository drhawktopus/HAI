"""Every secret the head reads (a token, a key, a password, a webhook) can live in a root-only file instead of the
environment: config.py reads them through `_secret_value(NAME, NAME_FILE)`, and a standalone service that does not
import config (the host-ops sidecar, the coding runner) reads its own NAME_FILE beside NAME. The environment still wins
where it is set. A secret-shaped variable read from the environment in a file that never reads its NAME_FILE has no
file form and fails this test, in any non-test file of the head."""
import ast
import os
import tempfile
import unittest

import config

HEAD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_END = ("TOKEN", "APIKEY", "API_KEY", "PASSWORD", "PASS", "SECRET", "WEBHOOK")
NOT_SECRETS = {"CRITIC_CHARS_PER_TOKEN"}    # a ratio, not a secret
SKIP_DIRS = {"tests", "__pycache__", ".git", "backups", "node_modules"}


def _env_reads(src):
    """(line, NAME) for every os.getenv / environ.get / environ[...] read of a constant upper-case name."""
    out = []
    for node in ast.walk(ast.parse(src)):
        name = None
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("getenv", "get") \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            name = node.args[0].value
        elif isinstance(node, ast.Subscript) and getattr(node.value, "attr", "") == "environ" \
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            name = node.slice.value
        if name and name.isupper():
            out.append((node.lineno, name))
    return out


def _bare_secrets(src):
    reads = _env_reads(src)
    names = {n for _, n in reads}
    return [(ln, n) for ln, n in reads
            if n.endswith(SECRET_END) and n not in NOT_SECRETS and n + "_FILE" not in names]


def _head_files():
    for root, dirs, files in os.walk(HEAD):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


class SecretsTakeAFile(unittest.TestCase):
    def test_the_scan_sees_a_bare_secret_and_passes_a_paired_one(self):
        self.assertEqual(_bare_secrets('import os\nX = os.environ.get("SOME_TOKEN", "")\n'), [(2, "SOME_TOKEN")])
        self.assertEqual(_bare_secrets('import os\nX = os.getenv("A_PASS") or open(os.getenv("A_PASS_FILE")).read()\n'),
                         [])
        self.assertEqual(_bare_secrets('import os\nX = os.environ["B_WEBHOOK"]\n'), [(2, "B_WEBHOOK")])

    def test_no_secret_in_the_head_is_read_from_the_environment_alone(self):
        bad, files, secret_reads = [], 0, 0
        for path in _head_files():
            with open(path, encoding="utf-8-sig") as fh:
                src = fh.read()
            try:
                reads = _env_reads(src)
            except SyntaxError as e:
                self.fail("cannot read %s, so it is not checked: %s" % (path, e))
            files += 1
            secret_reads += sum(1 for _, n in reads if n.endswith(SECRET_END))
            secret_reads += sum(1 for node in ast.walk(ast.parse(src)) if isinstance(node, ast.Call)
                                and getattr(node.func, "id", "") == "_secret_value")
            bad += ["%s:%d %s" % (os.path.relpath(path, HEAD), ln, n) for ln, n in _bare_secrets(src)]
        self.assertGreaterEqual(files, 50, "scanned only %d files - the walk is not seeing the head" % files)
        self.assertGreaterEqual(secret_reads, 10, "saw only %d secret reads - the scan is not seeing them" % secret_reads)
        self.assertEqual(bad, [], "secrets read from the environment with no _FILE form "
                                  "(use config._secret_value(NAME, NAME_FILE)): %s" % bad)

    def test_the_file_is_read_when_the_variable_is_unset_and_the_variable_wins_when_set(self):
        with tempfile.NamedTemporaryFile("w", suffix=".secret", delete=False, encoding="utf-8") as f:
            f.write("  from-the-file\n")
        old = {k: os.environ.get(k) for k in ("HAI_TEST_SECRET", "HAI_TEST_SECRET_FILE")}
        try:
            os.environ.pop("HAI_TEST_SECRET", None)
            os.environ["HAI_TEST_SECRET_FILE"] = f.name
            self.assertEqual(config._secret_value("HAI_TEST_SECRET", "HAI_TEST_SECRET_FILE"), "from-the-file")
            os.environ["HAI_TEST_SECRET"] = "from-the-env"
            self.assertEqual(config._secret_value("HAI_TEST_SECRET", "HAI_TEST_SECRET_FILE"), "from-the-env")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
