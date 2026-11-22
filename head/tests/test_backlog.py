import os
import tempfile
import unittest

import yaml

import config
import backlog


def _write_yaml(path, items, version=1):
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"version": version, "items": items}, fh, sort_keys=False)


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backlog_path = os.path.join(self.tmp, "backlog.yaml")
        self.proposals_path = os.path.join(self.tmp, "proposals.yaml")
        self.completed_path = os.path.join(self.tmp, "completed.json")
        self._orig_b = config.SELFENG_BACKLOG
        self._orig_p = config.SELFENG_PROPOSALS
        self._orig_c = config.SELFENG_COMPLETED
        config.SELFENG_BACKLOG = self.backlog_path
        config.SELFENG_PROPOSALS = self.proposals_path
        config.SELFENG_COMPLETED = self.completed_path

    def tearDown(self):
        config.SELFENG_BACKLOG = self._orig_b
        config.SELFENG_PROPOSALS = self._orig_p
        config.SELFENG_COMPLETED = self._orig_c

    # --- fail-soft loading ---
    def test_missing_file_is_empty_not_crash(self):
        self.assertEqual(backlog.load_backlog(), [])
        self.assertEqual(backlog.eligible_items(), [])

    def test_malformed_file_is_empty_not_crash(self):
        with open(self.backlog_path, "w", encoding="utf-8") as fh:
            fh.write("this: is: not: valid: yaml: : :\n  - broken")
        self.assertEqual(backlog.load_backlog(), [])

    def test_non_dict_yaml_is_empty(self):
        with open(self.backlog_path, "w", encoding="utf-8") as fh:
            fh.write("- just\n- a\n- list\n")
        self.assertEqual(backlog.load_backlog(), [])

    # --- normalization ---
    def test_unknown_kind_defaults_to_judgment(self):
        _write_yaml(self.backlog_path, [
            {"id": "x1", "objective": "do a thing", "kind": "bogus", "approved_overnight": True},
        ])
        items = backlog.load_backlog()
        self.assertEqual(items[0]["kind"], "judgment")
        # judgment => never eligible even when approved
        self.assertEqual(backlog.eligible_items(), [])

    def test_unknown_domain_defaults_to_orchestrator(self):
        _write_yaml(self.backlog_path, [
            {"id": "x1", "objective": "thing", "domain": "bogus"},
            {"id": "x2", "objective": "thing", "domain": "homelab"},
        ])
        items = {it["id"]: it for it in backlog.load_backlog()}
        self.assertEqual(items["x1"]["domain"], "orchestrator")
        self.assertEqual(items["x2"]["domain"], "homelab")

    def test_item_without_id_or_objective_dropped(self):
        _write_yaml(self.backlog_path, [
            {"objective": "no id here"},
            {"id": "no-objective"},
            {"id": "ok", "objective": "valid"},
        ])
        ids = [it["id"] for it in backlog.load_backlog()]
        self.assertEqual(ids, ["ok"])

    def test_dedup_on_id_first_wins(self):
        _write_yaml(self.backlog_path, [
            {"id": "dup", "objective": "first"},
            {"id": "dup", "objective": "second"},
        ])
        items = backlog.load_backlog()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["objective"], "first")

    # --- eligibility (the safety-critical filter) ---
    def test_eligible_requires_approved_and_test_verifiable(self):
        _write_yaml(self.backlog_path, [
            {"id": "a", "objective": "approved tv", "kind": "test-verifiable", "approved_overnight": True},
            {"id": "b", "objective": "unapproved tv", "kind": "test-verifiable", "approved_overnight": False},
            {"id": "c", "objective": "approved judgment", "kind": "judgment", "approved_overnight": True},
            {"id": "d", "objective": "approved tv missing flag default", "kind": "test-verifiable"},
        ])
        eligible = [it["id"] for it in backlog.eligible_items()]
        self.assertEqual(eligible, ["a"])

    def test_commission_kind_is_kept_but_never_eligible(self):
        _write_yaml(self.backlog_path, [
            {"id": "ep1", "objective": "big thing", "kind": "commission",
             "integration_acceptance": "integration test green", "approved_overnight": True},
        ])
        items = backlog.load_backlog()
        self.assertEqual(items[0]["kind"], "commission")
        self.assertEqual(items[0]["integration_acceptance"], "integration test green")
        self.assertEqual(backlog.eligible_items(), [])       # commissions never run directly
        self.assertEqual([e["id"] for e in backlog.list_commissions()], ["ep1"])
        self.assertEqual(backlog.find_item("ep1")["id"], "ep1")
        self.assertIsNone(backlog.find_item("nope"))

    def test_legacy_epic_kind_normalizes_to_commission(self):
        # Human-curated backlog.yaml still holds pre-rename `kind: epic` items; they must load as
        # commissions (not degrade to judgment) and stay ineligible to run directly.
        _write_yaml(self.backlog_path, [
            {"id": "ep-old", "objective": "big legacy thing", "kind": "epic",
             "approved_overnight": True},
        ])
        items = backlog.load_backlog()
        self.assertEqual(items[0]["kind"], "commission")
        self.assertEqual(backlog.eligible_items(), [])
        self.assertEqual([e["id"] for e in backlog.list_commissions()], ["ep-old"])

    def test_leaf_carries_parent_and_depends_on(self):
        _write_yaml(self.backlog_path, [
            {"id": "l2", "objective": "leaf", "kind": "test-verifiable", "parent_id": "ep1",
             "depends_on": ["l1"]},
        ])
        it = backlog.load_backlog()[0]
        self.assertEqual(it["parent_id"], "ep1")
        self.assertEqual(it["depends_on"], ["l1"])

    def test_done_ids_excluded(self):
        _write_yaml(self.backlog_path, [
            {"id": "a", "objective": "x", "kind": "test-verifiable", "approved_overnight": True},
            {"id": "b", "objective": "y", "kind": "test-verifiable", "approved_overnight": True},
        ])
        eligible = [it["id"] for it in backlog.eligible_items(done_ids={"a"})]
        self.assertEqual(eligible, ["b"])

    # --- proposals: HAI cannot self-approve ---
    def test_propose_appends_and_forces_unapproved(self):
        item = backlog.propose("Title", "Do the proposed thing", acceptance="tests pass")
        self.assertFalse(item["approved_overnight"])
        self.assertEqual(item["proposed_by"], "hai")
        loaded = backlog.load_proposals()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["objective"], "Do the proposed thing")
        self.assertFalse(loaded[0]["approved_overnight"])

    def test_propose_ignores_approval_attempt(self):
        # Even if a caller passes kind=judgment-ish junk, it's normalized; approval is never honored.
        item = backlog.propose("T", "obj", kind="weird")
        self.assertEqual(item["kind"], "judgment")
        self.assertFalse(item["approved_overnight"])

    def test_propose_empty_objective_raises(self):
        with self.assertRaises(ValueError):
            backlog.propose("title", "   ")

    def test_proposals_never_eligible_even_if_approved_field_present(self):
        # A proposal file is NOT the approval source; eligibility reads backlog only.
        backlog.propose("T", "obj", kind="test-verifiable")
        self.assertEqual(backlog.eligible_items(), [])

    def test_multiple_proposals_accumulate(self):
        backlog.propose("one", "first obj")
        backlog.propose("two", "second obj")
        self.assertEqual(len(backlog.load_proposals()), 2)


class DoneLedgerTests(unittest.TestCase):
    """Tests for the persistent done-ledger.

    Ensures completed items are not re-run across test runs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backlog_path = os.path.join(self.tmp, "backlog.yaml")
        self.completed_path = os.path.join(self.tmp, "completed.json")
        self._orig_b = config.SELFENG_BACKLOG
        self._orig_c = config.SELFENG_COMPLETED
        config.SELFENG_BACKLOG = self.backlog_path
        config.SELFENG_COMPLETED = self.completed_path

    def tearDown(self):
        config.SELFENG_BACKLOG = self._orig_b
        config.SELFENG_COMPLETED = self._orig_c

    def _approved(self, *ids):
        _write_yaml(self.backlog_path, [
            {"id": i, "objective": f"obj {i}", "kind": "test-verifiable", "approved_overnight": True}
            for i in ids
        ])

    def test_missing_ledger_is_empty(self):
        self.assertEqual(backlog.load_completed(), {})

    def test_corrupt_ledger_is_empty_not_crash(self):
        with open(self.completed_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(backlog.load_completed(), {})

    def test_record_and_load_roundtrip(self):
        self.assertTrue(backlog.record_completed("bl-1", run_id="ovr-x"))
        got = backlog.load_completed()
        self.assertIn("bl-1", got)
        self.assertEqual(got["bl-1"]["run_id"], "ovr-x")
        self.assertTrue(got["bl-1"]["at"])

    def test_record_accumulates(self):
        backlog.record_completed("bl-1")
        backlog.record_completed("bl-2", run_id="ovr-y")
        self.assertEqual(set(backlog.load_completed()), {"bl-1", "bl-2"})

    def test_eligible_excludes_ledgered_items(self):
        self._approved("bl-1", "bl-2")
        self.assertEqual({it["id"] for it in backlog.eligible_items()}, {"bl-1", "bl-2"})
        backlog.record_completed("bl-1", run_id="ovr-x")
        self.assertEqual({it["id"] for it in backlog.eligible_items()}, {"bl-2"})

    def test_ledger_and_run_done_ids_combine(self):
        self._approved("bl-1", "bl-2", "bl-3")
        backlog.record_completed("bl-1")
        self.assertEqual({it["id"] for it in backlog.eligible_items(done_ids={"bl-2"})}, {"bl-3"})

    def test_record_without_id_or_path_is_false(self):
        self.assertFalse(backlog.record_completed(""))
        config.SELFENG_COMPLETED = ""
        self.assertFalse(backlog.record_completed("bl-1"))

    def test_deleting_entry_reenables_item(self):
        # documented escape hatch: a human deletes the entry to deliberately re-run an item
        self._approved("bl-1")
        backlog.record_completed("bl-1")
        self.assertEqual(backlog.eligible_items(), [])
        items = backlog.load_completed()
        del items["bl-1"]
        import json as _json
        with open(self.completed_path, "w", encoding="utf-8") as fh:
            _json.dump({"version": 1, "items": items}, fh)
        self.assertEqual({it["id"] for it in backlog.eligible_items()}, {"bl-1"})

    def test_skipped_armed_returns_ledgered_but_armed(self):
        self._approved("bl-1", "bl-2", "bl-3")
        backlog.record_completed("bl-1")
        backlog.record_completed("bl-2")
        skipped = backlog.skipped_armed()
        ids = {s["id"] for s in skipped}
        self.assertEqual(ids, {"bl-1", "bl-2"})
        for s in skipped:
            self.assertEqual(s["why"], "in done-ledger")

    def test_skipped_armed_empty_when_all_eligible(self):
        self._approved("bl-1", "bl-2")
        skipped = backlog.skipped_armed()
        self.assertEqual(skipped, [])

    def test_skipped_armed_honors_run_done_ids(self):
        self._approved("bl-1", "bl-2")
        skipped = backlog.skipped_armed(done_ids={"bl-1"})
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["id"], "bl-1")

    def test_skipped_armed_combines_ledger_and_done_ids(self):
        self._approved("bl-1", "bl-2", "bl-3")
        backlog.record_completed("bl-1")
        skipped = backlog.skipped_armed(done_ids={"bl-2"})
        ids = {s["id"] for s in skipped}
        self.assertEqual(ids, {"bl-1", "bl-2"})

    def test_test_fixture_flag_normalized_strict_bool(self):
        _write_yaml(self.backlog_path, [
            {"id": "fx-1", "objective": "drill", "kind": "test-verifiable",
             "test_fixture": True, "approved_overnight": True},
            {"id": "bl-1", "objective": "real", "kind": "test-verifiable",
             "approved_overnight": True}])
        items = {it["id"]: it for it in backlog.load_backlog()}
        self.assertIs(items["fx-1"]["test_fixture"], True)
        self.assertIs(items["bl-1"]["test_fixture"], False)

    def test_fixture_items_stay_eligible_when_armed(self):
        # Eligibility is unchanged by the flag — an armed fixture runs, every run, forever
        # (the ledger skip lives in the run loop, not here).
        _write_yaml(self.backlog_path, [
            {"id": "fx-1", "objective": "drill", "kind": "test-verifiable",
             "test_fixture": True, "approved_overnight": True}])
        self.assertEqual([it["id"] for it in backlog.eligible_items()], ["fx-1"])
        backlog.record_completed("fx-1")   # even IF somehow ledgered, that's the ledger's say —
        self.assertEqual(backlog.eligible_items(), [])   # eligibility semantics stay uniform


if __name__ == "__main__":
    unittest.main()
