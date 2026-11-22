"""A1-team delegation: a brief becomes ONE desk order pinned to the helper, the lead waits on the desk's status (not a
clock) while keeping its model warm, and the helper's delivered answer comes back. No network: the desk is faked."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import a1_delegate as D  # noqa: E402

TEAM = {"helper": "bighonker", "helper_model": "qwen3.5:9b", "helper_ctx": 7168, "helper_think": False,
        "helper_temperature": 0.6}


class FakeDesk:
    def __init__(self, unc, statuses, answer="def f(): return 1", reply=None):
        self.unc, self.statuses, self.answer, self.reply = unc, list(statuses), answer, reply
        self.posts, self.gets = [], 0

    def __call__(self, method, path, body=None, timeout=30):
        if method == "POST":
            self.posts.append(body)
            if self.reply is not None:
                return self.reply
            step = body["spec"]["name"] + "_bighonker"
            d = os.path.join(self.unc, "delegated", step)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "out.json"), "w", encoding="utf-8") as f:
                json.dump({"rows": [{"answer": self.answer, "wall_s": 3.2, "eval_count": 41, "done_reason": "stop"}]}, f)
            # the desk's REAL reply shape: no top-level id; the record id is on the step
            return {"ok": True, "order": body["spec"]["name"], "kind": "render_small", "requires": body["requires"],
                    "parallel": False, "steps": [{"host": "bighonker", "id": "34f16f6038a0", "order": step}]}
        self.gets += 1
        assert path == "/p20/order/34f16f6038a0", "status must be asked by the STEP's record id, got %s" % path
        return {"ok": True, "id": "34f16f6038a0", "status": self.statuses.pop(0) if self.statuses else "done",
                "hold_reason": "plex", "reason": None}


class Delegate(unittest.TestCase):
    def setUp(self):
        self.unc = tempfile.mkdtemp()
        self.t = [0.0]
        self.warm = []

    def tearDown(self):
        shutil.rmtree(self.unc, ignore_errors=True)

    def _d(self, desk):
        def sleep(s):
            self.t[0] += s
        d = D.Delegator(TEAM, "a1v22-team-q38.20261006", self.unc, lambda: "tok", lambda: self.warm.append(self.t[0]),
                        http=desk, sleep=sleep, clock=lambda: self.t[0])
        d.exercise = "wordy"
        return d

    def test_one_pinned_order_and_the_answer_comes_back(self):
        desk = FakeDesk(self.unc, ["pending", "running", "done"])
        d = self._d(desk)
        self.assertEqual(d("write f"), "def f(): return 1")
        body = desk.posts[0]
        self.assertEqual(body["only"], ["bighonker"])
        self.assertIn("model.qwen3.5:9b", body["requires"])
        items = json.load(open(body["spec"]["args"][0], encoding="utf-8"))["items"]
        self.assertEqual(items, [{"id": "brief", "prompt": "write f"}])
        self.assertEqual(d.pieces[0]["status"], "done")
        self.assertEqual(d.pieces[0]["helper_tokens"], 41)

    def test_the_lead_stays_warm_while_it_waits(self):
        desk = FakeDesk(self.unc, ["running"] * 300 + ["done"])         # 300 polls x 2 s = 10 min of waiting
        self._d(desk)("long piece")
        self.assertGreaterEqual(len(self.warm), 2, "no keep-warm in 10 minutes of waiting")

    def test_a_slow_piece_beats_the_foundry_while_the_lead_waits(self):
        """15 min waiting on a rigby piece streamed nothing, the stall demon read a wedge and
        respawned the arm. Every poll of the wait is a work beat on the foundry's own channel."""
        desk = FakeDesk(self.unc, ["running"] * 300 + ["done"])
        beats = []

        def sleep(s):
            self.t[0] += s
        d = D.Delegator(TEAM, "a1v22-team-q38.20261006", self.unc, lambda: "tok", lambda: None, http=desk,
                        sleep=sleep, clock=lambda: self.t[0], beat=lambda: beats.append(int(self.t[0])))
        d.exercise = "wordy"
        d("long piece")
        self.assertGreaterEqual(len(beats), 300, "one beat per poll")
        self.assertEqual(beats, sorted(beats), "the beat value moves, so the demon reads work")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            D.work_beat(clock=lambda: 1760000123.7)
        self.assertEqual(buf.getvalue(), "[beat] 1760000123\n", "the foundry's own `[beat] N` line, nothing else")

    def test_a_failed_or_held_piece_is_reported_not_hung(self):
        for status, word in (("failed", "ended failed"), ("held", "held")):
            desk = FakeDesk(self.unc, [status])
            out = self._d(desk)("x")
            self.assertTrue(out.startswith("ERROR") and word in out, out)

    def test_the_desks_real_reply_is_understood_and_the_old_shape_is_not_required(self):
        # pinned on the live incident: a reply WITHOUT a top-level id placed the order; reading reply["id"] made the
        # lead see "not placed" and re-brief four times in 45 s while BigHonker ran every piece
        desk = FakeDesk(self.unc, ["claimed", "done"])
        d = self._d(desk)
        self.assertEqual(d("write f"), "def f(): return 1")
        self.assertEqual(d.pieces[0]["id"], "34f16f6038a0")
        self.assertEqual(desk.gets, 2, "the lead WAITED on the desk's status, it did not return at the POST")
        # the desk saying ok: false is a refusal even when it echoes steps
        desk = FakeDesk(self.unc, [], reply={"ok": False, "error": "held at the gate", "steps": [{"id": "x", "order": "y"}]})
        self.assertIn("held at the gate", self._d(desk)("x"))

    def test_a_desk_refusal_is_reported(self):
        out = self._d(FakeDesk(self.unc, [], reply={"ok": False, "error": "no factory has model.qwen3.5:9b"}))("x")
        self.assertIn("no factory has", out)

    def test_a_reviewer_piece_is_named_as_one(self):
        # A1-review: the same Delegator with prefix a1review- places the reviewer's order, so the deck and the
        # delivered dir tell a review from a helper piece by name
        desk = FakeDesk(self.unc, ["done"])
        self._d(desk)("x")
        self.assertTrue(desk.posts[0]["spec"]["name"].startswith("a1team-"), desk.posts[0]["spec"]["name"])
        desk = FakeDesk(self.unc, ["done"])
        d = D.Delegator(TEAM, "a1v22-rev.20261006", self.unc, lambda: "tok", lambda: None, http=desk,
                        sleep=lambda s: None, clock=lambda: 0.0, prefix="a1review-")
        d.exercise = "wordy"
        self.assertEqual(d("package"), "def f(): return 1")
        self.assertTrue(desk.posts[0]["spec"]["name"].startswith("a1review-a1v22-rev-wordy-d1."),
                        desk.posts[0]["spec"]["name"])
        # the reviewer is a short service order the lead waits on: it runs next on its box
        self.assertEqual(desk.posts[0]["spec"]["priority"], D.GPU_PIECE_PRIORITY)


class Placed(unittest.TestCase):
    """The placed delegate: the piece names its family / tier / lens and no host and no model; Spock and the policy row
    choose both. The answer comes back through the same desk status + delivery as a pinned piece."""

    def setUp(self):
        self.unc = tempfile.mkdtemp()
        self.t = [0.0]
        self.share = tempfile.mkdtemp()           # the Fellowship form's payload goes on the share: never the real one
        self._share_unc = D.SHARE_UNC
        D.SHARE_UNC = self.share

    def tearDown(self):
        D.SHARE_UNC = self._share_unc
        shutil.rmtree(self.unc, ignore_errors=True)
        shutil.rmtree(self.share, ignore_errors=True)

    def _d(self, desk, **team):
        def sleep(s):
            self.t[0] += s
        t = dict({"placed": True, "family": "coding-f2", "tier": "8gb"}, **team)
        d = D.PlacedDelegator(t, "a1v22-cascade.20261010", self.unc, lambda: "tok", lambda: None,
                              http=desk, sleep=sleep, clock=lambda: self.t[0], beat=lambda: None)
        d.exercise = "forth"
        return d

    def test_the_piece_names_no_host_and_no_model(self):
        desk = FakeDesk(self.unc, ["pending", "done"])
        self.assertEqual(self._d(desk)("write f"), "def f(): return 1")
        body = desk.posts[0]
        self.assertNotIn("only", body)
        self.assertEqual(body["requires"], ["order.compute"])
        self.assertEqual((body["family"], body["tier"], body["lens"]), ("coding-f2", "8gb", "least_energy"))
        args = body["spec"]["args"]
        for tok in ("{POLICY_MODEL}", "{POLICY_NUM_CTX}", "{POLICY_THINK_ARGS}", "{POLICY_TEMPERATURE_ARGS}"):
            self.assertIn(tok, args)
        self.assertFalse(any("qwen" in str(a) for a in args), "a model leaked into a placed piece")
        # a service order a live lead waits on runs next, never behind filler
        self.assertEqual(body["spec"]["priority"], D.GPU_PIECE_PRIORITY)
        self.assertEqual(D.GPU_PIECE_PRIORITY, D.RIGBY_PIECE_PRIORITY)

    def test_urgency_picks_the_lens_and_an_explicit_lens_wins(self):
        desk = FakeDesk(self.unc, ["done"])
        self.assertEqual(self._d(desk, urgency="interactive").lens(), "fastest")
        self.assertEqual(self._d(desk, urgency="interactive", lens="best_quality").lens(), "best_quality")

    def test_a_piece_naming_its_urgency_is_sent_to_the_weighing_and_a_lens_arm_is_not(self):
        """the desk places an order with family + tier + URGENCY by the per-request weighing, the rule chat
        turns use; a team that names a lens is a pricing arm and keeps it."""
        desk = FakeDesk(self.unc, ["done"])
        self.assertEqual(self._d(desk, urgency="background").placement(),
                         {"family": "coding-f2", "tier": "8gb", "lens": "least_energy", "urgency": "background"})
        self.assertNotIn("urgency", self._d(desk, urgency="interactive", lens="best_quality").placement())
        self.assertNotIn("urgency", self._d(desk).placement(), "no urgency named = the table's lens path, as before")
        desk = FakeDesk(self.unc, ["pending", "done"])
        self._d(desk, urgency="background")("write f")
        self.assertEqual(desk.posts[0]["urgency"], "background")

    BRIEF = 'Write `f(s)` that returns s.split("\\t") and keeps "quotes" \'too\'\nline two \\ backslash'

    def test_the_piece_also_offers_its_fellowship_form_naming_no_worker(self):
        """Design § D: the table may pick the Fellowship; the desk then sends this form and Tolkien picks rigby or a
        tablet from the row. So the form names no worker, no model and no settings of its own."""
        desk = FakeDesk(self.unc, ["done"])
        self._d(desk)(self.BRIEF)
        fel = desk.posts[0]["fellowship"]
        self.assertEqual(fel["requires"], ["fellowship.placed"])
        s = fel["spec"]
        self.assertEqual((s["kind"], s["tasks"], s["priority"]), ("llm", ["brief"], D.RIGBY_PIECE_PRIORITY))
        for k in ("arms", "model", "ctx", "temperature", "reasoning", "reservation"):
            self.assertNotIn(k, s, "the Fellowship form must leave %s to the table row" % k)
        self.assertTrue(s["payload"].startswith(D.SHARE_CONTAINER + "/tabfoundry-jobs/campaigns/a1team-placed/"))
        rel = s["payload"][len(D.SHARE_CONTAINER) + 1:]
        src = open(os.path.join(self.share, *rel.split("/")), encoding="utf-8").read()
        import ast
        tree = ast.parse(src)
        tasks = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "TASKS")
        self.assertEqual(ast.literal_eval(tasks.value)["brief"]["prompt"], self.BRIEF)

    def _exec_template(self, template, **fields):
        """Render a piece template against a stand-in f2 payload and import it, as Tolkien does."""
        import importlib.util
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        f2 = os.path.join(d, "f2.py")
        with open(f2, "w", encoding="utf-8") as f:
            f.write("TEMPERATURE = 0.6\nCONTEXT = 2048\nCONTROL_TASKS = {'control_add': {}}\nTASKS = {'f2_task': {}}\n"
                    "def score(code, checks, work): return 0, 0, ''\n"
                    "def extract_code(text): return 'EXTRACTED'\n")
        p = os.path.join(d, "piece.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(template % dict(fields, f2=f2, brief=self.BRIEF))
        s = importlib.util.spec_from_file_location("piece", p)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        return m

    def test_a_piece_payload_is_the_f2_payload_with_its_own_task(self):
        """live decoy: the piece re-exported score + CONTROL_TASKS only, the one grader read extract_code
        for an unfenced answer, and a CORRECT answer failed grading - the piece ended "failed" and the lead never saw
        it. A piece now carries f2's whole surface; only TASKS (and rigby's pinned sampling) are its own."""
        placed = self._exec_template(D.PLACED_PIECE_PAYLOAD)
        self.assertEqual(placed.extract_code("x"), "EXTRACTED")
        self.assertEqual((placed.TEMPERATURE, placed.CONTEXT), (0.6, 2048), "a blank-temperature row: f2's defaults")
        self.assertEqual(list(placed.TASKS), ["brief"])
        self.assertEqual(placed.TASKS["brief"]["prompt"], self.BRIEF)
        self.assertIn("control_add", placed.CONTROL_TASKS)
        rigby = self._exec_template(D.PIECE_PAYLOAD, temperature=0.7, ctx=32768)
        self.assertEqual(rigby.extract_code("x"), "EXTRACTED")
        self.assertEqual((rigby.TEMPERATURE, rigby.CONTEXT), (0.7, 32768), "rigby's team sampling wins over f2's")
        self.assertEqual(list(rigby.TASKS), ["brief"])

    def test_a_piece_handed_back_and_replaced_is_read_where_it_ended(self):
        """live: the desk sent a rigby-rung piece to the Fellowship, Tolkien handed it back (the 1060 off the
        bus), and the desk re-placed it on BigHonker as `<name>_bighonker`. The answer is read from the host and name
        the desk reports at the end, never from the first reply."""
        posted = []

        def desk(method, path, body=None, timeout=30):
            if method == "POST":
                posted.append(body)
                return {"ok": True, "steps": [{"host": "fellowship", "id": "h1",
                                                "order": body["spec"]["name"] + "_fellowship"}]}
            name = posted[0]["spec"]["name"] + "_bighonker"
            out = os.path.join(self.unc, "delegated", name)
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "out.json"), "w", encoding="utf-8") as f:
                json.dump({"rows": [{"answer": "def f(): return 3", "wall_s": 1.0, "eval_count": 9}]}, f)
            return {"ok": True, "status": "done", "host": "bighonker", "order": name}
        d = self._d(desk, tier="rigby")
        self.assertEqual(d("write f"), "def f(): return 3")
        p = d.pieces[0]
        self.assertEqual((p["host"], p["status"]), ("bighonker", "done"))
        self.assertTrue(p["step"].endswith("_bighonker"))

    def test_a_piece_the_desk_sends_to_the_fellowship_is_read_from_its_results_file(self):
        def desk(method, path, body=None, timeout=30):
            if method == "POST":
                rel = body["fellowship"]["spec"]["deliver_to"][len(D.SHARE_CONTAINER) + 1:]
                p = os.path.join(self.share, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"control_passed": True, "rows": [
                        {"task": "brief", "answer": "def f(): return 2", "wall_s": 5.0, "gen_tokens": 30,
                         "gen_tps": 16.9, "finish_reason": "stop"}]}, f)
                return {"ok": True, "steps": [{"host": "fellowship", "id": "f1", "order": "x_fellowship"}]}
            return {"ok": True, "status": "done"}
        d = self._d(desk, tier="fellowship")
        self.assertEqual(d("write f"), "def f(): return 2")
        p = d.pieces[0]
        self.assertEqual((p["host"], p["helper_tok_s"], p["control_passed"]), ("fellowship", 16.9, True))


class PlacedWindow(unittest.TestCase):
    """The placed window: one window per run, placed by the table (family / tier / lens), no host, no model."""

    def test_the_window_names_no_host_and_no_model(self):
        unc = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, unc, ignore_errors=True)
        d = D.PlacedWindowDelegator({"placed": True, "window": True, "family": "coding-f2", "tier": "8gb",
                                     "urgency": "interactive"}, "a1v22-cascade.20261010", unc, lambda: "tok",
                                    lambda: None, lead_order_id="abc123", beat=lambda: None)
        d.items_dir = os.path.join(unc, "windows", "w")
        body = d._window_body("a1win-x")
        self.assertNotIn("only", body)
        self.assertEqual(body["requires"], ["order.compute"])
        self.assertEqual((body["family"], body["tier"], body["lens"], body["urgency"]),
                         ("coding-f2", "8gb", "fastest", "interactive"))
        args = body["spec"]["args"]
        self.assertTrue(args[0].endswith(os.path.join("windows", "w")))
        for tok in ("{POLICY_MODEL}", "{POLICY_NUM_CTX}", "{POLICY_TEMPERATURE_ARGS}", "{POLICY_THINK_ARGS}"):
            self.assertIn(tok, args)
        self.assertEqual(args[-2:], ["--lead-order", "abc123"])
        self.assertTrue(body["spec"]["payload"].endswith("helper_window.py"))
        self.assertEqual(body["spec"]["priority"], D.GPU_PIECE_PRIORITY)

    def test_a_placed_helper_never_lands_on_the_leads_own_box(self):
        import socket
        me = [socket.gethostname().lower()]
        unc = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, unc, ignore_errors=True)
        team = {"placed": True, "window": True, "family": "tool", "tier": "8gb"}
        w = D.PlacedWindowDelegator(team, "a1v22-err.20261010", unc, lambda: "tok", lambda: None, beat=lambda: None)
        w.items_dir = os.path.join(unc, "windows", "w")
        self.assertEqual(w._window_body("a1win-x")["exclude"], me)
        self.assertNotIn("fellowship", w._window_body("a1win-x"), "a window is a GPU factory's order: no Fellowship form")
        share, saved = tempfile.mkdtemp(), D.SHARE_UNC
        self.addCleanup(shutil.rmtree, share, ignore_errors=True)
        D.SHARE_UNC = share                               # the piece's Fellowship form writes there: never the real share
        try:
            p = D.PlacedDelegator(team, "a1v22-err.20261010", unc, lambda: "tok", lambda: None, beat=lambda: None)
            p.exercise = "forth"
            self.assertEqual(p._order_body("brief", "piece-x")[0]["exclude"], me)
        finally:
            D.SHARE_UNC = saved


class Window(unittest.TestCase):
    """WindowDelegator: ONE desk order per lead run; pieces are item files a (faked) worker answers beside
    them; CLOSE ends the window; a held or ended window surfaces as an ERROR the lead can act on; a refused window
    fails every piece fast without writing items."""

    def setUp(self):
        self.unc = tempfile.mkdtemp()
        self.t = [0.0]
        self.warm = []
        self.worker = [None]

    def tearDown(self):
        shutil.rmtree(self.unc, ignore_errors=True)

    def _d(self, desk, lead="lead123"):
        def sleep(s):
            self.t[0] += s
            if self.worker[0]:
                self.worker[0]()
        d = D.WindowDelegator(TEAM, "a1v22-team-q38.20261006", self.unc, lambda: "tok",
                              lambda: self.warm.append(self.t[0]), http=desk, sleep=sleep, clock=lambda: self.t[0],
                              lead_order_id=lead)
        d.exercise = "wordy"
        return d

    def _worker(self, d, answer="def f(): return 1", after_polls=0):
        calls = [0]

        def work():
            calls[0] += 1
            if calls[0] <= after_polls or not d.items_dir or not os.path.isdir(d.items_dir):
                return
            for f in os.listdir(d.items_dir):
                if f.endswith(".item.json"):
                    iid = f[:-len(".item.json")]
                    out = os.path.join(d.items_dir, iid + ".out.json")
                    if not os.path.exists(out):
                        json.load(open(os.path.join(d.items_dir, f), encoding="utf-8"))   # must be whole JSON
                        with open(out, "w", encoding="utf-8") as fh:
                            json.dump({"id": iid, "answer": answer, "wall_s": 3.0, "eval_count": 41,
                                       "done_reason": "stop", "gpu_j": 12.5, "error": ""}, fh)
        return work

    def test_an_errand_turn_sends_the_conversation_and_tools_and_maps_the_tool_calls_back(self):
        desk = FakeDesk(self.unc, [])
        d = self._d(desk)
        seen = {}

        def work():
            if not d.items_dir or not os.path.isdir(d.items_dir):
                return
            for f in os.listdir(d.items_dir):
                if f.endswith(".item.json"):
                    iid = f[:-len(".item.json")]
                    out = os.path.join(d.items_dir, iid + ".out.json")
                    if not os.path.exists(out):
                        seen.update(json.load(open(os.path.join(d.items_dir, f), encoding="utf-8")))
                        with open(out, "w", encoding="utf-8") as fh:
                            json.dump({"id": iid, "answer": "", "wall_s": 1.0, "error": "",
                                       "tool_calls": [{"function": {"name": "coding_read",
                                                                    "arguments": '{"path": "a.py"}'}}]}, fh)
        self.worker[0] = work
        msgs = [{"role": "system", "content": "errand"}, {"role": "user", "content": "find X"}]
        tools = [{"name": "coding_read", "description": "read", "parameters": {"path": "p"}}]
        got = d.turn(msgs, tools)
        self.assertEqual(got, {"content": "", "tool_calls": [{"name": "coding_read", "arguments": {"path": "a.py"}}]})
        self.assertEqual(seen["messages"], msgs)
        self.assertEqual(seen["tools"][0]["function"]["name"], "coding_read", "sent in ollama's schema")
        self.assertEqual(d.pieces[0]["item"], "e1")
        self.assertEqual(d.pieces[0]["kind"], "errand")

    def test_one_order_many_pieces_then_close(self):
        desk = FakeDesk(self.unc, [])
        d = self._d(desk)
        self.worker[0] = self._worker(d)
        self.assertEqual(d("write f"), "def f(): return 1")
        self.assertEqual(d("write g"), "def f(): return 1")
        self.assertEqual(len(desk.posts), 1, "one desk order for the whole run")
        spec = desk.posts[0]["spec"]
        self.assertTrue(spec["payload"].endswith("helper_window.py"))
        self.assertEqual(spec["args"][0], d.items_dir)
        self.assertIn("--lead-order", spec["args"])
        self.assertEqual(spec["args"][spec["args"].index("--lead-order") + 1], "lead123")
        self.assertEqual(desk.posts[0]["only"], ["bighonker"])
        self.assertIn("progress_glob", spec)
        self.assertNotIn("progress_total", spec, "items are not known in advance")
        self.assertTrue(spec["name"].startswith("a1win-"))
        self.assertEqual(sorted(f for f in os.listdir(d.items_dir) if f.endswith(".item.json")),
                         ["d1.item.json", "d2.item.json"])
        self.assertEqual([p["item"] for p in d.pieces], ["d1", "d2"])
        self.assertEqual([p["status"] for p in d.pieces], ["done", "done"])
        self.assertEqual(d.pieces[0]["helper_gpu_j"], 12.5)
        self.assertEqual(d.pieces[0]["step"], spec["name"] + "_bighonker")
        self.assertEqual(d.window["status"], "placed")
        w = d.close()
        self.assertTrue(os.path.exists(os.path.join(d.items_dir, "CLOSE")))
        self.assertEqual(w["status"], "done")
        self.assertIn("ended", w)

    def test_a_slow_piece_keeps_the_lead_warm_and_asks_the_desk_about_the_window(self):
        desk = FakeDesk(self.unc, ["running"] * 50)
        d = self._d(desk)
        self.worker[0] = self._worker(d, after_polls=130)       # 130 polls x 2 s = 260 s > WARM_EVERY_S
        self.assertEqual(d("slow piece"), "def f(): return 1")
        self.assertGreaterEqual(len(self.warm), 1, "the lead's model was kept warm while it waited")
        self.assertGreaterEqual(desk.gets, 1, "the window's status was asked while waiting")

    def test_a_held_or_ended_window_is_an_error_not_a_hang(self):
        desk = FakeDesk(self.unc, ["held"])
        d = self._d(desk)
        self.worker[0] = None
        out = d("x")
        self.assertTrue(out.startswith("ERROR") and "held" in out, out)
        self.assertEqual(d.pieces[0]["status"], "held")
        desk = FakeDesk(self.unc, ["failed"])
        d = self._d(desk)
        self.worker[0] = None
        out = d("x")
        self.assertIn("ended failed", out)
        n_items = len([f for f in os.listdir(d.items_dir) if f.endswith(".item.json")])
        self.assertIn("has ended", d("y"), "once ended, the next piece fails at once")
        self.assertEqual(len([f for f in os.listdir(d.items_dir) if f.endswith(".item.json")]), n_items, "no new item written")

    def test_a_refused_window_fails_every_piece_without_items(self):
        desk = FakeDesk(self.unc, [], reply={"ok": False, "error": "no factory has model.qwen3.5:9b"})
        d = self._d(desk)
        out = d("x")
        self.assertIn("could not be placed", out)
        self.assertEqual(d.pieces[0]["status"], "refused")
        self.assertEqual([f for f in os.listdir(d.items_dir) if f.endswith(".item.json")], [])
        self.assertEqual(len(desk.posts), 1)
        self.assertIn("could not be placed", d("y"))
        self.assertEqual(len(desk.posts), 1, "no re-placement per piece")
        d.close()
        self.assertFalse(os.path.exists(os.path.join(d.items_dir, "CLOSE")))

    def test_close_without_the_desk_reporting_done_is_bounded_and_said(self):
        desk = FakeDesk(self.unc, ["running"] * 500)
        d = self._d(desk)
        self.worker[0] = self._worker(d)
        d("x")
        w = d.close()
        self.assertEqual(w["status"], "closing")
        self.assertTrue(os.path.exists(os.path.join(d.items_dir, "CLOSE")))


class Rigby(unittest.TestCase):
    """A1-team with rigby as the helper: a Tolkien `llm` order placed through Kirk's desk by capability, its answer read
    from the stated delivery path. No network, no Tolkien: the desk and Tolkien's delivery are faked."""
    RTEAM = {"helper": "rigby", "helper_model": "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf", "helper_ctx": 32768,
             "helper_think": False, "helper_temperature": 0.6, "gpu_layers": 99,
             "override_tensor": ["blk\\.(?!(3|7)\\.).*=CPU", "ffn_=CPU"], "op_offload": False}
    BRIEF = 'Write `f(s)` that returns s.split("\\t") and keeps "quotes" \'too\'\nline two \\ backslash'

    def setUp(self):
        self.share = tempfile.mkdtemp()
        self._unc = D.SHARE_UNC
        D.SHARE_UNC = self.share
        self.t = [0.0]

    def tearDown(self):
        D.SHARE_UNC = self._unc
        shutil.rmtree(self.share, ignore_errors=True)

    def _run(self, answer="def f(s): return s.split('\\t')", statuses=("pending", "running", "done"), deliver=True):
        posts, st = [], list(statuses)

        def desk(method, path, body=None, timeout=30):
            if method == "POST":
                posts.append(body)
                if deliver:          # Tolkien's deliver_to, translated back to the share the lead reads
                    rel = body["spec"]["deliver_to"][len(D.SHARE_CONTAINER) + 1:]
                    p = os.path.join(self.share, *rel.split("/"))
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "w", encoding="utf-8") as f:
                        json.dump({"control_passed": True, "rows": [
                            {"task": "control_add", "answer": "def add(a, b): return a + b"},
                            {"task": "brief", "answer": answer, "wall_s": 61.0, "gen_tokens": 230, "gen_tps": 3.8,
                             "finish_reason": "stop"}]}, f)
                return {"ok": True, "order": body["spec"]["name"],
                        "steps": [{"host": "fellowship", "id": "r1d", "order": body["spec"]["name"] + "_fellowship"}]}
            assert path == "/p20/order/r1d", path
            return {"ok": True, "status": st.pop(0) if st else "done"}

        def sleep(s):
            self.t[0] += s
        d = D.RigbyDelegator(self.RTEAM, "a1v22-py-agentic-hai-q38-best-thon-spot-team-ri.20261007", r"\\x\a1",
                             lambda: "tok", lambda: None, http=desk, sleep=sleep, clock=lambda: self.t[0])
        d.exercise = "wordy"
        return d(self.BRIEF), posts, d

    def test_the_run_reserves_rigby_tags_its_pieces_and_releases_on_close(self):
        # the owner the order carries its own pause slip for Tolkien's rigby lane - no session pauses it by hand
        inbox = os.path.join(self.share, "tabfoundry-jobs", "inbox")
        os.makedirs(inbox)
        out, posts, d = self._run()
        self.assertEqual(posts[0]["spec"]["reservation"], d.order_name, "pieces are admitted under the reservation")
        self.assertTrue(d.reserve("desk42"))
        res = [f for f in os.listdir(inbox) if f.endswith(".reserve.lane.json")]
        self.assertEqual(len(res), 1)
        with open(os.path.join(inbox, res[0]), encoding="utf-8") as f:
            body = json.load(f)
        self.assertEqual((body["action"], body["lane"], body["by"], body["owner_id"]),
                         ("reserve", "rigby", d.order_name, "desk42"))
        d.close()
        self.assertEqual(len([f for f in os.listdir(inbox) if f.endswith(".release.lane.json")]), 1)

    def test_a_reserve_that_cannot_be_written_is_said_not_raised(self):
        out, posts, d = self._run()                    # no inbox dir under the fake share
        self.assertFalse(d.reserve("desk42"))
        self.assertEqual(d.close(), {"rigby_reservation": "released"}, "close never raises")

    def test_the_piece_is_a_desk_order_for_rigby_by_capability(self):
        out, posts, d = self._run()
        self.assertEqual(out, "def f(s): return s.split('\\t')")
        body = posts[0]
        self.assertEqual((body["only"], body["requires"]), (["fellowship"], ["rigby.llm", "rigby.gt730"]))
        s = body["spec"]
        self.assertEqual((s["kind"], s["arms"], s["tasks"], s["reasoning"], s["priority"]),
                         ("llm", ["rigby"], ["brief"], "off", D.RIGBY_PIECE_PRIORITY))
        self.assertEqual((s["gpu_layers"], s["op_offload"], s["override_tensor"]),
                         (99, False, self.RTEAM["override_tensor"]))
        self.assertTrue(s["payload"].startswith(D.SHARE_CONTAINER + "/tabfoundry-jobs/campaigns/a1team-rigby/"))
        p = d.pieces[0]
        self.assertEqual((p["status"], p["answer_chars"], p["helper_tok_s"], p["control_passed"]),
                         ("done", len(out), 3.8, True))

    def test_the_generated_payload_carries_the_brief_byte_for_byte(self):
        _, posts, _ = self._run()
        rel = posts[0]["spec"]["payload"][len(D.SHARE_CONTAINER) + 1:]
        src = open(os.path.join(self.share, *rel.split("/")), encoding="utf-8").read()
        import ast
        tree = ast.parse(src)                 # it is valid Python
        tasks = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "TASKS")
        self.assertEqual(ast.literal_eval(tasks.value)["brief"]["prompt"], self.BRIEF)
        self.assertIn(D.F2_PAYLOAD, src)

    def test_a_cpu_team_block_asks_for_rigby_llm_only(self):
        team = {k: v for k, v in self.RTEAM.items() if k not in ("gpu_layers", "override_tensor", "op_offload")}
        self.RTEAM, saved = team, self.RTEAM
        try:
            _, posts, _ = self._run()
        finally:
            self.RTEAM = saved
        self.assertEqual(posts[0]["requires"], ["rigby.llm"])
        self.assertNotIn("gpu_layers", posts[0]["spec"])

    def test_failures_come_back_as_errors_the_lead_can_act_on(self):
        out, _, d = self._run(statuses=("pending", "failed"))
        self.assertTrue(out.startswith("ERROR: the helper order ended failed"), out)
        out, _, d = self._run(deliver=False)
        self.assertEqual((out, d.pieces[0]["status"]), ("ERROR: rigby's answer was not delivered", "no_delivery"))
        out, _, _ = self._run(answer="   ")
        self.assertEqual(out, "ERROR: rigby returned an empty answer")


class DeskIdForTheRunningOrder(unittest.TestCase):
    """The order's desk id is found though its spec FILE lacks the box suffix its name and ORDER_OUT carry (10-11)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.q = os.path.join(self.root, "jobs", "queue")
        os.makedirs(self.q)
        self.out = os.path.join(self.root, "output", "compute", "tb-bi-qwen3.5-9b-th.20261011-0241_softserve")

    def spec(self, filename, name, fid):
        json.dump({"name": name, "fleet_order_id": fid}, open(os.path.join(self.q, filename), "w"))

    def test_the_exact_file_name_is_read(self):
        self.spec("tb-bi-qwen3.5-9b-th.20261011-0241_softserve.job.json", "tb-bi-qwen3.5-9b-th.20261011-0241_softserve",
                  "41908c229218")
        self.assertEqual(D.desk_id_for(self.out, self.root), "41908c229218")

    def test_a_spec_filed_without_the_suffix_is_found_by_its_name(self):
        self.spec("coldload-x.job.json", "coldload-x_softserve", "other")          # a neighbour is never taken
        self.spec("tb-bi-qwen3.5-9b-th.20261011-0241.job.json", "tb-bi-qwen3.5-9b-th.20261011-0241_softserve",
                  "41908c229218")
        self.assertEqual(D.desk_id_for(self.out + "\\", self.root), "41908c229218")

    def test_no_spec_naming_this_order_is_none(self):
        self.spec("coldload-x.job.json", "coldload-x_softserve", "other")
        self.assertIsNone(D.desk_id_for(self.out, self.root))


class WindowEffortLevel(unittest.TestCase):
    """A pinned window sends the effort LEVEL when the team names one: a bare --think is MEDIUM on qwen3.8."""

    def body(self, **team):
        unc = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, unc, ignore_errors=True)
        d = D.WindowDelegator(dict(TEAM, **team), "tbwin-x", unc, lambda: "tok", lambda: None)
        d.items_dir = os.path.join(unc, "windows", "w")
        return d._window_body("tbwin-x")["spec"]["args"]

    def test_a_named_level_is_sent_and_a_bare_think_is_not(self):
        args = self.body(helper_think=True, helper_think_level="xhigh")
        self.assertEqual(args[args.index("--think-level") + 1], "xhigh")
        self.assertNotIn("--think", args)

    def test_thinking_without_a_level_stays_a_bare_think_and_off_sends_neither(self):
        self.assertIn("--think", self.body(helper_think=True))
        off = self.body(helper_think=False)
        self.assertNotIn("--think", off)
        self.assertNotIn("--think-level", off)


if __name__ == "__main__":
    unittest.main()
