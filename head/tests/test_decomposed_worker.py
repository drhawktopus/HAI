"""The decomposed-driver worker (#12 P2): routing, fail-closed chain, mechanical leaf verdicts.

A leaf lands only when the workspace HEAD moved - the answer text is never the verdict. All
collaborators are patched; nothing here reaches a model, the runner, or the absurd store on
disk."""
import unittest
from unittest import mock

import app
import config


MACHINE = "pintest-machine"

DECOMPOSED = ("SUPERVISED LOCAL CODING JOB\nSupervisor: Claude\n"
              "Workspace: orchestrator-real\nMode: decomposed edit-test-review\n\nCOMMISSION: x")
PLAIN = ("SUPERVISED LOCAL CODING JOB\nSupervisor: Claude\n"
         "Workspace: orchestrator-real\nMode: isolated edit-test-review\n\nCOMMISSION: x")


class Routing(unittest.TestCase):

    def setUp(self):
        p = mock.patch.object(config, "MACHINE_USERS", {MACHINE})
        p.start()
        self.addCleanup(p.stop)

    def test_machine_decomposed_packet_routes_to_the_driver(self):
        self.assertTrue(app._is_decomposed_job(MACHINE, DECOMPOSED))

    def test_plain_supervised_packet_does_not(self):
        self.assertFalse(app._is_decomposed_job(MACHINE, PLAIN))

    def test_human_decomposed_text_does_not(self):
        self.assertFalse(app._is_decomposed_job("pintest-human", DECOMPOSED))


class _WorkerCase(unittest.TestCase):
    """Drive _decomposed_worker with every collaborator patched and a scripted HEAD."""

    def setUp(self):
        self.updates = []
        self.heads = []           # scripted: each _head() call pops the next value
        self.cores = []           # each _run_query_core call recorded
        self.abandoned = []

        class FakeStore:
            def update(s, run_id, **kw):
                self.updates.append(kw)
            def get(s, run_id):
                return {"run_id": run_id}

        def fake_core(q, status=None):
            self.cores.append(q.text)
            return {"answer": "survey/leaf answer"}

        def fake_git(workspace, action, limit=10):
            return {"entries": [self.heads.pop(0) if self.heads else "same"]}

        def fake_task(action, **kw):
            if action == "list":
                return {"tasks": [{"id": "stale1", "status": "active"}]}
            if action == "abandon":
                self.abandoned.append(kw.get("task_id"))
                return {"status": "cancelled"}
            return {}

        self._patch(app, "ABSURD", FakeStore())
        self._patch(app, "_run_query_core", fake_core)
        self._patch(app.coding_client, "git", fake_git)
        self._patch(app.coding_client, "task", fake_task)
        self._patch(app, "_absurd_notify", lambda rec: None)
        self._patch(app, "_plan_propose_dep", lambda session: (lambda *a, **k: ""))
        self._patch(app, "_plan_critic_dep", lambda session: (lambda *a, **k: ""))

    def _patch(self, obj, attr, value):
        p = mock.patch.object(obj, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def _plan(self, verdict="ready", n=2):
        """Trail shape mirroring emit_plan's REAL vocabulary: success is "ready", not-clean is
        "needs_review" - a fake speaking an invented dialect lets a wrong literal pass its own pins."""
        leaves = [{"id": "l%d" % k, "title": "leaf %d" % k, "objective": "o", "acceptance": "a",
                   "evidence": "snippet  # f.py:1", "depends_on": [], "integration": k == n}
                  for k in range(1, n + 1)]
        return {"leaves": leaves, "ordered_ids": ["l%d" % k for k in range(1, n + 1)],
                "verdict": verdict, "rounds": [], "structural_clean": verdict == "ready"}

    def run_worker(self, trail):
        self._patch(app.decompose, "emit_plan", lambda **kw: trail)
        q = app.Q(text=DECOMPOSED, user=MACHINE, ephemeral=True)
        app._decomposed_worker("run-test", q)
        return [u for u in self.updates if "status" in u]

    def test_rejected_plan_never_executes_a_leaf(self):
        trail = self._plan(verdict="needs_review")
        trail["open_findings"] = [{"severity": "high", "explanation": "why it was refused"}]
        states = self.run_worker(trail)
        self.assertEqual(states[-1]["status"], "error")
        self.assertEqual(len(self.cores), 1)   # the gather pass only - no leaf ran
        plans = [u["plan"] for u in self.updates if "plan" in u]
        self.assertEqual(plans[-1]["open_findings"][0]["explanation"], "why it was refused")

    def test_leaves_land_when_head_moves(self):
        self.heads = ["h0", "h1", "h1", "h2"]  # leaf1: before h0 / after h1; leaf2: h1 -> h2
        states = self.run_worker(self._plan())
        self.assertEqual(states[-1]["status"], "done")
        self.assertEqual(len(self.cores), 3)   # gather + 2 leaves, no retries

    def test_stuck_leaf_retries_once_then_fails_closed(self):
        self.heads = ["same"] * 10             # HEAD never moves
        states = self.run_worker(self._plan())
        self.assertEqual(states[-1]["status"], "error")
        self.assertEqual(len(self.cores), 3)   # gather + attempt + retry, then STOP
        self.assertIn("stale1", self.abandoned)

    def test_answer_text_is_never_the_verdict(self):
        """A cover-letter 'success' with an unmoved HEAD is a FAILURE - the whole point."""
        self._patch(app, "_run_query_core",
                    lambda q, status=None: (self.cores.append(q.text) or
                                            {"answer": "Done! Merged successfully, all green."}))
        self.heads = ["same"] * 10
        states = self.run_worker(self._plan(n=1))
        self.assertEqual(states[-1]["status"], "error")

    def test_run_record_carries_phase_and_progress(self):
        """The homepage tile's coarse position: driver-owned phase/progress fields on the run
        record, immune to the inner think-hard chatter that overwrites last_status."""
        self.heads = ["h0", "h1", "h1", "h2"]
        self._patch(app.decompose, "emit_plan", lambda **kw: self._plan())
        app._decomposed_worker("run-phases", app.Q(text=DECOMPOSED, user=MACHINE, ephemeral=True))
        phases = [(u.get("phase"), u.get("progress")) for u in self.updates if "phase" in u]
        self.assertIn(("gathering", "survey"), phases)
        self.assertIn(("planning", "plan"), phases)
        self.assertIn(("executing", "leaf 1/2"), phases)
        self.assertIn(("executing", "leaf 2/2"), phases)
        self.assertEqual(phases[-1], ("done", "2/2 leaves"))

    def test_driver_opts_into_the_hardened_gate(self):
        """The driver requests the full gate - strict evidence, advisory pass, a live dispute
        callable - and persists the advisory findings on the run record. The interactive /plan
        flow gets none of these (its contract is pinned in test_plan_session)."""
        seen = {}

        def recorder(**kw):
            seen.update(kw)
            return self._plan()

        self._patch(app.decompose, "emit_plan", recorder)
        self.heads = ["h0", "h1", "h1", "h2"]
        app._decomposed_worker("run-gatekw", app.Q(text=DECOMPOSED, user=MACHINE, ephemeral=True))
        self.assertIs(seen.get("strict_evidence"), True)
        self.assertIs(seen.get("advisory_pass"), True)
        self.assertTrue(callable(seen.get("dispute")))
        plans = [u["plan"] for u in self.updates if "plan" in u]
        self.assertIn("advisory_findings", plans[-1])

    def test_gather_packet_triggers_the_survey_subset_and_leaves_do_not(self):
        """Producer/consumer: the worker's own gather packet must be the exact text the
        toolset derivation narrows on; leaf packets must keep the full set."""
        self.heads = ["h0", "h1", "h1", "h2"]
        self.run_worker(self._plan())
        with mock.patch.object(config, "MACHINE_USERS", {MACHINE}):
            self.assertIs(app._heavy_toolset(MACHINE, self.cores[0]),
                          app.tools.CODING_SURVEY_TOOLS)
            for leaf_text in self.cores[1:]:
                self.assertIs(app._heavy_toolset(MACHINE, leaf_text),
                              app.tools.CODING_JOB_TOOLS)


class PlainPathSweep(unittest.TestCase):
    """Machine-user supervised runs sweep non-terminal tasks on both terminal paths; humans and non-supervised machine runs do not. The sweep is fail-soft."""

    def setUp(self):
        self.updates = []
        self.abandoned = []
        self.notified = []

        class FakeStore:
            def update(s, run_id, **kw):
                self.updates.append(kw)
            def get(s, run_id):
                return {"run_id": run_id}

        def fake_task(action, **kw):
            if action == "list":
                return {"tasks": [{"id": "orphan1", "status": "active"},
                                  {"id": "done1", "status": "done"}]}
            if action == "abandon":
                self.abandoned.append(kw.get("task_id"))
                return {"status": "cancelled"}
            return {}

        self._patch(app, "ABSURD", FakeStore())
        self._patch(app, "_run_query_core", lambda q, status=None: {"answer": "fine"})
        self._patch(app.coding_client, "task", fake_task)
        self._patch(app, "_absurd_notify", lambda rec: self.notified.append(rec))
        self._patch(config, "MACHINE_USERS", {MACHINE})

    def _patch(self, obj, attr, value):
        p = mock.patch.object(obj, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def run_plain(self, user=MACHINE, text=PLAIN):
        app._absurd_worker("run-sweep-test", app.Q(text=text, user=user, ephemeral=True))
        return [u["status"] for u in self.updates if "status" in u]

    def test_machine_supervised_done_path_sweeps(self):
        states = self.run_plain()
        self.assertEqual(states[-1], "done")
        self.assertEqual(self.abandoned, ["orphan1"])
        self.assertEqual(len(self.notified), 1)

    def test_machine_supervised_error_path_sweeps(self):
        self._patch(app, "_run_query_core",
                    mock.Mock(side_effect=RuntimeError("boom")))
        states = self.run_plain()
        self.assertEqual(states[-1], "error")
        self.assertEqual(self.abandoned, ["orphan1"])

    def test_human_run_never_sweeps(self):
        self.run_plain(user="pintest-human")
        self.assertEqual(self.abandoned, [])

    def test_machine_non_supervised_run_never_sweeps(self):
        self.run_plain(text="ordinary big question, no packet heading")
        self.assertEqual(self.abandoned, [])

    def test_sweep_failure_keeps_the_terminal_state_and_notifies(self):
        self._patch(app.coding_client, "task",
                    mock.Mock(side_effect=RuntimeError("runner unreachable")))
        states = self.run_plain()
        self.assertEqual(states[-1], "done")
        self.assertEqual(len(self.notified), 1)


class EvidenceRoundTrip(unittest.TestCase):

    def test_leaves_from_items_preserves_evidence(self):
        """The trail's item dicts carry evidence; the Leaf rebuild must not drop it - the
        driver hands lf.evidence to every implementer."""
        import decompose
        leaf = decompose.Leaf(id="l1", title="t", objective="o", acceptance="a",
                              evidence="snippet  # f.py:3")
        item = leaf.as_backlog_item(parent_id="e", workspace="w")
        back = decompose._leaves_from_items([item])
        self.assertEqual(back[0].evidence, "snippet  # f.py:3")


class TheRealPlanMachinery(unittest.TestCase):
    """P3 integration: the worker through the REAL emit_plan/parse/validate/topo chain - only
    the two model deps and the execution collaborators are scripted. Proves the cross-seam
    wiring the unit pins mock away: a planner's JSON becomes ordered leaves whose evidence
    reaches the implementer packets, and the P1 evidence gate bites through the whole stack."""

    GOOD_PLAN = ('{"leaves": ['
                 '{"id": "l1", "title": "add helper", "objective": "add f()", '
                 '"acceptance": "unit green", "evidence": "def old(): pass  # mod.py:5", '
                 '"depends_on": [], "integration": false},'
                 '{"id": "l2", "title": "integrate", "objective": "wire f()", '
                 '"acceptance": "integration green", "depends_on": ["l1"], "integration": true}'
                 '], "notes": ""}')
    BARE_PLAN = GOOD_PLAN.replace('"evidence": "def old(): pass  # mod.py:5", ', "")

    def setUp(self):
        self.updates = []
        self.heads = []
        self.cores = []

        class FakeStore:
            def update(s, run_id, **kw):
                self.updates.append(kw)
            def get(s, run_id):
                return {"run_id": run_id}

        def fake_core(q, status=None):
            self.cores.append(q.text)
            return {"answer": "survey/leaf answer"}

        self._patch(app, "ABSURD", FakeStore())
        self._patch(app, "_run_query_core", fake_core)
        self._patch(app.coding_client, "git",
                    lambda workspace, action, limit=10: {"entries": [self.heads.pop(0)
                                                                     if self.heads else "same"]})
        self._patch(app.coding_client, "task", lambda action, **kw: {"tasks": []})
        self._patch(app, "_absurd_notify", lambda rec: None)
        self._patch(config, "MACHINE_USERS", {MACHINE})

    def _patch(self, obj, attr, value):
        p = mock.patch.object(obj, attr, value)
        p.start()
        self.addCleanup(p.stop)

    def _run(self, plan_json):
        self._patch(app, "_plan_propose_dep", lambda session: (lambda *a, **k: plan_json))
        self._patch(app, "_plan_critic_dep",
                    lambda session: (lambda *a, **k: '{"verdict": "pass", "findings": []}'))
        q = app.Q(text=DECOMPOSED, user=MACHINE, ephemeral=True)
        app._decomposed_worker("run-integration", q)
        return [u for u in self.updates if "status" in u]

    def test_planned_evidence_reaches_the_implementer_packet(self):
        self.heads = ["h0", "h1", "h1", "h2"]
        states = self._run(self.GOOD_PLAN)
        self.assertEqual(states[-1]["status"], "done")
        self.assertEqual(len(self.cores), 3)                     # gather + 2 leaves
        self.assertIn("def old(): pass  # mod.py:5", self.cores[1])
        self.assertIn("wire f()", self.cores[2])

    def test_an_evidence_less_plan_is_refused_before_any_leaf_runs(self):
        """The P1 structural gate, exercised through the real refine loop: the scripted planner
        never adds evidence, so the plan ends needs_review and the worker refuses to execute."""
        states = self._run(self.BARE_PLAN)
        self.assertEqual(states[-1]["status"], "error")
        self.assertEqual(len(self.cores), 1)                     # the gather pass only
