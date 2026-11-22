"""The release welcome persists once per account and release across devices via a server-side record.
The client-side logic (welcomeOnce in CHAT_HTML) is not testable in this environment (no Node.js); it is verified separately by `Lab\\bin\\welcome_page_check.py`."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


class TheAccountRemembersTheWelcome(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(app.config, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.c = TestClient(app.app)

    def seen(self, user):
        return self.c.get("/prefs/welcome", params={"user": user}).json()["seen"]

    def test_a_new_account_has_seen_nothing(self):
        self.assertEqual(self.seen("nobody"), {})

    def test_shown_on_one_device_is_seen_on_every_other(self):
        r = self.c.post("/prefs/welcome", json={"user": "test", "release": "1.9"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("1.9", self.seen("test"))
        self.assertEqual(self.seen("someone_else"), {}, "each account has its own record")

    def test_a_repeat_keeps_the_first_time_and_a_new_release_is_added_beside_it(self):
        first = self.c.post("/prefs/welcome", json={"user": "test", "release": "1.9"}).json()["seen"]["1.9"]
        again = self.c.post("/prefs/welcome", json={"user": "test", "release": "1.9"}).json()["seen"]["1.9"]
        self.assertEqual(first, again)
        self.c.post("/prefs/welcome", json={"user": "test", "release": "2.0"})
        self.assertEqual(sorted(self.seen("test")), ["1.9", "2.0"], "a new release shows once more, then never")

    def test_a_release_that_is_not_a_version_is_refused_and_nothing_is_written(self):
        for bad in ("", "latest", "../x", "1.9.1.2", "1.9<script>"):
            r = self.c.post("/prefs/welcome", json={"user": "test", "release": bad})
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.seen("test"), {})

    def test_an_unreadable_record_reads_as_nothing_seen(self):
        p = app._welcome_path("test")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not json", encoding="utf-8")
        self.assertEqual(self.seen("test"), {})
        self.c.post("/prefs/welcome", json={"user": "test", "release": "1.9"})
        self.assertIn("1.9", self.seen("test"))

    def test_the_record_sits_beside_the_accounts_other_prefs(self):
        self.assertEqual(app._welcome_path("Alex").parent, app._pin_path("Alex").parent)
        self.assertEqual(app._welcome_path("Alex").name, "Alex.welcome.json")


if __name__ == "__main__":
    unittest.main()
