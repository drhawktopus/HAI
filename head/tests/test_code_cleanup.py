"""Pins for code_cleanup.py + the #30 code-half operator tools.

Hermetic: the code file lives in a temp mirror, staging is a temp dir, and the real detector +
applier are copied into a temp Lab/bin so the engine's integration is exercised without depending on
any container mount. The order submit is injected. Covers work-file building, the byte-exact apply
(archive-first, ast-checked, re-scanned), the refusal cases (clean file, partial draft, drifted span,
path escape), and admin-gating + pending-not-immediate-write of the two tools.
"""
import ast
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import code_cleanup as CC
import tools


# These tests build their mirrors as <root>/Lab/...: pin that table for the module, whatever CODE_MIRRORS the
# environment running the suite carries.
_MIRRORS = mock.patch.object(CC, "CODE_MIRRORS", {"orchestrator": "orchestrator-build", "foundry": "mothership-build",
                                                  "tolkien": "tolkien-build", "lab": "Lab", "menu": "menu"})


def setUpModule():
    _MIRRORS.start()


def tearDownModule():
    _MIRRORS.stop()


class TheOrderIsWeighedNotPinned(unittest.TestCase):
    """The cleanup order states its work family and tier and names no model: the desk weighs the menu's rows and fills
    the model and window from the chosen one (the routing checklist's "Orders that name a model" row)."""

    class _Desk:
        def __init__(self, reply):
            self.reply, self.bodies = reply, []

        def FactoryOrderReq(self, **body):
            self.bodies.append(body)
            return body

        def p20_order(self, req):
            return self.reply

    def test_the_body_names_no_model(self):
        body = CC.code_order_body("s30-code-menu")
        self.assertEqual(body["requires"], ["order.compute"])
        self.assertEqual((body["family"], body["tier"], body["kind"], body["urgency"]),
                         ("work-w1", "lead", "review", "background"))
        args = body["spec"]["args"]
        self.assertEqual(args[args.index("--model") + 1], "{POLICY_MODEL}")
        self.assertEqual(args[args.index("--num-ctx") + 1], "{POLICY_NUM_CTX}")
        self.assertIn("{POLICY_THINK_ARGS}", args, "the row's thinking, never a hard-coded off")
        self.assertIn("{POLICY_TEMPERATURE_ARGS}", args)
        self.assertNotIn("model.", " ".join(body["requires"]))

    def test_a_placed_order_says_where(self):
        desk = self._Desk({"ok": True, "steps": [{"id": "abc", "host": "bighonker"}]})
        res = CC.submit_code_order("s30-code-menu", desk=desk)
        self.assertEqual(res["placed"], [{"host": "bighonker", "id": "abc", "order": "s30-code-menu"}])
        self.assertEqual(desk.bodies[0]["tier"], "lead")

    def test_a_held_order_waits_at_the_desk(self):
        desk = self._Desk({"ok": True, "held": True, "id": "h1", "why": ["every box is busy"]})
        res = CC.submit_code_order("s30-code-menu", desk=desk)
        self.assertEqual(res["placed"][0]["id"], "h1")

    def test_a_refusal_is_an_error_not_an_empty_success(self):
        desk = self._Desk({"ok": False, "error": "no row"})
        with self.assertRaises(RuntimeError):
            CC.submit_code_order("s30-code-menu", desk=desk)


class TheMirrorTableIsASetting(unittest.TestCase):
    def _table(self, value):
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "CODE_MIRRORS"}
        if value is not None:
            env["CODE_MIRRORS"] = value
        head = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, "-c", "import json, code_cleanup as C; print(json.dumps(C.CODE_MIRRORS))"],
                           cwd=head, env=env, capture_output=True, text=True, timeout=60)
        return r.returncode, (json.loads(r.stdout) if r.returncode == 0 else r.stderr)

    def test_the_setting_names_each_mirrors_folder(self):
        rc, table = self._table('{"lab": "Lab", "menu": "some/folder"}')
        self.assertEqual((rc, table), (0, {"lab": "Lab", "menu": "some/folder"}))

    def test_without_the_setting_the_folders_are_the_public_trees(self):
        rc, table = self._table(None)
        self.assertEqual(rc, 0, table)
        self.assertEqual(table, {"orchestrator": "head", "foundry": "foundry", "tolkien": "tablet-foundry",
                                 "lab": "tools", "menu": "menu", "engine": "engine"})

    def test_a_bad_value_stops_the_import(self):
        rc, _err = self._table("{not json")
        self.assertNotEqual(rc, 0)

    def test_both_cleanup_tools_name_every_mirror_in_the_table(self):
        # the model reads the tool's own schema as its contract: a mirror the setting adds but the schema does not
        # name is refused as "outside the schema"
        for spec in (tools._CODE_PROPOSE_CLEANUP, tools._CODE_APPLY_CLEANUP):
            said = spec["function"]["parameters"]["properties"]["mirror"]["description"]
            for name in CC.CODE_MIRRORS:
                self.assertIn(name, said, spec["function"]["name"])


def _find_lab_bin():
    """The real Lab/bin, from the container mount or the local checkout — the test copies the
    detector + applier out of it so it exercises the real code, not a reimplementation."""
    here = os.path.dirname(os.path.abspath(__file__))
    for root in ("/downloads/Lab/bin", "/code-mirror/Lab/bin",
                 os.path.join(here, "..", "..", "Lab", "bin")):
        if os.path.exists(os.path.join(root, "code_narrative_scan.py")):
            return os.path.abspath(root)
    raise unittest.SkipTest("Lab/bin (code_narrative_scan.py) not reachable from the test host")


DIRTY = ("import os\n"
         "x = 3  # trailing note 2026-01-01, war story\n"
         "\n"
         "# a dated block 2026-02-02\n"
         "# second line, Alex said so\n"
         "def f():\n"
         '    """Docstring ' + "2026-03-03" + ': fixed after the outage — unicode."""\n'
         "    return x\n")


def _mirror(src=DIRTY, rel="bin/t.py"):
    """A temp code_root with `src` at lab/<rel>, a temp stage, and a temp lab_bin holding the real
    detector+applier. Returns (code_root, stage, lab_bin, rel)."""
    real = _find_lab_bin()
    root = tempfile.mkdtemp(prefix="cc-root-")
    stage = tempfile.mkdtemp(prefix="cc-stage-")
    lab_bin = tempfile.mkdtemp(prefix="cc-lab-")
    for m in ("code_narrative_scan.py", "code_apply_cleanup.py"):
        shutil.copy(os.path.join(real, m), os.path.join(lab_bin, m))
    p = os.path.join(root, "Lab", rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, "w", encoding="utf-8", newline="").write(src)
    return root, stage, lab_bin, rel


def _drafts(stage, name, work, keep_docstring=True, drop_ids=()):
    """Simulate the payload output: KEEP-CLEAN docstrings, CUT comments, omit any id in drop_ids."""
    results = []
    for it in work["items"]:
        if it["id"] in drop_ids:
            continue
        if it["kind"] == "docstring" and keep_docstring:
            results.append({"id": it["id"], "disposition": "KEEP-CLEAN",
                            "new_text": '    """Return x."""\n    raise RuntimeError("copied context")'})
        elif it["id"] == "0001":
            results.append({"id": it["id"], "disposition": "KEEP-CLEAN",
                            "new_text": "# Keep x stable.\nx = 999  # copied context"})
        else:
            results.append({"id": it["id"], "disposition": "CUT", "new_text": ""})
    io.open(os.path.join(stage, name + ".drafts.json"), "w", encoding="utf-8",
            newline="\n").write(json.dumps({"results": results}, ensure_ascii=False))


class TestEngine(unittest.TestCase):
    def test_a_virtual_environment_under_any_name_is_not_swept(self):
        """Third-party code is not the lab's: a `.venv-inspect` folder in a mirror is skipped like `.venv`."""
        root, stage, lab, rel = _mirror()
        vendored = os.path.join(root, "Lab", ".venv-inspect", "Lib", "site-packages", "pkg.py")
        os.makedirs(os.path.dirname(vendored), exist_ok=True)
        with io.open(vendored, "w", encoding="utf-8", newline="") as f:
            f.write(DIRTY)
        info = CC.build_code_work("lab", stage_dir=stage, code_root=root, lab_bin=lab)
        self.assertEqual(info["files"], 1, "only the mirror's own file is swept")

    def test_build_apply_end_to_end(self):
        root, stage, lab, rel = _mirror()
        rel2 = "bin/u.py"
        p2 = os.path.join(root, "Lab", rel2.replace("/", os.sep))
        with io.open(p2, "w", encoding="utf-8", newline="") as f:
            f.write(DIRTY.replace("x = 3", "x = 4"))
        stale = os.path.join(root, "Lab", "_pre-sync-20260730-181424", "stale.py")
        os.makedirs(os.path.dirname(stale), exist_ok=True)
        with io.open(stale, "w", encoding="utf-8", newline="") as f:
            f.write(DIRTY)
        info = CC.build_code_work("lab", stage_dir=stage, code_root=root, lab_bin=lab)
        self.assertEqual(info["files"], 2, "restore snapshots are not active source")
        self.assertEqual(info["candidates"], 6, "three candidates in each of two files")
        old_drafts = os.path.join(stage, info["name"] + ".drafts.json")
        with io.open(old_drafts, "w", encoding="utf-8", newline="\n") as f:
            f.write("{}")
        info = CC.build_code_work("lab", stage_dir=stage, code_root=root, lab_bin=lab)
        self.assertFalse(os.path.exists(old_drafts), "fresh work cannot see stale drafts")
        self.assertEqual(len([n for n in os.listdir(stage)
                              if n.startswith(info["name"] + ".drafts.json.superseded-")]), 1)
        work = CC.read_code_work(info["name"], stage)
        self.assertEqual(len(work["items"]), 6)
        self.assertEqual(len(work["candidates"]), 6, "candidates carry file-scoped offsets")
        self.assertTrue(all(c["raw"].endswith(('"""', "'''"))
                            for c in work["candidates"] if c["kind"] == "docstring"))
        self.assertEqual({c["file"] for c in work["candidates"]}, {rel, rel2})
        _drafts(stage, info["name"], work)

        prev = CC.code_apply(info["name"], stage_dir=stage, code_root=root, lab_bin=lab, dry_run=True)
        self.assertEqual(prev["n_files"], 2)
        self.assertEqual(prev["totals"], {"KEEP-CLEAN": 3, "CUT": 3, "MOVE": 0})
        self.assertEqual(prev["remaining"], 0)
        self.assertTrue(prev.get("dry_run"))
        self.assertEqual(CC._semantic_ast("\ufeffx = 1\n"),
                         CC._semantic_ast("\ufeffx = 1  # commentary\n"))

        done = CC.code_apply(info["name"], stage_dir=stage, code_root=root, lab_bin=lab,
                             dry_run=False, stamp="test")
        self.assertTrue(done.get("applied"))
        self.assertEqual(len(done["archived"]), 2)
        for target in (rel, rel2):
            path = os.path.join(root, "Lab", target.replace("/", os.sep))
            self.assertTrue(os.path.exists(path + ".preclean-test"), "each original must be archived")
            out = io.open(path, encoding="utf-8").read()
            tree = ast.parse(out)
            self.assertIn("return x", out)
            fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
            self.assertEqual(ast.get_docstring(fn), "Return x.")
            self.assertNotIn("x = 999", out, "copied context must never become executable code")
            self.assertNotIn("raise RuntimeError", out, "copied context must be discarded")
            for gone in ("war story", "outage", "Alex said", "dated block"):
                self.assertNotIn(gone, out, "narrative survived in %s: %s" % (target, gone))

    def test_clean_file_raises_no_order(self):
        clean = ("# insert the script dir before sibling imports\n"
                 "y = '2026-01-01 is data in a string, not a comment'\n")
        root, stage, lab, rel = _mirror(src=clean)
        with self.assertRaises(ValueError):
            CC.build_code_work("lab", rel, stage_dir=stage, code_root=root, lab_bin=lab)

    def test_missing_draft_refuses_partial_apply(self):
        root, stage, lab, rel = _mirror()
        info = CC.build_code_work("lab", rel, stage_dir=stage, code_root=root, lab_bin=lab)
        work = CC.read_code_work(info["name"], stage)
        _drafts(stage, info["name"], work, drop_ids=("0001",))    # one candidate undispositioned
        with self.assertRaises(ValueError):
            CC.code_apply(info["name"], stage_dir=stage, code_root=root, lab_bin=lab, dry_run=True)

    def test_drifted_span_refused(self):
        root, stage, lab, rel = _mirror()
        info = CC.build_code_work("lab", rel, stage_dir=stage, code_root=root, lab_bin=lab)
        work = CC.read_code_work(info["name"], stage)
        _drafts(stage, info["name"], work)
        p = os.path.join(root, "Lab", rel.replace("/", os.sep))    # mutate AFTER the scan
        src = io.open(p, encoding="utf-8").read()
        io.open(p, "w", encoding="utf-8", newline="").write("z = 0  # prepended line shifts every offset\n" + src)
        with self.assertRaises(ValueError):
            CC.code_apply(info["name"], stage_dir=stage, code_root=root, lab_bin=lab, dry_run=True)

    def test_path_escape_refused(self):
        root, stage, lab, _rel = _mirror()
        with self.assertRaises(ValueError):
            CC.build_code_work("lab", "../../etc/passwd", stage_dir=stage, code_root=root, lab_bin=lab)

    def test_unknown_mirror_refused(self):
        with self.assertRaises(ValueError):
            CC._code_path("nope", "x.py", code_root=tempfile.mkdtemp())

class TestApplyFingerprint(unittest.TestCase):
    """The preview/confirm fingerprint gate, hermetic: a stub detector+applier stands in for Lab/bin
    so the gate is exercised on every host, not only where the mount exists."""

    STORY = "# story\nx = 1\n"

    def _stub(self, marker="# story"):
        """A stand-in detector/applier that reports the real byte span of `marker`, so the drift
        guard can be exercised on its own."""
        class Stub:
            def scan_source(self, src, rel, **_kw):
                i = src.find(marker)
                if i < 0:
                    return []
                line = src[:i].count("\n") + 1
                return [{"kind": "comment", "line_start": line, "line_end": line,
                         "text": marker, "raw": marker, "off_start": i, "off_end": i + len(marker)}]

            def plan(self, src, fc, disp):
                return src.replace(marker + "\n", ""), {"KEEP-CLEAN": 0, "CUT": 1, "MOVE": 0}, []
        return Stub()

    def _stage(self):
        """A staged work + drafts + source triple for mirror 'lab' with one candidate."""
        root = tempfile.mkdtemp(prefix="ccfp-root-")
        stage = tempfile.mkdtemp(prefix="ccfp-stage-")
        rel = "bin/t.py"
        path = os.path.join(root, "Lab", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        io.open(path, "w", encoding="utf-8", newline="").write(self.STORY)
        name = "s30-code-lab-fp"
        work = {"version": "code-work-v2", "task": "code", "mirror": "lab", "file": None,
                "files": [rel], "items": [{"id": "0001", "kind": "comment", "file": rel, "lines": "1-1"}],
                "candidates": [{"id": "0001", "kind": "comment", "file": rel, "line_start": 1,
                                "line_end": 1, "text": "# story", "raw": "# story",
                                "off_start": 0, "off_end": 7}]}
        drafts = {"results": [{"id": "0001", "disposition": "CUT", "new_text": ""}]}
        io.open(os.path.join(stage, name + ".work.json"), "w", encoding="utf-8",
                newline="\n").write(json.dumps(work))
        io.open(os.path.join(stage, name + ".drafts.json"), "w", encoding="utf-8",
                newline="\n").write(json.dumps(drafts))
        return root, stage, name, rel

    def _apply(self, stage, root, name, **kw):
        with mock.patch.object(CC, "_lab_import", return_value=self._stub()):
            return CC.code_apply(name, stage_dir=stage, code_root=root, **kw)

    def test_preview_carries_a_fingerprint(self):
        root, stage, name, rel = self._stage()
        prev = self._apply(stage, root, name, dry_run=True)
        fp = prev["fingerprint"]
        self.assertEqual(fp["version"], "code-apply-fingerprint-v1")
        self.assertTrue(fp["work_sha256"] and fp["drafts_sha256"], "both staged inputs are hashed")
        self.assertEqual(sorted(fp["sources"]), [rel], "every target source is hashed")
        self.assertEqual(fp["sources"][rel], CC._file_sha256(
            os.path.join(root, "Lab", rel.replace("/", os.sep))))

    def test_unchanged_inputs_apply(self):
        root, stage, name, rel = self._stage()
        prev = self._apply(stage, root, name, dry_run=True)
        done = self._apply(stage, root, name, dry_run=False, stamp="fp",
                           expect_fingerprint=prev["fingerprint"])
        self.assertTrue(done.get("applied"), "identical bytes apply exactly as before")
        self.assertTrue(os.path.exists(os.path.join(root, "Lab", rel) + ".preclean-fp"))

    def test_drafts_changed_after_preview_refuse_and_write_nothing(self):
        root, stage, name, rel = self._stage()
        prev = self._apply(stage, root, name, dry_run=True)
        source = os.path.join(root, "Lab", rel)
        before = io.open(source, encoding="utf-8").read()
        # Reviewers may correct a drafts file after a preview is staged; the apply must refuse rather
        # than write text nobody previewed.
        drafts = json.load(io.open(os.path.join(stage, name + ".drafts.json"), encoding="utf-8"))
        drafts["results"][0]["new_text"] = "# Revised wording nobody previewed."
        io.open(os.path.join(stage, name + ".drafts.json"), "w", encoding="utf-8",
                newline="\n").write(json.dumps(drafts, ensure_ascii=False))
        try:
            self._apply(stage, root, name, dry_run=False, stamp="fp2",
                       expect_fingerprint=prev["fingerprint"])
            self.fail("a changed drafts file must refuse the apply")
        except ValueError as exc:
            self.assertIn("drafts file changed after the preview", str(exc))
        self.assertEqual(io.open(source, encoding="utf-8").read(), before,
                         "nothing may be written when the fingerprint mismatches")
        self.assertFalse(any(n.endswith(".preclean-fp2") for n in os.listdir(os.path.join(root, "Lab"))),
                         "a refused apply must not archive anything")

    def test_work_file_changed_after_preview_refuses(self):
        root, stage, name, rel = self._stage()
        prev = self._apply(stage, root, name, dry_run=True)
        path = os.path.join(stage, name + ".work.json")
        data = json.load(io.open(path, encoding="utf-8"))
        data["rebuilt"] = True
        io.open(path, "w", encoding="utf-8", newline="\n").write(json.dumps(data, ensure_ascii=False))
        with self.assertRaises(ValueError) as cm:
            self._apply(stage, root, name, dry_run=False, stamp="fp3",
                       expect_fingerprint=prev["fingerprint"])
        self.assertIn("work file changed after the preview", str(cm.exception))

    def test_source_changed_after_preview_refuses(self):
        """A source edited after the preview is refused even when the edit keeps every candidate span
        the same length, which is the case span-drift cannot see."""
        root, stage, name, rel = self._stage()
        prev = self._apply(stage, root, name, dry_run=True)
        source = os.path.join(root, "Lab", rel)
        src = io.open(source, encoding="utf-8").read()
        edited = src.replace("x = 1", "x = 2")
        self.assertEqual(len(edited), len(src), "the edit must not move any offset")
        io.open(source, "w", encoding="utf-8", newline="").write(edited)
        with self.assertRaises(ValueError) as cm:
            self._apply(stage, root, name, dry_run=False, stamp="fp4",
                       expect_fingerprint=prev["fingerprint"])
        self.assertIn("source %s changed after the preview" % rel, str(cm.exception))
        self.assertEqual(io.open(source, encoding="utf-8").read(), edited,
                         "the refused apply leaves the file alone")

    def test_missing_fingerprint_is_not_a_match(self):
        root, stage, name, rel = self._stage()
        with self.assertRaises(ValueError) as cm:
            self._apply(stage, root, name, dry_run=False, stamp="fp6",
                       expect_fingerprint={"version": "code-apply-fingerprint-v1", "name": name})
        self.assertIn("no fingerprint", str(cm.exception))

    def test_fingerprint_fires_first_and_drift_guard_still_works_alone(self):
        """An edit that moves offsets is caught by the fingerprint when one is given (the check runs
        before planning), and by the pre-existing drift guard when no fingerprint is given: both
        guards hold and neither masks the other."""
        root, stage, name, rel = self._stage()
        prev = self._apply(stage, root, name, dry_run=True)
        source = os.path.join(root, "Lab", rel)
        io.open(source, "w", encoding="utf-8", newline="").write("z = 0  # prepended line\n" + self.STORY)
        with self.assertRaises(ValueError) as cm:
            self._apply(stage, root, name, dry_run=False, stamp="fp5",
                       expect_fingerprint=prev["fingerprint"])
        self.assertIn("source %s changed after the preview" % rel, str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            self._apply(stage, root, name, dry_run=True)
        self.assertIn("drifted", str(cm.exception))

    def test_fingerprint_mismatch_lists_every_changed_input(self):
        good = {"version": "code-apply-fingerprint-v1", "name": "n", "work_sha256": "a" * 64,
                "drafts_sha256": "b" * 64, "sources": {"a.py": "c" * 64, "b.py": "d" * 64}}
        bad = dict(good, work_sha256="e" * 64, sources={"a.py": "f" * 64, "b.py": "d" * 64})
        out = CC._fingerprint_mismatch(good, bad)
        self.assertEqual(len(out), 2, "both the work file and one source are named")
        self.assertEqual(CC._fingerprint_mismatch(good, good), [])


class TestCodeTools(unittest.TestCase):
    def test_code_tools_admin_only(self):
        for t in ("code_propose_cleanup", "code_apply_cleanup"):
            self.assertFalse(tools._allowed(t, "Riley"), "%s must be admin-gated" % t)
            self.assertTrue(tools._allowed(t, "Alex"), "%s must allow admin" % t)

    def test_code_tools_registered_in_both_lists(self):
        for lst in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            names = {t["function"]["name"] for t in lst}
            for t in ("code_propose_cleanup", "code_apply_cleanup"):
                self.assertIn(t, names)
        for schema in (tools._CODE_PROPOSE_CLEANUP, tools._CODE_APPLY_CLEANUP):
            self.assertEqual(schema["function"]["parameters"]["required"], ["mirror"])

    def test_apply_registers_a_pending_action_not_an_immediate_write(self):
        summary = {"KEEP-CLEAN": 1, "CUT": 5, "MOVE": 0}
        prev = {"totals": summary, "n_files": 1, "remaining": 0, "notes": [],
                "files": [{"file": "x.py", "summary": summary, "lines_before": 200,
                           "lines_after": 190, "remaining": 0}]}
        done = dict(prev, mirror="orchestrator", archived=["x.py.preclean-test"])

        def _register(_kind, _preview, execute, **_kwargs):
            return {"status": "pending", "confirmed_result": execute()}

        with mock.patch.object(CC, "code_apply", side_effect=[prev, done]) as ca, \
             mock.patch.object(tools.pending_actions, "register", side_effect=_register) as reg:
            out = tools._code_apply_cleanup_tool({"mirror": "orchestrator"}, "Alex")
        self.assertEqual(out["status"], "pending")
        self.assertEqual(out["confirmed_result"]["files"], ["x.py"])
        reg.assert_called_once()
        self.assertEqual(ca.call_count, 2)
        self.assertTrue(ca.call_args_list[0].kwargs.get("dry_run"), "first call must be a dry-run preview")
        self.assertFalse(ca.call_args_list[1].kwargs.get("dry_run"), "confirmation performs the write")

    def test_confirmation_is_anchored_to_the_preview_fingerprint(self):
        summary = {"KEEP-CLEAN": 1, "CUT": 5, "MOVE": 0}
        fp = {"version": "code-apply-fingerprint-v1", "name": "s30-code-orchestrator",
              "work_sha256": "a" * 64, "drafts_sha256": "b" * 64, "sources": {"x.py": "c" * 64}}
        prev = {"totals": summary, "n_files": 1, "remaining": 0, "notes": [],
                "files": [{"file": "x.py", "summary": summary, "lines_before": 200,
                           "lines_after": 190, "remaining": 0}], "fingerprint": fp}
        done = dict(prev, mirror="orchestrator", archived=["x.py.preclean-test"])

        def _register(_kind, _preview, execute, **_kwargs):
            return {"status": "pending", "confirmed_result": execute()}

        with mock.patch.object(CC, "code_apply", side_effect=[prev, done]) as ca, \
             mock.patch.object(tools.pending_actions, "register", side_effect=_register) as reg:
            out = tools._code_apply_cleanup_tool({"mirror": "orchestrator"}, "Alex")
        self.assertEqual(out["status"], "pending")
        self.assertEqual(ca.call_args_list[1].kwargs.get("expect_fingerprint"), fp,
                        "the apply must be anchored to the bytes the preview used")
        self.assertEqual(reg.call_args.kwargs["metadata"]["fingerprint"], fp,
                        "the fingerprint is recorded on the pending card")

    def test_confirmation_reports_a_refusal_instead_of_writing(self):
        summary = {"KEEP-CLEAN": 1, "CUT": 5, "MOVE": 0}
        prev = {"totals": summary, "n_files": 1, "remaining": 0, "notes": [], "fingerprint": {"x": 1},
                "files": [{"file": "x.py", "summary": summary, "lines_before": 200,
                           "lines_after": 190, "remaining": 0}]}
        captured = {}

        def _register(_kind, _preview, execute, **_kwargs):
            try:
                captured["result"] = execute()
            except ValueError as exc:      # pending_actions.confirm turns this into status:error
                captured["error"] = str(exc)
            return {"status": "pending"}

        with mock.patch.object(CC, "code_apply", side_effect=[prev, ValueError(
                "refusing to apply: drafts file changed after the preview")]), \
             mock.patch.object(tools.pending_actions, "register", side_effect=_register):
            tools._code_apply_cleanup_tool({"mirror": "orchestrator"}, "Alex")
        self.assertNotIn("result", captured, "the confirmation wrote nothing")
        self.assertIn("drafts file changed after the preview", captured["error"])

    def test_apply_refuses_on_bad_plan(self):
        with mock.patch.object(CC, "code_apply", side_effect=ValueError("no drafts yet")), \
             mock.patch.object(tools.pending_actions, "register") as reg:
            out = tools._code_apply_cleanup_tool({"mirror": "orchestrator", "file": "x.py"}, "Alex")
        self.assertIn("no drafts yet", out)
        reg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
