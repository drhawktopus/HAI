"""Tests for `backlog.propose()` and `load_proposals()`.

The class-17 pins assert the WIRE (BRAIN_TOOLS + dispatch), not just behaviour - a tool that
works and is unreachable is a capability nobody has.

`propose()` writes to `config.SELFENG_PROPOSALS`, a REAL read-write mount in the container.
Every test here inherits `_ProposalCase`, which redirects all three self-eng paths to a temp dir
and ASSERTS the redirect took effect before anything writes."""
import os
import shutil
import tempfile
import unittest

import yaml

import backlog
import config
import tools


class _ProposalCase(unittest.TestCase):
    """Redirects the self-eng store into a temp dir. Inherit this — never write a test in this
    file that touches config.SELFENG_* directly."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proposals = os.path.join(self.tmp, "proposals.yaml")
        self.backlog_file = os.path.join(self.tmp, "backlog.yaml")
        self.completed = os.path.join(self.tmp, "completed.json")
        self._orig = (config.SELFENG_BACKLOG, config.SELFENG_PROPOSALS, config.SELFENG_COMPLETED)
        config.SELFENG_BACKLOG = self.backlog_file
        config.SELFENG_PROPOSALS = self.proposals
        config.SELFENG_COMPLETED = self.completed
        # Isolation check: ensure paths redirect to tmpdir, not live storage.
        self.assertTrue(config.SELFENG_PROPOSALS.startswith(self.tmp),
                        "the proposals path was not redirected — this test would write LIVE state")
        self.admin = sorted(config.ADMIN_USERS)[0]

    def tearDown(self):
        (config.SELFENG_BACKLOG, config.SELFENG_PROPOSALS, config.SELFENG_COMPLETED) = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, path, items, version=1):
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump({"version": version, "items": items}, fh, sort_keys=False)


def _names(tool_list):
    return {(t.get("function") or {}).get("name") for t in tool_list if isinstance(t, dict)}


class TheWireExists(_ProposalCase):

    def test_offered_to_the_quick_brain(self):
        self.assertIn("propose_work", _names(tools.BRAIN_TOOLS))

    def test_offered_to_the_heavy_brain(self):
        # G3's reflex ("prep the changes you'd need") deliberates on the heavy tier — a tool it
        # cannot see cannot be the reflex's landing place.
        self.assertIn("propose_work", _names(tools.HEAVY_TOOLS))

    def test_dispatch_routes_it(self):
        """The seam that makes a registered schema actually DO something. A missing dispatch arm
        is invisible in every list-membership test above."""
        out = tools.run_tool("propose_work", {"action": "list"}, "", user=self.admin)
        self.assertNotIn("Unknown tool", out)
        self.assertIn("No proposals waiting", out)


class ProposingIsNotArming(_ProposalCase):
    """
        A proposal is never self-armed; it requires separate approval and scheduling.
    """

    def test_the_filed_item_is_not_approved(self):
        item = backlog.propose("T", "do the thing")
        self.assertIs(item["approved_overnight"], False)

    def test_filing_makes_nothing_eligible(self):
        """The property that actually matters: eligibility reads the human-owned backlog file, so
        a proposal cannot become runnable no matter what it contains."""
        self._write(self.backlog_file, [])
        backlog.propose("T", "do the thing", kind="test-verifiable")
        self.assertEqual(backlog.eligible_items(), [])

    def test_filing_never_writes_the_approved_backlog(self):
        self._write(self.backlog_file, [{"id": "bl-1", "objective": "human item",
                                         "kind": "test-verifiable", "approved_overnight": True}])
        before = open(self.backlog_file, "rb").read()
        tools.run_tool("propose_work", {"action": "propose", "title": "T", "objective": "obj"},
                       "", user=self.admin)
        self.assertEqual(open(self.backlog_file, "rb").read(), before,
                         "propose_work touched the human-owned backlog file")

    def test_the_answer_says_it_is_not_armed(self):
        out = tools.run_tool("propose_work",
                             {"action": "propose", "title": "T", "objective": "obj"},
                             "", user=self.admin)
        self.assertIn("not armed", out.lower())

    def test_an_unattended_run_cannot_file_one(self):
        """Test that unattended runs cannot propose work (preventing self-feeding loops)."""
        self.assertIn("propose_work", tools.SANDBOX_BLOCKED_TOOLS)
        self.assertNotIn("propose_work", _names(tools.sandbox_safe(tools.BRAIN_TOOLS)))

    def test_a_friend_cannot_file_one(self):
        out = tools.run_tool("propose_work", {"action": "list"}, "", user="Jordan")
        self.assertIn("Permission denied", out)


class TheStoreSurvivesAWrite(_ProposalCase):
    """The clobber guard. `_load_file` fail-softs to empty on any error and `propose()` writes the
    WHOLE file back — so an unparseable file meant a silent total wipe. Latent until G4(b) gave
    propose() a production caller."""

    BROKEN = "this: is: not: valid: yaml: : :\n  - broken\n"

    def test_an_unreadable_file_refuses_instead_of_wiping(self):
        with open(self.proposals, "w", encoding="utf-8") as fh:
            fh.write(self.BROKEN)
        before = open(self.proposals, "rb").read()
        with self.assertRaises(RuntimeError):
            backlog.propose("T", "obj")
        self.assertEqual(open(self.proposals, "rb").read(), before,
                         "the unreadable proposals file was overwritten — this is the data loss "
                         "the guard exists to prevent")

    def test_the_tool_reports_that_nothing_was_written(self):
        with open(self.proposals, "w", encoding="utf-8") as fh:
            fh.write(self.BROKEN)
        out = tools.run_tool("propose_work",
                             {"action": "propose", "title": "T", "objective": "obj"},
                             "", user=self.admin)
        self.assertIn("nothing was written", out)

    def test_an_absent_file_is_still_a_fresh_start(self):
        """Positive control: ensure a guard that refuses ALL writes doesn't pass by accident."""
        self.assertFalse(os.path.exists(self.proposals))
        item = backlog.propose("T", "first ever proposal")
        self.assertTrue(os.path.exists(self.proposals))
        self.assertEqual([p["id"] for p in backlog.load_proposals()], [item["id"]])

    def test_an_append_preserves_what_was_already_there(self):
        """Second positive control, and the real-world case: the live file holds 3 proposals."""
        first = backlog.propose("one", "first objective")
        second = backlog.propose("two", "second objective")
        self.assertEqual([p["id"] for p in backlog.load_proposals()], [first["id"], second["id"]])

    def test_an_empty_file_is_not_treated_as_corrupt(self):
        open(self.proposals, "w", encoding="utf-8").close()
        item = backlog.propose("T", "obj")
        self.assertEqual([p["id"] for p in backlog.load_proposals()], [item["id"]])


class TheAnswersAreHonest(_ProposalCase):
    def test_an_empty_objective_is_refused_not_filed(self):
        out = tools.run_tool("propose_work", {"action": "propose", "title": "T", "objective": " "},
                             "", user=self.admin)
        self.assertIn("needs an objective", out)
        self.assertEqual(backlog.load_proposals(), [])

    def test_list_shows_what_is_waiting(self):
        backlog.propose("Fix the thing", "make the thing work")
        out = tools.run_tool("propose_work", {"action": "list"}, "", user=self.admin)
        self.assertIn("Fix the thing", out)
        self.assertIn("1 proposal waiting", out)

    def test_list_reports_the_armed_count_separately(self):
        """Proposals and armed work are different questions; answering one with the other is how
        'I proposed it' becomes 'it is queued to run'."""
        self._write(self.backlog_file, [{"id": "bl-1", "objective": "human item",
                                         "kind": "test-verifiable", "approved_overnight": True}])
        backlog.propose("Fix the thing", "make the thing work")
        out = tools.run_tool("propose_work", {"action": "list"}, "", user=self.admin)
        self.assertIn("1 proposal waiting", out)
        self.assertIn("1 approved item currently eligible", out)


if __name__ == "__main__":
    unittest.main()
