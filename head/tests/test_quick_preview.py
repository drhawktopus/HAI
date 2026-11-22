import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _restore(env0, saved_modules):
    """Restore `sys.modules` entries and env vars to their pre-test state.

    Reloading modules replaces the object in `sys.modules`. If not restored,
    subsequent tests patching via attribute lookup resolve to the old (unpatched)
    object, causing silent mock failures. Restoring module-level attributes or
    env vars alone is insufficient; the module identities (`id(mod)`) must be
    restored to prevent leakage between tests."""
    if env0 is None:
        os.environ.pop("P20_QUEUE_DIR", None)
    else:
        os.environ["P20_QUEUE_DIR"] = env0
    for name, mod in saved_modules.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


class TestQuickPreviewQueue(unittest.TestCase):
    """The queue side: a quick preview is a JOB KIND in the existing store, never a second queue."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env0 = os.environ.get("P20_QUEUE_DIR")
        self._saved = {"p20_queue": sys.modules.get("p20_queue")}
        os.environ["P20_QUEUE_DIR"] = self.tmp
        for m in ("p20_queue",):
            sys.modules.pop(m, None)
        import p20_queue
        self.q = p20_queue
        self.q.QUEUE_DIR = self.tmp

    def tearDown(self):
        _restore(self._env0, self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_it_lands_in_the_SAME_store_as_every_other_render_job(self):
        self.q.enqueue_quick_preview("run_a", 250, "klein", "ck.safetensors", "core", "a prompt")
        jobs = self.q.all_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["kind"], "quick_preview")
        self.assertIn(jobs[0], self.q.pending())

    def test_it_is_marked_APPROXIMATE_in_the_record_itself(self):
        """An approximate preview must be impossible to mistake for a real one. v11's ck1200 was
        elected from 87 previews rendered at the wrong strength; an unlabelled approximation is
        that same mistake with a bigger gap."""
        j = self.q.enqueue_quick_preview("run_a", 250, "klein", "ck.safetensors", "core", "p")
        self.assertIs(j["approximate"], True)
        self.assertEqual(j["tag"], "APPROX")
        self.assertIn("NOT valid evidence", j["caveat"])
        self.assertIn("klein", j["caveat"])

    def test_it_carries_no_consent_fields_and_is_attributed_to_the_foundry(self):
        """`enqueue()` carries user/person/consent because those render a PERSON at someone's
        request. This renders a concept checkpoint the factory just made: no requester, no subject.
        Mixing the two would poison the consent audit trail."""
        j = self.q.enqueue_quick_preview("run_a", 250, "klein", "ck.safetensors", "core", "p")
        self.assertEqual(j["initiator"], "foundry")
        for absent in ("user", "person", "consent_reason", "use"):
            self.assertNotIn(absent, j)

    def test_it_declares_what_it_needs_so_it_cannot_be_built_by_the_wrong_box(self):
        j = self.q.enqueue_quick_preview("run_a", 250, "klein", "ck.safetensors", "core", "p")
        self.assertEqual(j["requires"], ["render.klein"])

    def test_rush_by_default_because_a_stale_preview_is_worthless(self):
        """A preview of step 250 means nothing once the run is at 900. It must not wait for a
        cheap-energy window the way a user's render legitimately can."""
        self.assertTrue(self.q.enqueue_quick_preview("r", 1, "klein", "c", "n", "p")["rush"])


class TestBuildableBy(unittest.TestCase):
    """The routing filter: /p20/jobs?host= must never hand a job to a box that cannot build it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env0 = os.environ.get("P20_QUEUE_DIR")
        self._saved = {"p20_queue": sys.modules.get("p20_queue")}
        os.environ["P20_QUEUE_DIR"] = self.tmp
        sys.modules.pop("p20_queue", None)
        import p20_queue
        self.q = p20_queue
        self.q.QUEUE_DIR = self.tmp

    def tearDown(self):
        _restore(self._env0, self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_job_with_no_requires_is_buildable_by_anyone(self):
        'Ensures backward compatibility: jobs without `requires` (e.g., legacy jobs or consent-gated renders) must be buildable by any worker, including those lacking specific capabilities. Filtering these out would cause existing workers to silently stop receiving work.'
        jobs = [{"id": "a"}, {"id": "b", "requires": []}]
        self.assertEqual(self.q.buildable_by(jobs, []), jobs)
        self.assertEqual(self.q.buildable_by(jobs, ["render.klein"]), jobs)

    def test_a_klein_preview_is_NOT_offered_to_a_box_without_the_klein_base(self):
        """The failure this prevents is not a crash -- it is a render against the WRONG base model,
        which produces a picture that looks fine and means nothing."""
        jobs = [{"id": "k", "requires": ["render.klein"]}]
        self.assertEqual(self.q.buildable_by(jobs, ["render.flux2", "probe"]), [])
        self.assertEqual(self.q.buildable_by(jobs, ["render.klein"]), jobs)

    def test_every_requirement_must_be_met_not_just_one(self):
        jobs = [{"id": "x", "requires": ["render.klein", "probe"]}]
        self.assertEqual(self.q.buildable_by(jobs, ["render.klein"]), [])
        self.assertEqual(self.q.buildable_by(jobs, ["render.klein", "probe"]), jobs)

    def test_a_box_with_no_capabilities_is_offered_nothing_that_states_a_requirement(self):
        """'Cannot tell' is not 'can build anything'. A factory that has published no capabilities
        must not be handed work that declares what it needs."""
        jobs = [{"id": "k", "requires": ["render.klein"]}]
        self.assertEqual(self.q.buildable_by(jobs, []), [])
        self.assertEqual(self.q.buildable_by(jobs, None), [])


class TestPlacementIsAdditive(unittest.TestCase):
    """Ensures placement logic is additive, not destructive.

    Regressions where lowering VRAM thresholds silently remove guards for heavy
    workloads (e.g., 32B models on small cards) are prevented by pinning both
    the old safety gates and the new routing paths."""

    def setUp(self):
        self._saved = {"scheduler": sys.modules.get("scheduler")}
        sys.modules.pop("scheduler", None)
        import scheduler
        self.s = scheduler

    def tearDown(self):
        _restore(os.environ.get("P20_QUEUE_DIR"), self._saved)

    def test_the_32B_render_gate_is_still_20GB(self):
        self.assertEqual(self.s.KINDS["render"]["vram_gb"], 20.0,
                         "lowering this lets a 32B render be placed on a card that cannot hold it")

    def test_a_small_render_kind_exists_beside_it(self):
        self.assertIn("render_small", self.s.KINDS)
        self.assertLess(self.s.KINDS["render_small"]["vram_gb"],
                        self.s.KINDS["render"]["vram_gb"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
