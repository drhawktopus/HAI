r"""MEASUREMENT ORDER: BigHonker's RTX 3070 refuses a fresh page's WebGL on most launches through Direct3D 11, whatever the sandbox. Is it the backend, the GPU process's watchdog or
compositing, or the card? Each arm 12 fresh launches. The browser's own log of the first arm goes to the order's folder: it says why a GPU process died.
Args: <out dir> (the factory appends the order name)."""
import json
import os
import re
import sys

here = os.path.dirname(sys.executable)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(os.path.dirname(here), "ms-playwright"))
from playwright.sync_api import sync_playwright  # noqa: E402

out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)
TRY = """() => { const c = document.createElement('canvas'); let why = '';
  c.addEventListener('webglcontextcreationerror', e => { why = e.statusMessage; });
  const gl = c.getContext('webgl2');
  if (!gl) return {ok: false, why};
  const i = gl.getExtension('WEBGL_debug_renderer_info');
  return {ok: true, name: i ? gl.getParameter(i.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)}; }"""
B = ["--ignore-gpu-blocklist"]
LOG = os.path.join(out_dir, "chrome-d3d11.log")
ARMS = [("d3d11 with the browser's log", B + ["--use-angle=d3d11", "--enable-logging", "--log-file=" + LOG, "--v=0"]),
        ("d3d11on12", B + ["--use-angle=d3d11on12"]),
        ("d3d9", B + ["--use-angle=d3d9"]),
        ("opengl", B + ["--use-angle=gl"]),
        ("vulkan", B + ["--use-angle=vulkan", "--enable-features=Vulkan"]),
        ("d3d11, no gpu watchdog", B + ["--use-angle=d3d11", "--disable-gpu-watchdog"]),
        ("d3d11, no direct composition", B + ["--use-angle=d3d11", "--disable-direct-composition"]),
        ("d3d11, no gpu shader disk cache", B + ["--use-angle=d3d11", "--disable-gpu-shader-disk-cache"]),
        ("d3d11, one browser, 12 pages", None)]
N = 12
rows = []
with sync_playwright() as p:
    for name, args in ARMS:
        ok, whys, names = 0, {}, set()
        if args is None:                 # the same 12 tries inside ONE browser: is it the launch or the context?
            b = p.chromium.launch(channel="chromium", args=B + ["--use-angle=d3d11"], chromium_sandbox=True)
            for i in range(N):
                g = b.new_page()
                g.goto("data:text/html,<canvas></canvas>")
                r = g.evaluate(TRY)
                ok += bool(r["ok"])
                names.add(r.get("name") or "")
                if not r["ok"]:
                    whys[r["why"][:120]] = whys.get(r["why"][:120], 0) + 1
                g.close()
            b.close()
        else:
            for i in range(N):
                try:
                    b = p.chromium.launch(channel="chromium", args=args, chromium_sandbox=True)
                    g = b.new_page()
                    g.goto("data:text/html,<canvas></canvas>")
                    r = g.evaluate(TRY)
                    b.close()
                except Exception as exc:
                    r = {"ok": False, "why": "launch: " + str(exc).splitlines()[0][:120]}
                ok += bool(r["ok"])
                names.add(r.get("name") or "")
                if not r["ok"]:
                    whys[r["why"][:120]] = whys.get(r["why"][:120], 0) + 1
        rows.append({"arm": name, "tries": N, "opened": ok, "renderers": sorted(n for n in names if n), "why_not": whys})
        print("[backend] %d/%d %s" % (len(rows), len(ARMS), json.dumps(rows[-1])[:260]), flush=True)
        with open(os.path.join(out_dir, "backend.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
try:
    with open(LOG, encoding="utf-8", errors="replace") as f:
        lines = [ln.strip()[:240] for ln in f if re.search(r"gpu|GPU|d3d|D3D|angle|ANGLE|device|context", ln) and re.search(r"ERROR|FATAL|WARNING|lost|crash|fail", ln)]
    with open(os.path.join(out_dir, "chrome-errors.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines[:200]))
except OSError:
    pass
print("[backend] done", flush=True)
