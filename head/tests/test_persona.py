"""Phase 3 (UI redesign): persona = a reusable HAT, decoupled from identity.

Behavior (persona) is the SELECTED hat; identity (namespace/corpus/memory) is unchanged. The nursing path
must stay byte-identical for nursing users; new: any permitted user can wear General/Nursing/Law and stay
themselves (grounded in their OWN corpus, never another person's private uploads)."""
import types
import unittest
from pathlib import Path

import config

APP = Path("app.py").read_text(encoding="utf-8")

try:
    import app as _app
    _APP_IMPORTABLE = True
except Exception:
    _app = None
    _APP_IMPORTABLE = False


def _q(user="Alex", persona="", mode=""):
    return types.SimpleNamespace(user=user, persona=persona, mode=mode)


class DefaultPersonaTests(unittest.TestCase):
    def test_valid_personas(self):
        self.assertTrue(config.is_valid_persona("nursing"))
        self.assertTrue(config.is_valid_persona("law"))
        self.assertTrue(config.is_valid_persona("general"))
        self.assertFalse(config.is_valid_persona("wizard"))
        self.assertFalse(config.is_valid_persona(""))

    def test_defaults_per_user(self):
        self.assertEqual(config.default_persona_for("Riley"), "nursing")   # nursing namespace
        self.assertEqual(config.default_persona_for("Morgan"), "nursing")
        self.assertEqual(config.default_persona_for("Officer Parker"), "law")  # explicit map
        self.assertEqual(config.default_persona_for("Jordan"), "general")     # personal but not nursing
        self.assertEqual(config.default_persona_for("Alex"), "general")      # admin/homelab
        self.assertEqual(config.default_persona_for("Nobody"), "general")    # unknown -> general


class PersonaSharedCorpusTests(unittest.TestCase):
    """The two corpus layers: a persona-shared reference corpus unioned with the user's own corpus."""

    def test_law_namespace_registered_and_readonly(self):
        self.assertIn("law", config.RAG_NAMESPACES)                 # the shared Law corpus exists
        self.assertFalse(config.is_writable_namespace("law"))       # reference, not an upload target

    def test_shared_namespace_mapping(self):
        self.assertEqual(config.shared_namespace_for_persona("law"), "law")
        self.assertEqual(config.shared_namespace_for_persona("nursing"), "nursing_shared")  # nursing on two layers too
        self.assertIsNone(config.shared_namespace_for_persona("general"))

    def test_nursing_shared_corpus_registered_and_readonly(self):
        self.assertIn("nursing_shared", config.RAG_NAMESPACES)
        self.assertFalse(config.is_writable_namespace("nursing_shared"))
        # Preview grounds in the SHARED corpus, not a student's private one.
        self.assertEqual(config.NURSING_PREVIEW_NAMESPACE, "nursing_shared")

    @unittest.skipUnless(_APP_IMPORTABLE, "app not importable in this env")
    def test_grounding_unions_identity_and_shared(self):
        gn = _app._grounding_namespaces
        self.assertEqual(gn("jordan", "law"), ["jordan", "law"])   # own corpus + Law corpus
        self.assertEqual(gn(None, "law"), ["law"])               # Rowan: no identity ns -> just Law
        # Nursing now ALSO gets the shared layer: each student's own corpus + the shared nursing corpus.
        self.assertEqual(gn("nursing", "nursing"), ["nursing", "nursing_shared"])          # Riley
        self.assertEqual(gn("nursing_morgan", "nursing"), ["nursing_morgan", "nursing_shared"])  # Morgan
        self.assertEqual(gn("nursing_shared", "nursing"), ["nursing_shared"])              # preview: de-duped
        self.assertEqual(gn("homelab", "general"), ["homelab"])  # general unchanged
        self.assertEqual(gn(None, "general"), [])                # ungrounded stays ungrounded
        self.assertEqual(gn("law", "law"), ["law"])              # de-dupe if identity == shared


@unittest.skipUnless(_APP_IMPORTABLE, "app not importable in this env (heavy deps absent)")
class ResolvePersonaTests(unittest.TestCase):
    def test_explicit_persona_wins(self):
        self.assertEqual(_app._resolve_persona(_q(user="Jordan", persona="law"), "jordan"), "law")
        self.assertEqual(_app._resolve_persona(_q(user="Jordan", persona="nursing"), "jordan"), "nursing")

    def test_invalid_persona_falls_back(self):
        # Garbage persona -> back-compat/default, never an error.
        self.assertEqual(_app._resolve_persona(_q(user="Alex", persona="wizard"), "homelab"), "general")

    def test_legacy_nursing_user_still_nursing(self):
        # No persona field (old client) + a nursing identity -> nursing, exactly as before.
        self.assertEqual(_app._resolve_persona(_q(user="Riley", persona="", mode="ebp"), "nursing"), "nursing")

    def test_legacy_admin_preview_still_nursing(self):
        # No persona + admin picks a nursing sub-mode -> nursing preview, exactly as before.
        self.assertEqual(_app._resolve_persona(_q(user="Alex", persona="", mode="tutor"), "homelab"), "nursing")

    def test_legacy_general_stays_general(self):
        self.assertEqual(_app._resolve_persona(_q(user="Alex", persona="", mode=""), "homelab"), "general")

    def test_non_admin_cannot_legacy_preview(self):
        # A general user with no persona + a nursing sub-mode must NOT auto-enter nursing (that was
        # admin-only); they land on their default persona.
        self.assertEqual(_app._resolve_persona(_q(user="Jordan", persona="", mode="ebp"), "jordan"), "general")


class GroundedTestUserTests(unittest.TestCase):
    """Ephemeral GROUNDED test users so we test a grounded experience cleanly, never as a real profile."""

    def test_is_test_user(self):
        for u in ("test", "test-homelab", "TEST", "Test-Law"):
            self.assertTrue(config.is_test_user(u), u)
        for u in ("Alex", "Riley", "Jordan", "", None):
            self.assertFalse(config.is_test_user(u), u)

    def test_grounded_test_users_map_to_corpora(self):
        self.assertEqual(config.namespace_for_user("test-homelab"), "homelab")
        self.assertEqual(config.namespace_for_user("test-nursing"), "nursing_shared")
        self.assertEqual(config.namespace_for_user("test-law"), "law")
        self.assertIsNone(config.namespace_for_user("test"))   # plain test stays ungrounded

    def test_grounded_test_user_grounds(self):
        # test-homelab wearing General grounds in the homelab library (its own identity namespace).
        self.assertEqual(config.grounding_namespaces(config.namespace_for_user("test-homelab"), "general"),
                         ["homelab"])


class ToolCorpusLayerTests(unittest.TestCase):
    """search_docs / get_document (the ACTIVE tool path) must see the SAME two corpus layers as the passive
    seed-grounding — else a nursing user's search_docs would miss the shared nursing corpus."""

    TOOLS = Path("tools.py").read_text(encoding="utf-8")
    OLLAMA = Path("ollama_client.py").read_text(encoding="utf-8")

    def test_config_single_source_resolver(self):
        self.assertEqual(config.grounding_namespaces("jordan", "law"), ["jordan", "law"])
        self.assertEqual(config.grounding_namespaces("nursing", "nursing"), ["nursing", "nursing_shared"])
        self.assertEqual(config.grounding_namespaces(None, "general"), [])

    def test_search_docs_uses_two_layers(self):
        self.assertIn("def _corpus_namespaces(user, persona=None):", self.TOOLS)
        self.assertIn("for nsname in _corpus_namespaces(user, persona):", self.TOOLS)
        self.assertIn("def run_tool(name, args, user_text, user=None, persona=None):", self.TOOLS)

    def test_persona_threaded_through_chat(self):
        # robust to added kwargs (e.g. telemetry): just assert persona is a chat() param + threaded to run_tool
        self.assertIn("persona=None", self.OLLAMA)
        self.assertIn("tools.run_tool(fn, args, user_text, user=user, persona=persona)", self.OLLAMA)

    def test_app_persists_resolved_persona_and_passes_it(self):
        self.assertIn("q.persona = persona", APP)
        self.assertIn("persona=q.persona", APP)

    def test_corpus_namespaces_runtime(self):
        import tools
        self.assertEqual(tools._corpus_namespaces("Jordan", "law"), ["jordan", "law"])
        self.assertEqual(tools._corpus_namespaces("Riley", "nursing"), ["nursing", "nursing_shared"])
        self.assertEqual(tools._corpus_namespaces("Riley", None), ["nursing"])  # non-persona callers unchanged


class StaticWiringTests(unittest.TestCase):
    def test_law_persona_defined_and_selected(self):
        self.assertIn("LAW_SYSTEM = (", APP)
        self.assertIn("legal-information assistant", APP)
        self.assertIn("covering United States law with a FOCUS ON", APP)   # US law...
        self.assertIn("_LAW_STATE.upper()", APP)                           # ...with a focus on the state setting
        self.assertIn("PRIMARY METHOD", APP)                               # live web-search of authoritative sources
        self.assertIn("_LAW_SOURCES", APP)                                 # the state's sources are a setting too

    def test_law_state_is_a_setting(self):
        """The persona's state and its authoritative sources are settings, with a generic fallback when unset."""
        self.assertTrue(hasattr(config, "LAW_STATE") and hasattr(config, "LAW_STATE_SOURCES"))
        self.assertIn('config.LAW_STATE or "your state"', APP)
        self.assertNotIn("ilga.gov", APP)
        self.assertNotIn("Illinois", APP)
        self.assertIn("base_system = (_nursing_system(nmode) if nursing_mode else (LAW_SYSTEM if law_mode else SYSTEM + DOCTRINE)) + GROUNDING_GUARDRAILS", APP)

    def test_grounding_guardrails_shared_across_personas(self):
        self.assertIn("GROUNDING_GUARDRAILS = (", APP)
        self.assertIn("DON'T CLAIM ABSENCE FROM A PARTIAL LOOK", APP)
        self.assertIn("DON'T CLAIM 'NOTHING EXISTS' WITHOUT ACTUALLY SEARCHING", APP)
        self.assertIn("else SYSTEM + DOCTRINE)) + GROUNDING_GUARDRAILS", APP)  # appended for all personas

    @unittest.skip("Rule (3) is UNPROVEN — measured 2026-08-10 as having no effect, so nothing "
                   "should enforce it. Interleaved A/B on the 27b, 4 pairs, same question with a "
                   "known-correct answer: guardrail 1 right / 1 wrong / 2 mixed, control 2 right / "
                   "2 wrong. Both arms land evenly and the two arms WITHIN a pair mostly agree, so "
                   "something time-varying drives the answer, not the rule. (Earlier n=1 said 'it "
                   "works' and a BLOCKED n=3 said the exact reverse, 3-0 each way — both noise.) "
                   "The rule stays in the prompt because it is conservative and free, but pinning "
                   "it would block its removal and imply evidence we do not have. Re-enable only "
                   "if someone measures an effect. Detail: HAI-Orchestrator-Internals.md.")
    def test_guardrails_forbid_inventing_a_mechanism(self):
        """Asserts that Rule (3) (forbid inventing mechanisms) is present in GROUNDING_GUARDRAILS.

        Checks the constant's VALUE rather than source text to ensure the rule reaches the model
        and is robust to rewrapping."""
        if not _APP_IMPORTABLE:
            self.skipTest("app not importable in this environment")
        g = _app.GROUNDING_GUARDRAILS
        for rule in ("(1)", "(2)", "(3)"):
            self.assertIn(rule, g, "guardrail %s missing from the constant" % rule)
        self.assertIn("DON'T INVENT A MECHANISM YOUR SOURCES DON'T CONTAIN", g)
        # The two halves that make it actionable rather than a scold: label the ungrounded part,
        # and hand back the cheapest experiment instead of a guess.
        self.assertIn("HYPOTHESIS TO TEST", g)
        self.assertIn("CHEAPEST EXPERIMENT", g)
        # It must reach every persona, not just the general one.
        self.assertIn("else SYSTEM)) + GROUNDING_GUARDRAILS", APP)

    def test_grounding_union_wired(self):
        self.assertIn("def _grounding_namespaces(identity_ns, persona, user=None):", APP)   # + the owner's uploads
        self.assertIn("_spaces = _grounding_namespaces(ns, persona, q.user)", APP)
        self.assertIn("_found = rag.search(t, k=8 if force_docs else 4, namespace=_sns)", APP)

    def test_persona_decoupled_from_identity(self):
        self.assertIn("identity_ns = config.namespace_for_user(q.user)", APP)
        self.assertIn("persona = _resolve_persona(q, identity_ns)", APP)
        self.assertIn('nursing_mode = (persona == "nursing")', APP)
        self.assertIn('law_mode = (persona == "law")', APP)

    def test_privacy_guarantee_preview_admin_only(self):
        # Wearing nursing without a nursing identity re-grounds to the shared corpus ONLY for admins.
        self.assertIn("if nursing_mode and not config.is_nursing_namespace(identity_ns):", APP)
        self.assertIn("if q.user in config.ADMIN_USERS and config.NURSING_PREVIEW_NAMESPACE:", APP)

    def test_q_has_persona_field(self):
        self.assertIn("persona: str = \"\"", APP)

    def test_modes_exposes_default_personas(self):
        self.assertIn('"default_personas": {u: config.default_persona_for(u) for u in roster.all_profiles()}', APP)


if __name__ == "__main__":
    unittest.main()
