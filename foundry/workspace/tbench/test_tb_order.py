"""tb_order.price: rewards from Harbor's result.json, model-call facts from the window rows, limits counted.

The share root and the head's mount of it are settings; this file states them explicitly with
documentation-range addresses rather than relying on any lab value."""
import json
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("SSD_SHARED_UNC", r"\\10.0.0.204\SSDShared")
os.environ.setdefault("SSD_SHARE_MOUNT", "/mnt/ssdshared")

import tb_order as O

SHARE = r"\\10.0.0.204\SSDShared"


class Price(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.run = os.path.join(self.d, "run")
        self.items = os.path.join(self.d, "items")
        os.makedirs(os.path.join(self.run, "jobs", "2026-10-11__01-00-00"))
        os.makedirs(self.items)
        res = {"stats": {"evals": {"terminus-2__lab-window__terminal-bench-2": {
            "reward_stats": {"reward": {"1.0": ["fix-git__abc"], "0.0": ["prove-plus-comm__def"]}},
            "exception_stats": {"AgentTimeoutError": ["overfull-hbox__ghi"]}}}}}
        json.dump(res, open(os.path.join(self.run, "jobs", "2026-10-11__01-00-00", "result.json"), "w"))
        for i, (reason, prompt, j) in enumerate([("stop", 1000, 10.0), ("loop", 16000, 12.0), ("stop", 17000, 9.0)]):
            json.dump({"done_reason": reason, "prompt_eval_count": prompt, "wall_s": 2.0, "gpu_j": j},
                      open(os.path.join(self.items, "r%05d.out.json" % i), "w"))
        with open(os.path.join(self.run, "relay.jsonl"), "w") as f:
            f.write(json.dumps({"id": "r9", "error": "ended_no_progress"}) + "\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_rewards_exceptions_and_limits_are_all_counted(self):
        b = O.price(self.run, self.items, 17408)
        self.assertEqual(b["tasks"], {"fix-git": {"reward": 1.0}, "prove-plus-comm": {"reward": 0.0},
                                      "overfull-hbox": {"exception": "AgentTimeoutError"}})
        self.assertEqual((b["solved"], b["graded"], b["exceptions"]), (1, 2, 1))
        self.assertEqual(b["model_calls"], 3)
        self.assertEqual(b["calls_at_ctx"], 1, "17,000 of 17,408 is at the ceiling (>= 16,538); 16,000 is under")
        self.assertEqual(b["done_reasons"], {"stop": 2, "loop": 1})
        self.assertEqual(b["no_progress_ends"], 1)
        self.assertEqual(b["gpu_j"], 31)

    def test_an_amd_window_is_priced_from_its_delivered_rows_by_id(self):
        # Mothership meters at close: the live rows carry gpu_j None, the window's delivered out.json carries joules
        for i in range(3):
            p = os.path.join(self.items, "r%05d.out.json" % i)
            row = json.load(open(p))
            json.dump(dict(row, id="r%05d" % i, gpu_j=None), open(p, "w"))
        wout = os.path.join(self.d, "window_out.json")
        self.assertIsNone(O.price(self.run, self.items, 17408, wout)["gpu_j"], "not delivered yet: None, never 0")
        json.dump({"rows": [{"id": "r%05d" % i, "gpu_j": 100.0 * (i + 1)} for i in range(3)]}, open(wout, "w"))
        b = O.price(self.run, self.items, 17408, wout)
        self.assertEqual((b["gpu_j"], b["gpu_j_rows"]), (600, 3))

    def test_the_sundae_command_carries_the_leads_tested_context_before_the_tasks(self):
        cmd = O.run_cmd(r"\\10.0.0.204\SSDShared\tabfoundry-jobs\campaigns\tbench\r",
                        r"\\10.0.0.204\SSDShared\mothership-build\workspace\a1\windows\w",
                        {"port": 8765, "tasks": ["fix-git", "prove-plus-comm"]}, {"helper_ctx": 14336})
        self.assertTrue(cmd.endswith("8765 14336 fix-git prove-plus-comm"), cmd)
        self.assertIn("'/mnt/ssdshared/tabfoundry-jobs/campaigns/tbench/r/tb_run.sh'", cmd)

    def test_an_errand_arm_passes_the_helpers_window_port_and_ctx(self):
        plan = {"port": 8765, "tasks": ["fix-git"], "helper": {"helper_ctx": 14336}}
        cmd = O.run_cmd(r"\\10.0.0.204\SSDShared\tabfoundry-jobs\campaigns\tbench\r", r"\\x\w",
                        plan, {"helper_ctx": 122880}, r"\\10.0.0.204\SSDShared\mothership-build\workspace\a1\windows\h")
        self.assertTrue(cmd.startswith("HELPER_ITEMS='/mnt/ssdshared/mothership-build/workspace/a1/windows/h' "
                                       "HELPER_PORT=8766 HELPER_CTX=14336 sh "), cmd)
        self.assertTrue(cmd.endswith("8765 122880 fix-git"), cmd)
        self.assertNotIn("ERRAND_MODE", cmd, "the look-only arm (A4) runs unchanged")

    def test_a_commands_arm_sets_the_errand_mode_and_only_then(self):
        """B8: one variable - the errand arm with RUN + FETCH differs from the look-only arm by ERRAND_MODE alone."""
        plan = {"port": 8765, "tasks": ["fix-git"], "helper": {"helper_ctx": 14336}, "errand_mode": "commands"}
        cmd = O.run_cmd(r"\\10.0.0.204\SSDShared\tabfoundry-jobs\campaigns\tbench\r", r"\\x\w",
                        plan, {"helper_ctx": 122880}, r"\\10.0.0.204\SSDShared\mothership-build\workspace\a1\windows\h")
        self.assertIn("HELPER_CTX=14336 ERRAND_MODE=commands sh ", cmd)
        solo = O.run_cmd(r"\\x\r", r"\\x\w", dict(plan, helper=None), {"helper_ctx": 122880})
        self.assertNotIn("ERRAND_MODE", solo, "no helper window, no errands, no mode")

    def test_a_trial_that_fetched_its_own_key_is_void_never_solved_or_graded(self):
        json.dump({"fix-git__abc": {"task": "fix-git", "fetches": 2, "leak": True, "urls": ["https://x/y"]},
                   "prove-plus-comm__def": {"task": "prove-plus-comm", "fetches": 1, "leak": False,
                                            "error": "no distinctive key line - cannot be audited (not clean)"}},
                  open(os.path.join(self.run, "fetch_audit.json"), "w"))
        b = O.price(self.run, self.items, 17408)
        self.assertEqual((b["solved"], b["graded"], b["void"]), (0, 0, 2))
        self.assertIn("fetched its own key", b["tasks"]["fix-git"]["void"])
        self.assertIn("cannot be audited", b["tasks"]["prove-plus-comm"]["void"])

    def test_no_progress_ends_count_trials_not_log_lines(self):
        # the 10-10 -2336 arm's log shape: an end, then its confirmation turn, logged again (5 lines = 3 trials)
        end = {"error": "ended_no_progress", "why": "repeat"}
        lines = [{"error": None, "answer_from_thinking": True}, end, end, {"error": None}, end, end, {"error": None}, end]
        with open(os.path.join(self.run, "relay.jsonl"), "w") as f:
            for i, r in enumerate(lines):
                f.write(json.dumps(dict(r, id="r%05d" % i)) + "\n")
        b = O.price(self.run, self.items, 17408)
        self.assertEqual((b["no_progress_ends"], b["answers_from_thinking"]), (3, 1))

    def test_an_unmetered_row_makes_the_total_none_never_a_partial_sum(self):
        json.dump({"done_reason": "stop", "gpu_j": None}, open(os.path.join(self.items, "r00009.out.json"), "w"))
        b = O.price(self.run, self.items, 17408)
        self.assertIsNone(b["gpu_j"])
        self.assertEqual(b["gpu_j_rows"], 3)

    def test_no_result_json_is_an_error_not_an_empty_run(self):
        shutil.rmtree(os.path.join(self.run, "jobs"))
        with self.assertRaises(AssertionError):
            O.price(self.run, self.items, 17408)

    def test_sundae_sees_the_share_under_mnt(self):
        self.assertEqual(O.sundae_path(r"\\10.0.0.204\SSDShared\tabfoundry-jobs\campaigns\tbench\x"),
                         "/mnt/ssdshared/tabfoundry-jobs/campaigns/tbench/x")


if __name__ == "__main__":
    unittest.main()
