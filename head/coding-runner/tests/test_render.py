"""The right-tools set in the runner: a job's file as a standard viewer shows it, a fetched file as a FILE,
a picture handed back for a model to look at. Each class names the fault it exists for."""
import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import render
from runner import CodingRunner, ConflictError, PolicyError

# an L-shaped prism: asymmetric left to right, so a mirror image is a different picture
_L = [(0, 0), (30, 0), (30, 10), (10, 10), (10, 40), (0, 40)]


def _prism_triangles(outline, height=10.0):
    tris = []
    n = len(outline)
    for i in range(1, n - 1):                       # caps (a fan: the L is star-shaped from its first corner)
        a, b, c = outline[0], outline[i], outline[i + 1]
        tris.append(((a[0], a[1], 0.0), (c[0], c[1], 0.0), (b[0], b[1], 0.0)))
        tris.append(((a[0], a[1], height), (b[0], b[1], height), (c[0], c[1], height)))
    for i in range(n):                              # walls
        a, b = outline[i], outline[(i + 1) % n]
        tris.append(((a[0], a[1], 0.0), (b[0], b[1], 0.0), (b[0], b[1], height)))
        tris.append(((a[0], a[1], 0.0), (b[0], b[1], height), (a[0], a[1], height)))
    return tris


def stl_bytes(tris, header_len=80):
    body = b"".join(struct.pack("<12fH", 0.0, 0.0, 0.0, *[c for v in t for c in v], 0) for t in tris)
    return b"\0" * header_len + struct.pack("<I", len(tris)) + body


class TheStlIsReadByTheFormatsOwnRules(unittest.TestCase):
    """The cookie cutter's file had a 50-byte header; the job's reader and its 33 tests used the same wrong number."""

    def test_a_valid_binary_stl_is_read_and_measured(self):
        fmt, tris = render.read_stl(stl_bytes(_prism_triangles(_L)))
        rep = render.stl_report(tris)
        self.assertEqual((fmt, rep["triangles"], rep["size"]), ("binary", 20, [30.0, 40.0, 10.0]))
        self.assertTrue(rep["closed_solid"], rep)
        self.assertEqual(rep["distinct_heights"], 2, "a flat slab has two heights: a claimed flange would add a third")

    def test_a_header_of_the_wrong_length_is_refused_with_the_arithmetic(self):
        bad = stl_bytes(_prism_triangles(_L), header_len=50)
        with self.assertRaises(render.RenderError) as cm:
            render.read_stl(bad)
        said = str(cm.exception)
        self.assertIn("80-byte header", said)
        self.assertIn("the file is %d bytes" % len(bad), said)
        self.assertIn("slicer", said)

    def test_an_ascii_stl_is_read(self):
        lines = ["solid t"]
        for t in _prism_triangles(_L):
            lines += ["facet normal 0 0 0", "outer loop"] + ["vertex %g %g %g" % v for v in t] + ["endloop", "endfacet"]
        fmt, tris = render.read_stl(("\n".join(lines) + "\nendsolid t\n").encode())
        self.assertEqual((fmt, len(tris)), ("ascii", 20))

    def test_a_solid_with_a_hole_is_said_to_be_open(self):
        _fmt, tris = render.read_stl(stl_bytes(_prism_triangles(_L)[:-1]))
        self.assertFalse(render.stl_report(tris)["closed_solid"])


class TheViewsSayWhichWayIsWhich(unittest.TestCase):
    """The cutter was a MIRROR IMAGE of the logo and nothing showed it: the model's y ran up, the logo's down."""

    @staticmethod
    def _ink_centre_x(im):
        px = im.convert("L").crop((0, 34, im.width, im.height))
        xs = [x for y in range(0, px.height, 4) for x in range(0, px.width, 4) if px.getpixel((x, y)) < 250]
        return sum(xs) / float(len(xs)) / px.width

    def test_the_part_turned_over_is_the_mirror_of_the_part_from_above(self):
        _fmt, tris = render.read_stl(stl_bytes(_prism_triangles(_L)))
        top, bottom = render.render_stl_view(tris, "top"), render.render_stl_view(tris, "bottom")
        self.assertLess(self._ink_centre_x(top), 0.5, "the L's foot points right, its mass sits left, seen from above")
        self.assertGreater(self._ink_centre_x(bottom), 0.5, "and right, turned over")
        self.assertIn("+X is right", render.VIEWS["top"][0])
        self.assertIn("+X is LEFT", render.VIEWS["bottom"][0])

    def test_every_view_draws_the_part(self):
        _fmt, tris = render.read_stl(stl_bytes(_prism_triangles(_L)))
        for name in render.VIEWS:
            im = render.render_stl_view(tris, name)
            colours = im.convert("L").getcolors(im.width * im.height)
            self.assertGreater(len(colours), 3, name)


class APageCannotReachTheHomeNetwork(unittest.TestCase):
    """A rendered page runs the job's own scripts in a real browser: every request passes the web door's rule."""

    def verdict(self, url, resolves=None):
        root = Path(tempfile.gettempdir()) / "ws-root"
        infos = [(2, 1, 6, "", (a, 0)) for a in (resolves or [])]
        with mock.patch.object(render.netguard.socket, "getaddrinfo", return_value=infos):
            return render.request_verdict(url, root)

    def test_public_hosts_and_inline_data_load(self):
        self.assertIsNone(self.verdict("https://cdn.jsdelivr.net/npm/three@0.168.0/build/three.module.js", ["151.101.1.229"]))
        self.assertIsNone(self.verdict("data:image/png;base64,AAAA"))
        self.assertIsNone(self.verdict("blob:null/1234"))

    def test_the_home_network_the_tailnet_and_local_names_are_refused(self):
        tailnet = "http://%s/" % ".".join(("100", "64", "0", "1"))   # a CGNAT address, built so no real one is written
        for url, res in (("http://10.0.0.1/", None), (tailnet, None), ("http://127.0.0.1:8099/health", None),
                         ("http://printer.lan/", None), ("http://nas/", None), ("https://rebind.example.com/", ["10.0.0.205"]),
                         ("http://[::1]/", None)):
            self.assertIsNotNone(self.verdict(url, res), url)

    def test_only_files_inside_the_jobs_workspace_load(self):
        root = Path(tempfile.gettempdir()) / "ws-root"
        self.assertIsNone(render.request_verdict((root / "job" / "a.js").as_uri(), root))
        self.assertIn("outside", render.request_verdict((root.parent / "other" / "secret.txt").as_uri(), root))
        self.assertIn("outside", render.request_verdict((root / ".." / "x").as_uri(), root))

    def test_other_schemes_are_not_loaded(self):
        self.assertIn("scheme", self.verdict("ftp://example.com/x"))


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        (self.workspace / "job").mkdir(parents=True)
        (self.workspace / ".env").write_text("SECRET=nope\n", encoding="utf-8")
        (self.workspace / "job" / "note.txt").write_text("x\n", encoding="utf-8")
        for args in (("init",), ("config", "user.email", "r@example.invalid"), ("config", "user.name", "R"),
                     ("add", "."), ("commit", "-m", "fixture")):
            subprocess.run(["git", *args], cwd=self.workspace, check=True, capture_output=True)
        registry = {"workspaces": [{"id": "fixture", "path": str(self.workspace), "description": "t", "read": True,
                                    "write": True, "git": True, "protected_paths": [".env"],
                                    "ignored_paths": [".git"], "commands": {}}]}
        (self.base / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
        self.runner = CodingRunner(self.base / "registry.json", self.base / "state")

    def tearDown(self):
        self.temp.cleanup()


class AFetchedFileLandsAsAFile(_Fixture):
    """The first cookie sitting retyped 1 KB of logo path data by hand and dropped a subpath."""

    def test_bytes_are_written_exactly_and_read_back(self):
        data = bytes(range(256)) * 4
        made = self.runner.file_create_bytes("fixture", "job/ref/logo.png", data=data)
        self.assertEqual((made["path"], made["bytes"]), ("job/ref/logo.png", 1024))
        back = self.runner.file_read_bytes("fixture", "job/ref/logo.png")
        self.assertEqual((back["data"], back["sha256"]), (data, made["sha256"]))

    def test_it_never_overwrites_escapes_or_touches_a_protected_path(self):
        self.runner.file_create_bytes("fixture", "job/a.bin", data=b"1")
        with self.assertRaises(ConflictError):
            self.runner.file_create_bytes("fixture", "job/a.bin", data=b"2")
        with self.assertRaises(PolicyError):
            self.runner.file_create_bytes("fixture", "../out.bin", data=b"1")
        with self.assertRaises(PolicyError):
            self.runner.file_create_bytes("fixture", ".env", data=b"1")
        with self.assertRaises(PolicyError):
            self.runner.file_read_bytes("fixture", "../registry.json")

    def test_a_file_over_the_limit_is_refused_with_the_number(self):
        with mock.patch("runner.BYTES_MAX", 10), self.assertRaises(PolicyError) as cm:
            self.runner.file_create_bytes("fixture", "job/big.bin", data=b"x" * 11)
        self.assertIn("10-byte", str(cm.exception))


class RenderThroughTheRunner(_Fixture):
    def test_an_stl_comes_back_as_named_pictures_beside_it_with_its_measurements(self):
        (self.workspace / "job" / "part.stl").write_bytes(stl_bytes(_prism_triangles(_L)))
        out = self.runner.render("fixture", "job/part.stl")
        self.assertTrue(out["rendered"])
        self.assertEqual(out["solid"]["size"], [30.0, 40.0, 10.0])
        self.assertEqual([s["path"] for s in out["shots"]],
                         ["job/renders/part-%s.png" % v for v in ("top", "bottom", "front", "iso")])
        for s in out["shots"]:
            self.assertTrue((self.workspace / s["path"]).is_file(), s["path"])

    def test_a_file_no_viewer_opens_is_the_finding_not_an_error(self):
        (self.workspace / "job" / "bad.stl").write_bytes(stl_bytes(_prism_triangles(_L), header_len=50))
        out = self.runner.render("fixture", "job/bad.stl")
        self.assertFalse(out["rendered"])
        self.assertIn("not a valid STL", out["why"])

    def test_a_type_it_does_not_show_is_refused_by_name(self):
        with self.assertRaises(PolicyError) as cm:
            self.runner.render("fixture", "job/note.txt")
        self.assertIn(".html", str(cm.exception))


def _have_browser():
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


# In the runner image (PLAYWRIGHT_BROWSERS_PATH is set there) a missing browser FAILS these; elsewhere they are skipped.
@unittest.skipUnless(_have_browser() or os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), "no browser outside the runner image")
class TheConsoleComesThroughWhole(unittest.TestCase):
    """The console is kept whole: tighter caps (500 characters a line, the last 40 lines) cut a quarter of real jobs'
    renders, and a job then works around them instead of reading them."""

    def test_a_long_line_and_many_lines_are_kept_whole(self):
        lines = ["log: " + "x" * 3000] + ["log: row %d" % i for i in range(300)]
        kept, dropped = render.keep_console(lines)
        self.assertEqual((kept, dropped), (lines, 0))

    def test_a_flood_keeps_its_start_and_end_says_what_it_left_out_and_fits_the_budget(self):
        lines = ["log: frame %06d %s" % (i, "y" * 80) for i in range(5000)]
        kept, dropped = render.keep_console(lines)
        self.assertEqual(kept[0], lines[0])
        self.assertEqual(kept[-1], lines[-1])
        self.assertEqual(len(kept) + dropped, 5000)
        self.assertGreater(dropped, 0)
        self.assertLessEqual(sum(len(x) for x in kept), render.CONSOLE_BUDGET)

    def test_one_line_bigger_than_the_budget_does_not_empty_the_console_silently(self):
        kept, dropped = render.keep_console(["log: " + "z" * 90000])
        self.assertEqual((kept, dropped), ([], 1))


class ThePageSaysWhatDrewIt(_Fixture):
    """A shader that fails to compile is reported even when the page hides it (a clean console and passing tests on a
    blank canvas), and a render drawn in software says so."""

    PAGE = """<!doctype html><canvas id=c width=64 height=64></canvas><script>
      const gl = document.getElementById('c').getContext('webgl2');
      const s = gl.createShader(gl.FRAGMENT_SHADER);
      gl.shaderSource(s, '#version 300 es\\nprecision highp float; out vec4 o; void main(){ o = NOT_DECLARED; }');
      gl.compileShader(s);   // the page never asks for the log, as that page did not
    </script>"""

    def test_a_shader_the_page_hid_is_reported_and_software_is_named(self):
        (self.workspace / "job" / "hidden.html").write_text(self.PAGE, encoding="utf-8")
        got = self.runner.render("fixture", "job/hidden.html")
        self.assertEqual(got["console"], [], "the point: the console says nothing")
        self.assertEqual(len(got["webgl"]["failed_shaders"]), 1)
        self.assertIn("NOT_DECLARED", got["webgl"]["failed_shaders"][0])
        self.assertIn("swiftshader", got["webgl"]["renderer"].lower())
        self.assertTrue(got["webgl"]["software"])
        self.assertGreater(got["webgl"]["frames_per_second"], 0)

    def test_an_svg_page_reports_its_renderer_too(self):
        (self.workspace / "job" / "shape.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><circle cx="50" cy="50" r="40" fill="red"/></svg>', encoding="utf-8")
        got = self.runner.render("fixture", "job/shape.svg")
        self.assertTrue(got["webgl"]["renderer"], got["webgl"])
        self.assertNotIn("note", got["webgl"])

    def test_a_clean_page_reports_no_failed_shader(self):
        (self.workspace / "job" / "plain.html").write_text("<!doctype html><p>hello</p>", encoding="utf-8")
        got = self.runner.render("fixture", "job/plain.html")
        self.assertEqual(got["webgl"]["failed_shaders"], [])
        self.assertEqual(got["webgl"]["refused_contexts"], 0)


class APageGoesToAGpuAsABundle(_Fixture):
    def test_the_bundle_is_the_pages_folder_without_git_or_old_renders(self):
        import io
        import tarfile
        job = self.workspace / "job"
        (job / "p.html").write_text("<p>x</p>", encoding="utf-8")
        (job / "assets").mkdir()
        (job / "assets" / "a.js").write_text("1", encoding="utf-8")
        (job / "renders").mkdir()
        (job / "renders" / "old.png").write_bytes(b"x")
        got = self.runner.page_bundle("fixture", "job/p.html")
        self.assertEqual(got["page"], "p.html")
        with tarfile.open(fileobj=io.BytesIO(got["bundle"]), mode="r:gz") as tar:
            names = tar.getnames()
        self.assertIn("p.html", names)
        self.assertIn("assets/a.js", names)
        self.assertFalse([n for n in names if n.startswith("renders")], names)

    def test_a_folder_too_big_for_the_runners_file_limit_is_refused(self):
        (self.workspace / "job" / "p.html").write_text("<p>x</p>", encoding="utf-8")
        with mock.patch("runner.BYTES_MAX", 1):
            with self.assertRaises(PolicyError):
                self.runner.page_bundle("fixture", "job/p.html")

    def _names(self, got):
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(got["bundle"]), mode="r:gz") as tar:
            return tar.getnames()

    def test_a_page_that_names_no_parent_still_sends_only_its_folder(self):
        job = self.workspace / "job"
        (job / "other.txt").write_text("not needed", encoding="utf-8")
        (job / "sub").mkdir()
        (job / "sub" / "p.html").write_text("<p>x</p>", encoding="utf-8")
        got = self.runner.page_bundle("fixture", "job/sub/p.html")
        self.assertEqual(got["page"], "p.html")
        self.assertEqual(self._names(got), ["p.html"])

    def test_a_named_parent_widens_the_bundle_that_far_and_no_further(self):
        job = self.workspace / "job"
        (job / "main.html").write_text("<p>m</p>", encoding="utf-8")
        (self.workspace / "sibling.txt").write_text("not reached", encoding="utf-8")
        (job / "tests").mkdir()
        (job / "tests" / "back.html").write_text('<a href="../main.html">', encoding="utf-8")
        (job / "renders").mkdir()
        (job / "renders" / "old.png").write_bytes(b"x")
        got = self.runner.page_bundle("fixture", "job/tests/back.html")
        self.assertEqual(got["page"], "tests/back.html")
        names = self._names(got)
        self.assertIn("main.html", names)
        self.assertIn("tests/back.html", names)
        self.assertNotIn("sibling.txt", names)
        self.assertFalse([n for n in names if "renders" in n], names)

    def test_a_bundle_never_leaves_the_workspace(self):
        job = self.workspace / "job"
        (job / "p.html").write_text('<a href="../../../../../../../../x">', encoding="utf-8")
        got = self.runner.page_bundle("fixture", "job/p.html")
        self.assertEqual(got["page"], "job/p.html", "the workspace root is the widest it goes")

    def test_a_wide_bundle_too_big_narrows_to_the_pages_folder(self):
        job = self.workspace / "job"
        (job / "big.bin").write_bytes(os.urandom(6000))
        (job / "tests").mkdir()
        (job / "tests" / "back.html").write_text('<a href="../big.bin">', encoding="utf-8")
        with mock.patch("runner.BYTES_MAX", 3000):
            got = self.runner.page_bundle("fixture", "job/tests/back.html")
        self.assertEqual(got["page"], "back.html")

    def test_pictures_land_in_renders_and_a_second_render_replaces_them(self):
        (self.workspace / "job" / "p.html").write_text("<p>x</p>", encoding="utf-8")
        result = {"kind": "page", "shots": [{"file": "p-00-load.png", "after": "load", "varied": 0.5}],
                  "webgl": {"renderer": "ANGLE (NVIDIA)"}}
        for data in (b"first", b"second"):
            got = self.runner.file_shots("fixture", "job/p.html", result=result, pictures={"p-00-load.png": data})
        self.assertEqual(got["shots"], [{"after": "load", "varied": 0.5, "path": "job/renders/p-00-load.png"}])
        self.assertEqual((self.workspace / "job" / "renders" / "p-00-load.png").read_bytes(), b"second")
        self.assertEqual(got["webgl"]["renderer"], "ANGLE (NVIDIA)")
        self.assertTrue(got["rendered"])


class ARealBrowserShowsThePage(_Fixture):
    PAGE = """<!doctype html><html><body style="margin:0;background:#fff">
<canvas id="c" width="200" height="200"></canvas>
<script>
  const g = document.getElementById('c').getContext('2d');
  g.fillStyle = '#c00'; g.fillRect(20, 20, 120, 90);
  console.error('the page said this');
  fetch('http://10.0.0.1/').catch(() => {});
  const i = new Image(); i.src = 'file:///etc/passwd'; document.body.appendChild(i);
  document.body.addEventListener('click', () => { g.fillStyle = '#00c'; g.fillRect(0, 0, 200, 200); });
  undefinedFunctionCall();
</script></body></html>"""

    def test_a_page_is_drawn_heard_clicked_and_kept_off_the_home_network(self):
        (self.workspace / "job" / "page.html").write_text(self.PAGE, encoding="utf-8")
        out = self.runner.render("fixture", "job/page.html", width=200, height=200, steps=[{"click": [100, 100]}])
        self.assertTrue(out["rendered"], out)
        self.assertEqual([s["after"] for s in out["shots"]], ["load", "click"])
        self.assertGreater(out["shots"][0]["varied"], 0.1, "the red rectangle is on the canvas")
        self.assertEqual(out["shots"][1]["varied"], 0.0, "painted one flat colour by the click")
        first, second = [(self.workspace / s["path"]).read_bytes() for s in out["shots"]]
        self.assertNotEqual(first, second, "the click changed the picture")
        self.assertTrue(any("the page said this" in line for line in out["console"]), out["console"])
        self.assertTrue(any("undefinedFunctionCall" in e for e in out["script_errors"]), out["script_errors"])
        refused = " ".join(out["requests_refused"])
        self.assertIn("10.0.0.1", refused)
        self.assertIn("outside the folder this render was given", refused)

    def test_a_page_opened_with_a_query_and_a_fragment_reads_them(self):
        page = ("<!doctype html><script>console.log('SAW ' + location.search + ' ' + location.hash)</script>")
        (self.workspace / "job" / "q.html").write_text(page, encoding="utf-8")
        out = self.runner.render("fixture", "job/q.html", query="?view=back&m=1#top")
        self.assertTrue(any("SAW ?view=back&m=1 #top" in line for line in out["console"]), out["console"])
        self.assertEqual(out["requests_refused"], [], "the query is not a different file")

    def test_a_page_that_reaches_a_parent_file_is_shown_whole_from_its_bundle(self):
        """A page in a subfolder that opens ../main.html renders on a GPU factory, which gets only the bundle."""
        import io
        import tarfile
        import tempfile
        from pathlib import Path
        import render as R
        job = self.workspace / "job"
        (job / "main.html").write_text("<!doctype html><script>console.log('MAIN RAN ' + location.search)</script>", encoding="utf-8")
        (job / "tests").mkdir()
        (job / "tests" / "back.html").write_text('<!doctype html><iframe src="../main.html?view=back"></iframe>', encoding="utf-8")
        got = self.runner.page_bundle("fixture", "job/tests/back.html")
        self.assertEqual(got["page"], "tests/back.html")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "page"
            with tarfile.open(fileobj=io.BytesIO(got["bundle"]), mode="r:gz") as tar:
                tar.extractall(root)
            out = R.render_page(root / got["page"], root, Path(d) / "out")
        self.assertEqual(out["requests_refused"], [])
        self.assertTrue(any("MAIN RAN ?view=back" in line for line in out["console"]), out["console"])


if __name__ == "__main__":
    unittest.main()
