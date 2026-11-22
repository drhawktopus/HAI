import unittest
from unittest import mock

import config
import energy


def _state(current, today, tomorrow=None, current_hour=21):
    """Build an HA-shaped state dict. today/tomorrow are lists of 24 prices."""
    def _d(lst):
        return {str(i + 1): v for i, v in enumerate(lst)} if lst else {}
    return {"state": current,
            "attributes": {"current_hour": current_hour,
                           "today": _d(today), "tomorrow": _d(tomorrow or [])}}


# a realistic utility price shape: cheap overnight (~0.03), peak evening (~0.20)
TODAY = [0.038, 0.033, 0.033, 0.034, 0.036, 0.041, 0.055, 0.07, 0.08, 0.085, 0.09, 0.095,
         0.10, 0.105, 0.11, 0.12, 0.14, 0.16, 0.174, 0.2025, 0.182, 0.12, 0.06, 0.04]
TOMORROW = [0.035, 0.033, 0.033, 0.034, 0.037, 0.042, 0.056, 0.072, 0.082, 0.086, 0.091, 0.096,
            0.101, 0.106, 0.111, 0.121, 0.141, 0.161, 0.175, 0.203, 0.183, 0.121, 0.061, 0.041]


class CurveTests(unittest.TestCase):
    def test_parse_curve(self):
        c = energy.price_curve(_state(0.182, TODAY, TOMORROW, current_hour=21))
        self.assertEqual(c["current"], 0.182)
        self.assertEqual(c["current_hour"], 21)
        self.assertEqual(len(c["today"]), 24)
        self.assertEqual(c["today"][0], 0.038)
        self.assertEqual(c["tomorrow"][19], 0.203)

    def test_bad_state_is_none(self):
        self.assertIsNone(energy.price_curve("not a dict"))
        self.assertIsNone(energy.price_curve(123))

    def test_unparseable_current(self):
        c = energy.price_curve(_state("unavailable", TODAY))
        self.assertIsNone(c["current"])


class GateTests(unittest.TestCase):
    def test_cheap_is_runnable(self):
        c = energy.price_curve(_state(0.04, TODAY, current_hour=23))
        ok, info = energy.is_runnable_now(c, ceiling=0.10)
        self.assertTrue(ok)
        self.assertEqual(info["reason"], "cheap")

    def test_expensive_is_not_runnable(self):
        c = energy.price_curve(_state(0.2025, TODAY, current_hour=20))
        ok, info = energy.is_runnable_now(c, ceiling=0.10)
        self.assertFalse(ok)
        self.assertEqual(info["reason"], "expensive")
        self.assertEqual(info["price"], 0.2025)

    def test_at_ceiling_is_runnable(self):
        c = energy.price_curve(_state(0.10, TODAY))
        ok, _ = energy.is_runnable_now(c, ceiling=0.10)
        self.assertTrue(ok)

    def test_price_unknown_fails_closed(self):
        # explicit curves only (curve=None would trigger a live HA fetch, non-deterministic)
        ok, info = energy.is_runnable_now({"current": None}, ceiling=0.10)
        self.assertFalse(ok)
        self.assertEqual(info["reason"], "price_unknown")
        ok2, _ = energy.is_runnable_now({}, ceiling=0.10)   # empty curve also fails closed
        self.assertFalse(ok2)


class WindowTests(unittest.TestCase):
    def test_cheapest_window_picks_overnight(self):
        # from hour 21 today, the cheapest 4h window should be the overnight trough (today 22-24 + tmrw 1+)
        c = energy.price_curve(_state(0.182, TODAY, TOMORROW, current_hour=21))
        w = energy.cheapest_window(c, 4)
        self.assertIsNotNone(w)
        self.assertLess(w["avg_price"], 0.06)   # the cheap trough, not the peak

    def test_window_skips_none_prices(self):
        broken = TODAY[:]; broken[2] = None
        c = energy.price_curve(_state(0.04, broken, TOMORROW, current_hour=1))
        w = energy.cheapest_window(c, 4)
        self.assertIsNotNone(w)   # still finds a valid all-priced window

    def test_no_curve_returns_none(self):
        self.assertIsNone(energy.cheapest_window(None, 4))


class LabelTests(unittest.TestCase):
    def test_label_contains_day_hours_and_price(self):
        # Curve with a known cheap overnight trough
        c = energy.price_curve(_state(0.182, TODAY, TOMORROW, current_hour=21))
        label = energy.cheapest_window_label(c, 4)
        self.assertIn("cheapest", label)
        self.assertIn("4h:", label)
        self.assertIn("/kWh", label)
        # Verify it uses actual values from the window, not placeholders
        w = energy.cheapest_window(c, 4)
        self.assertIn(w["day"], label)
        self.assertIn(str(w["avg_price"]), label)
        # Hours are zero-padded in the label
        sh = f"{w['start_hour']:02d}:00"
        eh = f"{w['end_hour']:02d}:00"
        self.assertIn(sh, label)
        self.assertIn(eh, label)

    def test_label_unavailable_for_none_curve(self):
        self.assertEqual(energy.cheapest_window_label(None, 4),
                         "cheapest window unavailable")

    def test_label_unavailable_for_all_none_prices(self):
        # Curve with all None prices -> cheapest_window returns None
        bad = energy.price_curve(_state("unavailable", [None]*24, [None]*24, current_hour=1))
        self.assertEqual(energy.cheapest_window_label(bad, 4),
                         "cheapest window unavailable")

    def test_overnight_window_label_disambiguates_days(self):
        # Craft a curve where the cheapest 4h window crosses midnight:
        # today hours 23-24 are expensive (0.50), but tonight's wrap
        # into tomorrow hours 1-2 is cheap → start_hour=23, end_hour=2
        # The label must NOT read "today 23:00-02:00" (which implies
        # 02:00 on today, i.e. before 23:00). It should clarify the day
        # transition, e.g. "today/tomorrow".
        expensive_today = [0.50]*24
        cheap_tomorrow  = [0.50]*24
        # Make hours 22-23 on today and hours 1-2 on tomorrow very cheap
        expensive_today[21] = 0.02  # hour 22 (index 21)
        expensive_today[22] = 0.02  # hour 23 (index 22)
        expensive_today[23] = 0.02  # hour 24 (index 23)
        cheap_tomorrow[0] = 0.02    # hour 1  (index 0)
        cheap_tomorrow[1] = 0.02    # hour 2  (index 1)
        c = energy.price_curve(_state(0.50, expensive_today, cheap_tomorrow, current_hour=1))
        w = energy.cheapest_window(c, 4)
        self.assertIsNotNone(w)
        # Assert cross-midnight structure (start > end) rather than branching on it.
        # This precondition check ensures the test fails if `cheapest_window` stops
        # returning a cross-midnight window, preventing silent passes on invalid data.
        # Without this assertion, the subsequent label checks would run even if the
        # fixture failed to produce the intended cross-midnight scenario.
        self.assertGreater(w["start_hour"], w["end_hour"],
                           f"fixture no longer yields a cross-midnight window ({w!r}) — this test "
                           f"can no longer exercise the thing it is named for")
        label = energy.cheapest_window_label(c, 4)
        # Must NOT be the ambiguous "{day} {sh}-{eh}" format alone;
        # should contain a day-disambiguation token like "tomorrow"
        # or a slash indicating the transition
        self.assertIn("today/tomorrow", label.lower(),
            f"Overnight window label '{label}' is ambiguous: "
            f"start_hour ({w['start_hour']}) > end_hour ({w['end_hour']}) "
            f"but no day-transition marker present")


class PriceOfTheHour(unittest.TestCase):
    """Price is the PSP value if available, else a 0.11 placeholder (explicitly flagged in the record). Bands are derived strictly from defined limits; no arbitrary caps are applied."""

    def test_the_psp_price_when_the_sensor_answers(self):
        p = energy.price_now(energy.price_curve(_state(0.182, TODAY, TOMORROW, current_hour=21)))
        self.assertEqual((p["usd_per_kwh"], p["source"], p["hour"]), (0.182, "psp", 21))

    def test_the_placeholder_when_it_cannot_be_read_and_it_says_so(self):
        # explicit curves only: curve=None would read the live sensor
        for curve in ({}, {"current": None}, energy.price_curve(_state("unavailable", TODAY))):
            p = energy.price_now(curve)
            self.assertEqual((p["usd_per_kwh"], p["source"]), (config.ENERGY_PRICE_PLACEHOLDER, "placeholder"))
        self.assertEqual(config.ENERGY_PRICE_PLACEHOLDER, 0.11, "Alex: 'we could even just say $0.11 /kwhr'")

    def test_bands_come_only_from_lines_that_are_set(self):
        with mock.patch.object(config, "ENERGY_PEAK_PRICE", None), mock.patch.object(config, "ENERGY_EXTREME_PRICE", None):
            self.assertEqual(energy.price_band(1.00), "normal", "an unset line never engages")
        with mock.patch.object(config, "ENERGY_PEAK_PRICE", 0.15), mock.patch.object(config, "ENERGY_EXTREME_PRICE", 0.50):
            self.assertEqual([energy.price_band(p) for p in (0.11, 0.15, 0.49, 0.50, 1.00)],
                             ["normal", "peak", "peak", "extreme", "extreme"])

    def test_cents_per_solve(self):
        self.assertAlmostEqual(energy.cents_per_solve(3.6e6, 0.11), 11.0)       # one kWh at the placeholder
        self.assertAlmostEqual(energy.cents_per_solve(208, 0.11), 0.000635556, places=6)
        self.assertIsNone(energy.cents_per_solve(None, 0.11), "unmetered is never a zero")


if __name__ == "__main__":
    unittest.main()
