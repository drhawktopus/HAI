"""A rubric reaches only the turn about its assignment; the notes header names where the notes came from."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
import ingest  # noqa: E402

RUBRIC = "> " + ingest.INTENTS["rubric"][1]
GENERAL = "> " + ingest.INTENTS["general"][1]


class RubricBindsOnlyItsAssignment(unittest.TestCase):
    def test_a_rubric_chunk_is_told_by_its_stored_note(self):
        self.assertTrue(app._rubric_chunk({"text": "# Week 3 lab\n\n" + RUBRIC + "\n\nSteps"}))
        self.assertFalse(app._rubric_chunk({"text": "# A resume\n\n" + GENERAL + "\n\nSkills"}))
        self.assertFalse(app._rubric_chunk({"text": ""}))

    def test_a_turn_about_something_else_does_not_get_the_rubric(self):
        self.assertFalse(app._turn_names_doc("week3-sql-lab-rubric.md", "explain this SQL schema to me", []))
        self.assertFalse(app._turn_names_doc("week3-sql-lab-rubric.md", "what is a pandas DataFrame", []))

    def test_a_turn_that_names_or_pins_the_assignment_gets_it(self):
        self.assertTrue(app._turn_names_doc("week3-sql-lab-rubric.md", "grade my week3 lab against the rubric", []))
        self.assertTrue(app._turn_names_doc("week3-sql-lab-rubric.md", "explain this SQL schema", ["week3-sql-lab-rubric.md"]))

    def test_short_or_generic_filename_words_do_not_count(self):
        # "final", "docx" and short tokens are not names: a turn saying "final" is not about this document
        self.assertFalse(app._turn_names_doc("x-final.docx.md", "is this the final version", []))


class NotesHeaderNamesTheOrigin(unittest.TestCase):
    def test_homelab_notes_keep_their_name(self):
        self.assertEqual(app._notes_origin([{"ns": "homelab"}], "alex"), "the homelab documentation")
        self.assertEqual(app._notes_origin([], "casey"), "the homelab documentation")

    def test_a_users_own_library_is_named_as_theirs(self):
        self.assertEqual(app._notes_origin([{"ns": "casey"}, {"ns": "casey"}], "casey"), "your own library")

    def test_mixed_origins_say_so(self):
        self.assertEqual(app._notes_origin([{"ns": "casey"}, {"ns": "law"}], "casey"), "your library and the shared reference notes")


if __name__ == "__main__":
    unittest.main()
