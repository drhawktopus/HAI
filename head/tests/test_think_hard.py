import json
import unittest

import config
import app
import deliberation


class ThinkHardPoolTests(unittest.TestCase):
    """The decorrelated CRITIC POOL + iterate-to-convergence loop in interactive think-hard."""

    def setUp(self):
        self._orig = {
            "chat": app.ollama_client.chat, "ens": app._ensure_lead,
            "pool": list(config.THINK_HARD_CRITIC_POOL),
            "passes": config.THINK_HARD_MAX_PASSES, "budget": config.THINK_HARD_BUDGET_SECONDS,
            "nbudget": config.NURSING_THINK_HARD_BUDGET_SECONDS,
        }
        app._ensure_lead = lambda *_a, **_k: True
        config.THINK_HARD_CRITIC_POOL = ["model-a", "model-b"]   # note: excludes HEAVY_MODEL (the re-check critic)
        config.THINK_HARD_MAX_PASSES = 12
        config.THINK_HARD_BUDGET_SECONDS = 1800
        config.NURSING_THINK_HARD_BUDGET_SECONDS = 1800
        self.critic_models = []      # pool critics that ran
        self.rechecks = 0            # single-critic convergence re-checks
        self.refines = 0             # refinement rounds
        self.medical_called = False
        self.pool_findings = 1       # findings each pool critic returns (0 => a clean draft)
        self.recheck_seq = [0]       # findings each successive re-check returns (0 => converged)
        self.recheck_i = 0

        def fake_chat(host, model, messages, tools, num_ctx, status_callback=None, user=None, **kw):
            sysmsg = messages[0].get("content") if messages else ""
            if sysmsg == deliberation.NURSING_CRITIQUE_SYSTEM:
                self.medical_called = True
                return json.dumps({"verdict": "changes_requested", "summary": "med",
                                   "findings": [{"severity": "high", "explanation": "clinical concern"}]}), None
            # startswith, not ==: the chat critics send CHAT_CRITIQUE_SYSTEM (CRITIQUE_SYSTEM plus the
            # G2b evidence-trail rules). Matching the shared prefix recognises both and does not
            # re-break when the chat suffix is tuned.
            if sysmsg.startswith(deliberation.CRITIQUE_SYSTEM):
                if model in config.THINK_HARD_CRITIC_POOL:          # a POOL critic
                    self.critic_models.append(model)
                    fs = [{"severity": "high", "explanation": "pool issue %d from %s" % (j, model)}
                          for j in range(self.pool_findings)]
                    return json.dumps({"verdict": "changes_requested" if fs else "pass",
                                       "summary": "s", "findings": fs}), None
                self.rechecks += 1                                  # a single-critic RE-CHECK
                n = self.recheck_seq[self.recheck_i] if self.recheck_i < len(self.recheck_seq) else 0
                self.recheck_i += 1
                fs = [{"severity": "high", "explanation": "residual %d" % j} for j in range(n)]
                return json.dumps({"verdict": "changes_requested" if fs else "pass",
                                   "summary": "rc", "findings": fs}), None
            if "FINAL REFINEMENT" in sysmsg:
                self.refines += 1
            return "a complete, well-supported answer from " + str(model), None

        app.ollama_client.chat = fake_chat

    def tearDown(self):
        app.ollama_client.chat = self._orig["chat"]
        app._ensure_lead = self._orig["ens"]
        config.THINK_HARD_CRITIC_POOL = self._orig["pool"]
        config.THINK_HARD_MAX_PASSES = self._orig["passes"]
        config.THINK_HARD_BUDGET_SECONDS = self._orig["budget"]
        config.NURSING_THINK_HARD_BUDGET_SECONDS = self._orig["nbudget"]

    def _run(self, nursing=False, medical=False):
        q = app.Q(text="do x", user="Alex", effort_mode="think_hard", ephemeral=True, medical_critique=medical)
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "do x"}]
        return app._run_think_hard(q, "do x", messages, [], False, None, nursing_mode=nursing)

    def test_general_runs_decorrelated_pool(self):
        r = self._run()
        self.assertEqual(r["route"], "heavyweight")
        self.assertEqual(self.critic_models, ["model-a", "model-b"])   # both decorrelated critics ran
        self.assertEqual(self.refines, 1)                              # one refine, then re-check converged

    def test_nursing_runs_pool_and_medical_is_additive(self):
        self._run(nursing=True, medical=True)
        self.assertEqual(self.critic_models, ["model-a", "model-b"])   # full pool ran for nursing too
        self.assertTrue(self.medical_called)                          # medical added on top, not replacing

    def test_single_model_pool_is_unchanged(self):
        config.THINK_HARD_CRITIC_POOL = ["solo"]
        self._run()
        self.assertEqual(self.critic_models, ["solo"])

    def test_tight_budget_degrades_to_one_critic(self):
        config.THINK_HARD_MAX_PASSES = 2          # draft + one critic; no room for a 2nd critic or a refine
        self._run()
        self.assertEqual(self.critic_models, ["model-a"])
        self.assertEqual(self.refines, 0)

    def test_iterates_to_convergence(self):
        self.recheck_seq = [1, 0]                 # findings drop 2 -> 1 -> 0 (converged) over two rounds
        self._run()
        self.assertEqual(self.refines, 2)
        self.assertEqual(self.rechecks, 2)

    def test_clean_draft_skips_refinement(self):
        self.pool_findings = 0                    # pool finds nothing -> no refine loop at all (stays cheap)
        self._run()
        self.assertEqual(self.critic_models, ["model-a", "model-b"])   # pool still ran
        self.assertEqual(self.refines, 0)

    def test_stall_stops_early(self):
        self.recheck_seq = [2]                    # re-check finds the same count -> no progress -> stop
        self._run()
        self.assertEqual(self.refines, 1)         # exactly one round, then stalled (didn't burn the budget)
        self.assertEqual(self.rechecks, 1)


class EffortTelemetryTests(unittest.TestCase):

    def test_completion_logs_effort_tele_line(self):
        app_src = open("app.py", encoding="utf-8").read()
        body = app_src.split("def _run_think_hard(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("EFFORT_TELE", body)
        self.assertIn("state.elapsed_seconds", body)
        self.assertIn("effort_label", body)
        # fail-soft: the log call is wrapped so telemetry can never break an answer
        tele_idx = body.index('notes.log("EFFORT_TELE')
        self.assertIn("except Exception", body[tele_idx:tele_idx + 400])

    def test_transcript_wired_across_modes(self):
        # Assert source-level wiring: transcript collected in all paths and rendered by UI.
        app_src = open("app.py", encoding="utf-8").read()
        th_body = app_src.split("def _run_think_hard(", 1)[1].split("\ndef ", 1)[0]
        # think-hard returns the compact trail; the full trail is not serialized but remains in memory for critics.
        self.assertIn('"transcript": ollama_client.compact_trail(_tr_steps)', th_body)
        mo_body = app_src.split("def _dispatch_lead(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("transcript=_tr_steps", mo_body)             # both mothership passes collect
        self.assertIn("function steps(el, trail)", app_src)        # UI renderer exists
        self.assertIn("steps(el, d.transcript);", app_src)         # wired on answer routes
        self.assertIn("steps(el, rec.transcript);", app_src)       # wired on reopened Absurd

    def test_view_axis_present_and_presentation_only(self):
        # View axis changes presentation only; no request payload or query params.
        app_src = open("app.py", encoding="utf-8").read()
        self.assertIn('id="viewMode"', app_src)
        for opt in ('"normal"', '"thinking"', '"verbose"', '"summary"'):
            self.assertIn('<option value=' + opt, app_src)
        self.assertIn("body.view-summary .thoughts", app_src)       # Summary hides trails via CSS
        self.assertIn("p-thoughts", app_src)                        # Thinking opens journals only
        self.assertIn("localStorage.setItem('hai.view'", app_src)   # display pref persists
        self.assertNotIn("viewMode.value})", app_src)               # never serialized into a request
        self.assertNotIn("view_mode", app_src.split("CHAT_HTML", 1)[0])  # no backend param grew


if __name__ == "__main__":
    unittest.main()
