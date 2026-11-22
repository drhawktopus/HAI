"""Camera tools for HAI.

Behaviour, not source text. Three properties worth protecting, none obvious from the code:

  * a cat question must match `dog` too — Sam's label flickers between them
  * an unavailable event backend must SAY SO, never return an empty result that reads as "nothing happened"
  * the 0.17 score lives under `data.top_score`; reading the old top-level field silently yields 0%
"""
import os
import sys
import time
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cameras  # noqa: E402


def ev(camera="basement", label="cat", score=0.7, ago=60, zones=None, legacy_score=False):
    e = {"camera": camera, "label": label, "start_time": time.time() - ago,
         "zones": zones or [], "id": "x%d" % ago}
    if legacy_score:
        e["top_score"] = score
    else:
        e["data"] = {"top_score": score}
    return e


class FakeFrigate(object):
    """Swap in for cameras._get. Records calls so param-passing can be asserted."""

    def __init__(self, payload, ok=True):
        self.payload, self.ok, self.calls = payload, ok, []

    def __call__(self, path, params=None):
        self.calls.append((path, params or {}))
        return self.ok, self.payload


class AnimalLabelFlicker(unittest.TestCase):
    """The load-bearing one: Sam's label flickers between `cat` and `dog` on the same animal seconds
    apart, so a cat query that only matches `cat` loses about half the sightings."""

    def setUp(self):
        self._get = cameras._get

    def tearDown(self):
        cameras._get = self._get

    def test_asking_for_cat_also_returns_dog(self):
        cameras._get = FakeFrigate([ev(label="dog", score=0.6, ago=30)])
        out = cameras.events(label="cat")
        self.assertIn("dog", out)
        self.assertNotIn("No ", out[:4])

    def test_asking_for_person_does_NOT_return_cats(self):
        """The widening must be specific to the cat/dog confusion, not a general free-for-all."""
        cameras._get = FakeFrigate([ev(label="cat"), ev(label="dog")])
        out = cameras.events(label="person")
        self.assertTrue(out.startswith("No "), out[:80])

    def test_last_seen_cat_matches_a_dog_labelled_sighting(self):
        cameras._get = FakeFrigate([ev(label="dog", score=0.6, ago=120, zones=["litter_tray"])])
        out = cameras.last_seen(label="cat", zone="litter_tray")
        self.assertNotIn("No cat", out)
        self.assertIn("litter_tray", out)


class FailuresAreLoud(unittest.TestCase):
    """An unreachable event backend must never look like a quiet house."""

    def setUp(self):
        self._get = cameras._get

    def tearDown(self):
        cameras._get = self._get

    def test_unreachable_frigate_says_so(self):
        cameras._get = FakeFrigate("Could not reach Frigate (URLError: refused)", ok=False)
        for out in (cameras.events(), cameras.last_seen(), cameras.status()):
            self.assertIn("Frigate", out)
            self.assertNotIn("No cat", out)

    def test_genuine_absence_is_distinguishable_from_failure(self):
        cameras._get = FakeFrigate([])
        out = cameras.events(label="cat")
        self.assertIn("No ", out)
        self.assertIn("real absence", out)


class ScoreField(unittest.TestCase):
    def setUp(self):
        self._get = cameras._get

    def tearDown(self):
        cameras._get = self._get

    def test_reads_the_0_17_nested_score(self):
        cameras._get = FakeFrigate([ev(score=0.82)])
        self.assertIn("82%", cameras.events())

    def test_still_reads_the_legacy_top_level_score(self):
        cameras._get = FakeFrigate([ev(score=0.82, legacy_score=True)])
        self.assertIn("82%", cameras.events())


class Filtering(unittest.TestCase):
    def setUp(self):
        self._get = cameras._get

    def tearDown(self):
        cameras._get = self._get

    def test_zone_filter_excludes_events_outside_it(self):
        cameras._get = FakeFrigate([ev(zones=["couch"]), ev(zones=[], ago=90)])
        out = cameras.events(label="cat", zone="couch")
        self.assertIn("1 detection", out)

    def test_camera_is_passed_through_to_frigate(self):
        f = FakeFrigate([])
        cameras._get = f
        cameras.events(camera="basement")
        self.assertEqual(f.calls[0][1].get("cameras"), "basement")

    def test_results_are_newest_first(self):
        cameras._get = FakeFrigate([ev(ago=600, score=0.5), ev(ago=10, score=0.9)])
        out = cameras.events()
        self.assertLess(out.index("90%"), out.index("50%"))


class StatusReportsDetectionNotJustDecode(unittest.TestCase):
    """The status answer must surface detection_fps, not camera_fps: the decode metric can read
    healthy while detection is off entirely."""

    def setUp(self):
        self._get = cameras._get

    def tearDown(self):
        cameras._get = self._get

    def test_detection_fps_is_reported(self):
        cameras._get = FakeFrigate({"cameras": {"basement": {"camera_fps": 5.1,
                                                             "detection_fps": 0.0}},
                                    "detectors": {"cpu1": {"inference_speed": 12.7}}})
        out = cameras.status()
        self.assertIn("detection", out)
        self.assertIn("5.1", out)
        self.assertIn("12.7", out)


class ScanIsQueuedNotInstant(unittest.TestCase):
    def test_rejects_a_bad_time_format_before_writing_anything(self):
        out = cameras.scan_footage("basement", "2am", "4am")
        self.assertIn("HH:MM", out)

    def test_rejects_an_unknown_camera(self):
        out = cameras.scan_footage("garage", "02:00", "04:00")
        self.assertIn("Unknown camera", out)

    def test_says_plainly_that_it_is_queued_work(self):
        import tempfile
        cameras.config.TABLET_INBOX_HOST = tempfile.mkdtemp()
        out = cameras.scan_footage("basement", "02:00", "04:00")
        self.assertIn("queued work", out.lower())
        self.assertIn("cam-scan.basement", out)


class GuardsAreActuallyLoadBearing(unittest.TestCase):
    """Control class: break a guard, prove the suite would notice."""

    def setUp(self):
        self._get = cameras._get

    def tearDown(self):
        cameras._get = self._get

    def test_dog_widening_is_load_bearing(self):
        self.assertIn("dog", cameras.ANIMAL_LABELS)
        self.assertIn("cat", cameras.ANIMAL_LABELS)

    def test_an_empty_result_is_never_silent(self):
        cameras._get = FakeFrigate([])
        self.assertTrue(len(cameras.events()) > 40,
                        "empty result produced a terse answer a model could read as nothing-to-say")


class HumanFacingTimesAreCENTRAL(unittest.TestCase):
    """Human-facing timestamps must be America/Chicago, not UTC.

    `time.localtime()` reads the process timezone — correct in the container (TZ=America/Chicago) but
    wrong in any host-level script or one-off `docker run` (host is UTC). A defect that works until it
    is moved is worse than one that fails consistently.

    The fixture straddles the date boundary on purpose (02:00 UTC == 21:00 the previous day Central); a
    midday fixture passes against a UTC implementation and proves nothing."""

    # Computed, not hand-written: a hardcoded epoch is easy to get wrong, and the straddle control
    # below only catches it if the fixture genuinely crosses the date boundary, so it runs first.
    UTC_EVENING = 1786672800.0          # this epoch = 21:00 Central / 02:00 next-day UTC (straddles midnight)

    def test_the_fixture_actually_straddles_the_boundary(self):
        """Control first: if the two zones agreed here, everything below would be vacuous."""
        import datetime as _dt
        utc_date = _dt.datetime.fromtimestamp(self.UTC_EVENING, _dt.timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(utc_date, "2026-08-14")
        self.assertEqual(cameras.fmt_local(self.UTC_EVENING, "%Y-%m-%d"), "2026-08-13")

    def test_display_time_is_central(self):
        self.assertEqual(cameras.fmt_local(self.UTC_EVENING), "2026-08-13 21:00:00")

    def test_it_does_not_consult_the_PROCESS_timezone(self):
        """The property, not the output: a naive `fromtimestamp` would agree on a Central host and
        differ anywhere else, so assert the CALL passes a zone rather than assert a string."""
        calls = []
        real = cameras.datetime

        class SpyDatetime(real):
            @classmethod
            def fromtimestamp(cls, ts, tz=None):
                calls.append(tz)
                return real.fromtimestamp(ts, tz)

        cameras.datetime = SpyDatetime
        try:
            cameras.fmt_local(self.UTC_EVENING)
        finally:
            cameras.datetime = real
        self.assertTrue(calls, "the spy caught nothing - this test proved nothing")
        self.assertEqual([c for c in calls if c is None], [],
                         "a display path called fromtimestamp() with NO timezone")

    def test_the_scan_order_NAME_is_central_too(self):
        """The scan-order name is a user-facing surface (a filename read in a listing), so it is Central too."""
        import tempfile
        cameras.config.TABLET_INBOX_HOST = tempfile.mkdtemp()
        out = cameras.scan_footage("basement", "02:00", "04:00")
        self.assertIn("cam-scan.basement", out)


class NoEventBackendIsANNOUNCEDNotERRORED(unittest.TestCase):
    """With no event backend, questions must be ANNOUNCED as unavailable, not ERRORED.

    "There is no backend yet" and "the backend is broken" are different claims and the caller must be
    able to tell them apart — an error string invites debugging a backend that was removed on purpose.

    These tests do NOT stub `_get` — unlike every other class in this file, the behaviour under test
    IS `_get`'s short-circuit."""

    def setUp(self):
        self._backend = cameras.EVENT_BACKEND
        cameras.EVENT_BACKEND = None

    def tearDown(self):
        cameras.EVENT_BACKEND = self._backend

    def test_every_event_backed_answer_explains_itself(self):
        for name, out in (("events", cameras.events()),
                          ("last_seen", cameras.last_seen()),
                          ("status", cameras.status())):
            self.assertIn("mid-move", out, name)
            self.assertIn("Tolkien", out, name)
            self.assertIn("not a fault to chase", out, name)
            # The exact old failure mode, pinned by shape.
            self.assertNotIn("URLError", out, name)
            self.assertNotIn("No address associated", out, name)

    def test_it_does_not_even_attempt_the_network(self):
        """Short-circuiting BEFORE the call is the point -- a 12 s timeout per question on a name
        that cannot resolve is a cost paid for nothing."""
        called = []

        real = cameras.urllib.request.urlopen
        cameras.urllib.request.urlopen = lambda *a, **k: called.append(a) or real(*a, **k)
        try:
            cameras.events()
        finally:
            cameras.urllib.request.urlopen = real
        self.assertEqual(called, [], "a request was attempted with no backend configured")

    def test_the_still_working_path_is_not_swallowed(self):
        """`scan_footage` never used Frigate. If the no-backend guard were applied too broadly it
        would take out the ONE camera capability that still works, which would be a worse
        regression than the one it fixes."""
        self.assertIn("Unknown camera", cameras.scan_footage("garage", "02:00", "04:00"))
        self.assertIn("HH:MM", cameras.scan_footage("basement", "2am", "4am"))

    def test_CONTROL_the_guard_is_load_bearing(self):
        """A negative is worthless unless the harness can see the positive: with a backend set, the
        short-circuit must STOP firing — otherwise the assertions above would pass just as happily
        against a module that returns that sentence unconditionally.

        Proved by stubbing `urlopen`, not by letting real name resolution fail: a unit test must not
        buy its evidence with a network round trip."""
        cameras.EVENT_BACKEND = "probe"
        reached = []

        def boom(*a, **k):
            reached.append(a)
            raise urllib.error.URLError("stubbed - we only care that the call was ATTEMPTED")

        real = cameras.urllib.request.urlopen
        cameras.urllib.request.urlopen = boom
        try:
            ok, msg = cameras._get("/api/stats")
        finally:
            cameras.urllib.request.urlopen = real

        self.assertTrue(reached, "with a backend configured, _get must actually attempt the request")
        self.assertFalse(ok)
        self.assertNotIn("mid-move", msg,
                         "the no-backend sentence fires even WITH a backend -- the guard is not "
                         "keyed on what it claims to be keyed on")


if __name__ == "__main__":
    unittest.main(verbosity=2)
