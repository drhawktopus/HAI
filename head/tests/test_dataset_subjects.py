"""Subjects are visually verified, never inferred from captions to avoid substring ambiguities.

Tests redirect DATASET_FLAGS_FILE to a temp directory; the real file contains hand-made
curation data and must not be modified by tests."""
import json
import os
import shutil
import tempfile
import unittest

import tools


class SubjectFlagTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = tools.DATASET_FLAGS_FILE
        tools.DATASET_FLAGS_FILE = os.path.join(self._tmp, "flags.json")

    def tearDown(self):
        tools.DATASET_FLAGS_FILE = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_subject_is_a_separate_axis_from_the_verdict(self):
        """Tagging a subject must not disturb keep/no/maybe — they answer different questions."""
        tools.set_dataset_flag("Alex", "met_form_2691.jpg", "keep")
        ok, counts = tools.set_dataset_flag("Alex", "met_form_2691.jpg", "subject-female")
        self.assertTrue(ok)
        self.assertEqual(counts["female"], 1)
        self.assertEqual(tools.dataset_flags("Alex").get("met_form_2691.jpg"), "keep")
        self.assertEqual(tools.dataset_subject_flags("Alex").get("met_form_2691.jpg"), "female")

    def test_stored_under_its_own_key(self):
        tools.set_dataset_flag("Alex", "a.jpg", "subject-male")
        with open(tools.DATASET_FLAGS_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertIn("Alex::subjects", raw)
        self.assertNotIn("a.jpg", raw.get("Alex", {}),
                         "a subject tag leaked into the pick verdicts")

    def test_clearing(self):
        tools.set_dataset_flag("Alex", "a.jpg", "subject-both")
        tools.set_dataset_flag("Alex", "a.jpg", "subject-clear")
        self.assertNotIn("a.jpg", tools.dataset_subject_flags("Alex"))

    def test_unknown_subject_is_refused_with_the_real_options(self):
        ok, msg = tools.set_dataset_flag("Alex", "a.jpg", "subject-woman")
        self.assertFalse(ok)
        self.assertIn("female", msg)
        self.assertEqual(tools.dataset_subject_flags("Alex"), {})

    def test_admin_only(self):
        ok, msg = tools.set_dataset_flag("Jordan", "a.jpg", "subject-female")
        self.assertFalse(ok)
        self.assertEqual(tools.dataset_subject_flags("Jordan"), {})

    def test_existing_verdict_values_still_work(self):
        """The new branch must not shadow keep/no/maybe or the caption axis."""
        for flag in ("keep", "no", "maybe", "caption-bad"):
            ok, _ = tools.set_dataset_flag("Alex", "b.jpg", flag)
            self.assertTrue(ok, "%s stopped working" % flag)


class ResumeReviewTests(unittest.TestCase):
    """Resuming an unfinished review must restore the gallery from server-side verdicts -
    no client state required, so it works from a different device."""

    def test_gallery_endpoint_exists_and_is_wired(self):
        import app
        self.assertTrue(any(getattr(r, "path", "") == "/dataset/gallery"
                            for r in app.app.routes),
                        "no GET /dataset/gallery route — the page cannot rebuild the gallery")

    def test_the_review_does_not_pop_up_on_page_load(self):
        """The review does not pop up on page load; the gallery opens only when the user asks for candidates."""
        import app
        self.assertNotIn("resumeReview", app.CHAT_HTML)
        self.assertNotIn("Picking up your review", app.CHAT_HTML)
        self.assertIn("gallery(el, d.gallery)", app.CHAT_HTML, "the asked-for gallery still renders")

    def test_gallery_opens_on_the_first_undecided_item(self):
        import app
        self.assertIn("findIndex", app.CHAT_HTML)
        self.assertIn("subjPass ? !x.subject : !x.flag", app.CHAT_HTML,
                      "the resume point must depend on WHICH pass this is")


class FinishedBatchTests(unittest.TestCase):
    """Paging a batch that is fully judged must SAY so rather than silently re-serving it."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._of = tools.DATASET_FLAGS_FILE
        self._om = tools.DATASET_CANDIDATES_FILE
        self._od = tools.config.DOCS_DIR
        self._os = tools.config.STATE_DIR
        tools.DATASET_FLAGS_FILE = os.path.join(self._tmp, "flags.json")
        tools.DATASET_CANDIDATES_FILE = os.path.join(self._tmp, "man.json")
        # The lister refuses to name an image that is not on disk (anti-hallucination guard), so a
        # manifest without real files exercises that path instead of the one under test.
        tools.config.DOCS_DIR = self._tmp
        tools.config.STATE_DIR = os.path.join(self._tmp, "State")
        os.makedirs(os.path.join(self._tmp, "State", "images", "Alex"), exist_ok=True)

    def tearDown(self):
        tools.DATASET_FLAGS_FILE = self._of
        tools.DATASET_CANDIDATES_FILE = self._om
        tools.config.DOCS_DIR = self._od
        tools.config.STATE_DIR = self._os
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _manifest(self, files):
        with open(tools.DATASET_CANDIDATES_FILE, "w", encoding="utf-8") as fh:
            json.dump({"generated": "now", "min_score": 4,
                       "candidates": [{"source_file": f, "image": "cand_%03d.png" % (i + 1),
                                       "set": "s", "keep_score": 5, "user": "Alex"}
                                      for i, f in enumerate(files)]}, fh)
        for i in range(len(files)):
            open(os.path.join(self._tmp, "State", "images", "Alex",
                              "cand_%03d.png" % (i + 1)), "wb").close()

    def test_all_judged_batch_tells_the_model_to_queue_a_new_one(self):
        self._manifest(["a.jpg", "b.jpg"])
        for f in ("a.jpg", "b.jpg"):
            tools.set_dataset_flag("Alex", f, "keep")
        out = tools._dataset_candidates("Alex", {"limit": 2})
        self.assertIn("already been judged", out)
        self.assertIn("action='next'", out)

    def test_unjudged_batch_says_nothing_extra(self):
        self._manifest(["a.jpg", "b.jpg"])
        tools.set_dataset_flag("Alex", "a.jpg", "keep")     # only one of the two
        out = tools._dataset_candidates("Alex", {"limit": 2})
        self.assertNotIn("already been judged", out)


class SourceGroundingTests(unittest.TestCase):
    """The `source` param requires a live enum to prevent invalid values,
    not just descriptive advice. Prose discourages incorrect usage; an enum
    makes it unrepresentable."""

    def setUp(self):
        self._orig_reg = tools._dataset_registry
        self._orig_sets = tools.existing_set_names
        tools._dataset_registry = lambda force=False: {"pd12m": {}, "met": {}, "met_bulk": {}}
        tools.existing_set_names = lambda: ["met-form-v2"]

    def tearDown(self):
        tools._dataset_registry = self._orig_reg
        tools.existing_set_names = self._orig_sets

    def _acquire_schema(self):
        box = tools.tools_for(tools.BRAIN_TOOLS, "Alex")
        return [t for t in box if t["function"]["name"] == "dataset_acquire"][0]

    def test_source_enum_is_injected_from_the_live_registry(self):
        props = self._acquire_schema()["function"]["parameters"]["properties"]
        self.assertEqual(sorted(props["source"].get("enum") or []), ["met", "met_bulk", "pd12m"])

    def test_grounding_does_not_mutate_the_module_level_schema(self):
        """A leaked enum would freeze one request's registry into every later one."""
        self._acquire_schema()
        raw = [t for t in tools.BRAIN_TOOLS if t["function"]["name"] == "dataset_acquire"][0]
        self.assertNotIn("enum", raw["function"]["parameters"]["properties"]["source"])

    def test_no_registry_means_no_enum_rather_than_an_empty_one(self):
        """An empty enum would make EVERY source unrepresentable — worse than no grounding."""
        tools._dataset_registry = lambda force=False: {}
        props = self._acquire_schema()["function"]["parameters"]["properties"]
        self.assertNotIn("enum", props["source"])


class RecaptionModeTests(unittest.TestCase):

    def test_recaption_is_an_accepted_mode(self):
        out = tools._dataset_acquire("Alex", {"name": "x", "mode": "recaptionz"})
        self.assertIn("recaption", out, "recaption missing from the accepted-mode list")

    def test_recaption_without_a_folder_refuses_and_starts_nothing(self):
        out = tools._dataset_acquire("Alex", {"name": "x", "mode": "recaption"})
        self.assertIn("train_dir", out)
        self.assertIn("Nothing was started", out)

    def test_schema_exposes_the_recaption_parameters(self):
        t = [x for x in tools.BRAIN_TOOLS if x["function"]["name"] == "dataset_acquire"][0]
        props = t["function"]["parameters"]["properties"]
        self.assertIn("train_dir", props)
        self.assertIn("min_confidence", props)
        self.assertIn("recaption", props["mode"]["description"],
                      "the model is never told the mode exists")


class GridModeTests(unittest.TestCase):

    def test_grid_is_wired(self):
        import app
        for probe in ("gal-grid", "renderGrid", "setMode", "gal-tile"):
            self.assertIn(probe, app.CHAT_HTML, "grid mode missing %s" % probe)

    def test_mode_toggle_lives_in_the_head_not_the_undo_row(self):
        """The undo row is hidden until there is something to undo; a mode switch parked there would
        be invisible until the user happened to act."""
        import app
        # Anchor on where the head is BUILT, not on the CSS rule of the same name.
        head = app.CHAT_HTML[app.CHAT_HTML.index("head.className='gal-head'"):]
        self.assertIn("head.appendChild(modeBtn)", head[:1200],
                      "the grid toggle must be appended to the always-visible head")

    def test_tally_is_callable_outside_paint(self):
        """Grid taps must update the running total without repainting an image not on screen."""
        import app
        self.assertIn("function updateTally()", app.CHAT_HTML)
        paint = app.CHAT_HTML[app.CHAT_HTML.index("function paint()"):]
        self.assertNotIn("tally.textContent", paint[:900],
                         "paint() should call updateTally(), not inline the tally again")


class GalleryUiTests(unittest.TestCase):
    """Tests that the Gallery UI HTML is valid and includes expected subject controls."""

    def test_subject_buttons_are_wired(self):
        import app
        for probe in ("dataset.subj", "subject-clear", "it.subject"):
            self.assertIn(probe, app.CHAT_HTML, "gallery is missing %s" % probe)

    def test_no_eaten_escapes_in_the_rendered_ui(self):
        import app
        for ch in ("\x08", "\x0c", "\x07", "\x0b"):
            self.assertNotIn(ch, app.CHAT_HTML,
                             "a backslash escape was eaten by Python: %r" % ch)


if __name__ == "__main__":
    unittest.main()
