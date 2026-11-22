"""McCoy's P20 create / status rules read the text of an ASK ("is my run done yet?"). A supervised job packet describes
work in prose, and a phrase like "invisible to a running job" must not turn it into a status ask that skips triage
(it then had no weighing and fell to the brain ladder's small model, twice)."""
import unittest

import mccoy


class _Q:
    def __init__(self, text, user="routetester"):
        self.text, self.user, self.routing, self.effort_mode = text, user, "auto", "normal"
        self.persona = self.mode = ""
        self.chat_id = self.absurd_max_seconds = self.self_eng_max_passes = None
        self.external_agent = self.external_use = None
        self.ephemeral = False


PACKET = ("SUPERVISED LOCAL CODING JOB\nSupervisor: Claude\nWorkspace: orchestrator-real\n"
          "Objective: A coding-runner restart must be invisible to a running job.")


class AJobPacketIsTriaged(unittest.TestCase):
    def test_a_job_packet_that_mentions_a_running_job_is_not_a_status_ask(self):
        self.assertIsNone(mccoy.special_route(PACKET, _Q(PACKET)))

    def test_a_real_status_ask_still_takes_the_status_route(self):
        ask = "what is the status of my training run?"
        self.assertEqual(mccoy.special_route(ask, _Q(ask)), "p20-status")


if __name__ == "__main__":
    unittest.main()
