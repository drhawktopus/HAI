"""
Tests that `get_document` tool description includes HOMELAB handoff surface.

The tool description is part of the contract read by the LLM. It must explicitly
guide the model to use this tool for status/handoff queries (e.g., "CURRENT-PROJECTS.md"),
not just study materials like drafts or syllabi.
"""
import unittest

import tools


def _desc(schema):
    return schema["function"]["description"]


class GetDocumentAdvertisesTheHandoffSurface(unittest.TestCase):
    def setUp(self):
        self.d = _desc(tools._GET_DOCUMENT)
        self.low = self.d.lower()

    def test_names_the_handoff_documents_by_filename(self):
        for doc in ("current-projects", "journal", "project-backlog"):
            self.assertIn(doc, self.low, f"{doc} is not named, so nothing routes a status question here")

    def test_carries_the_phrases_a_person_actually_types(self):
        """The routing lever. These are the words from the real 08-11 exchange and its neighbours."""
        for phrase in ("what did claude do", "where were we", "what's the status",
                       "what happened", "catch me up", "pick up where we left off", "handoff"):
            self.assertIn(phrase, self.low, f"missing trigger phrase: {phrase!r}")

    def test_still_serves_the_STUDY_audience(self):
        """Shared across personas — the nursing/student use must not be displaced by the homelab one."""
        for word in ("draft", "assignment", "syllabus", "review", "summarize"):
            self.assertIn(word, self.low, f"study-material use lost the word {word!r}")

    def test_states_the_NEWEST_FIRST_read_rule(self):
        """CURRENT-PROJECTS is additive; a later block supersedes an earlier one. A reader that does
        not know that can quote a superseded block back as current — measured this session: HAI
        answers from seed-grounding, which has no recency weighting at all."""
        self.assertIn("newest-first", self.low)
        self.assertIn("supersedes", self.low)

    def test_warns_that_a_TRUNCATED_read_is_partial(self):
        """`GET_DOC_MAX_CHARS` is 12000 and CURRENT-PROJECTS.md is >800KB, so a read of it is ALWAYS
        partial. Without this the model cannot tell a quiet truncation from a complete file."""
        self.assertIn("partial page", self.low)      # a long document comes in pages, each partial page says so
        self.assertIn("partial", self.low)

    def test_tells_the_model_the_information_EXISTS(self):
        """The 08-11 failure mode was 'I don't have a record', said about material that was indexed."""
        self.assertIn("rather than saying you have no record", self.low)


class SchemaStillValid(unittest.TestCase):
    def test_shape_is_unchanged(self):
        fn = tools._GET_DOCUMENT["function"]
        self.assertEqual(fn["name"], "get_document")
        self.assertEqual(fn["parameters"]["required"], ["filename"])
        self.assertIn("filename", fn["parameters"]["properties"])

    def test_still_registered_on_both_tiers(self):
        """A tool in neither list is invisible; in one, invisible on the other tier."""
        self.assertIn(tools._GET_DOCUMENT, tools.BRAIN_TOOLS)
        self.assertIn(tools._GET_DOCUMENT, tools.HEAVY_TOOLS)

    def test_description_stays_within_a_sane_budget(self):
        """Descriptions are paid for on every single call, on both tiers. Kept deliberately bounded —
        the 8B's context is the documented scarce resource here."""
        self.assertLess(len(_desc(tools._GET_DOCUMENT)), 1600)


if __name__ == "__main__":
    unittest.main()
