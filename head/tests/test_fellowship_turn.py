"""Tests for fellowship_turn. A McCoy-decided turn that places a Fellowship row first is an order to Toklien.

The desk is faked (`desk=`); this module does not place orders. If the desk cannot serve the turn, it returns
None (allowing the next row to serve) and the order is cancelled."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import fellowship_turn as FT
import scheduler
import tablet_orders


class FakeQ:
    user, chat_id = "Alex", "abcdef123456"


def cand(host="fellowship", box="rigby", order=None, **kw):
    e = {"model": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "think": "default"}
    if order is not None:
        e["order"] = order
    c = {"host": host, "box": box, "entry": e, "quality": 1.0, "q_low": 0.9, "time_s": 1.9, "j": 102.8,
         "role": "home", "eligible": False}
    c.update(kw)
    return c


ORDER = {"arms": ["rigby"], "model": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "ctx": 32768, "temperature": 0.7}


class FakeDesk:
    """The in-process doors the module calls: p20_order, p20_order_cancel, _order_status_view."""
    def __init__(self, reply=None, statuses=()):
        self.reply = reply or {"ok": True, "steps": [{"host": "fellowship", "id": "desk1", "order": "o"}]}
        self.statuses = list(statuses)
        self.orders, self.cancels, self.views = [], [], 0

    class FactoryOrderReq(dict):
        def __init__(self, **kw):
            super().__init__(**kw)

    class OrderCancelReq(dict):
        def __init__(self, **kw):
            super().__init__(**kw)

    def p20_order(self, req):
        self.orders.append(dict(req))
        return self.reply

    def p20_order_cancel(self, req):
        self.cancels.append(dict(req))
        return {"ok": True}

    def _order_status_view(self, oid):
        self.views += 1
        st = self.statuses.pop(0) if len(self.statuses) > 1 else (self.statuses[0] if self.statuses else "running")
        return {"ok": True, "id": oid, "order": "hai-fel-x", "status": st}


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        for p in (mock.patch.object(tablet_orders, "SHARE", self.d),
                  mock.patch.object(tablet_orders, "FOUNDRY_SHARE", "/ssdshared/tabfoundry-jobs"),
                  mock.patch.object(FT, "POLL_S", 0.0), mock.patch.object(FT, "STATUS_EVERY_S", 0.0),
                  mock.patch("cancellation.is_cancelled", return_value=False)):
            p.start()
            self.addCleanup(p.stop)

    def deliver(self, path, answer="391.", control=True, task=FT.TASK):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"control_passed": control, "rows": [{"task": "control_add", "ok": True},
                                                            {"task": task, "answer": answer, "arm": "rigby",
                                                             "gen_tps": 48.0}]}, f)


class WantsAndRender(_Base):
    def test_only_a_first_row_fellowship_with_order_fields_is_wanted(self):
        self.assertIsNotNone(FT.wants([cand(order=ORDER), cand(host="bighonker", box="bighonker")]))
        self.assertIsNone(FT.wants([cand(host="bighonker", box="bighonker"), cand(order=ORDER)]),
                          "a Fellowship row behind a GPU row lost the weighing")
        self.assertIsNone(FT.wants([cand()]), "no order fields = nothing to fill a spec from")
        self.assertIsNone(FT.wants([]))

    def test_render_is_the_bare_turn_with_a_little_recent_dialogue(self):
        self.assertEqual(FT.render([{"role": "system", "content": "big system prompt"},
                                    {"role": "user", "content": "17 times 23?"}]), "17 times 23?")
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "and 17 times 23?"}]
        self.assertEqual(FT.render(msgs), "Earlier in this conversation:\nUser: hi\nAssistant: hello\n\nUser: and 17 times 23?")
        long = [{"role": "user", "content": "x" * 5000}, {"role": "assistant", "content": "y" * 5000},
                {"role": "user", "content": "now?"}]
        self.assertLessEqual(len(FT.render(long)), FT.HISTORY_CHARS + 60)
        self.assertEqual(FT.render([]), "")

    def test_the_last_turn_is_the_users_own_words_not_kirks_grounded_message(self):
        grounded = "17 times 23?\n\n[Possibly-relevant notes retrieved from the homelab documentation. " + "x" * 3800 + "]"
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
                {"role": "user", "content": grounded}]
        self.assertEqual(FT.render(msgs, text="17 times 23?"),
                         "Earlier in this conversation:\nUser: hi\nAssistant: hello\n\nUser: 17 times 23?")
        self.assertEqual(FT.render([{"role": "user", "content": grounded}], text="17 times 23?"), "17 times 23?")
        self.assertEqual(FT.render([], text="17 times 23?"), "17 times 23?")
        self.assertIn("[Possibly-relevant", FT.render(msgs), "without the bare text the grounded message is all there is")

    def test_the_turn_carries_todays_date(self):
        """Inject the current date to override the model's stale training data."""
        import time as _t
        line = FT.today_line()
        self.assertIn(_t.strftime("%Y"), line)
        self.assertTrue(line.startswith("(Today is ") and line.endswith(".)\n\n"), repr(line))

    def test_the_payload_is_the_chat1_surface_plus_the_one_task(self):
        tasks, surface = run_payload(FT.payload_text("what's 2+2?"))
        self.assertEqual(list(tasks), ["turn"])
        self.assertEqual(tasks["turn"]["prompt"], "what's 2+2?")
        self.assertEqual(tasks["turn"]["kind"], "chat")
        self.assertEqual(surface, "chat1", "everything else is the CHAT1 payload's surface")

    def test_a_batch_payload_carries_every_task_in_order(self):
        tasks, _ = run_payload(FT.payload_tasks_text([("d001", "first 'quoted' prompt"), ("d002", "second\nprompt")]))
        self.assertEqual(list(tasks), ["d001", "d002"])
        self.assertEqual(tasks["d002"]["prompt"], "second\nprompt")


def run_payload(text):
    """Execute a generated payload against a stub CHAT1 file -> (its TASKS, the surface it inherited). Behaviour, not
    spelling: the payload must build the tasks it was given."""
    import os
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    try:
        stub = os.path.join(d, "chat1.py")
        with open(stub, "w", encoding="utf-8") as f:
            f.write("SURFACE = 'chat1'\nTASKS = {'chat_day': {}}\n")
        assert repr(FT.CHAT1_PAYLOAD) in text, "the payload must load the CHAT1 surface"
        g = {}
        exec(compile(text.replace(repr(FT.CHAT1_PAYLOAD), repr(stub)), "payload", "exec"), g)
        return g["TASKS"], g.get("SURFACE")
    finally:
        shutil.rmtree(d, ignore_errors=True)


class SubmitAndWait(_Base):
    def test_a_live_turn_jumps_the_lane_and_housekeeping_queues_behind_real_work(self):
        # Live turns are priority 100 (user waiting); background tasks like titles are 0 so they never block live work.
        desk = FakeDesk()
        FT.submit(FakeQ(), "17 times 23?", "chat-turn", "fellowship", True, desk=desk)
        FT.submit(FakeQ(), "title this", "chat-turn", "fellowship", False, desk=desk, priority=0)
        live, title = desk.orders
        self.assertEqual((live["spec"]["priority"], live["fellowship"]["spec"]["priority"]), (100, 100))
        self.assertEqual((title["spec"]["priority"], title["fellowship"]["spec"]["priority"]), (0, 0))
        self.assertEqual(title["lens"], "least_energy", "a background order is placed by energy, not speed")

    def test_the_order_is_the_fellowship_form_pinned_to_the_fellowship(self):
        desk = FakeDesk()
        oid, result, name = FT.submit(FakeQ(), "17 times 23?", "chat-turn", "fellowship", True, desk=desk)
        self.assertEqual(oid, "desk1")
        body = desk.orders[0]
        self.assertEqual(body["only"], ["fellowship"])
        self.assertEqual((body["family"], body["tier"], body["lens"], body["rush"]), ("chat-turn", "fellowship", "fastest", True))
        self.assertEqual(body["fellowship"]["requires"], ["fellowship.placed"])
        fel = body["fellowship"]["spec"]
        self.assertEqual((fel["kind"], fel["tasks"], fel["replicates"], fel["priority"]), ("llm", ["turn"], 1, 100))
        for k in ("arms", "model", "ctx"):
            self.assertNotIn(k, fel, "the form names no worker - Tolkien fills it from the row")
        self.assertTrue(fel["payload"].startswith("/ssdshared/tabfoundry-jobs/campaigns/hai-chat/"))
        self.assertTrue(os.path.exists(os.path.join(self.d, "campaigns", "hai-chat", os.path.basename(os.path.dirname(result)),
                                                    name + ".payload.py")), "the payload is on the share before the order")
        self.assertTrue(result.endswith(name + ".results.json"))
        self.assertEqual(body["user"], "Alex")

    def test_a_hold_is_cancelled_and_not_served_here(self):
        desk = FakeDesk(reply={"ok": True, "held": True, "id": "h1", "why": ["no worker present"]})
        oid, why, _n = FT.submit(FakeQ(), "hi", "chat-turn", "fellowship", True, desk=desk)
        self.assertIsNone(oid)
        self.assertIn("held", why)
        self.assertEqual(desk.cancels[0]["id"], "h1")

    def test_wait_reads_the_delivered_answer_when_the_order_is_done(self):
        desk = FakeDesk(statuses=["queued", "running", "done"])
        result = os.path.join(self.d, "campaigns", "hai-chat", "x", "o.results.json")
        self.deliver(result)
        seen = []
        ans, why, info = FT.wait("desk1", result, status=seen.append, desk=desk)
        self.assertEqual((ans, why), ("391.", None))
        self.assertEqual(info["row"]["arm"], "rigby")
        self.assertTrue(any("Fellowship is answering" in s for s in seen), seen)

    def test_wait_gives_up_on_an_ended_or_held_order_and_cancels_a_hold(self):
        desk = FakeDesk(statuses=["running", "failed"])
        self.assertIsNone(FT.wait("desk1", os.path.join(self.d, "none.json"), desk=desk)[0])
        desk = FakeDesk(statuses=["held"])
        ans, why, _i = FT.wait("desk1", os.path.join(self.d, "none.json"), desk=desk)
        self.assertIsNone(ans)
        self.assertEqual(desk.cancels[0]["id"], "desk1", "a hand-back is cancelled so a late answer never arrives twice")

    def test_a_failed_control_or_an_empty_answer_is_not_an_answer(self):
        result = os.path.join(self.d, "r.json")
        self.deliver(result, control=False)
        self.assertIsNone(FT.read_answer(result)[0])
        self.deliver(result, answer="   ")
        self.assertIsNone(FT.read_answer(result)[0])

    def test_the_users_stop_cancels_the_order(self):
        desk = FakeDesk(statuses=["running"])
        with mock.patch("cancellation.is_cancelled", return_value=True):
            ans, why, _i = FT.wait("desk1", os.path.join(self.d, "none.json"), desk=desk)
        self.assertIsNone(ans)
        self.assertEqual(desk.cancels[0]["why"], "the user stopped the turn")

    def test_answer_turn_end_to_end_on_the_fake_desk(self):
        desk = FakeDesk(statuses=["running", "done"])
        real_submit = FT.submit

        def submit_and_deliver(*a, **kw):
            oid, result, name = real_submit(*a, **kw)
            self.deliver(result)
            return oid, result, name
        with mock.patch.object(FT, "submit", side_effect=submit_and_deliver):
            got = FT.answer_turn(FakeQ(), [{"role": "user", "content": "17 times 23?"}], cand(order=ORDER),
                                 "chat-turn", "fellowship", True, desk=desk)
        self.assertEqual(got["answer"], "391.")
        self.assertEqual(got["worker"], "rigby")
        self.assertIn("Fellowship rigby", got["label"])
        self.assertEqual(got["order"], "desk1")


if __name__ == "__main__":
    unittest.main()
