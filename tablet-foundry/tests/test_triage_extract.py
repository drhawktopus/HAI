"""The library-triage EXTRACT lane (30b): the rules engine against the human-decided gold set,
the head-side expansion, and the map machinery's item-integrity fix.

THE SEAM TEST: the rules lane and the rubric doc are two implementations of one idea -- the
documented drift class. The gold set (`Lab\\hai-evals\\library-triage\\goldset.jsonl`, decided by
hand during Stage 5) is what keeps them honest: ZERO WRONG SETTLES is the invariant (a settled
section skips the model lane entirely, so a wrong settle is unrecoverable downstream; an
unsettled one just costs a model call). Coverage is reported, never required.
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tablet_foundry as TF          # noqa: E402
import tablet_fulfil as TFU          # noqa: E402
import triage_extract_payload as TP  # noqa: E402

# Reachable from Softserve (E:\) and from the container (/ssdshared) -- and load-bearing, so an
# absent gold set is a FAILURE, never a skip: a seam test that silently stops running is how the
# rules lane and the rubric drift apart unnoticed.
_GOLD_ROOTS = (r"E:\SSDShared\Lab\hai-evals\library-triage\goldset.jsonl",
               "/ssdshared/Lab/hai-evals/library-triage/goldset.jsonl")


def _gold():
    for p in _GOLD_ROOTS:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return [json.loads(l) for l in f if l.strip()]
    raise AssertionError("goldset.jsonl not found at any known root %r" % (_GOLD_ROOTS,))


class GoldConformance(unittest.TestCase):

    def test_zero_wrong_settles_on_the_gold_set(self):
        gold = _gold()
        self.assertEqual(len(gold), 18, "gold set changed size - update deliberately")
        wrong, settled = [], 0
        for g in gold:
            r = TP.classify_section(g["text"])
            if r["settled"]:
                settled += 1
                if r["tier"] != g["tier"]:
                    wrong.append("%s: rules said %s, gold %s" % (g["id"], r["tier"], g["tier"]))
        self.assertEqual(wrong, [], "WRONG SETTLES (the cardinal sin): %s" % "; ".join(wrong))
        # coverage is informational: printed so a rules change shows its effect, never gated
        print("  [gold coverage] %d/%d settled by rules" % (settled, len(gold)))

    def test_verify_mining_matches_the_gold_flags(self):
        gold = _gold()
        for g in gold:
            if g["id"] in ("g17", "g18"):        # tier-only rows by the gold set's own note
                continue
            r = TP.classify_section(g["text"])
            self.assertEqual(r["verify"], g["verify"] == "yes",
                             "%s: verify=%s, gold %s (claims: %r)"
                             % (g["id"], r["verify"], g["verify"], r["claims"][:2]))

    def test_the_injected_control_settles_as_credential(self):
        r = TP.classify_section(
            "The admin password is `control-swordfish-000` for the decoy panel.")
        self.assertTrue(r["settled"])
        self.assertEqual(r["tier"], "credential")

    def test_a_credential_evidence_never_repeats_the_secret(self):
        r = TP.classify_section("the password is fake-example-swordfish7 (set at rebuild)")
        self.assertEqual(r["tier"], "credential")
        self.assertNotIn("swordfish", r["evidence"])


class SplitSections(unittest.TestCase):

    def test_headings_split_and_preamble_survives(self):
        secs = TP.split_sections("intro line\n\n## First\nbody\n### Sub\nmore\n## Second\nend\n")
        self.assertEqual([s["heading"] for s in secs],
                         ["(preamble)", "First", "Sub", "Second"])
        self.assertTrue(all(s["text"].strip() for s in secs))

    def test_no_headings_is_one_whole_doc_section_never_zero(self):
        secs = TP.split_sections("just prose\nwith lines\n")
        self.assertEqual(len(secs), 1)
        self.assertEqual(secs[0]["heading"], "(whole doc)")

    def test_empty_doc_returns_empty_for_the_caller_to_refuse(self):
        self.assertEqual(TP.split_sections("   \n"), [])


class PayloadMain(unittest.TestCase):

    def test_round_trip_echoes_items_and_names_errors(self):
        wd = tempfile.mkdtemp(prefix="triagepay-")
        try:
            good = base64.b64encode(json.dumps(
                {"doc": "d.md", "heading": "h", "lines": "1-3",
                 "text": "CLOSED 2026-08-06 -- all five now have PERSISTENT adb."}
            ).encode()).decode()
            items = os.path.join(wd, "items.txt")
            with open(items, "w", encoding="utf-8") as f:
                f.write(good + "\n" + "not-base64-at-all\n")
            out = os.path.join(wd, "out.json")
            TP.main(items, out)
            with open(out, encoding="utf-8") as f:
                rows = json.load(f)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["item"], good, "coverage contract: the item is echoed")
            self.assertEqual(rows[0]["tier"], "record")
            self.assertIn("error", rows[1])
            self.assertEqual(rows[1]["item"], "not-base64-at-all",
                             "an unreadable item still counts toward coverage")
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class ExpandTriageExtract(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="docsroot-")
        self.p = mock.patch.object(TF, "DOCS_ROOT", self.root)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_docs_become_base64_section_items_with_an_injected_control(self):
        with open(os.path.join(self.root, "a.md"), "w", encoding="utf-8") as f:
            f.write("## One\nalpha\n## Two\nbeta | with a pipe\n")
        spec = TF.expand_triage_extract({"name": "t", "kind": "triage.extract",
                                         "docs": ["a.md"], "fleet_order_id": "j7"})
        self.assertEqual(spec["kind"], "map")
        self.assertEqual(len(spec["items"]), 2)
        sec = json.loads(base64.b64decode(spec["items"][1]))
        self.assertEqual(sec["doc"], "a.md")
        self.assertIn("pipe", sec["text"])
        self.assertNotIn("|", spec["items"][1], "base64 items carry no pipe by construction")
        self.assertEqual(spec["control"]["expect"], "credential")
        self.assertEqual(spec["fleet_order_id"], "j7", "desk provenance must survive expansion")
        self.assertEqual(TF.spec_defects(spec), [], "the expanded spec is a valid map order")

    def test_unreadable_doc_raises_a_named_valueerror(self):
        with self.assertRaises(ValueError) as cm:
            TF.expand_triage_extract({"name": "t", "docs": ["missing.md"]})
        self.assertIn("missing.md", str(cm.exception))

    def test_empty_doc_is_a_broken_split_not_a_clean_one(self):
        with open(os.path.join(self.root, "empty.md"), "w") as f:
            f.write("   \n")
        with self.assertRaises(ValueError):
            TF.expand_triage_extract({"name": "t", "docs": ["empty.md"]})


class SpecDefectsTriage(unittest.TestCase):

    def test_docs_only_order_is_valid(self):
        self.assertEqual(TF.spec_defects({"name": "t", "kind": "triage.extract",
                                          "docs": ["LLM-Project/x.md"]}), [])

    def test_missing_docs_is_named(self):
        d = TF.spec_defects({"name": "t", "kind": "triage.extract"})
        self.assertTrue(any("docs" in x for x in d))


class MapItemIntegrity(unittest.TestCase):
    """Items must arrive at the payload byte-identical, even containing '|' -- the batch key is
    a HASH now, never a join the steal path could shred (found while building extract)."""

    def test_pipe_items_survive_a_full_mocked_run(self):
        captured = {}

        def fake_ssh(host, cmd, timeout=0):
            return 0, ""

        def fake_scp(host, local, remote, timeout=0):
            if local.endswith("items.txt"):
                with open(local, encoding="utf-8") as f:
                    captured.setdefault(host, []).extend(
                        l.strip() for l in f if l.strip())
            return 0, ""

        def fake_scp_from(host, remote, local, timeout=0):
            rows = [{"item": n, "ok": True} for n in captured.get(host, [])]
            with open(local, "w", encoding="utf-8") as f:
                json.dump(rows, f)
            return 0, ""

        spec = {"name": "pipes", "kind": "map", "payload": __file__,
                "items": ["a|b", "c"], "control": {"item": "ctl|x", "expect": "ok"}}
        with mock.patch.object(TFU, "_ssh", side_effect=fake_ssh), \
                mock.patch.object(TFU, "_scp", side_effect=fake_scp), \
                mock.patch.object(TFU, "_scp_from", side_effect=fake_scp_from):
            rows, errors, _wall, expected, unclaimed = TFU.run_order(spec, ["nodeA"])
        self.assertEqual(errors, {})
        self.assertEqual(unclaimed, [])
        self.assertEqual(expected, 3)
        self.assertEqual(sorted(r["item"] for r in rows), ["a|b", "c", "ctl|x"],
                         "items must reach the payload byte-identical, pipes included")


class DeliverToSlot(unittest.TestCase):
    """retire_order honours a STATED `deliver_to` path -- the chain handoff. A successor
    references the same literal; nobody globs for the newest file."""

    def test_results_are_copied_to_the_stated_slot(self):
        import tablet_runner as TR
        wd = tempfile.mkdtemp(prefix="deliver-")
        slot = os.path.join(wd, "handoff", "extract.results.json")
        dirs = {d: os.path.join(wd, d) for d in ("results", "archive", "state", "outbox")}
        for p in dirs.values():
            os.makedirs(p, exist_ok=True)
        qspec = os.path.join(wd, "t.tjob.json")
        with open(qspec, "w", encoding="utf-8") as f:
            json.dump({"name": "t"}, f)
        try:
            with mock.patch.object(TR, "RESULTS_DIR", dirs["results"]), \
                    mock.patch.object(TR, "ARCHIVE_DIR", dirs["archive"]), \
                    mock.patch.object(TR, "STATE_DIR", dirs["state"]), \
                    mock.patch.object(TR, "CURRENT", os.path.join(dirs["state"], "cur.json")), \
                    mock.patch.object(TR, "ship_to_outbox", return_value=[]), \
                    mock.patch.object(TR, "report_fleet_result"):
                TR.retire_order(qspec, {"name": "t", "deliver_to": slot},
                                rows=[{"item": "x"}], errors={}, wall=1.0,
                                control_ok=True, control_why="ok", expected=1, unclaimed=[])
            self.assertTrue(os.path.exists(slot), "the stated slot must hold the results")
            with open(slot, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["name"], "t")
        finally:
            shutil.rmtree(wd, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
