"""fleet_engine: the engine half of fleet_deploy - the pure parts that decide WHERE a lab tree goes and WHICH launcher line
moves (the remote halves are proven live: install hashes every file, activate verifies the serving process).

The fleet table is a setting, so this file states it explicitly with documentation-range addresses
instead of relying on the lab's lab.env."""
import json
import os
import sys
import unittest

os.environ["FLEET_HOSTS"] = json.dumps({"mothership": "10.0.0.76", "bighonker": "10.0.0.167",
                                        "softserve": "10.0.0.204"})
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fleet_engine as E  # noqa: E402

BH = ("$ollama = 'C:\\Users\\example\\AppData\\Local\\Programs\\Ollama-0.40.5\\ollama.exe'\r\n"
      "# ROLLBACK: 'C:\\Users\\example\\AppData\\Local\\Programs\\Ollama-0.40.5\\ollama.exe' was the old tree\r\n"
      "& cmd.exe /d /c \"$ollama serve >> $log 2>&1\"\r\n")
MO = ("# 2026-10-15: ollama 0.40.5 parallel tree. ROLLBACK = ...\n"
      "Start-Process -FilePath 'C:\\Users\\someone\\AppData\\Local\\AMD\\AI_Bundle\\Ollama-0.40.5\\ollama.exe' -ArgumentList "
      "'serve' -RedirectStandardError 'C:\\Users\\someone\\AppData\\Local\\Ollama\\serve.log'\n")


class LabTree(unittest.TestCase):
    def test_beside_the_stock_tree_and_never_nested(self):
        stock = r"C:\Users\example\AppData\Local\Programs\Ollama-0.40.5"
        self.assertEqual(E.lab_tree(stock, "620d222"), stock + "-lab620d222")
        self.assertEqual(E.lab_tree(stock + "-lab620d222", "abc1234"), stock + "-lababc1234",
                         "a lab tree's next tag still sits beside the STOCK tree")


class LabRevision(unittest.TestCase):
    """Lab revisions are tagged on the same tree; the tag distinguishes them from stock."""

    def order(self, source):
        import json
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "engine.json"), "w", encoding="utf-8") as f:
            json.dump({"source": source}, f)
        return d

    def test_a_revision_source_tree_names_its_own_tag(self):
        self.assertEqual(E.tag_of(self.order("\\\\x\\lab-engine\\llama.cpp-620d222")), "620d222")
        self.assertEqual(E.tag_of(self.order("\\\\x\\lab-engine\\llama.cpp-620d222-r2")), "620d222-r2")
        with self.assertRaises(SystemExit):
            E.tag_of(self.order("\\\\x\\lab-engine\\llama.cpp-latest"))

    def test_a_revision_tree_sits_beside_the_stock_tree_and_reads_back_as_its_tag(self):
        import re
        stock = r"C:\Users\example\AppData\Local\Programs\Ollama-0.40.5"
        tree = E.lab_tree(stock + "-lab620d222", "620d222-r2")
        self.assertEqual(tree, stock + "-lab620d222-r2")
        self.assertEqual(re.search(r"-lab(%s)$" % E.TAG_RE, tree).group(1), "620d222-r2")
        self.assertEqual(re.search(r"-lab(%s)$" % E.TAG_RE, stock + "-lab620d222").group(1), "620d222")
        self.assertEqual(E.lab_tree(tree, "abc1234"), stock + "-lababc1234", "the next tag is still beside the STOCK tree")

    def test_rigbys_build_dir_reads_back_as_the_same_tag(self):
        import re
        out = "TAG /home/alex/lab-engine/llama.cpp-620d222-r2/build-cuda-114/bin\n"
        self.assertEqual(re.findall(r"^TAG .*/llama\.cpp-(%s)/" % E.TAG_RE, out, re.M), ["620d222-r2"])

    def test_a_hand_typed_tag_may_only_add_a_revision_to_the_builds_own_commit(self):
        d = self.order("\\\\x\\lab-engine\\llama.cpp-620d222")
        self.assertEqual(E.revision_tag(d, "620d222-r2"), "620d222-r2")
        self.assertEqual(E.revision_tag(d, "620d222"), "620d222")
        for bad in ("abc1234", "620d222-r2x", "620d2229", "620d222-2", ""):
            with self.assertRaises(SystemExit, msg=bad):
                E.revision_tag(d, bad)

    def test_a_revision_on_one_box_only_is_drift(self):
        rows = {h: {"engine": {"lab": True, "tag": t}} for h, t in
                (("bighonker", "620d222-r2+compat"), ("softserve", "620d222+compat"))}
        self.assertEqual(E.drift(rows), 1)


class Repoint(unittest.TestCase):
    def test_only_the_active_line_moves_comments_stay_history(self):
        old = r"C:\Users\example\AppData\Local\Programs\Ollama-0.40.5"
        new, n = E.repoint(BH, old, old + "-lab620d222")
        self.assertEqual(n, 1)
        self.assertIn("$ollama = '" + old + "-lab620d222\\ollama.exe'", new)
        self.assertIn("# ROLLBACK: '" + old + "\\ollama.exe' was the old tree", new)
        self.assertIn("\r\n", new, "line endings are kept")

    def test_mothership_start_process_line_and_its_log_path_untouched(self):
        old = r"C:\Users\someone\AppData\Local\AMD\AI_Bundle\Ollama-0.40.5"
        new, n = E.repoint(MO, old, old + "-lab620d222")
        self.assertEqual(n, 1)
        self.assertIn("-lab620d222\\ollama.exe'", new)
        self.assertIn("'C:\\Users\\someone\\AppData\\Local\\Ollama\\serve.log'", new)

    def test_a_tree_named_twice_on_active_lines_is_not_one_line(self):
        old = r"C:\x\Ollama-0.40.5"
        _new, n = E.repoint("$a = '" + old + "\\ollama.exe'\n$b = '" + old + "\\lib'\n", old, old + "-lab1")
        self.assertEqual(n, 2, "the caller refuses anything but exactly one active line")


class Markers(unittest.TestCase):
    def test_no_marker_occurs_in_stock_llama_cpp(self):
        # stock llama.cpp output contains `pending_token_idx`; markers must avoid this to prevent false positives
        for m in E.MARKERS:
            self.assertNotIn(m, "pending_token_idx pending_token sampled")
            self.assertTrue(m.startswith("lab_"), m)


class FleetParity(unittest.TestCase):
    def test_every_gpu_factory_has_a_set_and_a_launcher(self):
        import fleet_deploy
        hosts = {b["host"] for b in fleet_deploy.FLEET}
        self.assertEqual(set(E.SETS), hosts)
        self.assertEqual(set(E.LAUNCHERS), hosts)
        self.assertEqual(E.SETS["mothership"], "amd")

    def test_an_engine_deploy_reaches_rigby_the_fourth_factory(self):
        from unittest import mock
        with mock.patch.object(E, "rigby_swap", return_value={"result": "OK - swapped"}) as sw:
            rows, bad = E.deploy("unused", hosts=["rigby"], tag="620d222")
        sw.assert_called_once_with("620d222", False)
        self.assertEqual((list(rows), bad), (["rigby"], 0))


class Drift(unittest.TestCase):
    """Markers alone read rigby's pre-parity e467e5b build as in sync: the version tag decides."""

    def rows(self, **tags):
        return {h: {"engine": {"lab": True, "tag": t}} for h, t in tags.items()}

    def test_one_lab_version_everywhere_is_parity(self):
        self.assertEqual(E.drift(self.rows(bighonker="620d222", mothership="620d222", rigby="620d222")), 0)

    def test_every_box_patched_but_one_on_another_version_is_drift(self):
        self.assertEqual(E.drift(self.rows(bighonker="620d222", rigby="e467e5b")), 1)

    def test_a_tag_the_check_could_not_read_is_drift_not_parity(self):
        self.assertEqual(E.drift(self.rows(bighonker="unknown", rigby="unknown")), 1)


class LanGate(unittest.TestCase):
    """Firewall is checked before the LAN switch (held on failure); LAN is checked after (rolled back)."""
    TREE = r"C:\x\Ollama-0.40.5-lab620d222"

    def deploy(self, host, fw, lan, **kw):
        from unittest import mock
        calls = []
        self.lan_asks = []

        def remote(h, c):
            calls.append(c["action"])
            if c["action"] == "install":
                return {"result": "INSTALLED", "lab_tree": self.TREE}
            return {"result": "OK - serving", "serving": [self.TREE + r"\ollama.exe"]}

        def lan_ok(h, tries=6, wait_s=5):
            self.lan_asks.append(tries)
            return lan
        with mock.patch.object(E, "stage_set", return_value="t.tar"), mock.patch.object(E.subprocess, "run"), \
                mock.patch.object(E, "_remote", side_effect=remote), \
                mock.patch.object(E, "firewall_verdict", return_value=fw), mock.patch.object(E, "lan_ok", lan_ok):
            rows, bad = E.deploy("order", hosts=[host], tag="620d222", **kw)
        return rows[host]["result"], bad, calls

    def test_with_the_operator_at_the_console_a_box_with_no_rule_is_switched_and_waited_on(self):
        res, bad, calls = self.deploy("mothership", "none", True, operator_present=True)
        self.assertEqual((res, bad, calls), ("OK - serving", 0, ["install", "activate"]))
        self.assertEqual(self.lan_asks, [E.OPERATOR_WAIT_TRIES], "the LAN check waits for his click, not 30 seconds")

    def test_a_click_that_never_comes_rolls_the_box_back(self):
        res, bad, calls = self.deploy("mothership", "none", False, operator_present=True)
        self.assertEqual((calls, bad), (["install", "activate", "rollback"], 1))
        self.assertTrue(res.startswith("FAILED"))

    def test_a_block_rule_is_held_even_with_the_operator_present(self):
        res, bad, calls = self.deploy("mothership", "block", True, operator_present=True)
        self.assertEqual((calls, bad), (["install"], 1), "Windows shows no second prompt for a Block rule")
        self.assertTrue(res.startswith("HELD"))

    def test_the_flag_changes_nothing_for_a_box_that_was_not_held(self):
        self.assertEqual(self.deploy("bighonker", "none", True, operator_present=True),
                         ("OK - serving", 0, ["install", "activate"]))
        self.assertEqual(self.lan_asks, [6])

    def test_a_block_rule_holds_the_switch_before_anything_restarts(self):
        res, bad, calls = self.deploy("bighonker", "block", True)
        self.assertEqual((calls, bad), (["install"], 1))
        self.assertTrue(res.startswith("HELD"))
        self.assertIn('set rule name=ollama.exe dir=in program="%s\\ollama.exe" new action=allow' % self.TREE, res)

    def test_no_allow_rule_holds_a_console_launch_box_but_not_a_system_launch_box(self):
        res, bad, calls = self.deploy("mothership", "none", True)
        self.assertEqual((calls, bad), (["install"], 1))
        self.assertIn("add rule name=ollama.exe dir=in action=allow", res)
        res, bad, calls = self.deploy("bighonker", "none", True)
        self.assertEqual((res, bad, calls), ("OK - serving", 0, ["install", "activate"]))

    def test_on_box_ok_but_lan_unreachable_rolls_back_and_prints_the_firewall_fix(self):
        res, bad, calls = self.deploy("mothership", "allow", False)
        self.assertEqual((calls, bad), (["install", "activate", "rollback"], 1))
        self.assertIn(r"lab620d222\ollama.exe", res)

    def test_allowed_and_reachable_stays_deployed(self):
        self.assertEqual(self.deploy("mothership", "allow", True), ("OK - serving", 0, ["install", "activate"]))

    def test_a_paused_lane_gets_its_runner_task_started_again(self):
        from unittest import mock
        ssh = []

        def run(argv, **kw):
            ssh.append(argv)
            return mock.Mock(stdout="SUCCESS", stderr="")
        sdr = mock.Mock(return_value={"result": "OK - serving", "steps": []})
        with mock.patch.object(E, "stage_set", return_value="t.tar"), mock.patch.object(E.subprocess, "run", run), \
                mock.patch.object(E, "_remote", side_effect=lambda h, c: {"result": "INSTALLED",
                                                                          "lab_tree": self.TREE}), \
                mock.patch.object(E, "firewall_verdict", return_value="allow"), \
                mock.patch.object(E, "lan_ok", return_value=True):
            rows, bad = E.deploy("order", hosts=["softserve"], tag="620d222", stop_deploy_requeue=sdr,
                                 deck_order=lambda h: "fitprobe-softserve.x", run_on=lambda b, c: {})
        self.assertEqual(bad, 0)
        self.assertIn(["ssh", "softserve", 'schtasks /run /tn "SS Foundry Runner"'], ssh)
        self.assertIn("runner task SS Foundry Runner: SUCCESS", rows["softserve"]["steps"])

    def test_every_gpu_factory_has_a_lan_address(self):
        self.assertEqual(set(E.LAN), set(E.SETS))


@unittest.skipUnless(__import__("shutil").which("bash"), "needs bash")
class RigbySwap(unittest.TestCase):
    """RIGBY_SWAP run for real on temp dirs (no CUDA process here, so the lane reads free)."""

    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp()
        self.live, self.new = os.path.join(self.d, "live"), os.path.join(self.d, "new")
        for p, body in ((self.live, b"stock engine"), (self.new, b"core lab_resume core")):
            os.makedirs(p)
            with open(os.path.join(p, "libllama-server-impl.so"), "wb") as f:
                f.write(body)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def swap(self, mode, new=None):
        """Through rigby_swap's own subprocess call (the seam that bit), with ssh swapped for a local bash."""
        import subprocess
        from unittest import mock
        real = subprocess.run
        p = lambda s: s.replace("\\", "/")
        arch = p(os.path.join(self.d, "arch-engine-"))

        def local(argv, **kw):
            self.sent = kw["input"]
            return real(["bash", "-s", "--", p(self.live), p(new or self.new), arch, mode, "|".join(E.MARKERS)], **kw)
        with mock.patch.object(E, "RIGBY_ARCHIVE", arch), mock.patch.object(E.subprocess, "run", local):
            res = E.rigby_swap("x", rollback=(mode == "rollback"))["result"]
        self.assertNotIn(b"\r", self.sent)
        return res

    def live_bytes(self):
        with open(os.path.join(self.live, "libllama-server-impl.so"), "rb") as f:
            return f.read()

    def test_install_archives_the_live_bin_then_copies_and_rollback_restores_it(self):
        self.assertTrue(self.swap("install").startswith("OK"))
        self.assertEqual(self.live_bytes(), b"core lab_resume core")
        self.assertEqual(len([n for n in os.listdir(self.d) if n.startswith("arch-")]), 1)
        self.assertTrue(self.swap("rollback").startswith("OK"))
        self.assertEqual(self.live_bytes(), b"stock engine")

    def test_rollback_takes_the_newest_engine_archive_not_a_lexically_later_patch_archive(self):
        self.swap("install")
        os.makedirs(os.path.join(self.d, "arch-llama.cpp-latest-zzz"))   # build_slot_resume.sh's own, beside
        self.assertTrue(self.swap("rollback").startswith("OK"))
        self.assertEqual(self.live_bytes(), b"stock engine")

    def test_a_build_without_the_lab_markers_is_refused_and_nothing_moves(self):
        with open(os.path.join(self.new, "libllama-server-impl.so"), "wb") as f:
            f.write(b"stock pending_token_idx")
        self.assertTrue(self.swap("install").startswith("FAILED"))
        self.assertEqual(self.live_bytes(), b"stock engine")
        self.assertEqual([n for n in os.listdir(self.d) if n.startswith("arch-")], [])


if __name__ == "__main__":
    unittest.main()
