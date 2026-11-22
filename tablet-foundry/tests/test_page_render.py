"""Tests page rendering without a browser: verifies the order's checks and proof rule against the driver's isolated libraries."""
import json
import os
import tempfile
import unittest
from unittest import mock

import tablet_foundry
import tolkien_llm
import tolkien_page


class TheOrder(unittest.TestCase):
    def test_a_page_order_runs_on_rigbys_lane(self):
        self.assertEqual(tolkien_llm.lane_of_spec({"kind": "page.render"}), "rigby")

    def test_a_good_order_and_a_proof_have_no_defects(self):
        self.assertEqual(tolkien_page.page_defects({"name": "a", "request": "/ssdshared/x/request.json", "out_dir": "/ssdshared/x/out"}), [])
        self.assertEqual(tolkien_page.page_defects({"name": "a", "prove": True, "out_dir": "/ssdshared/x/out"}), [])

    def test_a_windows_path_is_refused_by_name(self):
        d = tolkien_page.page_defects({"name": "a", "request": "\\\\10.0.0.204\\x", "out_dir": "E:\\x"})
        self.assertEqual(len(d), 2)

    def test_the_foundrys_intake_knows_the_kind(self):
        self.assertEqual(tablet_foundry.spec_defects({"kind": "page.render", "name": "a", "request": "/s/r.json", "out_dir": "/s/o"}), [])


class TheProof(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "proof.json")

    def _write(self, row):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(row, f)

    def test_every_launch_or_nothing(self):
        self.assertFalse(tolkien_page.proven(self.path), "no proof is not proven")
        self._write({"launches": 12, "opened": 11})
        self.assertFalse(tolkien_page.proven(self.path))
        self._write({"launches": 12, "opened": 12})
        self.assertTrue(tolkien_page.proven(self.path))

    def test_rigby_offers_pages_only_with_a_card_and_a_proof(self):
        with mock.patch.object(tolkien_page, "proven", lambda path=None: True), mock.patch.object(tolkien_page.os.path, "exists", lambda p: True):
            self.assertEqual(tolkien_page.capabilities(), ["rigby.page"])
        with mock.patch.object(tolkien_page, "proven", lambda path=None: False):
            self.assertEqual(tolkien_page.capabilities(), [])


class TheDriversLibraries(unittest.TestCase):
    def test_only_the_named_ones_are_linked_never_the_hosts_glibc(self):
        with tempfile.TemporaryDirectory() as host, tempfile.TemporaryDirectory() as dest:
            for name in ("libGLX_nvidia.so.0", "libnvidia-glcore.so.550", "libGLdispatch.so.0", "libc.so.6", "ld-linux-x86-64.so.2", "libstdc++.so.6"):
                open(os.path.join(host, name), "w").close()
            n = tolkien_page.link_driver_libraries(host, dest)
            self.assertEqual(n, 3)
            self.assertEqual(sorted(os.listdir(dest)), ["libGLX_nvidia.so.0", "libGLdispatch.so.0", "libnvidia-glcore.so.550"])
            self.assertEqual(tolkien_page.link_driver_libraries(host, dest), 3, "linking twice is fine")

    def test_no_library_means_no_browser_and_the_order_says_so(self):
        with mock.patch.object(tolkien_page, "link_driver_libraries", lambda *a: 0):
            got, err = tolkien_page.run_page({"name": "a", "out_dir": "/nowhere"})
        self.assertEqual(got, {})
        self.assertIn("driver's libraries", err)

    def test_a_page_opens_at_the_path_and_query_the_request_names(self):
        import json
        import sys
        import tempfile
        import types
        seen = {}

        def render_page(src, root, out_dir, **kw):
            seen.update(src=str(src), root=str(root), query=kw.get("query"))
            return {"kind": "page"}

        fake = types.SimpleNamespace(render_page=render_page, RenderError=RuntimeError)
        with tempfile.TemporaryDirectory() as work:
            with open(os.path.join(work, "request.json"), "w", encoding="utf-8") as f:
                json.dump({"page": "tests/back.html", "query": "?view=back#top"}, f)
            with mock.patch.dict(sys.modules, {"render": fake}):
                tolkien_page._worker_render(work)
        self.assertEqual(seen["query"], "?view=back#top")
        self.assertTrue(seen["src"].replace("\\", "/").endswith("page/tests/back.html"), seen["src"])
        self.assertTrue(seen["root"].replace("\\", "/").endswith("/page"), seen["root"])

    def test_the_browser_gets_the_libraries_and_only_the_browser_env(self):
        with mock.patch.dict(os.environ, {"TOKEN_FOR_THE_TABLETS": "x", "PATH": "/bin"}):
            env = tolkien_page.browser_env("/tmp/a")
        self.assertEqual(env["LD_LIBRARY_PATH"], tolkien_page.NV_DIR)
        self.assertNotIn("TOKEN_FOR_THE_TABLETS", env)


if __name__ == "__main__":
    unittest.main()
