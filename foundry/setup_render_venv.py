r"""Setup the GPU browser venv for page renders (<FOUNDRY_ROOT>\venv-render).
Playwright and Pillow are pinned to match the runner image to ensure consistent rendering.
Chromium is installed inside the venv (PLAYWRIGHT_BROWSERS_PATH) so the browser binary is co-located."""
import os
import subprocess
import sys

PINS = {"playwright": "1.64.1", "pillow": "12.3.0"}          # Match the runner image versions to ensure compatibility.

root = sys.argv[1] if len(sys.argv) > 1 else ""
assert root and os.path.isdir(root), "usage: py -3.12 - <FOUNDRY_ROOT>   (got %r)" % root
assert sys.version_info[:2] == (3, 12), "run this with Python 3.12, got %s" % sys.version
venv = os.path.join(root, "venv-render")
py = os.path.join(venv, "Scripts", "python.exe")
browsers = os.path.join(venv, "ms-playwright")
env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=browsers)


def run(argv):
    print("$ " + " ".join(argv), flush=True)
    r = subprocess.run(argv, capture_output=True, text=True, env=env)
    for ln in (r.stdout + r.stderr).strip().splitlines()[-4:]:
        print("    " + ln, flush=True)
    assert r.returncode == 0, "failed (rc %s)" % r.returncode
    return r.stdout


if not os.path.exists(py):
    run([sys.executable, "-m", "venv", venv])
run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q"] + ["%s==%s" % kv for kv in PINS.items()])
run([py, "-m", "playwright", "install", "chromium"])
out = run([py, "-c", "import PIL, playwright.sync_api as s; p = s.sync_playwright().start(); "
                     "b = p.chromium.launch(channel='chromium'); g = b.new_page(); g.goto('data:text/html,<p>ok</p>'); "
                     "print('versions', PIL.__version__, g.inner_text('p')); b.close(); p.stop()"])
got = out.split("versions", 1)[1].split()
assert got == [PINS["pillow"], "ok"], "not in force: %s" % got
print("VERDICT OK %s playwright %s" % (venv, PINS["playwright"]))
