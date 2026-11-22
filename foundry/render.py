"""What a job's file LOOKS like to a standard viewer: a page in a real browser, a part in a standard STL reader.

A job's own tests can share its own mistake (a header of the wrong size, read back by a reader that expects the same
wrong size), so the file is opened the way any other program would open it:

  render_page  an .html / .svg file opened in headless Chromium at the size asked, with optional clicks, drags and
               waits, one screenshot after the load and after each step, and everything the page said: console lines,
               script errors, requests that failed, requests this module REFUSED.
  render_stl   the file read by the STL format's own rules (never the job's reader), a report of the solid, and
               shaded views named for the axis each looks along, so a mirrored part reads as mirrored.

The browser may load public web addresses (a page's own CDN scripts) and files inside the job's workspace. EVERY
request is checked with netguard.public_refusal, the rule fetch_url uses: a page a job wrote cannot reach the home
network or the tailnet through its scripts. WebSockets and service workers are off.
"""
from __future__ import annotations

import struct
from pathlib import Path
from urllib.parse import unquote, urlparse

import netguard

PAGE_TYPES = (".html", ".htm", ".svg")
STL_TYPES = (".stl",)
# One call's shots: the load plus this many steps. A per-call size (each shot is an image a model then reads), not a
# limit on the work: a job that wants more calls again.
MAX_STEPS = 12
VIEW_PX = 480
# The runner has no GPU, so here Chromium draws WebGL in software (SwiftShader through ANGLE). A page sent to a GPU factory
# (render_order.py) passes its own arguments. Every result says what drew it: `webgl.renderer`.
CHROMIUM_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
SOFTWARE_RENDERERS = ("swiftshader", "llvmpipe", "softpipe", "software", "basic render")
# How long the frame rate is counted, and how long the page gets to answer afterwards. A page whose main thread is stuck
# never answers an evaluate, so the answer is asked for with Playwright's own clock.
RATE_WINDOW_MS = 2000
ANSWER_MS = 30000
CONSOLE_BUDGET = 22000
# Installed before any of the page's scripts: a shader that fails to compile writes nothing to the console unless the
# page asks for its log, so a page that hid the failure would look clean to a console check and its tests could pass on
# a blank canvas. The frame counter is the rate the page actually held.
_GL_WATCH = """(() => {
  const w = window.__hai_gl = {failed_shaders: [], refused_contexts: 0, frames: 0};
  const open = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
    const got = open.call(this, kind, ...rest);
    if (!got && String(kind).startsWith('webgl') && !w.probing) w.refused_contexts++;
    return got;
  };
  const watch = (proto) => {
    if (!proto) return;
    const compile = proto.compileShader, link = proto.linkProgram;
    proto.compileShader = function (s) {
      compile.call(this, s);
      if (!this.getShaderParameter(s, this.COMPILE_STATUS))
        w.failed_shaders.push('compile: ' + String(this.getShaderInfoLog(s) || '').slice(0, 1500));
    };
    proto.linkProgram = function (p) {
      link.call(this, p);
      if (!this.getProgramParameter(p, this.LINK_STATUS))
        w.failed_shaders.push('link: ' + String(this.getProgramInfoLog(p) || '').slice(0, 1500));
    };
  };
  if (window.WebGLRenderingContext) watch(WebGLRenderingContext.prototype);
  if (window.WebGL2RenderingContext) watch(WebGL2RenderingContext.prototype);
  const beat = () => { w.frames++; requestAnimationFrame(beat); };
  requestAnimationFrame(beat);
})()"""
_GL_RENDERER = """() => {
  const c = document.createElementNS('http://www.w3.org/1999/xhtml', 'canvas');   // an .svg page's createElement makes an SVG element, with no getContext
  window.__hai_gl.probing = true;
  const gl = c.getContext('webgl2') || c.getContext('webgl');
  window.__hai_gl.probing = false;
  if (!gl) return null;
  const i = gl.getExtension('WEBGL_debug_renderer_info');
  const name = i ? gl.getParameter(i.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  const lose = gl.getExtension('WEBGL_lose_context');
  if (lose) lose.loseContext();
  return name;
}"""


class RenderError(Exception):
    """The file cannot be shown, with the reason a viewer would give."""


# ---------------------------------------------------------------- the request guard
def request_verdict(url: str, root: Path) -> str | None:
    """None when a rendered page may make this request, else why not. `root` is the job's workspace."""
    u = urlparse(url)
    if u.scheme in ("data", "blob", "about"):
        return None
    if u.scheme == "file":
        raw = unquote(u.path)
        if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":      # file:///C:/x on Windows
            raw = raw[1:]
        try:
            Path(raw).resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            return "a file outside the folder this render was given"
        return None
    if u.scheme in ("http", "https"):
        return netguard.public_refusal(u.hostname or "")
    return "scheme %r is not loaded" % (u.scheme,)


# ---------------------------------------------------------------- pages
def _nonblank(png_path: Path) -> float:
    """The share of the picture that is not its most common colour: 0.0 is ONE FLAT COLOUR (an empty canvas, or a page
    painted solid)."""
    from PIL import Image
    with Image.open(png_path) as im:
        small = im.convert("RGB").resize((160, 160))
        colours = small.getcolors(160 * 160) or []
    if not colours:
        return 1.0
    return round(1.0 - max(n for n, _c in colours) / float(160 * 160), 4)


def keep_console(lines: list) -> tuple:
    """The page's whole console, whole lines. Only a real flood is trimmed, and only to protect the fields after it: a tool
    reply over the 24,000-character cap loses its END, and that is where the script errors are. Measured on 36 real
    renders: median one line of 47 characters, longest about 22,000 characters; tighter caps (500 characters a line, the
    last 40 lines) cut a quarter of them. -> (lines kept, lines dropped). The start and the end of a flood both stay: the
    first error and the last state."""
    total = sum(len(x) for x in lines)
    if total <= CONSOLE_BUDGET:
        return list(lines), 0
    head, tail, room = [], [], CONSOLE_BUDGET // 2
    for line in lines:
        if room - len(line) < 0:
            break
        room -= len(line)
        head.append(line)
    room = CONSOLE_BUDGET // 2
    for line in reversed(lines[len(head):]):
        if room - len(line) < 0:
            break
        room -= len(line)
        tail.append(line)
    tail.reverse()
    return head + tail, len(lines) - len(head) - len(tail)


def _step_label(step: dict) -> str:
    for k in ("click", "drag", "scroll", "key", "wait_ms"):
        if k in step:
            return k
    raise RenderError("a step is one of click [x,y], drag [x1,y1,x2,y2], scroll dy, key name, wait_ms n: got %r" % (step,))


_PAGE_FACTS = """() => ({
  title: document.title,
  text: (document.body ? document.body.innerText : '').slice(0, 6000),
  images: Array.from(document.images).map(i => ({src: i.currentSrc || i.src, alt: i.alt || '',
      width: i.naturalWidth, height: i.naturalHeight}))
    .filter(i => i.src && !i.src.startsWith('data:') && i.width * i.height > 0)
    .sort((a, b) => b.width * b.height - a.width * a.height).slice(0, 40)
})"""


def webgl_report(page) -> dict:
    """What drew this page and how it held up: the renderer, whether that is software, the frames a second it held over
    RATE_WINDOW_MS, and any shader that failed. `frames_per_second` is None with a reason when the page stopped answering."""
    try:
        page.wait_for_function("() => true", timeout=ANSWER_MS)
        start = page.evaluate("[window.__hai_gl.frames, performance.now()]")
        page.wait_for_timeout(RATE_WINDOW_MS)
        page.wait_for_function("() => true", timeout=ANSWER_MS)
        end = page.evaluate("[window.__hai_gl.frames, performance.now()]")
        renderer = page.evaluate(_GL_RENDERER)
        failed, refused = page.evaluate("[window.__hai_gl.failed_shaders, window.__hai_gl.refused_contexts]")
    except Exception as exc:                                       # a stuck page is a finding, never a crash of the tool
        said = "the page stopped answering" if "Timeout" in type(exc).__name__ else "this report could not read the page, which says nothing about the page"
        return {"renderer": None, "software": None, "frames_per_second": None, "failed_shaders": [],
                "refused_contexts": None, "note": "%s: %s" % (said, str(exc).splitlines()[0][:200])}
    out = {"renderer": renderer, "software": bool(renderer) and any(s in renderer.lower() for s in SOFTWARE_RENDERERS),
           "frames_per_second": round((end[0] - start[0]) * 1000.0 / max(end[1] - start[1], 1.0), 1),
           "failed_shaders": failed[:10], "refused_contexts": refused}
    if refused:                                                    # the browser's fault, not the page's: say so
        out["note"] = "the browser refused this page a WebGL context, so a blank picture here says nothing about the page"
    return out


def render_page(src, root: Path, out_dir: Path, width: int = 400, height: int = 400, steps=None,
                wait_ms: int = 1500, name: str | None = None, chromium_args=None, launch_options=None,
                query: str = "") -> dict:
    """`src`: a file in the workspace (Path), or a public web address (str) - then the result also carries the page's
    title, its visible text and its pictures largest first, so a job can find an image and fetch it.
    `chromium_args` / `launch_options`: a factory's own browser settings; the default draws in software.
    `query`: "?a=b#c" for a workspace page, opened as the page's own address would be."""
    web = isinstance(src, str)
    address = src if web else src.resolve().as_uri() + (query or "")
    stem = name or (urlparse(src).hostname or "page" if web else src.stem)
    steps = list(steps or [])
    if len(steps) > MAX_STEPS:
        raise RenderError("one call takes %d steps at most (each is a picture to read); call again for more" % MAX_STEPS)
    labels = [_step_label(s) for s in steps]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RenderError("this runner has no browser installed (playwright is missing from its image)") from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    console, errors, refused, failed, shots = [], [], [], [], []

    def guard(route):
        why = request_verdict(route.request.url, root)
        if why:
            refused.append("%s (%s)" % (route.request.url[:200], why))
            route.abort()
        else:
            route.continue_()

    def shoot(page, n, label):
        file = "%s-%02d-%s.png" % (stem, n, label)
        page.screenshot(path=str(out_dir / file))
        shots.append({"file": file, "after": label, "varied": _nonblank(out_dir / file)})

    with sync_playwright() as p:
        browser = p.chromium.launch(args=CHROMIUM_ARGS if chromium_args is None else list(chromium_args),
                                    **(launch_options or {}))
        try:
            ctx = browser.new_context(viewport={"width": int(width), "height": int(height)}, service_workers="block")
            ctx.add_init_script(_GL_WATCH)
            ctx.route("**/*", guard)
            if hasattr(ctx, "route_web_socket"):
                ctx.route_web_socket("**/*", lambda ws: ws.close())
            page = ctx.new_page()
            page.on("console", lambda m: console.append("%s: %s" % (m.type, m.text)))
            page.on("pageerror", lambda e: errors.append(str(e)[:1000]))
            page.on("requestfailed", lambda r: failed.append("%s (%s)" % (r.url[:200], r.failure or "failed")))
            loaded = "loaded"
            try:
                resp = page.goto(address, wait_until="load")
                if web and resp is not None and resp.status >= 400:
                    loaded = "HTTP %d from the site" % resp.status
                page.wait_for_load_state("networkidle")
            except Exception as exc:                       # still loading at the browser's own limit: show what is there
                loaded = "NOT FINISHED LOADING: %s" % (str(exc).splitlines()[0][:200],)
            page.wait_for_timeout(int(wait_ms))
            webgl = webgl_report(page)
            shoot(page, 0, "load")
            facts = {}
            if web:
                try:
                    facts = page.evaluate(_PAGE_FACTS)
                except Exception as exc:                   # a page that will not answer still has its screenshot
                    facts = {"title": "", "text": "", "images": [], "facts_error": str(exc)[:200]}
            for n, (step, label) in enumerate(zip(steps, labels), 1):
                if label == "click":
                    page.mouse.click(*[float(v) for v in step["click"][:2]])
                elif label == "drag":
                    x1, y1, x2, y2 = [float(v) for v in step["drag"][:4]]
                    page.mouse.move(x1, y1)
                    page.mouse.down()
                    page.mouse.move(x2, y2, steps=12)
                    page.mouse.up()
                elif label == "scroll":
                    page.mouse.wheel(0, float(step["scroll"]))
                elif label == "key":
                    page.keyboard.press(str(step["key"]))
                page.wait_for_timeout(int(step.get("wait_ms", 400)))
                shoot(page, n, label)
        finally:
            browser.close()
    kept, dropped = keep_console(console)
    out = {"kind": "web page" if web else "page", "viewport": [int(width), int(height)], "load": loaded, "shots": shots,
           "webgl": webgl, "script_errors": errors[-20:], "requests_refused": refused[-20:], "requests_failed": failed[-20:],
           "console_lines": len(console), "console": kept}
    if dropped:
        out["console_note"] = "%d lines from the middle of a very long console were left out; the first and last lines are here" % dropped
    if web:
        out.update(address=address, title=facts.get("title", ""), text=facts.get("text", ""),
                   images=facts.get("images", []))
    return out


# ---------------------------------------------------------------- STL
def read_stl(data: bytes):
    """-> (format, triangles) by the STL format's own rules; triangles is an (n, 3, 3) float array of vertices.
    Binary STL: an 80-byte header, a 4-byte little-endian triangle count, then 50 bytes per triangle - so the file is
    exactly 84 + 50 * count bytes. Anything else that is not an ASCII `solid` file is refused with the arithmetic."""
    import numpy as np
    n_bytes = len(data)
    if n_bytes >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * count == n_bytes:
            if count == 0:
                raise RenderError("the STL holds 0 triangles")
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                                count=count, offset=84)
            return "binary", np.array(rec["v"], dtype=float)
    head = data[:512].lstrip().lower()
    if head.startswith(b"solid") and b"facet" in data[:4096].lower():
        verts = []
        for line in data.decode("ascii", "replace").splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0].lower() == "vertex":
                verts.append([float(x) for x in parts[1:]])
        if not verts or len(verts) % 3:
            raise RenderError("an ASCII STL whose vertex lines do not make whole triangles (%d vertices)" % len(verts))
        return "ascii", np.array(verts, dtype=float).reshape(-1, 3, 3)
    if n_bytes < 84:
        raise RenderError("not an STL: %d bytes, shorter than the 84-byte binary header" % n_bytes)
    count = struct.unpack_from("<I", data, 80)[0]
    raise RenderError(
        "not a valid STL. A binary STL is an 80-byte header, a 4-byte triangle count, then 50 bytes per triangle. The "
        "count at byte 80 reads %d, which needs %d bytes; the file is %d bytes. A slicer will refuse it or load "
        "scrambled triangles. (A header of the wrong length puts the count in the wrong place.)"
        % (count, 84 + 50 * count, n_bytes))


def stl_report(tris) -> dict:
    """What the solid IS: size, triangle count, and whether it is one closed surface (every edge shared by two)."""
    import numpy as np
    lo, hi = tris.reshape(-1, 3).min(axis=0), tris.reshape(-1, 3).max(axis=0)
    pts, inv = np.unique(np.round(tris.reshape(-1, 3), 5), axis=0, return_inverse=True)
    ids = inv.reshape(-1, 3)
    degenerate = int(((ids[:, 0] == ids[:, 1]) | (ids[:, 1] == ids[:, 2]) | (ids[:, 0] == ids[:, 2])).sum())
    edges = np.sort(np.concatenate([ids[:, [0, 1]], ids[:, [1, 2]], ids[:, [2, 0]]]), axis=1)
    _u, counts = np.unique(edges, axis=0, return_counts=True)
    open_edges = int((counts != 2).sum())
    return {"triangles": int(len(tris)), "min": [round(float(v), 3) for v in lo], "max": [round(float(v), 3) for v in hi],
            "size": [round(float(v), 3) for v in (hi - lo)], "distinct_heights": int(len(np.unique(np.round(pts[:, 2], 3)))),
            "degenerate_triangles": degenerate, "edges_not_shared_by_two": open_edges,
            "closed_solid": open_edges == 0 and degenerate == 0}


# name -> (what it says on the picture, the rows of the view matrix: screen right, screen up, towards the viewer)
VIEWS = {
    "top": ("TOP: looking down the Z axis. +X is right, +Y is up.", ((1, 0, 0), (0, 1, 0), (0, 0, 1))),
    "bottom": ("BOTTOM: looking up the Z axis (the part turned over). +X is LEFT, +Y is up.", ((-1, 0, 0), (0, 1, 0), (0, 0, -1))),
    "front": ("FRONT: looking along +Y. +X is right, +Z is up.", ((1, 0, 0), (0, 0, 1), (0, -1, 0))),
    "iso": ("3/4 VIEW from above the front right.", None),
}


def _view_matrix(name):
    import numpy as np
    rows = VIEWS[name][1]
    if rows is not None:
        return np.array(rows, dtype=float)
    towards = np.array([0.55, -0.65, 0.52])                     # the viewer stands in front (-Y), to the right, above
    towards = towards / np.linalg.norm(towards)
    right = np.cross(np.array([0.0, 0.0, 1.0]), towards)
    right = right / np.linalg.norm(right)
    return np.array([right, np.cross(towards, right), towards])


def render_stl_view(tris, name: str, px: int = VIEW_PX):
    """One shaded picture of the solid (far triangles first, lit from the viewer's upper left), with its caption."""
    import numpy as np
    from PIL import Image, ImageDraw
    m = _view_matrix(name)
    v = tris @ m.T                                              # (n, 3, 3): screen x, screen y, depth towards viewer
    normals = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    length = np.linalg.norm(normals, axis=1)
    keep = length > 0
    v, normals = v[keep], normals[keep] / length[keep][:, None]
    light = np.array([-0.35, 0.45, 0.82])
    shade = np.clip(np.abs(normals @ (light / np.linalg.norm(light))), 0.0, 1.0)
    order = np.argsort(v[:, :, 2].mean(axis=1))
    lo, hi = v[:, :, :2].reshape(-1, 2).min(axis=0), v[:, :, :2].reshape(-1, 2).max(axis=0)
    ss, cap = 2, 34                                             # drawn at twice the size, then reduced: smooth edges
    box = px * ss
    scale = (box * 0.9) / max(float((hi - lo).max()), 1e-9)
    off = (box - (hi - lo) * scale) / 2.0
    im = Image.new("RGB", (box, box), (255, 255, 255))
    d = ImageDraw.Draw(im)
    xy = (v[:, :, :2] - lo) * scale + off
    xy[:, :, 1] = box - xy[:, :, 1]                             # screen y runs down
    for i in order:
        g = int(70 + 165 * shade[i])
        d.polygon([tuple(p) for p in xy[i]], fill=(g, g, min(255, g + 8)))
    im = im.resize((px, px), Image.LANCZOS)
    sheet = Image.new("RGB", (px, px + cap), (255, 255, 255))
    sheet.paste(im, (0, cap))
    ImageDraw.Draw(sheet).text((6, 10), VIEWS[name][0], fill=(0, 0, 0))
    return sheet


def render_stl(src: Path, out_dir: Path, views=None) -> dict:
    fmt, tris = read_stl(src.read_bytes())
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = []
    for name in (views or list(VIEWS)):
        if name not in VIEWS:
            raise RenderError("an STL view is one of %s: got %r" % (", ".join(VIEWS), name))
        file = "%s-%s.png" % (src.stem, name)
        render_stl_view(tris, name).save(out_dir / file)
        shots.append({"file": file, "view": VIEWS[name][0]})
    return {"kind": "stl", "format": fmt, "read_by": "the STL format's own layout (not the job's reader)",
            "solid": stl_report(tris), "shots": shots}
