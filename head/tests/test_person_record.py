"""Person-record store — the #19/#20 crossover identity substrate.

#19 heritage groundwork (schema-only) + #20 identity-library fields (models / arcface / consent).
BEHAVIOR still deferred: this exercises STORAGE + shape, not tier-gating / consent enforcement.
"""
import json
import pathlib
import tempfile
import unittest

import config
import person_record


class PersonRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "person-records.json"
        self._orig = getattr(config, "PERSON_RECORDS_FILE", None)
        config.PERSON_RECORDS_FILE = str(self.tmp)

    def tearDown(self):
        config.PERSON_RECORDS_FILE = self._orig

    # ── #19 groundwork (unchanged behavior) ─────────────────────────────────────────────
    def test_unknown_key_returns_defaults_not_error(self):
        rec = person_record.record("nobody")
        self.assertEqual(rec["heritage"], "")
        self.assertEqual(rec["model_tier"], "swap")      # reserved default
        self.assertFalse(rec["consent"]["granted"])
        self.assertEqual(rec["consent"]["scope"]["uses"], [])   # #20 shape, nothing granted
        self.assertEqual(rec["refs"], [])
        self.assertEqual(rec["models"], [])               # #20: was `lora: None`
        self.assertIsNone(rec["arcface_anchor"])

    def test_heritage_roundtrips_and_persists(self):
        person_record.set_heritage("alex-upload", "straight-to-wavy, not tightly coiled")
        self.assertEqual(person_record.get_heritage("alex-upload"),
                         "straight-to-wavy, not tightly coiled")
        raw = json.loads(self.tmp.read_text(encoding="utf-8"))
        self.assertIn("alex-upload", raw)
        self.assertEqual(raw["alex-upload"]["heritage"], "straight-to-wavy, not tightly coiled")

    def test_get_heritage_empty_when_unset(self):
        self.assertEqual(person_record.get_heritage("ghost"), "")

    def test_update_merges_and_rejects_unknown_fields(self):
        person_record.update("avery", heritage="dirty blond, dyed", name="Avery")
        person_record.update("avery", traits={"eyes": "hazel/green"})   # merge, not clobber
        rec = person_record.record("avery")
        self.assertEqual(rec["name"], "Avery")
        self.assertEqual(rec["heritage"], "dirty blond, dyed")
        self.assertEqual(rec["traits"], {"eyes": "hazel/green"})
        with self.assertRaises(KeyError):
            person_record.update("avery", not_a_field=1)

    def test_model_tier_reserved_defaults_swap(self):
        self.assertEqual(person_record.model_tier("nobody"), "swap")
        person_record.update("x", model_tier="bogus")
        self.assertEqual(person_record.model_tier("x"), "swap")   # invalid -> swap

    def test_identity_reads_heritage_from_record_as_fallback(self):
        import identity
        src = open("identity.py", encoding="utf-8").read()
        self.assertIn("HERITAGE_HINTS.get(up) or person_record.get_heritage(up)", src)

    # ── #20 identity-library fields (stored only; behavior deferred) ─────────────────────
    def test_add_model_appends_full_template(self):
        person_record.add_model("avery", base="flux2-dev", path="/f/avery_v1_1000.safetensors",
                                trigger="g4bi", steps=1000, rank=16, resolution=448, status="ready")
        person_record.add_model("avery", kind="video_lora", base="hunyuan-1.5")  # second model
        models = person_record.record("avery")["models"]
        self.assertEqual(len(models), 2)
        # first entry has the full template shape, provided fields set, defaults for the rest
        m0 = models[0]
        self.assertEqual(m0["kind"], "image_lora")     # template default
        self.assertEqual(m0["base"], "flux2-dev")
        self.assertEqual(m0["trigger"], "g4bi")
        self.assertEqual(m0["status"], "ready")
        self.assertIsNone(m0["quality"])               # template default
        self.assertEqual(models[1]["kind"], "video_lora")

    def test_add_model_rejects_unknown_field_and_bad_kind(self):
        with self.assertRaises(KeyError):
            person_record.add_model("avery", not_a_model_field=1)
        with self.assertRaises(ValueError):
            person_record.add_model("avery", kind="totally_bogus")

    def test_arcface_anchor_stores_pointer(self):
        person_record.set_arcface_anchor("avery", "/f/avery_arcface.npy",
                                         source_refs=["a.jpg", "b.jpg"], computed="2026-07-17")
        anc = person_record.record("avery")["arcface_anchor"]
        self.assertEqual(anc["vec_path"], "/f/avery_arcface.npy")
        self.assertEqual(anc["source_refs"], ["a.jpg", "b.jpg"])
        self.assertEqual(anc["computed"], "2026-07-17")

    def test_grant_consent_stores_and_validates(self):
        person_record.grant_consent("avery", ["named"], allowlist=["Alex", "Avery"],
                                    granted_by="Avery")
        c = person_record.record("avery")["consent"]
        self.assertTrue(c["granted"])
        self.assertEqual(c["scope"]["uses"], ["named"])
        self.assertEqual(c["scope"]["allowlist"], ["Alex", "Avery"])
        self.assertEqual(c["scope"]["granted_by"], "Avery")
        with self.assertRaises(ValueError):
            person_record.grant_consent("avery", ["public", "not_a_use"])

    def test_empty_consent_grant_is_not_granted(self):
        person_record.grant_consent("avery", [])
        self.assertFalse(person_record.record("avery")["consent"]["granted"])

    def test_full_revocation_clears_everything_and_binds_immediately(self):
        person_record.grant_consent("avery", ["personal", "named"], allowlist=["Alex"],
                                    granted_by="Avery")
        self.assertTrue(person_record.check_use("avery", "named", "Alex")[0])
        person_record.revoke_consent("avery", revoked_by="Avery", reason="changed my mind")
        c = person_record.record("avery")["consent"]
        self.assertFalse(c["granted"])
        self.assertEqual(c["scope"]["uses"], [])
        self.assertEqual(c["scope"]["allowlist"], [])
        allowed, why = person_record.check_use("avery", "named", "Alex")
        self.assertFalse(allowed)                      # binds on the very next check

    def test_partial_revocation_of_uses_keeps_the_rest(self):
        person_record.grant_consent("avery", ["personal", "named"], allowlist=["Alex"])
        person_record.revoke_consent("avery", uses=["named"], revoked_by="Avery")
        c = person_record.record("avery")["consent"]
        self.assertTrue(c["granted"])                  # personal still granted
        self.assertEqual(c["scope"]["uses"], ["personal"])
        self.assertEqual(c["scope"]["allowlist"], ["Alex"])   # untouched by use-revocation
        self.assertFalse(person_record.check_use("avery", "named", "Alex")[0])

    def test_partial_revocation_of_allowlist_user(self):
        person_record.grant_consent("avery", ["named"], allowlist=["Alex", "Riley"])
        person_record.revoke_consent("avery", remove_users=["Riley"], revoked_by="Avery")
        self.assertTrue(person_record.check_use("avery", "named", "Alex")[0])
        self.assertFalse(person_record.check_use("avery", "named", "Riley")[0])

    def test_revocation_appends_audit_history_with_prior_scope(self):
        person_record.grant_consent("avery", ["named"], allowlist=["Alex"], granted_by="Avery",
                                    granted_date="2026-07-23")
        person_record.revoke_consent("avery", revoked_by="Avery", reason="testing",
                                     revoked_date="2026-07-23T12:00:00Z")
        hist = person_record.record("avery")["consent_history"]
        self.assertEqual([h["action"] for h in hist], ["grant", "revoke"])
        rev = hist[-1]
        self.assertEqual(rev["by"], "Avery")
        self.assertEqual(rev["reason"], "testing")
        self.assertEqual(rev["date"], "2026-07-23T12:00:00Z")
        self.assertEqual(rev["revoked_uses"], ["named"])
        self.assertEqual(rev["removed_users"], ["Alex"])
        self.assertEqual(rev["prior_scope"]["uses"], ["named"])   # snapshot preserved

    def test_revoke_unknown_use_raises_and_changes_nothing(self):
        person_record.grant_consent("avery", ["named"], allowlist=["Alex"])
        with self.assertRaises(ValueError):
            person_record.revoke_consent("avery", uses=["not_a_use"])
        self.assertTrue(person_record.check_use("avery", "named", "Alex")[0])

    def test_revoke_on_unknown_person_is_safe_noop_shape(self):
        person_record.revoke_consent("ghost", revoked_by="admin")
        c = person_record.record("ghost")["consent"]
        self.assertFalse(c["granted"])
        self.assertEqual(len(person_record.record("ghost")["consent_history"]), 1)

    def test_consent_scope_normalized_on_partial_legacy_record(self):
        # a legacy/partial record with only {granted: True} still reads back a full scope shape
        person_record.update("legacy", consent={"granted": True})
        c = person_record.record("legacy")["consent"]
        self.assertTrue(c["granted"])
        self.assertEqual(c["scope"]["uses"], [])          # normalized, no KeyError for callers
        self.assertIn("allowlist", c["scope"])


if __name__ == "__main__":
    unittest.main()

class ConsentEnforcementTests(unittest.TestCase):
    """#20 Phase 1: the gates that make consent.scope BIND (fail-closed)."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "person-records.json"
        self._orig = getattr(config, "PERSON_RECORDS_FILE", None)
        config.PERSON_RECORDS_FILE = str(self.tmp)
        person_record.grant_consent("quinn", ["personal", "named"],
                                    allowlist=["Alex", "Quinn"], granted_by="Alex")
        person_record.add_model("quinn", kind="image_lora", status="ready", trigger="k1nzie")
        person_record.grant_consent("openperson", ["named"], allowlist=[])

    def tearDown(self):
        config.PERSON_RECORDS_FILE = self._orig

    def test_unknown_person_denied(self):
        ok, why = person_record.check_use("ghost", "named", "Alex")
        self.assertFalse(ok); self.assertIn("no consent", why)

    def test_unknown_use_denied(self):
        ok, why = person_record.check_use("quinn", "definitely_not_a_use", "Alex")
        self.assertFalse(ok); self.assertIn("unknown use", why)

    def test_ungranted_use_denied(self):
        ok, why = person_record.check_use("quinn", "public", "Alex")
        self.assertFalse(ok); self.assertIn("not granted", why)

    def test_allowlisted_user_allowed(self):
        self.assertTrue(person_record.check_use("quinn", "named", "Alex")[0])
        self.assertTrue(person_record.check_use("quinn", "personal", "Quinn")[0])

    def test_non_allowlisted_user_denied(self):
        ok, why = person_record.check_use("quinn", "named", "Jordan")
        self.assertFalse(ok); self.assertIn("allowlist", why)

    def test_anonymous_denied_when_allowlist_set(self):
        ok, _ = person_record.check_use("quinn", "named", None)
        self.assertFalse(ok)

    def test_empty_allowlist_admits_any_requester(self):
        self.assertTrue(person_record.check_use("openperson", "named", "Jordan")[0])

    def test_kind_gate_requires_ready_model(self):
        self.assertTrue(person_record.check_use("quinn", "named", "Alex", kind="image_lora")[0])
        ok, why = person_record.check_use("quinn", "named", "Alex", kind="video_lora")
        self.assertFalse(ok); self.assertIn("no ready", why)

    def test_kind_gate_ignores_non_ready_models(self):
        person_record.add_model("quinn", kind="video_lora", status="training")
        ok, _ = person_record.check_use("quinn", "named", "Alex", kind="video_lora")
        self.assertFalse(ok)

    def test_usable_models_filters_by_consent(self):
        self.assertEqual(len(person_record.usable_models("quinn", "Alex")), 1)
        self.assertEqual(person_record.usable_models("quinn", "Jordan"), [])

    def test_visible_people_scopes_listing(self):
        self.assertIn("quinn", person_record.visible_people("Alex"))
        self.assertNotIn("quinn", person_record.visible_people("Jordan"))
        self.assertIn("openperson", person_record.visible_people("Jordan"))

