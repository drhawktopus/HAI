"""Render an HAI page via the shared render.py, driven by a Tolkien order.

Order formats:
  {"kind": "page.render", "name", "request": <path>, "out_dir": <dir>}
  {"kind": "page.render", "name", "prove": true, "out_dir": <dir>}

Constraints:
- Vulkan driver libraries must be on the loader path to avoid silent SwiftShader fallback; link only the specific libs from HOSTLIB, as host glibc must not reach this image's Python.
- Browser runs as unprivileged user (not root) to protect container keys; serves a copy of the page from /tmp.
- result.json is written last to signal completion."""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

PAGE_KIND = "page.render"
HOSTLIB = os.environ.get("TOLKIEN_HOSTLIB", "/hostlib")
PROOF = os.environ.get("TOLKIEN_PAGE_PROOF", "/tabfoundry/webgl_proof.json")
NV_DIR = os.environ.get("TOLKIEN_PAGE_NVLIBS", "/tmp/hai-page-nvlibs")
ICD = "/usr/share/vulkan/icd.d/nvidia_icd.json"
BROWSERS = "/ms-playwright"
UNPRIVILEGED = 65534
# Set EGL/GL/display env vars below to force hardware GPU; otherwise browser silently falls back to SwiftShader (0/12 proofs pass).
NV_LIBS = ("*nvidia*", "libGLdispatch*", "libOpenGL*", "libGLX.so*", "libEGL.so*", "libGL.so*", "libGLESv2*", "libX11.so*", "libXext.so*", "libxcb.so*", "libXau*", "libXdmcp*",
           "libXrandr*", "libXrender*", "libwayland*", "libdrm*", "libbsd*", "libmd.so*")
ARGS = ["--use-angle=vulkan", "--use-vulkan=native", "--enable-features=Vulkan", "--disable-vulkan-surface", "--ignore-gpu-blocklist", "--no-sandbox"]
PROOF_LAUNCHES = 12
_OPEN = """() => { const c = document.createElement('canvas'); const gl = c.getContext('webgl2');
  if (!gl) return null; const i = gl.getExtension('WEBGL_debug_renderer_info');
  return i ? gl.getParameter(i.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER); }"""


def page_defects(spec):
    d = []
    if not spec.get("name"):
        d.append("missing required field: name")
    out = spec.get("out_dir")
    if not isinstance(out, str) or not out.startswith("/"):
        d.append("out_dir must be an absolute path Tolkien sees (the jobs share mount)")
    if not spec.get("prove"):
        req = spec.get("request")
        if not isinstance(req, str) or not req.startswith("/"):
            d.append("request must be an absolute path Tolkien sees (the jobs share mount)")
    return d


def proven(path=None):
    """Every launch of the last proof opened WebGL on a real card. Absent or unreadable is not proven: a refused context is a blank picture of a good page."""
    try:
        with open(path or PROOF, encoding="utf-8") as f:
            row = json.load(f)
        return bool(row["launches"]) and row["opened"] == row["launches"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def capabilities():
    return ["rigby.page"] if os.path.exists("/dev/nvidia0") and proven() else []


def link_driver_libraries(hostlib=None, folder=None):
    """The driver's libraries, and only those, in a folder of their own. -> how many are linked."""
    hostlib, folder = hostlib or HOSTLIB, folder or NV_DIR
    os.makedirs(folder, exist_ok=True)
    linked = set()
    for pat in NV_LIBS:
        for f in glob.glob(os.path.join(hostlib, pat)):
            t = os.path.join(folder, os.path.basename(f))
            if os.path.isfile(f):
                if not os.path.lexists(t):
                    os.symlink(f, t)
                linked.add(t)
    return len(linked)


def browser_env(home):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TZ")}
    env.update(LD_LIBRARY_PATH=NV_DIR, VK_ICD_FILENAMES=ICD, XDG_RUNTIME_DIR=home, HOME=home, PLAYWRIGHT_BROWSERS_PATH=BROWSERS)
    return env


def _as_unprivileged(argv, home, log=None):
    """The worker half of this file, as the unprivileged user. Runs until it ends: a stuck page is the render's own limit (render.py asks with Playwright's clock)."""
    r = subprocess.run([sys.executable, os.path.abspath(__file__)] + argv, env=browser_env(home), user=UNPRIVILEGED, group=UNPRIVILEGED,
                       extra_groups=[], cwd=home, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("the render worker ended rc %s: %s" % (r.returncode, (r.stderr or r.stdout)[-400:]))
    return r.stdout


def run_page(spec, log=print):
    """-> (summary, error). Everything in the temp folder goes, whatever happened."""
    n = link_driver_libraries()
    if not n:
        return {}, "none of the driver's libraries are in %s" % HOSTLIB
    work = tempfile.mkdtemp(prefix="hai-page-")
    os.chmod(work, 0o777)
    out_dir = spec["out_dir"]
    try:
        os.makedirs(out_dir, exist_ok=True)
        if spec.get("prove"):
            row = json.loads(_as_unprivileged(["--worker-prove"], work).strip().splitlines()[-1])
            row["when"] = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(PROOF, "w", encoding="utf-8") as f:
                json.dump(row, f)
            with open(os.path.join(out_dir, "webgl_proof.json"), "w", encoding="utf-8") as f:
                json.dump(row, f)
            log("page proof: %s" % json.dumps(row)[:200])
            return row, ""
        req = spec["request"]
        shutil.copytree(os.path.join(os.path.dirname(req), "page"), os.path.join(work, "page"))
        shutil.copyfile(req, os.path.join(work, "request.json"))
        for root, dirs, files in os.walk(work):
            for name in dirs + files:
                os.chmod(os.path.join(root, name), 0o777)
        _as_unprivileged(["--worker-render", work], work)
        with open(os.path.join(work, "out", "result.json"), encoding="utf-8") as f:
            result = json.load(f)
        for shot in result.get("shots") or []:
            shutil.copyfile(os.path.join(work, "out", shot["file"]), os.path.join(out_dir, shot["file"]))
        with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f)
        log("page render: %s" % json.dumps(result.get("webgl"))[:200])
        return result, ""
    except Exception as exc:                                       # noqa: BLE001 - the caller retires the order with this
        return {}, str(exc)[:600]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _worker_render(work):
    import render
    with open(os.path.join(work, "request.json"), encoding="utf-8") as f:
        req = json.load(f)
    out = os.path.join(work, "out")
    os.makedirs(out, exist_ok=True)
    from pathlib import Path
    try:
        result = render.render_page(Path(work) / "page" / req["page"], Path(work) / "page", Path(out), width=int(req.get("width") or 400), height=int(req.get("height") or 400),
                                    steps=req.get("steps"), wait_ms=int(req.get("wait_ms") or 1500), chromium_args=ARGS, launch_options={"channel": "chromium"},
                                    query=req.get("query") or "")
    except render.RenderError as exc:
        result = {"error": str(exc)}
    result["box"] = "rigby"
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f)


def _worker_prove():
    import render
    from playwright.sync_api import sync_playwright
    opened, names = 0, set()
    with sync_playwright() as p:
        for _ in range(PROOF_LAUNCHES):
            try:
                b = p.chromium.launch(channel="chromium", args=ARGS)
                g = b.new_page()
                g.goto("data:text/html,<canvas></canvas>")
                name = g.evaluate(_OPEN)
                b.close()
            except Exception:                                      # noqa: BLE001 - a launch that fails is a failed launch
                name = None
            if name and not any(s in name.lower() for s in render.SOFTWARE_RENDERERS):
                opened += 1
                names.add(name)
    print(json.dumps({"launches": PROOF_LAUNCHES, "opened": opened, "backend": "vulkan", "renderers": sorted(names)}))


if __name__ == "__main__":
    if sys.argv[1] == "--worker-render":
        _worker_render(sys.argv[2])
    elif sys.argv[1] == "--worker-prove":
        _worker_prove()
