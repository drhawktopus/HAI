r"""Creates the Inspect AI venv at <FOUNDRY_ROOT>\\venv-inspect with Python 3.12.\n\nVersions are pinned to match rigby's Tolkien image to ensure all four GPU factories share identical behavior. A1 compute orders reference this venv as `python_exe: {FOUNDRY_ROOT}\\venv-inspect\\Scripts\\python.exe`.\n\nUsage: ssh <box> \"py -3.12 - <FOUNDRY_ROOT>\" < setup_inspect_venv.py\n\nPrints VERDICT OK only after verifying pinned dependencies and caching tiktoken's o200k_base encoding (required for token estimation when providers do not report cache counts).\n"""
import os
import subprocess
import sys

PINS = {"inspect_ai": "0.3.276", "tiktoken": "0.14.0"}       # Pins must match the tablet-foundry image to ensure environment consistency.

root = sys.argv[1] if len(sys.argv) > 1 else ""
assert root and os.path.isdir(root), "usage: py -3.12 - <FOUNDRY_ROOT>   (got %r)" % root
assert sys.version_info[:2] == (3, 12), "run this with Python 3.12 (rigby's image is 3.12), got %s" % sys.version
venv = os.path.join(root, "venv-inspect")
py = os.path.join(venv, "Scripts", "python.exe")
cache = os.path.join(venv, "tiktoken-cache")                 # TIKTOKEN_CACHE_DIR: one place, whoever runs the order


def run(argv, **kw):
    print("$ " + " ".join(argv), flush=True)
    r = subprocess.run(argv, capture_output=True, text=True, **kw)
    tail = (r.stdout + r.stderr).strip().splitlines()[-4:]
    for ln in tail:
        print("    " + ln, flush=True)
    assert r.returncode == 0, "failed (rc %s)" % r.returncode
    return r.stdout


if not os.path.exists(py):
    run([sys.executable, "-m", "venv", venv])
run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q"] + ["%s==%s" % kv for kv in PINS.items()])
os.makedirs(cache, exist_ok=True)
env = dict(os.environ, TIKTOKEN_CACHE_DIR=cache)
out = run([py, "-c", "import sys, inspect_ai, tiktoken; tiktoken.get_encoding('o200k_base'); "
                     "from inspect_ai.model import CompactionSummary, compaction; "
                     "print('versions', sys.version.split()[0], inspect_ai.__version__, tiktoken.__version__)"], env=env)
got = out.split("versions", 1)[1].split()
assert got[1:] == [PINS["inspect_ai"], PINS["tiktoken"]], "pins not in force: %s" % got
assert os.listdir(cache), "the o200k_base encoding was not cached under %s" % cache
print("VERDICT OK %s python %s inspect_ai %s tiktoken %s" % (venv, got[0], got[1], got[2]))
