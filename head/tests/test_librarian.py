"""The librarian reflex (librarian.py) — HAI's two purpose-built library writers (10d Part 2).

Structural guarantees under pin: the model supplies CONTENT ONLY — path, filename, header, tag,
date and position are code-shaped · journal is OWN-FILE and date-named (discoverable by
catch_up's filename parser) · the CP block prepends directly under the H1 (newest-first-at-TOP
law) and never restructures the rest · every write is byte-verified and every failure RAISES
(the loud-instrument rule) · claim_sweep enumerates Journal/*-hai.md and nothing else from
Journal (ruling #3: "proofreading is good").
"""
import datetime
import os
import shutil
import tempfile
import unittest
from unittest import mock

import config
import librarian


def _now(h=14, m=30):
    from zoneinfo import ZoneInfo
    return datetime.datetime(2026, 8, 15, h, m, tzinfo=config.LAB_ZONE)


class JournalAppend(unittest.TestCase):
    def setUp(self):
        self.docs = tempfile.mkdtemp(prefix="librarian-test-")
        os.makedirs(os.path.join(self.docs, "Journal"))
        self._p = mock.patch.object(config, "DOCS_DIR", self.docs)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.docs, ignore_errors=True)

    def test_first_write_creates_the_dated_own_file_with_header(self):
        out = librarian.journal_append("Supervised the night render order to completion.",
                                       now=_now())
        self.assertTrue(out["created"])
        self.assertEqual(os.path.basename(out["path"]), "2026-08-15-hai.md",
                         "date-named so catch_up's filename parser discovers it immediately")
        with open(out["path"], encoding="utf-8") as fh:
            body = fh.read()
        self.assertTrue(body.startswith("# 2026-08-15 — HAI work journal"))
        self.assertIn("## 14:30 CT", body)
        self.assertIn("Supervised the night render order", body)

    def test_second_write_appends_a_new_timestamped_section(self):
        librarian.journal_append("first", now=_now(9, 0))
        out = librarian.journal_append("second entry", now=_now(21, 5))
        self.assertFalse(out["created"])
        with open(out["path"], encoding="utf-8") as fh:
            body = fh.read()
        self.assertEqual(body.count("# 2026-08-15 — HAI work journal"), 1)
        self.assertIn("## 09:00 CT", body)
        self.assertIn("## 21:05 CT", body)
        self.assertLess(body.index("first"), body.index("second entry"))

    def test_empty_entry_raises_loudly(self):
        with self.assertRaises(ValueError):
            librarian.journal_append("   ")

    def test_the_model_never_chooses_the_path(self):
        """No path parameter exists at all — the whitelist-is-the-ceiling rule for file writes."""
        import inspect
        params = inspect.signature(librarian.journal_append).parameters
        self.assertEqual(set(params), {"text", "now"})


class HandoffNote(unittest.TestCase):
    def setUp(self):
        self.docs = tempfile.mkdtemp(prefix="librarian-cp-")
        os.makedirs(os.path.join(self.docs, "Reference"))
        self.cp = os.path.join(self.docs, "Reference", "CURRENT-PROJECTS.md")
        with open(self.cp, "w", encoding="utf-8") as fh:
            fh.write("# Shared Current Projects\n\n"
                     "> ## [OLDER BLOCK] yesterday\n>\n> existing content stays put\n\n"
                     "## A section body\n\ntext\n")
        self._p = mock.patch.object(config, "DOCS_DIR", self.docs)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.docs, ignore_errors=True)

    def _read(self):
        with open(self.cp, encoding="utf-8") as fh:
            return fh.read()

    def test_block_lands_directly_under_the_h1_above_older_blocks(self):
        librarian.handoff_note("Night order shipped", ["result: 40 renders, all green",
                                                      "next: Alex's eye on the picks"],
                              author="HAI", now=_now())
        body = self._read()
        h1 = body.index("# Shared Current Projects")
        mine = body.index("[HAI SESSION] 2026-08-15 14:30 CT")
        older = body.index("[OLDER BLOCK]")
        self.assertTrue(h1 < mine < older, "newest-first-at-TOP: new block above the older one")
        self.assertIn("> ## 🤖 [HAI SESSION] 2026-08-15 14:30 CT — Night order shipped", body)
        self.assertIn("> result: 40 renders, all green", body)
        self.assertIn("existing content stays put", body, "append-only: never restructures")
        self.assertIn("## A section body", body)

    def test_fixed_format_is_code_generated_not_model_chosen(self):
        """A model-supplied line that LOOKS like a header still lands as a content bullet inside
        the blockquote — the retrieval-facing surface is never the model's to shape."""
        librarian.handoff_note("x", ["# I am pretending to be a title"], now=_now())
        body = self._read()
        self.assertIn("> # I am pretending to be a title", body)
        self.assertFalse(any(ln.startswith("# I am pretending")
                             for ln in body.splitlines()),
                         "the fake H1 must stay quoted, never a real heading")

    def test_missing_h1_refuses_rather_than_guessing_a_position(self):
        with open(self.cp, "w", encoding="utf-8") as fh:
            fh.write("no heading here\n")
        with self.assertRaises(ValueError):
            librarian.handoff_note("x", ["y"], now=_now())

    def test_headline_and_lines_are_required(self):
        with self.assertRaises(ValueError):
            librarian.handoff_note("", ["y"], now=_now())
        with self.assertRaises(ValueError):
            librarian.handoff_note("x", [], now=_now())


class ToolWrappers(unittest.TestCase):

    def setUp(self):
        self.docs = tempfile.mkdtemp(prefix="librarian-wrap-")
        os.makedirs(os.path.join(self.docs, "Journal"))
        os.makedirs(os.path.join(self.docs, "Reference"))
        with open(os.path.join(self.docs, "Reference", "CURRENT-PROJECTS.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# Shared Current Projects\n\nbody\n")
        self._p = mock.patch.object(config, "DOCS_DIR", self.docs)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.docs, ignore_errors=True)

    def test_journal_wrapper_success_message_survives_a_real_call(self):
        import tools
        out = tools._journal_append_tool({"entry": "wrapper pin entry"}, "Alex")
        self.assertIn("Recorded in", out)
        self.assertIn("-hai.md", out)

    def test_handoff_wrapper_success_message_survives_a_real_call(self):
        import tools
        out = tools._handoff_note_tool({"headline": "wrapper pin",
                                        "lines": ["one line"]}, "Alex")
        self.assertIn("prepended", out)

    def test_wrapper_failure_is_a_returned_report_never_an_exception(self):
        import tools
        out = tools._journal_append_tool({"entry": "   "}, "Alex")
        self.assertIn("FAILED", out)


class MachineUserCodingState(unittest.TestCase):
    """Verify that MachineUser supervised-job packets include the entire coding toolset.

    Historical context: a selector bug previously dropped 'coding_create', causing false
    'capability missing' errors despite correct dispatch. This test ensures all coding
    tools are present in live state to prevent regression."""

    def test_machine_user_state_includes_every_coding_tool(self):
        import tools
        live = tools.live_state_tools("ClaudeSupervisor")
        coding = {t["function"]["name"] for t in tools.CODING_TOOLS}
        self.assertTrue(coding <= live, "missing: %s" % sorted(coding - live))
        self.assertIn("coding_create", live)

    def test_ordinary_users_do_not_inherit_the_coding_state(self):
        import tools
        self.assertNotIn("coding_create", tools.live_state_tools("Riley"))


class ClaimSweepCoverage(unittest.TestCase):
    def test_sweep_enumerates_hai_journals_and_only_hai_journals(self):
        """Ruling #3: claim_review watches Journal/*-hai.md from day one — and must NOT start
        sweeping every session journal (Journal/ is otherwise out of its scope)."""
        import claim_sweep
        docs = tempfile.mkdtemp(prefix="sweep-test-")
        try:
            jdir = os.path.join(docs, "Journal")
            os.makedirs(jdir)
            for name in ("2026-08-15-hai.md", "2026-08-15-claude.md", "notes.txt"):
                with open(os.path.join(jdir, name), "w", encoding="utf-8") as fh:
                    fh.write("x\n")
            snap = tempfile.mkdtemp(prefix="sweep-snap-")
            with mock.patch.object(config, "DOCS_DIR", docs), \
                 mock.patch.object(claim_sweep, "DIRS", []), \
                 mock.patch.object(claim_sweep, "SNAP_DIR", os.path.join(snap, "s")), \
                 mock.patch.object(claim_sweep, "FINDINGS_DIR", os.path.join(snap, "f")), \
                 mock.patch.object(claim_sweep, "notify_desk", lambda m: None):
                out = claim_sweep.sweep(dry_run=True)
            # First run is a baseline: it snapshots what it ENUMERATED. Exactly the -hai file.
            self.assertEqual(out.get("snapshotted"), 1)
            shutil.rmtree(snap, ignore_errors=True)
        finally:
            shutil.rmtree(docs, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
