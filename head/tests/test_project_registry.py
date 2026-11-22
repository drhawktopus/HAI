"""Tests for project_registry.load_registry().

All value assertions use an embedded fixture written at test time — never the live
PROJECT-REGISTRY.md — so a real registry edit (e.g. adding #36) can never break a pin.
Every test is deterministic (no env-dependent skips) to keep the suite count stable.
"""
import os
import shutil
import tempfile
import unittest

import project_registry as pr

# em-dash (U+2014) is the suffix/charter separator; #30 carries one INSIDE its name.
FIXTURE = (
    "# PROJECT REGISTRY\n\n"
    "| # | project | state |\n"
    "|---|---|---|\n"
    "| #10 | Per-user RAG namespaces / nursing tutor | SHIPPED |\n"
    "| #12 | HAI Self-Engineering Agent — [[06-SE-PLAN]] — CAMPAIGN | SHIPPED (Phases 1-5; pipeline live) |\n"
    "| #19 | Image generation — identity & family portraits — [[11-IMG-PLAN]] | **CONCLUDED** |\n"
    "| #30 | **Library re-do — knowledge base, not a pile** — CAMPAIGN | **CONCLUDED (2026-08-26)** — [[30-CONCLUSION]] |\n\n"
    "**Next free: #36.**\n\n"
    "## Campaigns\n\n"
    "| campaign | member/child work | charter |\n"
    "|---|---|---|\n"
    "| #12 HAI Self-Engineering | Phases 1-5 shipped | [[06-SE-PLAN]] |\n\n"
    "## Epics\n\n"
    "| epic | the thread | members |\n"
    "|---|---|---|\n"
    "| **Delegation** | work moves to the lab's machinery | #12 → #34 → #35 |\n"
)


class TestProjectRegistry(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "Reference"))
        with open(os.path.join(self.dir, "Reference", "PROJECT-REGISTRY.md"), "w",
                  encoding="utf-8") as fh:
            fh.write(FIXTURE)
        self.r = pr.load_registry(docs_dir=self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _proj(self, num):
        return next(p for p in self.r["projects"] if p["num"] == num)

    def test_no_error_and_counts(self):
        self.assertNotIn("error", self.r)
        self.assertEqual(len(self.r["projects"]), 4)
        self.assertEqual(len(self.r["campaigns"]), 1)
        self.assertEqual(len(self.r["epics"]), 1)

    def test_plain_project(self):
        p = self._proj(10)
        self.assertEqual(p["name"], "Per-user RAG namespaces / nursing tutor")
        self.assertEqual(p["state"], "SHIPPED")
        self.assertEqual(p["state_detail"], "")
        self.assertFalse(p["is_campaign"])
        self.assertFalse(p["is_epic"])
        self.assertEqual(p["charter"], "")

    def test_campaign_flag_charter_and_detail(self):
        p = self._proj(12)
        self.assertEqual(p["name"], "HAI Self-Engineering Agent")
        self.assertTrue(p["is_campaign"])
        self.assertEqual(p["charter"], "06-SE-PLAN")
        self.assertEqual(p["state"], "SHIPPED")
        self.assertEqual(p["state_detail"], "Phases 1-5; pipeline live")

    def test_bold_state_and_charter(self):
        p = self._proj(19)
        self.assertEqual(p["name"], "Image generation — identity & family portraits")
        self.assertEqual(p["state"], "CONCLUDED")
        self.assertEqual(p["state_detail"], "")
        self.assertEqual(p["charter"], "11-IMG-PLAN")

    def test_internal_emdash_name_bold_and_col3_charter(self):
        # The hard case: name contains an internal em-dash, cell is bold, and the
        # charter wikilink lives in the STATE column, not the project column.
        p = self._proj(30)
        self.assertEqual(p["name"], "Library re-do — knowledge base, not a pile")
        self.assertTrue(p["is_campaign"])
        self.assertEqual(p["state"], "CONCLUDED")
        self.assertEqual(p["state_detail"], "2026-08-26")
        self.assertEqual(p["charter"], "30-CONCLUSION")

    def test_next_free(self):
        self.assertEqual(self.r["next_free"], 36)

    def test_campaign_row(self):
        c = self.r["campaigns"][0]
        self.assertEqual(c["num"], 12)
        self.assertEqual(c["name"], "HAI Self-Engineering")
        self.assertEqual(c["members"], "Phases 1-5 shipped")
        self.assertEqual(c["charter"], "06-SE-PLAN")

    def test_epic_row(self):
        e = self.r["epics"][0]
        self.assertEqual(e["name"], "Delegation")
        self.assertEqual(e["thread"], "work moves to the lab's machinery")
        self.assertEqual(e["members"], ["#12", "#34", "#35"])

    def test_source_field(self):
        self.assertEqual(self.r["source"], os.path.join("Reference", "PROJECT-REGISTRY.md"))

    def test_fail_soft_missing_dir(self):
        r = pr.load_registry(docs_dir=os.path.join(self.dir, "does-not-exist"))
        self.assertIn("error", r)
        self.assertEqual(r["projects"], [])
        self.assertEqual(r["campaigns"], [])
        self.assertEqual(r["epics"], [])
        self.assertIsNone(r["next_free"])


if __name__ == "__main__":
    unittest.main()
