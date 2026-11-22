"""Audits the --deploying step 0 logic: verifies that live work is adopted while conflicts are rejected. All external commands (ssh, scp, git) are mocked to ensure deterministic testing without side effects."""
import unittest
from unittest import mock

import orch_mirror_audit as A

FILES = {"f%03d.py" % i: "h%d" % i for i in range(120)}       # past the audit's 100-pair floor


class StepZero(unittest.TestCase):
    def run_step0(self, live_changes, dirty=(), going=(), mirror_extra=None, argv=None):
        live = dict(FILES, **live_changes)
        mine = dict(FILES, **(mirror_extra or {}))
        git, adopted = [], []

        def fake_run(argv, **kw):
            git.append(argv)
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(A, "local_hashes", lambda: dict(mine)), \
                mock.patch.object(A, "live_hashes", lambda: live), \
                mock.patch.object(A, "mirror_dirty", lambda: set(dirty)), \
                mock.patch.object(A, "adopt", lambda names, lv: adopted.extend(names) or list(names)), \
                mock.patch.object(A.subprocess, "run", fake_run):
            rc = A.main(argv if argv is not None else ["--deploying"] + list(going))
        return rc, adopted, git

    def test_a_committed_mirror_only_file_refuses_a_deploy_that_does_not_carry_it(self):
        rc, _, _ = self.run_step0({}, going=["f050.py"], mirror_extra={"tool.py": "never deployed"})
        self.assertEqual(rc, 1, "finished work sitting only in the mirror is drift (2026-11-15: 24 such files)")

    def test_the_same_file_carried_by_the_deploy_goes_up(self):
        rc, _, _ = self.run_step0({}, going=["tool.py"], mirror_extra={"tool.py": "never deployed"})
        self.assertEqual(rc, 0)

    def test_an_uncommitted_mirror_only_file_is_work_in_progress_and_does_not_refuse(self):
        rc, _, _ = self.run_step0({}, going=["f050.py"], dirty=["new.py"], mirror_extra={"new.py": "being written"})
        self.assertEqual(rc, 0)

    def test_the_plain_report_calls_committed_mirror_only_files_drift(self):
        rc, _, _ = self.run_step0({}, mirror_extra={"tool.py": "never deployed"}, argv=[])
        self.assertEqual(rc, 1)
        rc, _, _ = self.run_step0({}, argv=[])
        self.assertEqual(rc, 0, "positive control: an identical tree is clean")

    def test_work_live_moved_to_is_adopted_and_committed_and_the_deploy_goes_on(self):
        rc, adopted, git = self.run_step0({"f001.py": "doc's fix"}, going=["f050.py"])
        self.assertEqual(rc, 0)
        self.assertEqual(adopted, ["f001.py"])
        commits = [a for a in git if "commit" in a]
        self.assertTrue(commits and commits[0][-1] == "f001.py", "only the adopted path is committed")

    def test_a_file_both_sides_changed_still_refuses(self):
        rc, adopted, _ = self.run_step0({"f001.py": "doc's fix"}, dirty=["f001.py"], going=["f050.py"])
        self.assertEqual(rc, 1)
        self.assertEqual(adopted, [], "the mirror's own uncommitted edit is never overwritten")

    def test_a_file_this_deploy_carries_is_not_adopted(self):
        rc, adopted, _ = self.run_step0({"f050.py": "older live"}, going=["f050.py"])
        self.assertEqual((rc, adopted), (0, []), "the deploy's own file goes up; live's copy is not pulled over it")

    def test_nothing_moved_nothing_adopted(self):
        rc, adopted, git = self.run_step0({}, going=["f050.py"])
        self.assertEqual((rc, adopted), (0, []))
        self.assertFalse(any("commit" in a for a in git))


if __name__ == "__main__":
    unittest.main()
