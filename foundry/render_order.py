r"""A factory order that shows one page on this box's real GPU, for HAI's coding_render.

Args: <request.json> <ORDER_OUT> (the factory appends the order name).
request.json: {"page": "<file, relative to the folder beside request.json named page>", "width", "height", "steps", "wait_ms"}.
Writes ORDER_OUT\result.json (render.render_page's dict, plus the box) and the screenshots beside it; the factory ships them.

The page folder is copied to this box first, so the browser's file rule (render.request_verdict) covers a local folder and
nothing else. The copy is removed before the order ships: the product is the pictures.
render.py and netguard.py are the coding runner's, byte for byte (test_fleet_deploy.TheRenderToolIsOneSource).
"""
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(sys.executable).resolve().parents[1] / "ms-playwright"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render  # noqa: E402

GPU_ARGS = ["--use-angle=d3d11", "--ignore-gpu-blocklist"]
LAUNCH = {"channel": "chromium", "chromium_sandbox": True}
# Backend selection is machine-specific due to driver instability (e.g., D3D11 swap chain failures on some RTX cards).
# The probe validates a backend only if it succeeds across all launches; renders must use the validated backend for the local hardware.
BACKENDS = (("d3d11", GPU_ARGS), ("vulkan", ["--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist"]),
            ("opengl", ["--use-angle=gl", "--ignore-gpu-blocklist"]))


def proof_path():
    return Path(sys.executable).resolve().parents[1] / "webgl_proof.json"


def args_for(req=None):
    """A request's own arguments (a measurement), else what this box's proof chose, else the default."""
    if req and req.get("chromium_args"):
        return list(req["chromium_args"])
    try:
        return list(json.loads(proof_path().read_text(encoding="utf-8"))["args"])
    except (OSError, ValueError, KeyError, TypeError):
        return GPU_ARGS


PROOF_LAUNCHES = 12
_OPEN = """() => { const c = document.createElement('canvas'); const gl = c.getContext('webgl2');
  if (!gl) return null; const i = gl.getExtension('WEBGL_debug_renderer_info');
  return i ? gl.getParameter(i.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER); }"""


def prove(out_dir):
    """Verify that the local environment can provide WebGL contexts for page rendering.\\n    Writes the detected GPU identifier to <ORDER_OUT>/render.browser for downstream consumption by p20_foundry."""
    from playwright.sync_api import sync_playwright
    tried, row = [], None
    with sync_playwright() as p:
        for backend, args in BACKENDS:
            opened, names = 0, set()
            for _ in range(PROOF_LAUNCHES):
                try:
                    b = p.chromium.launch(args=args, **LAUNCH)
                    g = b.new_page()
                    g.goto("data:text/html,<canvas></canvas>")
                    name = g.evaluate(_OPEN)
                    b.close()
                except Exception as exc:                          # a launch that fails is a failed launch
                    print("[render] launch failed: %s" % str(exc).splitlines()[0][:200], flush=True)
                    name = None
                if name and not any(s in name.lower() for s in render.SOFTWARE_RENDERERS):
                    opened += 1
                    names.add(name)
            tried.append({"backend": backend, "opened": opened})
            row = {"launches": PROOF_LAUNCHES, "opened": opened, "backend": backend, "args": args, "renderers": sorted(names),
                   "tried": tried, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
            if opened == PROOF_LAUNCHES:
                break
    text = json.dumps(row)
    proof_path().write_text(text, encoding="utf-8")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "webgl_proof.json").write_text(text, encoding="utf-8")
    print("[render] proof %s" % text, flush=True)


def main():
    if sys.argv[1] == "prove":
        return prove(sys.argv[2])
    request_file, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    req = json.loads(request_file.read_text(encoding="utf-8"))
    page_root = out_dir / "page"
    shutil.copytree(request_file.parent / "page", page_root)
    try:
        try:
            result = render.render_page(page_root / req["page"], page_root, out_dir, width=int(req.get("width") or 400),
                                        height=int(req.get("height") or 400), steps=req.get("steps"),
                                        wait_ms=int(req.get("wait_ms") or 1500), chromium_args=args_for(req), launch_options=LAUNCH,
                                        query=req.get("query") or "")
        except render.RenderError as exc:
            result = {"error": str(exc)}
    finally:
        shutil.rmtree(page_root, ignore_errors=True)
    result["box"] = socket.gethostname().lower()
    (out_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    print("[render] 1/1 %s" % json.dumps(result.get("webgl"))[:200], flush=True)


if __name__ == "__main__":
    main()
