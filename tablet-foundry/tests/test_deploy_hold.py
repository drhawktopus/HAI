"""A deploy must hold Tolkien's intake from before its stop until after the swap to prevent killed turns."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry as tf
import tablet_runner as tr


class IntakeHold(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.flag = os.path.join(self.d, "DEPLOY_HOLD")
        p = mock.patch.object(tf, "DEPLOY_HOLD", self.flag)
        p.start()
        self.addCleanup(p.stop)

    def hold(self, age=0):
        open(self.flag, "w").close()
        t = time.time() - age
        os.utime(self.flag, (t, t))

    def test_no_flag_no_hold(self):
        self.assertFalse(tf.intake_held())

    def test_a_flag_holds(self):
        self.hold()
        self.assertTrue(tf.intake_held())

    def test_a_deploy_that_died_cannot_stop_the_factory_for_good(self):
        self.hold(tf.DEPLOY_HOLD_MAX_S + 60)
        self.assertFalse(tf.intake_held())

    def test_both_lanes_pick_nothing_and_do_not_wake_under_a_hold(self):
        q = ["/q/a.tjob.json"]
        with mock.patch.object(tr, "queued", return_value=q), mock.patch.object(tr, "lane_of", return_value="rigby"):
            self.assertEqual(tr.open_to_start(q), q, "positive control: no hold, the pick sees its work")
            self.assertTrue(tr._work_waiting("rigby", {}), "positive control")
            self.hold()
            self.assertEqual(tr.open_to_start(q), [])
            self.assertFalse(tr._work_waiting("rigby", {}), "a lane that kept waking would spin for the whole hold")
        with mock.patch.object(tr, "queued", return_value=q), mock.patch.object(tr, "lane_of", return_value="fleet"):
            self.assertFalse(tr._fleet_work_waiting({}))


if __name__ == "__main__":
    unittest.main()
