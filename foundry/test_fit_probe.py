"""fit_probe.search: the largest context that stays fully on the card, found from the box's own start value, with
every stop measured (first spill, native maximum, server clamp) and never a chosen cap."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fit_probe as FP  # noqa: E402


def card(threshold):
    return lambda ctx: ctx <= threshold


class FitSearch(unittest.TestCase):

    def test_start_below_the_limit_doubles_then_bisects(self):
        best, spill, tried = FP.search(card(20000), start=6144)
        self.assertLessEqual(best, 20000)
        self.assertGreater(best, 20000 - FP.RESOLUTION)
        self.assertGreater(spill, 20000)
        self.assertEqual(tried[:3], [6144, 12288, 24576], "starts at the box value, then doubles to the first spill")

    def test_start_above_the_limit_halves_then_bisects(self):
        # the Softserve 9B shape: the box's 8192 spills
        best, spill, _ = FP.search(card(5000), start=8192)
        self.assertLessEqual(best, 5000)
        self.assertGreater(best, 5000 - FP.RESOLUTION)
        self.assertGreater(spill, 5000)

    def test_nothing_fits_is_reported_as_zero(self):
        best, spill, tried = FP.search(card(100), start=8192)
        self.assertEqual(best, 0)
        self.assertEqual(tried[-1], FP.RESOLUTION, "stops at the measurement resolution, not before")

    def test_native_maximum_is_the_ceiling(self):
        best, spill, _ = FP.search(card(10 ** 9), start=6144, top=32768)
        self.assertEqual((best, spill), (32768, None))

    def test_a_server_clamp_stops_the_climb(self):
        seen = []

        def probe(ctx):
            seen.append(ctx)
            return True
        best, spill, _ = FP.search(probe, start=4096, clamped=lambda: len(seen) >= 3)
        self.assertEqual((best, spill), (16384, None))
        self.assertEqual(seen, [4096, 8192, 16384])

    def test_exact_power_of_two_limit_is_found(self):
        best, spill, _ = FP.search(card(16384), start=4096)
        self.assertEqual(best, 16384)


class FitsMeansNoSharedSpill(unittest.TestCase):
    """A model does not fit if it spills to shared memory, even if it fits the dedicated VRAM limit."""

    def test_a_shared_memory_spill_on_a_full_card_does_not_fit(self):
        spilled = {"shared_mb": 1544, "dedicated_mb": 23526, "adapter_mb": 23683, "card_mb": 24560}
        self.assertFalse(FP.fits(17_600_000_000, 17_600_000_000, spilled))
        self.assertTrue(FP.fits(17_600_000_000, 17_600_000_000, dict(spilled, shared_mb=170, adapter_mb=24438)))
        self.assertTrue(FP.fits(1, 1, None), "unreadable counters fall back to ollama's own test")
        self.assertFalse(FP.fits(2, 1, None))


if __name__ == "__main__":
    unittest.main()
