"""The /board work-board page (Piece C item 3).

A SEPARATE route from CHAT_HTML that renders the numbered PROJECT-REGISTRY board (`/projects`) and
the cross-user live-runs panel (`/runs/active`), resolving identity via `/profiles`. Keeping it off
CHAT_HTML means a render bug here cannot take down the chat/login surface. BOARD_HTML is a RAW
string, so Python does not interpret its embedded JS escapes; the served JS is `node --check`ed at
deploy time (the gate has no node), so these are structural + wiring pins only."""
import re
import unittest

import app


def _board_script():
    m = re.findall(r"<script>(.*?)</script>", app.BOARD_HTML, re.S)
    assert m, "BOARD_HTML has no <script> block — the page would be inert"
    return m[0]


class BoardPageTests(unittest.TestCase):
    def test_route_is_registered(self):
        """Verifies the route is registered in the app, not just defined in the module."""
        paths = {getattr(r, "path", None) for r in app.app.routes}
        self.assertIn("/board", paths)

    def test_handler_returns_the_page(self):
        html = app.work_board()
        self.assertIn("<!doctype html>", html)
        self.assertIn("HAI Work Board", html)

    def test_script_has_its_loaders(self):
        js = _board_script()
        for fn in ("async function loadBoard(",
                   "async function loadRuns(",
                   "async function loadProfiles("):
            self.assertIn(fn, js, "the board lost %r" % fn)

    def test_script_reads_the_right_endpoints(self):
        js = _board_script()
        for ep in ("/projects", "/runs/active?user=", "/profiles"):
            self.assertIn(ep, js, "the board no longer fetches %r" % ep)

    def test_admin_gate_is_handled_gracefully(self):
        """/runs/active is admin-only; a non-admin viewer must get a note, not a broken panel."""
        self.assertIn("status === 403", _board_script())


if __name__ == "__main__":
    unittest.main()
