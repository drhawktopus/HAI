"""Verifies the release welcome and spotlight tour steps against the live chat HTML.

This prevents silent failures where a tour step references a missing DOM element. It also asserts the target count to ensure the HTML parser is working."""
import os
import re
import unittest

import app

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui")


def _read(name):
    with open(os.path.join(UI, name), encoding="utf-8") as f:
        return f.read()


class ServedFiles(unittest.TestCase):
    def test_both_files_are_served_as_javascript(self):
        from fastapi.testclient import TestClient
        c = TestClient(app.app)
        for name in ("hai-tour.js", "hai-welcome.js"):
            r = c.get("/ui/" + name)
            self.assertEqual(r.status_code, 200, name)
            self.assertIn("javascript", r.headers["content-type"])
            self.assertEqual(r.content, _read(name).encode("utf-8"))

    def test_only_the_named_files_are_served(self):
        from fastapi.testclient import TestClient
        c = TestClient(app.app)
        for name in ("app.py", "..%2Fapp.py", "config.py", "nope.js"):
            self.assertEqual(c.get("/ui/" + name).status_code, 404, name)


class PageWiring(unittest.TestCase):
    def test_the_scripts_load_before_the_page_script(self):
        h = app.CHAT_HTML
        tour = h.index('<script src="/ui/hai-tour.js"></script>')
        welcome = h.index('<script src="/ui/hai-welcome.js"></script>')
        main = h.index("<script>\nconst log = document.getElementById('log')")
        self.assertLess(tour, welcome)
        self.assertLess(welcome, main)

    def test_picking_an_account_opens_the_welcome_and_a_fault_never_blocks_chat(self):
        h = app.CHAT_HTML
        body = h[h.index("function selectUser(name){"):h.index("function nedry(){")]
        hook = body.index("welcomeOnce(name);")
        self.assertGreater(hook, body.index("gate.classList.add('hidden');"), "the welcome opens after the gate closes")
        once = h[h.index("function welcomeOnce(name){"):]
        once = once[:once.index("\n}\n")]
        self.assertIn("if (!window.HAIWelcome) return;", once, "a page without the welcome script still chats")
        self.assertIn("try { HAIWelcome.open({version: HAI_RELEASE, user: name}); } catch(e) { return; }", once,
                      "a fault in the welcome never blocks chat")
        self.assertIn("const HAI_RELEASE = '1.9';", h)

    def test_the_gear_panel_reopens_whats_new_and_the_tour(self):
        h = app.CHAT_HTML
        gear = h[h.index('<div id="gearPanel"'):h.index('<div id="uploadPanel"')]
        self.assertIn('id="whatsNewBtn"', gear)
        self.assertIn('id="tourBtn"', gear)
        self.assertIn("getElementById('whatsNewBtn').onclick = ()=> openWelcome('summary')", h)
        self.assertIn("getElementById('tourBtn').onclick = ()=> openWelcome('tour')", h)


class TourTargetsExistOnThePage(unittest.TestCase):
    def test_every_tour_target_is_on_the_chat_page(self):
        src = _read("hai-tour.js")
        targets = re.findall(r"\b(?:target|fallback):'([^']+)'", src)
        self.assertEqual(len(targets), 12, "11 steps + the Branch step's fallback - the extractor found %r" % targets)
        h = app.CHAT_HTML
        for sel in targets:
            if sel.startswith("#"):
                self.assertIn('id="%s"' % sel[1:], h, sel)
            else:
                self.assertTrue(sel.startswith(".branch"), sel)
                self.assertIn("b.className = 'branch'; b.textContent = '⑂ Branch';", h)
                self.assertIn("b.title = 'Start a new chat that continues from this answer", h)
        self.assertIn("body.ctl-open header .axis", h, "the tour opens the phone controls with the page's own class")
        self.assertIn("body.side-hidden #chatSide", h, "and the sidebar with the page's own class")

    def test_branch_is_in_the_tour_and_the_release_copy(self):
        self.assertIn("fallbackTip:", _read("hai-tour.js"), "an empty chat still teaches Branch")
        w = _read("hai-welcome.js")
        self.assertIn('"name": "Branch from any answer"', w)
        self.assertIn("⑂ Branch under any answer", w)


if __name__ == "__main__":
    unittest.main()
