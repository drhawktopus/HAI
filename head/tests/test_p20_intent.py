"""Behavioural tests for the P20 create-intent gate (`p20_intent`).

These tests verify behaviour, not implementation details (e.g., source text matching), ensuring
that refactoring `app.py` does not break the gate's logic.

Two classes of cases:
  * REGRESSIONS: Scenarios that previously caused incorrect routing/job queuing.
  * PRESERVED POSITIVES: Valid inputs from v2/v3/v4 widenings to prevent over-correction."""
import unittest

import p20_intent


class RegressionsThatQueuedJunkJobs(unittest.TestCase):

    def test_long_pasted_document_is_not_a_render_request(self):
        # The real shape: a research instruction followed by ~14 KB of pasted manual text.
        # "Summarise" is not a create verb, but the pasted body contains both "create" and
        # "image" somewhere, which was enough under the old two-independent-searches rule.
        body = ("Configure the following settings. Create a schedule for each area. "
                "The image quality depends on the selected resolution. ") * 200
        text = ("Below is text extracted from the Lorex N844 NVR user manual. Summarise ONLY "
                "what it actually says about email alert setup.\n\n---\n" + body)
        self.assertGreater(len(text), p20_intent.MAX_PROMPT_CHARS)
        ok, why = p20_intent.create_intent(text)
        self.assertFalse(ok, "a pasted document must not queue a render (%s)" % why)

    def test_explicit_refusal_to_generate_is_honoured(self):
        text = ("Cancel my queued image job bf8813f44b77. It was created by mistake. "
                "Do not generate any image.")
        ok, why = p20_intent.create_intent(text)
        self.assertFalse(ok, "'Do not generate any image' must not generate an image (%s)" % why)

    def test_negation_variants(self):
        for text in ("don't make a picture",
                     "do not render an image please",
                     "dont create any photos",
                     "never generate a picture of anyone"):
            self.assertFalse(p20_intent.wants_create(text), text)

    def test_distant_verb_and_noun_do_not_pair(self):
        # The core defect in miniature: a create verb and an image noun both present, far apart,
        # with no request between them. Under the old rule this returned True.
        text = ("Please create a summary of the meeting notes below and email it to the team. "
                "The attached spreadsheet has a column tracking how many support tickets each "
                "region logged last quarter, which we should discuss before the image "
                "rebranding project kicks off next month.")
        self.assertLess(len(text), p20_intent.MAX_PROMPT_CHARS, "must fail on proximity, not length")
        ok, why = p20_intent.create_intent(text)
        self.assertFalse(ok, "distant verb/noun must not pair (%s)" % why)


class PreservedPositives(unittest.TestCase):
    """Every phrasing an earlier widening was written to catch. Do not weaken without a reason."""

    def test_explicit_create_intent_phrase(self):
        self.assertTrue(p20_intent.wants_create("make a picture of Avery"))
        self.assertTrue(p20_intent.wants_create("generate an image of a mountain"))

    def test_verbless_noun_of_name_v3_volleyball_stranger(self):
        self.assertTrue(p20_intent.wants_create("now a photo of Avery playing volleyball"))

    def test_verb_plus_image_noun_without_of_bridge_v4(self):
        self.assertTrue(p20_intent.wants_create("make her a picture where she is laughing"))
        self.assertTrue(p20_intent.wants_create("render a portrait in the style of a painting"))

    def test_verb_plus_named_library_subject_v4(self):
        # _tok is None for pronoun-resolved subjects, so only an EXPLICIT name qualifies here.
        self.assertTrue(p20_intent.wants_create("render Avery in a leather jacket", subj_tok="Avery"))
        self.assertFalse(p20_intent.wants_create("render her in a leather jacket", subj_tok=None))

    def test_verb_plus_pronoun_plus_repeat_cue_v4(self):
        # The live 13:38 / 13:49 shape.
        self.assertTrue(p20_intent.wants_create("make another one of her"))

    def test_long_but_legitimate_request_still_degrades_safely(self):
        # A very long scene prompt returns False. That is ACCEPTABLE and deliberate: the chat path
        # still carries render_person/generate_image, so the model emits the tool call itself.
        # Pinned so the trade-off is a decision on record rather than a surprise.
        text = "make a picture of Avery " + ("wearing an extremely detailed outfit " * 120)
        self.assertGreater(len(text), p20_intent.MAX_PROMPT_CHARS)
        self.assertFalse(p20_intent.wants_create(text))


class KnownNonRequests(unittest.TestCase):
    def test_scene_followups_require_known_image_context_and_an_imperative(self):
        for text in ['Put her in a bookshop.','Show her wearing a blue coat.','Make the same character sitting by a window.']:
            self.assertTrue(p20_intent.create_intent(text,subject_context=True)[0],text)
            self.assertFalse(p20_intent.create_intent(text,subject_context=False)[0],text)
        for text in ['How can I make her happy?','Show her picture.','Do not put her in a bookshop.','What is an image reference?']:
            self.assertFalse(p20_intent.create_intent(text,subject_context=True)[0],text)


    def test_howto_question_about_rendering(self):
        # The reason "render" is absent from IMAGE_NOUN: verb+noun used to self-satisfy on one word.
        self.assertFalse(p20_intent.wants_create("how do I render a video in premiere"))

    def test_ordinary_conversation(self):
        for text in ("what is the weather tomorrow",
                     "can you summarise this document for me",
                     "cancel job bf8813f44b77"):
            self.assertFalse(p20_intent.wants_create(text), text)


class GuardsAreActuallyLoadBearing(unittest.TestCase):
    """Control: prove each guard is what rejects its case.

    Without this, a test could pass because some UNRELATED clause happened to return False, and
    the guard it is meant to cover could be silently broken. Each assertion names the reason
    string, so removing a guard fails here loudly instead of quietly.
    """

    def test_length_guard_names_itself(self):
        _, why = p20_intent.create_intent("make a picture of Avery " + ("x " * 1200))
        self.assertIn("MAX_PROMPT_CHARS", why)

    def test_negation_guard_names_itself(self):
        _, why = p20_intent.create_intent("Do not generate any image.")
        self.assertIn("negation", why)

    def test_positive_path_names_its_clause(self):
        ok, why = p20_intent.create_intent("make a picture of Avery")
        self.assertTrue(ok)
        self.assertTrue(why and not why.startswith("no"), why)

    def test_old_rule_would_have_failed_these(self):
        # The bug, reconstructed exactly: two independent searches over the whole message. If this
        # ever stops being True, the regression cases above have stopped being meaningful.
        text = ("Cancel my queued image job bf8813f44b77. It was created by mistake. "
                "Do not generate any image.")
        old_rule = bool(p20_intent.CREATE_VERB.search(text)
                        and p20_intent.IMAGE_NOUN.search(text))
        self.assertTrue(old_rule, "the old rule must match, or this test proves nothing")
        self.assertFalse(p20_intent.wants_create(text), "the new rule must not")


if __name__ == "__main__":
    unittest.main()
