"""menu_refresh: the lab regenerates the placement table when the menu's inputs change (#34 Phase 7, self-sustaining)."""
import os
import tempfile
import types
import unittest
from unittest import mock

import menu_refresh as R


class Refresh(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.camp = os.path.join(self.d, "campaigns")
        os.makedirs(os.path.join(self.camp, "order1", "out.progress"))
        self.out = os.path.join(self.camp, "order1", "out.json")
        open(self.out, "w").write("{}")
        self.calls = []
        self.p = [mock.patch.object(R, "TREES", (self.camp,)), mock.patch.object(R, "FILES", ()),
                  mock.patch.object(R, "GLOBS", ()), mock.patch.object(R, "STATE", os.path.join(self.d, "state.json")),
                  mock.patch.object(R, "LOG", os.path.join(self.d, "log.txt")),
                  # the derived matrix: temp paths, so these tests never read the live policy.json / model-matrix.csv.
                  # No policy.json here = nothing to derive = the matrix step stays out of the chain assertions.
                  mock.patch.object(R, "POLICY", os.path.join(self.d, "policy.json")),
                  mock.patch.object(R, "MATRIX_OUT", os.path.join(self.d, "model-matrix.csv")),
                  mock.patch("sys.stdout", new_callable=__import__("io").StringIO)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def runner(self, fail_at=None):
        def run(argv, **kw):
            step = os.path.basename(argv[1])
            self.calls.append(step)
            return types.SimpleNamespace(returncode=1 if step == fail_at else 0, stdout="boom\n", stderr="")
        return run

    def test_first_run_builds_the_whole_chain_in_order_then_an_unchanged_input_builds_nothing(self):
        self.assertEqual(R.main([], runner=self.runner()), 0)
        self.assertEqual(self.calls, R.step_names())
        self.calls.clear()
        self.assertEqual(R.main([], runner=self.runner()), 0)
        self.assertEqual(self.calls, [], "no change -> no rebuild")

    def test_work_in_flight_does_not_trigger_a_rebuild_but_a_delivery_does(self):
        R.main([], runner=self.runner())
        self.calls.clear()
        open(os.path.join(self.camp, "order1", "out.progress", "a.r1.done.json"), "w").write("{}")
        open(os.path.join(self.camp, "order1", "out.json.partial.jsonl"), "w").write("{}\n")
        R.main([], runner=self.runner())
        self.assertEqual(self.calls, [], "progress markers and partial files are work in flight")
        os.makedirs(os.path.join(self.camp, "order2"))
        open(os.path.join(self.camp, "order2", "out.json"), "w").write("{}")
        R.main([], runner=self.runner())
        self.assertEqual(self.calls, R.step_names())

    def test_a_failed_step_stops_the_chain_and_the_next_run_retries(self):
        self.assertEqual(R.main([], runner=self.runner(fail_at="menu_standings.py")), 1)
        self.assertEqual(self.calls, ["hard_bench_fleet_tv.py", "reduce_chat1.py", "build_menu.py", "menu_standings.py"],
                         "policy is never built on a failed step")
        self.assertIn("FAILED at menu_standings.py", open(R.LOG, encoding="utf-8").read())
        self.calls.clear()
        R.main([], runner=self.runner())
        self.assertEqual(self.calls, R.step_names(), "the fingerprint did not advance, so it retries")

    def _policy(self, age=0):
        open(R.POLICY, "w").write("{}")
        if age:
            t = os.path.getmtime(R.POLICY) - age
            os.utime(R.POLICY, (t, t))

    def test_the_matrix_is_built_after_a_refresh_that_wrote_a_new_policy(self):
        self._policy()
        self.assertEqual(R.main([], runner=self.runner()), 0)
        self.assertEqual(self.calls, R.step_names() + ["menu_matrix.py"])

    def test_an_unchanged_menu_with_a_current_matrix_builds_nothing(self):
        self._policy(age=60)
        R.main([], runner=self.runner())
        open(R.MATRIX_OUT, "w").write("x")                      # newer than the policy and the code: current
        t = max(os.path.getmtime(R.POLICY), os.path.getmtime(os.path.join(R.HERE, "menu_matrix.py"))) + 5
        os.utime(R.MATRIX_OUT, (t, t))
        self.calls.clear()
        self.assertEqual(R.main([], runner=self.runner()), 0)
        self.assertEqual(self.calls, [])

    def test_a_missing_or_old_matrix_is_rebuilt_even_when_the_menu_did_not_change(self):
        self._policy(age=60)
        R.main([], runner=self.runner())
        self.calls.clear()
        R.main([], runner=self.runner())                         # no matrix output yet
        self.assertEqual(self.calls, ["menu_matrix.py"])

    def test_a_matrix_failure_is_loud_exits_1_and_never_re_runs_the_chain(self):
        self._policy()
        self.assertEqual(R.main([], runner=self.runner(fail_at="menu_matrix.py")), 1)
        self.assertIn("FAILED at menu_matrix.py", open(R.LOG, encoding="utf-8").read())
        self.calls.clear()
        R.main([], runner=self.runner())
        self.assertEqual(self.calls, ["menu_matrix.py"], "the chain's fingerprint advanced; only the matrix retries")

    def test_no_policy_yet_means_nothing_to_derive(self):
        self.assertFalse(R.matrix_stale())

    def test_zero_inputs_is_a_broken_instrument_not_a_clean_menu(self):
        with mock.patch.object(R, "TREES", (os.path.join(self.d, "missing"),)):
            with self.assertRaises(AssertionError):
                R.fingerprint()


if __name__ == "__main__":
    unittest.main()
