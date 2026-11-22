"""Pins for library_restructure.py + the #30 Phase-O operator tools.

File I/O is parameterized to tmp dirs (no container). The order submit and rag.search are
injected. Covers: domain registry, work-file building (skips small docs), draft->spec
validation (remap / drop-refusal / dup-refusal), the probe gate, and admin-gating of the
four new tools.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library_restructure as LR
import tools


def _docs(tmp, files):
    for rel, text in files.items():
        p = os.path.join(tmp, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        io.open(p, "w", encoding="utf-8", newline="\n").write(text)


def _bigdoc(n):
    body = "# Big Doc\n\nintro\n\n"
    for i in range(n):
        body += "## Section %d\n\nbody of section %d here.\n\n" % (i, i)
    return body


def _verdicts(stage, domain, box, rows):
    io.open(os.path.join(stage, "s30-%s-verify.verdicts.%s.json" % (domain, box)),
            "w", encoding="utf-8").write(json.dumps({"verdicts": rows}))


def _claims(stage, domain, rows):
    io.open(os.path.join(stage, "s30-%s-verify.claims.json" % domain),
            "w", encoding="utf-8").write(json.dumps({"claims": rows}))


def _disp(stage, domain, mapping):
    io.open(os.path.join(stage, "s30-%s-verify.dispositions.json" % domain),
            "w", encoding="utf-8").write(json.dumps(mapping))


class TestWorkBuilding(unittest.TestCase):
    def test_split_work_skips_small_docs_and_includes_omnibus(self):
        tmp = tempfile.mkdtemp()
        stage = tempfile.mkdtemp()
        _docs(tmp, {"Reference/Big.md": _bigdoc(12),
                    "Reference/Small.md": _bigdoc(3)})
        with mock.patch.dict(LR.DOMAINS, {"d": ["Reference/Big.md", "Reference/Small.md"]}):
            local, unc, included = LR.build_split_work("d", min_sections=10,
                                                       docs_dir=tmp, stage_dir=stage)
        self.assertEqual(included, ["Reference/Big.md"], "the 3-section doc is not an omnibus")
        work = json.load(io.open(local, encoding="utf-8"))
        self.assertEqual(work["task"], "split-map")
        self.assertEqual(len(work["items"]), 1)
        self.assertEqual(len(work["items"][0]["sections"]), 12)
        self.assertTrue(unc.endswith("s30-d-splitmap.work.json"))

    def test_split_work_refuses_when_nothing_is_oversized(self):
        tmp = tempfile.mkdtemp()
        _docs(tmp, {"Reference/Small.md": _bigdoc(3)})
        with mock.patch.dict(LR.DOMAINS, {"d": ["Reference/Small.md"]}):
            with self.assertRaises(ValueError):
                LR.build_split_work("d", min_sections=10, docs_dir=tmp,
                                    stage_dir=tempfile.mkdtemp())

    def test_index_work_lists_every_page(self):
        tmp = tempfile.mkdtemp()
        stage = tempfile.mkdtemp()
        _docs(tmp, {"Reference/A.md": "# Doc A\n\nlead a\n", "Reference/B.md": "# Doc B\n\nlead b\n"})
        with mock.patch.dict(LR.DOMAINS, {"d": ["Reference/A.md", "Reference/B.md"]}):
            local, _unc = LR.build_index_work("d", docs_dir=tmp, stage_dir=stage)
        work = json.load(io.open(local, encoding="utf-8"))
        self.assertEqual(work["task"], "index")
        self.assertEqual(len(work["items"][0]["docs"]), 2)
        self.assertEqual(work["items"][0]["docs"][0]["title"], "Doc A")

    def test_unknown_domain_raises(self):
        with self.assertRaises(ValueError):
            LR.domain_docs("nope")


class TestSubmit(unittest.TestCase):
    def test_submit_order_pins_27b_and_mothership(self):
        seen = {}

        def fake(spec, requires):
            seen["spec"], seen["requires"] = spec, requires
            return {"placed": [{"host": "mothership"}]}
        LR.submit_order("s30-x-splitmap", "split-map", submit_fn=fake)
        self.assertIn("model." + LR.config.HEAVY_MODEL, seen["requires"])
        self.assertEqual(seen["spec"]["args"][2:], ["--task", "split-map"])
        self.assertTrue(seen["spec"]["args"][0].endswith("s30-x-splitmap.work.json"))


class TestSpecsFromDrafts(unittest.TestCase):
    def _setup(self, child_headings):
        tmp = tempfile.mkdtemp()
        _docs(tmp, {"Reference/Big.md": _bigdoc(4)})   # sections: ## Section 0..3
        drafts = {"results": [{"doc": "Reference/Big.md", "child_pages": [
            {"title": "# Big - part one", "section_headings": child_headings[0], "rationale": "r"},
            {"title": "# Big - part two", "section_headings": child_headings[1], "rationale": "r"},
        ]}]}
        return tmp, drafts

    def test_valid_exhaustive_split_builds_specs(self):
        tmp, drafts = self._setup([["## Section 0", "## Section 1"],
                                   ["## Section 2", "## Section 3"]])
        specs, summary = LR.specs_from_drafts("d", drafts, docs_dir=tmp)
        self.assertEqual(len(specs), 1)
        self.assertEqual(len(specs[0]["children"]), 2)
        self.assertTrue(specs[0]["children"][0]["file"].endswith(".md"))
        self.assertIn("4/4 sections", summary[0])

    def test_near_miss_heading_is_remapped(self):
        # model returned a slightly-off heading; it maps to the real one
        tmp, drafts = self._setup([["## Section 0", "## Section 1"],
                                   ["## Section 2", "## Sectionn 3"]])
        specs, _ = LR.specs_from_drafts("d", drafts, docs_dir=tmp)
        allsec = [h for c in specs[0]["children"] for h in c["sections"]]
        self.assertIn("## Section 3", allsec)

    def test_dropped_section_refuses(self):
        tmp, drafts = self._setup([["## Section 0", "## Section 1"], ["## Section 2"]])
        with self.assertRaises(ValueError) as cm:
            LR.specs_from_drafts("d", drafts, docs_dir=tmp)
        self.assertIn("NO child", str(cm.exception))

    def test_duplicate_section_refuses(self):
        tmp, drafts = self._setup([["## Section 0", "## Section 1", "## Section 2", "## Section 3"],
                                   ["## Section 3"]])
        with self.assertRaises(ValueError) as cm:
            LR.specs_from_drafts("d", drafts, docs_dir=tmp)
        self.assertIn("two children", str(cm.exception))


class TestProbe(unittest.TestCase):
    def test_probe_runs_the_set(self):
        stage = tempfile.mkdtemp()
        io.open(os.path.join(stage, "s30-d.probes.json"), "w", encoding="utf-8").write(
            json.dumps({"probes": [{"question": "q", "expect": "Wanted.md"}]}))
        rep = LR.run_probe("d", stage_dir=stage,
                           search_fn=lambda q, k, ns: [{"source": "Wanted.md"}])
        self.assertEqual(rep["passed"], 1)

    def test_missing_probe_set_raises(self):
        with self.assertRaises(ValueError):
            LR.run_probe("d", stage_dir=tempfile.mkdtemp(),
                         search_fn=lambda q, k, ns: [])


class TestToolGating(unittest.TestCase):
    def test_operator_tools_are_admin_only(self):
        for t in ("library_propose_split", "library_draft_index", "library_probe",
                  "library_apply_split", "library_carve_h1", "library_check_refs", "library_apply_restructure",
                  "library_propose_restructure", "library_scaffold_restructure"):
            self.assertFalse(tools._allowed(t, "Riley"), "%s must be admin-gated" % t)
            self.assertTrue(tools._allowed(t, "Alex"), "%s must allow admin" % t)

    def test_tools_registered_in_both_lists(self):
        for lst in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            names = {t["function"]["name"] for t in lst}
            for t in ("library_propose_split", "library_draft_index", "library_probe",
                      "library_apply_split", "library_carve_h1", "library_check_refs", "library_apply_restructure",
                  "library_propose_restructure", "library_scaffold_restructure"):
                self.assertIn(t, names)

    def test_apply_split_registers_a_pending_action_not_an_immediate_write(self):
        # the WRITE tool must go through preview->confirm, never apply on the tool call
        import library_restructure as _LR
        with mock.patch.object(_LR, "verify_gate", return_value={"needs_disposition": 0}), \
             mock.patch.object(_LR, "read_drafts", return_value={"results": []}), \
             mock.patch.object(_LR, "specs_from_drafts",
                               return_value=([{"doc": "x"}], ["Big.md -> 2 pages"])), \
             mock.patch.object(_LR, "apply_split") as applied, \
             mock.patch.object(tools.pending_actions, "register",
                               return_value={"status": "pending"}) as reg:
            out = tools._library_apply_split_tool({"domain": "d"}, "Alex")
        self.assertEqual(out, {"status": "pending"})
        reg.assert_called_once()
        applied.assert_not_called()

    def test_apply_split_refuses_when_verify_gate_fails(self):
        # the currency gate is non-skippable: an unverified domain never reaches drafts or register
        import library_restructure as _LR
        with mock.patch.object(_LR, "verify_gate", side_effect=ValueError("VERIFY never ran")), \
             mock.patch.object(_LR, "read_drafts") as rd, \
             mock.patch.object(tools.pending_actions, "register") as reg:
            out = tools._library_apply_split_tool({"domain": "cameras"}, "Alex")
        self.assertIn("VERIFY never ran", out)
        rd.assert_not_called()
        reg.assert_not_called()

    def test_carve_h1_registers_a_pending_action_not_an_immediate_write(self):
        import library_restructure as _LR
        with mock.patch.object(_LR, "build_h1carve_spec",
                               return_value=({"doc": "/docs/x.md", "children": [{"file": "a.md"}]},
                                             ["x.md -> keep 'Core' + carve 1 page(s): a.md"])), \
             mock.patch.object(_LR, "apply_h1carve",
                               return_value={"kept": "# Core", "child_files": ["a.md"],
                                             "h1_blocks": 2}) as carved, \
             mock.patch.object(tools.pending_actions, "register",
                               return_value={"status": "pending"}) as reg:
            out = tools._library_carve_h1_tool({"doc": "LLM-Project/x.md"}, "Alex")
        self.assertEqual(out, {"status": "pending"})
        reg.assert_called_once()
        # only the dry-run preview ran on the tool call; the live carve waits for confirm
        carved.assert_called_once_with({"doc": "/docs/x.md", "children": [{"file": "a.md"}]},
                                       dry_run=True)


class TestH1CarveSpec(unittest.TestCase):
    def _multi(self, tmp, rel="LLM-Project/Multi.md", body=None):
        body = body or ("# 12 - Main Topic\n\nmain body\n\n"
                        "# Side Landscape\n\nside body\n\n"
                        "# Another Angle\n\nangle body\n")
        _docs(tmp, {rel: body})
        return rel

    def test_build_carve_spec_keeps_first_carves_rest(self):
        tmp = tempfile.mkdtemp()
        rel = self._multi(tmp)
        spec, summary = LR.build_h1carve_spec(rel, docs_dir=tmp)
        self.assertEqual([c["file"] for c in spec["children"]],
                         ["Side-Landscape.md", "Another-Angle.md"])
        self.assertEqual([c["h1"] for c in spec["children"]],
                         ["# Side Landscape", "# Another Angle"])
        self.assertIn("Main Topic", summary[0])
        rep = LR.apply_h1carve(spec, dry_run=True)               # round-trips through the carver
        self.assertEqual(rep["h1_blocks"], 3)
        self.assertEqual(rep["kept"], "# 12 - Main Topic")

    def test_build_carve_spec_refuses_single_h1(self):
        tmp = tempfile.mkdtemp()
        rel = self._multi(tmp, body="# Solo\n\nbody\n\n## sub\n\nx\n")
        with self.assertRaises(ValueError) as cm:
            LR.build_h1carve_spec(rel, docs_dir=tmp)
        self.assertIn("not multi-H1", str(cm.exception))

    def test_build_carve_spec_refuses_filename_collision(self):
        tmp = tempfile.mkdtemp()
        rel = self._multi(tmp, body="# Keep\n\nk\n\n# Same Name\n\na\n\n# Same  Name\n\nb\n")
        with self.assertRaises(ValueError) as cm:
            LR.build_h1carve_spec(rel, docs_dir=tmp)
        self.assertIn("same filename", str(cm.exception))

    def test_slug_h1_drops_ordinal_and_slugifies(self):
        self.assertEqual(LR._slug_h1("# 12 - Video Model Landscape"), "Video-Model-Landscape")
        self.assertEqual(LR._slug_h1("# Flux 2 on the 7900 XTX"), "Flux-2-on-the-7900-XTX")


class TestCheckRefs(unittest.TestCase):
    def _setup(self, tmp):
        _docs(tmp, {
            "LLM-Project/Parent.md": "# Parent\n\n## Pages in this document\n\n"
                                     "- **Child** — see `Child.md`\n",
            "LLM-Project/Child.md": "# Child\n\nbody. see LLM-Project/Parent.md § Phase B for it.\n",
            "Reference/Other.md": "# Other\n\nrefers to Parent.md as a whole, no section.\n",
            "Reference/Unrelated.md": "# Unrelated\n\nnothing here.\n",
        })

    def test_classifies_name_and_section_refs(self):
        tmp = tempfile.mkdtemp()
        self._setup(tmp)
        rep = LR.check_refs("LLM-Project/Parent.md", docs_dir=tmp)
        self.assertTrue(rep["exists"])
        self.assertTrue(rep["is_stub"])                       # has "## Pages in this document"
        self.assertEqual(rep["total"], 2)
        self.assertIn("Reference/Other.md", [r["file"] for r in rep["name_refs"]])
        self.assertIn("LLM-Project/Child.md", [r["file"] for r in rep["section_refs"]])
        self.assertIn("Phase B", rep["section_refs"][0]["anchor"])

    def test_nonexistent_doc_reports_not_exists(self):
        tmp = tempfile.mkdtemp()
        self._setup(tmp)
        rep = LR.check_refs("LLM-Project/Ghost.md", docs_dir=tmp)
        self.assertFalse(rep["exists"])
        self.assertFalse(rep["is_stub"])

    def test_non_stub_doc_flagged_not_stub(self):
        tmp = tempfile.mkdtemp()
        _docs(tmp, {"LLM-Project/Plain.md": "# Plain\n\none topic, no links list.\n",
                    "Reference/R.md": "# R\n\nsee Plain.md § Intro\n"})
        rep = LR.check_refs("LLM-Project/Plain.md", docs_dir=tmp)
        self.assertTrue(rep["exists"])
        self.assertFalse(rep["is_stub"])
        self.assertEqual(len(rep["section_refs"]), 1)

    def test_excludes_the_doc_itself(self):
        tmp = tempfile.mkdtemp()
        self._setup(tmp)
        rep = LR.check_refs("LLM-Project/Parent.md", docs_dir=tmp)
        for r in rep["name_refs"] + rep["section_refs"]:
            self.assertNotEqual(r["file"], "LLM-Project/Parent.md")


class TestManifest(unittest.TestCase):
    def _manifest(self, obj, raw=None):
        p = os.path.join(tempfile.mkdtemp(), "domains.json")
        io.open(p, "w", encoding="utf-8").write(raw if raw is not None else json.dumps(obj))
        return p

    def test_manifest_overrides_default_domain(self):
        p = self._manifest({"cameras": ["Reference/Child-A.md", "Reference/Child-B.md"]})
        with mock.patch.object(LR, "DOMAINS_MANIFEST", p):
            self.assertEqual(LR.domain_docs("cameras"),
                             ["Reference/Child-A.md", "Reference/Child-B.md"])

    def test_manifest_absent_uses_default(self):
        missing = os.path.join(tempfile.mkdtemp(), "nope.json")
        with mock.patch.object(LR, "DOMAINS_MANIFEST", missing):
            self.assertEqual(LR.domain_docs("cameras"), LR.DOMAINS["cameras"])

    def test_malformed_manifest_falls_back_to_default(self):
        p = self._manifest(None, raw="{ this is not json")
        with mock.patch.object(LR, "DOMAINS_MANIFEST", p):
            self.assertEqual(LR.domain_docs("cameras"), LR.DOMAINS["cameras"])

    def test_manifest_adds_a_new_domain(self):
        p = self._manifest({"network": ["Reference/Network-Infrastructure.md"]})
        with mock.patch.object(LR, "DOMAINS_MANIFEST", p):
            self.assertEqual(LR.domain_docs("network"), ["Reference/Network-Infrastructure.md"])
            self.assertIn("network", LR.known_domains())


class TestRestructureMapEngine(unittest.TestCase):
    def _omni(self, tmp, n=4):
        _docs(tmp, {"Runbook/Omni.md": _bigdoc(n)})   # H1 "# Big Doc", sections ## Section 0..n-1

    def _draftrow(self, disps, outline=None):
        return {"doc": "Runbook/Omni.md", "dispositions": disps, "master_outline": outline or []}

    def test_build_restructure_work_has_sections_and_wiki_menu(self):
        tmp, stage = tempfile.mkdtemp(), tempfile.mkdtemp()
        _docs(tmp, {"Runbook/Omni.md": _bigdoc(11),
                    "Reference/Docker-Media-Stack.md": "# Docker Media Stack\n\nthe stack\n",
                    "Reference/CURRENT-PROJECTS.md": "# Tracker\n\nrolling\n"})
        local, unc, name = LR.build_restructure_work("Runbook/Omni.md", min_sections=10,
                                                     docs_dir=tmp, stage_dir=stage)
        work = json.load(io.open(local, encoding="utf-8"))
        self.assertEqual(work["task"], "restructure-map")
        it = work["items"][0]
        self.assertEqual(len(it["sections"]), 11)
        paths = [w["path"] for w in it["wikis"]]
        self.assertIn("Reference/Docker-Media-Stack.md", paths)     # a real wiki is a drop target
        self.assertNotIn("Reference/CURRENT-PROJECTS.md", paths)    # the tracker is excluded
        self.assertNotIn("Runbook/Omni.md", paths)                 # the doc itself is excluded
        self.assertTrue(name.startswith("s30-restructure-"))

    def test_build_restructure_work_refuses_small_doc(self):
        tmp = tempfile.mkdtemp()
        _docs(tmp, {"Runbook/Small.md": _bigdoc(3)})
        with self.assertRaises(ValueError):
            LR.build_restructure_work("Runbook/Small.md", min_sections=10, docs_dir=tmp,
                                      stage_dir=tempfile.mkdtemp())

    def test_spec_from_draft_groups_children_and_marks_scaffold(self):
        tmp = tempfile.mkdtemp()
        self._omni(tmp, 4)
        disps = [
            {"heading": "## Section 0", "disposition": "child", "child_title": "Omni - alpha ops"},
            {"heading": "## Section 1", "disposition": "child", "child_title": "Omni - alpha ops"},
            {"heading": "## Section 2", "disposition": "retain"},
            {"heading": "## Section 3", "disposition": "drop",
             "drop_target": "Reference/Docker-Media-Stack.md"},
        ]
        spec, summary = LR.restructure_spec_from_draft("Runbook/Omni.md", self._draftrow(disps),
                                                       docs_dir=tmp)
        self.assertTrue(spec["_scaffold"])
        self.assertEqual(len(spec["children"]), 1)                  # two sections, one child_title
        self.assertEqual(spec["children"][0]["units"], ["## Section 0", "## Section 1"])
        self.assertEqual(spec["drop"], ["## Section 3"])
        retains = [b["retain"] for b in spec["master"]["blocks"] if "retain" in b]
        self.assertEqual(retains, ["## Section 2"])
        self.assertTrue(any(b.get("index") for b in spec["master"]["blocks"]))
        self.assertIn("1 child page(s), 1 retained, 1 dropped", summary[0])

    def test_spec_from_draft_refuses_unassigned_section(self):
        tmp = tempfile.mkdtemp()
        self._omni(tmp, 4)
        disps = [{"heading": "## Section 0", "disposition": "retain"}]   # 1..3 unassigned
        with self.assertRaises(ValueError) as cm:
            LR.restructure_spec_from_draft("Runbook/Omni.md", self._draftrow(disps), docs_dir=tmp)
        self.assertIn("NO disposition", str(cm.exception))

    def test_spec_from_draft_near_miss_heading_remapped(self):
        tmp = tempfile.mkdtemp()
        self._omni(tmp, 2)
        disps = [{"heading": "## Section 0", "disposition": "retain"},
                 {"heading": "## Sectionn 1", "disposition": "drop", "drop_target": ""}]  # typo
        spec, _ = LR.restructure_spec_from_draft("Runbook/Omni.md", self._draftrow(disps),
                                                 docs_dir=tmp)
        self.assertEqual(spec["drop"], ["## Section 1"])            # remapped to the real heading

    def test_spec_from_draft_child_without_title_refuses(self):
        tmp = tempfile.mkdtemp()
        self._omni(tmp, 2)
        disps = [{"heading": "## Section 0", "disposition": "child"},   # no child_title
                 {"heading": "## Section 1", "disposition": "retain"}]
        with self.assertRaises(ValueError) as cm:
            LR.restructure_spec_from_draft("Runbook/Omni.md", self._draftrow(disps), docs_dir=tmp)
        self.assertIn("no child_title", str(cm.exception))

    def test_scaffold_restructure_writes_spec_to_stage(self):
        tmp, stage = tempfile.mkdtemp(), tempfile.mkdtemp()
        self._omni(tmp, 2)
        name = LR._restructure_name("Runbook/Omni.md")
        disps = [{"heading": "## Section 0", "disposition": "retain"},
                 {"heading": "## Section 1", "disposition": "retain"}]
        io.open(os.path.join(stage, name + ".drafts.json"), "w", encoding="utf-8").write(
            json.dumps({"results": [self._draftrow(disps)]}))
        spec_unc, summary = LR.scaffold_restructure("Runbook/Omni.md", docs_dir=tmp, stage_dir=stage)
        self.assertTrue(os.path.exists(os.path.join(stage, name + ".spec.json")))
        written = json.load(io.open(os.path.join(stage, name + ".spec.json"), encoding="utf-8"))
        self.assertTrue(written["_scaffold"])
        self.assertEqual(written["doc_rel"], "Runbook/Omni.md")


class TestRestructureTool(unittest.TestCase):
    def test_load_restructure_spec_resolves_paths(self):
        stage, docs = tempfile.mkdtemp(), tempfile.mkdtemp()
        spec = {"doc_rel": "Runbook/RUNBOOK.md", "archive_name": "runbook-split-x",
                "children": [], "drop": [], "master": {"title": "# M", "blocks": []}}
        io.open(os.path.join(stage, "s.spec.json"), "w", encoding="utf-8").write(json.dumps(spec))
        out = LR.load_restructure_spec("s.spec.json", docs_dir=docs, stage_dir=stage)
        self.assertEqual(out["doc"], os.path.join(docs, "Runbook", "RUNBOOK.md"))
        self.assertEqual(out["archive_dir"], os.path.join(docs, "Archive", "runbook-split-x"))

    def test_load_restructure_spec_defaults_archive_from_stem(self):
        stage, docs = tempfile.mkdtemp(), tempfile.mkdtemp()
        io.open(os.path.join(stage, "s.spec.json"), "w", encoding="utf-8").write(
            json.dumps({"doc_rel": "Runbook/RUNBOOK.md", "master": {"title": "# M", "blocks": []}}))
        out = LR.load_restructure_spec("s.spec.json", docs_dir=docs, stage_dir=stage)
        self.assertEqual(out["archive_dir"], os.path.join(docs, "Archive", "restructure-runbook"))

    def test_load_restructure_spec_refuses_missing_doc_rel(self):
        stage = tempfile.mkdtemp()
        io.open(os.path.join(stage, "s.spec.json"), "w", encoding="utf-8").write(
            json.dumps({"master": {"title": "# M", "blocks": []}}))
        with self.assertRaises(ValueError):
            LR.load_restructure_spec("s.spec.json", docs_dir=tempfile.mkdtemp(), stage_dir=stage)

    def test_apply_restructure_round_trips(self):
        docs, stage = tempfile.mkdtemp(), tempfile.mkdtemp()
        _docs(docs, {"Runbook/Doc.md": "# Old\n\nintro\n\n## A\n\na body\n\n## B\n\nb body\n"})
        spec = {"doc_rel": "Runbook/Doc.md", "archive_name": "arch-x",
                "children": [{"file": "Doc-A.md", "title": "# Doc A", "units": ["## A"]}],
                "master": {"title": "# New", "blocks": [{"text": "hi"}, {"retain": "## B"},
                                                        {"index": True}]},
                "drop": []}
        io.open(os.path.join(stage, "s.spec.json"), "w", encoding="utf-8").write(json.dumps(spec))
        loaded = LR.load_restructure_spec("s.spec.json", docs_dir=docs, stage_dir=stage)
        rep = LR.apply_restructure(loaded)
        child = io.open(os.path.join(docs, "Runbook", "Doc-A.md"), encoding="utf-8").read()
        master = io.open(os.path.join(docs, "Runbook", "Doc.md"), encoding="utf-8").read()
        self.assertIn("## A\n\na body", child)
        self.assertIn("## B\n\nb body", master)              # retained verbatim
        self.assertIn("see `Doc-A.md`", master)
        self.assertNotIn("a body", master)                   # shipped to the child
        self.assertEqual(rep["children"], 1)

    def test_apply_restructure_registers_a_pending_action_not_an_immediate_write(self):
        import library_restructure as _LR
        with mock.patch.object(_LR, "load_restructure_spec",
                               return_value={"doc": "/docs/Runbook/RUNBOOK.md"}), \
             mock.patch.object(_LR, "apply_restructure",
                               return_value={"units": 6, "children": 2,
                                             "child_files": ["a.md", "b.md"],
                                             "retained": ["## X"], "dropped": ["## Y"]}) as applied, \
             mock.patch.object(tools.pending_actions, "register",
                               return_value={"status": "pending"}) as reg:
            out = tools._library_apply_restructure_tool({"spec": "s.spec.json"}, "Alex")
        self.assertEqual(out, {"status": "pending"})
        reg.assert_called_once()
        applied.assert_called_once_with({"doc": "/docs/Runbook/RUNBOOK.md"}, dry_run=True)

    def test_load_restructure_spec_refuses_scaffold(self):
        stage = tempfile.mkdtemp()
        io.open(os.path.join(stage, "s.spec.json"), "w", encoding="utf-8").write(
            json.dumps({"_scaffold": True, "doc_rel": "Runbook/RUNBOOK.md",
                        "master": {"title": "# M", "blocks": []}}))
        with self.assertRaises(ValueError) as cm:
            LR.load_restructure_spec("s.spec.json", docs_dir=tempfile.mkdtemp(), stage_dir=stage)
        self.assertIn("SCAFFOLD", str(cm.exception))

    def test_propose_restructure_submits_an_order_not_a_write(self):
        import library_restructure as _LR
        with mock.patch.object(tools.notes, "log"), \
             mock.patch.object(_LR, "build_restructure_work",
                               return_value=("/l.json", "\\\\unc\\l.json", "s30-restructure-x")), \
             mock.patch.object(_LR, "submit_order",
                               return_value={"placed": [{"host": "mothership"}],
                                             "drafts_unc": "\\\\unc\\d.json"}) as sub:
            out = tools._library_propose_restructure_tool({"doc": "Runbook/RUNBOOK.md"}, "Alex")
        sub.assert_called_once_with("s30-restructure-x", "restructure-map")
        self.assertIn("submitted", out)

    def test_scaffold_restructure_tool_calls_engine_and_flags_author(self):
        import library_restructure as _LR
        with mock.patch.object(tools.notes, "log"), \
             mock.patch.object(_LR, "scaffold_restructure",
                               return_value=("\\\\unc\\s30-restructure-x.spec.json",
                                             ["Omni.md -> 1 child page(s)"])) as sc:
            out = tools._library_scaffold_restructure_tool({"doc": "Runbook/RUNBOOK.md"}, "Alex")
        sc.assert_called_once()
        self.assertIn("AUTHOR-ME", out)
        self.assertIn("_scaffold", out)


class TestVerifyGate(unittest.TestCase):
    def test_gate_raises_when_verify_never_ran(self):
        with self.assertRaises(ValueError) as cm:
            LR.verify_gate("cameras", stage_dir=tempfile.mkdtemp())
        self.assertIn("VERIFY never ran", str(cm.exception))

    def test_gate_raises_on_undispositioned_non_current(self):
        stage = tempfile.mkdtemp()
        _verdicts(stage, "cameras", "softserve",
                  [{"id": "c1", "verdict": "UNREACHABLE", "claim_text": "frigate:5000"}])
        with self.assertRaises(ValueError) as cm:
            LR.verify_gate("cameras", stage_dir=stage)
        self.assertIn("unresolved", str(cm.exception))

    def test_gate_passes_when_non_current_is_dispositioned(self):
        stage = tempfile.mkdtemp()
        _verdicts(stage, "cameras", "softserve",
                  [{"id": "c1", "verdict": "UNREACHABLE", "claim_text": "frigate:5000"}])
        _disp(stage, "cameras", {"c1": {"action": "archive", "note": "frigate deleted 08-12"}})
        summ = LR.verify_gate("cameras", stage_dir=stage)
        self.assertEqual(summ["needs_disposition"], 1)
        self.assertEqual(summ["dispositioned"], 1)

    def test_gate_passes_when_all_current(self):
        stage = tempfile.mkdtemp()
        _verdicts(stage, "cameras", "softserve",
                  [{"id": "c1", "verdict": "CURRENT"}, {"id": "c2", "verdict": "CURRENT"}])
        self.assertEqual(LR.verify_gate("cameras", stage_dir=stage)["needs_disposition"], 0)

    def test_gate_merges_verdicts_across_boxes(self):
        stage = tempfile.mkdtemp()
        _verdicts(stage, "cameras", "softserve", [{"id": "c1", "verdict": "CURRENT"}])
        _verdicts(stage, "cameras", "mothership", [{"id": "c2", "verdict": "CURRENT"}])
        self.assertEqual(sum(LR.verify_gate("cameras", stage_dir=stage)["verdict_counts"].values()), 2)

    def test_gate_ignores_gold_rows_for_disposition(self):
        # a gold negative is intentionally STALE; it must NOT force a disposition
        stage = tempfile.mkdtemp()
        _verdicts(stage, "cameras", "softserve",
                  [{"id": "gold-neg", "verdict": "STALE"}, {"id": "c1", "verdict": "CURRENT"}])
        self.assertEqual(LR.verify_gate("cameras", stage_dir=stage)["needs_disposition"], 0)

    def test_gate_raises_when_gold_control_wrong(self):
        # instrument sanity: a gold control that missed its expected verdict voids the sweep
        stage = tempfile.mkdtemp()
        _claims(stage, "cameras", [{"id": "gold-pos", "type": "path_fresh", "expect": "CURRENT"}])
        _verdicts(stage, "cameras", "softserve", [{"id": "gold-pos", "verdict": "STALE"}])
        with self.assertRaises(ValueError) as cm:
            LR.verify_gate("cameras", stage_dir=stage)
        self.assertIn("not trustworthy", str(cm.exception))

    def test_gate_requires_judgment_claims_to_be_dispositioned(self):
        # a judgment claim gets no mechanical verdict; unresolved until dispositioned. This is the
        # class (room map, stream res, "supports X") that slipped the pipeline and shipped stale.
        stage = tempfile.mkdtemp()
        _claims(stage, "cameras", [{"id": "j1", "type": "judgment", "claim_text": "ch2 = basement"}])
        _verdicts(stage, "cameras", "softserve", [{"id": "gold-x", "verdict": "CURRENT"}])
        with self.assertRaises(ValueError) as cm:
            LR.verify_gate("cameras", stage_dir=stage)
        self.assertIn("unresolved", str(cm.exception))
        _disp(stage, "cameras", {"j1": {"verdict": "STALE", "action": "rewrite ch2 -> basement"}})
        self.assertEqual(LR.verify_gate("cameras", stage_dir=stage)["dispositioned"], 1)

    def test_verified_state_block_includes_truth_and_verdicts(self):
        stage = tempfile.mkdtemp()
        _verdicts(stage, "cameras", "softserve",
                  [{"id": "c1", "verdict": "CURRENT", "claim_text": "detector on :8801",
                    "evidence": "tcp ok"}])
        io.open(os.path.join(stage, "s30-cameras-current-truth.md"), "w",
                encoding="utf-8").write("NVR -> tablet decode -> ncnn. Frigate deleted 08-12.")
        block, _summ = LR.verified_state_block("cameras", stage_dir=stage)
        self.assertIn("Frigate deleted 08-12", block)
        self.assertIn("detector on :8801", block)
        self.assertIn("Verified current state", block)

    def test_verified_state_block_runs_the_gate_first(self):
        with self.assertRaises(ValueError):
            LR.verified_state_block("cameras", stage_dir=tempfile.mkdtemp())

    def test_build_split_work_embeds_verified_state(self):
        tmp, stage = tempfile.mkdtemp(), tempfile.mkdtemp()
        _docs(tmp, {"Reference/Big.md": _bigdoc(12)})
        with mock.patch.dict(LR.DOMAINS, {"d": ["Reference/Big.md"]}):
            local, _unc, _inc = LR.build_split_work("d", min_sections=10, docs_dir=tmp,
                                                    stage_dir=stage, verified_state="LIVE TRUTH X")
        work = json.load(io.open(local, encoding="utf-8"))
        self.assertEqual(work["items"][0]["verified_state"], "LIVE TRUTH X")

    def test_build_index_work_embeds_verified_state(self):
        tmp, stage = tempfile.mkdtemp(), tempfile.mkdtemp()
        _docs(tmp, {"Reference/A.md": "# Doc A\n\nlead a\n"})
        with mock.patch.dict(LR.DOMAINS, {"d": ["Reference/A.md"]}):
            local, _unc = LR.build_index_work("d", docs_dir=tmp, stage_dir=stage,
                                              verified_state="LIVE TRUTH Y")
        work = json.load(io.open(local, encoding="utf-8"))
        self.assertEqual(work["items"][0]["verified_state"], "LIVE TRUTH Y")

    def test_submit_verify_order_is_cpu_readonly_per_box(self):
        seen = []

        def fake(spec, requires, box):
            seen.append((spec, requires, box))
            return {"host": box}
        LR.submit_verify_order("cameras", ["softserve", "mothership"], submit_fn=fake)
        self.assertEqual(len(seen), 2)
        spec0, req0, _box0 = seen[0]
        self.assertEqual(spec0["load"], "cpu")
        self.assertTrue(spec0["payload"].endswith("triage_verify.py"))
        self.assertEqual(spec0["args"][2], "softserve")
        self.assertIn("order.compute", req0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
