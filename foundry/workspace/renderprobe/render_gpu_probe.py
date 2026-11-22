r"""MEASUREMENT ORDER: what does Chromium draw WebGL on when it runs as a factory order on this box?
Each arm: which renderer the browser reports, whether the sandbox starts, and the rate a real heavy page holds.
A page that holds a rate on a real card and crawls on SwiftShader is the positive control for the instrument.

Args: <heavy page path> <out dir> (the factory appends the order name)."""
import json
import os
import shutil
import sys
import time

here = os.path.dirname(sys.executable)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(os.path.dirname(here), "ms-playwright"))
from playwright.sync_api import sync_playwright  # noqa: E402

source_page, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)
page_path = os.path.join(out_dir, "page.html")
shutil.copyfile(source_page, page_path)
RENDERER = """() => { const c = document.createElement('canvas'); const gl = c.getContext('webgl2') || c.getContext('webgl');
  if (!gl) return 'NO WEBGL'; const i = gl.getExtension('WEBGL_debug_renderer_info');
  return i ? gl.getParameter(i.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER); }"""
COUNT = """() => { window.__n = 0; const f = () => { window.__n++; requestAnimationFrame(f); }; requestAnimationFrame(f); }"""
ARMS = [
    ("chromium-new-headless d3d11", {"channel": "chromium"}, ["--use-angle=d3d11", "--ignore-gpu-blocklist"], False),
    ("chromium-new-headless d3d11 sandboxed", {"channel": "chromium"}, ["--use-angle=d3d11", "--ignore-gpu-blocklist"], True),
    ("chromium-new-headless default", {"channel": "chromium"}, ["--ignore-gpu-blocklist"], False),
    ("headless-shell d3d11", {}, ["--use-angle=d3d11", "--ignore-gpu-blocklist"], False),
    ("chromium-new-headless swiftshader (control)", {"channel": "chromium"},
     ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"], False),
]
rows = []
with sync_playwright() as p:
    for name, kw, args, sandbox in ARMS:
        row = {"arm": name, "args": args, "sandbox": sandbox}
        t0 = time.time()
        try:
            b = p.chromium.launch(args=args, chromium_sandbox=sandbox, **kw)
            row["browser_start_s"] = round(time.time() - t0, 2)
            pg = b.new_page(viewport={"width": 400, "height": 400})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
            pg.goto("data:text/html,<canvas></canvas>")
            row["renderer"] = pg.evaluate(RENDERER)
            pg.goto("file:///" + page_path.replace("\\", "/"))
            pg.wait_for_timeout(1500)
            row["page_stats"] = pg.evaluate("window.__assbench ? window.__assbench.stats() : null")
            pg.evaluate(COUNT)
            pg.wait_for_timeout(500)
            n0, t1 = pg.evaluate("window.__n"), time.time()
            pg.wait_for_timeout(5000)
            n1, t2 = pg.evaluate("window.__n"), time.time()
            row["frames_per_second"] = round((n1 - n0) / (t2 - t1), 1)
            row["page_errors"] = errs
            pg.screenshot(path=os.path.join(out_dir, "%s.png" % name.replace(" ", "_").replace("(", "").replace(")", "")), timeout=120000)
            b.close()
        except Exception as exc:                                  # an arm that will not start is a result
            row["error"] = str(exc).splitlines()[0][:400]
        rows.append(row)
        print("[probe] %d/%d %s" % (len(rows), len(ARMS), json.dumps(row)[:300]), flush=True)
        with open(os.path.join(out_dir, "gpu_probe.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
print("[probe] done", flush=True)
