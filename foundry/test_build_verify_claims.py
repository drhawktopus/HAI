"""Pins for build_verify_claims -- the VERIFY-lane claim typer.

The fleet table, the head URL and the staging share are settings, so this file states them
explicitly with test addresses (documentation range) instead of relying on any lab values.

Predicted before running: the 6-row synthetic extract types to 3 mechanical + 1 judgment; gold is
7 controls per verifying box; a non-LAN target and unaddressed task stay judgment; the typer reads
both the EXTRACT (`verify`/`claims`) and CANDIDATES (`rules_verify`/`rules_claims`) field names."""
import io
import json
import os
import tempfile
import unittest
import unittest.mock

_FLEET = {"mothership": "10.0.0.76", "bighonker": "10.0.0.167", "softserve": "10.0.0.204"}
os.environ["FLEET_HOSTS"] = json.dumps(_FLEET)
os.environ["FLEET_TABLE"] = json.dumps({"mothership": {"root": r"F:\foundry"}, "bighonker": {"root": r"F:\foundry"},
                                        "softserve": {"root": r"E:\foundry"}})
os.environ["LAN_PREFIX"] = "10.0.0."
os.environ["HAI_URL"] = "http://10.0.0.205:8099"
os.environ["GOLD_CLOSED_PORT"] = "59321"
os.environ["LIBRARY_TRIAGE_UNC"] = r"\\10.0.0.204\SSDShared\library-triage"
os.environ["BIG_SHARED_UNC"] = r"\\10.0.0.204\Big Shared"

import build_verify_claims as B


class TestTypeClaim(unittest.TestCase):
    def one(self, cl):
        r = B.type_claim(cl)
        self.assertEqual(len(r), 1, "expected a single emission for %r" % cl)
        return r[0]

    def test_tnc_port(self):
        t, box, p, _ = self.one("Test-NetConnection 10.0.0.167 -Port 22")
        self.assertEqual((t, p), ("port_open", {"ip": "10.0.0.167", "port": 22}))
        self.assertNotEqual(box, "softserve")
        self.assertNotEqual(B.BOX_IP[box], "10.0.0.167")  # cross-wire, never loopback

    def test_url_known_get(self):
        t, _, p, _ = self.one("curl http://10.0.0.204:11434/api/tags")
        self.assertEqual(t, "http_reach")
        self.assertEqual(p["url"], "http://10.0.0.204:11434/api/tags")

    def test_url_unknown_path_falls_to_port(self):
        t, _, p, note = self.one("POST http://10.0.0.205:8099/p20/order")
        self.assertEqual((t, p), ("port_open", {"ip": "10.0.0.205", "port": 8099}))
        self.assertIn("not a known read-only GET", note)

    def test_bare_ipport(self):
        t, _, p, _ = self.one("Glances at 10.0.0.76:61208 shows GPU")
        self.assertEqual((t, p), ("port_open", {"ip": "10.0.0.76", "port": 61208}))

    def test_task_named_box(self):
        t, box, p, _ = self.one('scheduled task "Ollama Serve" runs on bighonker')
        self.assertEqual((t, box, p), ("task_exists", "bighonker", {"task": "Ollama Serve"}))

    def test_task_no_box_is_judgment(self):
        # a task claim with no box named cannot be addressed -> judgment
        self.assertEqual(self.one('the scheduled task "Nightly Sync" fires at 3am')[0], "judgment")

    def test_zdrive_rewritten_to_unc(self):
        t, box, p, _ = self.one(r"docs live in Z:\Documentation\Reference")
        self.assertEqual(t, "path_fresh")
        self.assertEqual(box, "bighonker")
        self.assertTrue(p["path"].startswith(r"\\10.0.0.204\Big Shared"))

    def test_a_drive_only_one_factory_has_is_checked_there(self):
        self.assertEqual(B.LOCAL_DRIVE, {"E": "softserve"}, "E: holds one factory's root; F: holds two, so no owner")
        t, box, p, note = self.one(r"the queue is E:\foundry\jobs\queue")
        self.assertEqual((t, box, p["path"]), ("path_fresh", "softserve", r"E:\foundry\jobs\queue"))
        self.assertIn("only factory", note)

    def test_a_path_on_another_drive_never_goes_to_the_box_that_owns_its_own(self):
        r = B.type_claim(r"F:\foundry\bin on mothership and softserve")
        self.assertEqual([x[1] for x in r], ["mothership"])

    def test_no_fleet_table_means_no_local_drive(self):
        with unittest.mock.patch.object(B, "LOCAL_DRIVE", {}):
            t, box, _, _ = self.one(r"the queue is E:\foundry\jobs\queue")
        self.assertEqual((t, box), ("path_fresh", B.FACTORIES[0]))

    def test_the_verify_boxes_are_named_never_assumed(self):
        self.assertEqual(B.main(["build_verify_claims.py", "hai", "src.json", "out.json"]), 2)

    def test_non_lan_is_judgment(self):
        self.assertEqual(self.one("reachable on the overlay 198.51.100.9:8080")[0], "judgment")

    def test_prose_is_judgment(self):
        self.assertEqual(self.one("The ncnn detector runs on the tablet fleet.")[0], "judgment")


SYNTH_EXTRACT = {"rows": [
    {"doc": "Reference/X.md", "lines": "1-5", "verify": True,
     "claims": ["Test-NetConnection 10.0.0.167 -Port 22"]},
    {"doc": "Reference/X.md", "lines": "6-9", "verify": True,
     "claims": ['scheduled task "Ollama Serve" on bighonker']},
    {"doc": "Reference/Y.md", "lines": "1-3", "verify": True,
     "claims": ["The detector runs the ncnn model on the tablets."]},
    {"doc": "Reference/Y.md", "lines": "4-8", "verify": True,
     "claims": ["reachable at http://10.0.0.204:11434/api/tags"]},
    {"doc": "(control)", "lines": None, "verify": True, "claims": ["ignored control row"]},
    {"doc": "Reference/Z.md", "lines": "1-2", "verify": False,
     "claims": ["not flagged, must be skipped"]},
]}


class TestBuild(unittest.TestCase):
    def _build(self, data, domain="hai", boxes=("mothership", "bighonker")):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "src.json")
        out = os.path.join(d, "out.json")
        io.open(src, "w", encoding="utf-8").write(json.dumps(data))
        payload = B.build(domain, src, out, boxes)
        on_disk = json.load(io.open(out, encoding="utf-8"))
        return payload, on_disk

    def test_counts(self):
        p, _ = self._build(SYNTH_EXTRACT)
        self.assertEqual(p["typed_mechanical"], 3)   # tnc, task, http
        self.assertEqual(p["judgment"], 1)           # the prose row
        self.assertEqual(p["rows_verify_flagged"], 4)   # control + unflagged excluded
        real = [c for c in p["claims"] if not str(c["id"]).startswith("gold")]
        gold = [c for c in p["claims"] if str(c["id"]).startswith("gold")]
        self.assertEqual(len(real), 4)
        self.assertEqual(len(gold), 14)              # 7 controls x 2 boxes

    def test_real_claims_shape(self):
        p, _ = self._build(SYNTH_EXTRACT)
        by = {(c["type"], json.dumps(c["params"], sort_keys=True)): c
              for c in p["claims"] if not str(c["id"]).startswith("gold")}
        self.assertIn(("port_open", json.dumps({"ip": "10.0.0.167", "port": 22},
                                               sort_keys=True)), by)
        task = [c for c in p["claims"] if c["type"] == "task_exists"
                and not str(c["id"]).startswith("gold")][0]
        self.assertEqual(task["target_box"], "bighonker")

    def test_gold_has_expect_and_ids(self):
        p, _ = self._build(SYNTH_EXTRACT)
        gold = [c for c in p["claims"] if str(c["id"]).startswith("gold")]
        self.assertTrue(all(g.get("expect") for g in gold))
        self.assertEqual(gold[0]["id"], "gold-01")

    def test_reads_candidates_field_names(self):
        cand = {"rows": [{"doc": "Reference/X.md", "lines": "1-5", "rules_verify": True,
                          "rules_claims": ["Test-NetConnection 10.0.0.76 -Port 22"]}]}
        p, _ = self._build(cand)
        self.assertEqual(p["typed_mechanical"], 1)
        self.assertEqual(p["judgment"], 0)

    def test_zero_rows_raises(self):
        with self.assertRaises(ValueError):
            self._build({"rows": []})

    def test_gold_scales_with_boxes(self):
        p, _ = self._build(SYNTH_EXTRACT, boxes=("mothership", "bighonker", "softserve"))
        gold = [c for c in p["claims"] if str(c["id"]).startswith("gold")]
        self.assertEqual(len(gold), 21)


if __name__ == "__main__":
    unittest.main()
