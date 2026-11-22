"""A finished chat's loaded model is a cache and must not block subsequent turns."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scheduler  # noqa: E402

URL = scheduler.CARDS["bighonker"]["url"]


class TheLeftoverModelIsACache(unittest.TestCase):
    def ask(self, holder, idle, takes_window=True, model="the-next-model"):
        """occupied_for with a live claim for another model; `idle` = Kirk's own window there is idle (its name) or not."""
        live = {"host": "bighonker", "resource": "live", "holder": holder, "note": "the-loaded-model"}
        with mock.patch.object(scheduler, "claim_for_url", lambda url, resource="gpu": live if resource == scheduler.LIVE else None), \
                mock.patch.object(scheduler, "idle_own_window", lambda host, m: idle), \
                mock.patch.object(scheduler, "own_window", lambda host: None), \
                mock.patch.object(scheduler, "render_busy", lambda host: (False, "")):
            return scheduler.occupied_for(URL, model, takes_window=takes_window)

    def test_a_chat_turn_takes_a_card_whose_only_occupant_is_a_finished_chats_model(self):
        self.assertIsNone(self.ask("hai-chat:Alex", idle="hai-win-bighonker-x"))

    def test_a_turn_in_flight_or_a_job_still_holds_it(self):
        self.assertIsNotNone(self.ask("hai-chat:Alex", idle=None), "its window is in use: a turn is being written")
        self.assertIsNotNone(self.ask("hai-job:Alex", idle="hai-win-bighonker-x"), "a job is live between its own calls")

    def test_a_call_that_is_not_a_chat_turn_never_takes_it(self):
        self.assertIsNotNone(self.ask("hai-chat:Alex", idle="hai-win-bighonker-x", takes_window=False),
                             "an embed or a title would load beside the model and evict it")
        self.assertIsNone(self.ask("hai-chat:Alex", idle=None, model="the-loaded-model"), "the same model joins, as before")


if __name__ == "__main__":
    unittest.main()
