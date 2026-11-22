"""Runs the page's own welcomeOnce (from CHAT_HTML) in node with a stand-in page, without importing app.py (importing it
here would reach live boxes). Four cases: not seen yet (shown once, recorded once); seen on another device (not shown,
nothing recorded); server unreachable (falls back to the browser's own marker); another account picked meanwhile (not
shown). Pass a file to check a served page instead of the mirror, e.g. a page fetched from the head.

    python welcome_page_check.py [app.py or a saved page]       exit 0 = all four pass
"""
import json
import re
import shutil
import subprocess
import sys

import os  # noqa: E402
import lab_settings as S  # noqa: E402
src = sys.argv[1] if len(sys.argv) > 1 else os.path.join((S.setting_json("CODE_MIRRORS", {}) or {}).get("orchestrator", ""), "app.py")
text = open(src, encoding="utf-8").read()
m = re.search(r"function welcomeOnce\(name\)\{.*?\n\}\n", text, re.S)
assert m, "no welcomeOnce in %s" % src
fn = m.group(0)
if src.endswith(".py") and chr(92) in fn:
    sys.exit("FAIL: the page function carries a backslash, and CHAT_HTML is a non-raw Python string")
node = shutil.which("node")
assert node, "node is not installed here"

HARNESS = """
const calls = [], opened = [];
let user = 'Alex';
const HAI_RELEASE = '1.9';
const window = {HAIWelcome: {open: o => { opened.push(o); return {}; }}};
const HAIWelcome = window.HAIWelcome;
function fetch(url, opts) {
  calls.push([url, opts ? opts.method : 'GET', opts ? opts.body : null]);
  if (DOWN && !opts) return Promise.reject(new Error('down'));
  return Promise.resolve({ok: true, json: () => Promise.resolve({seen: SEEN})});
}
FN
welcomeOnce('Alex');
if (CHANGE) user = 'Avery';
setTimeout(() => console.log(JSON.stringify({calls, opened})), 20);
"""


def run(seen, down=False, change=False):
    script = (HARNESS.replace("SEEN", seen).replace("DOWN", "true" if down else "false")
              .replace("CHANGE", "true" if change else "false").replace("FN", fn))
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


fails = []


def check(name, ok, detail):
    print("%-58s %s  %s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        fails.append(name)


g = run("{}")
posts = [c for c in g["calls"] if c[1] == "POST"]
check("not seen yet: shown once, recorded once", len(g["opened"]) == 1 and len(posts) == 1 and posts[0][0] == "/prefs/welcome"
      and '"release":"1.9"' in (posts[0][2] or ""), "opened %d, posts %s" % (len(g["opened"]), posts))
g = run('{"1.9": "2026-11-15T00:00:00Z"}')
check("seen on another device: not shown, nothing recorded",
      g["opened"] == [] and not [c for c in g["calls"] if c[1] == "POST"], "opened %d" % len(g["opened"]))
g = run("{}", down=True)
check("server unreachable: the browser's own marker decides", len(g["opened"]) == 1, "opened %d" % len(g["opened"]))
g = run("{}", change=True)
check("another account picked meanwhile: not shown", g["opened"] == [], "opened %d" % len(g["opened"]))
print("VERDICT: %s" % ("PASS, 4 of 4" if not fails else "FAIL: %s" % fails))
sys.exit(1 if fails else 0)
