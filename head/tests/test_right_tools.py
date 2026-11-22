"""The "right tools" suite stubs network, runner, and model calls to verify a job fetches files, uses standard viewers, and inspects images directly."""
import contextlib
import base64
import hashlib
import io
import json
import unittest
from unittest import mock
from PIL import Image

import coding_client
import config
import lanfetch
import ollama_client
import tools


def _names(pool):
    return [t["function"]["name"] for t in pool]


class TheSetTravelsWithTheCodingTools(unittest.TestCase):
    def test_every_path_that_has_the_coding_tools_has_these(self):
        for pool in (tools.CODING_TOOLS, tools.CODING_JOB_TOOLS, tools.HEAVY_TOOLS):
            for name in ("coding_fetch", "coding_render", "coding_look"):
                self.assertIn(name, _names(pool))

    def test_a_fetched_file_counts_as_new_work_and_a_repeated_render_does_not(self):
        p = ollama_client._LoopProgress()
        self.assertTrue(p.call("coding_render", {"path": "a.html"}, ok=True))
        self.assertFalse(p.call("coding_render", {"path": "a.html"}, ok=True), "the same render of an unchanged page")
        p.call("coding_fetch", {"url": "u", "path": "logo.png"}, ok=True)
        self.assertTrue(p.call("coding_render", {"path": "a.html"}, ok=True), "the workspace changed: look again")


class FetchToFile(unittest.TestCase):
    """The first cookie sitting retyped 1 KB of logo path data by hand, dropped a subpath and glued two numbers."""

    def fetch(self, got, made=None):
        wrote = []

        def create(ws, path, data):
            wrote.append((ws, path, data))
            return made or {"path": path, "sha256": "s", "bytes": len(data)}
        with mock.patch.object(lanfetch, "fetch_bytes", return_value=got), \
                mock.patch.object(coding_client, "create_bytes", create):
            return json.loads(tools.run_tool("coding_fetch", {"workspace": "w", "url": "https://x/logo.svg",
                                                              "path": "job/logo.svg"}, "t", user="Alex")), wrote

    def test_the_bytes_land_exactly_as_fetched(self):
        data = b"<svg>\x00\xff</svg>"
        out, wrote = self.fetch({"url": "https://x/logo.svg", "status": 200, "content_type": "image/svg+xml",
                                 "data": data, "capped": False})
        self.assertEqual(wrote, [("w", "job/logo.svg", data)])
        self.assertEqual((out["path"], out["bytes"], out["content_type"]), ("job/logo.svg", len(data), "image/svg+xml"))

    def test_a_refusal_an_http_error_or_a_cut_download_saves_nothing(self):
        for got, said in (({"error": "Refused: 10.0.0.1 is not a public internet address."}, "Refused"),
                          ({"url": "u", "status": 403, "content_type": "", "data": b"no", "capped": False}, "HTTP 403"),
                          ({"url": "u", "status": 200, "content_type": "", "data": b"x", "capped": True}, "larger than")):
            out, wrote = self.fetch(got)
            self.assertIn(said, out["error"])
            self.assertEqual(wrote, [], said)

    def test_the_web_door_refuses_the_home_network_and_other_schemes(self):
        self.assertIn("Refused", lanfetch.fetch_bytes("http://10.0.0.204:61208/api/4/gpu")["error"])
        self.assertIn("Refused", lanfetch.fetch_bytes("http://printer.lan/")["error"])
        self.assertIn("scheme", lanfetch.fetch_bytes("file:///etc/passwd")["error"])

    def test_a_public_file_comes_back_whole_with_its_type(self):
        class Resp:
            status_code, headers = 200, {"content-type": "image/png"}

            def iter_bytes(self):
                yield b"\x89PNG"
                yield b"\r\n"

        @contextlib.contextmanager
        def stream(method, url, **kw):
            yield Resp()
        with mock.patch.object(lanfetch, "_public_refusal", return_value=None), \
                mock.patch.object(lanfetch.httpx, "stream", stream):
            got = lanfetch.fetch_bytes("https://example.com/a.png")
        self.assertEqual((got["data"], got["content_type"], got["capped"]), (b"\x89PNG\r\n", "image/png", False))


class LookingAtAPicture(unittest.TestCase):
    """The model that knows what was asked looks with its own eyes; nothing describes a picture on its behalf."""
    # These are decoding tests as well as plumbing now: ABC was not a real PNG.
    _buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(_buffer, format="PNG")
    _png = _buffer.getvalue()
    READ = {"path": "job/renders/page-00-load.png", "sha256": hashlib.sha256(_png).hexdigest(),
            "bytes": len(_png), "data_b64": base64.b64encode(_png).decode("ascii")}

    def look(self, sees):
        ctx = ollama_client._CALL_CONTEXT
        ctx.serving, ctx.pictures = ("http://h", "m"), []
        self.addCleanup(lambda: (setattr(ctx, "serving", None), setattr(ctx, "pictures", None)))
        with mock.patch.object(coding_client, "read_bytes", return_value=dict(self.READ)), \
                mock.patch.object(ollama_client, "can_see", return_value=sees):
            out = json.loads(tools.run_tool("coding_look", {"workspace": "w", "path": self.READ["path"],
                                                            "question": "is it mirrored?"}, "t", user="Alex"))
        return out, list(ctx.pictures)

    def test_a_model_that_sees_gets_the_picture_itself(self):
        out, pictures = self.look(True)
        self.assertEqual(out["seen_by"], "you")
        self.assertEqual(pictures, [("w:job/renders/page-00-load.png", self.READ["data_b64"])])
        self.assertNotIn("error", out)

    def test_a_model_not_set_up_to_see_is_told_so_and_no_middleman_describes_it(self):
        """A model without vision capabilities must report that it cannot see, and must not invoke a helper to describe images."""
        for sees in (False, None):
            out, pictures = self.look(sees)
            self.assertEqual((out["seen_by"], pictures), (None, []))
            self.assertIn("not set up to take pictures", out["error"])
            self.assertNotIn("description", out)
        self.assertFalse(hasattr(tools, "_describe_picture"))

    def test_only_pictures_are_looked_at(self):
        out = json.loads(tools.run_tool("coding_look", {"workspace": "w", "path": "job/part.stl"}, "t", user="Alex"))
        self.assertIn("coding_render", out["error"])

    def test_a_strata_lead_sees_exactly_when_its_own_box_declares_it(self):
        """`can_see` must rely solely on the box's declared capabilities (e.g., `sees.<model>`); it must not infer modality from the model name or defaults."""
        import scheduler
        cards = {"mothership": {"url": "http://h:11434"}}
        for declared, sees in ((["model.flash-next", "sees.flash-next"], True), (["model.flash-next"], False), ([], False)):
            with mock.patch.object(config, "swap_base", return_value="http://h:11435"), \
                    mock.patch.object(scheduler, "CARDS", cards), \
                    mock.patch.object(scheduler, "factory_capabilities", lambda h: set(declared)):
                self.assertIs(ollama_client.can_see("http://h:11434", "flash-next"), sees, declared)
        with mock.patch.object(scheduler, "factory_capabilities", lambda h: {"sees.declared"} if h == scheduler.FELLOWSHIP_HOST else set()):
            self.assertIs(ollama_client.can_see(config.TOLKIEN_WINDOW_URL, "declared"), True, "rigby's window sees what Tolkien declares")
            self.assertIs(ollama_client.can_see(config.TOLKIEN_WINDOW_URL, "another"), False, "and nothing else")

    def test_pictures_go_down_the_strata_wire_only_for_a_model_that_sees(self):
        msgs = [{"role": "user", "content": "build it"}, {"role": "tool", "content": "{}"},
                {"role": "user", "content": "[Picture from w:a.png]", "images": ["iVBORw0KGgo=", "/9j/4AAQ"]}]
        text_only = ollama_client._openai_messages(msgs)
        self.assertEqual(text_only[-1], {"role": "user", "content": "[Picture from w:a.png]"})
        seen = ollama_client._openai_messages(msgs, pictures=True)[-1]["content"]
        self.assertEqual([p["type"] for p in seen], ["image_url", "image_url", "text"])
        self.assertEqual([p["image_url"]["url"].split(";")[0] for p in seen[:2]], ["data:image/png", "data:image/jpeg"])
        self.assertEqual(seen[2]["text"], "[Picture from w:a.png]")
        self.assertEqual(ollama_client._openai_messages(msgs, pictures=True)[0], {"role": "user", "content": "build it"})

    def test_vision_is_read_from_the_box(self):
        resp = mock.Mock(json=lambda: {"capabilities": ["completion", "vision", "tools"]})
        with mock.patch.object(config, "swap_base", return_value=None), \
                mock.patch.object(ollama_client.httpx, "post", return_value=resp), \
                mock.patch.dict(ollama_client._CAN_SEE, {}, clear=True):
            self.assertIs(ollama_client.can_see("http://box:11434", "qwen3.8:27b"), True)


class APictureInTheConversation(unittest.TestCase):
    def test_it_follows_all_of_the_turns_tool_results_and_reaches_the_next_ask(self):
        asks = []

        def once(host, model, messages, *a, **k):
            asks.append([dict(m) for m in messages])
            if len(asks) == 1:
                return {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "coding_render", "arguments": {"workspace": "w", "path": "a.html"}}},
                    {"function": {"name": "coding_look", "arguments": {"workspace": "w", "path": "a.png"}}}]}}
            return {"message": {"role": "assistant", "content": "it is the right way round"}}

        def run_tool(fn, args, user_text, user=None, persona=None):
            if fn == "coding_look":
                self.assertTrue(ollama_client.offer_picture("w:a.png", "QUJD"))
            return json.dumps({"ok": fn})
        with mock.patch.object(ollama_client, "_chat_once", once), \
                mock.patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                mock.patch.object(ollama_client.tools, "run_tool", run_tool), \
                mock.patch.object(ollama_client, "can_see", return_value=True):
            answer, _esc = ollama_client.chat("http://h", "m", [{"role": "user", "content": "build the page"}], [])
        self.assertEqual(answer, "it is the right way round")
        tail = asks[1][-3:]
        self.assertEqual([m["role"] for m in tail], ["tool", "tool", "user"], "after BOTH results, never between them")
        self.assertEqual((tail[2]["content"], tail[2]["images"]), ("[Picture from w:a.png]", ["QUJD"]))
        self.assertIsNone(ollama_client.serving(), "nothing is left set after the tools ran")

    def test_a_newer_picture_leaves_what_was_already_sent_untouched(self):
        """The engine reuses only an exact start of the prompt: rewriting an older picture re-read the whole conversation."""
        msgs = [{"role": "user", "content": "go"}]
        ollama_client._add_pictures(msgs, [("w:a.png", "OLD")])
        sent = json.dumps(msgs)
        ollama_client._add_pictures(msgs, [("w:a.png", "NEW"), ("w:b.png", "B")])
        self.assertEqual(json.dumps(msgs[:2]), sent, "the prompt so far must stay byte for byte")
        self.assertEqual([m.get("images") for m in msgs[1:]], [["OLD"], ["NEW"], ["B"]])
        self.assertIn("newest picture", msgs[2]["content"], "the model must be able to tell which is current")
        self.assertEqual(msgs[3]["content"], "[Picture from w:b.png]", "a file's first picture has no such note")

    def test_a_picture_is_never_taken_for_the_runs_request(self):
        msgs = [{"role": "user", "content": "build the page"}]
        ollama_client._add_pictures(msgs, [("w:a.png", "QUJD")])
        self.assertEqual(ollama_client._current_brief(msgs)["content"], "build the page")
        self.assertEqual(ollama_client._last_user(msgs), "build the page")


if __name__ == "__main__":
    unittest.main()
