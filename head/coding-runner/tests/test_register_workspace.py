"""A workspace added at run time sits beside the baked registry.

The rule this tests: a new repository becomes usable only when the operator approves it, and
approval is meaningless unless the runner refuses everything it should refuse - a path
outside the allowed shares, a folder without local Git history, a duplicate or malformed
id, a command that is not one of the allowed templates. Nothing is written unless every
check passes.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from runner import CodingRunner, ConflictError, PolicyError


def repo(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "notes.md").write_text("one\n", encoding="utf-8")
    (path / "test_sample.py").write_text("import unittest\n\n"
                                         "class T(unittest.TestCase):\n"
                                         "    def test_ok(self):\n"
                                         "        self.assertTrue(True)\n", encoding="utf-8")
    for args in (["init"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "start"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    return path


class Registration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.shares = base / "shares"
        self.shares.mkdir()
        (base / "sample").mkdir()
        self.baked = base / "registry.json"
        self.local = base / "state" / "registry.local.json"
        self.baked.write_text(json.dumps({"workspaces": [
            {"id": "sample", "path": str(base / "sample"), "description": "fixture",
             "write": True, "git": False, "commands": {"compile": ["python", "-m", "compileall", "-q", "."]}}
        ]}), encoding="utf-8")
        self.state = base / "state"
        self.state.mkdir()
        import os
        os.environ["CODING_RUNNER_LOCAL_REGISTRY"] = str(self.local)
        os.environ["CODING_RUNNER_SHARE_ROOTS"] = str(self.shares)
        self.addCleanup(lambda: os.environ.pop("CODING_RUNNER_LOCAL_REGISTRY", None))
        self.addCleanup(lambda: os.environ.pop("CODING_RUNNER_SHARE_ROOTS", None))
        self.runner = CodingRunner(self.baked, self.state)

    def request(self, repo_path, id="foundry", commands=None):
        return {"id": id, "path": str(repo_path), "description": "a lab repo",
                "commands": commands if commands is not None else
                {"unit": ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"]}}

    # ---- a valid registration works at once ----------------------------------------------------
    def test_a_valid_registration_is_usable_immediately(self):
        result = self.runner.register_workspace(self.request(repo(self.shares / "foundry")))
        self.assertTrue(result["registered"])
        listed = [w["id"] for w in self.runner.workspace_list()]
        self.assertIn("foundry", listed, "the workspace is listed without a restart")
        read = self.runner.file_read("foundry", "notes.md")
        self.assertIn("one", read["content"], "its files are readable at once")
        self.assertTrue(self.runner.project_manifest("foundry")["git_branch"], "git works in it")

    def test_the_defaults_always_protect_git_secrets_and_backups(self):
        result = self.runner.register_workspace(dict(self.request(repo(self.shares / "foundry")),
                                                     protected_paths=["payload.json"]))
        self.assertEqual(result["workspace"]["protected_paths"],
                         [".git", ".env", "secrets", "backups", "payload.json"])
        with self.assertRaises(PolicyError):
            self.runner.file_create("foundry", "secrets/token.txt", content="x")

    def test_a_registered_workspace_is_shared_and_a_worktree_is_made_for_it(self):
        self.runner.register_workspace(self.request(repo(self.shares / "foundry")))
        a = self.runner.task_start("foundry", "job a")
        b = self.runner.task_start("foundry", "job b")
        self.assertTrue(a["worktree"] and b["worktree"], "two jobs at once on one repo")
        self.assertNotEqual(a["workspace"], b["workspace"])
        for task, folder in ((a, "job-a"), (b, "job-b")):
            self.runner.file_create(task["workspace"], folder + "/made.txt", content="x\n")
        self.assertTrue(self.runner.task_finish(a["id"], action="merge")["merged"])
        self.assertTrue(self.runner.task_finish(b["id"], action="merge")["merged"])
        self.assertTrue((self.shares / "foundry" / "job-a" / "made.txt").exists())
        self.assertTrue((self.shares / "foundry" / "job-b" / "made.txt").exists())

    def test_a_registered_workspace_survives_a_restart(self):
        self.runner.register_workspace(self.request(repo(self.shares / "foundry")))
        second = CodingRunner(self.baked, self.state)
        self.assertIn("foundry", [w["id"] for w in second.workspace_list()])
        self.assertIn("one", second.file_read("foundry", "notes.md")["content"])

    def test_a_baked_id_cannot_be_shadowed(self):
        before = self.runner.workspaces["sample"].root
        with self.assertRaises(PolicyError) as caught:
            self.runner.register_workspace(self.request(repo(self.shares / "copy-of-sample"), id="sample"))
        self.assertIn("baked registry", str(caught.exception))
        self.assertFalse(self.local.exists(), "the refusal wrote nothing")
        self.assertEqual(self.runner.workspaces["sample"].root, before)

    def test_a_local_entry_that_repeats_a_baked_id_is_reported_not_loaded(self):
        """An entry left in the runtime file from an earlier era never overrides the baked one."""
        self.local.parent.mkdir(parents=True, exist_ok=True)
        self.local.write_text(json.dumps({"workspaces": [
            {"id": "sample", "path": str(self.shares), "description": "impostor",
             "write": True, "git": False, "commands": {}}
        ]}), encoding="utf-8")
        runner = CodingRunner(self.baked, self.state)
        self.assertEqual(runner.workspaces["sample"].root, self.runner.workspaces["sample"].root)
        self.assertIn("sample", runner.shadowed_ids)

    # ---- everything refused writes nothing ------------------------------------------------------
    def test_a_path_outside_the_allowed_roots_is_refused(self):
        outside = Path(self.temp.name) / "elsewhere"
        with self.assertRaises(PolicyError) as caught:
            self.runner.register_workspace(self.request(repo(outside)))
        self.assertIn("share root", str(caught.exception))
        self.assertFalse(self.local.exists())

    def test_a_folder_without_git_history_is_refused(self):
        plain = self.shares / "no-git"
        plain.mkdir()
        with self.assertRaises(PolicyError) as caught:
            self.runner.register_workspace(self.request(plain))
        self.assertIn("Git repository", str(caught.exception))
        self.assertFalse(self.local.exists())

    def test_a_repository_with_a_remote_is_refused(self):
        path = repo(self.shares / "remote-repo")
        subprocess.run(["git", "remote", "add", "origin", "https://example.invalid/x"],
                       cwd=path, check=True, capture_output=True)
        with self.assertRaises(PolicyError) as caught:
            self.runner.register_workspace(self.request(path))
        self.assertIn("local-only", str(caught.exception))
        self.assertFalse(self.local.exists())

    def test_a_duplicate_id_is_refused(self):
        path = repo(self.shares / "dup")
        self.runner.register_workspace(self.request(path))
        with self.assertRaises(PolicyError) as caught:
            self.runner.register_workspace(self.request(path))
        self.assertIn("already exists", str(caught.exception))
        entries = json.loads(self.local.read_text(encoding="utf-8"))["workspaces"]
        self.assertEqual(len(entries), 1, "the second attempt wrote nothing")

    def test_a_bad_id_is_refused(self):
        path = repo(self.shares / "bad")
        for bad in ("ab", "Foundry", "foundry repo", "x" * 41, ""):
            with self.assertRaises(PolicyError) as caught:
                self.runner.register_workspace(self.request(path, id=bad))
            self.assertIn("id", str(caught.exception))
        self.assertFalse(self.local.exists())

    def test_a_command_that_is_not_a_template_is_refused(self):
        path = repo(self.shares / "cmds")
        for name, argv in (("shell", ["bash", "-c", "ls"]),
                           ("pip", ["pip", "install", "thing"]),
                           ("git-push", ["git", "push"]),
                           ("discover-all", ["python", "-m", "unittest", "discover"]),
                           ("run-any", ["python", "-c", "import os; os.system('ls')"]),
                           ("outside", ["python", "../other.py"])):
            with self.assertRaises(PolicyError) as caught:
                self.runner.register_workspace(dict(self.request(path), commands={name: argv}))
                self.fail(f"{name} should not be allowed")
            self.assertIn("not allowed", str(caught.exception))
        self.assertFalse(self.local.exists())

    def test_the_allowed_shapes_are_accepted(self):
        path = repo(self.shares / "ok")
        result = self.runner.register_workspace(dict(self.request(path), commands={
            "unit": ["env", "HAI_THINK_CAPS_PROBE=0", "python", "-m", "unittest",
                     "discover", "-s", "tests", "-t", "tests", "-v"],
            "compile": ["python", "-m", "compileall", "-q", "."],
            "one-file": ["python", "-W", "error::SyntaxWarning", "test_sample.py"],
        }))
        self.assertEqual(result["test_profiles"], ["compile", "one-file", "unit"])
        self.assertEqual(result["proposed_not_run"], ["compile", "one-file", "unit"])
        self.assertIn("not executed", result["note"], "the preview says these were checked, not run")

    def test_a_dry_run_answers_the_questions_without_writing(self):
        path = repo(self.shares / "dry")
        result = self.runner.register_workspace(self.request(path), dry_run=True)
        self.assertFalse(result["registered"])
        self.assertFalse(self.local.exists())
        self.assertNotIn("dry", [w["id"] for w in self.runner.workspace_list()])

    def test_a_git_untrusted_share_is_still_readable_by_git(self):
        """The runner runs as root over a folder owned by someone else: git must not refuse it."""
        path = repo(self.shares / "untrusted")
        subprocess.run(["chown", "-R", "12345:12345", str(path)], check=True, capture_output=True)
        self.runner.register_workspace(dict(self.request(path), id="untrusted"))
        status = self.runner.git_status("untrusted")
        self.assertEqual(status["returncode"], 0, status["stderr"])
        task = self.runner.task_start("untrusted", "job")
        self.runner.file_create(task["workspace"], "made.txt", content="x\n")
        checkpoint = self.runner.task_checkpoint(task["id"], note="from a git-untrusted share")
        self.assertTrue(checkpoint.get("git_commit"), "the checkpoint really committed")

    def test_two_tasks_can_be_open_on_one_registered_workspace_at_once(self):
        """The acceptance rule for a shared registration: nothing refuses the second job."""
        self.runner.register_workspace(dict(self.request(repo(self.shares / "many")), id="many"))
        first = self.runner.task_start("many", "first")
        second = self.runner.task_start("many", "second")
        active = [t["id"] for t in self.runner.task_list()["tasks"] if t["status"] == "active"]
        self.assertEqual(len(active), 2)
        self.assertTrue(first["worktree"] and second["worktree"])
        self.assertNotEqual(first["workspace"], second["workspace"])
        for task, folder in ((first, "job-one"), (second, "job-two")):
            self.runner.file_create(task["workspace"], folder + "/made.txt", content="x\n")
        self.runner.task_finish(first["id"], action="merge")
        self.runner.task_finish(second["id"], action="merge")
        self.assertTrue((self.shares / "many" / "job-one" / "made.txt").exists())
        self.assertTrue((self.shares / "many" / "job-two" / "made.txt").exists())

    def test_a_worktree_of_a_registered_workspace_is_trusted_by_git(self):
        self.runner.register_workspace(dict(self.request(repo(self.shares / "wtrepo")), id="wtrepo"))
        task = self.runner.task_start("wtrepo", "job")
        status = self.runner.git_status(task["workspace"])
        self.assertEqual(status["returncode"], 0, status["stderr"])
        self.runner.file_create(task["workspace"], "made.txt", content="x\n")
        diff = self.runner.git_diff(task["workspace"])
        self.assertIn("made.txt", diff["stdout"], "the diff the reviewer sees is real")

    def test_the_baked_registry_marks_hai_own_code_shared(self):
        """Several jobs can work on the orchestrator at once; each gets its own worktree and a
        merge combines with whatever landed since the job started. The deploy gate still runs
        before any of it reaches production."""
        baked = json.loads((Path(__file__).resolve().parent.parent / "registry.json")
                           .read_text(encoding="utf-8"))
        entry = next(w for w in baked["workspaces"] if w["id"] == "orchestrator-real")
        self.assertTrue(entry.get("shared"), "orchestrator-real takes more than one job at a time")

    def test_a_registered_workspace_asked_not_to_share_still_takes_one_job_at_a_time(self):
        """shared=False is honoured: the second job on the same repository is refused, its work intact."""
        self.runner.register_workspace(dict(self.request(repo(self.shares / "strict")),
                                           id="strict", shared=False))
        first = self.runner.task_start("strict", "job one")
        with self.assertRaises(ConflictError):
            self.runner.task_start("strict", "job two")
        self.assertIsNone(first["worktree"], "a non-shared workspace works in its own tree, one job at a time")


if __name__ == "__main__":
    unittest.main()
