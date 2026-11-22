"""A comment or docstring states a rule, never a story: no date, incident, quotation or name of who asked. The detector
(the lab's code_narrative_scan) reads every source file here; anything it finds that is not already in
tests/story_baseline.json fails the gate. The baseline only shrinks: clean a listed comment, then rewrite the baseline
with `python tests/test_no_new_stories.py --write`."""
import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASELINE = os.path.join(HERE, "story_baseline.json")
SKIP_DIRS = {"__pycache__", "backups", "workspaces", ".git", ".pytest_cache"}


def _detector():
    sys.path.insert(0, ROOT)
    import code_cleanup
    return code_cleanup._lab_import("code_narrative_scan")


def found(root=ROOT):
    """{relative path: {fingerprint: (line, first words)}} for every story the detector finds under `root`."""
    det, out = _detector(), {}
    # The suite's owner is an example name that also appears as example data in docstrings, so the owner-name signal
    # is off here; the owner's real name is caught by the lab's own scan and refused by the publish check.
    if hasattr(det, "OWNER"):
        det.OWNER = None
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(d, f)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as fh:
                    cands = det.scan_source(fh.read(), rel, embedded_js=True)   # the page scripts too
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for c in cands:
                text = " ".join((c.get("text") or "").split())
                out.setdefault(rel, {})[hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]] = (c.get("line_start"), text[:90])
    return out


class NoNewStories(unittest.TestCase):
    def test_the_detector_tells_a_story_from_a_rule(self):
        try:
            det = _detector()
        except Exception as exc:                                     # noqa: BLE001
            self.skipTest("the story detector is not available here: %r" % (exc,))
        story = '# 2026-01-02: the box hung for an hour, and Alex said "never again"\nx = 1\n'
        rule = "# a hung box is skipped for one minute, then asked again\nx = 1\n"
        self.assertEqual(len(det.scan_source(story, "a.py")), 1, "positive control: a dated incident with a quotation")
        self.assertEqual(det.scan_source(rule, "a.py"), [])

    def test_the_detector_sees_inside_an_embedded_page(self):
        """The chat page's script is a string in app.py: its comments are invisible to a Python-only scan."""
        try:
            det = _detector()
        except Exception as exc:                                     # noqa: BLE001
            self.skipTest("the story detector is not available here: %r" % (exc,))
        page = 'PAGE = """<script>\n  // 2026-01-02: Alex asked for this button\n  var a = 1;\n</script>"""\n'
        found = [c for c in det.scan_source(page, "a.py", embedded_js=True) if c["kind"] == "jscomment"]
        self.assertEqual(len(found), 1, "positive control: a dated comment inside a page's script")

    def test_the_detector_reads_a_powershell_block_comment(self):
        try:
            det = _detector()
        except Exception as exc:                                     # noqa: BLE001
            self.skipTest("the story detector is not available here: %r" % (exc,))
        block = '<#\n  2026-01-02: the box hung for an hour, and Alex said "never again"\n#>\n$x = 1\n'
        self.assertEqual(len(det.scan_source(block, "a.ps1")), 1, "positive control: a dated incident in a <# #> block")
        self.assertEqual(det.scan_source("<#\n  a hung box is asked again after a minute\n#>\n$x = 1\n", "a.ps1"), [])

    def test_the_detector_reads_docstrings_of_a_script_carried_in_a_string(self):
        """A deploy tool carries the script it runs elsewhere as a string: that script's docstrings are code too."""
        try:
            det = _detector()
        except Exception as exc:                                     # noqa: BLE001
            self.skipTest("the story detector is not available here: %r" % (exc,))
        tmpl = "REMOTE = '''\ndef f():\n    \"\"\"2026-01-02: the box hung, and Alex said 'never again'\"\"\"\n    return 1\n'''\n"
        found = [c for c in det.scan_source(tmpl, "a.py", embedded_js=True) if c["kind"] == "codestring"]
        self.assertEqual(len(found), 1, "positive control: a dated docstring inside an embedded script")

    def test_every_story_the_detector_finds_is_already_in_the_baseline(self):
        try:
            now = found()
        except Exception as exc:                                     # noqa: BLE001 - the detector is not on this machine
            self.skipTest("the story detector is not available here: %r" % (exc,))
        with open(BASELINE, encoding="utf-8") as fh:
            known = json.load(fh)
        new = ["%s:%s  %s" % (rel, line, words) for rel, items in sorted(now.items())
               for fp, (line, words) in sorted(items.items(), key=lambda kv: kv[1][0] or 0) if fp not in known.get(rel, [])]
        self.assertEqual(new, [], "a comment or docstring tells a story (state the rule, put the story in the journal):\n  "
                         + "\n  ".join(new))


if __name__ == "__main__":
    if "--write" in sys.argv:
        data = {rel: sorted(items) for rel, items in sorted(found().items())}
        with open(BASELINE, "w", encoding="utf-8", newline="") as fh:
            json.dump(data, fh, indent=1)
            fh.write("\n")
        print("baseline: %d known in %d file(s)" % (sum(len(v) for v in data.values()), len(data)))
    else:
        unittest.main()
