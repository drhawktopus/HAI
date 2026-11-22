"""HAI Flux.2 renders must never fall through to a Klein-only factory."""
import unittest
from unittest import mock
import p20_queue as Q


class RenderCapabilities(unittest.TestCase):
    def setUp(self):
        self.job = {"id": "test", "user": "Alex", "prompt": "a frog", "status": "pending"}

    def test_new_jobs_declare_the_actual_render_model(self):
        with mock.patch.object(Q, "_save"), mock.patch.object(Q, "_announce"):
            job = Q.enqueue("Alex", "", "a frog", None, None, use="generic", consent_reason="generic")
        self.assertEqual(job["requires"], ["render.flux2"])

    def test_legacy_generic_jobs_are_not_buildable_on_klein_only(self):
        self.assertEqual(Q.buildable_by([self.job], {"render.klein"}), [])

    def test_legacy_generic_jobs_remain_buildable_on_flux2(self):
        self.assertEqual(Q.buildable_by([self.job], {"render.flux2"}), [self.job])

    def test_identity_renders_also_require_flux2(self):
        self.job.update(kind="render", lora_path="consented.safetensors")
        self.assertEqual(Q.buildable_by([self.job], {"render.klein"}), [])

    def test_other_order_and_preview_capabilities_keep_their_own_contract(self):
        jobs = [{"kind": "factory_order", "spec": {}},
                {"kind": "quick_preview", "requires": ["render.klein"]}]
        self.assertEqual(Q.buildable_by(jobs, {"render.klein"}), jobs)
