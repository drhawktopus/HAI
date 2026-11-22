import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from runner import CodingRunner, ConflictError, PolicyError


class CodingRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "maths.py").write_text(
            "def add(a, b):\n    return a + b\n",
            encoding="utf-8",
        )
        (self.workspace / ".env").write_text("SECRET=nope\n", encoding="utf-8")
        (self.workspace / "tests").mkdir()
        (self.workspace / "tests" / "test_smoke.py").write_text(
            "import unittest\n\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self._git("init")
        self._git("config", "user.email", "runner@example.invalid")
        self._git("config", "user.name", "Runner Test")
        self._git("add", ".")
        self._git("commit", "-m", "fixture")
        registry = {
            "workspaces": [
                {
                    "id": "fixture",
                    "path": str(self.workspace),
                    "description": "test fixture",
                    "read": True,
                    "write": True,
                    "git": True,
                    "protected_paths": [".env", "secrets"],
                    "ignored_paths": [".git", "__pycache__"],
                    "commands": {
                        "unit": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
                        "git_transport_probe": ["git", "ls-remote", self.workspace.as_uri()],
                    },
                }
            ]
        }
        self.registry = self.base / "registry.json"
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        self.runner = CodingRunner(self.registry, self.base / "state")

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.workspace), *args],
            check=True,
            capture_output=True,
        )

    def test_registry_rejects_git_remotes(self):
        self._git("remote", "add", "origin", self.workspace.as_uri())
        with self.assertRaisesRegex(PolicyError, "must remain local-only"):
            CodingRunner(self.registry, self.base / "other-state")

    def test_test_commands_cannot_use_git_transport(self):
        result = self.runner.test_run("fixture", "git_transport_probe")
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("transport", result["stderr"].lower())

    def test_checkpoint_commits_and_restores(self):
        task = self.runner.task_start("fixture", "checkpoint+restore")
        tid = task["id"]
        self.runner.file_create("fixture", "src/extra.py", content="X = 1\n")
        cp = self.runner.task_checkpoint(tid, note="first")
        sha = cp["git_commit"]
        self.assertTrue(sha and len(sha) >= 7)

        # Make changes AFTER the checkpoint: a new untracked file + an edit to a tracked file.
        self.runner.file_create("fixture", "src/oops.py", content="bad = True\n")
        digest = self.runner.file_read("fixture", "src/maths.py")["sha256"]
        self.runner.apply_patch(
            "fixture", "src/maths.py", old="return a + b", new="return a - b", expected_sha256=digest
        )

        res = self.runner.task_restore(tid, cp["id"])
        self.assertEqual(res["restored_commit"], sha)
        # File that existed at checkpoint time is back; post-checkpoint file is gone; edit reverted.
        self.assertTrue((self.workspace / "src" / "extra.py").exists())
        self.assertFalse((self.workspace / "src" / "oops.py").exists())
        self.assertIn("return a + b", (self.workspace / "src" / "maths.py").read_text())
        # Protected path is preserved through the restore's clean step.
        self.assertTrue((self.workspace / ".env").exists())

    def test_restore_rejects_unknown_checkpoint(self):
        task = self.runner.task_start("fixture", "x")
        with self.assertRaises(PolicyError):
            self.runner.task_restore(task["id"], "0" * 32)

    def test_worktree_isolation_and_discard(self):
        task = self.runner.task_start("fixture", "isolated", isolate=True)
        wt = task["worktree"]
        self.assertTrue(wt)
        wtws = wt["workspace"]
        self.assertIn(wtws, self.runner.workspaces)
        # A file created in the worktree is invisible to the base workspace.
        self.runner.file_create(wtws, "isolated_only.py", content="ISO = 1\n")
        self.assertNotIn("isolated_only.py", json.dumps(self.runner.file_list("fixture")))
        self.assertIn("isolated_only.py", json.dumps(self.runner.file_list(wtws)))
        res = self.runner.task_finish(task["id"], action="discard")
        self.assertEqual(res["action"], "discard")
        self.assertFalse(res["merged"])
        self.assertNotIn(wtws, self.runner.workspaces)
        # Discarded work never reached the base tree.
        self.assertFalse((self.workspace / "isolated_only.py").exists())

    def test_abandon_cascades_escalation_without_reactivating(self):
        # Every terminal path closes the task's linked escalation packet; a packet left "ready" would be re-sent by the
        # desk forever. Closing it must NOT reactivate the task (the escalation_packet_resolve human-return path would).
        tid = self.runner.task_start("fixture", "blocked then abandoned")["id"]
        pid = self.runner.escalation_packet_create(tid, "capability_limit", "need a human")["id"]
        self.assertEqual(self.runner._read_task(tid).get("blocked_by_escalation"), pid)
        self.runner.task_abandon(tid)
        after = self.runner.escalation_packet_get(pid)
        self.assertEqual(after["status"], "cancelled")     # no longer "ready" -> no more DMs
        self.assertEqual(after["outcome"], "task_ended")
        t = self.runner._read_task(tid)
        self.assertEqual(t["status"], "cancelled")         # NOT reactivated to "active"
        self.assertNotIn("blocked_by_escalation", t)

    def test_a_restart_gives_a_live_task_its_worktree_back(self):
        # A runner restart re-registers every unfinished task's worktree, so a call naming it never answers
        # "unknown workspace" and a resumed job never rebuilds its files in the base workspace.
        task = self.runner.task_start("fixture", "isolated, then a restart", isolate=True)
        wtws = task["worktree"]["workspace"]
        self.runner.file_create(wtws, "work_so_far.py", content="DONE = 1\n")
        done = self.runner.task_start("fixture", "isolated, finished before the restart", isolate=True)
        self.runner.task_finish(done["id"], action="discard")
        (self.runner.tasks_dir / "broken.json").write_text("{not json", encoding="utf-8")
        restarted = CodingRunner(self.registry, self.base / "state")           # the new process
        self.assertIn(wtws, restarted.workspaces, "the live task's worktree is registered again at startup")
        self.assertIn("work_so_far.py", json.dumps(restarted.file_list(wtws)))
        self.assertNotIn(done["worktree"]["workspace"], restarted.workspaces, "a finished worktree stays gone")

    def test_worktree_discard_cascades_escalation(self):
        tid = self.runner.task_start("fixture", "iso blocked", isolate=True)["id"]
        pid = self.runner.escalation_packet_create(tid, "repeated_failure", "help")["id"]
        self.runner.task_finish(tid, action="discard")
        self.assertEqual(self.runner.escalation_packet_get(pid)["status"], "cancelled")
        self.assertNotIn("blocked_by_escalation", self.runner._read_task(tid))

    def test_task_update_terminal_cascades_but_nonterminal_does_not(self):
        tid = self.runner.task_start("fixture", "blocked then completed")["id"]
        pid = self.runner.escalation_packet_create(tid, "external_research", "help")["id"]
        self.runner.task_update(tid, status="complete")
        self.assertEqual(self.runner.escalation_packet_get(pid)["status"], "cancelled")
        # a NON-terminal status change must leave the packet open
        tid2 = self.runner.task_start("fixture", "still blocked")["id"]
        pid2 = self.runner.escalation_packet_create(tid2, "capability_limit", "help")["id"]
        self.runner.task_update(tid2, status="paused")
        self.assertEqual(self.runner.escalation_packet_get(pid2)["status"], "ready")

    def test_abandon_without_escalation_is_a_clean_noop(self):
        # The cascade must not disturb the normal abandon path when there is no packet.
        tid = self.runner.task_start("fixture", "no escalation")["id"]
        self.assertEqual(self.runner.task_abandon(tid)["status"], "cancelled")

    def test_discard_preserves_uncommitted_work(self):
        # A non-converged leaf's uncommitted work must survive `git branch -D` on discard: a
        # checkpoint captures it to coding-state (outside the branch) so a reviewer can port it.
        task = self.runner.task_start("fixture", "iso preserve", isolate=True)
        tid = task["id"]
        wtws = task["worktree"]["workspace"]
        self.runner.file_create(wtws, "salvage_me.py", content="SALVAGE = 42\n")
        res = self.runner.task_finish(tid, action="discard")
        cpid = res["preserved_checkpoint"]
        self.assertTrue(cpid, "discard with work must record a preservation checkpoint")
        # The work never reached base and the worktree is gone...
        self.assertFalse((self.workspace / "salvage_me.py").exists())
        self.assertNotIn(wtws, self.runner.workspaces)
        # ...but it is recoverable from the durable checkpoint diff in coding-state.
        cp = json.loads((self.runner.checkpoints_dir / tid / f"{cpid}.json").read_text(encoding="utf-8"))
        self.assertIn("salvage_me.py", cp["git_diff"]["stdout"])
        self.assertIn("+SALVAGE = 42", cp["git_diff"]["stdout"])

    def test_discard_with_no_work_preserves_nothing(self):
        # A clean discard (no work done) must NOT litter an empty checkpoint.
        task = self.runner.task_start("fixture", "iso empty", isolate=True)
        tid = task["id"]
        res = self.runner.task_finish(tid, action="discard")
        self.assertIsNone(res["preserved_checkpoint"])
        self.assertFalse((self.runner.checkpoints_dir / tid).exists())

    def test_abandon_preserves_work_and_reports_checkpoint(self):
        task = self.runner.task_start("fixture", "iso abandon", isolate=True)
        tid = task["id"]
        wtws = task["worktree"]["workspace"]
        self.runner.file_create(wtws, "abandoned_work.py", content="KEEP = 1\n")
        res = self.runner.task_abandon(tid)
        self.assertEqual(res["status"], "cancelled")
        cpid = res["preserved_checkpoint"]
        self.assertTrue(cpid)
        cp = json.loads((self.runner.checkpoints_dir / tid / f"{cpid}.json").read_text(encoding="utf-8"))
        self.assertIn("+KEEP = 1", cp["git_diff"]["stdout"])

    def test_shared_workspace_abandon_preserves_work_before_the_reset(self):
        # A NON-isolated task's abandon hard-resets the shared workspace - its uncommitted work must be saved first, kept
        # in its own marked section, never deleted.
        tid = self.runner.task_start("fixture", "shared abandon")["id"]
        self.runner.file_create("fixture", "unverified_work.py", content="KEEP_ME = 7\n")
        res = self.runner.task_abandon(tid)
        cpid = res["preserved_checkpoint"]
        self.assertTrue(cpid, "a shared-workspace abandon with work must record a preservation checkpoint")
        self.assertFalse((self.workspace / "unverified_work.py").exists(), "the workspace is back to baseline")
        cp = json.loads((self.runner.checkpoints_dir / tid / f"{cpid}.json").read_text(encoding="utf-8"))
        self.assertIn("+KEEP_ME = 7", cp["git_diff"]["stdout"])
        restored = self.runner.task_restore(tid, cpid)          # "we may want to come back to it"
        self.assertTrue(restored["restored_commit"])
        self.assertTrue((self.workspace / "unverified_work.py").exists())
        self.assertEqual(len(list((self.runner.checkpoints_dir / tid).glob("*.json"))), 1,
                         "a restore FORWARD to the kept work loses nothing, so it saves nothing")

    def test_a_restore_back_to_a_checkpoint_keeps_the_work_done_since(self):
        tid = self.runner.task_start("fixture", "restore back")["id"]
        self.runner.file_create("fixture", "first.py", content="A = 1\n")
        first = self.runner.task_checkpoint(tid, note="first")["id"]
        self.runner.file_create("fixture", "later.py", content="LATER = 2\n")
        self.runner.task_restore(tid, first)
        self.assertFalse((self.workspace / "later.py").exists())
        notes = [json.loads(p.read_text(encoding="utf-8")) for p in (self.runner.checkpoints_dir / tid).glob("*.json")]
        kept = [c for c in notes if "auto-preserved before a restore" in c["note"]]
        self.assertEqual(len(kept), 1)
        self.assertIn("+LATER = 2", kept[0]["git_diff"]["stdout"])

    def test_the_automatic_baseline_restore_keeps_the_work_first(self):
        tid = self.runner.task_start("fixture", "broad failure")["id"]
        self.runner.file_create("fixture", "half_done.py", content="HALF = 1\n")
        broken = {"returncode": 1, "stdout": "", "stderr": "ModuleNotFoundError: x", "timed_out": False}
        with patch.object(self.runner, "test_run", return_value=dict(broken)):
            self.runner.test_run_recover("fixture", "unit", tid)
            res = self.runner.test_run_recover("fixture", "unit", tid)
        self.assertEqual(res["recovery"]["action"], "restored_baseline")
        self.assertFalse((self.workspace / "half_done.py").exists())
        cpid = res["recovery"]["preserved_checkpoint"]
        self.assertTrue(cpid, "the work is saved before the automatic reset")
        cp = json.loads((self.runner.checkpoints_dir / tid / f"{cpid}.json").read_text(encoding="utf-8"))
        self.assertIn("+HALF = 1", cp["git_diff"]["stdout"])

    def _recovery_notes(self, reply, action):
        # A plain-string note must not break coding_review, which reads notes as dicts.
        tid = self.runner.task_start("fixture", "recovery note")["id"]
        with patch.object(self.runner, "test_run", return_value=dict(reply)):
            self.runner.test_run_recover("fixture", "unit", tid)
            res = self.runner.test_run_recover("fixture", "unit", tid)
        self.assertEqual(res["recovery"]["action"], action)
        notes = self.runner.task_get(tid)["notes"]
        self.assertTrue(notes, "the recovery must leave a note")
        self.assertTrue(all(isinstance(n, dict) and n.get("text") for n in notes), notes)

    def test_a_restored_baseline_note_is_the_one_note_shape(self):
        self._recovery_notes({"returncode": 1, "stdout": "", "stderr": "ModuleNotFoundError: x", "timed_out": False}, "restored_baseline")

    def test_a_blocked_retry_note_is_the_one_note_shape(self):
        self._recovery_notes({"returncode": 1, "stdout": "FAIL: test_x\nAssertionError: 1 != 2", "stderr": "", "timed_out": False},
                             "blocked_repeated_failure")

    def test_shared_abandon_already_checkpointed_is_not_saved_twice(self):
        tid = self.runner.task_start("fixture", "marked first")["id"]
        self.runner.file_create("fixture", "marked.py", content="M = 1\n")
        self.runner.task_checkpoint(tid, note="UNVERIFIED: marked by the caller")
        res = self.runner.task_abandon(tid)
        self.assertIsNone(res["preserved_checkpoint"], "the caller's marked checkpoint already holds this commit")
        self.assertEqual(len(list((self.runner.checkpoints_dir / tid).glob("*.json"))), 1)

    def test_shared_abandon_with_no_work_saves_nothing(self):
        tid = self.runner.task_start("fixture", "shared empty")["id"]
        self.assertIsNone(self.runner.task_abandon(tid)["preserved_checkpoint"])
        self.assertFalse((self.runner.checkpoints_dir / tid).exists())

    def test_merge_does_not_create_preservation_checkpoint(self):
        # Merge already lands the work in base, so no discard-preservation checkpoint is taken.
        task = self.runner.task_start("fixture", "iso merge nopreserve", isolate=True)
        tid = task["id"]
        wtws = task["worktree"]["workspace"]
        self.runner.file_create(wtws, "merged.py", content="M = 1\n")
        self.runner.task_checkpoint(tid, note="iso work")   # a commit so the ff-merge has something
        res = self.runner.task_finish(tid, action="merge")
        self.assertTrue(res["merged"])
        self.assertIsNone(res["preserved_checkpoint"])

    def test_a_merge_carries_work_that_was_never_committed(self):
        # A merge fast-forwards COMMITS, then the worktree is force-removed: uncommitted work is checkpointed first, or
        # nothing is merged and nothing removed.
        task = self.runner.task_start("fixture", "iso merge, never committed", isolate=True)
        tid, wtws = task["id"], task["worktree"]["workspace"]
        self.runner.file_create(wtws, "never_committed.py", content="N = 1\n")
        res = self.runner.task_finish(tid, action="merge")
        self.assertTrue(res["merged"])
        self.assertTrue((self.workspace / "never_committed.py").exists(), "the work is in the base after the merge")

    def test_a_merge_carries_work_in_an_ignored_folder(self):
        """A folder hidden from git by `.git/info/exclude` (shared by every worktree) still ships: a checkpoint that
        commits nothing and a merge that says merged must never remove the worktree with its work."""
        exclude = self.workspace / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with open(exclude, "a", encoding="utf-8") as f:
            f.write("\nbox2/\n")
        task = self.runner.task_start("fixture", "iso merge, ignored folder", isolate=True)
        tid, wtws = task["id"], task["worktree"]["workspace"]
        self.runner.file_create(wtws, "box2/job/generate.py", content="G = 1\n")
        wt_root = self.runner.workspaces[wtws].root
        (wt_root / "box2" / "job" / "part.stl").write_bytes(b"solid x\nendsolid x\n")     # what a test writes
        (wt_root / "box2" / "job" / "__pycache__").mkdir()
        (wt_root / "box2" / "job" / "__pycache__" / "g.cpython-312.pyc").write_bytes(b"\0")
        res = self.runner.task_finish(tid, action="merge")
        self.assertTrue(res["merged"])
        self.assertEqual((self.workspace / "box2" / "job" / "generate.py").read_text(encoding="utf-8"), "G = 1\n")
        self.assertTrue((self.workspace / "box2" / "job" / "part.stl").exists(), "a file a test wrote is carried too")
        self.assertFalse((self.workspace / "box2" / "job" / "__pycache__").exists(), "caches are not work")
        tracked = subprocess.run(["git", "ls-files", "box2"], cwd=self.workspace, capture_output=True, text=True).stdout
        self.assertEqual(tracked.strip(), "", "carried, not committed: what git keeps out of history stays out")

    def test_a_merge_that_cannot_save_the_work_removes_nothing(self):
        task = self.runner.task_start("fixture", "iso merge, save fails", isolate=True)
        tid, wtws = task["id"], task["worktree"]["workspace"]
        self.runner.file_create(wtws, "still_here.py", content="S = 1\n")
        with patch.object(self.runner, "task_checkpoint", side_effect=RuntimeError("boom")):
            with self.assertRaises(ConflictError):
                self.runner.task_finish(tid, action="merge")
        self.assertIn(wtws, self.runner.workspaces)
        self.assertIn("still_here.py", json.dumps(self.runner.file_list(wtws)))
        self.assertFalse(self.runner.task_get(tid).get("worktree_finished"))

    # In a worktree whose base sits on a share owned by another user, git refuses every command ("dubious
    # ownership") and answers nothing. Nothing may read as clean: a checkpoint that committed nothing, a merge that
    # moved nothing, and a removal after either would each lose the job's files.
    def _git_refuses_in(self, root, only=None):
        """Patch the runner so git answers like the live failure for commands run in `root` (optionally only `only`)."""
        real = self.runner._run

        def run(argv, cwd, *, timeout):
            if argv[:1] == ["git"] and str(root) in argv and (only is None or only in argv):
                return {"argv": argv, "returncode": 128, "timed_out": False, "stdout": "",
                        "stderr": "fatal: detected dubious ownership in repository at '%s'\n" % root,
                        "stdout_truncated": False, "stderr_truncated": False}
            return real(argv, cwd, timeout=timeout)
        return patch.object(self.runner, "_run", run)

    def test_git_is_told_a_tasks_worktree_has_its_bases_trust(self):
        task = self.runner.task_start("fixture", "iso trust", isolate=True)
        wt = self.runner.workspaces[task["worktree"]["workspace"]]
        seen = []
        real = self.runner._run

        def run(argv, cwd, *, timeout):
            seen.append(argv)
            return real(argv, cwd, timeout=timeout)
        with patch.object(self.runner, "_run", run):
            self.runner._git(wt, ["status", "--porcelain"])
            self.runner._git(self.runner.workspaces["fixture"], ["status", "--porcelain"])
        self.assertEqual(seen[0][:4], ["git", "-c", "safe.directory=%s" % wt.root, "-C"])
        self.assertEqual(seen[1][:2], ["git", "-C"], "a base workspace keeps the operator's own allow-list")

    def test_git_that_cannot_work_in_a_repository_is_an_error_not_an_empty_answer(self):
        task = self.runner.task_start("fixture", "iso refused", isolate=True)
        wt = self.runner.workspaces[task["worktree"]["workspace"]]
        with self._git_refuses_in(wt.root):
            with self.assertRaises(ConflictError) as ctx:
                self.runner._git(wt, ["status", "--porcelain"])
        self.assertIn("dubious ownership", str(ctx.exception))
        missing = self.runner._git(wt, ["cat-file", "-t", "0" * 40])
        self.assertNotEqual(missing["returncode"], 0, "an ordinary 'no such object' is still an answer, not an error")

    def test_a_merge_removes_nothing_when_git_cannot_say_what_the_worktree_holds(self):
        task = self.runner.task_start("fixture", "iso merge, git silent", isolate=True)
        tid, wtws = task["id"], task["worktree"]["workspace"]
        self.runner.file_create(wtws, "would_be_lost.py", content="L = 1\n")
        with self._git_refuses_in(self.runner.workspaces[wtws].root):
            with self.assertRaises(ConflictError) as ctx:
                self.runner.task_finish(tid, action="merge")
        self.assertIn("nothing was merged or removed", str(ctx.exception))
        self.assertIn("would_be_lost.py", json.dumps(self.runner.file_list(wtws)))
        self.assertFalse(self.runner.task_get(tid).get("worktree_finished"))
        self.assertTrue(self.runner.task_finish(tid, action="merge")["merged"], "and it merges once git can answer")
        self.assertTrue((self.workspace / "would_be_lost.py").exists())

    def test_a_status_git_could_not_give_is_never_read_as_clean(self):
        task = self.runner.task_start("fixture", "iso status fails", isolate=True)
        wt = self.runner.workspaces[task["worktree"]["workspace"]]
        real = self.runner._run

        def run(argv, cwd, *, timeout):
            if "status" in argv:
                return {"argv": argv, "returncode": 1, "timed_out": False, "stdout": "", "stderr": "error: boom\n",
                        "stdout_truncated": False, "stderr_truncated": False}
            return real(argv, cwd, timeout=timeout)
        with patch.object(self.runner, "_run", run), self.assertRaises(ConflictError):
            self.runner._uncommitted(wt)

    def test_a_checkpoint_git_could_not_commit_says_so(self):
        task = self.runner.task_start("fixture", "iso checkpoint fails", isolate=True)
        tid, wtws = task["id"], task["worktree"]["workspace"]
        self.runner.file_create(wtws, "work.py", content="W = 1\n")
        with self._git_refuses_in(self.runner.workspaces[wtws].root, only="commit"):
            with self.assertRaises(ConflictError):
                self.runner.task_checkpoint(tid, note="cannot commit")
        self.assertTrue(self.runner.task_checkpoint(tid, note="can commit")["git_commit"])

    def test_a_merge_keeps_the_worktree_when_the_base_does_not_hold_its_last_commit(self):
        task = self.runner.task_start("fixture", "iso merge, base behind", isolate=True)
        tid, wtws = task["id"], task["worktree"]["workspace"]
        self.runner.file_create(wtws, "kept.py", content="K = 1\n")
        real = self.runner._git

        def git(workspace, args, timeout=15):
            if args[:2] == ["merge-base", "--is-ancestor"] and workspace.id == "fixture":
                return {"returncode": 1, "stdout": "", "stderr": ""}
            return real(workspace, args, timeout=timeout)
        with patch.object(self.runner, "_git", git), self.assertRaises(ConflictError) as ctx:
            self.runner.task_finish(tid, action="merge")
        self.assertIn("was not removed", str(ctx.exception))
        self.assertIn(wtws, self.runner.workspaces)
        self.assertIn("kept.py", json.dumps(self.runner.file_list(wtws)))

    def test_preservation_failure_never_breaks_discard(self):
        # Preservation is best-effort: if the checkpoint step fails, discard must STILL complete —
        # a stuck task must never become un-abandonable because salvage failed.
        task = self.runner.task_start("fixture", "iso preserve fails", isolate=True)
        tid = task["id"]
        wtws = task["worktree"]["workspace"]
        self.runner.file_create(wtws, "wip.py", content="WIP = 1\n")
        with patch.object(self.runner, "task_checkpoint", side_effect=RuntimeError("boom")):
            res = self.runner.task_finish(tid, action="discard")
        self.assertEqual(res["action"], "discard")
        self.assertIsNone(res["preserved_checkpoint"])
        self.assertNotIn(wtws, self.runner.workspaces)   # discard still tore the worktree down

    def test_worktree_merge_brings_work_into_base(self):
        task = self.runner.task_start("fixture", "isolated-merge", isolate=True)
        tid = task["id"]
        wtws = task["worktree"]["workspace"]
        self.runner.file_create(wtws, "merged_in.py", content="M = 1\n")
        self.runner.task_checkpoint(tid, note="iso work")  # commits on the task branch
        res = self.runner.task_finish(tid, action="merge")
        self.assertTrue(res["merged"])
        self.assertTrue((self.workspace / "merged_in.py").exists())

    def test_task_branch_diff_uses_metadata_after_checkpoint_commit(self):
        task = self.runner.task_start("fixture", "isolated diff", isolate=True)
        tid = task["id"]
        wtws = task["worktree"]["workspace"]
        self.runner.file_create(wtws, "review_me.py", content="VALUE = 42\n")
        self.runner.task_checkpoint(tid, note="commit isolated work")
        self.assertNotIn("review_me.py", self.runner.git_diff(wtws)["stdout"])

        diff = self.runner.task_branch_diff(tid)
        self.assertEqual(diff["diff_source"], "task_branch")
        self.assertEqual(diff["base_workspace"], "fixture")
        self.assertEqual(diff["branch"], f"task/{tid}")
        self.assertIn("review_me.py", diff["stdout"])
        self.assertIn("+VALUE = 42", diff["stdout"])
        self.assertEqual(diff["diff"], diff["stdout"])   # diff text also under "diff" for the orchestrator

    def test_task_branch_diff_non_isolated_uses_baseline(self):
        # A NON-isolated task commits STRAIGHT INTO the workspace, so a plain working-tree diff is empty
        # after the checkpoint -- the task diff must still surface its work (baseline..working) so the
        # overnight adversarial reviewer isn't blind. (Regression: this used to raise -> empty diff.)
        task = self.runner.task_start("fixture", "plain task")   # isolate defaults to False
        tid = task["id"]
        self.runner.file_create("fixture", "src/new_feature.py", content="FEATURE = 99\n")
        self.runner.task_checkpoint(tid, note="commit non-isolated work")   # commits into the workspace
        self.assertEqual(self.runner.git_diff("fixture")["stdout"].strip(), "")   # working tree now clean
        diff = self.runner.task_branch_diff(tid)
        self.assertEqual(diff["diff_source"], "task_baseline")
        self.assertEqual(diff["base_workspace"], "fixture")
        self.assertEqual(diff["baseline_commit"], task["baseline_commit"])
        self.assertIn("new_feature.py", diff["stdout"])
        self.assertIn("+FEATURE = 99", diff["stdout"])
        self.assertEqual(diff["diff"], diff["stdout"])

    def test_task_list_filters_finished(self):
        task = self.runner.task_start("fixture", "listable")
        tid = task["id"]
        ids = [t["id"] for t in self.runner.task_list()["tasks"]]
        self.assertIn(tid, ids)
        self.runner.task_update(tid, status="complete")
        self.assertNotIn(tid, [t["id"] for t in self.runner.task_list()["tasks"]])
        self.assertIn(tid, [t["id"] for t in self.runner.task_list(include_finished=True)["tasks"]])

    def test_pause_and_resume(self):
        task = self.runner.task_start("fixture", "resume me")
        tid = task["id"]
        self.runner.task_update(tid, plan=[
            {"step": "a", "status": "completed"},
            {"step": "b", "status": "pending"},
        ])
        self.runner.task_update(tid, status="paused")
        brief = self.runner.task_resume(tid)
        self.assertEqual(brief["task"]["status"], "active")
        self.assertEqual([s["step"] for s in brief["remaining_plan"]], ["b"])

    def test_resume_reattaches_worktree_after_restart(self):
        task = self.runner.task_start("fixture", "iso resume", isolate=True)
        tid = task["id"]
        wtws = task["worktree"]["workspace"]
        self.runner.file_create(wtws, "wip.py", content="W = 1\n")
        self.runner.task_checkpoint(tid, note="wip")
        # Simulate a runner restart dropping in-memory worktree registration.
        self.runner.workspaces.pop(wtws)
        self.assertNotIn(wtws, self.runner.workspaces)
        brief = self.runner.task_resume(tid)
        self.assertTrue(brief["worktree_reattached"])
        self.assertIn(wtws, self.runner.workspaces)
        self.assertIn("wip.py", json.dumps(self.runner.file_list(wtws)))
        self.assertTrue(brief["latest_checkpoint"]["git_commit"])

    def test_workspace_and_file_read(self):
        self.assertEqual(self.runner.workspace_list()[0]["id"], "fixture")
        result = self.runner.file_read("fixture", "src/maths.py")
        self.assertIn("return a + b", result["content"])
        self.assertEqual(len(result["sha256"]), 64)

    def test_project_manifest_detects_files_and_profiles(self):
        (self.workspace / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
        (self.workspace / "AGENTS.md").write_text("Test instructions\n", encoding="utf-8")
        result = self.runner.project_manifest("fixture")
        self.assertIn(result["git_branch"], {"master", "main"})
        self.assertEqual(result["detected_files"]["instructions"], ["AGENTS.md"])
        self.assertIn("pyproject.toml", result["detected_files"]["python"])
        self.assertEqual(result["test_profiles"]["unit"][0], "python")

    def test_missing_read_is_a_policy_error(self):
        with self.assertRaisesRegex(PolicyError, "path does not exist"):
            self.runner.file_read("fixture", "missing.py")

    @patch("runner.subprocess.run")
    def test_subprocess_environment_is_deterministic(self, run):
        run.return_value = subprocess.CompletedProcess(["python"], 0, b"", b"")
        self.runner._run(["python", "-V"], self.workspace, timeout=5)
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["EMBED_ENABLED"], "0")
        self.assertEqual(env["PYTHONPATH"], str(self.workspace))
    def test_file_list_excludes_git(self):
        paths = [item["path"] for item in self.runner.file_list("fixture")["entries"]]
        self.assertNotIn(".git", paths)
        self.assertIn("src/maths.py", paths)

    def test_search_returns_workspace_relative_path(self):
        result = self.runner.text_search("fixture", "return a")
        self.assertEqual(result["matches"][0]["path"], "src/maths.py")

    def test_rejects_traversal_absolute_and_colon_paths(self):
        for path in ("../outside.txt", "/etc/passwd", "C:/Windows/win.ini", "src/../.env"):
            with self.subTest(path=path), self.assertRaises(PolicyError):
                self.runner.file_read("fixture", path)

    def test_rejects_symlink_escape_when_supported(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        link = self.workspace / "escape.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks are unavailable")
        with self.assertRaises(PolicyError):
            self.runner.file_read("fixture", "escape.txt")

    def test_protected_path_cannot_be_edited(self):
        digest = self.runner.file_read("fixture", ".env")["sha256"]
        with self.assertRaises(PolicyError):
            self.runner.apply_patch(
                "fixture",
                ".env",
                old="SECRET=nope",
                new="SECRET=yes",
                expected_sha256=digest,
            )

    def test_patch_requires_current_hash_and_unique_old_text(self):
        read = self.runner.file_read("fixture", "src/maths.py")
        with self.assertRaises(ConflictError):
            self.runner.apply_patch(
                "fixture",
                "src/maths.py",
                old="return a + b",
                new="return a - b",
                expected_sha256="0" * 64,
            )
        result = self.runner.apply_patch(
            "fixture",
            "src/maths.py",
            old="return a + b",
            new="return int(a) + int(b)",
            expected_sha256=read["sha256"],
        )
        self.assertEqual(result["replacements"], 1)
        self.assertIn("int(a)", self.runner.file_read("fixture", "src/maths.py")["content"])

    def test_file_create_and_git_diff(self):
        self.runner.file_create("fixture", "src/new_module.py", content="VALUE = 1\n")
        status = self.runner.git_status("fixture")
        self.assertIn("src/new_module.py", status["stdout"])
        diff = self.runner.git_diff("fixture")
        self.assertIn("new file mode", diff["stdout"])
        self.assertIn("+VALUE = 1", diff["stdout"])
        self.assertEqual(diff["untracked_included"], ["src/new_module.py"])

    def test_a_tracked_folder_ships_out_and_git_records_it(self):
        ship = self.base / "shipped"
        ship.mkdir()
        out = self.runner.file_ship("fixture", "src", ship_root=str(ship))
        self.assertTrue(out["tracked"])
        self.assertEqual(out["files"], 1)
        self.assertFalse((self.workspace / "src").exists(), "the next job in this workspace never sees it")
        self.assertIn("return a + b", (Path(out["shipped_to"]) / "maths.py").read_text())
        log = subprocess.run(["git", "-C", str(self.workspace), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
        self.assertTrue(log.startswith("Ship src out of the workspace"), log)

    def test_an_untracked_job_folder_ships_out_too(self):
        ship = self.base / "shipped"
        ship.mkdir()
        job = self.workspace / "jobs" / "run-x"
        job.mkdir(parents=True)
        (job / "page.html").write_text("<p>x</p>", encoding="utf-8")
        out = self.runner.file_ship("fixture", "jobs/run-x", ship_root=str(ship))
        self.assertFalse(out["tracked"])
        self.assertFalse(job.exists())
        self.assertTrue((Path(out["shipped_to"]) / "page.html").is_file())

    def test_no_shipping_area_ships_nothing(self):
        with self.assertRaises(PolicyError):
            self.runner.file_ship("fixture", "src", ship_root=str(self.base / "not-mounted"))
        self.assertTrue((self.workspace / "src" / "maths.py").exists(), "refused before anything left")

    def test_a_protected_path_or_the_whole_workspace_never_ships(self):
        ship = self.base / "shipped"
        ship.mkdir()
        with self.assertRaises(PolicyError):
            self.runner.file_ship("fixture", ".env", ship_root=str(ship))
        with self.assertRaises(PolicyError):
            self.runner.file_ship("fixture", ".", ship_root=str(ship))

    def _isolated_shipping_fixture(self, chat="chat-a"):
        task = self.runner.task_start("fixture", "job artifact", isolate=True, owner="Alex", chat_id=chat)
        wt = Path(task["worktree"]["path"])
        folder = wt / "jobs/run-x"
        folder.mkdir(parents=True)
        (folder / "result.stl").write_bytes(b"actual generated artifact")
        (folder / "reference.png").write_bytes(b"reference")
        return task, folder

    def test_a_job_that_ended_inside_its_own_task_ships_that_work_labelled_and_leaves_nothing_behind(self):
        """A refused merge leaves the job's result in the task's worktree; that result is what ships, the base's older
        copy ships beside it, and the task is ended."""
        task, folder = self._isolated_shipping_fixture()
        base = self.workspace / "jobs/run-x"
        base.mkdir(parents=True)
        (base / "reference.png").write_bytes(b"untracked pre-task reference")
        with self.assertRaises(ConflictError):
            self.runner.task_finish(task["id"], action="merge")
        ship = self.base / "shipped";ship.mkdir()
        out = self.runner.job_ship("fixture", "jobs/run-x", owner="Alex", chat_id="chat-a", ship_root=str(ship))
        dest = Path(out["shipped_to"])
        self.assertIn("__unfinished-task-" + task["id"][:8], dest.name)
        self.assertTrue(out["unfinished_task"])
        self.assertEqual((out["files"], out["source_task_id"]), (2, task["id"]))
        self.assertEqual((dest / "result.stl").read_bytes(), b"actual generated artifact")
        self.assertEqual((Path(out["supporting_base_shipment"]) / "reference.png").read_bytes(), b"untracked pre-task reference")
        self.assertFalse(base.exists(), "the base's copy left the workspace too")
        self.assertFalse(folder.exists(), "the worktree is gone: the next blind job cannot read it")
        ended = self.runner.task_get(task["id"])
        self.assertEqual(ended["status"], "cancelled")
        self.assertTrue(ended.get("worktree_finished"))

    def test_job_shipping_does_not_take_another_chats_worktree(self):
        _task, folder = self._isolated_shipping_fixture(chat="other-chat")
        ship = self.base / "shipped";ship.mkdir()
        out = self.runner.job_ship("fixture", "src", owner="Alex", chat_id="chat-a", ship_root=str(ship))
        self.assertTrue((folder / "result.stl").exists())
        self.assertEqual(out["files"], 1)

    def test_job_shipping_refuses_ambiguous_worktrees_without_removing_anything(self):
        _a, folder_a = self._isolated_shipping_fixture()
        _b, folder_b = self._isolated_shipping_fixture()
        ship = self.base / "shipped";ship.mkdir()
        with self.assertRaisesRegex(ConflictError, "multiple"):
            self.runner.job_ship("fixture", "jobs/run-x", owner="Alex", chat_id="chat-a", ship_root=str(ship))
        self.assertTrue(folder_a.exists());self.assertTrue(folder_b.exists());self.assertEqual(list(ship.iterdir()), [])

    def test_unfinished_work_ships_with_no_base_folder_and_a_task_that_cannot_be_ended_is_said(self):
        task, folder = self._isolated_shipping_fixture()
        ship = self.base / "shipped";ship.mkdir()
        with patch.object(self.runner, "file_ship") as base_transport:
            out = self.runner.job_ship("fixture", "jobs/run-x", owner="Alex", chat_id="chat-a", ship_root=str(ship))
            base_transport.assert_not_called()
        self.assertIsNone(out["supporting_base_shipment"])
        self.assertTrue((Path(out["shipped_to"]) / "result.stl").exists())
        self.assertFalse(folder.exists())
        task2, folder2 = self._isolated_shipping_fixture()
        with patch.object(self.runner, "task_abandon", return_value={"preserved_checkpoint": None}):
            with self.assertRaisesRegex(ConflictError, "could not be removed"):
                self.runner.job_ship("fixture", "jobs/run-x", owner="Alex", chat_id="chat-a", ship_root=str(ship))
        self.assertTrue(folder2.exists(), "a task that could not be ended is left exactly as it was")

    def test_a_short_copy_of_unfinished_work_takes_nothing_out(self):
        import shutil
        task, folder = self._isolated_shipping_fixture()
        ship = self.base / "shipped";ship.mkdir()

        def short(src, dst, **kw):
            Path(dst).mkdir(parents=True)
            (Path(dst) / "result.stl").write_bytes(b"x")
        with patch.object(shutil, "copytree", short):
            with self.assertRaisesRegex(ConflictError, "short"):
                self.runner.job_ship("fixture", "jobs/run-x", owner="Alex", chat_id="chat-a", ship_root=str(ship))
        self.assertTrue((folder / "reference.png").exists())
        self.assertFalse(self.runner.task_get(task["id"]).get("worktree_finished"))

    def test_job_shipping_requires_complete_ownership_context(self):
        with self.assertRaises(PolicyError):
            self.runner.job_ship("fixture", "src", owner="Alex", chat_id="")
        self.assertTrue((self.workspace / "src/maths.py").exists())

    def test_file_move_requires_hash_and_preserves_content(self):
        read = self.runner.file_read("fixture", "src/maths.py")
        with self.assertRaises(ConflictError):
            self.runner.file_move(
                "fixture",
                "src/maths.py",
                "src/arithmetic.py",
                expected_sha256="0" * 64,
            )
        result = self.runner.file_move(
            "fixture",
            "src/maths.py",
            "src/arithmetic.py",
            expected_sha256=read["sha256"],
        )
        self.assertEqual(result["destination"], "src/arithmetic.py")
        self.assertFalse((self.workspace / "src" / "maths.py").exists())
        self.assertIn("return a + b", (self.workspace / "src" / "arithmetic.py").read_text())

    def test_file_move_rejects_collision(self):
        read = self.runner.file_read("fixture", "src/maths.py")
        (self.workspace / "src" / "existing.py").write_text("VALUE = 1\n", encoding="utf-8")
        with self.assertRaises(ConflictError):
            self.runner.file_move(
                "fixture",
                "src/maths.py",
                "src/existing.py",
                expected_sha256=read["sha256"],
            )

    def test_a_missing_hash_says_so_and_never_claims_the_file_changed(self):
        # A MISSING hash is not "file changed since it was read" (that answer sends the model re-reading in a loop)
        for call in (lambda: self.runner.apply_patch("fixture", "src/maths.py", old="a", new="b", expected_sha256=""),
                     lambda: self.runner.file_delete("fixture", "src/maths.py", expected_sha256="")):
            with self.assertRaisesRegex(ConflictError, "expected_sha256 is required") as cm:
                call()
            self.assertNotIn("changed", str(cm.exception))

    def test_file_delete_requires_hash_and_rejects_protected(self):
        read = self.runner.file_read("fixture", "src/maths.py")
        with self.assertRaises(ConflictError):
            self.runner.file_delete("fixture", "src/maths.py", expected_sha256="0" * 64)
        result = self.runner.file_delete(
            "fixture",
            "src/maths.py",
            expected_sha256=read["sha256"],
        )
        self.assertTrue(result["deleted"])
        self.assertFalse((self.workspace / "src" / "maths.py").exists())
        protected_hash = self.runner.file_read("fixture", ".env")["sha256"]
        with self.assertRaises(PolicyError):
            self.runner.file_delete("fixture", ".env", expected_sha256=protected_hash)

    def test_git_diff_does_not_expose_protected_untracked_files(self):
        protected = self.workspace / "secrets"
        protected.mkdir()
        (protected / "token.txt").write_text("do-not-show\n", encoding="utf-8")
        diff = self.runner.git_diff("fixture")
        self.assertNotIn("do-not-show", diff["stdout"])
        self.assertEqual(diff["untracked_skipped"], ["secrets/token.txt"])

    def test_only_registered_test_profiles_run(self):
        with self.assertRaises(PolicyError):
            self.runner.test_run("fixture", "shell")
        result = self.runner.test_run("fixture", "unit")
        self.assertEqual(result["returncode"], 0)
        self.assertFalse(result["timed_out"])

    def test_a_profile_runs_from_one_job_folder(self):
        # A shared scratch workspace is one folder per job; a unit run from the root would collect every job's
        # tests (a finished job's Chromium renders) and outlast the caller's wait. `path` scopes it.
        other = self.workspace / "jobs" / "other"
        mine = self.workspace / "jobs" / "mine"
        for d in (other, mine):
            (d / "tests").mkdir(parents=True)
        (other / "tests" / "test_broken.py").write_text("raise SystemExit('collected the wrong job')\n", encoding="utf-8")
        (mine / "tests" / "test_mine.py").write_text(
            "import unittest\n\nclass Mine(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8")
        result = self.runner.test_run("fixture", "unit", "jobs/mine")
        self.assertEqual(result["returncode"], 0, result["stderr"])
        self.assertIn("test_ok", result["stderr"])
        self.assertEqual(result["path"], "jobs/mine")
        self.assertEqual(result["timeout_seconds"], self.runner.test_run_timeout_seconds)
        root = self.runner.test_run("fixture", "unit")           # no path = the root, exactly as before
        self.assertEqual(root["returncode"], 0)
        self.assertNotIn("path", root)

    def test_a_test_path_cannot_leave_the_workspace_or_be_a_file(self):
        for bad in ("../", "/etc", "jobs/../..", "src/maths.py", "missing"):
            with self.assertRaises(PolicyError, msg=bad):
                self.runner.test_run("fixture", "unit", bad)

    RUNAWAY = "def grow():\n    out = []\n    while True:\n        out.append('x' * 4096)\n\ngrow()\n"

    def test_a_runaway_gets_the_line_it_is_on_not_just_a_death(self):
        # returncode -9 alone reads as the time limit; the reply must say when the memory limit ended the run.
        self.runner.test_poll_seconds = 0.2
        got = self.runner._run(["python", "-u", "-c", self.RUNAWAY], self.workspace, timeout=120, memory_gb=0.2)
        self.assertIn("memory", got["ended_because"])
        self.assertFalse(got["timed_out"], "the time limit did not end this, and the reply must not say it did")
        self.assertIn("grow", got["stderr"], "the stack dump names the function")
        self.assertIsNone(got["returncode"])

    def test_the_system_killing_a_run_is_named_for_what_it_is(self):
        self.runner.test_poll_seconds = 0.1
        got = self.runner._run(["python", "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"], self.workspace, timeout=30, memory_gb=8)
        self.assertEqual(got["returncode"], -9)
        self.assertFalse(got["timed_out"])
        self.assertIn("NOT the time limit", got["ended_because"])

    def test_a_normal_run_under_the_limit_is_untouched(self):
        self.runner.test_poll_seconds = 0.1
        got = self.runner._run(["python", "-c", "print('built')"], self.workspace, timeout=30, memory_gb=8)
        self.assertEqual(got["returncode"], 0, got)
        self.assertIn("built", got["stdout"])
        self.assertNotIn("ended_because", got)

    def test_the_time_limit_still_ends_a_run_and_says_so(self):
        self.runner.test_poll_seconds = 0.1
        got = self.runner._run(["python", "-u", "-c", "import time\nprint('started')\ntime.sleep(60)"], self.workspace, timeout=1, memory_gb=8)
        self.assertTrue(got["timed_out"])
        self.assertIn("timed out", got["ended_because"])
        self.assertIn("started", got["stdout"], "the output so far comes back")

    def test_a_leftover_child_cannot_hang_the_run_and_is_cleaned_up(self):
        self.runner.test_poll_seconds = 0.1
        code = "import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\nprint('parent done')"
        got = self.runner._run(["python", "-c", code], self.workspace, timeout=30, memory_gb=8)
        self.assertEqual(got["returncode"], 0, got)
        self.assertIn("parent done", got["stdout"])

    def test_command_timeout_is_reported(self):
        result = self.runner._run(
            ["python", "-c", "import time; time.sleep(1)"],
            self.workspace,
            timeout=0.01,
        )
        self.assertTrue(result["timed_out"])

    def test_task_state_is_durable(self):
        task = self.runner.task_start(
            "fixture",
            "Improve addition",
            acceptance_criteria=["Tests pass"],
        )
        updated = self.runner.task_update(
            task["id"],
            plan=[{"step": "Inspect", "status": "in_progress"}],
            note="Started",
        )
        self.assertEqual(updated["plan"][0]["status"], "in_progress")
        reloaded = CodingRunner(self.registry, self.base / "state")
        self.assertEqual(reloaded.task_get(task["id"])["notes"][0]["text"], "Started")

    def test_task_get_includes_progress_summary(self):
        task = self.runner.task_start("fixture", "Make progress obvious")
        updated = self.runner.task_update(
            task["id"],
            plan=[
                {"step": "Inspect", "status": "completed"},
                {"step": "Run tests", "status": "in_progress"},
            ],
            note="Running unit tests",
        )

        progress = self.runner.task_get(updated["id"])["progress"]
        self.assertEqual(progress["current_phase"], "Run tests")
        self.assertEqual(progress["last_activity_text"], "Running unit tests")
        self.assertFalse(progress["needs_attention"])
        self.assertIn("active: Run tests", progress["status_line"])

    def test_task_list_flags_stale_active_tasks(self):
        task = self.runner.task_start("fixture", "Do not look stuck silently")
        stale = datetime.now(timezone.utc) - timedelta(minutes=31)
        task["updated_at"] = stale.isoformat()
        self.runner._write_task(task)

        listed = self.runner.task_list()["tasks"][0]
        self.assertEqual(listed["id"], task["id"])
        self.assertTrue(listed["needs_attention"])
        self.assertIn("no update", listed["snag_reason"])
        self.assertIn("needs attention", listed["status_line"])

    def test_checkpoint_captures_task_diff_and_test_evidence(self):
        task = self.runner.task_start("fixture", "Checkpoint this change")
        read = self.runner.file_read("fixture", "src/maths.py")
        self.runner.apply_patch(
            "fixture",
            "src/maths.py",
            old="return a + b",
            new="return int(a) + int(b)",
            expected_sha256=read["sha256"],
        )
        checkpoint = self.runner.task_checkpoint(
            task["id"],
            test_profile="unit",
            note="Before review",
        )
        self.assertEqual(checkpoint["test_returncode"], 0)
        self.assertGreater(checkpoint["diff_bytes"], 0)
        reloaded = CodingRunner(self.registry, self.base / "state")
        saved = reloaded.task_checkpoint_get(task["id"], checkpoint["id"])
        self.assertIn("return int(a)", saved["git_diff"]["stdout"])
        self.assertEqual(saved["note"], "Before review")

    def test_audit_log_records_operations(self):
        self.runner.workspace_list()
        lines = self.runner.audit_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[-1])["action"], "workspace_list")


class SourceHashesTests(unittest.TestCase):
    """D0 staleness guard, runner side: source_hashes() fingerprints the workspace (top-level *.py +
    tests/*.py) and reports whether HEAD is beyond the last 'sync-to-live baseline' commit."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ws = Path(self.temp.name) / "ws"
        self.ws.mkdir()
        (self.ws / "alpha.py").write_text("a = 1\n", encoding="utf-8")
        (self.ws / "keep.bak").write_text("old\n", encoding="utf-8")   # excluded (.bak)
        (self.ws / "src").mkdir()
        (self.ws / "src" / "nested.py").write_text("n = 1\n", encoding="utf-8")  # excluded (nested)
        (self.ws / "tests").mkdir()
        (self.ws / "tests" / "test_x.py").write_text("t = 1\n", encoding="utf-8")
        for args in (("init",), ("config", "user.email", "r@e.invalid"),
                     ("config", "user.name", "R"), ("add", "."), ("commit", "-m", "fixture initial")):
            self._git(*args)
        registry = {"workspaces": [{"id": "ws", "path": str(self.ws), "description": "t",
                    "read": True, "write": True, "git": True, "protected_paths": [],
                    "ignored_paths": [".git"], "commands": {}}]}
        reg = Path(self.temp.name) / "registry.json"
        reg.write_text(json.dumps(registry), encoding="utf-8")
        self.runner = CodingRunner(reg, Path(self.temp.name) / "state")

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.ws), *args], check=True, capture_output=True)

    def test_scope_top_level_py_plus_tests_only(self):
        rels = {f["rel"] for f in self.runner.source_hashes("ws")["files"]}
        self.assertIn("alpha.py", rels)          # top-level *.py
        self.assertIn("tests/test_x.py", rels)   # tests/*.py
        self.assertNotIn("keep.bak", rels)       # .bak excluded
        self.assertNotIn("src/nested.py", rels)  # nested non-tests excluded

    def test_sha_matches_content(self):
        import hashlib
        out = self.runner.source_hashes("ws")
        alpha = next(f for f in out["files"] if f["rel"] == "alpha.py")
        # sha256 of the file's actual on-disk bytes (read_bytes avoids any newline-translation skew).
        expected = hashlib.sha256((self.ws / "alpha.py").read_bytes()).hexdigest()
        self.assertEqual(alpha["sha256"], expected)

    def test_no_sync_baseline_reads_as_ahead(self):
        # No 'sync-to-live baseline' commit exists -> optimistic ahead (never block a fresh workspace).
        self.assertTrue(self.runner.source_hashes("ws")["ahead"])

    def test_head_at_sync_baseline_is_not_ahead(self):
        self._git("commit", "--allow-empty", "-m", "sync-to-live baseline 2026-07-06 evening")
        self.assertFalse(self.runner.source_hashes("ws")["ahead"])

    def test_commit_beyond_baseline_is_ahead(self):
        self._git("commit", "--allow-empty", "-m", "sync-to-live baseline 2026-07-06 evening")
        (self.ws / "alpha.py").write_text("a = 2\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "checkpoint: end-of-item commit: bl-x")
        self.assertTrue(self.runner.source_hashes("ws")["ahead"])


if __name__ == "__main__":
    unittest.main()
