"""Tests coding_render's page path on a real GPU. Box, runner, and share are stubbed, and the request folder is a temp dir."""
import base64
import io
import json
import os
import tarfile
import tempfile
import unittest
from unittest import mock

import coding_client
import gpu_render
import ollama_client
import p20_queue
import scheduler
import tools


def _bundle(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")


class WhichBoxShowsThePage(unittest.TestCase):
    def _free(self, caps, ranked, busy=(), exclude=()):
        place = mock.Mock(return_value=ranked)
        with mock.patch.object(scheduler, "factory_capabilities", lambda h: caps.get(h, set())), \
                mock.patch.object(scheduler, "place", place), \
                mock.patch.object(scheduler, "training_busy", lambda h: (h in busy, "a run" if h in busy else "")):
            return gpu_render.free_box(exclude), place

    def test_only_a_box_that_declares_the_browser_is_asked_about(self):
        (host, _), place = self._free({"bighonker": {"render.browser"}, "softserve": {"order.compute"}},
                                      [("bighonker", 1, "")])
        self.assertEqual(host, "bighonker")
        self.assertEqual(place.call_args[1]["only"], ("bighonker",))
        self.assertEqual(place.call_args[0][0], "page")

    def test_the_box_serving_this_job_is_never_asked_its_lane_waits_on_this_picture(self):
        _, place = self._free({"mothership": {"render.browser"}, "bighonker": {"render.browser"}}, [("bighonker", 1, "")],
                              exclude=("mothership",))
        self.assertEqual(place.call_args[1]["exclude"], ("mothership",))

    def test_a_busy_lane_is_passed_over_never_waited_for(self):
        (host, _), _ = self._free({"bighonker": {"render.browser"}, "softserve": {"render.browser"}},
                                  [("bighonker", 2, ""), ("softserve", 1, "")], busy=("bighonker",))
        self.assertEqual(host, "softserve")

    def test_no_box_free_and_no_box_able_say_which(self):
        (host, why), _ = self._free({"bighonker": {"render.browser"}}, [("bighonker", 1, "")], busy=("bighonker",))
        self.assertIsNone(host)
        self.assertIn("busy", why)
        (host, why), _ = self._free({}, [])
        self.assertIsNone(host)
        self.assertIn("render.browser", why)


class RigbysCardComesAfterTheFleets(unittest.TestCase):
    def _free(self, fleet=(), rigby=True, load=None, ranked=()):
        def caps(h):
            if h == scheduler.FELLOWSHIP_HOST:
                return {"rigby.page"} if rigby else set()
            return {"render.browser"} if h in fleet else set()
        load = load or {"ahead": 0, "free_nodes": 5, "tolkien_box_free": True, "why": "Tolkien: rigby free, 0 in line, 5 free tablets"}
        with mock.patch.object(scheduler, "factory_capabilities", caps), mock.patch.object(scheduler, "place", lambda *a, **k: list(ranked)), \
                mock.patch.object(scheduler, "training_busy", lambda h: (False, "")), mock.patch.object(scheduler, "fellowship_load", lambda *a: load):
            return gpu_render.free_box()

    def test_a_free_fleet_card_goes_first(self):
        self.assertEqual(self._free(fleet=("softserve",), ranked=[("softserve", 1, "")])[0], "softserve")

    def test_with_the_fleet_busy_a_free_rigby_takes_the_page(self):
        self.assertEqual(self._free(fleet=("softserve",), ranked=[])[0], "rigby")

    def test_rigby_busy_or_a_stale_report_is_never_free(self):
        busy = {"ahead": 1, "free_nodes": 5, "why": "Tolkien: rigby busy, 0 in line, 5 free tablets"}
        stale = {"ahead": 0, "free_nodes": None, "why": "Tolkien's free-spot report stale"}
        self.assertIsNone(self._free(load=busy)[0])
        self.assertIsNone(self._free(load=stale)[0])
        self.assertIn("rigby", self._free(load=stale)[1])

    def test_rigby_without_its_proof_is_not_asked(self):
        got, why = self._free(rigby=False)
        self.assertIsNone(got)
        self.assertIn("render.browser", why)


class AJobsNextPageIsNotBlockedByItsLastOne(unittest.TestCase):
    """A new render must not be forced to software fallback while the previous order's run record is still open."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = os.path.join(self.temp.name, "run-state.json")
        p = mock.patch.object(scheduler, "STATED_RUN", self.state)
        p.start()
        self.addCleanup(p.stop)

    def _run(self, name):
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump({"softserve": {"name": name, "state": "compute"}}, f)

    def test_a_page_order_on_the_lane_does_not_turn_the_next_page_away(self):
        self._run(scheduler.PAGE_ORDER_PREFIX + "20261105-a")
        self.assertTrue(scheduler.training_busy("softserve")[0], "the point: the lane does read busy")
        self.assertTrue(scheduler.page_on_lane("softserve"))
        with mock.patch.object(scheduler, "factory_capabilities", lambda h: {"render.browser"} if h == "softserve" else set()), \
                mock.patch.object(scheduler, "place", lambda *a, **k: [("softserve", 1, "")]):
            self.assertEqual(gpu_render.free_box(), ("softserve", ""))

    def test_any_other_order_on_the_lane_still_does(self):
        self._run("someones-six-hour-order")
        self.assertFalse(scheduler.page_on_lane("softserve"))
        with mock.patch.object(scheduler, "factory_capabilities", lambda h: {"render.browser"} if h == "softserve" else set()), \
                mock.patch.object(scheduler, "place", lambda *a, **k: [("softserve", 1, "")]):
            self.assertIsNone(gpu_render.free_box()[0])

    def test_the_prefix_is_the_one_the_orders_are_named_with(self):
        self.assertEqual(scheduler.PAGE_ORDER_PREFIX, "hai-page-")


class TheBundleCannotEscape(unittest.TestCase):
    def test_a_path_outside_the_folder_is_not_written(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "page")
            os.makedirs(dest)
            gpu_render._unpack(_bundle({"p.html": b"x", "sub/a.js": b"y", "../escaped.txt": b"z"}), dest)
            self.assertTrue(os.path.exists(os.path.join(dest, "sub", "a.js")))
            self.assertFalse(os.path.exists(os.path.join(d, "escaped.txt")))


class ShowPage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, value in ((gpu_render, {"JOBS_DIR": self.temp.name,
                                            "JOBS_UNC": r"\\share\renders"}),):
            p = mock.patch.multiple(target, **value)
            p.start()
            self.addCleanup(p.stop)
        self.status = "done"
        self.result = {"kind": "page", "shots": [{"file": "p-00-load.png", "after": "load", "varied": 0.4}],
                       "webgl": {"renderer": "ANGLE (NVIDIA, RTX 3070)", "software": False, "frames_per_second": 60.0}}
        self.spec = None
        self.filed = None
        stubs = [mock.patch.object(gpu_render, "free_box", lambda exclude=(): ("bighonker", "")),
                 mock.patch.object(coding_client, "page_bundle", lambda w, p: {"page": "p.html", "bundle_b64": _bundle({"p.html": b"x"})}),
                 mock.patch.object(coding_client, "file_shots", self._file_shots),
                 mock.patch.object(p20_queue, "enqueue_factory_order", self._enqueue),
                 mock.patch.object(p20_queue, "get", lambda i: {"status": self.status, "error": "boom"}),
                 mock.patch.object(scheduler, "factory_capabilities", lambda h: {"render.browser"})]
        for s in stubs:
            s.start()
            self.addCleanup(s.stop)

    def _enqueue(self, spec, requires=None, host="", rush=False):
        self.spec, self.requires, self.host = spec, requires, host
        rid = spec["name"].replace("hai-page-", "")
        self.folder = os.path.join(self.temp.name, rid)
        with open(os.path.join(self.folder, "request.json"), encoding="utf-8") as f:
            self.request = json.load(f)
        self.page_on_share = os.path.exists(os.path.join(self.folder, "page", "p.html"))
        if self.result is not None:
            out = os.path.join(self.folder, "out", spec["name"] + "_bighonker")
            os.makedirs(out)
            with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
                json.dump(self.result, f)
            with open(os.path.join(out, "p-00-load.png"), "wb") as f:
                f.write(b"PNG")
        return {"id": "abc"}

    def _file_shots(self, workspace, path, result, pictures):
        self.filed = (workspace, path, pictures)
        return dict(result, path=path, rendered=True)

    def test_the_picture_comes_back_filed_and_the_result_names_the_box_and_the_card(self):
        got, why = gpu_render.show_page("w", "job/p.html", 400, 400, [{"wait_ms": 1}], 900)
        self.assertEqual(why, "")
        self.assertEqual(got["drawn_on"], "bighonker")
        self.assertIn("RTX 3070", got["webgl"]["renderer"])
        self.assertEqual(self.filed[2], {"p-00-load.png": base64.b64encode(b"PNG").decode("ascii")})
        self.assertTrue(self.page_on_share)
        self.assertEqual(self.request["page"], "p.html")
        self.assertEqual(self.request["steps"], [{"wait_ms": 1}])
        self.assertEqual(self.host, "bighonker")

    def test_a_query_reaches_the_factory_in_the_request(self):
        gpu_render.show_page("w", "job/p.html", query="?view=back#top")
        self.assertEqual(self.request["query"], "?view=back#top")
        self.assertEqual(self.request["page"], "p.html")
        self.assertEqual(self.filed[1], "job/p.html", "the pictures are filed beside the file, not a path with a query")

    def test_the_order_asks_for_the_capability_and_runs_in_the_browser_venv(self):
        gpu_render.show_page("w", "job/p.html")
        self.assertIn("render.browser", self.requires)
        self.assertEqual(self.spec["mode"], "compute")
        self.assertIn("venv-render", self.spec["python_exe"])
        self.assertTrue(self.spec["payload"].endswith("render_order.py"))
        self.assertTrue(self.spec["args"][0].startswith("\\\\"), "a factory cannot see a mapped drive")

    def test_the_request_folder_is_gone_afterwards_whatever_happened(self):
        gpu_render.show_page("w", "job/p.html")
        self.assertEqual(os.listdir(self.temp.name), [])
        self.status = "failed"
        got, why = gpu_render.show_page("w", "job/p.html")
        self.assertIsNone(got)
        self.assertIn("failed", why)
        self.assertIn("boom", why)
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_an_order_that_shipped_nothing_is_not_a_picture(self):
        self.result = None
        got, why = gpu_render.show_page("w", "job/p.html")
        self.assertIsNone(got)
        self.assertIn("no result", why)

    def test_a_restart_while_the_order_runs_keeps_the_folder_and_the_same_order_is_waited_for(self):
        """A restart during execution must resume waiting for the original order (finish_page), not generate a new one."""
        class _Down(BaseException):
            pass
        seen = {}

        def note(handle):
            seen["h"] = handle
            raise _Down()                     # the process goes down right after the order is placed
        import ollama_client
        with mock.patch.object(ollama_client, "note_tool_handle", note), self.assertRaises(_Down):
            gpu_render.show_page("w", "job/p.html")
        self.assertTrue(os.path.isdir(self.folder), "the request folder stays for the run that comes back")
        self.spec = None
        got, why = gpu_render.finish_page(seen["h"])
        self.assertEqual(why, "")
        self.assertEqual(got["drawn_on"], "bighonker")
        self.assertIsNone(self.spec, "no second order was placed")
        self.assertEqual(os.listdir(self.temp.name), [], "and the folder is cleared once it has been read")

    def test_a_box_that_stops_reporting_while_the_order_waits_is_could_not_tell(self):
        self.status = "pending"
        with mock.patch.object(scheduler, "factory_capabilities", lambda h: set()):
            got, why = gpu_render.show_page("w", "job/p.html")
        self.assertIsNone(got)
        self.assertIn("stopped reporting", why)


class TheToolDrawsOnAGpuOrSaysWhyNot(unittest.TestCase):
    def _run(self, args, gpu=(None, "every GPU box is busy"), serving=("mothership", "qwen3.8:27b")):
        self.show = mock.Mock(return_value=gpu)
        self.soft = mock.Mock(return_value={"kind": "page", "shots": [], "rendered": True})
        with mock.patch.object(gpu_render, "show_page", self.show), mock.patch.object(coding_client, "render", self.soft), \
                mock.patch.object(ollama_client, "serving", lambda: serving):
            return json.loads(tools._coding_render(args))

    def test_a_page_on_a_gpu_never_touches_the_runners_browser(self):
        got = self._run({"workspace": "w", "path": "a.html"}, gpu=({"kind": "page", "drawn_on": "bighonker"}, ""))
        self.assertEqual(got["drawn_on"], "bighonker")
        self.soft.assert_not_called()
        self.assertEqual(self.show.call_args[1]["exclude"], ("mothership",))

    def test_no_gpu_free_draws_in_software_and_says_so(self):
        got = self._run({"workspace": "w", "path": "a.svg"})
        self.assertIn("software", got["drawn_on"])
        self.assertIn("busy", got["why_not_a_gpu"])

    def test_a_query_or_fragment_is_not_part_of_the_file(self):
        self._run({"workspace": "w", "path": "job/a.html?view=front&m=1"}, gpu=({"kind": "page", "drawn_on": "softserve"}, ""))
        self.assertEqual(self.show.call_args[0][:2], ("w", "job/a.html"))
        self.assertEqual(self.show.call_args[1]["query"], "?view=front&m=1")
        self._run({"workspace": "w", "path": "job/a.svg#top"}, gpu=({"kind": "page", "drawn_on": "softserve"}, ""))
        self.assertEqual(self.show.call_args[0][1], "job/a.svg")
        self.assertEqual(self.show.call_args[1]["query"], "#top")

    def test_software_gets_the_query_too(self):
        self._run({"workspace": "w", "path": "job/a.html?view=back"})
        self.assertEqual(self.soft.call_args[0][1], "job/a.html")
        self.assertEqual(self.soft.call_args[1]["query"], "?view=back")

    def test_a_question_mark_in_anything_else_is_left_alone(self):
        self._run({"workspace": "w", "path": "odd?name.stl"})
        self.show.assert_not_called()
        self.assertEqual(self.soft.call_args[0][1], "odd?name.stl")

    def test_an_stl_and_a_web_address_stay_in_the_runner(self):
        self._run({"workspace": "w", "path": "a.stl"})
        self.show.assert_not_called()
        got = self._run({"workspace": "w", "path": "job", "url": "https://example.com/a.html"})
        self.show.assert_not_called()
        self.assertNotIn("drawn_on", got)


if __name__ == "__main__":
    unittest.main()
