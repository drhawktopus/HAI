"""The tablets' hardware classes and their own lanes are settings (TABLET_CLASSES, TABLET_SKU_LANES): unset or malformed
means no table at all, never another lab's tablets."""
import os
import unittest
from unittest import mock

import tablet_foundry as tf


class TabletClassesAreASetting(unittest.TestCase):
    def test_unset_is_no_table(self):
        with mock.patch.dict(os.environ, {"TABLET_CLASSES_PROBE": ""}):
            self.assertEqual(tf._tablet_table("TABLET_CLASSES_PROBE"), {})

    def test_malformed_is_no_table(self):
        for raw in ("[1, 2", "[1, 2]", "not json"):
            with mock.patch.dict(os.environ, {"TABLET_CLASSES_PROBE": raw}):
                self.assertEqual(tf._tablet_table("TABLET_CLASSES_PROBE"), {}, raw)

    def test_the_lane_table_sits_over_the_class_table(self):
        # the gate's settings: sam is class B hardware and runs its own lane
        self.assertEqual(tf.TABLET_CLASSES.get("sam"), "B")
        self.assertEqual(tf.TABLET_SKU.get("sam"), "sam")
        self.assertEqual({k: v for k, v in tf.TABLET_SKU.items() if k != "sam"},
                         {k: v for k, v in tf.TABLET_CLASSES.items() if k != "sam"})


if __name__ == "__main__":
    unittest.main()
