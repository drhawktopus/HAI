"""#37 (charter § The design, remaining seams): "a cold wake (~30 s, shown honestly in the stream)". Waking Mothership
and loading the lead are SAID on the run's status line; a warm lead says nothing."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


class ColdWakeIsSaid(unittest.TestCase):
    def setUp(self):
        self.said = []
        p = [mock.patch.object(app.wol, "wake", lambda *_: None),
             mock.patch.object(app.time, "sleep", lambda *_: None),
             mock.patch.object(app.lead_ssh, "gaming_busy", lambda *a, **k: False),
             mock.patch.object(app.lead_ssh, "restart_ollama", lambda *a, **k: False),
             mock.patch.object(app.ollama_client, "port_open", lambda *a, **k: False)]   # asleep = no TCP either
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def test_an_asleep_mothership_is_announced_before_the_wait(self):
        answers = iter([False, True])           # asleep at first, then answering
        with mock.patch.object(app.ollama_client, "reachable_direct", lambda *_: next(answers)):
            self.assertTrue(app._ensure_lead(self.said.append))
        self.assertEqual(len(self.said), 1)
        self.assertIn("Waking Mothership", self.said[0])

    def test_awake_but_not_loaded_says_the_lead_is_loading(self):
        with mock.patch.object(app.ollama_client, "reachable_direct", lambda *_: True), \
                mock.patch.object(app.lead_ssh, "warm", lambda *a, **k: False):
            self.assertTrue(app._ensure_lead(self.said.append))
        self.assertEqual(len(self.said), 1)
        self.assertIn("loading the lead model", self.said[0])

    def test_a_warm_lead_says_nothing(self):
        with mock.patch.object(app.ollama_client, "reachable_direct", lambda *_: True), \
                mock.patch.object(app.lead_ssh, "warm", lambda *a, **k: True):
            self.assertTrue(app._ensure_lead(self.said.append))
        self.assertEqual(self.said, [])

    def test_no_status_line_is_fine(self):
        with mock.patch.object(app.ollama_client, "reachable_direct", lambda *_: True), \
                mock.patch.object(app.lead_ssh, "warm", lambda *a, **k: False):
            self.assertTrue(app._ensure_lead())


if __name__ == "__main__":
    unittest.main()
