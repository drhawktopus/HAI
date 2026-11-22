"""Several jobs at once on one workspace: several people on HAI, each able to run more than one job. A real git repository, a real runner."""
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from runner import CodingRunner, ConflictError


def repo(path):
    path.mkdir()
    (path / "shared.txt").write_text("one\n", encoding="utf-8")
    for args in (["init"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "start"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


class SharedWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.shared, self.strict = base / "shared", base / "strict"
        repo(self.shared)
        repo(self.strict)
        common = {"read": True, "write": True, "git": True, "protected_paths": [], "ignored_paths": [".git"], "commands": {}}
        registry = {"workspaces": [dict(common, id="scratch", path=str(self.shared), description="many jobs", shared=True),
                                   dict(common, id="own-code", path=str(self.strict), description="one at a time")]}
        (base / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
        self.runner = CodingRunner(base / "registry.json", base / "state")

    def job(self, workspace, folder, name="made.txt", text="x\n"):
        task = self.runner.task_start(workspace, "job " + folder)
        self.runner.file_create(task["workspace"], folder + "/" + name, content=text)
        return task

    def test_every_job_gets_its_own_tree_without_asking(self):
        a, b = self.runner.task_start("scratch", "a"), self.runner.task_start("scratch", "b")
        self.assertTrue(a["worktree"] and b["worktree"], "two jobs at once, and neither was refused")
        self.assertNotEqual(a["workspace"], b["workspace"])

    def test_a_workspace_that_is_not_shared_keeps_one_at_a_time(self):
        self.runner.task_start("own-code", "a")
        with self.assertRaises(ConflictError):
            self.runner.task_start("own-code", "b")

    def test_a_stray_file_in_the_base_does_not_stop_a_job(self):
        (self.shared / "stray.py").write_text("x", encoding="utf-8")
        self.assertTrue(self.runner.task_start("scratch", "a")["worktree"])

    def test_the_second_job_to_finish_combines_with_the_first(self):
        a, b = self.job("scratch", "job-a"), self.job("scratch", "job-b")
        self.assertTrue(self.runner.task_finish(a["id"], action="merge")["merged"])
        self.assertTrue(self.runner.task_finish(b["id"], action="merge")["merged"], "this was refused as diverged before")
        self.assertTrue((self.shared / "job-a" / "made.txt").exists())
        self.assertTrue((self.shared / "job-b" / "made.txt").exists())

    def test_jobs_finishing_together_all_land(self):
        tasks = [self.job("scratch", "job-%d" % i) for i in range(4)]
        errors = []

        def finish(t):
            try:
                self.runner.task_finish(t["id"], action="merge")
            except Exception as exc:                                 # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=finish, args=(t,)) for t in tasks]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(p.name for p in self.shared.glob("job-*/")), ["job-%d" % i for i in range(4)])

    def test_two_jobs_changing_one_file_refuse_and_lose_nothing(self):
        a = self.runner.task_start("scratch", "a")
        b = self.runner.task_start("scratch", "b")
        for task, text in ((a, "from a\n"), (b, "from b\n")):
            (Path(task["worktree"]["path"]) / "shared.txt").write_text(text, encoding="utf-8")
        self.runner.task_finish(a["id"], action="merge")
        with self.assertRaises(ConflictError) as caught:
            self.runner.task_finish(b["id"], action="merge")
        self.assertIn("shared.txt", str(caught.exception), "it names the file")
        self.assertEqual((self.shared / "shared.txt").read_text(encoding="utf-8"), "from a\n", "no half-merge left in the base")
        self.assertFalse((self.shared / ".git" / "MERGE_HEAD").exists())
        self.assertEqual((Path(b["worktree"]["path"]) / "shared.txt").read_text(encoding="utf-8"), "from b\n", "the second job's work is still there")

    def test_own_code_still_refuses_a_diverged_merge(self):
        a = self.runner.task_start("own-code", "a", isolate=True)
        b = self.runner.task_start("own-code", "b", isolate=True)
        self.runner.file_create(a["workspace"], "a.txt", content="a\n")
        self.runner.file_create(b["workspace"], "b.txt", content="b\n")
        self.runner.task_finish(a["id"], action="merge")
        with self.assertRaises(ConflictError) as caught:
            self.runner.task_finish(b["id"], action="merge")
        self.assertIn("diverged", str(caught.exception))
        self.assertFalse((self.strict / "b.txt").exists())

    def test_a_task_knows_whose_it_is_and_a_resume_moves_it_to_the_new_chat(self):
        task = self.runner.task_start("scratch", "a", owner="Sam", chat_id="chat-1")
        row = next(t for t in self.runner.task_list()["tasks"] if t["id"] == task["id"])
        self.assertEqual((row["owner"], row["chat_id"]), ("Sam", "chat-1"))
        self.runner.task_resume(task["id"], chat_id="chat-2")
        row = next(t for t in self.runner.task_list()["tasks"] if t["id"] == task["id"])
        self.assertEqual(row["chat_id"], "chat-2")


    def test_the_orchestrator_takes_two_jobs_at_once(self):
        """HAI's own code is a shared workspace: two durable tasks can be open on it at the
        same time, each in its own worktree, and a merge combines with what landed since the
        task started. Every change still passes the project's own deploy gate before it reaches
        production."""
        baked = json.loads((Path(__file__).resolve().parent.parent / "registry.json")
                           .read_text(encoding="utf-8"))
        entry = next(w for w in baked["workspaces"] if w["id"] == "orchestrator-real")
        self.assertTrue(entry.get("shared"), "orchestrator-real is shared")
        orch = Path(self.temp.name) / "orchestrator-copy"
        repo(orch)
        commands = {name: argv for name, argv in entry["commands"].items() if name in ("unit", "compile")}
        self.runner.workspaces["orchestrator-copy"] = self.runner._workspace_from_entry(
            dict(entry, id="orchestrator-copy", path=str(orch), local=False, commands=commands))
        a = self.runner.task_start("orchestrator-copy", "job a")
        b = self.runner.task_start("orchestrator-copy", "job b")
        active = [t for t in self.runner.task_list()["tasks"] if t["status"] == "active"]
        self.assertEqual(len(active), 2, "the second task was refused")
        self.assertTrue(a["worktree"] and b["worktree"])
        self.runner.file_create(a["workspace"], "fix-a.py", content="x\n")
        self.runner.file_create(b["workspace"], "fix-b.py", content="y\n")
        self.runner.task_finish(a["id"], action="merge")
        self.runner.task_finish(b["id"], action="merge")
        self.assertTrue((orch / "fix-a.py").exists() and (orch / "fix-b.py").exists())


if __name__ == "__main__":
    unittest.main()
