"""The lab's wall clock is a setting: config.LAB_TZ comes from the environment (HAI_TZ, else the container's TZ, else UTC)
and config.LAB_ZONE is that zone. No module names a zone in code; every date stamp (notes, memory dates, the librarian's
headers, cameras, the energy window) reads these two."""
import os
import unittest

import config


class LabTimezoneIsASetting(unittest.TestCase):
    def test_the_zone_follows_the_environment(self):
        want = os.environ.get("HAI_TZ") or os.environ.get("TZ") or "UTC"
        self.assertEqual(config.LAB_ZONE.key, config.LAB_TZ)
        self.assertIn(config.LAB_TZ, (want, "UTC"), "the zone is the environment's, or UTC when that name is unknown")

    def test_a_known_name_is_that_zone_and_an_unknown_name_is_utc(self):
        self.assertEqual(config.lab_zone("Asia/Tokyo").key, "Asia/Tokyo")
        self.assertEqual(config.lab_zone("Nowhere/Nope").key, "UTC", "a typo in TZ must not take every date stamp down")


if __name__ == "__main__":
    unittest.main()
