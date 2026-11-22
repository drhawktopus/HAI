"""infer_ollama's progress markers: item ids are caller-supplied and may contain characters invalid in Windows filenames; these must be sanitized to prevent OSError."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import infer_ollama  # noqa: E402


class MarkerNames(unittest.TestCase):
    def test_a_windows_reserved_character_becomes_underscore(self):
        self.assertEqual(infer_ollama.marker_name("chat:0373a5cdc7d9:0", 1), "chat_0373a5cdc7d9_0.r1.done")
        self.assertEqual(infer_ollama.marker_name('a<b>c"d/e\\f|g?h*i', 2), "a_b_c_d_e_f_g_h_i.r2.done")

    def test_an_ordinary_id_is_unchanged(self):
        self.assertEqual(infer_ollama.marker_name("w1_code_parse-01", 1), "w1_code_parse-01.r1.done")

    def test_every_marker_can_be_created_on_this_filesystem(self):
        with tempfile.TemporaryDirectory() as d:
            for i in ("chat:Alex.bak-pre-load-failure-cleanup:20", "x|y", "q?", "tab\there"):
                p = os.path.join(d, infer_ollama.marker_name(i, 1))
                open(p, "w").close()
                self.assertTrue(os.path.exists(p), i)


if __name__ == "__main__":
    unittest.main()
