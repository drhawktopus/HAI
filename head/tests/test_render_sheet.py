"""Render tests must execute a page with empty model scripts to produce a complete sheet, must not strip model visibility, and must not touch live systems."""
import base64
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from PIL import Image

import coding_client
import config
import gpu_render
import ollama_client
import tools


def png(colour, size=(40, 40)):
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


class _Workspace:
    """Stands in for the runner's file doors: frames can be read, a new file can be written once."""

    def __init__(self, files):
        self.files, self.written, self.read = dict(files), {}, []

    def read_bytes(self, workspace, path):
        self.read.append(path)
        if path not in self.files:
            return {"error": "no such file"}
        return {"path": path, "bytes": len(self.files[path]), "sha256": "x", "data_b64": base64.b64encode(self.files[path]).decode()}

    def create_bytes(self, workspace, path, data):
        if path in self.files or path in self.written:
            return {"error": "file already exists"}
        self.written[path] = data
        return {"path": path, "bytes": len(data)}


class _Habits(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        patcher = mock.patch.object(config, "STATE_DIR", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        ctx = ollama_client._CALL_CONTEXT
        ctx.serving, ctx.pictures = ("http://box:11434", "m"), []
        self.addCleanup(lambda: (setattr(ctx, "serving", None), setattr(ctx, "pictures", None)))

    def switch(self, **on):
        with open(os.path.join(self.state, "hai-habits.json"), "w", encoding="utf-8") as f:
            json.dump(on, f)

    def page_result(self, n=3):
        labels = ["load", "drag", "key", "click", "scroll", "wait_ms"]
        return {"kind": "page", "path": "job/page.html", "rendered": True,
                "shots": [{"after": labels[i % 6], "varied": 0.3, "path": "job/renders/page-%02d-%s.png" % (i, labels[i % 6])}
                          for i in range(n)]}

    def files_for(self, result):
        return {s["path"]: png((40 * i % 255, 80, 120)) for i, s in enumerate(result["shots"])}


class OneSheetOfEveryFrame(_Habits):
    def sheet(self, result, sees=True, files=None):
        ws = _Workspace(self.files_for(result) if files is None else files)
        with mock.patch.object(coding_client, "read_bytes", ws.read_bytes), \
                mock.patch.object(coding_client, "create_bytes", ws.create_bytes), \
                mock.patch.object(ollama_client, "can_see", return_value=sees):
            return tools._with_sheet("w", result), ws

    def test_a_render_with_several_pictures_hands_back_one_sheet_and_keeps_every_frame(self):
        self.switch(render_sheet=True)
        result = self.page_result(5)
        before = json.loads(json.dumps(result))
        out, ws = self.sheet(result)
        self.assertEqual(out["shots"], before["shots"], "the frames and their paths are untouched")
        info = out["sheet"]
        self.assertEqual((info["frames"], info["of"]), (5, 5))
        self.assertTrue(info["path"].startswith("job/renders/page-sheet-") and info["path"].endswith(".png"))
        self.assertEqual(list(ws.written), [info["path"]])
        with Image.open(io.BytesIO(ws.written[info["path"]])) as im:
            self.assertEqual(im.width, tools.SHEET_WIDTH)
            self.assertGreater(len(im.convert("RGB").getcolors(100000)), 4, "the frames are really in the sheet")
        self.assertIn("coding_look any of them", info["note"])
        self.assertIn("full size", info["note"])

    def test_a_model_that_sees_is_handed_the_sheet_once_and_a_blind_one_still_gets_the_file(self):
        self.switch(render_sheet=True)
        out, ws = self.sheet(self.page_result(3), sees=True)
        offered = list(ollama_client._CALL_CONTEXT.pictures)
        self.assertEqual(len(offered), 1)
        self.assertEqual(offered[0][0], "w:" + out["sheet"]["path"])
        self.assertEqual(base64.b64decode(offered[0][1]), ws.written[out["sheet"]["path"]])
        self.assertEqual(out["sheet"]["seen_by"], "you")
        ollama_client._CALL_CONTEXT.pictures = []
        blind, ws2 = self.sheet(self.page_result(3), sees=False)
        self.assertEqual(ollama_client._CALL_CONTEXT.pictures, [])
        self.assertNotIn("seen_by", blind["sheet"])
        self.assertEqual(len(ws2.written), 1)

    def test_the_switch_off_changes_nothing_and_reads_nothing(self):
        result = self.page_result(4)
        out, ws = self.sheet(result)
        self.assertEqual(out, result)
        self.assertEqual((ws.read, ws.written), ([], {}))
        self.switch(render_sheet=False)
        out, ws = self.sheet(result)
        self.assertEqual(out, result)

    def test_a_chat_named_as_an_exception_keeps_the_tools_it_started_with(self):
        self.switch(render_sheet=True, auto_exercise=True, except_chats=["chat-in-flight"])
        ctx = ollama_client._CALL_CONTEXT
        self.addCleanup(lambda: setattr(ctx, "window_conv", None))
        ctx.window_conv = "Alex/chat-in-flight"            # the shape Kirk sets: "<user>/<chat id>"
        result = self.page_result(3)
        out, ws = self.sheet(result)
        self.assertEqual(out, result)
        self.assertFalse(tools.habit_on("auto_exercise"))
        ctx.window_conv = "Alex/another-chat"
        out, ws = self.sheet(result)
        self.assertIn("sheet", out)
        self.assertTrue(tools.habit_on("auto_exercise"))

    def test_only_chats_turns_a_habit_on_for_the_chats_named_and_for_nothing_else(self):
        self.switch(render_sheet=True, only_chats=["proof-chat"])
        ctx = ollama_client._CALL_CONTEXT
        self.addCleanup(lambda: setattr(ctx, "window_conv", None))
        ctx.window_conv = None
        self.assertFalse(tools.habit_on("render_sheet"), "a call with no chat is not one of the chats named")
        ctx.window_conv = "Alex/another-chat"
        self.assertFalse(tools.habit_on("render_sheet"))
        ctx.window_conv = "ClaudeSupervisor/proof-chat"
        self.assertTrue(tools.habit_on("render_sheet"))
        self.assertFalse(tools.habit_on("auto_exercise"), "a habit that is off stays off for a named chat")

    def test_one_picture_needs_no_sheet_and_an_stl_s_views_get_one(self):
        self.switch(render_sheet=True)
        one, ws = self.sheet(self.page_result(1))
        self.assertNotIn("sheet", one)
        stl = {"kind": "stl", "path": "job/part.stl", "rendered": True,
               "shots": [{"view": "%s: looking somewhere" % v, "path": "job/renders/part-%s.png" % v.lower()}
                         for v in ("TOP", "BOTTOM", "FRONT", "ISO")]}
        out, ws = self.sheet(stl)
        self.assertEqual(out["sheet"]["frames"], 4)
        self.assertTrue(out["sheet"]["path"].startswith("job/renders/part-sheet-"))

    def test_a_sheet_that_cannot_be_made_leaves_the_result_exactly_as_it_was(self):
        self.switch(render_sheet=True)
        result = self.page_result(3)
        out, ws = self.sheet(result, files={})                       # no frame can be read
        self.assertEqual(out, result)
        with mock.patch.object(coding_client, "read_bytes", side_effect=RuntimeError("runner down")), \
                mock.patch.object(tools.notes, "log"):
            self.assertEqual(tools._with_sheet("w", result), result)

    def test_the_same_frames_twice_is_the_same_sheet_and_not_an_error(self):
        self.switch(render_sheet=True)
        result = self.page_result(3)
        ws = _Workspace(self.files_for(result))
        with mock.patch.object(coding_client, "read_bytes", ws.read_bytes), \
                mock.patch.object(coding_client, "create_bytes", ws.create_bytes), \
                mock.patch.object(ollama_client, "can_see", return_value=True):
            first = tools._with_sheet("w", result)
            second = tools._with_sheet("w", result)
        self.assertEqual(first["sheet"]["path"], second["sheet"]["path"])
        self.assertEqual(len(ws.written), 1)


class APageIsExercisedWhenTheModelScriptsNothing(_Habits):
    def render(self, args):
        self.show = mock.Mock(return_value=(None, "every GPU box is busy"))
        self.soft = mock.Mock(return_value={"kind": "page", "shots": [], "rendered": True})
        with mock.patch.object(gpu_render, "show_page", self.show), mock.patch.object(coding_client, "render", self.soft):
            return json.loads(tools._coding_render(args))

    def test_no_steps_gets_the_standard_set_scaled_to_the_viewport_and_says_so(self):
        self.switch(auto_exercise=True)
        out = self.render({"workspace": "w", "path": "a.html", "width": 600, "height": 400})
        steps = self.soft.call_args[0][4]
        self.assertEqual(steps, tools.standard_exercise(600, 400))
        self.assertEqual(steps[0]["drag"], [150, 200, 450, 200])
        self.assertEqual([next(k for k in ("drag", "scroll", "click", "key", "wait_ms") if k in s) for s in steps],
                         ["drag", "scroll", "click", "key", "wait_ms"])
        self.assertEqual(self.show.call_args[0][4], steps, "a page sent to a GPU box is exercised the same way")
        self.assertIn("standard set", out["exercise"])
        self.assertIn("Pass your own steps", out["exercise"])

    def test_the_models_own_steps_are_used_exactly_as_given(self):
        self.switch(auto_exercise=True)
        own = [{"key": "ArrowLeft"}, {"wait_ms": 100}]
        out = self.render({"workspace": "w", "path": "a.html", "steps": own})
        self.assertEqual(self.soft.call_args[0][4], own)
        self.assertNotIn("exercise", out)

    def test_an_stl_a_web_address_and_the_switch_off_are_left_alone(self):
        self.switch(auto_exercise=True)
        self.render({"workspace": "w", "path": "part.stl"})
        self.assertIsNone(self.soft.call_args[0][4])
        self.render({"workspace": "w", "url": "https://example.org/"})
        self.assertIsNone(self.soft.call_args[0][4])
        self.switch(auto_exercise=False)
        out = self.render({"workspace": "w", "path": "a.html"})
        self.assertIsNone(self.soft.call_args[0][4])
        self.assertNotIn("exercise", out)


if __name__ == "__main__":
    unittest.main()
