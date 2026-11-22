"""Tests for the render queue.

Verifies that the consent chokepoint gates correctly, denials do not leak existence,
and the queue round-trips successfully."""
import contextlib
import importlib
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import person_record
import p20_queue
import tools
import comfy_client
import energy


PEOPLE = {
    "avery": {
        "name": "Avery",
        "consent": {"granted": True,
                    "scope": {"uses": ["personal", "named"], "allowlist": ["Alex", "Avery"]}},
        "models": [{"kind": "image_lora", "status": "ready", "trigger": "g4bi",
                    "path": "F:\\out\\avery.safetensors"}],
        "model_tier": "lora",
    },
    "testsubject": {
        "name": "Test Subject",
        "render_prefs": {"style_always": "candid photo, natural skin texture",
                         "fit_phrase": "with FITPHRASE",
                         "caption_scaffold": "a young woman"},
        "consent": {"granted": True,
                    "scope": {"uses": ["personal", "named"], "allowlist": ["Alex", "Testuser"]}},
        "models": [{"kind": "image_lora", "status": "ready", "trigger": "t3stsub",
                    "path": "F:\\out\\testsubject.safetensors"}],
        "model_tier": "lora",
    },
    "riley": {
        "name": "Riley",
        "consent": {"granted": True,
                    "scope": {"uses": ["personal", "named"], "allowlist": ["Alex", "Riley"]}},
        "models": [],
        "model_tier": "none",
    },
}


class _P20Base(unittest.TestCase):
    """Shared fixture: fake people, temp queue, silenced notes log."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.queue_dir = os.path.join(self.tmp.name, "queue")
        self.patches = [
            mock.patch.object(person_record, "_load_all", return_value=json.loads(json.dumps(PEOPLE))),
            mock.patch.object(p20_queue, "QUEUE_DIR", self.queue_dir),
            mock.patch.object(tools.notes, "log", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()

    def _render(self, user, person, scene="laughing at a picnic, golden hour"):
        with mock.patch.object(comfy_client, "quota_remaining", return_value=None),              mock.patch.object(comfy_client, "record_use", mock.Mock()):
            return tools._render_person(user, {"person": person, "scene": scene})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class P20RenderTests(_P20Base):
    # --- resolve ---------------------------------------------------------------------------

    def test_resolve_key_name_case_and_miss(self):
        self.assertEqual(person_record.resolve("avery"), "avery")
        self.assertEqual(person_record.resolve("Avery"), "avery")
        self.assertEqual(person_record.resolve("  TESTSUBJECT "), "testsubject")
        self.assertIsNone(person_record.resolve("Beyonce"))
        self.assertIsNone(person_record.resolve(""))

    # --- library_people: consent-filtered, no leakage --------------------------------------

    def test_library_people_filters_by_requester(self):
        out_alex = tools._library_people("Alex")
        self.assertIn("Avery", out_alex)
        self.assertIn("Test Subject", out_alex)
        out_avery = tools._library_people("Avery")
        self.assertIn("Avery", out_avery)
        self.assertNotIn("Test Subject", out_avery)     # not on the subject's allowlist -> absent, not "denied"
        out_stranger = tools._library_people("Jordan")
        self.assertNotIn("Avery", out_stranger)
        self.assertNotIn("Test Subject", out_stranger)

    # --- render_person: the chokepoint -----------------------------------------------------

    def test_allowed_user_queues_job(self):
        out = self._render("Alex", "Avery")
        self.assertIn("Queued", out)
        jobs = p20_queue.pending()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["person"], "avery")
        self.assertEqual(jobs[0]["use"], "named")
        self.assertTrue(jobs[0]["prompt"].startswith("g4bi, "))
        self.assertTrue(jobs[0]["rush"])

    def test_self_request_is_personal_use(self):
        out = self._render("Avery", "Avery")
        self.assertIn("Queued", out)
        self.assertEqual(p20_queue.pending()[0]["use"], "personal")

    def test_denied_and_unknown_read_identically(self):
        denied = self._render("Jordan", "Test Subject")   # exists, Jordan not on allowlist
        unknown = self._render("Jordan", "Beyonce") # doesn't exist
        self.assertEqual(denied, unknown)          # existence never leaks
        self.assertEqual(p20_queue.pending(), [])  # and nothing queued

    def test_no_model_yet_is_a_distinct_friendly_answer(self):
        out = self._render("Alex", "Riley")
        self.assertIn("no trained image model", out)
        self.assertEqual(p20_queue.pending(), [])

    def test_pending_cap_three_per_user(self):
        for _ in range(3):
            self._render("Alex", "Avery")
            p20_queue.pending("Alex")  # distinct jobs each call
        self.assertEqual(len(p20_queue.pending("Alex")), 3)
        out = self._render("Alex", "Test Subject")
        # Assert the CAP and the refusal state, not the sentence - pinned wording drifts silently.
        self.assertNotIn("Queued!", out)
        self.assertIn("cap of 3 pending", out)
        self.assertEqual(len(p20_queue.pending("Alex")), 3)

    def test_quota_exhausted_refuses(self):
        with mock.patch.object(comfy_client, "quota_remaining", return_value=1):
            out = tools._render_person("Avery", {"person": "Avery", "scene": "x"})   # allowed requester, broke quota
        self.assertIn("quota", out)

    # --- queue mechanics --------------------------------------------------------------------

    def test_queue_roundtrip_and_energy_gate(self):
        j = p20_queue.enqueue("Alex", "avery", "g4bi, x", "F:\\out\\avery.safetensors", "g4bi",
                              use="named", consent_reason="ok", rush=False)
        self.assertEqual(p20_queue.get(j["id"])["status"], "pending")
        self.assertEqual(p20_queue.dispatchable(energy_ok=False), [])          # deferred at peak
        self.assertEqual(len(p20_queue.dispatchable(energy_ok=True)), 1)
        rush = p20_queue.enqueue("Alex2", "avery", "g4bi, y", "p", "g4bi",
                                 use="named", consent_reason="ok", rush=True)
        ids = [x["id"] for x in p20_queue.dispatchable(energy_ok=False)]
        self.assertEqual(ids, [rush["id"]])                                    # rush bypasses energy
        p20_queue.mark(j["id"], "done", image="a.png")
        self.assertEqual(p20_queue.get(j["id"])["status"], "done")
        self.assertEqual(len(p20_queue.pending()), 1)


class RenderStatusTests(_P20Base):
    def test_status_no_jobs(self):
        self.assertIn("no identity renders", tools._render_status("Morgan"))

    def test_status_done_and_pending_with_gpu_heartbeat(self):
        self._render("Avery", "Avery")
        job = p20_queue.pending("Avery")[0]
        with mock.patch.object(p20_queue, "gpu_status",
                               return_value={"state": "training", "detail": "run", "eta": "ETA: finish ~04:55"}),              mock.patch.object(energy, "is_runnable_now", return_value=(True, {})):
            out = tools._render_status("Avery")
        self.assertIn("queued behind a training run", out)
        self.assertIn("04:55", out)
        p20_queue.mark(job["id"], "done", image="x.png")
        out2 = tools._render_status("Avery")
        self.assertIn("DONE", out2)

    def test_status_stale_worker_is_honest(self):
        self._render("Avery", "Avery")
        with mock.patch.object(p20_queue, "gpu_status", return_value=None),              mock.patch.object(energy, "is_runnable_now", return_value=(True, {})):
            out = tools._render_status("Avery")
        self.assertIn("hasn't reported in", out)


class RenderPrefsTests(_P20Base):
    def _render_args(self, user, args):
        with mock.patch.object(comfy_client, "quota_remaining", return_value=None),              mock.patch.object(comfy_client, "record_use", mock.Mock()):
            return tools._render_person(user, args)

    def test_style_always_auto_applied(self):
        self._render_args("Alex", {"person": "Test Subject", "scene": "reading in a library"})
        p = p20_queue.pending("Alex")[0]["prompt"]
        self.assertIn("candid photo, natural skin texture", p)
        self.assertNotIn("DD cup", p)                      # fit NEVER applies by default

    def test_fit_applied_only_on_flag(self):
        self._render_args("Alex", {"person": "Test Subject", "scene": "reading in a library",
                                   "apply_fit_preference": True})
        p = p20_queue.pending("Alex")[0]["prompt"]
        self.assertIn("with FITPHRASE", p)
        self.assertTrue(p.startswith("t3stsub, a young woman, with FITPHRASE,"))

    def test_no_prefs_subject_unaffected(self):
        self._render_args("Alex", {"person": "Avery", "scene": "reading in a library",
                                   "apply_fit_preference": True})
        p = p20_queue.pending("Alex")[0]["prompt"]
        self.assertNotIn("DD cup", p)                      # no recorded phrase -> flag is inert

    # Mechanism: scene match suppresses `style_always` (independent of real vocabulary)
    # Uses a synthetic token so this check is independent of any extension's vocabulary.
    _STYLE_OFF_RULE = re.compile(r"(?i)\b(SUPPRESSTERM)\b")

    def _style_off_rule(self):
        """The style-suppressed scene rule is an extension hook (tools._ext_hook); this supplies a synthetic one."""
        real = tools._ext_hook
        rule = self._STYLE_OFF_RULE
        return mock.patch.object(tools, "_ext_hook", lambda name, default=None:
                                 rule if name == "STYLE_SUPPRESSED_SCENE" else real(name, default))

    def test_style_suppressed_on_a_named_scene_class_and_reply_says_so(self):
        with self._style_off_rule():
            out = self._render_args("Alex", {"person": "Test Subject",
                                             "scene": "SUPPRESSTERM in a library"})
        p = p20_queue.pending("Alex")[0]["prompt"]
        self.assertNotIn("candid photo", p)                # style_always suppressed
        self.assertIn("style preference is left off", out)  # ...and NOT silently
        self.assertIn("Queued", out)                        # informs, never blocks

    def test_style_still_applied_off_class(self):
        with self._style_off_rule():
            self._render_args("Alex", {"person": "Test Subject",
                                       "scene": "reading in a library"})
        p = p20_queue.pending("Alex")[0]["prompt"]
        self.assertIn("candid photo, natural skin texture", p)   # non-matching scene unchanged

    def test_fit_on_a_named_scene_class_still_applies_but_warns(self):
        with self._style_off_rule():
            out = self._render_args("Alex", {"person": "Test Subject",
                                             "scene": "SUPPRESSTERM in a library",
                                             "apply_fit_preference": True})
        p = p20_queue.pending("Alex")[0]["prompt"]
        self.assertIn("with FITPHRASE", p)                  # explicit request still honored
        self.assertIn("re-add a garment", out)              # ...with the measured warning


# Configurable regexes injected by the rules editor.
# Tests pin expected patterns to avoid tautologies: asserting live config would
# mask accidental disables. We assert the mechanism (enabled -> refuses;
# disabled -> flows) regardless of current runtime configuration.
_TYPE_A_ENABLED = {
    "_P20_BODY_MOD": re.compile(r"(?i)\b(TIER1TERM)\b"),
    "_P20_SEXUAL": re.compile(r"(?i)\b(TIER2TERM)\b"),
    "_P20_EXPLICIT": re.compile(r"(?i)\b(TIER3TERM|ATTIRETERM)\b"),
}
_TYPE_A_SENTINEL = re.compile(r"(?!)a")   # the editor's "disabled" form; matches nothing, ever
# The attire carve-out is always pinned to the synthetic token, in BOTH directions: it is not one of
# the three toggleable tiers, it is the exemption those tiers are evaluated against.
_ATTIRE_CARVEOUT = re.compile(r"(?i)\b(ATTIRETERM)\b")


@contextlib.contextmanager
def _type_a(enabled):
    """Pin the Type A patterns for the duration of a test (True = enforcing, False = disabled).

    Terms are SYNTHETIC tokens, not real vocabulary. The mechanism under test is "pattern matches ->
    refuse / pattern disabled -> flow", which is entirely independent of what the words are — so the
    tests prove the same thing without spelling anything out in source that would read badly out of
    context years later.
    """
    targets = (dict(_TYPE_A_ENABLED) if enabled
               else {k: _TYPE_A_SENTINEL for k in _TYPE_A_ENABLED})
    targets["_P20_ATTIRE_CARVEOUT"] = _ATTIRE_CARVEOUT
    with mock.patch.multiple(tools, **targets):
        yield


class BodyPolicyTests(_P20Base):
    def _r(self, user, person, scene):
        with mock.patch.object(comfy_client, "quota_remaining", return_value=None),              mock.patch.object(comfy_client, "record_use", mock.Mock()):
            return tools._render_person(user, {"person": person, "scene": scene})

    def test_tier1_self_always_flows(self):
        with _type_a(True):
            out = self._r("Avery", "Avery", "standing in a park, TIER1TERM, candid photo")
        self.assertIn("Queued", out)

    def test_tier1_other_blocked_by_default(self):
        with _type_a(True):
            out = self._r("Alex", "Avery", "walking downtown, TIER1TERM")
        self.assertIn("their own requests", out)
        self.assertEqual(p20_queue.pending(), [])

    def test_tier1_other_flows_with_allow_and_flags(self):
        people = json.loads(json.dumps(PEOPLE))
        people["avery"]["render_prefs"] = {"body_mods": "allow"}
        flagged = []
        with _type_a(True), mock.patch.object(person_record, "_load_all", return_value=people),              mock.patch.object(p20_queue, "flag_review",
                               side_effect=lambda *a: flagged.append(a)):
            out = self._r("Alex", "Avery", "walking downtown, TIER1TERM")
        self.assertIn("Queued", out)
        self.assertEqual(len(flagged), 1)
        self.assertIn("tier1term", flagged[0][3])   # _mods lowercases what the pattern matched

    def test_tier2_terms_never_free_text(self):
        with _type_a(True):
            out = self._r("Testuser", "Test Subject", "at a cafe, TIER2TERM")
        self.assertIn("can't come through scene text", out)

    def test_tier3_always_declined(self):
        with _type_a(True):
            out = self._r("Testuser", "Test Subject", "at home, TIER3TERM")
        self.assertIn("everyday/clothed", out)
        self.assertEqual(p20_queue.pending(), [])

    # --- the OTHER direction: with the tier disabled the same request must flow -----------------
    # Without these, "the pattern is off" and "the check is broken" look identical to the suite.
    def test_tier3_flows_when_tier_disabled(self):
        with _type_a(False):
            out = self._r("Testuser", "Test Subject", "at home, TIER3TERM")
        self.assertIn("Queued", out)
        self.assertEqual(len(p20_queue.pending()), 1)

    def test_tier2_flows_when_tier_disabled(self):
        with _type_a(False):
            out = self._r("Testuser", "Test Subject", "at a cafe, TIER2TERM")
        self.assertIn("Queued", out)

    def test_tier1_flows_when_tier_disabled(self):
        with _type_a(False):
            out = self._r("Alex", "Avery", "walking downtown, TIER1TERM")
        self.assertIn("Queued", out)


class MultiPersonTests(_P20Base):
    def test_second_library_person_in_scene_refused_honestly(self):
        with mock.patch.object(comfy_client, "quota_remaining", return_value=None),              mock.patch.object(comfy_client, "record_use", mock.Mock()):
            out = tools._render_person("Alex", {"person": "Avery",
                                                "scene": "and Riley on a beach vacation"})
        self.assertIn("one library person at a time", out)
        self.assertEqual(p20_queue.pending(), [])

    def test_generic_companion_is_fine(self):
        with mock.patch.object(comfy_client, "quota_remaining", return_value=None),              mock.patch.object(comfy_client, "record_use", mock.Mock()):
            out = tools._render_person("Alex", {"person": "Avery",
                                                "scene": "with a friend on a beach vacation, candid photo"})
        self.assertIn("Queued", out)


class AttireContextTests(_P20Base):
    def _r(self, user, person, scene):
        with mock.patch.object(comfy_client, "quota_remaining", return_value=None),              mock.patch.object(comfy_client, "record_use", mock.Mock()):
            return tools._render_person(user, {"person": person, "scene": scene})

    # Tests the CARVE-OUT LOGIC: aquatic scene + subject attire_policy.
    # Asserts the logic, not the operator's current tier setting.
    def _subject_with_policy(self):
        people = json.loads(json.dumps(PEOPLE))
        people["testsubject"]["render_prefs"]["attire_policy"] = "context-appropriate (subject ruling)"
        return people

    def test_attire_ok_in_context_with_policy(self):
        with _type_a(True), mock.patch.object(person_record, "_load_all",
                                              return_value=self._subject_with_policy()):
            out = self._r("Testuser", "Test Subject", "at the beach, ATTIRETERM, candid photo")
        self.assertIn("Queued", out)

    def test_attire_refused_without_context(self):
        with _type_a(True), mock.patch.object(person_record, "_load_all",
                                              return_value=self._subject_with_policy()):
            out = self._r("Testuser", "Test Subject", "ATTIRETERM in the kitchen")
        self.assertIn("everyday/clothed", out)

    def test_tier3_still_locked_in_context(self):
        # The carve-out strips ATTIRE terms only — a tier-3 directive must still trip the
        # tier even in an aquatic scene with the policy set. This is the carve-out's boundary.
        with _type_a(True), mock.patch.object(person_record, "_load_all",
                                              return_value=self._subject_with_policy()):
            out = self._r("Testuser", "Test Subject", "TIER3TERM at the beach")
        self.assertIn("everyday/clothed", out)

    def test_attire_refused_without_policy(self):
        with _type_a(True):
            out = self._r("Alex", "Avery", "at the beach, ATTIRETERM")
        self.assertIn("everyday/clothed", out)


if __name__ == "__main__":
    unittest.main()
