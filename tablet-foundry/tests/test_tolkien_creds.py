import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tablet_runner as tr  # noqa: E402

DOC = """# Camera credentials

| field | value |
|---|---|
| **Username** | `admin` |
| **Password** | `Ex4mple-Pw!$` |
"""


class CredentialResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tolkcred-")
        self.doc = os.path.join(self.tmp, "camera-credentials.md")
        with io.open(self.doc, "w", encoding="utf-8") as fh:
            fh.write(DOC)
        self._doc, self._env = tr.CAMERA_CRED_DOC, dict(os.environ)
        tr.CAMERA_CRED_DOC = self.doc
        os.environ.pop("TOLKIEN_NVR_USER", None)
        os.environ.pop("TOLKIEN_NVR_PASS", None)

    def tearDown(self):
        tr.CAMERA_CRED_DOC = self._doc
        os.environ.clear()
        os.environ.update(self._env)

    def test_it_reads_the_doc_with_no_env_and_no_new_copy(self):
        user, pw, src = tr._nvr_credentials()
        self.assertEqual(user, "admin")
        self.assertEqual(pw, "Ex4mple-Pw!$")
        self.assertIn("credentials doc", src)

    def test_the_TRAILING_DOLLAR_survives(self):
        _user, pw, _src = tr._nvr_credentials()
        self.assertEqual(len(pw), 12)
        self.assertTrue(pw.endswith("!$"), repr(pw))

    def test_env_OVERRIDES_the_doc(self):
        """The doc is a default, not a cage -- a rotated or compromised credential can be overridden
        without editing documentation."""
        os.environ["TOLKIEN_NVR_USER"] = "svc_cameras"
        os.environ["TOLKIEN_NVR_PASS"] = "somethingelse"
        user, pw, src = tr._nvr_credentials()
        self.assertEqual((user, pw), ("svc_cameras", "somethingelse"))
        self.assertIn("environment", src)

    def test_a_HALF_set_env_does_not_win(self):
        'Partial env overrides (user without password) must fall back to the credentials doc.\n\n    A half-credential is treated as a mistake, not a valid partial configuration.'
        os.environ["TOLKIEN_NVR_USER"] = "svc_cameras"
        user, _pw, src = tr._nvr_credentials()
        self.assertEqual(user, "admin", "a half-set env must not shadow the doc")
        self.assertIn("credentials doc", src)

    def test_a_MISSING_doc_names_itself_rather_than_failing_silently(self):
        tr.CAMERA_CRED_DOC = os.path.join(self.tmp, "does-not-exist.md")
        user, pw, why = tr._nvr_credentials()
        self.assertIsNone(user)
        self.assertIsNone(pw)
        self.assertIn("does-not-exist.md", why)
        self.assertIn("unreadable", why)

    def test_an_UNPARSEABLE_doc_is_a_DIFFERENT_error_from_a_missing_one(self):
        """
        Present-but-unparseable and absent are different failures.
        A doc reformat can look identical to a missing mount,
        leading to incorrect debugging paths.
        """
        with io.open(self.doc, "w", encoding="utf-8") as fh:
            fh.write("# Camera credentials\n\nnothing parseable here\n")
        user, _pw, why = tr._nvr_credentials()
        self.assertIsNone(user)
        self.assertIn("did not parse", why)
        self.assertNotIn("unreadable", why)

    def test_the_subscriber_REFUSES_to_start_without_credentials(self):
        """A subscriber with no credentials would connect, be refused, and look busy -- heartbeat
        reporting 'watching' while nothing is ever recorded. Refusing is the honest state."""
        tr.CAMERA_CRED_DOC = os.path.join(self.tmp, "gone.md")
        started, why = tr.camera_subscriber_start()
        self.assertFalse(started)
        self.assertIn("no NVR credentials", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
