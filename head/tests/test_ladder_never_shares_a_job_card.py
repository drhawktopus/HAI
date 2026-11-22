"""The brain ladder (the fail-open path for a turn the weighing could not place) serves a NEW turn, never a running
job's own call, so it asks whether Mothership's card is held with NO model: any live job holds it. A live claim is per
user, so asking with the small brain's model let a second job of the same account onto the card the first job held."""
import unittest
from unittest import mock

import app
import config


class TheLadderAsksWithNoModel(unittest.TestCase):
    def ladder(self, claim):
        asked = []

        def claimed(url, model=None):
            asked.append((url, model))
            return claim
        with mock.patch.object(app, "brain_order", return_value=[]), \
                mock.patch.object(app, "_card_claimed", claimed), \
                mock.patch.object(app.ollama_client, "reachable", return_value=True), \
                mock.patch("comfy_client.render_busy", return_value=False), \
                mock.patch.object(app, "brain_host_busy", return_value=False), \
                mock.patch.object(app.time, "sleep", lambda s: None), \
                mock.patch.object(config, "AUTO_WAKE_PRIMARY", False):
            got = app.pick_brain()
        return got, asked

    def test_a_free_card_serves_the_fail_open_turn_and_was_asked_with_no_model(self):
        got, asked = self.ladder(None)
        self.assertEqual(got[4], "Mothership (small brain)")
        self.assertEqual(asked[0], (config.HEAVY_HOST, None))

    def test_a_card_any_live_job_holds_is_not_taken_by_the_ladder(self):
        got, _ = self.ladder({"host": "mothership", "resource": "live", "holder": "hai-job:someone"})
        self.assertNotIn("Mothership", got[4] or "", "the ladder went to the next box, not onto the held card")


if __name__ == "__main__":
    unittest.main()
