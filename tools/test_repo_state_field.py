"""repo_state_field: a clean repo reads clean, and uncommitted, untracked and unpushed work each show by repo, with
the number of repos read, so a broken reader can never pass for "all clean"."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import repo_state_field as rsf  # noqa: E402


def git(path, *args):
    subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   check=True, capture_output=True)


class RepoState(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.origin = os.path.join(self.root, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.origin], check=True)
        self.repo = os.path.join(self.root, "config")
        subprocess.run(["git", "clone", "-q", self.origin, self.repo], check=True, capture_output=True)
        git(self.repo, "checkout", "-q", "-b", "main")
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("a\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "a")
        git(self.repo, "push", "-q", "-u", "origin", "main")

    def test_a_clean_pushed_repo_reads_clean_with_its_count(self):
        self.assertEqual(rsf.field([self.repo]), "All 1 repos committed and pushed.")

    def test_uncommitted_untracked_and_unpushed_each_show(self):
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("changed\n")
        with open(os.path.join(self.repo, "new.txt"), "w") as fh:
            fh.write("new\n")
        with open(os.path.join(self.repo, "b.txt"), "w") as fh:
            fh.write("b\n")
        git(self.repo, "add", "b.txt")
        git(self.repo, "commit", "-q", "-m", "b")
        text = rsf.field([self.repo])
        self.assertIn("⚠ config: 1 uncommitted, 1 untracked, 1 unpushed", text)
        self.assertIn("0 of 1 repos clean", text)

    def test_a_repo_without_an_upstream_is_judged_on_its_working_tree_only(self):
        plain = os.path.join(self.root, "plain")
        subprocess.run(["git", "init", "-q", plain], check=True)
        self.assertEqual(rsf.field([plain]), "All 1 repos committed and pushed.")

    def test_an_unreadable_repo_says_so_and_nothing_reads_clean_by_default(self):
        text = rsf.field([os.path.join(self.root, "no-such-repo"), self.repo])
        self.assertIn("no-such-repo: could not tell", text)
        self.assertIn("1 of 2 repos clean", text)
        self.assertIn("could not tell", rsf.field([]))

    def test_a_windows_owned_repo_reads_its_crlf_copy_clean_but_still_shows_a_real_change(self):
        win = os.path.join(self.root, "share")
        repo = os.path.join(win, "mirror")
        subprocess.run(["git", "init", "-q", repo], check=True)
        # a Windows checkout: committed by a git with core.autocrlf=true, so the blob is LF and the file on disk CRLF
        subprocess.run(["git", "-C", repo, "config", "core.autocrlf", "true"], check=True)
        with open(os.path.join(repo, "f.txt"), "wb") as fh:
            fh.write(b"one\r\ntwo\r\n")
        git(repo, "add", "f.txt")
        git(repo, "commit", "-q", "-m", "crlf on disk")
        # read from another machine (Linux over the share): its git has no autocrlf, and the stat differs, so it re-reads
        subprocess.run(["git", "-C", repo, "config", "--unset", "core.autocrlf"], check=True)
        os.environ["GIT_CONFIG_GLOBAL"] = os.path.join(self.root, "no-global-config")   # no Windows default here:
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"                                         # neither global nor system
        self.addCleanup(os.environ.pop, "GIT_CONFIG_GLOBAL", None)
        self.addCleanup(os.environ.pop, "GIT_CONFIG_NOSYSTEM", None)
        st = os.stat(os.path.join(repo, "f.txt"))
        os.utime(os.path.join(repo, "f.txt"), (st.st_atime + 100, st.st_mtime + 100))
        old = rsf.WINDOWS_ROOT
        try:
            rsf.WINDOWS_ROOT = ""
            self.assertIn("mirror: 1 uncommitted", rsf.field([repo]), "the noise this setting exists for")
            rsf.WINDOWS_ROOT = win
            self.assertEqual(rsf.field([repo]), "All 1 repos committed and pushed.")
            with open(os.path.join(repo, "f.txt"), "wb") as fh:
                fh.write(b"one\r\nthree\r\n")                   # a real change stays visible (the positive control)
            self.assertIn("mirror: 1 uncommitted", rsf.field([repo]))
        finally:
            rsf.WINDOWS_ROOT = old

    def test_a_repos_own_line_ending_setting_wins_under_the_windows_root(self):
        win = os.path.join(self.root, "share2")
        repo = os.path.join(win, "release")
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "core.autocrlf", "false"], check=True)
        with open(os.path.join(repo, "out.txt"), "wb") as fh:
            fh.write(b"a\n")
        git(repo, "add", "out.txt")
        git(repo, "commit", "-q", "-m", "lf")
        with open(os.path.join(repo, "out.txt"), "wb") as fh:
            fh.write(b"a\r\n")               # Windows git with this repo's own false calls this changed; so must we
        old = rsf.WINDOWS_ROOT
        try:
            rsf.WINDOWS_ROOT = win
            self.assertIn("release: 1 uncommitted", rsf.field([repo]))
        finally:
            rsf.WINDOWS_ROOT = old

    def test_reading_a_repo_never_rewrites_its_index(self):
        with open(os.path.join(self.repo, "a.txt"), "a") as fh:
            fh.write("")                     # same bytes, new stat: a plain `git status` would refresh the index
        os.utime(os.path.join(self.repo, "a.txt"), None)
        index = os.path.join(self.repo, ".git", "index")
        before = os.stat(index).st_mtime_ns, open(index, "rb").read()
        rsf.field([self.repo])
        self.assertEqual(before, (os.stat(index).st_mtime_ns, open(index, "rb").read()))

    def test_the_field_fits_the_embed_and_holds_no_quote_or_backslash(self):
        text = rsf.field([os.path.join(self.root, "it's\\odd")] * 60)
        self.assertLessEqual(len(text), rsf.MAX_CHARS)
        self.assertNotIn("'", text)
        self.assertNotIn("\\", text)


if __name__ == "__main__":
    unittest.main()
