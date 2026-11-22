"""Behavioral pins for `_derive_run_title`. The machine ID is the stable key; the title is human-only."""
import unittest

try:
    import app
    _APP = True
except Exception:
    _APP = False


@unittest.skipUnless(_APP, "app not importable in this environment")
class DeriveRunTitleTests(unittest.TestCase):
    def test_first_meaningful_line_becomes_the_title(self):
        t = app._derive_run_title("Review the native-history findings corpus", "abr-x")
        self.assertEqual(t, "Review the native-history findings corpus")

    def test_skips_a_mode_header(self):
        # a decomposed packet leads with "Mode: ..." — the title must not be that header.
        t = app._derive_run_title("Mode: decomposed edit-test-review\nRewire the camera pool",
                                  "abr-x")
        self.assertEqual(t, "Rewire the camera pool")

    def test_prefers_an_objective_line(self):
        t = app._derive_run_title("Mode: decomposed\nSTATE: leaf 1 landed\n"
                                  "Objective: add human-legible order titles\nmore text",
                                  "abr-x")
        self.assertEqual(t, "add human-legible order titles")

    def test_blank_text_falls_back_to_the_id(self):
        self.assertEqual(app._derive_run_title("", "abr-1787891440-ea1708"),
                         "abr-1787891440-ea1708")
        self.assertEqual(app._derive_run_title("   \n  \n", "abr-x"), "abr-x")

    def test_title_is_sanitized_and_capped(self):
        t = app._derive_run_title('"Padded title with quotes."', "abr-x")
        self.assertEqual(t, "Padded title with quotes")
        long = "x" * 200
        self.assertLessEqual(len(app._derive_run_title(long, "abr-x")), 60)


if __name__ == "__main__":
    unittest.main()
