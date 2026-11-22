"""#37: Model/Effort pin is per-account, not per-device."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


class PinFollowsTheAccount(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(app.config, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.c = TestClient(app.app)

    def test_a_pin_saved_on_one_device_is_read_on_another(self):
        r = self.c.post("/prefs/pin", json={"user": "test", "model": "force_27b", "effort": "think_hard"})
        self.assertEqual(r.status_code, 200, r.text)
        got = self.c.get("/prefs/pin", params={"user": "test"}).json()["pin"]
        self.assertEqual((got["model"], got["effort"]), ("force_27b", "think_hard"))

    def test_auto_and_normal_clear_the_pin(self):
        self.c.post("/prefs/pin", json={"user": "test", "model": "force_27b", "effort": "normal"})
        self.c.post("/prefs/pin", json={"user": "test", "model": "auto", "effort": "normal"})
        self.assertIsNone(self.c.get("/prefs/pin", params={"user": "test"}).json()["pin"])

    def test_each_user_has_their_own_pin(self):
        self.c.post("/prefs/pin", json={"user": "test", "model": "force_27b", "effort": "normal"})
        self.assertIsNone(self.c.get("/prefs/pin", params={"user": "someone_else"}).json()["pin"])

    def test_no_pin_yet_is_auto(self):
        self.assertIsNone(self.c.get("/prefs/pin", params={"user": "nobody"}).json()["pin"])


class ThePageSaysSo(unittest.TestCase):
    def test_the_page_tells_the_user_the_pin_follows_the_account(self):
        """The served page must not contradict the account-level pin persistence claim."""
        self.assertNotIn("remembered on this device", app.CHAT_HTML)
        self.assertIn("remembered for your account on every device", app.CHAT_HTML)


class TheCriticIsTheLead(unittest.TestCase):
    def test_the_default_critic_pool_is_the_lead_alone(self):
        """The default critic pool must match the lead heavy model to avoid redundant model reloads per pass."""
        self.assertEqual(app.config._THINK_HARD_POOL_DEFAULT, app.config.HEAVY_MODEL)


if __name__ == "__main__":
    unittest.main()
