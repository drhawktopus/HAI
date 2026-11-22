"""S10c: the lead's delegate() on rigby becomes a Tolkien map order on the brief payload. The spec it writes passes
Tolkien's REAL validator, its items parse with the REAL tablet payload (one spelling, no seam), the inbox hand-off is
whole-file, and the lead waits on the order's own status: delivered / held / void / retired-without-delivery / stopped.
Plus the end-to-end seam: an order with a `team` block offers the lead the delegate tool and prices the pieces."""
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import tablet_foundry as REAL_TF                   # inside Tolkien's container: the deployed module
except ImportError:
    sys.path.insert(0, r"E:\SSDShared\tolkien-build")  # the mirror; a seam test that cannot import it FAILS
    import tablet_foundry as REAL_TF
import a1_fellowship as F  # noqa: E402
import test_a1_payload as TP  # noqa: E402
import a1_payload as P  # noqa: E402
import a1_task  # noqa: E402

TEAM = {"helper": "fellowship", "helper_model": "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M", "helper_ctx": 2048}
BRIEF = "Write `def add(a, b)` returning a + b.\nReturn only the code; no prose | no tests."


class Lane:
    """A scripted fleet lane: on each sleep() it acts on the inbox the way Tolkien's intake and runner would."""

    def __init__(self, tf, outcome="deliver", answer="def add(a, b):\n    return a + b\n"):
        self.tf, self.outcome, self.answer, self.specs = tf, outcome, answer, []

    def __call__(self, _s):
        names = [n for n in os.listdir(self.tf.INBOX) if n.endswith(".tjob.json")]
        assert not [n for n in os.listdir(self.tf.INBOX) if n.endswith(".tmp")], "a half-written spec left behind"
        for n in names:
            with open(os.path.join(self.tf.INBOX, n), encoding="utf-8") as f:
                spec = json.load(f)
            self.specs.append(spec)
            os.remove(os.path.join(self.tf.INBOX, n))
            if self.outcome == "held":
                open(os.path.join(self.tf.QUEUE_DIR, n + ".HELD"), "w").close()
                continue
            rows = [{"arm": "brief", "id": "brief", "answer": self.answer, "wall_s": 12.5, "error": "", "_host": "merry"},
                    {"arm": "control", "id": "control", "answer": "4", "control_result": "BRIEF_CONTROL_PASS",
                     "_host": "pippin"}]
            usable = self.outcome != "void"
            out = {"name": spec["name"], "usable": usable, "control_passed": usable, "control": "" if usable else
                   "control: sentinel not found", "coverage": "", "rows": rows}
            if self.outcome == "retired_no_delivery":
                res = os.path.join(self.tf.RESULTS_DIR, spec["name"] + ".20261006-090000.results.json")
                with open(res, "w", encoding="utf-8") as f:
                    json.dump(out, f)
                self.tf._RECENT_RETIRED[spec["name"]] = ("2026-10-06T09:00:00", os.path.basename(res))
            else:
                os.makedirs(os.path.dirname(spec["deliver_to"]), exist_ok=True)
                with open(spec["deliver_to"], "w", encoding="utf-8") as f:
                    json.dump(out, f)


class Delegation(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.logs = []
        self.tf = types.SimpleNamespace(INBOX=os.path.join(self.d, "inbox"), QUEUE_DIR=os.path.join(self.d, "queue"),
                                        RESULTS_DIR=os.path.join(self.d, "results"), _RECENT_RETIRED={},
                                        log=self.logs.append)
        for p in (self.tf.INBOX, self.tf.QUEUE_DIR, self.tf.RESULTS_DIR):
            os.makedirs(p)
        self.team = dict(TEAM, delegated_dir=os.path.join(self.d, "delegated"))

    def dlg(self, lane, **kw):
        d = F.FellowshipDelegator(self.team, "a1v22-rigby-moe-hai-off-spot-team-fe.20261006-0900", tf=self.tf,
                                  sleep=lane, clock=lambda: 100.0, **kw)
        d.exercise = "wordy"
        return d

    def test_the_spec_passes_tolkiens_validator_and_its_items_parse_with_the_tablet_payload(self):
        d = self.dlg(Lane(self.tf))
        spec = d.spec(BRIEF, "a1team-x-wordy-d1.20261006-090000")
        self.assertEqual(REAL_TF.spec_defects(spec), [])
        self.assertEqual((spec["kind"], spec["batch"], spec["notify"]), ("map", 1, "failures"))
        # the payload sits in the desk share's payloads folder, the share being where Tolkien's own INBOX is
        share = os.path.dirname(self.tf.INBOX).replace("\\", "/")
        self.assertEqual(spec["payload"], share + "/payloads/map_brief.py")
        self.assertEqual(F.desk_share(types.SimpleNamespace(INBOX="/ssdshared/tabfoundry-jobs/inbox")),
                         "/ssdshared/tabfoundry-jobs", "the lab's own layout resolves as it always did")
        self.assertEqual(spec["control"]["expect"], d.mb.CONTROL_SENTINEL)
        parsed = [d.mb.parse_item(x) for x in spec["items"] + [spec["control"]["item"]]]
        self.assertEqual([p[-1] for p in parsed], [None, None])
        self.assertEqual(parsed[0][5], BRIEF, "the brief round-trips byte for byte")
        self.assertEqual(parsed[0][4], 2048)
        self.assertEqual(parsed[1][0], d.mb.ARM_CONTROL)

    def test_delivered_answer_is_the_tool_result_and_the_piece_is_priced(self):
        lane = Lane(self.tf)
        d = self.dlg(lane)
        self.assertEqual(d(BRIEF), "def add(a, b):\n    return a + b\n")
        self.assertEqual(len(lane.specs), 1)
        p = d.pieces[0]
        self.assertEqual((p["status"], p["host"], p["helper_wall_s"], p["exercise"], p["k"]), ("done", "merry", 12.5, "wordy", 1))
        self.assertTrue(p["control_passed"])
        self.assertEqual(p["answer_chars"], len("def add(a, b):\n    return a + b\n"))
        self.assertTrue(any("piece 1" in m for m in self.logs))
        self.assertEqual(os.listdir(self.tf.INBOX), [])

    def test_a_held_piece_is_an_error_the_lead_can_read(self):
        d = self.dlg(Lane(self.tf, outcome="held"))
        self.assertTrue(d(BRIEF).startswith("ERROR: the helper order is held"))
        self.assertEqual(d.pieces[0]["status"], "held")

    def test_a_void_piece_is_never_handed_to_the_lead_as_an_answer(self):
        d = self.dlg(Lane(self.tf, outcome="void"))
        self.assertTrue(d(BRIEF).startswith("ERROR: the helper order was void"))
        self.assertEqual(d.pieces[0]["status"], "void")

    def test_retired_without_delivery_falls_back_to_the_runners_own_note(self):
        d = self.dlg(Lane(self.tf, outcome="retired_no_delivery"))
        self.assertEqual(d(BRIEF), "def add(a, b):\n    return a + b\n")

    def test_a_stop_slip_ends_the_wait(self):
        stop = threading.Event()

        def lane(_s):
            stop.set()                                   # the lead's order is stopped while the piece is out
        d = self.dlg(lane, stop_event=stop)
        self.assertTrue(d(BRIEF).startswith("ERROR: the run was stopped"))
        self.assertEqual(d.pieces[0]["status"], "stopped")

    def test_a_reviewer_delegator_names_its_order_as_a_review(self):
        # A1-review on this layer: the same delegator with prefix a1review- - its order name and `why` say review,
        # the spec is otherwise the drafting spec (one tablet, the control beside it, Tolkien's validator clean)
        lane = Lane(self.tf)
        d = self.dlg(lane, prefix="a1review-")
        d("## Exercise brief\n...\n## The solution files as they stand\n...")
        name = d.pieces[0]["order"]
        self.assertTrue(name.startswith("a1review-"), name)
        spec = lane.specs[-1]
        self.assertEqual(spec["name"], name)
        self.assertIn("A1-review", spec["why"])
        self.assertIn("reads the lead's brief", spec["why"])
        self.assertEqual(REAL_TF.spec_defects(spec), [])
        self.assertNotIn("A1-review", self.dlg(Lane(self.tf)).spec("p", "a1team-x").get("why"))

    def test_second_piece_gets_its_own_order_name(self):
        lane = Lane(self.tf)
        d = self.dlg(lane)
        d(BRIEF)
        d(BRIEF + " again")
        self.assertEqual(len({s["name"] for s in lane.specs}), 2)
        self.assertEqual([p["k"] for p in d.pieces], [1, 2])


class Stub:
    made = []
    review_says = "the function is missing the edge case the brief names: add(0, 0)"

    def __init__(self, team, order_name, stop_event=None, prefix="a1team-"):
        self.team, self.order_name, self.pieces, self.exercise = team, order_name, [], ""
        self.prefix = prefix
        Stub.made.append(self)

    def __call__(self, brief):
        self.last_brief = brief
        self.pieces.append({"k": 1, "brief_chars": len(brief), "submitted": 10.0, "done": 25.0, "status": "done"})
        return Stub.review_says if self.prefix == "a1review-" else "HELPER: " + brief


class TeamRun(unittest.TestCase):
    """The payload imports `a1_fellowship` INSIDE run_agentic (and drops the cached module at load), so a patch on the
    module object this file imported lands on a different object than the one the payload gets - the REAL delegator
    then ran, and its container path `/ssdshared/...` resolves on this drive to the real share: a
    test wrote `a1team-a1-test-control_add-d1` into Tolkien's real inbox. The fake is installed in sys.modules, where
    the payload's import resolves, and the real class is made unreachable for the duration."""

    def run_team(self, script, team=TEAM, **extra):
        seen = []
        spec = dict(TP.SPEC, team=team) if team else dict(TP.SPEC)
        spec.update(extra)
        fake = types.ModuleType("a1_fellowship")
        fake.FellowshipDelegator = Stub
        real_init = F.FellowshipDelegator.__init__

        def forbidden(self_, *a, **k):
            raise AssertionError("the REAL FellowshipDelegator was constructed by a test - it would write to the real inbox")
        with mock.patch.object(TP.LP, "_tolkien", return_value=TP.fake_server(script, seen)), \
             mock.patch.object(TP.LP, "_served_ctx", return_value=32768), \
             mock.patch.dict(sys.modules, {"a1_fellowship": fake}), \
             mock.patch.object(F.FellowshipDelegator, "__init__", forbidden):
            row = P.run_agentic("control_add", P.CONTROL_TASKS["control_add"], spec=spec, ctx=32768,
                                temperature=0.6, seed=3, log_dir=tempfile.mkdtemp())
        self.assertIs(F.FellowshipDelegator.__init__, real_init)
        return row, seen

    def test_a_team_order_offers_delegate_and_prices_the_piece(self):
        Stub.made.clear()
        _, solo = self.run_team([TP.WRITE, TP.FINISH], team=None)
        delegate = TP.reply([("delegate", {"brief": "write add"})])
        row, seen = self.run_team([delegate, TP.WRITE, TP.FINISH])
        self.assertEqual(seen[0]["n_tools"], solo[0]["n_tools"] + 1, "the one tool A1-solo does not have")
        self.assertEqual(len(Stub.made), 1)
        self.assertEqual(Stub.made[0].exercise, "control_add")
        self.assertEqual(row["team"], TEAM)
        self.assertEqual(len(row["delegations"]), 1)
        self.assertEqual(row["delegate_wait_s"], 15.0)
        self.assertTrue(row["ok"])
        self.assertIsNone(a1_task.DELEGATE["fn"], "the hook is cleared after the item")

    def test_a_directed_team_order_tells_the_lead(self):
        Stub.made.clear()
        _, seen = self.run_team([TP.WRITE, TP.FINISH], team=dict(TEAM, mode="directed"))
        self.assertTrue(seen[0]["first_user"].endswith(P.H.DIRECTED_BRIEF))
        _, seen = self.run_team([TP.WRITE, TP.FINISH], team=TEAM)
        self.assertNotIn(P.H.DIRECTED_BRIEF, seen[0]["first_user"], "organic: the tool is merely available")
        self.assertEqual(a1_task.DELEGATE, {"fn": None, "brief": "", "finish_gate": False, "review_fn": None},
                         "the hook is cleared after the item")

    def test_a_finish_gate_spec_reaches_the_lead_on_rigby(self):
        # the gate needs agentic-hai (its coding_test runs the lead's own tests); in plain agentic mode run_task refuses
        _, seen = self.run_team([TP.WRITE, TP.FINISH], team=None, finish_gate=True, a1_mode="agentic-hai")
        self.assertTrue(seen, "no model call: the gate was refused by run_task (wrong mode?)")
        self.assertTrue(seen[0]["first_user"].endswith(P.H.FINISH_GATE_BRIEF))
        self.assertFalse(a1_task.DELEGATE["finish_gate"], "the hook is cleared after the item")
        _, seen = self.run_team([TP.WRITE, TP.FINISH], team=None)
        self.assertNotIn(P.H.FINISH_GATE_BRIEF, seen[0]["first_user"])

    def test_a_solo_order_records_no_team(self):
        row, _ = self.run_team([TP.WRITE, TP.FINISH], team=None)
        self.assertIsNone(row["team"])
        self.assertIsNone(row["delegations"])
        self.assertIsNone(row["review_pieces"])

    def test_a_review_spec_has_a_tablet_read_the_artifact_once_and_prices_it(self):
        # A1-review on the Fellowship layer (the charter's one shape at every layer: the reviewer is a tablet): at the
        # lead's accepted finish the reviewer Stub (prefix a1review-) is asked once; its findings give one repair round;
        # the next finish is final; the review and its wait land on the row, apart from delegations
        Stub.made.clear()
        review = dict(TEAM, mode="review")
        row, seen = self.run_team([TP.WRITE, TP.FINISH, TP.FINISH], team=None, review=review)
        reviewers = [s for s in Stub.made if s.prefix == "a1review-"]
        self.assertEqual(len(reviewers), 1)
        self.assertEqual(reviewers[0].exercise, "control_add")
        self.assertEqual(len(reviewers[0].pieces), 1, "asked once per exercise")
        self.assertIn("## The solution files as they stand", reviewers[0].last_brief, "the reviewer got the artifact")
        self.assertIn("def add(a, b)", reviewers[0].last_brief, "the lead's file as it stood at finish")
        self.assertEqual(row["review"]["clean"], False)
        self.assertEqual(row["review"]["repaired"], False, "the lead finished again without a change")
        self.assertEqual(row["review"]["findings"], Stub.review_says)
        self.assertEqual(row["review_spec"], review)
        self.assertEqual(len(row["review_pieces"]), 1)
        self.assertEqual(row["review_wait_s"], 15.0)
        self.assertIsNone(row["delegations"], "a review is not a delegation")
        self.assertTrue(row["ok"])
        self.assertIsNone(a1_task.DELEGATE["review_fn"], "the hook is cleared after the item")


if __name__ == "__main__":
    unittest.main()
