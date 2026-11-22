"""strata_serve.py's two refusals: a plan not installed (or a shard changed since), and a factory order holding the card."""
import json, os, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strata_serve as SS  # noqa: E402


class Refusals(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.gguf = os.path.join(self.d, "gguf")
        os.makedirs(self.gguf)
        open(os.path.join(self.gguf, "a.gguf"), "w").close()
        self.plan = {"gguf_dir": self.gguf, "data_dir": os.path.join(self.d, "data"), "model": "Q2_0",
                     "shards": {"a.gguf": "x"}}
        self.plan_path = os.path.join(self.d, "plan.json")
        os.makedirs(os.path.join(self.plan["data_dir"], "serve"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _install_stamp(self, plan_path=None):
        with open(SS._stamp_path(self.plan), "w", encoding="utf-8") as f:
            json.dump({"plan": plan_path or self.plan_path, "shards": SS._shards(self.plan)}, f)

    def test_never_installed_refuses(self):
        self.assertIn("not installed", SS.check_installed(self.plan, self.plan_path))

    def test_installed_and_unchanged_passes(self):
        self._install_stamp()
        self.assertIsNone(SS.check_installed(self.plan, self.plan_path))

    def test_a_changed_shard_refuses(self):
        self._install_stamp()
        with open(os.path.join(self.gguf, "a.gguf"), "w") as f:
            f.write("changed")
        self.assertIn("shard changed", SS.check_installed(self.plan, self.plan_path))

    def test_installed_for_another_plan_refuses(self):
        self._install_stamp(os.path.join(self.d, "other.json"))
        self.assertIn("another plan", SS.check_installed(self.plan, self.plan_path))

    def _run_state(self, body):
        p = os.path.join(self.d, "CURRENT_RUN.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_no_claim_is_free(self):
        self.assertIsNone(SS.factory_holds_card(os.path.join(self.d, "missing.json")))

    def test_a_compute_order_holds_the_card(self):
        p = self._run_state(json.dumps({"name": "menu-cell", "state": "compute"}))
        self.assertIn("menu-cell", SS.factory_holds_card(p))

    def test_released_states_are_free(self):
        for st in SS.RELEASING_STATES:
            self.assertIsNone(SS.factory_holds_card(self._run_state(json.dumps({"name": "x", "state": st}))), st)

    def test_unreadable_is_held(self):
        self.assertIn("unreadable", SS.factory_holds_card(self._run_state("{not json")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
