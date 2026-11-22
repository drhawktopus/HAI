"""Verifies coding client task ownership and test isolation; runner-side isolation is covered in coding-runner/tests/test_shared_workspace.py."""
import json
import unittest
from unittest import mock

import coding_client
import coding_test_box
import config
import gpu_render
import live_runs
import overnight
import scheduler
import tools


def row(owner="Sam", chat="chat-1", tid="t1"):
    return {"id": tid, "owner": owner, "chat_id": chat, "objective": "job of " + str(owner), "status": "active", "workspace": "hai-scratch"}


class TheDoor(unittest.TestCase):
    def door(self, action, user, chat, the_row=None, live=()):
        with mock.patch.object(coding_client, "task", lambda *a, **k: the_row if the_row is not None else row()), \
                mock.patch.object(live_runs, "active", lambda u, c=None: list(live)), mock.patch.object(config, "ADMIN_USERS", {"Jamie"}):
            return coding_client.task_door(action, "t1", user, chat)

    def test_your_own_task_opens(self):
        self.assertIsNone(self.door("resume", "Sam", "chat-1"))

    def test_someone_elses_task_does_not_exist_for_you(self):
        for action in ("get", "update", "finish", "resume", "abandon"):
            self.assertEqual(self.door(action, "Alex", "chat-9"), "no such task", action)

    def test_an_admin_can_reach_any_task(self):
        self.assertIsNone(self.door("resume", "Jamie", "chat-9"))

    def test_your_task_in_another_running_chat_is_refused_and_says_why(self):
        self.assertIn("another chat", self.door("resume", "Sam", "chat-2", live=[{"cancel_id": "r"}]))

    def test_your_task_from_a_chat_that_has_stopped_can_be_picked_up(self):
        self.assertIsNone(self.door("resume", "Sam", "chat-2"))

    def test_reading_it_from_another_running_chat_is_fine(self):
        self.assertIsNone(self.door("get", "Sam", "chat-2", live=[{"cancel_id": "r"}]))

    def test_a_task_from_before_owners_stays_open(self):
        self.assertIsNone(self.door("resume", "Alex", "chat-9", row(owner=None, chat=None)))

    def test_the_labs_own_code_is_not_asked_about(self):
        with mock.patch.object(coding_client, "task", side_effect=AssertionError("no lookup for the lab's own calls")):
            self.assertIsNone(coding_client.task_door("resume", "t1", None, None))


class TheTool(unittest.TestCase):
    def setUp(self):
        tools.set_request_chat("chat-1")
        self.addCleanup(tools.set_request_chat, None)

    def call(self, args, answer, user="Sam"):
        calls = []

        def fake(action, **kw):
            calls.append((action, kw))
            return row() if action == "get" else answer

        with mock.patch.object(coding_client, "task", fake), mock.patch.object(overnight, "stale_reason", lambda w: ""), \
                mock.patch.object(config, "ADMIN_USERS", {"Jamie"}), mock.patch.object(live_runs, "active", lambda u, c=None: []):
            return json.loads(tools.run_tool("coding_task", args, "", user=user)), calls

    def test_a_task_started_here_records_who_and_where(self):
        _out, calls = self.call({"action": "start", "workspace": "hai-scratch", "objective": "x"}, {"id": "t1"})
        kw = next(k for a, k in calls if a == "start")
        self.assertEqual((kw["owner"], kw["chat_id"]), ("Sam", "chat-1"))

    def test_the_list_shows_yours_and_marks_this_chats(self):
        tasks = [row("Sam", "chat-1", "mine-here"), row("Sam", "chat-2", "mine-there"), row("Alex", "chat-3", "theirs")]
        out, _ = self.call({"action": "list"}, {"tasks": tasks})
        self.assertEqual({t["id"]: t["in_this_chat"] for t in out["tasks"]}, {"mine-here": True, "mine-there": False})

    def test_a_refused_task_is_never_touched(self):
        out, calls = self.call({"action": "abandon", "task_id": "t1"}, {"ok": True}, user="Alex")
        self.assertEqual(out, {"error": "no such task"})
        self.assertEqual([a for a, _k in calls], ["get"])

    def test_a_resume_moves_the_task_to_this_chat(self):
        _out, calls = self.call({"action": "resume", "task_id": "t1"}, {"id": "t1"})
        self.assertEqual(next(k for a, k in calls if a == "resume")["chat_id"], "chat-1")


class TheNote(unittest.TestCase):
    def note(self, user, chat, tasks):
        import app
        messages = [{"role": "user", "content": "build a thing"}]
        with mock.patch.object(coding_client, "task", lambda *a, **k: {"tasks": tasks}), mock.patch.object(config, "ADMIN_USERS", {"Jamie"}):
            app._inject_unfinished_tasks(messages, "build a thing", True, user, chat)
        return json.dumps(messages)

    def test_a_chat_is_told_about_its_own_task(self):
        self.assertIn("job of Sam", self.note("Sam", "chat-1", [row()]))

    def test_and_not_your_other_chats(self):
        self.assertNotIn("job of Sam", self.note("Sam", "chat-2", [row()]))

    def test_and_not_anyone_elses(self):
        self.assertNotIn("job of Sam", self.note("Alex", "chat-1", [row()]))

    def test_a_task_from_before_owners_goes_to_the_admins_only(self):
        old = row(None, None)
        self.assertIn("job of None", self.note("Jamie", "chat-1", [old]))
        self.assertNotIn("job of None", self.note("Sam", "chat-1", [old]))


class WhichBox(unittest.TestCase):
    def setUp(self):
        self.host = next(iter(scheduler.CARDS))

    def pick(self, named, placed=None, busy=False, rigby=False):
        with mock.patch.object(coding_client, "test_workers", lambda: named), \
                mock.patch.object(scheduler, "place", lambda kind, **k: [(h, 1.0, "why") for h in (placed if placed is not None else named) if h in k["only"]]), \
                mock.patch.object(scheduler, "training_busy", lambda h: (busy, "busy" if busy else "")), mock.patch.object(gpu_render, "tolkien_box_is_free", lambda: rigby):
            return coding_test_box.free_box()

    def test_spock_picks_among_the_boxes_with_a_worker(self):
        self.assertEqual(self.pick([self.host]), self.host)

    def test_a_box_at_work_is_passed_over_for_rigby(self):
        self.assertEqual(self.pick([self.host, "rigby"], busy=True, rigby=True), "rigby")

    def test_rigby_at_work_is_passed_over_too(self):
        self.assertIsNone(self.pick(["rigby"], rigby=False))

    def test_no_worker_anywhere_means_where_it_always_ran(self):
        self.assertIsNone(self.pick([]))

    def test_a_placement_fault_never_fails_a_test(self):
        with mock.patch.object(coding_client, "test_workers", side_effect=RuntimeError("runner down")):
            self.assertIsNone(coding_test_box.free_box())

    def test_spock_knows_the_kind(self):
        self.assertIsInstance(scheduler.place("test", only=(self.host,)), list)

    def test_the_tool_passes_the_box_on(self):
        with mock.patch.object(coding_test_box, "free_box", lambda: "rigby"), \
                mock.patch.object(coding_client, "test", return_value={"returncode": 0}) as test:
            tools.run_tool("coding_test", {"workspace": "hai-scratch", "profile": "unit"}, "", user="Sam")
        self.assertEqual(test.call_args.kwargs["worker"], "rigby")


if __name__ == "__main__":
    unittest.main()
