"""Test catch_up error reporting.

Tests assert on specific error messages rather than just non-crash status,
ensuring failure modes are distinct and identifiable.
Runs in isolation: temp dir, no network, no model."""
import datetime
import pathlib
import tempfile
import unittest

import catch_up
import config


def _day(offset):
    return (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()


class CatchUpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        (root / "Reference").mkdir()
        (root / "Journal").mkdir()
        self._orig_docs = config.DOCS_DIR
        self._orig_fetch = getattr(config, "FETCH_ONLY_DIRS", {})
        config.DOCS_DIR = str(root)
        config.FETCH_ONLY_DIRS = {"homelab": [str(root / "Journal")]}
        self.root = root

    def tearDown(self):
        config.DOCS_DIR = self._orig_docs
        config.FETCH_ONLY_DIRS = self._orig_fetch
        self._tmp.cleanup()

    def _cp(self, text="# Shared Current Projects\n\n> newest block\n"):
        (self.root / "Reference" / "CURRENT-PROJECTS.md").write_text(text, encoding="utf-8")

    def _journal(self, offset, name_suffix="claude", text=None):
        name = f"{_day(offset)}-{name_suffix}.md"
        body = text or f"# Journal {name}\n\nwork happened\n"
        (self.root / "Journal" / name).write_text(body, encoding="utf-8")
        return name

    # -- happy path -------------------------------------------------------------------------------

    def test_happy_path_counts_order_and_pointer(self):
        self._cp()
        old = self._journal(5)
        newer = self._journal(1)
        newest = self._journal(0)
        out = catch_up.catch_up()
        self.assertIn("2 of 2 journal(s)", out, "counts must be stated, not implied")
        self.assertIn("newest block", out)
        self.assertIn(newest, out)
        self.assertIn(newer, out)
        self.assertNotIn(old, out.split("NEXT:")[0].replace(f"get_document('{old}')", ""),
                         "a journal outside the window must not be shown")
        self.assertLess(out.index(newest), out.index(newer), "newest first")
        self.assertIn("NEXT:", out)
        self.assertIn("ADDITIVE and newest-first", out, "the read rules travel with the data")
        self.assertIn(str(config.GET_DOC_MAX_CHARS), out, "the visibility-window fact must be stated")

    # -- loud, named failure modes ----------------------------------------------------------------

    def test_missing_current_projects_names_the_path(self):
        self._journal(0)
        out = catch_up.catch_up()
        self.assertIn("PROBLEM:", out)
        self.assertIn("CURRENT-PROJECTS.md", out)
        self.assertIn("missing or unreadable", out)

    def test_missing_journal_dir_is_distinct_from_no_recent(self):
        self._cp()
        config.FETCH_ONLY_DIRS = {"homelab": [str(self.root / "NoSuchDir")]}
        out = catch_up.catch_up()
        self.assertIn("missing or unreadable", out)
        self.assertIn("NoSuchDir", out, "the directory must be NAMED")
        self.assertNotIn("nothing recent", out, "unreadable must not read as 'no recent journals'")

    def test_empty_journal_dir_states_zero(self):
        self._cp()
        out = catch_up.catch_up()
        self.assertIn("0 .md files", out)

    def test_no_recent_journals_is_distinct_from_unreadable(self):
        self._cp()
        self._journal(6)   # exists, but outside the default 2-day window
        out = catch_up.catch_up()
        self.assertIn("No journal within the last 2 day(s)", out)
        self.assertIn("1 .md file(s) exist", out)
        self.assertIn("'nothing recent', not 'could not look'", out)
        self.assertNotIn("missing or unreadable", out.split("## Newest journals")[1])

    def test_topic_filter_miss_states_filter_and_count(self):
        self._cp()
        self._journal(0, "cameras")
        self._journal(1, "p20")
        out = catch_up.catch_up(topic="zebra")
        self.assertIn("'zebra'", out)
        self.assertIn("2 -> 0", out, "the candidate count it filtered FROM must be stated")

    def test_topic_filter_hit_narrows(self):
        self._cp()
        cam = self._journal(0, "cameras")
        self._journal(1, "p20")
        out = catch_up.catch_up(topic="cameras")
        self.assertIn(cam, out)
        self.assertIn("2 -> 1", out)

    def test_days_is_capped_and_the_cap_is_stated(self):
        self._cp()
        self._journal(0)
        out = catch_up.catch_up(days=99)
        self.assertIn(f"capped at {catch_up.DAYS_CAP}", out)
        self.assertIn("requested 99", out)

    def test_truncation_is_stated(self):
        self._cp("# CP\n" + "x" * (catch_up.CP_HEAD_CHARS + 500))
        self._journal(0, "claude", "# J\n" + "y" * (catch_up.JOURNAL_HEAD_CHARS + 500))
        out = catch_up.catch_up()
        self.assertIn("truncated at", out)
        self.assertIn("head shown:", out)

    def test_glob_not_rglob_keeps_subdirs_invisible(self):
        """The credentials-exclusion property, held here the same way as every other reader."""
        self._cp()
        sub = self.root / "Journal" / "Credentials"
        sub.mkdir()
        (sub / f"{_day(0)}-secrets.md").write_text("# secret\n", encoding="utf-8")
        visible = self._journal(0)
        out = catch_up.catch_up()
        self.assertIn(visible, out)
        self.assertNotIn("secrets", out)


if __name__ == "__main__":
    unittest.main()
