"""engine_pipeline: the detect decision; each phase against fakes; HOLD on a patch that does not apply; the state machine."""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock
import io

os.environ["LAB_ARCHIVE_DIR"] = tempfile.mkdtemp(prefix="lab-archive-test-")
os.environ["LAB_UPDATES_STATE"] = tempfile.mkdtemp(prefix="lab-updates-state-test-")   # the live state/log is never a test's
os.environ.setdefault("HAI_URL", "http://10.0.0.5:8099")   # a linked head (documentation range); read at import
os.environ["ENGINE_BUILD_BOX"] = "mothership"                      # the fixtures' fleet: build there,
os.environ["ENGINE_ROLLOUT"] = "bighonker,softserve,mothership"    # canary first, then the rest in order
import engine_pipeline as P  # noqa: E402

NOW = datetime.datetime(2026, 11, 4, 20, 0, tzinfo=datetime.timezone.utc)
REL14 = {"tag": "v0.40.14", "published_at": "2026-11-03T01:40:40Z"}          # the real one
SHA = "39febd2adb94e0ea6ebd7b25a64a29c7f2d1e1f3"


class Detect(unittest.TestCase):
    def test_current_fresh_and_due(self):
        self.assertTrue(P.detect_decision(REL14, "b11963", SHA, "39febd2", now=NOW)[1].startswith("OK - the fleet already serves"))
        start, why, t = P.detect_decision(REL14, "b11963", SHA, "620d222", now=NOW)
        self.assertFalse(start)
        self.assertIn("1.8 days old", why)
        later = NOW + datetime.timedelta(days=7)
        start, why, t = P.detect_decision(REL14, "b11963", SHA, "620d222", now=later)
        self.assertTrue(start)
        self.assertEqual(t, {"tag": "v0.40.14", "build": "b11963", "sha": SHA, "short": "39febd2"})
        self.assertTrue(P.detect_decision({}, None, None, "620d222", now=NOW)[1].startswith("WAIT"))


class Fake:
    """Every outside call the phases make, recorded."""
    def __init__(self, patch_rc=0, probe="LOSSLESS", deploy_ok=True, order_status="done"):
        self.calls, self.patch_rc, self.probe, self.deploy_ok, self.order_status = [], patch_rc, probe, deploy_ok, order_status
        self.archived = []

    def gh_json(self, url):
        self.calls.append(("gh_json", url))
        if "releases/latest" in url:
            return {"tag_name": "v0.40.14", "published_at": (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=9)).isoformat()}
        return {"object": {"sha": SHA}}

    def gh_text(self, url):
        return "b11963"

    def live_tag(self):
        return "620d222"

    def download(self, url, dest):
        raise AssertionError("prepare must not download in this test")

    def run(self, argv, timeout=0, **kw):
        self.calls.append(("run", argv))
        a = " ".join(argv)
        if len(argv) > 1 and argv[1].endswith("_patch.py"):   # python <patch> <tree>
            if not os.path.isfile(argv[1]):   # what python itself answers for a script that is not there
                return 2, "python: can't open file %r: [Errno 2] No such file or directory" % argv[1]
            return self.patch_rc, "already patched" if self.patch_rc == 0 else "AssertionError: anchor matched 0 times"
        if "apply_ollama_compat" in a:
            return 0, "call sites: 2"
        if "--engine-rollback" in a:
            return 0, "OK rolled back"
        if "--engine" in a:
            return (0 if self.deploy_ok else 1), "-- host OK" if self.deploy_ok else "REFUSED"
        if "submit_resume_probe" in a:
            host = argv[2]
            return 0, "True resumeprobe-%s-x ['0123456789ab'] None None" % host[:2]
        if "build_cuda114.sh" in a:
            return 0, "BUILD_OK=/x\nVERDICT: script reached its DONE marker"
        return 0, ""

    def desk_post(self, path, body):
        self.calls.append(("post", body["spec"]["name"]))
        return {"ok": True, "order": body["spec"]["name"], "steps": [{"host": "mothership", "id": "deadbeef1234"}]}

    def desk_get(self, path):
        return {"status": self.order_status}

    def archive(self, *a, **kw):
        self.archived.append((a[0], a[1]))
        return {}

    def sleep(self, s):
        pass


class Phases(unittest.TestCase):
    def test_detect_starts_prepare_when_due(self):
        st = {"phase": "detect", "live": {"short": "620d222"}, "steps": []}
        P.advance(st, io=Fake())
        self.assertEqual(st["phase"], "prepare")
        self.assertEqual(st["target"]["short"], "39febd2")

    def test_prepare_holds_on_a_patch_that_does_not_apply(self):
        with tempfile.TemporaryDirectory() as d:
            P.LAB_ENGINE = d
            os.makedirs(os.path.join(d, "llama.cpp-39febd2"))
            os.makedirs(os.path.join(d, "ollama-0.40.14-src", "ollama-0.40.14", "llama", "compat"))
            st = {"phase": "prepare", "live": {"short": "620d222"}, "target": {"tag": "v0.40.14", "build": "b11963", "sha": SHA, "short": "39febd2"}, "steps": []}
            f = Fake(patch_rc=1)
            P.advance(st, io=f)
            self.assertEqual(st["phase"], "hold")
            self.assertIn("does not apply", st["verdict"])
            self.assertIn("slot_pending_token_patch.py", st["verdict"], "the FIRST patch stopped it; nothing after ran")
            self.assertEqual(len([c for c in f.calls if c[0] == "run"]), 1)

    def test_prepare_then_build_submits_the_order_in_the_proven_name_shape(self):
        with tempfile.TemporaryDirectory() as d:
            P.LAB_ENGINE = d
            os.makedirs(os.path.join(d, "llama.cpp-39febd2"))
            os.makedirs(os.path.join(d, "ollama-0.40.14-src", "ollama-0.40.14", "llama", "compat"))
            st = {"phase": "prepare", "live": {"short": "620d222"}, "target": {"tag": "v0.40.14", "build": "b11963", "sha": SHA, "short": "39febd2"}, "steps": []}
            f = Fake()
            P.advance(st, io=f)
            self.assertEqual(st["phase"], "build")
            self.assertEqual([c[1] for c in f.calls if c[0] == "run"].__len__(), 6, "five lab patches (mid-turn continuation, the Vulkan attention fix) + compat")
            self.assertEqual(f.archived, [("lab-engine-tree", "39febd2")])
            P.advance(st, io=f)
            self.assertEqual(st["phase"], "delivered")
            name = [c[1] for c in f.calls if c[0] == "post"][0]
            self.assertTrue(name.startswith("labengine-39febd2-compat."), name)
            self.assertEqual(st["order"]["id"], "deadbeef1234")

    def test_delivered_waits_then_canary_then_fleet_then_verify(self):
        with tempfile.TemporaryDirectory() as d:
            odir = os.path.join(d, "labengine-20261105", "labengine-39febd2-compat.x_mothership")   # the factory's own suffix
            st = {"phase": "delivered", "live": {"short": "620d222"}, "target": {"tag": "v0.40.14", "build": "b11963", "sha": SHA, "short": "39febd2"},
                  "order": {"id": "deadbeef1234", "name": "labengine-39febd2-compat.x",
                            "dir_glob": os.path.join(d, "labengine-20261105", "labengine-39febd2-compat.x*")}, "steps": []}
            f = Fake(order_status="running")
            P.advance(st, io=f)
            self.assertEqual(st["phase"], "delivered", "no engine.json yet -> still waiting")
            self.assertIn("not delivered yet", st["verdict"])
            os.makedirs(odir)
            json.dump({"sets": {"nvidia": {"files": {}}, "amd": {"files": {}}}}, open(os.path.join(odir, "engine.json"), "w"))
            P.advance(st, io=f)
            self.assertEqual(st["phase"], "canary")
            self.assertEqual(st["order"]["dir"], odir, "the delivered dir is found by glob, suffix and all")
            P.CAMPAIGNS = d
            pdir = os.path.join(d, "labengine-20261105", "resumeprobe-bi-x_bighonker")
            os.makedirs(pdir)
            json.dump({"verdict": "CONTINUES LOSSLESS", "lab_continue": {"verdict": "CONTINUES LOSSLESS"}}, open(os.path.join(pdir, "resume.json"), "w"))
            P.advance(st, io=f)
            self.assertEqual(st["phase"], "fleet", st.get("verdict"))
            sdir = os.path.join(d, "labengine-20261105", "resumeprobe-so-x_softserve")
            os.makedirs(sdir)
            json.dump({"verdict": "CONTINUES LOSSLESS", "lab_continue": {"verdict": "CONTINUES LOSSLESS"}}, open(os.path.join(sdir, "resume.json"), "w"))
            P.advance(st, io=f)
            self.assertEqual(st["phase"], "verify", st.get("verdict"))
            self.assertEqual(st["fleet_done"], ["softserve", "mothership", P.TABLET_HEAD])
            runs = [c[1] for c in f.calls if c[0] == "run"]
            copy = [r for r in runs if r[:2] == ["bash", "-c"]]
            self.assertEqual(len(copy), 1, "one copy step to the tablet head")
            self.assertIn("'llama.cpp-39febd2'", copy[0][2], "the patched tree goes over")
            self.assertIn("'%s'" % P.TABLET_HEAD_PATCH, copy[0][2], "and the tablet head's own patch with it")
            self.assertIn("%s/patches" % P.TABLET_HEAD_ENGINE_TREE, copy[0][2], "into the folder the build reads it from")
            build = [r for r in runs if any(str(x).endswith("build_cuda114.sh") for x in r)]
            self.assertEqual(len(build), 1)
            self.assertEqual(build[0][2], P.TABLET_HEAD, "the build runs on the tablet head the settings name")
            self.assertEqual(build[0][build[0].index("--args") + 1], "39febd2:%s" % P.TABLET_HEAD_ENGINE_TREE,
                             "one <short>:<engine folder> argument (run_remote's --args takes no spaces)")

            class F2(Fake):
                def live_tag(self):
                    return "39febd2"
            P.advance(st, io=F2())
            self.assertEqual(st["phase"], "done")
            self.assertEqual(st["live"]["short"], "39febd2")

    def test_a_failed_canary_probe_rolls_back_and_holds(self):
        with tempfile.TemporaryDirectory() as d:
            P.CAMPAIGNS = d
            pdir = os.path.join(d, "labengine-20261105", "resumeprobe-bi-x_bighonker")
            os.makedirs(pdir)
            json.dump({"verdict": "NOT LOSSLESS"}, open(os.path.join(pdir, "resume.json"), "w"))
            st = {"phase": "canary", "live": {"short": "620d222"}, "target": {"tag": "v0.40.14", "build": "b11963", "sha": SHA, "short": "39febd2"},
                  "order": {"id": "x", "name": "n", "dir": d}, "steps": []}
            f = Fake()
            P.advance(st, io=f)
            self.assertEqual(st["phase"], "hold")
            self.assertIn("NOT LOSSLESS", st["verdict"])
            self.assertTrue(any("--engine-rollback" in " ".join(c[1]) for c in f.calls if c[0] == "run"), "rolled back")

    def test_hold_stays_until_reset(self):
        st = {"phase": "hold", "verdict": "HOLD - x", "steps": []}
        P.advance(st, io=Fake())
        self.assertEqual(st["phase"], "hold")

    def test_prepare_holds_when_a_patch_file_is_missing(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as empty, mock.patch.object(P, "PATCHES", empty):
            P.LAB_ENGINE = d
            os.makedirs(os.path.join(d, "llama.cpp-39febd2"))
            os.makedirs(os.path.join(d, "ollama-0.40.14-src", "ollama-0.40.14", "llama", "compat"))
            st = {"phase": "prepare", "live": {"short": "620d222"}, "target": {"tag": "v0.40.14", "build": "b11963", "sha": SHA, "short": "39febd2"}, "steps": []}
            f = Fake()
            P.advance(st, io=f)
            self.assertEqual(st["phase"], "hold")
            self.assertIn("missing", st["verdict"])
            for name in P.PATCH_ORDER:
                self.assertIn(name, st["verdict"])
            self.assertEqual([c for c in f.calls if c[0] == "run"], [], "no patch touched the tree")


class PatchesOnDisk(unittest.TestCase):
    """The patches are FILES in the engine repo, not names in a list: the fakes above answer for any name, so only this
    can fail when one is missing. No patches folder is a failure, not a skip."""
    NEED = P.PATCH_ORDER + (P.TABLET_HEAD_PATCH,)

    def test_every_patch_the_pipeline_applies_is_in_the_patches_folder(self):
        self.assertTrue(P.PATCHES, "no patches folder: set LAB_ENGINE_DIR (or ENGINE_PATCHES_DIR)")
        missing = [n for n in self.NEED if not os.path.isfile(os.path.join(P.PATCHES, n))]
        self.assertEqual(missing, [], "missing from %s" % P.PATCHES)

    def test_each_is_a_patch_script_that_takes_the_tree(self):
        for name in self.NEED:
            with open(os.path.join(P.PATCHES, name), encoding="utf-8") as fh:
                src = fh.read()
            compile(src, name, "exec")
            self.assertIn("sys.argv", src, name)


class WholeTurnVerdicts(unittest.TestCase):
    def test_recorded_27b_discrepancy_is_a_failed_gate(self):
        report={"verdict":"LOSSLESS","lab_continue":{"verdict":"NOT LOSSLESS in: other_seed"}}
        self.assertEqual(P.resume_probe_verdict(report),"NOT LOSSLESS in: other_seed")
    def test_a_raw_only_success_cannot_certify_whole_turn_continuation(self):
        self.assertEqual(P.resume_probe_verdict({"verdict":"LOSSLESS"}),"NO CONTINUATION RESULT")
    def test_complete_continuation_success_and_explicit_failure_keep_their_meaning(self):
        self.assertEqual(P.resume_probe_verdict({"verdict":"CONTINUES LOSSLESS","lab_continue":{"verdict":"CONTINUES LOSSLESS"}}),"LOSSLESS")
        self.assertEqual(P.resume_probe_verdict({"verdict":"NOT LOSSLESS"}),"NOT LOSSLESS")

class ExistingDeskCredential(unittest.TestCase):
    def test_protected_status_read_uses_the_existing_worker_header(self):
        with mock.patch.dict(os.environ,{'P20_WORKER_TOKEN':'synthetic-worker'}), \
             mock.patch.object(P.urllib.request,'urlopen',return_value=io.BytesIO(b'{"status":"done"}')) as request:
            self.assertEqual(P.desk_get('/p20/order/test')['status'],'done')
        self.assertEqual(request.call_args.args[0].get_header('X-p20-token'),'synthetic-worker')
    def test_local_factory_env_reuses_only_the_registered_worker_credential(self):
        with mock.patch.dict(os.environ,{'P20_WORKER_TOKEN':''}), \
             mock.patch('builtins.open',return_value=io.StringIO('OTHER_SECRET=not-forwarded\nP20_WORKER_TOKEN=synthetic-local\n')), \
             mock.patch.object(P.urllib.request,'urlopen',return_value=io.BytesIO(b'{}')) as request:
            P.desk_get('/p20/order/test')
        self.assertEqual(request.call_args.args[0].get_header('X-p20-token'),'synthetic-local')
        self.assertNotIn('not-forwarded',str(request.call_args))
    def test_missing_credential_refuses_before_sending_an_anonymous_request(self):
        with mock.patch.dict(os.environ,{'P20_WORKER_TOKEN':''}),mock.patch('builtins.open',side_effect=OSError()), \
             mock.patch.object(P.urllib.request,'urlopen') as request:
            with self.assertRaisesRegex(RuntimeError,'credential'):P.desk_get('/p20/order/test')
        request.assert_not_called()

class ProbeArtifactIdentity(unittest.TestCase):
    def test_an_unrelated_newer_report_does_not_count_as_the_submitted_order(self):
        with tempfile.TemporaryDirectory() as d,mock.patch.object(P,'CAMPAIGNS',d):
            folder=os.path.join(d,'labengine-20261109','resumeprobe-bi-unrelated_bighonker');os.makedirs(folder)
            with open(os.path.join(folder,'resume.json'),'w') as file:json.dump({'verdict':'LOSSLESS'},file)
            fake=Fake();self.assertEqual(P._probe({},fake,'bighonker'),'NO RESULT FILE')
            argv=next(c[1] for c in fake.calls if c[0]=='run')
            self.assertTrue(all(x in argv for x in ['--continue','--hang-up','--fresh-load']))
    def test_ambiguous_deliveries_fail_instead_of_choosing_the_newest(self):
        with tempfile.TemporaryDirectory() as d,mock.patch.object(P,'CAMPAIGNS',d):
            for suffix in ['','-second']:
                folder=os.path.join(d,'labengine-20261109','resumeprobe-bi-x_bighonker'+suffix);os.makedirs(folder)
                with open(os.path.join(folder,'resume.json'),'w') as file:json.dump({'verdict':'LOSSLESS'},file)
            self.assertEqual(P._probe({},Fake(),'bighonker'),'NO RESULT FILE')

if __name__ == "__main__":
    unittest.main()
