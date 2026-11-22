"""Pins for library_wikilinks.py — the deterministic [[wikilink]] converter behind
library_apply_wikilinks. Ported from the proven W3 scripts; these lock the behaviour that produced
the 606-link/0-broken vault: each conversion form, every safety guard, idempotency, byte-safety.

All I/O is parameterized to a tmp docs dir (no container). Counts are predicted before asserting."""
import io
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library_wikilinks as W


def _mk(root, rel, text):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, "w", encoding="utf-8", newline="\n").write(text)


class WikilinkBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wl-test-")
        for sub in W.LINK_DIRS:
            os.makedirs(os.path.join(self.d, sub), exist_ok=True)
        _mk(self.d, "Reference/Target-Doc.md", "# Target Doc\n\n## Design Doctrine\n\nbody\n")
        _mk(self.d, "Reference/Target-Doc-Child.md", "# Child\n\n## Sub Heading\n\nbody\n")
        _mk(self.d, "Runbook/RUNBOOK-Elevation-and-Headless-Tasks.md", "# Elevation\n\n## Access\n\nx\n")
        _mk(self.d, "LLM-Project/12-FOO-PLAN.md", "# Foo Plan\n\nx\n")
        _mk(self.d, "Reference/Credentials/secret-credentials.md", "# Secret\n\nx\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _src(self, text, name="Reference/Source.md"):
        _mk(self.d, name, text)
        return W.plan_wikilinks(self.d)

    def _out(self, plan, name="Reference/Source.md"):
        return plan["files"].get(name, "")


class TestForms(WikilinkBase):
    def test_section_ref_exact_anchor(self):
        # anchor resolves only on a CLEAN tail (proven W3 rule: never eat trailing prose)
        p = self._src("See `Target-Doc.md` §Design Doctrine\n")
        self.assertIn("[[Target-Doc#Design Doctrine]]", self._out(p))
        self.assertNotIn("`Target-Doc.md`", self._out(p))

    def test_section_ref_page_pointer_when_heading_missing(self):
        p = self._src("See `Target-Doc.md` §Missing Section here.\n")
        out = self._out(p)
        self.assertIn("[[Target-Doc]] §Missing Section", out)
        self.assertIn("here.", out)

    def test_child_anchor_resolution(self):
        p = self._src("ref `Target-Doc.md` §Sub Heading\n")
        self.assertIn("[[Target-Doc-Child#Sub Heading]]", self._out(p))

    def test_bare_mention(self):
        p = self._src("The manual is `Target-Doc.md` for detail.\n")
        self.assertIn("[[Target-Doc]]", self._out(p))

    def test_markdown_link(self):
        p = self._src("[click here](Target-Doc.md)\n")
        self.assertIn("[[Target-Doc]]", self._out(p))

    def test_alias(self):
        p = self._src("See `elevation` §Access\n")
        self.assertIn("[[RUNBOOK-Elevation-and-Headless-Tasks#Access]]", self._out(p))

    def test_numeric_prefix_resolver(self):
        p = self._src("Plan is `FOO-PLAN.md` today.\n")
        self.assertIn("[[12-FOO-PLAN]]", self._out(p))


class TestGuards(WikilinkBase):
    def test_self_ref_left(self):
        p = self._src("see `Target-Doc.md` §Design Doctrine\n", name="Reference/Target-Doc.md")
        self.assertNotIn("Reference/Target-Doc.md", p["files"])
        self.assertTrue(any(r[3] == "self-ref" for r in p["left"]))

    def test_fenced_code_skipped(self):
        p = self._src("```\nsee `Target-Doc.md` for x\n```\n")
        self.assertEqual(p["files"].get("Reference/Source.md", ""), "")

    def test_unresolved_left_untouched(self):
        p = self._src("see `Nonexistent-Doc.md` here.\n")
        self.assertNotIn("Reference/Source.md", p["files"])

    def test_credentials_subdir_not_a_target(self):
        p = self._src("see `secret-credentials.md` for the key.\n")
        self.assertNotIn("Reference/Source.md", p["files"])

    def test_readme_denied(self):
        _mk(self.d, "Notes/README.md", "# Readme\n")
        p = self._src("see `README.md` here.\n")
        self.assertNotIn("Reference/Source.md", p["files"])

    def test_tracker_exempt_as_source(self):
        p = self._src("see `Target-Doc.md` §Design Doctrine\n", name="Reference/CURRENT-PROJECTS.md")
        self.assertNotIn("Reference/CURRENT-PROJECTS.md", p["files"])
        _mk(self.d, "Reference/CURRENT-PROJECTS.md", "# Tracker\n")
        p2 = self._src("see `CURRENT-PROJECTS.md` now.\n")
        self.assertIn("[[CURRENT-PROJECTS]]", self._out(p2))


class TestSafety(WikilinkBase):
    def test_idempotent_after_apply(self):
        _mk(self.d, "Reference/Source.md", "The manual `Target-Doc.md` and `elevation` §Access.\n")
        first = W.apply_wikilinks(self.d)
        self.assertTrue(first["applied"])
        self.assertTrue(len(first["files"]) >= 1)
        second = W.plan_wikilinks(self.d)
        self.assertEqual(second["files"], {})
        self.assertEqual(len(second["edits"]), 0)

    def test_apply_archives_touched_files(self):
        _mk(self.d, "Reference/Source.md", "manual `Target-Doc.md` here.\n")
        rep = W.apply_wikilinks(self.d)
        self.assertTrue(os.path.isdir(rep["archive_dir"]))
        self.assertTrue(os.path.isfile(os.path.join(rep["archive_dir"], "Source.md")))

    def test_byte_safety_surrounding_text_preserved(self):
        _mk(self.d, "Reference/Source.md", "PRE `Target-Doc.md` MID text POST.\n")
        W.apply_wikilinks(self.d)
        out = io.open(os.path.join(self.d, "Reference", "Source.md"), encoding="utf-8").read()
        self.assertEqual(out, "PRE [[Target-Doc]] MID text POST.\n")

    def test_verify_clean_on_good_vault(self):
        _mk(self.d, "Reference/Source.md", "manual `Target-Doc.md` §Design Doctrine here.\n")
        W.apply_wikilinks(self.d)
        v = W.verify_wikilinks(self.d)
        self.assertEqual(len(v["missing_file"]), 0)
        self.assertEqual(len(v["missing_anchor"]), 0)
        self.assertEqual(len(v["odd_backtick"]), 0)


if __name__ == "__main__":
    unittest.main()
