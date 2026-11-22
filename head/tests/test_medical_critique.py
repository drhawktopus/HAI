"""Tests for the optional MedGemma medical second-opinion critique in nursing think-hard.

`config` and `deliberation` are stdlib-only (checked directly); `app` wiring is verified statically
and, when importable (the in-container suite), behaviorally — confirming the second critique runs on
the medical model and only when the user opted in."""
import unittest
from pathlib import Path

import config
import deliberation

APP = Path("app.py").read_text(encoding="utf-8")

try:
    import app as _app
    _APP_IMPORTABLE = True
except Exception:
    _app = None
    _APP_IMPORTABLE = False


class MedicalCritiqueConfigTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(config.NURSING_CRITIQUE_MODEL, "medgemma:27b")
        self.assertTrue(config.NURSING_CRITIQUE_ENABLED)
        # Nursing needs room for draft + the general critic pool + the medical second opinion + a refine
        # (>= 5 passes) plus budget headroom for the MedGemma swap. (General think-hard separately gained a big
        # iterate-to-convergence ceiling, so nursing is no longer literally general+1.)
        self.assertGreaterEqual(config.NURSING_THINK_HARD_MAX_PASSES, 5)
        self.assertGreaterEqual(config.NURSING_THINK_HARD_BUDGET_SECONDS, 900)


class MergeCritiquesTests(unittest.TestCase):
    def test_changes_wins_and_findings_concatenate(self):
        primary = {"verdict": "pass", "summary": "general ok", "findings": [{"severity": "low"}]}
        secondary = {"verdict": "changes_requested", "summary": "drug dose wrong",
                     "findings": [{"severity": "high", "explanation": "x"}]}
        merged = deliberation.merge_critiques(primary, secondary, second_label="medical")
        self.assertEqual(merged["verdict"], "changes_requested")
        self.assertEqual(len(merged["findings"]), 2)
        # The medical finding is tagged so refinement/deep-trace can tell them apart.
        self.assertEqual(merged["findings"][1]["source"], "medical")
        self.assertIn("general ok", merged["summary"])
        self.assertIn("drug dose wrong", merged["summary"])

    def test_both_pass_is_pass(self):
        merged = deliberation.merge_critiques(
            {"verdict": "pass", "summary": "a", "findings": []},
            {"verdict": "pass", "summary": "b", "findings": []})
        self.assertEqual(merged["verdict"], "pass")

    def test_unavailable_does_not_force_changes(self):
        merged = deliberation.merge_critiques(
            {"verdict": "pass", "summary": "a", "findings": []},
            {"verdict": "unavailable", "summary": "", "findings": []})
        self.assertEqual(merged["verdict"], "pass")

    def test_critique_systems_share_json_contract(self):
        for sysprompt in (deliberation.CRITIQUE_SYSTEM, deliberation.NURSING_CRITIQUE_SYSTEM):
            self.assertIn("Return JSON with verdict", sysprompt)
            self.assertIn("findings", sysprompt)
        # The medical critic is told NOT to use tools (MedGemma can't tool-call).
        self.assertIn("do NOT use", deliberation.NURSING_CRITIQUE_SYSTEM)


class MedicalCritiqueWiringTests(unittest.TestCase):
    def test_request_field(self):
        self.assertIn("medical_critique: bool = False", APP)

    def test_think_hard_runs_medical_critique_gated(self):
        self.assertIn("def _run_think_hard(q, task_text, messages, relevant, context_compacted, status, nursing_mode=False):", APP)
        self.assertIn("nursing_mode and q.medical_critique and config.NURSING_CRITIQUE_ENABLED", APP)
        self.assertIn("config.NURSING_CRITIQUE_MODEL", APP)
        self.assertIn("deliberation.NURSING_CRITIQUE_SYSTEM", APP)
        self.assertIn("deliberation.merge_critiques(critique, medical", APP)
        # Nursing think-hard gets the larger pass/budget allowance.
        self.assertIn("config.NURSING_THINK_HARD_MAX_PASSES if nursing_mode", APP)
        # The auxiliary medical model unloads immediately so it never lingers as a stray resident model.
        self.assertIn('keep_alive="0"', APP)

    def test_nursing_mode_threaded_through_dispatch(self):
        self.assertIn("nursing_mode=False):", APP)  # _dispatch_to_lead signature
        self.assertIn("nursing_mode=nursing_mode)", APP)
        self.assertEqual(APP.count("nursing_mode=nursing_mode"), 7)

    def test_ui_checkbox(self):
        self.assertIn('id="medCritique"', APP)
        self.assertIn("medCritiqueWrap.style.display", APP)
        # Only shown under a heavy effort (Hard or Absurd) while the Nursing persona is active.
        self.assertIn("const medAvail = (heavy && nursing);", APP)
        self.assertIn("medical_critique:(effortVal==='think_hard' && medCritique.checked)", APP)


_CRITIQUE_JSON = ('{"verdict":"changes_requested","summary":"s",'
                  '"findings":[{"severity":"high","explanation":"e","recommended_action":"a"}]}')
_FINAL = "A sufficiently long, complete final answer that stands on its own and is not a regression."


@unittest.skipUnless(_APP_IMPORTABLE, "app not importable in this env (heavy deps absent)")
class MedicalCritiqueBehaviorTests(unittest.TestCase):
    def setUp(self):
        self._orig_ensure = _app._ensure_lead
        self._orig_chat = _app.ollama_client.chat
        _app._ensure_lead = lambda *_a, **_k: True

    def tearDown(self):
        _app._ensure_lead = self._orig_ensure
        _app.ollama_client.chat = self._orig_chat

    def _record_models(self):
        calls = []

        def fake_chat(host, model, msgs, tools_, num_ctx, status_callback=None, user=None, keep_alive=None,
                      timeout=None, persona=None, **kwargs):
            sysprompt = msgs[0]["content"] if msgs else ""
            calls.append({"model": model, "system": sysprompt, "keep_alive": keep_alive})
            if "Return JSON with verdict" in sysprompt:
                return (_CRITIQUE_JSON, None)
            return (_FINAL, None)

        _app.ollama_client.chat = fake_chat
        return calls

    def _run(self, medical_critique):
        q = _app.Q(text="What is the evidence on X?", user="Riley", ephemeral=True,
                   effort_mode="think_hard", mode="ebp", medical_critique=medical_critique)
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": q.text}]
        return _app._run_think_hard(q, q.text, messages, [], False, None, nursing_mode=True)

    def test_medical_critique_runs_on_medgemma_when_opted_in(self):
        calls = self._record_models()
        result = self._run(medical_critique=True)
        models = [c["model"] for c in calls]
        # First pass runs on the normal heavyweight model.
        self.assertEqual(models[0], config.HEAVY_MODEL)
        # The medical critique runs exactly once, on the medical model, with the medical critic prompt.
        self.assertEqual(models.count(config.NURSING_CRITIQUE_MODEL), 1)
        med_call = next(c for c in calls if c["model"] == config.NURSING_CRITIQUE_MODEL)
        self.assertEqual(med_call["system"], deliberation.NURSING_CRITIQUE_SYSTEM)
        # The auxiliary medical model is loaded with keep_alive="0" so it unloads at once (no lingering).
        self.assertEqual(med_call["keep_alive"], "0")
        self.assertEqual(result["route"], "heavyweight")

    def test_no_medical_critique_when_not_opted_in(self):
        calls = self._record_models()
        self._run(medical_critique=False)
        models = [c["model"] for c in calls]
        self.assertNotIn(config.NURSING_CRITIQUE_MODEL, models)


if __name__ == "__main__":
    unittest.main()
