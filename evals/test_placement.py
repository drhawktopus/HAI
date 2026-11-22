"""Tests for the harness's placement/identity assertions. (#12 Phase G, Phase 1)

These are pure functions — no LLM call, no fleet call — so they run under the current no-measurement
constraint. They exist because every instrument I trusted without testing this shift returned a
well-formed answer about the wrong thing.

The property that matters is REFUSAL: a mismatch must stop the row being recorded, not decorate it.
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import pathlib
import tempfile
import unittest

import placement


class AssertPlacement(unittest.TestCase):
    def test_heavy_pin_served_on_quick_tier_REFUSES(self):
        """The exact defect that voided the night: a 27B-cut pin replayed on the 9B."""
        ok, why = placement.assert_placement("heavy", {"route": "local", "model": "qwen3.5:9b"})
        self.assertFalse(ok)
        self.assertIn("different system", why.lower())

    def test_heavy_pin_served_on_heavy_tier_RECORDS(self):
        ok, _ = placement.assert_placement("heavy", {"route": "heavyweight",
                                                     "model": "qwen3.6:27b"})
        self.assertTrue(ok)

    def test_an_ERROR_route_is_refused_not_graded(self):
        """route=error carries no tier — nothing was measured, so nothing may be recorded."""
        ok, why = placement.assert_placement("heavy", {"route": "error"})
        self.assertFalse(ok)
        self.assertIn("nothing was measured", why)

    def test_a_DIRECT_route_is_refused(self):
        """dataset-direct served a real answer with NO model — the incident route."""
        ok, why = placement.assert_placement(None, {"route": "dataset-direct"})
        self.assertFalse(ok)

    def test_tier_agnostic_pin_still_refuses_a_tierless_route(self):
        ok, _ = placement.assert_placement(None, {"route": "local", "model": "qwen3.5:9b"})
        self.assertTrue(ok, "a tier-agnostic pin on a real tier is fine")


class VoidOnDegradedContext(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self._orig = placement.NOTES_DIR
        placement.NOTES_DIR = self.dir

    def tearDown(self):
        placement.NOTES_DIR = self._orig

    def _log(self, text):
        import datetime
        day = datetime.datetime.now().strftime("%Y-%m-%d")
        (self.dir / f"{day}-orchestrator.md").write_text(text, encoding="utf-8")

    def test_a_downgrade_VOIDS_the_row(self):
        self._log("CTX DOWNGRADE host=h model=m step=1 attempt=2 requested=65536 using=32768 "
                  "after='boom' - treat any measurement taken from it as VOID\n")
        verdict, why = placement.void_if_degraded(0)
        self.assertEqual(verdict, "VOID_DEGRADED_CTX")
        self.assertIn("65536", why)
        self.assertIn("32768", why)

    def test_a_clean_log_does_NOT_void(self):
        """POSITIVE CONTROL: if this ever fails open, every row would be voided and the harness
        would look 'safe' while measuring nothing."""
        self._log("some other line\n")
        verdict, _ = placement.void_if_degraded(0)
        self.assertIsNone(verdict)

    def test_a_MISSING_log_is_UNKNOWN_not_clean(self):
        """'Could not measure' must never render as 'did not happen' — the rule this lane keeps
        re-learning. A missing work log means we cannot say the context was full."""
        placement.NOTES_DIR = self.dir / "nope"
        verdict, why = placement.void_if_degraded(0)
        self.assertEqual(verdict, "CTX_UNKNOWN")
        self.assertIn("COULD NOT MEASURE", why)


if __name__ == "__main__":
    unittest.main()
