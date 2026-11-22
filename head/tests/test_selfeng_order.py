"""
Tests for selfeng.overnight.

Key invariants:
- Exactly-one-window validation with double-lock (requires+host).
- Feed validity is judged by window coverage, not fetch age.
- Dispatcher states: WAIT, FIRE, or EXPIRE; stale feeds HOLD, never consume.
- Tests use direct tick calls; the background scheduler is inert during test execution.
"""
import datetime
import json
import tempfile
import time
import unittest
from unittest import mock

import app
import config
import energy
import p20_queue


def _ha_state(hours_ago=0, today_full=True, tomorrow_full=False, hole_hour=None):
    """A fake HA PSP sensor state: last_updated `hours_ago`, arrays as requested."""
    upd = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    def _arr(full):
        vals = {str(h): 0.04 for h in range(1, 25)} if full else {}
        if full and hole_hour is not None:
            vals[str(hole_hour)] = None
        return vals
    return {"state": "0.04", "last_updated": upd.isoformat(),
            "attributes": {"current_hour": 12, "today": _arr(today_full),
                           "tomorrow": _arr(tomorrow_full)}}


class WindowCoverage(unittest.TestCase):
    def test_fresh_today_data_covers_a_today_window(self):
        """Anchor to local noon (12:00) to avoid midnight boundary crossings.
        Using `time.time()` would create flaky tests during the 23:00 hour,
        where a 1-hour window crosses into the next day but the fixture
        lacks data for that day."""
        noon = datetime.datetime.now(config.LAB_ZONE).replace(
            hour=12, minute=0, second=0, microsecond=0).timestamp()
        ok, info = energy.window_covered(noon, noon + 3600, state=_ha_state(hours_ago=0))
        self.assertTrue(ok, info)

    def test_a_window_crossing_midnight_needs_tomorrow_data(self):
        """The behaviour the red gate above exposed, now pinned DELIBERATELY at a fixed 23:30
        anchor so it is exercised at every hour of the day rather than only in the hour it was
        discovered in. A window crossing local midnight touches hour 0 of the NEXT date, which
        lives in the `tomorrow` array — an empty one must refuse and NAME the hour.

        The second half is the positive control: the same window with tomorrow's prices present
        must PASS, otherwise this test would go green for a detector that refuses everything."""
        late = datetime.datetime.now(config.LAB_ZONE).replace(
            hour=23, minute=30, second=0, microsecond=0).timestamp()
        ok, info = energy.window_covered(late, late + 3600, state=_ha_state(hours_ago=0))
        self.assertFalse(ok, "an empty tomorrow array cannot cover hour 0 of tomorrow")
        self.assertEqual(info["reason"], "window_not_covered")
        self.assertTrue([h for h in info["missing_hours"] if h.endswith("T00:00")], info)

        ok2, info2 = energy.window_covered(late, late + 3600,
                                           state=_ha_state(hours_ago=0, tomorrow_full=True))
        self.assertTrue(ok2, info2)

    def test_three_day_old_data_covers_nothing_current(self):
        """The frozen-feed case: arrays anchored to the FETCH date, not the wall clock — stale
        'today' must not read as today."""
        now = time.time()
        ok, info = energy.window_covered(now, now + 3600, state=_ha_state(hours_ago=72))
        self.assertFalse(ok)
        self.assertEqual(info["reason"], "window_not_covered")

    def test_missing_timestamp_refuses(self):
        s = _ha_state()
        del s["last_updated"]
        ok, info = energy.window_covered(time.time(), time.time() + 3600, state=s)
        self.assertFalse(ok)
        self.assertEqual(info["reason"], "no_data_timestamp")

    def test_a_hole_in_the_window_refuses_and_names_the_hours(self):
        local_now = datetime.datetime.now(config.LAB_ZONE)
        hole = local_now.hour + 1                       # the utility's keys are 1-24 for hour-1 slots
        ok, info = energy.window_covered(time.time(), time.time() + 3600,
                                         state=_ha_state(hole_hour=hole))
        self.assertFalse(ok, "the current hour's slot is holed - fixture wrong if this passed")
        self.assertTrue(info["missing_hours"], "refusals must NAME what is missing")

    def test_a_window_across_a_clock_change_is_walked_in_real_hours(self):
        """Use fixed dates spanning DST transitions (fall-back and spring-forward) to ensure the window walk is validated daily, not just on the specific nights the event occurs."""
        chi = config.LAB_ZONE
        for fetched, start in ((datetime.datetime(2026, 10, 31, 23, 0, tzinfo=chi),
                                datetime.datetime(2026, 11, 1, 1, 30, tzinfo=chi)),            # fall back at 02:00
                               (datetime.datetime(2027, 3, 13, 23, 0, tzinfo=chi),
                                datetime.datetime(2027, 3, 14, 1, 30, tzinfo=chi))):           # spring forward
            st = _ha_state(tomorrow_full=True)
            st["last_updated"] = fetched.isoformat()
            ok, info = energy.window_covered(start.timestamp(), start.timestamp() + 3600, state=st)
            self.assertTrue(ok, (start.isoformat(), info))
            ok, info = energy.window_covered(start.timestamp(), start.timestamp() + 3600,
                                             state=dict(st, attributes=dict(st["attributes"], tomorrow={})))
            self.assertFalse(ok, "positive control: without that date's prices the same window must refuse")
            self.assertEqual(info["reason"], "window_not_covered", info)

    def test_tomorrow_array_covers_tomorrow(self):
        """The ~6pm day-ahead publication: yesterday's fetch legitimately covers today via its
        `tomorrow` array — the exact case a fetched-within-6h bound would wrongly kill."""
        tomorrow_noon = (datetime.datetime.now(config.LAB_ZONE)
                         + datetime.timedelta(days=1)).replace(hour=12, minute=0, second=0)
        ok, info = energy.window_covered(tomorrow_noon.timestamp(),
                                         tomorrow_noon.timestamp() + 3600,
                                         state=_ha_state(hours_ago=0, tomorrow_full=True))
        self.assertTrue(ok, info)


class OrderIntakeAndDispatch(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="selfeng-desk-")
        self._orig = p20_queue.QUEUE_DIR
        p20_queue.QUEUE_DIR = self._dir

    def tearDown(self):
        p20_queue.QUEUE_DIR = self._orig

    def _spec(self, **kw):
        start = time.time() - 60
        base = {"name": "selfeng_test", "kind": "selfeng.overnight", "max_seconds": 600,
                "window": {"start": start, "end": start + 3600}}
        base.update(kw)
        return base

    def test_valid_operator_window_is_held_with_both_locks(self):
        out = app._selfeng_order_intake(self._spec())
        self.assertTrue(out["ok"], out)
        job = p20_queue.get(out["id"])
        self.assertEqual(job["requires"], ["selfeng.overnight"])
        self.assertEqual(job["host"], "kirk")
        self.assertEqual(job["kind"], "factory_order")   # render workers' allow-list skips it
        self.assertFalse(p20_queue.buildable_by([job], ["render.klein", "bake.klein"]),
                         "no factory capability set may ever match a selfeng order")

    def test_both_window_forms_refused(self):
        out = app._selfeng_order_intake(self._spec(window_mode="cheapest_4h"))
        self.assertFalse(out["ok"])

    def test_neither_window_form_refused(self):
        s = self._spec()
        del s["window"]
        self.assertFalse(app._selfeng_order_intake(s)["ok"])

    def test_backwards_window_refused(self):
        s = self._spec(window={"start": time.time() + 3600, "end": time.time()})
        self.assertFalse(app._selfeng_order_intake(s)["ok"])

    def test_tick_fires_in_window_and_marks_first(self):
        out = app._selfeng_order_intake(self._spec())
        with mock.patch.object(app, "_overnight_start") as start, \
             mock.patch.object(app, "_overnight_busy", return_value=False), \
             mock.patch.object(app.backlog, "eligible_items", return_value=[{"id": "x"}]):
            app._selfeng_orders_tick()
        start.assert_called_once()
        self.assertEqual(p20_queue.get(out["id"])["status"], "done")
        self.assertTrue(start.call_args[0][0].ignore_energy_gate,
                        "operator window IS the energy decision - the gate must not re-judge it")

    def test_tick_waits_before_window(self):
        start_at = time.time() + 3600
        out = app._selfeng_order_intake(self._spec(window={"start": start_at,
                                                          "end": start_at + 3600}))
        with mock.patch.object(app, "_overnight_start") as start:
            app._selfeng_orders_tick()
        start.assert_not_called()
        self.assertEqual(p20_queue.get(out["id"])["status"], "pending")

    def test_tick_expires_past_window(self):
        out = app._selfeng_order_intake(self._spec(window={"start": time.time() - 7200,
                                                          "end": time.time() - 3600}))
        with mock.patch.object(app, "_overnight_start") as start:
            app._selfeng_orders_tick()
        start.assert_not_called()
        self.assertEqual(p20_queue.get(out["id"])["status"], "expired")

    def test_status_pane_serves_fate_never_the_work(self):
        """
        The order status view must carry the order's state but must not leak
        the spec body, preventing duplication of the fleet-desk pin.
        """
        out = app._selfeng_order_intake(self._spec())
        view = app._order_status_view(out["id"])
        self.assertTrue(view["ok"])
        self.assertEqual(view["status"], "pending")
        self.assertEqual(view["kind"], "selfeng.overnight")
        self.assertNotIn("spec", view, "the pane serves fate, never the work itself")
        self.assertNotIn("window", json.dumps(view), "no spec fields may leak through the pane")

    def test_status_pane_unknown_id_is_loud(self):
        view = app._order_status_view("nope00000000")
        self.assertFalse(view["ok"])
        self.assertIn("no such order", view["error"])

    def test_computed_mode_holds_on_stale_feed_and_names_why(self):
        """A dead utility feed means WAIT with the reason recorded — never expiry, never a fire on
        stale data. The order outlives the outage."""
        s = self._spec(window_mode="cheapest_4h")
        del s["window"]
        out = app._selfeng_order_intake(s)
        with mock.patch.object(app.energy, "window_covered",
                               return_value=(False, {"reason": "window_not_covered"})), \
             mock.patch.object(app, "_overnight_start") as start:
            app._selfeng_orders_tick()
        start.assert_not_called()
        job = p20_queue.get(out["id"])
        self.assertEqual(job["status"], "pending")
        self.assertIn("coverage", job.get("hold_reason", ""))


if __name__ == "__main__":
    unittest.main()
