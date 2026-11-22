"""Tests that the rigby chat window is correctly configured for vision. A measured llm order must remain as priced."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rigby_profiles
import tolkien_llm as tv

MODEL = "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"


class TheWindowsEyes(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        p = mock.patch.object(tv, "MODELS", self.d.name)
        p.start()
        self.addCleanup(p.stop)

    def touch(self, name):
        open(os.path.join(self.d.name, name), "w").close()

    def caps(self):
        self.touch("llama-server")
        with mock.patch.object(tv, "LLAMA_BIN", self.d.name), mock.patch("tolkien_page.capabilities", lambda: []):
            return tv.rigby_capabilities()

    def test_it_is_declared_only_when_both_files_are_there(self):
        self.assertNotIn("sees.%s" % MODEL, self.caps())
        self.touch(MODEL)
        self.assertNotIn("sees.%s" % MODEL, self.caps(), "the model alone does not see")
        self.touch(rigby_profiles.VISION[MODEL])
        self.assertIn("sees.%s" % MODEL, self.caps())

    def test_the_projector_goes_on_the_cpu(self):
        self.touch(rigby_profiles.VISION[MODEL])
        self.assertEqual(tv.window_vision_args(MODEL), ["-mm", os.path.join(self.d.name, rigby_profiles.VISION[MODEL]), "--no-mmproj-offload"])

    def test_no_file_no_argument(self):
        self.assertIsNone(tv.window_vision_args(MODEL), "a server told to load a missing projector would not start")

    def test_a_model_with_no_vision_file_is_left_alone(self):
        self.assertIsNone(tv.window_vision_args("some-other-model.gguf"))

    def test_it_is_not_in_the_production_profile(self):
        """An llm order's rigby arm launches from LAUNCH and rigby_server_kwargs: its menu row was priced without a projector."""
        kw = tv.rigby_server_kwargs(tv.launch_spec({"model": MODEL, "gpu": "gtx1060"}))
        self.assertNotIn("extra", kw)
        self.assertNotIn("mmproj", str(rigby_profiles.LAUNCH))

    def test_the_two_files_named_are_the_lab_s(self):
        self.assertIn(MODEL, rigby_profiles.LAUNCH, "a vision file for a model rigby does not serve would never be used")


if __name__ == "__main__":
    unittest.main()
