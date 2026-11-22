r"""MEASUREMENT ORDER: on some launches a page's getContext('webgl2') returns null on BigHonker's card, and the page draws nothing.
One variable per arm, the same 12 fresh browser launches each. A row: did the first context open (with the browser's own reason when
not), did a second context a moment later, and which GPU process flags were in force. Args: <out dir> (the factory appends the order name)."""
import json
import os
import sys
import time

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
  const name = i ? gl.getParameter(i.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  const l = gl.getExtension('WEBGL_lose_context'); if (l) l.loseContext();
  return {ok: true, name}; }"""
D3D = ["--use-angle=d3d11", "--ignore-gpu-blocklist"]
ARMS = [("sandbox on", D3D, True), ("sandbox off", D3D, False), ("sandbox off, gpu sandbox off", D3D + ["--disable-gpu-sandbox"], False),
        ("sandbox on, gpu sandbox off", D3D + ["--disable-gpu-sandbox"], True), ("sandbox on, in-process gpu", D3D + ["--in-process-gpu"], True)]
N = 12
rows = []
with sync_playwright() as p:
    for name, args, sandbox in ARMS:
        first_ok = later_ok = 0
        whys = {}
        names = set()
        for i in range(N):
            try:
                b = p.chromium.launch(channel="chromium", args=args, chromium_sandbox=sandbox)
                g = b.new_page(viewport={"width": 400, "height": 400})
                g.goto("data:text/html,<canvas></canvas>")
                a = g.evaluate(TRY)
                g.wait_for_timeout(1000)
                c = g.evaluate(TRY)
                b.close()
            except Exception as exc:                              # a launch that fails is a row of this arm
                a = c = {"ok": False, "why": "launch: " + str(exc).splitlines()[0][:160]}
            first_ok += bool(a["ok"])
            later_ok += bool(c["ok"])
            for r in (a, c):
                if r["ok"]:
                    names.add(r["name"])
                else:
                    whys[r["why"][:200]] = whys.get(r["why"][:200], 0) + 1
        row = {"arm": name, "launches": N, "first_context_ok": first_ok, "second_context_ok": later_ok, "renderers": sorted(names), "why_not": whys}
        rows.append(row)
        print("[flake] %d/%d %s" % (len(rows), len(ARMS), json.dumps(row)[:300]), flush=True)
        with open(os.path.join(out_dir, "flake.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
print("[flake] done", flush=True)
