"""Tests for EMBED_AVOID_HOSTS tie-break. These tests mock `scheduler.embed_placement` to return None to isolate the fallback logic; without this pin, they would read the live policy table. They also clear the claim store: a `live` claim left by another lane makes a host read held, which is not what these pins are measuring."""
import unittest
from unittest import mock

import config
import embeddings
import scheduler


class EmbedHostFloatTests(unittest.TestCase):

    def setUp(self):
        self._hosts = config.EMBED_HOSTS
        self._avoid = config.EMBED_AVOID_HOSTS
        embeddings._COOLDOWN.clear()
        embeddings._PLACED.update(at=None, order=None)
        embeddings._ORDER_NOTED[0] = None
        for name in ("bighonker", "softserve", "mothership"):
            scheduler.release(name, "live")
            scheduler.release(name, "gpu")
        self._p = mock.patch.object(scheduler, "embed_placement", return_value=None)    # no embed family: the fixed list
        self._p.start()

    def tearDown(self):
        self._p.stop()
        config.EMBED_HOSTS = self._hosts
        config.EMBED_AVOID_HOSTS = self._avoid
        embeddings._COOLDOWN.clear()
        embeddings._PLACED.update(at=None, order=None)

    def test_avoided_host_is_tried_last_not_first(self):
        """Softserve (load-bearing) must be deprioritised so an embed doesn't degrade Plex/VM/orchestrator."""
        config.EMBED_HOSTS = ["http://softserve:11434", "http://bighonker:11434"]
        config.EMBED_AVOID_HOSTS = ["http://softserve:11434"]
        order = embeddings._hosts_to_try()
        self.assertEqual(order[0], "http://bighonker:11434",
                         "an embed on the load-bearing host competes with services users depend on")
        self.assertEqual(order[-1], "http://softserve:11434")

    def test_avoided_host_is_deprioritised_never_dropped(self):
        """If the preferred boxes are down, using the load-bearing host beats losing retrieval entirely."""
        config.EMBED_HOSTS = ["http://softserve:11434", "http://bighonker:11434"]
        config.EMBED_AVOID_HOSTS = ["http://softserve:11434"]
        self.assertIn("http://softserve:11434", embeddings._hosts_to_try())

    def test_sole_host_is_still_used_even_if_avoided(self):
        config.EMBED_HOSTS = ["http://softserve:11434"]
        config.EMBED_AVOID_HOSTS = ["http://softserve:11434"]
        self.assertEqual(embeddings._hosts_to_try(), ["http://softserve:11434"])

    def test_can_be_switched_off(self):
        """An empty EMBED_AVOID_HOSTS disables avoidance; cascade order stands."""
        config.EMBED_HOSTS = ["http://softserve:11434", "http://bighonker:11434"]
        config.EMBED_AVOID_HOSTS = []
        self.assertEqual(embeddings._hosts_to_try()[0], "http://softserve:11434")

    def test_trailing_slash_does_not_defeat_the_match(self):
        """A config with a trailing slash on the avoided host must not silently re-enable the burden."""
        config.EMBED_HOSTS = ["http://softserve:11434", "http://bighonker:11434"]
        config.EMBED_AVOID_HOSTS = ["http://softserve:11434/"]
        self.assertEqual(embeddings._hosts_to_try()[0], "http://bighonker:11434")

    def test_a_failed_host_is_still_skipped_first(self):
        """The cooldown and the avoidance must compose: a dead 'other' falls back to the avoided host."""
        import time
        config.EMBED_HOSTS = ["http://softserve:11434", "http://bighonker:11434"]
        config.EMBED_AVOID_HOSTS = ["http://softserve:11434"]
        embeddings._COOLDOWN["http://bighonker:11434"] = time.monotonic() + 60
        self.assertEqual(embeddings._hosts_to_try(), ["http://softserve:11434"])

    def test_multiple_hosts_can_be_avoided(self):
        """EMBED_AVOID_HOSTS accepts a list; every listed host is deprioritised."""
        config.EMBED_HOSTS = ["http://softserve:11434", "http://sundae:11434", "http://bighonker:11434"]
        config.EMBED_AVOID_HOSTS = ["http://softserve:11434", "http://sundae:11434"]
        order = embeddings._hosts_to_try()
        self.assertEqual(order[0], "http://bighonker:11434")
        self.assertIn("http://softserve:11434", order)
        self.assertIn("http://sundae:11434", order)


if __name__ == "__main__":
    unittest.main()
