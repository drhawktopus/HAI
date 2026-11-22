import json
import os
import tempfile
import unittest

import config
import app


_COMMISSION = {"id": "ep-unify", "title": "Unify overnight onto deliberation", "kind": "commission",
               "objective": "route the overnight ladder through deliberation.py",
               "integration_acceptance": "an integration test proves a multi-pass run converges; suite green",
               "workspace": "orchestrator-real", "depends_on": [], "parent_id": "", "approved_overnight": True}

_PLAN_JSON = json.dumps({"leaves": [
    {"id": "l1", "title": "extract helper", "objective": "pull X into deliberation",
     "acceptance": "unit test for X passes", "depends_on": [], "integration": False},
    {"id": "l2", "title": "route ladder", "objective": "call deliberation from ladder",
     "acceptance": "unit test for routing passes", "depends_on": ["l1"], "integration": False},
    {"id": "lI", "title": "integration", "objective": "prove composition",
     "acceptance": "integration test green", "depends_on": ["l1", "l2"], "integration": True}]})
_PASS = json.dumps({"verdict": "pass", "findings": []})


class PlanSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = {
            "plans": config.SELFENG_PLANS_DIR, "admins": set(config.ADMIN_USERS),
            "chat": app.ollama_client.chat, "ens": app._ensure_lead,
            "find": app.backlog.find_item, "commissions": app.backlog.list_commissions,
        }
        config.SELFENG_PLANS_DIR = self.tmp
        config.ADMIN_USERS.add("Alex")
        app._ensure_lead = lambda *_a, **_k: True   # wake "succeeds" so the deps proceed to the (fake) chat
        app.backlog.find_item = lambda i: dict(_COMMISSION) if i == _COMMISSION["id"] else None
        app.backlog.list_commissions = lambda: [dict(_COMMISSION)]

        def fake_chat(host, model, messages, tools, num_ctx, user=None, **kw):
            if model == config.OVERNIGHT_PLAN_CRITIC_MODEL:
                return _PASS, None
            return _PLAN_JSON, None          # planner

        app.ollama_client.chat = fake_chat
        app._PLAN_SESSIONS.clear()

    def tearDown(self):
        config.SELFENG_PLANS_DIR = self._orig["plans"]
        config.ADMIN_USERS.clear(); config.ADMIN_USERS.update(self._orig["admins"])
        app.ollama_client.chat = self._orig["chat"]
        app._ensure_lead = self._orig["ens"]
        app.backlog.find_item = self._orig["find"]
        app.backlog.list_commissions = self._orig["commissions"]
        app._PLAN_SESSIONS.clear()

    def _q(self, text):
        return app.Q(text=text, user="Alex")

    def test_list_shows_commissions(self):
        r = app._handle_plan(self._q("/plan list"))
        self.assertIn("ep-unify", r["answer"])

    def test_start_decomposes_and_opens_session(self):
        r = app._handle_plan(self._q("/plan ep-unify"))
        self.assertEqual(r["verdict"], "ready")
        self.assertIn("lI", r["answer"])
        self.assertIn("Alex", app._PLAN_SESSIONS)
        # plan.md was written to the plans dir
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "ep-unify", "plan.md")))

    def test_plain_text_revises_active_session(self):
        app._handle_plan(self._q("/plan ep-unify"))
        r = app._handle_plan(self._q("make leaf 1 smaller"))
        self.assertTrue(r["answer"].startswith("Revised."))
        self.assertEqual(r["verdict"], "ready")

    def test_wake_failure_fails_fast_not_hang(self):
        # _ensure_lead returns False -> deps raise _LeadDown instead of calling the model
        # (which would hang 600s). The chat hook turns this into a clean message.
        app._ensure_lead = lambda *_a, **_k: False
        with self.assertRaises(app._LeadDown):
            app._handle_plan(self._q("/plan ep-unify"))

    def test_open_session_marks_busy(self):
        app._handle_plan(self._q("/plan ep-unify"))
        self.assertTrue(bool(app._PLAN_SESSIONS))          # open session -> busy predicate holds Mothership
        app._handle_plan(self._q("/plan cancel"))
        self.assertFalse(bool(app._PLAN_SESSIONS))

    def test_non_commission_is_rejected(self):
        app.backlog.find_item = lambda i: {"id": i, "kind": "test-verifiable", "objective": "x"}
        r = app._handle_plan(self._q("/plan something"))
        self.assertIn("not a commission", r["answer"])

    def test_cancel_closes_session(self):
        app._handle_plan(self._q("/plan ep-unify"))
        r = app._handle_plan(self._q("/plan cancel"))
        self.assertIn("closed", r["answer"].lower())
        self.assertNotIn("Alex", app._PLAN_SESSIONS)

    def test_approve_saves_and_exits(self):
        app._handle_plan(self._q("/plan ep-unify"))
        r = app._handle_plan(self._q("/plan approve"))
        self.assertIn("plan.yaml", r["answer"])
        self.assertNotIn("Alex", app._PLAN_SESSIONS)

    def test_start_includes_structured_plan_payload(self):
        # the CONTRACT the front-end card renders against (_plan_payload)
        r = app._handle_plan(self._q("/plan ep-unify"))
        p = r["plan"]
        self.assertEqual(p["commission_id"], "ep-unify")
        self.assertTrue(p["structural_clean"])
        ids = [lf["id"] for lf in p["leaves"]]
        self.assertIn("lI", ids)
        self.assertEqual(p["ordered_ids"][-1], "lI")          # integration leaf rendered last
        self.assertTrue(all("acceptance" in lf and "depends_on" in lf for lf in p["leaves"]))
        self.assertIn("verdict", p)
        self.assertIsInstance(p["open_findings"], list)


class NonAdminPlanningTests(unittest.TestCase):
    """Phase 4: planning is open to ALL, but a non-admin's plans live in an isolated, UNPRIVILEGED layer
    (their own dashboard) and their approve NEVER touches the self-eng backlog / SELFENG_PLANS_DIR."""

    def setUp(self):
        self.selfeng = tempfile.mkdtemp()
        self.userdir = tempfile.mkdtemp()
        self._orig = {
            "plans": config.SELFENG_PLANS_DIR, "userplans": config.USER_PLANS_DIR,
            "open": config.PLANNING_OPEN_TO_ALL, "admins": set(config.ADMIN_USERS),
            "chat": app.ollama_client.chat, "ens": app._ensure_lead,
            "find": app.backlog.find_item,
        }
        config.SELFENG_PLANS_DIR = self.selfeng
        config.USER_PLANS_DIR = self.userdir
        config.PLANNING_OPEN_TO_ALL = True
        config.ADMIN_USERS.discard("Jordan")            # Jordan is a plain user here
        app._ensure_lead = lambda *_a, **_k: True
        app.backlog.find_item = lambda i: None         # non-admins never touch the backlog anyway

        def fake_chat(host, model, messages, tools, num_ctx, user=None, **kw):
            return (_PASS, None) if model == config.OVERNIGHT_PLAN_CRITIC_MODEL else (_PLAN_JSON, None)
        app.ollama_client.chat = fake_chat
        app._PLAN_SESSIONS.clear()

    def tearDown(self):
        config.SELFENG_PLANS_DIR = self._orig["plans"]
        config.USER_PLANS_DIR = self._orig["userplans"]
        config.PLANNING_OPEN_TO_ALL = self._orig["open"]
        config.ADMIN_USERS.clear(); config.ADMIN_USERS.update(self._orig["admins"])
        app.ollama_client.chat = self._orig["chat"]
        app._ensure_lead = self._orig["ens"]
        app.backlog.find_item = self._orig["find"]
        app._PLAN_SESSIONS.clear()

    def _q(self, text):
        return app.Q(text=text, user="Jordan")

    def test_plans_dir_layer_separation(self):
        self.assertEqual(app._plans_dir_for("Alex"), config.SELFENG_PLANS_DIR)   # admin -> privileged layer
        self.assertTrue(app._plans_dir_for("Jordan").startswith(config.USER_PLANS_DIR))  # user -> own dir
        self.assertNotEqual(app._plans_dir_for("Jordan"), config.SELFENG_PLANS_DIR)

    def test_free_text_decomposes_for_non_admin(self):
        r = app._handle_plan(self._q("/plan build me a habit tracker"))
        self.assertEqual(r["verdict"], "ready")
        self.assertIn("test-verifiable leaves", r["answer"])
        self.assertIn("Jordan", app._PLAN_SESSIONS)
        self.assertTrue(r["plan"]["commission_id"].startswith("u-"))

    def test_non_admin_plan_never_writes_selfeng_dir(self):
        app._handle_plan(self._q("/plan build me a habit tracker"))
        app._handle_plan(self._q("/plan approve"))
        # nothing landed in the privileged self-eng plans dir
        self.assertEqual(os.listdir(self.selfeng), [])
        # it DID land in the user's own dashboard
        self.assertTrue(os.listdir(self.userdir))

    def test_non_admin_approve_is_dashboard_only(self):
        app._handle_plan(self._q("/plan build me a habit tracker"))
        r = app._handle_plan(self._q("/plan approve"))
        self.assertIn("your plans", r["answer"].lower())
        self.assertNotIn("promote.py", r["answer"])
        self.assertNotIn("backlog", r["answer"].lower())

    def test_load_reopens_saved_plan(self):
        start = app._handle_plan(self._q("/plan build me a habit tracker"))
        pid = start["plan"]["commission_id"]
        app._handle_plan(self._q("/plan approve"))
        r = app._handle_plan(self._q("/plan load " + pid))
        self.assertIn("Loaded your saved plan", r["answer"])
        self.assertIn("Jordan", app._PLAN_SESSIONS)
        self.assertEqual(r["plan"]["commission_id"], pid)

    def test_load_reads_legacy_epic_key_and_resaves_as_commission(self):
        # A plan.yaml saved before the rename carries the top-level "epic" key; loading it must
        # still work, and the load's own re-save migrates the file to the "commission" key.
        import yaml
        d = os.path.join(app._plans_dir_for("Jordan"), "legacy-plan-1")
        os.makedirs(d, exist_ok=True)
        legacy = {"epic": {"id": "legacy-plan-1", "title": "Legacy", "objective": "o",
                           "kind": "epic"},
                  "verdict": "ready", "structural_clean": True, "ordered_ids": ["l1"],
                  "leaves": [{"id": "l1", "title": "t", "objective": "o", "acceptance": "a",
                              "depends_on": [], "integration": False}]}
        with open(os.path.join(d, "plan.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump(legacy, fh)
        r = app._handle_plan(self._q("/plan load legacy-plan-1"))
        self.assertIn("Loaded your saved plan", r["answer"])
        self.assertEqual(r["plan"]["commission_id"], "legacy-plan-1")
        with open(os.path.join(d, "plan.yaml"), encoding="utf-8") as fh:
            resaved = yaml.safe_load(fh)
        self.assertIn("commission", resaved)
        self.assertEqual(resaved["commission"]["id"], "legacy-plan-1")

    def test_list_has_no_backlog_commissions_for_non_admin(self):
        app._handle_plan(self._q("/plan build me a habit tracker"))
        app._handle_plan(self._q("/plan approve"))
        r = app._handle_plan(self._q("/plan list"))
        self.assertNotIn("Backlog commissions", r["answer"])
        self.assertIn("saved plans", r["answer"].lower())


class DisputeDepTests(unittest.TestCase):
    """_plan_dispute_dep: the plan gate's fixer-dispute port. Author decides fix|dispute per
    finding; only disputed findings reach the adjudicator; an upheld dispute drops the finding,
    a rejected or unparseable exchange leaves it standing (fail-soft in every direction)."""

    FINDINGS = [{"severity": "high", "explanation": "leaf l1 cites the wrong anchor"}]

    def setUp(self):
        import decompose
        self.decompose = decompose
        self.leaves = [decompose.Leaf(id="l1", title="t", objective="o", acceptance="a",
                                      evidence="def f(): pass  # mod.py:5")]
        self.calls = {"author": 0, "adjudicate": 0}
        self._orig = {"chat": app.ollama_client.chat, "ens": app._ensure_lead}
        app._ensure_lead = lambda *_a, **_k: True

    def tearDown(self):
        app.ollama_client.chat = self._orig["chat"]
        app._ensure_lead = self._orig["ens"]

    def _wire(self, author_reply, adjudicate_reply):
        def fake_chat(host, model, messages, tools, num_ctx, user=None, **kw):
            system = messages[0]["content"]
            if system == self.decompose.PLAN_DISPUTE_AUTHOR_SYSTEM:
                self.calls["author"] += 1
                return author_reply, None
            if system == self.decompose.PLAN_DISPUTE_ADJUDICATE_SYSTEM:
                self.calls["adjudicate"] += 1
                return adjudicate_reply, None
            raise AssertionError("unexpected model call: " + system[:60])
        app.ollama_client.chat = fake_chat
        return app._plan_dispute_dep({"grounding": ""})

    def test_upheld_dispute_drops_the_finding(self):
        dep = self._wire(
            json.dumps([{"index": 0, "action": "dispute", "rationale": "the evidence quotes it"}]),
            json.dumps({"valid": True, "reason": "verified against the plan"}))
        self.assertEqual(dep(self.leaves, list(self.FINDINGS)), [])
        self.assertEqual(self.calls, {"author": 1, "adjudicate": 1})

    def test_rejected_dispute_keeps_the_finding(self):
        dep = self._wire(
            json.dumps([{"index": 0, "action": "dispute", "rationale": "vague"}]),
            json.dumps({"valid": False, "reason": "rationale does not hold"}))
        out = dep(self.leaves, list(self.FINDINGS))
        self.assertEqual(len(out), 1)

    def test_fix_action_skips_adjudication(self):
        dep = self._wire(
            json.dumps([{"index": 0, "action": "fix", "rationale": ""}]),
            json.dumps({"valid": True}))
        out = dep(self.leaves, list(self.FINDINGS))
        self.assertEqual(len(out), 1)
        self.assertEqual(self.calls["adjudicate"], 0)

    def test_malformed_author_reply_is_failsoft(self):
        dep = self._wire("no json here at all", json.dumps({"valid": True}))
        out = dep(self.leaves, list(self.FINDINGS))
        self.assertEqual(len(out), 1)
        self.assertEqual(self.calls["adjudicate"], 0)


if __name__ == "__main__":
    unittest.main()
