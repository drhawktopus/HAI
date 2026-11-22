"""Ensure backslash escapes in embedded UI JS survive Python string parsing.

`CHAT_HTML` is a non-raw triple-quoted string. Backslashes inside are interpreted
by Python at import time, not by the browser. A single unescaped newline (`\n`)
treated as a literal newline breaks the JS syntax, disabling the entire script.
`ast.parse` validates Python syntax only; it misses broken string contents.

We use a source-level scan rather than a JS parser to avoid false positives from
regex literals. Counting backslash runs is exact."""
import io
import os
import re
import unittest

import app

APP_PY = os.path.join(os.path.dirname(__file__), "..", "app.py")


def _chat_html_source():
    """The RAW source text of the CHAT_HTML literal, before Python interprets it."""
    src = io.open(APP_PY, encoding="utf-8").read()
    marker = 'CHAT_HTML = ' + '"' * 3
    start = src.index(marker)
    end = src.index('"' * 3, start + len(marker))
    return src[start:end]


def eaten_escapes(body):
    """(line, sequence) for every backslash run Python consumes instead of passing through.

    An ODD-length run before n/t/r/0 is interpreted by Python; an EVEN-length run reaches the
    browser as an escape, which is what the JavaScript wants.
    """
    found = []
    for m in re.finditer(r"(\\+)([ntr0])", body):
        if len(m.group(1)) % 2 == 1:
            found.append((body[: m.start()].count("\n") + 1, m.group(1) + m.group(2)))
    return found


class ChatHtmlEscapeTests(unittest.TestCase):
    def test_no_escape_is_eaten_by_python(self):
        body = _chat_html_source()
        bad = eaten_escapes(body)
        self.assertEqual(
            bad, [],
            "CHAT_HTML contains backslash escape(s) Python will consume before the browser sees "
            "them: %s. Double the backslash (write '\\\\n', not '\\n'). A single one of these "
            "disables the ENTIRE UI script, login gate included." % bad[:5])

    def test_the_checker_catches_the_historical_bug(self):
        """A guard nobody has watched fail is not a guard."""
        self.assertTrue(eaten_escapes(r"cap.textContent = a + '\n' + b;"),
                        "failed to flag the exact defect that broke the UI on 2026-07-25")
        self.assertEqual(eaten_escapes(r"cap.textContent = a + '\\n' + b;"), [],
                         "a correctly doubled escape must not be flagged")
        self.assertEqual(eaten_escapes(r"var re = /[&<>\"']/g;"), [],
                         "regex literals must not trip the checker")

    def test_the_ui_still_has_its_script_and_key_entry_points(self):
        """Cheap smoke test: the page must carry a script, and the pieces the gate depends on."""
        scripts = re.findall(r"<script>(.*?)</script>", app.CHAT_HTML, re.S)
        self.assertTrue(scripts, "CHAT_HTML has no <script> block — the UI would be inert")
        js = scripts[0]
        for needed in ("function add(", "async function send(", "gateList"):
            self.assertIn(needed, js, "the UI lost %r" % needed)

    def test_absurd_tracker_tile_is_wired(self):
        """Facelift item 4: the deep-work run tracker tile (dismiss + /board pointer) is in the UI."""
        html = app.CHAT_HTML
        self.assertIn('id="absurdtile"', html)          # the pinned element
        js = re.findall(r"<script>(.*?)</script>", html, re.S)[0]
        for fn in ("function renderAbsurdTile(",
                   "function absurdTileDismiss(",
                   "function clearAbsurdTile("):
            self.assertIn(fn, js, "the tracker tile lost %r" % fn)
        self.assertIn('href="/board"', js)              # kickoff pointer + tile link to the board
