"""A bulk replace: preview first, then apply.

The rule this tests: a mechanical change across many files is only safe if the apply is
refused whenever the workspace no longer matches the preview. A dry run writes nothing. An
apply is accepted only while every file still hashes to what the preview saw and the glob
still matches exactly the same set of files; otherwise nothing at all is written. Protected
paths, paths outside the workspace, .git, binary files and non-UTF-8 files are never touched.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from runner import CodingRunner, ConflictError, PolicyError


def repo(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "a.py").write_text("OLD_HOST = \"10.0.0.1\"\nx = 1\n", encoding="utf-8")
    (path / "b.py").write_text("url = \"http://10.0.0.1:8123\"\n", encoding="utf-8")
    (path / "keep.md").write_text("OLD_HOST stays here, 10.0.0.1 is here too; it is not a .py file; the word start is here\n", encoding="utf-8")
    (path / "nested").mkdir()
    (path / "nested" / "c.py").write_text("addr = \"10.0.0.1\"  # two in one file: 10.0.0.1\n",
                                          encoding="utf-8")
    (path / "secret.bin").write_bytes(b"\x00\x01OLD_HOST\x00")
    for args in (["init"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "start"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    return path


class BulkReplace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = repo(base / "code")
        registry = {"workspaces": [{
            "id": "code", "path": str(self.root), "description": "fixture repo",
            "read": True, "write": True, "git": True, "shared": True,
            "protected_paths": ["secrets"], "ignored_paths": [".git"],
            "commands": {"unit": ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"]},
        }]}
        (base / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
        self.runner = CodingRunner(base / "registry.json", base / "state")

    def snapshot(self):
        """Every file's bytes, so a refusal can be proven to have written nothing."""
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}

    def test_dry_run_writes_nothing_and_reports_counts_and_samples(self):
        before = self.snapshot()
        preview = self.runner.replace_files("code", glob="**/*.py",
                                           find="10.0.0.1", replace="localhost", max_files=10)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["file_count"], 3, "a.py, b.py and nested/c.py match; keep.md and secret.bin do not")
        self.assertEqual(preview["total_matches"], 4, "two matches in nested/c.py")
        paths = sorted(item["path"] for item in preview["files"])
        self.assertEqual(paths, ["a.py", "b.py", "nested/c.py"])
        for item in preview["files"]:
            self.assertRegex(item["sha256"], r"[0-9a-f]{64}")
            self.assertTrue(item["samples"], "the preview shows the caller what changes")
            sample = item["samples"][0]
            self.assertIn("10.0.0.1", sample["before"])
            self.assertIn("localhost", sample["after"])
        self.assertEqual(self.snapshot(), before, "a dry run writes nothing")

    def test_apply_with_the_preview_list_writes_every_file(self):
        preview = self.runner.replace_files("code", glob="**/*.py",
                                           find="10.0.0.1", replace="localhost", max_files=10)
        result = self.runner.replace_files("code", glob="**/*.py",
                                          find="10.0.0.1", replace="localhost", max_files=10,
                                          dry_run=False, files=preview["files"])
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["total_replacements"], 4)
        self.assertIn("localhost", (self.root / "a.py").read_text(encoding="utf-8"))
        self.assertIn("localhost", (self.root / "nested" / "c.py").read_text(encoding="utf-8"))
        self.assertNotIn("10.0.0.1", (self.root / "b.py").read_text(encoding="utf-8"))
        self.assertIn("10.0.0.1", (self.root / "keep.md").read_text(encoding="utf-8"),
                      "the glob decides what is touched: a non-matching file is left alone")
        self.assertIn(b"OLD_HOST", (self.root / "secret.bin").read_bytes(), "binary files are not replaced")
        new_shas = {item["path"]: item["new_sha256"] for item in result["files"]}
        for path, sha in new_shas.items():
            import hashlib
            self.assertEqual(sha, hashlib.sha256((self.root / path).read_bytes()).hexdigest())

    def test_apply_after_a_file_changed_writes_nothing(self):
        preview = self.runner.replace_files("code", glob="**/*.py",
                                           find="10.0.0.1", replace="localhost", max_files=10)
        (self.root / "a.py").write_text("OLD_HOST = \"10.0.0.9\"\n", encoding="utf-8")
        with self.assertRaises(ConflictError) as raised:
            self.runner.replace_files("code", glob="**/*.py",
                                      find="10.0.0.1", replace="localhost", max_files=10,
                                      dry_run=False, files=preview["files"])
        self.assertIn("changed since the dry run", str(raised.exception))
        self.assertIn("nothing was written", str(raised.exception))
        self.assertIn("10.0.0.1", (self.root / "b.py").read_text(encoding="utf-8"),
                     "b.py and c.py were NOT written just because a.py moved")
        self.assertIn("10.0.0.9", (self.root / "a.py").read_text(encoding="utf-8"))

    def test_a_file_set_that_changed_between_preview_and_apply_is_refused(self):
        preview = self.runner.replace_files("code", glob="**/*.py",
                                           find="10.0.0.1", replace="localhost", max_files=10)
        (self.root / "d.py").write_text("addr = \"10.0.0.1\"\n", encoding="utf-8")
        with self.assertRaises(ConflictError) as raised:
            self.runner.replace_files("code", glob="**/*.py",
                                      find="10.0.0.1", replace="localhost", max_files=10,
                                      dry_run=False, files=preview["files"])
        self.assertIn("file set changed", str(raised.exception))
        self.assertIn("newly matching: d.py", str(raised.exception))
        self.assertIn("10.0.0.1", (self.root / "b.py").read_text(encoding="utf-8"))

    def test_more_files_than_the_stated_limit_is_refused(self):
        with self.assertRaises(PolicyError) as raised:
            self.runner.replace_files("code", glob="**/*.py",
                                      find="10.0.0.1", replace="localhost", max_files=2)
        self.assertIn("more than the stated max_files of 2", str(raised.exception))
        self.assertIn("10.0.0.1", (self.root / "a.py").read_text(encoding="utf-8"))

    def test_max_files_is_required_and_bounded(self):
        for bad in (None, 0, -1, "10", True):
            with self.assertRaises(PolicyError):
                self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                         replace="localhost", max_files=bad)
        with self.assertRaises(PolicyError) as raised:
            self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                      replace="localhost", max_files=10_000)
        self.assertIn("may not exceed", str(raised.exception))

    def test_an_empty_find_is_refused(self):
        with self.assertRaises(PolicyError) as raised:
            self.runner.replace_files("code", glob="**/*.py", find="", replace="x", max_files=10)
        self.assertIn("find must not be empty", str(raised.exception))

    def test_an_invalid_regex_is_refused(self):
        with self.assertRaises(PolicyError) as raised:
            self.runner.replace_files("code", glob="**/*.py", find="192.168.0.(",
                                      replace="x", regex=True, max_files=10)
        self.assertIn("invalid regex", str(raised.exception))

    def test_a_glob_that_leaves_the_workspace_is_refused(self):
        for escape in ("../outside.py", "/etc/hosts", "a.py/../../b.py"):
            with self.assertRaises(PolicyError) as raised:
                self.runner.replace_files("code", glob=escape, find="10.0.0.1",
                                         replace="localhost", max_files=10)
            self.assertIn("relative to the workspace", str(raised.exception))
        (self.root.parent / "outside.py").write_text("10.0.0.1\n", encoding="utf-8")
        with self.assertRaises(PolicyError):
            self.runner.replace_files("code", glob="../*.py", find="10.0.0.1",
                                      replace="localhost", max_files=10)

    def test_a_protected_path_is_neither_previewed_nor_written(self):
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / "notes.py").write_text("host = \"10.0.0.1\"\n", encoding="utf-8")
        preview = self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                            replace="localhost", max_files=10)
        self.assertNotIn("secrets/notes.py", [item["path"] for item in preview["files"]])
        self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                  replace="localhost", max_files=10, dry_run=False, files=preview["files"])
        self.assertIn("10.0.0.1", (self.root / "secrets" / "notes.py").read_text(encoding="utf-8"))

    def test_git_history_is_never_touched(self):
        preview = self.runner.replace_files("code", glob="**/*", find="start",
                                           replace="rewritten", max_files=200)
        for item in preview["files"]:
            self.assertFalse(item["path"].startswith(".git"), "the glob reaches .git; the replace does not")
        self.runner.replace_files("code", glob="**/*", find="start", replace="rewritten",
                                  max_files=200, dry_run=False, files=preview["files"])
        self.assertIn("start", subprocess.run(["git", "-C", str(self.root), "log", "--oneline"],
                                             capture_output=True, text=True).stdout)

    def test_regex_with_groups_replaces_correctly(self):
        pattern = r"(\d+)\.(\d+)\.(\d+)\.(\d+)"
        preview = self.runner.replace_files("code", glob="**/*.py", find=pattern,
                                           replace=r"host-\1-\2-\3-\4", regex=True, max_files=10)
        result = self.runner.replace_files("code", glob="**/*.py", find=pattern,
                                          replace=r"host-\1-\2-\3-\4", regex=True, max_files=10,
                                          dry_run=False, files=preview["files"])
        self.assertEqual(result["total_replacements"], 4)
        self.assertIn("host-10-0-0-1", (self.root / "a.py").read_text(encoding="utf-8"))
        self.assertIn("host-10-0-0-1:8123", (self.root / "b.py").read_text(encoding="utf-8"))

    def test_regex_find_counts_and_literal_find_count_differ_correctly(self):
        preview = self.runner.replace_files("code", glob="nested/c.py",
                                           find="10\\.0\\.0\\.1", replace="localhost",
                                           regex=True, max_files=10)
        self.assertEqual(preview["total_matches"], 2)

    def test_a_write_that_fails_halfway_leaves_no_half_applied_change(self):
        """The rollback restores the bytes each file held before the call, not the new ones."""
        import pathlib
        from unittest import mock

        preview = self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                           replace="localhost", max_files=10)
        before = self.snapshot()
        target = (self.root / "nested" / "c.py").resolve()
        original = pathlib.Path.write_bytes

        def failing_write(self_path, data, **kw):
            if self_path == target:
                raise OSError("simulated disk full")
            return original(self_path, data, **kw)

        patch = mock.patch.object(pathlib.Path, "write_bytes", failing_write)
        patch.start()
        self.addCleanup(patch.stop)
        with self.assertRaises(ConflictError) as raised:
            self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                      replace="localhost", max_files=10,
                                      dry_run=False, files=preview["files"])
        self.assertIn("restored", str(raised.exception))
        self.assertEqual(self.snapshot(), before, "every file is back to what it held before the call")

    def test_a_read_only_workspace_is_refused(self):
        self.runner.workspaces["code"] = self.runner.workspaces["code"].__class__(
            id="code", root=self.runner.workspaces["code"].root, description="read only",
            read=True, write=False, git=True, protected_paths=(), ignored_paths=(".git",),
            commands={})
        with self.assertRaises(PolicyError) as raised:
            self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                      replace="localhost", max_files=10)
        self.assertIn("read-only", str(raised.exception))

    def test_a_replace_named_by_a_task_runs_in_that_task_sown_tree(self):
        task = self.runner.task_start("code", "bulk replace")
        worktree = Path(task["worktree"]["path"])
        preview = self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                           replace="localhost", max_files=10, task_id=task["id"])
        self.assertEqual(preview["file_count"], 3)
        self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1", replace="localhost",
                                  max_files=10, dry_run=False, files=preview["files"], task_id=task["id"])
        self.assertIn("localhost", (worktree / "a.py").read_text(encoding="utf-8"))
        self.assertIn("10.0.0.1", (self.root / "a.py").read_text(encoding="utf-8"),
                      "the base workspace is untouched while the task works in its own tree")

    def test_apply_without_a_file_list_is_refused(self):
        with self.assertRaises(PolicyError) as raised:
            self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                      replace="localhost", max_files=10, dry_run=False)
        self.assertIn("needs the file list", str(raised.exception))
        self.assertIn("10.0.0.1", (self.root / "a.py").read_text(encoding="utf-8"))

    def test_apply_entries_must_carry_a_path_and_a_hash(self):
        with self.assertRaises(PolicyError):
            self.runner.replace_files("code", glob="**/*.py", find="10.0.0.1",
                                      replace="localhost", max_files=10, dry_run=False,
                                      files=[{"path": "a.py"}])

    def test_the_preview_is_capped_so_it_can_be_read(self):
        (self.root / "many.py").write_text("10.0.0.1\n" * 50, encoding="utf-8")
        preview = self.runner.replace_files("code", glob="many.py", find="10.0.0.1",
                                           replace="localhost", max_files=10)
        self.assertEqual(preview["files"][0]["matches"], 50)
        self.assertEqual(len(preview["files"][0]["samples"]), 3)


if __name__ == "__main__":
    unittest.main()